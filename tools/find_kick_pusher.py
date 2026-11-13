"""Probe how Kick's live chat can be read: (1) the old Pusher key on every cluster, (2) Kick's new viewer socket
with a few subscribe message shapes. Uses a channel that is live right now. usage: python find_kick_pusher.py"""
import json
import time
from curl_cffi import requests
from websockets.sync.client import connect

S = requests.Session(impersonate="chrome")
room, slug = None, None
for u in ("https://web.kick.com/api/v1/livestreams?limit=10&sort=viewer_count_desc",
          "https://kick.com/stream/livestreams/en?page=1&limit=10&sort=desc",
          "https://kick.com/api/v1/featured-livestreams/en"):
    try:
        r = S.get(u, timeout=20)
        print("list", u, r.status_code, r.text[:300].replace("\n", " "))
        d = r.json()
        items = d.get("data") if isinstance(d, dict) else d
        items = items.get("livestreams") if isinstance(items, dict) and "livestreams" in items else items
        for it in items or []:
            ch = it.get("channel") or {}
            s = ch.get("slug") or it.get("slug") or (it.get("channel_slug"))
            if s:
                c = S.get(f"https://kick.com/api/v2/channels/{s}", timeout=20).json()
                if c.get("livestream"):
                    room, slug = c["chatroom"]["id"], s
                    break
    except Exception as e:
        print("list fail", u, e)
    if room:
        break
print("LIVE channel", slug, "chatroom", room)

KEY = "32cbd69e4b950bf97679"
for cl in ("us2", "mt1", "us3", "eu", "ap1", "ap2", "ap3", "ap4", "sa1"):
    try:
        with connect(f"wss://ws-{cl}.pusher.com/app/{KEY}?protocol=7&client=js&version=8.4.0&flash=false", open_timeout=10) as ws:
            first = ws.recv(timeout=8)
            print("PUSHER", cl, first[:160])
            if "connection_established" in first and room:
                ws.send(json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
                t0 = time.time()
                while time.time() - t0 < 20:
                    try:
                        print("  PFRAME", ws.recv(timeout=10)[:300])
                    except TimeoutError:
                        break
    except Exception as e:
        print("PUSHER", cl, "fail", str(e)[:160])

CLIENT = "e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823"
tok = S.get("https://websockets.kick.com/viewer/v1/token", headers={"X-CLIENT-TOKEN": CLIENT}, timeout=20).json()["data"]["token"]
chan = f"chatrooms.{room}.v2"
tries = [{"type": "subscribe", "data": {"channel": chan}}, {"type": "subscribe", "data": {"channels": [chan]}},
         {"type": "channel_handshake", "data": {"message": {"channelId": room}}},
         {"type": "subscribe", "data": {"channel": f"chatroom_{room}"}}, {"type": "ping"}]
with connect(f"wss://websockets.kick.com/viewer/v1/connect?token={tok}", open_timeout=15,
             additional_headers={"Origin": "https://kick.com"}) as ws:
    for m in tries:
        ws.send(json.dumps(m))
    t0, n = time.time(), 0
    while time.time() - t0 < 30 and n < 12:
        try:
            print("VFRAME", str(ws.recv(timeout=10))[:400]); n += 1
        except TimeoutError:
            print("(viewer socket quiet 10s)")
print("done")
