"""
api/routers/blocked_retry_routes.py -- retry one line an engine blocked on
content-moderation grounds, usually with another engine.
Thin adapter over `services.blocked_retry_service`.

Synchronous, so it takes a slot from the shared LLM cap (api/llm_slots.py;
429 when busy). Declares `jobs.start` plus `lines.read` (the reply carries the
line's source and English) and `lines.edit` (it writes the English); the paid-engine check
(`require_engines_allowed` on the named engine) runs as a dependency ahead
of body validation, so a household user naming a paid engine gets 403
whatever else the body holds. Only the engine name is accepted: never a
key, model or free-tier flag.
"""

from fastapi import APIRouter, Depends, Path, Request

from api.auth import require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.schemas import BlockedRetryRequest, BlockedRetryResult, error_responses
from services import blocked_retry_service

router = APIRouter(prefix="/api/lines", tags=["lines"])

_BUSY = "Another AI request is running; try the retry again in a moment."


async def _engine_gate(request: Request):
    """require_engines_allowed on the body's engine (default ollama), before
    the body is validated. An unreadable body counts as a paid engine."""
    try:
        body = await request.json()
    except Exception:
        body = None
    engine = body.get("engine", blocked_retry_service.DEFAULT_ENGINE) if isinstance(body, dict) else None
    require_engines_allowed(request, engine if isinstance(engine, str) else None)


@router.post("/dramas/{drama_id}/lines/{line_id}/retry-blocked",
             dependencies=[require_permission("jobs.start", "lines.read", "lines.edit"), Depends(_engine_gate)],
             response_model=BlockedRetryResult, responses=error_responses(400, 403, 404, 409, 422, 429, 500, 503),
             summary="Re-translate one content-blocked line with the chosen engine")
def post_retry_blocked(body: BlockedRetryRequest, request: Request,
                       drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with llm_slot(request, _BUSY):
        return blocked_retry_service.retry_blocked_line(drama_id, line_id, body.engine)
