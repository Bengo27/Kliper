"""Measure how a finished TikTok is edited, so styles can be compared with numbers (read-only on the video).

    python\\python.exe tools\\analyze_tiktok.py <video.mp4> [more.mp4 ...] [--name NAME] [--no-speech]

For each video it writes prototypes/analysis/<name>/ :
  report.json   cuts, shot lengths, speech pace, silence, loudness
  sheet.jpg     one frame per second in a grid (to read layout, text, overlays by eye)
  cuts.jpg      the frame just after each cut
and prints a one-line summary. Nothing is uploaded; the video file is only read.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from clipgenie.util import ffmpeg, ffprobe, load_settings  # noqa: E402

OUT = os.path.join(ROOT, "prototypes", "analysis")
SCENE = 0.30       # scene-change score that counts as a cut
PAUSE = 0.40       # a gap between words longer than this is a pause


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(path):
    r = _run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    d = json.loads(r.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    num, _, den = (v.get("r_frame_rate") or "30/1").partition("/")
    return {"duration": float(d.get("format", {}).get("duration") or 0), "width": v.get("width"), "height": v.get("height"),
            "fps": round(float(num) / float(den or 1), 2), "has_audio": bool(a)}


def cuts(path, crop=""):
    """Times of hard cuts (scene changes). crop: "top"/"bottom" half only (a split TikTok cuts the gameplay
    while the webcam half stays, which the whole-picture score misses)."""
    pre = {"top": "crop=iw:ih*0.42:0:0,", "bottom": "crop=iw:ih*0.5:0:ih*0.45,"}.get(crop, "")
    r = _run([ffmpeg(), "-hide_banner", "-i", path, "-an", "-vf", pre + f"select='gt(scene,{SCENE})',showinfo", "-f", "null", "-"])
    ts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    out = []
    for t in ts:                       # a flash or zoom can fire twice: keep cuts at least 0.25 s apart
        if not out or t - out[-1] >= 0.25:
            out.append(round(t, 2))
    return out


def silences(path, db=-35, d=0.35):
    r = _run([ffmpeg(), "-hide_banner", "-i", path, "-vn", "-af", f"silencedetect=noise={db}dB:d={d}", "-f", "null", "-"])
    s = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    e = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    return [(round(a, 2), round(b, 2)) for a, b in zip(s, e)]


def loudness(path):
    r = _run([ffmpeg(), "-hide_banner", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume: (-?[0-9.]+) dB", r.stderr)
    p = re.search(r"max_volume: (-?[0-9.]+) dB", r.stderr)
    return {"mean_db": float(m.group(1)) if m else None, "peak_db": float(p.group(1)) if p else None}


def sheet(path, out, dur, every=1.0, cols=8, w=216):
    n = max(1, int(dur / every) + 1)
    rows = (n + cols - 1) // cols
    _run([ffmpeg(), "-y", "-loglevel", "error", "-i", path, "-vf",
          f"fps=1/{every},scale={w}:-2,tile={cols}x{rows}", "-frames:v", "1", "-q:v", "3", out])


def cut_frames(path, out, times, cols=8, w=216):
    if not times:
        return
    times = times[:48]
    sel = "+".join(f"between(t,{t + 0.10:.2f},{t + 0.16:.2f})" for t in times)
    rows = (len(times) + cols - 1) // cols
    _run([ffmpeg(), "-y", "-loglevel", "error", "-i", path, "-vf",
          f"select='{sel}',scale={w}:-2,tile={cols}x{rows}", "-vsync", "vfr", "-frames:v", "1", "-q:v", "3", out])


def speech(path, work):
    from clipgenie.transcribe import transcribe
    wav = os.path.join(work, "audio.wav")
    _run([ffmpeg(), "-y", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav])
    s = load_settings()
    t = transcribe(wav, work, s.get("whisper_model") or "small", s.get("device") or "auto")
    try:
        os.remove(wav)
    except OSError:
        pass
    return [w for w in t.get("words", []) if w.get("text")]


def analyze(path, name=None, do_speech=True):
    name = name or re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(path))[0])[:60]
    work = os.path.join(OUT, name)
    os.makedirs(work, exist_ok=True)
    info = probe(path)
    dur = info["duration"]
    cs_top, cs_bot = cuts(path, "top"), cuts(path, "bottom")
    cs = []
    for t in sorted(cuts(path) + cs_top + cs_bot):     # a cut in either half counts
        if not cs or t - cs[-1] >= 0.25:
            cs.append(t)
    bounds = [0.0] + cs + [dur]
    shots = [round(b - a, 2) for a, b in zip(bounds, bounds[1:]) if b - a > 0.05]
    sl = sorted(shots)
    rep = {"file": path, "name": name, **info,
           "cuts": len(cs), "cut_times": cs, "cuts_webcam_half": len(cs_top), "cuts_game_half": len(cs_bot),
           "shot_avg": round(sum(shots) / len(shots), 2) if shots else dur,
           "shot_median": sl[len(sl) // 2] if sl else dur, "shot_longest": max(shots) if shots else dur,
           "cuts_per_min": round(len(cs) / dur * 60, 1) if dur else 0,
           "first_cut": cs[0] if cs else None}
    if info["has_audio"]:
        sil = silences(path)
        rep["quiet_total"] = round(sum(b - a for a, b in sil), 1)
        rep["quiet_pct"] = round(rep["quiet_total"] / dur * 100, 1) if dur else 0
        rep.update(loudness(path))
        if do_speech:
            try:
                ws = speech(path, work)
                gaps = [round(b["start"] - a["end"], 2) for a, b in zip(ws, ws[1:])]
                pauses = [g for g in gaps if g > PAUSE]
                rep.update(words=len(ws), words_per_sec=round(len(ws) / dur, 2) if dur else 0,
                           first_word=round(ws[0]["start"], 2) if ws else None,
                           pauses=len(pauses), pause_total=round(sum(pauses), 1),
                           pause_longest=max(pauses) if pauses else 0,
                           text=" ".join(w["text"].strip() for w in ws)[:600])
            except Exception as e:   # speech is a bonus: the cut numbers still stand
                rep["speech_error"] = str(e)[:200]
    sheet(path, os.path.join(work, "sheet.jpg"), dur)
    cut_frames(path, os.path.join(work, "cuts.jpg"), cs)
    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    return rep


def line(r):
    s = (f"{r['name'][:28]:28} {r['duration']:5.1f}s {r['width']}x{r['height']}  cuts {r['cuts']:2d} (cam {r['cuts_webcam_half']}, game {r['cuts_game_half']}) "
         f"({r['cuts_per_min']:4.1f}/min, shot avg {r['shot_avg']:4.1f}s, longest {r['shot_longest']:4.1f}s)")
    if "words" in r:
        s += (f"  speech {r['words_per_sec']:.1f} w/s, first word {r['first_word']}s, "
              f"pauses {r['pauses']} = {r['pause_total']}s (longest {r['pause_longest']}s)")
    if "quiet_pct" in r:
        s += f"  quiet {r['quiet_pct']}%  mean {r.get('mean_db')} dB"
    return s


if __name__ == "__main__":
    args = sys.argv[1:]
    nm = None
    if "--name" in args:
        i = args.index("--name")
        nm = args[i + 1]
        del args[i:i + 2]
    sp = "--no-speech" not in args
    files = [a for a in args if not a.startswith("--")]
    if not files:
        sys.exit(__doc__)
    for p in files:
        print(line(analyze(p, nm if len(files) == 1 else None, sp)), flush=True)
