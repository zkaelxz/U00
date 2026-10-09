"""vad_segments.py -- speech spans from voice-activity detection, capped in length.

Pure span building for a VAD-first transcription path: find where speech is,
then cut any span longer than a model's comfortable window at the quietest
point near the limit so a cut rarely lands inside a word. Nothing here reads
settings or touches the pipeline; callers pass a float mono waveform and its
sample rate. Times are seconds, like the rest of the pipeline.
"""
from typing import Callable, Iterable, NamedTuple, Optional

import numpy as np

# Silero (inside faster-whisper) only accepts 8 kHz or 16 kHz audio.
_VAD_SAMPLE_RATE = 16000
_FRAME_S = 0.02
# A piece shorter than this is too little context for an ASR model.
_MIN_PIECE_S = 1.0


class Span(NamedTuple):
    start_s: float
    end_s: float


class VadNotInstalledError(ImportError):
    """faster-whisper (which bundles the Silero VAD) is not installed."""


class VadFnFailed(Exception):
    """An injected vad_fn broke while scoring; speech_spans uses Silero instead."""


def _silero_spans(audio, sr, threshold, min_silence_ms, min_speech_ms):
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError as exc:
        raise VadNotInstalledError(str(exc)) from exc
    # Padding is applied by speech_spans so it also works with an injected vad_fn.
    options = VadOptions(threshold=threshold, min_silence_duration_ms=min_silence_ms,
                         min_speech_duration_ms=min_speech_ms, speech_pad_ms=0)
    stamps = get_speech_timestamps(audio, options, sampling_rate=sr)
    return [(s["start"] / sr, s["end"] / sr) for s in stamps]


def _normalize(spans: Iterable, duration: float) -> list:
    """Clamp to [0, duration], drop empty spans, sort, and merge overlaps."""
    clamped = []
    for start, end in spans:
        start, end = max(0.0, float(start)), min(duration, float(end))
        if end > start:
            clamped.append(Span(start, end))
    clamped.sort()
    out = []
    for span in clamped:
        if out and span.start_s <= out[-1].end_s:
            out[-1] = Span(out[-1].start_s, max(out[-1].end_s, span.end_s))
        else:
            out.append(span)
    return out


def speech_spans(audio, sr: int, *, threshold: float = 0.5, min_silence_ms: int = 300,
                 min_speech_ms: int = 250, pad_ms: int = 100,
                 vad_fn: Optional[Callable] = None) -> list:
    """Sorted, non-overlapping speech spans in seconds, clamped to the audio.

    vad_fn(audio, sr) -> iterable of (start_s, end_s) replaces Silero (tests,
    other detectors); it raises VadFnFailed to ask for Silero after all. Spans shorter than min_speech_ms are dropped, then each
    is widened by pad_ms per side so word edges survive; spans the padding
    makes touch are merged."""
    audio = np.asarray(audio)
    if audio.size == 0:
        return []
    duration = audio.shape[0] / sr
    if vad_fn is None:
        raw = _silero_spans(audio, sr, threshold, min_silence_ms, min_speech_ms)
    else:
        try:
            raw = vad_fn(audio, sr)
        except VadFnFailed:
            raw = _silero_spans(audio, sr, threshold, min_silence_ms, min_speech_ms)
    pad = pad_ms / 1000.0
    kept = [(s - pad, e + pad) for s, e in raw if (e - s) * 1000.0 >= min_speech_ms]
    return _normalize(kept, duration)


def merge_close(spans, gap_s: float = 0.3) -> list:
    """Joins neighbouring spans separated by at most gap_s."""
    out = []
    for span in sorted(spans):
        if out and span.start_s - out[-1].end_s <= gap_s:
            out[-1] = Span(out[-1].start_s, max(out[-1].end_s, span.end_s))
        else:
            out.append(Span(*span))
    return out


def _quietest_cut(audio, sr, lo_s: float, hi_s: float) -> float:
    """Time of the lowest-RMS 20 ms frame between lo_s and hi_s. When several
    frames tie (digital silence), the middle of the tied frames is used."""
    frame = max(1, int(round(_FRAME_S * sr)))
    first = int(lo_s * sr)
    n = (int(hi_s * sr) - first) // frame
    if n <= 0:
        return hi_s
    chunk = np.asarray(audio[first:first + n * frame], dtype=np.float64).reshape(n, frame)
    rms = np.sqrt(np.mean(chunk * chunk, axis=1))
    ties = np.flatnonzero(rms <= rms.min() + 1e-12)
    best = ties[len(ties) // 2]
    return float((first + best * frame + frame / 2) / sr)


def cap_spans(spans, audio, sr: int, *, max_s: float = 15.0,
              search_window_s: float = 3.0) -> list:
    """Cuts spans longer than max_s into pieces of at most max_s.

    Each cut is the quietest point in the last search_window_s before max_s of
    the remaining span, kept at least 1 s from both ends so no piece is tiny.
    Pieces tile the original span exactly; spans within max_s are unchanged."""
    audio = np.asarray(audio)
    out = []
    for span in spans:
        start, end = span
        while end - start > max_s:
            lo = max(start + max_s - search_window_s, start + _MIN_PIECE_S)
            hi = min(start + max_s, end - _MIN_PIECE_S)
            cut = _quietest_cut(audio, sr, lo, hi) if hi > lo else start + max_s
            cut = min(max(cut, start + _MIN_PIECE_S), start + max_s)
            out.append(Span(start, cut))
            start = cut
        out.append(Span(start, end))
    return out


def context_windows(spans, duration_s: float, pad_s: float = 2.0) -> list:
    """Each span widened by up to pad_s per side into the silence around it,
    never into a neighbouring span or past [0, duration_s].

    Qwen3-ASR transcribes a clip cut tight to the speech worse than the same
    clip with its surrounding silence: it writes numbers as words instead of
    digits (a 60-utterance FLEURS check in docs/asr-experiments.md). The
    widened window is only what the model hears; a line keeps its span's times."""
    spans = list(spans)
    out = []
    for n, (start, end) in enumerate(spans):
        floor = spans[n - 1][1] if n else 0.0
        ceiling = spans[n + 1][0] if n + 1 < len(spans) else duration_s
        out.append(Span(max(floor, start - pad_s), min(ceiling, end + pad_s)))
    return out
