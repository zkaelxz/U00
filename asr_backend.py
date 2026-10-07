"""
asr_backend.py -- pluggable transcription backends for the
"I don't have a transcript, let Whisper transcribe it" workflow in
services/transcribe_service.py (Whisper-text mode).

Backends are registered in BACKENDS / get_backend() at the bottom (a
seam that also holds the experimental MossTranscribeDiarizeBackend, whose
segments also carry a "speaker"). Each backend's transcribe() returns the same
shape core.transcribe_for_timing() already produces: a list of {"start": float, "end": float, "text": str}
segments -- a drop-in replacement at that one call site.

WHY Qwen3ASRBackend REUSES WHISPER FOR SEGMENTATION: Qwen3-ASR has no VAD/
segmentation of its own in this project's usage -- Whisper's vad_filter is
the only thing that provides segment boundaries here. So Qwen3ASRBackend
takes Whisper's segments as input and re-transcribes each segment's audio
slice with Qwen3-ASR, REPLACING ONLY THE TEXT, not the timing.

This is deliberate, not a shortcut: it isolates the one thing actually in
question -- transcription accuracy, the open question from the original
Qwen3-ASR-vs-Whisper comparison, unverified specifically for Japanese --
from segmentation/timing, which Whisper still provides identically either
way. If Qwen3ASRBackend also changed segment boundaries, a quality
difference you saw in a side-by-side benchmark could come from either
change and you couldn't tell which one actually helped.

One consequence: whatever VAD-merge timing problems Whisper's segmentation
already has (see core.diagnose_line_coverage's long_lines check) carry over
unchanged into Qwen3ASRBackend's output too -- swapping the transcriber
doesn't fix mis-drawn segment boundaries, only what's written inside them.
If timing itself is the problem, that's forced_align.py's job, not this
module's -- and note forced_align.py needs a real reference transcript to
align against, which this whisper-text-only mode by definition doesn't have.

SETUP:
    pip install "transformers>=5.15" torch
(see qwen3_native.py for why 5.15). Same Python 3.14/CUDA-wheel caveat as
forced_align.py -- see that module's docstring.
"""

import os
import tempfile
from typing import Optional

from core import (
    SPLIT_MAX_CJK_CHARS, SPLIT_MAX_SECONDS, ModelDownloadError, SplitRules, is_gpu_error,
    is_network_error, diagnose_hostname, extract_audio_slice, transcribe_for_timing,
)
import qwen3_native
from forced_align import LANGUAGE_NAMES

# Not a documented Qwen3-ASR limit (the model card states no maximum
# duration for the base model) -- this is a defensive fallback for the
# same failure signature forced_align.py guards against with its own
# (aligner-documented) HARD_CAP_SECONDS: an abnormally long "segment" is
# almost always a VAD-merge artifact regardless of which model transcribes
# it, and re-transcribing several real minutes of merged dialogue as one
# blob would produce a similarly-merged, similarly-wrong result. Skipping
# it (falling back to Whisper's own text for just that one segment) is
# safer than guessing at an undocumented cap and failing the whole run.
SEGMENT_DURATION_WARNING_SECONDS = 300.0

# Speech-detection backend: how much silence around a span the model also
# hears, and how long a pause may be before a sentence is cut there. Both are
# set from the FLEURS comparison in docs/asr-experiments.md.
CONTEXT_PAD_S = 2.0
MERGE_GAP_S = 1.0
MERGE_GAP_MIXED_S = 0.3

# Long-window backend. Silero's 0.5 threshold, dropping spans under 250 ms and
# 100 ms of padding lost quiet speech, short interjections and word edges; the
# whole file in one call was the best Qwen3-ASR score in every language tried
# (docs/asr-experiments.md), so spans are packed into windows as long as the
# forced aligner keeps in sync (forced_align.MAX_CHUNK_SECONDS notes drift
# past ~30 s). A pause up to LONG_MERGE_GAP_S stays inside a window; a longer
# one is mostly music or silence, which is where Qwen3-ASR invents text.
LONG_VAD_KWARGS = {"threshold": 0.35, "min_speech_ms": 0, "pad_ms": 300}
LONG_MERGE_GAP_S = 3.0
LONG_WINDOW_S = 30.0
LONG_CUT_SEARCH_S = 8.0

