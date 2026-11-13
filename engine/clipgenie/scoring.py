"""Combine chat, audio and transcript signals into ranked clip candidates."""
import math
import re
import numpy as np

from .util import fmt_time

# Phrases streamers say at clip-worthy moments, with weights
SPOKEN = [
    (r"\bno way\b", 2.0), (r"\boh my (god|gosh|days)\b", 2.0), (r"\bwhat the\b", 1.8),
    (r"\blet'?s go+\b", 2.0), (r"\bclip (it|that|this)\b", 2.5), (r"\bare you (kidding|serious)\b", 2.0),
    (r"\bwhat+\b[?!]", 1.4), (r"\bbro+\b", 0.8), (r"\bdude\b", 0.8), (r"\byo+\b", 0.7),
    (r"\bno+o+\b", 1.6), (r"\bha(ha)+\b", 1.4), (r"\blo+l\b", 1.0), (r"\bholy\b", 1.5),
    (r"\bi'?m (dead|done|crying)\b", 1.8), (r"\bhow (did|is|was)\b", 1.0), (r"\binsane\b", 1.4),
    (r"\bclutch\b", 1.6), (r"\bjump ?scare\b", 1.6), (r"\b(fuck|fucking|shit|damn)\b", 1.0),
    (r"\bwhy (would|did)\b", 1.0), (r"\bget (out|away)\b", 1.2), (r"\brun\b", 0.8),
    (r"\bwait wait\b", 1.2), (r"\bi can'?t\b", 0.8), (r"\bthat'?s crazy\b", 1.5),
    # what goes viral on CS / Just Chatting channels (xQc, ohnePixel, dima_wallhacks top clips)
    (r"\b(knife|karambit|butterfly|doppler|gold|gloves|kukri|fade|dragon lore|souvenir)\b", 1.3),
    (r"\b(vac|walling|wallhack(ing|s)?|cheater|cheating|aimbot|spinbot)\b", 1.5),
    (r"\b(caught|exposed|snitch(ing|ed)?)\b", 1.4), (r"\bwhat is that\b", 1.3),
]
# Klipon: what Israeli streamers say at clip-worthy moments. Hebrew glues small words to the front of the next
# word (ו, ה, ש, ב, ל, כ, מ: "ואין מצב", "שמה זה"), so every phrase may start with up to two of them.
HE = "(?:[והשבלכמ]{0,2})"
SPOKEN_HE = [
    (HE + r"אין (מצב|סיכוי|מילים|דברים כאלה)", 2.0), (HE + r"מה זה (היה|הדבר הזה)", 1.9),
    (HE + r"מה (קורה|הולך) (פה|כאן)", 1.6), (HE + r"מה (ה?לעזאזל|ה?פאק|ה?שיט)", 1.8),
    (r"(תעשו|עשו|תעשה|תעשי) קליפ|[ות]?קלפו|[ות]?תקלפו|קליפ (את )?זה|תקליפו", 2.5),
    (r"ו+א+ו+(?!\w)|וואו+|וואי+ ?וואי+|יא ?(אללה|רבאק|ווראדי|ווראדיני|אמא)", 1.6),
    (r"(אתה|את|אתם|אתן) (רציני|רצינית|רציניים|נורמלי|נורמלית|נורמלים|בסדר|דפוק|דפוקה|חולה|חולת)", 2.0),
    (HE + r"(אני|אנחנו) (מת|מתה|מתים|גמור|גמורה|בוכה|לא מאמין|לא מאמינה|לא יכול|לא יכולה)", 1.8),
    (HE + r"(על אמא שלי|נשבע לך|נשבע לכם|בחיים לא|בחיים שלי לא|תגיד לי שזה לא)", 1.8),
    (HE + r"(מטורף|מטורפת|מטורפים|פסיכי|פסיכית|הזוי|הזויה|שיגעון|משוגע|אגדי|היסטורי)(?!\w)", 1.4),
    (HE + r"איזה (מלך|מלכה|אגדה|גאון|תותח|תותחית|פספוס|באסה|מזל|הימור)", 1.5),
    (HE + r"(יאללה|קדימה|בוא+|בואו+) (נלך|נזוז|ביי)|יאללה+(?!\w)", 1.2),
    (r"לא+ ?,? ?לא+ ?,? ?לא+|לאאא+", 1.7), (r"די+ ?,? ?די+|דייי+", 1.3),
    (r"(ח[חה]){2,}ח?|(הא){3,}|(הה){3,}", 1.6), (HE + r"(אוי+ ?(ואבוי|לא|ואי)|אויש)", 1.5),
    (HE + r"(לך|לכו|תלך|תברח|תברחו|ברח|ברחו|רוץ|רוצו)(?!\w)", 1.0), (HE + r"(למה|איך) (הוא|היא|זה|אתה|את)", 1.0),
    (r"(פאק|שיט|זין|יבן ?זונה|בן ?זונה|כוס ?אמק|כוסאמק|מניאק|לעזאזל|יא ?חרא|חרא)(?!\w)", 1.0),
    (HE + r"(אייס|קלאץ'?|קלאצ'?|הדשוט|וואן ?טאפ|טריפל|קוואד)(?!\w)", 1.6),
    (HE + r"(צ'יטר|צ'יטרים|צ'יטים|ווליים|באן|תפסו אותו|נתפס)(?!\w)", 1.5),
    (HE + r"(נייף|סכין|קרמביט|באטרפליי|גלובס|כפפות|דופלר|ג'קפוט|זכיתי|זכינו|הרווחתי)(?!\w)", 1.3),
    (HE + r"(ניצחנו|ניצחתי|הפסדנו|הפסדתי|מתתי|מתנו|הרגתי|הרגנו)(?!\w)", 1.0),
    (HE + r"(אחי|אחשלי|אחושלי|אחותי|גבר|נשמה)(?!\w)", 0.6), (HE + r"(רגע ?,? ?רגע|חכה ?,? ?חכה|שניה ?,? ?שניה)", 1.2),
    (HE + r"(זה|היה) (מצחיק|ענק|חולני|קטלני|גדול)", 1.3), (HE + r"איך (עשיתי|עשית|עשה) (את )?זה", 1.4),
]
SPOKEN = SPOKEN + SPOKEN_HE
SPOKEN = [(re.compile(p, re.I), w) for p, w in SPOKEN]


