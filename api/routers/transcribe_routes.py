"""
api/routers/transcribe_routes.py -- Transcript-stage endpoints for one
drama (Phase 6's third Workspace stage, Migration Slices 20-21).

One config read/write and one job-starting action -- see
services/transcribe_service.py's own docstring for the job-does-everything
scope decision and the deliberately-out-of-scope list (audio upload;
hardsub_ocr was added in Slice 21). Job status/cancel for the transcribe
run is not duplicated here: poll it through the existing
GET /api/jobs/{job_id} (Migration Slice 8).

Route batch 2C adds auto-tune (start, status with the candidate scores,
apply a measured candidate), whose results are only readable here.

Parity audit B1 (R23) adds re-transcribing one line: a GPU-queued job that
proposes new source text (poll GET /api/jobs/{job_id}), and an apply route
that writes it only if the line is unchanged since the job started.
"""

from typing import Optional

from fastapi import APIRouter, Path, Request
from api.auth import require_paid_engines, require_permission
from api.schemas import (AutotuneApplyRequest, AutotuneRunRequest, AutotuneRunResult,
                         AutotuneStatus, ErrorResponse, RetranscribeApplyRequest,
                         RetranscribeApplyResult, RetranscribeLineRequest,
                         RetranscribeLineResult, TranscribeConfig, TranscribeConfigUpdate,
                         TranscribeRunRequest, TranscribeRunResult)
from services import transcribe_service

router = APIRouter(prefix="/api/transcribe", tags=["transcribe"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=TranscribeConfig,
            summary="Read-only Transcript-stage config for one drama",
            responses={404: {"model": ErrorResponse}})
def get_transcribe_config(drama_id: int = Path(ge=1)):
    return transcribe_service.get_transcribe_config(drama_id)


@router.post("/dramas/{drama_id}/config", dependencies=[require_permission("lines.edit")], response_model=TranscribeConfig,
            summary="Update Transcript-stage tuning knobs for one drama (partial update)",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_transcribe_config(payload: TranscribeConfigUpdate, request: Request,
                           drama_id: int = Path(ge=1)):
    if payload.use_groq:
        require_paid_engines(request)   # Groq cloud on the owner's key
    return transcribe_service.update_transcribe_config(drama_id, **payload.model_dump())


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=TranscribeRunResult,
            summary="Start the background transcribe-and-apply job for one drama",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                      409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                      503: {"model": ErrorResponse}})
def post_start_transcribe(payload: TranscribeRunRequest, request: Request,
                          drama_id: int = Path(ge=1)):
    if transcribe_service.get_transcribe_config(drama_id).get("use_groq"):
        require_paid_engines(request)   # the stored config sends audio to Groq cloud
    return transcribe_service.start_transcribe_run(
        drama_id, source_language=payload.source_language, chinese_script=payload.chinese_script,
        transcript_text=payload.transcript_text, run_diarize=payload.run_diarize,
        expected_speakers=payload.expected_speakers,
        initial_prompt=payload.initial_prompt, tesseract_cmd=payload.tesseract_cmd,
        extra_names=payload.extra_names)


# --- Route batch 2C: auto-tune speech-splitting sensitivity -----------------
# Local ASR only (transcribe_service.PAID_ENGINE_FUNCTIONS is empty), so no
# engine gate. Results live in this process's job memory: the GET reads them
# back and the apply only accepts a value that run measured.

@router.post("/dramas/{drama_id}/autotune", dependencies=[require_permission("jobs.start")], response_model=AutotuneRunResult,
             summary="Start auto-tuning speech-splitting sensitivity (min_silence_ms) for one drama",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_start_autotune(payload: AutotuneRunRequest, drama_id: int = Path(ge=1)):
    return transcribe_service.start_autotune_run(
        drama_id, candidates=payload.candidates, initial_prompt=payload.initial_prompt,
        extra_names=payload.extra_names)


@router.get("/dramas/{drama_id}/autotune", dependencies=[require_permission("library.read")], response_model=AutotuneStatus,
            summary="Status and (when done) per-candidate scores of this drama's auto-tune run",
            responses={404: {"model": ErrorResponse}})
def get_autotune(drama_id: int = Path(ge=1)):
    s = transcribe_service.get_autotune_status(drama_id)
    result = s.get("result") or {}
    return {"job_id": s["job_id"], "status": s["status"], "progress": s.get("progress"),
            "message": s.get("message") or "", "results": result.get("results"),
            "best_candidate_ms": result.get("best_candidate_ms")}


@router.post("/dramas/{drama_id}/autotune/apply", dependencies=[require_permission("lines.edit")], response_model=TranscribeConfig,
             summary="Store a measured auto-tune candidate as this drama's min_silence_ms",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_apply_autotune(payload: AutotuneApplyRequest, drama_id: int = Path(ge=1)):
    return transcribe_service.apply_autotune_candidate(drama_id, payload.candidate_ms)


# --- Parity audit B1 (R23): re-transcribe one line ----------------------------
# Local Whisper only (no Groq, no LLM), so no engine gate.

@router.post("/dramas/{drama_id}/lines/{line_id}/retranscribe",
             dependencies=[require_permission("jobs.start")], response_model=RetranscribeLineResult,
             summary="Re-transcribe one line's audio window and replace its source text (job)",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_retranscribe_line(payload: Optional[RetranscribeLineRequest] = None,
                           drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    payload = payload or RetranscribeLineRequest()
    return transcribe_service.start_retranscribe_line(
        drama_id, line_id, initial_prompt=payload.initial_prompt,
        extra_names=payload.extra_names)


@router.post("/dramas/{drama_id}/lines/{line_id}/retranscribe/apply",
             dependencies=[require_permission("lines.edit")], response_model=RetranscribeApplyResult,
             summary="Use a finished re-transcription's text for this line (compare-and-set)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_apply_retranscribe_line(payload: RetranscribeApplyRequest, drama_id: int = Path(ge=1),
                                 line_id: int = Path(ge=1)):
    return transcribe_service.apply_retranscribe_line(
        drama_id, line_id, payload.job_id, payload.expected_zh)
