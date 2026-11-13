"""The edit director: turns a raw clip window into an edit plan.

Plan (all times in seconds):
  teaser      (a, b) source-relative window shown first as a cold-open hook, or None
  segs        [(a, b)] source-relative pieces kept after jump cuts
  cam_zooms   [(t0, t1, strength)] output-time cut-in scale-ups on the WEBCAM panel (reactions)
  game_zooms  [(t0, t1, strength)] output-time cut-in scale-ups on the GAMEPLAY panel (action)
  cutaways    [(t0, t1)] output-time windows where the webcam goes full screen
  sfx         [(t, kind)] output-time sound effects ("whoosh", "boom")
"""
import re
import numpy as np

from .scoring import SPOKEN

LAUGH = re.compile(r"^(ha(ha)+|he(he)+|lmao+|lol+|bruh+)[.!?,]*$", re.I)


def _norm(t):
    return re.sub(r"[^\w']", "", t.lower())


def jump_cut_segments(words, clip_len, audio_z=None, hop=0.5, min_gap=0.4, pad=0.15,
                      keep_action=3.5, min_keep=12.0, linger=0.5, max_frac=0.5, shots=None):
    # 2.23: tighter (was gap 0.5 / pad 0.25 / action 2.6): loud CS2 audio kept too many pauses
    """Cut the dead air between sentences (speech-based, so loud game audio no longer
    blocks every cut), but keep gaps where something loud happens in the game."""
    ws = sorted((w for w in words if 0 <= w["start"] < clip_len), key=lambda w: w["start"])
    if len(ws) < 2:
        return [(0.0, round(clip_len, 3))]
    cuts = []
    # dead air before the first word (keep a little lead-in)
    if ws[0]["start"] > 1.8 and not _loud(audio_z, hop, 0, ws[0]["start"] - 0.7, keep_action):
        cuts += _around_shots(0.0, ws[0]["start"] - 0.6, shots, min_gap)
    for a, b in zip(ws, ws[1:]):
        g0, g1 = a["end"] + pad, b["start"] - pad
        if g1 - g0 < min_gap - 2 * pad:
            continue
        if _loud(audio_z, hop, g0, g1, keep_action):
            continue  # game action in the pause: keep it
        if linger > 0.5 and g0 >= clip_len - 4.5 and g1 - g0 < 3.0:
            continue  # 2.32: the ending is the reaction (chewing, the stare): let it breathe
        cuts += _around_shots(g0, g1, shots, min_gap)
    # dead air after the last word
    tail = ws[-1]["end"] + max(0.5, linger)   # 2.32: the end runs a moment after the last word
    if clip_len - tail > 1.2 and not _loud(audio_z, hop, tail, clip_len, keep_action):
        cuts += _around_shots(tail, clip_len, shots, min_gap)
    # 2.35.1: never cut most of the clip away. A long quiet stretch is usually the play itself (a silent
    # ace: "5 bullet deag ace" lost 23 of 34 s, all the kills). Keep the longest pauses until at most
    # max_frac of the clip is cut.
    cuts = sorted(cuts, key=lambda c: c[1] - c[0])
    while cuts and sum(b - a for a, b in cuts) > max_frac * clip_len:
        cuts.pop()
    segs, cur = [], 0.0
    for a, b in sorted(cuts):
        if a - cur > 0.25:
            segs.append((cur, a))
        cur = max(cur, b)
    if clip_len - cur > 0.25:
        segs.append((cur, clip_len))
    segs = _calm_cuts(segs)
    if not segs:
        return [(0.0, round(clip_len, 3))]
    kept = sum(b - a for a, b in segs)
    if kept < min_keep:  # cut too hard: only remove the longest pauses
        return jump_cut_segments(words, clip_len, audio_z, hop, min_gap + 0.5, pad, keep_action, 0, linger, max_frac, shots)
    return [(round(a, 3), round(b, 3)) for a, b in segs]



