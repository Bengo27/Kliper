"""Twitch chat replay parsing and chat-spike curve.

Supported files:
  * TwitchDownloader chat JSON (recommended, free: github.com/lay295/TwitchDownloader)
  * Plain text logs with [h:mm:ss] or h:mm:ss timestamps at the start of each line
  * CSV with a seconds or h:mm:ss column first
"""
import json
import re
import numpy as np

HYPE = {
    # emotes / words viewers spam when something happens
    "kekw": 1.5, "lul": 1.3, "lol": 1.0, "lmao": 1.3, "lmfao": 1.4, "omegalul": 1.6,
    "pog": 1.4, "poggers": 1.5, "pogchamp": 1.5, "pogu": 1.5, "w": 1.2, "l": 1.0,
    "monkas": 1.3, "omg": 1.3, "wtf": 1.4, "bruh": 1.1, "noway": 1.4, "no": 0.4,
    "ez": 1.0, "clutch": 1.5, "insane": 1.4, "holy": 1.2, "sheesh": 1.2,
    "dead": 1.1, "💀": 1.4, "😂": 1.2, "🤣": 1.2, "aware": 0.8, "copium": 1.0,
    "ratio": 0.8, "goated": 1.3, "cracked": 1.3, "scream": 1.2, "jumpscare": 1.5,
}
CLIP_WORDS = re.compile(r"\bclip\s*(it|that|this)?\b|\bclipped\b", re.I)
TS = re.compile(r"^\s*\[?(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\]?")


