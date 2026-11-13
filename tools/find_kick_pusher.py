"""Probe Kick's chat websocket (Centrifugo at websockets.kick.com since late 2026): get a viewer token, connect,
subscribe to a live channel's chatroom and print the raw frames. usage: python find_kick_pusher.py"""
import json
import time
from curl_cffi import requests
from websockets.sync.client import connect

S = requests.Session(impersonate="chrome")
CLIENT = "e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823"
slugs = ["xqc", "adinross", "westcol", "spursito", "coscu", "elzeein", "roshtein", "trainwreckstv", "amouranth",
         "kaicenat", "elmariana", "zonagemelosoficial", "suzyroxx", "ac7ionman", "jynxzi"]
room, slug = None, None
for s in slugs:
    try:
        d = S.get(f"https://kick.com/api/v2/channels/{s}", timeout=20).json()
    except Exception as e:
        print("chan fail", s, e); continue
    if d.get("livestream"):
        room, slug = d["chatroom"]["id"], s
        break
    room = room or d.get("chatroom", {}).get("id"); slug = slug or s
print("channel", slug, "chatroom", room)
r = S.get("https://websockets.kick.com/viewer/v1/token", headers={"X-CLIENT-TOKEN": CLIENT}, timeout=20)
print("token status", r.status_code, r.text[:300])
tok = (r.json().get("data") or {}).get("token")
url = f"wss://websockets.kick.com/viewer/v1/connect?token={tok}"
with connect(url, open_timeout=15, additional_headers={"Origin": "https://kick.com"}) as ws:
    ws.send(json.dumps({"id": 1, "connect": {"name": "js"}}))
    ws.send(json.dumps({"id": 2, "subscribe": {"channel": f"chatrooms.{room}.v2"}}))
    t0, n = time.time(), 0
    while time.time() - t0 < 45 and n < 14:
        try:
            raw = ws.recv(timeout=10)
        except TimeoutError:
            print("(no frame 10s)"); continue
        for part in str(raw).split("\n"):
            if part.strip() in ("{}", ""):
                ws.send("{}")   # centrifugo ping -> pong
                continue
            n += 1
            print("FRAME", part[:700])
print("done ws test")
import re
html = S.get("https://kick.com/xqc", timeout=30).text
PAT = re.compile(r"PUSHER_[A-Z_]+|wsHost|cluster:|new [A-Za-z$_]{1,3}\([a-zA-Z.$_]*PUSHER|subscribe\(|sendMessage\(\"|type:\"[a-z_]*subscribe|\"[a-z_.]*(?:subscribe|chatroom)[a-z_.]*\"")
seen = set()
for src in sorted(set(re.findall(r'src="([^"]+\.js[^"]*)"', html))):
    url = src if src.startswith("http") else "https://kick.com" + src
    js = S.get(url, timeout=30).text
    for m in PAT.finditer(js):
        ctx = js[max(0, m.start() - 200): m.end() + 260].replace("\n", " ")
        k = m.group(0) + ctx[150:260]
        if k in seen or len(seen) > 70:
            continue
        seen.add(k)
        print("-- [%s] %s: %s" % (url.split("/")[-1][:20], m.group(0), ctx))
print("done")
