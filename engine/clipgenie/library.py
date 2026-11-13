"""The TikTok library: every stream Klipon processed and the TikToks it made.

Stored in %APPDATA%/BengoGames/Klipon/library.json; videos live in
Videos/Klipon/<date> <stream title>/.
"""
import json
import os
import shutil
import re
import threading
import time

from .util import data_dir, load_settings, log, run, ffmpeg, fmt_time
from . import pipeline as pl
from . import render as rd
from . import twitch as tw

_LOCK = threading.RLock()


def _path():
    return os.path.join(data_dir(), "library.json")


def load():
    with _LOCK:
        try:
            with open(_path(), "r", encoding="utf-8") as f:
                lib = json.load(f)
        except Exception:
            return {"streams": {}, "schema": 29}
        if lib.get("schema", 0) < 29:
            # 2.9: new TikToks have no subtitles by default. TikToks made before that had them
            # (unless you'd turned them off), so record it and editing one keeps its subtitles.
            old_default = (load_settings().get("style") or {}).get("captions", True)
            for st in lib.get("streams", {}).values():
                for c in st.get("clips", []):
                    c.setdefault("style", {}).setdefault("captions", bool(old_default))
            lib["schema"] = 29
            try:
                save(lib)
            except Exception:
                pass
        return lib


def save(lib):
    with _LOCK:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lib, f, indent=1)
        os.replace(tmp, _path())


def update_stream(key, **fields):
    with _LOCK:
        lib = load()
        s = lib["streams"].setdefault(key, {"key": key, "clips": [], "created": time.time()})
        s.update(fields)
        save(lib)
        return s


def get_stream(key):
    return load()["streams"].get(key)


def look_style(look_id):
    """2.23: the style for an editing style picked before adding a stream ("" = none). Ready-made or your own."""
    from .style import preset
    if not look_id:
        return None
    if look_id.startswith("mine:"):
        st = (load_settings().get("my_styles") or {}).get(look_id[5:])
        return dict(st, look=look_id) if st else None
    p = preset(look_id)
    return dict(p["style"], look=p["id"]) if p else None


def update_clip(key, clip_id, **fields):
    with _LOCK:
        lib = load()
        s = lib["streams"].get(key)
        if not s:
            raise RuntimeError("Stream not found")
        for c in s["clips"]:
            if c["id"] == clip_id:
                c.update(fields)
                save(lib)
                return c
        raise RuntimeError("TikTok not found")


def _put(src, dst):
    """Move a finished video into place. If the old file is still open somewhere (a video player,
    a phone downloading it), wait a moment; if it stays locked, save under a new name instead."""
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    for i in range(12):
        try:
            os.replace(src, dst)
            return dst
        except PermissionError:
            time.sleep(0.5)
    base, ext = os.path.splitext(dst)
    base = re.sub(r"_v\d+$", "", base)
    for n in range(2, 100):
        alt = f"{base}_v{n}{ext}"
        if not os.path.exists(alt):
            os.replace(src, alt)
            log.warning("%s was in use, saved as %s", os.path.basename(dst), os.path.basename(alt))
            try:
                os.remove(dst)
            except OSError:
                pass
            return alt
    raise RuntimeError("Couldn't save the TikTok: its file is open in another program. Close it and try again.")


def _free_name(folder, n, title):
    """A file name in the stream folder that no other TikTok is using."""
    stem = _safe(title, 40).replace(" ", "_") or "tiktok"
    while True:
        p = os.path.join(folder, f"{n:02d}_{stem}.mp4")
        if not os.path.exists(p):
            return p
        n += 1


def output_root():
    s = load_settings()
    return s.get("out_dir") or os.path.join(os.path.expanduser("~"), "Videos", "Klipon")


def _safe(s, n=50):
    s = re.sub(r"[^\w\- ]+", "", s or "").strip()
    return re.sub(r"\s+", " ", s)[:n].strip()


def stream_key(source):
    if tw.is_link(source):
        r = tw.vod_range(source)   # 2.35.3: a time range of a VOD is its own stream
        return tw.stream_name(tw.vod_id(source)) + (f"_r{int(r[0])}_{int(r[1])}" if r else "")
    return "file_" + _safe(os.path.basename(source), 80)


def thumbnail(video, out):
    try:
        run([ffmpeg(), "-y", "-v", "error", "-ss", "1.0", "-i", video, "-frames:v", "1",
             "-vf", "scale=360:-2", "-q:v", "4", out])
        return out
    except Exception:
        return None


def channel_style(key, style=None):
    """2.35: the name tag shows the streamer the stream / clip is FROM (any streamer's VOD or clip), unless this
    TikTok has its own name typed in. No known channel (a video file) -> the name from Settings."""
    st = get_stream(key) or {}
    ch = (st.get("channel") or "").strip()
    out = dict({"watermark": ch} if ch else {}, **(style or {}))
    if st.get("trial_full"):   # 0.1.0: added during the free trial: keeps every feature, no trial mark
        out["_trial_full"] = True
    return out


def _ai_ok():
    from . import licence
    if not licence.ai_allowed():
        raise RuntimeError("TRIAL_USED: The AI isn't part of the free trial. Upgrade to use it.")


def _ai_settings():
    """Settings with the key the AI should use (public builds: the built-in server AI)."""
    st = dict(load_settings())
    from . import licence
    if licence.enforced():
        from . import ai
        st.pop("api_key", None)
        if licence.server_ai():
            st["api_key"], st["ai_model"] = ai.SERVER_KEY, ai.SERVER_MODEL
    return st


def _trial_room(s):
    """Free trial: how many more TikToks this stream may get (None = no limit). Every TikTok made from it counts,
    also ones thrown away later; re-making one doesn't."""
    from . import licence
    cap = licence.limits().get("per_stream") or 0
    if not cap:
        return None
    return max(0, cap - len((s or {}).get("clips") or []))


def _trial_full():
    from . import licence
    raise RuntimeError("TRIAL_USED: The free trial makes up to %d TikToks per stream. Upgrade to make more."
                       % licence.limits().get("per_stream"))


def style_opts(extra=None):
    """Default edit style (your editor's look) + saved settings + overrides."""
    from .style import EDITOR_STYLE
    o = dict(EDITOR_STYLE)
    s = load_settings()
    saved = dict(s.get("style") or {})
    saved.pop("hook_teaser", None)   # 2.2.2: cold-open replay is off by default; turn on per TikTok in Edit
    for k in ("cam_punch_every", "cam_zooms"):
        saved.pop(k, None)           # 2.9: small webcam zooms are gone for good
    saved.pop("captions", None)      # 2.9: Settings decides ("Subtitles on new TikToks"), off unless you turn it on
    o["captions"] = bool(s.get("subs_default", False))
    o.update(saved)
    for k in ("api_key", "ai_model", "whisper_model", "device", "chat_lag", "streamer", "language"):
        if s.get(k) not in (None, ""):
            o[k] = s[k]
    from . import licence
    if licence.enforced():
        # public builds: never the user's own key; the built-in AI (through the Klipon server) when it's on
        o.pop("api_key", None)
        if licence.server_ai():
            from . import ai
            o["api_key"], o["ai_model"] = ai.SERVER_KEY, ai.SERVER_MODEL
    o.update(extra or {})
    from . import licence
    if not licence.pro() and not o.get("_trial_full"):   # trial used up: Pro features never reach the render
        o["overlays"] = [x for x in (o.get("overlays") or []) if not x.startswith("cs2_")]
        o["killfeed_draw"] = False
        o["bg_music"] = ""
        if (o.get("react_sfx") or "") not in ("", "none"):
            o["react_sfx"] = ""   # built-in sound only
    return o


