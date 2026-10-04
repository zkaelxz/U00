import builtins

import numpy as np
import pytest

import vad_segments
from vad_segments import Span, cap_spans, merge_close, speech_spans

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
