"""Animated TikTok-style captions as ASS subtitles (burned in by ffmpeg/libass)."""
import re

W, H = 1080, 1920

FONTS = {
    "Montserrat ExtraBold": {"size": 96, "spacing": 0},
    "Anton": {"size": 120, "spacing": 1},
    "Bebas Neue": {"size": 140, "spacing": 2},
    "Archivo Black": {"size": 96, "spacing": 0},
    "Luckiest Guy": {"size": 108, "spacing": 1},
}

STYLES = ["editor", "pop", "bounce", "karaoke", "shake", "glow", "clean"]

# ---- "editor" style: the look of @hotgamerdude's edited TikToks ----
# lowercase phrases (1-4 words) that pop in, white Montserrat ExtraBold with a dark
# outline/shadow, hype words and sound effects (*laughs*) in green, swearing censored.
import re as _re
from .scoring import SPOKEN as _SPOKEN

SWEARS = {"fuck": "f*ck", "fucking": "f*cking", "fucked": "f*cked", "shit": "sh*t", "shitty": "sh*tty",
          "bitch": "b*tch", "bitches": "b*tches", "nigga": "n*gga", "niggas": "n*ggas", "dick": "d*ck",
          "pussy": "p*ssy", "cunt": "c*nt", "motherfucker": "motherf*cker", "bullshit": "bullsh*t", "ass": "a*s"}
_LAUGH = _re.compile(r"^(ha){2,}h?|^(he){2,}|^lol+$|^lmao+$", _re.I)
_SOUND = _re.compile(r"^[\[(*](laugh\w*|scream\w*|sigh\w*|gasp\w*|music|cough\w*|groan\w*|yell\w*)[\])*][.!?,]*$", _re.I)


def _censor(word):
    core = _re.sub(r"[^\w']", "", word).lower()
    if core in SWEARS:
        return word.lower().replace(core, SWEARS[core])
    return word


def _editor_text(w):
    t = w["text"].strip()
    m = _SOUND.match(t)
    if m:
        return "*" + m.group(1).lower().rstrip("ing").replace("laugh", "laughs") + "*", True
    if _LAUGH.match(_re.sub(r"[^\w]", "", t)):
        return "*laughs*", True
    return _censor(t), False


def hype_flags(words):
    """Mark words that are part of a hype phrase ("no way", "let's go", "what the f*ck"...)."""
    texts = [_re.sub(r"[^\w'*]", "", w["text"].lower()) for w in words]
    flags = [w["text"].endswith("!") and len(texts[i]) > 1 for i, w in enumerate(words)]
    joined, starts, pos = "", [], 0
    for t in texts:
        starts.append(len(joined))
        joined += t.replace("*", "u") + " "
    for rx, wgt in _SPOKEN:
        if wgt < 1.4:
            continue
        for m in rx.finditer(joined):
            for i, st in enumerate(starts):
                if m.start() <= st < m.end():
                    flags[i] = True
    # a swear word right after a hype phrase belongs to it ("what the f*ck")
    for i in range(1, len(flags)):
        if not flags[i] and flags[i - 1] and texts[i].replace("*", "u") in SWEARS:
            flags[i] = True
    return flags


def _phrase_starts(words):
    texts = " ".join(_re.sub(r"[^\w'*]", "", w["text"].lower()).replace("*", "u") for w in words)
    starts, pos = {}, 0
    idx_at = []
    for i, w in enumerate(words):
        idx_at.append(pos)
        pos += len(_re.sub(r"[^\w'*]", "", w["text"].lower())) + 1
    out = set()
    for rx, wgt in _SPOKEN:
        if wgt < 1.4:
            continue
        for m in rx.finditer(texts):
            for i, p in enumerate(idx_at):
                if p == m.start():
                    out.add(i)
    return out


OTHER_COLOR = "#5ec8ff"   # subtitles for someone else talking (teammates, TTS, calls)

# Scripts written without spaces between words (Chinese, Japanese, Thai, Lao, Khmer, Myanmar): Whisper gives
# their words without spaces, so captions join them with nothing in between.
_NOSPACE = _re.compile("[\u0E00-\u0EFF\u1000-\u109F\u1780-\u17FF\u3000-\u30FF\u31F0-\u31FF"
                       "\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]")
_TAGS = _re.compile(r"\{[^}]*\}")


def join_words(parts):
    """Join caption words: a space between them, except next to a no-space script (Japanese, Chinese, Thai...)."""
    out = ""
    for p in parts:
        if out:
            a, b = _TAGS.sub("", out)[-1:], _TAGS.sub("", p)[:1]
            if not (_NOSPACE.match(a) or _NOSPACE.match(b)):
                out += " "
        out += p
    return out


def vis_len(text):
    """Caption length in 'Latin letters': CJK characters are about two wide, combining marks take no room."""
    import unicodedata
    n = 0.0
    for ch in text:
        if unicodedata.combining(ch) or unicodedata.category(ch) == "Mn":
            continue
        n += 2.4 if unicodedata.east_asian_width(ch) in ("W", "F") else 1.0
    return n


def editor_chunks(words, max_words=4, max_chars=20):
    """Phrase-based chunks like a human editor: break on punctuation, pauses and length,
    and keep hype phrases together."""
    chunks, cur = [], []
    flags = hype_flags(words)
    phrase_starts = _phrase_starts(words)
    for i, w in enumerate(words):
        txt, sound = _editor_text(w)
        item = dict(w, text=txt, sound=sound, hype=flags[i])
        if sound:
            if cur:
                chunks.append(cur)
            chunks.append([item])
            cur = []
            continue
        if cur:
            gap = w["start"] - cur[-1]["end"]
            chars = vis_len(join_words([x["text"] for x in cur] + [txt]))
            nospace = bool(_NOSPACE.search(txt[:1] + cur[-1]["text"][-1:]))   # Japanese words are often 1-2 characters
            hype_edge = (cur[-1].get("hype") != item["hype"] and len(cur) >= 2) or \
                (item["hype"] and cur[-1].get("hype") and i in phrase_starts)
            if ((len(cur) >= max_words and not nospace) or chars > max_chars or gap > 0.45 or hype_edge
                    or bool(cur[-1].get("other")) != bool(item.get("other"))   # new speaker = new caption
                    or _re.search(r"[.!?,。！？、]$", cur[-1]["text"])):
                chunks.append(cur)
                cur = []
        cur.append(item)
    if cur:
        chunks.append(cur)
    # merge laughs that repeat
    out = []
    for c in chunks:
        if out and c[0].get("sound") and out[-1][0].get("sound") and c[0]["text"] == out[-1][0]["text"]:
            out[-1][-1]["end"] = c[-1]["end"]
            continue
        out.append(c)
    return out


