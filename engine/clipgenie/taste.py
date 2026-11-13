"""Learns what *you* think is a good clip: every 👍/👎 (and every TikTok you discard) is saved
with what was said, and the AI clipper gets the best recent examples as part of its brief."""
import json
import os
import threading
import time

from .util import data_dir

_LOCK = threading.Lock()
_KINDS = ("likes", "dislikes", "edits", "perf", "requests")   # everything that counts as feedback


def _path():
    return os.path.join(data_dir(), "taste.json")


def load():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"likes": [], "dislikes": []}


def _save(d):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _path())


# Why a clip got a thumbs-down (the quick buttons in the app)
REASONS = {
    "too_long": "too long",
    "slow_start": "slow start (first seconds don't grab)",
    "no_leadup": "missing the lead-up / the play that caused the reaction",
    "ends_early": "ends too early (cuts off the payoff)",
    "not_funny": "not funny / nothing happens",
    "wrong_moment": "wrong moment (the good bit is somewhere else)",
}


def vote(item_id, kind, text="", title="", why="", stream="", reasons=None):
    """kind: 'up', 'down' or None (undo). item_id is unique per stream moment.
    reasons: list of REASONS keys saying why a clip got a thumbs-down."""
    with _LOCK:
        d = load()
        prev = next((x for k in ("likes", "dislikes") for x in d.get(k, []) if x.get("id") == item_id), None)
        for k in ("likes", "dislikes"):
            d[k] = [x for x in d.get(k, []) if x.get("id") != item_id]
        if kind in ("up", "down"):
            rec = {"id": item_id, "text": (text or (prev or {}).get("text", ""))[:1500],
                   "title": title or (prev or {}).get("title", ""), "why": why or (prev or {}).get("why", ""),
                   "stream": stream, "at": time.time()}
            rs = [r for r in (reasons or []) if r in REASONS]
            if kind == "down" and rs:
                rec["reasons"] = rs
            d["likes" if kind == "up" else "dislikes"].append(rec)
            for k in ("likes", "dislikes"):
                d[k] = d[k][-60:]
        _save(d)
        return {"likes": len(d["likes"]), "dislikes": len(d["dislikes"])}


def reason_bias():
    """What your thumbs-down reasons say about how clips should be cut (last 20 thumbs-downs)."""
    from collections import Counter
    n = Counter(r for x in load().get("dislikes", [])[-20:] for r in x.get("reasons", []))
    out = {"len_scale": 1.0, "d_start": 0.0, "d_end": 0.0, "counts": dict(n)}
    if n["too_long"] >= 2:
        out["len_scale"] = 0.75 if n["too_long"] >= 4 else 0.85
    if n["ends_early"] >= 2:
        out["d_end"] = 3.0
    if n["no_leadup"] >= 2:
        out["d_start"] = -4.0
    elif n["slow_start"] >= 2:
        out["d_start"] = 1.5
    return out


# ---------------------------------------------------------------- how posted TikToks performed
def record_perf(item_id, title="", text="", views=None, likes=None, source="manual"):
    """Views a posted TikTok got (typed in, or synced from TikTok). views=None removes it."""
    with _LOCK:
        d = load()
        d["perf"] = [x for x in d.get("perf", []) if x.get("id") != item_id]
        if views is not None:
            d["perf"].append({"id": item_id, "title": title, "text": (text or "")[:1500], "views": int(views),
                              "likes": likes, "source": source, "at": time.time()})
            d["perf"] = d["perf"][-80:]
        _save(d)
        return len(d["perf"])


def perf_ranked():
    """(hits, flops, median): posted TikToks well above / well below your usual views."""
    import statistics
    perf = load().get("perf", [])
    if len(perf) < 3:
        return sorted(perf, key=lambda x: -x["views"]), [], None
    med = max(1.0, statistics.median(x["views"] for x in perf))
    hits = sorted([x for x in perf if x["views"] >= 1.5 * med], key=lambda x: -x["views"])
    flops = sorted([x for x in perf if x["views"] <= 0.5 * med], key=lambda x: x["views"])
    return hits, flops, med


def perf_block():
    hits, flops, med = perf_ranked()
    out = []
    if hits:
        out.append("REAL TIKTOK RESULTS - clips from this channel that got the MOST views"
                   + (f" (typical clip: {int(med)} views)" if med else "") + ". Find more like these:")
        out += [f'- {x["views"]} views: "{x.get("title", "")}": {x.get("text", "")[:350]}' for x in hits[:5]]
    if flops:
        out.append("Posted clips that FLOPPED (score similar moments lower):")
        out += [f'- {x["views"]} views: "{x.get("title", "")}": {x.get("text", "")[:200]}' for x in flops[:4]]
    return "\n".join(out)


