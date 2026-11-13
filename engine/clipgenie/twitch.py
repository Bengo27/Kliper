"""Twitch VOD links: find clips without downloading the whole stream.

- Metadata + stream URLs via yt-dlp.
- Audio-only rendition streamed straight into a 16 kHz WAV (a few hundred MB, not GBs).
- Chat replay fetched automatically from Twitch.
- Low-res frames for webcam detection.
- Only the chosen clips are downloaded in full quality.
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

from .util import ffmpeg, run, data_dir, log, CREATE_NO_WINDOW, work_threads

VOD_RX = re.compile(r"twitch\.tv/(?:[\w-]+/)?(?:videos?|v)/(\d+)", re.I)
GQL = "https://gql.twitch.tv/gql"
GQL_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"
COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
URL_TTL = 3600  # re-resolve stream URLs after an hour (Twitch tokens expire)
_SECTION_LOCK = threading.Lock()


CLIP_RX = re.compile(r"(?:clips\.twitch\.tv/(?:embed\?clip=)?|twitch\.tv/[\w-]+/clip/)([\w-]+)", re.I)
# 0.1.2: YouTube and Kick too (yt-dlp reads both). Their ids get a "yt-" / "kick-" prefix inside Clip Genie.
YT_RX = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|live/|embed/|v/)|youtu\.be/)([\w-]{11})", re.I)
YT_SHORT_RX = re.compile(r"youtube\.com/shorts/([\w-]{11})", re.I)
KICK_VOD_RX = re.compile(r"kick\.com/[\w-]+/videos/([\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12})", re.I)
KICK_CLIP_RX = re.compile(r"kick\.com/[\w-]+(?:/clips/|/?\?(?:[^#]+&)?clip=)(clip_[\w-]+)", re.I)


def clip_slug(s):
    """The clip id of a clip link, else None: a Twitch clip (clips.twitch.tv/... or twitch.tv/<name>/clip/...),
    a YouTube Short ("yt-<id>") or a Kick clip ("kick-clip_...")."""
    s = (s or "").strip()
    m = CLIP_RX.search(s)
    if m:
        return m.group(1)
    m = YT_SHORT_RX.search(s)
    if m:
        return "yt-" + m.group(1)
    m = KICK_CLIP_RX.search(s)
    return "kick-" + m.group(1) if m else None


def platform(vid):
    """'twitch', 'youtube' or 'kick' for an id from vod_id / clip_slug."""
    vid = str(vid or "")
    return "youtube" if vid.startswith("yt-") else "kick" if vid.startswith("kick-") else "twitch"


def stream_name(vid):
    """The library key / cache folder name of a VOD: twitch_<id> as before, yt-<id> / kick-<id> for the others."""
    return f"twitch_{vid}" if platform(vid) == "twitch" else str(vid)


def download_clip(url, folder, progress=None):
    """Download a Twitch clip in full quality. Returns (path, info)."""
    import yt_dlp
    slug = clip_slug(url)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "clip.mp4")
    if platform(slug) != "twitch":
        return _download_other_clip(url, slug, out, progress)

    def hook(d):
        if progress and d.get("status") == "downloading" and d.get("total_bytes"):
            progress(min(0.999, d.get("downloaded_bytes", 0) / d["total_bytes"]))
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "outtmpl": out, "overwrites": True,
            "format": "best[ext=mp4]/best", "progress_hooks": [hook]}
    cookies = os.path.join(data_dir(), "twitch_cookies.txt")
    if os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://clips.twitch.tv/{slug}", download=True)
    except Exception as e:
        raise RuntimeError("Couldn't download this Twitch clip (deleted?): " + str(e).splitlines()[-1][:200])
    if not os.path.isfile(out):
        raise RuntimeError("Couldn't download this Twitch clip.")
    # clips get the stream's title by default: drop chat commands like "!skins !farmskins"
    title = re.sub(r"\s+", " ", re.sub(r"(^|\s)![\w-]+", " ", info.get("title") or "")).strip()
    gm = fetch_clip_meta(slug)
    return out, {"title": title or "Twitch clip", "creator": info.get("creator") or info.get("uploader") or "",
                 "game": info.get("game") or gm["game"], "slug": slug,
                 # 2.35: the streamer the clip is FROM (yt-dlp's 'uploader' is whoever clipped it)
                 "channel": gm["channel"] or info.get("channel") or ""}


def _download_other_clip(url, slug, out, progress=None):
    """0.1.2: a YouTube Short or a Kick clip, through yt-dlp. Returns (path, info) like download_clip."""
    import yt_dlp
    from .util import ffmpeg

    def hook(d):
        tot = d.get("total_bytes") or d.get("total_bytes_estimate")
        if progress and d.get("status") == "downloading" and tot:
            progress(min(0.999, d.get("downloaded_bytes", 0) / tot))
    base = os.path.splitext(out)[0]
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "outtmpl": base + ".%(ext)s",
            "overwrites": True, "progress_hooks": [hook], "ffmpeg_location": ffmpeg(),
            "merge_output_format": "mp4", "format": "bv*+ba/b",
            # up to 1080p by the SHORTER side (a vertical Short is 1080x1920), H.264 + AAC first
            "format_sort": ["res:1080", "vcodec:h264", "acodec:aac"]}
    name = "YouTube" if platform(slug) == "youtube" else "Kick"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=True)
    except Exception as e:
        raise RuntimeError(f"Couldn't download this {name} clip (deleted or private?): " + str(e).splitlines()[-1][:200])
    if not os.path.isfile(out):
        raise RuntimeError(f"Couldn't download this {name} clip.")
    title = re.sub(r"\s+", " ", re.sub(r"(^|\s)[!#][\w-]+", " ", info.get("title") or "")).strip()
    if not re.search(r"\w", title):   # Kick clips are often titled "." or just an emoji
        title = ""
    return out, {"title": title or f"{name} clip", "creator": info.get("uploader") or "", "game": "",
                 "slug": slug, "channel": info.get("channel") or info.get("uploader") or ""}


def is_link(s):
    return isinstance(s, str) and s.strip().lower().startswith(
        ("http://", "https://", "www.", "twitch.tv", "youtube.com", "youtu.be", "m.youtube.com", "kick.com"))


def vod_id(url):
    """Twitch: the VOD number. 0.1.2: YouTube "yt-<id>", Kick "kick-<uuid>"."""
    m = VOD_RX.search(url or "")
    if m:
        return m.group(1)
    m = YT_RX.search(url or "")
    if m:
        return "yt-" + m.group(1)
    m = KICK_VOD_RX.search(url or "")
    if m:
        return "kick-" + m.group(1).lower()
    if re.fullmatch(r"\d{6,}", (url or "").strip()):
        return url.strip()
    raise RuntimeError("That doesn't look like a Twitch, YouTube or Kick video link "
                       "(twitch.tv/videos/…, youtube.com/watch?v=…, kick.com/<name>/videos/…)")


RANGE_RX = re.compile(r"[?&]cg_from=(\d+(?:\.\d+)?)&cg_to=(\d+(?:\.\d+)?)")


def vod_range(url):
    """2.35.3: only part of a VOD (the creator: pick the time codes or the whole VOD). The range rides in the link
    (...videos/123?cg_from=3600&cg_to=7200) so every place that reopens the stream gets it. (from, to) or None."""
    m = RANGE_RX.search(url or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (a, b) if b > a else None


def _plain_link(url):
    """A YouTube / Kick link without Clip Genie's range part."""
    u = re.sub(r"[?&]cg_from=[\d.]+&cg_to=[\d.]+", "", (url or "").strip())
    return u if u.startswith("http") else "https://" + u


