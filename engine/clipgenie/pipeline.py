"""Top-level analysis and render pipeline used by the server."""
import json
import os
import numpy as np

from .util import probe, cache_dir_for, fmt_time, log, HEAVY
from . import audio as au
from . import chat as ch
from . import scoring as sc
from . import webcam as wc
from . import render as rd
from . import twitch as tw


class Analysis:
    """Everything known about one VOD, kept in memory between panel calls."""

    def __init__(self, source):
        self.remote = None
        if tw.is_link(source):
            self.remote = tw.TwitchVod(source)
            self.video = self.remote.url
            self.info = self.remote.info()
            self.cache = self.remote.cache
        else:
            self.video = os.path.abspath(source)
            self.info = probe(self.video)
            self.info["source"] = "file"
            self.cache = cache_dir_for(self.video)
        self.transcript = {"words": [], "segments": []}
        self.audio_curve = None
        self.silences = []
        self.webcam = None
        self.clips = []

    # ---------- persistence ----------
    def save(self):
        with open(os.path.join(self.cache, "analysis.json"), "w", encoding="utf-8") as f:
            json.dump({"clips": self.clips, "webcam": self.webcam, "info": self.info}, f)
        if self.audio_curve is not None:
            np.savez_compressed(os.path.join(self.cache, "audio_curve.npz"),
                                db=self.audio_curve["db"], z=self.audio_curve["z"])

    def to_json(self):
        return {"video": self.video, "info": self.info, "webcam": self.webcam,
                "clips": self.clips, "duration_label": fmt_time(self.info["duration"])}


