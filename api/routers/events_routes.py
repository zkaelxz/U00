"""
api/routers/events_routes.py -- server-sent events (SSE) push for the React
client: job progress/status, the header bell's notification list and Live
session status, so the pages stop polling (user decision 2026-09-30,
"SSE everywhere"; polling stays as the client's fallback).

`GET /api/events?topics=jobs,notifications,live` (`library.read`, the
permission of all three GET routes it replaces). Events:

- `ready`         {"topics": [...]} once the stream is open.
- `job`           a JobRecord, exactly as `GET /api/jobs/{id}` returns it.
- `job_gone`      {"job_id"} for a job this stream showed that is now gone
                  or hidden from the caller.
- `notifications` a NotificationList, as `GET /api/notifications`.
- `live`          a LiveSessionStatus with `cues: []`, as `GET
                  /api/live/sessions/{id}`; the client fetches new cues
                  itself when `next_index` moves.
- `resync`        {"topics": [...]}: too much changed at once; re-read.
- `: ping`        a comment every ~15 s so proxies (Caddy) keep it open.

Every payload is re-read per event through the GET route's own service
call with this stream's principal (services/event_stream_service.py), and
with auth on the session is re-checked before every batch and heartbeat,
so a sign-out or a revoked permission ends the stream. EventSource sends
the session cookie and no custom headers; GET needs no CSRF token. A cap
(per user and in total) answers 429, and the client falls back to polling.
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.auth import require_permission, session_token
from api.notification_schemas import NotificationList
from api.schemas import ErrorResponse, JobRecord, LiveSessionStatus
from services import auth_service
from services import event_stream_service as events
from services.service_errors import InvalidInputError, RateLimitedError

router = APIRouter(prefix="/api/events", tags=["events"])

PERMISSION = "library.read"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _frame(name: str, data) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _serialize(name: str, data: dict) -> dict:
    """Through the GET routes' response models, so an event never carries a
    field its GET route would not."""
    if name == "job":
        return JobRecord(**data).model_dump(mode="json")
    if name == "notifications":
        return NotificationList(**data).model_dump(mode="json")
    if name == "live":
        return LiveSessionStatus(**data).model_dump(mode="json")
    if name == "job_gone":
        return {"job_id": str(data["job_id"])}
    return {"topics": [t for t in data.get("topics", ()) if t in events.TOPICS]}


def _revalidator(request: Request, principal):
    """A callable returning the stream's current principal, or None once it
    may no longer read (session gone, permission revoked). Auth off (the
    local owner principal) never changes."""
    if principal.get("is_local_owner") and principal.get("user_id") is None:
        return lambda: principal
    token = session_token(request)

    def check():
        # Not counted as activity: an open (even hidden) tab must not keep an
        # idle session alive.
        fresh = auth_service.resolve_session(token, touch=False)
        if fresh is None or PERMISSION not in fresh.get("permissions", ()):
            return None
        return fresh
    return check


async def _stream(sub, revalidate):
    loop = asyncio.get_running_loop()
    try:
        yield "retry: 3000\n\n"
        yield _frame("ready", {"topics": list(sub.topics)})
        started = last_write = loop.time()
        next_sweep = started
        while loop.time() - started < events.MAX_STREAM_SECONDS:
            if loop.time() >= next_sweep:
                try:
                    await run_in_threadpool(events.sweep_jobs)
                except Exception:
                    pass  # a DB hiccup skips one sweep, never ends the stream
                next_sweep = loop.time() + events.JOB_SWEEP_SECONDS
            deadline = min(next_sweep, last_write + events.HEARTBEAT_SECONDS,
                           started + events.MAX_STREAM_SECONDS)
            got = await sub.wait(max(0.0, deadline - loop.time()))
            if got:
                batch = sub.drain()
                principal = await run_in_threadpool(revalidate)
                if principal is None:
                    return
                out = await run_in_threadpool(events.build_events, sub, batch, principal)
                for name, data in out:
                    yield _frame(name, _serialize(name, data))
                if out:
                    last_write = loop.time()
                await asyncio.sleep(events.MIN_BATCH_SECONDS)
            if loop.time() - last_write >= events.HEARTBEAT_SECONDS:
                if await run_in_threadpool(revalidate) is None:
                    return
                yield ": ping\n\n"
                last_write = loop.time()
    finally:
        events.close_subscription(sub)


@router.get("", dependencies=[require_permission(PERMISSION)],
            summary="Server-sent events: job, notification and Live updates",
            response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}},
                       422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
async def stream_events(request: Request,
                        topics: Optional[str] = Query(None, max_length=64)):
    try:
        wanted = events.parse_topics(topics)
    except ValueError as exc:
        raise InvalidInputError(str(exc))
    principal = request.state.principal
    try:
        sub = events.open_subscription(principal, wanted, loop=asyncio.get_running_loop())
    except events.TooManyStreams as exc:
        raise RateLimitedError(str(exc))
    return StreamingResponse(_stream(sub, _revalidator(request, principal)),
                             media_type="text/event-stream", headers=SSE_HEADERS)