def _to_sec(h, m, s):
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def load_chat(path):
    """Returns list of (seconds, message_text)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    raw_s = raw.lstrip()
    msgs = []
    if raw_s.startswith("{") or raw_s.startswith("["):
        data = json.loads(raw_s)
        comments = data.get("comments", data) if isinstance(data, dict) else data
        for c in comments:
            t = c.get("content_offset_seconds")
            if t is None:
                t = c.get("time") or c.get("offset") or 0
            msg = c.get("message", "")
            if isinstance(msg, dict):
                msg = msg.get("body", "")
            msgs.append((float(t), str(msg)))
        return msgs
    for line in raw.splitlines():
        if "," in line[:14] and line.split(",", 1)[0].replace(".", "").isdigit():
            t, rest = line.split(",", 1)
            msgs.append((float(t), rest))
            continue
        m = TS.match(line)
        if m:
            t = _to_sec(m.group(1), m.group(2), m.group(3))
            rest = line[m.end():]
            # strip "user:" prefix if present
            if ":" in rest[:40]:
                rest = rest.split(":", 1)[1]
            msgs.append((float(t), rest.strip()))
    return msgs


def message_weight(text):
    t = text.lower()
    w = 1.0
    if CLIP_WORDS.search(t):
        w += 3.0
    toks = re.findall(r"[\w']+|[^\w\s]", t)
    hype = 0.0
    for tok in toks:
        hype = max(hype, HYPE.get(tok, 0.0))
    if "no way" in t or "noway" in t:
        hype = max(hype, 1.4)
    w += hype
    if text.isupper() and len(text) > 3:
        w += 0.5  # caps = excitement
    if text.count("!") >= 2 or text.count("?") >= 3:
        w += 0.3
    return w


def chat_curve(msgs, duration, hop=0.5, lag=6.0):
    """Weighted message rate -> z-score above an EWMA baseline.
    `lag` shifts the curve earlier: chat reacts a few seconds after the moment."""
    n = int(duration / hop) + 1
    rate = np.zeros(n, dtype=np.float32)
    for t, text in msgs:
        i = int((t - lag) / hop)
        if 0 <= i < n:
            rate[i] += message_weight(text)
    # smooth over ~5 s
    k = max(1, int(5 / hop))
    kern = np.ones(k, dtype=np.float32) / k
    sm = np.convolve(rate, kern, mode="same")
    # EWMA baseline + variance (as used by Twitch highlight bots)
    alpha = hop / 120.0
    mean = np.zeros(n, dtype=np.float32)
    var = np.zeros(n, dtype=np.float32)
    m, v = float(sm[: int(60 / hop)].mean() if n > 10 else sm.mean()), 1.0
    for i in range(n):
        x = float(sm[i])
        mean[i], var[i] = m, v
        d = x - m
        m += alpha * d
        v = (1 - alpha) * (v + alpha * d * d)
    z = (sm - mean) / np.sqrt(var + 1e-3)
    return np.clip(z, -3, 12).astype(np.float32)


# ---------- smarter chat reading (v2.2) ----------
# Research: chat reacts 5-20 s after the moment; what matters is velocity vs a rolling
# baseline, and WHICH emotes are spammed (laugh emotes = funny clip, pog emotes = hype clip).
FUNNY_TOK = {"kekw", "lul", "lol", "lmao", "lmfao", "omegalul", "icant", "kekl", "pepelaugh", "dead",
             "💀", "😂", "🤣", "😭", "xd", "haha", "hahaha", "lool", "looool", "bruh", "nah",
             "crying", "weirdchamp", "monkaw", "ratio", "bozo", "aware", "clueless", "sus"}
HYPE_TOK = {"pog", "poggers", "pogchamp", "pogu", "w", "ww", "www", "clutch", "insane", "holy",
            "sheesh", "ez", "goated", "cracked", "noway", "omg", "wtf", "monkas", "gg", "nice"}
SPAM_RX = re.compile(
    r"^\s*!|is now live|subscribed (with|at)|gifted (a|\d+)|is gifting|raid(ing|ed)? |"
    r"has raided|just followed|thank(s| you) for (the )?(follow|sub|raid|bits)|cheer\d+|"
    r"sery_?bot|streamelements|nightbot|moobot|fossabot|streamlabs|https?://|"
    r"\b(lag(ging)?|buffer(ing)?|stream (is )?(down|frozen)|audio (is )?(off|out))\b", re.I)


def is_spam(text):
    return bool(SPAM_RX.search(text or ""))


def message_kind(text):
    """'funny', 'hype' or None, from the emotes/words in one chat message."""
    t = (text or "").lower()
    toks = set(re.findall(r"[\w']+|[^\w\s]", t))
    toks |= {x for x in re.findall(r"\b(?:ha){2,}h?\b|\blo+l\b|\bl+m+a+o+\b", t)}
    if any(re.fullmatch(r"(?:ha){2,}h?|lo+l|l+m+a+o+", x) for x in toks):
        toks.add("lol")
    f = len(toks & FUNNY_TOK)
    h = len(toks & HYPE_TOK) + (1 if "no way" in t else 0)
    if f > h:
        return "funny"
    if h > f:
        return "hype"
    return "funny" if f else None


def chat_signals(msgs, duration, hop=0.5, lag=6.0):
    """Spam-filtered chat curves: (spike_z, funny_curve, hype_curve, kept_msgs).
    funny/hype curves = weighted emote counts, smoothed, same length as spike_z."""
    kept = [(t, m) for t, m in msgs if not is_spam(m)]
    n = int(duration / hop) + 1
    funny = np.zeros(n, dtype=np.float32)
    hype = np.zeros(n, dtype=np.float32)
    for t, m in kept:
        i = int((t - lag) / hop)
        if not 0 <= i < n:
            continue
        k = message_kind(m)
        if k == "funny":
            funny[i] += 1.0
        elif k == "hype":
            hype[i] += 1.0
    k = max(1, int(10 / hop))  # reactions trickle in over ~10 s
    kern = np.ones(k, dtype=np.float32)
    funny = np.convolve(funny, kern, mode="same")
    hype = np.convolve(hype, kern, mode="same")
    z = chat_curve(kept, duration, hop=hop, lag=lag) if kept else np.zeros(n, dtype=np.float32)
    return z, funny, hype, kept
