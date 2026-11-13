"""Kliper: updates from GitHub Releases (github.com/Bengo27/Kliper).

Each installer is built by GitHub Actions, which writes _build.py (BUILD = the run number) into the engine.
A release is tagged v<version>-beta.<run number>, so a release with a higher number than BUILD is newer.
news() tells the app about it (the bar at the top), install() downloads the new Setup.exe and opens it:
the installer stops the running engine, installs over the old copy (settings, clips and speech models stay)
and opens Kliper again."""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request

from .util import log

REPO = "Bengo27/Kliper"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=8"
CHECK_EVERY = 30 * 60   # GitHub allows 60 unauthenticated calls an hour per address

try:
    from ._build import BUILD   # written by .github/workflows/build.yml
except Exception:
    BUILD = 0                    # running from source: no update checks

_LOCK = threading.Lock()
_CACHE = {"at": 0.0, "data": None}
STATE = {"busy": False, "msg": "", "error": ""}


def _build_of(tag):
    m = re.search(r"beta\.(\d+)$", tag or "")
    return int(m.group(1)) if m else 0


def latest():
    """The newest release with an installer: {"tag", "build", "url", "name"}, or None (offline / none yet)."""
    with _LOCK:
        if time.time() - _CACHE["at"] < CHECK_EVERY:
            return _CACHE["data"]
        _CACHE["at"] = time.time()   # also after a failure: never hold the app up while offline
    data = _CACHE["data"]
    try:
        req = urllib.request.Request(API, headers={"User-Agent": "Kliper", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            rels = json.loads(r.read().decode("utf-8"))
        best = None
        for rel in rels if isinstance(rels, list) else []:
            if rel.get("draft"):
                continue
            exe = next((a for a in rel.get("assets") or [] if a.get("name", "").lower().endswith(".exe")), None)
            n = _build_of(rel.get("tag_name"))
            if exe and n and (best is None or n > best["build"]):
                best = {"tag": rel["tag_name"], "build": n, "url": exe["browser_download_url"],
                        "name": rel.get("name") or rel["tag_name"], "size": exe.get("size") or 0}
        data = best
    except Exception as e:
        log.info("Update check failed: %s", e)
    with _LOCK:
        _CACHE["data"] = data
    return data


def news():
    """For the app's top bar: {"newer": True, "latest", "url", "notice"} when a newer installer exists."""
    if not BUILD:
        return {}
    rel = latest()
    out = {"build": BUILD}
    if rel and rel["build"] > BUILD:
        out.update(newer=True, latest=rel["tag"].lstrip("v"), url=rel["url"],
                   notice="יש גרסה חדשה של קליפר. לחצו 'עדכון עכשיו': ההגדרות והקליפים נשמרים.")
    if STATE["busy"] or STATE["error"]:
        out["notice"] = STATE["error"] or STATE["msg"]
    return out


def install():
    """Download the newest Setup.exe (in the background) and open it. Returns at once."""
    rel = latest()
    if not rel or rel["build"] <= BUILD:
        raise RuntimeError("אין עדכון חדש כרגע.")
    if STATE["busy"]:
        return
    STATE.update(busy=True, error="", msg="מוריד את העדכון…")

    def run():
        try:
            dst = os.path.join(tempfile.gettempdir(), f"Kliper-Setup-{rel['build']}.exe")
            if not (os.path.isfile(dst) and rel["size"] and os.path.getsize(dst) == rel["size"]):
                req = urllib.request.Request(rel["url"], headers={"User-Agent": "Kliper"})
                with urllib.request.urlopen(req, timeout=60) as r, open(dst + ".part", "wb") as f:
                    total, got = rel["size"] or int(r.headers.get("Content-Length") or 0), 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            STATE["msg"] = "מוריד את העדכון… %d%%" % (got * 100 // total)
                os.replace(dst + ".part", dst)
            STATE["msg"] = "פותח את ההתקנה…"
            log.info("Opening the update installer %s", dst)
            if os.name == "nt":
                # its own process: the installer closes this engine before it replaces the files
                subprocess.Popen([dst], creationflags=0x00000008 | 0x00000200, close_fds=True)
            STATE.update(busy=False, msg="")
        except Exception as e:
            log.warning("Update download failed: %s", e)
            STATE.update(busy=False, msg="", error="ההורדה של העדכון נכשלה. נסו שוב, או הורידו מ-github.com/" + REPO)

    threading.Thread(target=run, daemon=True).start()
