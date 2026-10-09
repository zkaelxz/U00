"""
forced_align.py -- true forced alignment of a known transcript to audio,
via Qwen3-ForcedAligner (Qwen/Qwen3-ForcedAligner-0.6B-hf), as an alternative
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

SETUP:
    pip install "transformers>=5.15" torch
plus nagisa (Japanese) or soynlp (Korean) for those languages. If you're on
Python 3.14 (as this project's own requirements-optional.txt already warns for
PaddlePaddle), verify `pip install torch` actually gives you a CUDA-enabled
build before relying on GPU alignment -- PyTorch's Python 3.14 wheels have
had reported gaps where a CUDA install silently resolves to a CPU-only
build:
    python -c "import torch; print(torch.cuda.is_available())"

Model weights (~0.6B, about 1.8 GB) download from Hugging Face on
first use, same as Whisper.
"""

import os
import re
import tempfile

import qwen3_native
import memory_headroom
from core import (
    ModelDownloadError, is_gpu_error, is_network_error, diagnose_hostname,
    Line, lines_from_char_times, align_transcript_to_timing,
    extract_audio_slice as _extract_audio_slice, LANGUAGE_NAMES,
)


# The model's own documented cap is ~5 minutes (300s), but timing was
# reported to drift out of sync after roughly 30s on long inputs, so chunks
# are kept around a minute. HARD_CAP_SECONDS is the point past which a
# single line can't be salvaged by chunking at all.
MAX_CHUNK_SECONDS = 60.0
HARD_CAP_SECONDS = 300.0

# LANGUAGE_NAMES stays zh/ja/ko because asr_backend gates Qwen3-ASR on it; the
# aligner also takes English (it is in the processor's FORCED_ALIGNER_LANGUAGES).
ALIGNER_LANGUAGE_NAMES = {**LANGUAGE_NAMES, "en": "English"}

# Space-delimited languages: the aligner returns whole words, so word breaks
# must survive into the text it is given (CJK is sent as one run of characters).
_WORD_UNIT_LANGUAGES = {"English"}

# Loaded models stay cached across calls; core.release_gpu_models() clears
# this dict by name (it never imports this module), so keep the name.
# The one repo the aligner loads; the real-model check looks for exactly this id.
ALIGNER_REPO_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
_aligner_model_cache = {}


def load_qwen3_aligner(use_gpu: bool = False, on_device=None, on_gpu_fallback=None):
    """Loads (and caches) the Qwen3-ForcedAligner model.

    Unlike faster-whisper/ctranslate2 (see core.load_whisper_model's
    docstring), a transformers/torch model touches the GPU immediately
    during from_pretrained(..., device_map=...) rather than deferring
    CUDA init to first inference -- so a broken CUDA install is caught
    right here, not later during align().

    Raises ModelDownloadError on a network failure, reusing core.py's
    own classification/messaging so this looks consistent with the
    Whisper download-failure UI. Anything else propagates as-is.

    on_device("GPU"|"CPU") reports where the model actually runs (cached or
    not); on_gpu_fallback(exc) is called first when a requested GPU load fell
    back to the CPU.
    """
    cache_key = "gpu" if use_gpu else "cpu"
    memory_headroom.before_load("aligner", "qwen3", use_gpu, cache_key in _aligner_model_cache)
    if cache_key in _aligner_model_cache:
        if on_device:
            on_device("GPU" if use_gpu else "CPU")
        return _aligner_model_cache[cache_key]

    qwen3_native.require_transformers("Qwen3 forced alignment")
    import torch

    device = "cuda:0" if use_gpu else "cpu"
    try:
        model = qwen3_native.NativeQwen3Aligner.from_pretrained(
            qwen3_native.ALIGNER_REPO, device=device,
            dtype=qwen3_native.pick_dtype(torch, use_gpu))
    except Exception as exc:
        if use_gpu and is_gpu_error(exc):
            model = qwen3_native.NativeQwen3Aligner.from_pretrained(
                qwen3_native.ALIGNER_REPO, device="cpu",
                dtype=qwen3_native.pick_dtype(torch, False))
            cache_key = "cpu"
            use_gpu = False
            if on_gpu_fallback:
                on_gpu_fallback(exc)
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
    if on_device:
        on_device("GPU" if use_gpu else "CPU")
    return model