# ---------------------------------------------------------------- style rules the AI writes itself
def feedback_count(d=None):
    d = d or load()
    return sum(len(d.get(k, [])) for k in _KINDS)


def note_request(text, title="", did=""):
    """A change you typed for one TikTok ("make it longer, add the bit where..."): kept so the AI
    learns what you usually ask for and does it from the start next time."""
    with _LOCK:
        d = load()
        d.setdefault("requests", []).append({"id": "req%d" % int(time.time() * 1000), "text": (text or "")[:400],
                                             "title": title, "did": (did or "")[:300], "at": time.time()})
        d["requests"] = d["requests"][-30:]
        _save(d)


def forget_last_request():
    with _LOCK:
        d = load()
        if d.get("requests"):
            d["requests"].pop()
            _save(d)


def requests_block(n=8):
    rq = load().get("requests", [])[-n:]
    if not rq:
        return ""
    return "\n".join(["CHANGES THE STREAMER ASKED FOR on clips you made (do these from the start next time):"] +
                     [f'- on "{x.get("title", "")}": "{x.get("text", "")[:200]}"' for x in rq])


def feedback_stamp(d=None):
    """Changes whenever any feedback is added or changed (the lists are capped, so don't use the count)."""
    d = d or load()
    return round(sum(x.get("at", 0) for k in _KINDS for x in d.get(k, [])), 1)


def latest_feedback(d=None):
    d = d or load()
    return max([x.get("at", 0) for k in _KINDS for x in d.get(k, [])] + [0])


def rules():
    return load().get("rules") or {}


def rules_due(every=10):
    d = load()
    r = d.get("rules") or {}
    seen = set(r.get("seen", []))
    now = {x.get("id", "") + ":" + str(int(x.get("at", 0))) for k in _KINDS for x in d.get(k, [])}
    return len(now - seen) >= every or (not r and len(now) >= 5)


RULES_SYSTEM = """You study one Twitch streamer's feedback on the TikTok clips an AI clipper made for them
and write the clipper's standing instructions. You get: moments they rated up or down (with their reasons),
how they re-edited clips, and real TikTok view counts. Find the PATTERNS: what kinds of moments they want,
what they reject, how long clips should be, how clips should start and end. Be specific to this streamer and
grounded in the evidence (quote topics/situations, not generic advice). Real view counts outweigh thumbs.
Return ONLY JSON: {"rules": ["8-12 short imperative rules, most important first"],
"summary": "one plain sentence, addressed to the streamer as 'you', saying what you learned"}"""


def _digest(d):
    out = []
    likes = _seed() + d.get("likes", [])
    if likes:
        out.append("THUMBS UP:")
        out += [f'- "{x.get("title", "")}": {x.get("text", "")[:260]}' for x in likes[-25:]]
    if d.get("dislikes"):
        out.append("THUMBS DOWN:")
        for x in d["dislikes"][-25:]:
            why = ", ".join(REASONS[r] for r in x.get("reasons", []) if r in REASONS)
            out.append(f'- "{x.get("title", "")}"' + (f" [reason: {why}]" if why else "") + f': {x.get("text", "")[:200]}')
    pb = perf_block()
    if pb:
        out.append(pb)
    eb = edits_block()
    if eb:
        out.append(eb)
    rq = requests_block(20)
    if rq:
        out.append(rq)
    return "\n".join(out)


def update_rules(api_key, model=None):
    """Ask the AI to turn all your feedback into its own standing rules. Returns the rules dict."""
    from . import ai
    d = load()
    if not api_key or feedback_count(d) < 3:
        return d.get("rules") or {}
    r = ai._call(api_key, model, RULES_SYSTEM, _digest(d), max_tokens=1200)
    lines = [str(x).strip()[:240] for x in (r.get("rules") or []) if str(x).strip()][:12]
    if not lines:
        return d.get("rules") or {}
    with _LOCK:
        d = load()
        d["rules"] = {"rules": lines, "summary": str(r.get("summary", ""))[:300], "at": time.time(),
                      "n": feedback_count(d),
                      "seen": [x.get("id", "") + ":" + str(int(x.get("at", 0)))
                               for k in _KINDS for x in d.get(k, [])]}
        _save(d)
        return d["rules"]


_rules_busy = threading.Lock()


