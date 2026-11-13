"""Speech-to-text with word timings (faster-whisper, runs locally)."""
import json
import os

from .util import log, data_dir, work_threads


def _add_cuda_dlls():
    """pip-installed NVIDIA libs (cuBLAS/cuDNN) live in site-packages/nvidia/*/bin."""
    if os.name != "nt":
        return
    import site
    import glob
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


def _pick_device(pref):
    if pref in ("cpu", "cuda"):
        return pref
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def transcribe(wav_path, cache_dir, model_name=None, device="auto",
               language=None, progress=None, duration=None, status=None):
    model_name = model_name or DEFAULT_MODEL
    out = os.path.join(cache_dir, f"transcript_{model_name}_{language or 'auto'}.json")
    old_en = os.path.join(cache_dir, f"transcript_{model_name}_en.json")
    if not language and not os.path.isfile(out) and os.path.isfile(old_en):
        out = old_en   # streams transcribed before auto-detect (it was always English): keep that transcript
    if os.path.isfile(out):
        with open(out, "r", encoding="utf-8") as f:
            return json.load(f)

    _add_cuda_dlls()
    from faster_whisper import WhisperModel

    dev = _pick_device(device)
    local = os.environ.get("LOCALAPPDATA")
    models_dir = os.path.join(local, "BengoGames", "Kliper", "models") if local else os.path.join(data_dir(), "models")
    stop = _watch_download(models_dir, model_name, status)
    try:
        data = _run(WhisperModel, wav_path, model_name, dev, models_dir, language, progress, duration, stop, out)
    except Exception as e:
        if dev != "cuda":
            raise
        log.warning("GPU transcription failed (%s), using CPU", e)
        data = _run(WhisperModel, wav_path, model_name, "cpu", models_dir, language, progress, duration, stop, out)
    finally:
        stop()
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(out + ".tmp", out)
    _clear_parts(out)   # the saved pieces are only needed until the whole transcript is written
    return data


# Kliper: Hebrew speech models from ivrit.ai (Apache-2.0, huggingface.co/ivrit-ai), trained on many hours of
# Hebrew speech. Much better than the general Whisper models on Hebrew slang, names and fast talking.
MODELS = {"ivrit-turbo": "ivrit-ai/whisper-large-v3-turbo-ct2", "ivrit-large": "ivrit-ai/whisper-large-v3-ct2"}
DEFAULT_MODEL = "ivrit-turbo"


def model_id(model_name, language=None):
    """What faster-whisper loads for a model choice. The ivrit.ai models are tuned on Hebrew: when another language
    is picked on purpose (an English stream, English subtitles) the general large-v3-turbo is used instead."""
    model_name = model_name or DEFAULT_MODEL
    if model_name in MODELS:
        if language and language != "he":
            return "large-v3-turbo"
        return MODELS[model_name]
    return model_name


def speech_lang(opts):
    """The stream's speech language: Hebrew unless Settings says otherwise; "auto" = let Whisper guess."""
    lang = (opts or {}).get("language") or "he"
    return None if lang == "auto" else lang


def _names():
    try:
        from .taste import names_prompt
        return names_prompt()
    except Exception:
        return None


# download sizes of the faster-whisper models (bytes, about)
MODEL_BYTES = {"ivrit-turbo": 1.62e9, "ivrit-large": 3.09e9, "large-v3-turbo": 1.62e9, "turbo": 1.62e9,
               "tiny": 75e6, "base": 145e6, "small": 484e6, "medium": 1.53e9,
               "large-v1": 3.09e9, "large-v2": 3.09e9, "large-v3": 3.09e9, "large": 3.09e9}


def _folder_bytes(d):
    n = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


def _watch_download(models_dir, model_name, status):
    """0.1.0: while the speech model downloads (first run), report 'Downloading speech model: 1.4 of 3.1 GB (45%)'
    from the growing models folder. Returns stop()."""
    import threading
    done = threading.Event()
    if not status:
        return done.set
    total = MODEL_BYTES.get(model_name)

    def watch():
        start = _folder_bytes(models_dir) if os.path.isdir(models_dir) else 0
        while not done.wait(1.5):
            got = (_folder_bytes(models_dir) if os.path.isdir(models_dir) else 0) - start
            if got < 1e6:
                continue   # already downloaded: nothing to show
            gb = got / 1e9
            if total:
                status("מוריד את מודל התמלול: %.1f מתוך %.1f GB (%d%%)" % (gb, total / 1e9, min(99, got * 100 / total)))
            else:
                status("מוריד את מודל התמלול: %.1f GB" % gb)
    threading.Thread(target=watch, daemon=True).start()
    return done.set


