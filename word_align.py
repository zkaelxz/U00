"""
word_align.py -- word-level forced alignment of Whisper's OWN transcribed
text against its own audio, via Meta's MMS (Massively Multilingual
Speech) CTC forced-alignment model, through torchaudio's MMS_FA pipeline.

WHY THIS EXISTS: transcribe_for_timing() only ever produces SEGMENT-level
timestamps -- a VAD-merged segment (see min_silence_duration_ms) can span
minutes and hold several real sentences' worth of dialogue as ONE line
with ONE start/end pair (a real documented case: a single "line"
spanning 6.4 minutes). This re-aligns that segment's own text against
its own audio slice to get real per-word timing, then regroups the
words into multiple correctly-timed lines at their actual pauses. It can
never recover text Whisper didn't already transcribe -- only re-time
what's already there.

WHY MMS, NOT WhisperX's OWN wav2vec2-XLSR-53 default: checked both
against real, documented reports before picking (not assumed). XLSR-53's
per-language fine-tunes -- what WhisperX itself defaults to for
alignment -- have a confirmed real-world report of breaking/drifting on
rare kanji in Japanese; MMS is a single, broader model reported to hold
up better on exactly that case. Montreal Forced Aligner is reportedly
more precise still (sub-15ms boundary error in one benchmark), but needs
phonetic dictionaries and a fundamentally different install/tooling path
-- not the pip-install-plus-Hugging-Face-download pattern every other
optional backend in this app follows -- for a precision gain this app's
actual use (splitting a subtitle line back into readable chunks with
roughly-correct timing, not phoneme-grade sync) doesn't need.

CJK has no space token to anchor word boundaries the way English does,
so CTC alignment on its own only ever gives per-CHARACTER timing for
Chinese/Japanese/Korean, never words directly -- that's why this always
aligns against ALREADY-SEGMENTED words (from segment.py's own jieba/
sudachipy/kiwipiepy, already built for Reader ruby-text) rather than
raw text, matching torchaudio's own documented MMS_FA usage pattern of
aligning a pre-split word list, not inferring boundaries itself.

EXPERIMENTAL, off by default: written against torchaudio's documented
MMS_FA API and uroman's documented Python API, but NOT run against real
Chinese/Japanese/Korean speech in this environment (no GPU, no real
audio, no network for the ~1.1GB model download). There IS a confirmed
real GitHub issue (m-bain/whisperX#84) of a DIFFERENT but related CTC
aligner failing outright on some Japanese text ("no characters in this
segment found in model dictionary") -- so realign_long_segment() fails
SOFT: any per-line alignment problem keeps that line's original timing
rather than losing it or crashing the whole transcription job. A
genuinely missing dependency, by contrast, fails LOUD and immediately
(WordAlignError) so the caller can show one clear, actionable message
instead of every single line silently no-op'ing with no visible error.

Requires: `pip install torchaudio uroman` (torchaudio is already a
transitive dependency of faster-whisper/pyannote.audio elsewhere in this
app; torchaudio.pipelines.MMS_FA downloads its own model, ~1.1GB, from
Meta on first use).
"""
import os
import tempfile


class WordAlignError(RuntimeError):
    """Raised when the alignment dependencies aren't installed. Kept
    separate from a per-line alignment failure (see realign_long_segment,
    which catches those and degrades gracefully instead of raising) --
    this one means the feature can't run AT ALL, not that one line's
    audio was hard to align."""


def _check_dependencies():
    try:
        import torch  # noqa: F401
        import torchaudio  # noqa: F401
        import uroman  # noqa: F401
    except ImportError as exc:
        raise WordAlignError(
            "Word-level realignment needs: pip install torchaudio uroman") from exc


def align_words(audio_path: str, words: list, device: str = "cpu"):
    """
    Aligns a list of already-segmented words against their own audio,
    using Meta's MMS forced-alignment model. Returns [(word, start, end),
    ...], one entry per input word, in the same order.

    Non-Latin scripts (Chinese, Japanese, Korean, and most of the world)
    aren't in MMS's training vocabulary directly -- its acoustic model
    was trained on ROMANIZED transcripts across 1000+ languages sharing
    one Latin-based token inventory, so each word is romanized via
    `uroman` before alignment; the words returned are still your
    ORIGINAL (un-romanized) input, just with timing attached.
    """
    import torch
    import torchaudio
    import uroman as ur

    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model().to(device)
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()
    uromanizer = ur.Uroman()

    waveform, sr = torchaudio.load(audio_path)
    if sr != bundle.sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, bundle.sample_rate)

    romanized = [uromanizer.romanize_string(w).lower() for w in words]
    with torch.inference_mode():
        emission, _ = model(waveform.to(device))
    tokens = tokenizer(romanized)
    token_spans = aligner(emission[0], tokens)

    num_frames = emission.shape[1]
    ratio = waveform.shape[1] / num_frames / bundle.sample_rate
    results = []
    for word, spans in zip(words, token_spans):
        results.append((word, spans[0].start * ratio, spans[-1].end * ratio))
    return results