def process(source, progress, meta=None, analysis_cache=None):
    """Find clips in a VOD (link or file) and build TikToks for the best ones."""
    key = stream_key(source)
    meta = meta or {}
    prev = get_stream(key) or {}
    made_before = [c for c in prev.get("clips", []) if c.get("status") != "discarded"]
    update_stream(key, source=source, status="processing", message="Starting", progress=0.0, started=time.time(),
                  title=meta.get("title") or prev.get("title"), date=meta.get("date") or prev.get("date"))

    def prog(p, msg=None):
        progress(p, msg)
        update_stream(key, progress=round(p, 3), message=msg or "")

    try:
        a = (analysis_cache or {}).get(key) or pl.Analysis(source)
        if analysis_cache is not None:
            analysis_cache[key] = a
        if a.remote:   # 2.35: whose stream this is (their name goes on the TikToks)
            try:
                ch = a.remote.resolve().get("uploader") or ""
                if ch:
                    update_stream(key, channel=ch)
            except Exception as e:
                log.warning("Couldn't read the channel name: %s", e)
        chosen_look = look_style(get_stream(key).get("look") or "")
        opts = style_opts(channel_style(key, chosen_look or {}))
        # re-ranking a stream: remember the moments behind TikToks already made from it
        old_cands = {}
        if made_before:
            if not a.clips:
                _restore(a)
            old_cands = {c["id"]: c for c in a.clips}
        res = pl.analyze(a, opts, lambda p, m=None: prog(0.75 * p, m))
        if (a.transcript or {}).get("language"):   # 0.1.2: the editor's subtitle language starts on this
            update_stream(key, language=a.transcript["language"])
        kept = _carry_over(a, made_before, old_cands)
        title = meta.get("title") or a.info.get("title") or os.path.splitext(os.path.basename(source))[0]
        date = meta.get("date") or time.strftime("%Y-%m-%d")
        folder = os.path.join(output_root(), _safe(f"{date} {title}", 80) or key)
        n_auto = int(opts.get("auto_count", 0))   # 0 = rank moments, you preview and pick
        min_score = int(opts.get("auto_min_score", 70))
        chosen = [c for c in a.clips if c["score"] >= min_score][:n_auto] if n_auto > 0 else []
        room = _trial_room({"clips": prev.get("clips") or []})
        if room is not None:
            chosen = chosen[:room]
        update_stream(key, title=title, date=date, folder=folder, duration=a.info.get("duration"),
                      candidates=len(a.clips), webcam=a.webcam)

        def rp(p, m=None):
            prog(0.75 + 0.25 * p, m)
        from .util import HEAVY
        # 2.35.1: in Auto each TikTok gets its game's look (CS2 -> CS2 + kill feed), same as one made by hand
        groups, looks = {}, {}
        for c in chosen:
            p = None if chosen_look else _game_look(a, c["id"])
            st = dict(p["style"], look=p["id"]) if p else (chosen_look or {})
            looks[c["id"]] = st
            groups.setdefault(st.get("look") or "", (st, []))[1].append(c["id"])
        built = []
        with HEAVY:   # 2.27.3
            for st, ids in groups.values():
                o = opts if chosen_look or not st else style_opts(channel_style(key, st))   # opts = the stream's own look
                built += pl.render_all(a, ids, {}, o, os.path.join(folder, ".work", "new"), rp)
        clips = []
        for r in built:
            c = next(x for x in a.clips if x["id"] == r["id"])
            r["path"] = _put(r["path"], _free_name(folder, len(kept) + len(clips) + 1, c.get("hook") or c["title"]))
            thumb = thumbnail(r["path"], os.path.splitext(r["path"])[0] + ".jpg")
            clips.append({
                "id": c["id"], "title": c.get("hook") or c["title"], "transcript_title": c["title"],
                "caption": c.get("caption", ""), "score": c["score"], "why": c.get("why", ""),
                "start": c["start"], "end": c["end"], "start_label": fmt_time(c["start"]),
                "path": r["path"], "thumb": thumb, "duration": r["duration"],
                "words": [w["text"] for w in c.get("words", [])],
                "status": "new", "edits": {}, "style": dict(looks.get(c["id"]) or chosen_look or {}, captions=bool(opts.get("captions"))), "rendered_at": time.time(), "checks": r.get("checks") or [], "kill_names": r.get("kill_names") or [],
            })
        made_ids = {x["id"] for x in clips} | {x["id"] for x in kept}
        others = [moment_row(c) for c in a.clips if c["id"] not in made_ids]
        msg = f"{len(clips)} new TikToks" if clips else f"{len(others)} moments ranked - preview and pick"
        update_stream(key, status="ready", message=msg, progress=1.0, clips=kept + clips, extra_candidates=others,
                      signals=getattr(a, "signals_used", []), viewer_clip_count=getattr(a, "viewer_clip_count", 0),
                      finished=time.time())
        prog(1.0, msg)
        return get_stream(key)
    except Exception as e:
        log.exception("Processing failed")
        update_stream(key, status="error", message=str(e))
        raise


CLIP_MAX_LEN = 180.0   # a video file this short is a clip (made into one TikTok), not a whole stream


def is_clip_source(source):
    """A Twitch clip link (0.1.2: or a YouTube Short / Kick clip), or a short video file on this PC."""
    if tw.clip_slug(source):
        return True
    if tw.is_link(source) or not os.path.isfile(source):
        return False
    try:
        from .util import probe
        return probe(source)["duration"] <= CLIP_MAX_LEN
    except Exception:
        return False


def clip_key(source):
    slug = tw.clip_slug(source)
    return ("clip_" + slug) if slug else ("clipfile_" + _safe(os.path.basename(source), 80))


def _stream_video(s):
    """The stream's video. 0.1.0: a Twitch clip whose downloaded file is gone (cache cleared) is downloaded again."""
    v = s.get("video")
    if not v:
        return s["source"]
    if not os.path.isfile(v) and tw.clip_slug(s.get("source") or ""):
        log.info("Clip file missing, downloading it again: %s", s["source"])
        tw.download_clip(s["source"], os.path.dirname(v))
    return v


