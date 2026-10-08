"""
api/routers/live_routes.py -- Live capture sessions (status also
pushed over GET /api/events, api/routers/events_routes.py).
Thin: see services/live_service.py.

Start fetches a public URL through yt-dlp from this PC, so it needs
`media.import_url`, and a paid translation engine (anything outside
`translate_engines.FREE_ENGINES`) also needs `engines.paid`. An omitted
engine runs the free default (Ollama), so it is gated as that, but an Ollama `-cloud` tag
also needs `engines.paid`. Reading a session is `library.read`; stopping one is
`jobs.cancel`. Sessions live in this process only (404 after a restart).
"""

from typing import List

from fastapi import APIRouter, Path, Query, Request

from api.auth import (is_local_request, require_cloud_model_allowed, require_engines_allowed,
                      require_permission)
from api.schemas import (ErrorResponse, LiveOllamaCheck, LiveSessionStart, LiveSessionStarted,
                         LiveSessionStatus, LiveSessionStopped, LiveSessionSummary)
from services import live_service

router = APIRouter(prefix="/api/live", tags=["live"])

_SID = Path(pattern=r"^live_[0-9a-f]{32}$")
_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/sessions", dependencies=[require_permission("media.import_url")],
             response_model=LiveSessionStarted,
             summary="Start a live capture session (yt-dlp + local Whisper + engine)",
             responses=_ERRS)
def post_session(body: LiveSessionStart, request: Request):
    require_engines_allowed(request, body.engine or live_service.LIVE_DEFAULT_ENGINE)
    require_cloud_model_allowed(request, (body.engine, body.model))
    return live_service.start_session(
        body.url, source_language=body.source_language, whisper_size=body.whisper_size,
        segment_seconds=body.segment_seconds, overlap_seconds=body.overlap_seconds,
        engine=body.engine, model=body.model, max_minutes=body.max_minutes,
        use_gpu=body.use_gpu, use_saved_cookies=is_local_request(request),
        reply_without_thinking=body.reply_without_thinking)


@router.get("/sessions", dependencies=[require_permission("library.read")],
            response_model=List[LiveSessionSummary],
            summary="Live sessions started in this process")
def list_sessions(request: Request):
    return live_service.list_sessions(principal=request.state.principal)


@router.get("/ollama-check", dependencies=[require_permission("library.read")],
            response_model=LiveOllamaCheck,
            summary="Whether Ollama answers and has the model (no address in the reply)",
            responses=_ERRS)
def get_ollama_check(model: str = Query(None, max_length=100)):
    return live_service.check_ollama(model)


@router.get("/sessions/{session_id}", dependencies=[require_permission("library.read")],
            response_model=LiveSessionStatus,
            summary="Session status and cues[after:] (poll with after=next_index)",
            responses=_ERRS)
def get_session(request: Request, session_id: str = _SID,
                after: int = Query(0, ge=0, le=10**9)):
    return live_service.get_session(session_id, after, principal=request.state.principal)


@router.post("/sessions/{session_id}/stop", dependencies=[require_permission("jobs.cancel")],
             response_model=LiveSessionStopped,
             summary="Stop a session (idempotent on a finished one)", responses=_ERRS)
def post_stop(request: Request, session_id: str = _SID):
    return live_service.stop_session(session_id, principal=request.state.principal)
