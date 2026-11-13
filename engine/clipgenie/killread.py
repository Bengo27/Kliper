"""2.35: READ each CS2 kill (names, gun, headshot / no-scope / wallbang..., teams, your kill / your death) so the kill
feed can be drawn from scratch, sharp, like the kill-feed generators (the creator: the traced rows "look sloppy").
Icons come from the streamer's own CS2 install (cs2icons); names are read with Windows' built-in OCR.

read_kills(video, start, end, box) -> [{t, t_end, killer, victim, killer_team, victim_team, gun, extras, mine, death}]
or None when the CS2 icons aren't there (the caller then falls back to the traced rows).
"""
import collections
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

from .killfeed import _find_rows, _is_mine, FPS_OV
from .util import ffmpeg, CREATE_NO_WINDOW, log


def _crop_frames(video, start, end, box):
    x, y, w, h = box
    x0, w0 = max(0.0, x - 0.04), min(1.0, w + 0.08)
    y0, h0 = max(0.0, y - 0.03), min(1.0 - max(0.0, y - 0.03), h + 0.06)
    dur = max(0.5, end - start)
    probe = subprocess.run([ffmpeg(), "-hide_banner", "-i", video], capture_output=True,
                           creationflags=CREATE_NO_WINDOW).stderr.decode(errors="ignore")
    m = re.search(r"(\d{3,5})x(\d{3,5})", probe[probe.find("Video:"):])
    vw, vh = (int(m.group(1)), int(m.group(2))) if m else (1920, 1080)
    cw, ch = int(vw * min(w0, 1 - x0)) // 2 * 2, int(vh * h0) // 2 * 2
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", video,
           "-vf", f"fps={FPS_OV},crop={cw}:{ch}:{int(vw * x0)}:{int(vh * y0)}", "-an", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW).stdout
    n = len(raw) // (cw * ch * 3)
    return np.frombuffer(raw[: n * cw * ch * 3], np.uint8).reshape(n, ch, cw, 3) if n else None


def _masks(reg):
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    lum = (r * 3 + g * 6 + b) // 10
    sat = reg.max(-1) - reg.min(-1)
    gold = (r > 135) & (g > 85) & (r - b > 45)
    blue = (b > 135) & (b - r > 35)
    white = (lum > 165) & (sat < 60)
    return gold, blue, white, lum


def _sig(f, row):
    """Column profile of a row's text + icons (to follow the same row while CS2 moves rows up)."""
    y0, y1, x0, x1 = row
    import cv2
    gold, blue, white, lum = _masks(f[y0:y1, x0:x1])
    # 2.35.2: only text on the row's dark background (a light-blue sky looks like blue names)
    on_row = cv2.blur((lum < 95).astype(np.float32), (51, 1)) >= 0.3
    return ((gold | blue | white) & on_row).sum(0).astype(np.float32)


def _same(a, b):
    if abs(len(a) - len(b)) > 6:
        return 0.0
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]                      # rows are right-aligned
    if a.std() < 1e-3 or b.std() < 1e-3:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _tracks(fr):
    """Follow each kill row from when it appears until it's gone (it may move up as older rows leave)."""
    tracks = []
    for i, f8 in enumerate(fr):
        f = f8.astype(np.int16)
        used = set()
        for r in _find_rows(f):
            s = _sig(f, r)
            best = None
            for k, t in enumerate(tracks):
                if k in used or t["last"] < i - 8 or t["last"] == i:
                    continue
                sc = _same(t["sig"], s)
                if sc > 0.82 and (best is None or sc > best[0]):
                    best = (sc, k)
            if best is None:
                # 2.35.2: same spot, about the same width = the same row (its pattern can wobble over a bright
                # background; without this a kill broke into pieces and was drawn seconds late)
                for k, t in enumerate(tracks):
                    if k in used or t["last"] < i - 8 or t["last"] == i:
                        continue
                    p = t["rows"][t["last"]]
                    wa, wb = p[3] - p[2], r[3] - r[2]
                    if abs((p[0] + p[1]) - (r[0] + r[1])) <= 10 and abs(p[3] - r[3]) <= 10 and abs(wa - wb) <= 0.15 * max(wa, wb):
                        best = (0.0, k)
                        break
            if best:
                t = tracks[best[1]]
                used.add(best[1])
                t["last"], t["rows"][i] = i, r
                if len(t["rows"]) < 12:
                    t["sig"] = s                 # still fading in: keep the newest look
            else:
                tracks.append({"first": i, "last": i, "rows": {i: r}, "sig": s})
                used.add(len(tracks) - 1)
    return [t for t in tracks if len(t["rows"]) >= 8]