def import_clip(source, progress, analysis_cache=None):
    """2.17: a Twitch clip link or a short video file becomes one TikTok, using the WHOLE clip
    (no AI trimming, no hook check, no jump cuts: he trims it in the editor if he wants)."""
    from . import scoring as sc
    from . import audio as au
    from . import webcam as wc
    from .games import Games
    key = clip_key(source)
    update_stream(key, source=source, kind="clip", status="processing", message="Starting", progress=0.0, started=time.time())

    def prog(p, msg=None):
        progress(p, msg)
        update_stream(key, progress=round(p, 3), message=msg or "")

    try:
        meta = {}
        if tw.clip_slug(source):
            prog(0.02, "Downloading the clip from " + tw.platform(tw.clip_slug(source)).title())
            local = os.environ.get("LOCALAPPDATA")
            base = os.path.join(local, "BengoGames", "Klipon", "cache") if local else os.path.join(data_dir(), "cache")
            video, meta = tw.download_clip(source, os.path.join(base, key),
                                           lambda f: prog(0.02 + 0.18 * f, f"Downloading the clip ({int(f * 100)}%)"))
            if meta.get("channel"):
                update_stream(key, channel=meta["channel"])   # 2.35: the clip's streamer, for the name tag
        else:
            video = os.path.abspath(source)
        a = pl.Analysis(video)
        if analysis_cache is not None:
            analysis_cache[key] = a
        dur = float(a.info["duration"])
        opts = style_opts()
        title = meta.get("title") or os.path.splitext(os.path.basename(video))[0]
        if a.info.get("has_audio"):
            prog(0.22, "Reading the clip's audio")
            wav = au.extract_wav(a.video, a.cache, opts.get("audio_track"))
            a.wav_path = wav
            a.audio_curve = au.excitement_curve(wav)
            a.silences = au.silences(a.audio_curve["db"])
            prog(0.3, "Writing down what's said")
            from .transcribe import transcribe, speech_lang
            a.transcript = sc.strip_music(transcribe(
                wav, a.cache, model_name=opts.get("whisper_model") or None, device=opts.get("device", "auto"),
                language=speech_lang(opts), duration=dur,
                progress=lambda f: prog(0.3 + 0.4 * f, f"Writing down what's said ({int(f * 100)}%)"),
                status=lambda m: prog(0.3, m)))
            if a.transcript.get("language"):
                update_stream(key, language=a.transcript["language"])
            if opts.get("color_others", True):
                try:
                    from . import voice
                    voice.learn_my_voice(wav, a.transcript.get("speech_words") or a.transcript.get("words", []),
                                         cache_path=os.path.join(a.cache, "voice_me.json"))
                except Exception as e:
                    log.warning("Voice learning failed: %s", e)
        prog(0.72, "Finding your webcam")
        try:
            a.webcam = wc.detect_webcam(a.video, a.info, a.cache, count=12)
        except Exception as e:
            log.warning("Webcam detection failed: %s", e)
            a.webcam = {"found": False, "reason": str(e)}
        games = Games(default=meta.get("game") or "", title=title)
        ws = sc.words_in(a.transcript, 0.0, dur)
        a.clips = [{"id": "c1", "start": 0.0, "end": round(dur, 2), "peak": round(dur * 0.65, 2), "len": round(dur, 1),
                    "score": 80, "title": title, "hook": title, "caption": f"{title} {games.tags(0)}",
                    "why": "Imported clip", "reasons": ["Imported clip"], "category": "imported", "signals": {},
                    "text": " ".join(w["text"] for w in ws), "words": ws, "start_label": fmt_time(0),
                    "hook_checked": True, "game": games.at(0)}]
        a.save()
        date = time.strftime("%Y-%m-%d")
        folder = os.path.join(output_root(), _safe(f"{date} {title}", 80) or key)
        prev = get_stream(key) or {}
        old = next((c for c in prev.get("clips", []) if c["id"] == "c1" and c.get("status") != "discarded"), None)
        update_stream(key, video=a.video, title=title, date=date, folder=prev.get("folder") or folder, duration=dur, webcam=a.webcam,
                      candidates=1, extra_candidates=[], signals=[], clips=[old] if old else [])
        # the whole clip, start to end, with the pauses cut out (2.23: jump cuts on by default; loud game
        # action in a pause is kept, so the lead-up stays). A choice saved on this TikTok wins.
        style = dict((old or {}).get("style") or {})
        style.setdefault("jump_cuts", True)
        rerender(key, "c1", (old or {}).get("edits") or {}, style,
                 lambda p, m=None: prog(0.75 + 0.25 * p, m), analysis_cache, note="Imported again")
        update_stream(key, status="ready", message="TikTok made", progress=1.0, finished=time.time())
        return get_stream(key)
    except Exception as e:
        log.exception("Clip import failed")
        update_stream(key, status="error", message=str(e))
        raise


def _carry_over(a, made, old_cands):
    """Keep TikToks already made from this stream working after a re-rank: their moments get
    stable ids ('m1'...) in the new analysis, and new moments that repeat them are dropped."""
    kept = []
    for i, lc in enumerate(made):
        cand = old_cands.get(lc["id"]) or {"start": lc["start"], "end": lc["end"], "title": lc.get("transcript_title") or lc.get("title", ""),
                                           "peak": (lc["start"] + lc["end"]) / 2, "score": lc.get("score", 60)}
        nid = f"m{i + 1}"
        cand = dict(cand, id=nid)
        a.clips = [c for c in a.clips if c["id"] != nid and
                   min(c["end"], cand["end"]) - max(c["start"], cand["start"]) < 0.5 * (c["end"] - c["start"])]
        a.clips.append(cand)
        kept.append(dict(lc, id=nid))
    if made:
        a.save()
    return kept


def moment_row(c):
    return {"id": c["id"], "title": c.get("hook") or c["title"], "score": c["score"], "start": c["start"],
            "end": c["end"], "start_label": fmt_time(c["start"]), "why": c.get("why", ""),
            "category": c.get("category", ""), "signals": c.get("signals", {}), "ai_why": c.get("ai_why", ""),
            "game": c.get("game", ""),
            "viewer_clips": c.get("viewer_clips", []), "quote": (c.get("text") or "")[:220], "vote": c.get("vote"),
            "on_screen": c.get("on_screen", ""), "lead_up": bool(c.get("lead_up"))}


def _analysis(key, analysis_cache=None):
    s = get_stream(key)
    if not s:
        raise RuntimeError("Stream not found")
    a = (analysis_cache or {}).get(key)
    if a is None:
        a = pl.Analysis(_stream_video(s))
        _restore(a, need=True)
        if analysis_cache is not None:
            analysis_cache[key] = a
    return a


def feedback(key, clip_id, vote, analysis_cache=None, reasons=None):
    """👍/👎 on a moment or TikTok: remembered in the library and taught to the AI clipper."""
    from . import taste
    s = get_stream(key) or {}
    text, title, why, start = "", "", "", None
    try:
        a = _analysis(key, analysis_cache)
        c = next((x for x in a.clips if x["id"] == clip_id), None)
        if c:
            text = c.get("text") or " ".join(w["text"] for w in c.get("words", []))
            title = c.get("hook") or c.get("title", "")
            why, start = c.get("ai_why") or c.get("why", ""), c.get("start")
    except Exception:
        pass
    with _LOCK:
        lib = load()
        st = lib["streams"].get(key) or {}
        for lst in (st.get("clips", []), st.get("extra_candidates", [])):
            for x in lst:
                if x["id"] == clip_id:
                    x["vote"] = vote
                    x["vote_reasons"] = list(reasons or []) if vote == "down" else []
                    title = title or x.get("title", "")
                    if not text and isinstance(x.get("words"), list):
                        text = " ".join(x["words"])
                    start = x.get("start", start)
        save(lib)
    # keyed by stream + time, so re-ranking (which renumbers moments) keeps one vote per moment
    item = f"{key}@{int(round(start or 0))}"
    r = taste.vote(item, vote, text=text, title=title, why=why, stream=s.get("title", ""), reasons=reasons)
    _learn_in_background()
    return r


def _learn_in_background():
    """After new feedback: let the AI rewrite its own rules once enough has come in."""
    try:
        from . import taste
        st = _ai_settings()   # public builds: the built-in server AI learns from each user's own thumbs
        from . import licence
        if st.get("api_key") and licence.ai_allowed():
            taste.maybe_update_rules(st["api_key"], st.get("ai_model"))
    except Exception as e:
        log.warning("Rules update skipped: %s", e)


