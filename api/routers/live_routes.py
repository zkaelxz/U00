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
from fastapi.responses import FileResponse

from api.auth import (is_at_pc, is_local_request, local_only, require_cloud_model_allowed,
                      require_engines_allowed, require_permission)
from api.schemas import (LiveOllamaCheck, LiveSessionStart, LiveSessionStarted,
                         LiveSessionStatus, LiveSessionStopped, LiveSessionSummary, error_responses)
from lib.errors import ForbiddenError
from services import live_service, model_readiness_service

router = APIRouter(prefix="/api/live", tags=["live"])

_SID = Path(pattern=r"^live_[0-9a-f]{32}$")
_ERRS = error_responses(404, 422, 503)


@router.post("/sessions", dependencies=[require_permission("media.import_url")],
             response_model=LiveSessionStarted,
             summary="Start a live capture session (yt-dlp + local Whisper + engine)",
             responses=_ERRS)
def post_session(body: LiveSessionStart, request: Request):
    require_engines_allowed(request, body.engine or live_service.LIVE_DEFAULT_ENGINE)
    require_cloud_model_allowed(request, (body.engine, body.model))
    # The recorder downloads video for the whole session and its files are served to the PC only.
    if body.delayed_player and not is_at_pc(request):
        raise ForbiddenError("The delayed player can only be started at the PC.")
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_whisper_size, body.whisper_size)
    return live_service.start_session(
        body.url, source_language=body.source_language, whisper_size=body.whisper_size,
        segment_seconds=body.segment_seconds, overlap_seconds=body.overlap_seconds,
        engine=body.engine, model=body.model, max_minutes=body.max_minutes,
        use_gpu=body.use_gpu, use_saved_cookies=is_local_request(request),
        reply_without_thinking=body.reply_without_thinking, delayed_player=body.delayed_player,
        glossary_drama_id=body.glossary_drama_id, series_id=body.series_id,
        principal=request.state.principal)


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
            summary="Session status and cues[after:] (poll from the oldest still-pending cue id)",
            responses=_ERRS)
def get_session(request: Request, session_id: str = _SID,
                after: int = Query(0, ge=0, le=10**9)):
    return live_service.get_session(session_id, after, principal=request.state.principal)


@router.post("/sessions/{session_id}/stop", dependencies=[require_permission("jobs.cancel")],
             response_model=LiveSessionStopped,
             summary="Stop a session (idempotent on a finished one)", responses=_ERRS)
def post_stop(request: Request, session_id: str = _SID):
    return live_service.stop_session(session_id, principal=request.state.principal)


_PLAYER_FILE = Path(pattern=r"^(?:(?:master|video|audio)\.m3u8|[va]_\d{6}\.ts)$")


@router.get("/sessions/{session_id}/player/{name}", dependencies=[local_only()],
            summary="One file of the session's delayed player (a playlist or a segment)",
            responses=_ERRS)
def get_player_file(request: Request, session_id: str = _SID, name: str = _PLAYER_FILE):
    path = live_service.player_file(session_id, name, principal=request.state.principal)
    kind = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    # No store: the segments are deleted as the window moves on.
    return FileResponse(path, media_type=kind, headers={"Cache-Control": "no-store"})
