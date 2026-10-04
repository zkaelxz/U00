"""
forced_align.py -- true forced alignment of a known transcript to audio,
via Qwen3-ForcedAligner (Qwen/Qwen3-ForcedAligner-0.6B), as an alternative
to core.py's align_transcript_to_timing().

WHY THIS EXISTS: align_transcript_to_timing() recovers per-line timing by
running Whisper transcription, then character-diffing the user's real
transcript against WHISPER'S OWN (often wrong) text, and finally guessing
each matched character's time as an even proportional split across its
Whisper segment's duration. That's a reasonable trick when you have no
better tool, but it has two real sources of error: the proportional-split
assumes speech is evenly paced within a segment (it isn't), and diffing
against wrong ASR text can mismatch badly on segments Whisper transcribed
poorly.

Qwen3-ForcedAligner does the job directly: given audio and the ACTUAL text
spoken in it, it returns real per-unit timestamps -- no proportional
guessing, no diffing against possibly-wrong ASR output, because we already
know the ground-truth text (that's what "forced" alignment means).

THE CATCH: the aligner accepts at most ~5 minutes of audio per call, and it
still needs to know, roughly, which audio range corresponds to which lines
before it can align them precisely -- audio dramas run far longer than 5
minutes. So this module doesn't replace align_transcript_to_timing()
outright; it reuses it as a COARSE first pass (good enough to bucket lines
into sub-5-minute chunks and locate each chunk's audio slice), then
re-aligns each chunk's real text against its own audio slice for the
FINAL, precise timestamps. Only the coarse pass's chunk boundaries survive
into the output -- its per-character proportional-guess timestamps are
discarded and replaced by the aligner's real ones.

SETUP (not run inside this sandbox -- no GPU, no network; code is here to
run locally):
    pip install qwen-asr torch
qwen-asr recommends a clean Python 3.12 environment. If you're on Python
3.14 (as this project's own requirements-optional.txt already warns for
PaddlePaddle), verify `pip install torch` actually gives you a CUDA-enabled
build before relying on GPU alignment -- PyTorch's Python 3.14 wheels have
had reported gaps where a CUDA install silently resolves to a CPU-only
build:
    python -c "import torch; print(torch.cuda.is_available())"

Model weights (~0.6B, a few hundred MB) download from Hugging Face on
first use, same as Whisper.
"""

import os
import re
import tempfile

from core import (
    ModelDownloadError, is_gpu_error, is_network_error, diagnose_hostname,
    lines_from_char_times, align_transcript_to_timing,
    extract_audio_slice as _extract_audio_slice, LANGUAGE_NAMES,
)


# The model's own documented cap is ~5 minutes (300s), but timing was
# reported to drift out of sync after roughly 30s on long inputs, so chunks
# are kept around a minute. HARD_CAP_SECONDS is the point past which a
# single line can't be salvaged by chunking at all.
MAX_CHUNK_SECONDS = 60.0
HARD_CAP_SECONDS = 300.0

_aligner_model_cache = {}


def load_qwen3_aligner(use_gpu: bool = False):
    """Loads (and caches) the Qwen3-ForcedAligner model.

    Unlike faster-whisper/ctranslate2 (see core.load_whisper_model's
    docstring), a transformers/torch model touches the GPU immediately
    during from_pretrained(..., device_map=...) rather than deferring
    CUDA init to first inference -- so a broken CUDA install is caught
    right here, not later during align().

    Raises ModelDownloadError on a network failure, reusing core.py's
    own classification/messaging so this looks consistent with the
    Whisper download-failure UI. Anything else propagates as-is.
    """
    cache_key = "gpu" if use_gpu else "cpu"
    if cache_key in _aligner_model_cache:
        return _aligner_model_cache[cache_key]

    import torch
    from qwen_asr import Qwen3ForcedAligner

    device = "cuda:0" if use_gpu else "cpu"
    try:
        model = Qwen3ForcedAligner.from_pretrained(
            "Qwen/Qwen3-ForcedAligner-0.6B", dtype=torch.bfloat16, device_map=device,
        )
    except Exception as exc:
        if use_gpu and is_gpu_error(exc):
            model = Qwen3ForcedAligner.from_pretrained(
                "Qwen/Qwen3-ForcedAligner-0.6B", dtype=torch.bfloat16, device_map="cpu",
            )
            cache_key = "cpu"
        elif is_network_error(exc):
            diag = diagnose_hostname("huggingface.co")
            if diag["status"] == "blocked":
                raise ModelDownloadError(
                    "Couldn't download Qwen3-ForcedAligner -- huggingface.co is being "
                    f"blocked by a DNS blocker on your network.\n\n{diag['detail']}"
                ) from exc
            raise ModelDownloadError(
                "Couldn't download the Qwen3-ForcedAligner model.\n\n"
                "This is a network problem, not a problem with your audio or transcript. "
                "The model is fetched from Hugging Face the first time you use it."
            ) from exc
        else:
            raise

    _aligner_model_cache[cache_key] = model
    return model


