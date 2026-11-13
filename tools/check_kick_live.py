"""CI check for Kliper's live watcher: Kick channel info (live or not, chatroom id), the newest VOD, and the chat
socket (subscribe + wait up to 40 s for messages). usage: python check_kick_live.py <channel slug>"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
from clipgenie import live  # noqa: E402

slug = sys.argv[1]
d = live.channel_info(slug)
ls = d.get("livestream")
print("CHANNEL OK:", slug, "| chatroom", (d.get("chatroom") or {}).get("id"), "| live", bool(ls),
      "| start", (ls or {}).get("start_time"))
if ls:
    print("  live start (unix):", live._parse_time(ls.get("start_time") or ls.get("created_at")))
vods = None
try:
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
        pl = ydl.extract_info(f"https://kick.com/{slug}/videos", download=False)
    e = next(iter(pl.get("entries") or []), None)
    print("NEWEST VOD:", e and (e.get("url"), e.get("timestamp"), e.get("is_live")))
    if e and e.get("timestamp"):
        print("  find_vod ->", live.find_vod(slug, e["timestamp"] + 30))
except Exception as ex:
    print("VOD LIST FAILED:", ex)

path = os.path.join(os.environ.get("TEMP", "/tmp"), "kliper_chat_test.jsonl")
rec = live.ChatRecorder((d.get("chatroom") or {}).get("id"), path)
rec.start()
time.sleep(40)
rec.stop()
print("CHAT MESSAGES IN 40s:", rec.count)
if os.path.isfile(path):
    with open(path, encoding="utf-8") as f:
        for line in list(f)[:3]:
            print("  ", json.loads(line))
