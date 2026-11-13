"""2.21: measure how wide text is in a .ttf font (no extra libraries), so drawn shapes like the Twitch chat
cards fit their text exactly. Reads the font's character map (cmap format 4/12) and advance widths (hmtx)."""
import os
import struct

from .util import FONTS_DIR

_CACHE = {}


def _load(path):
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(n):
        tag, _cs, off, ln = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        tables[tag.decode("latin-1")] = (off, ln)
    upem = struct.unpack(">H", data[tables["head"][0] + 18: tables["head"][0] + 20])[0]
    n_hmetrics = struct.unpack(">H", data[tables["hhea"][0] + 34: tables["hhea"][0] + 36])[0]
    ho = tables["hmtx"][0]
    adv = [struct.unpack(">H", data[ho + 4 * i: ho + 4 * i + 2])[0] for i in range(n_hmetrics)]
    cmap = {}
    co = tables["cmap"][0]
    nsub = struct.unpack(">H", data[co + 2: co + 4])[0]
    subs = []
    for i in range(nsub):
        pid, eid, off = struct.unpack(">HHI", data[co + 4 + 8 * i: co + 12 + 8 * i])
        subs.append((pid, eid, co + off))
    for pid, eid, so in sorted(subs, key=lambda s: -struct.unpack(">H", data[s[2]:s[2] + 2])[0]):
        fmt = struct.unpack(">H", data[so:so + 2])[0]
        if fmt == 12:
            ngroups = struct.unpack(">I", data[so + 12: so + 16])[0]
            for g in range(ngroups):
                a, b, gid = struct.unpack(">III", data[so + 16 + 12 * g: so + 28 + 12 * g])
                for c in range(a, min(b, a + 70000) + 1):
                    cmap.setdefault(c, gid + c - a)
            break
        if fmt == 4:
            segx2 = struct.unpack(">H", data[so + 6: so + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(">%dH" % seg, data[so + 14: so + 14 + segx2])
            starts = struct.unpack(">%dH" % seg, data[so + 16 + segx2: so + 16 + 2 * segx2])
            deltas = struct.unpack(">%dh" % seg, data[so + 16 + 2 * segx2: so + 16 + 3 * segx2])
            ro_pos = so + 16 + 3 * segx2
            ranges = struct.unpack(">%dH" % seg, data[ro_pos: ro_pos + segx2])
            for k in range(seg):
                for c in range(starts[k], ends[k] + 1):
                    if c == 0xFFFF:
                        continue
                    if ranges[k] == 0:
                        gid = (c + deltas[k]) & 0xFFFF
                    else:
                        p = ro_pos + 2 * k + ranges[k] + 2 * (c - starts[k])
                        gid = struct.unpack(">H", data[p:p + 2])[0]
                        if gid:
                            gid = (gid + deltas[k]) & 0xFFFF
                    cmap.setdefault(c, gid)
            break
    return upem, adv, cmap


def text_width(text, font_file, size):
    """Width in pixels of text at this font size (ASS \\fs = size)."""
    path = os.path.join(FONTS_DIR, font_file)
    if path not in _CACHE:
        try:
            _CACHE[path] = _load(path)
        except Exception:
            _CACHE[path] = None
    m = _CACHE[path]
    if not m:
        return len(text) * size * 0.55
    upem, adv, cmap = m
    total = 0
    for ch in text:
        gid = cmap.get(ord(ch))
        if gid is None:   # drawn by a Windows fallback font: CJK is a full square, combining marks take no room
            import unicodedata
            if unicodedata.combining(ch) or unicodedata.category(ch) == "Mn":
                continue
            total += upem * (1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.6)
        else:
            total += adv[min(gid, len(adv) - 1)]
    return total * size / upem
