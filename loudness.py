"""ITU-R BS.1770-4 gated loudness of a mono signal, and the gain that matches a clip to a source.

numpy only: the K-weighting is applied as |H_K(f)|^2 on each block's power spectrum rather than
with IIR filters. Method and caveats are in docs/specs/stereo-track.md.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# Returned for silence, and treated as "nothing to match" by match_gain; never NaN or -inf.
LUFS_FLOOR = -70.0
BLOCK_S = 0.4
STEP_S = 0.1
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
LUFS_OFFSET = -0.691
MAX_BOOST_CAP_DB = 12.0
DEFAULT_PEAK_CEILING_DBFS = -1.0

# Pre-filter (high shelf) and RLB (high-pass) parameters that reproduce the standard's 48 kHz
# coefficients and extend them to other sample rates.
_SHELF_F0, _SHELF_GAIN_DB, _SHELF_Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
_HPF_F0, _HPF_Q = 38.13547087602444, 0.5003270373238773
_FFT_BATCH = 64


def k_weighting_coefficients(sr: int):
    """((b, a) shelf, (b, a) high-pass) biquad coefficients with a0 = 1."""
    k = np.tan(np.pi * _SHELF_F0 / sr)
    vh = 10.0 ** (_SHELF_GAIN_DB / 20.0)
    vb = vh ** 0.4996667741545416
    a0 = 1.0 + k / _SHELF_Q + k * k
    shelf = (np.array([(vh + vb * k / _SHELF_Q + k * k) / a0, 2.0 * (k * k - vh) / a0,
                       (vh - vb * k / _SHELF_Q + k * k) / a0]),
             np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / _SHELF_Q + k * k) / a0]))
    k = np.tan(np.pi * _HPF_F0 / sr)
    a0 = 1.0 + k / _HPF_Q + k * k
    hpf = (np.array([1.0, -2.0, 1.0]),
           np.array([1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / _HPF_Q + k * k) / a0]))
    return shelf, hpf


def k_weighting_power_response(freqs_hz, sr: int) -> np.ndarray:
    """|H_K(f)|^2, the product of the two biquads' analytic responses at the given frequencies."""
    z1 = np.exp(-2j * np.pi * np.asarray(freqs_hz, dtype=np.float64) / sr)
    power = np.ones_like(z1, dtype=np.float64)
    for b, a in k_weighting_coefficients(sr):
        num = b[0] + b[1] * z1 + b[2] * z1 ** 2
        den = a[0] + a[1] * z1 + a[2] * z1 ** 2
        power *= (num.real ** 2 + num.imag ** 2) / (den.real ** 2 + den.imag ** 2)
    return power


def _k_mean_square(blocks: np.ndarray, sr: int) -> np.ndarray:
    """Mean square of each row after K-weighting, by Parseval on the rfft power spectrum."""
    n = blocks.shape[1]
    response = k_weighting_power_response(np.arange(n // 2 + 1) * (sr / n), sr)
    fold = np.full(n // 2 + 1, 2.0)
    fold[0] = 1.0
    if n % 2 == 0:
        fold[-1] = 1.0
    out = np.empty(blocks.shape[0])
    for start in range(0, blocks.shape[0], _FFT_BATCH):
        spec = np.fft.rfft(blocks[start:start + _FFT_BATCH], axis=1)
        out[start:start + _FFT_BATCH] = ((spec.real ** 2 + spec.imag ** 2) * fold * response).sum(axis=1) / n ** 2
    return out


def _lufs(mean_square: float) -> float:
    return LUFS_OFFSET + 10.0 * np.log10(max(mean_square, 1e-30))


def _threshold_ms(lufs: float) -> float:
    return 10.0 ** ((lufs - LUFS_OFFSET) / 10.0)


def integrated_lufs(x, sr: int) -> float:
    """Gated loudness of a mono signal in LUFS; LUFS_FLOOR for silence or an empty signal.

    A signal shorter than one 400 ms block has nothing to gate, so it reads as the ungated
    K-weighted mean square of the whole signal.
    """
    x = np.asarray(x, dtype=np.float64)
    block = int(round(BLOCK_S * sr))
    if x.size == 0 or not np.all(np.isfinite(x)):
        return LUFS_FLOOR
    if x.size < block:
        value = _lufs(_k_mean_square(x[None, :], sr)[0])
        return value if value > ABSOLUTE_GATE_LUFS else LUFS_FLOOR
    blocks = sliding_window_view(x, block)[::int(round(STEP_S * sr))]
    ms = _k_mean_square(blocks, sr)
    ms = ms[ms > _threshold_ms(ABSOLUTE_GATE_LUFS)]
    if ms.size == 0:
        return LUFS_FLOOR
    ms = ms[ms > _threshold_ms(_lufs(ms.mean()) + RELATIVE_GATE_LU)]
    return max(_lufs(ms.mean()), LUFS_FLOOR)


def match_gain(source_lufs: float, clip_lufs: float, boost_cap_db: float = 6.0,
               clip_peak: float | None = None,
               peak_ceiling_dbfs: float = DEFAULT_PEAK_CEILING_DBFS) -> float:
    """Linear gain that brings the clip to the source's loudness.

    Boosts are limited to `boost_cap_db` (clamped to 0..12) and, when `clip_peak` (linear) is
    given, so the boosted peak stays under `peak_ceiling_dbfs`. Attenuation has no limit. If either
    loudness is silent or not finite there is nothing to match and the gain is 1.
    """
    if not (np.isfinite(source_lufs) and np.isfinite(clip_lufs)):
        return 1.0
    if source_lufs <= LUFS_FLOOR or clip_lufs <= LUFS_FLOOR:
        return 1.0
    cap = float(np.clip(boost_cap_db, 0.0, MAX_BOOST_CAP_DB))
    gain = 10.0 ** (min(source_lufs - clip_lufs, cap) / 20.0)
    if clip_peak is not None and np.isfinite(clip_peak) and clip_peak > 0 and gain > 1.0:
        gain = min(gain, max(1.0, 10.0 ** (peak_ceiling_dbfs / 20.0) / clip_peak))
    return float(gain)
