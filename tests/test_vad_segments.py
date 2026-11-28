import builtins

import numpy as np
import pytest

import vad_segments
from vad_segments import Span, cap_spans, fixed_spans, merge_close, speech_spans

SR = 16000


def tone(seconds, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def fake(spans):
    return lambda audio, sr: spans


def test_empty_audio():
    assert speech_spans(np.zeros(0, dtype=np.float32), SR, vad_fn=fake([(0, 1)])) == []


def test_pure_silence_has_no_spans():
    audio = np.zeros(SR * 5, dtype=np.float32)
    assert speech_spans(audio, SR, vad_fn=fake([])) == []


def test_one_short_span_is_padded():
    audio = tone(5)
    assert speech_spans(audio, SR, vad_fn=fake([(1.0, 2.0)])) == [Span(0.9, 2.1)]


def test_sorted_clamped_and_merged():
    audio = tone(5)
    raw = [(4.5, 6.0), (-1.0, 0.5), (2.0, 2.5), (2.55, 3.0)]
    got = speech_spans(audio, SR, vad_fn=fake(raw))
    assert got == [Span(0.0, 0.6), Span(1.9, 3.1), Span(4.4, 5.0)]


def test_short_speech_dropped():
    audio = tone(5)
    assert speech_spans(audio, SR, vad_fn=fake([(1.0, 1.1)])) == []


def test_vad_fn_receives_audio_and_rate():
    seen = []

    def vad(audio, sr):
        seen.append((len(audio), sr))
        return []

    speech_spans(tone(2), SR, vad_fn=vad)
    assert seen == [(2 * SR, SR)]


def test_missing_faster_whisper(monkeypatch):
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("faster_whisper"):
            raise ImportError("no faster_whisper")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(vad_segments.VadNotInstalledError):
        speech_spans(tone(1), SR)


def test_merge_close():
    spans = [Span(5.0, 6.0), Span(0.0, 1.0), Span(1.2, 2.0)]
    assert merge_close(spans, gap_s=0.3) == [Span(0.0, 2.0), Span(5.0, 6.0)]


def test_short_span_passes_through():
    audio = tone(20)
    assert cap_spans([Span(1.0, 16.0)], audio, SR) == [Span(1.0, 16.0)]


def test_long_span_cut_at_quiet_point():
    audio = tone(45)
    quiet = slice(int(15.5 * SR), int(15.7 * SR))
    audio[quiet] = 0.0
    pieces = cap_spans([Span(2.0, 42.0)], audio, SR)
    assert all(p.end_s - p.start_s <= 15.0 for p in pieces)
    assert 15.5 <= pieces[0].end_s <= 15.7
    assert all(p.end_s > p.start_s for p in pieces)
    assert pieces[0].start_s == 2.0 and pieces[-1].end_s == 42.0
    for a, b in zip(pieces, pieces[1:]):
        assert a.end_s == b.start_s


def test_no_tiny_trailing_piece():
    audio = tone(40)
    pieces = cap_spans([Span(0.0, 15.2)], audio, SR)
    assert all(p.end_s - p.start_s >= 1.0 for p in pieces)
    assert pieces[0].start_s == 0.0 and pieces[-1].end_s == 15.2


def test_cap_is_deterministic_on_silence():
    audio = np.zeros(SR * 40, dtype=np.float32)
    first = cap_spans([Span(0.0, 40.0)], audio, SR)
    assert first == cap_spans([Span(0.0, 40.0)], audio, SR)
    assert all(p.end_s - p.start_s <= 15.0 for p in first)


def test_fixed_spans_tile_the_audio_back_to_back():
    spans = fixed_spans(20.0, 8.0)
    assert spans == [Span(0.0, 8.0), Span(8.0, 16.0), Span(16.0, 20.0)]


def test_fixed_spans_fold_a_tiny_tail_into_the_window_before_it():
    assert fixed_spans(16.4, 8.0) == [Span(0.0, 8.0), Span(8.0, 16.4)]


def test_fixed_spans_of_nothing_is_nothing():
    assert fixed_spans(0.0, 8.0) == []


def _remembering_silero(monkeypatch):
    import sys
    import types
    scored = []

    class SileroVADModel:
        def __call__(self, audio, num_samples=512):
            scored.append(audio.shape[0])
            n = num_samples if isinstance(num_samples, int) else 512
            return (np.abs(audio).reshape(-1, n).mean(axis=1) * 10)[:, None]

    fake_vad = types.SimpleNamespace(SileroVADModel=SileroVADModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(vad=fake_vad))
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", fake_vad)
    monkeypatch.setattr(vad_segments, "_remembering", False)
    assert vad_segments.remember_speech_scores()
    return SileroVADModel, scored


def test_a_file_scored_once_is_not_scored_again_and_gets_the_same_scores(monkeypatch):
    model_class, scored = _remembering_silero(monkeypatch)
    model = model_class()
    audio = np.concatenate([np.zeros(SR), tone(2)]).astype(np.float32)
    audio = np.pad(audio, (0, 512 - audio.shape[0] % 512))
    first = model(audio)
    first[:] = -1                       # a caller changing its copy changes nothing kept
    again = model(audio.copy())
    assert scored == [audio.shape[0]]
    assert (again == (np.abs(audio).reshape(-1, 512).mean(axis=1) * 10)[:, None]).all()
    other = audio.copy()
    other[0] = 0.5
    model(other)
    assert scored == [audio.shape[0]] * 2


def test_remembered_scores_give_the_same_spans_as_scoring_afresh(monkeypatch):
    pytest.importorskip("faster_whisper")
    from faster_whisper.vad import SileroVADModel, get_vad_model
    # The model is shared by the whole test process, so start from the plain method and no scores.
    fresh = getattr(SileroVADModel.__call__, "unwrapped", SileroVADModel.__call__)
    monkeypatch.setattr(SileroVADModel, "__call__", fresh)
    monkeypatch.setattr(vad_segments, "_remembering", False)
    monkeypatch.delitem(get_vad_model().__dict__, "_baihe_scores", raising=False)
    audio = np.concatenate([np.zeros(SR), tone(3), np.zeros(SR), tone(1)]).astype(np.float32)
    with monkeypatch.context() as m:
        m.setattr(vad_segments, "remember_speech_scores", lambda: False)
        expected = [speech_spans(audio, SR, threshold=t, min_silence_ms=150) for t in (0.4, 0.5)]
    assert vad_segments.remember_speech_scores()
    assert [speech_spans(audio, SR, threshold=t, min_silence_ms=150) for t in (0.4, 0.5)] == expected
    assert len(get_vad_model().__dict__["_baihe_scores"]) == 1
    padded = np.pad(audio, (0, 512 - audio.shape[0] % 512))
    assert np.array_equal(get_vad_model()(padded), fresh(get_vad_model(), padded))


def test_a_call_the_memo_cannot_key_is_scored_as_before(monkeypatch):
    model_class, scored = _remembering_silero(monkeypatch)
    audio = np.zeros(1024, dtype=np.float32)
    model_class()(audio, num_samples=[512])  # an unhashable argument
    model_class()(audio, num_samples=[512])
    assert scored == [1024, 1024]