def range_link(url, a, b):
    vid = vod_id(url)
    if platform(vid) == "twitch":
        return f"https://www.twitch.tv/videos/{vid}?cg_from={int(a)}&cg_to={int(b)}"
    u = _plain_link(url)
    return u + ("&" if "?" in u else "?") + f"cg_from={int(a)}&cg_to={int(b)}"


def cache_dir(vid, rng=None):
    local = os.environ.get("LOCALAPPDATA")
    base = os.path.join(local, "GenieStudioFX", "ClipGenie", "cache") if local else os.path.join(data_dir(), "cache")
    d = os.path.join(base, stream_name(vid) + (f"_r{int(rng[0])}_{int(rng[1])}" if rng else ""))
    os.makedirs(d, exist_ok=True)
    return d


class TwitchVod:
    def __init__(self, url):
        self.id = vod_id(url)
        self.platform = platform(self.id)   # 0.1.2: twitch / youtube / kick
        self.url = f"https://www.twitch.tv/videos/{self.id}" if self.platform == "twitch" else _plain_link(url)
        # 2.35.3: a time range = its own stream: every time inside Clip Genie is counted from `off` (the range's
        # start); only the downloads below add it back
        self.range = vod_range(url)
        self.off = self.range[0] if self.range else 0.0
        self.cache = cache_dir(self.id, self.range)
        self._resolved_at = 0
        self.meta = {}
        self.resolve()

    # ---------- stream URLs ----------
    def resolve(self, force=False):
        if not force and time.time() - self._resolved_at < URL_TTL and self.meta:
            return self.meta
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        cookies = os.path.join(data_dir(), "twitch_cookies.txt")
        if os.path.isfile(cookies):
            opts["cookiefile"] = cookies
        if self.platform != "twitch":
            opts.pop("cookiefile", None)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
        except Exception as e:
            msg = str(e)
            if self.platform == "twitch" and ("sub" in msg.lower() or "login" in msg.lower()):
                raise RuntimeError("This VOD is subscriber-only. Download it and add the video file instead.")
            raise RuntimeError("Couldn't open this video (deleted or private?): " + msg.splitlines()[-1][:200])
        if self.platform == "twitch":
            fmts = [f for f in info.get("formats", []) if f.get("url") and f.get("protocol", "").startswith("m3u8")]
        else:   # YouTube: separate video and sound streams (HLS or plain https); Kick: HLS like Twitch
            fmts = [f for f in info.get("formats", []) if f.get("url")
                    and f.get("protocol", "").startswith(("m3u8", "https", "http"))
                    and not str(f.get("format_id", "")).endswith("-drc")]
        if not fmts:
            raise RuntimeError("No playable streams found for this video.")
        audio = [f for f in fmts if f.get("vcodec") == "none"]
        video = [f for f in fmts if f.get("vcodec") != "none" and f.get("height")]
        if self.platform == "twitch":
            video.sort(key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0))
            best = video[-1] if video else fmts[-1]
            low = next((f for f in video if (f.get("height") or 0) >= 360), best)
        else:
            # up to 1080p, H.264 and HLS first (the easiest for ffmpeg to seek in)
            def rank(f):   # 1080p by the shorter side, so a vertical video isn't cut to 480p
                return (min(f.get("height") or 0, f.get("width") or 10 ** 5) <= 1080, str(f.get("vcodec") or "").startswith("avc"),
                        str(f.get("protocol")).startswith("m3u8"), f.get("height") or 0, f.get("fps") or 0,
                        f.get("tbr") or 0)
            video.sort(key=rank)
            best = video[-1] if video else fmts[-1]
            low = min((f for f in video if (f.get("height") or 0) >= 360 and rank(f)[1] == rank(best)[1]),
                      key=lambda f: (f.get("height") or 0, not str(f.get("protocol")).startswith("m3u8")),
                      default=best)
            audio.sort(key=lambda f: (str(f.get("protocol")).startswith("m3u8"), f.get("abr") or f.get("tbr") or 0))
            audio = audio[::-1]
        sep = (audio[0]["url"] if audio else None)   # YouTube's video streams have no sound of their own
        self.meta = {
            "title": info.get("title") or f"{self.platform.title()} video {self.id}",
            "uploader": info.get("uploader") or "",
            "duration": float(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail"),
            "audio_url": (audio[0] if audio else low)["url"],
            "low_url": low["url"], "low_w": low.get("width") or 640, "low_h": low.get("height") or 360,
            "best_url": best["url"], "width": best.get("width") or 1920, "height": best.get("height") or 1080,
            "fps": float(best.get("fps") or 30),
            "best_audio": sep if best.get("acodec") == "none" else None,
            "low_audio": sep if low.get("acodec") == "none" else None,
            # Twitch chapters = which game was on at each point of the stream
            "chapters": [{"start": float(c.get("start_time") or 0), "end": float(c.get("end_time") or 0),
                          "game": (c.get("title") or "").strip()} for c in (info.get("chapters") or [])
                         if (c.get("title") or "").strip() and (c.get("end_time") or 0) > (c.get("start_time") or 0)],
            "game": "",
        }
        if not self.meta["chapters"] and self.platform == "twitch":
            try:
                self.meta["game"] = fetch_game(self.id)
            except Exception as e:
                log.warning("Couldn't read the stream's game from Twitch: %s", e)
        if self.range:
            a = min(self.off, max(0.0, self.meta["duration"] - 30))
            b = min(self.range[1], self.meta["duration"]) if self.meta["duration"] else self.range[1]
            self.off = a
            self.meta["vod_duration"] = self.meta["duration"]
            self.meta["duration"] = max(1.0, b - a)
            self.meta["title"] += f" ({_hms(a)}-{_hms(b)})"
            ch = [dict(c, start=max(0.0, c["start"] - a), end=min(b, c["end"]) - a) for c in self.meta["chapters"]
                  if c["end"] > a and c["start"] < b]
            self.meta["chapters"] = ch
            if len(ch) == 1:
                self.meta["game"] = ch[0]["game"]
        self._resolved_at = time.time()
        return self.meta

    def info(self):
        m = self.meta
        return {"duration": m["duration"], "width": int(m["width"]), "height": int(m["height"]),
                "fps": m["fps"], "has_audio": True, "title": m["title"], "source": self.platform,
                "vod_id": self.id, "uploader": m["uploader"],
                "chapters": m.get("chapters") or [], "game": m.get("game") or ""}

    def low_info(self):
        return {"duration": self.meta["duration"], "width": int(self.meta["low_w"]),
                "height": int(self.meta["low_h"]), "fps": 30, "has_audio": True}

    # ---------- audio ----------
    def audio_wav(self, progress=None):
        out = os.path.join(self.cache, "audio16k.wav")
        if os.path.isfile(out) and os.path.getsize(out) > 1000 and os.path.isfile(out + ".done"):
            return out
        self.resolve()
        cut = ["-ss", f"{self.off:.3f}", "-t", f"{self.meta['duration']:.3f}"] if self.range else []
        cmd = [ffmpeg(), "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
               *cut, "-i", self.meta["audio_url"], "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out]
        run_with_progress(cmd, self.meta["duration"], progress)
        open(out + ".done", "w").close()
        return out

    # ---------- chat ----------
    def chat(self, progress=None):
        out = os.path.join(self.cache, "chat.json")
        if os.path.isfile(out):
            with open(out, "r", encoding="utf-8") as f:
                return [tuple(x) for x in json.load(f)]
        if self.platform != "twitch":
            return []   # 0.1.2: chat replay is Twitch only for now
        users = []
        msgs = download_chat(self.id, self.meta.get("duration") or 0, progress, users_out=users,
                             start=self.off if self.range else 0.0)
        if self.range:   # 2.35.3: only the range, counted from its start
            keep = [i for i, m in enumerate(msgs) if 0 <= m[0] - self.off <= self.meta["duration"]]
            msgs = [(msgs[i][0] - self.off, msgs[i][1]) for i in keep]
            users = [[u[0] - self.off] + list(u[1:]) for u in users if 0 <= u[0] - self.off <= self.meta["duration"]]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(msgs, f)
        try:   # 2.20: names + colours, same order as chat.json
            with open(os.path.join(self.cache, "chat_users.json"), "w", encoding="utf-8") as f:
                json.dump(users, f)
        except Exception:
            pass
        return msgs

    # ---------- viewer-made clips ----------
    def viewer_clips(self, refresh=False):
        """Clips viewers made from this VOD: [{slug,title,views,offset,duration}].
        Cached for 6 hours (people keep clipping after the stream)."""
        out = os.path.join(self.cache, "viewer_clips.json")
        if not refresh and os.path.isfile(out) and time.time() - os.path.getmtime(out) < 6 * 3600:
            with open(out, "r", encoding="utf-8") as f:
                return json.load(f)
        if self.platform != "twitch":
            return []
        clips = fetch_viewer_clips(self.id)
        if self.range:
            clips = [dict(c, offset=c["offset"] - self.off) for c in clips
                     if 0 <= c["offset"] - self.off <= self.meta["duration"]]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(clips, f)
        return clips

    # ---------- frames / sections ----------
    def _inputs(self, at, which):
        """ffmpeg input options for the `best` or `low` video from `at` seconds, plus YouTube's separate sound."""
        args = ["-ss", f"{at:.3f}", "-i", self.meta[which + "_url"]]
        if self.meta.get(which + "_audio"):
            args += ["-ss", f"{at:.3f}", "-i", self.meta[which + "_audio"], "-map", "0:v:0", "-map", "1:a:0"]
        return args

    def frame(self, t, out, width=960):
        self.resolve()
        run([ffmpeg(), "-y", "-v", "error", "-ss", f"{t + self.off:.2f}", "-i", self.meta["low_url"],
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", out])
        return out

    def section(self, start, end, pad=12.0, progress=None):
        """Full-quality local copy of [start-pad, end+pad]. Returns (path, offset).
        Serialised so a preview and a build never download the same part twice."""
        with _SECTION_LOCK:
            return self._section(start, end, pad, progress)

    def _section(self, start, end, pad, progress):
        s = max(0.0, start - pad)
        e = min(self.meta.get("duration") or end + pad, end + pad)
        folder = os.path.join(self.cache, "sections")
        os.makedirs(folder, exist_ok=True)
        # reuse any downloaded section that already covers this range
        for f in os.listdir(folder):
            m = re.match(r"sec_(\d+)_(\d+)\.mp4$", f)
            if m and int(m.group(1)) / 10 <= start - 1 and int(m.group(2)) / 10 >= end + 1:
                p = os.path.join(folder, f)
                off = self.true_offset(p, int(m.group(1)) / 10)
                # Twitch can start a cut up to a segment (~10 s) early: make sure it still covers the clip
                length = (int(m.group(2)) - int(m.group(1))) / 10
                if off <= start - 1 and off + length >= end + 1:
                    return p, off
        path = os.path.join(folder, f"sec_{int(s * 10)}_{int(e * 10)}.mp4")
        self.resolve()
        tmp = path + f".{os.getpid()}.part.mp4"
        # frame-accurate cut (re-encoded at high quality so the clip starts exactly where we asked)
        def cmd():
            return [ffmpeg(), "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
                    *self._inputs(s + self.off, "best"), "-t", f"{e - s:.3f}",
                    "-threads", str(work_threads()),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", tmp]
        try:
            run_with_progress(cmd(), e - s, progress)
        except RuntimeError:
            # stream token may have expired - refresh once and retry
            self.resolve(force=True)
            run_with_progress(cmd(), e - s, progress)
        os.replace(tmp, path)
        return path, self.true_offset(path, s)

    def proxy(self, start, end):
        """Fast low-quality copy for the timeline preview (full quality only when building).
        Reuses a full-quality section if one already covers the range."""
        folder = os.path.join(self.cache, "sections")
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):
            m = re.match(r"(sec|tl)_(\d+)_(\d+)\.mp4$", f)
            if m:
                p = os.path.join(folder, f)
                off = self.true_offset(p, int(m.group(2)) / 10)
                if off <= start + 0.5 and off + (int(m.group(3)) - int(m.group(2))) / 10 >= end - 0.5:
                    return p, off
        s = max(0.0, start - 4)
        e = min(self.meta.get("duration") or end + 4, end + 12)  # extra: Twitch may start a few s early
        path = os.path.join(folder, f"tl_{int(s * 10)}_{int(e * 10)}.mp4")
        self.resolve()
        tmp = path + f".{os.getpid()}.part.mp4"
        def cmd():
            return [ffmpeg(), "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
                    *self._inputs(s + self.off, "low"), "-t", f"{e - s:.3f}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", tmp]
        try:
            run_with_progress(cmd(), e - s)
        except RuntimeError:
            self.resolve(force=True)
            run_with_progress(cmd(), e - s)
        os.replace(tmp, path)
        return path, self.true_offset(path, s)

    def true_offset(self, path, claimed):
        """Where the downloaded section really starts in the VOD. Twitch streams are cut in
        ~10 s pieces and seeking can land on the piece before, which made subtitles and cuts
        drift by seconds. We line the section's audio up with the stream audio to be sure."""
        side = path + ".offset.json"
        try:
            with open(side, "r", encoding="utf-8") as f:
                return float(json.load(f)["offset"])
        except Exception:
            pass
        off = claimed
        try:
            off = measure_offset(path, os.path.join(self.cache, "audio16k.wav"), claimed)
        except Exception as e:
            log.warning("Couldn't verify section offset: %s", e)
        with open(side, "w", encoding="utf-8") as f:
            json.dump({"offset": off, "claimed": claimed}, f)
        if abs(off - claimed) > 0.05:
            log.info("Section %s really starts at %.2f (asked %.2f)", os.path.basename(path), off, claimed)
        return off


def _envelope(x, hop=160):
    import numpy as np
    n = len(x) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    e = np.log1p(np.abs(x[:n * hop]).reshape(n, hop).mean(1))
    return (e - e.mean()).astype(np.float32)


def measure_offset(section_path, wav_path, claimed, search=30.0, probe_len=40.0):
    """Cross-correlate 10 ms loudness envelopes of the section and the full-stream audio."""
    import wave
    import numpy as np
    p = subprocess.run([ffmpeg(), "-v", "error", "-i", section_path, "-t", f"{probe_len}", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True, creationflags=CREATE_NO_WINDOW)
    sec = _envelope(np.frombuffer(p.stdout, np.int16).astype(np.float32))
    if len(sec) < 500:
        return claimed
    w = wave.open(wav_path, "rb")
    sr = w.getframerate()
    lo = max(0.0, claimed - search)
    w.setpos(int(lo * sr))
    ref = _envelope(np.frombuffer(w.readframes(int((2 * search + probe_len) * sr)), np.int16).astype(np.float32), hop=sr // 100)
    w.close()
    if len(ref) <= len(sec):
        return claimed
    corr = np.correlate(ref, sec, mode="valid")
    k = int(np.argmax(corr))
    norm = float(np.linalg.norm(sec) * np.linalg.norm(ref[k:k + len(sec)])) + 1e-6
    if corr[k] / norm < 0.35:  # no clear match: trust what we asked for
        return claimed
    return round(lo + k / 100.0, 2)


def run_with_progress(cmd, duration, progress=None):
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        if progress and line.startswith("out_time_ms=") and duration:
            try:
                t = int(line.split("=")[1]) / 1e6
                progress(max(0.0, min(0.999, t / duration)))
            except ValueError:
                pass
    err = p.stderr.read()
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("Download failed: " + err[-600:])


def _gql(body):
    req = urllib.request.Request(GQL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Client-ID": GQL_CLIENT, "Content-Type": "text/plain;charset=UTF-8",
                                          "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_game(vid, fetch=None):
    """The game / category a VOD is filed under on Twitch ('' if unknown)."""
    fetch = fetch or _gql
    resp = fetch({"query": 'query{video(id:"%s"){game{displayName name}}}' % int(vid)})
    item = resp[0] if isinstance(resp, list) else resp
    g = (((item.get("data") or {}).get("video") or {}).get("game")) or {}
    return g.get("displayName") or g.get("name") or ""


def fetch_clip_meta(slug, fetch=None):
    """2.35: the game a Twitch clip is filed under (Auto picks the CS2 style from it) and the streamer it is from
    (their name goes on the TikTok). Empty strings when unknown."""
    try:
        resp = (fetch or _gql)({"query": 'query{clip(slug:"%s"){game{displayName name} broadcaster{displayName login}}}'
                                         % re.sub(r"[^\w-]", "", slug)})
        item = resp[0] if isinstance(resp, list) else resp
        clip = ((item.get("data") or {}).get("clip")) or {}
        g, b = clip.get("game") or {}, clip.get("broadcaster") or {}
        return {"game": g.get("displayName") or g.get("name") or "", "channel": b.get("displayName") or b.get("login") or ""}
    except Exception:
        return {"game": "", "channel": ""}


def fetch_clip_game(slug, fetch=None):
    """2.35: the game / category a Twitch clip is filed under ('' if unknown)."""
    return fetch_clip_meta(slug, fetch)["game"]


def fetch_viewer_clips(vid, fetch=None):
    fetch = fetch or _gql
    q = ('query{video(id:"%s"){clips(first:100){edges{node{slug title viewCount '
         'durationSeconds videoOffsetSeconds createdAt}}}}}' % int(vid))
    resp = fetch({"query": q})
    item = resp[0] if isinstance(resp, list) else resp
    video = (item.get("data") or {}).get("video") or {}
    out = []
    for e in ((video.get("clips") or {}).get("edges") or []):
        n = e.get("node") or {}
        if n.get("videoOffsetSeconds") is None:
            continue
        out.append({"slug": n.get("slug"), "title": n.get("title") or "", "views": int(n.get("viewCount") or 0),
                    "offset": float(n["videoOffsetSeconds"]), "duration": float(n.get("durationSeconds") or 30)})
    return out


def download_chat(vid, duration, progress=None, fetch=None, users_out=None, start=0.0):
    """Page through the chat replay. Uses time offsets, and cursors when a single
    second has more messages than one page (busy moments are exactly the ones we want).
    Returns [(seconds, text)]."""
    fetch = fetch or _gql
    seen = set()
    msgs = []
    offset = int(start)          # 2.35.3: a time range starts paging at its start and stops at its end
    cursor = None
    cursor_ok = True
    stalls = 0
    while True:
        if cursor and cursor_ok:
            variables = {"videoID": str(vid), "cursor": cursor}
        else:
            variables = {"videoID": str(vid), "contentOffsetSeconds": int(offset)}
        body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": variables,
                 "extensions": {"persistedQuery": {"version": 1, "sha256Hash": COMMENTS_HASH}}}]
        try:
            resp = fetch(body)
            item = resp[0] if isinstance(resp, list) else resp
            if item.get("errors") and "cursor" in variables:
                raise RuntimeError(str(item["errors"])[:200])
        except Exception as e:
            if "cursor" in variables:
                cursor_ok = False  # Twitch sometimes blocks cursor paging; fall back to offsets
                cursor = None
                offset = int(offset) + 1
                continue
            if msgs:
                log.warning("Chat download stopped early: %s", e)
                break
            raise RuntimeError(f"Couldn't download chat: {e}")
        data = item.get("data") or {}
        video = data.get("video")
        if video is None:
            if msgs:
                break
            raise RuntimeError("Chat replay not available for this VOD.")
        comments = video.get("comments") or {}
        edges = comments.get("edges") or []
        last = offset
        new = 0
        for e in edges:
            n = e.get("node") or {}
            t = float(n.get("contentOffsetSeconds") or 0)
            cid = n.get("id") or f"{t}-{(n.get('message') or {}).get('fragments')}"
            last = max(last, t)
            if cid in seen:
                continue
            seen.add(cid)
            frags = ((n.get("message") or {}).get("fragments")) or []
            msgs.append((t, "".join((f.get("text") or "") for f in frags)))
            if users_out is not None:   # 2.20: who said it (for chat pop-ups)
                users_out.append([t, msgs[-1][1], (n.get("commenter") or {}).get("displayName") or "",
                                  (n.get("message") or {}).get("userColor") or ""])
            new += 1
        if progress and duration:
            progress(min(0.999, max(0.0, last - start) / duration))
        if not edges or not (comments.get("pageInfo") or {}).get("hasNextPage"):
            break
        if start and duration and last > start + duration + 30:
            break
        if int(last) <= int(offset) and not (cursor and cursor_ok):
            # whole page inside one second: try the cursor for the rest of this second
            cursor = edges[-1].get("cursor") if cursor_ok else None
            if not cursor:
                stalls += 1
                offset = int(offset) + 1
                if stalls > 200:
                    break
        else:
            cursor = edges[-1].get("cursor") if (cursor and cursor_ok) else None
            if new == 0 and cursor:
                cursor = None
            if not cursor:
                offset = max(int(last), int(offset) + (1 if new == 0 else 0))
            stalls = 0
        time.sleep(0.03)
    msgs.sort(key=lambda m: m[0])
    return msgs


def _hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
