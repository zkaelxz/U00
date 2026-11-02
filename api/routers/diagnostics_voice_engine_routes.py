"""
api/routers/diagnostics_voice_engine_routes.py -- voice engines that install into a venv
of their own (thin; see services/voice_engine_install_service.py). The status read is
`admin.diagnostics`; the install is `local_only()` + confirm=true and runs as a
background job, as is removing it (its own folder only). The engine is a fixed key; its
packages and folder are the server's own.

Progress is also visible through GET /api/jobs/{job_id}; cancel through
POST /api/jobs/{job_id}/cancel.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.schemas import error_responses
from api.schemas.voice_engines import (VoiceEngineInstallRequest, VoiceEngineInstallStarted,
                                       VoiceEngineInstallStatus, VoiceEngineRemoveRequest,
                                       VoiceEngineRemoveStarted)
from services import voice_engine_install_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/voice-engines", dependencies=[require_permission("admin.diagnostics")],
            response_model=VoiceEngineInstallStatus,
            summary="Voice engines in a folder of their own: what is installed, the install jobs")
def get_voice_engines():
    return svc.get_status()


@router.post("/voice-engines/{engine}/install", dependencies=[local_only()],
             response_model=VoiceEngineInstallStarted,
             summary="PC only: install a voice engine into its own folder as a background job "
                     "(confirm=true)",
             responses=error_responses(409, 422))
def post_voice_engine_install(engine: str, body: VoiceEngineInstallRequest):
    return svc.start_install(engine, confirm=body.confirm)


@router.post("/voice-engines/{engine}/remove", dependencies=[local_only()],
             response_model=VoiceEngineRemoveStarted,
             summary="PC only: delete a voice engine's own folder as a background job "
                     "(confirm=true)",
             responses=error_responses(409, 422))
def post_voice_engine_remove(engine: str, body: VoiceEngineRemoveRequest):
    return svc.start_remove(engine, confirm=body.confirm)
