"""
services/transcribe_service.py -- the Transcript-stage action for one
drama, for the /api/transcribe routes (api/routers/transcribe_routes.py)
and media_upload_service's upload-and-transcribe.

Applying a finished result can't depend on a client coming back for it
over a stateless API. The user's resolved decision (2026-09-28): the
background job itself does the whole pipeline (ASR, alignment, the DB
write, and the optional diarization chain-start) and reports a single "done" outcome a
client can poll for via the existing GET /api/jobs/{id} -- no separate
"apply" call, and no risk of "job succeeded but nothing was saved" if a
client never follows up.

Migration Slice 21 extends this with hardsub_ocr transcript_mode --
reading captions burned into video, via hardsub_ocr.extract_hardsub_
subtitles (the OCR cues already carry real per-cue timing, so unlike
Whisper's own text there's no separate alignment step -- same reasoning
as run_hardsub_ocr_job's own docstring).

Migration Slice 34 makes the run honour the two experimental Qwen3 choices:
asr_backend_choice == "qwen3_asr" (whisper transcript_mode) re-transcribes Whisper's VAD segments
with asr_backend.Qwen3ASRBackend, replacing only the text; alignment_method
== "qwen3_forced_align" (have_transcript mode) aligns the supplied
transcript with forced_align.align_with_qwen3. Built with mocks only -- the
real-model check is still owed by the user. Forced alignment needs a known
transcript, so requesting it in Whisper-text-only mode is an
InvalidInputError; a missing qwen-asr/torch package is a
DependencyUnavailableError, both raised at start (not inside the job).

The `chunk_and_tag` novel_narration path is services/narration_service.py.
A run needs its media already on disk; upload is media_upload_service.

Auto-tune (Step 6h's "🪄 Auto-tune" speech-splitting sensitivity) is a
separate action, not chained off the transcribe run: start_autotune_run
re-transcribes the drama's audio once per candidate min_silence_ms in ONE
process job (so Cancel terminates it mid-decode) and scores each with
score_autotune_segments. Nothing is applied by the job itself;
apply_autotune_candidate writes only the drama's own min_silence_ms column (db.update_drama, one field), and
only for a candidate the finished job actually measured. No paid engine is
used (PAID_ENGINE_FUNCTIONS is empty): the run is local ASR.

No FastAPI import: plain functions, plain dicts in, plain
values out. The one exception to "plain dicts" is start_transcribe_run,
which starts a real background job. Audio transcription runs in its own
process (background_jobs.start_process_job, _transcribe_worker) so Cancel
kills a model call that has no cancel point of its own; the parent applies
the result to the drama in the job's on_done hook, so the worker never
writes the database. Hardsub OCR, which already stops between frames,
stays a thread job (_run_transcribe_and_apply_job).
"""
import contextlib
import errno
import functools
import glob
import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import background_jobs
import core as core_module
import db
import diagnostics
import raw_transcript
import storage
from core import SOURCE_LANGUAGES, Line, align_transcript_to_timing, split_user_transcript, transcribe_for_timing
from services import asr_options_service, diarization_service, settings_service, source_service
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError, UnsupportedOperationError)
from translate_engines import redact_secrets

# Auto-tune functions that may spend on a paid engine: none (local ASR only).
PAID_ENGINE_FUNCTIONS = ()

# The per-drama tuning values used when the drama has none stored.
_DEFAULT_TUNING = {
    "whisper_size": core_module.DEFAULT_WHISPER_SIZE,
    "beam_size": 5,
    "min_silence_ms": 300,
    "vad_threshold": 0.5,
    "separate_vocals_first": False,
    "separation_backend": "auto",
    "realign_long_segments": False,
    "whisper_fast_mode": False,
    "use_groq": False,
}


CPU_DEFAULT_WHISPER_SIZE = "medium"

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


def _audio_duration_seconds(path) -> Optional[float]:
    """Best-effort audio length via ffprobe; None when it can't be read."""
    try:
        import media_inspect
        info = media_inspect.run_ffprobe(path, timeout=30)
        return float((info.get("format") or {}).get("duration") or 0.0) or None
    except Exception:
        return None


# A running job never reports 100%: only completion does. When Qwen3-ASR
# re-transcribes after Whisper, Whisper's stage fills 0..QWEN_SPLIT and
# Qwen3's batches fill QWEN_SPLIT..RUNNING_MAX; otherwise Whisper's stage
# fills 0..RUNNING_MAX.
RUNNING_MAX = 0.99
QWEN_SPLIT = 0.85


_SPEED_SETTING = "transcribe_speed"
# Outside this range a reading is a clock glitch or a near-empty file, not a speed.
_SPEED_BOUNDS = (0.01, 1000.0)
_SPEED_MIN_WORK_SECONDS = 5.0


def _speed_key(model: str, on_gpu: bool) -> str:
    return f"{model}|{'gpu' if on_gpu else 'cpu'}"


def measured_transcribe_speed(model: str, on_gpu: bool) -> Optional[float]:
    """Seconds of audio transcribed per second of work on the last finished
    run of this (model, device), or None. Never raises."""
    try:
        stored = db.get_app_setting(_SPEED_SETTING, {})
        value = stored.get(_speed_key(model, on_gpu)) if isinstance(stored, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        return value if _SPEED_BOUNDS[0] <= value <= _SPEED_BOUNDS[1] else None
    except Exception:
        return None


def record_transcribe_speed(model: str, on_gpu: bool, audio_seconds, work_seconds) -> None:
    """Keeps an exponential average (half old, half new) of a finished run's
    speed. Ignores non-numeric or out-of-range readings; never raises, so a
    settings hiccup cannot fail a finished transcription."""
    try:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (audio_seconds, work_seconds)):
            return
        if work_seconds < _SPEED_MIN_WORK_SECONDS or audio_seconds <= 0:
            return
        speed = float(audio_seconds) / float(work_seconds)
        if not _SPEED_BOUNDS[0] <= speed <= _SPEED_BOUNDS[1]:
            return
        stored = db.get_app_setting(_SPEED_SETTING, {})
        stored = dict(stored) if isinstance(stored, dict) else {}
        key = _speed_key(model, on_gpu)
        old = measured_transcribe_speed(model, on_gpu)
        stored[key] = round(speed if old is None else (old + speed) / 2, 4)
        db.set_app_setting(_SPEED_SETTING, stored)
    except Exception:
        pass


