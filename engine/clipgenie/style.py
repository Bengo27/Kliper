"""Default edit style, matched to the TikToks your editor made for @hotgamerdude
(May 2026 reels: "Mirage 1v4!", "ohnePixel blocked me!", "Why is it always green?" ...).
Every value can still be changed per TikTok in the app."""

EDITOR_STYLE = {
    # subtitles: lowercase 1-4 word phrases, white Montserrat ExtraBold with a dark
    # outline, hype words + sound effects (*laughs*) in green, swearing censored (sh*t)
    "style": "editor",
    "font": "Rubik Black",
    "highlight": "#3dff4a",
    "words_per_line": "4",
    "uppercase": False,
    "caption_seam": True,
    "size": 1.0,
    "captions": False,          # 2.9: no subtitles on new TikToks - add them per TikTok ("Add subtitles")
    "show_hook": False,
    # layout: webcam on top (~42% of the screen), gameplay below, channel watermark
    "layout": "auto",
    "cam_ratio": 0.42,
    "cam_tight": True,
    "watermark": "xHotGamerDude",
    # edit
    "jump_cuts": True,
    "jump_min_gap": 0.6,        # 2.32: was 1.1, which quietly undid the 2.23 tighter pause cuts
    "cut_boring": True,
    "end_linger": 1.8,          # 2.32: seconds the clip keeps running after the last word (0 = cut right away)         # 2.32: drop pure filler phrases ("um", "so yeah") in the lead-up (director.dull_cuts)
    "cam_punch_every": 0,       # 2.9: no small webcam punch-ins (he doesn't like them)
    "cam_zooms": False,         # 2.9: no small webcam zooms; full-screen webcam cutaways stay
    "zoom_whoosh": False,       # no swish on zooms (you didn't like it)
    "zoom": True,
    "hook_teaser": False,   # start at the real start of the moment (no cold-open replay)
    "cutaways": True,
    "sfx": True,
    "normalize_audio": False,
    "color_others": True,      # subtitles in another colour when someone else is talking
    "hook_check": True,        # AI checks the first 2 s grab attention before a TikTok is made
    "look_at_gameplay": True,  # check the footage so clips show the play, not just the reaction
    "other_color": "#5ec8ff",
    "volume_db": 0,          # your volume for every TikTok (0 = same as the stream)
    "duck_music": False,
    "bg_music_volume": 0.16,   # one steady music level (Settings > Music volume); the same default everywhere
    # speech: "" = detect the language (Settings > Speech language picks one by hand)
    "language": "",
    # clip finding
    "target_len": 22,
    "min_len": 14,
    "max_len": 38,
    "count": 16,
    "auto_count": 0,
    "auto_min_score": 70,
    # 2.18 looks (see PRESETS): all off = the look above
    "look": "editor",
    "title_bar": False,         # the TikTok's title on screen the whole video (*word* = coloured)
    "title_pos": "top",         # "top" of the screen, or "seam" (on the webcam, above the webcam/gameplay line)
    "title_highlight": "#ffd23f",
    "frame": False,             # whole edit shrunk onto black, room above for the title
    "watermark_pos": "bottom",  # channel name "bottom" (on the gameplay) or "seam" (on the webcam/gameplay line)
    "overlays": [],             # game HUD parts pasted on top: "cs2_hud", "cs2_killfeed" (render.HUD_PARTS)
    "react_sfx": "",            # 2.19: sound on big reactions: "" built-in bass hit, "none", or one of your sounds
    "chat_popups": "auto",      # 2.20: chat messages pop up: "auto" when you talk to chat, "always", "off"
    "chat_style": "grey",      # 2.21: ONE context chat message over the gameplay in a Twitch-chat box ("glass"); also "twitch", "pill", "clean"
    "chat_opacity": 100,        # chat box opacity in % (100 = solid Twitch grey)
    "chat_name_opacity": 100,   # viewer name opacity in %
    "title_style": "plain",     # 2.20: "plain", "box" (white box, CoD), "banner" (box in CAPS, Zelda), "quote" (Fortnite)
    "title_size": 1.0,          # 2.24: title text size (1 = 96 px)
    "subs_mode": "all",         # 2.24: "all" every line, "key" only the lines that carry the moment (1-3 CAPS words)
    "punch_more": False,        # 2.24: extra gameplay punch-ins every ~4 s
    "end_bw": False,            # 2.24: black-and-white ending + one big red word
    "end_word": "",             # 2.24.1: the red word; empty = no ending effect
}