def text_curve(transcript, duration, hop=0.5):
    n = int(duration / hop) + 1
    curve = np.zeros(n, dtype=np.float32)
    words = transcript.get("words", []) if transcript else []
    if not words:
        return curve
    # Phrase hits, using a sliding text window over words
    for i in range(len(words)):
        chunk = " ".join(w["text"] for w in words[i:i + 5])
        t = words[i]["start"]
        idx = int(t / hop)
        if idx >= n:
            break
        for rx, wgt in SPOKEN:
            if rx.match(chunk):
                curve[idx] += wgt
                break
        # repeated word ("no no no", "go go go")
        if i >= 2 and words[i]["text"].lower().strip(".,!?") == words[i - 1]["text"].lower().strip(".,!?") == words[i - 2]["text"].lower().strip(".,!?"):
            curve[idx] += 1.2
        if words[i]["text"].endswith("!"):
            curve[idx] += 0.4
    # speech-rate spikes (talking fast = hype)
    rate = np.zeros(n, dtype=np.float32)
    for w in words:
        i = int(w["start"] / hop)
        if i < n:
            rate[i] += 1
    k = max(1, int(4 / hop))
    kern = np.ones(k, dtype=np.float32)
    rate = np.convolve(rate, kern, mode="same") / (k * hop)
    base = np.median(rate[rate > 0]) if np.any(rate > 0) else 1.0
    curve += np.clip((rate - base * 1.6) / max(base, 0.5), 0, 3) * 0.6
    # story bonus: build-up (countdown / upgrade / "here we go") followed by a reaction
    music = music_times(transcript)
    sus = [t for t in _cues(transcript, 0, duration, SUSPENSE) if not _near(t, music)]
    outs = [t for t in _cues(transcript, 0, duration, OUTCOME) if not _near(t, music)]
    j = 0
    for t in outs:
        while j < len(sus) and sus[j] < t - 30:
            j += 1
        if j < len(sus) and t - 30 <= sus[j] <= t - 3:
            idx = int(t / hop)
            if idx < n:
                curve[idx] += 2.5
    # spread phrase hits over ~3 s
    k = max(1, int(3 / hop))
    curve = np.convolve(curve, np.ones(k, dtype=np.float32), mode="same")
    return curve


# ---------- funny moments (v2.2) ----------
# What gets clipped on this channel by viewers (top clips by views): banter with friends,
# roasts, outrageous lines, TTS messages, stories - not just loud gameplay.
FUNNY = [
    # laughing
    (r"\b(ha){2,}h?\b|\b(he){3,}h?\b|\blaugh(ing|s)?\b|\blmao\b|\blo+l\b", 2.2),
    (r"\bi'?m (dead|crying|weak|done)\b|\bi can'?t (breathe|stop)\b", 2.0),
    # banter / roasting / teammates
    (r"\bshut (the fuck |the hell )?up\b", 1.8), (r"\bwhat (did|do) you (just )?say\b", 2.4),
    (r"\bbro said\b|\bhe said\b|\bshe said\b|\byou said\b", 1.4),
    (r"\bwhy (would|did) you\b", 0.9), (r"\bwhat'?s wrong with you\b", 1.8),
    (r"\bare you (mad|okay|ok|serious|dumb|stupid|kidding|lying)\b", 1.6),
    (r"\byou'?re (so|actually|literally) (bad|weird|dumb|trash|stupid|lying|annoying)\b", 1.8),
    (r"\bi (actually |literally )?hate you\b", 1.8), (r"\b(ban|banning|blocking) you\b", 1.6),
    (r"\byou piss me off\b|\btroll(ed|ing)? me\b", 1.8), (r"\bwe don'?t judge\b", 1.6),
    (r"\byou look (like|\d+)\b|\byou sound like\b", 1.6), (r"\bwhat are you (on about|saying|talking about)\b", 1.0),
    (r"\bwho (the fuck|the hell) (is|are)\b", 1.3), (r"\bgo get a job\b", 2.0),
    (r"\b(n[i1]gg?a|n[i1]gger|niger|n[- ]?word|end word)s?\b", 2.0),
    (r"\b(racist|gay|diddy|girlfriend|boyfriend|kiss(ing)?|date|your mum|your mom|my mum|my mom)\b", 1.2),
    (r"\b(broke|no socks|scam(med|ming)?|hacked|lying to me|liar|clown|bozo|weirdo|freaky)\b", 1.2),
    (r"\bi'?m sorry\b|\bapolog(y|ise|ize)\b", 0.9), (r"\bnot on my watch\b", 1.4),
    (r"\b(said the n.?word|called me the n.?word|he said what|she said what|did (he|she|you) just say)\b", 2.2),
    (r"\b(crash(ing)? out|rage ?quit|i'?m done|i quit)\b", 1.2),
    # stories / funny topics
    (r"\b(one time|i remember|story ?time|when i was (a kid|young|little)|back in the day|i used to)\b", 1.3),
    (r"\bdid you (hear|see|know)\b|\byou (won'?t|will never) believe\b", 1.2),
]
# Klipon: the same for Hebrew banter, roasts and stories
FUNNY += [
    (r"(ח[חה]){2,}ח?|(הא){3,}|(הה){3,}|" + HE + r"(צוחק|צוחקת|צחקתי|מתפוצץ מצחוק|מתגלגל)", 2.2),
    (HE + r"(אני|אנחנו) (מת|מתה|מתים|בוכה|גמור|גמורה)|לא יכול (לנשום|להפסיק)", 2.0),
    (HE + r"(שתוק|שתקי|סתום|סתמי|תסתום|תסתמי|תשתוק|תשתקי)(?!\w)", 1.8),
    (HE + r"מה (אמרת|הוא אמר|היא אמרה|הרגע אמרת|אמרת עכשיו)", 2.4),
    (HE + r"(הוא|היא|אתה|את) (אמר|אמרה|אמרת) ש", 1.4),
    (HE + r"מה (נסגר|הבעיה) (איתך|איתו|איתה|אתך)", 1.8), (HE + r"(אתה|את) (מטומטם|מטומטמת|אידיוט|אידיוטית|דביל|דבילית|סתום|סתומה|גרוע|גרועה|זבל|חלאה|שקרן|שקרנית|מעצבן|מעצבנת)", 1.8),
    (HE + r"(אני )?(שונא|שונאת) (אותך|אותכם|אתכם)", 1.8), (HE + r"(חוסם|חוסמת|מבאן|מבאנת|באנתי) (אותך|אותו|אותה)", 1.6),
    (HE + r"(מה אתה|מה את|על מה אתה|על מה את) (מדבר|מדברת|אומר|אומרת|עושה)", 1.0),
    (HE + r"לך (תעבוד|תמצא עבודה|תתקלח|לישון)", 2.0), (HE + r"(אמא שלך|אמא שלי|החברה שלך|החבר שלך|דייט|נשיקה)", 1.2),
    (HE + r"(סליחה|מתנצל|מתנצלת|סורי)(?!\w)", 0.9), (HE + r"(עוקץ|עקצו|נעקצתי|פרייר|ליצן|מוזר|פריק|קמצן)(?!\w)", 1.2),
    (HE + r"(פעם אחת|אני זוכר|אני זוכרת|כשהייתי (קטן|קטנה|ילד|ילדה)|תקשיבו לסיפור|סיפור קטן|יש לי סיפור)", 1.3),
    (HE + r"(שמעתם|ראיתם|ידעתם) (ש|על|את)|לא תאמינו", 1.2), (HE + r"(עזבתי|יוצא|יוצאת|מוחק את המשחק|גמרתי עם זה)(?!\w)", 1.2),
]
FUNNY = [(re.compile(p, re.I), w) for p, w in FUNNY]


