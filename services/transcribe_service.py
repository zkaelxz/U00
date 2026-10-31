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

This also supports the hardsub_ocr transcript_mode --
reading captions burned into video, via hardsub_ocr.extract_hardsub_
subtitles (the OCR cues already carry real per-cue timing, so unlike
Whisper's own text there's no separate alignment step).

The run also honours the two experimental Qwen3 choices:
asr_backend_choice == "qwen3_asr" (whisper transcript_mode) re-transcribes Whisper's VAD segments
with asr_backend.Qwen3ASRBackend, replacing only the text; alignment_method
== "qwen3_forced_align" (have_transcript mode) aligns the supplied
transcript with forced_align.align_with_qwen3. Built with mocks only -- the
real-model check is still owed by the user. Forced alignment needs a known
transcript, so requesting it in Whisper-text-only mode is an
InvalidInputError; a missing or old transformers/torch is a
DependencyUnavailableError, both raised at start (not inside the job).

The `chunk_and_tag` novel_narration path is services/narration_service.py.
A run needs its media already on disk; upload is media_upload_service.

No FastAPI import: plain functions, plain dicts in, plain
values out. The one exception to "plain dicts" is start_transcribe_run,
which starts a real background job. Audio transcription runs in its own
process (job_launch.start_process_job, transcribe_pipeline._transcribe_worker) so Cancel
kills a model call that has no cancel point of its own; the parent applies
the result to the drama in the job's on_done hook, so the worker never
writes the database. Hardsub OCR, which already stops between frames,
stays a thread job (_run_transcribe_and_apply_job).
"""
import contextlib
import functools
import os
from types import SimpleNamespace
from typing import Optional

import background_jobs
from jobs import crash_retry, job_launch
import core as core_module
import whisper_models
import segment_splitting
import db
from lib.safe_paths import plain_file_in
from services.source_service import drama_audio_path as _drama_audio_path
import diagnostics
import diagnostics_torch
import memory_headroom
import raw_transcript
import sensitivity_preset as presets
import storage
from engine_backends import paid_calls
from asr_backend import audio_coverage_fraction, coverage_warning  # noqa: F401 (re-exported)
from ocr import default_hardsub_backend
from services import difficult_audio_service as difficult
from services import (asr_options_service, diarization_service, ownership_service, run_settings_service,
                      settings_service, source_service, timing_check_service, timing_settings_service,
                      transcribe_pipeline,
                      vocabulary_hint_service)
from services.transcribe_options_service import (
    _DEFAULT_TUNING, _check_run_request, apply_run_overrides,
    check_run_start, check_transcribe_fields, run_start,
    check_whisper_size_for_language, selectable_whisper_sizes, stored_hallucination_silence_sec,
    stored_min_pause_sec, stored_whisper_size, whisper_size_notice)
from services.transcribe_speed_service import (
    measured_diarize_runs, measured_diarize_speed, measured_stage_seconds,
    measured_transcribe_runs, measured_transcribe_speed, record_transcribe_speed)
from core import SOURCE_LANGUAGES  # noqa: F401  (tests/test_shared_constants.py checks it is the shared object)
from services.service_errors import (ConflictError, InvalidInputError,
                                     UnsupportedOperationError)


def _drama_video_path(drama_id: int, drama: dict) -> Optional[str]:
    """Same video-source check as source_service.get_source_config's
    has_video_source -- source_video_filename set,
    no existence check on disk (matching source_service, which also only
    checks presence of the filename for video, unlike audio)."""
    video_filename = drama.get("source_video_filename")
    if not video_filename:
        return None
    return plain_file_in(db.drama_dir(drama_id), video_filename)


def build_auto_initial_prompt(drama_id: int, extra_names: str = "") -> str:
    """Whisper's automatic initial_prompt for one drama: the series
    glossary's names
    (core.build_initial_prompt) and any extra_names the user typed, joined
    with "、" and ended with "。"; then, when the drama has a
    raw_novel_context.txt, a bounded novel excerpt merged in names-first and
    trimmed by core.combine_initial_prompt (its 900-character cap). Shared by
    the API transcribe and auto-tune paths so they can't drift. Returns "" when there's nothing to prime with. Raises
    NotFoundError for an unknown drama id."""
    drama = ownership_service.require_drama(drama_id)
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
    "Transcribe & Align" button would run (from the source config's transcript_mode),
    whether its Whisper model is already downloaded, and every tuning knob
    with its current per-drama value (falling back to _DEFAULT_TUNING).
    Raises NotFoundError for an unknown drama id."""
    drama = ownership_service.require_drama(drama_id)

    source = source_service.get_source_config(drama_id)
    whisper_size = stored_whisper_size(drama)

    return {
        "drama_id": drama_id,
        "transcript_mode": source["transcript_mode"],
        "has_audio_pipeline": source["has_audio_pipeline"],
        "audio_available": source["audio_available"],
        "alignment_method": drama.get("alignment_method") or "whisper_diff",
        "asr_backend_choice": asr_options_service.stored_asr_backend(drama),
        "asr_backend_notice": asr_options_service.removed_asr_backend_notice(drama),
        "whisper_size": whisper_size,
        "whisper_size_notice": whisper_size_notice(drama),
        "whisper_exclusions": whisper_models.whisper_language_exclusions(),
        "selectable_whisper_sizes": selectable_whisper_sizes("transcribe"),
        "whisper_model_cached": whisper_models.is_whisper_model_cached(whisper_size),
        "measured_speed": measured_transcribe_speed(whisper_size, settings_service.get("use_gpu")),
        "measured_speed_runs": measured_transcribe_runs(whisper_size, settings_service.get("use_gpu")),
        "measured_stage_seconds": measured_stage_seconds(whisper_size, settings_service.get("use_gpu")),
        "measured_diarize_speed": measured_diarize_speed(settings_service.get("use_gpu")),
        "measured_diarize_runs": measured_diarize_runs(settings_service.get("use_gpu")),
        "whisper_installed": diagnostics.check_dependency("faster_whisper"),
        # Loading ctranslate2 or cuBLAS here would lock their DLLs in the
        # server; the isolated GPU check measures it in a child.
        "gpu_runtime": diagnostics_torch.get_gpu_status_isolated()["gpu_runtime"],
        "beam_size": drama.get("beam_size") or _DEFAULT_TUNING["beam_size"],
        "min_silence_ms": drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"],
        "vad_threshold": drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"],
        "sensitivity_preset": presets.normalize(drama.get("sensitivity_preset")),
        "effective_vad_threshold": timing_settings_service.vad_threshold_for(
            presets.difficult_audio_view(drama)),
        "hallucination_silence_sec": stored_hallucination_silence_sec(drama),
        "min_pause_sec": stored_min_pause_sec(drama),
        "separate_vocals_first": bool(drama.get("separate_vocals_first")),
        "separation_backend": drama.get("separation_backend") or _DEFAULT_TUNING["separation_backend"],
        "realign_long_segments": bool(drama.get("realign_long_segments")),
        "whisper_fast_mode": bool(drama.get("whisper_fast_mode")),
        "whisper_repeat_guard": bool(drama.get("whisper_repeat_guard")),
        "split_by_sentences": bool(drama.get("split_by_sentences")),
        "difficult_audio": bool(drama.get("difficult_audio")),
        "vocabulary_hint": bool(drama.get("vocabulary_hint")),
        "use_groq": bool(drama.get("use_groq")),
        "has_video_source": source["has_video_source"],
        "hardsub_ocr_backend": drama.get("hardsub_ocr_backend")
                               or default_hardsub_backend(source["source_language"]),
        "hardsub_interval_sec": drama.get("hardsub_interval_sec") or 1.0,
        "auto_initial_prompt": build_auto_initial_prompt(drama_id),
        "source_language": source["source_language"],
        "chinese_script": source["chinese_script"],
        "run_start": run_start(drama),
    }


