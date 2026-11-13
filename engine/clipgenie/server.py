"""Local HTTP API on 127.0.0.1: serves the Clip Genie app (and the older Premiere panel)."""
import json
import re
import os
import subprocess
import time
import secrets
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse, parse_qs

from .util import data_dir, load_settings, save_settings, log, run, ffmpeg, lower_priority
from . import pipeline as pl
from . import tiktok as tt
from . import twitch as tw
from . import library as lb
from .util import HEAVY

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app")
# 2.27.3: was ONE lock for everything, so a TikTok waited ~30 min for a long stream's chat download.
STREAM_LOCK = threading.Lock()    # one stream ranked / clip imported at a time
EDIT_LOCK = threading.Lock()      # one TikTok made / re-made / AI-edited at a time
# heavy steps (speech, rendering...) share util.HEAVY, so two jobs never load the graphics card together
TIMELINE_LOCK = threading.Lock()  # timeline loads only wait for each other, not for a big job (2.16.4)
CACHE = {}


import contextlib


@contextlib.contextmanager
def _edit_job(key, progress):
    """2.27.3: making / editing a TikTok. Waits for: another TikTok job, the stream it comes from if that stream
    is still being ranked (its moments aren't final yet), and any heavy step of another job."""
    # same stream: wait for it BEFORE queueing for EDIT_LOCK, so TikToks from other streams don't wait behind this one
    s = lb.get_stream(key) or {}
    same = s.get("status") in ("processing", "queued")
    if same:
        progress(0.0, "Waiting: this stream is still being worked on")
        STREAM_LOCK.acquire()
    try:
        if not EDIT_LOCK.acquire(blocking=False):
            progress(0.0, "Waiting for the TikTok before this one")
            EDIT_LOCK.acquire()
        try:
            if not HEAVY.acquire(blocking=False):
                progress(0.0, "Waiting a moment: another stream is using your graphics card")
                HEAVY.acquire()
            try:
                yield
            finally:
                HEAVY.release()
        finally:
            EDIT_LOCK.release()
    finally:
        if same:
            STREAM_LOCK.release()

VERSION = "2.38.0"
PUBLIC_VERSION = "0.1.2"   # public / tester builds count from 0.1 (the creator, 12 Nov: "0.1 Beta"; 1.0 = paid launch)
PORT = 47860
JOBS = {}
ANALYSES = {}
TOKEN = None
LOCK = threading.Lock()

SECRET_KEYS = ("api_key", "tiktok_client_secret", "phone_key")


def _job(fn, *args):
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"id": jid, "status": "running", "progress": 0.0, "message": "Starting", "result": None}

    def progress(p, msg=None):
        JOBS[jid]["progress"] = round(float(p), 4)
        if msg:
            JOBS[jid]["message"] = msg

    def runner():
        try:
            JOBS[jid]["result"] = fn(*args, progress)
            JOBS[jid]["status"] = "done"
            JOBS[jid]["progress"] = 1.0
        except Exception as e:
            log.error("Job failed: %s", traceback.format_exc())
            JOBS[jid]["status"] = "error"
            JOBS[jid]["message"] = str(e)
    threading.Thread(target=runner, daemon=True).start()
    return jid


def _key(video):
    if tw.is_link(video):
        return "twitch:" + tw.vod_id(video) + str(tw.vod_range(video) or "")
    return os.path.normcase(os.path.abspath(video))