def analyze(a, opts, progress):
    """opts: chat_path, count, whisper_model, device, language, use_audio/use_chat/use_text,
    target_len, min_len, max_len, api_key, ai_model, streamer"""
    info = a.info
    dur = info["duration"]
    hop = au.HOP
    from .games import Games
    games = Games(chapters=info.get("chapters"), default=info.get("game"), title=info.get("title", ""))
    if games.names():
        log.info("Game(s) in this stream: %s", ", ".join(games.names()))
    try:  # "too long" thumbs-downs make every new clip shorter
        from . import taste as _taste
        _ls = _taste.reason_bias()["len_scale"]
        if _ls != 1.0:
            opts = dict(opts, target_len=float(opts.get("target_len", 30)) * _ls,
                        max_len=float(opts.get("max_len", 45)) * _ls)
            log.info("You said clips were too long: aiming %d%% shorter", round((1 - _ls) * 100))
    except Exception as e:
        log.warning("Couldn't apply length feedback: %s", e)
    n = int(dur / hop) + 1

    audio_z = None
    if a.remote:
        site = a.remote.platform.title()   # 0.1.2: Twitch / Youtube / Kick
        site = "YouTube" if site == "Youtube" else site
        progress(0.01, f"Downloading audio from {site}")
        wav = a.remote.audio_wav(lambda f: progress(0.01 + 0.12 * f, f"Downloading audio from {site} ({int(f * 100)}%)"))
    elif info["has_audio"]:
        progress(0.01, "Extracting audio")
        wav = au.extract_wav(a.video, a.cache, opts.get("audio_track"))
    if info["has_audio"]:
        with HEAVY:   # 2.27.3: loudness, speech and voice are heavy: a TikTok being made waits for them (and vice versa)
            progress(0.14 if a.remote else 0.05, "Measuring loudness")
            a.audio_curve = au.excitement_curve(wav)
            a.silences = au.silences(a.audio_curve["db"])
            if opts.get("use_audio", True):
                audio_z = a.audio_curve["z"]

            if opts.get("use_text", True):
                t0 = 0.15 if a.remote else 0.10
                span = 0.55 if a.remote else 0.62

                def tp(f):
                    progress(t0 + span * f, f"Transcribing speech ({int(f * 100)}%)")
                progress(t0, "Loading speech model (first run downloads it)")
                from .transcribe import transcribe
                a.transcript = transcribe(
                    wav, a.cache, model_name=opts.get("whisper_model", "small"),
                    device=opts.get("device", "auto"), language=opts.get("language") or None,
                    progress=tp, duration=dur, status=lambda m: progress(t0, m))
                a.transcript = sc.strip_music(a.transcript)
                a.wav_path = wav
                if opts.get("color_others", True):
                    progress(0.72 if a.remote else 0.73, "Learning your voice")
                    try:
                        from . import voice
                        voice.learn_my_voice(wav, a.transcript.get("speech_words") or a.transcript.get("words", []),
                                             cache_path=os.path.join(a.cache, "voice_me.json"))
                    except Exception as e:
                        log.warning("Voice learning failed: %s", e)

    chat_z = None
    msgs = []
    if opts.get("use_chat", True) and opts.get("chat_path"):
        progress(0.72, "Reading chat replay")
        try:
            msgs = ch.load_chat(opts["chat_path"])
        except Exception as e:
            log.warning("Chat file could not be read: %s", e)
    elif opts.get("use_chat", True) and a.remote:
        progress(0.70, "Downloading chat replay")
        try:
            msgs = a.remote.chat(lambda f: progress(0.70 + 0.08 * f, f"Downloading chat replay ({int(f * 100)}%)"))
        except Exception as e:
            log.warning("Chat download failed: %s", e)
            a.chat_error = str(e)
    chat_funny = None
    if msgs:
        chat_z, chat_funny, chat_hype, kept = ch.chat_signals(msgs, dur, hop=hop, lag=float(opts.get("chat_lag", 6)))
        msgs_clean = kept
    else:
        msgs_clean = []

    # viewer-made Twitch clips of this VOD
    viewer_clips = []
    if a.remote and opts.get("use_viewer_clips", True):
        progress(0.76, "Checking clips your viewers made")
        try:
            viewer_clips = a.remote.viewer_clips()
        except Exception as e:
            log.warning("Viewer clips unavailable: %s", e)

    # AI reads the whole transcript (needs a Claude API key in Settings)
    ai_moments = []
    if opts.get("api_key") and opts.get("use_ai_finder", True) and a.transcript.get("segments"):
        progress(0.77, "AI reading the stream for funny moments")
        from . import ai
        try:
            cache_path = os.path.join(a.cache, f"ai_moments_{opts.get('ai_model') or ai.DEFAULT_MODEL}.json")
            # the AI's read is cached per stream, but your feedback changes what it should look for:
            # if you've rated / edited / added views since the last read, read the stream again
            stamp_path, stamp = cache_path[:-5] + ".taste", "0"
            try:
                from . import taste as _t
                stamp = str(_t.feedback_stamp()) + ":" + str((_t.rules() or {}).get("at", 0)) + ":" + games.stamp()
                old = open(stamp_path).read().strip() if os.path.isfile(stamp_path) else None
                if old is None and os.path.isfile(cache_path):   # read before 2.9: stale if you gave feedback since
                    old = stamp if _t.latest_feedback() <= os.path.getmtime(cache_path) else ""
                elif old is not None and old.count(":") == 1:      # read before 2.14 (no game info in the stamp)
                    old = old + ":" + games.stamp() if not games.names() else old
                if os.path.isfile(cache_path) and old is not None and old != stamp:
                    os.replace(cache_path, cache_path + ".old")
                    log.info("Your feedback changed since the last AI read: reading the stream again")
            except Exception as e:
                log.warning("Taste check failed: %s", e)
            ai_moments = ai.find_moments(
                a.transcript, msgs_clean, dur, opts["api_key"], model=opts.get("ai_model"),
                cache_path=cache_path, games=games,
                progress=lambda f: progress(0.77 + 0.04 * f, f"AI reading the stream ({int(f * 100)}%)"))
            if ai_moments:
                with open(stamp_path, "w") as f:
                    f.write(stamp)
            elif os.path.isfile(cache_path + ".old"):
                os.replace(cache_path + ".old", cache_path)   # the new read failed: keep the old one
        except Exception as e:
            log.warning("AI moment finder failed: %s", e)
    ai_moments = ai_moments or load_ai_moments(a.cache)

    progress(0.81, "Scoring moments")
    use_text = opts.get("use_text", True)
    text_c = sc.text_curve(a.transcript, dur, hop) if use_text else None
    funny_c = sc.funny_curve(a.transcript, dur, hop) if use_text else None
    if funny_c is not None and games.names():
        # the game's own hype words ("wasted", "busted", "clutch", "glitch"...) count as a signal too
        for g in a.transcript.get("segments", []):
            v = games.cue_score(g["start"], g.get("text", ""))
            if v > 0:
                i0, i1 = int(g["start"] / hop), min(len(funny_c), int(g.get("end", g["start"] + 3) / hop) + 1)
                funny_c[i0:i1] = np.maximum(funny_c[i0:i1], 0.7 * v * max(1.0, float(funny_c.max() or 1.0)))
    # small chats (a few messages a minute) make "spikes" out of 2-3 messages: trust chat less
    weights = {"chat": 0.30, "audio": 0.22, "text": 0.18, "funny": 0.30, "clips": 0.40, "ai": 0.45}
    trust = 1.0
    if msgs:
        per_min = len(msgs_clean) / max(1.0, dur / 60.0)
        trust = min(1.0, max(0.15, (per_min - 2.0) / 18.0))
        weights["chat"] = round(0.30 * trust, 3)
        log.info("Chat: %.1f msgs/min, weight %.2f", per_min, weights["chat"])
        if chat_funny is not None and funny_c is not None and np.any(chat_funny):
            # laugh emotes in chat confirm a funny moment
            funny_c = funny_c + chat_funny / (float(chat_funny.max()) + 1e-6) * 3.0 * trust
    extra = {"funny": funny_c, "clips": sc.clips_curve(viewer_clips, dur, hop) if viewer_clips else None,
             "ai": sc.ai_curve(ai_moments, dur, hop) if ai_moments else None}
    if ai_moments:
        score, parts = sc.combine(n, audio_z, chat_z, text_c, weights, extra=extra)
    else:   # 2.22 no AI read (no Claude key): score how much each moment stands out from its surroundings
        score, parts = sc.combine_contrast(n, {"audio": audio_z, "chat": chat_z, "text": text_c, **extra}, weights, hop)
    a.clips = sc.build_candidates(score, parts, hop, a.transcript, dur,
                                  count=int(opts.get("count", 10)),
                                  opts={"target_len": float(opts.get("target_len", 30)),
                                        "min_len": float(opts.get("min_len", 15)),
                                        "max_len": float(opts.get("max_len", 45)),
                                        "story_max_len": float(opts.get("story_max_len", 58))},
                                  audio_z=a.audio_curve["z"] if a.audio_curve is not None else None,
                                  ai_moments=ai_moments, viewer_clips=viewer_clips)
    for c in a.clips:  # a "spike" in a tiny chat means less: show it honestly
        if "chat" in c.get("signals", {}):
            c["signals"]["chat"] = int(round(c["signals"]["chat"] * trust ** 0.5))
    a.signals_used = sorted(parts.keys())
    a.viewer_clip_count = len(viewer_clips)

    with HEAVY:   # 2.27.3
        progress(0.82, "Finding your webcam")
        try:
            if a.remote:
                a.webcam = wc.detect_webcam(a.remote.meta["low_url"], a.remote.low_info(), a.cache, count=20,
                                            **({"t_range": (a.remote.off, a.remote.off + info["duration"])} if a.remote.range else {}))
            else:
                a.webcam = wc.detect_webcam(a.video, info, a.cache)
        except Exception as e:
            log.warning("Webcam detection failed: %s", e)
            a.webcam = {"found": False, "reason": str(e)}

        if a.clips and opts.get("look_at_gameplay", True):
            progress(0.86, "Watching the gameplay")
            try:
                look_at_gameplay(a, opts, lambda f: progress(0.86 + 0.04 * f, f"Watching the gameplay ({int(f * 100)}%)"))
            except Exception as e:
                log.warning("Gameplay check failed: %s", e)

    try:  # how you usually trim/extend Clip Genie's picks
        from . import taste
        ds, de = taste.edit_bias()
        rb = taste.reason_bias()
        if not ds:
            ds = rb["d_start"]   # your own edits win; thumbs-down reasons fill in when there are none
        if not de:
            de = rb["d_end"]
        if ds or de:
            for c in a.clips:
                if c.get("lead_up") and ds > 0:
                    ds_c = 0.0   # never cut away the play we just added
                else:
                    ds_c = ds
                st = max(0.0, c["start"] + ds_c)
                en = min(a.info["duration"], c["end"] + de)
                if en - st >= 8:
                    c["start"], c["end"] = sc.snap_to_speech(st, en, a.transcript, a.info["duration"], max_back=2.0, max_fwd=2.0)
            log.info("Applied your usual edit: start %+.1fs, end %+.1fs", ds, de)
    except Exception as e:
        log.warning("Couldn't apply learned edits: %s", e)

    if opts.get("api_key") and a.clips:
        progress(0.9, "AI editor reviewing clips")
        from . import ai
        samples = {}
        for c in a.clips:
            samples[c["id"]] = [m for t, m in msgs if c["start"] + 2 <= t <= c["end"] + 12][:25]
        for c in a.clips:
            c["game"] = games.at((c["start"] + c["end"]) / 2)
        extra = games.block_all()
        if extra:
            extra += "\nCaption hashtags: use the tags that fit the clip's game (GTA VI: #gta6 #gtavi #gta)."
        a.clips = ai.refine(a.clips, opts["api_key"], model=opts.get("ai_model"),
                            streamer=opts.get("streamer"), chat_samples=samples, extra=extra)

    # 2.32: never stop on the build-up (taste test, case/upgrade, attempt, story): wait for the payoff
    if a.transcript and a.clips:
        z = a.audio_curve["z"] if a.audio_curve is not None else None
        hop = a.audio_curve["hop"] if a.audio_curve is not None else 0.5
        for c in a.clips:
            try:
                st, en, why = sc.extend_to_payoff(c["start"], c["end"], a.transcript, z, hop, a.info["duration"],
                                                  c.get("title") or "", max_len=float(opts.get("story_max_len", 58)))
                if why:
                    st, en = sc.snap_to_speech(st, en, a.transcript, a.info["duration"], max_back=0.0, max_fwd=2.0)
                    log.info("Clip %s runs on to the payoff (%s): %.1f -> %.1f", c.get("title"), why, c["end"], en)
                    c["end"], c["payoff"] = en, why
            except Exception as e:
                log.warning("Payoff check failed: %s", e)

    for i, c in enumerate(a.clips):
        c["id"] = f"c{i + 1}"
        c["start_label"] = fmt_time(c["start"])
        c["len"] = round(c["end"] - c["start"], 1)
        c["words"] = sc.words_in(a.transcript, c["start"], c["end"])
        c["game"] = games.at((c["start"] + c["end"]) / 2)
        if not c.get("caption"):
            c["caption"] = f"{c['title']} {games.tags(c['start'])}"
    a.save()
    note = ""
    if a.remote and getattr(a, "chat_error", None):
        note = " (chat replay unavailable)"
    progress(1.0, f"Found {len(a.clips)} clips{note}")
    out = a.to_json()
    out["chat_messages"] = len(msgs)
    return out