def _raise_if_job_cancelled(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def _drama_audio_path(drama_id: int, drama: dict) -> Optional[str]:
    """Mirrors source_service._audio_available's own file-exists check."""
    audio_filename = drama.get("audio_filename")
    if not audio_filename:
        return None
    path = os.path.join(db.drama_dir(drama_id), audio_filename)
    return path if os.path.exists(path) else None


def _drama_video_path(drama_id: int, drama: dict) -> Optional[str]:
    """Same video-source check as source_service.get_source_config's
    has_video_source -- source_video_filename set,
    no existence check on disk (matching source_service, which also only
    checks presence of the filename for video, unlike audio)."""
    video_filename = drama.get("source_video_filename")
    if not video_filename:
        return None
    return os.path.join(db.drama_dir(drama_id), video_filename)


def _default_hardsub_backend(source_language: str) -> str:
    """PaddleOCR for Chinese (confirmed more accurate on
    stylized/small captions), Tesseract otherwise."""
    return "paddle" if source_language == "zh" else "tesseract"


def build_auto_initial_prompt(drama_id: int, extra_names: str = "") -> str:
    """Whisper's automatic initial_prompt for one drama: the series
    glossary's names
    (core.build_initial_prompt) and any extra_names the user typed, joined
    with "、" and ended with "。"; then, when the drama has a
    raw_novel_context.txt, a bounded novel excerpt merged in names-first and
    trimmed by core.combine_initial_prompt (its 900-character cap). Shared by
    cli.cmd_align (no extra names) and the API transcribe/auto-tune paths so
    they can't drift. Returns "" when there's nothing to prime with. Raises
    NotFoundError for an unknown drama id."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    terms = db.list_glossary_terms(drama["series_id"]) if drama.get("series_id") else []
    glossary_names = core_module.build_initial_prompt(terms).rstrip("。")
    prompt = "、".join(x for x in (glossary_names, (extra_names or "").strip()) if x)
    if prompt:
        prompt += "。"
    novel_path = os.path.join(db.drama_dir(drama_id), "raw_novel_context.txt")
    if os.path.exists(novel_path):
        with open(novel_path, "r", encoding="utf-8") as f:
            prompt = core_module.combine_initial_prompt(
                prompt, core_module.extract_novel_excerpt_for_prompt(f.read()))
    return prompt


def _resolve_initial_prompt(drama_id: int, initial_prompt, extra_names) -> str:
    """A non-empty initial_prompt is an explicit full override; otherwise the
    automatic prompt plus the typed extra names."""
    if not isinstance(initial_prompt, str) or not isinstance(extra_names, str):
        raise InvalidInputError("initial_prompt and extra_names must be text.")
    if initial_prompt.strip():
        return initial_prompt
    return build_auto_initial_prompt(drama_id, extra_names)


def get_transcribe_config(drama_id: int) -> dict:
    """Read-only Transcript-stage summary for one drama: which action the
    "Transcribe & Align" button would run (from Slice 19's transcript_mode),
    whether its Whisper model is already downloaded, and every tuning knob
    with its current per-drama value (falling back to _DEFAULT_TUNING).
    Raises NotFoundError for an unknown drama id."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    source = source_service.get_source_config(drama_id)
    whisper_size = stored_whisper_size(drama)

    return {
        "drama_id": drama_id,
        "transcript_mode": source["transcript_mode"],
        "has_audio_pipeline": source["has_audio_pipeline"],
        "audio_available": source["audio_available"],
        "alignment_method": drama.get("alignment_method") or "whisper_diff",
        "asr_backend_choice": drama.get("asr_backend_choice") or "whisper",
        "whisper_size": whisper_size,
        "whisper_model_cached": core_module.is_whisper_model_cached(whisper_size),
        "measured_speed": measured_transcribe_speed(whisper_size, settings_service.get_use_gpu()),
        "whisper_installed": diagnostics.check_dependency("faster_whisper"),
        "beam_size": drama.get("beam_size") or _DEFAULT_TUNING["beam_size"],
        "min_silence_ms": drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"],
        "vad_threshold": drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"],
        "separate_vocals_first": bool(drama.get("separate_vocals_first")),
        "separation_backend": drama.get("separation_backend") or _DEFAULT_TUNING["separation_backend"],
        "realign_long_segments": bool(drama.get("realign_long_segments")),
        "whisper_fast_mode": bool(drama.get("whisper_fast_mode")),
        "use_groq": bool(drama.get("use_groq")),
        "has_video_source": source["has_video_source"],
        "hardsub_ocr_backend": drama.get("hardsub_ocr_backend")
                               or _default_hardsub_backend(source["source_language"]),
        "hardsub_interval_sec": drama.get("hardsub_interval_sec") or 1.0,
        "auto_initial_prompt": build_auto_initial_prompt(drama_id),
    }


_BOOL_FIELDS = ("separate_vocals_first", "realign_long_segments", "whisper_fast_mode", "use_groq")
_SEPARATION_BACKENDS = ("auto", "audio_separator", "demucs")


def update_transcribe_config(drama_id: int, **fields) -> dict:
    """Field-scoped partial update for the tuning knobs Slice 20 newly
    persists (whisper_size/alignment_method/asr_backend_choice already had
    their own DB columns and are updated the same way elsewhere -- accepted
    here too for a single write path). Only fields actually passed (not
    None) are validated and written. Raises NotFoundError for an unknown
    drama id, InvalidInputError for an out-of-range/unknown value. Returns
    the updated get_transcribe_config(drama_id)."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    updates = {}
    if "whisper_size" in fields and fields["whisper_size"] is not None:
        # A fixed set: faster-whisper downloads whatever repo name it is given.
        if fields["whisper_size"] not in _allowed_whisper_sizes():
            raise InvalidInputError(f"Unknown whisper_size {fields['whisper_size']!r}.")
        updates["whisper_size"] = fields["whisper_size"]
    if "alignment_method" in fields and fields["alignment_method"] is not None:
        if fields["alignment_method"] not in ("whisper_diff", "qwen3_forced_align"):
            raise InvalidInputError(f"Unknown alignment_method {fields['alignment_method']!r}.")
        updates["alignment_method"] = fields["alignment_method"]
    if "asr_backend_choice" in fields and fields["asr_backend_choice"] is not None:
        if fields["asr_backend_choice"] not in ("whisper", "qwen3_asr", "qwen3_asr_vad", "moss_td"):
            raise InvalidInputError(f"Unknown asr_backend_choice {fields['asr_backend_choice']!r}.")
        # Only a change TO moss_td needs the toggle: the form re-sends the
        # stored value with every save, and a run start checks it again.
        if (fields["asr_backend_choice"] == "moss_td"
                and drama.get("asr_backend_choice") != "moss_td"
                and not asr_options_service.get_moss_experimental()):
            raise InvalidInputError(_MOSS_OFF_MESSAGE)
        updates["asr_backend_choice"] = fields["asr_backend_choice"]
    if "beam_size" in fields and fields["beam_size"] is not None:
        if not 1 <= fields["beam_size"] <= 10:
            raise InvalidInputError("beam_size must be between 1 and 10.")
        updates["beam_size"] = fields["beam_size"]
    if "min_silence_ms" in fields and fields["min_silence_ms"] is not None:
        if not 300 <= fields["min_silence_ms"] <= 3000:
            raise InvalidInputError("min_silence_ms must be between 300 and 3000.")
        updates["min_silence_ms"] = fields["min_silence_ms"]
    if "vad_threshold" in fields and fields["vad_threshold"] is not None:
        if not 0.1 <= fields["vad_threshold"] <= 0.9:
            raise InvalidInputError("vad_threshold must be between 0.1 and 0.9.")
        updates["vad_threshold"] = fields["vad_threshold"]
    if "separation_backend" in fields and fields["separation_backend"] is not None:
        if fields["separation_backend"] not in _SEPARATION_BACKENDS:
            raise InvalidInputError(f"Unknown separation_backend {fields['separation_backend']!r}.")
        updates["separation_backend"] = fields["separation_backend"]
    if "hardsub_ocr_backend" in fields and fields["hardsub_ocr_backend"] is not None:
        if fields["hardsub_ocr_backend"] not in ("tesseract", "paddle"):
            raise InvalidInputError(f"Unknown hardsub_ocr_backend {fields['hardsub_ocr_backend']!r}.")
        updates["hardsub_ocr_backend"] = fields["hardsub_ocr_backend"]
    if "hardsub_interval_sec" in fields and fields["hardsub_interval_sec"] is not None:
        if not 0.5 <= fields["hardsub_interval_sec"] <= 3.0:
            raise InvalidInputError("hardsub_interval_sec must be between 0.5 and 3.0.")
        updates["hardsub_interval_sec"] = fields["hardsub_interval_sec"]
    for bf in _BOOL_FIELDS:
        if bf in fields and fields[bf] is not None:
            updates[bf] = 1 if fields[bf] else 0

    if updates:
        db.update_drama(drama_id, **updates)

    return get_transcribe_config(drama_id)


def _speaker_range(expected_speakers=None, min_speakers=None, max_speakers=None):
    """Step 105: (min_speakers, max_speakers) for the chained speaker
    detection, each None when unset. Raises InvalidInputError for a bad
    range, or a range combined with an exact count (diarize.validate_speaker_hints)."""
    import diarize as diarize_module
    try:
        _num, lo, hi = diarize_module.validate_speaker_hints(
            expected_speakers, min_speakers, max_speakers)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    return lo, hi


_MOSS_OFF_MESSAGE = ("MOSS-Transcribe-Diarize is experimental and turned off. Turn it on in "
                     "Settings > Transcription experiments first.")


def _require_moss_backend() -> None:
    """Step 104: the experimental MOSS backend needs its Settings toggle on
    and its package installed; never falls back to Whisper silently."""
    if not asr_options_service.get_moss_experimental():
        raise InvalidInputError(_MOSS_OFF_MESSAGE)
    if not asr_options_service.moss_installed():
        raise DependencyUnavailableError(
            "MOSS-Transcribe-Diarize isn't installed. It installs from its GitHub repository "
            "(OpenMOSS/MOSS-Transcribe-Diarize), not from pip's index, and needs Transformers 5.")


def _require_vad_packages() -> None:
    """The speech-detection Qwen3 backend needs faster-whisper (it bundles the
    Silero VAD and decodes the audio for it)."""
    if importlib.util.find_spec("faster_whisper") is None:
        raise DependencyUnavailableError(
            "Qwen3 ASR with speech detection needs faster-whisper, which isn't installed. "
            "Install it with: pip install faster-whisper")


def require_qwen3_packages(feature: str) -> None:
    """Raises DependencyUnavailableError naming the missing package(s) and the
    pip line (qwen-asr's own Diagnostics entry: diagnostics.MODEL_ENGINE_REGISTRY)
    when qwen-asr or torch can't be imported, so a Qwen3 choice never
    silently degrades to plain Whisper."""
    missing = [name for name, module in (("qwen-asr", "qwen_asr"), ("torch", "torch"))
               if importlib.util.find_spec(module) is None]
    if missing:
        raise DependencyUnavailableError(
            f"{feature} needs {' and '.join(missing)}, which isn't installed. "
            "Install it with: pip install qwen-asr torch")


_CHINESE_SCRIPTS = ("simplified", "traditional")


def start_transcribe_run(drama_id: int, source_language: Optional[str] = None,
                          chinese_script: Optional[str] = None,
                          transcript_text: Optional[str] = None, run_diarize: bool = False,
                          expected_speakers: Optional[int] = None,
                          initial_prompt: str = "", tesseract_cmd: Optional[str] = None,
                          extra_names: str = "", min_speakers: Optional[int] = None,
                          max_speakers: Optional[int] = None) -> dict:
    """Starts the background job that transcribes (or aligns a supplied
    transcript against) this drama's stored audio, then -- once that's
    done, inside the same job -- applies the result to the drama's lines
    and optionally chain-starts a diarization run. Poll status via the
    existing GET /api/jobs/{job_id}; once "done", the DB write has already
    happened (see this module's own docstring for why).

    source_language / chinese_script default to this drama's own stored
    values (Slice 19) when omitted. initial_prompt is an optional
    full override of Whisper's prompt; when it's empty or omitted the run
    uses build_auto_initial_prompt (series glossary plus extra_names plus
    raw-novel excerpt), the prompt cli.cmd_align builds without extra names.

    transcript_text is required (and only used) when this drama's
    transcript_mode is "have_transcript" -- per Slice 19, it's
    deliberately never persisted, matching today's one-click behavior of
    accepting it fresh each run.

    hardsub_ocr's own settings (backend, sample interval) come from this
    drama's persisted values (Slice 21) -- update them first via
    update_transcribe_config if a run needs different ones.
    tesseract_cmd is an optional, client-supplied path to the tesseract
    binary; omitted, the saved Settings Tesseract path applies
    (settings_service.get_tesseract_cmd).

    Raises NotFoundError for an unknown drama id; UnsupportedOperationError
    if there's no audio available (non-hardsub_ocr modes) or no video
    source (hardsub_ocr), or transcript_mode is "have_transcript" with no
    transcript_text supplied, or the drama has no audio pipeline
    (novel_narration); InvalidInputError for an unknown language/script;
    DependencyUnavailableError if use_groq is on with no Groq key
    configured, or a chosen Qwen3 backend's package (qwen-asr/torch) isn't
    installed; InvalidInputError if alignment_method is
    "qwen3_forced_align" while transcript_mode is "whisper" (forced
    alignment needs a known transcript); ConflictError if a transcription is already running for
    this drama."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    min_speakers, max_speakers = _speaker_range(expected_speakers, min_speakers, max_speakers)

    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(
            f"Drama {drama_id} has no audio pipeline (content mode "
            f"{drama.get('content_mode')!r}); novel chunking isn't available via this API yet.")

    source_language = source_language or drama.get("source_language") or "zh"
    chinese_script = chinese_script or drama.get("chinese_script") or "simplified"
    if source_language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"Unknown source_language {source_language!r}.")
    if chinese_script not in _CHINESE_SCRIPTS:
        raise InvalidInputError(f"Unknown chinese_script {chinese_script!r}.")

    transcript_mode = drama.get("transcript_mode") or "have_transcript"

    # Resolved independently of transcript_mode: a hardsub_ocr drama
    # commonly also has real audio on disk (the video upload extracts one
    # alongside saving the video), and diarization always needs actual audio
    # regardless of where the transcript text itself came from, so it
    # diarizes off this drama-level audio for every transcript_mode,
    # hardsub_ocr included.
    diarize_audio_path = _drama_audio_path(drama_id, drama)

    audio_path = None
    video_path = None
    if transcript_mode == "hardsub_ocr":
        video_path = _drama_video_path(drama_id, drama)
        if video_path is None:
            raise UnsupportedOperationError(f"No video source available for drama {drama_id}.")
    else:
        audio_path = _drama_audio_path(drama_id, drama)
        if audio_path is None:
            raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")
        if transcript_mode == "have_transcript" and not (transcript_text or "").strip():
            raise UnsupportedOperationError(
                "transcript_mode is 'have_transcript' but no transcript_text was supplied.")

    asr_backend_choice = drama.get("asr_backend_choice") or "whisper"
    alignment_method = drama.get("alignment_method") or "whisper_diff"
    if transcript_mode == "whisper":
        if alignment_method == "qwen3_forced_align":
            raise InvalidInputError(
                "Qwen3 forced alignment needs a transcript to align, but this drama is in "
                "Whisper-text-only mode. Supply a transcript, or set alignment_method back "
                "to 'whisper_diff'.")
        if asr_backend_choice == "qwen3_asr":
            require_qwen3_packages("Qwen3-ASR")
        elif asr_backend_choice == "qwen3_asr_vad":
            require_qwen3_packages("Qwen3-ASR")
            _require_vad_packages()
        elif asr_backend_choice == "moss_td":
            _require_moss_backend()
    elif transcript_mode == "have_transcript" and alignment_method == "qwen3_forced_align":
        require_qwen3_packages("Qwen3 forced alignment")

    hf_token = settings_service.resolve_key("hf_token") if run_diarize else None
    groq_api_key = settings_service.resolve_key("groq") if drama.get("use_groq") else None
    if drama.get("use_groq") and not groq_api_key:
        raise DependencyUnavailableError(
            "use_groq is on but no Groq API key is configured. Set one in Settings first.")

    job_id = f"transcribe_{drama_id}"
    description = f"Transcription (drama #{drama_id})"
    whisper_size = stored_whisper_size(drama)
    beam_size = drama.get("beam_size") or _DEFAULT_TUNING["beam_size"]
    min_silence_ms = drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"]
    vad_threshold = drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"]
    separation_backend = drama.get("separation_backend") or _DEFAULT_TUNING["separation_backend"]
    prompt = _resolve_initial_prompt(drama_id, initial_prompt or "", extra_names or "")
    use_gpu = settings_service.get_use_gpu()
    if transcript_mode == "hardsub_ocr":
        started = background_jobs.start_job(
            job_id, _run_transcribe_and_apply_job, job_id, drama_id, audio_path, transcript_mode,
            transcript_text, source_language, chinese_script, whisper_size, beam_size,
            min_silence_ms, vad_threshold, bool(drama.get("separate_vocals_first")),
            separation_backend,
            bool(drama.get("realign_long_segments")), bool(drama.get("whisper_fast_mode")),
            bool(drama.get("use_groq")), groq_api_key, hf_token, expected_speakers,
            prompt, video_path,
            drama.get("hardsub_ocr_backend") or _default_hardsub_backend(source_language),
            drama.get("hardsub_interval_sec") or 1.0,
            tesseract_cmd or settings_service.get_tesseract_cmd(), diarize_audio_path,
            use_gpu, asr_backend_choice, alignment_method,
            min_speakers=min_speakers, max_speakers=max_speakers,
            gpu_touching=True, description=description)
    else:
        # The worker's temp files go here; removed by on_finish however the
        # run ends, since a killed worker cannot clean up after itself.
        scratch_dir = storage.new_workdir(job_id)
        try:
            started = background_jobs.start_process_job(
                job_id, _transcribe_worker,
                args=(audio_path, transcript_mode, transcript_text, source_language,
                      chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
                      bool(drama.get("separate_vocals_first")), separation_backend,
                      bool(drama.get("realign_long_segments")),
                      bool(drama.get("whisper_fast_mode")), bool(drama.get("use_groq")), prompt,
                      use_gpu, asr_backend_choice, alignment_method,
                      settings_service.get_whisper_model_path(),
                      asr_options_service.get_qwen_asr_batch_size(),
                      asr_options_service.get_vad_refine_timing(), scratch_dir),
                gpu_touching=True, description=description, kill_whole_tree=True,
                # Spawn, not Linux's default fork: a forked child of a process
                # that has already initialised CUDA cannot use the GPU.
                start_method="spawn",
                on_done=functools.partial(
                    _apply_on_done, drama_id=drama_id, source_language=source_language,
                    whisper_size=whisper_size, use_gpu=use_gpu, transcript_mode=transcript_mode,
                    alignment_method=alignment_method, hf_token=hf_token,
                    diarize_audio_path=diarize_audio_path, expected_speakers=expected_speakers,
                    min_speakers=min_speakers, max_speakers=max_speakers),
                on_finish=functools.partial(_remove_scratch_dir, scratch_dir,
                                            part_dir=os.path.dirname(audio_path)))
        except BaseException:
            _remove_scratch_dir(scratch_dir)
            raise
        if not started:
            _remove_scratch_dir(scratch_dir)
    if not started:
        raise ConflictError(f"A transcription is already running for drama {drama_id}.")
    return {"job_id": job_id}


def validate_transcribe_options(drama_id: int, source_language: Optional[str] = None,
                                chinese_script: Optional[str] = None,
                                transcript_text: Optional[str] = None,
                                expected_speakers: Optional[int] = None,
                                min_speakers: Optional[int] = None,
                                max_speakers: Optional[int] = None, **_ignored) -> None:
    """Validate-only pre-check for a run that starts after the audio exists
    (B-09: upload-and-transcribe with a video). Raises the same errors as
    start_transcribe_run for everything that doesn't depend on the audio or
    video file being on disk yet; starts nothing. Keep in step with
    start_transcribe_run's checks."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    _speaker_range(expected_speakers, min_speakers, max_speakers)
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(
            f"Drama {drama_id} has no audio pipeline (content mode "
            f"{drama.get('content_mode')!r}); novel chunking isn't available via this API yet.")
    if (source_language or drama.get("source_language") or "zh") not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"Unknown source_language {source_language!r}.")
    if (chinese_script or drama.get("chinese_script") or "simplified") not in _CHINESE_SCRIPTS:
        raise InvalidInputError(f"Unknown chinese_script {chinese_script!r}.")
    transcript_mode = drama.get("transcript_mode") or "have_transcript"
    if transcript_mode == "have_transcript" and not (transcript_text or "").strip():
        raise UnsupportedOperationError(
            "transcript_mode is 'have_transcript' but no transcript_text was supplied.")
    asr_backend_choice = drama.get("asr_backend_choice") or "whisper"
    alignment_method = drama.get("alignment_method") or "whisper_diff"
    if transcript_mode == "whisper":
        if alignment_method == "qwen3_forced_align":
            raise InvalidInputError(
                "Qwen3 forced alignment needs a transcript to align, but this drama is in "
                "Whisper-text-only mode. Supply a transcript, or set alignment_method back "
                "to 'whisper_diff'.")
        if asr_backend_choice == "qwen3_asr":
            require_qwen3_packages("Qwen3-ASR")
        elif asr_backend_choice == "qwen3_asr_vad":
            require_qwen3_packages("Qwen3-ASR")
            _require_vad_packages()
        elif asr_backend_choice == "moss_td":
            _require_moss_backend()
    elif transcript_mode == "have_transcript" and alignment_method == "qwen3_forced_align":
        require_qwen3_packages("Qwen3 forced alignment")
    if drama.get("use_groq") and not settings_service.resolve_key("groq"):
        raise DependencyUnavailableError(
            "use_groq is on but no Groq API key is configured. Set one in Settings first.")


_MODEL_DOWNLOAD_SIZES = {"large-v3": "~3 GB", "large-v3-turbo": "~1.6 GB", "large-v2": "~3 GB", "large-v1": "~3 GB",
                         "large": "~3 GB", "medium": "~1.5 GB", "small": "~500 MB",
                         "base": "~150 MB", "tiny": "~75 MB"}


def _allowed_whisper_sizes() -> frozenset:
    """Whisper model names a drama may store: the Workspace picker's
    (core.WHISPER_MODELS, plus tiny/base in the React picker) and the known
    download sizes above. Anything else is refused by update_transcribe_config."""
    return frozenset(core_module.WHISPER_MODELS) | frozenset(_MODEL_DOWNLOAD_SIZES)


def default_whisper_size() -> str:
    """The model used when a drama has none saved: large-v3-turbo with the GPU
    on, medium without it. On a CPU the large models are many times slower
    than the clip and a 1.5-3 GB download; medium is the lighter choice that
    is still usable for Chinese, Japanese and Korean. A model the user saved
    is never replaced."""
    if settings_service.get_use_gpu():
        return _DEFAULT_TUNING["whisper_size"]
    return CPU_DEFAULT_WHISPER_SIZE


def stored_whisper_size(drama: dict) -> str:
    """The drama's saved whisper_size, or the default when it is empty or
    not one of _allowed_whisper_sizes() (e.g. a value planted in the DB by
    hand): an arbitrary string must never reach WhisperModel, where it
    would be read as a Hugging Face repo id or a local path. Logs a warning
    (without the value) when it falls back."""
    size = drama.get("whisper_size")
    if not size:
        return default_whisper_size()
    if size not in _allowed_whisper_sizes():
        import applog
        applog.get_logger().warning(
            f"drama {drama.get('id')}: stored whisper_size is not a known model size; "
            f"using the default {default_whisper_size()}")
        return default_whisper_size()
    return size


def _separation_device_label(kind: str) -> str:
    return {"gpu": "on GPU", "cpu": "on CPU (slow)"}.get(kind, "")


def _model_loading_message(whisper_size: str, cached: bool) -> str:
    if cached:
        return f"Loading Whisper model {whisper_size}..."
    size = _MODEL_DOWNLOAD_SIZES.get(whisper_size)
    hint = f", {size}" if size else ""
    return f"Loading Whisper model {whisper_size} (downloading on first use{hint})"


class _ThreadReporter:
    """Progress, stages and cancel for the pipeline running inside a thread
    job, which owns the job record directly."""

    def __init__(self, job_id):
        self.job_id = job_id

    def progress(self, frac, message=""):
        background_jobs.update_progress(self.job_id, frac, message)

    def stage(self, message, frac=0.0):
        return background_jobs.stage_ticker(self.job_id, message, frac=frac)

    def cancelled(self) -> bool:
        return background_jobs.is_cancel_requested(self.job_id)

    def raise_if_cancelled(self):
        _raise_if_job_cancelled(self.job_id)


class _ProcessStage:
    """stage_ticker's start/stop/with shape inside the worker process: the
    parent's watcher runs the real ticker (background_jobs.report_stage)."""

    def __init__(self, result_queue, message, frac):
        self._queue, self._message, self._frac = result_queue, message, frac
        self._open = False

    def start(self):
        if not self._open:
            self._open = True
            background_jobs.report_stage(self._queue, self._message, self._frac)
        return self

    def stop(self):
        """Idempotent."""
        if self._open:
            self._open = False
            background_jobs.report_stage(self._queue, "", self._frac)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


class _ProcessReporter:
    """The same inside the worker process: progress and stages go to the
    parent through the result queue. A cancel kills the process, so there is
    nothing to check here; the parent re-checks before applying anything.
    Every report and stage-boundary check ends the worker if its parent has
    died (background_jobs.exit_if_parent_gone)."""

    job_id = None

    def __init__(self, result_queue):
        self._queue = result_queue

    def progress(self, frac, message=""):
        background_jobs.report_progress(self._queue, frac, message)

    def stage(self, message, frac=0.0):
        return _ProcessStage(self._queue, message, frac)

    def cancelled(self) -> bool:
        background_jobs.exit_if_parent_gone()
        return False

    def raise_if_cancelled(self):
        background_jobs.exit_if_parent_gone()


def _run_transcribe_and_apply_job(job_id, drama_id, audio_path, transcript_mode, transcript_text,
                                   source_language, chinese_script, whisper_size, beam_size,
                                   min_silence_ms, vad_threshold, separate_vocals_first,
                                   separation_backend, realign_long_segments, whisper_fast_mode,
                                   use_groq, groq_api_key, hf_token, expected_speakers,
                                   initial_prompt="", video_path=None, hardsub_ocr_backend=None,
                                   hardsub_interval=1.0, tesseract_cmd=None,
                                   diarize_audio_path=None, use_gpu=False,
                                   asr_backend_choice="whisper", alignment_method="whisper_diff",
                                   min_speakers=None, max_speakers=None):
    """The thread-job body (start_transcribe_run uses it for hardsub_ocr):
    runs the pipeline in this thread (_transcribe_pipeline), applies the
    result to the drama's lines and optionally chain-starts diarization
    (_apply_transcription) -- all before reporting "done", so a client
    polling GET /api/jobs/{job_id} never observes a state where the job
    succeeded but nothing was saved.

    min_speakers/max_speakers (Step 105): a speaker-count range for the
    chained speaker detection, already checked by start_transcribe_run.

    use_gpu is the persisted server-side toggle (db.app_settings, read via
    settings_service.get_use_gpu() in start_transcribe_run, default off).

    diarize_audio_path is resolved once in start_transcribe_run, from the
    drama's own stored audio_filename, independent of transcript_mode --
    for a hardsub_ocr drama there is no transcribe-time audio_path at all
    (see module docstring), but a real one commonly still exists on disk
    (the video-upload flow extracts it alongside the video), and
    diarization always needs actual audio regardless of where the
    transcript text came from. If it's not available, diarization is
    skipped (diarize_started stays False), same as the existing
    no-hf_token case."""
    outcome = _transcribe_pipeline(
        _ThreadReporter(job_id), audio_path, transcript_mode, transcript_text, source_language,
        chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
        separate_vocals_first, separation_backend, realign_long_segments, whisper_fast_mode,
        use_groq, groq_api_key, initial_prompt, use_gpu, asr_backend_choice, alignment_method,
        local_model_path=settings_service.get_whisper_model_path(),
        qwen_batch_size=asr_options_service.get_qwen_asr_batch_size(),
        video_path=video_path, hardsub_ocr_backend=hardsub_ocr_backend,
        hardsub_interval=hardsub_interval, tesseract_cmd=tesseract_cmd)
    background_jobs.set_result(job_id, _apply_transcription(
        job_id, drama_id, outcome, source_language=source_language, whisper_size=whisper_size,
        use_gpu=use_gpu, transcript_mode=transcript_mode, alignment_method=alignment_method,
        hf_token=hf_token, diarize_audio_path=diarize_audio_path,
        expected_speakers=expected_speakers, min_speakers=min_speakers,
        max_speakers=max_speakers))


def _transcribe_worker(audio_path, transcript_mode, transcript_text, source_language,
                       chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
                       separate_vocals_first, separation_backend, realign_long_segments,
                       whisper_fast_mode, use_groq, initial_prompt, use_gpu, asr_backend_choice,
                       alignment_method, local_model_path, qwen_batch_size, vad_refine_timing,
                       scratch_dir, result_queue):
    """Process-job target, started with spawn on every platform (top level
    and plain arguments only, so it pickles; nothing here may depend on
    state set up in the parent process after import): runs the pipeline for
    an audio transcript_mode and
    puts ("ok", outcome) -- the plain dict _apply_transcription takes -- or
    ("error", type name, redacted message). Writes nothing to the database.
    Every temp file goes under scratch_dir, which the parent removes however
    the run ends. The Groq key is read from the environment here, never
    passed in.

    Not covered by scratch_dir: model downloads go to their own caches. A
    cancel during audio-separator's first download of its model leaves a
    truncated file there (the library writes straight to the final path and
    skips a file that exists), and loading it fails on the next run until
    that file is deleted."""
    background_jobs.start_own_process_group()
    try:
        os.makedirs(scratch_dir, exist_ok=True)
        tempfile.tempdir = scratch_dir
        groq_api_key = settings_service.resolve_key("groq") if use_groq else None
        outcome = _transcribe_pipeline(
            _ProcessReporter(result_queue), audio_path, transcript_mode, transcript_text,
            source_language, chinese_script, whisper_size, beam_size, min_silence_ms,
            vad_threshold, separate_vocals_first, separation_backend, realign_long_segments,
            whisper_fast_mode, use_groq, groq_api_key, initial_prompt, use_gpu,
            asr_backend_choice, alignment_method, local_model_path=local_model_path,
            qwen_batch_size=qwen_batch_size, vad_refine_timing=vad_refine_timing,
            vocals_work_dir=scratch_dir)
        result_queue.put(("ok", outcome))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))


def _move_into_place(src, dst):
    """Moves src over dst so a reader never sees a partial dst: os.replace
    on one volume (shutil.move would copy then delete when dst exists on
    Windows). Across volumes, copies next to dst first and replaces from
    there."""
    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    fd, tmp = tempfile.mkstemp(prefix=".part-", suffix=os.path.splitext(dst)[1],
                               dir=os.path.dirname(dst) or None)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    with contextlib.suppress(OSError):
        os.remove(src)


def _remove_scratch_dir(path, _job_id=None, part_dir=None):
    shutil.rmtree(path, ignore_errors=True)
    if part_dir:
        # A worker killed during _move_into_place's cross-volume copy leaves
        # its .part- file beside vocals.wav, outside the scratch folder.
        for leftover in glob.glob(os.path.join(glob.escape(part_dir), ".part-*.wav")):
            with contextlib.suppress(OSError):
                os.remove(leftover)


def _apply_on_done(job_id, outcome, **apply_kwargs):
    """on_done for the transcribe process job: its return value is the job's
    result (background_jobs.start_process_job)."""
    return _apply_transcription(job_id, apply_kwargs.pop("drama_id"), outcome, **apply_kwargs)


def _transcribe_pipeline(rep, audio_path, transcript_mode, transcript_text, source_language,
                         chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
                         separate_vocals_first, separation_backend, realign_long_segments,
                         whisper_fast_mode, use_groq, groq_api_key, initial_prompt, use_gpu,
                         asr_backend_choice, alignment_method, local_model_path=None,
                         qwen_batch_size=1, video_path=None, hardsub_ocr_backend=None,
                         hardsub_interval=1.0, tesseract_cmd=None, vocals_work_dir=None,
                         vad_refine_timing=False) -> dict:
    """Runs ASR (or hardsub OCR, thread jobs only) and returns a plain dict:
    {"failed_reason", ...} when nothing should be applied, else the lines
    and everything _apply_transcription needs. Touches no database row.
    `rep` is a _ThreadReporter or _ProcessReporter.

    Reuses transcribe_for_timing exactly as
    services.workspace_job_service.run_transcribe_job does (see that
    function's own docstring for the reasoning behind each parameter).

    local_model_path is Settings > Offline Whisper model folder (a folder
    holding an already-downloaded faster-whisper model, used instead of a
    Hugging Face download); qwen_batch_size is the saved Qwen3-ASR batch
    size; vad_refine_timing is the saved forced-aligner timing option of the
    "qwen3_asr_vad" backend. vocals_work_dir: where vocal separation writes before its result
    is moved next to the audio, so a killed worker leaves no partial file.

    asr_backend_choice / alignment_method (Slice 34) are the drama's stored
    choices: "qwen3_asr" and the experimental "moss_td" (Step 104: replaces
    Whisper, keeps MOSS's own speaker labels and skips the pyannote chain
    when it produced any) only apply in whisper transcript_mode, and
    "qwen3_forced_align" only in have_transcript mode. Import/download/other Qwen3 failures end the job
    with a failed_reason ("dependency_missing", "model_download",
    "qwen3_asr"); a forced-align ValueError (e.g. an oversized line) falls
    back to the diff alignment and is reported as forced_align_error."""
    gpu_fallback_msg = []
    whisper_clock = {}
    word_align_error = None
    forced_align_error = None
    coverage_msg = None
    device_msg = ""
    device_suffix = ""
    moss_run = transcript_mode == "whisper" and asr_backend_choice == "moss_td"
    moss_info = {}
    vad_run = transcript_mode == "whisper" and asr_backend_choice == "qwen3_asr_vad"

    if transcript_mode == "hardsub_ocr":
        import hardsub_ocr
        segments = hardsub_ocr.extract_hardsub_subtitles(
            video_path, language=source_language, sample_interval=hardsub_interval,
            ocr_backend=hardsub_ocr_backend, chinese_script=chinese_script,
            tesseract_cmd=tesseract_cmd, job_id=rep.job_id,
            cancel_check=rep.raise_if_cancelled,
            progress_cb=lambda frac: rep.progress(
                frac, f"Reading captions from video... {frac * 100:.0f}%"))
        if not segments:
            return {"failed_reason": "empty"}
        # OCR already produces real per-cue timing straight from the video --
        # no separate alignment step needed, same reasoning as the Whisper-
        # text-override branch below, just sourced from captions.
        lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                 for i, seg in enumerate(segments) if seg["text"].strip()]
        raw_backend, raw_model, raw_mode = "hardsub_ocr", hardsub_ocr_backend, "hardsub_ocr"
    else:
        if separate_vocals_first:
            import audio_preprocess
            vocals_path = os.path.join(os.path.dirname(audio_path), "vocals.wav")
            sep = {"ticker": None, "device": ""}

            def _sep_message(frac):
                where = f" {sep['device']}" if sep["device"] else ""
                return f"Separating vocals{where}, {frac * 100:.0f}%"

            def _sep_event(event, value):
                if sep["ticker"]:
                    sep["ticker"].stop()
                    sep["ticker"] = None
                if event == "loading":
                    # The model download/load cannot report progress.
                    sep["ticker"] = rep.stage(
                        "Loading the vocal separation model (downloads on first use)").start()
                elif event == "device":
                    sep["device"] = _separation_device_label(value)
                    rep.progress(0.0, _sep_message(0.0))

            rep.progress(0.0, "Separating vocals from background music...")
            try:
                separated = audio_preprocess.separate_vocals(
                    audio_path, os.path.join(vocals_work_dir, "vocals.wav")
                    if vocals_work_dir else vocals_path, backend=separation_backend,
                    progress_cb=lambda frac: rep.progress(frac, _sep_message(frac)),
                    cancel_check_cb=rep.cancelled,
                    use_gpu=use_gpu, event_cb=_sep_event)
            except audio_preprocess.VocalSeparationCancelled:
                return {"failed_reason": "cancelled"}
            except audio_preprocess.VocalSeparationError as exc:
                return {"failed_reason": "vocal_separation", "detail": str(exc)}
            finally:
                if sep["ticker"]:
                    sep["ticker"].stop()
            if vocals_work_dir and os.path.abspath(separated) != os.path.abspath(vocals_path):
                _move_into_place(separated, vocals_path)
                separated = vocals_path
            audio_path = separated

        if rep.cancelled():
            return {"failed_reason": "cancelled"}

        qwen_run = transcript_mode == "whisper" and asr_backend_choice == "qwen3_asr" and not moss_run
        stage_max = QWEN_SPLIT if qwen_run else RUNNING_MAX
        step_label = " (step 1 of 2)" if qwen_run else ""

        if vad_run:
            # No Whisper: the speech detector draws the boundaries and
            # Qwen3-ASR writes the text.
            if rep.cancelled():
                return {"failed_reason": "cancelled"}
            vad_ticker = rep.stage(
                "Finding speech and transcribing with Qwen3-ASR (no percent until the "
                "first batch finishes)").start()

            def _vad_progress(frac):
                vad_ticker.stop()
                rep.progress(min(max(frac, 0.0), 1.0) * RUNNING_MAX,
                             f"Transcribing with Qwen3-ASR... {frac * 100:.0f}%")

            import asr_backend
            import vad_segments
            try:
                segments = asr_backend.get_backend("qwen3_asr_vad").transcribe(
                    audio_path, source_language, use_gpu=use_gpu, batch_size=qwen_batch_size,
                    progress_cb=_vad_progress, cancel_check=rep.raise_if_cancelled,
                    refine_timing=vad_refine_timing)
            except vad_segments.VadNotInstalledError as exc:
                return {"failed_reason": "dependency_missing",
                        "detail": "Speech detection needs faster-whisper (it bundles the Silero "
                                  "VAD): pip install faster-whisper "
                                  f"({redact_secrets(str(exc))})"}
            except ImportError as exc:
                return {"failed_reason": "dependency_missing",
                        "detail": "Qwen3-ASR needs qwen-asr and torch: pip install qwen-asr torch "
                                  f"({redact_secrets(str(exc))})"}
            except core_module.ModelDownloadError as exc:
                return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
            except ValueError as exc:
                return {"failed_reason": "qwen3_asr", "detail": redact_secrets(str(exc))}
            except background_jobs.JobCancelled:
                core_module.release_gpu_models()
                raise
            finally:
                vad_ticker.stop()
        elif moss_run:
            # Step 104 (experimental): one pass that also labels speakers;
            # replaces Whisper for this run, only when chosen explicitly.
            rep.progress(0.0, "Transcribing and detecting speakers with "
                              "MOSS-Transcribe-Diarize (experimental)...")
            try:
                import asr_backend
                segments = asr_backend.get_backend("moss_td").transcribe(
                    audio_path, source_language, use_gpu=use_gpu, run_info=moss_info)
            except ImportError as exc:
                return {"failed_reason": "dependency_missing",
                        "detail": "MOSS-Transcribe-Diarize isn't installed "
                                  f"({redact_secrets(str(exc))})"}
            except core_module.ModelDownloadError as exc:
                return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
            except Exception as exc:
                return {"failed_reason": "moss_td", "detail": redact_secrets(str(exc))}
            device_msg = "GPU" if moss_info.get("device") == "cuda" else "CPU"
            # One blocking call with no cancel hook: honour a cancel that
            # arrived meanwhile before replacing any lines.
            if rep.cancelled():
                return {"failed_reason": "cancelled"}
        elif use_groq:
            rep.progress(0.0, "Transcribing via Groq's cloud API...")
            try:
                segments = core_module.transcribe_with_groq(
                    audio_path, source_language, groq_api_key,
                    progress_cb=lambda frac: rep.progress(
                        min(frac, 1.0) * stage_max,
                        f"Transcribing via Groq's cloud API{step_label}... {frac * 100:.0f}%"))
            except core_module.GroqTranscriptionError as exc:
                # Raw text stays in the in-memory result (as for vocal
                # separation and model download here); jobs_service.
                # project_result redacts "detail" before it is returned or
                # mirrored to job_records.
                return {"failed_reason": "groq", "detail": str(exc)}
        else:
            try:
                model_cached = bool(local_model_path) or core_module.is_whisper_model_cached(
                    whisper_size)
                # Loaded here (cached in core, so transcribe_for_timing reuses
                # it) so the download/load phase and the device actually
                # chosen are visible instead of "Starting..." for minutes.
                with rep.stage(_model_loading_message(whisper_size, model_cached)):
                    core_module.load_whisper_model(whisper_size, use_gpu=use_gpu,
                                                   local_model_path=local_model_path)
                rep.raise_if_cancelled()
                device_msg = core_module.describe_whisper_device(
                    core_module.get_whisper_device_info(whisper_size, use_gpu=use_gpu,
                                                        local_model_path=local_model_path))
                device_suffix = f" ({device_msg})" if device_msg else ""
                rep.progress(0.0, f"Transcribing... starting; the percent appears once "
                                  f"the first lines are found{device_suffix}")

                def _whisper_progress(frac):
                    # The clock starts at the first percent: model download and
                    # load are not transcription speed.
                    if frac > 0 and "t" not in whisper_clock:
                        whisper_clock.update(t=time.monotonic(), p=min(frac, 1.0))
                    rep.raise_if_cancelled()
                    rep.progress(min(frac, 1.0) * stage_max,
                                 f"Transcribing{step_label}... {frac * 100:.0f}%{device_suffix}")
                segments = transcribe_for_timing(
                    audio_path, whisper_size, language=source_language, use_gpu=use_gpu,
                    local_model_path=local_model_path, hf_token=None,
                    initial_prompt=initial_prompt,
                    beam_size=beam_size,
                    min_silence_duration_ms=min_silence_ms, vad_threshold=vad_threshold,
                    on_gpu_fallback=lambda exc: gpu_fallback_msg.append(core_module.short_reason(exc)),
                    progress_cb=_whisper_progress,
                    fast_mode=whisper_fast_mode)
                if "t" in whisper_clock:
                    whisper_clock["work"] = time.monotonic() - whisper_clock["t"]
            except core_module.ModelDownloadError as exc:
                return {"failed_reason": "model_download", "detail": str(exc)}
            except background_jobs.JobCancelled:
                core_module.release_gpu_models()   # hand the VRAM back on a cancel too
                raise

        if not segments:
            return {"failed_reason": "empty"}

        if realign_long_segments and not moss_run and not vad_run and not rep.cancelled():
            import word_align
            try:
                with rep.stage("Splitting long merged lines", frac=stage_max):
                    segments = word_align.realign_oversized_segments(
                        segments, audio_path, source_language, chinese_script=chinese_script)
            except word_align.WordAlignError as exc:
                word_align_error = str(exc)

        if transcript_mode == "whisper":
            raw_backend, raw_model, raw_mode = "whisper", whisper_size, "whisper"
            if moss_run:
                raw_backend, raw_model = "moss_td", "MOSS-Transcribe-Diarize"
            elif vad_run:
                raw_backend, raw_model = "qwen3_asr_vad", "Qwen3-ASR"
            elif asr_backend_choice == "qwen3_asr":
                if rep.cancelled():
                    return {"failed_reason": "cancelled"}
                # Timing stays Whisper's VAD segments; only the text is replaced
                # (asr_backend.py's module docstring explains why).
                qwen_ticker = rep.stage(
                    "Re-transcribing with Qwen3-ASR (step 2 of 2; no percent until "
                    "the first batch finishes)", frac=QWEN_SPLIT).start()

                def _qwen_progress(frac):
                    # The first finished batch ends the no-percent phase; the
                    # ticker would otherwise overwrite the message.
                    qwen_ticker.stop()
                    rep.raise_if_cancelled()
                    rep.progress(
                        QWEN_SPLIT + min(max(frac, 0.0), 1.0) * (RUNNING_MAX - QWEN_SPLIT),
                        f"Re-transcribing with Qwen3-ASR (step 2 of 2)... {frac * 100:.0f}%")

                try:
                    import asr_backend
                    segments = asr_backend.Qwen3ASRBackend().transcribe(
                        audio_path, source_language, whisper_segments=segments, use_gpu=use_gpu,
                        batch_size=qwen_batch_size, progress_cb=_qwen_progress)
                except ImportError as exc:
                    return {"failed_reason": "dependency_missing",
                            "detail": "Qwen3-ASR needs qwen-asr and torch: pip install qwen-asr torch "
                                      f"({redact_secrets(str(exc))})"}
                except core_module.ModelDownloadError as exc:
                    return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
                except ValueError as exc:
                    return {"failed_reason": "qwen3_asr", "detail": redact_secrets(str(exc))}
                finally:
                    qwen_ticker.stop()
                raw_backend, raw_model = "qwen3_asr", "Qwen3-ASR"
            lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"],
                          speaker=seg.get("speaker") or None, flag=seg.get("flag"),
                          flag_note=seg.get("flag_note") or "")
                     for i, seg in enumerate(
                         s for s in core_module.split_long_segments(segments)
                         if s["text"].strip())]
            coverage_msg = coverage_warning(
                segments, _audio_duration_seconds(audio_path),
                qwen3_asr=raw_backend == "qwen3_asr")
        else:
            rep.progress(RUNNING_MAX, "Aligning transcript to audio timing...")
            user_lines = split_user_transcript(transcript_text)
            if alignment_method == "qwen3_forced_align":
                if rep.cancelled():
                    return {"failed_reason": "cancelled"}
                rep.progress(RUNNING_MAX,
                             "Aligning with Qwen3-ForcedAligner (true forced alignment)...")
                try:
                    import forced_align
                    lines = forced_align.align_with_qwen3(
                        audio_path, user_lines, segments, language=source_language, use_gpu=use_gpu)
                except ImportError as exc:
                    return {"failed_reason": "dependency_missing",
                            "detail": "Qwen3 forced alignment needs qwen-asr and torch: "
                                      f"pip install qwen-asr torch ({redact_secrets(str(exc))})"}
                except core_module.ModelDownloadError as exc:
                    return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
                except ValueError as exc:
                    # Fall back to the diff alignment, but say so in the
                    # result instead of hiding it.
                    forced_align_error = redact_secrets(str(exc))
                    lines = align_transcript_to_timing(user_lines, segments)
            else:
                lines = align_transcript_to_timing(user_lines, segments)
            raw_backend, raw_model, raw_mode = "whisper", whisper_size, "aligned_transcript"

    core_module.release_gpu_models()
    return {"lines": lines, "segments": segments, "raw_backend": raw_backend,
            "raw_model": raw_model, "raw_mode": raw_mode, "audio_path": audio_path,
            "gpu_fallback_msgs": gpu_fallback_msg, "device_msg": device_msg,
            "whisper_clock": whisper_clock, "word_align_error": word_align_error,
            "forced_align_error": forced_align_error, "coverage_warning": coverage_msg,
            "moss_run": moss_run, "moss_truncated": bool(moss_info.get("truncated"))}