def maybe_update_rules(api_key, model=None, every=10):
    """Called after feedback: refresh the rules in the background once enough new feedback came in."""
    if not api_key or not rules_due(every):
        return False

    def run():
        if not _rules_busy.acquire(False):
            return
        try:
            update_rules(api_key, model)
        except Exception as e:
            from .util import log
            log.warning("Couldn't update the AI's style rules: %s", e)
        finally:
            _rules_busy.release()
    threading.Thread(target=run, daemon=True).start()
    return True


# Moments the creator told us he liked before the vote buttons existed
SEED_LIKES = [
    {"title": "Teammate's 'catacombs' accent", "text": "he's a ticket box, ticket box. Catacombs, what? What you say catacombs? "
     "What is this guy saying bro? This motherfucker talking in hieroglyphs. Didn't I ban you for being stupid at one point?",
     "why": "teammate saying nonsense on comms and him losing it"},
    {"title": "Teammates calling him the N-word", "text": "(friends on voice chat slur-bantering him and his reaction)",
     "why": "he finds friends' edgy banter at him funny"},
]


def _seed():
    """the creator's own starting likes: only in his copy. Public builds learn only from each user's own thumbs."""
    try:
        from . import licence
        if licence.enforced():
            return []
    except Exception:
        pass
    return SEED_LIKES


def _examples(likes, n_full=4):
    """Your best-rated moments, most useful first: real TikTok hits, then the newest thumbs-up."""
    hits = {x["id"]: x["views"] for x in perf_ranked()[0]}
    order = sorted(likes, key=lambda x: (-(hits.get(x.get("id"), 0)), -x.get("at", 0)))
    out = []
    for i, x in enumerate(order):
        n = 900 if i < n_full else 280     # full transcript for the top few, so the AI can match their feel
        v = f' ({hits[x["id"]]} views on TikTok)' if x.get("id") in hits else ""
        out.append(f'- "{x.get("title", "")}"{v}: {x.get("text", "")[:n]}')
    return out


def prompt_block(max_likes=10, max_dislikes=6):
    d = load()
    likes = (_seed() + d.get("likes", []))[-max_likes:]
    dislikes = d.get("dislikes", [])[-max_dislikes:]
    out = ["\nTHE STREAMER'S OWN TASTE (learned from their ratings, edits and real TikTok results - weigh this heavily):"]
    r = d.get("rules") or {}
    if r.get("rules"):
        out.append("RULES learned from all their feedback (follow these):")
        out += [f"- {x}" for x in r["rules"]]
    if likes:
        out.append("EXAMPLES of moments they LIKED - match the feel of these (who is talking, the energy, how it starts and ends):")
        out += _examples(likes)
    if dislikes:
        out.append("Moments they did NOT like (score similar moments low):")
        for x in dislikes:
            why = ", ".join(REASONS[k] for k in x.get("reasons", []) if k in REASONS)
            out.append(f'- "{x.get("title", "")}"' + (f" [why: {why}]" if why else "") + f': {x.get("text", "")[:200]}')
    rb = reason_bias()
    if rb["counts"]:
        top = ", ".join(f"{REASONS[k]} x{v}" for k, v in sorted(rb["counts"].items(), key=lambda kv: -kv[1]) if k in REASONS)
        out.append(f"Their most common complaints about your clips: {top}. Fix these in every pick.")
    pb = perf_block()
    if pb:
        out.append(pb)
    eb = edits_block()
    if eb:
        out.append(eb)
    rq = requests_block()
    if rq:
        out.append(rq)
    return "\n".join(out)


# ---------------------------------------------------------------- learning from your edits
import difflib
import re as _re

_TOK = _re.compile(r"[^\w']+")


def _norm(t):
    return _TOK.sub("", (t or "").lower())


def _pairs(old_words, new_words):
    """Word swaps between the transcript and your edited subtitles ("diema" -> "Dima")."""
    a = [_norm(w) for w in old_words]
    b = [w for w in new_words]
    bn = [_norm(w) for w in b]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, bn, autojunk=False).get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1 and i2 - i1 <= 3:
            for k in range(i2 - i1):
                wrong, right = a[i1 + k], _TOK.sub("", b[j1 + k]).strip()
                if wrong and right and wrong != right.lower() and len(wrong) > 1:
                    out.append((wrong, right))
    return out