def update_transcribe_config(drama_id: int, **fields) -> dict:
    """Field-scoped partial update for the tuning knobs the config newly
    persists (whisper_size/alignment_method/asr_backend_choice already had
    their own DB columns and are updated the same way elsewhere -- accepted
    here too for a single write path). Only fields actually passed (not
    None) are validated and written. Raises NotFoundError for an unknown
    drama id, InvalidInputError for an out-of-range/unknown value. Returns
    the updated get_transcribe_config(drama_id)."""
    drama = ownership_service.require_drama(drama_id)
    # The language first: the model and backend checks below are against the language being saved.
    language = fields.get("source_language") or drama.get("source_language") or "zh"
    source_changes = {k: fields[k] for k in ("source_language", "chinese_script") if fields.get(k) is not None}
    updates = check_transcribe_fields(fields, language)
    if fields.get("run_start") is not None:
        updates["transcribe_start_json"] = check_run_start(fields["run_start"])
    if source_changes:
        source_service.update_source_config(drama_id, **source_changes)
    if updates:
        db.update_drama(drama_id, **updates)

    return get_transcribe_config(drama_id)


def start_transcribe_run(drama_id: int, source_language: Optional[str] = None,
                          chinese_script: Optional[str] = None,
                          transcript_text: Optional[str] = None, run_diarize: bool = False,
                          expected_speakers: Optional[int] = None,
                          initial_prompt: str = "", tesseract_cmd: Optional[str] = None,
                          extra_names: str = "", min_speakers: Optional[int] = None,
                          max_speakers: Optional[int] = None,
                          overrides: Optional[dict] = None) -> dict:
    """Starts the background job that transcribes (or aligns a supplied
    transcript against) this drama's stored audio, then -- once that's
    done, inside the same job -- applies the result to the drama's lines
    and optionally chain-starts a diarization run. Poll status via the
    existing GET /api/jobs/{job_id}; once "done", the DB write has already
    happened (see this module's own docstring for why).

    overrides (a queued item's own settings, transcribe_options_service.QUEUE_SETTING_KEYS)
    are laid over the title for this run only; nothing is saved to the title.

    source_language / chinese_script default to this drama's own stored
    values when omitted. initial_prompt is an optional
    full override of Whisper's prompt; when it's empty or omitted the run
    uses build_auto_initial_prompt (series glossary plus extra_names plus
    raw-novel excerpt).

    transcript_text is required (and only used) when this drama's
    transcript_mode is "have_transcript" -- it's
    deliberately never persisted, matching today's one-click behavior of
    accepting it fresh each run.

    hardsub_ocr's own settings (backend, sample interval) come from this
    drama's persisted values -- update them first via
    update_transcribe_config if a run needs different ones.
    tesseract_cmd is an optional, client-supplied path to the tesseract
    binary; omitted, the saved Settings Tesseract path applies
    (settings_service.get("tesseract_cmd")).

    Raises NotFoundError for an unknown drama id; UnsupportedOperationError
    if there's no audio available (non-hardsub_ocr modes) or no video
    source (hardsub_ocr), or transcript_mode is "have_transcript" with no
    transcript_text supplied, or the drama has no audio pipeline
    (novel_narration); InvalidInputError for an unknown language/script;
    DependencyUnavailableError if use_groq is on with no Groq key
    configured, or a chosen Qwen3 backend's package (torch, or transformers 5.15+) isn't
    installed; InvalidInputError if alignment_method is
    "qwen3_forced_align" while transcript_mode is "whisper" (forced
    alignment needs a known transcript); ConflictError if a transcription is already running for
    this drama."""
    drama = apply_run_overrides(ownership_service.require_drama(drama_id), overrides)
    if db.load_line_ids(drama_id):
        # The run replaces every line the title already has.
        ownership_service.require_acting_lines_edit()
    # Resolved independently of transcript_mode: a hardsub_ocr drama
    # commonly also has real audio on disk (the video upload extracts one
    # alongside saving the video), and diarization always needs actual audio
    # regardless of where the transcript text itself came from, so it
    # diarizes off this drama-level audio for every transcript_mode,
    # hardsub_ocr included.
    diarize_audio_path = _drama_audio_path(drama_id, drama)
    media = {}

    def require_media(transcript_mode):
        if transcript_mode == "hardsub_ocr":
            media["video"] = _drama_video_path(drama_id, drama)
            if media["video"] is None:
                raise UnsupportedOperationError(f"No video source available for title {drama_id}.")
        else:
            media["audio"] = _drama_audio_path(drama_id, drama)
            if media["audio"] is None:
                raise UnsupportedOperationError(f"No audio available for title {drama_id}.")

    (min_speakers, max_speakers, source_language, chinese_script, transcript_mode,
     asr_backend_choice, alignment_method) = _check_run_request(
        drama, source_language, chinese_script, transcript_text,
        expected_speakers, min_speakers, max_speakers, require_media)
    run = _resolve_run(
        drama_id, drama, source_language=source_language, chinese_script=chinese_script,
        transcript_mode=transcript_mode, transcript_text=transcript_text,
        asr_backend_choice=asr_backend_choice, alignment_method=alignment_method,
        audio_path=media.get("audio"), video_path=media.get("video"),
        diarize_audio_path=diarize_audio_path, run_diarize=run_diarize,
        expected_speakers=expected_speakers, min_speakers=min_speakers,
        max_speakers=max_speakers, initial_prompt=initial_prompt, extra_names=extra_names)
    job_id = f"transcribe_{drama_id}"
    if transcript_mode == "hardsub_ocr":
        started = _start_hardsub_job(job_id, drama_id, drama, run, tesseract_cmd)
    else:
        started = _start_worker_job(job_id, drama_id, drama, run)
    if not started:
        raise ConflictError(f"A transcription is already running for title {drama_id}.")
    return {"job_id": job_id}


