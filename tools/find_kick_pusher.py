"""Find how kick.com's chat connects right now (websocket URL, token, subscribe messages), by reading the JavaScript
of a channel page. usage: python find_kick_pusher.py   (needs curl_cffi; Kick is behind Cloudflare)"""
import re
from curl_cffi import requests

S = requests.Session(impersonate="chrome")
PAT = re.compile(r"WEBSOCKET_CONNECTION_URL|WEBSOCKET_CLIENT_TOKEN|viewer/v1|chatrooms\.|ChatMessage|pusher:subscribe|"
                 r"\"subscribe\"|channel_subscribe|chat\.message|ws-us2", re.I)
done, shown = set(), 0
for page in ("https://kick.com/xqc", "https://kick.com/xqc/videos"):
    html = S.get(page, timeout=30).text
    srcs = sorted(set(re.findall(r'src="([^"]+\.js[^"]*)"', html)))
    print("page", page, len(html), "scripts", len(srcs))
    for src in srcs:
        url = src if src.startswith("http") else "https://kick.com" + src
        if url in done:
            continue
        done.add(url)
        try:
            js = S.get(url, timeout=30).text
        except Exception as e:
            print("skip", url, e)
            continue
        last = -10 ** 9
        for m in PAT.finditer(js):
            if m.start() - last < 1500 or shown > 40:
                continue
            last = m.start()
            shown += 1
            print("=== %s in %s" % (m.group(0), url.split("/")[-1][:50]))
            print(js[max(0, m.start() - 700): m.end() + 900].replace("\n", " "))
print("done")
