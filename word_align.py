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

Experimental and opt-in per drama (realign_long_segments, off by
default): written against torchaudio's documented MMS_FA API and uroman's
documented Python API rather than tuned on real Chinese/Japanese/Korean
speech. There IS a confirmed
real GitHub issue (m-bain/whisperX#84) of a DIFFERENT but related CTC
aligner failing outright on some Japanese text ("no characters in this
segment found in model dictionary") -- so realign_long_segment() fails
SOFT: any per-line alignment problem keeps that line's original timing
rather than losing it or crashing the whole transcription job. A
genuinely missing dependency, by contrast, fails LOUD and immediately
(WordAlignError) so the caller can show one clear, actionable message
instead of every single line silently no-op'ing with no visible error.

Requires: `pip install torchaudio uroman soundfile` (torchaudio and
soundfile are already transitive/direct dependencies of faster-whisper/
pyannote.audio elsewhere in this app; torchaudio.pipelines.MMS_FA
downloads its own model, ~1.1GB, from Meta on first use).
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
        import soundfile  # noqa: F401
    except ImportError as exc:
        # exc.name is the specific module that failed to import, and
        # happens to match its pip package name exactly for all four of
        # these -- names the one actually missing instead of bundling
        # all of them into one message that can't say which it was.
        raise WordAlignError(
            f"Word-level realignment needs: pip install {exc.name}") from exc


class _LoadedAligner:
    """The MMS_FA model plus its tokenizer/aligner/uromanizer, loaded once
    and reused across every oversized segment in one realignment run
    -- reloading the model per segment was real, avoidable
    latency and memory churn on audio with several long segments."""

    def __init__(self, bundle, model, tokenizer, aligner, uromanizer, device):
        self.bundle = bundle
        self.model = model
        self.tokenizer = tokenizer
        self.aligner = aligner
        self.uromanizer = uromanizer
        self.device = device


def load_aligner(device: str = "cpu") -> _LoadedAligner:
    """Loads the MMS forced-alignment model once. Pass the result to
    align_words()/realign_long_segment() as `aligner=` to reuse it."""
    import torchaudio
    import uroman as ur

    bundle = torchaudio.pipelines.MMS_FA
    return _LoadedAligner(bundle, bundle.get_model().to(device), bundle.get_tokenizer(),
                          bundle.get_aligner(), ur.Uroman(), device)


def align_words(audio_path: str, words: list, device: str = "cpu",
                aligner: "_LoadedAligner | None" = None):
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

    Reads audio_path via soundfile, not torchaudio.load() -- the same
    fix diarize() already needed: torchaudio>=2.9 routes
    load()/save() through torchcodec by default, which can fail (no
    compiled-per-FFmpeg-version DLLs) for a plain WAV read that never
    needed torchcodec's decode path at all. MMS_FA itself is still the
    real torchaudio model this aligns against -- only the file read
    changes.
    """
    import torch
    import torchaudio
    import soundfile as sf

    loaded = aligner if aligner is not None else load_aligner(device)
    device = loaded.device
    bundle = loaded.bundle
    model = loaded.model
    tokenizer = loaded.tokenizer
    uromanizer = loaded.uromanizer

    waveform, sr = sf.read(audio_path, dtype="float32", always_2d=True)
    waveform = torch.from_numpy(waveform.T)  # (frames, channels) -> (channels, frames)
    if sr != bundle.sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, bundle.sample_rate)

    romanized = [uromanizer.romanize_string(w).lower() for w in words]
    with torch.inference_mode():
        emission, _ = model(waveform.to(device))
    tokens = tokenizer(romanized)
    token_spans = loaded.aligner(emission[0], tokens)

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
                          min_pause_seconds: float = 0.6, device: str = "cpu",
                          aligner: "_LoadedAligner | None" = None):
    """
    Re-splits ONE oversized/VAD-merged Whisper segment into multiple
    correctly-timed lines, using MMS word-level alignment against that
    segment's own audio slice.

    Falls back to [segment] unchanged if alignment fails for any reason,
    or if the segment's text doesn't even segment into 2+ words (nothing
    to split) -- see the module docstring for why this fails soft rather
    than raising: a confirmed real failure mode exists for CTC aligners
    on some CJK text, and one line's alignment trouble must never lose
    that line or crash a whole transcription job over it. The real
    exception is logged (applog) before falling back, though -- silently
    swallowing it made a real, confirmed break in align_words() itself
    (a torchcodec-routing failure, the same class already fixed
    for diarize()) invisible: every segment just quietly stayed unsplit
    with no error shown anywhere.
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
        if aligner is not None:
            aligned = align_words(slice_path, words, device=device, aligner=aligner)
        else:
            aligned = align_words(slice_path, words, device=device)
    except Exception as exc:
        import applog
        applog.get_logger().error(
            f"word-level realignment failed on segment {segment['start']:.1f}-"
            f"{segment['end']:.1f}s, keeping it unsplit: {exc}")
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
    loaded = None  # loaded lazily on the first oversized segment, then reused
    load_failed = False
    for seg in segments:
        duration = seg["end"] - seg["start"]
        if (not load_failed and duration >= min_duration_to_realign
                and (seg.get("text") or "").strip()):
            if loaded is None:
                try:
                    loaded = load_aligner(device)
                except Exception as exc:
                    import applog
                    applog.get_logger().error(
                        f"word-level realignment model failed to load, keeping every "
                        f"segment unsplit: {exc}")
                    load_failed = True
                    out.append(seg)
                    continue
            out.extend(realign_long_segment(
                audio_path, seg, language, chinese_script=chinese_script,
                min_pause_seconds=min_pause_seconds, device=device, aligner=loaded))
        else:
            out.append(seg)
    return out
