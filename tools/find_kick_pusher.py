"""Find the Pusher (chat websocket) app key + cluster that kick.com's own website uses right now, by reading its
JavaScript. usage: python find_kick_pusher.py   (needs curl_cffi; Kick is behind Cloudflare)"""
import re
from curl_cffi import requests

S = requests.Session(impersonate="chrome")
html = S.get("https://kick.com/", timeout=30).text
srcs = sorted(set(re.findall(r'src="([^"]+\.js[^"]*)"', html)))
print("page", len(html), "scripts", len(srcs))
seen = set()
for src in srcs:
    url = src if src.startswith("http") else "https://kick.com" + src
    try:
        js = S.get(url, timeout=30).text
    except Exception as e:
        print("skip", url, e)
        continue
    for m in re.finditer(r"pusher|wsHost|cluster|ws-[a-z0-9]+\.pusher\.com|wss://[\w.\-/]+", js, re.I):
        ctx = js[max(0, m.start() - 160): m.end() + 160].replace("\n", " ")
        keys = re.findall(r'["\']([0-9a-f]{20})["\']', ctx)
        hosts = re.findall(r'wss?://[\w.\-]+', ctx) + re.findall(r'ws-[a-z0-9]+\.pusher\.com', ctx)
        clus = re.findall(r'cluster\W{1,4}["\']([a-z0-9]{2,5})["\']', ctx)
        sig = (tuple(keys), tuple(hosts), tuple(clus))
        if (keys or hosts or clus) and sig not in seen:
            seen.add(sig)
            print("FOUND in", url.split("/")[-1][:60], "| keys", keys, "| hosts", hosts, "| cluster", clus)
            print("   ", ctx[:330])
print("done")
