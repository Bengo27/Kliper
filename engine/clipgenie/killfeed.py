"""2.22: find when the CS2 kill feed is showing, so the kill-feed overlay only appears when someone gets a kill.

CS2 draws each kill as a grey row in the top-right corner: player names in team colours (CT blue / T gold)
around a white weapon icon. Rows stay ~5-7 s and fade. We sample that corner a few times a second and call a
frame "kill feed on" when at least one band holds both coloured name text and white icon pixels."""
import logging
import subprocess

import numpy as np

from .util import ffmpeg, CREATE_NO_WINDOW

log = logging.getLogger("clipgenie")

W, H, FPS = 480, 240, 4
BAND, STEP = 12, 6


def _frame_rows(f, mode="cs2"):
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    luma = (r * 3 + g * 6 + b) // 10
    white = luma > 220                                                                         # weapon icon / text
    if mode == "cs2":
        named = ((b > 170) & (b - r > 60) & (g > 100)) | ((r > 180) & (g > 120) & (r - b > 90))   # CT blue / T gold names
    else:   # "text" (CoD, Fortnite...): bright text lines on a darker background. Not tuned on real footage yet.
        named = white & (np.roll(luma, 2, axis=1) < 120)                                       # sharp text edges
    rows = 0
    for y in range(0, H - BAND, STEP):
        c = int(named[y:y + BAND].sum())
        w = int(white[y:y + BAND].sum())
        # a real row: some name text AND some icon, but not a band full of sky / snow
        if 25 < c < 0.25 * BAND * W and 20 < w < 0.3 * BAND * W:
            rows += 1
    return rows


def visible(video, start, end, box, mode="cs2"):
    """Per-sample 'rows that look like kill-feed entries' between start and end (seconds in `video`).
    box = (x, y, w, h) fractions of the frame around the kill feed. Returns (times, rows)."""
    x, y, w, h = box
    # look a little wider than the pasted box: long rows reach further left
    x0, w0 = max(0.0, x - 0.04), min(1.0, w + 0.08)
    y0, h0 = max(0.0, y - 0.03), min(1.0 - max(0.0, y - 0.03), h + 0.06)
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.5, end - start):.3f}", "-i", video,
           "-vf", f"fps={FPS},crop=iw*{min(w0, 1 - x0):.4f}:ih*{h0:.4f}:iw*{x0:.4f}:ih*{y0:.4f},scale={W}:{H}",
           "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW).stdout
    n = len(raw) // (W * H * 3)
    if not n:
        return np.zeros(0), np.zeros(0)
    fr = np.frombuffer(raw[: n * W * H * 3], np.uint8).reshape(n, H, W, 3).astype(np.int16)
    rows = np.array([_frame_rows(f, mode) for f in fr], dtype=np.float32)
    return max(0.0, start) + np.arange(n) / FPS, rows


def windows(video, start, end, box, hold=1.0, min_on=1.5, merge=1.5, mode="cs2"):
    """[(a, b)] stretches (seconds in `video`) when the kill feed shows kills. A row has to be seen in 2 of
    3 samples in a row (no one-frame blips), then it stays up `hold` s after the last row fades."""
    try:
        t, rows = visible(video, start, end, box, mode)
    except Exception as e:
        log.warning("Kill feed check failed: %s", e)
        return None
    if not len(t):
        return []
    on = rows > 0
    sm = np.convolve(on.astype(np.float32), np.ones(3, dtype=np.float32), mode="same") >= 2
    out = []
    i = 0
    while i < len(sm):
        if not sm[i]:
            i += 1
            continue
        j = i
        while j < len(sm) and sm[j]:
            j += 1
        a, b = float(t[i]) - 0.25, float(t[j - 1]) + hold
        if out and a - out[-1][1] <= merge:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
        i = j
    return [(round(max(start, a), 2), round(min(end, b), 2)) for a, b in out if b - a >= min_on]


# ---------------------------------------------------------------------------------------------------------
# 2.22: clean kill-feed rows. Cut the game's real rows out of each frame (no gameplay behind them, no box) and
# write them as a transparent video. Only for sick clutches: a burst of kills (or kills with the red outline
# CS2 puts around YOUR kills), not every single kill.
FPS_OV = 30
ROW_BG = (20, 20, 24)


