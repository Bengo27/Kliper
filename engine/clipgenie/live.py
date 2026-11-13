"""Kliper: follow your Kick channel while you stream ("מעקב אחרי הלייב").

While you are live it does only light work, so the stream and the game are not slowed down:
  * every ~90 s: asks Kick whether the channel is live (one small web request);
  * while live: reads the stream's chat from Kick's public chat socket and writes each message to a file
    (Kick keeps no chat replay, so this is the only way to have chat for the VOD).
Nothing is downloaded, transcribed or rendered during the stream.

When the stream ends it waits for Kick's VOD, saves the chat into that VOD's cache (chat.json, times counted
from the VOD's start) and starts the normal processing of the VOD, at low priority.
Sessions live in <data>/live/<livestream id>/ (meta.json + chat.jsonl)."""
import datetime as dt
import json
import os
import threading
import time

from .util import data_dir, log

PUSHER_URL = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
LIVE_CHAT = False   # Kick moved its chat off Pusher (Nov 2026); chat isn't needed for shorts, so it's off
POLL_OFF = 90       # seconds between "are you live?" checks
POLL_LIVE = 60
VOD_WAIT_MAX = 6 * 3600   # give up looking for the VOD after this long

STATUS = {"enabled": False, "channel": "", "live": False, "title": "", "since": None, "chat": 0, "chat_ok": True,
          "state": "off", "message": "", "checked_at": None}


def _parse_time(s):
    """Kick times: '2026-11-13 08:00:00' (UTC) or ISO with Z / offset -> unix seconds."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    if "T" not in s and " " in s:
        s = s.replace(" ", "T")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def _ie():
    """yt-dlp's Kick extractor: it already knows how to talk to Kick's API past Cloudflare (curl_cffi)."""
    import yt_dlp
    ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
    return ydl, ydl.get_info_extractor("Kick")


def channel_info(slug):
    ydl, ie = _ie()
    with ydl:
        return ie._call_api(f"v2/channels/{slug}", slug, note=False)


def find_vod(slug, live_start):
    """The VOD of the stream that started at live_start: (url, title, start) or None while Kick hasn't made it."""
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
        pl = ydl.extract_info(f"https://kick.com/{slug}/videos", download=False)
    best = None
    for e in (pl or {}).get("entries") or []:
        ts = e.get("timestamp")
        if e.get("is_live") or not ts or not e.get("url"):
            continue
        gap = abs(ts - live_start)
        if gap < 20 * 60 and (best is None or gap < best[0]):
            best = (gap, e["url"].split("#")[0], e.get("title") or "", ts)
    return best[1:] if best else None


class ChatRecorder(threading.Thread):
    """Writes every chat message of one chatroom to chat.jsonl: {"t": unix time, "u": name, "c": colour, "m": text}."""

    def __init__(self, chatroom_id, path):
        super().__init__(daemon=True)
        self.chatroom_id, self.path = chatroom_id, path
        self.stop_ev = threading.Event()
        self.count = 0
        self.subscribed = False

    def stop(self):
        self.stop_ev.set()

    def run(self):
        from websockets.sync.client import connect
        backoff = 2
        while not self.stop_ev.is_set():
            try:
                with connect(PUSHER_URL, open_timeout=15, close_timeout=3) as ws:
                    ws.send(json.dumps({"event": "pusher:subscribe",
                                        "data": {"auth": "", "channel": f"chatrooms.{self.chatroom_id}.v2"}}))
                    backoff = 2
                    with open(self.path, "a", encoding="utf-8") as f:
                        while not self.stop_ev.is_set():
                            try:
                                raw = ws.recv(timeout=30)
                            except TimeoutError:
                                ws.send(json.dumps({"event": "pusher:ping", "data": {}}))
                                continue
                            ev = json.loads(raw)
                            name = ev.get("event", "")
                            if name == "pusher:error":
                                # Kick moved its chat off this Pusher app (Nov 2026): stop instead of retrying forever
                                log.warning("Live chat unavailable: %s", ev.get("data"))
                                STATUS["chat_ok"] = False
                                self.stop_ev.set()
                                break
                            if name == "pusher_internal:subscription_succeeded":
                                self.subscribed = True
                                continue
                            if name == "pusher:ping":
                                ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                                continue
                            if not name.endswith("ChatMessageEvent"):
                                continue
                            d = ev.get("data")
                            d = json.loads(d) if isinstance(d, str) else (d or {})
                            snd = d.get("sender") or {}
                            row = {"t": _parse_time(d.get("created_at")) or time.time(),
                                   "u": snd.get("username") or "",
                                   "c": ((snd.get("identity") or {}).get("color") or ""),
                                   "m": d.get("content") or ""}
                            f.write(json.dumps(row, ensure_ascii=False) + "\n")
                            f.flush()
                            self.count += 1
                            STATUS["chat"] = self.count
            except Exception as e:
                if self.stop_ev.is_set():
                    break
                log.warning("Live chat: connection lost (%s), reconnecting in %ds", e, backoff)
                self.stop_ev.wait(backoff)
                backoff = min(60, backoff * 2)