def load_ai_moments(cache):
    """Any AI moment list cached for this VOD (whichever model made it)."""
    import glob
    for f in sorted(glob.glob(os.path.join(cache, "ai_moments_*.json"))):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return []


def moment_preview(a, clip_id, out_dir):
    """Quick low-res preview of a moment (no edit), so you can check it before building a TikTok."""
    from .util import ffmpeg, run
    c = next(c for c in a.clips if c["id"] == clip_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"moment_{clip_id}_{int(c['start'] * 10)}_{int(c['end'] * 10)}.mp4")
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        return out
    src = a.remote.resolve()["low_url"] if a.remote else a.video
    tmp = out + ".part.mp4"
    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{c['start'] + (a.remote.off if a.remote else 0):.2f}", "-i", src, "-t", f"{c['end'] - c['start']:.2f}",
         "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", tmp])
    os.replace(tmp, out)
    return out


def _apply_edit(c, e):
    c = dict(c)
    for key in ("start", "end"):
        if key in (e or {}):
            c[key] = float(e[key])
    if (e or {}).get("hook") is not None:
        c["hook"] = e["hook"]
    if (e or {}).get("text"):
        c["edited_text"] = e["text"]
    # timeline editor
    if (e or {}).get("segs"):
        c["segs_override"] = [[float(a), float(b)] for a, b in e["segs"]]
    if (e or {}).get("captions") is not None:
        c["captions_override"] = [dict(s=float(x["s"]), e=float(x["e"]), text=str(x["text"]), other=bool(x.get("other")))
                                  for x in e["captions"]]
    if (e or {}).get("seg_gain") is not None:
        c["seg_gain"] = [("mute" if g == "mute" else max(-30.0, min(15.0, float(g or 0)))) for g in e["seg_gain"]]
    if (e or {}).get("volume_db") is not None:
        c["volume_db"] = max(-30.0, min(20.0, float(e["volume_db"])))
    if (e or {}).get("music_volume") is not None:
        c["music_volume"] = max(0.0, min(0.6, float(e["music_volume"])))
    if (e or {}).get("sfx") is not None:   # 2.19: sounds placed in the Timeline [{at (STREAM seconds), name, vol}]
        c["sfx_marks"] = [{"at": float(m["at"]), "name": str(m["name"]), "vol": float(m.get("vol", 0.5))}
                          for m in e["sfx"] if isinstance(m, dict) and m.get("name") is not None and m.get("at") is not None]
    if (e or {}).get("cam_box"):
        c["cam_box"] = [float(v) for v in e["cam_box"]]
    if (e or {}).get("game_box"):   # 2.28: where the gameplay is cut from (Timeline)
        c["game_box"] = [float(v) for v in e["game_box"]]
    return c