def _find_rows(f):
    """[(y0, y1, x0, x1)] kill rows in one native-resolution RGB frame (int16 HxWx3), CS2 look."""
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    luma = (r * 3 + g * 6 + b) // 10
    white = luma > 220
    named = ((b > 170) & (b - r > 60) & (g > 100)) | ((r > 180) & (g > 120) & (r - b > 90))
    m = white | named | ((r > 150) & (g > 100) & (r - b > 60))   # 2.35: dimmer gold names count as text too
    # 2.35.2: text only counts next to the row's dark background: a light-blue sky has the blue-name colour and
    # turned the whole corner into "text", so rows were lost for seconds and drawn kills came in ~2 s late
    import cv2
    # (the same line around it, +-25 px, is 30 %+ dark: sky between two rows has no dark on its line)
    near_dark = cv2.blur((luma < 95).astype(np.float32), (51, 1)) >= 0.3
    named &= near_dark
    white &= near_dark
    m &= near_dark
    H, W = luma.shape
    act = np.nonzero(m.sum(1) >= 2)[0]
    out = []
    if not len(act):
        return out
    groups, s, p = [], act[0], act[0]
    for y in act[1:]:
        if y - p > 5:
            groups.append((s, p))
            s = y
        p = y
    groups.append((s, p))
    for ya, yb in groups:
        if not 9 <= yb - ya + 1 <= 40:
            continue
        cols = np.nonzero(m[ya:yb + 1].sum(0) >= 1)[0]
        if not len(cols):
            continue
        x0, x1 = _grow(f, luma, ya, yb, int(cols.min()), int(cols.max()))   # 2.35: the real row edge, not the text
        y0, y1 = max(0, ya - 6), min(H, yb + 7)
        nm, wh = int(named[ya:yb + 1, x0:x1].sum()), int(white[ya:yb + 1, x0:x1].sum())
        if nm < 15 or wh < 6 or x1 - x0 < 60:
            continue
        reg = luma[y0:y1, x0:x1]
        if float(np.median(reg[~m[y0:y1, x0:x1]])) > 125:     # kill rows have a dark background (sky / snow don't)
            continue
        out.append((y0, y1, x0, x1))
    return out


def _red(f):
    return (f[..., 0] > 150) & (f[..., 1] < 95) & (f[..., 2] < 105) & (f[..., 0] - f[..., 1] > 70)


def _grow(f, luma, ya, yb, t0, t1):
    """2.35: widen a row from its text (t0..t1) to the row's real edges, so a name the colour test missed
    ("trunk" in gold) isn't cut off: stop at the red outline of your kills, else where the dark row background ends
    (never less than 12 px past the text)."""
    H, W = luma.shape
    red = _red(f[ya:yb + 1])
    dark = luma[ya:yb + 1] < 95

    def edge(x, step, lim):
        last, miss = x + step * 12, 0
        for k in range(1, lim):
            c = x + step * k
            if c < 0 or c >= W:
                break
            if red[:, c].mean() > 0.5:
                return c + step * 2          # keep the outline itself
            if dark[:, c].mean() >= 0.55 or (k <= 12):
                if k > 12:
                    last = c
                miss = 0
            else:
                miss += 1
                if miss >= 3:
                    break
        return last
    x0, x1 = max(0, edge(t0, -1, 110)), min(W, edge(t1, 1, 50) + 1)
    # a red outline (even a faint one while the row fades in) is the edge: never go past it into the gameplay
    rc = np.nonzero(red[:, x0:x1].mean(0) > 0.3)[0]
    if len(rc):
        lo, hi = x0 + int(rc.min()), x0 + int(rc.max())
        if lo < t0:
            x0 = max(0, lo - 2)
        if hi > t1:
            x1 = min(W, hi + 3)
    return x0, x1


def _icons(f, row):
    """How many icon blobs sit between the two names. A plain kill has 1 (the gun); knife, no-scope, headshot,
    wallbang, through-smoke... each add one."""
    y0, y1, x0, x1 = row
    r, g, b = f[y0:y1, x0:x1, 0], f[y0:y1, x0:x1, 1], f[y0:y1, x0:x1, 2]
    luma = (r * 3 + g * 6 + b) // 10
    named = ((b > 170) & (b - r > 60) & (g > 100)) | ((r > 180) & (g > 120) & (r - b > 90))
    nc = np.nonzero(named.any(0))[0]
    if len(nc) < 2:
        return 1
    # the gap between the left name and the right name = the widest run of columns without name colour
    gaps = np.diff(nc)
    i = int(np.argmax(gaps))
    if gaps[i] < 25:
        return 1
    a, bnd = nc[i] + 1, nc[i + 1]
    cols = (luma[:, a:bnd] > 200).any(0)
    blobs, run, gap = 0, False, 0
    for c in cols:
        if c:
            if not run:
                blobs += 1
            run, gap = True, 0
        else:
            gap += 1
            if gap > 7:
                run = False
    return max(1, blobs)