def _group_aligned_words_into_lines(aligned_words, offset: float, min_pause_seconds: float = 0.6):
    """
    aligned_words: [(word, start, end), ...] with start/end relative to
    the audio SLICE they were aligned against (i.e. relative to the
    original oversized segment's own start, not the whole file) --
    `offset` shifts them back onto the original file's timeline.

    Groups consecutive words into one line until a pause of
    min_pause_seconds or more separates two words, the same "a gap ends
    a line" idea min_silence_duration_ms already applies to whole
    segments, just applied at word granularity within one oversized
    segment here. Words already include their own surrounding whitespace
    as separate entries where segment.py's segmenters produce it (see
    segment_ko), so joining with no separator is correct across zh/ja/ko.
    """
    if not aligned_words:
        return []
    lines = []
    cur_words = [aligned_words[0][0]]
    cur_start = aligned_words[0][1]
    cur_end = aligned_words[0][2]
    for word, start, end in aligned_words[1:]:
        if start - cur_end >= min_pause_seconds:
            lines.append({"start": offset + cur_start, "end": offset + cur_end,
                          "text": "".join(cur_words)})
            cur_words = [word]
            cur_start = start
        else:
            cur_words.append(word)
        cur_end = end
    lines.append({"start": offset + cur_start, "end": offset + cur_end,
                  "text": "".join(cur_words)})
    return lines


def realign_long_segment(audio_path: str, segment: dict, language: str,
                          chinese_script: str = "simplified",
                          min_pause_seconds: float = 0.6, device: str = "cpu"):
    """
    Re-splits ONE oversized/VAD-merged Whisper segment into multiple
    correctly-timed lines, using MMS word-level alignment against that
    segment's own audio slice.

    Falls back to [segment] unchanged if alignment fails for any reason,
    or if the segment's text doesn't even segment into 2+ words (nothing
    to split) -- see the module docstring for why this fails soft rather
    than raising: a confirmed real failure mode exists for CTC aligners
    on some CJK text, and one line's alignment trouble must never lose
    that line or crash a whole transcription job over it.
    """
    import segment as segment_module

    words = [w for w, _ in segment_module.segment_and_annotate(
        segment["text"], language, chinese_script=chinese_script) if w.strip()]
    if len(words) < 2:
        return [segment]

    fd, slice_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        import core as core_module
        core_module.extract_audio_slice(audio_path, segment["start"], segment["end"], slice_path)
        aligned = align_words(slice_path, words, device=device)
    except Exception:
        return [segment]
    finally:
        if os.path.exists(slice_path):
            os.remove(slice_path)

    lines = _group_aligned_words_into_lines(aligned, segment["start"], min_pause_seconds)
    return lines or [segment]


def realign_oversized_segments(segments, audio_path: str, language: str,
                                chinese_script: str = "simplified",
                                min_duration_to_realign: float = 12.0,
                                min_pause_seconds: float = 0.6, device: str = "cpu"):
    """
    Re-splits every segment longer than min_duration_to_realign using
    realign_long_segment(); shorter segments are returned unchanged --
    a short segment already has a tight enough start/end that word-level
    re-alignment has little to offer, and running this heavy,
    model-download-backed step on every single segment in a file would
    cost real time for no real benefit on the segments that don't need it.

    Raises WordAlignError immediately (not per-segment) if the
    dependencies aren't installed at all -- so the caller sees one clear
    message instead of every segment silently, invisibly no-op'ing.
    """
    _check_dependencies()
    out = []
    for seg in segments:
        duration = seg["end"] - seg["start"]
        if duration >= min_duration_to_realign and (seg.get("text") or "").strip():
            out.extend(realign_long_segment(
                audio_path, seg, language, chinese_script=chinese_script,
                min_pause_seconds=min_pause_seconds, device=device))
        else:
            out.append(seg)
    return out