def look_at_gameplay(a, opts, progress=None):
    """Make sure each top moment SHOWS the play the reaction is about (edge bug, clutch, reveal):
    move the start back to the action using the words, on-screen motion and (with an API key)
    the AI looking at the frames."""
    from . import vision
    words = a.transcript.get("speech_words") or a.transcript.get("words", [])
    top = sorted(a.clips, key=lambda c: -c["score"])[: int(opts.get("vision_top", 12))]
    max_len = float(opts.get("max_len", 38))
    cam = (a.webcam or {}).get("box") if (a.webcam or {}).get("found") else None
    items, ctx = [], {}
    for k, c in enumerate(top):
        if progress:
            progress(k / max(1, len(top)) * (0.6 if opts.get("api_key") else 1.0))
        new_st, react = vision.lead_up(c["start"], c["end"], words)
        w0 = max(0.0, min(new_st, c["start"]) - 6.0)
        w1 = min(a.info["duration"], max(c["end"], w0 + 12))
        w1 = min(w1, w0 + 70)
        try:
            frames, tmp, off = vision.moment_frames(a, w0, w1, fps=1.0 if w1 - w0 <= 45 else 0.7)
            times, z = vision.motion_curve(tmp, off, w0, w1, cam_box=cam)
        except Exception as e:
            log.warning("Couldn't grab gameplay for %s: %s", c["id"], e)
            frames, times, z = [], [], []
        if react is not None:
            onset = vision.motion_onset(times, z, react) if len(z) else None
            if onset is not None:
                new_st = max(react - 15.0, min(new_st, onset - 1.5))
            if new_st < c["start"] - 0.5:
                c["start"] = round(new_st, 2)
                c["lead_up"] = True
                c["reasons"] = ["Shows the play first"] + [r for r in c.get("reasons", []) if r != "Shows the play first"]
                c["why"] = " + ".join(c["reasons"])
        if len(z):
            sel = (times >= c["start"]) & (times <= c["end"])
            if sel.any():
                c.setdefault("signals", {})["visual"] = int(round(100 * (1 - np.exp(-max(0.0, float(z[sel].max())) / 4.0))))
        if frames and opts.get("api_key"):
            lines = [(w["start"], w["text"]) for w in words if w0 <= w["start"] <= w1]
            merged, cur = [], None
            for t, x in lines:  # group words into lines with a start time
                if cur and t - cur[0] < 4:
                    cur[1].append(x)
                else:
                    cur = [t, [x]]
                    merged.append(cur)
            items.append({"id": c["id"], "frames": frames[:48], "start": c["start"], "end": c["end"], "game": c.get("game", ""),
                          "lines": [(t, " ".join(xs)) for t, xs in merged]})
            ctx[c["id"]] = (w0, w1)
    if items:
        from . import ai
        if progress:
            progress(0.65)
        from .games import Games
        gm = Games(chapters=a.info.get("chapters"), default=a.info.get("game"), title=a.info.get("title", ""))
        res = ai.look_at_moments(items, opts["api_key"], model=opts.get("ai_model"), extra=gm.block_all())
        for c in a.clips:
            r = res.get(c["id"])
            if not r:
                continue
            w0, w1 = ctx[c["id"]]
            try:
                cs, ce = float(r.get("clip_start")), float(r.get("clip_end"))
            except (TypeError, ValueError):
                cs = ce = None
            if cs is not None and w0 - 1 <= cs < c["end"] and c["start"] - 15 <= cs <= c["start"] + 8:
                c["start"] = round(cs, 2)
            if ce is not None and c["start"] + 8 <= ce <= w1 + 1 and ce >= c["end"] - 6:
                c["end"] = round(ce, 2)
            if c["end"] - c["start"] > max_len * 1.3:   # too long: keep the play + reaction, trim the tail
                c["end"] = round(c["start"] + max_len * 1.3, 2)
            if r.get("on_screen"):
                c["on_screen"] = str(r["on_screen"])[:160]
            try:
                vs = float(r.get("visual_score"))
                c.setdefault("signals", {})["visual"] = int(vs)
                c["score"] = int(max(1, min(99, round(c["score"] + 0.15 * (vs - 50)))))
            except (TypeError, ValueError):
                pass
    if progress:
        progress(1.0)


