"""Game playbooks: what makes a good TikTok in the game being played.

Kliper reads which game a stream (or each part of a stream) is on from Twitch, and gives the AI the
matching playbook so it knows what to look for. Add a game by adding an entry to PLAYBOOKS; anything
without one gets the general rules. Your own notes from Settings ("Notes for the AI") are added on top.
"""
import re

from .util import load_settings

PLAYBOOKS = [
    {
        "name": "Grand Theft Auto VI",
        "match": r"grand theft auto (vi|6)\b|\bgta\s*(vi|6)\b",
        "tags": "#gta6 #gtavi #gta #gaming #fyp",
        "guide": """GTA VI is the biggest release in years: everyone is searching for it, so clips of it travel far beyond this
channel's usual audience. The first weeks are a race: FIRST REACTIONS and discoveries are worth the most.
What gets views in GTA (learned from years of GTA V / RP clips, plus what works at a big launch):
- First-time reactions: seeing something for the first time and losing it ("wait you can do THAT?", "look at the detail").
  Small details, new mechanics, things NPCs do or say, easter eggs, references to older GTA games.
- Chaos that escalates: a normal moment turns into a disaster (a small crash becomes a 5-star chase, a quiet robbery goes
  wrong, a stunt fails or somehow works). The setup -> things go wrong -> reaction arc is the clip. Keep the lead-up.
- Physics / ragdoll / car comedy: getting launched, falling off things, vehicles doing something ridiculous.
- Police and wanted-level moments: close escapes, getting busted or wasted at the worst possible time.
- Bugs and glitches (huge in launch month): anything broken, floating, clipping, or an NPC behaving wrongly.
- NPC and character moments: funny or unhinged NPC lines, arguments with pedestrians, anything Jason or Lucia say or do
  that the streamer reacts to hard.
- People over gameplay still applies: the streamer's panic, laughing, arguing with chat or friends about what just happened.
- Online / RP (when it exists): voice-chat encounters with strangers, trolling, role-play banter, heists going wrong.
Weak in GTA: driving from A to B with nothing happening, long cutscenes with no reaction, menu / map / shopping,
mission briefings, calm free-roam.
SPOILERS: for story missions, never put the plot twist, a death, or an ending in the title or caption. Title the
streamer's reaction instead ("I was NOT ready for this mission"). A spoiled title gets skipped and reported.""",
        "cues": r"\b(wasted|busted|five stars?|5 stars?|wanted level|cops?|police|helicopter|ragdoll|glitch(ed)?|bugged|"
                r"npc|lucia|jason|vice city|leonida|heist|getaway|car jack(ed)?|ran (him|her|them) over)\b",
    },
    {
        "name": "Grand Theft Auto V",
        "match": r"grand theft auto (v|5)\b|\bgta\s*(v|5|online|rp)\b|fivem",
        "tags": "#gta #gta5 #gtarp #gaming #fyp",
        "guide": """GTA V / GTA Online / RP. What gets views: chaos that escalates (small mistake -> full police chase),
physics and ragdoll comedy, role-play banter and voice-chat encounters with strangers, trolling, heists or robberies
going wrong, close escapes, getting wasted or busted at the worst moment. Keep the lead-up so the viewer sees it go
wrong. Weak: plain driving, menus, long quiet stretches.""",
        "cues": r"\b(wasted|busted|five stars?|5 stars?|cops?|police|ragdoll|heist|getaway|npc)\b",
    },
    {
        "name": "Counter-Strike 2",
        "match": r"counter-?strike|\bcs2\b|\bcs:?go\b",
        "tags": "#cs2 #counterstrike #gaming #fyp",
        "guide": """CS2. What gets views: clutches and aces, absurd or lucky kills, cheaters / suspicious plays and getting
VAC-banned talk, case and skin unboxings (the opening AND the reaction), gambling wins and losses, team-chat banter,
teammates raging or saying something ridiculous, movement bugs (edge bugs, jump bugs): always show the play before the
reaction. Weak: buy phases, quiet rounds, plain callouts.""",
        "cues": r"\b(ace|clutch|1v[2-5]|knife|unbox(ed|ing)?|case|skin|vac|cheater|wallhack|edge ?bug|jump ?bug|no ?scope)\b",
    },
    {
        "name": "Call of Duty",
        "match": r"call of duty|\bcod\b|black ops|warzone|modern warfare",
        "tags": "#callofduty #bo7 #cod #warzone #gaming #fyp",
        "guide": """Call of Duty (Black Ops 7 / Warzone). What got the most views in Nov 2026: the streamer's reaction to the game
itself (big opinions on the new game, zombies, "the BO7 experience"), absurd deaths (noob tube, getting sniped mid-sentence,
spawn deaths), clutch multi-kills and last-circle wins in Warzone, voice-chat banter with randoms and teammates, rage.
Keep the kill feed / killcam moment in: the viewer needs to see what killed them. Weak: loadout menus, long loading,
quiet looting.""",
        "cues": r"\b(nuke|killcam|noob ?tube|sniped|camping|spawn(ed)?|loadout|zombies|wall ?bang|last circle|gulag|"
                r"triple|quad|clutch|uav|chopper)\b",
    },
    {
        "name": "Fortnite",
        "match": r"fortnite",
        "tags": "#fortnite #fortniteclips #gaming #fyp",
        "guide": """Fortnite. The biggest streamer clips (Jynxzi, Nov 2026: 1.3M, 455K, 376K) are short reactions to almost winning or
almost dying ("Ahh close again tho"), build/edit fights that go wrong or right, getting third-partied, trick shots, funny
emotes and trash talk, and screaming at the storm. One line the streamer says becomes the TikTok's quote title, so pick
moments with a quotable line. Keep it short (10-25 s). Weak: looting, rotating across the map, the lobby.""",
        "cues": r"\b(victory royale|third.?part(y|ied)|storm|build|edit|box(ed)?|cranked|one pump|snipe[d]?|emote|"
                r"close again|almost|w key|zone)\b",
    },
    {
        "name": "The Legend of Zelda",
        "match": r"zelda|ocarina|tears of the kingdom|breath of the wild",
        "tags": "#zelda #ocarinaoftime #nintendo #gaming #fyp",
        "guide": """The Legend of Zelda (the Ocarina of Time remake, Nov 2026; also Tears of the Kingdom / Breath of the Wild).
What gets views: first reactions to remade scenes and bosses (nostalgia: "peak is back", "I was NOT ready"), comparing it to
the original, boss fights that go wrong, getting lost or stuck in a temple, funny NPC lines and voice acting, singing along
to the music, and talking to chat about it. Keep the game's own dialogue on screen when the reaction is about what a
character said. No spoilers of the ending in titles. Weak: menus, long walks across the map.""",
        "cues": r"\b(zelda|link|ganon(dorf)?|navi|epona|temple|boss|ocarina|master sword|deku|hyrule|remake|nostalgi\w*|"
                r"original)\b",
    },
    {
        "name": "Just Chatting",
        "match": r"just chatting|irl|talk shows",
        "tags": "#justchatting #streamer #twitch #fyp",
        "guide": """Just Chatting / IRL. It is all about people: stories with a payoff, hot takes and arguments, call-outs and
drama, banter with chat or friends, reactions to something shown on screen, awkward or wholesome moments with strangers,
flirting and rejections. Start on the line that sets the story up; end right after the punchline or reaction.""",
        "cues": r"",
    },
]