def _analysis(video):
    key = _key(video)
    with LOCK:
        a = ANALYSES.get(key)
        if a is None:
            if not tw.is_link(video) and not os.path.isfile(video):
                raise RuntimeError("Video file not found: " + video)
            a = pl.Analysis(video)
            ANALYSES[key] = a
            # restore a previous analysis of this VOD if there is one
            cached = os.path.join(a.cache, "analysis.json")
            if os.path.isfile(cached):
                try:
                    with open(cached, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    a.clips, a.webcam = d.get("clips", []), d.get("webcam")
                    import glob
                    tr = sorted(glob.glob(os.path.join(a.cache, "transcript_*.json")), key=os.path.getmtime)
                    if tr:
                        with open(tr[-1], "r", encoding="utf-8") as f:
                            a.transcript = json.load(f)
                        from .scoring import strip_music
                        a.transcript = strip_music(a.transcript)
                    npz = os.path.join(a.cache, "audio_curve.npz")
                    if os.path.isfile(npz):
                        import numpy as np
                        from .audio import HOP, silences
                        z = np.load(npz)
                        a.audio_curve = {"hop": HOP, "db": z["db"], "z": z["z"]}
                        a.silences = silences(z["db"])
                except Exception as e:
                    log.warning("Could not restore cache: %s", e)
        return a


def _pick_file(kind="video"):
    """Native Windows 'Open' dialog (the app runs in a browser window, which can't give us file paths)."""
    if os.name != "nt":
        return None
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$f=New-Object System.Windows.Forms.OpenFileDialog;"
          + ("$f.Filter='Audio|*.mp3;*.wav;*.m4a;*.aac;*.ogg;*.flac|All files|*.*';$f.Title='"
             + ("Choose a sound effect" if kind == "sfx" else "Choose background music") + "';"
             if kind in ("audio", "sfx") else
             "$f.Filter='Videos|*.mp4;*.mkv;*.mov;*.flv;*.ts;*.webm|All files|*.*';$f.Title='Choose a stream recording';") +
          "$t=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
          "if($f.ShowDialog($t) -eq 'OK'){[Console]::Out.Write($f.FileName)}")
    import subprocess
    p = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stdout.strip() or None


