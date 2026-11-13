"""Optional AI pass (Claude API): re-ranks candidates like a TikTok editor and
writes the hook, title and post caption. Works without it, using heuristics."""
import json
import re
import urllib.error
import urllib.request

from .util import log

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
# Public builds: the AI runs through the Klipon server (OpenRouter there; the key never leaves the server).
# library.style_opts puts this in place of an API key; _send routes it.
SERVER_KEY = "clipgenie-server"
SERVER_MODEL = "server"


def _pub(system):
    """Public builds: the prompts were written for the creator's channel. Drop his name and his channel's stats so
    the AI doesn't think every streamer is him."""
    s = str(system or "")
    s = s.replace("the Twitch streamer xHotGamerDude (TikTok @hotgamerdude_)", "a Twitch streamer")
    s = s.replace("the Twitch streamer xHotGamerDude", "a Twitch streamer")
    s = re.sub(r"What works for THIS channel \(@hotgamerdude.*?\n\n", "", s, flags=re.S)
    s = s.replace("(from its 102 posted reels and its most-viewed viewer clips)", "")
    s = s.replace(" (Axo, Dima, Ohne...)", "")
    return s


def _send(api_key, body, timeout=120):
    """One AI request (Claude message format) -> the Claude-style answer dict."""
    if api_key == SERVER_KEY:
        from . import licence
        return licence.ai(dict(body, system=_pub(body.get("system"))), timeout=timeout + 30)
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

GUIDE = """You are a top TikTok editor for gaming/Twitch streamers. You pick which
stream moments will perform as short-form clips and package them.

What performs (use this to judge):
- The first 2-3 seconds must hook: the clip opens on action, a reaction, or a clear setup.
- Viewers must want to watch to the end (length: see the channel notes below).
- Strong payoffs: funny lines and banter (most-clipped on this channel), teammates or TTS roasting
  the streamer, clutch plays, fails, jumpscares, unexpected moments. Weak: calm talking, menus, loading screens, reading donations.
- Something must happen every 3-5 seconds.

What works for THIS channel (@hotgamerdude, learned from 102 posted reels and their views):
- Best by far (~13k views vs ~700 typical): chaos and panic in party/co-op games: getting chased,
  being attacked, screaming, everything going wrong at once, friends turning on each other.
- Funny drama/banter with friends and teammates (betrayal, roasting, outrageous lines): can go big.
- Clean skill plays with a clear payoff (ace, deagle flicks, clutch at 2 hp): solid.
- Generic rage, complaining about the game or slow talking: performs worst, score it low.
- Shorter is better: 15-25 seconds is the sweet spot; trim setup that isn't needed.
Titles on this channel are short and punchy, e.g. "Getting chased", "Betrayal of the year",
"Seagulls are insane", "Deagle master!", "Clean ace!", "What is that sensitivity bruv?".

Big-streamer titles that work (top Twitch clips): a quote of the line, the reaction or the situation,
1-5 words: "caught", "WHAT IS THAT", "VAC LIVE", "2nd knife in cs2", "bro called me the N word after this".

What ohnePixel and dima_wallhacks actually POST as shorts (studied Nov 2026: 39 ohnePixel and 48 dima
shorts on YouTube, with view counts, lengths and frames). These are their own editors' picks, so weigh it:
- Length: ohnePixel's are 9-28 s most of the time (median 23 s; several of his biggest are 10-16 s).
  dima's are 10-44 s (median 29 s). Nothing rambles: one moment, one payoff, out. Prefer 12-30 s.
- What their biggest are about: something NEW or never-seen ("New Gun in CS2", "Valve JUST added this",
  a strange server/mod/game mode), an "incident" (something goes wrong in a funny way), a callback to a
  known moment ("the incident repeats"), another known person in the clip (pros, big streamers, friends),
  absurd skill or movement, nostalgia ("Old CS2 was incredible"), cheaters in low elo, and a comparison
  that shocks. Plain unboxings and "insane update" news sit near the bottom of their own channels.
- They clip OTHER games and side content when it is novel (Minecraft, Happy Wheels, Roblox, IRL): a new
  game or a first time doing something is a reason to clip, not a reason to skip.
- Structure: one continuous take. The clip opens with the situation already happening and ENDS on the
  reaction (the laugh, the scream, the "what?!") - no outro, no explaining afterwards.
- Title style (copy it): 2-5 words, lower-case, describing the SITUATION without giving away the
  punchline, usually ending in ONE emoji from 💀 😭 🤯 😳. Patterns: "the <thing> incident 💀",
  "<thing> is insane 💀", "wait for it 💀", "average day in <game>…", "<name> failed successfully 😭",
  "paused at the wrong time 😭", "3000 elo btw 😭", "it never goes away", "they had ONE job 💀".

For each candidate you get the transcript of the window and the signals that flagged it.
Return ONLY JSON: {"clips":[{"id":"c1","keep":true,"score":0-100,"title":"short title",
"hook":"post title in the channel's style, 2-5 words","caption":"TikTok caption: the title plus 3-5 hashtags like #cs2 #funnymoments #streamer #gaming",
"trim_start":seconds to cut from the start (0-10),"trim_end":seconds to cut from the end (0-10)}]}
Be honest: score low if the transcript shows nothing interesting happening."""