def funny_curve(transcript, duration, hop=0.5):
    """Banter / laughing / roasting / storytelling density over time."""
    n = int(duration / hop) + 1
    curve = np.zeros(n, dtype=np.float32)
    words = (transcript or {}).get("words", [])
    if not words:
        return curve
    for i in range(len(words)):
        chunk = " ".join(w["text"] for w in words[i:i + 5])
        idx = int(words[i]["start"] / hop)
        if idx >= n:
            break
        for rx, wgt in FUNNY:
            if rx.match(chunk):
                curve[idx] += wgt
                break
    # back-and-forth conversation: lots of questions and short replies in a short time
    q = np.zeros(n, dtype=np.float32)
    for g in (transcript or {}).get("segments", []):
        i = int(g["start"] / hop)
        if i < n:
            txt = g["text"]
            q[i] += 0.5 * txt.count("?") + (0.3 if len(txt.split()) <= 4 else 0.0)
    k = max(1, int(20 / hop))
    qd = np.convolve(q, np.ones(k, dtype=np.float32), mode="same")
    base = float(np.median(qd)) + 1e-3
    curve += np.clip((qd - base * 2.0) / 3.0, 0, 2.0) * np.convolve(
        (curve > 0).astype(np.float32), np.ones(k, dtype=np.float32), mode="same").clip(0, 1)
    k = max(1, int(6 / hop))  # banter plays out over several seconds
    return np.convolve(curve, np.ones(k, dtype=np.float32) / k * 2.0, mode="same")


def clips_curve(viewer_clips, duration, hop=0.5):
    """Viewer-made Twitch clips: the strongest 'humans already liked this' signal.
    Clips are made after the fact, so the moment is usually in the last part of the clip."""
    n = int(duration / hop) + 1
    curve = np.zeros(n, dtype=np.float32)
    for c in viewer_clips or []:
        a, d = float(c["offset"]), float(c.get("duration") or 30)
        w = 1.0 + math.log10(1 + int(c.get("views") or 0))
        i0, i1 = int(a / hop), int((a + d) / hop)
        for i in range(max(0, i0), min(n, i1 + 1)):
            pos = (i - i0) / max(1, i1 - i0)          # 0 at clip start, 1 at end
            curve[i] += w * (0.5 + 0.5 * pos)
    return curve


def ai_curve(moments, duration, hop=0.5):
    n = int(duration / hop) + 1
    curve = np.zeros(n, dtype=np.float32)
    for m in moments or []:
        pk = float(m.get("peak", (m["start"] + m["end"]) / 2))
        w = max(0.0, float(m.get("score", 60)) - 40) / 15.0   # 60 -> 1.3, 90 -> 3.3
        for i in range(max(0, int(m["start"] / hop)), min(n, int(m["end"] / hop) + 1)):
            curve[i] = max(curve[i], w * (0.6 + 0.4 * math.exp(-abs(i * hop - pk) / 6.0)))
    return curve


def _norm(z):
    if z is None or len(z) == 0:
        return z
    z = np.asarray(z, dtype=np.float32)
    s = float(np.std(z)) or 1.0
    return (z - float(np.median(z))) / s


SPARSE = ("clips", "ai")   # mostly-zero signals: scale by their own max, not by std


def combine(n, audio_z=None, chat_z=None, text_c=None, weights=None, extra=None):
    weights = weights or {"chat": 0.45, "audio": 0.30, "text": 0.25}
    total = np.zeros(n, dtype=np.float32)
    used = 0.0
    parts = {}
    arrays = [("audio", audio_z), ("chat", chat_z), ("text", text_c)] + list((extra or {}).items())
    for key, arr in arrays:
        if arr is None or len(arr) == 0 or not np.any(arr) or not weights.get(key):
            continue
        a = np.zeros(n, dtype=np.float32)
        m = min(n, len(arr))
        if key in SPARSE:
            a[:m] = np.asarray(arr[:m], dtype=np.float32) / (float(np.max(arr)) + 1e-6) * 6.0
        else:
            a[:m] = _norm(arr[:m])
        a = np.clip(a, -2, 10)
        parts[key] = a
        total += weights[key] * a
        used += weights[key]
    if used > 0:
        total /= used
    # smooth ~3 s so one blip doesn't win
    k = 6
    total = np.convolve(total, np.ones(k, dtype=np.float32) / k, mode="same")
    return total, parts


# ---------- stand-out scoring (2.22): how much a moment beats the minutes around it ----------
# On a lively stream every signal is "high" most of the time, so raw levels pick near-random spots.
# What makes a clip is a moment that is louder / funnier / busier in chat than the few minutes around it.
# 2.22 sweep on 26 library clips (72 settings): 20 s moment, 15 min around, strong agree bonus was best
CONTRAST = {"moment": 20.0, "around": 900.0, "agree": 0.8, "agree_at": 1.5}


def local_contrast(x, hop, moment=None, around=None):
    """Robust z-score of a signal against its own neighbourhood: (moment level - local median) / local spread.
    `moment` s = how long a moment's level is averaged over, `around` s = the stretch of stream it's compared to."""
    moment = CONTRAST["moment"] if moment is None else moment
    around = CONTRAST["around"] if around is None else around
    x = np.asarray(x, dtype=np.float32)
    n = len(x)
    if n < 4:
        return np.zeros(n, dtype=np.float32)
    k = max(1, int(moment / hop))
    m = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    step = max(1, int(2.0 / hop))                     # baseline on a 2 s grid: fast enough for 6 h streams
    ds = m[::step]
    half = max(2, int(around / 2 / (step * hop)))
    half = min(half, max(2, len(ds) - 1))
    pad = np.pad(ds, half, mode="reflect")
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * half + 1)
    base = np.median(win, axis=1)
    mad = np.median(np.abs(win - base[:, None]), axis=1) * 1.4826
    floor = 0.5 * float(np.std(ds)) + 1e-6           # quiet stretches: don't blow up tiny wiggles
    spread = np.maximum(mad, floor)
    c = (ds - base) / spread
    return np.interp(np.arange(n), np.arange(len(ds)) * step, c).astype(np.float32)