def languages(model, wav_path, points=8, win=30.0):
    """The languages spoken in a stream, most heard first: Whisper's guess at up to `points` places spread over
    the audio (speech only), so a music intro or a second language later on is not missed."""
    import wave
    import numpy as np
    found = {}
    try:
        with wave.open(wav_path, "rb") as wf:
            if wf.getframerate() != 16000 or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                return []
            n, w = wf.getnframes(), int(win * 16000)
            starts = [0] if n <= w else [int(i * (n - w) / (points - 1)) for i in range(points)]
            for s in starts:
                wf.setpos(s)
                a = np.frombuffer(wf.readframes(w), np.int16).astype(np.float32) / 32768.0
                if len(a) < 3 * 16000:
                    continue
                try:
                    lang, p, _ = model.detect_language(a, vad_filter=True)
                except Exception:
                    continue   # no speech in this window
                if p >= 0.6:
                    found[lang] = found.get(lang, 0) + 1
    except Exception as e:
        log.warning("Language check failed: %s", e)
        return []
    return sorted(found, key=lambda k: -found[k])


WIN = 10.0   # seconds per language label on a mixed-language stream


def _wav_ok(wf):
    return wf.getframerate() == 16000 and wf.getnchannels() == 1 and wf.getsampwidth() == 2


def _read(wav_path, a, b=None):
    """Seconds a..b of a 16 kHz mono wav as floats (b None = to the end)."""
    import wave
    import numpy as np
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        wf.setpos(min(int(a * sr), wf.getnframes()))
        n = int((b - a) * sr) if b is not None else wf.getnframes()
        return np.frombuffer(wf.readframes(n), np.int16).astype(np.float32) / 32768.0


def _quiet(wav_path, t, look=3.0):
    """The quietest half second within `look` s of t: a place to cut that doesn't split a word."""
    import numpy as np
    a0 = max(0.0, t - look)
    x = _read(wav_path, a0, t + look)
    hop = 8000
    k = len(x) // hop
    if not k:
        return t
    e = (x[:k * hop].reshape(k, hop) ** 2).mean(1)
    return round(a0 + (int(np.argmin(e)) + 0.5) * 0.5, 3)


def language_spans(model, wav_path, langs, win=WIN, max_len=600.0):
    """A stream where more than one language was heard: [(start, end, language)]. Every `win` seconds of sound is
    labelled with the most likely of `langs`, a single window that differs from both neighbours is treated as a
    slip, and the cut between two languages goes at the quietest moment nearby. Stretches longer than max_len are
    split so a piece never fills the memory. None when the audio isn't a 16 kHz mono wav."""
    import wave
    import numpy as np
    try:
        with wave.open(wav_path, "rb") as wf:
            if not _wav_ok(wf):
                return None
            dur = wf.getnframes() / 16000.0
    except Exception:
        return None
    labels, t = [], 0.0
    while t < dur:
        a = _read(wav_path, t, min(dur, t + win))
        lab = None
        if len(a) >= 16000 and float(np.sqrt((a ** 2).mean())) > 0.003:
            try:
                probs = dict(model.detect_language(a)[2])
                lab = max(langs, key=lambda x: probs.get(x, 0.0))
            except Exception:
                lab = None
        labels.append(lab)
        t += win
    last = next((x for x in labels if x), langs[0])
    for i, x in enumerate(labels):   # quiet bits take the language of the talking before them
        last = x or last
        labels[i] = last
    for i in range(1, len(labels) - 1):
        if labels[i - 1] == labels[i + 1] != labels[i]:
            labels[i] = labels[i - 1]
    spans, start = [], 0.0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[i - 1]:
            end = dur if i == len(labels) else _quiet(wav_path, i * win)
            spans.append((start, end, labels[i - 1]))
            start = end
    out = []
    for a, b, lang in spans:
        while b - a > max_len + 20:
            c = _quiet(wav_path, a + max_len - 10, 10.0)
            out.append((a, c, lang))
            a = c
        if b - a > 0.05:
            out.append((a, b, lang))
    return out


CHUNK = 600.0   # seconds per saved piece: a crash or a close loses at most this much speech-to-text work


def _cuts(wav_path, chunk=CHUNK):
    """[(start, end)] pieces of about `chunk` seconds, each cut at the quietest half second near its end so no
    word is split. None when the file isn't a 16 kHz mono wav (then it's transcribed in one go)."""
    import wave
    try:
        with wave.open(wav_path, "rb") as wf:
            if not _wav_ok(wf):
                return None
            dur = wf.getnframes() / 16000.0
    except Exception:
        return None
    out, a = [], 0.0
    while dur - a > chunk + 20:
        c = _quiet(wav_path, a + chunk - 10, 10.0)
        out.append((a, c))
        a = c
    out.append((a, dur))
    return out


def _parts_dir(out):
    return out[:-5] + "_parts"


def _clear_parts(out):
    import shutil
    shutil.rmtree(_parts_dir(out), ignore_errors=True)


def _units(model, wav_path, language, langs):
    """What to transcribe, one saved piece each: [(start, end or None, language)]."""
    if not language:
        log.info("Languages heard: %s", ", ".join(langs) or "unknown")
        if len(langs) > 1:
            spans = language_spans(model, wav_path, langs)
            if spans:
                log.info("Language parts: %s", ", ".join("%s %d-%ds" % (l, a, b) for a, b, l in spans[:40]))
                return spans
    lang = language or (langs[0] if len(langs) == 1 else None)   # one language (the usual case): all of it
    cuts = _cuts(wav_path)
    return [(a, b, lang) for a, b in cuts] if cuts else [(0.0, None, lang)]