def _clip_text(key, c, analysis_cache=None):
    text = " ".join(c.get("words") or []) if isinstance(c.get("words"), list) else ""
    if not text:
        try:
            a = _analysis(key, analysis_cache)
            cand = next((x for x in a.clips if x["id"] == c["id"]), None)
            text = (cand or {}).get("text", "")
        except Exception:
            pass
    return text


def set_views(key, clip_id, views, likes=None, source="manual", analysis_cache=None):
    """How many views a posted TikTok got: the strongest signal for what to clip next."""
    from . import taste
    views = None if views in (None, "") else max(0, int(views))
    c = update_clip(key, clip_id, views=views, views_source=source if views is not None else None,
                    views_at=time.time() if views is not None else None)
    taste.record_perf(f"{key}@{int(round(c.get('start') or 0))}", title=c.get("title", ""),
                      text=_clip_text(key, c, analysis_cache), views=views, likes=likes, source=source)
    _learn_in_background()
    return c


def _toks(t):
    import re
    return [w for w in re.sub(r"#\w+", " ", (t or "").lower()).replace("'", "").split() if re.sub(r"\W", "", w)]


def sync_views(analysis_cache=None):
    """Pull view counts for your posted videos from TikTok and match them to Klipon's TikToks
    by title/caption. Returns {"matched": n, "videos": n}."""
    import difflib
    import re
    from . import tiktok as tt
    videos = tt.video_list()
    norm = lambda t: re.sub(r"[^a-z0-9 ]+", "", " ".join(_toks(t)))
    lib = load()
    todo = []
    for key, st in lib["streams"].items():
        for c in st.get("clips", []):
            if c.get("status") == "discarded" or c.get("views_source") == "manual":
                continue
            names = [n for n in {norm(c.get("title")), norm(c.get("caption"))} if len(n) >= 4]
            best, best_r = None, 0.0
            for v in videos:
                if v.get("create_time") and c.get("rendered_at") and v["create_time"] < c["rendered_at"] - 86400 * 2:
                    continue   # posted before this TikTok existed
                desc = norm((v.get("title") or "") + " " + (v.get("video_description") or ""))
                for n in names:
                    r = 1.0 if (len(n.split()) >= 2 and n in desc) else difflib.SequenceMatcher(None, n, desc[:len(n) + 20]).ratio()
                    if r > best_r:
                        best, best_r = v, r
            if best is not None and best_r >= 0.8:
                todo.append((key, c["id"], best))
    for key, cid, v in todo:
        set_views(key, cid, v.get("view_count") or 0, likes=v.get("like_count"), source="tiktok", analysis_cache=analysis_cache)
        update_clip(key, cid, status="posted", tiktok_id=v.get("id"))
    return {"matched": len(todo), "videos": len(videos)}


def moment_preview(key, clip_id, analysis_cache=None):
    a = _analysis(key, analysis_cache)
    return pl.moment_preview(a, clip_id, os.path.join(a.cache, "previews"))


def _game_look(a, clip_id):
    """2.20: a brand-new TikTok gets the look made for its game (Settings: "Pick the look for each game");
    2.35: CS2 always does in Auto (the creator: CS2 streams get the CS2 + kill feed style automatically).
    2.35.1: used by auto-made TikToks too (a CS2 VOD's auto-made TikTok came out in the default look)."""
    from .style import look_for_game, preset
    cand0 = next((c for c in a.clips if c["id"] == clip_id), {})
    p = preset(look_for_game(cand0.get("game") or a.info.get("game") or a.info.get("title") or ""))
    if p and not (load_settings().get("game_looks") or p["id"] == "cs2"):
        p = None
    if p:
        log.info("Using the %s look for this %s TikTok", p["name"], cand0.get("game") or "game")
    return p


def rerender(key, clip_id, edits, style, progress, analysis_cache=None, new_from_candidate=False, note=None):
    """Re-build one TikTok with edited subtitles / style / trim."""
    from . import licence
    licence.check_open()   # 0.1.0: beta over / account blocked: nothing new is made
    s = get_stream(key)
    if not s:
        raise RuntimeError("Stream not found")
    a = (analysis_cache or {}).get(key)
    if a is None:
        a = pl.Analysis(_stream_video(s))
        _restore(a, need=True)
        if analysis_cache is not None:
            analysis_cache[key] = a
    existing = next((c for c in s["clips"] if c["id"] == clip_id), None)
    if not existing and _trial_room(s) == 0:
        _trial_full()
    style = dict(style or {})
    picked =look_style(s.get("look") or "") if not existing and not style.get("look") else None
    if picked:
        style = dict(picked, **style)
    elif not existing and not style.get("look"):
        p = _game_look(a, clip_id)
        if p:
            style = dict(p["style"], **style, look=p["id"])
    opts = style_opts(channel_style(key, style))
    progress(0.05, "Re-building TikTok")
    if opts.get("title_bar") and not opts.get("title_text") and existing:
        opts["title_text"] = existing.get("title") or ""   # the title on screen = this TikTok's title (renames too)
    game = next((c.get("game") for c in a.clips if c["id"] == clip_id), "") or ""
    if game and opts.get("overlays") and not re.search(r"counter-?strike|\bcs2\b|\bcs:?go\b", game, re.I):
        # CS2 HUD parts only make sense on CS2 footage: on another game they'd paste a random strip
        opts["overlays"] = [o for o in opts["overlays"] if not o.startswith("cs2_")]
    idx = (s["clips"].index(existing) + 1) if existing else len(s["clips"]) + 1
    folder = s.get("folder") or os.path.join(output_root(), key)
    try:  # learn from how you changed this TikTok (trims, extensions, subtitle fixes)
        from . import taste
        cand = next((c for c in a.clips if c["id"] == clip_id), None)
        taste.learn_edit(key, cand, edits or {}, a.transcript.get("speech_words") or a.transcript.get("words", []))
    except Exception as e:
        log.warning("Couldn't learn from this edit: %s", e)
    sub = _subs_words(a, clip_id, opts, edits, progress)
    if sub is not None:   # 0.1.2: subtitles in the language picked in the editor (a copy: the stream stays as it is)
        import copy
        a = copy.copy(a)
        a.transcript = sub
    # build it in a side folder first, so a half-made file can never overwrite another TikTok
    r = pl.render_all(a, [clip_id], {clip_id: edits or {}}, opts, os.path.join(folder, ".work", "new"),
                      lambda p, m=None: progress(0.05 + 0.9 * p, m))[0]
    # keep a stable file name per TikTok (overwrite)
    final = os.path.join(folder, f"{idx:02d}_{_safe((existing or {}).get('title') or r['title'], 40).replace(' ', '_') or clip_id}.mp4")
    if existing and existing.get("path") and os.path.dirname(os.path.abspath(existing["path"])) == os.path.abspath(folder):
        final = existing["path"]          # same file as before, even if the title changed
    if existing:
        _remember(key, existing, folder, note or "Your edit")     # so "Undo" can bring this version back
    final = _put(r["path"], final)
    thumb = thumbnail(final, os.path.splitext(final)[0] + ".jpg")
    style = dict(style or {})
    style.setdefault("captions", bool(opts.get("captions")))   # remember whether this TikTok has subtitles
    fields = dict(path=final, thumb=thumb, duration=r["duration"], edits=edits or {}, style=style,
                  rendered_at=time.time(), checks=r.get("checks") or [], kill_names=r.get("kill_names") or [])
    if existing:
        fields["status"] = "edited" if existing["status"] in ("new", "edited") else existing["status"]
        c = update_clip(key, clip_id, **fields)
    else:
        cand = next(c for c in a.clips if c["id"] == clip_id)
        c = dict(id=clip_id, title=cand.get("hook") or cand["title"], transcript_title=cand["title"],
                 caption=cand.get("caption", ""), score=cand["score"], why=cand.get("why", ""),
                 start=cand["start"], end=cand["end"], start_label=fmt_time(cand["start"]),
                 words=[w["text"] for w in cand.get("words", [])], status="new", **fields)
        with _LOCK:
            lib = load()
            st = lib["streams"][key]
            st["clips"].append(c)
            st["extra_candidates"] = [x for x in st.get("extra_candidates", []) if x["id"] != clip_id]
            save(lib)
    progress(1.0, "TikTok updated")
    return c