def combine_contrast(n, signals, weights, hop):
    """Like `combine`, but every continuous signal is first turned into its stand-out score, and moments where
    several signals stand out together get a bonus. `signals`: {"audio", "chat", "text", "funny", "clips", "ai"}."""
    total = np.zeros(n, dtype=np.float32)
    agree = np.zeros(n, dtype=np.float32)
    used = 0.0
    parts = {}
    for key, arr in signals.items():
        if arr is None or len(arr) == 0 or not np.any(arr) or not weights.get(key):
            continue
        a = np.zeros(n, dtype=np.float32)
        m = min(n, len(arr))
        if key in SPARSE:
            a[:m] = np.asarray(arr[:m], dtype=np.float32) / (float(np.max(arr)) + 1e-6) * 6.0
        else:
            a[:m] = local_contrast(arr[:m], hop)
            agree += (a > CONTRAST["agree_at"]).astype(np.float32)
        a = np.clip(a, -2, 10)
        parts[key] = a
        total += weights[key] * a
        used += weights[key]
    if used > 0:
        total /= used
    total += CONTRAST["agree"] * np.maximum(0.0, agree - 1.0)   # 2+ signals standing out at once
    k = 6
    total = np.convolve(total, np.ones(k, dtype=np.float32) / k, mode="same")
    return total, parts


def pick_peaks(score, hop, count=10, min_gap=45.0, ignore_start=20.0, ignore_end=10.0):
    s = score.copy()
    n = len(s)
    s[: int(ignore_start / hop)] = -1e9
    if ignore_end > 0:
        s[max(0, n - int(ignore_end / hop)):] = -1e9
    peaks = []
    gap = int(min_gap / hop)
    for _ in range(count * 3):
        i = int(np.argmax(s))
        if s[i] <= 0.4:  # nothing interesting left
            break
        peaks.append(i)
        s[max(0, i - gap): i + gap] = -1e9
        if len(peaks) >= count * 2:
            break
    return peaks


def _segment_bounds(transcript):
    segs = transcript.get("segments", []) if transcript else []
    return [s["start"] for s in segs], [s["end"] for s in segs]


def choose_window(peak_t, transcript, duration, min_len=15, max_len=45,
                  target=30, pre_ratio=0.6):
    """Build the clip around the peak: enough setup before, payoff after,
    and snap to sentence boundaries so we never cut mid-word."""
    pre = target * pre_ratio
    post = target - pre
    start = peak_t - pre
    end = peak_t + post
    starts, ends = _segment_bounds(transcript)
    if starts:
        cands = [s for s in starts if peak_t - pre * 1.35 <= s <= peak_t - 4]
        if cands:
            start = min(cands, key=lambda s: abs(s - (peak_t - pre)))
        cands = [e for e in ends if peak_t + 2.5 <= e <= peak_t + post * 1.6]
        if cands:
            end = min(cands, key=lambda e: abs(e - (peak_t + post)))
    if end - start < min_len:
        extra = min_len - (end - start)
        start -= extra * 0.6
        end += extra * 0.4
    if end - start > max_len:
        over = (end - start) - max_len
        start += over * 0.6
        end -= over * 0.4
    start = max(0.0, start - 0.25)
    end = min(duration, end + 0.4)
    return round(start, 2), round(end, 2)


# ---------- story awareness: never end a clip before the payoff ----------
SUSPENSE = re.compile(
    r"\b(three,? two,? one|3,? 2,? 1|two,? one|here we go|we go|please,? please|come on,? come on|"
    r"left or right|let'?s see|moment of truth|upgrade|upgrader|spin|opening|open it|roll|"
    r"\d+ ?(%|percent)|all in|double or nothing|last round|clutch|1v\d|one v)\b|"
    # Klipon: Hebrew build-up
    r"(?:[והשבלכמ]{0,2})(שלוש,? שתיים,? אחת|שלוש,? שתיים|יאללה בוא נראה|בוא נראה|בואו נראה|רגע האמת|קדימה קדימה|"
    r"בבקשה,? בבקשה|נו,? נו|ימינה או שמאלה|פותח|פותחים|אני פותח|הכל או כלום|כפול או כלום|סיבוב אחרון|ראונד אחרון|"
    r"אחד על|אחד נגד|הימור|מהמר|מהמרים|מסובב|סיבוב)", re.I)
OUTCOME = re.compile(
    r"\b(no way|what the|oh my (god|days)|let'?s go+|yes+|no+o|i won|we won|i lost|lost it|"
    r"bullshit|fuck|shit|are you (kidding|serious)|unlucky|lucky|insane|holy|brother|bro+)\b|"
    # Klipon: Hebrew outcomes
    r"(?:[והשבלכמ]{0,2})(אין מצב|מה זה|יאללה|כן+(?!\w)|לא+ ?לא+|זכיתי|זכינו|ניצחנו|ניצחתי|הפסדתי|הפסדנו|"
    r"איזה מזל|איזה באסה|באסה|וואו+|פאק|שיט|זין|כוס ?אמק|אתה רציני|מטורף|פסיכי|אחי+(?!\w))", re.I)


def music_times(transcript):
    """Words that are probably a song playing on stream: the same 3-word phrase repeated
    3+ times within 20 s. Returns list of (start, end) spans."""
    words = (transcript or {}).get("words", [])
    toks = [re.sub(r"[^\w']", "", w["text"].lower()) for w in words]
    spans = []
    N = 4
    for i in range(len(words) - N + 1):
        gram = " ".join(toks[i:i + N])
        if len(gram) < 10:
            continue
        hits = [j for j in range(i, min(len(words) - N + 1, i + 80))
                if words[j]["start"] - words[i]["start"] <= 20 and " ".join(toks[j:j + N]) == gram]
        if len(hits) >= 3:
            spans.append((words[i]["start"], words[hits[-1] + N - 1]["end"]))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