def _runs(cols, gap):
    """[(a, b)] runs of True columns, joining runs closer than `gap`."""
    idx = np.nonzero(cols)[0]
    if not len(idx):
        return []
    out, a, p = [], idx[0], idx[0]
    for c in idx[1:]:
        if c - p > gap:
            out.append((a, p + 1))
            a = c
        p = c
    out.append((a, p + 1))
    return out


def _match_icon(blob, pool, wpen=0.8):
    """Best CS2 icon for one white blob (float coverage, h x w). Returns (name, score)."""
    import cv2
    from . import cs2icons
    h, w = blob.shape
    if h < 6 or w < 4:
        return None, 0.0
    b = blob > 0.45
    best = (None, 0.0)
    for name, ic in pool.items():
        m = cs2icons.raster(ic, h)
        mw = m.shape[1]                          # the icon's own width at this height: its shape
        if abs(mw - w) > max(6, 0.35 * w):
            continue
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA) > 127
        inter, union = (m & b).sum(), (m | b).sum()
        sc = inter / max(1, union) - wpen * abs(mw - w) / max(1, w)   # a wrong width = a different gun
        if sc > best[1]:
            best = (name, float(sc))
    return best


def _parse(f, row):
    """One row -> where the two names are, their teams, the gun + extra icons, your kill / your death."""
    from . import cs2icons
    y0, y1, x0, x1 = row
    reg = f[y0:y1, x0:x1]
    gold, blue, white, lum = _masks(reg)
    # 2.35.3: read only inside the row's dark box: the sky above / below it (light blue = the blue-name colour,
    # pale sky = "white") broke the reading, so a kill over the sky was drawn seconds late
    dark = lum < 95
    band = np.nonzero(dark.mean(1) >= 0.25)[0]
    if len(band):
        inside = np.zeros(lum.shape, bool)
        cols = np.nonzero(dark[band[0]:band[-1] + 1].mean(0) >= 0.2)[0]      # and the sky left / right of it
        if len(cols):          # 3 px in: the red outline blended with the sky also looks like name colours
            inside[band[0] + 3:band[-1] - 2, cols[0] + 3:cols[-1] - 2] = True
            gold, blue, white = gold & inside, blue & inside, white & inside
    named = gold | blue
    text = _runs(named.sum(0) >= 1, 7)
    if len(text) < 2:
        return None
    blobs = [(a, b) for a, b in _runs((white & ~named).sum(0) >= 2, 2) if b - a >= 4]
    first_text, last_text = text[0], text[-1]
    mid = [(a, b) for a, b in blobs if a >= first_text[1] - 1 and b <= last_text[0] + 1]
    # 2.35.3: an assist "A + B": the small "+" sits between two names; it is not the gun (was read as a decoy)
    hgt = y1 - y0
    mid = [(a, b) for a, b in mid if not (b - a <= 0.6 * hgt
                                          and any(0 <= a - t[1] <= 0.8 * hgt for t in text)
                                          and any(0 <= t[0] - b <= 0.8 * hgt for t in text))]
    pre = [(a, b) for a, b in blobs if b <= first_text[0] + 1]
    if not mid:
        return None
    # killer = all text left of the gun (an assist "A + B" is two runs), victim = all text right of the last icon
    k_runs = [t for t in text if t[1] <= mid[0][0] + 1] or [first_text]
    v_runs = [t for t in text if t[0] >= mid[-1][1] - 1] or [last_text]
    kx, vx = (k_runs[0][0], k_runs[-1][1]), (v_runs[0][0], v_runs[-1][1])
    cov = np.clip((lum.astype(np.float32) - 90) / 110.0, 0, 1) * (white & ~named)

    def blob(a, b):
        sub = cov[:, a:b]
        rr = np.nonzero((sub > 0.45).sum(1) >= 2)[0]          # ignore single stray pixels above / below
        return sub[rr[0]:rr[-1] + 1] if len(rr) else sub[:0]
    # icons can be several pieces (the headshot's arrow, an AWP's scope): try joining up to 3 neighbouring pieces
    # and keep whatever fits a real icon best
    def spans(bl, pool, need, wpen=0.8):
        out, i = [], 0
        while i < len(bl):
            best = (None, need, 1)
            for n in (1, 2, 3):
                if i + n > len(bl) or bl[i + n - 1][1] - bl[i][0] > 3 * (y1 - y0):
                    break
                nm, sc = _match_icon(blob(bl[i][0], bl[i + n - 1][1]), pool, wpen)
                if nm and sc > best[1]:
                    best = (nm, sc, n)
            if best[0]:
                out.append(best[:2])
            i += best[2]
        return out
    g = spans(mid, cs2icons.guns(), 0.3)
    if not g:
        return None
    gun, gsc = g[0]
    # the gun used up the first pieces; the rest of the middle (and anything before the killer) are extras
    used = 0
    for n in (1, 2, 3):
        if n <= len(mid) and _match_icon(blob(mid[0][0], mid[n - 1][1]), cs2icons.guns())[0] == gun:
            used = n
            break
    extras = [nm for nm, sc in spans(pre, cs2icons.extras(), 0.35, 0.2) + spans(mid[max(1, used):], cs2icons.extras(), 0.35, 0.2)]

    def team(a, b):
        return "T" if gold[:, a:b].sum() >= blue[:, a:b].sum() else "CT"
    bg = reg[(~named) & (~white) & (lum < 110)]
    death = bool(len(bg) > 20 and np.median(bg[:, 0]) - np.median(bg[:, 1]) > 22)
    return {"killer_x": kx, "victim_x": vx, "killer_team": team(*kx), "victim_team": team(*vx),
            "gun": gun, "gun_score": round(gsc, 2), "extras": extras, "mine": bool(_is_mine(f, row)), "death": death}


