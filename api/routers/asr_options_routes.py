"""
api/routers/asr_options_routes.py -- experimental transcription settings
(Steps 103 and 104). Thin: see services/asr_options_service.py.

- `GET /api/settings/asr-options` (`admin.settings`): the Qwen3-ASR batch
  size and whether the experimental MOSS-Transcribe-Diarize backend is on
  and installed. No path, key or secret is involved.
- `POST /api/settings/asr-options` (`local_only()`): saves either option.
  Settings are PC-only, like `POST /api/settings`.
"""

from fastapi import APIRouter

from api.asr_options_schemas import AsrOptions, AsrOptionsUpdate
from api.auth import local_only, require_permission
from api.schemas import ErrorResponse
from services import asr_options_service

router = APIRouter(prefix="/api/settings/asr-options", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=AsrOptions,
            summary="Experimental transcription settings (Qwen3-ASR batching, MOSS backend)")
def get_asr_options():
    return asr_options_service.get_asr_options()


@router.post("", dependencies=[local_only()], response_model=AsrOptions,
             summary="PC only: save the experimental transcription settings",
             responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def update_asr_options(body: AsrOptionsUpdate):
    return asr_options_service.set_asr_options(**body.model_dump(exclude_unset=True))