GENERAL = """Unknown or other game. Use the general rules: people over gameplay, chaos and panic, big reactions, banter,
fails and close calls, anything a viewer would send to a friend."""


def playbook(name):
    """The playbook matching a game name (or stream title), or None."""
    n = (name or "").lower()
    if not n:
        return None
    for p in PLAYBOOKS:
        if re.search(p["match"], n):
            return p
    return None


def notes():
    return (load_settings().get("ai_notes") or "").strip()[:1500]


class Games:
    """Which game was on screen at each point of one stream."""

    def __init__(self, chapters=None, default="", title=""):
        self.chapters = [c for c in (chapters or []) if c.get("game")]
        self.default = default or ""
        if not self.default and not self.chapters:
            p = playbook(title)       # local file / no Twitch info: guess from the stream title
            self.default = p["name"] if p else ""

    def at(self, t):
        for c in self.chapters:
            if c["start"] <= t < c["end"]:
                return c["game"]
        return self.default

    def names(self):
        out = []
        for g in [c["game"] for c in self.chapters] + [self.default]:
            if g and g not in out:
                out.append(g)
        return out

    def block_for(self, name):
        """Prompt text for one game: its playbook (or the general rules) plus your notes."""
        p = playbook(name)
        out = [f"GAME BEING PLAYED: {name or 'unknown'}", (p or {}).get("guide", GENERAL)]
        n = notes()
        if n:
            out.append("THE STREAMER'S OWN NOTES FOR YOU (follow these): " + n)
        return "\n".join(out)

    def block_all(self):
        """Prompt text covering every game in the stream (for calls that look at clips from all of it)."""
        names = self.names()
        if not names:
            n = notes()
            return ("THE STREAMER'S OWN NOTES FOR YOU (follow these): " + n) if n else ""
        out = ["GAMES IN THIS STREAM (each candidate says which game it is from):"]
        seen = []
        for g in names:
            p = playbook(g)
            key = (p or {}).get("name", "general")
            if key in seen:
                continue
            seen.append(key)
            out.append(f"--- {g} ---\n" + (p or {}).get("guide", GENERAL))
        n = notes()
        if n:
            out.append("THE STREAMER'S OWN NOTES FOR YOU (follow these): " + n)
        return "\n".join(out)

    def tags(self, t):
        p = playbook(self.at(t))
        return (p or {}).get("tags", "#twitch #gaming #fyp")

    def cue_score(self, t, text):
        """0..1: how many of the game's own hype words are in this text (wasted, busted, clutch...)."""
        p = playbook(self.at(t))
        if not p or not p.get("cues") or not text:
            return 0.0
        return min(1.0, len(re.findall(p["cues"], text.lower())) / 3.0)

    def stamp(self):
        return "|".join(self.names()) + "#" + str(len(notes()))
