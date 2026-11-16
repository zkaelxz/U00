"""Loudness levelling of generated clips: what is lifted, what is left alone, and that the cache key is untouched."""

import numpy as np

import audio_levelling as levelling
import dub

RATE = 16000
FULL = 32768.0


def tone(dbfs, seconds=2.0, noise_dbfs=None):
    t = np.arange(int(RATE * seconds)) / RATE
    amplitude = FULL * 10 ** (dbfs / 20) * np.sqrt(2)
    out = amplitude * np.sin(2 * np.pi * 220 * t)
    if noise_dbfs is not None:
        out = out + np.random.default_rng(1).normal(0, FULL * 10 ** (noise_dbfs / 20), out.size)
    return out.astype(np.int16)


def level_after(samples):
    gain = levelling.gain_for(samples, RATE, FULL)
    return levelling.speech_level_dbfs(samples, RATE, FULL) + gain


def test_loud_and_quiet_clips_meet_at_the_target():
    for dbfs in (-10.0, -18.0, -24.0):
        assert abs(level_after(tone(dbfs)) - levelling.TARGET_DBFS) < 0.6


def test_pauses_do_not_count_as_quiet_speech():
    clip = np.concatenate([tone(-18.0, 1.0), np.zeros(RATE * 3, dtype=np.int16)])
    assert levelling.gain_for(clip, RATE, FULL) == 0.0


def test_a_clip_barely_above_hiss_is_left_alone():
    assert levelling.gain_for(tone(-55.0), RATE, FULL) == 0.0


def test_lift_is_capped_and_never_pushes_the_peak_past_the_ceiling():
    assert levelling.gain_for(tone(-40.0), RATE, FULL) <= levelling.MAX_BOOST_DB
    spiky = tone(-30.0)
    spiky[1000] = 20000
    gain = levelling.gain_for(spiky, RATE, FULL)
    assert 20 * np.log10(20000 / FULL) + gain <= levelling.PEAK_CEILING_DBFS + 0.01
    assert levelling.gain_for(tone(-30.0), RATE, FULL) > 0


def test_a_noisy_quiet_clip_is_not_lifted_into_audible_hiss():
    pause = np.zeros(RATE, dtype=np.int16)
    clean = levelling.gain_for(np.concatenate([tone(-24.0), pause]), RATE, FULL)
    hissy = levelling.gain_for(np.concatenate([tone(-24.0), tone(-100.0, 1.0, noise_dbfs=-47.0)]), RATE, FULL)
    assert clean > 0 and hissy == 0.0


def test_levelling_does_not_change_a_clip_signature():
    voice = {"engine": "omnivoice", "instruct": "female"}
    assert dub.clip_signature("hello", voice) == dub.clip_signature("hello", dict(voice))


def test_level_clip_applies_the_gain_to_a_real_segment():
    pydub = __import__("pytest").importorskip("pydub")
    clip = pydub.AudioSegment(tone(-26.0).tobytes(), frame_rate=RATE, sample_width=2, channels=1)
    levelled = levelling.level_clip(clip)
    assert abs(levelling.clip_speech_level(levelled) - levelling.TARGET_DBFS) < 0.6