def _taste_block():
    try:
        from . import taste
        return "\n" + taste.prompt_block()
    except Exception:
        return ""


def refine(candidates, api_key, model=None, streamer=None, chat_samples=None, extra=""):
    if not api_key or not candidates:
        return candidates
    items = []
    for c in candidates:
        items.append({
            "id": c["id"], "length_s": c["len"], "signals": c["reasons"], "game": c.get("game", ""),
            "transcript": c["text"][:1200],
            "chat": (chat_samples or {}).get(c["id"], [])[:25],
        })
    prompt = (f"Streamer: {streamer or 'a Twitch streamer'}\n"
              f"Candidates:\n{json.dumps(items, ensure_ascii=False)}")
    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": 4000,
        "system": GUIDE + ("\n\n" + extra if extra else "") + _taste_block(),
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = _send(api_key, body)
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {"clips": []}
    except Exception as e:
        log.warning("AI refine failed, using heuristics: %s", e)
        return candidates

    by_id = {c["id"]: c for c in data.get("clips", []) if isinstance(c, dict)}
    out = []
    for c in candidates:
        r = by_id.get(c["id"])
        if not r:
            out.append(c)
            continue
        if r.get("keep") is False and len(candidates) > 3:
            c["ai_rejected"] = True
        try:
            c["score"] = int(round(0.4 * c["score"] + 0.6 * float(r.get("score", c["score"]))))
        except (TypeError, ValueError):
            pass
        for k in ("title", "hook", "caption"):
            if isinstance(r.get(k), str) and r[k].strip():
                c[k] = r[k].strip()
        ts = _num(r.get("trim_start")); te = _num(r.get("trim_end"))
        if c.get("lead_up"):
            ts = 0.0  # the start was moved back to show the play: keep it
        if c["len"] - ts - te >= 12:
            c["start"] = round(c["start"] + ts, 2)
            c["end"] = round(c["end"] - te, 2)
            c["len"] = round(c["end"] - c["start"], 1)
        c["ai"] = True
        out.append(c)
    out = [c for c in out if not c.get("ai_rejected")] or out
    out.sort(key=lambda c: -c["score"])
    return out


def _num(v):
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