# 2.18: ready-made looks, studied from the most-viewed ohnePixel and dima_wallhacks TikToks (Nov 2026).
# They only set how the TikTok LOOKS; the channel name comes from each streamer's own settings.
LOOK_KEYS = ("look", "layout", "title_bar", "title_pos", "title_highlight", "title_style", "frame", "watermark_pos",
             "overlays", "cam_ratio", "cutaways", "zoom", "jump_cuts", "title_size", "subs_mode", "punch_more", "end_bw",
             "sfx", "chat_style", "chat_pos")   # 2.35: a look can bring its own chat style; 2.25: a look can come with or without sound effects (Clean: none); the switch still overrides
PRESETS = [
    {"id": "editor", "name": "Simple",
     "about": "Webcam on top, gameplay below, your channel name. No title.",
     "style": {"layout": "auto", "title_bar": False, "frame": False, "watermark_pos": "bottom", "overlays": []}},
    {"id": "dima", "name": "Professional",
     "about": "A short title on the webcam line, with one word in colour.",
     "style": {"layout": "auto", "title_bar": True, "title_pos": "seam", "title_highlight": "#ffd23f", "frame": False,
               "watermark_pos": "bottom", "overlays": []}},   # 2.36.1: no CS2 top bar (the creator)
    {"id": "ohne", "name": "Bold title",
     "about": "Big title at the top the whole video.",
     "style": {"layout": "auto", "title_bar": True, "title_pos": "top", "title_highlight": "#ffd23f", "frame": False,
               "watermark_pos": "bottom", "overlays": []}},
    {"id": "framed", "name": "Cinematic",
     "about": "The video sits smaller on black, with the title above it.",
     "style": {"layout": "auto", "title_bar": True, "title_pos": "top", "title_highlight": "#a37bff", "frame": True,
               "watermark_pos": "bottom", "overlays": []}},
    {"id": "clean", "name": "Clean",
     "about": "Just webcam and gameplay with your channel name. Nothing else.",
     "style": {"layout": "auto", "title_bar": False, "frame": False, "watermark_pos": "bottom", "overlays": [],
               "sfx": False}},
    # 2.20: one look per game, from that game's most-liked streamer TikToks (docs/editing-research.md)
    {"id": "cs2", "name": "CS2 + kill feed", "game": "cs2",
     "about": "Big webcam on top, short CAPS title on the line, sharp kill feed rows popping in on a clutch, "
              "chat as plain text low on the gameplay.",
     "style": {"layout": "split", "cam_ratio": 0.46, "title_bar": True, "title_pos": "seam", "title_style": "caps",
               "title_highlight": "#ffd23f", "frame": False, "watermark_pos": "bottom",
               "overlays": ["cs2_killfeed"],   # 2.34: back, with the clean clutch-only kill feed (the creator)
               # 2.35: no CS2 top bar strip (the creator: "remove this bit"; it grows at round end and gets cut off)
               "killfeed_style": "clean", "killfeed_when": "clutch",
               "chat_style": "caption", "chat_pos": "low"}},   # 2.35: chat as plain outlined text, low (the creator's example)
    {"id": "cod", "name": "Call of Duty", "game": "cod",
     "about": "Webcam on top, the title as clean text with an outline on the line.",
     "style": {"layout": "split", "title_bar": True, "title_pos": "seam", "title_style": "plain", "frame": False,
               "watermark_pos": "bottom", "overlays": []}},
    # 2.24: measured from 4 top dima_wallhacks-style TikToks (tools/analyze_tiktok.py, 11 Nov 2026)
    {"id": "viral", "name": "Viral",
     "about": "CAPS title on the webcam line, subtitles only on the key lines (top of the gameplay), "
              "quick gameplay zoom-ins. Type a word in Cutting for a black-and-white ending with it in big red.",
     "style": {"layout": "split", "title_bar": True, "title_pos": "seam", "title_style": "caps", "title_size": 1.0,
               "title_highlight": "#ffd23f", "frame": False, "watermark_pos": "bottom", "overlays": [],
               "captions": True, "subs_mode": "key", "style": "editor", "highlight": "#ff3b5c",
               "punch_more": True, "zoom": True, "end_bw": True, "cutaways": True, "sfx": True}},
    {"id": "fullstream", "name": "Full stream",
     "about": "The whole stream picture on black (nothing cut off: kill feed, map, your webcam corner), title above.",
     "style": {"layout": "letterbox", "title_bar": True, "title_pos": "top", "title_style": "plain", "frame": False,
               "watermark_pos": "bottom", "overlays": []}},
    {"id": "zelda", "name": "Zelda", "game": "zelda",
     "about": "Webcam on top, a CAPS title on the line (clean text, no box), the game's own text left readable.",
     "style": {"layout": "split", "title_bar": True, "title_pos": "seam", "title_style": "caps", "frame": False,
               "watermark_pos": "bottom", "overlays": []}},
    {"id": "fortnite", "name": "Fortnite", "game": "fortnite",
     "about": "Full-screen gameplay, your webcam as a box over it, what you said as a big quote.",
     "style": {"layout": "camover", "title_bar": True, "title_pos": "middle", "title_style": "quote", "frame": False,
               "watermark_pos": "bottom", "overlays": []}},
]