def _device_callbacks(on_device, on_gpu_fallback) -> dict:
    """Only the callbacks that were given, so callers that don't listen load the
    model exactly as before."""
    return {k: v for k, v in (("on_device", on_device), ("on_gpu_fallback", on_gpu_fallback))
            if v}


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


def _repair_unit_spans(spans, lo: float, hi: float):
    """Makes (start, end) spans monotonic, inside [lo, hi], and non-zero where
    the neighbours leave room. The aligner can return items with start == end
    (Qwen3-ASR issue #197), which collapse subtitle lines to zero length.
    A run of zero-length spans is spread evenly over the gap between the
    previous span's end and the next span's start (or lo/hi at the ends); if
    that gap is itself empty the spans stay zero-length so _bad_line_timings
    still sends the line to the coarse-timing fallback."""
    fixed, prev_end = [], lo
    for start, end in spans:
        start = min(max(start, prev_end, lo), hi)
        end = min(max(end, start), hi)
        fixed.append([start, end])
        prev_end = end
    i = 0
    while i < len(fixed):
        if fixed[i][1] > fixed[i][0]:
            i += 1
            continue
        j = i
        while j < len(fixed) and fixed[j][1] <= fixed[j][0]:
            j += 1
        left = fixed[i - 1][1] if i > 0 else lo
        right = fixed[j][0] if j < len(fixed) else hi
        if right > left:
            step = (right - left) / (j - i)
            for k in range(i, j):
                fixed[k] = [left + (k - i) * step, left + (k - i + 1) * step]
        i = j
    return [tuple(f) for f in fixed]


def _align_chunk(model, audio_path: str, chunk_lines, language_name: str, tmp_dir: str,
                 repaired_lines=None):
    """Runs the aligner on one chunk's audio slice against the REAL text
    of the lines assigned to it, and returns {global_line_idx: [times]}.

    Doesn't assume the aligner's output granularity (character-level for
    some languages, word-level for others, per Qwen3-ForcedAligner's own
    docs) -- instead it walks each returned unit's own .text field over
    the concatenated input to find its position, so this works the same
    way whether a "unit" is one character or a whole word.

    Indices of lines that had any unit's timing changed by the repair are
    added to repaired_lines (a set) when one is given.
    """
    word_units = language_name in _WORD_UNIT_LANGUAGES
    chars, line_of_char = [], []
    for ln in chunk_lines:
        if word_units:
            if chars and ln.zh.strip():
                chars.append(" ")
                line_of_char.append(None)
            text = " ".join(ln.zh.split())
        else:
            text = re.sub(r"\s+", "", ln.zh)
        for ch in text:
            chars.append(ch)
            line_of_char.append(ln.idx)
    concatenated = "".join(chars)
    if not concatenated.strip():
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
    # The upper bound never cuts off a real unit that runs past the coarse
    # chunk end; it only limits where a zero-length tail can be spread.
    raw = [(u.start_time, u.end_time) for u in units]
    spans = _repair_unit_spans(
        raw, 0.0, max([chunk_end - chunk_start] + [e for _, e in raw]))
    per_line_times = {}
    pos = 0
    for unit, (unit_start, unit_end), orig in zip(units, spans, raw):
        unit_len = max(len(unit.text), 1)
        if word_units:
            # The aligner drops punctuation from its words ("world!" -> "world"),
            # so find each word forward from the last one instead of counting.
            found = concatenated.find(unit.text, pos) if unit.text else -1
            if found >= 0:
                pos = found
        for offset in range(unit_len):
            char_pos = pos + offset
            if char_pos >= len(line_of_char):
                break
            li = line_of_char[char_pos]
            if li is None:
                continue
            per_line_times.setdefault(li, []).extend(
                [chunk_start + unit_start, chunk_start + unit_end]
            )
            if repaired_lines is not None and (unit_start, unit_end) != orig:
                repaired_lines.add(li)
        pos += unit_len
    return per_line_times


TIMING_FALLBACK_NOTE = ("Forced alignment returned zero-length or out-of-order timing for this "
                        "line, so it uses the approximate timing instead -- check it lines up.")