def _apply_transcription(job_id, drama_id, outcome, *, source_language, whisper_size, use_gpu,
                         transcript_mode, alignment_method, hf_token, diarize_audio_path,
                         expected_speakers, min_speakers=None, max_speakers=None) -> dict:
    """Applies a _transcribe_pipeline outcome to the drama and returns the
    job's result: replaces its lines (history snapshot first), writes the
    raw transcript, marks it "aligned", records MOSS speakers, chain-starts
    diarization when hf_token and diarize_audio_path are set, and records
    the run's speed. A failed outcome is returned unchanged and writes
    nothing; so does a run whose cancel arrived before the write."""
    if "failed_reason" in outcome:
        return outcome
    lines, segments = outcome["lines"], outcome["segments"]
    gpu_fallback_msg = outcome["gpu_fallback_msgs"]
    whisper_clock = outcome["whisper_clock"]
    forced_align_error = outcome["forced_align_error"]

    # Every stage can end with a cancel that arrived mid-call: never
    # replace the drama's lines after one.
    if background_jobs.is_cancel_requested(job_id):
        return {"failed_reason": "cancelled"}

    # Step 25 item 2's same safety rule, ported here: never let an empty
    # result silently wipe out an already-populated drama.
    existing_lines_before = db.load_line_objects(drama_id)
    if not lines and existing_lines_before:
        return {"failed_reason": "empty_kept_existing",
                "existing_line_count": len(existing_lines_before)}

    background_jobs.cancel_line_jobs(drama_id)
    if existing_lines_before:
        db.save_line_history_snapshot(drama_id, existing_lines_before, "before re-transcribe")
    # Full sync on purpose: these brand-new lines replace the drama's lines.
    # Line jobs were cancelled and the old lines snapshotted just above.
    db.save_lines(drama_id, lines)
    raw_transcript.write_raw_transcript(
        db.drama_dir(drama_id), segments, lines, backend=outcome["raw_backend"],
        model=outcome["raw_model"], language=source_language, mode=outcome["raw_mode"])
    db.update_drama(drama_id, status="aligned")

    # MOSS already labelled speakers: record them as characters and don't
    # chain pyannote over them (it would relabel every line).
    moss_speakers = sorted({ln.speaker for ln in lines if ln.speaker}) if outcome["moss_run"] else []
    for label in moss_speakers:
        db.upsert_character(drama_id, label)

    diarize_started = False
    if hf_token and diarize_audio_path and not moss_speakers:
        import diarize as diarize_module
        diarize_started = background_jobs.start_process_job(
            f"diarize_{drama_id}", diarize_module.diarize_subprocess_worker,
            args=(diarize_audio_path, hf_token, expected_speakers or None,
                  diarization_service.worker_options(min_speakers, max_speakers)),
            gpu_touching=True, description=f"Diarization (drama #{drama_id})",
            on_done=diarization_service.make_apply_on_done(
                drama_id, expected_speakers, min_speakers=min_speakers,
                max_speakers=max_speakers))

    if "work" in whisper_clock and whisper_clock["p"] < 0.5:
        # Only the Whisper pass counts, and only the part after its first
        # percent: audio_seconds * (1 - that first percent) over the time taken.
        audio_seconds = _audio_duration_seconds(outcome["audio_path"])
        if audio_seconds:
            record_transcribe_speed(
                whisper_size, bool(use_gpu) and not gpu_fallback_msg,
                audio_seconds * (1.0 - whisper_clock["p"]), whisper_clock["work"])

    return {
        "line_count": len(lines),
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "device": (f"GPU unavailable ({gpu_fallback_msg[0]}); using CPU"
                   if gpu_fallback_msg else outcome["device_msg"]) or None,
        "word_align_error": outcome["word_align_error"],
        "asr_backend": outcome["raw_backend"],
        "alignment_method": ("qwen3_forced_align" if transcript_mode == "have_transcript"
                             and alignment_method == "qwen3_forced_align"
                             and not forced_align_error else "whisper_diff"),
        "forced_align_error": forced_align_error,
        "coverage_warning": outcome["coverage_warning"],
        "diarize_started": diarize_started,
        **({"partial": True, "errors": [
            "MOSS stopped at its output limit; the end of the audio may be missing."]}
           if outcome["moss_truncated"] else {}),
    }


