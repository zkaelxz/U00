"""
api/routers/metadata_research_routes.py -- "Research online" for a drama's
metadata (roadmap Step 37). See services/metadata_research_service.py: a
lookup returns per-field values with cited sources and writes nothing; apply
writes only the fields the user chose to replace.

A lookup is media.import_url (an outbound search, like autofill) plus
require_engines_allowed for Gemini, and takes a slot from the shared LLM cap
(api/llm_slots.py; 429 when busy). Apply is admin.library (a drama-metadata
write). Budget and provenance are library.read.
"""

from fastapi import APIRouter, Path, Request

from api.auth import require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.metadata_research_schemas import (ProvenanceList, ResearchApplied, ResearchApply,
                                           ResearchBudget, ResearchRequest, ResearchResult)
from api.schemas import ErrorResponse
from services import metadata_research_service

router = APIRouter(prefix="/api/metadata", tags=["metadata"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


@router.get("/research/budget", dependencies=[require_permission("library.read")],
            response_model=ResearchBudget,
            summary="Today's free grounded searches left, and the monthly cap")
def research_budget():
    return metadata_research_service.budget_status()


@router.post("/dramas/{drama_id}/research", dependencies=[require_permission("media.import_url")],
             response_model=ResearchResult,
             summary="Research the drama's metadata online with cited sources (writes nothing)",
             responses={**_ERRS, 403: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
def research(payload: ResearchRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, "gemini")
    with llm_slot(request):
        return metadata_research_service.research(
            drama_id, mode=payload.mode, model=payload.model, allow_paid=payload.allow_paid,
            refresh=payload.refresh)


@router.post("/dramas/{drama_id}/research/apply", dependencies=[require_permission("admin.library")],
             response_model=ResearchApplied,
             summary="Keep, replace or save-both each researched field (per-field provenance)",
             responses=_ERRS)
def apply_research(payload: ResearchApply, request: Request, drama_id: int = Path(ge=1)):
    return metadata_research_service.apply_research(
        drama_id, payload.research_id, payload.choices, seen=payload.seen,
        principal=request.state.principal)


@router.get("/dramas/{drama_id}/provenance", dependencies=[require_permission("library.read")],
            response_model=ProvenanceList,
            summary="Stored per-field sources for the drama's researched metadata",
            responses={404: {"model": ErrorResponse}})
def provenance(drama_id: int = Path(ge=1)):
    return metadata_research_service.list_provenance(drama_id)
