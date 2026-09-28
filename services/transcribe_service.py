"""
services/transcribe_service.py -- the Transcript-stage action for one
drama: the real trigger/apply logic that (despite the tab's name) lives
inside `tabs/workspace_tab.py`'s `with tab_translate:` block, not
`with tab_transcript:` (which holds only settings widgets). Shared by the
FastAPI /api/transcribe routes (a later integration step, not this file)
and that Streamlit block's own "Transcribe & Align" / "Transcribe with
Whisper" button (`run_prep`, `workspace_tab.py:3005`, handled at
`3154-3234` and applied at `3304-3497`).

Migration Slice 20 (Phase 6's third Workspace stage), built on Slice 19's
Source-stage config. A migration-architect scoping pass on this stage
found a real problem this slice had to solve, not just port: today's
"apply the finished job's result to the drama's lines" step runs as a
side effect of Streamlit's own render loop the next time it reruns while
the job shows "done" -- there's no clean way to expose that over a
stateless API. The user's resolved decision (2026-09-28): the background
job itself does the whole pipeline (ASR, alignment, the DB write, and the
optional diarization chain-start) and reports a single "done" outcome a
client can poll for via the existing GET /api/jobs/{id} -- no separate
"apply" call, and no risk of "job succeeded but nothing was saved" if a
client never follows up.

Deliberately out of scope for this slice (each a real, separately
buildable follow-up, not an oversight):
  - hardsub_ocr transcript_mode -- reading captions burned into video.
    Raises UnsupportedOperationError for now; the OCR pipeline itself
    (run_hardsub_ocr_job) is untouched and still works from Streamlit.
  - The `chunk_and_tag` novel_narration path -- fully synchronous today
    (no background job at all), a real LLM call over the whole chunked
    text with no natural job boundary; needs its own scope/benchmark
    pass before deciding whether a synchronous API call is a good fit.
  - asr_backend_choice == "qwen3_asr" and alignment_method ==
    "qwen3_forced_align" -- both experimental, optional-dependency,
    unverified-on-real-content paths in the current Streamlit code;
    left for a follow-up once the plain Whisper-text / character-diff
    path here is confirmed working end-to-end.
  - Audio/video upload (per Slice 19 -- unchanged: this slice still
    requires audio already on disk, i.e. source_service's
    audio_available == True).
  - The "🪄 Auto-tune" feature (a separate Source-tab action, not chained
    off this one -- confirmed by the Slice 20 scoping pass).

No Streamlit or FastAPI import: plain functions, plain dicts in, plain
values out. The one exception to "plain dicts" is start_transcribe_run,
which starts a real background thread (background_jobs.start_job) --
same shape as diarization_service.start_diarization_run.
"""
import os
from typing import Optional

import background_jobs
import core as core_module
import db
import raw_transcript
from core import Line, align_transcript_to_timing, split_user_transcript, transcribe_for_timing
from services import settings_service, source_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError

# Matches the Streamlit widgets' own hardcoded defaults exactly (see
# tabs/workspace_tab.py: beam_size slider ~2184, min_silence_ms slider
# ~2189 default 300, vad_threshold slider ~2203 default 0.5,
# separate_vocals_first/realign_long_segments/whisper_fast_mode/use_groq
# checkboxes default False, separation_backend selectbox default "auto").
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


def _drama_audio_path(drama_id: int, drama: dict) -> Optional[str]:
    """Mirrors source_service._audio_available's own file-exists check."""
    audio_filename = drama.get("audio_filename")
    if not audio_filename:
        return None
    path = os.path.join(db.drama_dir(drama_id), audio_filename)
    return path if os.path.exists(path) else None