# ---------------------------------------------------------------------------
# Auto-tune speech-splitting sensitivity (Step 6h)
# ---------------------------------------------------------------------------

def autotune_job_id(drama_id: int) -> str:
    return f"autotune_{drama_id}"


def score_autotune_segments(candidate_ms, segments) -> dict:
    """One candidate's score: the number of long/merged lines
    (core.diagnose_line_coverage) and total non-empty lines."""
    cand_lines = [Line(idx=i, start=s["start"], end=s["end"], zh=s["text"])
                  for i, s in enumerate(segments or []) if (s.get("text") or "").strip()]
    coverage = core_module.diagnose_line_coverage(cand_lines)
    return {"candidate_ms": candidate_ms, "long_lines": len(coverage["long_lines"]),
            "total_lines": len(cand_lines)}


def _autotune_all_worker(audio_path, model_size, language, use_gpu, hf_token, initial_prompt,
                         beam_size, candidates, vad_threshold, fast_mode, result_queue):
    """Process-job target (top-level, picklable): transcribes once per
    candidate, holding every other setting constant, and returns only the
    scores (no segments, no token)."""
    try:
        results = []
        for n, candidate_ms in enumerate(candidates):
            background_jobs.report_progress(
                result_queue, n / len(candidates),
                f"Testing candidate {n + 1} of {len(candidates)} ({candidate_ms}ms)...")
            segments = core_module.transcribe_for_timing(
                audio_path, model_size, language=language, use_gpu=use_gpu,
                hf_token=hf_token, initial_prompt=initial_prompt, beam_size=beam_size,
                min_silence_duration_ms=candidate_ms, vad_threshold=vad_threshold,
                fast_mode=fast_mode)
            results.append(score_autotune_segments(candidate_ms, segments))
        best = min(results, key=lambda r: r["long_lines"])["candidate_ms"] if results else None
        result_queue.put(("ok", {"results": results, "best_candidate_ms": best}))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))