def _key_line(ch):
    """2.24 "key lines only" subtitles (the dima_wallhacks look): keep a caption only when it carries the moment:
    a hype phrase, a short shout ("PLEASE!"), or a short question ("ENGLISH OR SPANISH?")."""
    if ch[0].get("sound"):
        return False
    phrase = " ".join(x["text"] for x in ch).strip()
    if any(x.get("hype") for x in ch):
        return True
    return len(ch) <= 3 and phrase.endswith(("!", "?"))


def _editor_events(words, opts):
    hl = ass_c(opts.get("highlight", "#3dff4a"))
    key = opts.get("subs_mode") == "key"
    lower = not opts.get("uppercase", False) and not key
    per = opts.get("words_per_line", 4)
    max_words = 6 if per in ("line", 0, None) else max(1, int(per))
    if key:
        max_words = min(max_words, 3)
    ev = []
    oc = ass_c(opts.get("other_color", OTHER_COLOR))
    for ch in editor_chunks(words, max_words=max_words):
        if key and not _key_line(ch):
            continue
        st = ch[0]["start"]
        en = max(ch[-1]["end"] + 0.12, st + 0.35)
        base = oc if (ch[0].get("other") and opts.get("color_others", True)) else "&HFFFFFF"
        if ch[0].get("sound"):
            txt = r"{\1c" + hl + "&}" + _esc(ch[0]["text"])
        else:
            phrase = " ".join(x["text"] for x in ch)
            short_excl = len(ch) <= 2 and phrase.endswith("!")
            parts = []
            for x in ch:
                t = _esc(x["text"].lower() if lower else x["text"].upper())
                if short_excl or x.get("hype"):
                    parts.append(r"{\1c" + hl + "&}" + t + r"{\1c" + base + "&}")
                else:
                    parts.append(t)
            txt = r"{\1c" + base + "&}" + join_words(parts)
        pop = r"{\fscx86\fscy86\t(0,70,\fscx106\fscy106)\t(70,140,\fscx100\fscy100)}"
        if ch[0].get("other") and opts.get("voice_tags") and not ch[0].get("sound"):
            # 2.22: someone else on voice chat: a small speaker icon + label above their words
            icon = r"{\p1}m 0 18 l 14 18 30 4 30 52 14 38 0 38 m 38 16 l 44 12 50 28 44 44 38 40 42 28{\p0}"
            label = (opts.get("voice_label") or "TEAMMATE").upper()
            pop = r"{\fscx52\fscy52\1c" + oc + r"&}" + icon + "  " + _esc(label) + r"\N{\fscx100\fscy100}"
        ev.append((st, en, pop + txt))
    return ev


def ass_color(hex_rgb, alpha=0):
    h = (hex_rgb or "#ffffff").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_c(hex_rgb):
    """Override-tag colour: &HBBGGRR (no alpha byte)."""
    return ass_color(hex_rgb)[4:].join(["&H", ""])