# The "Split lines by sentences" option and the long-window backend: a line
# ends at every sentence end; one still too long is cut at commas, then at the
# longest pauses between words.
SENTENCE_SPLIT_RULES = SplitRules(max_seconds=SPLIT_MAX_SECONDS, max_chars=SPLIT_MAX_CJK_CHARS,
                                  per_sentence=True, count_latin=False)
# Whisper's speech-detection pause in that mode (faster-whisper's own default):
# shorter pauses feed it sentence fragments; lines are cut afterwards instead.
SENTENCE_SPLIT_MIN_SILENCE_MS = 2000

# Below this much audio a low figure says little (a short clip can be one line).
COVERAGE_MIN_AUDIO_SECONDS = 30.0
COVERAGE_WARN_FRACTION = 0.15


def audio_coverage_fraction(segments, audio_seconds) -> Optional[float]:
    """Share (0-1) of the audio covered by segments that have text, overlaps
    counted once; None when the audio length is unknown."""
    if not audio_seconds or audio_seconds <= 0:
        return None
    spans = sorted((max(0.0, float(s["start"])), min(float(s["end"]), audio_seconds))
                   for s in segments if (s.get("text") or "").strip())
    covered, cur_end = 0.0, 0.0
    for start, end in spans:
        start = max(start, cur_end)
        if end > start:
            covered += end - start
            cur_end = end
    return min(covered / audio_seconds, 1.0)


def coverage_warning(segments, audio_seconds, qwen3_asr: bool = False) -> Optional[str]:
    """A sentence when transcribed lines cover very little of a long enough
    audio file (speech missed, e.g. singing or music the speech detector
    skipped), else None."""
    fraction = audio_coverage_fraction(segments, audio_seconds)
    if fraction is None or audio_seconds < COVERAGE_MIN_AUDIO_SECONDS \
            or fraction >= COVERAGE_WARN_FRACTION:
        return None
    msg = (f"Only {fraction * 100:.0f}% of the audio has text: try another engine, "
           "turn vocal separation on, or check the language.")
    if qwen3_asr:
        msg += (" Qwen3-ASR only re-transcribes the speech Whisper found, "
                "so it cannot add lines Whisper missed.")
    return msg


# Loaded models stay cached across calls; core.release_gpu_models() clears
# this dict by name (it never imports this module), so keep the name.
_asr_model_cache = {}

# Results come back in input order (qwen3_native checks the count), so batching
# only changes throughput. It stays off by default until a real before/after run
# on a GPU confirms that padding a batch of varied-length clips doesn't change
# the text (docs/asr-experiments.md). The 1-16 range lives in
# services/asr_options_service.


def effective_qwen_batch_size(requested) -> int:
    return max(1, int(requested or 1))


class WhisperBackend:
    """Wraps the existing Whisper transcription path unchanged -- a pure
    refactor behind a common interface, not a behavior change. Existing
    callers of core.transcribe_for_timing() are unaffected; this exists
    so a caller can pick a backend without an if/else on
    which model to call directly."""
    name = "whisper"

    def transcribe(self, audio_path, language, whisper_size="medium", use_gpu=False,
                    local_model_path=None, hf_token=None, initial_prompt="",
                    beam_size=5, min_silence_duration_ms=2000, on_gpu_fallback=None):
        return transcribe_for_timing(
            audio_path, whisper_size, language=language, use_gpu=use_gpu,
            local_model_path=local_model_path, hf_token=hf_token,
            initial_prompt=initial_prompt, beam_size=beam_size,
            min_silence_duration_ms=min_silence_duration_ms,
            on_gpu_fallback=on_gpu_fallback,
        )


