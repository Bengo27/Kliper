"""Render a clip to a 1080x1920 TikTok from an edit plan (see director.py):
cold-open hook, jump cuts, separate webcam / gameplay zooms, reaction cutaways,
sound effects, animated captions, loudness normalising."""
import os
import re
import shutil
import subprocess
import numpy as np

from .util import ffmpeg, ffprobe, run, FONTS_DIR, ENGINE_DIR, log, work_threads, CREATE_NO_WINDOW, probe
from . import captions as cap
from . import director as dr

OW, OH = 1080, 1920


def _even(v):
    return int(round(v / 2.0)) * 2


# ---------------------------------------------------------------- zoom curves
def _zoom_expr(windows, kind):
    """zoompan z-expression made of hard cuts: the panel jumps to a bigger scale
    at the start of a window and jumps back at the end (no animated zoom)."""
    if not windows:
        return "1"
    lo, k, hi = (0.18, 0.06, 0.38) if kind == "cam" else (0.14, 0.05, 0.30)
    terms = []
    for a, b, s in windows:
        amp = min(hi, lo + k * min(float(s), 2.5))
        terms.append(f"{amp:.3f}*between(it,{a:.3f},{b - 0.001:.3f})")
    return "1+" + "+".join(terms)


def _zp(src, dst, w, h, fps, z, cx=0.5, cy=0.5):
    return (f"[{src}]zoompan=z='{z}':d=1:s={w}x{h}:fps={fps:.3f}:"
            f"x='iw*{cx:.4f}-iw/zoom/2':y='ih*{cy:.4f}-ih/zoom/2'[{dst}]")


# ---------------------------------------------------------------- layout
def _resolve_layout(webcam, layout):
    if layout in ("auto", "fullcam") and webcam and webcam.get("wide"):
        return "camblur"   # 0.1.2: two people too far apart for a 9:16 crop: the whole picture, sharp over blurred
    if layout == "auto":
        if webcam and webcam.get("found"):
            return "fullcam" if webcam.get("layout") == "fullcam" else "split"
        return "blur"
    has_box = bool(webcam and webcam.get("found") and webcam.get("box") and webcam.get("layout") != "fullcam")
    if layout == "split" and not has_box:
        return "blur"
    if layout == "camover" and not has_box:
        return "crop"
    return layout


BLUR_GAME_ZOOM = 1.2   # 2.37.1 (the creator: no-webcam gameplay "too small"): show the middle 1/1.2 of the width, bigger


def _blur_game(W, H):
    """'blur' layout (no webcam): crop width, crop x and TikTok height of the gameplay in the middle."""
    cw = _even(W / BLUR_GAME_ZOOM)
    return cw, (W - cw) // 2, _even(OW * H / cw)


CAMBLUR_CENTER = 0.42  # 2.28 'camblur': where the middle of the sharp webcam sits (a bit above centre)


def camblur_box(info, webcam):
    """2.28 webcam layout: the sharp webcam (full width) over a blurred, zoomed copy of itself.
    Returns (crop w, h, x, y) in the stream and (top, height) of the sharp webcam in the TikTok."""
    W, H = info["width"], info["height"]
    if webcam and webcam.get("found") and webcam.get("box") and webcam.get("layout") != "fullcam":
        cw, ch, cx, cy = _cam_geometry(info, webcam)[:4]
    else:   # the whole picture is the webcam (Just Chatting) or no webcam found: use the whole picture
        cw, ch, cx, cy = W // 2 * 2, H // 2 * 2, 0, 0
    f = (webcam or {}).get("face")
    if f:   # 2.28: zoom in on the face (about 3.4 face-widths across), keeping the cam's shape
        tw = min(cw, max(cw * 0.4, f[2] * W * 3.4))
        th = min(ch, tw * ch / max(1, cw))
        fx, fy = (f[0] + f[2] / 2) * W, (f[1] + f[3] / 2) * H
        nx = min(max(cx, fx - tw / 2), cx + cw - tw)
        ny = min(max(cy, fy - th * 0.45), cy + ch - th)
        cw, ch, cx, cy = _even(tw), _even(th), _even(nx), _even(ny)
    fh = _even(min(OH * 0.62, OW * ch / max(1, cw)))
    top = _even(min(max(0, OH * CAMBLUR_CENTER - fh / 2), OH - fh))
    return (cw, ch, cx, cy), (top, fh)


LETTERBOX_CENTER = 0.47   # 'letterbox' (full stream): where the middle of the 16:9 picture sits


def letterbox_box(info):
    """(top, height) of the whole 16:9 stream picture in the 'letterbox' layout."""
    h = _even(OW * info["height"] / max(1, info["width"]))
    return _even(OH * LETTERBOX_CENTER - h / 2), h


CAMOVER = (0.035, 0.56, 0.56)   # 'camover' (Fortnite): webcam box x, y (fractions) and width


