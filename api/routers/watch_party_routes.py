"""
api/routers/watch_party_routes.py -- watch parties: one account's phones playing the same title in step.

A room is the creating account's own (services/watch_party_service.py), so every route needs only `lines.read`, the
permission to see the title's lines and play it. `GET /rooms/{code}/stream` is server-sent events: `state` (a
WatchPartyRoom) when the room opens and whenever anyone plays, pauses, seeks or joins; `ended` once the room is gone;
`ping` every 15 s so a proxy keeps it open. The session is re-checked every few seconds, like the main event stream.
"""

import asyncio
import json

from fastapi import APIRouter, Path, Request
from fastapi.responses import StreamingResponse

from api import auth as api_auth
from api.auth import require_permission
from api.routers.events_routes import Auth, SSE_HEADERS, revalidator
from api.schemas import (ErrorResponse, WatchPartyCreateRequest, WatchPartyEnded, WatchPartyRoom,
                         WatchPartyStateRequest, error_responses)
from services import event_stream_service as events
from services import watch_party_service

router = APIRouter(prefix="/api/watch-party", tags=["watch-party"])

PERMISSION = "lines.read"
_ERRS = error_responses(404, 422)
Code = Path(min_length=8, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")

POLL_SECONDS = 0.4
HEARTBEAT_SECONDS = 15.0
MAX_STREAM_SECONDS = 30 * 60


def _frame(name: str, data) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.post("/rooms", dependencies=[require_permission(PERMISSION)], response_model=WatchPartyRoom,
             summary="Start a watch party on a title the caller can see", responses=error_responses(404, 422, 429))
def create_room(body: WatchPartyCreateRequest, request: Request):
    return watch_party_service.create_room(request.state.principal, body.drama_id)


@router.get("/rooms/{code}", dependencies=[require_permission(PERMISSION)], response_model=WatchPartyRoom,
            summary="A watch party's current play state", responses=_ERRS)
def get_room(request: Request, code: str = Code):
    return watch_party_service.get_room(request.state.principal, code)


@router.post("/rooms/{code}/state", dependencies=[require_permission(PERMISSION)], response_model=WatchPartyRoom,
             summary="Play, pause or seek for everyone in the party", responses=_ERRS)
def post_state(body: WatchPartyStateRequest, request: Request, code: str = Code):
    return watch_party_service.set_state(request.state.principal, code, body.playing, body.position)


@router.delete("/rooms/{code}", dependencies=[require_permission(PERMISSION)], response_model=WatchPartyEnded,
               summary="End a watch party", responses=_ERRS)
def delete_room(request: Request, code: str = Code):
    watch_party_service.end_room(request.state.principal, code)
    return {"ended": True}


async def _stream(request, principal, code, stream, first, revalidate):
    loop = asyncio.get_running_loop()
    try:
        yield "retry: 3000\n\n"
        yield _frame("state", WatchPartyRoom(**first).model_dump(mode="json"))
        sent_seq = first["seq"]
        started = last_write = loop.time()
        auth = Auth(principal, revalidate, started)
        while loop.time() - started < MAX_STREAM_SECONDS:
            if await request.is_disconnected():
                return
            await asyncio.sleep(POLL_SECONDS)
            if await auth.current(loop.time()) is None:
                return
            seq = watch_party_service.current_seq(principal, code)
            if seq is None:
                yield _frame("ended", {})
                return
            if seq != sent_seq:
                room = watch_party_service.get_room(principal, code)
                sent_seq = room["seq"]
                yield _frame("state", WatchPartyRoom(**room).model_dump(mode="json"))
                last_write = loop.time()
            elif loop.time() - last_write >= HEARTBEAT_SECONDS:
                yield _frame("ping", {})
                last_write = loop.time()
    finally:
        watch_party_service.close_stream(stream)


@router.get("/rooms/{code}/stream", dependencies=[require_permission(PERMISSION)],
            summary="Server-sent events: the party's play state", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}}, 404: {"model": ErrorResponse},
                       429: {"model": ErrorResponse}})
async def stream_room(request: Request, code: str = Code):
    principal = request.state.principal
    stream, first = watch_party_service.open_stream(principal, code)
    return StreamingResponse(_stream(request, principal, code, stream, first, revalidator(request, principal)),
                             media_type="text/event-stream", headers=SSE_HEADERS)
