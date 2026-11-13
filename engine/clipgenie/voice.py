"""Who's talking: you, or someone else (teammates on Discord, TTS, a call)?

Uses the Resemblyzer voice encoder (Apache-2.0, GE2E speaker embeddings), re-implemented in plain
numpy so it needs no extra installs. Your voice is learned automatically from each stream: you're
the one talking the most, so the biggest group of similar-sounding voices is you. Speech that
doesn't sound like you is marked as someone else and gets its own subtitle colour.
"""
import json
import os
import wave

import numpy as np

from .util import log

SR = 16000
N_FFT, HOP, N_MELS = 400, 160, 40
PARTIAL = 160  # frames (1.6 s)
_W = None
_MEL = None


def _weights():
    global _W
    if _W is None:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "voice_encoder.npz")
        _W = {k: v.astype(np.float32) for k, v in np.load(p).items()}
    return _W


def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    mel = f / (200.0 / 3)
    log_t = f >= 1000.0
    mel = np.where(log_t, 15.0 + np.log(np.maximum(f, 1e-10) / 1000.0) / (np.log(6.4) / 27.0), mel)
    return mel


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    f = (200.0 / 3) * m
    log_t = m >= 15.0
    return np.where(log_t, 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0)), f)


def _mel_basis():
    """Slaney-style mel filterbank (same as librosa's default)."""
    global _MEL
    if _MEL is None:
        fft_f = np.linspace(0, SR / 2, 1 + N_FFT // 2)
        mel_f = _mel_to_hz(np.linspace(_hz_to_mel(0), _hz_to_mel(SR / 2), N_MELS + 2))
        fdiff = np.diff(mel_f)
        ramps = mel_f[:, None] - fft_f[None, :]
        w = np.zeros((N_MELS, len(fft_f)))
        for i in range(N_MELS):
            lower = -ramps[i] / fdiff[i]
            upper = ramps[i + 2] / fdiff[i + 1]
            w[i] = np.maximum(0, np.minimum(lower, upper))
        w *= (2.0 / (mel_f[2:N_MELS + 2] - mel_f[:N_MELS]))[:, None]
        _MEL = w.astype(np.float32)
    return _MEL


def mel_frames(wav):
    """(frames, 40) mel power spectrogram, like Resemblyzer's wav_to_mel_spectrogram."""
    pad = N_FFT // 2
    y = np.pad(wav.astype(np.float32), pad, mode="constant")
    n = 1 + (len(y) - N_FFT) // HOP
    if n <= 0:
        return np.zeros((0, N_MELS), np.float32)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(N_FFT + 1)[:-1].astype(np.float32)
    spec = np.abs(np.fft.rfft(y[idx] * win, axis=1)) ** 2
    return (spec @ _mel_basis().T).astype(np.float32)


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _encode_batch(mels):
    """mels: (B, 160, 40) -> (B, 256) L2-normalised embeddings."""
    W = _weights()
    h_in = mels
    B = mels.shape[0]
    for layer in range(3):
        wih, whh = W[f"lstm.weight_ih_l{layer}"], W[f"lstm.weight_hh_l{layer}"]
        b = W[f"lstm.bias_ih_l{layer}"] + W[f"lstm.bias_hh_l{layer}"]
        xproj = h_in @ wih.T + b  # (B, T, 1024)
        h = np.zeros((B, 256), np.float32)
        c = np.zeros((B, 256), np.float32)
        outs = np.empty((B, mels.shape[1], 256), np.float32)
        for t in range(mels.shape[1]):
            g = xproj[:, t] + h @ whh.T
            i, f, gg, o = g[:, :256], g[:, 256:512], g[:, 512:768], g[:, 768:]
            c = _sigmoid(f) * c + _sigmoid(i) * np.tanh(gg)
            h = _sigmoid(o) * np.tanh(c)
            outs[:, t] = h
        h_in = outs
    e = np.maximum(0, h @ W["linear.weight"].T + W["linear.bias"])
    return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)


def _normalize(wav, target_dbfs=-30):
    rms = np.sqrt(np.mean(wav ** 2) + 1e-12)
    gain = 10 ** ((target_dbfs - 20 * np.log10(rms + 1e-12)) / 20)
    return wav * gain if gain > 1 else wav  # only ever turns quiet audio up (like Resemblyzer)