def _is_mine(f, row):
    """CS2 outlines the rows of YOUR kills in red."""
    y0, y1, x0, x1 = row
    red = _red(f[y0:y1, max(0, x0 - 2):x1 + 2])
    if not red.size:
        return False
    # 2.35: a red line down the left or right end of the row (the box edges are a few px inside the cut-out)
    mid = red[red.shape[0] // 4: red.shape[0] * 3 // 4]
    sides = mid[:, :8].any(1).mean(), mid[:, -8:].any(1).mean()
    return max(sides) > 0.6


def _rounded(h, w, rad=5):
    yy, xx = np.mgrid[0:h, 0:w]
    dx = np.maximum(np.maximum(rad - xx, xx - (w - 1 - rad)), 0)
    dy = np.maximum(np.maximum(rad - yy, yy - (h - 1 - rad)), 0)
    return (dx * dx + dy * dy) <= rad * rad


def clutch_windows(events, gap=6.0, min_kills=3, hold=2.5):
    """events = [(t, mine)] one per new kill row. A clutch = a burst of `min_kills`+ kills with <= `gap` s between
    them, or 2+ kills where one has the red outline (yours). Returns [(a, b)] with the feed visible around it."""
    out, cur = [], []
    for ev in sorted(events):
        ev = (ev + (False,))[:3]
        if cur and ev[0] - cur[-1][0] > gap:
            out.append(cur)
            cur = []
        cur.append(ev)
    if cur:
        out.append(cur)
    res = []
    for c in out:
        mine = sum(1 for _, m, _s in c if m)
        flashy = sum(1 for _, m, sp in c if m and sp)          # your special kills
        if len(c) + flashy >= min_kills or (mine and len(c) >= 2) or flashy:
            res.append((max(0.0, c[0][0] - 0.3), c[-1][0] + hold))
    return res


# 2.35: rows are REDRAWN sharp at SCALE x (like the kill-feed generator PNGs, the creator): our own dark box and red
# outline, and the names / gun / extra icons re-traced from the stream into crisp flat shapes in clean colours
# (no reading of names, so nothing gets misread; no game art copied).
SCALE = 3
RED = (226, 36, 36)
CT_BLUE, T_GOLD, ICON = (120, 166, 240), (236, 192, 92), (246, 246, 246)


def _smooth(a, lo=0.45, hi=0.7):
    t = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _box_alpha(h, w, rad, inset=0.0):
    """Anti-aliased rounded-rectangle coverage (h x w floats)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    qx = np.abs(xx - w / 2) - (w / 2 - inset - rad)
    qy = np.abs(yy - h / 2) - (h / 2 - inset - rad)
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - rad
    return np.clip(0.5 - d, 0.0, 1.0)


def _pick(reg, mask, default):
    px = reg[mask]
    if len(px) < 8:
        return np.array(default, np.float32)
    c = np.median(px, 0).astype(np.float32)
    return c * (240.0 / max(1.0, float(c.max())))      # same hue, full brightness


def _redraw_row(reg, mine, S=SCALE):
    """reg = the row cut out of the frame (h x w x 3, int16). Returns a sharp RGBA row (h*S x w*S)."""
    import cv2
    h, w = reg.shape[:2]
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    lum = (r * 3 + g * 6 + b) // 10
    sat = reg.max(-1) - reg.min(-1)
    cov = np.clip((lum.astype(np.float32) - 85) / 95.0, 0, 1)
    k = np.ones((3, 3), np.uint8)
    cls = {"gold": (r > 135) & (g > 85) & (r - b > 45),
           "blue": (b > 135) & (b - r > 35),
           "white": (lum > 150) & (sat < 70)}
    big = (h * S, w * S)
    out = np.zeros(big + (4,), np.float32)
    # the box: solid dark, rounded; a red outline on your own kills
    a_box = _box_alpha(h * S, w * S, 4 * S, inset=S * 0.5)
    out[..., :3] = np.array(ROW_BG, np.float32)
    out[..., 3] = a_box * 0.92
    if mine:
        ring = np.clip(a_box - _box_alpha(h * S, w * S, 3.5 * S, inset=S * 0.5 + 2 * S), 0, 1)
        out[..., :3] = out[..., :3] * (1 - ring[..., None]) + np.array(RED, np.float32) * ring[..., None]
        out[..., 3] = np.maximum(out[..., 3], ring)
    colours = {"gold": _pick(reg, cls["gold"] & (lum > 140), T_GOLD), "blue": _pick(reg, cls["blue"] & (lum > 120), CT_BLUE),
               "white": np.array(ICON, np.float32)}
    for name in ("white", "gold", "blue"):
        m = cv2.dilate(cls[name].astype(np.uint8), k).astype(bool)
        c = np.where(m, cov, 0.0).astype(np.float32)
        if not c.any():
            continue
        up = _smooth(cv2.resize(c, (w * S, h * S), interpolation=cv2.INTER_CUBIC))
        up = up * a_box                                   # never outside the box
        out[..., :3] = out[..., :3] * (1 - up[..., None]) + colours[name] * up[..., None]
        out[..., 3] = np.maximum(out[..., 3], up)
    out[..., 3] *= 255
    return np.clip(out, 0, 255).astype(np.uint8)


def clean_overlay(video, start, end, box, out_path, when="clutch"):
    """Writes out_path (transparent .mov, 0 s = `start`) holding only the real kill rows, and returns
    ([(a, b)] windows in `video` seconds, stats). when = "clutch" (bursts only) or "all" (every kill)."""
    x, y, w, h = box
    x0, w0 = max(0.0, x - 0.04), min(1.0, w + 0.08)
    y0, h0 = max(0.0, y - 0.03), min(1.0 - max(0.0, y - 0.03), h + 0.06)
    dur = max(0.5, end - start)
    probe = subprocess.run([ffmpeg(), "-hide_banner", "-i", video], capture_output=True, creationflags=CREATE_NO_WINDOW).stderr.decode(errors="ignore")
    import re
    m = re.search(r"(\d{3,5})x(\d{3,5})", probe[probe.find("Video:"):])
    vw, vh = (int(m.group(1)), int(m.group(2))) if m else (1920, 1080)
    cw, ch = int(vw * min(w0, 1 - x0)) // 2 * 2, int(vh * h0) // 2 * 2
    cx, cy = int(vw * x0), int(vh * y0)
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", video,
           "-vf", f"fps={FPS_OV},crop={cw}:{ch}:{cx}:{cy}", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW).stdout
    n = len(raw) // (cw * ch * 3)
    if not n:
        return [], {"frames": 0}
    fr = np.frombuffer(raw[: n * cw * ch * 3], np.uint8).reshape(n, ch, cw, 3)
    rows = [_find_rows(f.astype(np.int16)) for f in fr]
    # a row has to be seen in 3 frames in a row (no blips)
    keep = []
    for i in range(n):
        ok = []
        for r in rows[i]:
            near = sum(1 for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < n and any(abs(q[0] - r[0]) < 8 for q in rows[j]))
            if near >= 2:
                ok.append(r)
        keep.append(ok)
    events, seen = [], []        # a new kill = a row at a spot that was empty during the last 0.5 s
    for i in range(n):
        for r in keep[i]:
            prev = [q for j in range(max(0, i - 15), i) for q in keep[j]]
            if not any(abs(q[0] - r[0]) < 8 for q in prev):
                fi = fr[min(n - 1, i + 3)].astype(np.int16)
                mine, ic = _is_mine(fi, r), _icons(fi, r)
                # your kill with an extra icon (no-scope, knife, headshot, wallbang...) counts double
                events.append((i / FPS_OV, mine, ic >= 2))
    wins = [(0.0, dur)] if when == "all" else clutch_windows(events)
    stats = {"frames": n, "kills": len(events), "mine": sum(1 for _, m, _s in events if m), "special": sum(1 for *_, sp in events if sp), "clutches": len(wins)}
    if not events or not wins:
        return [], stats
    alpha_on = np.zeros(n, bool)
    for a, b in wins:
        alpha_on[int(a * FPS_OV):int(b * FPS_OV) + 1] = True
    S = SCALE
    enc = subprocess.Popen([ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{cw * S}x{ch * S}",
                            "-r", str(FPS_OV), "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb", out_path],
                           stdin=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:
        for i in range(n):
            out = np.zeros((ch * S, cw * S, 4), np.uint8)
            if alpha_on[i]:
                f = fr[i].astype(np.int16)
                rows_ = sorted(keep[i])
                for j, (ya, yb, xa, xb) in enumerate(rows_):
                    ya, yb = ya + 1, yb - 1                       # a little tighter than the cut-out
                    if j + 1 < len(rows_) and rows_[j + 1][0] + 1 < yb + 3:   # keep a small gap between rows
                        yb = max(ya + 8, (yb + rows_[j + 1][0] + 1) // 2 - 1)
                    if j > 0 and rows_[j - 1][1] - 1 > ya - 3:
                        ya = min(yb - 8, (ya + rows_[j - 1][1] - 1) // 2 + 2)
                    row = _redraw_row(f[ya:yb, xa:xb], _is_mine(f, (ya, yb, xa, xb)), S)
                    dst = out[ya * S:yb * S, xa * S:xb * S]
                    a = row[..., 3:4].astype(np.float32) / 255
                    dst[..., :3] = (row[..., :3] * a + dst[..., :3] * (1 - a)).astype(np.uint8)
                    dst[..., 3] = np.maximum(dst[..., 3], row[..., 3])
            enc.stdin.write(out.tobytes())
        enc.stdin.close()
        enc.wait()
    except Exception:
        enc.kill()
        raise
    stats["size"] = (cw * S, ch * S)
    return [(round(start + a, 2), round(start + b, 2)) for a, b in wins], stats
