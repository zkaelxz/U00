"""Binaural cue estimation (ILD, ITD, reliability) for one stereo line.

Pure numpy DSP with no app surface. The method, conventions and limits are in
docs/specs/stereo-track.md. Sign convention: positive ILD means the left channel is
louder and positive ITD means the right channel arrives later, so both mean "source
toward the left".
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

FRAME = 1024
HOP = FRAME // 4
# Zero-padding the FFT to twice the frame keeps delays (cue measurement and rendering) from wrapping.
NFFT = 2 * FRAME
N_BANDS = 28
BAND_FMIN_HZ = 100.0
BAND_FMAX_HZ = 18000.0
BAND_FMAX_NYQUIST_FRACTION = 0.45
ITD_BAND_HZ = (180.0, 6500.0)
MAX_ITD_S = 0.0009
GCC_UPSAMPLE = 4
ENERGY_MARGIN_DB = 10.0
ENERGY_FLOOR_PERCENTILE = 20.0
MIN_GCC_PEAK = 0.25
DECAY_S = 0.3
SMOOTH_TAU_S = 0.15
LEVEL_TAU_S = 0.3
LEVEL_CLAMP_DB = 3.0

_EPS = 1e-12
_PAD = FRAME - HOP
_GCC_CHUNK = 128
# Periodic sqrt-Hann: analysis x synthesis gives a Hann, which sums to 2 at 75% overlap.
_WINDOW = np.sqrt(np.hanning(FRAME + 1)[:-1])
_OLA_GAIN = 2.0


@dataclass(frozen=True)
class CueTracks:
    """Per-frame cues for one line. `reliable` marks frames that were actually measured."""
    sr: int
    ild_db: np.ndarray        # (frames, N_BANDS)
    itd_s: np.ndarray         # (frames,)
    peak: np.ndarray          # (frames,) GCC-PHAT peak height, ~0..1
    energy_db: np.ndarray     # (frames,) stereo frame energy
    reliable: np.ndarray      # (frames,) bool
    band_centers_hz: np.ndarray

    @property
    def n_frames(self) -> int:
        return int(self.itd_s.shape[0])

    @property
    def hop_s(self) -> float:
        return HOP / self.sr


def frame_count(n_samples: int) -> int:
    return (_PAD + max(int(n_samples), 1) - 1) // HOP + 1


def stft(x) -> np.ndarray:
    """(frames, NFFT//2+1) spectra of sqrt-Hann frames, signal padded so every sample has 4 overlaps."""
    x = np.asarray(x, dtype=np.float64)
    frames_n = frame_count(x.size)
    padded = np.zeros((frames_n - 1) * HOP + FRAME)
    padded[_PAD:_PAD + x.size] = x
    frames = sliding_window_view(padded, FRAME)[::HOP]
    return np.fft.rfft(frames * _WINDOW, n=NFFT, axis=1)


def istft(spec: np.ndarray, n_samples: int, offset: int = 0) -> np.ndarray:
    """Inverse of `stft`. `offset` is the delay (samples) the caller added to every frame so
    negative delays fit in the buffer; the frame is read back from [offset, offset+FRAME)."""
    frames = np.fft.irfft(spec, n=NFFT, axis=1)[:, offset:offset + FRAME] * _WINDOW
    out = np.zeros((frames.shape[0] - 1) * HOP + FRAME)
    # Frames of the same phase mod 4 tile the buffer exactly, so each phase is one slice add.
    for phase in range(FRAME // HOP):
        part = frames[phase::FRAME // HOP]
        if part.size:
            out[phase * HOP:phase * HOP + part.size] += part.reshape(-1)
    return out[_PAD:_PAD + n_samples] / _OLA_GAIN


def bin_freqs(sr: int) -> np.ndarray:
    return np.arange(NFFT // 2 + 1) * (sr / NFFT)


@lru_cache(maxsize=8)
def band_layout(sr: int):
    """(lo_bin, hi_bin, centers_hz) for the log-spaced ILD bands; every band owns at least one bin."""
    fmax = min(BAND_FMAX_HZ, BAND_FMAX_NYQUIST_FRACTION * sr)
    edges = np.geomspace(BAND_FMIN_HZ, fmax, N_BANDS + 1)
    freqs = bin_freqs(sr)
    lo = np.searchsorted(freqs, edges[:-1], side="left")
    hi = np.maximum(np.searchsorted(freqs, edges[1:], side="left"), lo + 1)
    return lo, hi, np.sqrt(edges[:-1] * edges[1:])


def _band_power(spec: np.ndarray, sr: int) -> np.ndarray:
    lo, hi, _ = band_layout(sr)
    power = spec.real ** 2 + spec.imag ** 2
    cum = np.concatenate([np.zeros((power.shape[0], 1)), np.cumsum(power, axis=1)], axis=1)
    return cum[:, hi] - cum[:, lo]


def band_ild(spec_l: np.ndarray, spec_r: np.ndarray, sr: int) -> np.ndarray:
    return 10.0 * np.log10((_band_power(spec_l, sr) + _EPS) / (_band_power(spec_r, sr) + _EPS))


def gcc_phat_itd(spec_l: np.ndarray, spec_r: np.ndarray, sr: int):
    """Per-frame (itd_s, peak): GCC-PHAT lag of the strongest peak within +-MAX_ITD_S, parabolic refined."""
    freqs = bin_freqs(sr)
    sel = np.flatnonzero((freqs >= ITD_BAND_HZ[0]) & (freqs <= ITD_BAND_HZ[1]))
    lo, hi = int(sel[0]), int(sel[-1]) + 1
    n_fft = GCC_UPSAMPLE * NFFT
    reach = int(round(MAX_ITD_S * sr * GCC_UPSAMPLE))
    lags = np.arange(-reach - 1, reach + 2)
    scale = n_fft / (2.0 * (hi - lo))
    itd = np.zeros(spec_l.shape[0])
    peak = np.zeros(spec_l.shape[0])
    for start in range(0, spec_l.shape[0], _GCC_CHUNK):
        stop = start + _GCC_CHUNK
        cross = spec_l[start:stop, lo:hi] * np.conj(spec_r[start:stop, lo:hi])
        cross /= np.abs(cross) + 1e-18
        padded = np.zeros((cross.shape[0], n_fft // 2 + 1), dtype=np.complex128)
        padded[:, lo:hi] = cross
        # Zero-padding the spectrum interpolates the correlation: index m is lag m / GCC_UPSAMPLE samples.
        corr = np.fft.irfft(padded, n=n_fft, axis=1)[:, lags % n_fft] * scale
        inner = corr[:, 1:-1]
        best = np.argmax(inner, axis=1)
        rows = np.arange(corr.shape[0])
        y0 = inner[rows, best]
        ym, yp = corr[rows, best], corr[rows, best + 2]
        denom = ym - 2.0 * y0 + yp
        delta = np.where(denom < -1e-12, 0.5 * (ym - yp) / np.where(denom < -1e-12, denom, 1.0), 0.0)
        lag_samples = (lags[1:-1][best] + np.clip(delta, -1.0, 1.0)) / GCC_UPSAMPLE
        # cross = L*conj(R) peaks at tL - tR; the public ITD is tR - tL.
        itd[start:stop] = -lag_samples / sr
        peak[start:stop] = y0
    return itd, peak


def estimate_cues(left, right, sr: int, min_peak: float = MIN_GCC_PEAK) -> CueTracks:
    """Raw (unsmoothed, unfilled) cues for one line, with the reliability mask over the line."""
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    if left.shape != right.shape or left.ndim != 1:
        raise ValueError("left and right must be 1-D arrays of the same length")
    spec_l, spec_r = stft(left), stft(right)
    ild = band_ild(spec_l, spec_r, sr)
    itd, peak = gcc_phat_itd(spec_l, spec_r, sr)
    energy = 10.0 * np.log10((spec_l.real ** 2 + spec_l.imag ** 2).sum(axis=1)
                             + (spec_r.real ** 2 + spec_r.imag ** 2).sum(axis=1) + _EPS)
    floor = np.percentile(energy, ENERGY_FLOOR_PERCENTILE)
    reliable = (energy > floor + ENERGY_MARGIN_DB) & (peak > min_peak)
    return CueTracks(sr=int(sr), ild_db=ild, itd_s=itd, peak=peak, energy_db=energy,
                     reliable=reliable, band_centers_hz=band_layout(sr)[2])


def line_summary(cues: CueTracks):
    """Median (ild_db per band, itd_s) over reliable frames; a line with none is centred."""
    if not cues.reliable.any():
        return np.zeros(cues.ild_db.shape[1]), 0.0
    return np.median(cues.ild_db[cues.reliable], axis=0), float(np.median(cues.itd_s[cues.reliable]))


def stabilize(cues: CueTracks, decay_s: float = DECAY_S) -> CueTracks:
    """Unreliable frames hold the previous value, decaying toward the line median."""
    ild_med, itd_med = line_summary(cues)
    if not cues.reliable.any():
        return replace(cues, ild_db=np.zeros_like(cues.ild_db), itd_s=np.zeros_like(cues.itd_s))
    keep = float(np.exp(-cues.hop_s / decay_s))
    ild, itd = np.empty_like(cues.ild_db), np.empty_like(cues.itd_s)
    cur_ild, cur_itd = ild_med, itd_med
    for i in range(cues.n_frames):
        if cues.reliable[i]:
            cur_ild, cur_itd = cues.ild_db[i], cues.itd_s[i]
        else:
            cur_ild = ild_med + (cur_ild - ild_med) * keep
            cur_itd = itd_med + (cur_itd - itd_med) * keep
        ild[i], itd[i] = cur_ild, cur_itd
    return replace(cues, ild_db=ild, itd_s=itd)


def one_pole(x: np.ndarray, tau_s: float, hop_s: float) -> np.ndarray:
    """Causal one-pole smoothing along axis 0; it lags the input by about tau_s."""
    x = np.asarray(x, dtype=np.float64)
    keep = float(np.exp(-hop_s / tau_s))
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, x.shape[0]):
        y[i] = keep * y[i - 1] + (1.0 - keep) * x[i]
    return y


def smooth(cues: CueTracks, tau_s: float = SMOOTH_TAU_S) -> CueTracks:
    return replace(cues, ild_db=one_pole(cues.ild_db, tau_s, cues.hop_s),
                   itd_s=one_pole(cues.itd_s, tau_s, cues.hop_s))


def track_cues(left, right, sr: int) -> CueTracks:
    """Estimate, fill unreliable frames and smooth: the tracks the renderers expect."""
    return smooth(stabilize(estimate_cues(left, right, sr)))


def level_track(cues: CueTracks, tau_s: float = LEVEL_TAU_S, clamp_db: float = LEVEL_CLAMP_DB) -> np.ndarray:
    """Per-frame level (dB) relative to the line's median reliable level; 0 where unreliable."""
    if not cues.reliable.any():
        return np.zeros(cues.n_frames)
    rel = np.where(cues.reliable, cues.energy_db - np.median(cues.energy_db[cues.reliable]), 0.0)
    return np.clip(one_pole(rel, tau_s, cues.hop_s), -clamp_db, clamp_db)


@lru_cache(maxsize=8)
def _interp_taps(sr: int):
    centers = band_layout(sr)[2]
    freqs = np.maximum(bin_freqs(sr), 1.0)
    pos = np.interp(np.log(freqs), np.log(centers), np.arange(N_BANDS))
    base = np.minimum(np.floor(pos).astype(int), N_BANDS - 2)
    return base, pos - base


def interp_band_gains(values: np.ndarray, sr: int) -> np.ndarray:
    """Interpolate per-band values (..., N_BANDS) to every FFT bin on a log-frequency axis; ends are held."""
    base, frac = _interp_taps(sr)
    return values[..., base] * (1.0 - frac) + values[..., base + 1] * frac


def resample_frames(arr: np.ndarray, n: int) -> np.ndarray:
    """Stretch a per-frame track to n frames over normalised time (a dub clip is not the source's length)."""
    m = arr.shape[0]
    if m == n:
        return arr
    if m == 1:
        return np.repeat(arr, n, axis=0)
    pos = np.linspace(0.0, m - 1, n) if n > 1 else np.zeros(1)
    base = np.minimum(np.floor(pos).astype(int), m - 2)
    frac = (pos - base).reshape((-1,) + (1,) * (arr.ndim - 1))
    return arr[base] * (1.0 - frac) + arr[base + 1] * frac
