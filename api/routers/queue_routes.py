"""
api/routers/queue_routes.py -- the user-managed job queue on the Jobs page.
Thin: see services/queue_service.py.

Every route needs `jobs.start`. The service adds the rest: an item's title
must be the caller's, a Translate item on a paid engine needs `engines.paid`
(as the Translate stage's run does), a member sees and changes only items
for their own titles, and only the PC owner may start, pause or schedule.
"""

from fastapi import APIRouter, Path, Request

from api.auth import holds_paid_engines, require_permission
from api.schemas import (ErrorResponse, QueueAddItem, QueueMove, QueuePolicy,
                         QueueScheduleSet, QueueState)
from services import queue_service

router = APIRouter(prefix="/api/queue", tags=["queue"])

_ERRORS = {403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
           409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_ITEM = Path(ge=1, lt=2**63)


@router.get("", dependencies=[require_permission("jobs.start")], response_model=QueueState,
            summary="The job queue: run state, schedule and the items the caller can see")
def get_queue(request: Request):
    return queue_service.get_queue(request.state.principal)


@router.post("/items", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Add a title's step to the end of the queue", responses=_ERRORS)
def add_item(body: QueueAddItem, request: Request):
    return queue_service.add_item(request.state.principal, body.drama_id, body.step,
                                  holds_paid_engines(request))


@router.post("/items/{item_id}/move", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Move an item up or down", responses=_ERRORS)
def move_item(body: QueueMove, request: Request, item_id: int = _ITEM):
    return queue_service.move_item(request.state.principal, item_id, body.direction)


@router.post("/items/{item_id}/remove", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Remove an item that is not running", responses=_ERRORS)
def remove_item(request: Request, item_id: int = _ITEM):
    return queue_service.remove_item(request.state.principal, item_id)


@router.post("/items/{item_id}/retry", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Put a failed, skipped or interrupted item back to waiting",
             responses=_ERRORS)
def retry_item(request: Request, item_id: int = _ITEM):
    return queue_service.retry_item(request.state.principal, item_id)


@router.post("/clear-finished", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Remove the caller's finished items", responses=_ERRORS)
def clear_finished(request: Request):
    return queue_service.clear_finished(request.state.principal)


@router.post("/start", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Start (or resume) the queue now", responses=_ERRORS)
def start(request: Request):
    return queue_service.start(request.state.principal)


@router.post("/pause", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Start no new item; a running one finishes", responses=_ERRORS)
def pause(request: Request):
    return queue_service.pause(request.state.principal)


@router.post("/schedule", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Start the queue once at HH:MM on the PC's clock, or clear the schedule",
             responses=_ERRORS)
def set_schedule(body: QueueScheduleSet, request: Request):
    return queue_service.set_schedule(request.state.principal, body.time)


@router.post("/policy", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Whether a failed item stops the queue", responses=_ERRORS)
def set_policy(body: QueuePolicy, request: Request):
    return queue_service.set_policy(request.state.principal, body.stop_on_failure)
