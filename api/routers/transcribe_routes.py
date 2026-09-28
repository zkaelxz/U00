"""
api/routers/transcribe_routes.py -- Transcript-stage endpoints for one
drama (Phase 6's third Workspace stage, Migration Slice 20).

One config read/write and one job-starting action -- see
services/transcribe_service.py's own docstring for the job-does-everything
scope decision and the deliberately-out-of-scope list (hardsub_ocr,
chunk_and_tag, the two experimental qwen3 backends, audio upload,
auto-tune). Job status/cancel is not duplicated here: poll the started job
through the existing GET /api/jobs/{job_id} (Migration Slice 8).
"""

from fastapi import APIRouter, Path

from api.schemas import (ErrorResponse, TranscribeConfig, TranscribeConfigUpdate,
                         TranscribeRunRequest, TranscribeRunResult)
from services import transcribe_service

router = APIRouter(prefix="/api/transcribe", tags=["transcribe"])


@router.get("/dramas/{drama_id}/config", response_model=TranscribeConfig,
            summary="Read-only Transcript-stage config for one drama",
            responses={404: {"model": ErrorResponse}})
def get_transcribe_config(drama_id: int = Path(ge=1)):
    return transcribe_service.get_transcribe_config(drama_id)


@router.post("/dramas/{drama_id}/config", response_model=TranscribeConfig,
            summary="Update Transcript-stage tuning knobs for one drama (partial update)",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_transcribe_config(payload: TranscribeConfigUpdate, drama_id: int = Path(ge=1)):
    return transcribe_service.update_transcribe_config(drama_id, **payload.model_dump())


@router.post("/dramas/{drama_id}/run", response_model=TranscribeRunResult,
            summary="Start the background transcribe-and-apply job for one drama",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                      409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                      503: {"model": ErrorResponse}})
def post_start_transcribe(payload: TranscribeRunRequest, drama_id: int = Path(ge=1)):
    return transcribe_service.start_transcribe_run(
        drama_id, source_language=payload.source_language, chinese_script=payload.chinese_script,
        transcript_text=payload.transcript_text, run_diarize=payload.run_diarize,
        expected_speakers=payload.expected_speakers,
        initial_prompt=payload.initial_prompt)
