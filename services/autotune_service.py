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

A candidate is scored by how many lines are long/merged. A title with lines the
owner corrected (benchmark_reference_service) is scored by CER against those
lines instead, since few long lines says nothing about whether the words are right.
"""
import functools
from typing import Optional

import background_jobs
from jobs import crash_retry, job_launch, process_worker
import line_alignment
import whisper_models
import whisper_transcribe
import db
import sensitivity_preset as presets
from core import Line
from services import (
    asr_options_service,
    benchmark_reference_service,
    jobs_service,
    ownership_service,
    settings_service,
    timing_settings_service,
)
from lib.errors import (ConflictError, InvalidInputError, UnsupportedOperationError)
from services.transcribe_options_service import _DEFAULT_TUNING, require_audio_pipeline, stored_whisper_size
from services.transcribe_service import _drama_audio_path, _resolve_initial_prompt, get_transcribe_config
from engine_backends.shared import redact_secrets

# Fewer corrected lines than this is too small a sample to rank candidates by:
# one odd line could pick the winner, so the long-line count is used instead.
MIN_REFERENCE_LINES = 5

# Auto-tune functions that may spend on a paid engine: none (local ASR only).
PAID_ENGINE_FUNCTIONS = ()


def autotune_job_id(drama_id: int) -> str:
    return f"autotune_{drama_id}"


def score_autotune_segments(candidate_ms, segments, references=None) -> dict:
    """One candidate's score: the number of long/merged lines
    (core.diagnose_line_coverage) and total non-empty lines, plus its CER
    against `references` (benchmark_reference_service.reference_lines) when
    the title has any."""
    cand_lines = [Line(idx=i, start=s["start"], end=s["end"], zh=s["text"])
                  for i, s in enumerate(segments or []) if (s.get("text") or "").strip()]
    coverage = line_alignment.diagnose_line_coverage(cand_lines)
    score = {"candidate_ms": candidate_ms, "long_lines": len(coverage["long_lines"]),
             "total_lines": len(cand_lines)}
    if references:
        score["cer"] = benchmark_reference_service.score_against_references(segments, references)["cer"]
    return score


def best_candidate(results: list):
    """The winning candidate_ms: lowest CER when every candidate has one,
    else fewest long lines. Ties go to the earlier (shorter) candidate."""
    if not results:
        return None
    if all(r.get("cer") is not None for r in results):
        return min(results, key=lambda r: r["cer"])["candidate_ms"]
    return min(results, key=lambda r: r["long_lines"])["candidate_ms"]


def _autotune_all_worker(audio_path, model_size, language, use_gpu, hf_token, initial_prompt,
                         beam_size, candidates, vad_threshold, fast_mode, preset, repeat_guard, result_queue,
                         references=None, corrected_lines=0):
    """Process-job target (top-level, picklable): transcribes once per
    candidate, holding every other setting constant, and returns only the
    scores (no segments, no token)."""
    process_worker.start_own_process_group()
    try:
        results = []
        for n, candidate_ms in enumerate(candidates):
            process_worker.report_progress(
                result_queue, n / len(candidates),
                f"Testing candidate {n + 1} of {len(candidates)} ({candidate_ms}ms)...")
            segments = whisper_transcribe.transcribe_for_timing(
                audio_path, model_size, language=language, use_gpu=use_gpu,
                hf_token=hf_token, initial_prompt=initial_prompt, beam_size=beam_size,
                min_silence_duration_ms=candidate_ms, vad_threshold=vad_threshold,
                fast_mode=fast_mode, repeat_guard=repeat_guard,
                sensitivity_preset=preset)
            results.append(score_autotune_segments(candidate_ms, segments, references))
        result_queue.put(("ok", {"results": results, "best_candidate_ms": best_candidate(results),
                                 "scored_by": "cer" if results and all(r.get("cer") is not None for r in results) else "long_lines",
                                 "reference_lines": corrected_lines,
                                 "reference_minimum": MIN_REFERENCE_LINES}))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))


def _note_cpu_rerun(job_id, result):
    """on_done: its return replaces the result, so a run that went on on the
    CPU after a GPU crash says so in the Jobs list and the Auto-tune panel."""
    result = crash_retry.as_gpu_fallback(result)
    if isinstance(result, dict) and result.get("gpu_fallback"):
        result["device_notice"] = whisper_models.gpu_fallback_notice("Auto-tune",
                                                                     result["gpu_fallback"])
    return result


def start_autotune_run(drama_id: int, candidates: Optional[list] = None,
                       initial_prompt: str = "", extra_names: str = "") -> dict:
    """Starts the auto-tune process job for this drama's stored audio, using
    the drama's own persisted whisper_size/beam_size/vad_threshold/
    whisper_fast_mode and language.
    candidates defaults to core.DEFAULT_AUTOTUNE_CANDIDATES_MS; each must be
    an int in 300..3000 (the slider's range), at most 6, no duplicates.
    Poll get_autotune_status(drama_id) (GET /api/transcribe/dramas/{id}/
    autotune); its "done" result is
    {"results": [{candidate_ms, long_lines, total_lines, cer}], "best_candidate_ms",
    "scored_by" ("cer" when the title has corrected lines, else "long_lines"), "reference_lines"}.

    NotFoundError, UnsupportedOperationError (no audio pipeline / no audio),
    InvalidInputError (bad candidates), ConflictError (already running)."""
    drama = ownership_service.require_drama(drama_id)
    require_audio_pipeline(drama_id, drama)
    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for title {drama_id}.")
    if candidates is None:
        candidates = list(whisper_transcribe.DEFAULT_AUTOTUNE_CANDIDATES_MS)
    if (not isinstance(candidates, (list, tuple)) or not candidates or len(candidates) > 6
            or any(isinstance(c, bool) or not isinstance(c, int)
                   or not whisper_transcribe.MIN_SILENCE_MS_MIN <= c <= whisper_transcribe.MIN_SILENCE_MS_MAX
                   for c in candidates)
            or len(set(candidates)) != len(candidates)):
        raise InvalidInputError(
            f"candidates must be 1-6 distinct whole numbers between {whisper_transcribe.MIN_SILENCE_MS_MIN} "
            f"and {whisper_transcribe.MIN_SILENCE_MS_MAX} (ms).")
    if (drama.get("split_by_sentences")
            and asr_options_service.stored_asr_backend(drama) in ("whisper", "qwen3_asr")):
        # The run then splits on a fixed silence, so a tuned min_silence would not apply.
        raise UnsupportedOperationError(
            "Auto-tune is unavailable while 'Split lines by sentences' is on: that mode "
            "ignores the minimum silence setting.")
    initial_prompt = _resolve_initial_prompt(drama_id, initial_prompt, extra_names)
    corrected = benchmark_reference_service.reference_lines(drama_id, drama)["lines"]
    references = corrected if len(corrected) >= MIN_REFERENCE_LINES else []
    job_id = autotune_job_id(drama_id)
    use_gpu = settings_service.get("use_gpu")
    args = (audio_path, stored_whisper_size(drama),
            drama.get("source_language") or "zh", use_gpu,
            settings_service.resolve_key("hf_token") or None, initial_prompt,
            drama.get("beam_size") or _DEFAULT_TUNING["beam_size"], list(candidates),
            timing_settings_service.vad_threshold_for(drama),
            bool(drama.get("whisper_fast_mode")),
            presets.normalize(drama.get("sensitivity_preset")),
            bool(drama.get("whisper_repeat_guard")))
    started = job_launch.start_process_job(
        # A keyword, because the launcher appends the result queue as the last positional argument.
        job_id, functools.partial(_autotune_all_worker, references=references,
                                          corrected_lines=len(corrected)),
        args=args,
        # One CPU run after a native GPU crash (jobs/crash_retry.py): local Whisper, nothing billed.
        cpu_retry_args=(args[:3] + (False,) + args[4:]) if use_gpu else None,
        gpu_touching=True, description=f"Auto-tuning (title #{drama_id})",
        on_done=_note_cpu_rerun,
        kill_whole_tree=True, start_method="spawn", line_fields=())
    if not started:
        raise ConflictError(f"Auto-tune is already running for title {drama_id}.")
    return {"job_id": job_id, "candidates": list(candidates)}


def get_autotune_status(drama_id: int) -> dict:
    """{job_id, status, progress, message, result} for this drama's auto-tune
    job; result is {"results": [...], "best_candidate_ms"} only when done
    (else None). The message (or a failed job's error) is redacted.
    NotFoundError when the drama doesn't exist. No auto-tune job resident in
    this process (results live only in background_jobs memory) is the normal
    first answer: status "idle", job_id ""."""
    ownership_service.require_drama(drama_id)
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
                         "total_lines": r.get("total_lines"), "cer": r.get("cer")}
                        for r in raw.get("results") or [] if isinstance(r, dict)],
            "best_candidate_ms": raw.get("best_candidate_ms"),
            "scored_by": raw.get("scored_by") or "long_lines",
            "reference_lines": raw.get("reference_lines") or 0,
            "reference_minimum": raw.get("reference_minimum") or MIN_REFERENCE_LINES,
            "device_notice": raw.get("device_notice"),
        }
    message = job.get("error") if status == "error" else job.get("message")
    return {"job_id": job_id, "status": status, "progress": job.get("progress"),
            "message": jobs_service.scrub_text(str(message)) if message else "", "result": result}


def apply_autotune_candidate(drama_id: int, candidate_ms: int) -> dict:
    """"Use Nms": stores the chosen value as this drama's
    own min_silence_ms (a single-column db.update_drama write -- nothing
    else on the drama or its lines is touched). Only a candidate measured
    by this drama's finished auto-tune job is accepted, so a stale or
    foreign value can't be applied through this path (the plain config
    update, update_transcribe_config, remains for hand-set values).
    Returns the updated transcribe config."""
    ownership_service.require_drama(drama_id)
    job = background_jobs.get_status(autotune_job_id(drama_id))
    if not job or job.get("status") != "done":
        raise UnsupportedOperationError("No finished auto-tune results for this title.")
    measured = {r.get("candidate_ms") for r in (job.get("result") or {}).get("results") or []}
    if isinstance(candidate_ms, bool) or candidate_ms not in measured:
        raise InvalidInputError("candidate_ms must be one of the measured candidates.")
    db.update_drama(drama_id, min_silence_ms=candidate_ms)
    return get_transcribe_config(drama_id)