_CJK = "\u3000-\u303F\u3040-\u30FF\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF"
_CYR = "\u0400-\u052F"
_ARAB = "\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u200C\u200D"
_DEVA = "\u0900-\u097F"
_HEB = "\u0590-\u05FF\uFB1D-\uFB4F"
# The stream language's own script (from Whisper's language code). Words in it are kept;
# words in any OTHER non-Latin script are still dropped (songs / other languages).
LANG_SCRIPT = {
    "ja": _CJK, "zh": _CJK, "yue": _CJK, "ko": "\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF\u3000-\u303F\uFF00-\uFFEF",
    "ru": _CYR, "uk": _CYR, "be": _CYR, "bg": _CYR, "sr": _CYR, "mk": _CYR, "kk": _CYR, "mn": _CYR,
    "tt": _CYR, "ba": _CYR, "tg": _CYR, "el": "\u0370-\u03FF\u1F00-\u1FFF",
    "ar": _ARAB, "fa": _ARAB, "ur": _ARAB, "ps": _ARAB, "sd": _ARAB, "he": _HEB, "yi": _HEB,
    "th": "\u0E00-\u0E7F", "lo": "\u0E80-\u0EFF", "km": "\u1780-\u17FF", "my": "\u1000-\u109F",
    "hi": _DEVA, "mr": _DEVA, "ne": _DEVA, "sa": _DEVA, "bn": "\u0980-\u09FF", "as": "\u0980-\u09FF",
    "pa": "\u0A00-\u0A7F", "gu": "\u0A80-\u0AFF", "ta": "\u0B80-\u0BFF", "te": "\u0C00-\u0C7F",
    "kn": "\u0C80-\u0CFF", "ml": "\u0D00-\u0D7F", "si": "\u0D80-\u0DFF", "ka": "\u10A0-\u10FF",
    "hy": "\u0530-\u058F", "am": "\u1200-\u139F", "bo": "\u0F00-\u0FFF", "vi": "\u0300-\u036F\u1E00-\u1EFF",
}


def _off_script(*languages):
    """Regex that finds a character outside Latin AND outside the scripts of the stream's languages."""
    own = "".join(LANG_SCRIPT.get((x or "").lower().replace("_", "-").split("-")[0], "") for x in languages)
    return re.compile("[^\x00-\u024F\u2019*" + own + "]")


def in_language(text, language):
    """0.1.2: True when every letter of `text` is written in the language's own script (Latin letters for
    English, Spanish, ...; kana/kanji for Japanese; Arabic letters for Arabic ...). Digits and punctuation
    always pass. Used so subtitles in a picked language never show a word in another language's letters."""
    code = (language or "").lower().replace("_", "-").split("-")[0]
    own = LANG_SCRIPT.get(code)
    latin = not own or code == "vi"   # Vietnamese is Latin letters with extra accents
    rx = re.compile("[" + own + "]") if own else None
    for ch in text:
        if not ch.isalpha():
            continue
        if rx and rx.match(ch):
            continue
        if latin and ch <= "\u024F":
            continue
        return False
    return True


_LONG_RUN = re.compile(r"(.)\1{6,}")


def strip_music(transcript):
    """Remove song lyrics (music playing on stream) from the transcript so they never
    become subtitles, titles or 'hype words'."""
    if not transcript or transcript.get("_music_stripped"):
        return transcript
    spans = music_times(transcript)
    off = _off_script(transcript.get("language"), *(transcript.get("languages") or []))
    # also drop words Whisper wasn't sure about (mumbles, songs, other languages)
    def keep(w):
        if w.get("p", 1.0) < 0.2:
            return False
        if off.search(w["text"]):   # neither Latin nor the stream's own script
            return False
        return not _near((w["start"] + w["end"]) / 2, spans, 0.1)
    words = [w for w in transcript.get("words", []) if keep(w)]
    # a laugh written out as hundreds of the same letter ("hhhh...", Arabic "هههه...") filled a whole subtitle line
    words = [dict(w, text=_LONG_RUN.sub(lambda m: m.group(1) * 6, w["text"])) if _LONG_RUN.search(w["text"]) else w
             for w in words]
    segs = []
    for g in transcript.get("segments", []):
        inside = sum(max(0, min(g["end"], b) - max(g["start"], a)) for a, b in spans)
        if inside < 0.5 * max(0.1, g["end"] - g["start"]):
            segs.append(g)
    # every word that isn't a song (even unsure ones): used to find where talking starts/stops
    speech = [w for w in transcript.get("words", []) if not _near((w["start"] + w["end"]) / 2, spans, 0.1)]
    out = dict(transcript, words=words, segments=segs, music=spans, speech_words=speech, _music_stripped=True)
    return out


def _near(t, spans, pad=0.3):
    return any(a - pad <= t <= b + pad for a, b in spans)


def _cues(transcript, t0, t1, rx):
    words = [w for w in (transcript or {}).get("words", []) if t0 <= w["start"] <= t1]
    out = []
    for i in range(len(words)):
        chunk = " ".join(w["text"] for w in words[i:i + 4])
        if rx.match(chunk):
            out.append(words[i]["start"])
    return out


def extend_for_story(st, en, peak, transcript, audio_z, hop, duration, max_len=58.0):
    """If the clip builds up to something (countdown, upgrade, 'here we go', 'please please'),
    start it at the build-up and run it until the outcome and the reaction have played out."""
    music = music_times(transcript)
    # backwards: the peak is a payoff of an earlier build-up -> start at the build-up
    before = [t for t in _cues(transcript, peak - 32, peak - 2, SUSPENSE) if not _near(t, music)]
    if before and min(before) - 5 < st:
        target = max(min(before) - 5, en - max_len)
        starts = [g["start"] for g in transcript.get("segments", []) if target - 3 <= g["start"] <= target + 3]
        st = round(min(starts, key=lambda x: abs(x - target)) if starts else target, 2)
    sus = _cues(transcript, st, en + 12, SUSPENSE)
    if not sus:
        return st, en, peak
    last_sus = max(sus)
    look_end = min(duration, last_sus + 30)
    # outcome = strongest reaction after the last build-up cue
    outs = [t for t in _cues(transcript, last_sus + 0.5, look_end, OUTCOME) if not _near(t, music)]
    best_t = None
    for t in outs:  # first moment with at least two reactions close together
        if sum(1 for u in outs if t <= u <= t + 6) >= 2:
            best_t = t
            break
    if best_t is None and outs:
        best_t = outs[0]
    if best_t is None and audio_z is not None:
        i0, i1 = int((last_sus + 0.5) / hop), int(look_end / hop)
        seg = audio_z[i0:i1]
        if len(seg) and float(seg.max()) >= 2.5:
            best_t = (i0 + int(seg.argmax())) * hop
    if best_t is None or best_t <= en - 4:
        return st, en, peak
    # aftermath: keep the reaction going until a real pause, max 10 s
    words = [w for w in transcript.get("words", []) if best_t <= w["start"] <= best_t + 12]
    end = best_t + 3.0
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] > 2.0:
            end = max(end, a["end"] + 0.5)
            break
        end = max(end, b["end"] + 0.4)
    end = min(end, best_t + 10.0, duration)
    new_en = max(en, end)
    new_st = st
    if new_en - new_st > max_len:
        # start closer to the build-up, snapped to a sentence start
        target = max(st, min(sus) - 6, new_en - max_len)
        starts = [g["start"] for g in transcript.get("segments", []) if target - 4 <= g["start"] <= target + 4]
        new_st = min(starts, key=lambda x: abs(x - target)) if starts else target
    return round(new_st, 2), round(new_en, 2), round(best_t, 2)