TIMING_REPAIRED_NOTE = ("Forced alignment returned zero-length or out-of-order timing for part of "
                        "this line; it was estimated from the neighbouring words -- check it "
                        "lines up.")


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
                      use_gpu: bool = False, on_device=None, on_gpu_fallback=None,
                      cancel_check=None):
    """Drop-in alternative to core.align_transcript_to_timing() -- same
    inputs, same Line-list output -- that refines timing with true forced
    alignment instead of a character-diff heuristic. See the module
    docstring for why this still needs whisper_segments (as a coarse
    first pass, not as the source of the final timestamps).

    cancel_check() runs before the model load, after it and before each
    chunk, and should raise to stop; a load or one chunk already running
    can't be interrupted, so a cancel lands at the next of those points.
    """
    if language not in ALIGNER_LANGUAGE_NAMES:
        raise ValueError(
            f"Qwen3-ForcedAligner doesn't cover language={language!r} in this project's "
            f"usage (supported: {sorted(ALIGNER_LANGUAGE_NAMES)}) -- use "
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
    if cancel_check:
        cancel_check()
    model = load_qwen3_aligner(use_gpu=use_gpu, **_device_callbacks(on_device, on_gpu_fallback))
    if cancel_check:
        cancel_check()
    language_name = ALIGNER_LANGUAGE_NAMES[language]

    per_line_times, repaired = {}, set()
    with tempfile.TemporaryDirectory(prefix="baihe_forced_align_") as tmp_dir:
        for chunk in chunks:
            if cancel_check:
                cancel_check()
            per_line_times.update(_align_chunk(model, audio_path, chunk, language_name, tmp_dir,
                                              repaired_lines=repaired))

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
        elif ln.idx in repaired:
            ln.flag, ln.flag_note = "timing_uncertain", TIMING_REPAIRED_NOTE
    return lines


def refine_segment_timing(audio_path: str, groups, language: str, use_gpu: bool = False,
                          cancel_check=None, progress_cb=None, on_device=None,
                          on_gpu_fallback=None):
    """Refines the line times inside each speech span with the forced aligner.

    groups: one list per span of {"start", "end", "text"} dicts, in order; the
    first start and last end of a group are the span's bounds, and the
    aligner only sees that slice. Returns the flat segment list with refined
    start/end. A line whose aligned times are unusable keeps its estimated
    times and is flagged timing_uncertain (as are repaired ones), the same
    repair rules as align_with_qwen3. Times stay inside their span and never
    overlap. cancel_check() runs before each span and should raise to stop."""
    if language not in ALIGNER_LANGUAGE_NAMES:
        raise ValueError(
            f"Qwen3-ForcedAligner doesn't cover language={language!r} in this project's "
            f"usage (supported: {sorted(ALIGNER_LANGUAGE_NAMES)}).")
    model = load_qwen3_aligner(use_gpu=use_gpu, **_device_callbacks(on_device, on_gpu_fallback))
    language_name = ALIGNER_LANGUAGE_NAMES[language]
    out = []
    with tempfile.TemporaryDirectory(prefix="baihe_forced_align_") as tmp_dir:
        for n, group in enumerate(groups):
            if cancel_check:
                cancel_check()
            lines = [Line(idx=i, start=s["start"], end=s["end"], zh=s["text"])
                     for i, s in enumerate(group)]
            span_start, span_end = lines[0].start, lines[-1].end
            repaired = set()
            times = _align_chunk(model, audio_path, lines, language_name, tmp_dir,
                                 repaired_lines=repaired)
            bad = _bad_line_timings(times)
            prev_end = span_start
            for ln, seg in zip(lines, group):
                new = dict(seg)
                t = times.get(ln.idx)
                if t and ln.idx not in bad:
                    start = min(max(min(t), prev_end, span_start), span_end)
                    end = min(max(max(t), start), span_end)
                    if end > start:
                        new["start"], new["end"] = start, end
                        if ln.idx in repaired:
                            new["flag"], new["flag_note"] = "timing_uncertain", TIMING_REPAIRED_NOTE
                    else:
                        bad.add(ln.idx)
                else:
                    bad.add(ln.idx)
                if ln.idx in bad:
                    if prev_end < new["end"]:
                        new["start"] = max(new["start"], prev_end)
                    elif out and out[-1]["start"] < new["start"] < out[-1]["end"]:
                        # The previous line was aligned over this line's whole estimate.
                        out[-1]["end"] = new["start"]
                        out[-1]["flag"], out[-1]["flag_note"] = (
                            "timing_uncertain", TIMING_REPAIRED_NOTE)
                    new["flag"], new["flag_note"] = "timing_uncertain", TIMING_FALLBACK_NOTE
                prev_end = max(prev_end, new["end"])
                out.append(new)
            if progress_cb:
                progress_cb((n + 1) / len(groups))
    return out