LANG_NAMES = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian",
              "nl": "Dutch", "pl": "Polish", "tr": "Turkish", "ru": "Russian", "uk": "Ukrainian", "ar": "Arabic",
              "he": "Hebrew", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "ja": "Japanese",
              "ko": "Korean", "zh": "Chinese"}


def _subs_words(a, clip_id, opts, edits=None, progress=None, make=True):
    """0.1.2 (Dienne: pick the language first, then make the subtitles; languages must never cross).
    With a subtitle language picked in the editor (style 'subs_lang'), this TikTok's part of the stream is
    written down again with speech-to-text told that language, and any word in another language's letters is
    left out. Returns a copy of the transcript to make the subtitles from, or None (no language picked).
    make=False only uses what was written down before (for quick reads like the subtitle text box)."""
    import math
    from . import scoring as sc
    lang = str(opts.get("subs_lang") or "").strip().lower()
    if not lang or not a.transcript:
        return None
    cand = next((c for c in a.clips if c["id"] == clip_id), None)
    if not cand:
        return None
    e = edits or {}
    s0 = max(0.0, math.floor(min(cand["start"], float(e.get("start", cand["start"]))) - 3))
    s1 = math.ceil(max(cand["end"], float(e.get("end", cand["end"]))) + 8)
    tr = a.transcript
    words = list(tr.get("words", []))
    wav = os.path.join(a.cache, "audio16k.wav")
    if os.path.isfile(wav):
        from . import transcribe as tsc
        if make and progress:
            progress(0.04, "Writing the subtitles in " + LANG_NAMES.get(lang, lang))
        cached = os.path.join(a.cache, f"subs_{opts.get('whisper_model') or 'ivrit-turbo'}_{lang}_{int(s0 * 10)}_{int(s1 * 10)}.json")
        if make or os.path.isfile(cached):
            new = tsc.transcribe_part(wav, a.cache, s0, s1, lang, model_name=opts.get("whisper_model") or None,
                                      device=opts.get("device", "auto"))
            others = [w for w in words if w.get("other")]   # keep the 'someone else is talking' colour

            def other(w):
                m = (w["start"] + w["end"]) / 2
                return any(o["start"] - 0.15 <= m <= o["end"] + 0.15 for o in others)
            words = ([w for w in words if not s0 <= (w["start"] + w["end"]) / 2 < s1] +
                     [dict(w, other=True) if other(w) else w for w in new])
            words.sort(key=lambda w: w["start"])
    words = [w for w in words if sc.in_language(w["text"], lang)]
    return dict(tr, words=words, language=lang)


HISTORY_MAX = 6


def _remember(key, clip, folder, note, with_file=True):
    """Keep the TikTok as it is right now (video + settings) before changing it, so it can be undone."""
    h = {"at": time.time(), "note": (note or "")[:120], "title": clip.get("title"), "caption": clip.get("caption"),
         "edits": clip.get("edits") or {}, "style": clip.get("style") or {}, "duration": clip.get("duration"),
         "status": clip.get("status"), "file": None}
    src = clip.get("path")
    if with_file and src and os.path.isfile(src):
        try:
            hdir = os.path.join(folder, ".work", "history")
            os.makedirs(hdir, exist_ok=True)
            h["file"] = os.path.join(hdir, f"{clip['id']}_{int(h['at'] * 1000)}.mp4")
            shutil.copy2(src, h["file"])
        except OSError as e:
            log.warning("Couldn't keep the old version for undo: %s", e)
            h["file"] = None
    with _LOCK:
        lib = load()
        for c in lib["streams"][key]["clips"]:
            if c["id"] == clip["id"]:
                hist = c.setdefault("history", [])
                hist.append(h)
                for old in hist[:-HISTORY_MAX]:
                    if old.get("file"):
                        try:
                            os.remove(old["file"])
                        except OSError:
                            pass
                c["history"] = hist[-HISTORY_MAX:]
        save(lib)


def undo(key, clip_id):
    """Put a TikTok back to how it was before the last change (edit or AI request). Instant: the
    old video was kept. Can be pressed again to go back further."""
    s = get_stream(key)
    c = next((x for x in (s or {}).get("clips", []) if x["id"] == clip_id), None)
    if not c or not c.get("history"):
        raise RuntimeError("Nothing to undo on this TikTok.")
    h = c["history"][-1]
    fields = {"title": h.get("title"), "caption": h.get("caption"), "history": c["history"][:-1]}
    if h.get("file"):
        if not os.path.isfile(h["file"]):
            raise RuntimeError("The earlier version of this video is gone, so it can't be undone.")
        final = _put(h["file"], c["path"])
        fields.update(path=final, thumb=thumbnail(final, os.path.splitext(final)[0] + ".jpg"),
                      duration=h.get("duration"), edits=h.get("edits") or {}, style=h.get("style") or {},
                      status=h.get("status") or c.get("status"), rendered_at=time.time())
    out = update_clip(key, clip_id, **fields)
    if (h.get("note") or "").startswith("AI: "):
        try:    # you undid what the AI did: don't let it learn from that request
            from . import taste
            taste.forget_last_request()
        except Exception as e:
            log.warning("Couldn't forget the undone request: %s", e)
    return dict(out, undone=h.get("note") or "last change")


def _phrases(words, gap=0.7, max_words=14):
    """Words -> short phrases [(start, end, text, other)] for showing the AI a timestamped transcript."""
    out, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] >= gap or len(cur) >= max_words
                    or bool(w.get("other")) != bool(cur[-1].get("other"))):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return [(p[0]["start"], p[-1]["end"], " ".join(x["text"] for x in p), bool(p[0].get("other"))) for p in out]