# ---------- 2.32: wait for the payoff (every kind of moment, also AI-picked ones) ----------
# "About to" lines per situation: when one of these is near the end of a clip, the thing it
# leads up to (the taste, the case/upgrade result, the attempt, the punchline) must be in it.
ABOUT_TO = {
    "taste": r"\b(let me (see|try|taste|eat)|let'?s (try|taste|eat|see)|first (reaction|bite|taste|try)|here goes|"
             r"i'?m (gonna|going to) (go|try|eat|bite|taste)|try (it|this|one|that)|(one|a|first) bite|taste test|"
             r"smells? like|moment of truth|down the hatch|cheers)\b",
    "gamble": r"\b(upgrade|upgrader|spin|roll|open(ing)? (it|this|the|a|one)|let'?s open|case|battle|coinflip|"
              r"all in|double or nothing|\d+ ?(%|percent)|three,? two,? one|3,? 2,? 1|come on,? come on|please,? please|"
              r"let'?s (go for it|do it|gamble|click)|last (one|try|chance))\b",
    "attempt": r"\b(watch this|watch (me|him)|let'?s see if|one try|first try|try again|here we go|last round|clutch|"
               r"1v\d|one v (one|two|three|four|five)|wait for it|hold on,? hold on)\b",
    "story": r"\b(and then|so then|guess what|you know what (he|she|they) (did|said)|long story short|"
             r"turns out|plot twist|the best part)\b",
}
ABOUT_TO = {k: re.compile(v, re.I) for k, v in ABOUT_TO.items()}
PAYOFF = re.compile(
    r"\b(no way|what the|oh my (god|days|gosh)|let'?s go+|ye+s+|no+o+|i won|we won|i lost|lost it|"
    r"bullshit|fuck(ing)?|shit|are you (kidding|serious)|unlucky|lucky|insane|holy|wo+w|who+a|mm+|hmm+|ew+|"
    r"eww+|yuck|gross|disgusting|nasty|delicious|tasty|sour|sweet|bitter|salty|spicy|good|bad|"
    r"what\?|ha(ha)+|lmao|i hate|i love|it works|it worked|boom|told you|gotcha)\b", re.I)


def _tokens(text):
    return {t for t in re.findall(r"[a-z']+", (text or "").lower()) if len(t) >= 4}


def extend_to_payoff(st, en, transcript, audio_z, hop, duration, title="", max_len=58.0, look=25.0):
    """Never end a clip on the build-up. If the last ~8 s lead up to something ("let me try it",
    "first reaction", an upgrade, "watch this", "and then...") or the clip's own title line is
    said just after the end, run the clip on until the payoff and the reaction have played out.
    Returns (start, end, why) with why = "" when nothing changed."""
    words = (transcript or {}).get("words", [])
    if not words:
        return st, en, ""
    music = music_times(transcript)
    inside = [w for w in words if st <= w["start"] < en]
    after = [w for w in words if en <= w["start"] < min(duration, en + look) and not _near(w["start"], music)]
    if not inside or not after:
        return st, en, ""
    why, cue_t = "", None
    tail = [w for w in inside if w["start"] >= en - 8.0]
    for i in range(len(tail)):
        chunk = " ".join(w["text"] for w in tail[i:i + 5])
        for kind, rx in ABOUT_TO.items():
            if rx.search(chunk):
                # the payoff already happened inside the clip after the cue: fine as it is
                later = [w for w in inside if w["start"] > tail[i]["start"] + 0.8]
                if any(PAYOFF.search(w["text"]) for w in later):
                    continue
                why, cue_t = kind, tail[i]["start"]
        # (keep looking: the last cue wins)
    # the title line (often the punchline) is said right after the clip ends
    tw = _tokens(title) - {"bro", "this", "that", "what", "with", "they", "them", "when", "after"}
    title_end = None
    if len(tw) >= 2:
        for i in range(len(after)):
            win = _tokens(" ".join(w["text"] for w in after[i:i + 10]))
            if len(tw & win) >= max(2, int(0.6 * len(tw))) and not len(tw & _tokens(" ".join(w["text"] for w in inside))) >= len(tw & win):
                # run to the end of the sentence that says it (not further)
                k = i
                while k < len(after) - 1 and not after[k]["text"].rstrip().endswith((".", "?", "!"))                         and k < i + 14 and after[k + 1]["start"] - after[k]["end"] < 1.2:
                    k += 1
                title_end = after[k]["end"]
                why = why or "title"
                break
    if not why:
        return st, en, ""
    # payoff = first reaction after the end (or after the cue), else a loud moment
    pay = None
    reach = en + (20.0 if why == "taste" else 12.0)   # the payoff must come soon, or it's another moment
    for w in after:
        if w["start"] > reach:
            break
        if (PAYOFF.search(w["text"]) or LAUGH_W.match(w["text"])
                or (w["text"].isupper() and len(re.sub(r"\W", "", w["text"])) > 2)):
            pay = w["start"]
            break
    if why == "title":
        pay = None   # the title line itself is the payoff
    if pay is None and audio_z is not None and why != "title":
        i0, i1 = int(en / hop), int(min(duration, reach) / hop)
        seg = audio_z[i0:i1]
        if len(seg) and float(seg.max()) >= 2.0:
            pay = (i0 + int(seg.argmax())) * hop
    if pay is None and title_end is None:
        return st, en, ""
    new_en = en
    if pay is not None:
        # aftermath: keep the reaction until a real pause, at most 8 s
        # aftermath: the reaction sentence plus a little more, stopping at a real pause or at
        # the first sentence end past ~2.5 s (so it doesn't run into the next topic), max 6 s
        # 2.32: let the reaction run (the creator: "run a little instead of cutting instantly"); a taste
        # reaction is slow (chew, pull a face, then the verdict), so it gets the most room
        least, most, pause = AFTERMATH.get(why, (3.0, 7.0, 1.8))
        new_en = pay + least
        ws = [w for w in after if w["start"] >= pay]
        for w, nx in zip(ws, ws[1:] + [None]):
            new_en = max(new_en, w["end"] + 0.4)
            if nx is None or nx["start"] - w["end"] > pause or nx["start"] > pay + most:
                break
            if w["end"] >= pay + least and w["text"].rstrip().endswith((".", "?", "!")):
                break
        new_en = min(new_en, pay + most)
    if title_end is not None:
        new_en = max(new_en, title_end + 0.6)
    new_en = min(duration, new_en, st + max_len)
    if new_en <= en + 0.5:
        return st, en, ""
    return st, round(new_en, 2), why


