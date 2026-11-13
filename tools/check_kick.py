"""CI check: can yt-dlp read a Kick VOD? Prints what it found or the error. usage: python check_kick.py <kick vod url>"""
import sys
import traceback

import yt_dlp

url = sys.argv[1]
try:
    with yt_dlp.YoutubeDL({"skip_download": True, "verbose": False}) as ydl:
        d = ydl.extract_info(url, download=False)
    print("KICK OK:", d.get("title"), "| duration", d.get("duration"), "| uploader", d.get("uploader"),
          "| formats", len(d.get("formats") or []))
except Exception:
    traceback.print_exc()
    print("KICK FAILED")
    sys.exit(1)
