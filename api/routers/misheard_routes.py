"""
api/routers/misheard_routes.py -- Review's "May be mis-heard" suggestions for one title:
list them, and apply one (to a line or to every line that holds the heard spelling).
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import MisheardApplyBody, MisheardApplyResult, MisheardList, error_responses
from services import misheard_service

router = APIRouter(prefix="/api/misheard", tags=["misheard"])


@router.get("/dramas/{drama_id}", dependencies=[require_permission("lines.read")],
            response_model=MisheardList,
            summary="Words that may have been mis-heard: they sound like a trusted word spelled differently",
            responses=error_responses(404))
def get_misheard(drama_id: int = Path(ge=1)):
    return misheard_service.find_for_title(drama_id)


@router.post("/dramas/{drama_id}/apply", dependencies=[require_permission("lines.edit")],
             response_model=MisheardApplyResult,
             summary="Replace a heard spelling with the suggested one on one line or on every line",
             responses=error_responses(404, 409))
def post_apply_misheard(body: MisheardApplyBody, drama_id: int = Path(ge=1)):
    return misheard_service.apply(drama_id, body.heard, body.suggested, body.line_id, body.expected_zh)