def _resolve_run(drama_id, drama, *, source_language, chinese_script, transcript_mode,
                 transcript_text, asr_backend_choice, alignment_method, audio_path, video_path,
                 diarize_audio_path, run_diarize, expected_speakers, min_speakers, max_speakers,
                 initial_prompt, extra_names) -> SimpleNamespace:
    """Every value the run needs, read in the parent before the job starts:
    the worker process must not read the database."""
    voice_detector = asr_options_service.resolve_voice_detector(drama, source_language)

    hf_token = settings_service.resolve_key("hf_token") if run_diarize else None
    groq_api_key = settings_service.resolve_key("groq") if drama.get("use_groq") else None

    whisper_size = stored_whisper_size(drama)
    # The title's saved language passed above; this run may name another one.
    check_whisper_size_for_language(whisper_size, source_language)
    beam_size = drama.get("beam_size") or _DEFAULT_TUNING["beam_size"]
    min_silence_ms = drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"]
    # Difficult audio runs with the Sensitive preset (a threshold the title changed still wins),
    # but only when the app transcribes the audio itself: a supplied transcript or Groq
    # ignores the option, and the worker says so in the result.
    run_row = (presets.difficult_audio_view(drama)
               if transcript_mode == "whisper" and not drama.get("use_groq") else drama)
    vad_threshold = timing_settings_service.vad_threshold_for(run_row)
    preset = presets.normalize(run_row.get("sensitivity_preset"))
    hallucination_silence_sec = stored_hallucination_silence_sec(drama)
    min_pause_sec = stored_min_pause_sec(drama)
    separation_backend = drama.get("separation_backend") or _DEFAULT_TUNING["separation_backend"]
    prompt = _resolve_initial_prompt(drama_id, initial_prompt or "", extra_names or "")
    qwen_prompt = (vocabulary_hint_service.hint_for_run(drama)
                   if asr_backend_choice.startswith("qwen3") else None)
    use_gpu = settings_service.get("use_gpu")
    # Frozen with the other settings for the saved run settings.
    gpu_app_settings = raw_transcript.current_gpu_app_settings()
    timing = timing_settings_service.values()
    run_settings = run_settings_service.for_transcribe(
        drama, source_language, chinese_script, whisper_size, beam_size, min_silence_ms,
        vad_threshold, preset, hallucination_silence_sec, min_pause_sec, separation_backend,
        use_gpu, asr_backend_choice, alignment_method, transcript_mode, bool(hf_token),
        min_speakers, max_speakers, timing=timing_settings_service.changed(timing))
    return SimpleNamespace(
        source_language=source_language, chinese_script=chinese_script,
        transcript_mode=transcript_mode, transcript_text=transcript_text,
        asr_backend_choice=asr_backend_choice, alignment_method=alignment_method,
        audio_path=audio_path, video_path=video_path, diarize_audio_path=diarize_audio_path,
        run_diarize=run_diarize, expected_speakers=expected_speakers,
        min_speakers=min_speakers, max_speakers=max_speakers, voice_detector=voice_detector,
        hf_token=hf_token, groq_api_key=groq_api_key, whisper_size=whisper_size,
        beam_size=beam_size, min_silence_ms=min_silence_ms, vad_threshold=vad_threshold,
        preset=preset, hallucination_silence_sec=hallucination_silence_sec,
        min_pause_sec=min_pause_sec, separation_backend=separation_backend, prompt=prompt,
        qwen_prompt=qwen_prompt, use_gpu=use_gpu, gpu_app_settings=gpu_app_settings,
        timing=timing, run_settings=run_settings,
        description=f"Transcription (title #{drama_id})")


