"""Looking at the gameplay, not just listening.

1. lead_up(): when a clip opens with a reaction to something that already happened ("I just hit an
   edge bug", "did you see that", "someone clip that", "huh? what the fuck"), move the start back so
   the play itself is in the clip.
2. moment_frames(): grabs a few low-res frames around a moment (from the Twitch low-quality stream
   or the local file), lined up exactly with the stream audio.
3. motion_onset(): where the on-screen action starts (big change between frames in the gameplay).
The frames also go to the AI clipper so it can *see* what happened (ai.look_at_moments).
"""
import os
import re
import subprocess

import numpy as np

from .util import ffmpeg, log, CREATE_NO_WINDOW

# phrases that react to something that ALREADY happened on screen
RETRO = re.compile(
    r"\b(i just|did you see|you see that|see that|clip (it|that|this)|someone clip|that was (so )?(clean|sick|insane|crazy|nuts|mad|filthy|cold|disgusting)|"
    r"no way|how did (i|that|he)|what was that|let'?s go+|yo+|holy|what the (fuck|hell)|oh (shit|my god|my days)|bro what|huh)\b", re.I)
# short gasps/reactions that belong to the action itself (keep them, and start before them)
GASP = re.compile(r"^(huh|what|wait|oh|ooh|ah|no+|yo+|bro|shit|fuck|wtf|holy|damn|yes|go|wow|ayo)[\W]*$", re.I)


def lead_up(st, en, words, max_back=15.0, game_lead=3.0):
    """Returns a new start if the clip opens with a reaction to an earlier on-screen moment."""
    opening = [w for w in words if st - 0.5 <= w["start"] <= st + 6]
    text = " ".join(w["text"] for w in opening)
    if not RETRO.search(text):
        return st, None
    # walk back over gasps/short reactions ("Huh? What the fuck? Oh, shit.") - they are the action
    before = [w for w in words if st - max_back <= w["start"] < st]
    first = st
    for w in reversed(before):
        if GASP.match(w["text"].strip()) or (first - w["end"] < 2.5 and len(w["text"]) <= 6):
            first = w["start"]
        elif first - w["end"] > 2.5:
            break
        else:
            break
    new_st = max(st - max_back, first - game_lead)
    return round(new_st, 2), round(first, 2)


def _clip_lowres(src, t0, dur, out):
    subprocess.run([ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, t0):.2f}", "-i", src, "-t", f"{dur:.2f}",
                    "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                    "-c:a", "aac", "-b:a", "64k", out], check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)


def moment_frames(a, t0, t1, fps=1.0, width=512, work=None):
    """[(stream_time, jpeg_bytes, gray_small)] for t0..t1, exact to the stream audio."""
    work = work or os.path.join(a.cache, "vision")
    os.makedirs(work, exist_ok=True)
    tmp = os.path.join(work, f"seg_{int(t0 * 10)}_{int(t1 * 10)}.mp4")
    src = a.remote.resolve()["low_url"] if a.remote else a.video
    off = t0
    if not os.path.isfile(tmp):
        _clip_lowres(src, t0 - 2 + (a.remote.off if a.remote else 0), (t1 - t0) + 14, tmp)   # a bit extra: Twitch may start early
    wav = getattr(a, "wav_path", None) or os.path.join(a.cache, "audio16k.wav")
    if a.remote and os.path.isfile(wav):
        from .twitch import measure_offset
        off = measure_offset(tmp, wav, t0 - 2, search=15.0, probe_len=min(30.0, t1 - t0 + 10))
    else:
        off = max(0.0, t0 - 2)
    # jpegs at fps
    p = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t0 - off):.2f}", "-i", tmp, "-t", f"{t1 - t0:.2f}",
                        "-vf", f"fps={fps},scale={width}:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    jpgs = [b"\xff\xd8" + x for x in p.stdout.split(b"\xff\xd8")[1:]]
    out = [(round(t0 + i / fps, 2), j) for i, j in enumerate(jpgs)]
    return out, tmp, off


def motion_curve(tmp, off, t0, t1, cam_box=None, fps=5, w=96, h=54):
    """On-screen action per 1/fps s in the gameplay (webcam area ignored). Returns (times, z)."""
    p = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t0 - off):.2f}", "-i", tmp, "-t", f"{t1 - t0:.2f}",
                        "-vf", f"fps={fps},scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    fr = np.frombuffer(p.stdout, np.uint8)
    if len(fr) < w * h * 3:
        return np.zeros(0), np.zeros(0)
    fr = fr[: len(fr) // (w * h) * (w * h)].reshape(-1, h, w).astype(np.float32)
    mask = np.ones((h, w), bool)
    if cam_box:
        x, y, bw, bh = cam_box
        mask[int(y * h):int((y + bh) * h) + 1, int(x * w):int((x + bw) * w) + 1] = False
    d = np.array([np.abs(fr[i] - fr[i - 1])[mask].mean() for i in range(1, len(fr))])
    if not len(d):
        return np.zeros(0), np.zeros(0)
    med = float(np.median(d))
    mad = float(np.median(np.abs(d - med))) + 1e-3
    z = (d - med) / (1.4826 * mad)
    times = t0 + (np.arange(1, len(fr)) / fps)
    return times, z


def motion_onset(times, z, before, max_back=15.0):
    """Start of the burst of on-screen action that leads into `before` (the reaction)."""
    if not len(z):
        return None
    sel = (times >= before - max_back) & (times <= before + 1)
    if not sel.any():
        return None
    tz, zz = times[sel], z[sel]
    k = int(np.argmax(zz))
    if zz[k] < 2.0:
        return None
    i = k
    while i > 0 and zz[i - 1] > 0.8:   # walk back to where the action began
        i -= 1
    return float(tz[i])