def learn_edit(stream_key, cand, edits, words):
    """Remember how you changed one of Clip Genie's picks: trims/extensions and subtitle fixes."""
    if not cand or not edits:
        return
    with _LOCK:
        d = load()
        o0, o1 = float(cand["start"]), float(cand["end"])
        n0, n1 = float(edits.get("start", o0)), float(edits.get("end", o1))
        rec = {"id": f"{stream_key}@{int(round(o0))}", "title": cand.get("hook") or cand.get("title", ""),
               "d_start": round(n0 - o0, 2), "d_end": round(n1 - o1, 2), "at": time.time()}
        if n0 < o0 - 0.5:
            rec["added_before"] = " ".join(w["text"] for w in words if n0 <= w["start"] < o0)[:200]
        if n1 < o1 - 0.5:
            rec["cut_ending"] = " ".join(w["text"] for w in words if n1 <= w["start"] < o1)[:200]
        if n1 > o1 + 0.5:
            rec["added_after"] = " ".join(w["text"] for w in words if o1 <= w["start"] < n1)[:200]
        segs = edits.get("segs") or []
        if len(segs) > 1:
            rec["cut_middle_s"] = round(sum(b[0] - a[1] for a, b in zip(segs, segs[1:]) if b[0] > a[1]), 1)
        if abs(rec["d_start"]) > 0.5 or abs(rec["d_end"]) > 0.5 or rec.get("cut_middle_s"):
            d["edits"] = [x for x in d.get("edits", []) if x.get("id") != rec["id"]] + [rec]
            d["edits"] = d["edits"][-60:]
        # subtitle fixes
        fixes = []
        if edits.get("text"):
            orig = [w["text"] for w in words if o0 - 0.05 <= w["start"] <= o1 + 0.05]
            fixes += _pairs(orig, edits["text"].split())
        for c in edits.get("captions") or []:
            s0, s1 = n0 + float(c["s"]), n0 + float(c["e"])
            orig = [w["text"] for w in words if s0 - 0.05 <= (w["start"] + w["end"]) / 2 <= s1 + 0.05]
            if orig:
                fixes += _pairs(orig, str(c.get("text", "")).split())
        if fixes:
            corr = d.setdefault("corrections", {})
            for wrong, right in fixes:
                e = corr.setdefault(wrong, {"to": right, "n": 0})
                if e["to"] != right:
                    e.update(to=right, n=0)
                e["n"] += 1
        _save(d)


def edit_bias(min_edits=3, clamp=8.0):
    """How you usually move Clip Genie's start/end (median of your recent edits), or (0, 0)."""
    ed = [x for x in load().get("edits", [])[-20:]]
    if len(ed) < min_edits:
        return 0.0, 0.0
    import statistics
    ds = statistics.median(x["d_start"] for x in ed)
    de = statistics.median(x["d_end"] for x in ed)
    f = lambda v: 0.0 if abs(v) < 1.0 else max(-clamp, min(clamp, v))
    return f(ds), f(de)


def corrections(min_n=1):
    return {k: v["to"] for k, v in load().get("corrections", {}).items() if v.get("n", 0) >= min_n}


def fix_words(words):
    """Apply your subtitle fixes to new subtitles (same mistake won't come back)."""
    corr = corrections()
    if not corr:
        return words
    out = []
    for w in words:
        key = _norm(w["text"])
        if key in corr:
            m = _re.match(r"^(\W*)(.*?)(\W*)$", w["text"])
            w = dict(w, text=(m.group(1) if m else "") + corr[key] + (m.group(3) if m else ""))
        out.append(w)
    return out


def names_prompt():
    """Names you've corrected, to help the speech-to-text spell them right next time."""
    names = sorted({v for v in corrections().values() if v[:1].isupper()})
    return ("Names: " + ", ".join(names[:30]) + ".") if names else None


def edits_block():
    d = load()
    ed = d.get("edits", [])[-8:]
    out = []
    ds, de = edit_bias()
    if ds or de:
        out.append(f"When the streamer edits your picks they usually move the start by {ds:+.0f}s and the end by {de:+.0f}s "
                   "(negative = earlier). Choose start/end that way from the beginning.")
    for x in ed:
        bits = []
        if x.get("added_before"):
            bits.append(f'added the lead-up "{x["added_before"][:120]}"')
        if x.get("cut_ending"):
            bits.append(f'cut the ending "{x["cut_ending"][:100]}"')
        if x.get("added_after"):
            bits.append(f'kept going to "{x["added_after"][:100]}"')
        if x.get("cut_middle_s"):
            bits.append(f'cut {x["cut_middle_s"]}s out of the middle')
        if bits:
            out.append(f'- "{x.get("title", "")}": ' + "; ".join(bits))
    if out:
        out.insert(0, "HOW THE STREAMER EDITS YOUR CLIPS (learn from it):")
    return "\n".join(out)