def _run(WhisperModel, wav_path, model_name, dev, models_dir, language, progress, duration, loaded=None, out=None):
    compute = "float16" if dev == "cuda" else "int8"
    log.info("Loading Whisper %s (%s) on %s", model_name, model_id(model_name, language), dev)
    model = WhisperModel(model_id(model_name, language), device=dev, compute_type=compute, download_root=models_dir,
                         cpu_threads=work_threads())
    if loaded:
        loaded()   # stop the download bar
    import ctranslate2
    # Pieces are saved as they finish (next to the transcript, in *_parts) so a crash or a close at 90 % of a
    # long VOD carries on from the last saved piece instead of starting again.
    parts = _parts_dir(out) if out else None
    meta_p = os.path.join(parts, "meta.json") if parts else None
    meta = None
    if meta_p and os.path.isfile(meta_p):
        try:
            with open(meta_p, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = None
    if meta and meta.get("wav_size") == os.path.getsize(wav_path) and meta.get("language") == language:
        log.info("Carrying on with the speech-to-text saved before (%d pieces)", len(meta["units"]))
        langs, units = meta["langs"], [tuple(u) for u in meta["units"]]
    else:
        if out:
            _clear_parts(out)
        langs = [language] if language else languages(model, wav_path)
        units = _units(model, wav_path, language, langs)
        if parts:
            os.makedirs(parts, exist_ok=True)
            with open(meta_p, "w", encoding="utf-8") as f:
                json.dump({"wav_size": os.path.getsize(wav_path), "language": language, "langs": langs,
                           "units": units}, f)
    total = duration or (units[-1][1] if units and units[-1][1] else 0) or 1.0
    words, segs, heard = [], [], None
    for i, (a, b, lang) in enumerate(units):
        part_p = os.path.join(parts, "%04d.json" % i) if parts else None
        if part_p and os.path.isfile(part_p):
            with open(part_p, encoding="utf-8") as f:
                piece = json.load(f)
            segs += piece["segments"]
            words += piece["words"]
            heard = heard or piece.get("language")
            continue
        ctranslate2.set_random_seed(1000 + i)   # the same result whether or not the run was stopped before
        piece = {"segments": [], "words": []}
        segments, info = model.transcribe(
            wav_path if b is None else _read(wav_path, a, b),
            language=lang,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
            beam_size=1,
            task="transcribe",
            initial_prompt=_names(),
        )
        heard = heard or info.language
        piece["language"] = info.language
        if not duration and b is None:
            total = getattr(info, "duration", None) or 1.0
        for seg in segments:
            piece["segments"].append({"start": round(a + seg.start, 3), "end": round(a + seg.end, 3),
                                      "text": seg.text.strip()})
            for w in (seg.words or []):
                t = w.word.strip()
                if t:
                    piece["words"].append({"start": round(a + w.start, 3), "end": round(a + w.end, 3),
                                           "text": t, "p": round(w.probability, 3)})
            if progress:
                progress(min(0.999, (a + seg.end) / total))
        if part_p:
            with open(part_p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(piece, f)
            os.replace(part_p + ".tmp", part_p)
        segs += piece["segments"]
        words += piece["words"]
    return {"language": langs[0] if langs else heard, "languages": langs or [heard],
            "segments": segs, "words": words}


def transcribe_part(wav_path, cache_dir, a, b, language, model_name=None, device="auto"):
    """0.1.2: one TikTok's subtitles in the language picked in the editor. Only that part of the stream
    (a..b seconds) is written down again, and speech-to-text is TOLD the language, so it never guesses another
    one. Saved, so re-making the TikTok is instant. Words in stream seconds."""
    model_name = model_name or DEFAULT_MODEL
    out = os.path.join(cache_dir, f"subs_{model_name}_{language}_{int(a * 10)}_{int(b * 10)}.json")
    if os.path.isfile(out):
        with open(out, "r", encoding="utf-8") as f:
            return json.load(f)
    _add_cuda_dlls()
    from faster_whisper import WhisperModel
    local = os.environ.get("LOCALAPPDATA")
    models_dir = os.path.join(local, "BengoGames", "Kliper", "models") if local else os.path.join(data_dir(), "models")

    def go(dev):
        model = WhisperModel(model_id(model_name, language), device=dev, compute_type="float16" if dev == "cuda" else "int8",
                             download_root=models_dir, cpu_threads=work_threads())
        segments, _ = model.transcribe(_read(wav_path, a, b), language=language, word_timestamps=True,
                                       vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
                                       condition_on_previous_text=False, beam_size=1, task="transcribe",
                                       initial_prompt=_names())
        words = []
        for seg in segments:
            for w in (seg.words or []):
                t = w.word.strip()
                if t:
                    words.append({"start": round(a + w.start, 3), "end": round(a + w.end, 3), "text": t,
                                  "p": round(w.probability, 3)})
        return words
    dev = _pick_device(device)
    try:
        words = go(dev)
    except Exception as e:
        if dev != "cuda":
            raise
        log.warning("GPU transcription failed (%s), using CPU", e)
        words = go("cpu")
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(words, f)
    os.replace(out + ".tmp", out)
    return words