# ---------- v2.2: AI reads the whole stream and finds the moments ----------
FIND_GUIDE = """You are the clipper for the Twitch streamer xHotGamerDude (TikTok @hotgamerdude_).
You read a timestamped transcript of part of a stream (the streamer's mic, teammates on voice chat,
and text-to-speech donation messages all appear in it) plus some chat messages, and you find the
moments worth turning into 15-40 second TikToks.

What gets the most views on this channel (from its 102 posted reels and its most-viewed viewer clips):
1. FUNNY moments beat everything: banter with friends/teammates (Axo, Dima, Ohne...), roasting,
   teammates insulting or trolling the streamer (including slurs said as banter between friends:
   that is funny on this channel, treat it as a strong moment), outrageous lines, TTS messages that
   roast the streamer and his reaction, him telling a funny story or going on a funny tangent,
   arguing with someone or something in a funny way, being caught out, misunderstandings.
2. Chaos/panic: getting chased, screaming, everything going wrong at once.
3. Gambling/upgrade/case moments ONLY with the build-up AND the result AND the reaction.
4. Clean skill plays with a clear payoff.
Not worth clipping: menus, quiet gameplay callouts, reading reviews, sponsor/promo-code talk,
song lyrics (music playing on stream), asking chat generic questions, technical problems.

What goes viral for big streamers (studied ~1,300 of the most-viewed Twitch clips of xQc,
ohnePixel, dima_wallhacks and the top Just Chatting streamers):
- People beat gameplay. The biggest clips are about the people: drama and call-outs, relationship /
  "caught" moments, someone saying something outrageous (incl. N-word slips between friends),
  embarrassing moments, rage and crash-outs, and reactions to something absurd.
- Case/knife/gold unboxings are ohnePixel's #1 format (over a third of his top 100): the clip is
  the spin -> the reveal -> the scream. Big losses ("minus 1k") do almost as well as wins.
- dima_wallhacks' best: catching or calling out cheaters (VAC bans live, "worst cheater"), meeting
  pros (1v1 vs s1mple), absurd bugs, and random out-of-context bits.
- xQc's best: arguments and opinions about other streamers, being caught/exposed, chaos in GTA RP,
  over-the-top reactions ("wow", "?????"), meme moments.
- The punchline lands fast: typical length 20-32 s; the setup is one or two lines, not a story.
- Clip titles are 1-5 words: a quote of the line ("bro called me the N word after this lmao",
  "I BROKE IT!"), the reaction ("WHAT IS THAT", "caught") or the situation ("VAC LIVE",
  "2nd knife in cs2", "dima fixes xqc"). Write titles that way.

What ohnePixel and dima_wallhacks actually POST as shorts (studied Nov 2026: 39 ohnePixel and 48 dima
shorts on YouTube, with view counts, lengths and frames). These are their own editors' picks, so weigh it:
- Length: ohnePixel's are 9-28 s most of the time (median 23 s; several of his biggest are 10-16 s).
  dima's are 10-44 s (median 29 s). Nothing rambles: one moment, one payoff, out. Prefer 12-30 s.
- What their biggest are about: something NEW or never-seen ("New Gun in CS2", "Valve JUST added this",
  a strange server/mod/game mode), an "incident" (something goes wrong in a funny way), a callback to a
  known moment ("the incident repeats"), another known person in the clip (pros, big streamers, friends),
  absurd skill or movement, nostalgia ("Old CS2 was incredible"), cheaters in low elo, and a comparison
  that shocks. Plain unboxings and "insane update" news sit near the bottom of their own channels.
- They clip OTHER games and side content when it is novel (Minecraft, Happy Wheels, Roblox, IRL): a new
  game or a first time doing something is a reason to clip, not a reason to skip.
- Structure: one continuous take. The clip opens with the situation already happening and ENDS on the
  reaction (the laugh, the scream, the "what?!") - no outro, no explaining afterwards.
- Title style (copy it): 2-5 words, lower-case, describing the SITUATION without giving away the
  punchline, usually ending in ONE emoji from 💀 😭 🤯 😳. Patterns: "the <thing> incident 💀",
  "<thing> is insane 💀", "wait for it 💀", "average day in <game>…", "<name> failed successfully 😭",
  "paused at the wrong time 😭", "3000 elo btw 😭", "it never goes away", "they had ONE job 💀".

What clip research shows (use it):
- Chat lines are timestamped when typed: chat reacts 5-20 s AFTER the moment, so the moment is
  before the chat burst. Laugh emotes/LMAO/💀 in chat = funny, POG/W/NO WAY = hype.
- The best moments line up: something happens, the voice gets loud/fast, then chat bursts.
- Strangers on TikTok have no context: the setup must be inside the clip and the first 2-3
  seconds must already be interesting.

Important for this channel:
- Do NOT score a moment lower because it contains slurs, racial jokes or edgy banter between
  friends. The streamer wants those clips; Klipon censors the subtitles.
- Teammates or TTS saying something absurd/nonsense and the streamer losing it = top tier.
- Use the whole 0-100 range: the best 2-3 moments of a stream should be 80-95.
- Keep moments 15-35 s: start on the line that sets it up, end right after the reaction.

Rules:
- A moment needs context: start where the setup starts (the question, the line that triggers it),
  end after the punchline/reaction lands. Usually 12-40 s.
- Never end on the build-up. A taste test needs the bite AND the first reaction; a case, upgrade or
  gamble needs the result AND the reaction; an attempt ("watch this") needs how it went; a story needs
  its punchline. If the payoff comes later, end the moment after the payoff (up to ~55 s).
- Only real standouts. It is fine to return none. Max 4 per chunk.
- score 0-100 = how likely it does well as a TikTok on this channel (80+ only for genuinely funny
  or wild moments a stranger would enjoy without context).
Return ONLY JSON: {"moments":[{"start":"h:mm:ss","end":"h:mm:ss","peak":"h:mm:ss",
"type":"funny|banter|story|hype|skill|fail","score":0-100,"title":"2-6 word TikTok title in the
channel's style","why":"one short sentence"}]}"""


