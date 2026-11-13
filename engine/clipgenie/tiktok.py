"""TikTok Content Posting API: login (desktop PKCE), send to drafts, direct post.

Uses your own TikTok developer app (client key + secret in Kliper settings).
- Send to drafts (inbox upload) works straight away.
- Direct post publishes as private ("only me") until TikTok audits the app.
"""
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.error
import urllib.request

from .util import data_dir, load_settings, log

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload,video.publish"
STATS_SCOPE = "video.list"   # read your posted videos' view counts (asked for only when you turn it on)


def scopes():
    return SCOPES + ("," + STATS_SCOPE if load_settings().get("tiktok_stats") else "")
_pending = {}


def _token_path():
    return os.path.join(data_dir(), "tiktok_token.json")


def _keys():
    s = load_settings()
    ck, cs = s.get("tiktok_client_key"), s.get("tiktok_client_secret")
    if not ck or not cs:
        raise RuntimeError("Add your TikTok client key and secret in Kliper settings first.")
    return ck, cs


def redirect_uri(port):
    return load_settings().get("tiktok_redirect_uri") or f"http://localhost:{port}/tiktok/callback"


def login_url(port):
    ck, _ = _keys()
    verifier = secrets.token_urlsafe(48)[:64]
    # TikTok desktop PKCE: code_challenge is the hex SHA-256 of the verifier
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    state = secrets.token_urlsafe(16)
    _pending[state] = verifier
    q = {"client_key": ck, "scope": scopes(), "response_type": "code",
         "redirect_uri": redirect_uri(port), "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def handle_callback(params, port):
    if params.get("error"):
        raise RuntimeError(params.get("error_description") or params["error"])
    state = params.get("state", "")
    verifier = _pending.pop(state, None)
    if not verifier:
        raise RuntimeError("Login expired. Press Connect again.")
    ck, cs = _keys()
    tok = _post_form(API + "/oauth/token/", {
        "client_key": ck, "client_secret": cs, "code": params.get("code", ""),
        "grant_type": "authorization_code", "redirect_uri": redirect_uri(port),
        "code_verifier": verifier})
    if "access_token" not in tok:
        raise RuntimeError(tok.get("error_description") or json.dumps(tok))
    tok["obtained_at"] = time.time()
    with open(_token_path(), "w", encoding="utf-8") as f:
        json.dump(tok, f)
    return tok


def _token():
    try:
        with open(_token_path(), "r", encoding="utf-8") as f:
            tok = json.load(f)
    except Exception:
        return None
    if time.time() > tok.get("obtained_at", 0) + tok.get("expires_in", 0) - 300:
        ck, cs = _keys()
        new = _post_form(API + "/oauth/token/", {
            "client_key": ck, "client_secret": cs, "grant_type": "refresh_token",
            "refresh_token": tok.get("refresh_token", "")})
        if "access_token" not in new:
            return None
        new["obtained_at"] = time.time()
        with open(_token_path(), "w", encoding="utf-8") as f:
            json.dump(new, f)
        tok = new
    return tok["access_token"]


def logout():
    try:
        os.remove(_token_path())
    except OSError:
        pass


def _api(method, path, body=None, token=None):
    token = token or _token()
    if not token:
        raise RuntimeError("Connect your TikTok account first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        resp = json.loads(e.read().decode() or "{}")
    err = resp.get("error", {})
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok: {err.get('message') or err.get('code')}")
    return resp.get("data", {})


def account():
    if not _token():
        return {"connected": False}
    try:
        u = _api("GET", "/user/info/?fields=open_id,display_name,avatar_url").get("user", {})
        return {"connected": True, "display_name": u.get("display_name"), "avatar": u.get("avatar_url")}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def creator_info():
    return _api("POST", "/post/publish/creator_info/query/", {})


def _chunks(size):
    MB = 1024 * 1024
    if size <= 64 * MB:
        return size, 1
    chunk = 10 * MB
    return chunk, size // chunk  # last chunk absorbs the remainder


def _upload(upload_url, path, chunk, count):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            data = f.read(end - start + 1)
            req = urllib.request.Request(upload_url, data=data, method="PUT", headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            with urllib.request.urlopen(req, timeout=300) as r:
                r.read()


def send_to_drafts(path):
    size = os.path.getsize(path)
    chunk, count = _chunks(size)
    d = _api("POST", "/post/publish/inbox/video/init/", {
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": chunk, "total_chunk_count": count}})
    _upload(d["upload_url"], path, chunk, count)
    return d["publish_id"]


def direct_post(path, title, privacy="SELF_ONLY", allow_comment=True, allow_duet=True,
                allow_stitch=True, cover_ms=1000):
    info = creator_info()
    allowed = info.get("privacy_level_options") or ["SELF_ONLY"]
    if privacy not in allowed:
        privacy = "SELF_ONLY" if "SELF_ONLY" in allowed else allowed[0]
    size = os.path.getsize(path)
    chunk, count = _chunks(size)
    d = _api("POST", "/post/publish/video/init/", {
        "post_info": {"title": title[:2200], "privacy_level": privacy,
                      "disable_comment": not allow_comment or info.get("comment_disabled", False),
                      "disable_duet": not allow_duet or info.get("duet_disabled", False),
                      "disable_stitch": not allow_stitch or info.get("stitch_disabled", False),
                      "video_cover_timestamp_ms": int(cover_ms)},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": chunk, "total_chunk_count": count}})
    _upload(d["upload_url"], path, chunk, count)
    return d["publish_id"]


def status(publish_id):
    return _api("POST", "/post/publish/status/fetch/", {"publish_id": publish_id})


def video_list(limit=100):
    """Your most recent posted videos with their view counts (needs the video.list scope)."""
    out, cursor = [], None
    fields = "id,title,video_description,create_time,view_count,like_count,comment_count,share_count"
    while len(out) < limit:
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        try:
            d = _api("POST", "/video/list/?fields=" + fields, body)
        except RuntimeError as e:
            if "scope" in str(e).lower():
                raise RuntimeError("TikTok hasn't given Kliper permission to read your views yet. "
                                   "Add the 'video.list' scope to your TikTok developer app, then reconnect TikTok.")
            raise
        out += d.get("videos") or []
        cursor = d.get("cursor")
        if not d.get("has_more") or not cursor:
            break
    return out[:limit]

