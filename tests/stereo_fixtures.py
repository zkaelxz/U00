"""Synthetic stereo sources for the stereo DSP tests (numpy only)."""
import numpy as np

SR = 16000


def speech_like(seconds=3.0, sr=SR, seed=0, band=(150.0, 7000.0)):
    """Band-limited noise under a syllable-rate envelope with real gaps, so the energy gate has a floor."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    spec[(freqs < band[0]) | (freqs > band[1])] = 0.0
    noise = np.fft.irfft(spec, n=n)
    noise /= np.max(np.abs(noise))
    t = np.arange(n) / sr
    env = np.clip(np.sin(2 * np.pi * 3.0 * t), 0, None) ** 2 + 0.003
    return noise * env


def delayed(x, delay_samples):
    """Fractional delay by linear phase on a zero-padded FFT (no wrap)."""
    pad = int(abs(delay_samples)) + 16
    size = x.size + 2 * pad
    spec = np.fft.rfft(x, n=size) * np.exp(-2j * np.pi * np.fft.rfftfreq(size) * (delay_samples + pad))
    return np.fft.irfft(spec, n=size)[pad:pad + x.size]


def place(x, itd_s, ild_db, sr=SR):
    """Stereo from a mono source: positive ITD delays R, positive ILD makes L louder."""
    gain_l, gain_r = 10 ** (ild_db / 40.0), 10 ** (-ild_db / 40.0)
    return gain_l * delayed(x, -itd_s * sr / 2), gain_r * delayed(x, itd_s * sr / 2)


def in_band_bands(cues, lo_hz=300.0, hi_hz=3000.0):
    return (cues.band_centers_hz > lo_hz) & (cues.band_centers_hz < hi_hz)


def delayed_varying(x, delay_samples, half_width=16):
    """y[n] = x[n - delay[n]] with a Hann-windowed sinc, so a moving source keeps its full band."""
    n = np.arange(x.size)
    pos = n - delay_samples
    base = np.floor(pos).astype(int)
    taps = np.arange(-half_width + 1, half_width + 1)
    idx = base[:, None] + taps[None, :]
    frac = pos[:, None] - idx
    weights = np.sinc(frac) * (0.5 + 0.5 * np.cos(np.pi * frac / half_width))
    valid = (idx >= 0) & (idx < x.size)
    return (x[np.clip(idx, 0, x.size - 1)] * weights * valid).sum(axis=1)