def load_qwen3_asr(use_gpu: bool = False, model_size: str = "1.7B", on_device=None,
                   on_gpu_fallback=None):
    """Loads (and caches) the Qwen3-ASR model. Same GPU-fallback/network-
    error handling pattern as forced_align.load_qwen3_aligner() and
    core.load_whisper_model() -- see forced_align.py's docstring for why a
    transformers/torch model's CUDA failure surfaces here, at load time,
    rather than deferred to first inference like ctranslate2/faster-whisper.

    on_device("GPU"|"CPU") is called with where the model actually runs, cached
    or not; on_gpu_fallback(exc) first when a requested GPU load fell back to
    the CPU. A fallback is not cached under the GPU key, so every call retries
    the GPU and reports it again.
    """
    cache_key = f"{model_size}_{'gpu' if use_gpu else 'cpu'}"
    if cache_key in _asr_model_cache:
        if on_device:
            on_device("GPU" if use_gpu else "CPU")
        return _asr_model_cache[cache_key]

    qwen3_native.require_transformers("Qwen3-ASR")
    import torch

    device = "cuda:0" if use_gpu else "cpu"
    model_id = qwen3_native.asr_repo_id(model_size)
    try:
        model = qwen3_native.NativeQwen3ASR.from_pretrained(
            model_id, device=device, dtype=qwen3_native.pick_dtype(torch, use_gpu))
    except Exception as exc:
        if use_gpu and is_gpu_error(exc):
            model = qwen3_native.NativeQwen3ASR.from_pretrained(
                model_id, device="cpu", dtype=qwen3_native.pick_dtype(torch, False))
            cache_key = f"{model_size}_cpu"
            use_gpu = False
            if on_gpu_fallback:
                on_gpu_fallback(exc)
        elif is_network_error(exc):
            diag = diagnose_hostname("huggingface.co")
            if diag["status"] == "blocked":
                raise ModelDownloadError(
                    f"Couldn't download {model_id} -- huggingface.co is being blocked by "
                    f"a DNS blocker on your network.\n\n{diag['detail']}"
                ) from exc
            raise ModelDownloadError(
                f"Couldn't download the {model_id} model.\n\nThis is a network problem, not "
                "a problem with your audio. The model is fetched from Hugging Face the first "
                "time you use it."
            ) from exc
        else:
            raise

    _asr_model_cache[cache_key] = model
    if on_device:
        on_device("GPU" if use_gpu else "CPU")
    return model


def _device_callbacks(task, on_device, on_gpu_fallback) -> dict:
    """Loader keyword arguments that tag a model load's device reports with the
    task that loaded it, so one job can tell the ASR model from the aligner.
    Empty when nobody listens, which keeps callers that don't care unchanged."""
    out = {}
    if on_device:
        out["on_device"] = lambda label: on_device(task, label)
    if on_gpu_fallback:
        out["on_gpu_fallback"] = lambda exc: on_gpu_fallback(task, exc)
    return out


class Qwen3ASRBackend:
    name = "qwen3_asr"

    def __init__(self, model_size: str = "1.7B"):
        self.model_size = model_size

    def transcribe(self, audio_path, language, whisper_segments, use_gpu=False, batch_size=1,
                   progress_cb=None, on_device=None, on_gpu_fallback=None, prompt=None):
        """whisper_segments: the segmentation from WhisperBackend.transcribe()
        (or core.transcribe_for_timing() directly) -- see module docstring
        for why this backend needs Whisper's boundaries rather than
        producing its own.

        batch_size (experimental): how many segments go to Qwen3-ASR
        in one call. 1 (the default) is the original one-segment-at-a-time
        behaviour. Timing is Whisper's either way; only throughput changes.
        Not yet validated on real audio -- see docs/asr-experiments.md.

        prompt: an optional "Vocabulary: ..." hint (vocabulary_hint_service); None or
        empty sends the request exactly as without it.

        progress_cb(fraction): called after each batch, 0..1 of the segments
        to re-transcribe. on_device(task, "GPU"|"CPU") and on_gpu_fallback(task, exc)
        report where the model loaded (task names it: "Qwen3-ASR")."""
        if language not in LANGUAGE_NAMES:
            raise ValueError(
                f"Qwen3-ASR doesn't cover language={language!r} in this project's usage "
                f"(supported: {sorted(LANGUAGE_NAMES)}) -- use WhisperBackend instead."
            )
        if not whisper_segments:
            raise ValueError(
                "Qwen3ASRBackend re-transcribes Whisper's VAD segments (see module docstring) "
                "-- run WhisperBackend.transcribe() first to get segment boundaries."
            )

        model = load_qwen3_asr(use_gpu=use_gpu, model_size=self.model_size,
                               **_device_callbacks("Qwen3-ASR", on_device, on_gpu_fallback))
        language_name = LANGUAGE_NAMES[language]
        batch_size = effective_qwen_batch_size(batch_size)
        out = list(whisper_segments)
        with tempfile.TemporaryDirectory(prefix="baihe_qwen3_asr_") as tmp_dir:
            # See SEGMENT_DURATION_WARNING_SECONDS -- an oversized segment is
            # almost certainly a VAD-merge artifact; it keeps Whisper's own
            # text (already in `out`) rather than failing the whole run.
            todo = [i for i, seg in enumerate(whisper_segments)
                    if seg["end"] - seg["start"] <= SEGMENT_DURATION_WARNING_SECONDS]
            for pos in range(0, len(todo), batch_size):
                batch = todo[pos:pos + batch_size]
                texts = self._transcribe_batch(model, audio_path, whisper_segments, batch,
                                               language_name, tmp_dir, prompt)
                for i, text in texts.items():
                    seg = whisper_segments[i]
                    out[i] = {"start": seg["start"], "end": seg["end"], "text": text}
                if progress_cb:
                    progress_cb(min(pos + len(batch), len(todo)) / len(todo))
        return out

    def _transcribe_batch(self, model, audio_path, segments, indices, language_name, tmp_dir,
                          prompt=None):
        """{segment index: text} for one batch. With more than one
        index, transcribe() gets a list of slices and returns one result per
        input in input order (qwen3_native checks the count); results are
        keyed back by segment index, and a batch that raises or whose result
        count doesn't match falls back to one call per segment rather than
        guessing which text belongs to which line."""
        extra = {"prompt": prompt} if prompt else {}
        paths = {}
        try:
            for i in indices:
                paths[i] = os.path.join(tmp_dir, f"seg_{i}.wav")
                extract_audio_slice(audio_path, segments[i]["start"], segments[i]["end"], paths[i])
            if len(indices) > 1:
                try:
                    results = model.transcribe(audio=[paths[i] for i in indices],
                                               language=language_name, **extra)
                except Exception as exc:
                    # e.g. CUDA out of memory on a large batch: retry this
                    # batch one segment at a time rather than failing the run.
                    import applog
                    applog.get_logger().error(
                        f"Qwen3-ASR batch of {len(indices)} failed, retrying one by one: {exc}")
                    results = None
                if results is not None and len(results) == len(indices):
                    return {i: (r.text if r is not None else "")
                            for i, r in zip(indices, results)}
            texts = {}
            for i in indices:
                results = model.transcribe(audio=paths[i], language=language_name, **extra)
                texts[i] = results[0].text if results else ""
            return texts
        finally:
            for path in paths.values():
                if os.path.exists(path):
                    os.unlink(path)


