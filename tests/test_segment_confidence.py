"""whisper_transcribe.segment_confidence and its path from a Whisper segment to a Line."""
import math
from types import SimpleNamespace

import pytest

import whisper_transcribe as wt


def _seg(**kw):
    base = dict(start=0.0, end=2.0, text="你好", words=None)
    return SimpleNamespace(**{**base, **kw})


def test_mean_token_probability_is_the_base():
    assert wt.segment_confidence(_seg(avg_logprob=math.log(0.8), no_speech_prob=0.0,
                                      compression_ratio=1.2)) == 0.8


def test_no_speech_chance_and_repetition_lower_it():
    clean = wt.segment_confidence(_seg(avg_logprob=-0.2, no_speech_prob=0.0, compression_ratio=1.0))
    quiet = wt.segment_confidence(_seg(avg_logprob=-0.2, no_speech_prob=0.5, compression_ratio=1.0))
    looping = wt.segment_confidence(_seg(avg_logprob=-0.2, no_speech_prob=0.0, compression_ratio=3.0))
    assert quiet == pytest.approx(clean / 2, abs=0.001)
    assert looping == pytest.approx(clean / 2, abs=0.001)


def test_stays_within_zero_and_one():
    assert wt.segment_confidence(_seg(avg_logprob=0.5)) == 1.0
    assert wt.segment_confidence(_seg(avg_logprob=-50.0, no_speech_prob=1.0)) == 0.0


@pytest.mark.parametrize("seg", [_seg(), _seg(avg_logprob=None), _seg(avg_logprob="x"), _seg(avg_logprob=True)])
def test_none_when_whisper_reported_nothing_usable(seg):
    assert wt.segment_confidence(seg) is None


def test_segment_dicts_carry_it_only_when_known():
    out = wt._segment_dicts([_seg(avg_logprob=-0.1, no_speech_prob=0.0, compression_ratio=1.0), _seg()], set())
    assert out[0]["confidence"] == pytest.approx(math.exp(-0.1), abs=0.001)
    assert "confidence" not in out[1]


def test_pipeline_ignores_a_damaged_stored_value():
    from services.transcribe_pipeline import _usable_confidence
    assert _usable_confidence(0.5) == 0.5
    assert [_usable_confidence(v) for v in ("0.5", 7, -1, True, None)] == [None] * 5