def _merged_opts(o):
    s = load_settings()
    out = dict(s)
    out.update({k: v for k, v in (o or {}).items() if v is not None})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "ClipGenie/" + VERSION

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send(self, code, obj=None, ctype="application/json", raw=None, cors=True):
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-ClipGenie-Token")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, raw=b"")

    def _authed(self, q=None):
        tok = self.headers.get("X-ClipGenie-Token", "") or (q or {}).get("t", "")
        return secrets.compare_digest(tok, TOKEN)

    PHONE = False  # set on the phone (Wi-Fi) server

    def _host_ok(self):
        # blocks DNS-rebinding: only answer requests addressed to this machine
        host = (self.headers.get("Host") or "").split(":")[0]
        if self.PHONE:
            return _private_ip(host)
        return host in ("127.0.0.1", "localhost")

    def _phone_ok(self):
        """Phone requests need the pairing cookie (set by opening the link / QR code once)."""
        if not self.PHONE:
            return True
        key = phone_key()
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "cg_phone" and key and secrets.compare_digest(v, key):
                return True
        return False

    def _static(self, rel):
        rel = rel.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(APP_DIR, rel))
        if not full.startswith(os.path.normpath(APP_DIR)) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"}, cors=False)
        ext = os.path.splitext(full)[1].lower()
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                 ".woff2": "font/woff2", ".ttf": "font/ttf", ".ico": "image/x-icon"}.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        if ext == ".html":
            body = body.replace(b"__CG_TOKEN__", TOKEN.encode())
        return self._send(200, raw=body, ctype=ctype, cors=False)

    def _media(self, path, download=False):
        """Serve a video/image from the library with HTTP Range support (for seeking)."""
        path = os.path.abspath(path)
        roots = [os.path.abspath(lb.output_root()), os.path.abspath(data_dir())]
        loc = os.environ.get("LOCALAPPDATA")
        if loc:
            roots.append(os.path.abspath(os.path.join(loc, "GenieStudioFX")))
        lib = lb.load()
        sources = set()
        for st in lib["streams"].values():
            if st.get("folder"):
                roots.append(os.path.abspath(st["folder"]))
            if st.get("source") and not tw.is_link(st["source"]):
                sources.add(os.path.normcase(os.path.abspath(st["source"])))
        allowed = any(os.path.normcase(path).startswith(os.path.normcase(r) + os.sep) for r in roots) \
            or os.path.normcase(path) in sources
        # 2.35.4: only media, never settings / tokens / logs (they sit in the same data folder)
        name = os.path.basename(path).lower()
        if os.path.splitext(name)[1] in (".json", ".log", ".txt", ".ps1", ".py", ".ini", ".cfg") or "token" in name \
                or "cookie" in name:
            allowed = False
        if not allowed or not os.path.isfile(path):
            return self._send(404, {"error": "not found"}, cors=False)
        size = os.path.getsize(path)
        ext = os.path.splitext(path)[1].lower()
        ctype = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska", ".mov": "video/quicktime",
                 ".jpg": "image/jpeg", ".png": "image/png"}.get(ext, "application/octet-stream")
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if (a and b) else size - 1
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if download:
            safe = "".join(ch for ch in os.path.basename(path) if ch.isalnum() or ch in "._- ") or "tiktok.mp4"
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # Open the file only while reading each piece: a paused video player keeps its connection
        # open, and on Windows a file held open here can't be replaced when the TikTok is re-made.
        pos, left = start, end - start + 1
        try:
            while left > 0:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                pos += len(chunk)
                left -= len(chunk)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, FileNotFoundError):
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if not self._host_ok():
                return self._send(403, {"error": "forbidden"}, cors=False)
            if u.path == "/health":
                return self._send(200, {"ok": True, "version": VERSION})
            if u.path == "/" or u.path == "/app":
                self.send_response(302)
                self.send_header("Location", "/app/")
                self.end_headers()
                return
            if self.PHONE and u.path.startswith("/p/"):
                if secrets.compare_digest(u.path[3:], phone_key() or "-"):
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"cg_phone={phone_key()}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict")
                    # 2.36.1: "Export to phone" QR opens that TikTok straight away (?open=<stream key>|<clip id>)
                    op = q.get("open") or ""
                    ok_open = re.fullmatch(r"[\w.\- ]{1,200}\|[\w.\-]{1,60}", op)
                    self.send_header("Location", "/app/" + ("#open=" + quote(op, safe="") if ok_open else ""))
                    self.end_headers()
                    return
                return self._send(403, {"error": "This phone link is old. Scan the QR code in Clip Genie on your PC again."})
            if u.path.startswith("/app/"):
                rel = (u.path[5:].strip("/") or "index.html").lower()
                if not self._phone_ok() and (rel.endswith(".html") or "." not in os.path.basename(rel)):
                    return self._send(403, raw=PHONE_LOCKED, ctype="text/html; charset=utf-8", cors=False)
                return self._static(u.path[5:])
            if u.path == "/phone":
                if self.PHONE:
                    return self._send(403, {"error": "forbidden"})
                if not self._authed(q):
                    return self._send(401, {"error": "unauthorised"}, cors=False)
                return self._send(200, phone_status())
            if u.path == "/media":
                if not self._authed(q):
                    return self._send(401, {"error": "unauthorised"}, cors=False)
                return self._media(q.get("p", ""), download=q.get("dl") == "1")
            if u.path == "/tiktok/callback" and not self.PHONE:
                try:
                    tt.handle_callback(q, PORT)
                    msg = "TikTok connected. You can close this tab and go back to Clip Genie."
                except Exception as e:
                    msg = "TikTok login failed: " + str(e)
                html = (f"<html><body style='font-family:sans-serif;background:#1d1d1d;color:#eee;"
                        f"display:flex;align-items:center;justify-content:center;height:100vh'>"
                        f"<h2>{msg}</h2></body></html>")
                return self._send(200, raw=html.encode("utf-8"), ctype="text/html; charset=utf-8")
            if not self._authed():
                return self._send(401, {"error": "unauthorised"})
            if u.path == "/job":
                j = JOBS.get(q.get("id", ""))
                return self._send(200 if j else 404, j or {"error": "no such job"})
            if u.path == "/settings":
                s = load_settings()
                for k in SECRET_KEYS:
                    if s.get(k):
                        s[k] = "••••" + s[k][-4:]
                return self._send(200, s)
            if u.path == "/analysis":
                return self._send(200, _analysis(q["video"]).to_json())
            if u.path == "/frame":
                a = _analysis(q["video"])
                t = float(q.get("t", a.info["duration"] / 3))
                out = os.path.join(a.cache, "frame.jpg")
                if a.remote:
                    a.remote.frame(t, out)
                else:
                    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", a.video,
                         "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", out])
                return self._send(200, {"path": out, "width": a.info["width"], "height": a.info["height"]})
            if u.path == "/library":
                lib = lb.load()
                streams = sorted(lib["streams"].values(), key=lambda s: -(s.get("created") or 0))
                return self._send(200, {"streams": streams, "out_root": lb.output_root(),
                                        "style": {k: v for k, v in lb.style_opts().items() if k != "api_key"},
                                        "ai_ready": (bool(load_settings().get("api_key")) or _public()) and _ai_allowed(), "ai_trial": not _ai_allowed(), "public": _public(),
                                        "server_ai": _public() and _ai_allowed(), "pro": _pro(), "busy": STREAM_LOCK.locked() or EDIT_LOCK.locked(),
                                        "version": shown_version(), "looks": _looks(), "news": _news()})
            if u.path == "/taste":
                return self._send(200, _taste_status())
            if u.path == "/library/timeline":
                with TIMELINE_LOCK:
                    return self._send(200, lb.timeline(q["key"], q["clip_id"], CACHE))
            if u.path == "/library/moment_preview":
                return self._send(200, {"path": lb.moment_preview(q["key"], q["clip_id"], CACHE)})
            if u.path == "/library/words":
                return self._send(200, {"words": lb.clip_words(q["key"], q["clip_id"], CACHE)})
            if u.path == "/tiktok/account":
                return self._send(200, tt.account())
            if u.path == "/tiktok/login":
                return self._send(200, {"url": tt.login_url(PORT)})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            log.error(traceback.format_exc())
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"}, cors=False)
        if not self._authed():
            return self._send(401, {"error": "unauthorised"})
        try:
            b = self._body()
            if self.PHONE and u.path in PC_ONLY:
                return self._send(403, {"error": "Do this on your PC."})
            if u.path == "/phone":
                if self.PHONE:
                    return self._send(403, {"error": "forbidden"})
                if b.get("on"):
                    _need_pro("phone")
                st = load_settings()
                st["phone_access"] = bool(b.get("on"))
                if b.get("new_key") or not st.get("phone_key"):
                    st["phone_key"] = secrets.token_urlsafe(12)
                save_settings(st)
                set_phone_server(st["phone_access"])
                return self._send(200, phone_status())
            if u.path.startswith("/account"):   # Sign in with Twitch + free trial (licence.py)
                from . import licence
                if u.path == "/account":
                    return self._send(200, licence.status(fresh=bool(b.get("fresh"))))
                if u.path == "/account/signin":
                    return self._send(200, licence.sign_in())
                if u.path == "/account/guest":
                    return self._send(200, licence.guest(b.get("name") or ""))
                if u.path == "/account/poll":
                    return self._send(200, licence.poll(b.get("state") or ""))
                if u.path == "/account/signout":
                    return self._send(200, licence.sign_out())
            if u.path == "/settings":
                s = load_settings()
                for k, v in b.items():
                    if k in SECRET_KEYS and isinstance(v, str) and v.startswith("••••"):
                        continue
                    s[k] = v
                save_settings(s)
                return self._send(200, {"ok": True})
            if u.path == "/analyze":
                a = _analysis(b["video"])
                opts = _merged_opts(b.get("opts"))
                opts["chat_path"] = b.get("chat_path") or None
                jid = _job(pl.analyze, a, opts)
                return self._send(200, {"job": jid})
            if u.path == "/webcam":
                a = _analysis(b["video"])
                if b.get("none"):
                    a.webcam = {"found": False, "layout": "gameplay", "method": "manual"}
                elif b.get("box"):
                    a.webcam = {"found": True, "layout": "split", "box": [float(v) for v in b["box"]],
                                "method": "manual"}
                elif b.get("layout"):
                    a.webcam = dict(a.webcam or {}, layout=b["layout"])
                a.save()
                return self._send(200, {"webcam": a.webcam})
            if u.path == "/preview":
                a = _analysis(b["video"])
                opts = _merged_opts(b.get("opts"))
                path = pl.preview(a, b["clip_id"], b.get("edit") or {}, opts, os.path.join(a.cache, "previews"))
                return self._send(200, {"path": path})
            if u.path == "/render":
                a = _analysis(b["video"])
                opts = _merged_opts(b.get("opts"))
                out_dir = b.get("out_dir")
                if not out_dir:
                    if a.remote:
                        vids = os.path.join(os.path.expanduser("~"), "Videos")
                        safe = "".join(ch for ch in a.info.get("title", a.remote.id) if ch.isalnum() or ch in " -_")[:50].strip()
                        out_dir = os.path.join(vids, "Clip Genie Exports", safe or a.remote.id)
                    else:
                        out_dir = os.path.join(os.path.dirname(a.video), "Clip Genie Exports")
                jid = _job(pl.render_all, a, b["clip_ids"], b.get("edits") or {}, opts, out_dir)
                return self._send(200, {"job": jid})
            if u.path == "/library/add":
                src = (b.get("source") or "").strip()
                if not src:
                    raise RuntimeError("Paste a Twitch, YouTube or Kick link, or choose a video file.")
                if lb.is_clip_source(src):
                    # a Twitch clip or a short video file: the whole thing becomes one TikTok
                    key = lb.clip_key(src)
                    _trial_use(key)
                    lb.update_stream(key, source=src, kind="clip", status="queued", message="Waiting to start", progress=0.0,
                                     **({"look": str(b.get("look") or "")} if "look" in b else {}),
                                     title=b.get("title") or (os.path.basename(src) if not tw.is_link(src) else "Clip"))

                    def imp(progress):
                        with STREAM_LOCK, HEAVY:   # a short clip is heavy almost all the way through
                            return lb.import_clip(src, progress, analysis_cache=CACHE)
                    return self._send(200, {"job": _job(imp), "key": key, "kind": "clip"})
                if tw.is_link(src):
                    tw.vod_id(src)
                    rg = b.get("range")   # 2.35.3: [from, to] seconds = only that part of the VOD
                    if rg:
                        _need_pro("range")
                        a0, b0 = float(rg[0]), float(rg[1])
                        if a0 < 0 or b0 - a0 < 60:
                            raise RuntimeError("Pick a part of the VOD at least 1 minute long (From before To).")
                        src = tw.range_link(src, a0, b0)
                elif not os.path.isfile(src):
                    raise RuntimeError("Video file not found: " + src)
                key = lb.stream_key(src)
                _trial_use(key)
                lb.update_stream(key, source=src, status="queued", message="Waiting to start", progress=0.0,
                                 **({"look": str(b.get("look") or "")} if "look" in b else {}),
                                 title=b.get("title") or (os.path.basename(src) if not tw.is_link(src) else None))

                def proc(progress):
                    with STREAM_LOCK:          # heavy steps inside take util.HEAVY themselves
                        return lb.process(src, progress, analysis_cache=CACHE)
                return self._send(200, {"job": _job(proc), "key": key})
            if u.path == "/pick_file":
                return self._send(200, {"path": _pick_file(b.get("kind", "video"))})
            if u.path == "/music":
                # copy the chosen track into Clip Genie's own folder so it keeps working if moved
                src = (b.get("path") or "").strip()
                s2 = load_settings()
                st = dict(s2.get("style") or {})
                if b.get("clear"):
                    st["bg_music"] = ""
                elif src:
                    _need_pro("music")
                    if not os.path.isfile(src):
                        raise RuntimeError("Music file not found")
                    import shutil
                    mdir = os.path.join(data_dir(), "music")
                    os.makedirs(mdir, exist_ok=True)
                    dst = os.path.join(mdir, os.path.basename(src))
                    if os.path.abspath(src) != os.path.abspath(dst):
                        shutil.copy2(src, dst)
                    st["bg_music"] = dst
                if "volume" in b:
                    st["bg_music_volume"] = max(0.0, min(1.0, float(b["volume"])))
                s2["style"] = st
                save_settings(s2)
                return self._send(200, {"bg_music": st.get("bg_music", ""), "bg_music_volume": st.get("bg_music_volume", 0.16)})
            if u.path == "/library/rerender":
                key, cid = b["key"], b["clip_id"]

                def rr(progress):
                    with _edit_job(key, progress):
                        return lb.rerender(key, cid, b.get("edits") or {}, b.get("style") or {}, progress, CACHE)
                return self._send(200, {"job": _job(rr)})
            if u.path == "/library/ask":
                key, cid = b["key"], b["clip_id"]

                def ask(progress):
                    with _edit_job(key, progress):
                        return lb.ask(key, cid, b.get("text", ""), b.get("edits") or {}, b.get("style") or {}, progress, CACHE)
                return self._send(200, {"job": _job(ask)})
            if u.path == "/library/undo":
                return self._send(200, lb.undo(b["key"], b["clip_id"]))
            if u.path == "/library/find":
                key = b["key"]

                def fm(progress):
                    return lb.find_moment(key, b.get("text", ""), progress, CACHE)
                return self._send(200, {"job": _job(fm)})
            if u.path == "/library/preview":   # 2.29: live 9:16 preview layout (fast: nothing is rendered)
                return self._send(200, lb.preview(b["key"], b["clip_id"], b.get("style") or {}, b.get("cam_box"),
                                                  b.get("game_box"), CACHE))
            if u.path == "/library/timeline_captions":
                caps = lb.timeline_captions(b["key"], b["clip_id"], float(b["clip_start"]), b["segs"],
                                            b.get("style"), CACHE)
                return self._send(200, {"captions": caps})
            if u.path == "/library/feedback":
                return self._send(200, lb.feedback(b["key"], b["clip_id"], b.get("vote"), CACHE, b.get("reasons")))
            if u.path == "/library/views":
                return self._send(200, lb.set_views(b["key"], b["clip_id"], b.get("views"), analysis_cache=CACHE))
            if u.path == "/taste/rules":
                from . import taste
                st = lb._ai_settings()   # public builds: the built-in server AI
                if not st.get("api_key"):
                    return self._send(400, {"error": "Add your Claude API key in Settings first."})
                return self._send(200, _taste_status(taste.update_rules(st["api_key"], st.get("ai_model"))))
            if u.path == "/tiktok/sync_views":
                st = load_settings()
                if not st.get("tiktok_stats"):
                    st["tiktok_stats"] = True   # ask TikTok for view counts from the next login on
                    save_settings(st)
                return self._send(200, lb.sync_views(CACHE))
            if u.path == "/library/clip":
                allowed = {k: v for k, v in (b.get("fields") or {}).items() if k in ("title", "caption", "status")}
                if allowed.get("status") == "discarded":
                    try:  # throwing a TikTok away tells the AI you didn't like that kind of moment
                        lb.feedback(b["key"], b["clip_id"], "down", CACHE)
                    except Exception:
                        pass
                return self._send(200, lb.update_clip(b["key"], b["clip_id"], **allowed))
            if u.path == "/library/remove_stream":
                with lb._LOCK:
                    lib = lb.load()
                    lib["streams"].pop(b["key"], None)
                    lb.save(lib)
                return self._send(200, {"ok": True})
            if u.path == "/open_update":   # 0.1.0: the admin dashboard's update link, in the normal browser
                link = (_news().get("url") or "").strip()
                if not re.match(r"^https://[^\s\"'<>]{3,500}$", link):
                    raise RuntimeError("No download link yet. Ask the person who sent you Clip Genie.")
                import webbrowser
                webbrowser.open(link)
                return self._send(200, {"ok": True})
            if u.path == "/library/open_folder":
                st = lb.get_stream(b["key"]) or {}
                folder = st.get("folder") or lb.output_root()
                os.makedirs(folder, exist_ok=True)
                if os.name == "nt":
                    os.startfile(folder)  # noqa
                return self._send(200, {"ok": True, "folder": folder})
            if u.path == "/library/post_open":
                # "Post everywhere": open each site's upload page in your browser (where you're logged in)
                # and show the clip in its folder, ready to drag in. The app copies the caption.
                st = lb.get_stream(b["key"]) or {}
                c = next((x for x in st.get("clips", []) if x["id"] == b["clip_id"]), None)
                if not c or not os.path.isfile(c.get("path") or ""):
                    return self._send(400, {"error": "Can't find this TikTok's video file."})
                import webbrowser
                opened = []
                for name in b.get("platforms") or []:
                    if name in POST_PAGES:
                        webbrowser.open(POST_PAGES[name])
                        opened.append(name)
                        time.sleep(0.4)
                if os.name == "nt":
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(c["path"])])
                return self._send(200, {"ok": True, "opened": opened, "path": c["path"]})
            if u.path == "/sfx":
                # 2.19: your sound effects ({"path"} imports a file, {"name", "delete": true} removes one)
                from . import sounds
                if b.get("delete"):
                    sounds.remove_sound(str(b.get("name") or ""))
                    return self._send(200, {"sounds": sounds.list_sounds()})
                _need_pro("sfx")
                name = sounds.import_sound(b.get("path"))
                return self._send(200, {"sounds": sounds.list_sounds(), "added": name})
            if u.path == "/styles":
                # 2.18: your own saved looks ({"name", "style"} saves, {"name", "delete": true} removes)
                name = str(b.get("name") or "").strip()[:40]
                if not name:
                    raise RuntimeError("Give your style a name first.")
                s = load_settings()
                mine = dict(s.get("my_styles") or {})
                if b.get("delete"):
                    mine.pop(name, None)
                else:
                    _need_pro("styles")
                    keep = {k: v for k, v in (b.get("style") or {}).items() if k not in ("captions", "api_key", "title_text")}
                    keep["look"] = "mine:" + name
                    mine[name] = keep
                s["my_styles"] = mine
                save_settings(s)
                return self._send(200, _looks())
            if u.path == "/library/style":
                s = load_settings()
                s["style"] = {k: v for k, v in (b.get("style") or {}).items()}
                save_settings(s)
                return self._send(200, {"ok": True})
            if u.path == "/tiktok/logout":
                tt.logout()
                return self._send(200, {"ok": True})
            if u.path == "/tiktok/upload":
                items = b["items"]  # [{path, caption}]
                mode = b.get("mode", "draft")

                def up(progress):
                    done = []
                    for i, it in enumerate(items):
                        progress(i / len(items), f"Uploading {i + 1} of {len(items)}")
                        if mode == "direct":
                            pid = tt.direct_post(it["path"], it.get("caption", ""), b.get("privacy", "SELF_ONLY"),
                                                 b.get("allow_comment", True), b.get("allow_duet", True),
                                                 b.get("allow_stitch", True))
                        else:
                            pid = tt.send_to_drafts(it["path"])
                        done.append({"path": it["path"], "publish_id": pid})
                    progress(1.0, "Sent to TikTok" if mode == "direct" else "Sent to your TikTok drafts")
                    return done
                return self._send(200, {"job": _job(up)})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            log.error(traceback.format_exc())
            return self._send(500, {"error": str(e)})