def _start_hardsub_job(job_id, drama_id, drama, run, tesseract_cmd) -> bool:
    return job_launch.start_job(
        job_id, _run_transcribe_and_apply_job, job_id, drama_id, run.audio_path,
        run.transcript_mode, run.transcript_text, run.source_language, run.chinese_script,
        run.whisper_size, run.beam_size, run.min_silence_ms, run.vad_threshold,
        bool(drama.get("separate_vocals_first")), run.separation_backend,
        bool(drama.get("realign_long_segments")), bool(drama.get("whisper_fast_mode")),
        bool(drama.get("use_groq")), run.groq_api_key, run.hf_token, run.expected_speakers,
        run.prompt, run.video_path,
        drama.get("hardsub_ocr_backend") or default_hardsub_backend(run.source_language),
        drama.get("hardsub_interval_sec") or 1.0,
        tesseract_cmd or settings_service.get("tesseract_cmd") or None, run.diarize_audio_path,
        run.use_gpu, run.asr_backend_choice, run.alignment_method,
        min_speakers=run.min_speakers, max_speakers=run.max_speakers,
        hallucination_silence_sec=run.hallucination_silence_sec,
        min_pause_sec=run.min_pause_sec, gpu_app_settings=run.gpu_app_settings,
        gpu_touching=True, description=run.description, run_settings=run.run_settings,
        diarize_requested=run.run_diarize, line_fields=db.FULL_SYNC)


