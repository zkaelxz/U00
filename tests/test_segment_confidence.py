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


def _word(text, start, prob):
    return {"word": text, "start": start, "end": start + 0.4, "probability": prob}


def test_a_sentence_is_scored_from_its_own_words_against_its_segment():
    from segment_splitting import scaled_line_confidence
    assert scaled_line_confidence(0.5, [_word("a", 0, 0.9)], 0.6) == 0.75
    assert scaled_line_confidence(0.5, [_word("a", 0, 0.3)], 0.6) == 0.25
    assert scaled_line_confidence(0.9, [_word("a", 0, 0.99)], 0.5) == 1.0


@pytest.mark.parametrize("words,segment_mean", [
    ([{"word": "a", "start": 0, "end": 1}], 0.6),   # another engine: no probabilities
    (None, 0.6), ([_word("a", 0, 0.9)], None), ([_word("a", 0, 0.9)], 0.0)])
def test_without_word_probabilities_the_segment_score_stays(words, segment_mean):
    from segment_splitting import scaled_line_confidence
    assert scaled_line_confidence(0.5, words, segment_mean) == 0.5
    assert scaled_line_confidence(None, [_word("a", 0, 0.9)], 0.6) is None


def test_the_pipeline_scores_each_sentence_of_a_long_segment_on_its_own():
    from types import SimpleNamespace
    from services import transcribe_pipeline as tp
    words = [_word("你好。", 0.0, 0.9), _word("再见。", 10.0, 0.3)]
    run = tp._PipelineRun.__new__(tp._PipelineRun)
    run.segments = [{"start": 0.0, "end": 10.4, "text": "你好。再见。", "words": words, "confidence": 0.5}]
    run.o = SimpleNamespace(min_pause_sec=1.0, timing={})
    run.tuning = SimpleNamespace(max_seconds=8, max_cjk_chars=40, floors=tp.segment_splitting.DEFAULT_FLOORS,
                                 blocked_phrases=(), stock_isolation_sec=3.0)
    run.sentence_lines = False
    run.audio_path = "unused"
    lines = run._split_into_lines()
    assert len(lines) == 2 and lines[0].confidence > 0.5 > lines[1].confidence
