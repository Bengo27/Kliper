"""Shared helpers: paths, ffmpeg lookup, process running, logging."""
import json
import os
import shutil
import subprocess
import sys
import logging

log = logging.getLogger("clipgenie")

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(ENGINE_DIR)
FONTS_DIR = os.path.join(ENGINE_DIR, "fonts")

# Hide console windows for child processes on Windows
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# 2.27.3: work that loads the graphics card / processor hard (speech, voice, webcam, gameplay check, rendering).
# Only one job does heavy work at a time; light work (downloading audio, chat, viewer clips, the AI read)
# runs alongside, so a TikTok can be made while another stream's chat downloads.
import threading as _threading
HEAVY = _threading.RLock()


def work_threads():
    """Leave headroom for Premiere: use about 3/4 of the cores (min 2)."""
    n = os.cpu_count() or 8
    return max(2, n - max(2, n // 4))


def lower_priority():
    """Run the engine (and every ffmpeg/Whisper it starts) below normal priority,
    so Premiere stays smooth while clips render."""
    if sys.platform == "win32":
        try:
            import ctypes
            BELOW_NORMAL = 0x00004000
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), BELOW_NORMAL)
        except Exception:
            pass
    else:
        try:
            os.nice(5)
        except Exception:
            pass


def data_dir():
    """Per-user folder for settings, tokens, caches."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "GenieStudioFX", "ClipGenie")
    else:
        d = os.path.join(os.path.expanduser("~"), ".clipgenie")
    os.makedirs(d, exist_ok=True)
    return d


def _find_bin(name):
    exe = name + (".exe" if sys.platform == "win32" else "")
    for cand in (
        os.path.join(ROOT_DIR, "ffmpeg", "bin", exe),
        os.path.join(ROOT_DIR, "ffmpeg", exe),
        os.path.join(ENGINE_DIR, "ffmpeg", exe),
    ):
        if os.path.isfile(cand):
            return cand
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"{name} not found. Re-run the Clip Genie installer.")


def ffmpeg():
    return _find_bin("ffmpeg")


def ffprobe():
    return _find_bin("ffprobe")


def run(cmd, check=True, cwd=None):
    log.debug("run: %s", " ".join(str(c) for c in cmd))
    p = subprocess.run(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
        cwd=cwd,
    )
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace")[-2500:]
        raise RuntimeError(f"{os.path.basename(str(cmd[0]))} failed:\n{err}")
    return p


def probe(path):
    p = run([ffprobe(), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path])
    info = json.loads(p.stdout.decode("utf-8", "replace"))
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("No video stream found in " + path)
    fps = 30.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    try:
        n, d = rate.split("/")
        if float(d) > 0 and float(n) > 0:
            fps = float(n) / float(d)
    except Exception:
        pass
    return {
        "duration": float(info["format"].get("duration") or v.get("duration") or 0),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps,
        "has_audio": a is not None,
    }


def cache_dir_for(video_path):
    """Analysis cache lives next to the VOD in a hidden folder."""
    base = os.path.splitext(os.path.basename(video_path))[0]
    d = os.path.join(os.path.dirname(os.path.abspath(video_path)), ".clipgenie", base)
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        d = os.path.join(data_dir(), "cache", base)
        os.makedirs(d, exist_ok=True)
        return d


def fmt_time(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def load_settings():
    path = os.path.join(data_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(s):
    path = os.path.join(data_dir(), "settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
