"""2.35: the real CS2 kill-feed icons (guns, knives, grenades, headshot, no-scope, wallbang, smoke, blind, in-air),
read from the streamer's OWN CS2 install (Steam), so the kill feed can be drawn sharp like the kill-feed generator
PNGs. Nothing is downloaded and no game art ships with Clip Genie: without CS2 installed this returns nothing and the
kill feed falls back to the re-traced rows.

icons() -> {name: Icon}. Icon.polys = closed outlines in the icon's own units (viewBox), Icon.w / Icon.h = its size.
"""
import json
import math
import os
import re
import struct
import xml.etree.ElementTree as ET

from .util import data_dir, log

PAK = os.path.join("steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "pak01_dir.vpk")
WANT = ("panorama/images/icons/equipment/", "panorama/images/hud/deathnotice/")
# the extra icons CS2 puts after the gun in a kill row (file name -> our name)
EXTRAS = {"icon_headshot": "headshot", "noscope": "noscope", "penetrate": "wallbang", "smoke_kill": "smoke",
          "blind_kill": "blind", "inairkill": "inair", "icon_suicide": "suicide", "domination": "domination",
          "revenge": "revenge"}
# not weapons: never picked as the gun of a kill
NOT_GUNS = {"ammobox", "ammobox_threepack", "armor", "armor_helmet", "assaultsuit", "assaultsuit_helmet_only",
            "clothing_hands", "customplayer", "defuser", "disconnect", "flair0", "grenadepack", "grenadepack2",
            "healthshot", "heavy_armor", "helmet", "kevlar", "movelinear", "pet", "spray0", "worldent", "dronegun",
            "c4", "flashbang_assist", "smokegrenade_impact"}


class Icon:
    def __init__(self, name, w, h, polys):
        self.name, self.w, self.h, self.polys = name, w, h, polys


# ------------------------------------------------------------------ find CS2 + read its pack (VPK v2)
def find_pak():
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    libs = []
    for r in roots:
        libs.append(r)
        try:
            txt = open(os.path.join(r, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="ignore").read()
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', txt)]
        except OSError:
            pass
    for lib in libs:
        p = os.path.join(lib, PAK)
        if os.path.isfile(p):
            return p
    return None


def _vpk_tree(path):
    with open(path, "rb") as f:
        sig, ver, tree = struct.unpack("<III", f.read(12))
        if sig != 0x55AA1234:
            raise ValueError("not a VPK")
        hdr = 12 + (16 if ver == 2 else 0)
        f.seek(hdr)
        data = f.read(tree)
    p, out = 0, {}

    def s():
        nonlocal p
        e = data.index(b"\0", p)
        r = data[p:e].decode("utf-8", "replace")
        p = e + 1
        return r
    while True:
        ext = s()
        if not ext:
            break
        while True:
            folder = s()
            if not folder:
                break
            while True:
                name = s()
                if not name:
                    break
                _crc, pre, arc, off, ln, _t = struct.unpack_from("<IHHIIH", data, p)
                p += 18
                out[f"{folder}/{name}.{ext}"] = (arc, off, ln, data[p:p + pre])
                p += pre
    return out, hdr + tree


def _vpk_read(path, ent, base):
    arc, off, ln, pre = ent
    if not ln:
        return pre
    if arc == 0x7FFF:
        src, at = path, base + off
    else:
        src, at = path.replace("_dir.vpk", "_%03d.vpk" % arc), off
    with open(src, "rb") as f:
        f.seek(at)
        return pre + f.read(ln)


# ------------------------------------------------------------------ SVG -> outlines
def _mat_mul(a, b):
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def _parse_transform(t):
    m = (1, 0, 0, 1, 0, 0)
    for kind, args in re.findall(r"(\w+)\s*\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.findall(r"-?[\d.]+(?:e-?\d+)?", args)]
        if kind == "matrix" and len(v) == 6:
            n = tuple(v)
        elif kind == "translate":
            n = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif kind == "scale":
            n = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif kind == "rotate":
            a = math.radians(v[0])
            n = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
        else:
            continue
        m = _mat_mul(m, n)
    return m


def _bez(p0, p1, p2, p3, n=10):
    return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t * t * p2[0] + t ** 3 * p3[0],
             (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t * t * p2[1] + t ** 3 * p3[1])
            for t in (i / n for i in range(1, n + 1))]


def _path(d):
    toks = re.findall(r"[A-Za-z]|-?(?:\d+\.?\d*|\.\d+)(?:[eE]-?\d+)?", d)
    polys, cur, pos, start, last_c, cmd, i = [], [], (0.0, 0.0), (0.0, 0.0), None, None, 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    polys.append(cur)
                cur, pos, last_c = [], start, None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = pos if rel else (0.0, 0.0)
        if c == "M":
            if cur:
                polys.append(cur)
            pos = (ox + num(), oy + num())
            start, cur, last_c = pos, [pos], None
            cmd = "l" if rel else "L"            # extra pairs after M are line-tos
        elif c == "L":
            pos = (ox + num(), oy + num())
            cur.append(pos)
            last_c = None
        elif c == "H":
            pos = ((ox if rel else 0) + num(), pos[1])
            cur.append(pos)
            last_c = None
        elif c == "V":
            pos = (pos[0], (oy if rel else 0) + num())
            cur.append(pos)
            last_c = None
        elif c == "C":
            p1 = (ox + num(), oy + num())
            p2 = (ox + num(), oy + num())
            p3 = (ox + num(), oy + num())
            cur += _bez(pos, p1, p2, p3)
            pos, last_c = p3, p2
        elif c == "S":
            p1 = (2 * pos[0] - last_c[0], 2 * pos[1] - last_c[1]) if last_c else pos
            p2 = (ox + num(), oy + num())
            p3 = (ox + num(), oy + num())
            cur += _bez(pos, p1, p2, p3)
            pos, last_c = p3, p2
        elif c == "Q":
            q = (ox + num(), oy + num())
            p3 = (ox + num(), oy + num())
            p1 = (pos[0] + 2 / 3 * (q[0] - pos[0]), pos[1] + 2 / 3 * (q[1] - pos[1]))
            p2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
            cur += _bez(pos, p1, p2, p3)
            pos, last_c = p3, None
        else:                                     # arcs etc.: not used by these icons; skip the numbers
            i += 1
    if cur:
        polys.append(cur)
    return [p for p in polys if len(p) >= 3]


def _svg(text):
    root = ET.fromstring(re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", text, count=0))
    vb = [float(x) for x in (root.get("viewBox") or "0 0 32 32").replace(",", " ").split()]
    out = []

    def walk(el, m):
        m = _mat_mul(m, _parse_transform(el.get("transform")))
        tag = el.tag.split("}")[-1]
        fill = (el.get("fill") or "").lower()
        if fill != "none" and tag in ("path", "polygon", "rect"):
            if tag == "path":
                polys = _path(el.get("d") or "")
            elif tag == "polygon":
                v = [float(x) for x in re.findall(r"-?[\d.]+(?:e-?\d+)?", el.get("points") or "")]
                polys = [list(zip(v[0::2], v[1::2]))]
            else:
                x, y = float(el.get("x") or 0), float(el.get("y") or 0)
                w, h = float(el.get("width") or 0), float(el.get("height") or 0)
                polys = [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]]
            for p in polys:
                out.append([(m[0] * px + m[2] * py + m[4] - vb[0], m[1] * px + m[3] * py + m[5] - vb[1]) for px, py in p])
        for ch in el:
            walk(ch, m)
    walk(root, (1, 0, 0, 1, 0, 0))
    return vb[2], vb[3], out


# ------------------------------------------------------------------ public
_CACHE = None


def icons():
    """{name: Icon} for every gun / equipment icon and the kill-feed extras, or {} without CS2 installed."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    store = os.path.join(data_dir(), "cs2_icons.json")
    raw = None
    pak = find_pak()
    try:
        if os.path.isfile(store) and (not pak or os.path.getmtime(store) >= os.path.getmtime(pak)):
            raw = json.load(open(store, encoding="utf-8"))
    except Exception:
        raw = None
    if raw is None and pak:
        try:
            tree, base = _vpk_tree(pak)
            raw = {}
            for k, ent in tree.items():
                if k.endswith(".vsvg_c") and k.startswith(WANT):
                    d = _vpk_read(pak, ent, base)
                    a, b = d.find(b"<svg"), d.rfind(b"</svg>")
                    if a < 0 or b < 0:
                        continue
                    name = os.path.basename(k)[:-len(".vsvg_c")]
                    if k.startswith(WANT[1]):
                        if name not in EXTRAS:
                            continue
                        name = EXTRAS[name]
                    try:
                        w, h, polys = _svg(d[a:b + 6].decode("utf-8", "replace"))
                    except Exception as e:
                        log.warning("CS2 icon %s skipped: %s", name, e)
                        continue
                    if polys:
                        raw[name] = [w, h, [[[round(x, 3), round(y, 3)] for x, y in p] for p in polys]]
            with open(store, "w", encoding="utf-8") as f:
                json.dump(raw, f)
            log.info("Read %d CS2 kill-feed icons from %s", len(raw), pak)
        except Exception as e:
            log.warning("Couldn't read the CS2 icons: %s", e)
            raw = None
    _CACHE = {n: Icon(n, v[0], v[1], [[tuple(pt) for pt in p] for p in v[2]]) for n, v in (raw or {}).items()}
    return _CACHE


def guns():
    return {n: i for n, i in icons().items() if n not in NOT_GUNS and n not in EXTRAS.values()}


def extras():
    return {n: i for n, i in icons().items() if n in EXTRAS.values()}


def raster(icon, height, pad=0):
    """The icon as a uint8 mask, `height` px tall (keeps its shape), filled even-odd like the game draws it."""
    import cv2
    import numpy as np
    xs = [x for p in icon.polys for x, _ in p]
    ys = [y for p in icon.polys for _, y in p]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    s = (height - 1) / max(1e-6, y1 - y0)
    w = int(math.ceil((x1 - x0) * s)) + 1
    m = np.zeros((height + 2 * pad, w + 2 * pad), np.uint8)
    for p in icon.polys:   # XOR each outline in: even-odd, so holes (trigger guards, the skull's eye) stay open
        one = np.zeros_like(m)
        pts = np.array([[(x - x0) * s + pad, (y - y0) * s + pad] for x, y in p], np.float32)
        cv2.fillPoly(one, [np.round(pts * 16).astype(np.int32)], 255, lineType=cv2.LINE_AA, shift=4)
        m = np.bitwise_xor(m, (one > 127).astype(np.uint8) * 255)
    return m


def ass_drawing(icon, height):
    """The icon as an ASS vector drawing (\\p1), `height` px tall, top-left at 0,0. Returns (drawing, width)."""
    xs = [x for p in icon.polys for x, _ in p]
    ys = [y for p in icon.polys for _, y in p]
    x0, y0 = min(xs), min(ys)
    s = height / max(1e-6, max(ys) - y0)
    parts = []
    for p in icon.polys:
        pts = [((x - x0) * s, (y - y0) * s) for x, y in p]
        parts.append("m %.1f %.1f l " % pts[0] + " ".join("%.1f %.1f" % q for q in pts[1:]))
    return " ".join(parts), (max(xs) - x0) * s
