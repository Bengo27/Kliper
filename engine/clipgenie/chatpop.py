"""2.20: chat pop-ups. When the streamer talks to chat ("chat", "you guys", "y'all"...), the real chat messages
typed around that moment pop up over the video, with an animation, the way the stream showed them.

Times here are STREAM seconds. render.prepare maps them into the TikTok; captions.chat_lines draws them."""
import json
import os
import re

from .chat import is_spam

TALK_TO_CHAT = re.compile(r"^(chat|chatters?|chatt?|y'?all|guys|everyone|everybody)$", re.I)
PALETTE = ["#ff4f8b", "#5ec8ff", "#ffd23f", "#3dff4a", "#a37bff", "#ff8a3d", "#00e5ff"]


def _users(cache):
    p = os.path.join(cache, "chat_users.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return {(round(float(t), 2), txt): (name, color) for t, txt, name, color in json.load(f)}
    except Exception:
        return {}


def talk_moments(words, t0, t1):
    """Stream times inside [t0, t1] where the streamer (not someone else) addresses chat."""
    out = []
    for i, w in enumerate(words):
        if not (t0 <= w["start"] <= t1) or w.get("other"):
            continue
        tok = re.sub(r"[^\w']", "", w["text"]).lower()
        if TALK_TO_CHAT.match(tok):
            prev = re.sub(r"[^\w']", "", words[i - 1]["text"]).lower() if i else ""
            if tok in ("guys",) and prev not in ("you", "ya"):
                continue   # "you guys" only, not "those guys"
            if not out or w["start"] - out[-1] > 6:
                out.append(w["start"])
    return out


_STOP = set("a an the and or but so to of in on at it is i you he she we they me my your this that was be are "
             "for with just like what no yes yeah ok okay bro lol lmao im its dont do did not oh".split())


def _content(text):
    return [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2 and w not in _STOP]


def replied_messages(words, msgs, t0, t1, lookback=25.0):
    """2.21 context: chat messages the streamer READS OUT or ANSWERS inside [t0, t1].
    A message counts when most of its content words are said by the streamer within ~15 s after it was typed.
    Returns [(said_at, typed_at, text)] best first; said_at = when the streamer starts responding."""
    mine = [w for w in words if not w.get("other") and t0 - 1 <= w["start"] <= t1 + 1]
    out = []
    for t, x in msgs:
        if not (t0 - lookback <= t <= t1) or is_spam(x):
            continue
        cw = _content(x)
        if len(cw) < 2:
            continue
        said = [w for w in mine if t <= w["start"] <= t + 15]
        if not said:
            continue
        spoken = {re.sub(r"[^a-z0-9']", "", w["text"].lower()): w["start"] for w in said}
        hits = [spoken[c] for c in cw if c in spoken]
        if len(hits) >= 2 and len(hits) / len(cw) >= 0.5:
            out.append((min(hits), float(t), x.strip(), len(hits) / len(cw)))
    out.sort(key=lambda r: -r[3])
    return [(a, b, c) for a, b, c, _ in out]


def popups_for(clip, words, msgs, cache, mode="auto", max_per=3, max_total=8):
    """[{at, text, name, color, dur}]: at most ONE chat message for the clip, the one that explains it.
    mode: 'auto' = only when the streamer reads out / answers chat or talks to chat, 'always' = also pick the
    best message around the peak, 'off' = none."""
    if mode == "off" or not msgs:
        return []
    st, en = float(clip["start"]), float(clip["end"])
    names = _users(cache)
    out, used = [], set()
    # 1st: the messages the streamer reads out / answers. They explain the moment, so they always go in,
    # shown just BEFORE he responds (even if he typed-time was before the clip starts).
    seen_txt = set()
    for said_at, typed_at, x in replied_messages(words, msgs, st, en):
        show = max(st + 0.2, said_at - 0.8)
        key = " ".join(_content(x))
        if show > en - 1.0 or (typed_at, x) in used or key in seen_txt:
            continue   # the same message spammed twice only pops up once
        used.add((typed_at, x))
        seen_txt.add(key)
        name, color = names.get((round(typed_at, 2), x), ("", ""))
        out.append({"at": round(show, 2), "text": x, "name": name, "context": True,
                    "color": color or PALETTE[sum(map(ord, name or x)) % len(PALETTE)]})
    # 2.21 (the creator): ONE message per clip, the one that gives the clip its context. Not a moving chat feed.
    if out:
        best = out[0]                              # the message he reads out / answers (best word match)
    else:
        # no clear read-out: if he talks to chat, take the most "readable" message typed just before
        # (a question or a real sentence beats "W" / "KEKW"); 'always' also looks around the clip's peak
        moments = talk_moments(words, st, en)
        if not moments and mode == "always":
            moments = [float(clip.get("peak", (st + en) / 2))]
        best, best_score = None, 0.0
        for m in moments:
            for t, x in msgs:
                if not (m - 12 <= t <= m + 2) or is_spam(x):
                    continue
                cw = _content(x)
                score = len(cw) + (2 if "?" in x else 0) - (0 if len(x) <= 70 else 3)
                if len(cw) >= 2 and score > best_score:
                    name, color = names.get((round(float(t), 2), x), ("", ""))
                    best_score = score
                    best = {"at": round(max(st + 0.2, m - 0.8), 2), "text": x.strip(), "name": name,
                            "color": color or PALETTE[sum(map(ord, name or x)) % len(PALETTE)]}
        if not best:
            return []
    best["dur"] = 4.8   # stays up while he responds
    return [best] if best["at"] <= en - 1.5 else []
