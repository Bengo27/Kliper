"""Audio extraction and excitement curve (loudness + shout energy)."""
import os
import numpy as np

from .util import ffmpeg, run

SR = 16000
HOP = 0.5  # seconds per analysis frame


def extract_wav(video_path, cache_dir, audio_stream=None):
    out = os.path.join(cache_dir, "audio16k.wav")
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        return out
    cmd = [ffmpeg(), "-y", "-v", "error", "-i", video_path]
    if audio_stream is not None:
        cmd += ["-map", f"0:a:{int(audio_stream)}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", out]
    run(cmd)
    return out


def _read_pcm(wav_path):
    # Minimal WAV reader (16-bit mono) using a memory map so long VODs are cheap.
    with open(wav_path, "rb") as f:
        head = f.read(4096)
    idx = head.find(b"data")
    if idx < 0:
        raise RuntimeError("Bad WAV file")
    start = idx + 8
    return np.memmap(wav_path, dtype="<i2", mode="r", offset=start)


def rolling_z(x, win):
    """z-score against a rolling baseline (median / MAD) so quiet and loud
    stretches of a stream are judged against their own surroundings."""
    n = len(x)
    if n == 0:
        return x
    half = max(1, win // 2)
    # Downsample the baseline computation for speed on long streams.
    step = max(1, half // 8)
    centres = np.arange(0, n, step)
    med = np.empty(len(centres))
    mad = np.empty(len(centres))
    for i, c in enumerate(centres):
        seg = x[max(0, c - half): min(n, c + half)]
        m = np.median(seg)
        med[i] = m
        mad[i] = np.median(np.abs(seg - m)) * 1.4826 + 1e-6
    med_full = np.interp(np.arange(n), centres, med)
    mad_full = np.interp(np.arange(n), centres, mad)
    return (x - med_full) / mad_full


def excitement_curve(wav_path):
    """Returns per-HOP arrays: loudness dB, shout energy ratio, combined z-score."""
    pcm = _read_pcm(wav_path)
    hop = int(SR * HOP)
    nfr = len(pcm) // hop
    db = np.zeros(nfr, dtype=np.float32)
    shout = np.zeros(nfr, dtype=np.float32)
    freqs = np.fft.rfftfreq(hop, 1.0 / SR)
    band = (freqs >= 900) & (freqs <= 4000)
    window = np.hanning(hop).astype(np.float32)
    for i in range(nfr):
        x = pcm[i * hop:(i + 1) * hop].astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        db[i] = 20.0 * np.log10(rms)
        spec = np.abs(np.fft.rfft(x * window)) ** 2
        tot = float(spec.sum()) + 1e-12
        shout[i] = float(spec[band].sum()) / tot
    win = int(300 / HOP)  # 5 minute baseline
    z_loud = rolling_z(db, win)
    z_shout = rolling_z(shout, win)
    combined = 0.7 * z_loud + 0.3 * z_shout
    # Sudden rises (onsets) matter: jumpscares, slams, screams
    rise = np.zeros_like(db)
    rise[2:] = db[2:] - np.minimum(db[1:-1], db[:-2])
    combined = combined + 0.25 * np.clip(rolling_z(rise, win), 0, None)
    return {"hop": HOP, "db": db, "z": combined.astype(np.float32)}


def silences(db, hop=HOP, thresh_db=None, min_len=0.6):
    """Return list of (start, end) seconds where audio is quiet."""
    if thresh_db is None:
        thresh_db = float(np.percentile(db, 20)) + 3.0
    out = []
    start = None
    for i, v in enumerate(db):
        if v < thresh_db:
            if start is None:
                start = i
        else:
            if start is not None and (i - start) * hop >= min_len:
                out.append((start * hop, i * hop))
            start = None
    if start is not None and (len(db) - start) * hop >= min_len:
        out.append((start * hop, len(db) * hop))
    return out


def shot_times(wav_path, t0, t1, jump_db=14.0, above_db=6.0, min_sep=0.25):
    """2.35.1: sharp loud sounds (gunshots, hits) between t0 and t1, in the same seconds as the wav.
    Pause cuts keep these: a quiet streamer during a clutch is the play, not dead air.
    A 10 ms frame counts when its attack energy jumps 14+ dB over the last 0.3 s and is well above the
    stretch's usual level. Checked on "5 bullet deag ace": hits at 2.5, 5.4, 9.0, 10.0 s = the 4 kills."""
    try:
        pcm = _read_pcm(wav_path)
    except Exception:
        return []
    a0, a1 = max(0, int((t0 - 2.0) * SR)), min(len(pcm), int((t1 + 0.5) * SR))
    if a1 - a0 < SR:
        return []
    x = pcm[a0:a1].astype(np.float32) / 32768.0
    y = np.diff(x, prepend=x[:1])          # the attack of a shot, not its boom
    hop = SR // 100
    n = len(y) // hop
    e = 10.0 * np.log10(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    lvl = float(np.median(e))
    pad = np.concatenate([np.full(30, e[0]), e])
    base = np.median(np.lib.stride_tricks.sliding_window_view(pad, 30)[:n], axis=1)
    out = []
    for i in np.nonzero((e - base > jump_db) & (e > lvl + above_db))[0]:
        t = a0 / SR + i * 0.01
        if t0 <= t <= t1 and (not out or t - out[-1] > min_sep):
            out.append(round(float(t), 2))
    return out