def _cam_geometry(info, webcam, inset=(0.02, 0.02, 0.02, 0.05)):
    """Webcam crop. A small safety margin keeps overlays that touch the cam
    (sponsor banners, borders, alerts) out of the shot; most on the bottom edge."""
    W, H = info["width"], info["height"]
    bx, by, bw, bh = webcam["box"]
    if webcam.get("method") != "manual":
        l, t, r, b = inset
        bx, by, bw, bh = bx + bw * l, by + bh * t, bw * (1 - l - r), bh * (1 - t - b)
    cw, ch = int(bw * W) // 2 * 2, int(bh * H) // 2 * 2
    cx, cy = -(-int(bx * W) // 2) * 2, -(-int(by * H) // 2) * 2
    # face centre inside the cam box (for zooming on the face)
    f = webcam.get("face")
    if f:
        fcx = ((f[0] + f[2] / 2) - bx) / max(bw, 1e-6)
        fcy = ((f[1] + f[3] / 2) - by) / max(bh, 1e-6)
    else:
        fcx, fcy = 0.5, 0.45
    return cw, ch, cx, cy, min(0.85, max(0.15, fcx)), min(0.85, max(0.15, fcy))


def cam_panel_h(info, webcam, ratio=None):
    """Height of the webcam panel. With a ratio (the editor style uses ~0.42) the panel
    has a fixed share of the screen and the webcam is cropped to fit."""
    if ratio:
        return _even(OH * float(ratio))
    W, H = info["width"], info["height"]
    cw, ch = _even(webcam["box"][2] * W), _even(webcam["box"][3] * H)
    return _even(min(900, max(560, OW * ch / max(cw, 1))))


def seam_y(info, webcam, layout, ratio=None):
    """Height (px) of the webcam panel in split layout = where webcam meets gameplay."""
    if layout != "split" or not (webcam and webcam.get("box")):
        return None
    return cam_panel_h(info, webcam, ratio)


# 2.18: game HUD parts a 9:16 crop cuts off. box = where it is in the 16:9 stream (x, y, w, h as fractions),
# width = how wide it's shown (fraction of the TikTok), align = left / center / right.
HUD_PARTS = {
    # 2.36.1: hidden (the creator: "remove the cs2 top bar, doesn't make sense to be there"); kept only for fit_cs2_bar's default
    "cs2_hud": {"name": "CS2 top bar (score, timer, players)", "box": [0.345, 0.0, 0.31, 0.075], "width": 1.0, "align": "center",
                "hidden": True},
    # 2.22: tighter around the kill rows (they sit ~6-16 % down, right-aligned), shown in our own card
    "cs2_killfeed": {"name": "CS2 kill feed", "box": [0.785, 0.055, 0.21, 0.105], "width": 0.74, "align": "right",
                     "killfeed": "cs2"},
    # 2.36.1: the Call of Duty / Fortnite kill feeds (guessed boxes) were removed (the creator: "these kill feeds don't
    # seem to work"). TikToks that still list them simply skip them (_overlays only keeps names in HUD_PARTS).
}


def fit_cs2_bar(video, start, end, n=10):
    """2.33: the CS2 top bar's real width in this stream. Its size depends on the HUD scale and on player
    pictures (5 a side), so the fixed box cut the outer players off. The bar's tile borders stay in the same
    place all clip long (even when the pictures or the round change) while the game behind moves: sample
    frames, keep the columns with edges in most frames, grow out from the timer in the middle.
    Returns [x, y, w, h] (fractions) or None to use the default box."""
    import numpy as np
    import subprocess
    from .util import ffmpeg
    W, H = 960, 54   # top 7.5 % of the frame
    span = max(0.5, end - start)
    frames = []
    for k in range(n):
        t = start + span * (k + 0.5) / n
        r = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                            "-vf", f"crop=iw:ih*0.075:0:0,scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if len(r.stdout) == W * H:
            frames.append(np.frombuffer(r.stdout, np.uint8).reshape(H, W).astype(np.float32))
    if len(frames) < 5:
        return None
    dx = np.abs(np.diff(np.stack(frames), axis=2)).mean(axis=1)    # frames x columns
    pers = np.median(dx, axis=0)                                    # edges that are there in most frames
    k = 15
    pers = np.array([pers[max(0, i - k // 2):i + k // 2 + 1].max() for i in range(len(pers))])
    edgy = pers >= 8
    c, gap = len(edgy) // 2, int(W * 0.02)
    if edgy[c - int(W * 0.05):c + int(W * 0.05)].mean() < 0.6:     # no bar in the middle (HUD off, menu, ...)
        return None

    def reach(step):
        x, last, miss = c, c, 0
        while 0 <= x + step < len(edgy):
            x += step
            if edgy[x]:
                last, miss = x, 0
            else:
                miss += 1
                if miss > gap:
                    break
        return abs(last - c)
    half = min(reach(-1), reach(1)) / W            # the bar is centred: trust the shorter side
    default = HUD_PARTS["cs2_hud"]["box"]
    if half <= default[2] / 2 + 0.01 or half > 0.36:   # no wider than the default, or the scene is too still to tell
        return None
    half += 0.004
    return [round(0.5 - half, 4), 0.0, round(2 * half, 4), default[3]]


def _overlays(info, webcam, layout, opts):
    """[(crop, out_w, x, y)] for the HUD parts switched on, in the layout's 1080x1920 picture."""
    names = [n for n in (opts.get("overlays") or []) if n in HUD_PARTS and not HUD_PARTS[n].get("hidden")]
    if not names or layout in ("letterbox", "camblur"):   # full stream shows the HUD already; webcam-only has no game
        return []
    W, H = info["width"], info["height"]
    top = seam_y(info, webcam, layout, opts.get("cam_ratio")) or int(OH * 0.12)   # top of the gameplay
    out = []
    for n in names:
        p = HUD_PARTS[n]
        bx, by, bw, bh = (opts.get("hud_fit") or {}).get(n) or p["box"]
        cw, ch = _even(bw * W), _even(bh * H)
        crop = f"{cw}:{ch}:{_even(bx * W)}:{_even(by * H)}"
        w = _even(OW * p["width"])
        x = {"left": 0, "center": (OW - w) // 2, "right": OW - w}[p["align"]]
        border = p["align"] != "center" and not p.get("killfeed")   # 2.22: kill feeds get our own card instead
        out.append((crop, w, x, _even(top + (10 if border else 0)), border, n))
        top += int(w * ch / max(1, cw)) + (20 if border else 0)
    return out


KF_CARD = {"bg": "0x18181b", "alpha": 0.9, "accent": "0xff4655", "radius": 22, "pad": 14, "bar": 10, "margin": 24}


def _killfeed_card(src, crop, w, y, lab_in, lab_out, enable, windows, fps, align="right"):
    """2.22: our own kill-feed card: the game's real kill rows inside a rounded dark card with a red accent bar,
    sliding in from the right edge each time a kill shows. Returns filter-graph lines."""
    c = KF_CARD
    cw, ch_, cx, cy = [int(v) for v in crop.split(":")]
    inner = _even(w - 2 * c["pad"] - c["bar"])
    rows_h = _even(inner * ch_ / max(1, cw))
    W, H = inner + 2 * c["pad"] + c["bar"], rows_h + 2 * c["pad"]
    R = c["radius"]
    left = align == "left"
    x0 = c["margin"] if left else OW - W - c["margin"]
    # rounded-corner alpha, worked out once and looped (geq is slow per frame)
    a_full = int(255 * c["alpha"])
    inside = (f"lte(hypot(max(abs(X-{W / 2})-{W / 2 - R},0),max(abs(Y-{H / 2})-{H / 2 - R},0)),{R})")
    out = [
        f"color=c={c['bg']}:s={W}x{H}:r={fps:.3f},format=rgba,"
        f"drawbox=x=0:y=0:w={c['bar']}:h={H}:color={c['accent']}:t=fill,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{a_full}*{inside}',trim=end_frame=1,loop=loop=-1:size=1,setpts=N/{fps:.3f}/TB[{src}bg]",
        f"[{src}]crop={crop},scale={inner}:{rows_h},setsar=1,format=rgba[{src}rows]",
        f"[{src}bg][{src}rows]overlay={c['bar'] + c['pad']}:{c['pad']}:shortest=1[{src}card]",
    ]
    xs = str(x0)
    if windows:   # slide in from its side of the screen over 0.22 s at the start of each kill
        slide = "+".join(f"between(t,{a:.2f},{a + 0.22:.2f})*{W + c['margin']}*pow(1-(t-{a:.2f})/0.22,2)" for a, b in windows)
        xs = f"{x0}{'-' if left else '+'}({slide})"
    out.append(f"[{lab_in}][{src}card]overlay=x='{xs}':y={y}:eval=frame{enable}[{lab_out}]")
    return out


def kfc_size(path):
    try:
        i = probe(path)
        return int(i["width"]), int(i["height"])
    except Exception:
        return 488, 178


def killfeed_times(video, clip, plan, opts, webcam=None, work=None):
    """2.22: {part: [(a, b)] TikTok times when that game's kill feed shows a kill}. A part left out = show it the
    whole time. Only for kill-feed overlays that are switched on, with `killfeed_auto` on (default)."""
    parts = [n for n in (opts.get("overlays") or []) if HUD_PARTS.get(n, {}).get("killfeed")]
    if not parts or not opts.get("killfeed_auto", True):
        return {}
    if _resolve_layout(webcam, opts.get("layout", "auto")) == "letterbox":
        return {}
    from . import killfeed
    res = {}
    clips = plan.setdefault("killfeed_clip", {})
    clean = (opts.get("killfeed_style") or "clean") == "clean"
    for n in parts:
        p = HUD_PARTS[n]
        if clean and p["killfeed"] == "cs2" and opts.get("killfeed_draw", True):
            # 2.35: read every kill and draw the rows from scratch, sharp (needs the CS2 icons from the user's own
            # CS2 install; without them -> the traced rows below)
            try:
                from . import killread
                kills = killread.read_kills(video, clip["start"], clip["end"], p["box"])
            except Exception as e:
                log.warning("Reading the kill feed failed: %s", e)
                kills = None
            if kills is not None:
                if (opts.get("killfeed_when") or "clutch") == "clutch":
                    wins_ = killfeed.clutch_windows([(k["t"], k["mine"], bool(k["extras"]) and k["mine"]) for k in kills])
                    kills = [k for k in kills if any(a - 0.5 <= k["t"] <= b for a, b in wins_)]
                # 2.35.3: names as read (shown in the editor) + his fixes {read: typed} (the creator: OCR gets names wrong)
                fix = {k: v for k, v in (opts.get("kf_names") or {}).items() if isinstance(v, str)}
                plan["kill_names"] = list(dict.fromkeys(n for k in kills for n in (k["killer"], k["victim"]) if n))
                kills = [dict(k, killer=fix.get(k["killer"], k["killer"]), victim=fix.get(k["victim"], k["victim"]))
                         for k in kills]
                plan["kill_rows"] = _kill_rows(kills, clip, plan)
                log.info("Kill feed drawn: %d kills (%s)", len(kills), ", ".join(f"{k['killer']} {k['gun']} {k['victim']}" for k in kills))
                continue
        if clean and p["killfeed"] == "cs2" and work:
            # 2.22: clean rows (no box, no gameplay), only in a sick clutch (setting killfeed_when: clutch / all)
            ovp = os.path.join(work, f"kf_{clip['id']}_{n}.mov")
            try:
                wins, st = killfeed.clean_overlay(video, clip["start"], clip["end"], p["box"], ovp,
                                                  opts.get("killfeed_when") or "clutch")
                log.info("Kill feed (%s): %s", n, st)
            except Exception as e:
                log.warning("Clean kill feed failed: %s", e)
                wins = None
            if wins and os.path.isfile(ovp):
                clips[n] = ovp
        else:
            wins = killfeed.windows(video, clip["start"], clip["end"], p["box"], mode=p["killfeed"])
        if wins is None:   # the check failed: better to show it all the time than never
            continue
        out = []
        for a, b in wins:
            a, b = a - clip["start"], b - clip["start"]
            off = plan["t_off"]
            for s, e in plan["segs"]:
                lo, hi = max(a, s), min(b, e)
                if hi - lo > 0.05:
                    o0, o1 = off + lo - s, off + hi - s
                    if out and o0 - out[-1][1] < 0.3:
                        out[-1] = (out[-1][0], o1)
                    else:
                        out.append((o0, o1))
                off += e - s
        res[n] = [(round(a, 2), round(b, 2)) for a, b in out]
    return res


CAM_MIN_W = 0.16   # 2.35.3: smallest webcam crop, as a fraction of the stream's width


def _kill_rows(kills, clip, plan):
    """2.35: read kills (stream time) -> rows on the TikTok's timeline, following the cuts (a row cut in two by a
    jump cut becomes two pieces; only the first slides in, only the last fades out)."""
    rows = []
    for i, k in enumerate(kills):
        a, b = k["t"] - clip["start"], k["t_end"] - clip["start"]
        off, pieces = plan["t_off"], []
        for s0, e0 in plan["segs"]:
            lo, hi = max(a, s0), min(b, e0)
            if hi - lo > 0.05:
                pieces.append((off + lo - s0, off + hi - s0))
            off += e0 - s0
        for j, (t0, t1) in enumerate(pieces):
            rows.append(dict(k, t0=round(t0, 3), t1=round(t1, 3), enter=(j == 0), leave=(j == len(pieces) - 1),
                             kid=i, order=k["t"]))
    return rows


def frame_box(opts):
    """2.18 'framed' look: the whole edit shrunk onto a black background, with room above for the title.
    Returns (scale, x, y) or None when the frame is off."""
    if not opts.get("frame"):
        return None
    s = max(0.5, min(0.95, float(opts.get("frame_scale", 0.78))))
    w, h = _even(OW * s), _even(OH * s)
    return s, _even((OW - w) / 2), _even((OH - h) * 0.62)


def _caption_opts(opts, info, webcam):
    """Where subtitles, the title and the name tag go (in the final 1080x1920 picture)."""
    o = dict(opts)
    fb = frame_box(opts)
    sc, fx, fy = fb if fb else (1.0, 0, 0)
    layout = _resolve_layout(webcam, opts.get("layout", "auto"))
    y = seam_y(info, webcam, layout, opts.get("cam_ratio"))
    seam = (fy + y * sc) if y else None                       # webcam/gameplay line, in final pixels
    title_at_seam = bool(o.get("title_bar")) and o.get("title_pos") == "seam" and seam
    if layout == "letterbox" and not fb:
        # full stream: title above the picture, subtitles and channel name below it
        top, h = letterbox_box(info)
        if o.get("title_bar"):
            o["title_y"] = max(0.03, (top - 230) / OH) if o.get("title_pos") != "middle" else 0.38
        o["position"] = (top + h + 150) / OH
        o["watermark_y"] = (top + h + 60) / OH
        return o
    if o.get("title_bar") and o.get("title_pos") == "middle" and not title_at_seam:
        o["title_y"] = 0.33   # Fortnite: the quote in the middle, above the webcam box
    if layout == "camblur" and not fb:   # 2.28: title above the webcam, subtitles and name below it
        top, fh = camblur_box(info, webcam)[1]
        if o.get("title_bar") and o.get("title_pos") != "middle":
            o["title_y"] = max(0.03, (top - 150) / OH)
        o["position"] = min(0.86, (top + fh + 120) / OH)
        o["watermark_y"] = min(0.93, (top + fh + 260) / OH)
        return o
    if layout == "camover":   # keep subtitles and the name clear of the webcam box (lower left)
        o["position"], o["watermark_y"] = 0.49, 0.95
    game_top = seam
    if seam and not fb:   # 2.25: below the CS2 top bar when it is shown, so nothing sits on top of it
        for crop, w, x, oy, border, n in _overlays(info, webcam, layout, opts):
            if not HUD_PARTS[n].get("killfeed") and w >= OW * 0.9:   # a full-width bar (CS2 top bar)
                cw, ch = [int(v) for v in crop.split(":")[:2]]
                game_top = max(game_top, oy + int(w * ch / max(1, cw)))
    if o.get("subs_mode") == "key" and seam:
        o["position"] = game_top / OH + 0.075      # 2.24: key lines sit at the top of the gameplay
    elif o.get("caption_seam", True) and seam:
        o["position"] = (seam / OH + 0.17) if title_at_seam else seam / OH
    elif fb and o.get("position") is not None:
        o["position"] = (fy + float(o["position"]) * OH * sc) / OH
    if o.get("title_bar") and o.get("title_pos") != "middle":
        if title_at_seam:
            o["title_y"] = (seam - 112) / OH      # on the webcam, just above the webcam/gameplay line
            o["title_seam"] = seam / OH           # 2.35: 2+ lines: the gap between lines sits ON the webcam/gameplay line
        elif fb:
            o["title_y"] = max(0.015, (fy / 2 - 60) / OH)   # middle of the black band above the video
        else:
            o["title_y"] = float(o.get("title_y", 0.06))
    if o.get("watermark_pos") == "seam" and seam:
        # 2.25 (the creator): the name tag must not cover the webcam: it goes just under the webcam line, on the
        # gameplay (top left, below the CS2 top bar when shown)
        o["watermark_y"] = (game_top + 52) / OH
        o["watermark_x"] = (fx + 36) / OW
        o["watermark_size"] = 0.85
    elif fb:
        o["watermark_y"] = (fy + float(o.get("watermark_y", 0.84)) * OH * sc) / OH
    return o


def game_panel(info, webcam, layout, cam_ratio=None):
    """2.28: size (w, h) of the gameplay part of the TikTok in this layout, or None when there is none."""
    if layout == "split":
        return OW, OH - cam_panel_h(info, webcam, cam_ratio)
    if layout in ("crop", "camover"):
        return OW, OH
    return None


def default_game_box(info, webcam, layout, game_focus=0.5, cam_ratio=None):
    """2.28: the part of the stream the gameplay is cut from when you haven't moved it: [x, y, w, h] fractions."""
    W, H = info["width"], info["height"]
    p = game_panel(info, webcam, layout, cam_ratio)
    if not p:
        return None
    gw = _even(min(W, H * p[0] / p[1]))
    if layout == "split":
        gx = _split_game_x(info, webcam, gw, game_focus)
    else:
        gx = _even(min(max(0, game_focus * W - gw / 2), W - gw))
    return [round(gx / W, 4), 0.0, round(gw / W, 4), 1.0]


def _game_crop(info, box, panel):
    """[x, y, w, h] fractions -> crop w, h, x, y in pixels with the panel's shape (the box can be smaller = zoomed in)."""
    W, H = info["width"], info["height"]
    asp = panel[0] / panel[1]
    bw = max(0.1, min(1.0, float(box[2]))) * W
    gh = min(H, bw / asp)
    gw = gh * asp
    gx = min(max(0, float(box[0]) * W), W - gw)
    gy = min(max(0, float(box[1]) * H), H - gh)
    return _even(gw), _even(gh), _even(gx), _even(gy)


def _split_game_x(info, webcam, gw, game_focus):
    W = info["width"]
    gx = (W - gw) * game_focus
    bx, bw = webcam["box"][0], webcam["box"][2]
    cam_l, cam_r = bx * W - W * 0.01, (bx + bw) * W + W * 0.01
    if cam_r > gx and cam_l < gx + gw:  # keep the cam overlay out of the gameplay half
        if cam_l < W / 2 and cam_r - gx <= W * 0.15 and cam_r + gw <= W:
            gx = cam_r
        elif cam_l >= W / 2 and (gx + gw) - cam_l <= W * 0.15 and cam_l - gw >= 0:
            gx = cam_l - gw
    return _even(min(max(0, gx), W - gw))


def _layout_graph(info, webcam, layout, plan, fps, game_focus=0.5, still=False, opts_tight=True, cam_ratio=None,
                  game_box=None):
    """From [vc] to [lay] (1080x1920). Returns list of filter strings."""
    W, H = info["width"], info["height"]
    dur = plan["out_dur"]
    zoom_on = not still
    g = []
    cam_pts, game_pts = plan["cam_zooms"], plan["game_zooms"]
    if layout == "split":
        cw, ch, cx, cy, fcx, fcy = _cam_geometry(info, webcam)
        cam_h = cam_panel_h(info, webcam, cam_ratio)
        f = webcam.get("face")
        if f and opts_tight:
            # frame the webcam panel tighter on the face (head and shoulders)
            aspect = OW / cam_h
            face_w = f[2] * W
            # 2.28: tighter on the face (was 4.2 face-widths, min half the cam): bigger face, same for every streamer
            # 2.35.3: never zoom into less than 1/6 of the screen width (~3.5x): a streamer far back in a small cam
            # (Kai Cenat) was a blurry face patch blown up ~7x, and leaning away left only the chair
            tw = min(cw, max(cw * 0.38, face_w * 3.2, W * CAM_MIN_W))
            th = min(ch, tw / aspect)
            tw = min(cw, th * aspect)
            fx_abs, fy_abs = cx + fcx * cw, cy + fcy * ch
            nx = min(max(cx, fx_abs - tw / 2), cx + cw - tw)
            ny = min(max(cy, fy_abs - th * 0.45), cy + ch - th)
            fcx, fcy = (fx_abs - nx) / tw, (fy_abs - ny) / th
            cx, cy, cw, ch = _even(nx), _even(ny), _even(tw), _even(th)
        game_h = OH - cam_h
        if game_box:   # 2.28: you placed the gameplay box in the Timeline
            gw, gh, gx, gy = _game_crop(info, game_box, (OW, game_h))
        else:
            gw, gh, gy = _even(min(W, H * OW / game_h)), H, 0
            gx = _split_game_x(info, webcam, gw, game_focus)
        cuts = plan["cutaways"] if zoom_on else []
        g.append(f"[vc]split={3 if cuts else 2}[c0][g0]" + ("[f0]" if cuts else ""))
        g.append(f"[c0]crop={cw}:{ch}:{cx}:{cy},scale={OW}:{cam_h}:force_original_aspect_ratio=increase,"
                 f"crop={OW}:{cam_h},setsar=1[c1]")
        g.append(f"[g0]crop={gw}:{gh}:{gx}:{gy},scale={OW}:{game_h}:force_original_aspect_ratio=increase,"
                 f"crop={OW}:{game_h},setsar=1[g1]")
        if zoom_on:
            g.append(_zp("c1", "cam", OW, cam_h, fps, _zoom_expr(cam_pts, "cam"), fcx, fcy))
            g.append(_zp("g1", "game", OW, game_h, fps, _zoom_expr(game_pts, "game")))
        else:
            g.append("[c1]null[cam]")
            g.append("[g1]null[game]")
        g.append("[cam][game]vstack=inputs=2[stk]")
        if cuts:
            # full-screen webcam cutaway, centred on the face
            fw = _even(min(cw, ch * 9 / 16))
            fx = _even(min(max(cx, cx + fcx * cw - fw / 2), cx + cw - fw))
            g.append(f"[f0]crop={fw}:{ch}:{fx}:{cy},scale={OW}:{OH},setsar=1,"
                     f"zoompan=z='1.08':d=1:s={OW}x{OH}:fps={fps:.3f}:"
                     f"x='iw/2-iw/zoom/2':y='ih*{fcy:.3f}-ih/zoom/2'[full]")
            en = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in cuts)
            g.append(f"[stk][full]overlay=0:0:enable='{en}'[lay]")
        else:
            g.append("[stk]null[lay]")
        return g
    if layout == "camblur":
        # 2.28 webcam only: the sharp webcam across the middle, the same webcam blurred and zoomed behind it.
        # No zooms (the whole thing is the webcam: a zoom would be a small webcam punch-in).
        (cw, ch, cx, cy), (top, fh) = camblur_box(info, webcam)
        g.append(f"[vc]crop={cw}:{ch}:{cx}:{cy},setsar=1,split=2[b0][f0]")
        g.append(f"[b0]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
                 f"boxblur=28:4,eq=brightness=-0.10:saturation=1.1[bg]")
        g.append(f"[f0]scale={OW}:{fh}:force_original_aspect_ratio=increase,crop={OW}:{fh},setsar=1[fg]")
        g.append(f"[bg][fg]overlay=0:{top}[lay]")
        return g
    if layout == "letterbox":
        # 2.20 'full stream': the whole 16:9 picture (their webcam corner, kill feed, HUD) on black. No zooms.
        top, h = letterbox_box(info)
        g.append(f"[vc]scale={OW}:{h},setsar=1,pad={OW}:{OH}:0:{top}:black[lay]")
        return g
    if layout == "camover":
        # 2.20 Fortnite look: full-screen gameplay, the webcam as a box over it (lower left)
        cw, ch, cx, cy, fcx, fcy = _cam_geometry(info, webcam)
        if game_box:   # 2.28
            gcw, gch, gx, gy = _game_crop(info, game_box, (OW, OH))
        else:
            gcw, gch, gy = _even(min(W, H * 9 / 16)), H, 0
            gx = _even(min(max(0, game_focus * W - gcw / 2), W - gcw))
        bx, by, bw = CAMOVER
        camw = _even(OW * bw)
        g.append("[vc]split=2[c0][g0]")
        g.append(f"[g0]crop={gcw}:{gch}:{gx}:{gy},scale={OW}:{OH},setsar=1[gb0]")
        if zoom_on:
            g.append(_zp("gb0", "gb", OW, OH, fps, _zoom_expr(game_pts, "game")))
        else:
            g.append("[gb0]null[gb]")
        g.append(f"[c0]crop={cw}:{ch}:{cx}:{cy},scale={camw - 12}:-2,setsar=1,pad=iw+12:ih+12:6:6:color=white[cb]")
        g.append(f"[gb][cb]overlay={_even(OW * bx)}:{_even(OH * by)}[lay]")
        return g
    pts = dr._merge(list(cam_pts) + list(game_pts))
    if layout in ("fullcam", "crop"):
        cw, chh, cy = _even(min(W, H * 9 / 16)), H, 0
        fx = (webcam or {}).get("face_cx", game_focus) if layout == "fullcam" else game_focus
        cx = _even(min(max(0, fx * W - cw / 2), W - cw))
        if layout == "crop" and game_box:   # 2.28
            cw, chh, cx, cy = _game_crop(info, game_box, (OW, OH))
        g.append(f"[vc]crop={cw}:{chh}:{cx}:{cy},scale={OW}:{OH},setsar=1[l0]")
        if zoom_on:
            g.append(_zp("l0", "lay", OW, OH, fps, _zoom_expr(pts, "cam" if layout == "fullcam" else "game"),
                         0.5, 0.4 if layout == "fullcam" else 0.5))
        else:
            g.append("[l0]null[lay]")
        return g
    # blur: whole gameplay frame over a blurred copy; zoom the gameplay
    gcw, gcx, fg_h = _blur_game(W, H)
    g.append("[vc]split=2[b0][f0]")
    g.append(f"[b0]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},boxblur=24:4,eq=brightness=-0.08[bg]")
    g.append(f"[f0]crop={gcw}:{H}:{gcx}:0,scale={OW}:{fg_h},setsar=1[f1]")
    if zoom_on:
        g.append(_zp("f1", "fg", OW, fg_h, fps, _zoom_expr(pts, "game")))
    else:
        g.append("[f1]null[fg]")
    g.append("[bg][fg]overlay=(W-w)/2:(H-h)/2-80[lay]")
    return g


# ---------------------------------------------------------------- sound effects
def _sfx_files(work):
    files = {
        "whoosh": (os.path.join(work, "sfx_whoosh.wav"),
                   "anoisesrc=d=0.5:color=pink:amplitude=0.9,highpass=f=500,lowpass=f=7000,"
                   "afade=t=in:d=0.28:curve=exp,afade=t=out:st=0.28:d=0.22"),
        "boom": (os.path.join(work, "sfx_boom.wav"),
                 "sine=f=55:d=0.8,volume=2.2,afade=t=out:st=0.04:d=0.75:curve=exp"),
    }
    for kind, (path, src) in files.items():
        if not os.path.isfile(path):
            run([ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", src, "-ar", "48000", "-ac", "2", path])
    return {k: v[0] for k, v in files.items()}


# ---------------------------------------------------------------- command
def build_command(video, info, clip, plan, ass_name, webcam, opts, out_path, work=None, still_at=None):
    fps = min(60.0, info["fps"] or 30.0)
    dur = clip["end"] - clip["start"]
    has_a = info["has_audio"] and still_at is None
    teaser = plan.get("teaser")
    # the teaser is read as its own input so ffmpeg never has to buffer the whole
    # clip in memory while it jumps ahead to the peak
    pieces = ([(1, 0.0, teaser[1] - teaser[0])] if teaser else []) + [(0, a, b) for a, b in plan["segs"]]
    g = []
    n = len(pieces)
    for i, (src, a, b) in enumerate(pieces):
        g.append(f"[{src}:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
        if has_a:
            # per-piece volume from the timeline (dB; None/absent = 0 dB, "mute" = silent)
            k = i - (1 if teaser else 0)
            gains = plan.get("seg_gain") or []
            gdb = gains[k] if 0 <= k < len(gains) else 0
            vol = 0.0 if gdb == "mute" else 10 ** (float(gdb or 0) / 20.0)
            g.append(f"[{src}:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,volume={vol:.4f},"
                     f"afade=t=in:d=0.03,afade=t=out:st={max(0.0, b - a - 0.03):.3f}:d=0.03[a{i}]")
    layout = _resolve_layout(webcam, opts.get("layout", "auto"))
    ovs = _overlays(info, webcam, layout, opts)
    kfc = {} if (teaser or still_at is not None) else {k: v for k, v in (plan.get("killfeed_clip") or {}).items()
                                                      if os.path.isfile(v)}
    kf_idx = {}
    for j, (nm, path_) in enumerate(kfc.items()):
        kf_idx[nm] = 1 + j
        pcs = []
        for i, (src, a, b) in enumerate(pieces):
            g.append(f"[{kf_idx[nm]}:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[kp{j}_{i}]")
            pcs.append(f"[kp{j}_{i}]")
        g.append("".join(pcs) + f"concat=n={n}:v=1:a=0,format=rgba[kfv{j}]")
    vc = "vcx" if ovs else "vc"
    if has_a:
        g.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[{vc}][ac0]")
    else:
        g.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[{vc}]")
    if ovs:
        g.append(f"[vcx]split={len(ovs) + 1}[vc]" + "".join(f"[os{k}]" for k in range(len(ovs))))
    g += _layout_graph(info, webcam, layout, plan, fps, float(opts.get("game_focus", 0.5)), still=still_at is not None,
                       opts_tight=bool(opts.get("cam_tight", True)), cam_ratio=opts.get("cam_ratio"),
                       game_box=clip.get("game_box"))
    lab = "lay"
    if ovs:   # 2.18: parts of the game a 9:16 crop cuts off (CS2 top bar, kill feed), pasted on top
        hide = None
        if layout == "split" and plan["cutaways"] and still_at is None:
            hide = "not(" + "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in plan["cutaways"]) + ")"
        kfs = plan.get("killfeed") or {}   # 2.22: kill feeds only while they show a kill (missing = always)
        for k, (crop, w, x, y, border, name) in enumerate(ovs):
            cond = [hide] if hide else []
            kf = kfs.get(name) if HUD_PARTS[name].get("killfeed") else None
            if kf is not None:
                cond.append("(" + ("+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in kf) or "0") + ")")
            en = (":enable='" + "*".join(cond) + "'") if cond else ""
            if HUD_PARTS[name].get("killfeed") and plan.get("kill_rows") is not None:   # 2.35: drawn in the subtitles
                g.append(f"[os{k}]nullsink")
                continue
            if name in kf_idx:   # 2.22 clean rows: the real rows on transparent, bigger, popping in per clutch
                j = list(kfc).index(name)
                kc = plan["killfeed_clip"][name]
                cw_, ch_ = kfc_size(kc)
                w2 = _even(OW * 0.92)
                h2 = _even(w2 * ch_ / max(1, cw_))
                wins_ = kf or []
                xs = str(OW - w2 - 12)
                if wins_:   # small slide from the right when the clutch starts
                    xs += "+(" + "+".join(f"between(t,{a:.2f},{a + 0.2:.2f})*{w2 // 3}*pow(1-(t-{a:.2f})/0.2,2)" for a, b in wins_) + ")"
                g.append(f"[os{k}]nullsink")
                g.append(f"[kfv{j}]scale={w2}:{h2},format=rgba[kfs{j}]")
                g.append(f"[{lab}][kfs{j}]overlay=x='{xs}':y={y}:eval=frame:format=auto{en}[lo{k}]")
                lab = f"lo{k}"
                continue
            if HUD_PARTS[name].get("killfeed") and (opts.get("killfeed_style") or "clean") == "card":
                g += _killfeed_card(f"os{k}", crop, w, y, lab, f"lo{k}", en, kf if still_at is None else None, fps,
                                    align=HUD_PARTS[name]["align"])
                lab = f"lo{k}"
                continue
            bd = ",pad=iw+8:ih+8:4:4:color=white@0.85" if border else ""   # insets get a thin frame
            g.append(f"[os{k}]crop={crop},scale={w - (8 if border else 0)}:-2,setsar=1{bd}[ov{k}]")
            g.append(f"[{lab}][ov{k}]overlay={x}:{y}{en}[lo{k}]")
            lab = f"lo{k}"
    fb = frame_box(opts)
    if fb:   # 2.18: framed look: the edit shrunk onto black, room above for the title
        sc, fx, fy = fb
        g.append(f"[{lab}]scale={_even(OW * sc)}:{_even(OH * sc)},setsar=1,pad={OW}:{OH}:{fx}:{fy}:black[layf]")
        lab = "layf"
    if plan.get("bw_from") is not None:   # 2.24: black-and-white ending (subtitles / red word stay in colour)
        g.append(f"[{lab}]hue=s=0:enable='gte(t,{plan['bw_from']:.2f})'[bwv]")
        lab = "bwv"
    mark = _trial_wordmark() if opts.get("trial_mark") else None   # free trial: the Clip Genie logo on the video
    vo = "vpre" if mark else "vout"
    if ass_name:
        g.append(f"[{lab}]ass=filename={ass_name}:fontsdir=fonts[{vo}]")
    else:
        g.append(f"[{lab}]null[{vo}]")

    extra_inputs = []
    for path_ in kfc.values():
        extra_inputs += ["-t", f"{dur + 0.2:.3f}", "-i", path_]     # the rows video already starts at the clip start
    if teaser:
        extra_inputs += ["-ss", f"{clip['start'] + teaser[0]:.3f}", "-t", f"{teaser[1] - teaser[0] + 0.2:.3f}", "-i", video]
    if has_a:
        mix = ["[ac0]"]
        if plan.get("sfx") and work:
            from .sounds import sound_path
            files = _sfx_files(work)
            vol = float(opts.get("sfx_volume", 0.35))
            react = (opts.get("react_sfx") or "").strip()      # 2.19: your own sound on big reactions
            k = 0
            for e in plan["sfx"]:
                t, kind = e[0], e[1]
                if kind == "file":                             # a sound you placed in the Timeline
                    path, v = e[2], float(e[3])
                elif kind == "boom" and react == "none":
                    continue
                elif kind == "boom" and react and sound_path(react):
                    path, v = sound_path(react), vol * 1.4
                else:
                    path, v = files[kind], vol * (1.4 if kind == "boom" else 1.0)
                if not path or not os.path.isfile(path):
                    continue
                idx = 1 + len(kfc) + (1 if teaser else 0) + k
                extra_inputs += ["-i", path]
                ms = int(max(0, t) * 1000)
                # each sound at one fixed level (no fades, no levelling), max 10 s
                g.append(f"[{idx}:a]atrim=0:10,aresample=48000,aformat=channel_layouts=stereo,"
                         f"adelay={ms}|{ms},volume={v:.2f}[s{k}]")
                mix.append(f"[s{k}]")
                k += 1
        # loudness: one fixed gain for the whole clip (measured beforehand), never a moving
        # compressor - so your voice keeps its natural ups and downs
        # one level for the whole TikTok: the stream's own level + the volume you chose (dB)
        vg = clip.get("volume_db")
        vg = float(opts.get("volume_db", 0.0) if vg is None else vg)
        g.append(f"[ac0]volume={vg:.2f}dB[acg]")
        mix[0] = "[acg]"
        if len(mix) > 1:
            g.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[am0]")
        else:
            g.append("[acg]anull[am0]")
        music = (opts.get("bg_music") or "").strip()
        out_len = plan["out_dur"]
        if music and os.path.isfile(music) and opts.get("bg_music_on", True):
            # background track: plays from the start of the song (loops if the TikTok is longer)
            idx = 1 + sum(1 for x in extra_inputs if x == "-i")
            # from where the song's sound actually starts (skips the silent intro)
            extra_inputs += ["-stream_loop", "-1", "-ss", f"{music_start(music):.3f}", "-i", music]
            mv = clip.get("music_volume")
            mv = float(opts.get("bg_music_volume", 0.16) if mv is None else mv)
            g.append(f"[{idx}:a]aformat=channel_layouts=stereo,aresample=48000,volume={mv:.3f},"
                     f"atrim=0:{out_len:.3f}[bgm0]")
            if opts.get("duck_music", False):
                g.append("[am0]aformat=channel_layouts=stereo,aresample=48000,asplit=2[am1][sc]")
                g.append("[bgm0][sc]sidechaincompress=threshold=0.05:ratio=2.5:attack=80:release=900[bgm]")
            else:  # steady music level (no pumping)
                g.append("[am0]aformat=channel_layouts=stereo,aresample=48000[am1]")
                g.append("[bgm0]anull[bgm]")
            g.append("[am1][bgm]amix=inputs=2:normalize=0:duration=first[am]")
        else:
            g.append("[am0]anull[am]")
        g.append("[am]aresample=48000[aout]")   # no auto-levelling, no compressor
    if mark:   # added last so the sound inputs above keep their numbers
        idx = 1 + sum(1 for x in extra_inputs if x == "-i")
        extra_inputs += ["-i", mark]
        mw = int(OW * TRIAL_MARK_W)
        g.append(f"[{idx}:v]scale={mw}:-1,format=rgba,colorchannelmixer=aa=0.85[tmk]")
        g.append(f"[vpre][tmk]overlay=x=(W-w)/2:y={int(OH * TRIAL_MARK_Y)}-h:format=auto[vout]")

    cmd = [ffmpeg(), "-y", "-v", "error", "-ss", f"{clip['start']:.3f}", "-t", f"{dur + 0.2:.3f}",
           "-i", video] + extra_inputs + ["-filter_complex", ";".join(g), "-map", "[vout]"]
    if still_at is not None:
        return cmd + ["-ss", f"{still_at:.2f}", "-frames:v", "1", "-q:v", "3", out_path]
    if has_a:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-threads", str(work_threads()),
            "-c:v", "libx264", "-preset", opts.get("preset", "medium"), "-crf", str(opts.get("crf", 19)),
            "-pix_fmt", "yuv420p", "-r", f"{fps:.3f}", "-movflags", "+faststart", out_path]
    return cmd


_LEN_CACHE = {}
_START_CACHE = {}


def music_start(path):
    """Where the song really starts: skips silent / hiss-only intros. Looks for the first
    moment the music gets within 20 dB of its normal level (so quiet tape hiss doesn't count)."""
    key = (path, os.path.getmtime(path))
    if key in _START_CACHE:
        return _START_CACHE[key]
    t = 0.0
    try:
        p = subprocess.run([ffmpeg(), "-v", "error", "-t", "60", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                            "-f", "s16le", "-"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        x = np.frombuffer(p.stdout, np.int16).astype(np.float32)
        if len(x) > 8000:
            rms = np.sqrt(np.convolve(x * x, np.ones(3200) / 3200, mode="valid"))[::80]   # 400 ms windows, 10 ms steps
            db = 20 * np.log10(rms + 1e-6)
            loud = np.nonzero(db > np.percentile(db, 75) - 20)[0]
            if len(loud):
                t = 0.0 if loud[0] == 0 else loud[0] * 0.01 + 0.25   # window -> onset, minus a tiny lead
    except Exception as e:
        log.warning("Couldn't find where the music starts: %s", e)
    _START_CACHE[key] = round(t, 3)
    return _START_CACHE[key]


def _audio_len(path):
    if path not in _LEN_CACHE:
        try:
            p = run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", path], check=False)
            _LEN_CACHE[path] = float(p.stdout.decode().strip() or 0)
        except Exception:
            _LEN_CACHE[path] = 0.0
    return _LEN_CACHE[path]


def _safe_name(s):
    s = re.sub(r"[^\w\- ]+", "", s).strip().replace(" ", "_")
    return s[:40] or "clip"


def _workdir(out_dir):
    out_dir = os.path.abspath(out_dir)
    work = os.path.join(out_dir, ".work")
    fonts = os.path.join(work, "fonts")
    os.makedirs(fonts, exist_ok=True)
    for f in os.listdir(FONTS_DIR):
        dst = os.path.join(fonts, f)
        if not os.path.isfile(dst):
            shutil.copy2(os.path.join(FONTS_DIR, f), dst)
    return work


def _clip_audio(audio_curve, clip):
    if audio_curve is None:
        return None, 0.5
    hop = audio_curve["hop"]
    i0, i1 = int(clip["start"] / hop), int(np.ceil(clip["end"] / hop)) + 1
    return np.asarray(audio_curve["z"][i0:i1]), hop


def _map_chunks(chunks, segs, t_off):
    """Timeline captions (clip time) -> output time, following the cuts."""
    out = []
    for c in chunks:
        st = dr.map_time(c["s"], segs, t_off)
        if st is None:  # starts inside a cut: begin at the next kept piece
            nxt = [a for a, b in segs if a >= c["s"] and a < c["e"]]
            st = dr.map_time(nxt[0], segs, t_off) if nxt else None
        en = dr.map_time(max(c["s"], c["e"] - 0.01), segs, t_off)
        if en is None:
            prev = [b for a, b in segs if b <= c["e"] and b > c["s"]]
            en = dr.map_time(prev[-1] - 0.01, segs, t_off) if prev else None
        if st is None or en is None or en <= st or not c["text"].strip():
            continue
        out.append((round(st, 3), round(en + 0.01, 3), c["text"].strip(), bool(c.get("other"))))
    return out


def fill_caption_gaps(caps, words, segs, opts):
    """Subtitles for speech that has none yet (e.g. you extended the TikTok in the timeline)."""
    def covered(w):
        m = (w["start"] + w["end"]) / 2
        return any(c["s"] - 0.05 <= m <= c["e"] + 0.05 for c in caps)
    def kept(w):
        m = (w["start"] + w["end"]) / 2
        return any(a <= m < b for a, b in segs)
    todo = [w for w in cap.clean_words(words) if kept(w) and not covered(w)]
    if not todo:
        return []
    out = []
    for ch in cap.editor_chunks(todo, max_words=4):
        out.append({"s": round(ch[0]["start"], 3), "e": round(ch[-1]["end"] + 0.1, 3),
                    "text": " ".join(x["text"] for x in ch), "other": bool(ch[0].get("other")), "auto": True})
    return out


def prepare(clip, transcript_words, opts, audio_curve=None, webcam=None, still=False):
    """Returns (plan, caption_words)."""
    rel = [dict(w, start=w["start"] - clip["start"], end=w["end"] - clip["start"]) for w in transcript_words
           if w["start"] >= clip["start"] - 0.05 and w["end"] <= clip["end"] + 0.05]
    try:
        from . import taste
        rel = taste.fix_words(rel)   # your earlier subtitle fixes
    except Exception:
        pass
    z, hop = _clip_audio(audio_curve, clip)
    layout = _resolve_layout(webcam, opts.get("layout", "auto"))
    popts = dict(opts)
    if still:
        popts.update(jump_cuts=False, hook_teaser=False, zoom=False, cutaways=False, sfx=False)
    plan = dr.plan_edit(clip, rel, z, hop, popts, has_cam=(layout == "split"))
    if layout in ("fullcam", "camblur"):
        # the whole picture is the webcam here, so any zoom would be a small webcam punch-in (not wanted)
        plan["cam_zooms"], plan["game_zooms"] = [], []
    plan["chat"] = []
    for m in clip.get("chat_pops") or []:   # 2.20: chat pop-ups (stream time -> TikTok time)
        o = dr.map_time(float(m["at"]) - clip["start"], plan["segs"], plan["t_off"])
        if o is not None:
            plan["chat"].append(dict(m, t=round(o, 3)))
    if clip.get("sfx_marks") and not still:
        # 2.19: sounds you placed in the Timeline (stream time) -> TikTok time, following the cuts
        from .sounds import sound_path
        extra = []
        for m in clip["sfx_marks"]:
            o = dr.map_time(float(m["at"]) - clip["start"], plan["segs"], plan["t_off"])
            p = sound_path(m.get("name"))
            if o is not None and p:
                extra.append((round(o, 3), "file", p, max(0.0, min(2.0, float(m.get("vol", 0.5))))))
        plan["sfx"] = sorted(list(plan["sfx"]) + extra, key=lambda e: e[0])
    if clip.get("captions_override") is not None:
        caps = list(clip["captions_override"]) + fill_caption_gaps(clip["captions_override"], rel, plan["segs"], opts)
        caps.sort(key=lambda c: c["s"])
        plan["chunks"] = _map_chunks(caps, plan["segs"], plan["t_off"])
        return plan, []
    words = cap.retime_words(rel, plan["segs"])
    if clip.get("edited_text"):
        words = cap.apply_text_edit(words, clip["edited_text"])
    off = plan["t_off"]
    words = [dict(w, start=round(w["start"] + off, 3), end=round(w["end"] + off, 3)) for w in words]
    return plan, words


def quiet_start(video, start, before=0.7, after=0.15):
    """Nudge the first frame onto the quietest moment just before the start, so the TikTok
    begins in a breath between words instead of in the middle of one."""
    t0 = max(0.0, start - before)
    try:
        p = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{start + after - t0:.3f}", "-i", video,
                            "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        x = np.abs(np.frombuffer(p.stdout, np.int16).astype(np.float32))
        win = 1600  # 100 ms
        if len(x) < win * 2:
            return start
        e = np.convolve(x, np.ones(win) / win, mode="valid")[::160]
        k = int(np.argmin(e))
        return round(t0 + k * 0.01, 3)
    except Exception:
        return start


TRIAL_MARK_W, TRIAL_MARK_Y = 0.40, 0.785   # trial mark: share of the width; bottom edge (above TikTok's caption area)


def _trial_wordmark():
    """The trial mark: Clip Genie logo + name with a FREE TRIAL badge under it (app/brand/clipgenie_trial_mark.png,
    made from clipgenie_wordmark.png + free_trial_badge.png), or None if it's missing (then text only)."""
    p = os.path.join(os.path.dirname(ENGINE_DIR), "app", "brand", "clipgenie_trial_mark.png")
    return p if os.path.isfile(p) else None


def _write_ass(work, name, words, plan, opts, info, webcam, hook=None):
    """Write the overlay file burned onto the TikTok: subtitles (when on) and the channel name.
    The channel name is shown even when subtitles are off. Returns the file name, or None if
    there's nothing to draw."""
    subs = bool(opts.get("captions", True))
    if not subs and not (opts.get("watermark") or "").strip() and not opts.get("title_bar") and not plan.get("chat") \
            and not plan.get("end_word") and not plan.get("kill_rows") and not opts.get("trial_mark"):
        return None
    o = dict(_caption_opts(opts, info, webcam), _chunks=plan.get("chunks") if subs else None, _chat=plan.get("chat") or [],
             _end=plan.get("end_word"))
    layout = _resolve_layout(webcam, opts.get("layout", "auto"))
    sy = seam_y(info, webcam, layout, opts.get("cam_ratio"))
    fb = frame_box(opts)
    # 2.21: chat sits over the GAMEPLAY (the creator: not over the webcam), lower left, above the channel name.
    # chat_y = bottom edge of the newest message.
    if layout == "letterbox":
        top, h = letterbox_box(info)
        o["chat_y"] = (top + h - 24) / OH                 # inside the bottom of the stream picture
    elif layout == "camover":
        o["chat_y"] = CAMOVER[1] - 0.015                  # just above the webcam box
    else:
        wm_y = float(o.get("watermark_y", 0.84)) if (o.get("watermark") or "").strip() else 0.9
        # the creator 11 Nov: default spot = upper part of the gameplay, a little below the webcam line (circled on his screenshot)
        o["chat_y"] = min(0.86, wm_y - 0.045, (sy / OH if sy else 0.46) + 0.16)
        if opts.get("chat_pos") == "low":   # 2.35: low on the gameplay, just above the channel name (CS2 look)
            o["chat_y"] = min(0.86, wm_y - 0.035)
        if fb:
            o["chat_y"] = min(o["chat_y"], (fb[2] + OH * fb[0] - 30) / OH)
    if plan.get("kill_rows") and layout not in ("fullcam", "camblur", "letterbox"):   # 2.35: drawn kill feed
        o["_kills"] = plan["kill_rows"]
        o["kf_y"] = (sy / OH + 0.045) if sy and layout != "camover" else 0.03   # top of the gameplay, right side
    if plan.get("end_word") and sy and layout not in ("letterbox", "camover"):
        o["end_word_y"] = (sy + 40) / OH          # 2.24: on the webcam/gameplay line, like dima's "HUMBLED"
    with open(os.path.join(work, name), "w", encoding="utf-8") as f:
        f.write(cap.build_ass(words if subs else [], o, plan["out_dur"], hook=hook if subs else None,
                              hook_len=max(2.2, plan["t_off"] + 0.4) if plan["teaser"] else None))
        if opts.get("trial_mark") and not _trial_wordmark():   # free trial, no logo file: text instead
            end = plan["out_dur"]
            f.write(f"\nDialogue: 9,0:00:00.00,{int(end // 3600)}:{int(end % 3600 // 60):02d}:{end % 60:05.2f},KF,,0,0,0,,"
                    f"{{\\an8\\pos({OW // 2},{int(OH * TRIAL_MARK_Y) + 4})\\fs34\\b1\\bord3\\3c&H000000&\\1c&HFFFFFF&\\alpha&H30&}}"
                    + ("free trial" if _trial_wordmark() else "Made with Clip Genie · free trial") + "\n")
    return name


def end_ending(plan, words, opts, clip):
    """2.24: black-and-white ending + one big red word (the dima_wallhacks punchline: "HUMBLED" after a 16-1 loss).
    2.24.1: ONLY when you typed the word (end_word). A guessed word made no sense (the creator, 11 Nov), so with no
    word there is no ending effect at all."""
    if not opts.get("end_bw"):
        return
    word = (opts.get("end_word") or "").strip()
    dur = plan["out_dur"]
    if not word or len(word) > 14 or dur < 8:
        return
    t0 = round(max(dur * 0.6, dur - float(opts.get("end_len", 2.6))), 2)
    plan["bw_from"] = t0
    plan["end_word"] = (t0, word)


def render_clip(video, info, clip, transcript_words, webcam, opts, out_dir,
                audio_curve=None, silences=None, index=1):
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    work = _workdir(out_dir)
    if info.get("has_audio") and not clip.get("segs_override"):
        s0 = quiet_start(video, clip["start"])
        # 2.35.1: in loud game audio the quietest spot can be inside a word ("Yo, to in|crease"): start
        # on that word instead
        w = next((w for w in transcript_words if w["start"] + 0.05 < s0 < w["end"] - 0.05), None)
        if w and clip["start"] - w["start"] <= 1.5:
            s0 = max(0.0, round(w["start"] - 0.06, 3))
        clip = dict(clip, start=s0)
    try:   # free trial (public builds): the server decides if videos get the "Made with Clip Genie" mark
        from . import licence
        if licence.limits()["watermark"] and not opts.get("_trial_full"):
            opts = dict(opts, trial_mark=True)
    except Exception:
        log.warning("Trial check failed", exc_info=True)
    plan, words = prepare(clip, transcript_words, opts, audio_curve, webcam)
    plan["killfeed"] = killfeed_times(video, clip, plan, opts, webcam, work)
    if "cs2_hud" in (opts.get("overlays") or []) and not (opts.get("hud_fit") or {}).get("cs2_hud"):
        try:
            box = fit_cs2_bar(video, clip["start"], clip["end"])
            if box:
                log.info("CS2 top bar is wider here: %s", box)
                opts = dict(opts, hud_fit=dict(opts.get("hud_fit") or {}, cs2_hud=box))
        except Exception as e:
            log.warning("CS2 top bar fit skipped: %s", e)
    if opts.get("title_bar") and not opts.get("title_text"):
        opts = dict(opts, title_text=clip.get("hook") or clip.get("title") or "")
    end_ending(plan, words, opts, clip)
    ass_name = _write_ass(work, f"{clip['id']}.ass", words, plan, opts, info, webcam,
                          hook=clip.get("hook") if opts.get("show_hook", False) else None)
    name = f"{index:02d}_{_safe_name(clip.get('title', clip['id']))}.mp4"
    out_path = os.path.join(out_dir, name)
    cmd = build_command(video, info, clip, plan, ass_name, webcam, opts, out_path, work=work)
    run(cmd, cwd=work)
    summary = {"cuts": len(plan["segs"]) - 1, "cam_zooms": len(plan["cam_zooms"]),
               "game_zooms": len(plan["game_zooms"]), "cutaways": len(plan["cutaways"]),
               "teaser": bool(plan["teaser"]), "sfx": len(plan["sfx"])}
    log.info("Rendered %s (%.1fs) %s", name, plan["out_dur"], summary)
    try:   # 2.35.1: self-check (too much cut, shots cut out, mid-word ends, black / frozen / silent file)
        from . import checkup
        checks = checkup.check(out_path, clip, plan, transcript_words, opts)
    except Exception as e:
        log.warning("Self-check failed: %s", e)
        checks = []
    if checks:
        log.info("Self-check %s: %s", name, [c["code"] for c in checks])
    return dict({"path": out_path, "duration": plan["out_dur"], "checks": checks,
                 "kill_names": plan.get("kill_names") or []}, **summary)


def render_preview(video, info, clip, transcript_words, webcam, opts, out_dir, silences=None, audio_curve=None):
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    work = _workdir(out_dir)
    plan, words = prepare(clip, transcript_words, opts, audio_curve, webcam, still=True)
    peak_rel = clip.get("peak", clip["start"]) - clip["start"]
    at = peak_rel
    if words:
        w = min(words, key=lambda w: abs(w["start"] - peak_rel))
        at = w["start"] + min(0.15, (w["end"] - w["start"]) / 2)
    at = max(0.1, min(plan["out_dur"] - 0.1, at))
    if opts.get("title_bar") and not opts.get("title_text"):
        opts = dict(opts, title_text=clip.get("hook") or clip.get("title") or "")
    ass_name = _write_ass(work, f"prev_{clip['id']}.ass", words, plan, opts, info, webcam)
    out_path = os.path.join(out_dir, f"preview_{clip['id']}.jpg")
    cmd = build_command(video, info, clip, plan, ass_name, webcam, opts, out_path, work=work, still_at=at)
    run(cmd, cwd=work)
    return out_path


# ---------------- 2.29: live 9:16 preview ----------------
def _cover(sw, sh, dw, dh):
    """The centred part of a sw x sh picture that 'scale ... increase, crop' keeps for a dw x dh output."""
    if sw * dh > sh * dw:          # source wider: crop the sides
        w = sh * dw / dh
        return (sw - w) / 2, 0.0, w, float(sh)
    h = sw * dh / dw
    return 0.0, (sh - h) / 2, float(sw), h


def preview_geometry(info, webcam, opts, game_box=None, title_text=""):
    """2.29: everything the app needs to DRAW the finished 9:16 TikTok live from the stream footage, using the same
    maths as the real render: picture layers (source part -> place in 1080x1920), title, channel name + Twitch
    logo, subtitle line. Source rects are fractions of the stream. No zooms / cutaways (it's a still layout)."""
    W, H = info["width"], info["height"]
    layout = _resolve_layout(webcam, opts.get("layout", "auto"))
    gf = float(opts.get("game_focus", 0.5))
    L = []

    def layer(sx, sy, sw, sh, dx, dy, dw, dh, blur=0, dark=0.0, border=0):
        L.append({"src": [round(sx / W, 5), round(sy / H, 5), round(sw / W, 5), round(sh / H, 5)],
                  "dst": [round(dx), round(dy), round(dw), round(dh)], "blur": blur, "dark": dark, "border": border})

    if layout == "split":
        cw, ch, cx, cy, fcx, fcy = _cam_geometry(info, webcam)
        cam_h = cam_panel_h(info, webcam, opts.get("cam_ratio"))
        f = webcam.get("face")
        if f and bool(opts.get("cam_tight", True)):
            aspect = OW / cam_h
            tw = min(cw, max(cw * 0.38, f[2] * W * 3.2, W * CAM_MIN_W))
            th = min(ch, tw / aspect)
            tw = min(cw, th * aspect)
            fx_abs, fy_abs = cx + fcx * cw, cy + fcy * ch
            nx = min(max(cx, fx_abs - tw / 2), cx + cw - tw)
            ny = min(max(cy, fy_abs - th * 0.45), cy + ch - th)
            cx, cy, cw, ch = _even(nx), _even(ny), _even(tw), _even(th)
        game_h = OH - cam_h
        if game_box:
            gw, gh, gx, gy = _game_crop(info, game_box, (OW, game_h))
        else:
            gw, gh, gy = _even(min(W, H * OW / game_h)), H, 0
            gx = _split_game_x(info, webcam, gw, gf)
        a, b, c, d = _cover(cw, ch, OW, cam_h)
        layer(cx + a, cy + b, c, d, 0, 0, OW, cam_h)
        a, b, c, d = _cover(gw, gh, OW, game_h)
        layer(gx + a, gy + b, c, d, 0, cam_h, OW, game_h)
    elif layout == "letterbox":
        top, h = letterbox_box(info)
        layer(0, 0, W, H, 0, top, OW, h)
    elif layout == "camover":
        cw, ch, cx, cy = _cam_geometry(info, webcam)[:4]
        if game_box:
            gcw, gch, gx, gy = _game_crop(info, game_box, (OW, OH))
        else:
            gcw, gch, gy = _even(min(W, H * 9 / 16)), H, 0
            gx = _even(min(max(0, gf * W - gcw / 2), W - gcw))
        layer(gx, gy, gcw, gch, 0, 0, OW, OH)
        bx, by, bw = CAMOVER
        camw = _even(OW * bw) - 12
        layer(cx, cy, cw, ch, OW * bx + 6, OH * by + 6, camw, camw * ch / max(1, cw), border=6)
    elif layout in ("fullcam", "crop"):
        cw, chh, cy = _even(min(W, H * 9 / 16)), H, 0
        fx = (webcam or {}).get("face_cx", gf) if layout == "fullcam" else gf
        cx = _even(min(max(0, fx * W - cw / 2), W - cw))
        if layout == "crop" and game_box:
            cw, chh, cx, cy = _game_crop(info, game_box, (OW, OH))
        layer(cx, cy, cw, chh, 0, 0, OW, OH)
    elif layout == "camblur":
        (cw, ch, cx, cy), (top, fh) = camblur_box(info, webcam)
        a, b, c, d = _cover(cw, ch, OW, OH)
        layer(cx + a, cy + b, c, d, 0, 0, OW, OH, blur=28, dark=0.10)
        a, b, c, d = _cover(cw, ch, OW, fh)
        layer(cx + a, cy + b, c, d, 0, top, OW, fh)
    else:   # blur
        gcw, gcx, fg_h = _blur_game(W, H)
        a, b, c, d = _cover(W, H, OW, OH)
        layer(a, b, c, d, 0, 0, OW, OH, blur=24, dark=0.08)
        layer(gcx, 0, gcw, H, 0, (OH - fg_h) / 2 - 80, OW, fg_h)
    for crop, w, x, y, border, n in _overlays(info, webcam, layout, opts):   # CS2 top bar etc. (not the kill feed)
        if HUD_PARTS[n].get("killfeed"):
            continue
        cw_, ch_, cx_, cy_ = [int(v) for v in crop.split(":")]
        ww = w - (8 if border else 0)
        layer(cx_, cy_, cw_, ch_, x + (4 if border else 0), y + (4 if border else 0), ww, ww * ch_ / max(1, cw_),
              border=4 if border else 0)
    fb = frame_box(opts)
    if fb:   # framed look: everything shrunk onto black
        s, fx, fy = fb
        for l in L:
            dx, dy, dw, dh = l["dst"]
            l["dst"] = [round(fx + dx * s), round(fy + dy * s), round(dw * s), round(dh * s)]
    co = _caption_opts(opts, info, webcam)
    out = {"layout": layout, "layers": L, "w": OW, "h": OH}
    wm = (opts.get("watermark") or "").strip()
    if wm:
        show_logo = co.get("watermark_logo", True) is not False
        ls = max(0.2, min(3.0, float(co.get("logo_size", 1.0) or 1.0)))
        scale = float(co.get("watermark_size", 1.0)) * ls             # 2.35.4: logo + name sized as one
        fs = int(58 * scale)
        lh = int(72 * scale)
        lw = int(lh * 0.86) if show_logo else 0
        gap = int(16 * scale) if show_logo else 0
        total = lw + gap + int(len(wm) * fs * 0.47)
        x0 = OW // 2 - total // 2 if co.get("watermark_x") is None else int(OW * float(co["watermark_x"]))
        cy_ = int(OH * float(co.get("watermark_y", 0.84)))
        x0 += int(OW * float(co.get("logo_dx", 0) or 0))          # 2.35.2: logo + name move as one
        cy_ += int(OH * float(co.get("logo_dy", 0) or 0))
        op = co.get("watermark_opacity")
        out["name"] = {"text": wm, "fs": fs, "x": x0 + lw + gap, "y": cy_ - lh // 2 + int(lh * 0.42),
                       "alpha": max(0.0, min(1.0, float(100 if op is None else op) / 100.0)),
                       "logo": [x0, cy_ - lh // 2, lh] if show_logo else None}
    if co.get("title_bar") and (title_text or "").strip():
        ts = co.get("title_style") or "plain"
        boxed = ts in ("box", "banner")
        out["title"] = {"text": title_text, "upper": ts in ("banner", "caps"), "quote": ts == "quote", "boxed": boxed,
                        "size": int((80 if boxed else 96) * float(co.get("title_size", 1.0))),
                        "y": int(OH * float(co.get("title_y", 0.07))),
                        "hl": co.get("title_box_highlight") or "#e8213a" if boxed else (co.get("title_highlight") or co.get("highlight") or "#3dff4a")}
    if co.get("captions"):
        from .captions import FONTS
        font = co.get("font", "Anton")
        size = int(FONTS.get(font, FONTS["Anton"])["size"] * float(co.get("size", 1.0)))
        style = co.get("style", "pop")
        if style == "clean":
            size = int(size * 0.8)
        key = co.get("subs_mode") == "key"
        out["subs"] = {"y": int(OH * float(co.get("position", 0.62))), "size": size, "font": font,
                       "upper": key or (co.get("uppercase", False) if style == "editor" else co.get("uppercase", True)),
                       "hl": co.get("highlight", "#3dff4a"), "other": co.get("other_color", "#5ec8ff"), "key": key}
    return out