def ask(key, clip_id, text, edits, style, progress, analysis_cache=None):
    """"Tell the AI what to change": you type what you want on one TikTok (make it longer, add the bit
    where..., turn the music down) and the AI edits it. Returns the TikTok with 'ai_reply'."""
    from . import ai, taste
    st = _ai_settings()
    _ai_ok()
    if not st.get("api_key"):
        raise RuntimeError("Add your Claude API key in Settings to use this.")
    text = (text or "").strip()
    if not text:
        raise RuntimeError("Type what you want changed first.")
    s = get_stream(key)
    if not s:
        raise RuntimeError("Stream not found")
    a = _analysis(key, analysis_cache)
    lc = next((c for c in s["clips"] if c["id"] == clip_id), None)
    if not lc:
        raise RuntimeError("TikTok not found")
    edits, style = dict(edits or {}), dict(style or {})
    opts = style_opts(channel_style(key, style))
    dur = float(a.info.get("duration") or 0)
    c0 = float(edits.get("start", lc["start"]))
    c1 = float(edits.get("end", lc["end"]))
    progress(0.05, "AI is reading your request")
    w0, w1 = max(0.0, c0 - 90), min(dur or c1 + 240, c1 + 240)
    pl.label_voices(a, w0, w1)
    words_all = a.transcript.get("speech_words") or a.transcript.get("words", [])
    others = {round(w["start"], 2) for w in a.transcript.get("words", []) if w.get("other") and w0 <= w["start"] <= w1}
    words = [dict(w, other=bool(w.get("other")) or round(w["start"], 2) in others) for w in words_all if w0 <= w["start"] <= w1]
    if not words:
        raise RuntimeError("There's no speech around this TikTok for the AI to work with.")
    cur_cuts = []
    segs = edits.get("segs") or []
    for (a0, b0), (a1, _b1) in zip(segs, segs[1:]):
        if a1 > b0:
            cur_cuts.append([round(c0 + float(b0), 1), round(c0 + float(a1), 1)])
    lines = []
    for p0, p1, t, other in _phrases(words):
        mark = ">> " if c0 - 0.3 <= p0 < c1 else "   "
        lines.append(f"{mark}[{p0:.1f}] {'(other) ' if other else ''}{t}")
    ctx = (f"CLIP NOW: \"{lc.get('title', '')}\" start={c0:.1f} end={c1:.1f} ({c1 - c0:.0f}s), cut out={cur_cuts or 'nothing'}\n"
           f"SETTINGS NOW: subtitles={'on' if opts.get('captions') else 'off'}, volume_db={edits.get('volume_db', opts.get('volume_db', 0))}, "
           f"music={'none' if not opts.get('bg_music') else 'on, volume ' + str(edits.get('music_volume', opts.get('bg_music_volume', 0.16)))}, "
           f"jump_cuts={bool(opts.get('jump_cuts'))}, cutaways={bool(opts.get('cutaways'))}, gameplay_zooms={bool(opts.get('zoom'))}, "
           f"layout={opts.get('layout', 'auto')}, caption=\"{lc.get('caption', '')}\"\n"
           f"The stream is {dur:.0f}s long. Transcript from {w0:.0f}s to {w1:.0f}s:\n" + "\n".join(lines))
    try:
        r = ai.edit_request(text, ctx, st["api_key"], st.get("ai_model"))
    except Exception as e:
        raise RuntimeError(f"The AI couldn't be reached: {e}")
    reply = str(r.get("reply") or "").strip()
    cannot = str(r.get("cannot") or "").strip()
    changed = False

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # --- timing: start / end / cuts
    n0, n1 = num(r.get("start")), num(r.get("end"))
    n0 = c0 if n0 is None else n0
    n1 = c1 if n1 is None else n1
    if isinstance(r.get("cut"), list):
        cuts = sorted([float(x[0]), float(x[1])] for x in r["cut"]
                      if isinstance(x, (list, tuple)) and len(x) == 2 and num(x[0]) is not None and num(x[1]) is not None)
    else:
        cuts = [list(x) for x in cur_cuts]     # null = leave the parts already cut out as they are
    timing = r.get("changed") is not False and (abs(n0 - c0) > 0.4 or abs(n1 - c1) > 0.4 or cuts != [list(x) for x in cur_cuts])
    if timing:
        def snap_start(t):
            near = [w for w in words_all if abs(w["start"] - t) <= 1.5]
            return max(0.0, min(near, key=lambda w: abs(w["start"] - t))["start"] - 0.25) if near else t

        def snap_end(t):
            near = [w for w in words_all if abs(w["end"] - t) <= 1.5]
            return (min(near, key=lambda w: abs(w["end"] - t))["end"] + 0.35) if near else t
        n0 = round(max(0.0, snap_start(n0)) if abs(n0 - c0) > 0.4 else c0, 2)
        n1 = round(min(dur or 1e9, snap_end(n1)) if abs(n1 - c1) > 0.4 else c1, 2)
        if n1 - n0 < 6 or n1 - n0 > 240:
            timing = False
            cannot = (cannot + " " if cannot else "") + "The new length didn't make sense, so I left the timing alone."
    if timing:
        kept, t = [], n0
        for a0, b0 in cuts:
            a0, b0 = max(n0, snap_end(a0) - 0.2), min(n1, snap_start(b0) + 0.1)
            if b0 - a0 >= 1.0 and a0 - t >= 1.0:
                kept.append([round(t - n0, 3), round(a0 - n0, 3)])
                t = b0
        if n1 - t >= 1.0:
            kept.append([round(t - n0, 3), round(n1 - n0, 3)])
        edits["start"], edits["end"] = n0, n1
        for k in ("captions", "text", "seg_gain", "segs"):
            edits.pop(k, None)       # the old cuts / hand-typed subtitles no longer line up
        if len(kept) > 1:
            edits["segs"] = kept
            edits["captions"] = timeline_captions(key, clip_id, n0, kept, style, analysis_cache)
        changed = True
    # --- settings
    if isinstance(r.get("subtitles"), bool) and r["subtitles"] != bool(opts.get("captions")):
        style["captions"] = r["subtitles"]; changed = True
    for k_ai, k in (("jump_cuts", "jump_cuts"), ("cutaways", "cutaways"), ("gameplay_zooms", "zoom")):
        if isinstance(r.get(k_ai), bool) and r[k_ai] != bool(opts.get(k)):
            style[k] = r[k_ai]; changed = True
    if r.get("layout") in ("auto", "split", "blur", "crop", "fullcam", "camblur") and r["layout"] != opts.get("layout", "auto"):
        style["layout"] = r["layout"]; changed = True
    v = num(r.get("volume_db"))
    if v is not None and abs(v - float(edits.get("volume_db", opts.get("volume_db", 0)) or 0)) > 0.4:
        edits["volume_db"] = max(-20.0, min(15.0, v)); changed = True
    v = num(r.get("music_volume"))
    if v is not None and opts.get("bg_music"):
        edits["music_volume"] = max(0.0, min(0.5, v)); changed = True
    fields = {}
    for k in ("title", "caption"):
        if isinstance(r.get(k), str) and r[k].strip() and r[k].strip() != (lc.get(k) or ""):
            fields[k] = r[k].strip()[:150 if k == "title" else 2200]
    need_render = changed
    note = "AI: " + text[:100]
    if fields:
        if not need_render:
            _remember(key, lc, s.get("folder") or "", note, with_file=False)
        changed = True
    if need_render:
        c = rerender(key, clip_id, edits, style, lambda p, m=None: progress(0.15 + 0.85 * p, m), analysis_cache, note=note)
    if fields:
        update_clip(key, clip_id, **fields)   # after the old title/caption were kept for undo
    if need_render:
        c = next(x for x in get_stream(key)["clips"] if x["id"] == clip_id)
    else:
        c = next(x for x in get_stream(key)["clips"] if x["id"] == clip_id)
    if not reply:
        reply = "Done." if changed else "I wasn't sure what to change. Can you say it another way?"
    if changed:
        try:
            taste.note_request(text, title=lc.get("title", ""), did=reply)
            _learn_in_background()
        except Exception as e:
            log.warning("Couldn't remember this request: %s", e)
    out = dict(c, ai_reply=reply, ai_cannot=cannot, ai_changed=changed)
    progress(1.0, "Done" if changed else "Nothing changed")
    return out