def _looks():
    from .style import PRESETS
    from .render import HUD_PARTS
    from .sounds import list_sounds
    return {"presets": PRESETS, "mine": load_settings().get("my_styles") or {},
            "hud_parts": [{"id": k, "name": v["name"]} for k, v in HUD_PARTS.items() if not v.get("hidden")],
            "sounds": list_sounds()}


def _taste_status(rules=None):
    from . import taste
    d = taste.load()
    r = rules if rules is not None else (d.get("rules") or {})
    rb = taste.reason_bias()
    hits, flops, med = taste.perf_ranked()
    ds, de = taste.edit_bias()
    return {"rules": r.get("rules", []), "summary": r.get("summary", ""), "rules_at": r.get("at"),
            "likes": len(d.get("likes", [])), "dislikes": len(d.get("dislikes", [])), "edits": len(d.get("edits", [])),
            "views": len(d.get("perf", [])), "median_views": med, "reasons": rb["counts"],
            "len_scale": rb["len_scale"], "edit_bias": [ds or rb["d_start"], de or rb["d_end"]],
            "reason_labels": taste.REASONS, "ai_ready": bool(lb._ai_settings().get("api_key"))}


PHONE_PORT = int(os.environ.get("CLIPGENIE_PHONE_PORT") or 47861)   # 2.36.1: a second copy (tester test) uses its own
POST_PAGES = {"tiktok": "https://www.tiktok.com/tiktokstudio/upload",
              "youtube": "https://www.youtube.com/upload",
              "instagram": "https://www.instagram.com/"}