# seconds after the payoff: (at least, at most, a pause this long ends it)
AFTERMATH = {"taste": (4.5, 10.0, 2.5), "gamble": (3.5, 8.0, 2.0), "attempt": (3.0, 7.0, 1.8), "story": (2.5, 6.0, 1.5)}

LAUGH_W = re.compile(r"^(ha(ha)+|he(he)+|lmao+|lol+)[.!?,]*$", re.I)


def words_in(transcript, start, end):
    return [w for w in (transcript or {}).get("words", []) if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]


def _reasons(parts, i0, i1):
    out = []
    labels = {"chat": "Chat spike", "audio": "Loud reaction", "text": "Hype words",
              "funny": "Funny / banter", "clips": "Viewers clipped this", "ai": "AI: strong moment"}
    for k in ("clips", "ai", "funny", "chat", "audio", "text"):
        if k in parts:
            v = float(parts[k][i0:i1].max()) if i1 > i0 else 0
            if v > 1.5:
                out.append((v, labels[k]))
    out.sort(reverse=True)
    return [o[1] for o in out] or ["Strong overall moment"]


def _heuristic_hook(text, reasons):
    t = text.lower()
    for rx, label in ((r"no way", "No way this happened"), (r"clip", "Chat clipped this"),
                      (r"let'?s go", "Wait for the end"), (r"oh my god", "I was not ready"),
                      (r"ha(ha)+|lol|dead", "I couldn't stop laughing"),
                      (r"jump ?scare|scream|run", "Do not look away")):
        if re.search(rx, t):
            return label
    if "Chat spike" in reasons:
        return "Chat went crazy"
    return "Wait for it"


def _title_from(transcript, start, end, peak):
    music = (transcript or {}).get("music") or []
    segs = [s for s in (transcript or {}).get("segments", []) if s["end"] > start and s["start"] < end
            and not any(min(s["end"], b) - max(s["start"], a) > 0.3 for a, b in music)]
    if not segs:
        return "Stream moment"

    def rank(s):
        hits = sum(1 for rx, _ in SPOKEN if rx.search(s["text"])) + 1.5 * sum(1 for rx, _ in FUNNY if rx.search(s["text"]))
        if len(s["text"].split()) < 3:
            hits -= 2
        near = -abs((s["start"] + s["end"]) / 2 - peak)
        return (hits, near)
    best = max(segs, key=rank)["text"]
    best = re.sub(r"\s+", " ", best).strip(" .")
    words = best.split()
    best = " ".join(words[:6]) if len(words) > 6 else best
    return best[:1].upper() + best[1:]


def snap_to_speech(st, en, transcript, duration, max_back=12.0, max_fwd=6.0):
    """Start at the beginning of a sentence and end at the end of one, so a TikTok never
    opens or stops mid-sentence. A sentence starts after . ? ! , at a Whisper segment start,
    or after a long pause (word timings alone aren't reliable enough)."""
    words = (transcript or {}).get("speech_words") or (transcript or {}).get("words", [])
    if not words:
        return st, en
    import bisect
    seg_starts = {round(g["start"], 2) for g in (transcript or {}).get("segments", [])}
    starts = [w["start"] for w in words]

    def begins(i):
        if i == 0:
            return True
        prev = words[i - 1]
        return (prev["text"].rstrip().endswith((".", "?", "!")) or round(words[i]["start"], 2) in seg_starts
                or words[i]["start"] - prev["end"] >= 1.5)

    def ends(k):
        if k + 1 >= len(words):
            return True
        return words[k]["text"].rstrip().endswith((".", "?", "!")) or words[k + 1]["start"] - words[k]["end"] >= 1.2

    i = bisect.bisect_left(starts, st)
    if i >= len(words) or words[i]["start"] > en:
        return st, en
    # 1st choice: the start of this stretch of talking (last real pause), so the whole
    # back-and-forth is in; 2nd: the start of the current sentence
    j, best_sentence = i, (i if begins(i) else None)
    while j > 0 and st - words[j - 1]["start"] <= max_back:
        if words[j]["start"] - words[j - 1]["end"] >= 1.2:
            break
        j -= 1
        if best_sentence is None and begins(j):
            best_sentence = j
    if not (j == 0 or words[j]["start"] - words[j - 1]["end"] >= 1.2):
        j = best_sentence if best_sentence is not None else i
    gap = words[j]["start"] - (words[j - 1]["end"] if j > 0 else 1.0)
    st = max(0.0, words[j]["start"] - max(0.1, min(0.8, gap - 0.1)))   # lead-in inside the pause
    k = bisect.bisect_right(starts, en) - 1
    if k >= j:
        k0 = k
        while not ends(k) and k + 1 < len(words) and words[k + 1]["end"] - en <= max_fwd:
            k += 1
        if not ends(k):
            k = k0
        en = min(duration, words[k]["end"] + 0.6)
    return round(st, 2), round(en, 2)


def _strength(v):
    """normalised signal (z-ish, 0..10) -> 0..100 for the moment breakdown bars"""
    return int(round(100 * (1 - math.exp(-max(0.0, v) / 2.2))))


def signal_breakdown(parts, i0, i1):
    out = {}
    for k in ("chat", "audio", "funny", "text", "clips", "ai"):
        if k in parts and i1 > i0:
            out[k] = _strength(float(parts[k][i0:i1].max()))
    return out


def _category(sig, story, ai_type=None):
    if ai_type in ("funny", "banter", "story", "hype", "skill", "fail"):
        return {"skill": "hype", "fail": "funny"}.get(ai_type, ai_type)
    if sig.get("funny", 0) >= 55 and sig.get("funny", 0) >= sig.get("text", 0) - 10:
        return "funny"
    if story:
        return "story"
    return "hype"