def find_moment(key, text, progress, analysis_cache=None):
    """Find a moment in a stream from your description ("the bit where the guy asks for my number").
    The AI searches everything that was said; matches go to the top of the stream's moments list."""
    from . import ai
    from . import scoring as sc
    st = _ai_settings()
    _ai_ok()
    if not st.get("api_key"):
        raise RuntimeError("Add your Claude API key in Settings to use this.")
    text = (text or "").strip()
    if len(text) < 4:
        raise RuntimeError("Describe the moment you're looking for first.")
    s = get_stream(key)
    if not s:
        raise RuntimeError("Stream not found")
    a = _analysis(key, analysis_cache)
    words_all = a.transcript.get("speech_words") or a.transcript.get("words", [])
    if not words_all:
        raise RuntimeError("This stream hasn't been read yet. Rank it first, then search.")
    dur = float(a.info.get("duration") or words_all[-1]["end"])
    progress(0.05, "AI is searching the stream")
    # sections of about 20 minutes of talking, overlapping a little so nothing falls in a gap
    phrases = _phrases(words_all, gap=0.8, max_words=16)
    sections, cur, size = [], [], 0
    for p0, p1, t, _o in phrases:
        line = f"[{p0:.0f}] {t}"
        cur.append((p0, p1, line))
        size += len(line) + 1
        if size >= 45000:
            sections.append(cur)
            cur, size = cur[-25:], sum(len(x[2]) + 1 for x in cur[-25:])
    if cur and (not sections or cur[-1] is not sections[-1][-1]):
        sections.append(cur)
    secs = [(c[0][0], c[-1][1], "\n".join(x[2] for x in c)) for c in sections]
    try:
        found = ai.search_moment(text, secs, st["api_key"], st.get("ai_model"),
                                 progress=lambda f: progress(0.05 + 0.85 * f, f"AI is searching the stream ({int(f * 100)}%)"))
    except Exception as e:
        raise RuntimeError(f"The AI couldn't be reached: {e}")
    rows = []
    stamp = int(time.time()) % 100000
    for i, m in enumerate(found[:3]):
        near0 = [w for w in words_all if abs(w["start"] - m["start"]) <= 1.5]
        near1 = [w for w in words_all if abs(w["end"] - m["end"]) <= 1.5]
        st0 = max(0.0, (min(near0, key=lambda w: abs(w["start"] - m["start"]))["start"] - 0.25) if near0 else m["start"])
        en0 = min(dur, (min(near1, key=lambda w: abs(w["end"] - m["end"]))["end"] + 0.35) if near1 else m["end"])
        if en0 - st0 < 6:
            continue
        ws = sc.words_in(a.transcript, st0, en0)
        title = m["title"] or "Found moment"
        c = {"id": f"f{stamp}{i}", "start": round(st0, 2), "end": round(en0, 2), "len": round(en0 - st0, 1),
             "peak": round(st0 + (en0 - st0) * 0.65, 2), "score": int(round(m["confidence"])),
             "title": title, "hook": title, "caption": f"{title} #twitch #gaming #fyp",
             "why": "You described it", "ai_why": m["what"], "category": "found", "signals": {}, "reasons": ["You described it"],
             "text": " ".join(w["text"] for w in ws), "words": ws, "start_label": fmt_time(st0),
             "found": True, "found_from": text[:300], "hook_checked": True}
        a.clips.append(c)
        rows.append(dict(moment_row(c), found=True))
    if rows:
        a.save()
        with _LOCK:
            lib = load()
            stt = lib["streams"][key]
            stt["extra_candidates"] = rows + [x for x in stt.get("extra_candidates", [])]
            save(lib)
    progress(1.0, f"Found {len(rows)}" if rows else "Nothing matched")
    return {"found": rows, "searched_minutes": round(dur / 60)}


def _restore(a, need=False):
    import glob
    import numpy as np
    from .audio import HOP, silences
    cached = os.path.join(a.cache, "analysis.json")
    if need and not os.path.isfile(cached):
        # 0.1.0: the stream's saved data was cleared from this PC (e.g. a disk clean-up)
        raise RuntimeError("This stream's saved data was cleared from your PC. Paste its link again on the home page "
                           "to rebuild it: your TikToks and their edits are kept.")
    if os.path.isfile(cached):
        with open(cached, "r", encoding="utf-8") as f:
            d = json.load(f)
        a.clips, a.webcam = d.get("clips", []), d.get("webcam")
    tr = sorted(glob.glob(os.path.join(a.cache, "transcript_*.json")), key=os.path.getmtime)   # the newest one
    if tr:
        with open(tr[-1], "r", encoding="utf-8") as f:
            a.transcript = json.load(f)
        from .scoring import strip_music
        a.transcript = strip_music(a.transcript)
    npz = os.path.join(a.cache, "audio_curve.npz")
    if os.path.isfile(npz):
        z = np.load(npz)
        a.audio_curve = {"hop": HOP, "db": z["db"], "z": z["z"]}
        a.silences = silences(z["db"])


def clip_words(key, clip_id, analysis_cache=None):
    """Exact transcript words for a TikTok (for the subtitle editor)."""
    s = get_stream(key)
    a = (analysis_cache or {}).get(key)
    if a is None:
        a = pl.Analysis(_stream_video(s))
        _restore(a, need=True)
        if analysis_cache is not None:
            analysis_cache[key] = a
    c = next((x for x in a.clips if x["id"] == clip_id), None)
    if not c:
        return []
    from .scoring import words_in
    made = next((x for x in s.get("clips", []) if x["id"] == clip_id), None) or {}
    sub = _subs_words(a, clip_id, made.get("style") or {}, made.get("edits"), make=False)
    return [w["text"] for w in words_in(sub or a.transcript, c["start"], c["end"])]


def waveform(path, t0, t1, per_sec=40):
    """Peak levels (0-1) for the timeline's audio track, per_sec values per second."""
    import numpy as np
    sr = 8000
    try:
        raw = run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{t1 - max(0.0, t0):.3f}", "-i", path,
                   "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]).stdout
        x = np.abs(np.frombuffer(raw, np.int16).astype(np.float32)) / 32768.0
        hop = sr // per_sec
        n = len(x) // hop
        pk = x[:n * hop].reshape(n, hop).max(1) if n else np.zeros(0)
        lead = int(round(max(0.0, -t0) * per_sec))
        pk = np.concatenate([np.zeros(lead), pk])
        return {"per_sec": per_sec, "peaks": [round(float(v) ** 0.6, 3) for v in pk]}
    except Exception as e:
        log.warning("Waveform failed: %s", e)
        return {"per_sec": per_sec, "peaks": []}


def timeline_captions(key, clip_id, clip_start, segs, style=None, analysis_cache=None):
    """Fresh subtitles for the kept parts (timeline 'Redo subtitles'), made exactly like the
    automatic ones: cleaned-up words, your word fixes, phrase chunks, other-speaker colour."""
    from . import captions as cap
    from . import taste
    a = _analysis(key, analysis_cache)
    segs = sorted([float(s[0]), float(s[1])] for s in segs)
    t0, t1 = clip_start + segs[0][0], clip_start + segs[-1][1]
    opts = style_opts(channel_style(key, style or {}))
    if opts.get("color_others", True):
        pl.label_voices(a, t0, t1)
    sub = _subs_words(a, clip_id, opts, {"start": t0, "end": t1})
    ws = []
    for w in (sub or a.transcript).get("words", []):
        m = (w["start"] + w["end"]) / 2 - clip_start
        if any(x <= m < y for x, y in segs):
            ws.append(dict(w, start=round(w["start"] - clip_start, 3), end=round(w["end"] - clip_start, 3)))
    ws = taste.fix_words(cap.clean_words(ws))
    if opts.get("style") == "editor":
        chunks = cap.editor_chunks(ws, max_words=4)
    else:
        chunks = cap.group_words(ws, opts.get("words_per_line", 3))
    return [{"s": round(ch[0]["start"], 2), "e": round(ch[-1]["end"] + 0.1, 2),
             "text": " ".join(w["text"] for w in ch), "other": bool(ch[0].get("other"))} for ch in chunks]