def label_voices(a, t0, t1):
    """Mark words between t0 and t1 that aren't you (teammates, TTS) so they get their own colour."""
    try:
        from . import voice
        vp = os.path.join(a.cache, "voice_me.json")
        wav = getattr(a, "wav_path", None) or os.path.join(a.cache, "audio16k.wav")
        if not (os.path.isfile(vp) and os.path.isfile(wav)):
            return
        me = voice.learn_my_voice(wav, [], cache_path=vp)
        ws = [w for w in a.transcript.get("words", []) if t0 - 1 <= w["start"] <= t1 + 1 and "other" not in w]
        if ws:
            voice.label_words(wav, ws, me, voice._threshold(vp))
    except Exception as e:
        log.warning("Couldn't tell voices apart: %s", e)


def clip_webcam(a, c):
    if c.get("cam_box"):
        base = dict(a.webcam or {})
        base.update(found=True, layout="split", box=c["cam_box"], method="manual")
        return base
    return _clip_scene(a, c)


def _boxes_overlap(b1, b2):
    ax, ay, aw, ah = b1
    bx, by, bw, bh = b2
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / max(1e-6, aw * ah + bw * bh - inter)


def _clip_scene(a, c):
    """2.31: the layout is decided per CLIP, not once per stream: a stream can switch scenes (full webcam, gameplay
    with a webcam corner, a screen share with no webcam). Looks at ~12 frames inside the clip (cached per clip):
      - a big face (full webcam) in the clip -> full-webcam layout
      - no face at all in the clip -> no webcam panel (full gameplay)
      - otherwise -> the stream's own webcam box (measured from more frames), unless the webcam is somewhere else."""
    base = a.webcam
    try:
        st, en = float(c["start"]), float(c["end"])
        path = os.path.join(a.cache, f"scene2_{int(st * 10)}_{int(en * 10)}.json")   # 2.35: scene2 = overlay-trimmed box
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                per = json.load(f)
        else:
            from . import webcam as wc
            if a.remote:
                per = wc.detect_webcam(a.remote.meta["low_url"], a.remote.low_info(), None, count=12,
                                       t_range=(st + a.remote.off, en + a.remote.off))
            else:
                per = wc.detect_webcam(a.video, a.info, None, count=12, t_range=(st, en))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(per, f)
    except Exception as e:
        log.warning("Per-clip webcam check failed (using the stream's): %s", e)
        return base
    if per.get("layout") == "fullcam":
        if not (base and base.get("layout") == "fullcam"):
            log.info("This clip is full webcam (the stream's layout is %s)", (base or {}).get("layout"))
        return per
    if not per.get("found"):
        if per.get("presence", 1) == 0 and base and base.get("found"):
            log.info("No webcam in this clip: using the gameplay without a webcam panel")
            return dict(per, found=False, layout="gameplay")
        return base
    if base and base.get("found") and base.get("layout") == "split" and base.get("box") \
            and _boxes_overlap(base["box"], per["box"]) >= 0.35:
        if per.get("trimmed") and not base.get("trimmed"):
            return per   # 2.35: the stream's box is from before the overlay trim (could hold a sponsor panel)
        return base
    log.info("This clip's webcam is somewhere else than the stream's: %s", per.get("box"))
    return per