def get_transcribe_config(drama_id: int) -> dict:
    """Read-only Transcript-stage summary for one drama: which action the
    "Transcribe & Align" button would run (from Slice 19's transcript_mode),
    whether its Whisper model is already downloaded, and every tuning knob
    with its current per-drama value (falling back to the same defaults the
    Streamlit widgets use). Raises NotFoundError for an unknown drama id."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    source = source_service.get_source_config(drama_id)
    whisper_size = drama.get("whisper_size") or _DEFAULT_TUNING["whisper_size"]

    return {
        "drama_id": drama_id,
        "transcript_mode": source["transcript_mode"],
        "has_audio_pipeline": source["has_audio_pipeline"],
        "audio_available": source["audio_available"],
        "alignment_method": drama.get("alignment_method") or "whisper_diff",
        "asr_backend_choice": drama.get("asr_backend_choice") or "whisper",
        "whisper_size": whisper_size,
        "whisper_model_cached": core_module.is_whisper_model_cached(whisper_size),
        "beam_size": drama.get("beam_size") or _DEFAULT_TUNING["beam_size"],
        "min_silence_ms": drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"],
        "vad_threshold": drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"],
        "separate_vocals_first": bool(drama.get("separate_vocals_first")),
        "separation_backend": drama.get("separation_backend") or _DEFAULT_TUNING["separation_backend"],
        "realign_long_segments": bool(drama.get("realign_long_segments")),
        "whisper_fast_mode": bool(drama.get("whisper_fast_mode")),
        "use_groq": bool(drama.get("use_groq")),
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
        updates["whisper_size"] = fields["whisper_size"]
    if "alignment_method" in fields and fields["alignment_method"] is not None:
        if fields["alignment_method"] not in ("whisper_diff", "qwen3_forced_align"):
            raise InvalidInputError(f"Unknown alignment_method {fields['alignment_method']!r}.")
        updates["alignment_method"] = fields["alignment_method"]
    if "asr_backend_choice" in fields and fields["asr_backend_choice"] is not None:
        if fields["asr_backend_choice"] not in ("whisper", "qwen3_asr"):
            raise InvalidInputError(f"Unknown asr_backend_choice {fields['asr_backend_choice']!r}.")
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
    for bf in _BOOL_FIELDS:
        if bf in fields and fields[bf] is not None:
            updates[bf] = 1 if fields[bf] else 0

    if updates:
        db.update_drama(drama_id, **updates)

    return get_transcribe_config(drama_id)


def start_transcribe_run(drama_id: int, source_language: str = "zh", chinese_script: str = "simplified",
                          transcript_text: Optional[str] = None, run_diarize: bool = False,
                          expected_speakers: Optional[int] = None) -> dict:
    """Starts the background job that transcribes (or aligns a supplied
    transcript against) this drama's stored audio, then -- once that's
    done, inside the same job -- applies the result to the drama's lines
    and optionally chain-starts a diarization run. Poll status via the
    existing GET /api/jobs/{job_id}; once "done", the DB write has already
    happened (see this module's own docstring for why, vs. the Streamlit
    tab's render-loop-apply approach).

    transcript_text is required (and only used) when this drama's
    transcript_mode is "have_transcript" -- per Slice 19, it's
    deliberately never persisted, matching today's one-click behavior of
    accepting it fresh each run.

    Raises NotFoundError for an unknown drama id; UnsupportedOperationError
    if there's no audio available, or transcript_mode is "hardsub_ocr"
    (out of scope for this slice -- see module docstring) or
    "have_transcript" with no transcript_text supplied; ConflictError if a
    transcription is already running for this drama."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")

    transcript_mode = drama.get("transcript_mode") or "have_transcript"
    if transcript_mode == "hardsub_ocr":
        raise UnsupportedOperationError(
            "hardsub_ocr transcription isn't available via this API yet -- use the Streamlit "
            "Transcript tab, or switch this drama's transcript_mode first.")
    if transcript_mode == "have_transcript" and not (transcript_text or "").strip():
        raise UnsupportedOperationError(
            "transcript_mode is 'have_transcript' but no transcript_text was supplied.")

    hf_token = settings_service.resolve_key("hf_token") if run_diarize else None
    groq_api_key = settings_service.resolve_key("groq") if drama.get("use_groq") else None

    job_id = f"transcribe_{drama_id}"
    started = background_jobs.start_job(
        job_id, _run_transcribe_and_apply_job, job_id, drama_id, audio_path, transcript_mode,
        transcript_text, source_language, chinese_script,
        drama.get("whisper_size") or _DEFAULT_TUNING["whisper_size"],
        drama.get("beam_size") or _DEFAULT_TUNING["beam_size"],
        drama.get("min_silence_ms") or _DEFAULT_TUNING["min_silence_ms"],
        drama.get("vad_threshold") or _DEFAULT_TUNING["vad_threshold"],
        bool(drama.get("separate_vocals_first")),
        drama.get("separation_backend") or _DEFAULT_TUNING["separation_backend"],
        bool(drama.get("realign_long_segments")), bool(drama.get("whisper_fast_mode")),
        bool(drama.get("use_groq")), groq_api_key, hf_token, expected_speakers,
        gpu_touching=True, description=f"Transcription (drama #{drama_id})")
    if not started:
        raise ConflictError(f"A transcription is already running for drama {drama_id}.")
    return {"job_id": job_id}