def load_audio_16k(audio_path):
    """The file as a 16 kHz mono float32 waveform (what the Silero VAD takes).
    Raises VadNotInstalledError when faster-whisper (which decodes it) is missing."""
    from vad_segments import VadNotInstalledError
    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        raise VadNotInstalledError(str(exc)) from exc
    return decode_audio(audio_path, sampling_rate=16000)


class Qwen3ASRVadBackend:
    """Qwen3-ASR with its own segment boundaries: speech spans from the Silero
    VAD (vad_segments), capped at ~15 s, each transcribed by Qwen3ASRBackend.
    Unlike Qwen3ASRBackend it does not need Whisper at all. A line's start and
    end are its span's bounds (a long span's text is split into several lines
    with estimated times), or, with refine_timing, the forced aligner's times.
    Opt-in only (asr_backend_choice "qwen3_asr_vad")."""
    name = "qwen3_asr_vad"
    long_windows = False

    def __init__(self, model_size: str = "1.7B"):
        self.model_size = model_size

    def transcribe(self, audio_path, language, use_gpu=False, batch_size=1, progress_cb=None,
                   cancel_check=None, refine_timing=False, vad_fn=None,
                   mixed_languages=False, stage_cb=None, on_device=None,
                   on_gpu_fallback=None, prompt=None):
        """Segments as {"start", "end", "text"} (plus "flag"/"flag_note" where
        refined timing is uncertain). cancel_check() is called between batches
        and between aligned spans and should raise to stop; nothing is written
        here, so a cancel leaves the caller's lines untouched.
        progress_cb(fraction) is as for Qwen3ASRBackend.transcribe, scaled to
        the transcription part (the last 10% when refine_timing is on).

        mixed_languages: each span is transcribed with Qwen3-ASR's own
        language detection (one span per call, no batching) and a line's
        "lang" is set where it differs from `language` (mixed_language.py).
        language=None is the same detection with no title language to compare
        with: every line gets its detected "lang" (zh, ja, ko or en; a
        span heard as anything else is retried in the language most spans
        had). refine_timing is ignored then: the aligner takes one language per run.

        stage_cb(text) is called as each stage starts, naming the processor
        for the CPU-only ones (decoding, speech detection) so a busy CPU while
        the GPU waits is explained. on_device(task, "GPU"|"CPU") and
        on_gpu_fallback(task, exc) report where Qwen3-ASR and the aligner loaded.
        prompt: see Qwen3ASRBackend.transcribe."""
        import vad_segments
        from core import filter_hallucinated_segments, split_long_segments
        if language is None:
            mixed_languages = True
        elif language not in LANGUAGE_NAMES:
            raise ValueError(
                f"Qwen3-ASR doesn't cover language={language!r} in this project's usage "
                f"(supported: {sorted(LANGUAGE_NAMES)}) -- use WhisperBackend instead."
            )
        # Uses this class's VAD settings, not the drama's saved vad_threshold/min_silence_ms,
        # which are tuned for Whisper's own VAD.
        if stage_cb:
            stage_cb("Loading audio (CPU)")
        audio = load_audio_16k(audio_path)
        sr = 16000
        if stage_cb:
            stage_cb("Finding speech (CPU)")
        # A short pause inside a sentence is not a place to cut: Qwen3-ASR does
        # worse on the halves. Language detection keeps the finer spans so a
        # quick change of speaker and language is not merged into one.
        long_windows = self.long_windows and not mixed_languages
        spans = vad_segments.cap_spans(
            vad_segments.merge_close(
                vad_segments.speech_spans(audio, sr, vad_fn=vad_fn,
                                          **(LONG_VAD_KWARGS if long_windows else {})),
                gap_s=(LONG_MERGE_GAP_S if long_windows
                       else MERGE_GAP_MIXED_S if mixed_languages else MERGE_GAP_S)),
            audio, sr,
            **({"max_s": LONG_WINDOW_S, "search_window_s": LONG_CUT_SEARCH_S}
               if long_windows else {}))
        windows = vad_segments.context_windows(spans, len(audio) / sr, CONTEXT_PAD_S)
        del audio
        if not spans:
            return []
        if cancel_check:
            cancel_check()
        if stage_cb:
            stage_cb("Loading the Qwen3-ASR model")
        span_segments = [{"start": w.start_s, "end": w.end_s, "text": ""} for w in windows]
        # A long window holds many lines whose split times are only estimates
        # until the aligner places them.
        refine_timing = (refine_timing or long_windows) and not mixed_languages
        scale = 0.9 if refine_timing else 1.0

        def _progress(frac):
            if cancel_check:
                cancel_check()
            if progress_cb:
                progress_cb(frac * scale)

        if mixed_languages:
            transcribed = self._transcribe_mixed(audio_path, language, spans, windows, use_gpu,
                                                 _progress, cancel_check, on_device,
                                                 on_gpu_fallback, prompt)
        else:
            transcribed = Qwen3ASRBackend(model_size=self.model_size).transcribe(
                audio_path, language, span_segments, use_gpu=use_gpu, batch_size=batch_size,
                progress_cb=_progress, on_device=on_device, on_gpu_fallback=on_gpu_fallback,
                prompt=prompt)
            # The model heard the padded windows; a line is timed by its span.
            transcribed = [{**seg, "start": span.start_s, "end": span.end_s}
                           for seg, span in zip(transcribed, spans)]

        # Filter after splitting: a loop shows up as identical consecutive pieces.
        pieces = []
        for n, seg in enumerate(transcribed):
            text = (seg["text"] or "").strip()
            if text:
                pieces.extend({**p, "span": n} for p in split_long_segments(
                    [{**seg, "text": text}],
                    **({"rules": SENTENCE_SPLIT_RULES} if long_windows else {})))
        pieces = filter_hallucinated_segments(pieces)
        groups = {}
        for p in pieces:
            groups.setdefault(p.pop("span"), []).append(p)
        if refine_timing and groups:
            import forced_align
            if stage_cb:
                stage_cb("Loading the Qwen3 forced aligner")
            lines = forced_align.refine_segment_timing(
                audio_path, list(groups.values()), language, use_gpu=use_gpu,
                cancel_check=cancel_check,
                **_device_callbacks("Qwen3 forced alignment", on_device, on_gpu_fallback),
                progress_cb=(lambda f: progress_cb(0.9 + 0.1 * f)) if progress_cb else None)
            return lines
        return [p for group in groups.values() for p in group]

    def _transcribe_mixed(self, audio_path, language, spans, windows, use_gpu, progress_cb,
                          cancel_check, on_device=None, on_gpu_fallback=None, prompt=None):
        """Up to one segment per span (none when it has no text), with
        "lang"/"flag" set per mixed_language.transcribe_spans."""
        import mixed_language
        model = load_qwen3_asr(use_gpu=use_gpu, model_size=self.model_size,
                               **_device_callbacks("Qwen3-ASR", on_device, on_gpu_fallback))
        extra = {"prompt": prompt} if prompt else {}
        with tempfile.TemporaryDirectory(prefix="baihe_qwen3_asr_") as tmp_dir:
            def transcribe(span, language_name):
                window = windows[spans.index(span)]
                path = os.path.join(tmp_dir, "span.wav")
                extract_audio_slice(audio_path, window.start_s, window.end_s, path)
                try:
                    results = model.transcribe(audio=path, language=language_name, **extra)
                finally:
                    if os.path.exists(path):
                        os.unlink(path)
                first = results[0] if results else None
                text = (getattr(first, "text", "") or "").strip()
                seg = {"start": span.start_s, "end": span.end_s, "text": text}
                return ([seg] if text else []), getattr(first, "language", None)

            def run_span(span):
                # language=None: Qwen3-ASR detects this span's language itself.
                segments, name = transcribe(span, None)
                code = mixed_language.qwen_language_code(name)
                if code is None and name:
                    code = mixed_language.UNSUPPORTED_LANGUAGE
                return segments, code

            return mixed_language.transcribe_spans(
                spans, language, run_span,
                lambda span, lang: transcribe(span, mixed_language.QWEN_LANGUAGE_NAMES[lang])[0],
                cancel_check=cancel_check, progress_cb=progress_cb)