def _ai_allowed():
    from . import licence
    try:
        return licence.ai_allowed()
    except Exception:
        return False


def _pro():
    from . import licence
    try:
        return licence.pro()
    except Exception:
        return False


def _need_pro(what):
    from . import licence
    licence.need_pro(what)


def shown_version():
    """The ONE version people see: the app, engine.log, the installer name and the release tag all use
    PUBLIC_VERSION. VERSION is only the internal build number (in /health, where the launcher compares it with
    server.py to restart the engine after an update, and after "engine" in the log header)."""
    return PUBLIC_VERSION


def _public():
    """Public / tester build (licence enforced): PC only, the app hides the AI and the API key box."""
    from . import licence
    return licence.enforced()


def _news():
    """0.1.0: update notice + message from the admin dashboard (public builds)."""
    if not _public():
        return {}
    try:
        from . import licence
        return licence.news()
    except Exception:
        return {}


def _trial_use(key):
    """Free trial: a NEW stream/clip counts once on the licence server (public builds only)."""
    from . import licence
    if licence.enforced():
        d = licence.use(key) or {}
        if d.get("plan") == "trial":   # 0.1.0: counted in the free trial: this stream keeps every feature
            lb.update_stream(key, trial_full=True)


PC_ONLY = {"/open_update", "/account/signin", "/account/guest", "/account/signout", "/sfx","/pick_file", "/library/open_folder", "/library/post_open", "/music", "/phone", "/tiktok/login", "/tiktok/sync_views"}
PHONE_LOCKED = ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='font:16px system-ui;background:#161618;color:#eee;padding:32px'>"
                "<h2>Clip Genie</h2><p>Open the phone link (or scan the QR code) from Clip Genie on your PC: "
                "Settings &rarr; Use on your phone.</p></body>").encode()