def _run_transcribe_and_apply_job(job_id, drama_id, audio_path, transcript_mode, transcript_text,
                                   source_language, chinese_script, whisper_size, beam_size,
                                   min_silence_ms, vad_threshold, separate_vocals_first,
                                   separation_backend, realign_long_segments, whisper_fast_mode,
                                   use_groq, groq_api_key, hf_token, expected_speakers):
    """The background job body itself: runs ASR, applies the result to the
    drama's lines, and optionally chain-starts diarization -- all before
    reporting "done", so a client polling GET /api/jobs/{job_id} never
    observes a state where the job succeeded but nothing was saved.

    Reuses transcribe_for_timing exactly as
    services.workspace_job_service.run_transcribe_job does (see that
    function's own docstring for the reasoning behind each parameter);
    this is a separate job body rather than a call to run_transcribe_job
    because that function stops after producing segments and leaves the
    apply step for Streamlit's own render loop -- see this module's
    docstring for why this slice can't reuse that split.

    use_gpu is hardcoded False here, deliberately: Streamlit's own
    "use_gpu" toggle is a bare st.session_state value with no DB/settings
    persistence anywhere (unlike the tuning knobs Slice 20 does persist),
    so there's no server-side source of truth for an API caller yet --
    out of scope for this slice, left for whenever GPU control becomes a
    real settings_service concern."""
    if separate_vocals_first:
        import audio_preprocess
        background_jobs.update_progress(job_id, 0.0, "Separating vocals from background music...")
        vocals_path = os.path.join(os.path.dirname(audio_path), "vocals.wav")
        try:
            audio_path = audio_preprocess.separate_vocals(
                audio_path, vocals_path, backend=separation_backend,
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Removing background music... {frac * 100:.0f}%"),
                cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id))
        except audio_preprocess.VocalSeparationCancelled:
            background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
            return
        except audio_preprocess.VocalSeparationError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "vocal_separation", "detail": str(exc)})
            return

    if background_jobs.is_cancel_requested(job_id):
        background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
        return

    gpu_fallback_msg = []
    if use_groq:
        background_jobs.update_progress(job_id, 0.0, "Transcribing via Groq's cloud API...")
        try:
            segments = core_module.transcribe_with_groq(
                audio_path, source_language, groq_api_key,
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing via Groq's cloud API... {frac * 100:.0f}%"))
        except core_module.GroqTranscriptionError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "groq", "detail": str(exc)})
            return
    else:
        try:
            segments = transcribe_for_timing(
                audio_path, whisper_size, language=source_language, use_gpu=False,
                local_model_path=None, hf_token=None, initial_prompt="", beam_size=beam_size,
                min_silence_duration_ms=min_silence_ms, vad_threshold=vad_threshold,
                on_gpu_fallback=lambda exc: gpu_fallback_msg.append(str(exc)),
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing... {frac * 100:.0f}%"),
                fast_mode=whisper_fast_mode)
        except core_module.ModelDownloadError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "model_download", "detail": str(exc)})
            return

    if not segments:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return

    word_align_error = None
    if realign_long_segments and not background_jobs.is_cancel_requested(job_id):
        import word_align
        background_jobs.update_progress(job_id, 1.0, "Splitting long merged lines...")
        try:
            segments = word_align.realign_oversized_segments(
                segments, audio_path, source_language, chinese_script=chinese_script)
        except word_align.WordAlignError as exc:
            word_align_error = str(exc)

    if transcript_mode == "whisper":
        lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                 for i, seg in enumerate(segments) if seg["text"].strip()]
        raw_backend, raw_model, raw_mode = "whisper", whisper_size, "whisper"
    else:
        user_lines = split_user_transcript(transcript_text)
        lines = align_transcript_to_timing(user_lines, segments)
        raw_backend, raw_model, raw_mode = "whisper", whisper_size, "aligned_transcript"

    core_module.release_gpu_models()

    # Step 25 item 2's same safety rule, ported here: never let an empty
    # result silently wipe out an already-populated drama.
    existing_lines_before = db.load_line_objects(drama_id)
    if not lines and existing_lines_before:
        background_jobs.set_result(job_id, {
            "failed_reason": "empty_kept_existing",
            "existing_line_count": len(existing_lines_before),
        })
        return

    background_jobs.cancel_line_jobs(drama_id)
    if existing_lines_before:
        db.save_line_history_snapshot(drama_id, existing_lines_before, "before re-transcribe")
    db.save_lines(drama_id, lines)
    raw_transcript.write_raw_transcript(
        db.drama_dir(drama_id), segments, lines, backend=raw_backend, model=raw_model,
        language=source_language, mode=raw_mode)
    db.update_drama(drama_id, status="aligned")

    diarize_started = False
    if hf_token:
        import diarize as diarize_module
        diarize_started = background_jobs.start_process_job(
            f"diarize_{drama_id}", diarize_module.diarize_subprocess_worker,
            args=(audio_path, hf_token, expected_speakers or None),
            gpu_touching=True, description=f"Diarization (drama #{drama_id})")

    background_jobs.set_result(job_id, {
        "line_count": len(lines),
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "word_align_error": word_align_error,
        "diarize_started": diarize_started,
    })