def preview(key, clip_id, style=None, cam_box=None, game_box=None, analysis_cache=None):
    """2.29: the live 9:16 preview layout for one TikTok with the style being edited (nothing is rendered)."""
    from . import render as rd
    s = get_stream(key)
    if not s:
        raise RuntimeError("Stream not found")
    a = (analysis_cache or {}).get(key)
    if a is None:
        a = pl.Analysis(_stream_video(s))
        _restore(a, need=True)
        if analysis_cache is not None:
            analysis_cache[key] = a
    lc = next((x for x in s["clips"] if x["id"] == clip_id), {})
    c = next((x for x in a.clips if x["id"] == clip_id), None)
    if not c:
        raise RuntimeError("Clip not found")
    c = pl._apply_edit(c, lc.get("edits") or {})
    if cam_box:
        c = dict(c, cam_box=[float(v) for v in cam_box])
    opts = style_opts(channel_style(key, dict(lc.get("style") or {}, **(style or {}))))
    wc = pl.clip_webcam(a, c) or {}
    title = (opts.get("title_text") or "").strip() or lc.get("title") or c.get("title") or ""
    gb = [float(v) for v in game_box] if game_box else c.get("game_box")
    return rd.preview_geometry(a.info, wc, opts, gb, title)


def _game_box_info(a, c, wc, opts):
    """2.28: the gameplay box for the Timeline: where it is now and its shape (w/h as fractions of the stream)."""
    from . import render as rd
    try:
        layout = rd._resolve_layout(wc, opts.get("layout", "auto"))
        panel = rd.game_panel(a.info, wc, layout, opts.get("cam_ratio"))
        if not panel:
            return {"game_box": None, "game_shape": None}
        box = c.get("game_box") or rd.default_game_box(a.info, wc, layout, float(opts.get("game_focus", 0.5)),
                                                       opts.get("cam_ratio"))
        shape = (panel[0] / panel[1]) * a.info["height"] / a.info["width"]   # box w / box h in fractions
        return {"game_box": box, "game_shape": round(shape, 5)}
    except Exception as e:
        log.warning("Gameplay box unavailable: %s", e)
        return {"game_box": None, "game_shape": None}


def timeline(key, clip_id, analysis_cache=None):
    """Everything the timeline editor needs for one TikTok, in clip time (0 = clip start)."""
    from . import director as dr
    from . import captions as cap
    from .util import probe
    s = get_stream(key)
    if not s:
        raise RuntimeError("Stream not found")
    a = (analysis_cache or {}).get(key)
    if a is None:
        a = pl.Analysis(_stream_video(s))
        _restore(a, need=True)
        if analysis_cache is not None:
            analysis_cache[key] = a
    c = next((x for x in a.clips if x["id"] == clip_id), None)
    lc = next((x for x in s["clips"] if x["id"] == clip_id), {})
    if not c:
        raise RuntimeError("Clip not found")
    edits = lc.get("edits") or {}
    c = pl._apply_edit(c, edits)
    c = pl.linger_end(a, c, edits, style_opts(lc.get("style") or {}))
    start, end = c["start"], c["end"]
    pad = 30.0   # room to extend the TikTok in the timeline
    w0, w1 = max(0.0, start - pad), min(a.info["duration"], end + pad)
    # source video for the preview player
    if a.remote:
        path, off = a.remote.proxy(w0, w1)   # quick low-res preview; the TikTok is built in full quality
    else:
        path, off = a.video, 0.0
    vinfo = probe(path)
    opts = style_opts(channel_style(key, lc.get("style") or {}))
    if opts.get("color_others", True):
        pl.label_voices(a, w0, w1)
    from . import taste
    words = [dict(w, start=round(w["start"] - start, 3), end=round(w["end"] - start, 3))
             for w in a.transcript.get("words", []) if w0 <= w["start"] <= w1]
    words = taste.fix_words(words)
    if c.get("segs_override"):
        segs = c["segs_override"]
    else:
        z = None
        if a.audio_curve is not None:
            i0, i1 = int(start / a.audio_curve["hop"]), int(end / a.audio_curve["hop"]) + 1
            z = a.audio_curve["z"][i0:i1]
        in_clip = [w for w in words if 0 <= w["start"] <= end - start]
        segs = dr.plan_edit(dict(c, segs_override=None, shots=pl.clip_shots(a, c)), in_clip, z, 0.5,
                            dict(opts, hook_teaser=False, zoom=False, cutaways=False, sfx=False))["segs"]
        segs = [[a_, b_] for a_, b_ in segs]
    if c.get("captions_override") is not None:
        caps = c["captions_override"]
    else:
        in_clip = cap.clean_words([w for w in words if 0 <= w["start"] <= end - start])
        if c.get("edited_text"):
            in_clip = cap.apply_text_edit(in_clip, c["edited_text"])
        if opts.get("style") == "editor":
            chunks = cap.editor_chunks(in_clip, max_words=4)
        else:
            chunks = cap.group_words(in_clip, opts.get("words_per_line", 3))
        caps = [{"s": round(ch[0]["start"], 2), "e": round(ch[-1]["end"] + 0.1, 2),
                 "text": " ".join(w["text"] for w in ch), "other": bool(ch[0].get("other"))} for ch in chunks]
    wc = pl.clip_webcam(a, c) or {}
    gains = list(c.get("seg_gain") or [])
    segs = [[a_, b_, (gains[i] if i < len(gains) else 0)] for i, (a_, b_) in enumerate([s_[:2] for s_ in segs])]
    music = (opts.get("bg_music") or "").strip()
    mv = c.get("music_volume")
    from .sounds import list_sounds
    return {
        "sfx": [{"t": round(m["at"] - start, 3), "name": m["name"], "vol": m.get("vol", 0.5)} for m in c.get("sfx_marks") or []],
        "sounds": list_sounds(),
        "wave": waveform(path, w0 - off, w1 - off),
        "music": music if music and os.path.isfile(music) and opts.get("bg_music_on", True) else "",
        "music_volume": float(opts.get("bg_music_volume", 0.16) if mv is None else mv),
        "volume_db": float(opts.get("volume_db", 0) if c.get("volume_db") is None else c["volume_db"]),
        "key": key, "clip_id": clip_id,
        "video": path, "video_offset": round(off, 3), "width": vinfo["width"], "height": vinfo["height"],
        "clip_start": start, "clip_len": round(end - start, 3),
        "window": [round(w0 - start, 3), round(w1 - start, 3)],
        "segs": segs, "captions": caps, "words": [{"s": w["start"], "e": w["end"], "t": w["text"], "o": bool(w.get("other"))} for w in words],
        "cam_box": wc.get("box") if wc.get("found") else None,
        **_game_box_info(a, c, wc, opts),
        "peak": round(c.get("peak", start) - start, 2),
    }