def _calm_cuts(segs, min_cut=0.45, min_piece=1.2, max_join=1.0):
    """2.35.3: no jumpy run of cuts (the creator on VAC LIVED: "too many cuts back to back"). A cut that removes under
    min_cut s is dropped (saves nothing, just jolts), and a kept bit shorter than min_piece s between cuts is joined
    to its neighbour across the smaller of its two pauses ("No!" 0.7 s between two cuts), only when that pause is
    short (<= max_join s): longer dead air is always cut (the creator asked for more cuts in 2.32)."""
    segs = [list(s) for s in segs]
    i = 0
    while i < len(segs) - 1:
        if segs[i + 1][0] - segs[i][1] < min_cut:
            segs[i][1] = segs[i + 1][1]
            del segs[i + 1]
        else:
            i += 1
    while len(segs) > 1:
        best = None
        for i, (a, b) in enumerate(segs):
            if b - a >= min_piece:
                continue
            gaps = []
            if i > 0:
                gaps.append((segs[i][0] - segs[i - 1][1], i - 1))
            if i < len(segs) - 1:
                gaps.append((segs[i + 1][0] - segs[i][1], i))
            g = min(gaps)
            if g[0] <= max_join and (best is None or b - a < best[0]):
                best = (b - a, g[1])
        if best is None:
            break
        j = best[1]                           # join across the smaller pause
        segs[j][1] = segs[j + 1][1]
        del segs[j + 1]
    return [tuple(s) for s in segs]


def _around_shots(g0, g1, shots, min_gap, before=1.2, after=0.8, group=2.5, long_gap=3.0):
    """2.35.1: a pause with gunshots in it is the play. Keep each burst of shots (from just before the
    first shot, so the aim is seen, to just after the last) and cut only the dead bits around it."""
    inside = [t for t in (shots or []) if g0 <= t <= g1]
    if not inside or g1 - g0 < long_gap:   # short pauses: cut as before (he wants the tight cuts)
        return [(g0, g1)]
    keep, cur = [], [inside[0], inside[0]]
    for t in inside[1:]:
        if t - cur[1] <= group:
            cur[1] = t
        else:
            keep.append(cur)
            cur = [t, t]
    keep.append(cur)
    out, pos = [], g0
    for a, b in keep:
        if a - before - pos >= min_gap:
            out.append((pos, a - before))
        pos = max(pos, b + after)
    if g1 - pos >= min_gap:
        out.append((pos, g1))
    return out


FILLER = {"um", "umm", "uh", "uhh", "erm", "hmm", "mm", "like", "so", "yeah", "yea", "okay", "ok", "anyway",
          "well", "right", "alright", "you", "know", "i", "mean", "and", "but", "just", "kinda", "basically"}
_STOP = {"the", "and", "for", "you", "your", "this", "that", "with", "what", "from", "have", "just", "they",
         "them", "then", "there", "their", "into", "after", "about", "over", "when", "were", "was", "are", "his",
         "her", "him", "its", "it's", "one", "all", "out", "how", "why", "who", "did", "not", "bro"}