def _worker_args(drama, run, scratch_dir, use_gpu) -> tuple:
    return (run.audio_path, run.transcript_mode, run.transcript_text, run.source_language,
            run.chinese_script, run.whisper_size, run.beam_size, run.min_silence_ms,
            run.vad_threshold, bool(drama.get("separate_vocals_first")),
            run.separation_backend, bool(drama.get("realign_long_segments")),
            bool(drama.get("whisper_fast_mode")), bool(drama.get("use_groq")), run.prompt,
            use_gpu, run.asr_backend_choice, run.alignment_method,
            settings_service.get("whisper_model_path") or None,
            asr_options_service.get_qwen_asr_batch_size(),
            asr_options_service.get_vad_refine_timing(),
            asr_options_service.get_mixed_languages(),
            run.hallucination_silence_sec, run.min_pause_sec,
            bool(drama.get("whisper_repeat_guard")),
            bool(drama.get("split_by_sentences")), run.preset, run.voice_detector,
            run.qwen_prompt, difficult.mark(run.timing, drama), scratch_dir)


def _cpu_retry_args(drama, run, scratch_dir):
    # Not with Groq: the crashed run's paid calls never reach groq_usage, so a
    # CPU rerun would bill the user twice and log it once.
    if not run.use_gpu or drama.get("use_groq"):
        return None
    return _worker_args(drama, run, scratch_dir, False)


def _start_worker_job(job_id, drama_id, drama, run) -> bool:
    # The worker's temp files go here; removed by on_finish however the
    # run ends, since a killed worker cannot clean up after itself.
    scratch_dir = storage.new_workdir(job_id)
    # Held until the job is registered: before that nothing owns the folder.
    hold = contextlib.ExitStack()
    hold.enter_context(storage.holding(scratch_dir))
    try:
        args = _worker_args(drama, run, scratch_dir, run.use_gpu)
        started = job_launch.start_process_job(
            job_id, transcribe_pipeline._transcribe_worker, args=args,
            cpu_retry_args=_cpu_retry_args(drama, run, scratch_dir),
            gpu_touching=True, description=run.description, kill_whole_tree=True,
            run_settings=run.run_settings, line_fields=db.FULL_SYNC,
            # Spawn, not Linux's default fork: a forked child of a process
            # that has already initialised CUDA cannot use the GPU.
            start_method="spawn",
            on_done=functools.partial(
                _apply_on_done, drama_id=drama_id, source_language=run.source_language,
                whisper_size=run.whisper_size, use_gpu=run.use_gpu,
                transcript_mode=run.transcript_mode, alignment_method=run.alignment_method,
                hf_token=run.hf_token, diarize_audio_path=run.diarize_audio_path,
                expected_speakers=run.expected_speakers, min_speakers=run.min_speakers,
                max_speakers=run.max_speakers, gpu_app_settings=run.gpu_app_settings,
                diarize_requested=run.run_diarize),
            on_finish=functools.partial(transcribe_pipeline._remove_scratch_dir, scratch_dir,
                                        part_dir=os.path.dirname(run.audio_path)))
    except BaseException:
        transcribe_pipeline._remove_scratch_dir(scratch_dir)
        raise
    finally:
        hold.close()
    if not started:
        transcribe_pipeline._remove_scratch_dir(scratch_dir)
    return started


