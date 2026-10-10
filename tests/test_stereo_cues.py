import itertools

import numpy as np
import pytest

import stereo_cues as sc
from tests.stereo_fixtures import SR, delayed_varying, in_band_bands, place, speech_like

ITDS_S = (-0.6e-3, 0.0, 0.4e-3)
ILDS_DB = (-8.0, 0.0, 6.0)


def _noise(like, snr_db, seed):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(like.size) * np.sqrt(np.mean(like ** 2)) * 10 ** (-snr_db / 20)


def test_stft_round_trips_any_length():
    rng = np.random.default_rng(0)
    for n in (1, 700, 1024, 5000):
        x = rng.standard_normal(n)
        assert sc.stft(x).shape[0] == sc.frame_count(n)
        np.testing.assert_allclose(sc.istft(sc.stft(x), n), x, atol=1e-10)


def test_istft_offset_reads_back_a_delayed_frame():
    x = np.random.default_rng(1).standard_normal(3000)
    spec = sc.stft(x)
    omega = 2 * np.pi * np.arange(spec.shape[1]) / sc.NFFT
    shifted = spec * np.exp(-1j * omega * 20)
    np.testing.assert_allclose(sc.istft(shifted, x.size, offset=20), x, atol=1e-10)


@pytest.mark.parametrize("sr,top_hz", [(48000, 18000.0), (16000, 7200.0)])
def test_band_layout_covers_100hz_to_the_cap_with_a_bin_each(sr, top_hz):
    lo, hi, centers = sc.band_layout(sr)
    assert len(centers) == sc.N_BANDS == 28
    assert np.all(hi > lo)
    assert centers[0] > 100 and centers[-1] < top_hz
    assert np.all(np.diff(centers) > 0)
    assert np.allclose(np.diff(np.log(centers)), np.log(top_hz / 100.0) / sc.N_BANDS)


@pytest.mark.parametrize("itd,ild", list(itertools.product(ITDS_S, ILDS_DB)))
def test_known_placement_is_recovered_on_reliable_frames(itd, ild):
    left, right = place(speech_like(), itd, ild)
    cues = sc.estimate_cues(left, right, SR)
    rel = cues.reliable
    assert rel.sum() > 50
    assert np.abs(cues.itd_s[rel] - itd).max() < 0.05e-3
    # Per-frame ILD is the median over in-band bands; single narrow bands scatter by up to ~2 dB.
    frame_ild = np.median(cues.ild_db[:, in_band_bands(cues, 300, 5000)], axis=1)
    assert np.abs(frame_ild[rel] - ild).max() < 1.0


def test_sign_convention_positive_means_left():
    left, right = place(speech_like(), 0.4e-3, 6.0)
    cues = sc.estimate_cues(left, right, SR)
    assert np.median(cues.itd_s[cues.reliable]) > 0.3e-3
    assert np.median(cues.ild_db[cues.reliable][:, in_band_bands(cues)]) > 5.0


def test_narrowband_source_costs_itd_accuracy_and_peak_height():
    # PHAT weights every bin equally, so empty bins outside a 200-3500 Hz source add leakage
    # phase error: measured worst frame ~0.09 ms (vs ~0.005 ms full band) and peak ~0.5 (vs ~1.0).
    wide = speech_like()
    narrow = speech_like(band=(200.0, 3500.0))
    c_wide = sc.estimate_cues(*place(wide, 0.4e-3, 0.0), SR)
    c_narrow = sc.estimate_cues(*place(narrow, 0.4e-3, 0.0), SR)
    err = np.abs(c_narrow.itd_s[c_narrow.reliable] - 0.4e-3)
    assert err.max() < 0.12e-3
    assert err.max() > 5 * np.abs(c_wide.itd_s[c_wide.reliable] - 0.4e-3).max()
    assert c_narrow.peak[c_narrow.reliable].mean() < 0.7 * c_wide.peak[c_wide.reliable].mean()


def test_uncorrelated_noise_at_10db_snr_keeps_itd_within_a_tenth_of_a_ms():
    voice = speech_like()
    left, right = place(voice, 0.4e-3, 6.0)
    cues = sc.estimate_cues(left + _noise(voice, 10, 1), right + _noise(voice, 10, 2), SR)
    rel = cues.reliable
    assert rel.sum() > 30
    assert np.abs(cues.itd_s[rel] - 0.4e-3).max() < 0.1e-3