def build_candidates(score, parts, hop, transcript, duration, count=10, opts=None, audio_z=None,
                     ai_moments=None, viewer_clips=None):
    opts = opts or {}
    peaks = pick_peaks(score, hop, count=int(count * 1.6) + 2, min_gap=opts.get("min_gap", 45))
    # moments people already picked out (viewer clips) or the AI found are always considered,
    # even when a louder moment sits right next to them
    anchors = list(ai_moments or [])
    for v in viewer_clips or []:
        d = float(v.get("duration") or 30)
        anchors.append({"start": v["offset"], "end": v["offset"] + d, "peak": v["offset"] + 0.75 * d,
                        "type": "viewer", "score": min(95.0, 70 + 8 * math.log10(1 + v.get("views", 0))),
                        "title": v.get("title", ""), "why": f"Viewers clipped this ({v.get('views', 0)} views)"})
    for m in sorted(anchors, key=lambda m: -m.get("score", 0)):
        if m.get("score", 0) >= 65 and all(abs(pi * hop - m["peak"]) > 20 for pi in peaks):
            peaks.append(int(m["peak"] / hop))
    peaks.sort(key=lambda pi: -float(score[min(pi, len(score) - 1)]))
    out = []
    taken = []
    for rank, pi in enumerate(peaks):
        t = pi * hop
        st, en = choose_window(t, transcript, duration,
                               min_len=opts.get("min_len", 15), max_len=opts.get("max_len", 45),
                               target=opts.get("target_len", 30))
        # an AI-found moment here knows where the setup starts and the joke lands: use its bounds
        aim = None
        for m in anchors:
            if m["start"] - 3 <= t <= m["end"] + 3:
                if aim is None or m.get("score", 0) > aim.get("score", 0):
                    aim = m
        if aim:
            a0, a1 = float(aim["start"]), float(aim["end"])
            if a1 - a0 < opts.get("min_len", 15):
                pad = (opts.get("min_len", 15) - (a1 - a0)) / 2
                a0, a1 = a0 - pad, a1 + pad
            st, en = max(0.0, round(a0 - 0.3, 2)), min(duration, round(a1 + 0.6, 2))
            mx = float(opts.get("max_len", 45))
            if en - st > mx:  # too long for TikTok: keep the part around the punchline
                pk = float(aim.get("peak", (a0 + a1) / 2))
                st = max(st, min(pk - 0.6 * mx, en - mx))
                en = min(en, st + mx)
        story = False
        if not (aim and aim.get("type") != "viewer") and ("audio" in parts or audio_z is not None):
            # the AI already chose where the setup starts and the payoff ends
            st2, en2, t2 = extend_for_story(st, en, t, transcript, audio_z, hop, duration,
                                            max_len=float(opts.get("story_max_len", 58)))
            story = (st2, en2) != (st, en)
            st, en, t = st2, en2, t2
        st, en = snap_to_speech(st, en, transcript, duration,
                                max_back=4.0 if aim else 12.0, max_fwd=3.0 if aim else 6.0)
        hard = float(opts.get("max_len", 45)) * 1.2
        if en - st > hard:
            en = st + hard
            st, en = snap_to_speech(st, en, transcript, duration, max_back=0.0, max_fwd=0.0)
        clash = False
        for a, b in taken:  # touching a neighbour by a second or two: just trim, don't drop
            ov = min(en, b) - max(st, a)
            if ov <= 0:
                continue
            if ov <= 2.5 and st >= a:
                st = round(b, 2)
            elif ov <= 2.5:
                en = round(a, 2)
            else:
                clash = True
        if clash or en - st < 8:
            continue
        taken.append((st, en))
        i0, i1 = int(st / hop), int(en / hop)
        ws = words_in(transcript, st, en)
        text = " ".join(w["text"] for w in ws)
        reasons = _reasons(parts, i0, i1)
        raw = float(score[pi])
        # chaos = several big reactions packed together (your best-performing clips)
        if "audio" in parts:
            a_seg = parts["audio"][i0:i1]
            peaks_n = int((a_seg > 2.0).sum())
            raw *= 1.0 + min(0.35, peaks_n * 0.03)
        if "text" in parts:
            raw *= 1.0 + min(0.2, float((parts["text"][i0:i1] > 1.5).sum()) * 0.02)
        if "funny" in parts:  # viewers clip funny moments most on this channel
            raw *= 1.0 + min(0.3, float((parts["funny"][i0:i1] > 1.5).sum()) * 0.015)
        # streamer clips need talking: penalise windows that are mostly silence or music
        talk = sum(min(w["end"], en) - max(w["start"], st) for w in ws) / max(1.0, en - st)
        if talk < 0.12 and len(peaks) > count and not aim:
            continue
        if talk < 0.25:
            raw *= 0.6 + talk * 1.6
        sc = raw  # turned into 0-99 once all moments are known
        if aim:
            sc = ("ai", raw, float(aim.get("score", 60)))
        sig = signal_breakdown(parts, i0, i1)
        vc = [c for c in viewer_clips or [] if c["offset"] < en and c["offset"] + c.get("duration", 30) > st]
        c = {
            "id": f"c{len(out) + 1}",
            "peak": round(t, 2), "start": st, "end": en,
            "start_label": fmt_time(st), "len": round(en - st, 1),
            "score": sc,
            "reasons": reasons, "why": " + ".join(reasons),
            "text": text,
            "title": _title_from(transcript, st, en, t),
            "hook": _heuristic_hook(text, reasons),
            "caption": "",
            "signals": sig,
            "category": _category(sig, story, (aim or {}).get("type")),
        }
        if vc:
            c["viewer_clips"] = [{"title": v["title"], "views": v["views"], "slug": v.get("slug")} for v in vc]
            c["why"] = f"{len(vc)} viewer clip{'s' if len(vc) > 1 else ''} + " + c["why"]
        if aim:
            c["ai_why"] = aim.get("why", "")
            if aim.get("title") and (aim.get("type") != "viewer" or len(aim["title"]) >= 4):
                c["title"] = c["hook"] = aim["title"]
        out.append(c)
        if len(out) >= count:
            break
    # relative 0-99 score: the best moment of the stream ~95, weak ones ~50
    raws = [c["score"][1] if isinstance(c["score"], tuple) else c["score"] for c in out]
    mx = max(raws) if raws else 1.0
    for c, r in zip(out, raws):
        base = 45 + 50 * (max(0.0, r) / (mx + 1e-6)) ** 1.3
        if ai_moments and not isinstance(c["score"], tuple):
            base *= 0.85   # the AI read the whole stream and didn't flag this one
        if isinstance(c["score"], tuple):
            # the AI understood the joke; the signals only nudge it
            base = 0.25 * base + 0.75 * min(99.0, 15 + c["score"][2] * 1.05)
        c["score"] = int(max(1, min(99, round(base))))
    out.sort(key=lambda c: -c["score"])
    for i, c in enumerate(out):
        c["id"] = f"c{i + 1}"
    return out