OCR_PS = r"""
param([string]$dir)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(); $task.Result }
$e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
Get-ChildItem -Path $dir -Filter *.png | ForEach-Object {
  $f = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) ([Windows.Storage.StorageFile])
  $s = Await ($f.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $d = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($s)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $b = Await ($d.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $r = Await ($e.RecognizeAsync($b)) ([Windows.Media.Ocr.OcrResult])
  $t = ($r.Lines | ForEach-Object { $_.Text }) -join ' '
  Write-Output ($_.BaseName + "`t" + $t)
  $s.Dispose()
}
"""


def _ocr(crops):
    """{key: text} for {key: RGB crop}, read with Windows OCR in one go ({} if OCR isn't available)."""
    import cv2
    if not crops:
        return {}
    d = tempfile.mkdtemp(prefix="cg_ocr_")
    try:
        for k, im in crops.items():
            g = (im[..., 0].astype(np.int32) * 3 + im[..., 1].astype(np.int32) * 6 + im[..., 2]) // 10
            g = np.clip((g - 40) * 2.2, 0, 255).astype(np.uint8)            # names bright, background dark
            g = cv2.resize(255 - g, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)   # dark text on white
            g = cv2.copyMakeBorder(g, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
            cv2.imwrite(os.path.join(d, f"{k}.png"), g)
        ps = os.path.join(d, "ocr.ps1")
        with open(ps, "w", encoding="utf-8-sig") as f:
            f.write(OCR_PS)
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps, "-dir", d],
                           capture_output=True, timeout=180, creationflags=CREATE_NO_WINDOW)
        out = {}
        for line in r.stdout.decode("utf-8", "ignore").splitlines():
            if "\t" in line:
                k, t = line.split("\t", 1)
                out[k.strip().lstrip("﻿")] = t.strip()
        return out
    except Exception as e:
        log.warning("Kill feed name reading failed: %s", e)
        return {}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _vote(texts):
    texts = [t for t in texts if t]
    if not texts:
        return ""
    c = collections.Counter(texts).most_common()
    top = [t for t, n in c if n == c[0][1]]
    return max(top, key=len)