def clip_shots(a, c):
    """2.35.1: stream times of gunshots / sharp hits inside the clip (from the stream's 16 kHz audio)."""
    wav = getattr(a, "wav_path", None) or os.path.join(a.cache, "audio16k.wav")
    if not os.path.isfile(wav):
        return []
    try:
        from . import audio as au
        return au.shot_times(wav, c["start"], c["end"])
    except Exception as e:
        log.warning("Shot check skipped: %s", e)
        return []


def _localize(a, c, progress=None):
    """Returns (video, info, clip, words, audio_curve, silences) ready to render.
    For Twitch links this downloads just this clip's section in full quality."""
    words = a.transcript.get("words", [])
    if not a.remote:
        return a.video, a.info, c, words, a.audio_curve, a.silences
    path, off = a.remote.section(c["start"], c["end"], progress=progress)
    info = probe(path)
    sec_len = info["duration"]
    c2 = dict(c, start=round(c["start"] - off, 3), end=round(c["end"] - off, 3),
              peak=round(c.get("peak", c["start"]) - off, 3))
    if c.get("sfx_marks"):   # placed sounds are in stream time too
        c2["sfx_marks"] = [dict(m, at=round(m["at"] - off, 3)) for m in c["sfx_marks"]]
    if c.get("shots"):   # and gunshots
        c2["shots"] = [round(t - off, 3) for t in c["shots"]]
    if c.get("chat_pops"):   # and chat pop-ups
        c2["chat_pops"] = [dict(m, at=round(m["at"] - off, 3)) for m in c["chat_pops"]]
    w2 = [dict(w, start=w["start"] - off, end=w["end"] - off) for w in words
          if off - 1 <= w["start"] <= off + sec_len + 1]
    ac = None
    if a.audio_curve is not None:
        i = int(round(off / a.audio_curve["hop"]))
        ac = {"hop": a.audio_curve["hop"], "db": a.audio_curve["db"][i:], "z": a.audio_curve["z"][i:]}
    sil = [(s0 - off, s1 - off) for s0, s1 in a.silences if s1 > off and s0 < off + sec_len]
    return path, info, c2, w2, ac, sil