_PHONE_SRV = None


def _private_ip(host):
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the Wi-Fi/LAN address
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def phone_key():
    return load_settings().get("phone_key")


def phone_status():
    st = load_settings()
    ip = lan_ip()
    on = bool(st.get("phone_access")) and _PHONE_SRV is not None
    url = f"http://{ip}:{PHONE_PORT}/p/{st.get('phone_key')}" if (ip and st.get("phone_key")) else None
    return {"on": on, "url": url if on else None, "ip": ip}


class PhoneHandler(Handler):
    PHONE = True


def set_phone_server(on):
    """Serve the app to phones on your home Wi-Fi (separate port, pairing link required)."""
    global _PHONE_SRV
    if on and _PHONE_SRV is None:
        try:
            _PHONE_SRV = ThreadingHTTPServer(("0.0.0.0", PHONE_PORT), PhoneHandler)
        except OSError as e:
            log.warning("Phone access couldn't start: %s", e)
            return
        threading.Thread(target=_PHONE_SRV.serve_forever, daemon=True).start()
        log.info("Phone access on http://%s:%d", lan_ip(), PHONE_PORT)
    elif not on and _PHONE_SRV is not None:
        srv, _PHONE_SRV = _PHONE_SRV, None
        threading.Thread(target=srv.shutdown, daemon=True).start()


