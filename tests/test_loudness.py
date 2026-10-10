import math

import numpy as np
import pytest

import loudness

SR = 48000


def _sine(freq=997.0, amp=1.0, seconds=3.0, sr=SR):
    return amp * np.sin(2 * np.pi * freq * np.arange(int(seconds * sr)) / sr)


def test_k_weighting_reproduces_the_standards_48khz_coefficients():
    # Table 1 and 2 of ITU-R BS.1770-4 (pre-filter and RLB filter at 48 kHz).
    shelf, hpf = loudness.k_weighting_coefficients(48000)
    np.testing.assert_allclose(shelf[0], [1.53512485958697, -2.69169618940638, 1.19839281085285], atol=1e-9)
    np.testing.assert_allclose(shelf[1], [1.0, -1.69065929318241, 0.73248077421585], atol=1e-9)
    np.testing.assert_allclose(hpf[0], [1.0, -2.0, 1.0])
    np.testing.assert_allclose(hpf[1], [1.0, -1.99004745483398, 0.99007225036621], atol=1e-9)


def test_997hz_mono_sine_at_full_scale_reads_minus_3_01_lufs():
    # From the standard's formula: L = -0.691 + 10 log10(G * mean square) with G = 1 for one channel,
    # mean square 0.5 and |H_K(997 Hz)|^2 = 0.691 dB (the offset is chosen to cancel it): -3.0103.
    expected = -0.691 + 10 * math.log10(0.5) + 10 * math.log10(float(loudness.k_weighting_power_response([997.0], SR)[0]))
    assert expected == pytest.approx(-3.0103, abs=0.001)
    assert loudness.integrated_lufs(_sine(), SR) == pytest.approx(expected, abs=0.1)
    assert loudness.integrated_lufs(_sine(sr=44100), 44100) == pytest.approx(-3.01, abs=0.1)


def test_level_changes_read_one_for_one_in_lufs():
    assert loudness.integrated_lufs(_sine(amp=0.1), SR) - loudness.integrated_lufs(_sine(), SR) == pytest.approx(-20.0, abs=0.01)


def test_k_weighting_is_a_high_pass_with_a_high_shelf():
    low, mid, high = loudness.k_weighting_power_response([20.0, 997.0, 10000.0], SR)
    assert 10 * math.log10(low) < -10
    assert 10 * math.log10(mid) == pytest.approx(0.69, abs=0.01)
    assert 10 * math.log10(high) == pytest.approx(4.0, abs=0.3)


def test_quiet_passages_are_gated_out_of_the_integrated_value():
    loud = _sine(amp=0.1, seconds=2.0)
    quiet = _sine(amp=0.1 * 10 ** (-35 / 20), seconds=2.0)
    gated = loudness.integrated_lufs(np.concatenate([loud, quiet]), SR)
    # The quiet blocks (-35 LU) fall under the relative gate; the three blocks straddling the join
    # (75%, 50%, 25% loud) stay in, so the mean power is (17 + 1.5) / 20 of the loud passage's.
    assert gated == pytest.approx(loudness.integrated_lufs(loud, SR) + 10 * math.log10(18.5 / 20), abs=0.05)
    assert gated > loudness.integrated_lufs(loud, SR) - 0.5


def test_everything_below_the_absolute_gate_is_silence():
    assert loudness.integrated_lufs(_sine(amp=10 ** (-80 / 20)), SR) == loudness.LUFS_FLOOR


def test_line_shorter_than_a_block_uses_the_ungated_mean_square():
    short = _sine(seconds=0.2)
    assert loudness.integrated_lufs(short, SR) == pytest.approx(-3.01, abs=0.1)


@pytest.mark.parametrize("signal", [np.zeros(SR), np.zeros(100), np.zeros(0), np.full(SR, np.nan)])
def test_silence_and_junk_never_give_nan(signal):
    value = loudness.integrated_lufs(signal, SR)
    assert value == loudness.LUFS_FLOOR
    assert math.isfinite(value)


def test_match_gain_boosts_up_to_the_cap_only():
    assert loudness.match_gain(-23.0, -27.0) == pytest.approx(10 ** (4 / 20))
    assert loudness.match_gain(-23.0, -40.0) == pytest.approx(10 ** (6 / 20))
    assert loudness.match_gain(-23.0, -40.0, boost_cap_db=0.0) == pytest.approx(1.0)
    assert loudness.match_gain(-23.0, -60.0, boost_cap_db=12.0) == pytest.approx(10 ** (12 / 20))
    assert loudness.match_gain(-23.0, -60.0, boost_cap_db=50.0) == pytest.approx(10 ** (12 / 20))
    assert loudness.match_gain(-23.0, -60.0, boost_cap_db=-3.0) == pytest.approx(1.0)


def test_match_gain_attenuation_has_no_lower_limit():
    assert loudness.match_gain(-40.0, -10.0) == pytest.approx(10 ** (-30 / 20))
    assert loudness.match_gain(-60.0, -10.0, boost_cap_db=0.0) == pytest.approx(10 ** (-50 / 20))


def test_peak_guard_limits_a_boost_but_never_attenuates():
    ceiling = 10 ** (loudness.DEFAULT_PEAK_CEILING_DBFS / 20)
    boosted = loudness.match_gain(-23.0, -29.0, clip_peak=0.8)
    assert boosted == pytest.approx(ceiling / 0.8)
    assert boosted * 0.8 <= ceiling + 1e-12
    assert loudness.match_gain(-23.0, -29.0, clip_peak=0.1) == pytest.approx(10 ** (6 / 20))
    assert loudness.match_gain(-23.0, -23.0, clip_peak=0.99) == pytest.approx(1.0)
    assert loudness.match_gain(-40.0, -10.0, clip_peak=0.99) == pytest.approx(10 ** (-30 / 20))


@pytest.mark.parametrize("source,clip", [(loudness.LUFS_FLOOR, -20.0), (-20.0, loudness.LUFS_FLOOR),
                                          (float("nan"), -20.0), (-20.0, float("-inf"))])
def test_match_gain_with_nothing_to_match_is_unity(source, clip):
    assert loudness.match_gain(source, clip) == 1.0


def test_matching_a_quiet_clip_lands_on_the_source_loudness():
    source = loudness.integrated_lufs(_sine(amp=0.2), SR)
    clip = _sine(amp=0.1)
    gain = loudness.match_gain(source, loudness.integrated_lufs(clip, SR))
    assert loudness.integrated_lufs(clip * gain, SR) == pytest.approx(source, abs=0.05)