def preview(a, clip_id, edit, opts, out_dir):
    c = next(c for c in a.clips if c["id"] == clip_id)
    c = _apply_edit(c, edit)
    if opts.get("color_others", True):
        label_voices(a, c["start"], c["end"])
    video, info, c, words, ac, sil = _localize(a, c)
    return rd.render_preview(video, info, c, words, clip_webcam(a, c), opts, out_dir, silences=sil)


def hook_check(a, clips, opts):
    """Before a TikTok is first made, the AI checks the opening grabs attention. If the first
    seconds are dead air or filler it starts the clip a little later, on the first strong line.
    Never touches clips you edited, or clips whose start was moved back to show the play."""
    if not opts.get("hook_check", True) or not opts.get("api_key"):
        return 0
    words_all = a.transcript.get("speech_words") or a.transcript.get("words", [])
    items = []
    for c in clips:
        if c.get("hook_checked") or c.get("lead_up") or c["end"] - c["start"] < 14:
            continue
        ws = [w for w in words_all if c["start"] - 0.05 <= w["start"] < c["end"]]
        if len(ws) < 6:
            continue
        opening = [[round(w["start"] - c["start"], 1), w["text"]] for w in ws if w["start"] - c["start"] <= 9]
        items.append({"id": c["id"], "length_s": round(c["end"] - c["start"], 1), "title": c.get("hook") or c.get("title", ""),
                      "opening_words": opening, "rest": " ".join(w["text"] for w in ws if w["start"] - c["start"] > 9)[:500]})
    if not items:
        return 0
    from . import ai, taste
    res = ai.hook_check(items, opts["api_key"], model=opts.get("ai_model"),
                        slow_starts=taste.reason_bias()["counts"].get("slow_start", 0))
    moved = 0
    for c in clips:
        r = res.get(c["id"])
        if r is None:
            continue
        c["hook_checked"] = True
        shift = r.get("start_at", 0.0)
        if r.get("strong") or shift < 0.8 or shift > 7.0 or (c["end"] - c["start"] - shift) < 12:
            continue
        # land on the start of a word, with a short breath before it
        ws = [w for w in words_all if c["start"] + shift - 0.6 <= w["start"] <= c["start"] + shift + 0.6]
        if not ws:
            continue
        w = min(ws, key=lambda w: abs(w["start"] - (c["start"] + shift)))
        new_start = round(max(c["start"], w["start"] - 0.2), 2)
        if new_start - c["start"] >= 0.8:
            log.info("Hook check: '%s' now starts %.1fs later (%s)", c.get("title", c["id"]), new_start - c["start"], r.get("why", ""))
            c["hook_note"] = r.get("why", "")
            c["start"] = new_start
            c["len"] = round(c["end"] - c["start"], 1)
            c["start_label"] = fmt_time(c["start"])
            c["words"] = sc.words_in(a.transcript, c["start"], c["end"])
            moved += 1
    a.save()
    return moved