LOG_SESSIONS = 3                  # engine.log + engine.prev1.log + engine.prev2.log
LOG_MAX_BYTES = 5 * 1024 * 1024   # one session: engine.log up to 5 MB, then one overflow file (engine.log.1)


def _log_file():
    """engine.log for this session. The last sessions are kept as engine.prev1.log / prev2, older ones are
    deleted, and a long session rolls over at LOG_MAX_BYTES, so the log folder stays a few MB."""
    import logging.handlers
    d = data_dir()
    path = os.path.join(d, "engine.log")
    prev = [os.path.join(d, f"engine.prev{i}.log") for i in range(1, LOG_SESSIONS)]
    try:
        if os.path.exists(path):
            if os.path.exists(prev[-1]):
                os.remove(prev[-1])
            for a, b in reversed(list(zip(prev, prev[1:]))):
                if os.path.exists(a):
                    os.replace(a, b)
            os.replace(path, prev[0])
        if os.path.exists(path + ".1"):
            os.remove(path + ".1")
    except OSError:
        pass   # another engine still has the log open (testing on a spare port): just append to it

    class _Log(logging.handlers.RotatingFileHandler):
        header = ""

        def doRollover(self):   # every file starts with the session header, also after a roll-over
            super().doRollover()
            if self.stream and self.header:
                self.stream.write(self.header + " (continued)\n")
    return _Log(path, maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8")


def main(port=PORT):
    global TOKEN
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[_log_file(), logging.StreamHandler()])
    lower_priority()
    TOKEN = secrets.token_hex(24)
    with open(os.path.join(data_dir(), "engine_token"), "w") as f:
        f.write(TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if load_settings().get("phone_access"):
        set_phone_server(True)
    try:
        shown = shown_version()
    except Exception:
        shown = PUBLIC_VERSION
    header = "===== Clip Genie %s (engine build %s) session start %s, pid %d =====" % (
        shown, VERSION, time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid())
    for h in logging.getLogger().handlers:
        if hasattr(h, "header"):
            h.header = header
    log.info(header)
    log.info("Clip Genie %s (engine build %s) on http://127.0.0.1:%d", shown, VERSION, port)
    srv.serve_forever()