def dull_cuts(words, clip_len, audio_z=None, hop=0.5, peak=None, title="", max_frac=0.25, min_keep=12.0,
              shots=None):
    """2.32: skip the boring bits in the lead-up, like the reference edits (dima / Shorts cut the gameplay
    12-20 times a minute by dropping stretches where nothing happens). Only drops whole phrases that are
    filler ("um, so, yeah") or quiet talk with no reaction, no loud game moment and no word from the title,
    never the first phrase, never two in a row, never anything from ~5 s before the peak to the end."""
    ws = sorted((w for w in words if 0 <= w["start"] < clip_len), key=lambda w: w["start"])
    if len(ws) < 6:
        return []
    phrases, cur = [], [ws[0]]
    for a, b in zip(ws, ws[1:]):
        if b["start"] - a["end"] >= 0.35 or a["text"].rstrip()[-1:] in ".?!":
            phrases.append(cur)
            cur = []
        cur.append(b)
    phrases.append(cur)
    if len(phrases) < 3:
        return []
    peak = clip_len * 0.65 if peak is None else peak
    protect = max(0.0, peak - 5.0)
    cands = []
    for i, ph in enumerate(phrases):
        if i == 0 or i == len(phrases) - 1:
            continue
        s, e = ph[0]["start"], ph[-1]["end"]
        if e > protect:
            break
        toks = [_norm(w["text"]) for w in ph]
        filler = all(t in FILLER or not t for t in toks)
        # Tested on his 16 TikToks: a "quiet talk = boring" rule dropped the actual joke lines
        # ("I think we should hang out sometime"), so only pure filler phrases are dropped.
        if not filler or _loud(audio_z, hop, s, e, 1.8) or any(s - 1 <= t <= e + 1 for t in shots or []):
            continue
        prev_end = phrases[i - 1][-1]["end"]
        next_start = phrases[i + 1][0]["start"]
        c0, c1 = max(prev_end + 0.1, s - 0.15), min(next_start - 0.1, e + 0.15)
        if c1 - c0 >= 0.5:
            cands.append((0 if filler else 1, i, round(c0, 3), round(c1, 3)))
    out, used, dropped = [], set(), 0.0
    budget = min(clip_len * max_frac, max(0.0, clip_len - min_keep))
    for _, i, c0, c1 in sorted(cands, key=lambda c: (c[0], -(c[3] - c[2]))):
        if i - 1 in used or i + 1 in used or dropped + (c1 - c0) > budget:
            continue
        used.add(i)
        dropped += c1 - c0
        out.append((c0, c1))
    return sorted(out)


def _subtract(segs, cuts):
    out = []
    for a, b in segs:
        pieces = [(a, b)]
        for c0, c1 in cuts:
            nxt = []
            for x, y in pieces:
                if c1 <= x or c0 >= y:
                    nxt.append((x, y))
                    continue
                if c0 - x > 0.25:
                    nxt.append((x, c0))
                if y - c1 > 0.25:
                    nxt.append((c1, y))
            pieces = nxt
        out += pieces
    return [(round(a, 3), round(b, 3)) for a, b in out]


def _loud(audio_z, hop, t0, t1, thr):
    if audio_z is None or t1 <= t0:
        return False
    i0, i1 = max(0, int(t0 / hop)), int(np.ceil(t1 / hop))
    seg = audio_z[i0:i1]
    return len(seg) > 0 and float(np.max(seg)) >= thr


def map_time(t, segs, offset=0.0):
    off = offset
    for a, b in segs:
        if a <= t < b:
            return off + (t - a)
        off += b - a
    return None


def find_events(words, clip_len, audio_z=None, hop=0.5):
    """Reactions (streamer) vs game action, source-relative."""
    reactions, actions = [], []
    ws = [w for w in words if 0 <= w["start"] < clip_len]
    for i, w in enumerate(ws):
        chunk = " ".join(x["text"] for x in ws[i:i + 4])
        strength = 0.0
        for rx, wgt in SPOKEN:
            if rx.match(chunk):
                strength = max(strength, wgt)
        if LAUGH.match(w["text"]):
            strength = max(strength, 1.6)
        if w["text"].isupper() and len(_norm(w["text"])) > 2:
            strength = max(strength, 1.2)
        if strength > 0:
            reactions.append((w["start"], strength))
    if audio_z is not None:
        z = np.asarray(audio_z[: int(clip_len / hop) + 1])
        for i in range(1, len(z) - 1):
            if z[i] >= 2.2 and z[i] >= z[i - 1] and z[i] >= z[i + 1]:
                t = i * hop
                talking = any(w["start"] - 0.5 <= t <= w["end"] + 0.5 for w in ws)
                s = float(min(3.0, z[i] / 2))
                if talking:
                    reactions.append((t, s + 0.5))  # shouting = reaction
                else:
                    actions.append((t, s))
    return _thin(reactions, 1.8), _thin(actions, 1.8)