def _run_transcribe_and_apply_job(job_id, drama_id, audio_path, transcript_mode, transcript_text,
                                   source_language, chinese_script, whisper_size, beam_size,
                                   min_silence_ms, vad_threshold, separate_vocals_first,
                                   separation_backend, realign_long_segments, whisper_fast_mode,
                                   use_groq, groq_api_key, hf_token, expected_speakers,
                                   initial_prompt="", video_path=None, hardsub_ocr_backend=None,
                                   hardsub_interval=1.0, tesseract_cmd=None,
                                   diarize_audio_path=None, use_gpu=False,
                                   asr_backend_choice="whisper", alignment_method="whisper_diff",
                                   min_speakers=None, max_speakers=None,
                                   hallucination_silence_sec=core_module.DEFAULT_HALLUCINATION_SILENCE_SEC,
                                   min_pause_sec=segment_splitting.MIN_WORD_GAP_SECONDS,
                                   gpu_app_settings=None, diarize_requested=False):
    """The thread-job body (start_transcribe_run uses it for hardsub_ocr):
    runs the pipeline in this thread (_transcribe_pipeline), applies the
    result to the drama's lines and optionally chain-starts diarization
    (_apply_transcription) -- all before reporting "done", so a client
    polling GET /api/jobs/{job_id} never observes a state where the job
    succeeded but nothing was saved.

    min_speakers/max_speakers: a speaker-count range for the
    chained speaker detection, already checked by start_transcribe_run.

    use_gpu is the persisted server-side toggle (db.app_settings, read via
    settings_service.get("use_gpu") in start_transcribe_run, default on; CPU when no usable GPU).

    diarize_audio_path is resolved once in start_transcribe_run, from the
    drama's own stored audio_filename, independent of transcript_mode --
    for a hardsub_ocr drama there is no transcribe-time audio_path at all
    (see module docstring), but a real one commonly still exists on disk
    (the video-upload flow extracts it alongside the video), and
    diarization always needs actual audio regardless of where the
    transcript text came from. If it's not available, diarization is
    skipped (diarize_started stays False), same as the existing
    no-hf_token case."""
    try:
        outcome = transcribe_pipeline._transcribe_pipeline(
            transcribe_pipeline._ThreadReporter(job_id), audio_path, transcript_mode, transcript_text, source_language,
            chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
            separate_vocals_first, separation_backend, realign_long_segments, whisper_fast_mode,
            use_groq, groq_api_key, initial_prompt, use_gpu, asr_backend_choice, alignment_method,
            local_model_path=settings_service.get("whisper_model_path") or None,
            qwen_batch_size=asr_options_service.get_qwen_asr_batch_size(),
            video_path=video_path, hardsub_ocr_backend=hardsub_ocr_backend,
            hardsub_interval=hardsub_interval, tesseract_cmd=tesseract_cmd,
            hallucination_silence_sec=hallucination_silence_sec, min_pause_sec=min_pause_sec)
    except memory_headroom.HeadroomError as exc:
        outcome = {"failed_reason": "headroom", "detail": str(exc)}
    except ImportError as exc:
        outcome = {**transcribe_pipeline.missing_package_outcome(
            transcribe_pipeline.MISSING_OCR_MESSAGE if transcript_mode == "hardsub_ocr"
            else transcribe_pipeline.MISSING_TRANSCRIPTION_MESSAGE),
            "import_error": whisper_models.short_reason(exc)}
    background_jobs.set_result(job_id, _apply_transcription(
        job_id, drama_id, outcome, source_language=source_language, whisper_size=whisper_size,
        use_gpu=use_gpu, transcript_mode=transcript_mode, alignment_method=alignment_method,
        hf_token=hf_token, diarize_audio_path=diarize_audio_path,
        expected_speakers=expected_speakers, min_speakers=min_speakers,
        max_speakers=max_speakers, gpu_app_settings=gpu_app_settings,
        diarize_requested=diarize_requested))


def _apply_on_done(job_id, outcome, **apply_kwargs):
    """on_done for the transcribe process job: its return value is the job's
    result (job_launch.start_process_job)."""
    return _apply_transcription(job_id, apply_kwargs.pop("drama_id"), outcome, **apply_kwargs)