def _ts(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def group_words(words, per_line=3):
    """Split words into on-screen groups (1 word, 3 words, or a short line)."""
    if per_line in ("line", 0, None):
        per_line = 6
    per_line = int(per_line)
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            ends_sentence = re.search(r"[.!?]$", cur[-1]["text"]) is not None
            if len(cur) >= per_line or gap > 0.6 or (ends_sentence and len(cur) >= 2):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def _word_spans(group, style, hl, uppercase):
    """Yield (start, end, text_with_tags) — one event per active word."""
    out = []
    for j, w in enumerate(group):
        st = w["start"]
        en = group[j + 1]["start"] if j + 1 < len(group) else max(w["end"], w["start"] + 0.25)
        parts = []
        for k, ww in enumerate(group):
            t = _esc(ww["text"].upper() if uppercase else ww["text"])
            if k == j:
                parts.append(_active(style, hl, t))
            else:
                parts.append(_inactive(style, t))
        prefix = ""
        if j == 0 and style in ("pop", "bounce", "shake", "glow"):
            prefix = r"{\fscx72\fscy72\t(0,90,\fscx108\fscy108)\t(90,170,\fscx100\fscy100)}"
        out.append((st, en, prefix + join_words(parts)))
    return out


def _active(style, hl, t):
    c = ass_c(hl)
    if style == "pop":
        return r"{\1c" + c + r"&\fscx112\fscy112}" + t + r"{\1c&HFFFFFF&\fscx100\fscy100}"
    if style == "bounce":
        return (r"{\1c" + c + r"&\fscx100\fscy100\t(0,70,\fscx128\fscy128)\t(70,170,\fscx114\fscy114)}"
                + t + r"{\1c&HFFFFFF&\fscx100\fscy100}")
    if style == "karaoke":
        return r"{\1a&H00&\3c" + c + r"&\bord14\shad0}" + t + r"{\1a&H55&\3c&H000000&\bord6}"
    if style == "shake":
        return (r"{\1c" + c + r"&\frz-5\t(0,50,\frz5)\t(50,100,\frz-4)\t(100,150,\frz3)\t(150,200,\frz0)}"
                + t + r"{\1c&HFFFFFF&\frz0}")
    if style == "glow":
        return r"{\3c" + c + r"&\bord9\blur9}" + t + r"{\3c&H000000&\bord6\blur0}"
    # clean
    return r"{\1c" + c + r"&}" + t + r"{\1c&HFFFFFF&}"


def _inactive(style, t):
    if style == "karaoke":
        return r"{\1a&H55&}" + t
    return t


def _watermark_lines(name, opts, duration):
    """Twitch glitch logo + channel name, centred on the gameplay near the bottom."""
    show_logo = opts.get("watermark_logo", True) is not False      # 2.22: logo can be hidden / resized / nudged
    ls = max(0.2, min(3.0, float(opts.get("logo_size", 1.0) or 1.0)))
    # 2.35.4: the size slider scales logo AND name as one (the creator: "the sizing is all in 1")
    scale = float(opts.get("watermark_size", 1.0)) * ls
    fs = int(58 * scale)                      # name font size
    lh = int(72 * scale)                      # logo height
    lw = int(lh * 0.86) if show_logo else 0
    text_w = int(len(name) * fs * 0.47)       # Montserrat ExtraBold average width
    gap = int(16 * scale) if show_logo else 0
    total = lw + gap + text_w
    x0 = W // 2 - total // 2
    if opts.get("watermark_x") is not None:     # 2.18: name tag on the left (on the webcam/gameplay line)
        x0 = int(W * float(opts["watermark_x"]))
    cy = int(H * float(opts.get("watermark_y", 0.84)))
    # 2.35.2: the nudge moves logo AND name as one (the creator: "it needs to be one object")
    x0 += int(W * float(opts.get("logo_dx", 0) or 0))
    cy += int(H * float(opts.get("logo_dy", 0) or 0))
    y0 = cy - lh // 2
    ly0, lx0 = y0, x0
    ty = cy - lh // 2 + int(lh * 0.42)                               # name centred on the logo's body
    k = lh / 104.0
    def path(pts):
        cmd = "m %d %d" % (round(pts[0][0] * k), round(pts[0][1] * k))
        for x, y in pts[1:]:
            cmd += " l %d %d" % (round(x * k), round(y * k))
        return cmd
    outer = [(14, 0), (90, 0), (90, 56), (66, 80), (48, 80), (32, 96), (32, 80), (0, 80), (0, 14)]
    inner = [(10, 8), (82, 8), (82, 52), (62, 72), (44, 72), (32, 84), (32, 72), (10, 72)]
    eye1 = [(36, 22), (44, 22), (44, 46), (36, 46)]
    eye2 = [(58, 22), (66, 22), (66, 46), (58, 46)]
    # 0.1 (the creator): opacity of the logo + name, 0-100 (100 = solid)
    op = opts.get("watermark_opacity")
    op = max(0.0, min(100.0, float(100 if op is None else op)))
    alpha = r"\alpha&H%02X&" % round(255 * (1 - op / 100.0))
    base = r"{\an7\pos(%d,%d)\bord0\shad0\blur0.6" % (lx0, ly0) + alpha + r"\p1"
    t0, t1 = _ts(0), _ts(duration)
    out = [] if not show_logo else [
        f"Dialogue: 0,{t0},{t1},Cap,,0,0,0,," + base + r"\1c&HFF4191&}" + path(outer) + r"{\p0}",
        f"Dialogue: 0,{t0},{t1},Cap,,0,0,0,," + base + r"\1c&HFFFFFF&}" + path(inner) + r"{\p0}",
        f"Dialogue: 0,{t0},{t1},Cap,,0,0,0,," + base + r"\1c&HFF4191&}" + path(eye1) + " " + path(eye2) + r"{\p0}",
    ]
    out += [
        f"Dialogue: 0,{t0},{t1},Cap,,0,0,0,," +
        r"{\an4\pos(%d,%d)\fnMontserrat ExtraBold\fs%d\bord3\shad1\3c&H000000&\1c&HFFFFFF&%s}%s"
        % (x0 + lw + gap, ty, fs, alpha, _esc(name)),
    ]
    return out


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


TITLE_CAP_MID = 0.56   # where the middle of a CAPS line sits below its an8 (top-anchored) top, as a fraction of the font size


def _title_lines(text, opts, duration):
    """2.18: the TikTok's title on screen for the whole video (the ohnePixel-clip look). Words in *stars*
    get the highlight colour. Emoji are dropped: the subtitle renderer can't draw colour emoji."""
    text = re.sub(r"\s+", " ", _EMOJI.sub("", text or "")).strip()
    if not text:
        return []
    # 2.20 title styles: "plain" white with outline, "box" black text in a white box (TikTok sticker look, CoD),
    # "banner" the same in CAPS (Zelda), "quote" the line in quotes, big, in the middle (Fortnite)
    ts = opts.get("title_style") or "plain"
    if ts in ("banner", "caps"):   # "caps": clean CAPS text with outline, no box (the creator: no boxes)
        text = text.upper()
    if ts == "quote" and not text.startswith(("“", '"')):
        text = "“" + text.strip('"') + "”"
    boxed = ts in ("box", "banner")
    base_c = "&H111111" if boxed else "&HFFFFFF"
    hl = ass_c(opts.get("title_highlight") or opts.get("highlight", "#3dff4a"))
    if boxed:
        hl = ass_c(opts.get("title_box_highlight") or "#e8213a")   # yellow can't be read on white: red instead
    parts = re.split(r"(\*[^*]+\*)", text)
    body = ""
    for p in parts:
        if p.startswith("*") and p.endswith("*") and len(p) > 2:
            body += r"{\1c" + hl + "&}" + _esc(p[1:-1]) + r"{\1c" + base_c + "&}"
        else:
            body += _esc(p)
    size = int((80 if boxed else 96) * float(opts.get("title_size", 1.0)))
    y = int(H * float(opts.get("title_y", 0.07)))
    if boxed:   # opaque white box behind the text (padding = the border)
        tag = (r"{\an8\pos(%d,%d)\fnMontserrat ExtraBold\fs%d\bord14\shad0\3c&HFFFFFF&\1c" + base_c + r"&\q0}") % (W // 2, y, size)
        return [f"Dialogue: 2,{_ts(0)},{_ts(duration)},TitleBox,,90,90,0,," + tag + body]
    # 2.35: wrap the lines ourselves so they sit close together (the creator: "spacing too far"; the font's own line
    # height left a big gap between "NEW PHLOXIO" and "ALLEGATIONS"). Lines are balanced and each is drawn at
    # 0.9x the font size below the last.
    from .fontmetrics import text_width
    words, inside = [], False
    for w in text.split(" "):   # *star* highlights can span words: mark each word on its own
        st, en = w.startswith("*"), w.endswith("*") and len(w) > 1
        core = w.strip("*")
        hi = inside or st
        if st and not en:
            inside = True
        if en and not st:
            inside = False
        if core and _NOSPACE.search(core):
            words.extend((ch, hi) for ch in core)   # no-space scripts can wrap between any two characters
        elif core:
            words.append((core, hi))
    maxw = W - 140

    def width(ws, sz):
        t = join_words([c for c, _ in ws])
        return text_width(t, "Montserrat-ExtraBold.ttf", sz) or len(t) * sz * 0.62

    # 2.35 (the creator): at most TWO lines (a 3rd line covered the webcam). One line if it fits; else the most even
    # 2-line split; if even that is too wide, the title gets smaller until it fits (down to 55 %).
    full = size
    while True:
        if width(words, size) <= maxw or len(words) < 2:
            lines = [words]
        else:
            best = min(range(1, len(words)), key=lambda i: max(width(words[:i], size), width(words[i:], size)))
            lines = [words[:best], words[best:]]
        if max(width(ln, size) for ln in lines) <= maxw or size <= full * 0.55:
            break
        size = int(size * 0.92)
    step = size * 0.9
    if opts.get("title_seam"):
        # the webcam/gameplay line runs through the middle of a 1-line title, and through the gap of a 2-line one
        seam = float(opts["title_seam"]) * H
        y = int(seam - TITLE_CAP_MID * size - (len(lines) - 1) * step / 2)
    out = []
    for i, ln in enumerate(lines):
        b = join_words([(r"{\1c" + hl + "&}" + _esc(c) + r"{\1c" + base_c + "&}") if hi else _esc(c)
                        for c, hi in ln])
        tag = (r"{\an8\pos(%d,%d)\fnMontserrat ExtraBold\fs%d\bord6\shad2\3c&H000000&\1c&HFFFFFF&\q2}"
               % (W // 2, y + int(i * step), size))
        out.append(f"Dialogue: 2,{_ts(0)},{_ts(duration)},Title,,70,70,0,," + tag + b)
    return out


def _rrect(w, h, r):
    """ASS vector drawing of a rounded rectangle (smooth curves, drawn sharp at any size)."""
    r = max(1, min(r, w // 2, h // 2))
    k = r * 0.448   # bezier handle for a circular corner
    return (f"m {r} 0 l {w - r} 0 b {w - k:.1f} 0 {w} {k:.1f} {w} {r} l {w} {h - r} b {w} {h - k:.1f} {w - k:.1f} {h} {w - r} {h} "
            f"l {r} {h} b {k:.1f} {h} 0 {h - k:.1f} 0 {h - r} l 0 {r} b 0 {k:.1f} {k:.1f} 0 {r} 0")


# 2.21: Twitch-chat cards. style -> (card fill, card alpha, radius, accent bar)
CHAT_CARDS = {
    "twitch": ("#18181b", 0x12, 18, None),        # Twitch's own dark chat colour, nearly solid
    "grey": ("#18181b", 0x00, 30, None),          # the creator 11 Nov: exact Twitch dark-mode grey, curved edges, ONE line: name: message
    "glass": ("#0e0e10", 0x58, 22, "#9146ff"),    # see-through dark with a Twitch-purple bar
    "pill": ("#18181b", 0x10, 34, None),          # very round, name in a purple pill
    "clean": (None, 0, 0, None),                  # no box: white text with a black outline (the creator, 11 Nov 2026)
    "caption": (None, 0, 0, None),                # 2.35: just the message, white + outline, no name (dima-style CS2 TikToks)
}


NEW_CHAT_STYLES = ("bubble", "sticker", "neon", "banner", "stream")


def chat_lines_cards(pops, opts, duration, style, show=4.8):
    """2.21 prototypes: other chat designs. Name on its own line, message under it (wrapped to 2 lines).
    bubble = speech bubble with a tail; sticker = white tilted sticker with a name tag; neon = glowing purple
    outline; banner = full-width strip with a CHAT tag; stream = a real-looking Twitch chat panel."""
    from .fontmetrics import text_width
    out = []
    base = int(H * float(opts.get("chat_y", 0.3)))
    fs, nfs = 52, 34
    for p in pops[:1]:
        t0, t1 = p["t"], min(duration, p["t"] + float(p.get("dur") or show))
        name = _EMOJI.sub("", p.get("name") or "").strip()[:25]
        msg = re.sub(r"\s+", " ", _EMOJI.sub("", _censor_line(p.get("text") or ""))).strip()
        if not msg or t1 - t0 < 0.4:
            continue
        col = p.get("color") or "#9146ff"
        full = style == "banner"
        padx, pady = (40, 22)
        maxw = (W - 60 if full else 900) - 2 * padx - (150 if full else 0)
        words, lines, cur = msg.split(" "), [], ""
        for wd in words:
            trial = (cur + " " + wd).strip()
            if text_width(trial, "Inter-SemiBold.ttf", fs) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur); cur = wd
        lines.append(cur)
        lines = lines[:2]
        lw = max(text_width(l, "Inter-SemiBold.ttf", fs) for l in lines)
        nw = text_width(name, "Inter-ExtraBold.ttf", nfs) if name else 0
        head = (nfs + 14) if (name and style != "banner") else 0
        if style == "stream":
            head = 56 + (nfs + 6 if name else 0)
        lh = fs + 10
        w = int(max(lw, nw if style != "banner" else 0, 360 if style == "stream" else 0) + 2 * padx + (150 if full else 0))
        w = W - 60 if full else w
        h = int(head + len(lines) * lh + 2 * pady)
        x = (W - w) // 2
        y = base - h
        spec = {   # fill, fill alpha, radius, text colour, shadow
            "grey": ("#18181b", 0x00, 34, "&HF1EFEF", False),   # exact Twitch dark-mode chat colour #18181b, text #efeff1   # the creator 11 Nov: simple grey box, curved edges, Twitch name
            "bubble": ("#ffffff", 0x00, 44, "&H1A1A1A", True),
            "sticker": ("#ffffff", 0x00, 30, "&H111111", True),
            "neon": ("#0b0b16", 0x60, 26, "&HFFFFFF", False),
            "banner": ("#000000", 0x58, 14, "&HFFFFFF", False),
            "stream": ("#18181b", 0x08, 12, "&HEFEFF1", True),
        }[style]
        fill, alpha, rad, tcol, shadow = spec
        tilt = r"\frz-3" if style == "sticker" else ""
        org = r"\org(%d,%d)" % (x + w // 2, y + h // 2)
        move = lambda dx, dy: r"\move(%d,%d,%d,%d,0,220)" % (x + dx, y + 40 + dy, x + dx, y + dy)
        pop = r"\fscx88\fscy88\t(0,220,\fscx104\fscy104)\t(220,360,\fscx100\fscy100)"
        fade = r"\fad(150,260)"

        def shape(dx, dy, ww, hh, rr, color, al, extra="", drawing=None):
            tags = (r"{\an7" + move(dx, dy) + org + pop + fade + tilt + r"\bord0\shad0\1c" + ass_c(color)
                    + r"&\1a&H%02X&%s\p1}" % (al, extra))
            return f"Dialogue: 3,{_ts(t0)},{_ts(t1)},ChatBox,,0,0,0,," + tags + (drawing or _rrect(ww, hh, rr)) + r"{\p0}"

        def text(dx, cy, body, size=fs, extra=""):
            out.append(f"Dialogue: 4,{_ts(t0)},{_ts(t1)},ChatText,,0,0,0,," + r"{\an4" + move(dx, cy) + org + pop + fade + tilt
                       + r"\fs%d%s\q2}" % (size, extra) + body)
        if shadow:
            out.append(shape(0, 10, w, h, rad, "#000000", 0x98, r"\blur12"))
        if style == "neon":   # glow = blurred thick purple copy, then the crisp outline
            out.append(shape(0, 0, w, h, rad, "#9146ff", 0x30, r"\bord10\3c&HFF4691&\blur14"))
        if style == "bubble":   # tail at the bottom-left, pointing at the streamer
            tail = "m 0 0 l 46 0 l 0 40"
            out.append(shape(70, h - 6, 46, 40, 1, fill, alpha, drawing=tail))
        out.append(shape(0, 0, w, h, rad, fill, alpha,
                         {"neon": r"\bord5\3c&HFF4691&", "sticker": r"\bord7\3c&H111111&"}.get(style, "")))
        if style == "banner":
            out.append(shape(0, 0, 150, h, rad, "#9146ff", 0x00))
            out.append(shape(100, 0, 60, h, 1, "#9146ff", 0x00))   # squares off the tag's inner edge
            text(18, h // 2, r"{\fnInter\b800\1c&HFFFFFF&}CHAT", 38)
        if style == "stream":
            out.append(shape(0, 0, w, 56, rad, "#26262c", 0x00))
            out.append(shape(0, 28, w, 28, 1, "#26262c", 0x00))
            text(padx, 28, r"{\fnInter\b800\1c&HA0A0A8&}STREAM CHAT", 24, r"\fsp3")
        if style == "sticker" and name:   # name tag sitting on the top-left corner of the sticker
            tw = int(nw + 36)
            out.append(shape(24, -26, tw, nfs + 18, (nfs + 18) // 2, col, 0x00, r"\bord5\3c&H111111&"))
            text(24 + 18, -26 + (nfs + 18) // 2, r"{\fnInter\b800\1c&HFFFFFF&}" + _esc(name), nfs)
        ty = head + pady + lh // 2
        lx = padx + (150 if full else 0)
        if name and style in ("grey", "bubble", "neon"):
            text(lx, pady + nfs // 2 + 4, r"{\fnInter\b800\1c" + ass_c(col) + "&}" + _esc(name), nfs)
        if name and style == "stream":
            text(lx, 56 + 10 + nfs // 2, r"{\fnInter\b800\1c" + ass_c(col) + "&}" + _esc(name), nfs)
        body = [_esc(l) for l in lines]
        if style == "banner" and name:
            body[0] = r"{\fnInter\b800\1c" + ass_c(col) + "&}" + _esc(name) + r"{\1c" + tcol + "&}: " + body[0]
        for i, l in enumerate(body):
            text(lx, ty + i * lh, r"{\fnInter\b600\1c" + tcol + "&}" + l)
    return out


def chat_lines(pops, opts, duration, show=3.6, max_stack=3):
    """2.21 Twitch-style chat pop-ups: rounded cards sized to the text (Inter font, the user's name colour),
    a soft shadow, slide in + small bounce, older messages pushed up, fade out.
    pops: [{t (TikTok time), name, text, color}]."""
    from .fontmetrics import text_width
    style = opts.get("chat_style") or "glass"   # the creator: Twitch-chat BOX pop-up (glass card); subtitles/titles are the clean text
    if style in NEW_CHAT_STYLES:
        return chat_lines_cards(pops, opts, duration, style)
    fill, alpha, rad, accent = CHAT_CARDS.get(style, CHAT_CARDS["twitch"])
    if opts.get("chat_opacity") not in (None, ""):   # box opacity 0-100 % (100 = solid)
        alpha = int(round((100 - max(0, min(100, float(opts["chat_opacity"])))) / 100 * 255))
    name_a = int(round((100 - max(0, min(100, float(opts.get("chat_name_opacity", 100))))) / 100 * 255))   # viewer name opacity
    fs, pad_x, pad_y = (54, 30, 18) if len(pops) == 1 else (38, 26, 15)   # one context message = big and clear
    h = fs + 2 * pad_y + 6
    gap = 14
    x = 36   # side margin; each card is then centred (see below)
    base = int(H * float(opts.get("chat_y", 0.3)))          # bottom edge of the newest card
    maxw = W - 2 * x
    out = []
    arrivals = [p["t"] for p in pops]
    for i, p in enumerate(pops):
        t0, t1 = p["t"], min(duration, p["t"] + float(p.get("dur") or show))
        if t1 - t0 < 0.4:
            continue
        name = "" if style == "caption" else _EMOJI.sub("", p.get("name") or "").strip()[:25]
        msg = re.sub(r"\s+", " ", _EMOJI.sub("", _censor_line(p.get("text") or ""))).strip()
        if not msg:
            continue
        pill = style == "pill" and name
        lead = (28 if accent else 0)
        pill_w = int(text_width(name, "Inter-ExtraBold.ttf", fs - 4) + 30) if pill else 0
        name_w = (pill_w + 14) if pill else (text_width(name + ": ", "Inter-ExtraBold.ttf", fs) if name else 0)
        room = maxw - 2 * pad_x - lead - name_w
        mfont = "Inter-ExtraBold.ttf" if fill is None else "Inter-SemiBold.ttf"   # clean text is bolder
        while msg and text_width(msg, mfont, fs) > room:
            msg = msg[:-2].rstrip() + "…" if len(msg) > 2 else ""
        if not msg:
            continue
        w = int(2 * pad_x + lead + name_w + text_width(msg, mfont, fs)) + 2
        x = (W - w) // 2   # 2.21: each card centred across the screen (the creator: "needs better centering")
        col = ass_c(p.get("color") or "#9146ff")
        cuts = [a for a in arrivals[i + 1:] if t0 < a < t1]
        bounds = [t0] + cuts + [t1]
        for k in range(min(len(bounds) - 1, max_stack)):
            a, b = bounds[k], bounds[k + 1]
            y = base - h - k * (h + gap)
            y_prev = base - h - (k - 1) * (h + gap)
            last = (k == len(bounds) - 2) or (k == max_stack - 1)

            anim = opts.get("chat_anim") or "center"   # the creator 11 Nov: bounce from the centre, no sliding in
            # where it comes in from (dx, dy offset at the start) and how long the move takes
            start_off, mv_ms = {"center": ((0, 0), 1), "pop": ((0, 34), 200), "spring": ((0, 0), 1), "drop": ((0, -90), 220),
                                "slide": ((-160, 0), 240), "wobble": ((0, 20), 200)}.get(anim, ((0, 34), 200))

            def mvtag(dx, dy):
                if k == 0:
                    return r"\move(%d,%d,%d,%d,0,%d)" % (x + dx + start_off[0], y + dy + start_off[1], x + dx, y + dy, mv_ms)
                return r"\move(%d,%d,%d,%d,0,160)" % (x + dx, y_prev + dy, x + dx, y + dy)   # pushed up
            # the bounce itself (scale / squash / tilt), then fades out on its last stretch
            pop = {
                "center": r"\fscx30\fscy30\t(0,180,\fscx112\fscy112)\t(180,300,\fscx95\fscy95)\t(300,400,\fscx102\fscy102)\t(400,480,\fscx100\fscy100)",
                "pop": r"\fscx88\fscy88\t(0,200,\fscx104\fscy104)\t(200,330,\fscx100\fscy100)",
                "spring": r"\fscx55\fscy55\t(0,170,\fscx116\fscy116)\t(170,290,\fscx93\fscy93)\t(290,400,\fscx104\fscy104)\t(400,500,\fscx100\fscy100)",
                "drop": r"\t(200,280,\fscx110\fscy86)\t(280,380,\fscx96\fscy106)\t(380,460,\fscx100\fscy100)",
                "slide": r"\t(200,300,\fscx106)\t(300,420,\fscx100)",
                "wobble": r"\fscx90\fscy90\frz-7\t(0,180,\fscx104\fscy104\frz5)\t(180,320,\frz-3\fscx100\fscy100)\t(320,440,\frz0)",
            }.get(anim, "") if k == 0 else ""
            fade = r"\fad(%d,%d)" % (140 if k == 0 else 0, 240 if last else 0)
            org = r"\org(%d,%d)" % (x + w // 2, y + h // 2)

            def shape(dx, dy, ww, hh, rr, color, al, extra=""):
                cen = anim == "center" and k == 0   # scale around each piece's own middle
                tags = r"{\an%d" % (5 if cen else 7) + (mvtag(dx + ww // 2, dy + hh // 2) if cen else mvtag(dx, dy)) + org + pop + fade + r"\bord0\shad0\1c" + ass_c(color) + r"&\1a&H%02X&%s\p1}" % (al, extra)
                return f"Dialogue: 3,{_ts(a)},{_ts(b)},ChatBox,,0,0,0,," + tags + _rrect(ww, hh, rr) + r"{\p0}"
            clean = fill is None
            if not clean:
                out.append(shape(0, 8, w, h, rad, "#000000", 0x90, r"\blur10"))       # soft shadow
                out.append(shape(0, 0, w, h, rad, fill, alpha))                        # the card
            if accent:
                out.append(shape(12, 12, 7, h - 24, 4, accent, 0x00))                  # Twitch-purple bar
            tx = pad_x + lead
            outline = (r"\bord%d\3c&H000000&\shad2\4c&H000000&\4a&H60&" % (5 if fs > 40 else 4) if clean else "") + r"\fs%d" % fs

            def text(dx, body, centre_x=None):   # middle-left at the card's vertical centre; centred for the centre bounce
                cen = anim == "center" and k == 0 and centre_x is not None
                out.append(f"Dialogue: 4,{_ts(a)},{_ts(b)},ChatText,,0,0,0,," + r"{\an%d" % (5 if cen else 4) + mvtag(centre_x if cen else dx, h // 2) + org + pop + fade + outline + r"\q2}" + body)
            if pill:   # name in a Twitch-purple pill, message after it
                ph = h - 20
                out.append(shape(tx - 12, 10, pill_w, ph, ph // 2, "#9146ff", 0x00))
                text(tx + 3, (r"{\fnInter\b800\fs%d\1c&HFFFFFF&}" % (fs - 4)) + _esc(name))
                text(tx - 12 + pill_w + 14, r"{\fnInter\b600\1c&HEFEFF1&}" + _esc(msg))
            else:
                white = "&HFFFFFF" if clean else "&HEFEFF1"
                ntxt = (r"{\fnInter\b800\1a&H%02X&\1c" % name_a + col + "&}" + _esc(name) + r"{\1a&H00&\1c" + white + "&}: ") if name else ""
                text(tx, ntxt + r"{\fnInter\b" + ("800" if clean else "600") + r"\1c" + white + "&}" + _esc(msg), centre_x=(tx + w - pad_x) // 2)
    return out


def chat_lines_v1(pops, opts, duration, show=3.4, step=84, max_stack=3):
    """2.20: chat pop-ups. Each message slides in from the left with a small bounce, older ones are pushed up,
    each fades out after a few seconds. pops: [{t (TikTok time), name, text, color}]."""
    out = []
    base = int(H * float(opts.get("chat_y", 0.3)))
    x = 40
    arrivals = [p["t"] for p in pops]
    for i, p in enumerate(pops):
        t0 = p["t"]
        t1 = min(duration, t0 + show)
        if t1 - t0 < 0.4:
            continue
        name = _esc(_EMOJI.sub("", p.get("name") or "").strip())[:22]
        msg = _esc(_EMOJI.sub("", _censor_line(p.get("text") or "")).strip())
        if len(msg) > 46:
            msg = msg[:44].rstrip() + "…"
        if not msg:
            continue
        body = ((r"{\b1\1c" + ass_c(p.get("color") or "#5ec8ff") + "&}" + name + r"{\b0\1c&HFFFFFF&}: ") if name else "") + msg
        cuts = [a for a in arrivals[i + 1:] if t0 < a < t1]
        bounds = [t0] + cuts + [t1]
        for k in range(len(bounds) - 1):
            a, b = bounds[k], bounds[k + 1]
            if k >= max_stack:   # pushed off the top of the stack
                break
            y_now, y_prev = base - k * step, base - (k - 1) * step
            if k == 0:   # slide in from the left, small overshoot
                anim = (r"\move(%d,%d,%d,%d,0,220)\fscx88\fscy88\t(0,160,\fscx104\fscy104)\t(160,260,\fscx100\fscy100)"
                        % (-700, y_now, x, y_now))
            else:        # pushed up by a newer message
                anim = r"\move(%d,%d,%d,%d,0,150)" % (x, y_prev, x, y_now)
            fade = r"\fad(0,260)" if (k == len(bounds) - 2 or k == max_stack - 1) else ""
            out.append(f"Dialogue: 3,{_ts(a)},{_ts(b)},ChatBox,,0,0,0,," + r"{\an1" + anim + fade + r"\q2}" + body)
    return out


def _censor_line(text):
    return " ".join(_censor(w) for w in (text or "").split())


def chunk_events(chunks, opts, style, hl, uppercase):
    """Captions typed/placed by hand in the timeline editor (one block = one caption)."""
    ev = []
    oc = ass_c(opts.get("other_color", OTHER_COLOR))
    for ch in chunks:
        st, en, text = ch[0], ch[1], ch[2]
        other = len(ch) > 3 and ch[3] and opts.get("color_others", True)
        base = oc if other else "&HFFFFFF"
        toks = text.split()
        if style == "editor":
            key = opts.get("subs_mode") == "key"
            lower = not opts.get("uppercase", False) and not key
            fake = [{"start": st, "end": en, "text": t} for t in toks]
            flags = hype_flags(fake)
            if key and not _key_line([dict(f, hype=fl) for f, fl in zip(fake, flags)]):
                continue   # 2.24: hand-made subtitles follow "key lines only" too
            parts = []
            for t, f in zip(toks, flags):
                t2 = _esc(_censor(t).lower() if lower else _censor(t).upper())
                parts.append((r"{\1c" + ass_c(hl) + "&}" + t2 + r"{\1c" + base + "&}") if f else t2)
            pop = r"{\fscx86\fscy86\t(0,70,\fscx106\fscy106)\t(70,140,\fscx100\fscy100)}"
            ev.append((st, en, pop + r"{\1c" + base + "&}" + join_words(parts)))
        else:
            ws = [{"start": st + (en - st) * i / max(1, len(toks)), "end": st + (en - st) * (i + 1) / max(1, len(toks)),
                   "text": t} for i, t in enumerate(toks)]
            ev.extend(_word_spans(ws, style, hl, uppercase))
    return ev


def build_ass(words, opts, duration, hook=None, hook_len=None):
    """words: [{start,end,text}] already on the clip's output timeline."""
    style = opts.get("style", "pop")
    font = opts.get("font", "Anton")
    hl = opts.get("highlight", "#00e5ff")
    per_line = opts.get("words_per_line", 3)
    uppercase = opts.get("uppercase", True)
    ypos = float(opts.get("position", 0.62))  # fraction of frame height
    fdef = FONTS.get(font, FONTS["Anton"])
    size = int(fdef["size"] * float(opts.get("size", 1.0)))
    if style == "clean":
        size = int(size * 0.8)
    outline = 0 if style == "clean" else 7
    shadow = 3 if style == "clean" else 2
    blur = 0
    if style == "editor":
        outline, shadow, blur = 5, 2, 1

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,"
        f"{fdef['spacing']},0,1,{outline},{shadow},5,60,60,0,-1",
        "Style: ChatText,Inter,38,&H00F1EFEF,&H00F1EFEF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,-1",
        "Style: ChatBox,Montserrat ExtraBold,42,&H00FFFFFF,&H00FFFFFF,&H40140F12,&H80000000,0,0,0,0,100,100,0,0,3,14,0,1,40,40,0,-1",
        "Style: KF,Inter,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,-1",
        "Style: TitleBox,Montserrat ExtraBold,80,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,14,0,8,90,90,0,-1",
        "Style: Title,Montserrat ExtraBold,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,6,2,8,70,70,0,-1",
        "Style: Hook,Archivo Black,58,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,18,0,8,80,80,170,-1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    pos = r"{\an5\pos(%d,%d)\q2%s}" % (W // 2, int(H * ypos), (r"\blur%d" % blur) if blur else "")
    if hook and opts.get("show_hook", False):
        hook_len = hook_len or min(3.2, max(1.5, duration * 0.12))
        lines.append(f"Dialogue: 2,{_ts(0.05)},{_ts(hook_len)},Hook,,0,0,0,,"
                     r"{\fad(120,250)}" + _esc(hook.upper()))
    events = []
    if opts.get("_chunks") is not None:
        events = chunk_events(opts["_chunks"], opts, style, hl, uppercase)
    elif style == "editor":
        events = _editor_events(clean_words(words), opts)
    else:
        for g in group_words(clean_words(words), per_line):
            events.extend(_word_spans(g, style, hl, uppercase))
    events.sort(key=lambda e: e[0])
    wm = (opts.get("watermark") or "").strip()
    if wm:
        lines.extend(_watermark_lines(wm, opts, duration))
    if opts.get("title_bar"):
        lines.extend(_title_lines(opts.get("title_text") or "", opts, duration))
    if opts.get("_chat"):
        lines.extend(chat_lines(opts["_chat"], opts, duration))
    if opts.get("_kills"):   # 2.35: the CS2 kill feed drawn from scratch
        lines.extend(killfeed_lines(opts["_kills"], opts, duration))
    if opts.get("_end"):
        # 2.24: the ending word (black-and-white ending): one big red word that slams in
        t0, word = opts["_end"]
        y = int(H * float(opts.get("end_word_y", 0.36)))
        lines.append(f"Dialogue: 3,{_ts(t0)},{_ts(duration)},Title,,0,0,0,,"
                     + (r"{\an5\pos(%d,%d)\fnMontserrat ExtraBold\fs150\bord9\shad3\3c&H000000&\1c%s&"
                        r"\fscx170\fscy170\t(0,120,\fscx96\fscy96)\t(120,200,\fscx100\fscy100)}%s")
                     % (W // 2, y, ass_c(opts.get("end_word_color") or "#ff2a3a"), _esc(word.upper())))
    # one caption on screen at a time: each line ends exactly when the next begins
    for i, (st, en, txt) in enumerate(events):
        if i + 1 < len(events):
            en = min(en, events[i + 1][0])
        en = min(en, duration)
        if en - st < 0.04:
            continue
        lines.append(f"Dialogue: 1,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{pos}{txt}")
    return "\n".join(lines) + "\n"


def clean_words(words):
    """Sort words and remove overlaps/duplicates Whisper sometimes produces."""
    out = []
    for w in sorted(words, key=lambda w: (w["start"], w["end"])):
        if out and w["start"] < out[-1]["start"] + 0.02 and w["text"] == out[-1]["text"]:
            continue  # duplicate word
        w = dict(w)
        if out and w["start"] < out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["start"] + 0.05, w["start"])
        w["end"] = max(w["end"], w["start"] + 0.05)
        out.append(w)
    return out


def retime_words(words, keep_segments):
    """Map source-time words onto the output timeline after jump cuts.
    keep_segments: [(src_start, src_end), ...] in order."""
    out = []
    offset = 0.0
    for s, e in keep_segments:
        for w in words:
            mid = (w["start"] + w["end"]) / 2
            if s <= mid < e:
                ns = offset + max(0.0, w["start"] - s)
                ne = offset + min(e, w["end"]) - s
                out.append({"start": round(ns, 3), "end": round(max(ne, ns + 0.08), 3), "text": w["text"],
                            "other": bool(w.get("other"))})
        offset += e - s
    out.sort(key=lambda w: w["start"])
    return out


def apply_text_edit(words, new_text):
    """User edited the subtitle text for a clip. Keep timings where word counts
    match, otherwise spread the new words over the same time span."""
    new = new_text.split()
    if not words or not new:
        return words
    if len(new) == len(words):
        return [dict(w, text=t) for w, t in zip(words, new)]
    t0, t1 = words[0]["start"], words[-1]["end"]
    # weight by word length so long words stay up longer
    lens = [max(2, len(t)) for t in new]
    tot = float(sum(lens))
    out, t = [], t0
    for word, ln in zip(new, lens):
        d = (t1 - t0) * ln / tot
        out.append({"start": round(t, 3), "end": round(t + d * 0.95, 3), "text": word})
        t += d
    return out


# ---------------------------------------------------------------------------------------------------------
# 2.35: the CS2 kill feed drawn from scratch (the creator: like the kill-feed generator PNGs, not traced from the stream).
# Same look as the game / the generators: a see-through black row (63 %), a red outline on YOUR kills (darker row),
# a dark-red row for your death, names in CT blue / T gold with a small black shadow, the real CS2 icons in white.
KF_SCALE = 2.15                       # the game's 16 px font -> 34 px on the 1080 x 1920 TikTok
KF_COL = {"CT": "&HE69C6F&", "T": "&H54BEEA&"}
KF_PRE = ("blind", "inair")           # drawn before the killer's name, like CS2
KF_ORDER = ("noscope", "smoke", "wallbang", "headshot")


def killfeed_lines(rows, opts, duration):
    """rows = [{t0, t1, enter, leave, kid, killer, victim, killer_team, victim_team, gun, extras, mine, death}] in
    TikTok time. Rows stack down from opts['kf_y'] (top), right-aligned, newest at the bottom; when an older row
    leaves, the rest slide up."""
    from . import cs2icons
    from .fontmetrics import text_width
    ic = cs2icons.icons()
    if not rows or not ic:
        return []
    K = float(opts.get("kf_scale") or KF_SCALE)
    fs, rh, pad, gap, ih = round(16 * K), round(35 * K), round(8 * K), round(3 * K) + 2, round(24 * K)
    xr = W - int(opts.get("kf_right", 26))
    top = int(H * float(opts.get("kf_y", 0.45)))
    sx = 90                                               # a little narrower, like the game's Stratum font

    def tw(t):
        return text_width(t, "Inter-ExtraBold.ttf", fs) * sx / 100.0

    # lay out each kill once
    lay = {}
    for r in rows:
        if r["kid"] in lay:
            continue
        items = []                                        # (kind, value, width)
        for e in KF_PRE:
            if e in r.get("extras", []) and e in ic:
                d, w = cs2icons.ass_drawing(ic[e], ih)
                items.append(("icon", d, w))
        if r.get("killer"):
            items.append(("text", (r["killer"], r.get("killer_team", "T")), tw(r["killer"])))
        g = ic.get(r.get("gun")) or ic.get("knife")
        if g:
            d, w = cs2icons.ass_drawing(g, ih)
            items.append(("icon", d, w))
        for e in KF_ORDER:
            if e in r.get("extras", []) and e in ic:
                d, w = cs2icons.ass_drawing(ic[e], ih)
                items.append(("icon", d, w))
        if r.get("victim"):
            items.append(("text", (r["victim"], r.get("victim_team", "CT")), tw(r["victim"])))
        m = round(3 * K)
        width = int(2 * pad + sum(w for *_, w in items) + m * 2 * len(items))
        lay[r["kid"]] = (items, width, m)

    out = []
    times = sorted({r["t0"] for r in rows} | {r["t1"] for r in rows})
    prev_slot = {}
    for a, b in zip(times, times[1:]):
        if b - a < 0.02:
            continue
        act = sorted([r for r in rows if r["t0"] <= a + 1e-3 and r["t1"] >= b - 1e-3], key=lambda r: (r["order"], r["kid"]))
        slots = {}
        for i, r in enumerate(act):
            items, width, m = lay[r["kid"]]
            y = top + i * (rh + gap)
            y_old = prev_slot.get(r["kid"], y)
            x = xr - width
            first = r["enter"] and abs(r["t0"] - a) < 1e-3
            last = r["leave"] and abs(r["t1"] - b) < 1e-3
            fade = r"\fad(%d,%d)" % (140 if first else 0, 220 if last else 0)

            def mv(dx, dy):
                if first:   # slides in from the right
                    return r"\move(%d,%d,%d,%d,0,160)" % (x + dx + 70, y + dy, x + dx, y + dy)
                if y_old != y:   # an older row left: slide up
                    return r"\move(%d,%d,%d,%d,0,180)" % (x + dx, y_old + dy, x + dx, y + dy)
                return r"\pos(%d,%d)" % (x + dx, y + dy)
            ev = f"{_ts(a)},{_ts(b)},KF,,0,0,0,,"
            if r.get("mine"):
                box = r"\1c&H000000&\1a&H18&\bord%d\3c&H0000E1&\3a&H00&" % max(2, round(1.6 * K))
            elif r.get("death"):
                box = r"\1c&H060663&\1a&H2D&\bord0"
            else:
                box = r"\1c&H000000&\1a&H5F&\bord0"
            out.append("Dialogue: 6," + ev + r"{\an7" + mv(0, 0) + fade + box + r"\shad0\p1}" + _rrect(width, rh, round(3 * K)) + r"{\p0}")
            cx = pad
            for kind, val, w in items:
                cx += m
                if kind == "icon":
                    out.append("Dialogue: 7," + ev + r"{\an7" + mv(int(cx), (rh - ih) // 2) + fade
                               + r"\1c&HFFFFFF&\1a&H00&\bord0\shad0\p1}" + val + r"{\p0}")
                else:
                    txt, team = val
                    out.append("Dialogue: 7," + ev + r"{\an4" + mv(int(cx), rh // 2) + fade
                               + r"\fnInter\b800\fs%d\fscx%d\1c%s\bord0\shad%d\4c&H000000&\4a&H20&\q2}" % (fs, sx, KF_COL.get(team, KF_COL["T"]), max(1, round(K)))
                               + _esc(txt))
                cx += w + m
            slots[r["kid"]] = y
        prev_slot = slots
    return out