def chat_for_vod(session_dir, vod_start):
    """chat.jsonl -> ([(seconds from VOD start, text)], [[seconds, text, name, colour]]) like Kliper's Twitch chat."""
    msgs, users = [], []
    p = os.path.join(session_dir, "chat.jsonl")
    if not os.path.isfile(p):
        return msgs, users
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = float(r.get("t") or 0) - vod_start
            if t < 0 or not r.get("m"):
                continue
            msgs.append((round(t, 2), r["m"]))
            users.append([round(t, 2), r["m"], r.get("u") or "", r.get("c") or ""])
    order = sorted(range(len(msgs)), key=lambda i: msgs[i][0])
    return [msgs[i] for i in order], [users[i] for i in order]


class LiveWatcher(threading.Thread):
    """on_vod(url, title) is called once per finished stream, after its chat is in the VOD's cache."""

    def __init__(self, get_settings, on_vod):
        super().__init__(daemon=True)
        self.get_settings, self.on_vod = get_settings, on_vod
        self.rec = None
        self.session = None
        self.wake = threading.Event()
        self.root = os.path.join(data_dir(), "live")
        os.makedirs(self.root, exist_ok=True)

    def poke(self):
        """Settings changed: check now."""
        self.wake.set()

    def _slug(self, st):
        s = (st.get("live_channel") or st.get("twitch_channel") or "").strip().lower()
        for pre in ("https://", "http://", "www.", "kick.com/"):
            s = s.replace(pre, "")
        return s.strip("/@ ").split("/")[0]

    def run(self):
        while True:
            wait = POLL_OFF
            try:
                wait = self.tick()
            except Exception as e:
                log.warning("Live watcher: %s", e)
                STATUS["message"] = "לא הצלחתי לבדוק את הערוץ ב-Kick. אנסה שוב בעוד דקה."
            self.wake.wait(wait)
            self.wake.clear()

    def _save(self, meta):
        with open(os.path.join(meta["dir"], "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

    def _sessions(self):
        out = []
        for d in os.listdir(self.root):
            p = os.path.join(self.root, d, "meta.json")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
        return out

    def tick(self):
        st = self.get_settings() or {}
        slug = self._slug(st)
        on = bool(st.get("live_watch")) and bool(slug)
        STATUS.update(enabled=on, channel=slug, checked_at=time.time())
        if not on:
            if self.rec:
                self._end_live()
            STATUS.update(live=False, state="off", message="")
            return POLL_OFF
        data = channel_info(slug) or {}
        ls = data.get("livestream") if isinstance(data.get("livestream"), dict) else None
        if ls and not self.session:
            self._start_live(slug, data, ls)
        elif not ls and self.session:
            self._end_live()
        if self.session:
            STATUS.update(live=True, state="live", title=self.session.get("title", ""),
                          since=self.session.get("start"), message="")
        else:
            STATUS.update(live=False, state="watching", message="")
        self._finish_ended(slug)
        return POLL_LIVE if self.session else POLL_OFF

    def _start_live(self, slug, data, ls):
        lid = str(ls.get("id") or int(time.time()))
        d = os.path.join(self.root, lid)
        os.makedirs(d, exist_ok=True)
        old = os.path.join(d, "meta.json")
        if os.path.isfile(old):   # Kliper was restarted during the stream: carry on with the same session
            with open(old, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["status"] = "live"
        else:
            meta = {"id": lid, "dir": d, "slug": slug, "title": ls.get("session_title") or "",
                    "start": _parse_time(ls.get("start_time") or ls.get("created_at")) or time.time(),
                    "chatroom": (data.get("chatroom") or {}).get("id"), "status": "live", "ended": None}
        self._save(meta)
        self.session = meta
        if meta.get("chatroom") and LIVE_CHAT:
            self.rec = ChatRecorder(meta["chatroom"], os.path.join(d, "chat.jsonl"))
            self.rec.start()
        log.info("Live: %s is live (%s)", slug, meta["title"])

    def _end_live(self):
        if self.rec:
            self.rec.stop()
            self.rec = None
        if self.session:
            self.session.update(status="ended", ended=time.time())
            self._save(self.session)
            log.info("Live: stream %s ended, waiting for the VOD", self.session["id"])
        self.session = None
        STATUS.update(live=False, chat=0)

    def _finish_ended(self, slug):
        for meta in self._sessions():
            if meta.get("status") != "ended" or meta.get("slug") != slug:
                continue
            if time.time() - (meta.get("ended") or 0) > VOD_WAIT_MAX:
                meta["status"] = "no_vod"
                self._save(meta)
                continue
            vod = find_vod(slug, meta["start"])
            if not vod:
                STATUS["message"] = "השידור נגמר. מחכה ש-Kick יפרסם את ה-VOD…"
                continue
            url, title, vstart = vod
            from . import twitch as tw
            cache = tw.cache_dir(tw.vod_id(url))
            msgs, users = chat_for_vod(meta["dir"], vstart)
            if msgs and not os.path.isfile(os.path.join(cache, "chat.json")):
                with open(os.path.join(cache, "chat.json"), "w", encoding="utf-8") as f:
                    json.dump(msgs, f)
                with open(os.path.join(cache, "chat_users.json"), "w", encoding="utf-8") as f:
                    json.dump(users, f)
            meta.update(status="queued", vod=url)
            self._save(meta)
            log.info("Live: VOD %s found (%d chat messages), processing it", url, len(msgs))
            STATUS["message"] = "ה-VOD נמצא. קליפר מעבד אותו עכשיו."
            self.on_vod(url, title or meta.get("title") or None)


WATCHER = None


def start(get_settings, on_vod):
    global WATCHER
    if WATCHER is None:
        WATCHER = LiveWatcher(get_settings, on_vod)
        WATCHER.start()
    return WATCHER