def _alike(a, b):
    """Same name read slightly differently ("(im 6'6)" vs "(1m GIG)")."""
    import difflib
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def read_kills(video, start, end, box):
    from . import cs2icons
    if not cs2icons.guns():
        return None
    fr = _crop_frames(video, start, end, box)
    if fr is None:
        return []
    pieces, crops = [], {}
    for k, t in enumerate(_tracks(fr)):
        idx = sorted(t["rows"])
        picks = [idx[min(len(idx) - 1, int(len(idx) * q))] for q in (0.3, 0.45, 0.6, 0.75, 0.9)]
        parsed = []
        for j, i in enumerate(picks):
            p = _parse(fr[i].astype(np.int16), t["rows"][i])
            if not p:
                continue
            y0, y1, x0, x1 = t["rows"][i]
            for side in ("killer", "victim"):
                a, b = p[side + "_x"]
                crops[f"{k}_{j}_{side}"] = fr[i][y0:y1, max(0, x0 + a - 3):x0 + b + 3]
            p["key"] = f"{k}_{j}"
            parsed.append(p)
        if parsed:
            pieces.append({"t": t["first"], "t_end": t["last"], "samples": parsed})
    names = _ocr(crops)
    for pc in pieces:
        for p in pc["samples"]:
            p["killer"] = names.get(p["key"] + "_killer", "")
            p["victim"] = names.get(p["key"] + "_victim", "")
        pc["vkey"] = _key(_vote([p["victim"] for p in pc["samples"]]))
    # one kill can show up as several pieces (the row fades in, moves up when older rows leave): join pieces with
    # the same victim (a player dies once) whose times touch; a piece whose names couldn't be read joins the
    # named piece it overlaps.
    pieces.sort(key=lambda x: x["t"])
    kills = []
    for pc in pieces:
        host = None
        for kl in kills:
            near = pc["t"] <= kl["t_end"] + int(1.5 * FPS_OV) and pc["t_end"] >= kl["t"] - int(1.5 * FPS_OV)
            if near and pc["vkey"] and kl["vkey"] and _alike(pc["vkey"], kl["vkey"]):
                host = kl
                break
        if host is None and not pc["vkey"]:
            host = next((kl for kl in kills if pc["t"] <= kl["t_end"] and pc["t_end"] >= kl["t"]), None)
        if host is None and not pc["vkey"]:
            # 2.35.2: an unreadable piece that starts right where another unreadable one ended = the same row
            host = next((kl for kl in kills if not kl["vkey"] and 0 <= pc["t"] - kl["t_end"] <= int(0.5 * FPS_OV)), None)
        if host:
            host["t"], host["t_end"] = min(host["t"], pc["t"]), max(host["t_end"], pc["t_end"])
            host["samples"] += pc["samples"]
            host["vkey"] = host["vkey"] or pc["vkey"]
        else:
            kills.append(pc)
    out = []
    for kl in kills:
        sm = kl["samples"]

        def most(key, default):
            c = collections.Counter(p[key] for p in sm if p[key]).most_common(1)
            return c[0][0] if c else default
        ex = collections.Counter(tuple(p["extras"]) for p in sm).most_common(1)
        if not kl["vkey"] and kl["t_end"] - kl["t"] < FPS_OV:   # a short unreadable blip: not a kill
            continue
        out.append({"t": round(start + kl["t"] / FPS_OV, 2), "t_end": round(start + kl["t_end"] / FPS_OV, 2),
                    "killer": _vote([p["killer"] for p in sm]), "victim": _vote([p["victim"] for p in sm]),
                    "gun": most("gun", "knife"), "extras": list(ex[0][0]) if ex else [],
                    "killer_team": most("killer_team", "T"), "victim_team": most("victim_team", "CT"),
                    "mine": sum(p["mine"] for p in sm) * 2 > len(sm), "death": sum(p["death"] for p in sm) * 2 > len(sm)})
    return out
