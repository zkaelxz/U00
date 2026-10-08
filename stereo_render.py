"""Place a mono clip in stereo from cue tracks (tiers `static`, `follow`, `rtf`).

Pure numpy DSP with no app surface; see docs/specs/stereo-track.md. Every renderer returns
(samples, 2) float64 with the same length as the mono clip, power preserving
(g_L^2 + g_R^2 = 2) and unclipped. Cue tracks come from the source line and are stretched over the
clip's duration, so a dub of a different length still follows the original's movement.
"""
from __future__ import annotations

import numpy as np

from stereo_cues import (
    HOP, MAX_ITD_S, NFFT, CueTracks, interp_band_gains, istft, level_track, line_summary,
    resample_frames, stft,
)

RTF_BLOCK_S = 10.0
RTF_SMOOTH_OCTAVES = 1.0 / 3.0
RTF_MAX_RATIO_DB = 20.0

_TINY = 1e-30


def _gains(ild_db: np.ndarray):
    """Power-preserving channel gains for an L/R power ratio r = 10^(ILD/10)."""
    r = np.power(10.0, ild_db / 10.0)
    return np.sqrt(2.0 * r / (1.0 + r)), np.sqrt(2.0 / (1.0 + r))


def _shift_samples(sr: int) -> int:
    return int(np.ceil(MAX_ITD_S * sr / 2.0)) + 2