# 2.26 (the creator: "keep it simple"): these stay working for TikToks already made with them, but aren't offered any more.
# Clean is nearly the same as Simple.
# 2.27: down to 2 (the creator): Simple and Professional (+ "Other" = your own styles).
# 2.34: CS2 is back (the creator: "bring back cs2 editing style, have kill feeds too").
HIDDEN_LOOKS = ("clean", "zelda", "fortnite", "ohne", "framed", "cod", "viral", "fullstream")
for _p in PRESETS:
    if _p["id"] in HIDDEN_LOOKS:
        _p["hidden"] = True

for _p in PRESETS:   # every look sets these, so switching looks never leaves one behind
    _p["style"]["watermark_pos"] = "bottom"   # 2.28: the Twitch name sits in ONE spot in every style (the creator)
    _p["style"].setdefault("title_size", 1.0)
    _p["style"].setdefault("subs_mode", "all")
    _p["style"].setdefault("punch_more", False)
    _p["style"].setdefault("end_bw", False)
    _p["style"].setdefault("title_style", "plain")
    _p["style"].setdefault("cam_ratio", 0.42)
    _p["style"].setdefault("title_pos", "top")
    _p["style"].setdefault("chat_style", "grey")   # 2.35: switching away from CS2 brings the Twitch chat box back
    _p["style"].setdefault("chat_pos", "")


def preset(look_id):
    return next((p for p in PRESETS if p["id"] == look_id), None)


# 2.20: which look fits which game (Twitch category / chapter name). Used when "pick the look for each game" is on.
GAME_LOOKS = [
    (r"counter-?strike|\bcs2\b|\bcs:?go\b", "cs2"),
    (r"call of duty|\bcod\b|black ops|warzone|modern warfare", "cod"),
]   # 2.26: Zelda and Fortnite looks are hidden (the creator didn't like them): those games get the default style


def look_for_game(game):
    import re
    for rx, look in GAME_LOOKS:
        if game and re.search(rx, game, re.I) and look not in HIDDEN_LOOKS:   # 2.27: hidden looks aren't picked
            return look
    return None


STYLE_NOTES = """Learned from @hotgamerdude's last 102 reels (Instagram, Oct 2026).

Editor's look (May 2026 reels):
- Webcam on top (~42% of the screen), gameplay below, xHotGamerDude watermark on the gameplay.
- Subtitles: lowercase 1-4 word phrases, white Montserrat ExtraBold with a dark outline,
  sitting on the webcam/gameplay line; hype words and sound effects (*laughs*) in green;
  swearing censored (sh*t).
- Rhythm: gameplay keeps running (about 1 hard cut per 10 s); full-screen webcam on big reactions.
  (The editor also punched the webcam in and out every ~1.5-2 s: the creator doesn't like those small
  zooms, so Klipon doesn't do them since 2.9. Subtitles are off by default since 2.9 too.)

What performs (views):
- Chaos/panic in party games (How to Fish): median ~11k views vs ~660 for the rest.
- Funny drama with friends (Betrayal of the year 17.6k, Axo might be gay 6k).
- Clean skill plays: Deagle master 6.1k, Clean ace 3.6k, 2 hp round 3.2k.
- 15-20 s clips have the best median; generic rage/complaining does worst.
"""