def start_autotune_run(drama_id: int, candidates: Optional[list] = None,
                       initial_prompt: str = "", extra_names: str = "") -> dict:
    """Starts the auto-tune process job for this drama's stored audio, using
    the drama's own persisted whisper_size/beam_size/vad_threshold/
    whisper_fast_mode and language.
    candidates defaults to core.DEFAULT_AUTOTUNE_CANDIDATES_MS; each must be
    an int in 300..3000 (the slider's range), at most 6, no duplicates.
    Poll get_autotune_status(drama_id) (GET /api/transcribe/dramas/{id}/
    autotune); its "done" result is
    {"results": [{candidate_ms, long_lines, total_lines}], "best_candidate_ms"}.

    NotFoundError, UnsupportedOperationError (no audio pipeline / no audio),
    InvalidInputError (bad candidates), ConflictError (already running)."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(f"Drama {drama_id} has no audio pipeline.")
    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")
    if candidates is None:
        candidates = list(core_module.DEFAULT_AUTOTUNE_CANDIDATES_MS)
    if (not isinstance(candidates, (list, tuple)) or not candidates or len(candidates) > 6
            or any(isinstance(c, bool) or not isinstance(c, int) or not 300 <= c <= 3000
                   for c in candidates)
            or len(set(candidates)) != len(candidates)):
        raise InvalidInputError(
            "candidates must be 1-6 distinct whole numbers between 300 and 3000 (ms).")
    initial_prompt = _resolve_initial_prompt(drama_id, initial_prompt, extra_names)
    job_id = autotune_job_id(drama_id)
    started = background_jobs.start_process_job(
        job_id, _autotune_all_worker,
        args=(audio_path, stored_whisper_size(drama),
              drama.get("source_language") or "zh", settings_service.get_use_gpu(),
              settings_service.resolve_key("hf_token") or None, initial_prompt,
              drama.get("beam_size") or _DEFAULT_TUNING["beam_size"], list(candidates),
              drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"],
              bool(drama.get("whisper_fast_mode"))),
        gpu_touching=True, description=f"Auto-tuning (drama #{drama_id})")
    if not started:
        raise ConflictError(f"Auto-tune is already running for drama {drama_id}.")
    return {"job_id": job_id, "candidates": list(candidates)}


def get_autotune_status(drama_id: int) -> dict:
    """{job_id, status, progress, message, result} for this drama's auto-tune
    job; result is {"results": [...], "best_candidate_ms"} only when done
    (else None). The message (or a failed job's error) is redacted.
    NotFoundError when the drama doesn't exist. No auto-tune job resident in
    this process (results live only in background_jobs memory) is the normal
    first answer: status "idle", job_id ""."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    job_id = autotune_job_id(drama_id)
    job = background_jobs.get_status(job_id)
    if not job:
        return {"job_id": "", "status": "idle", "progress": 0.0, "message": "", "result": None}
    status = job.get("status")
    result = None
    if status == "done":
        raw = job.get("result") or {}
        result = {
            "results": [{"candidate_ms": r.get("candidate_ms"), "long_lines": r.get("long_lines"),
                         "total_lines": r.get("total_lines")}
                        for r in raw.get("results") or [] if isinstance(r, dict)],
            "best_candidate_ms": raw.get("best_candidate_ms"),
        }
    message = job.get("error") if status == "error" else job.get("message")
    return {"job_id": job_id, "status": status, "progress": job.get("progress"),
            "message": redact_secrets(str(message)) if message else "", "result": result}


def apply_autotune_candidate(drama_id: int, candidate_ms: int) -> dict:
    """"Use Nms": stores the chosen value as this drama's
    own min_silence_ms (a single-column db.update_drama write -- nothing
    else on the drama or its lines is touched). Only a candidate measured
    by this drama's finished auto-tune job is accepted, so a stale or
    foreign value can't be applied through this path (the plain config
    update, update_transcribe_config, remains for hand-set values).
    Returns the updated transcribe config."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    job = background_jobs.get_status(autotune_job_id(drama_id))
    if not job or job.get("status") != "done":
        raise UnsupportedOperationError("No finished auto-tune results for this drama.")
    measured = {r.get("candidate_ms") for r in (job.get("result") or {}).get("results") or []}
    if isinstance(candidate_ms, bool) or candidate_ms not in measured:
        raise InvalidInputError("candidate_ms must be one of the measured candidates.")
    db.update_drama(drama_id, min_silence_ms=candidate_ms)
    return get_transcribe_config(drama_id)


# --- Re-transcribe one line (parity audit B1, inventory R23) ----------------
# Review's "Re-transcribe" re-runs Whisper on one line's own timing window,
# shows what it heard, and only on "Use this" replaces that line's source
# text: the job cuts the window, transcribes it
# with the drama's full-transcribe Whisper settings and the same automatic
# prompt, and keeps the proposal in its in-process result WITHOUT writing.
# The line text never goes through GET /api/jobs (only line_id does): it is
# read back raw with get_retranscribe_result (lines.read, like auto-tune's
# "results are only readable here"), and apply_retranscribe_line writes only
# `zh` for that line id when the client's expected base and proposal match
# the raw values held here and the line is unchanged since the job started (a
# compare-and-set, so the user's edits win). Proposals live in memory only:
# after an API restart the user re-transcribes. Local Whisper even when the
# drama's full transcribe uses Groq, so no paid-engine gate.

# Proposed text kept in the job result: same cap as a line edit.
_RETRANSCRIBE_MAX_CHARS = 2000
# ffmpeg cutting one line's window; a hung ffmpeg ends the job instead of
# holding the GPU slot.
_RETRANSCRIBE_SLICE_TIMEOUT_S = 120

# Running/queued jobs that replace this drama's lines or also write `zh`, so a
# one-line re-transcription alongside them would be pointless or race them.
_RETRANSCRIBE_BLOCKING_PREFIXES = ("transcribe_", "fixflag_", "resegment_", "narration_")


def retranscribe_line_job_id(drama_id: int) -> str:
    return f"retranscribe_{drama_id}"


def _find_line(drama_id: int, line_id: int):
    return next((ln for ln in db.load_line_objects(drama_id) if ln.id == line_id), None)


def start_retranscribe_line(drama_id: int, line_id: int, initial_prompt: str = "",
                            extra_names: str = "") -> dict:
    """Starts the GPU-queued job that re-transcribes one line's timing window
    and proposes new text for it (nothing is written; see
    apply_retranscribe_line). initial_prompt / extra_names resolve exactly as
    for a full transcribe run (_resolve_initial_prompt). Returns {job_id,
    drama_id, line_id}; poll GET /api/jobs/{job_id} for status (its result
    shows only line_id), then read the proposal with get_retranscribe_result.

    NotFoundError for an unknown drama or a line id that isn't this drama's;
    UnsupportedOperationError when the drama has no audio pipeline or no
    audio, or the line has no timing window; InvalidInputError for a
    non-text prompt; ConflictError while a re-transcription, a full
    transcription, fix-flagged, a re-segment or a narration run is running
    or queued for this drama."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    line = _find_line(drama_id, line_id)
    if line is None:
        raise NotFoundError(f"No line with id {line_id} in drama {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(f"Drama {drama_id} has no audio pipeline.")
    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")
    if not float(line.end) > float(line.start):
        raise UnsupportedOperationError("This line has no timing window to re-transcribe.")
    prompt = _resolve_initial_prompt(drama_id, initial_prompt, extra_names)
    for prefix in _RETRANSCRIBE_BLOCKING_PREFIXES:
        other = background_jobs.get_status(f"{prefix}{drama_id}")
        if other and other.get("status") in ("running", "queued"):
            raise ConflictError("Another job is changing this drama's lines. "
                                "Try again when it finishes.")
    job_id = retranscribe_line_job_id(drama_id)
    started = background_jobs.start_job(
        job_id, _run_retranscribe_line_job, job_id, drama_id, line_id, audio_path,
        float(line.start), float(line.end), line.zh,
        drama.get("source_language") or "zh",
        stored_whisper_size(drama),
        drama.get("beam_size") or _DEFAULT_TUNING["beam_size"],
        drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"],
        drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"],
        bool(drama.get("whisper_fast_mode")), settings_service.get_use_gpu(), prompt,
        gpu_touching=True, description=f"Re-transcribing a line (drama #{drama_id})")
    if not started:
        raise ConflictError("A line is already being re-transcribed for this drama.")
    return {"job_id": job_id, "drama_id": drama_id, "line_id": line_id}


def _run_retranscribe_line_job(job_id, drama_id, line_id, audio_path, start, end, zh_before,
                               source_language, whisper_size, beam_size, min_silence_ms,
                               vad_threshold, fast_mode, use_gpu, initial_prompt):
    """Job body: cut [start, end) from the drama's audio and transcribe it.
    Writes nothing to the line. Result on success: {"line_id", "proposed_zh",
    "base_zh", "base_start", "base_end"} (proposed_zh capped at
    _RETRANSCRIBE_MAX_CHARS; base_zh raw, for the apply compare), plus
    "gpu_fallback" when it ran on CPU. GET /api/jobs shows only line_id and
    gpu_fallback (jobs_service's allowlist); the text is read in-process by
    get_retranscribe_result and apply_retranscribe_line. A failed_reason
    instead when the audio couldn't be cut ("audio_slice", including an
    ffmpeg timeout), nothing was heard ("empty"), the job was cancelled, or
    the line no longer exists ("line_gone")."""
    # Which line this run is for, visible to pollers before it finishes.
    background_jobs.set_result(job_id, {"line_id": line_id}, mirror=True)
    slice_path = os.path.join(os.path.dirname(audio_path), f"_retranscribe_slice_{line_id}.wav")
    gpu_fallback = []
    try:
        try:
            core_module.extract_audio_slice(audio_path, start, end, slice_path,
                                            timeout=_RETRANSCRIBE_SLICE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            background_jobs.set_result(job_id, {
                "line_id": line_id, "failed_reason": "audio_slice",
                "detail": "Cutting this line's audio took too long and was stopped."})
            return
        except Exception:
            background_jobs.set_result(job_id, {"line_id": line_id, "failed_reason": "audio_slice",
                                                "detail": "Couldn't cut this line's audio."})
            return
        if background_jobs.is_cancel_requested(job_id):
            background_jobs.set_result(job_id, {"line_id": line_id, "failed_reason": "cancelled"})
            return
        background_jobs.update_progress(job_id, 0.1, _model_loading_message(
            whisper_size, core_module.is_whisper_model_cached(whisper_size)))
        try:
            segments = core_module.transcribe_for_timing(
                slice_path, whisper_size, language=source_language, use_gpu=use_gpu,
                initial_prompt=initial_prompt, beam_size=beam_size,
                min_silence_duration_ms=min_silence_ms, vad_threshold=vad_threshold,
                on_gpu_fallback=lambda exc: gpu_fallback.append(core_module.short_reason(exc)),
                fast_mode=fast_mode)
        except core_module.ModelDownloadError as exc:
            background_jobs.set_result(job_id, {"line_id": line_id, "failed_reason": "model_download",
                                                "detail": redact_secrets(str(exc))})
            return
    finally:
        if os.path.exists(slice_path):
            os.remove(slice_path)
        core_module.release_gpu_models()

    new_zh = " ".join((s.get("text") or "").strip() for s in segments or []).strip()
    if not new_zh:
        background_jobs.set_result(job_id, {"line_id": line_id, "failed_reason": "empty"})
        return
    if background_jobs.is_cancel_requested(job_id):
        background_jobs.set_result(job_id, {"line_id": line_id, "failed_reason": "cancelled"})
        return
    if _find_line(drama_id, line_id) is None:
        background_jobs.set_result(job_id, {
            "line_id": line_id, "failed_reason": "line_gone",
            "detail": "The line was merged, split or deleted meanwhile; nothing was changed."})
        return
    result = {"line_id": line_id, "proposed_zh": new_zh[:_RETRANSCRIBE_MAX_CHARS],
              "base_zh": zh_before or "", "base_start": start, "base_end": end}
    if gpu_fallback:
        result["gpu_fallback"] = gpu_fallback[0]
    background_jobs.set_result(job_id, result)


def _finished_proposal(drama_id: int, line_id: int) -> dict:
    """This drama's finished re-transcription result for this line, as held
    in this process. NotFoundError for an unknown drama or line, or when there
    is none (not run, still running, failed, another line's, or the API
    restarted since)."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if _find_line(drama_id, line_id) is None:
        raise NotFoundError(f"No line with id {line_id} in drama {drama_id}.")
    job = background_jobs.get_status(retranscribe_line_job_id(drama_id)) or {}
    result = job.get("result") if job.get("status") == "done" else None
    if (not isinstance(result, dict) or result.get("line_id") != line_id
            or not result.get("proposed_zh")):
        raise NotFoundError("No finished re-transcription for this line.")
    return result


def get_retranscribe_result(drama_id: int, line_id: int) -> dict:
    """{job_id, line_id, status: "done", proposed_zh, base_zh}: the finished
    proposal for this line, raw (the same line text lines.read already
    returns). NotFoundError as _finished_proposal."""
    result = _finished_proposal(drama_id, line_id)
    return {"job_id": retranscribe_line_job_id(drama_id), "line_id": line_id, "status": "done",
            "proposed_zh": result["proposed_zh"], "base_zh": result.get("base_zh") or ""}


def apply_retranscribe_line(drama_id: int, line_id: int, job_id, expected_zh,
                            expected_proposed) -> dict:
    """Writes a finished re-transcription's
    proposed_zh to that line's `zh` and nothing else. expected_zh and
    expected_proposed must equal the raw base_zh and proposed_zh held for this
    run (what get_retranscribe_result showed), so what was shown is exactly
    what is written and an apply is tied to the run the user saw. The write
    is ONE compare-and-set (db.update_line_fields_if) on the line's zh, start
    and end as they were when the job started, so a line edited or re-timed
    since then is left alone (ConflictError). The old text is kept first as a
    line-history snapshot (restorable from History). Returns {drama_id,
    line_id, zh}.

    InvalidInputError for non-text input or a job_id that isn't this drama's
    re-transcription; NotFoundError as _finished_proposal; ConflictError when
    the expected values don't match this run or the line changed since the
    job started."""
    if (not isinstance(job_id, str) or not isinstance(expected_zh, str)
            or not isinstance(expected_proposed, str)):
        raise InvalidInputError("job_id, expected_zh and expected_proposed must be text.")
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if job_id != retranscribe_line_job_id(drama_id):
        raise InvalidInputError("job_id is not this drama's re-transcription.")
    result = _finished_proposal(drama_id, line_id)
    base = result.get("base_zh") or ""
    proposed = result["proposed_zh"]
    if expected_zh != base or expected_proposed != proposed:
        raise ConflictError("This isn't the re-transcription you were shown. Run it again.")
    line = _find_line(drama_id, line_id)
    unchanged = {"zh": base, "start": result["base_start"], "end": result["base_end"]}
    if line is None or (line.zh or "", float(line.start), float(line.end)) != (
            base, unchanged["start"], unchanged["end"]):
        raise ConflictError("This line changed since it was re-transcribed; your edit was kept.")
    db.save_line_history_snapshot(drama_id, db.load_line_objects(drama_id),
                                  f"before re-transcribing line {line.idx + 1}")
    if not db.update_line_fields_if(drama_id, line_id, {"zh": proposed}, unchanged):
        raise ConflictError("This line changed since it was re-transcribed; your edit was kept.")
    return {"drama_id": drama_id, "line_id": line_id, "zh": proposed}
