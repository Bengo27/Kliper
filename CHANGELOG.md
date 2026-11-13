# Patch notes

The newest version is at the top. The version is the one the app shows (top left) and the Setup file is named after.

## 0.1.2 Beta

**New**
- **YouTube and Kick links.** Paste a YouTube video or live (youtube.com/watch, youtu.be, /live) or a Kick VOD
  (kick.com/&lt;name&gt;/videos/…). YouTube Shorts and Kick clips become one TikTok, like Twitch clips.
  The "which part of the VOD" time range works for them too. Chat pop-ups stay Twitch only for now.
- **Subtitles in your language.** Speech is no longer forced to English: the language is detected, and streams
  that switch between languages get each part in its own language. Japanese, Chinese, Korean, Arabic, Hebrew,
  Hindi, Thai, Russian and more are drawn with the right letters (right to left where needed).
- **Pick the subtitle language first.** Editor → Subtitles starts with *Language of the subtitles*. That
  TikTok's subtitles are then written in that language only: a word in another language is left out, so
  languages never mix. Settings also has a *Speech language* for whole streams.
- **Better layouts for IRL, Just Chatting, VTuber and couch streams.** When the whole picture is the camera,
  the TikTok uses the full screen instead of showing the same room twice. Two or three people on camera
  all stay in the shot.

**Fixed**
- Long VODs: speech-to-text is saved every 10 minutes, so a crash or closing the app carries on from there
  instead of starting again.
- A laugh written out as hundreds of the same letter no longer fills a whole subtitle line.
- One version number everywhere: the app, the log file, the Setup file and the release all say the same.
- `engine.log` starts fresh each session and old logs stay small.
- A stream with two saved transcripts could use the wrong one.

## 0.1.1 Beta

- No login: Clip Genie works without an account.
- Free, with built-in AI (free models only).