def linger_end(a, c, edit, opts):
    """2.32: let the clip run a moment after the last word (the chewing face, the stare, the laugh)
    instead of cutting the instant the talking stops. Skipped when you set the end yourself."""
    linger = float(opts.get("end_linger", 1.8) or 0)
    if linger <= 0 or (edit or {}).get("end") is not None or not a.transcript:
        return c
    words = a.transcript.get("speech_words") or a.transcript.get("words", [])
    last = [w for w in words if c["start"] <= w["start"] < c["end"]]
    if not last:
        return c
    target = last[-1]["end"] + linger
    nxt = [w["start"] for w in words if w["start"] >= c["end"]]
    if nxt:
        target = min(target, nxt[0] - 0.15)   # never into the next sentence
    target = min(target, float(a.info.get("duration") or target))
    # 2.35.1: always finish the last word, even when the next sentence follows straight on
    # ("eighth loss in a row" stopped 0.1 s before the end of "left.")
    target = max(target, last[-1]["end"] + 0.05)
    if target > c["end"] + 0.2 or (c["end"] < last[-1]["end"] < target):
        return dict(c, end=round(target, 2))
    return c


def render_all(a, clip_ids, edits, opts, out_dir, progress):
    """edits: {clip_id: {"text": "...", "start": s, "end": e, "hook": "..."}}"""
    chosen = [c for c in a.clips if c["id"] in clip_ids]
    try:
        hook_check(a, [c for c in chosen if not (edits or {}).get(c["id"])], opts)
    except Exception as e:
        log.warning("Hook check failed: %s", e)
    results = []
    n = max(1, len(chosen))
    for k, c in enumerate(chosen):
        c = _apply_edit(c, (edits or {}).get(c["id"], {}))
        c = linger_end(a, c, (edits or {}).get(c["id"], {}), opts)
        if opts.get("color_others", True):
            label_voices(a, c["start"], c["end"])
        if (opts.get("chat_popups") or "auto") != "off" and a.remote:
            try:   # 2.20: chat pop-ups when you talk to chat
                from . import chatpop
                c["chat_pops"] = chatpop.popups_for(c, a.transcript.get("words", []), a.remote.chat(), a.cache,
                                                    mode=opts.get("chat_popups") or "auto")
            except Exception as e:
                log.warning("Chat pop-ups skipped: %s", e)
        c["shots"] = clip_shots(a, c)   # 2.35.1: gunshots in quiet stretches are kept by the pause cuts
        if a.remote:
            progress(k / n, f"Downloading clip {k + 1} of {len(chosen)}")
            dl = lambda f, k=k: progress((k + 0.5 * f) / n, f"Downloading clip {k + 1} of {len(chosen)} ({int(f * 100)}%)")
        else:
            dl = None
        video, info, cl, words, ac, sil = _localize(a, c, dl)
        progress((k + (0.5 if a.remote else 0)) / n, f"Rendering clip {k + 1} of {len(chosen)}")
        r = rd.render_clip(video, info, cl, words, clip_webcam(a, c), opts, out_dir,
                           audio_curve=ac, silences=sil, index=k + 1)
        r.update({"id": c["id"], "title": c["title"], "caption": c.get("caption", "")})
        results.append(r)
    progress(1.0, f"Built {len(results)} TikToks")
    return results
