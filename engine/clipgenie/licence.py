"""Sign in with Twitch + the free trial (public / tester builds).

The trial count lives on the licence server (server/licence), never on this PC, so reinstalling or deleting
Klipon's files doesn't reset it. The server also knows this PC (hashed Windows id, board and disk serials),
so a second Twitch account on the same PC gets no new trial.

Only ENFORCED in the compiled engine (tools/build_engine.py), or with env KLIPON_TRIAL=1 for testing.
Running from source (the creator's own copy) never asks."""
import hashlib, json, os, subprocess, threading, time, urllib.error, urllib.request, webbrowser

from .util import load_settings, save_settings

# Baked in at build time (tools/build_engine.py --licence-url). The env override only works when running from source.
LICENCE_URL = "https://clipgenie-licence.REPLACE.workers.dev"
COMPILED = "__compiled__" in globals()
_LOCK = threading.Lock()
_PARTS = None
_STATUS = {"at": 0, "data": None}
TRIAL_PER_STREAM = 10   # free trial: most TikToks from one stream/clip (the server can override: limits.per_stream)


def enforced():
    return COMPILED or os.environ.get("KLIPON_TRIAL") == "1"


def _url():
    if not COMPILED and os.environ.get("KLIPON_LICENCE_URL"):
        return os.environ["KLIPON_LICENCE_URL"].rstrip("/")
    return LICENCE_URL.rstrip("/")


GENERIC = {"", "none", "0", "default string", "to be filled by o.e.m.", "system serial number", "not applicable",
           "not specified", "n/a", "00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff"}


def parts():
    """Hashed PC parts: Windows id + SMBIOS UUID + board serial + system disk serial (generic values dropped)."""
    global _PARTS
    if _PARTS is not None:
        return _PARTS
    raw = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            raw.append(("win", winreg.QueryValueEx(k, "MachineGuid")[0]))
    except Exception:
        pass
    ps = ("$u=(Get-CimInstance Win32_ComputerSystemProduct).UUID;"
          "$b=(Get-CimInstance Win32_BaseBoard).SerialNumber;"
          "$d=(Get-CimInstance Win32_DiskDrive | Where-Object Index -eq 0).SerialNumber;"
          "Write-Output \"$u`n$b`n$d\"")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=20, creationflags=0x08000000).stdout.splitlines()
        for kind, v in zip(("uuid", "board", "disk"), out + ["", "", ""]):
            raw.append((kind, v))
    except Exception:
        pass
    ok = [(k, str(v).strip()) for k, v in raw if str(v).strip().lower() not in GENERIC and len(str(v).strip()) >= 4]
    _PARTS = [hashlib.sha256(f"clipgenie-pc-v1|{k}|{v.lower()}".encode()).hexdigest() for k, v in ok]
    return _PARTS