def _merge(windows, max_len=3.0):
    """Overlapping punch-ins become one (max 3 s). A punch-in that would start right
    as another ends is dropped, so every scale-up is followed by a visible cut back out."""
    out = []
    for a, b, st in sorted(windows):
        if out:
            pa, pb, ps = out[-1]
            if a < pb - 0.05:
                out[-1] = (pa, min(max(pb, b), pa + max_len), max(ps, st))
                continue
            if a - pb < 0.6:
                continue
        out.append((a, min(b, a + max_len), st))
    return out


def _thin(events, min_sep):
    out = []
    for t, s in sorted(events, key=lambda e: -e[1]):
        if all(abs(t - u) >= min_sep for u, _ in out):
            out.append((t, s))
    return sorted(out)


def plan_edit(clip, words_rel, audio_z=None, hop=0.5, opts=None, has_cam=True):
    """words_rel / audio_z are relative to the clip start."""
    opts = opts or {}
    clip_len = clip["end"] - clip["start"]
    seg_gain = None
    if clip.get("segs_override"):
        segs, seg_gain = [], []
        gains = list(clip.get("seg_gain") or [])
        order = sorted(range(len(clip["segs_override"])), key=lambda i: clip["segs_override"][i][0])
        for i in order:
            a, b = clip["segs_override"][i][:2]
            a, b = max(0.0, a), min(clip_len, b)
            if b - a >= 0.2:
                segs.append((round(a, 3), round(b, 3)))
                seg_gain.append(gains[i] if i < len(gains) else 0)
        if not segs:
            segs, seg_gain = [(0.0, round(clip_len, 3))], [0]
    elif opts.get("jump_cuts", True):
        shots = [t - clip["start"] for t in clip.get("shots") or []]   # 2.35.1: stream time -> clip time
        segs = jump_cut_segments(words_rel, clip_len, audio_z, hop, min_gap=float(opts.get("jump_min_gap", 0.6)),
                                 linger=float(opts.get("end_linger", 1.8) or 0), shots=shots)
        if opts.get("cut_boring", True):
            peak = clip.get("peak", clip["start"] + clip_len * 0.65) - clip["start"]
            drop = dull_cuts(words_rel, clip_len, audio_z, hop, peak, clip.get("title") or "", shots=shots)
            if drop:
                segs = _subtract(segs, drop) or segs
    else:
        segs = [(0.0, round(clip_len, 3))]

    # cold-open: show the peak first, then rewind to the setup
    teaser = None
    peak = clip.get("peak", clip["start"] + clip_len * 0.65) - clip["start"]
    if opts.get("hook_teaser", True) and clip_len >= 14 and peak > 5:
        a, b = max(0.0, peak - 1.0), min(clip_len, peak + 1.4)
        teaser = (round(a, 3), round(b, 3))
    t_off = (teaser[1] - teaser[0]) if teaser else 0.0
    out_dur = t_off + sum(b - a for a, b in segs)

    reactions, actions = find_events(words_rel, clip_len, audio_z, hop)
    cam_z, game_z, sfx, cutaways = [], [], [], []

    def out_t(t):
        return map_time(t, segs, t_off)

    # cut points in the output (where jump cuts happen) - punch-ins land on these
    bounds, acc = [t_off], t_off
    for a, b in segs:
        acc += b - a
        bounds.append(round(acc, 3))
    if teaser:
        bounds = [0.0] + bounds

    def window(t):
        """Instant scale-up starting at the nearest cut (or the moment itself),
        held until the next cut, then an instant cut back out."""
        before = [x for x in bounds if t - 0.8 <= x <= t + 0.1]
        t0 = before[-1] if before else t - 0.1
        after = [x for x in bounds if x > t0 + 0.9]
        t1 = min(after[0] if after else out_dur, t0 + 2.6)
        if t1 - t0 < 0.9:
            t1 = min(out_dur, t0 + 1.4)
        return round(max(0.0, t0), 2), round(t1, 2)

    if opts.get("zoom", True):
        if teaser:
            game_z.append((0.0, round(t_off, 2), 1.4))   # cold open starts punched in
        for t, s in reactions:
            o = out_t(t)
            if o is not None:
                a, b = window(o)
                (cam_z if has_cam else game_z).append((a, b, s))
        for t, s in actions:
            o = out_t(t)
            if o is not None:
                a, b = window(o)
                game_z.append((a, b, s))
        # pacing: alternate scale on jump cuts so something changes every few seconds
        busy = sorted(cam_z + game_z)
        alt = 0
        for i in range(len(bounds) - 1):
            a, b = bounds[i], bounds[i + 1]
            if b - a < 1.0 or a < t_off:
                continue
            if any(not (z[1] <= a or z[0] >= b) for z in busy):
                continue
            alt += 1
            if alt % 2 == 0:
                target = game_z if (alt % 4 == 0 or not has_cam) else cam_z
                target.append((round(a, 2), round(min(b, a + 3.0), 2), 0.8))
        # editor rhythm: keep the gameplay running and keep switching the webcam
        # between normal and punched-in every ~2 s (what your editor did)
        every = float(opts.get("cam_punch_every", 0))
        if every and has_cam:
            t = t_off + 0.8
            while t < out_dur - 1.0:
                if not any(z[0] - 0.6 <= t <= z[1] + 0.6 for z in cam_z):
                    cam_z.append((round(t, 2), round(min(out_dur, t + every * 0.55), 2), 0.9))
                t += every
        if opts.get("punch_more"):
            # 2.24: keep the gameplay moving like dima's edits: a quick gameplay punch-in every ~4 s
            # wherever nothing else is happening (gameplay only: no webcam zooms)
            busy = sorted(cam_z + game_z)
            t = t_off + 1.5
            while t < out_dur - 1.5:
                if not any(z[0] - 0.8 <= t <= z[1] + 0.8 for z in busy):
                    game_z.append((round(t, 2), round(min(out_dur, t + 1.6), 2), 0.9))
                    t += 4.0
                else:
                    t += 0.5
        cam_z = _merge(cam_z)
        game_z = _merge(game_z)
        if not opts.get("cam_zooms", True):
            # 2.9: no small webcam zooms. Reactions that wanted a punch-in still get the
            # big full-screen webcam cutaway below; nothing moves to the gameplay panel.
            cam_z = []

    # reaction cutaway: the strongest reaction goes full-screen webcam
    if has_cam and opts.get("cutaways", True) and reactions:
        best = sorted(reactions, key=lambda e: -e[1])
        n_cut = 2 if out_dur > 28 else 1
        for t, s in best:
            if len(cutaways) >= n_cut or s < 1.4:
                break
            o = out_t(t)
            if o is None or o < t_off + 1.0 or o > out_dur - 1.5:
                continue
            c = (round(o - 0.15, 2), round(min(out_dur, o + 1.5), 2))
            if all(c[0] > d[1] + 3 or c[1] < d[0] - 3 for d in cutaways):
                cutaways.append(c)
                sfx.append((c[0], "boom"))
        cutaways.sort()
        cam_z = [z for z in cam_z if not any(not (z[1] <= a or z[0] >= b) for a, b in cutaways)]
    if teaser and opts.get("zoom_whoosh", False):
        sfx.append((max(0.0, t_off - 0.18), "whoosh"))  # rewind into the setup

    if opts.get("sfx", True) and opts.get("zoom_whoosh", False):
        need = float(opts.get("sfx_min_strength", 1.2))
        for a, b, st in game_z + cam_z:
            if st >= need and all(abs(a - u) > 1.0 for u, _ in sfx):
                sfx.append((max(0.0, a - 0.05), "whoosh"))
    sfx.sort()
    if not opts.get("sfx", True):
        sfx = []
    return {"teaser": teaser, "segs": segs, "seg_gain": seg_gain, "t_off": round(t_off, 3), "out_dur": round(out_dur, 3),
            "cam_zooms": sorted(cam_z), "game_zooms": sorted(game_z),
            "cutaways": cutaways, "sfx": sfx[:int(opts.get("max_sfx", 8))]}
