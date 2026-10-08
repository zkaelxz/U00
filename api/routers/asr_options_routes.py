"""
api/routers/asr_options_routes.py -- experimental transcription settings
(Step 103). Thin: see services/asr_options_service.py.

- `GET /api/settings/asr-options` (`admin.settings`): the Qwen3-ASR batch
  size and the other experiment toggles. No path, key or secret is involved.
- `POST /api/settings/asr-options` (`local_only()`): saves any option.
  Settings are PC-only, like `POST /api/settings`.
- `POST /api/settings/asr-options/voice-detector/download` (`local_only()`):
  starts the opt-in ASMR voice detector model download as a background job.
"""

from fastapi import APIRouter

from api.asr_options_schemas import AsrOptions, AsrOptionsUpdate, AsrVadDownloadStarted
from api.auth import local_only, require_permission
from api.schemas import ErrorResponse
from services import asr_options_service

router = APIRouter(prefix="/api/settings/asr-options", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=AsrOptions,
            summary="Experimental transcription settings (Qwen3-ASR batching, speech detection, mixed languages)")
def get_asr_options():
    return asr_options_service.get_asr_options()


@router.post("", dependencies=[local_only()], response_model=AsrOptions,
             summary="PC only: save the experimental transcription settings",
             responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def update_asr_options(body: AsrOptionsUpdate):
    return asr_options_service.set_asr_options(**body.model_dump(exclude_unset=True))


@router.post("/voice-detector/download", dependencies=[local_only()],
             response_model=AsrVadDownloadStarted,
             summary="PC only: download the ASMR voice detector model (about 119 MB)",
             responses={409: {"model": ErrorResponse}})
def download_voice_detector():
    return asr_options_service.start_asmr_vad_download()
