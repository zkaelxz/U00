"""
api/routers/three_run_routes.py -- "Run 3 times, review differences" in Review:
open a review (undo snapshot), record each of three ordinary Translate runs,
list the lines the runs disagree on, and write the owner's per-line picks.
Logic lives in services/three_run_service.py; the runs themselves start through
the normal Translate run route.
"""

from fastapi import APIRouter, Path, Response
from api.auth import require_permission
from api.schemas import (ThreeRunBegin, ThreeRunBegun, ThreeRunCapture, ThreeRunKeepRest,
                         ThreeRunPickResult, ThreeRunPicks, ThreeRunState, error_responses)
from services import three_run_service

router = APIRouter(prefix="/api/three-run", tags=["three-run"])

_ERRORS = error_responses(400, 404, 409, 422)


@router.get("/dramas/{drama_id}", dependencies=[require_permission("lines.read")], response_model=ThreeRunState,
            summary="The open 3-run review: runs recorded and the lines that differ", responses=error_responses(404))
def get_state(drama_id: int = Path(ge=1)):
    return three_run_service.state(drama_id)


@router.post("/dramas/{drama_id}/begin", dependencies=[require_permission("lines.edit")], response_model=ThreeRunBegun,
             summary="Open a 3-run review and take its undo snapshot", responses=_ERRORS)
def begin(body: ThreeRunBegin, drama_id: int = Path(ge=1)):
    return three_run_service.begin(drama_id, body.line_ids)


@router.post("/dramas/{drama_id}/capture", dependencies=[require_permission("lines.edit")], response_model=ThreeRunState,
             summary="Record the English a finished translation run left on the lines", responses=_ERRORS)
def capture(body: ThreeRunCapture, drama_id: int = Path(ge=1)):
    return three_run_service.capture_run(drama_id, body.run)


@router.post("/dramas/{drama_id}/pick", dependencies=[require_permission("lines.edit")], response_model=ThreeRunPickResult,
             summary="Use the chosen run's English for each listed line (compare-and-set)", responses=_ERRORS)
def pick(body: ThreeRunPicks, drama_id: int = Path(ge=1)):
    return three_run_service.pick_runs(drama_id, [p.model_dump() for p in body.picks])


@router.post("/dramas/{drama_id}/keep-rest", dependencies=[require_permission("lines.edit")], response_model=ThreeRunPickResult,
             summary="Use one run's English for every differing line not picked yet", responses=_ERRORS)
def keep_rest(body: ThreeRunKeepRest, drama_id: int = Path(ge=1)):
    return three_run_service.keep_run_for_rest(drama_id, body.run)


@router.post("/dramas/{drama_id}/restore", dependencies=[require_permission("lines.edit")], response_model=ThreeRunPickResult,
             summary="Put back the English the review's lines had before it (en only, compare-and-set)", responses=_ERRORS)
def restore(drama_id: int = Path(ge=1)):
    return three_run_service.restore_original(drama_id)


@router.delete("/dramas/{drama_id}", dependencies=[require_permission("lines.edit")], status_code=204,
               summary="Close the 3-run review (the lines keep what they hold)", responses=error_responses(404))
def end(drama_id: int = Path(ge=1)):
    three_run_service.end(drama_id)
    return Response(status_code=204)