class Qwen3ASRLongBackend(Qwen3ASRVadBackend):
    """Qwen3-ASR on long windows: gentle speech detection that keeps short and
    quiet speech, neighbouring spans packed into windows of up to
    LONG_WINDOW_S, every window cut into sentence lines and timed by the
    forced aligner. Subtitle line length comes from the text and the aligned
    timings, not from where the speech detector found a pause, so the model
    always hears whole sentences with their context. With mixed_languages it
    runs exactly as the "qwen3_asr_vad" backend (per-span language detection
    needs the short spans)."""
    name = "qwen3_asr_long"
    long_windows = True


# ---------------------------------------------------------------------------
# MOSS-Transcribe-Diarize (experimental, opt-in)
# ---------------------------------------------------------------------------
#
# One model that transcribes AND labels speakers in a single pass
# (https://github.com/OpenMOSS/MOSS-Transcribe-Diarize, Apache-2.0), unlike
# Whisper (+ pyannote afterwards). Written against that repo's README and
# moss_transcribe_diarize/inference_utils.py at commit 61bc29c (package
# version 0.1.0; model revision MOSS_HF_REVISION). It is not on PyPI -- it
# installs from its own repository into the app's own Python environment and
# needs Transformers >= 5.6, which the Qwen3 backends (transformers >= 5.15)
# also satisfy. Off unless Settings > Transcription experiments turns
# it on (services/asr_options_service.get_moss_experimental), and only ever
# picked explicitly per drama -- never switched to automatically.
#
# Unlike Qwen3ASRBackend it produces its own segment boundaries (that is the
# point of trying it), so a comparison against Whisper+pyannote measures
# both segmentation and text at once; see docs/asr-experiments.md.