def _bucket_into_chunks(coarse_lines, max_chunk_seconds: float = MAX_CHUNK_SECONDS):
    """Groups coarse-timed lines into chunks whose audio span stays under
    max_chunk_seconds, so each chunk can be sent to the aligner in one
    call.

    Raises ValueError, naming the offending line, if a SINGLE line's own
    coarse span already exceeds the aligner's hard cap -- this happens
    when Whisper's VAD merged several real lines into one oversized
    segment (exactly what core.diagnose_line_coverage()'s long_lines
    check flags), and needs fixing there before forced alignment can help.
    """
    for ln in coarse_lines:
        if ln.end - ln.start > HARD_CAP_SECONDS:
            raise ValueError(
                f"Line {ln.idx} spans {ln.end - ln.start:.0f}s on its own, over the "
                f"aligner's ~5-minute limit. This is almost always the VAD-merge pattern "
                f"core.diagnose_line_coverage() flags as a 'long_line' -- fix the "
                f"transcript split for that line (or lower min_silence_duration_ms and "
                f"re-run recognition) before using forced alignment."
            )

    chunks = []
    current = []
    for ln in coarse_lines:
        if current and (ln.end - current[0].start) > max_chunk_seconds:
            chunks.append(current)
            current = [ln]
        else:
            current.append(ln)
    if current:
        chunks.append(current)
    return chunks


def _align_chunk(model, audio_path: str, chunk_lines, language_name: str, tmp_dir: str):
    """Runs the aligner on one chunk's audio slice against the REAL text
    of the lines assigned to it, and returns {global_line_idx: [times]}.

    Doesn't assume the aligner's output granularity (character-level for
    some languages, word-level for others, per Qwen3-ForcedAligner's own
    docs) -- instead it walks each returned unit's own .text field over
    the concatenated input to find its position, so this works the same
    way whether a "unit" is one character or a whole word.
    """
    chars, line_of_char = [], []
    for ln in chunk_lines:
        for ch in re.sub(r"\s+", "", ln.zh):
            chars.append(ch)
            line_of_char.append(ln.idx)
    concatenated = "".join(chars)
    if not concatenated:
        return {}

    chunk_start, chunk_end = chunk_lines[0].start, chunk_lines[-1].end
    slice_path = os.path.join(tmp_dir, f"chunk_{chunk_start:.3f}.wav")
    _extract_audio_slice(audio_path, chunk_start, chunk_end, slice_path)
    try:
        results = model.align(audio=slice_path, text=concatenated, language=language_name)
    finally:
        if os.path.exists(slice_path):
            os.unlink(slice_path)

    units = results[0] if results else []
    per_line_times = {}
    pos = 0
    for unit in units:
        unit_len = max(len(unit.text), 1)
        for offset in range(unit_len):
            char_pos = pos + offset
            if char_pos >= len(line_of_char):
                break
            li = line_of_char[char_pos]
            per_line_times.setdefault(li, []).extend(
                [chunk_start + unit.start_time, chunk_start + unit.end_time]
            )
        pos += unit_len
    return per_line_times


TIMING_FALLBACK_NOTE = ("Forced alignment returned zero-length or out-of-order timing for this "
                        "line, so it uses the approximate timing instead -- check it lines up.")


def _bad_line_timings(per_line_times) -> set:
    """Line indices whose aligned units include a zero-duration span
    (Qwen3-ASR issue #197) or whose unit start times go backwards -- the
    aligner's known failure modes, where its timing can't be trusted."""
    bad = set()
    for li, times in per_line_times.items():
        spans = list(zip(times[0::2], times[1::2]))
        if (any(end <= start for start, end in spans)
                or any(b[0] < a[0] for a, b in zip(spans, spans[1:]))):
            bad.add(li)
    return bad


def align_with_qwen3(audio_path: str, user_lines, whisper_segments, language: str,
                      use_gpu: bool = False):
    """Drop-in alternative to core.align_transcript_to_timing() -- same
    inputs, same Line-list output -- that refines timing with true forced
    alignment instead of a character-diff heuristic. See the module
    docstring for why this still needs whisper_segments (as a coarse
    first pass, not as the source of the final timestamps).
    """
    if language not in LANGUAGE_NAMES:
        raise ValueError(
            f"Qwen3-ForcedAligner doesn't cover language={language!r} in this project's "
            f"usage (supported: {sorted(LANGUAGE_NAMES)}) -- use "
            f"core.align_transcript_to_timing() instead."
        )
    if not whisper_segments:
        raise ValueError(
            "align_with_qwen3 needs whisper_segments for its coarse first pass (see module "
            "docstring) -- run core.transcribe_for_timing() first, same as for the existing "
            "diff-based alignment."
        )

    coarse_lines = align_transcript_to_timing(user_lines, whisper_segments)
    chunks = _bucket_into_chunks(coarse_lines)
    model = load_qwen3_aligner(use_gpu=use_gpu)
    language_name = LANGUAGE_NAMES[language]

    per_line_times = {}
    with tempfile.TemporaryDirectory(prefix="baihe_forced_align_") as tmp_dir:
        for chunk in chunks:
            per_line_times.update(_align_chunk(model, audio_path, chunk, language_name, tmp_dir))

    # Where the aligner's own output is broken, the coarse diff alignment
    # (already computed above) is the better answer -- flagged, so the
    # person checks those few lines instead of trusting them blindly.
    bad = _bad_line_timings(per_line_times)
    for li in bad:
        per_line_times[li] = [coarse_lines[li].start, coarse_lines[li].end]

    total_audio_end = whisper_segments[-1]["end"] if whisper_segments else 0.0
    lines = lines_from_char_times(user_lines, per_line_times, total_audio_end)
    for ln in lines:
        if ln.idx in bad:
            ln.flag, ln.flag_note = "timing_uncertain", TIMING_FALLBACK_NOTE
    return lines
