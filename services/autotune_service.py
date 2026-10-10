"""
services/autotune_service.py -- the "Auto-tune" speech-splitting sensitivity
action for one drama, for the /api/transcribe/dramas/{id}/autotune routes
(api/routers/transcribe_routes.py).

It is a separate action, not chained off the transcribe run:
start_autotune_run re-transcribes the drama's audio once per candidate
min_silence_ms in ONE process job (so Cancel terminates it mid-decode) and
scores each with score_autotune_segments. Nothing is applied by the job
itself; apply_autotune_candidate writes only the drama's own min_silence_ms
column (db.update_drama, one field), and only for a candidate the finished job
actually measured. No paid engine is used (PAID_ENGINE_FUNCTIONS is empty):
the run is local ASR.
"""
from typing import Optional

import background_jobs
import core as core_module
import db
import sensitivity_preset as presets
from core import Line
from services import asr_options_service, settings_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from services.transcribe_service import (_DEFAULT_TUNING, _drama_audio_path, _resolve_initial_prompt,
                                         get_transcribe_config, stored_whisper_size)
from translate_engines import redact_secrets

# Auto-tune functions that may spend on a paid engine: none (local ASR only).
PAID_ENGINE_FUNCTIONS = ()


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
                         beam_size, candidates, vad_threshold, fast_mode, preset, repeat_guard, result_queue):
    """Process-job target (top-level, picklable): transcribes once per
    candidate, holding every other setting constant, and returns only the
    scores (no segments, no token)."""
    background_jobs.start_own_process_group()
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
                fast_mode=fast_mode, repeat_guard=repeat_guard,
                sensitivity_preset=preset)
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
            or any(isinstance(c, bool) or not isinstance(c, int)
                   or not core_module.MIN_SILENCE_MS_MIN <= c <= core_module.MIN_SILENCE_MS_MAX
                   for c in candidates)
            or len(set(candidates)) != len(candidates)):
        raise InvalidInputError(
            f"candidates must be 1-6 distinct whole numbers between {core_module.MIN_SILENCE_MS_MIN} "
            f"and {core_module.MIN_SILENCE_MS_MAX} (ms).")
    if (drama.get("split_by_sentences")
            and asr_options_service.stored_asr_backend(drama) in ("whisper", "qwen3_asr")):
        # The run then splits on a fixed silence, so a tuned min_silence would not apply.
        raise UnsupportedOperationError(
            "Auto-tune is unavailable while 'Split lines by sentences' is on: that mode "
            "ignores the minimum silence setting.")
    initial_prompt = _resolve_initial_prompt(drama_id, initial_prompt, extra_names)
    job_id = autotune_job_id(drama_id)
    started = background_jobs.start_process_job(
        job_id, _autotune_all_worker,
        args=(audio_path, stored_whisper_size(drama),
              drama.get("source_language") or "zh", settings_service.get_use_gpu(),
              settings_service.resolve_key("hf_token") or None, initial_prompt,
              drama.get("beam_size") or _DEFAULT_TUNING["beam_size"], list(candidates),
              presets.stored_vad_threshold(drama),
              bool(drama.get("whisper_fast_mode")),
              presets.normalize(drama.get("sensitivity_preset")),
              bool(drama.get("whisper_repeat_guard"))),
        gpu_touching=True, description=f"Auto-tuning (drama #{drama_id})",
        kill_whole_tree=True, start_method="spawn")
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