def test_louder_decoy_takes_over_the_cues_without_lowering_confidence():
    # Measured, not hidden: an independent source at ITD -0.5 ms wins the ITD once it is as loud as
    # the voice (+0.4 ms), and the GCC peak stays ~1, so the reliability mask cannot flag the bias.
    voice = speech_like(seed=0)
    decoy = np.roll(speech_like(seed=7), SR // 6)
    voice_l, voice_r = place(voice, 0.4e-3, 0.0)
    decoy_l, decoy_r = place(decoy, -0.5e-3, 0.0)
    medians, peaks = {}, {}
    for decoy_db in (-6, 6):
        gain = 10 ** (decoy_db / 20)
        cues = sc.estimate_cues(voice_l + gain * decoy_l, voice_r + gain * decoy_r, SR)
        medians[decoy_db] = np.median(cues.itd_s[cues.reliable])
        peaks[decoy_db] = cues.peak[cues.reliable].mean()
    assert medians[-6] == pytest.approx(0.4e-3, abs=0.05e-3)
    assert medians[6] == pytest.approx(-0.5e-3, abs=0.05e-3)
    assert peaks[6] > 0.9


def test_moving_source_is_tracked_after_the_smoothing_delay():
    n = 3 * SR
    t = np.arange(n) / SR
    delay = (-0.5e-3 + 0.33e-3 * t) * SR
    src = speech_like(seed=2)
    left, right = delayed_varying(src, -delay / 2), delayed_varying(src, delay / 2)
    raw = sc.estimate_cues(left, right, SR)
    frame_t = (np.arange(raw.n_frames) * sc.HOP - sc.FRAME // 4) / SR
    truth = -0.5e-3 + 0.33e-3 * frame_t
    assert np.abs(raw.itd_s - truth)[raw.reliable].max() < 0.02e-3

    tracked = sc.smooth(sc.stabilize(raw))
    lag = int(round(sc.SMOOTH_TAU_S / raw.hop_s))
    err = np.abs(tracked.itd_s[lag:] - truth[:-lag])
    seen = raw.reliable[lag:] & (np.arange(err.size) > 30)
    # Held frames decay toward the line median, so only measured frames are compared.
    assert err[seen].max() < 0.1e-3
    assert np.abs(np.median(raw.itd_s[raw.reliable]) - truth).max() > 0.4e-3


def test_one_pole_lags_a_ramp_by_about_its_time_constant():
    hop, tau = 0.016, 0.15
    ramp = np.arange(400) * hop
    keep = np.exp(-hop / tau)
    # Steady-state lag of a discrete one-pole on a ramp is hop * keep / (1 - keep), just under tau.
    np.testing.assert_allclose(ramp[300] - sc.one_pole(ramp, tau, hop)[300], hop * keep / (1 - keep), atol=1e-6)


def test_silent_and_noise_only_spans_have_no_reliable_frames_and_are_centred():
    rng = np.random.default_rng(3)
    steady = rng.standard_normal(SR)
    for left, right in ((np.zeros(SR), np.zeros(SR)), (steady, np.roll(steady, 5))):
        raw = sc.estimate_cues(left, right, SR)
        assert not raw.reliable.any()
        fixed = sc.stabilize(raw)
        assert not fixed.ild_db.any() and not fixed.itd_s.any()


def test_unreliable_frames_hold_then_decay_toward_the_line_median():
    raw = sc.estimate_cues(*place(speech_like(), 0.4e-3, 6.0), SR)
    fixed = sc.stabilize(raw)
    assert (~raw.reliable).any()
    first_gap = int(np.flatnonzero(~raw.reliable & (np.arange(raw.n_frames) > np.argmax(raw.reliable)))[0])
    median = np.median(raw.itd_s[raw.reliable])
    assert fixed.itd_s[first_gap] == pytest.approx(median + (raw.itd_s[first_gap - 1] - median) * np.exp(-raw.hop_s / sc.DECAY_S))
    np.testing.assert_array_equal(fixed.itd_s[raw.reliable], raw.itd_s[raw.reliable])


def test_interp_band_gains_hits_band_centres_and_holds_the_ends():
    values = np.arange(sc.N_BANDS, dtype=float)
    per_bin = sc.interp_band_gains(values, SR)
    freqs = sc.bin_freqs(SR)
    centres = sc.band_layout(SR)[2]
    assert per_bin[0] == 0.0 and per_bin[-1] == sc.N_BANDS - 1
    assert np.all(np.diff(per_bin) >= 0)
    k = int(np.argmin(np.abs(freqs - centres[10])))
    assert per_bin[k] == pytest.approx(10.0, abs=0.2)


def test_resample_frames_stretches_over_normalised_time():
    track = np.array([[0.0], [10.0]])
    np.testing.assert_allclose(sc.resample_frames(track, 5)[:, 0], [0, 2.5, 5, 7.5, 10])
    assert sc.resample_frames(track, 2) is track
    np.testing.assert_array_equal(sc.resample_frames(np.array([3.0]), 4), [3, 3, 3, 3])


def test_level_track_is_clamped_and_zero_without_reliable_frames():
    raw = sc.estimate_cues(*place(speech_like(), 0.0, 0.0), SR)
    level = sc.level_track(raw)
    assert np.abs(level).max() <= sc.LEVEL_CLAMP_DB + 1e-9 and level.any()
    silent = sc.estimate_cues(np.zeros(SR), np.zeros(SR), SR)
    assert not sc.level_track(silent).any()
