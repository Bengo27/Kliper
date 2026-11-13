"""2.19: your own sound effects. Imported files are copied into %APPDATA%/GenieStudioFX/ClipGenie/sfx/ so they
keep working if the original moves. A sound is known by its file name without the extension."""
import os
import re
import shutil

from .util import data_dir, log

EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")
MAX_MB = 25


def sfx_dir():
    d = os.path.join(data_dir(), "sfx")
    os.makedirs(d, exist_ok=True)
    return d


def list_sounds():
    """[{name, path}] sorted by name."""
    out = []
    for f in sorted(os.listdir(sfx_dir()), key=str.lower):
        p = os.path.join(sfx_dir(), f)
        if os.path.isfile(p) and f.lower().endswith(EXTS):
            out.append({"name": os.path.splitext(f)[0], "path": p})
    return out


def sound_path(name):
    """The file for a sound name, or None if it isn't in the library (deleted)."""
    if not name:
        return None
    for s in list_sounds():
        if s["name"] == name:
            return s["path"]
    return None


def import_sound(src):
    src = (src or "").strip()
    if not os.path.isfile(src):
        raise RuntimeError("Sound file not found.")
    if not src.lower().endswith(EXTS):
        raise RuntimeError("That isn't a sound file Clip Genie can use (mp3, wav, ogg, m4a, aac, flac).")
    if os.path.getsize(src) > MAX_MB * 1024 * 1024:
        raise RuntimeError(f"That sound is too big (over {MAX_MB} MB). Sound effects are usually a few seconds long.")
    base, ext = os.path.splitext(os.path.basename(src))
    base = re.sub(r"[^\w\- ]+", "", base).strip()[:40] or "sound"
    name, n = base, 2
    while sound_path(name) and os.path.abspath(sound_path(name)) != os.path.abspath(src):
        name, n = f"{base} {n}", n + 1
    dst = os.path.join(sfx_dir(), name + ext.lower())
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
    log.info("Imported sound effect '%s'", name)
    return name


def remove_sound(name):
    p = sound_path(name)
    if p:
        try:
            os.remove(p)
        except OSError as e:
            raise RuntimeError(f"Couldn't remove that sound: {e}")