MOSS_MODEL_ID = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
# Package tested: pip install
#   "git+https://github.com/OpenMOSS/MOSS-Transcribe-Diarize@61bc29cd4120be7b5d3b761b64cd5dff57263642"
# The Hugging Face model revision loaded. The model needs trust_remote_code
# (the package doesn't register its classes with transformers' Auto*), so the
# Python files it downloads run inside this process: pin the revision so a
# change on the Hub can't run new code here. Moving the pin needs a review
# of the upstream diff.
MOSS_HF_REVISION = "704aa4a9c304e8520be88901e0d1960158ef5b15"
# Upper bound on generated tokens for one file. The upstream subtitle app
# uses 2048 per request; an audio drama episode is longer, so allow more and
# report when the limit was reached (the tail may be missing).
MOSS_MAX_NEW_TOKENS = 16384


class MossNotInstalledError(ImportError):
    pass


def load_moss_transcribe_diarize(use_gpu: bool = False):
    """(model, processor, device, dtype), cached in _asr_model_cache so
    core.release_gpu_models() frees it like the Qwen models. Falls back to
    CPU when use_gpu is off or CUDA isn't available."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        import moss_transcribe_diarize  # noqa: F401 -- only checks the package is installed
    except ImportError as exc:
        raise MossNotInstalledError(str(exc)) from exc

    device = torch.device("cuda:0" if use_gpu and torch.cuda.is_available() else "cpu")
    cache_key = f"moss_{device.type}"
    if cache_key in _asr_model_cache:
        return _asr_model_cache[cache_key]
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    try:
        model = AutoModelForCausalLM.from_pretrained(
            MOSS_MODEL_ID, revision=MOSS_HF_REVISION, trust_remote_code=True, dtype="auto",
            attn_implementation="sdpa",
        ).to(dtype=dtype).to(device).eval()
        processor = AutoProcessor.from_pretrained(MOSS_MODEL_ID, revision=MOSS_HF_REVISION,
                                                  trust_remote_code=True)
    except Exception as exc:
        if is_network_error(exc):
            raise ModelDownloadError(
                f"Couldn't download the {MOSS_MODEL_ID} model.\n\nThis is a network problem, "
                "not a problem with your audio. The model is fetched from Hugging Face the "
                "first time you use it.") from exc
        raise
    loaded = (model, processor, device, dtype)
    _asr_model_cache[cache_key] = loaded
    return loaded


def moss_segments_from_transcript(parsed) -> list:
    """Plain {"start", "end", "text", "speaker"} dicts from
    moss_transcribe_diarize.parse_transcript()'s TranscriptSegment objects,
    dropping empty text and turns with no positive length."""
    out = []
    for seg in parsed or []:
        text = (getattr(seg, "text", "") or "").strip()
        start, end = float(getattr(seg, "start", 0.0)), float(getattr(seg, "end", 0.0))
        if not text or end <= start:
            continue
        speaker = getattr(seg, "speaker", None)
        out.append({"start": start, "end": end, "text": text,
                    "speaker": str(speaker) if speaker else None})
    return out


class MossTranscribeDiarizeBackend:
    """Experimental: transcribes the whole file in one pass and returns
    segments that also carry a "speaker" label (e.g. "S01")."""
    name = "moss_td"

    def transcribe(self, audio_path, language=None, use_gpu=False, run_info=None):
        """Segments as {"start", "end", "text", "speaker"}. language is
        accepted for interface parity; MOSS detects it itself. run_info, if
        given, is filled with {"device": "cuda"|"cpu", "truncated": bool}."""
        model, processor, device, dtype = load_moss_transcribe_diarize(use_gpu=use_gpu)
        try:
            from moss_transcribe_diarize import parse_transcript
            from moss_transcribe_diarize.inference_utils import (
                build_transcription_messages, generate_transcription)
        except ImportError as exc:
            raise MossNotInstalledError(str(exc)) from exc
        messages = build_transcription_messages(audio_path)
        result = generate_transcription(
            model, processor, messages, max_new_tokens=MOSS_MAX_NEW_TOKENS, do_sample=False,
            device=device, dtype=dtype)
        if run_info is not None:
            run_info["device"] = "cuda" if getattr(device, "type", "") == "cuda" else "cpu"
            run_info["truncated"] = int(result.get("generated_tokens") or 0) >= MOSS_MAX_NEW_TOKENS
        return moss_segments_from_transcript(parse_transcript(result.get("text") or ""))


# The seam: every selectable transcription backend by its stored
# asr_backend_choice value. Experimental ones are listed separately so a
# caller can refuse them unless their toggle is on.
BACKENDS = {
    WhisperBackend.name: WhisperBackend,
    Qwen3ASRBackend.name: Qwen3ASRBackend,
    Qwen3ASRVadBackend.name: Qwen3ASRVadBackend,
    Qwen3ASRLongBackend.name: Qwen3ASRLongBackend,
    MossTranscribeDiarizeBackend.name: MossTranscribeDiarizeBackend,
}
EXPERIMENTAL_BACKENDS = frozenset({MossTranscribeDiarizeBackend.name})


def get_backend(name: str):
    """A new backend instance for a stored asr_backend_choice. Raises
    ValueError for an unknown name."""
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(f"Unknown transcription backend {name!r}.") from None