def _apply_transcription(job_id, drama_id, outcome, *, source_language, whisper_size, use_gpu,
                         transcript_mode, alignment_method, hf_token, diarize_audio_path,
                         expected_speakers, min_speakers=None, max_speakers=None,
                         gpu_app_settings=None, diarize_requested=False) -> dict:
    """Applies a _transcribe_pipeline outcome to the drama and returns the
    job's result: replaces its lines (history snapshot first), writes the
    raw transcript, marks it "aligned", chain-starts
    diarization when hf_token and diarize_audio_path are set, and records
    the run's speed. A failed outcome is returned unchanged and writes
    nothing; so does a run whose cancel arrived before the write.
    diarize_requested is the "Detect speakers after transcribing" switch: when it
    is on and no detection starts, the result says why (diarize_skipped_reason)."""
    # Logged here, not in the pipeline: a spawned worker has no redirected library, so its
    # logger would write to the real one.
    import applog
    if outcome.get("stage_summary"):
        applog.get_logger().info(f"transcription of title {drama_id}: {outcome['stage_summary']}")
    crash = outcome.pop(crash_retry.REASON_KEY, None)
    import_error = outcome.pop("import_error", None)
    if import_error:
        applog.get_logger().error(f"transcription of title {drama_id} could not import a "
                                  f"package: {import_error}")
    _record_groq_usage(drama_id, outcome.pop("groq_usage", None))
    if "failed_reason" in outcome:
        if crash:
            # The CPU run failed too: the result still says the GPU crashed first.
            outcome["gpu_fallback"] = crash
        if outcome["failed_reason"] == "no_lines" and diarize_requested:
            return {**outcome, "diarize_skipped_reason": "the transcription produced no lines"}
        return outcome
    lines, segments = outcome["lines"], outcome["segments"]
    gpu_fallback_msg = ([crash] if crash else []) + list(outcome["gpu_fallback_msgs"])
    forced_align_error = outcome["forced_align_error"]

    # Every stage can end with a cancel that arrived mid-call: never
    # replace the drama's lines after one.
    if background_jobs.is_cancel_requested(job_id):
        return {"failed_reason": "cancelled"}

    # Never let an empty
    # result silently wipe out an already-populated drama.
    existing_lines_before = db.load_line_objects(drama_id)
    if not lines and existing_lines_before:
        return {"failed_reason": "empty_kept_existing",
                "existing_line_count": len(existing_lines_before)}
    if not lines:
        return {"failed_reason": "no_lines", "stage_summary": outcome.get("stage_summary", ""),
                **({"diarize_skipped_reason": "the transcription produced no lines"}
                   if diarize_requested else {})}

    background_jobs.cancel_line_jobs(drama_id)
    if existing_lines_before:
        db.save_line_history_snapshot(drama_id, existing_lines_before, "before re-transcribe")
    # Full sync on purpose: these brand-new lines replace the drama's lines.
    # Line jobs were cancelled and the old lines snapshotted just above.
    db.save_lines(drama_id, lines)
    raw_transcript.write_raw_transcript(
        db.drama_dir(drama_id), segments, lines, backend=outcome["raw_backend"],
        model=outcome["raw_model"], language=source_language, mode=outcome["raw_mode"],
        settings=raw_transcript.build_run_settings(
            **outcome["run_config"], expected_speakers=expected_speakers,
            min_speakers=min_speakers, max_speakers=max_speakers,
            gpu_fallback_msgs=gpu_fallback_msg, stage_seconds=outcome["stage_seconds"],
            **(gpu_app_settings or raw_transcript.current_gpu_app_settings()))
        if outcome.get("run_config") else None)
    db.update_drama(drama_id, status="aligned")
    timing_check_service.after_run(drama_id, outcome)

    diarize_started, diarize_skipped_reason = _chain_diarization(
        job_id, drama_id, hf_token, diarize_audio_path, expected_speakers, min_speakers,
        max_speakers, diarize_requested)
    _record_speed(outcome, whisper_size, use_gpu and not crash)

    return {
        "line_count": len(lines),
        "stage_seconds": outcome["stage_seconds"],
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "device_notice": (
            whisper_models.gpu_fallback_notice(outcome.get("gpu_fallback_task", "Transcription"),
                                            gpu_fallback_msg[0])
            if gpu_fallback_msg else None),
        "device": (f"GPU unavailable ({gpu_fallback_msg[0]}); using CPU"
                   if gpu_fallback_msg else outcome["device_msg"]) or None,
        "word_align_error": outcome["word_align_error"],
        "asr_backend": outcome["raw_backend"],
        "asr_backend_notice": asr_options_service.removed_asr_backend_notice(
            db.get_drama(drama_id) or {}),
        "alignment_method": ("qwen3_forced_align" if transcript_mode == "have_transcript"
                             and alignment_method == "qwen3_forced_align"
                             and not forced_align_error else "whisper_diff"),
        "forced_align_error": forced_align_error,
        "coverage_warning": outcome["coverage_warning"],
        "ollama_notice": outcome.get("ollama_notice"),
        "diarize_started": diarize_started,
        "diarize_skipped_reason": diarize_skipped_reason,
    }