def embed(wav_list):
    """One embedding per audio piece (float32 16 kHz arrays). Short pieces are padded."""
    parts, owner = [], []
    for k, w in enumerate(wav_list):
        m = mel_frames(_normalize(w))
        if len(m) < PARTIAL:
            m = np.pad(m, ((0, PARTIAL - len(m)), (0, 0)))
        step = 80  # 50 % overlap
        starts = list(range(0, max(1, len(m) - PARTIAL + 1), step))
        for s in starts:
            parts.append(m[s:s + PARTIAL])
            owner.append(k)
    if not parts:
        return np.zeros((0, 256), np.float32)
    out = np.zeros((len(wav_list), 256), np.float32)
    P = np.stack(parts)
    E = np.concatenate([_encode_batch(P[i:i + 64]) for i in range(0, len(P), 64)])
    for k, e in zip(owner, E):
        out[k] += e
    return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


# ---------------------------------------------------------------- streams
def _read(wav_path, t0, t1):
    w = wave.open(wav_path, "rb")
    try:
        w.setpos(max(0, int(t0 * SR)))
        x = np.frombuffer(w.readframes(max(0, int((t1 - t0) * SR))), np.int16).astype(np.float32) / 32768.0
    finally:
        w.close()
    return x


def speech_runs(words, max_gap=0.35, min_len=0.6, max_len=6.0):
    """Group words into short stretches of continuous speech (one speaker each, usually)."""
    runs, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > max_gap or w["end"] - cur[0]["start"] > max_len):
            runs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    return [r for r in runs if r[-1]["end"] - r[0]["start"] >= min_len]


def learn_my_voice(wav_path, words, cache_path=None, sample=220):
    """Your voice profile for this stream: the biggest cluster of similar voices."""
    if cache_path and os.path.isfile(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return np.asarray(json.load(f)["me"], np.float32)
    runs = [r for r in speech_runs(words) if r[-1]["end"] - r[0]["start"] >= 1.2]
    if len(runs) < 10:
        return None
    pick = np.linspace(0, len(runs) - 1, min(sample, len(runs))).astype(int)
    wavs = [_read(wav_path, runs[i][0]["start"], runs[i][-1]["end"]) for i in pick]
    E = embed(wavs)
    me = E.mean(0)
    for keep in (0.8, 0.7, 0.6, 0.6):  # peel off the voices that aren't the main one
        me = me / (np.linalg.norm(me) + 1e-9)
        sims = E @ me
        top = sims >= np.quantile(sims, 1 - keep)
        me = E[top].mean(0)
    me = me / (np.linalg.norm(me) + 1e-9)
    sims = E @ me
    # threshold between "me" and "others" for this stream (Otsu split of the similarities)
    best, thr = -1.0, 0.7
    for t in np.linspace(0.5, 0.85, 36):
        lo, hi = sims[sims < t], sims[sims >= t]
        if len(lo) < 5 or len(hi) < 5:
            continue
        v = len(lo) * len(hi) * (hi.mean() - lo.mean()) ** 2
        if v > best:
            best, thr = v, float(t)
    thr = float(max(0.6, min(0.76, thr - 0.03)))   # lean towards "it's you" when unsure
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"me": me.tolist(), "threshold": thr, "n": int(len(E))}, f)
    log.info("Learned your voice from %d bits of speech (threshold %.2f)", len(E), thr)
    return me


def _threshold(cache_path, default=0.68):
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return float(json.load(f).get("threshold", default))
    except Exception:
        return default


def label_words(wav_path, words, me, threshold=0.7):
    """Adds w['other']=True to words that don't sound like you. Returns the words.
    Short bits of speech give noisier voice prints, so they need less similarity to count as you,
    and unsure bits take the label of the nearest confident neighbour."""
    if me is None or not words:
        return words
    runs = speech_runs(words, max_len=3.0, min_len=0.25)
    if not runs:
        return words
    wavs = [_read(wav_path, r[0]["start"] - 0.05, r[-1]["end"] + 0.05) for r in runs]
    sims = embed(wavs) @ me
    lab, conf = [], []
    for r, sim in zip(runs, sims):
        ln = r[-1]["end"] - r[0]["start"]
        thr = threshold - 0.14 * float(np.clip((1.6 - ln) / 1.2, 0, 1))
        lab.append(bool(sim < thr))
        conf.append(abs(float(sim) - thr) >= 0.04)
    for i in range(len(runs)):
        if conf[i]:
            continue
        best = None
        for j in range(len(runs)):
            if conf[j] and j != i:
                gap = min(abs(runs[j][0]["start"] - runs[i][-1]["end"]), abs(runs[i][0]["start"] - runs[j][-1]["end"]))
                if gap <= 1.5 and (best is None or gap < best[0]):
                    best = (gap, lab[j])
        if best:
            lab[i] = best[1]
    for r, sim, other in zip(runs, sims, lab):
        for w in r:
            w["other"] = other
            w["voice"] = round(float(sim), 3)
    return words
