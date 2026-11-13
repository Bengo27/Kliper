# Clip Genie

**Turn Twitch, YouTube and Kick VODs and clips into ready-to-post TikToks, for free.**

What's new: see the **[patch notes](CHANGELOG.md)**.

Paste a Twitch, YouTube or Kick link. Clip Genie finds the best moments, puts the webcam and gameplay into a
vertical 1080x1920 layout, cuts the dead air, and adds titles, subtitles, chat pop-ups and (for CS2) a
redrawn kill feed. Everything is rendered on your own PC.

Made by the Twitch streamer **xHotGamerDude** (TikTok **@hotgamerdude_**).

## Features

- **Finds the moments**: speech recognition (Whisper), voice loudness, chat activity, viewer clips and
  gunshots are combined to rank a whole VOD. You can also pick a time range of the VOD.
- **Automatic layout**: webcam + gameplay split, full webcam, or blurred background, chosen per clip.
- **Smart cuts**: pauses and filler are cut, but kills, payoffs and reactions are kept.
- **Styles**: Simple, Professional, CS2 + kill feed, or your own saved styles.
- **CS2 kill feed** read from the stream and redrawn sharp (uses the icons from your own CS2 install).
- **Editor + timeline**: move the webcam / gameplay boxes, music, sound effects, subtitles, live 9:16 preview.
- **AI** (optional): finds funny moments, writes titles, checks the opening, and edits on request
  ("make it longer", "add the bit where...").
- **Learns your taste**: thumbs up / down on moments teach it what *you* like.
- **Export** to your PC or straight to your phone with a QR code.

## Download and install (Windows)

Free. No login, no account, no API key.

1. Go to the **[latest release](../../releases/latest)** and download **`Clip.Genie.Setup.<version>.Beta.exe`**
   (under *Assets*).
2. Double-click it. If Windows says *"Windows protected your PC"*, click **More info**, then **Run anyway**
   (the installer isn't signed yet).
3. Follow the steps and open Clip Genie at the end.
4. Answer 3 quick questions (your Twitch name, music, sounds), then paste a Twitch VOD or clip link and press
   **Make TikToks**. The first time it also downloads the speech model (a few minutes).

**Needs:** Windows 10 / 11 (64-bit), about 5 GB free space and an internet connection. An NVIDIA graphics card
makes it much faster (it works without one, just slower).

**Found a bug?** [Open an issue](../../issues) with what went wrong, the stream link and roughly when. If
something breaks, also attach `%APPDATA%\GenieStudioFX\ClipGenie\engine.log` (paste that into the File Explorer
address bar to find it).

The official build includes the built-in AI (free models, a daily amount per PC).

## Run from source (Windows)

1. Install **Python 3.11** and **FFmpeg** (put `ffmpeg.exe` / `ffprobe.exe` in `ffmpeg\bin\` or on your PATH).
2. `pip install -r requirements.txt`
3. Start the app:
   ```
   python engine\run_app.pyw
   ```
   or the engine only: `python engine\run_engine.py`, then open the address it prints.

Running from source never asks for a sign-in. For the AI, add your own Anthropic API key in Settings
(the built-in AI is only in the official build).

## Project layout

| Folder | What |
|---|---|
| `engine/clipgenie/` | The engine: HTTP server, ranking, transcription, webcam detection, rendering (ffmpeg). |
| `app/` | The app UI (HTML / JS / CSS) shown in the window. |
| `server/licence/` | Cloudflare Worker: sign-in, the free-version switches, admin page and the built-in AI pass-through. See `server/licence/SETUP.md`. |
| `tools/` | Build scripts (compiled engine, installer). |
| `docs/` | Editing research the styles are based on. |

## Licence

Clip Genie is free software under the **GNU General Public License v3.0**: see [LICENSE](LICENSE).
You may use, change and share it, but if you share a changed version you must publish its source under
the same licence.

Third-party parts keep their own licences: pywebview, pythonnet, bottle and their dependencies in
`engine/vendor/` (BSD / MIT), `app/qrcode.js` (MIT), fonts in `engine/fonts/` and `app/` (SIL Open Font
License). FFmpeg, Whisper models and the Python packages are downloaded separately under their own licences.
Counter-Strike 2 icons are read from your own game install and are not included.

The name "Clip Genie" and its logo belong to the creator. If you make a fork, please give it its own name.