def _record_groq_usage(drama_id, groq_usage) -> None:
    if not groq_usage:
        return
    try:
        # An audio length that could not be read is logged under its own name,
        # not as a free request.
        unknown = not groq_usage["failed"] and not groq_usage["seconds"]
        paid_calls.record_audio_call(
            "groq", core_module.GROQ_DEFAULT_MODEL,
            "transcribe_groq_length_unknown" if unknown else "transcribe_groq",
            **groq_usage, drama_id=drama_id)
    except Exception as exc:
        # A lost usage row must not fail a transcription that was paid for.
        import applog
        applog.get_logger().warning(
            f"Groq usage was not logged: {whisper_models.short_reason(exc)}")


def _chain_diarization(job_id, drama_id, hf_token, diarize_audio_path, expected_speakers,
                       min_speakers, max_speakers, diarize_requested) -> tuple:
    """(started, skipped_reason) for the speaker detection that follows a
    saved transcription."""
    if diarize_requested and not hf_token:
        return False, "no Hugging Face token"
    if diarize_requested and not diarize_audio_path:
        return False, "no audio file for this title"
    if diarize_requested and not diagnostics.check_dependency("pyannote.audio"):
        return False, "pyannote.audio is not installed"
    if not (hf_token and diarize_audio_path):
        return False, None
    import diarize as diarize_module
    try:
        args = (diarize_audio_path, hf_token, expected_speakers or None,
                diarization_service.worker_options(min_speakers, max_speakers))
        started = job_launch.start_process_job(
            f"diarize_{drama_id}", diarize_module.diarize_subprocess_worker, args=args,
            cpu_retry_args=diarization_service.cpu_retry_args(args), gpu_touching=True, description=f"Diarization (title #{drama_id})",
            kill_whole_tree=True, start_method="spawn",
            on_done=diarization_service.make_apply_on_done(
                drama_id, expected_speakers, min_speakers=min_speakers,
                max_speakers=max_speakers,
                speakers_at_start=diarization_service.speakers_snapshot(drama_id)),
            run_settings=run_settings_service.for_diarize(
                expected_speakers, min_speakers, max_speakers,
                settings_service.get("use_gpu")), line_fields=("speaker", "speaker_manual"))
    except ConflictError:
        # A clean stop cancels every job and refuses new ones, so one
        # that began after the lines were saved refuses the follow-up
        # job. The saved lines stay and the job ends cancelled, not
        # error; diarization can be run again from the Speakers step.
        # The stop sets the refusal before it cancels jobs, so a refusal
        # in that gap sees no cancel request yet.
        if not (background_jobs.is_cancel_requested(job_id)
                or job_launch.is_stopping()):
            raise
        raise background_jobs.JobCancelled(job_id) from None
    return started, None


def _record_speed(outcome, whisper_size, use_gpu) -> None:
    whisper_clock = outcome["whisper_clock"]
    if "work" not in whisper_clock or whisper_clock["p"] >= 0.5:
        return
    # Only the Whisper pass counts, and only the part after its first
    # percent: audio_seconds * (1 - that first percent) over the time taken.
    audio_seconds = transcribe_pipeline._audio_duration_seconds(outcome["audio_path"])
    if audio_seconds:
        record_transcribe_speed(
            whisper_size, bool(use_gpu) and not outcome["gpu_fallback_msgs"],
            audio_seconds * (1.0 - whisper_clock["p"]), whisper_clock["work"],
            stage_seconds=outcome["stage_seconds"])