def _post(path, body, timeout=15):
    req = urllib.request.Request(_url() + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Klipon"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}
    except Exception:
        raise RuntimeError("Can't reach the Klipon server. Check your internet and try again.")


def _get(path, timeout=15):
    req = urllib.request.Request(_url() + path, headers={"User-Agent": "Klipon"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        raise RuntimeError("Can't reach the Klipon server. Check your internet and try again.")


def _version():
    from .server import PUBLIC_VERSION
    return PUBLIC_VERSION   # only enforced (public) builds talk to the licence server


def _token():
    return (load_settings().get("account") or {}).get("token") or ""


_AUTO = {"at": 0}


def _auto_token():
    """Klipon is free (the creator, 12 Nov: "no login"): no sign-in screen. When there is no account yet, this PC
    quietly gets its own (the server's per-PC guest account, the same one again after a reinstall), so the
    built-in AI still has a per-PC daily cap. Tried at most once a minute; "" when the server can't be reached."""
    tok = _token()
    if tok or not enforced():
        return tok
    with _LOCK:
        if time.time() - _AUTO["at"] < 60:
            return ""
        _AUTO["at"] = time.time()
    try:
        code, d = _post("/auth/guest", {"parts": parts(), "name": ""})
    except RuntimeError:
        return ""
    if code != 200 or not d.get("token"):
        return ""
    s = load_settings()
    s["account"] = {"token": d["token"], "user": d.get("user") or {}}
    save_settings(s)
    _STATUS.update(at=0, data=None)
    return d["token"]


def _free():
    # no login anywhere (the creator, 12 Nov): when the server can't be reached, treat Klipon as free too
    return bool(news().get("free", True))


def _forget():
    s = load_settings()
    s.pop("account", None)
    save_settings(s)
    _STATUS.update(at=0, data=None)


def status(fresh=False):
    """{enforced, signed_in, user, plan, limit, used, left, buy_url}."""
    if not enforced():
        return {"enforced": False}
    tok = _auto_token()
    if not tok:   # signed out: say whether Klipon is free (the server's "Klipon is free" switch)
        return {"enforced": True, "signed_in": False, "free": _free()}
    with _LOCK:
        if not fresh and _STATUS["data"] and time.time() - _STATUS["at"] < 60:
            return _STATUS["data"]
    code, d = _post("/me", {"token": tok, "parts": parts(), "version": _version()})
    if code == 426:
        return {"enforced": True, "signed_in": True, "update": True, "min_version": d.get("min_version")}
    if code == 401:
        _forget()
        return {"enforced": True, "signed_in": False, "free": _free()}
    if code == 403:   # blocked from the admin dashboard
        return {"enforced": True, "signed_in": True, "banned": True, "plan": "trial", "left": 0,
                "limit": 0, "used": 0, "limits": {"watermark": True, "ai": False, "pro": False}}
    if code == 410:   # 0.1.0: the beta is over (kill switch in the admin dashboard)
        d = {"enforced": True, "signed_in": True, "closed": True, "plan": "trial", "left": 0,
             "limit": 0, "used": 0, "limits": {"watermark": True, "ai": False, "pro": False}}
        with _LOCK:
            _STATUS.update(at=time.time(), data=d)
        return d
    if code != 200:
        raise RuntimeError("The Klipon server had a problem. Try again in a minute.")
    d = dict(d, enforced=True, signed_in=True)
    with _LOCK:
        _STATUS.update(at=time.time(), data=d)
    return d


def limits():
    """What this copy may use: {"watermark": trial mark on videos, "ai": AI features}. The server decides
    (TRIAL_WATERMARK / TRIAL_AI); signed out or unknown = trial limits."""
    if not enforced():
        return {"watermark": False, "ai": True, "per_stream": 0, "pro": True}
    try:
        st = status()
    except RuntimeError:
        st = _STATUS["data"] or {}
    # public builds are PC only: no AI / API key for anyone (the creator, 12 Nov). "free" = the server's
    # "Klipon is free" switch: no limits at all (the creator, 12 Nov: free for everyone).
    # "ai" = the server's built-in AI (OpenRouter, the key stays on the server); never the user's own key.
    lim = st.get("limits") or {}
    if st.get("plan") in ("paid", "owner") or (st.get("free") and not st.get("banned") and not st.get("closed")):
        return {"watermark": False, "ai": bool(lim.get("ai")), "per_stream": 0, "pro": True}
    # 0.1.0 (the creator): while the trial lasts EVERYTHING is free (Pro features, no watermark); once the free
    # streams are used up the Pro features lock (CS2 kill feed, own styles, music + own sounds, part of a VOD,
    # export to phone) and videos get the trial mark (if the server's watermark switch is on).
    # Streams added during the trial keep their features (opts "_trial_full", see library.channel_style).
    active = (st.get("plan") == "trial" and not st.get("banned") and not st.get("closed")
              and int(st.get("left") or 0) > 0)
    return {"watermark": lim.get("watermark", True) is not False and not active, "ai": bool(lim.get("ai")) and active,
            # per_stream: most TikToks the trial makes from one stream/clip (the creator, 12 Nov: 10); 0 = no limit
            "per_stream": int(lim.get("per_stream", TRIAL_PER_STREAM) or 0),
            "pro": active and lim.get("pro", True) is not False}


def check_open():
    """0.1.0: raise when this copy may not make anything (the beta is over / account blocked)."""
    if not enforced():
        return
    try:
        st = status()
    except RuntimeError:
        return   # can't reach the server: the add-stream check stops new streams anyway
    if st.get("closed"):
        raise RuntimeError("TRIAL_USED: The Klipon beta has ended. Thanks for testing!")
    if st.get("banned"):
        raise RuntimeError("TRIAL_USED: This account is blocked from Klipon.")


def pro():
    return limits().get("pro", True)


PRO_MSG = {
    "killfeed": "The CS2 kill feed",
    "styles": "Saving your own styles",
    "music": "Background music",
    "sfx": "Your own sound effects",
    "range": "Picking part of a VOD",
    "phone": "Export to phone",
}


def need_pro(what):
    """Raise for a Pro feature in the free trial (the app shows it as a 'Pro' preview)."""
    if not pro():
        raise RuntimeError("PRO: %s is part of Klipon Pro, coming soon. Your free trial had it unlocked."
                           % PRO_MSG.get(what, "This"))


_NEWS = {"at": 0, "data": {}}


def news():
    """0.1.0: newest version + its download link + a message for everyone, from the admin dashboard
    ({latest, url, notice, min_version}). Public builds only; checked every 10 minutes; {} when offline."""
    if not enforced():
        return {}
    with _LOCK:
        if time.time() - _NEWS["at"] < 600:
            return _NEWS["data"]
        _NEWS["at"] = time.time()   # also after a failure: don't hold the app up while offline
    try:
        code, d = _get("/news", timeout=5)
        d = d if code == 200 and isinstance(d, dict) else {}
    except RuntimeError:
        d = _NEWS["data"]
    with _LOCK:
        _NEWS["data"] = d
    return d


def ai_allowed():
    return limits()["ai"]


def server_ai():
    """Public builds: the AI runs through the Klipon server (built-in, no key for the user)."""
    return enforced() and ai_allowed()


def ai(body, timeout=150):
    """Send one Claude-style AI request ({system, messages, max_tokens}) through the Klipon server.
    Returns the Claude-style answer ({content:[{type:'text', text}]})."""
    tok = _auto_token()
    if not tok:
        raise RuntimeError("The AI can't reach the Klipon server right now. Try again in a minute.")
    code, d = _post("/ai", {"token": tok, "version": _version(),
                            "body": {k: body.get(k) for k in ("system", "messages", "max_tokens")}}, timeout=timeout)
    if code == 200 and d.get("content"):
        return d
    if code == 429:
        raise RuntimeError("You've used today's AI (%s uses). It comes back tomorrow; everything else still works."
                           % d.get("daily", ""))
    if code == 401:
        _forget()
        raise RuntimeError("The AI had a hiccup. Try again.")
    if code == 403:
        raise RuntimeError("The AI is switched off right now. Everything else still works.")
    raise RuntimeError("The AI is busy right now. Try again in a minute.")


def sign_in():
    """Start Sign in with Twitch: opens the browser, returns the state to poll."""
    code, d = _post("/auth/start", {"parts": parts()})
    if code != 200 or not d.get("url"):
        raise RuntimeError(d.get("error") or "Couldn't start Twitch sign-in. Try again.")
    webbrowser.open(d["url"])
    return {"state": d["state"]}


def guest(name=""):
    """Continue without Twitch: the trial is counted for this PC only (the server allows one guest account per PC)."""
    code, d = _post("/auth/guest", {"parts": parts(), "name": str(name or "")[:25]})
    if code != 200 or not d.get("token"):
        raise RuntimeError(d.get("error") or "Couldn't start the free trial. Try again.")
    s = load_settings()
    s["account"] = {"token": d["token"], "user": d.get("user") or {}}
    save_settings(s)
    _STATUS.update(at=0, data=None)
    return status(fresh=True)


def poll(state):
    code, d = _get("/auth/poll?state=" + urllib.request.quote(str(state)))
    if code == 404:
        return {"expired": True}
    if d.get("token"):
        s = load_settings()
        s["account"] = {"token": d["token"], "user": d.get("user") or {}}
        save_settings(s)
        _STATUS.update(at=0, data=None)
        return status(fresh=True)
    return {"waiting": True}


def sign_out():
    tok = _token()
    if tok:
        try:
            _post("/signout", {"token": tok})
        except RuntimeError:
            pass
    _forget()
    return {"enforced": enforced(), "signed_in": False}


def use(item):
    """Count one new stream/clip against the trial (the same item again is free). Raises when not allowed."""
    if not enforced():
        return None
    tok = _auto_token()
    if not tok:
        return None   # no login (the creator, 12 Nov): never block making TikToks because of an account
    code, d = _post("/use", {"token": tok, "parts": parts(), "item": str(item)[:200], "version": _version()})
    if code == 426:
        raise RuntimeError("UPDATE: This copy of Klipon is out of date. Download the newest version to keep going.")
    if code == 401:
        _forget()
        return None
    if code == 403:
        raise RuntimeError("TRIAL_USED: This account is blocked from Klipon.")
    if code == 410:
        _STATUS.update(at=0, data=None)
        raise RuntimeError("TRIAL_USED: The Klipon beta has ended. Thanks for testing!")
    if code == 402 and d.get("reason") == "trials_closed":
        raise RuntimeError("TRIAL_USED: Free trials have ended. Upgrade to keep making TikToks.")
    if code == 402:
        raise RuntimeError("TRIAL_USED: Your free trial is used up (%s streams). Upgrade to keep making TikToks."
                           % d.get("limit", 3))
    if code != 200 or not d.get("ok"):
        raise RuntimeError("The Klipon server had a problem. Try again in a minute.")
    d = dict(d, enforced=True, signed_in=True)
    with _LOCK:
        _STATUS.update(at=time.time(), data=d)
    return d
