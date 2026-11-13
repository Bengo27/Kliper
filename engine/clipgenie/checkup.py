"""2.35.1: self-check after every TikTok is made (the creator: "self-diagnosis so we don't mess up").

Looks at the finished file and at what the edit did, and returns plain warnings that the app shows on the
TikTok. It never changes the TikTok; it only says where to look. Each check is cheap (one ffmpeg pass).
"""
import re

from .util import ffmpeg, run, probe, log


def _mmss(t):
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


def _removed(segs, clip_len):
    out, cur = [], 0.0
    for a, b in segs:
        if a - cur > 0.05:
            out.append((cur, a))
        cur = max(cur, b)
    if clip_len - cur > 0.05:
        out.append((cur, clip_len))
    return out


def check(out_path, clip, plan, words, opts):
    """clip / words in the same seconds as the source section; plan segs relative to clip start.
    Returns [{"code", "text"}] (empty = all good)."""
    warn = []
    add = lambda code, text: warn.append({"code": code, "text": text})
    st, en = float(clip["start"]), float(clip["end"])
    clip_len = max(0.1, en - st)
    segs = plan.get("segs") or [(0.0, clip_len)]
    own_cuts = bool(clip.get("segs_override"))

    # 1. cut down too much (the "5 bullet deag ace" bug: 34 s -> 10 s)
    kept = sum(b - a for a, b in segs)
    if not own_cuts and kept < 0.5 * clip_len and clip_len - kept > 8:
        add("cut_a_lot", f"Pause cuts took out {int(clip_len - kept)} of {int(clip_len)} s. Check nothing important "
                         "was cut (Timeline shows the cut parts).")

    # 2. action cut out: gunshots / hits inside a removed part
    shots = [t - st for t in clip.get("shots") or []]
    if not own_cuts and shots:
        for a, b in _removed(segs, clip_len):
            n = sum(1 for t in shots if a <= t <= b)
            if b - a >= 3.0 and n >= 4:
                add("action_cut", f"{n} sharp sounds (shots, hits) were cut out between {_mmss(a)} and {_mmss(b)} of the clip.")

    # 3. starts or ends in the middle of a word
    spoken = [w for w in words if w.get("end", 0) > w.get("start", 0)]
    first_seg, last_seg = segs[0], segs[-1]
    a0, b1 = st + first_seg[0], st + last_seg[1]
    if any(w["start"] < a0 - 0.08 and w["end"] > a0 + 0.12 for w in spoken):
        add("starts_mid_word", "It starts in the middle of a word.")
    if any(w["start"] < b1 - 0.12 and w["end"] > b1 + 0.08 for w in spoken):
        add("ends_mid_word", "It ends in the middle of a word.")
    # (a "more talking right after the end" check fired on almost every TikTok: streamers keep talking)

    # 4. the file itself: length, picture, sound, black or frozen picture
    try:
        info = probe(out_path)
    except Exception as e:
        add("bad_file", f"The video file could not be read ({e}).")
        return warn
    dur = float(info.get("duration") or 0)
    want = float(plan.get("out_dur") or 0)
    if want and abs(dur - want) > 1.0:
        add("wrong_length", f"The video is {dur:.1f} s but should be {want:.1f} s.")
    if (info.get("width"), info.get("height")) != (1080, 1920):
        add("wrong_size", f"The video is {info.get('width')}x{info.get('height')}, not 1080x1920.")
    if not info.get("has_audio"):
        add("no_sound", "The video has no sound track.")
    try:
        r = run([ffmpeg(), "-hide_banner", "-nostats", "-i", out_path,
                 "-vf", "scale=270:-2,blackdetect=d=0.8:pix_th=0.08,freezedetect=n=0.002:d=2.5",
                 "-af", "volumedetect", "-f", "null", "-"], check=False)
        txt = r.stderr.decode("utf-8", "replace")
    except Exception as e:
        log.warning("Self-check pass skipped: %s", e)
        txt = ""
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", txt):
        b0, b2 = float(m.group(1)), float(m.group(2))
        add("black", f"Black picture for {b2 - b0:.1f} s at {_mmss(b0)} (check the stream: it may be in the source).")
    for m in re.finditer(r"freeze_start: ([\d.]+)[\s\S]*?freeze_duration: ([\d.]+)", txt):
        add("frozen", f"The picture stands still for {float(m.group(2)):.1f} s at {_mmss(float(m.group(1)))}.")
    mv = re.search(r"max_volume: (-?[\d.]+) dB", txt)
    if mv and float(mv.group(1)) < -45:
        add("silent", "The sound is (almost) silent.")
    return warn
