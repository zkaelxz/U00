"""
api/routers/live_follow_routes.py -- the Live page's followed channels.
Thin: see services/live_follow_service.py (and live_follow_poller.py for
the checks).

Checks are manual (Check now, Check all) unless a channel's hourly check
is on. They fetch YouTube from this PC, so adding, changing, checking and
removing a follow need `media.import_url`, like a Live start; listing is
`library.read`. "Start Live" from a follow is a Live start and gated the
same way (`media.import_url`, plus `engines.paid` for a paid engine or an
Ollama cloud model). Turning on the automatic start is the PC owner's only,
checked in the service (403). A follow the caller can't see is a 404.
"""

from fastapi import APIRouter, Path, Request

from api.auth import (is_at_pc, is_local_request, require_cloud_model_allowed, require_engines_allowed,
                      require_permission)
from api.schemas import (ErrorResponse, LiveFollow, LiveFollowAdd, LiveFollowCheckAll,
                         LiveFollowCheckQueued, LiveFollowList, LiveFollowRemoved,
                         LiveFollowUpdate, LiveSessionStarted)
from services import live_follow_service, live_service, model_readiness_service

router = APIRouter(prefix="/api/live/follows", tags=["live"])

_ID = Path(ge=1, le=2**62)
_ERRS = {403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def _check_engine(request: Request, options) -> None:
    if options is not None:
        require_engines_allowed(request, options.engine or live_service.LIVE_DEFAULT_ENGINE,
                                model=options.model)


@router.get("", dependencies=[require_permission("library.read")], response_model=LiveFollowList,
            summary="Followed channels the caller can see (never a URL)")
def list_follows(request: Request):
    return live_follow_service.list_follows(principal=request.state.principal)


@router.post("", dependencies=[require_permission("media.import_url")], response_model=LiveFollow,
             summary="Follow a YouTube channel for live streams", responses=_ERRS)
def add_follow(body: LiveFollowAdd, request: Request):
    _check_engine(request, body.options)
    return live_follow_service.add_follow(
        body.channel, mode=body.mode, auto_check=body.auto_check,
        options=body.options.model_dump() if body.options else None,
        principal=request.state.principal)


# Declared before "/{follow_id}" so the literal path is matched first.
@router.post("/check-all", dependencies=[require_permission("media.import_url")],
             response_model=LiveFollowCheckAll,
             summary="Queue a check of every channel the caller may change, one after another",
             responses=_ERRS)
def check_all(request: Request):
    return live_follow_service.check_all(principal=request.state.principal)


@router.post("/{follow_id}", dependencies=[require_permission("media.import_url")],
             response_model=LiveFollow, summary="Change a follow's mode or Live options",
             responses=_ERRS)
def update_follow(body: LiveFollowUpdate, request: Request, follow_id: int = _ID):
    _check_engine(request, body.options)
    return live_follow_service.update_follow(
        follow_id, mode=body.mode, auto_check=body.auto_check,
        options=body.options.model_dump() if body.options else None,
        principal=request.state.principal)


@router.post("/{follow_id}/remove", dependencies=[require_permission("media.import_url")],
             response_model=LiveFollowRemoved, summary="Stop following a channel", responses=_ERRS)
def remove_follow(request: Request, follow_id: int = _ID):
    return live_follow_service.remove_follow(follow_id, principal=request.state.principal)


@router.post("/{follow_id}/check", dependencies=[require_permission("media.import_url")],
             response_model=LiveFollowCheckQueued,
             summary="Queue one check of this channel now", responses=_ERRS)
def check_now(request: Request, follow_id: int = _ID):
    return live_follow_service.check_now(follow_id, principal=request.state.principal)


@router.post("/{follow_id}/start", dependencies=[require_permission("media.import_url")],
             response_model=LiveSessionStarted,
             summary="Start Live on this channel's stream with the follow's options",
             responses=_ERRS)
def start_live(request: Request, follow_id: int = _ID):
    principal = request.state.principal
    engine, model = live_follow_service.follow_engine(follow_id, principal=principal)
    require_engines_allowed(request, engine or live_service.LIVE_DEFAULT_ENGINE, model=model)
    require_cloud_model_allowed(request, (engine, model))
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_live_follow, follow_id, principal)
    return live_follow_service.start_from_follow(
        follow_id, principal=principal, use_saved_cookies=is_local_request(request))