def _hms(t):
    t = int(max(0, t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _parse_t(v):
    if isinstance(v, (int, float)):
        return float(v)
    parts = [float(p) for p in str(v).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _call(api_key, model, system, prompt, max_tokens=2000):
    body = {"model": model or DEFAULT_MODEL, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": prompt}]}
    resp = _send(api_key, body)
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def chunk_prompts(transcript, msgs, duration, chunk=360.0, overlap=30.0):
    """Timestamped transcript (+ a sample of chat) split into ~6 minute chunks."""
    segs = (transcript or {}).get("segments", [])
    out = []
    t = 0.0
    while t < duration:
        a, b = t, min(duration, t + chunk)
        lines = [(g["start"], f"[{_hms(g['start'])}] {g['text'].strip()}") for g in segs if a <= g["start"] < b]
        chat = [(ts, f"[{_hms(ts)}] chat: {m[:120]}") for ts, m in (msgs or []) if a <= ts < b]
        if len(chat) > 80:  # keep it short: an even sample
            chat = chat[:: max(1, len(chat) // 80)]
        merged = [x[1] for x in sorted(lines + chat)]
        if lines:
            out.append((a, b, "\n".join(merged)))
        t += chunk - overlap
    return out


def find_moments(transcript, msgs, duration, api_key, model=None, cache_path=None, progress=None,
                 workers=4, games=None):
    """Returns [{start,end,peak,type,score,title,why}] in seconds. Cached per VOD."""
    if cache_path:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if not api_key:
        return []
    from concurrent.futures import ThreadPoolExecutor
    chunks = chunk_prompts(transcript, msgs, duration)
    try:
        from . import taste
        guide = FIND_GUIDE + "\n" + taste.prompt_block()
    except Exception:
        guide = FIND_GUIDE
    done = [0]

    def one(ch):
        a, b, text = ch
        if games is not None:   # tell the AI which game this part of the stream is, and what works in it
            text = games.block_for(games.at((a + b) / 2)) + "\n\n" + text
        try:
            data = _call(api_key, model, guide, f"Stream section {_hms(a)} - {_hms(b)}:\n{text}")
        except Exception as e:
            log.warning("AI moment finder chunk %s failed: %s", _hms(a), e)
            data = {}
        done[0] += 1
        if progress:
            progress(done[0] / max(1, len(chunks)))
        res = []
        for m in data.get("moments", []) or []:
            try:
                s0, e0 = _parse_t(m["start"]), _parse_t(m["end"])
                pk = _parse_t(m.get("peak") or (s0 + e0) / 2)
                if e0 - s0 < 4 or e0 - s0 > 90 or not (a - 5 <= s0 <= b + 5):
                    continue
                res.append({"start": s0, "end": e0, "peak": min(max(pk, s0), e0),
                            "type": str(m.get("type", "funny")).lower(), "score": float(m.get("score", 60)),
                            "title": str(m.get("title", ""))[:60], "why": str(m.get("why", ""))[:200]})
            except (KeyError, ValueError, TypeError):
                continue
        return res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        found = [m for r in ex.map(one, chunks) for m in r]
    # chunks overlap: drop duplicates, keep the higher score
    found.sort(key=lambda m: -m["score"])
    keep = []
    for m in found:
        if all(min(m["end"], k["end"]) - max(m["start"], k["start"]) < 0.5 * (m["end"] - m["start"]) for k in keep):
            keep.append(m)
    keep.sort(key=lambda m: m["start"])
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(keep, f)
    return keep


# ---------- v2.6: the AI looks at the gameplay too ----------
LOOK_GUIDE = """You are the clipper for the Twitch streamer xHotGamerDude. You get frames from the stream
(one per second, labelled with the stream time; the streamer's webcam is in the top-left corner,
the rest is the game) and what was said, with times, for one candidate TikTok moment.

Work out what actually happens ON SCREEN and when:
- the action the streamer reacts to (a kill, an ace, an edge bug / movement trick, a jumpscare, getting
  killed, a case/upgrade result, a knife/gold reveal, something absurd) often happens a few seconds
  BEFORE they talk about it. The TikTok must show the action, then the reaction.
- if nothing visual matters (pure talking), say so.
Return ONLY JSON: {"on_screen":"one short sentence of what happens in the game","action_start":seconds or null,
"action_end":seconds or null,"clip_start":seconds,"clip_end":seconds,"visual_score":0-100}
clip_start = 1-3 s before the action (or the first line of the setup if there is no action),
clip_end = after the reaction lands. Use the stream times from the labels."""


def look_at_moments(items, api_key, model=None, workers=4, extra=""):
    """items: [{id, frames:[(t, jpeg)], lines:[(t, text)]}] -> {id: result dict}"""
    import base64
    from concurrent.futures import ThreadPoolExecutor

    def one(it):
        content = []
        for t, jpg in it["frames"]:
            content.append({"type": "text", "text": f"t={t:.0f}s"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.b64encode(jpg).decode()}})
        said = "\n".join(f"[{t:.1f}s] {x}" for t, x in it["lines"]) or "(nothing said)"
        content.append({"type": "text", "text": f"What was said:\n{said}\n\nCurrent clip: {it['start']:.1f}s to {it['end']:.1f}s."})
        body = {"model": model or DEFAULT_MODEL, "max_tokens": 400,
                "system": LOOK_GUIDE + ("\n\n" + extra + ("\nThis moment's game: " + it["game"] if it.get("game") else "") if extra else ""),
                "messages": [{"role": "user", "content": content}]}
        try:
            resp = _send(api_key, body)
            text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            return it["id"], (json.loads(m.group(0)) if m else None)
        except Exception as e:
            log.warning("AI couldn't look at %s: %s", it["id"], e)
            return it["id"], None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return {k: v for k, v in ex.map(one, items) if v}


HOOK_GUIDE = """You are a TikTok editor checking the OPENING of clips from a Twitch stream before they are posted.
Viewers swipe away within 2 seconds unless the clip opens on something: a strong or funny line, a reaction,
a question, a clear setup, or the action already happening.
For each clip you get opening_words as [seconds from the clip start, word] for the first 9 seconds, and the rest of the transcript.
Decide if the opening grabs attention. If it does, strong=true. If the first seconds are filler (uh, so, yeah, okay,
trailing end of a previous sentence, dead air, slow preamble that the payoff doesn't need), give start_at = the time of
the first word of the first line worth opening on (a sentence start, 1-7 seconds in). NEVER cut setup that the joke or
payoff needs to make sense. When unsure, strong=true.
Return ONLY JSON: {"clips":[{"id":"c1","strong":true|false,"start_at":seconds,"why":"a few words"}]}"""


def hook_check(items, api_key, model=None, slow_starts=0):
    """{clip id: {"strong": bool, "start_at": seconds, "why": str}} for the clips the AI answered."""
    if not api_key or not items:
        return {}
    guide = HOOK_GUIDE
    if slow_starts >= 2:
        guide += (f"\nThis streamer has complained {slow_starts} times that clips start too slowly: be strict, "
                  "trim any opening that isn't immediately interesting.")
    try:
        data = _call(api_key, model, guide, "Clips:\n" + json.dumps(items, ensure_ascii=False), max_tokens=1500)
    except Exception as e:
        log.warning("AI hook check failed: %s", e)
        return {}
    out = {}
    for c in data.get("clips", []) if isinstance(data, dict) else []:
        if isinstance(c, dict) and c.get("id"):
            out[str(c["id"])] = {"strong": c.get("strong") is not False, "start_at": _num(c.get("start_at")),
                                 "why": str(c.get("why", ""))[:120]}
    return out


# ---------- v2.10: "tell the AI what to change" on one TikTok ----------
ASK_GUIDE = """You are the video editor for a Twitch streamer's TikTok clips. The streamer typed a change they want
on ONE clip, in their own words (often dictated, so loosely phrased). Work out what they mean and make the edit.

You get:
- the clip as it is now: start/end in STREAM SECONDS, parts already cut out, and its settings
- the stream transcript around the clip, one line per phrase: `[stream seconds] text`. Lines marked (other) are
  someone else talking (a teammate, friend, viewer on call, text-to-speech). Lines inside the current clip are marked >>.

Return ONLY JSON:
{"start": seconds, "end": seconds,
 "cut": [[from, to], ...] | null, // null = leave the current cuts exactly as they are. Otherwise the FULL list of
                                  // stretches inside start..end to remove (stream seconds), including current cuts
                                  // you want to keep; [] = remove nothing
 "subtitles": true|false|null, "title": "new title"|null, "caption": "new TikTok caption"|null,
 "volume_db": number|null,        // voice/game volume change in dB, -20..15
 "music_volume": number|null,     // background music 0..0.5
 "jump_cuts": true|false|null,    // remove pauses between sentences
 "cutaways": true|false|null,     // big full-screen webcam on strong reactions
 "gameplay_zooms": true|false|null,
 "layout": "auto"|"split"|"blur"|"crop"|"fullcam"|"camblur"|null,
 "changed": true|false,
 "reply": "1-2 short plain sentences to the streamer: what you changed (mention the moment, not raw seconds)",
 "cannot": "anything they asked that you could not do, in plain words, else empty string"}

Rules:
- Change ONLY what they asked for. Everything else keeps its current value (null = leave as it is).
- start/end must sit on phrase boundaries from the transcript: start at the first word of a phrase, end after a phrase finishes.
- "Make it longer" with no detail: extend the end to the next natural stopping point (10-20 s more). "Shorter": trim the weakest end.
- If they describe a moment ("the bit where someone comes in", "when he says X"), FIND it in the transcript and include it
  completely, with the reaction after it. If it is far from the clip (more than ~25 s of nothing in between), keep both
  parts and put the dull stretch between them in "cut".
- Aim for under 60 seconds after cuts; never more than 120.
- If you cannot find what they describe, or the request is unclear, set changed=false, change nothing, and say what you
  need in "reply" (e.g. ask what was said in that bit).
- Things you cannot do here (add effects, text overlays, emojis, other footage, sound effects by name): say so in "cannot"."""


def edit_request(instruction, context, api_key, model=None):
    """context: the prompt text describing the clip + transcript. Returns the parsed JSON dict ({} on failure)."""
    data = _call(api_key, model, ASK_GUIDE, context + "\n\nTHE STREAMER'S REQUEST:\n" + instruction.strip()[:1500],
                 max_tokens=1200)
    return data if isinstance(data, dict) else {}


# ---------- v2.11: find a moment from a description ----------
SEARCH_GUIDE = """You are searching a Twitch stream's transcript for ONE moment the streamer remembers and described in
their own words (often dictated and loose; they may misremember exact words). You get the description and a section of
the transcript, one line per phrase: `[stream seconds] text`.

Find the part(s) of THIS section that match what they describe: the situation, who says what, the topic. Match on meaning,
not exact words. For each match give a clip that would work as a TikTok: start where the moment's setup begins (first
word of a phrase), end after the payoff/reaction finishes. 10-60 seconds, longer only if the moment needs it.

Return ONLY JSON: {"matches":[{"start": seconds, "end": seconds, "confidence": 0-100,
"title": "2-5 word TikTok title", "what": "one short sentence: what happens in it"}]}
confidence: 90+ = clearly the moment described; 60-89 = probably; below 50 = don't include it.
If nothing in this section matches, return {"matches": []}. Do not invent matches."""


def search_moment(description, sections, api_key, model=None, progress=None, workers=4):
    """sections: [(t0, t1, text)]. Returns [{start, end, confidence, title, what}] best first."""
    from concurrent.futures import ThreadPoolExecutor
    done, errors = [0], []

    def one(sec):
        a, b, text = sec
        try:
            data = _call(api_key, model, SEARCH_GUIDE,
                         f"WHAT THE STREAMER IS LOOKING FOR:\n{description.strip()[:1200]}\n\n"
                         f"Transcript section ({a:.0f}s to {b:.0f}s):\n{text}", max_tokens=900)
        except Exception as e:
            errors.append(str(e))
            data = {}
        done[0] += 1
        if progress:
            progress(done[0] / max(1, len(sections)))
        out = []
        for m in (data.get("matches") or []) if isinstance(data, dict) else []:
            try:
                s0, e0, cf = float(m["start"]), float(m["end"]), float(m.get("confidence", 60))
            except (KeyError, TypeError, ValueError):
                continue
            if cf < 50 or e0 - s0 < 4 or e0 - s0 > 200 or not (a - 10 <= s0 <= b + 10):
                continue
            out.append({"start": s0, "end": e0, "confidence": cf, "title": str(m.get("title", ""))[:60],
                        "what": str(m.get("what", ""))[:200]})
        return out

    with ThreadPoolExecutor(max_workers=workers) as ex:
        found = [m for r in ex.map(one, sections) for m in r]
    if not found and errors and len(errors) == len(sections):
        raise RuntimeError(errors[0])
    found.sort(key=lambda m: -m["confidence"])
    keep = []
    for m in found:   # sections overlap: drop repeats
        if all(min(m["end"], k["end"]) - max(m["start"], k["start"]) < 0.4 * (m["end"] - m["start"]) for k in keep):
            keep.append(m)
    return keep