def render_static(mono, cues: CueTracks) -> np.ndarray:
    """One ILD and one ITD for the whole line (medians): a gain and a linear-phase fractional delay."""
    x = np.asarray(mono, dtype=np.float64)
    n = x.size
    if n == 0:
        return np.zeros((0, 2))
    ild_bands, itd = line_summary(cues)
    gain_l, gain_r = _gains(np.array(np.median(ild_bands)))
    half = float(np.clip(itd, -MAX_ITD_S, MAX_ITD_S)) * cues.sr / 2.0
    shift = _shift_samples(cues.sr)
    size = n + 2 * shift
    spec = np.fft.rfft(x, n=size)
    omega = 2.0 * np.pi * np.arange(size // 2 + 1) / size
    out_l = np.fft.irfft(spec * gain_l * np.exp(1j * omega * (half - shift)), n=size)
    out_r = np.fft.irfft(spec * gain_r * np.exp(-1j * omega * (half + shift)), n=size)
    return np.stack([out_l[shift:shift + n], out_r[shift:shift + n]], axis=1)


def render_follow(mono, cues: CueTracks) -> np.ndarray:
    """Per-frame band gains plus a per-frame +-tau/2 linear-phase delay, in the STFT domain."""
    x = np.asarray(mono, dtype=np.float64)
    if x.size == 0:
        return np.zeros((0, 2))
    spec = stft(x)
    frames = spec.shape[0]
    ild = resample_frames(cues.ild_db, frames)
    itd = np.clip(resample_frames(cues.itd_s, frames), -MAX_ITD_S, MAX_ITD_S)
    gain_l, gain_r = (interp_band_gains(g, cues.sr) for g in _gains(ild))
    omega = 2.0 * np.pi * np.arange(spec.shape[1]) / NFFT
    half = (itd * cues.sr / 2.0)[:, None]
    shift = _shift_samples(cues.sr)
    out_l = istft(spec * gain_l * np.exp(1j * omega * (half - shift)), x.size, shift)
    out_r = istft(spec * gain_r * np.exp(-1j * omega * (half + shift)), x.size, shift)
    return np.stack([out_l, out_r], axis=1)


def _parametric_rtf(cues: CueTracks, n_bins: int):
    ild_bands, itd = line_summary(cues)
    gain_l, gain_r = (interp_band_gains(g, cues.sr) for g in _gains(ild_bands))
    half = float(np.clip(itd, -MAX_ITD_S, MAX_ITD_S)) * cues.sr / 2.0
    omega = 2.0 * np.pi * np.arange(n_bins) / NFFT
    return gain_l * np.exp(1j * omega * half), gain_r * np.exp(-1j * omega * half)


def _octave_smooth(mag: np.ndarray) -> np.ndarray:
    """Moving average over +-1/6 octave around each bin (a 1/3 octave window)."""
    k = np.arange(mag.size)
    span = 2.0 ** (RTF_SMOOTH_OCTAVES / 2.0)
    lo = np.floor(k / span).astype(int)
    hi = np.minimum(np.ceil(k * span).astype(int) + 1, mag.size)
    cum = np.concatenate([[0.0], np.cumsum(mag)])
    return (cum[hi] - cum[lo]) / (hi - lo)


def _block_rtf(src_l, src_r, ref, weight, param_l, param_r):
    """Covariance RTF of both channels against the mid for one block, shrunk toward the parametric model."""
    total = weight.sum()
    if total <= 0:
        return param_l, param_r
    w = weight[:, None]
    den = (w * (ref.real ** 2 + ref.imag ** 2)).sum(axis=0)
    floor = 1e-12 * den.max() + _TINY
    h, coh = [], []
    for chan in (src_l, src_r):
        num = (w * chan * np.conj(ref)).sum(axis=0)
        chan_den = (w * (chan.real ** 2 + chan.imag ** 2)).sum(axis=0)
        h.append(num / (den + floor))
        coh.append((num.real ** 2 + num.imag ** 2) / ((chan_den + floor) * (den + floor)))
    gamma = np.clip(0.5 * (coh[0] + coh[1]), 0.0, 1.0)
    return gamma * h[0] + (1 - gamma) * param_l, gamma * h[1] + (1 - gamma) * param_r


def _finish_rtf(h_l: np.ndarray, h_r: np.ndarray):
    """Smooth magnitudes over 1/3 octave, cap the L/R ratio, renormalise to |H_L|^2 + |H_R|^2 = 2."""
    mag_l, mag_r = _octave_smooth(np.abs(h_l)), _octave_smooth(np.abs(h_r))
    ratio_db = 20.0 * np.log10((mag_l + _TINY) / (mag_r + _TINY))
    gain_l, gain_r = _gains(np.clip(ratio_db, -RTF_MAX_RATIO_DB, RTF_MAX_RATIO_DB))
    return gain_l * np.exp(1j * np.angle(h_l)), gain_r * np.exp(1j * np.angle(h_r))


def _block_weights(n_clip_frames: int, n_src_frames: int, block: int):
    """Hann crossfade weights (clip frames x blocks) and block start frames, for 50% overlapping blocks."""
    if n_src_frames <= block:
        return np.ones((n_clip_frames, 1)), [0]
    hop = block // 2
    starts = [i * hop for i in range(-(-(n_src_frames - block) // hop) + 1)]
    pos = np.linspace(0.0, n_src_frames - 1, n_clip_frames) if n_clip_frames > 1 else np.zeros(1)
    weights = np.zeros((n_clip_frames, len(starts)))
    for j, start in enumerate(starts):
        x = (pos - start + 0.5) / block
        inside = (x > 0) & (x < 1)
        weights[inside, j] = 0.5 * (1.0 - np.cos(2.0 * np.pi * x[inside]))
    # Normalising keeps the first and last half block at full weight where no neighbour overlaps.
    return weights / weights.sum(axis=1, keepdims=True), starts


def render_rtf(mono, src_left, src_right, cues: CueTracks, block_s: float = RTF_BLOCK_S,
               level_follow: bool = False) -> np.ndarray:
    """EXPERIMENTAL. Covariance-based relative transfer function of the source line, per 10 s block.

    Needs the stereo source (src_left/src_right) in addition to its cue tracks. With `level_follow`
    the output also follows the source's level contour relative to its median (smoothed, +-3 dB).
    """
    x = np.asarray(mono, dtype=np.float64)
    if x.size == 0:
        return np.zeros((0, 2))
    spec_l, spec_r = stft(src_left), stft(src_right)
    ref = 0.5 * (spec_l + spec_r)
    n_bins = ref.shape[1]
    param_l, param_r = _parametric_rtf(cues, n_bins)
    reliable = cues.reliable.astype(np.float64)
    block = max(2, int(round(block_s * cues.sr / HOP)))
    block -= block % 2

    clip_spec = stft(x)
    weights, starts = _block_weights(clip_spec.shape[0], ref.shape[0], block)
    rtf_l = np.empty((len(starts), n_bins), dtype=np.complex128)
    rtf_r = np.empty_like(rtf_l)
    for j, start in enumerate(starts):
        span = slice(start, start + block)
        rtf_l[j], rtf_r[j] = _finish_rtf(*_block_rtf(
            spec_l[span], spec_r[span], ref[span], reliable[span], param_l, param_r))

    h_l, h_r = weights @ rtf_l, weights @ rtf_r
    if level_follow:
        gain = np.power(10.0, resample_frames(level_track(cues), clip_spec.shape[0]) / 20.0)[:, None]
        h_l, h_r = h_l * gain, h_r * gain
    omega = 2.0 * np.pi * np.arange(n_bins) / NFFT
    delay = np.exp(-1j * omega * _shift_samples(cues.sr))
    shift = _shift_samples(cues.sr)
    return np.stack([istft(clip_spec * h_l * delay, x.size, shift),
                     istft(clip_spec * h_r * delay, x.size, shift)], axis=1)
