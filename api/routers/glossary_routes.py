"""
api/routers/glossary_routes.py -- series glossary terms, project/series
instructions and the read-only option catalogues (Migration Slice 46).

Terms belong to the drama's series (see services/glossary_service.py).
Deleting a term needs an explicit confirm=true, mirroring the Translate
tab's confirm checkbox and Slice 17's clear-history route.

Route batch 2C adds glossary-from-novel: start (engines.paid-gated on the
drama's engine), status with the proposals, and apply by term text.

Parity X10 adds the same trio for the drama's source lines (from-lines);
both applies take optional per-term edits (overrides). The pre-translate
review (X28) reuses these routes plus the translate-run start.

Each status carries the run's run_id; an apply sends it back and gets 409
(nothing written) when the held run is another one. Required on from-lines,
optional on from-novel for older callers.
"""

from typing import List

from fastapi import APIRouter, Path, Query, Request
from api.auth import require_engines_allowed, require_permission
from api.schemas import (ErrorResponse, GlossaryCatalogues, GlossaryDeleteResult,
                         GlossaryInstructions, GlossaryInstructionsUpdate,
                         GlossaryProposalsApplyRequest, GlossaryRunCancelRequest, GlossaryTerm,
                         GlossaryTermUpsert, JobCancelResult, LinesGlossaryApplyRequest, LinesGlossaryRunResult,
                         NovelGlossaryApplyResult, NovelGlossaryRunResult, NovelGlossaryStatus)
from services import glossary_service
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/glossary", tags=["glossary"])


@router.get("/catalogues", dependencies=[require_permission("library.read")], response_model=GlossaryCatalogues,
            summary="Style presets, term categories/policies and workflow tiers (read-only)")
def get_catalogues():
    return glossary_service.get_catalogues()


@router.get("/dramas/{drama_id}/terms", dependencies=[require_permission("library.read")], response_model=List[GlossaryTerm],
            summary="The drama's series glossary (empty when it has no series)",
            responses={404: {"model": ErrorResponse}})
def get_terms(drama_id: int = Path(ge=1)):
    return glossary_service.list_glossary_terms(drama_id)


@router.post("/dramas/{drama_id}/terms", dependencies=[require_permission("lines.edit")], response_model=GlossaryTerm,
             summary="Create or update a glossary term (update when id is given)",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_term(payload: GlossaryTermUpsert, drama_id: int = Path(ge=1)):
    return glossary_service.upsert_glossary_term(drama_id, payload.model_dump(exclude_unset=True))


@router.delete("/dramas/{drama_id}/terms/{term_id}", dependencies=[require_permission("lines.edit")], response_model=GlossaryDeleteResult,
               summary="Delete a glossary term (requires confirm=true)",
               responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def delete_term(drama_id: int = Path(ge=1), term_id: int = Path(ge=1),
                confirm: bool = Query(False)):
    glossary_service.delete_glossary_term(drama_id, term_id, confirm=confirm)
    return {"deleted": True}


@router.get("/dramas/{drama_id}/instructions", dependencies=[require_permission("library.read")], response_model=GlossaryInstructions,
            summary="Project and series instructions for a drama",
            responses={404: {"model": ErrorResponse}})
def get_instructions(drama_id: int = Path(ge=1)):
    return glossary_service.get_instructions(drama_id)


@router.post("/dramas/{drama_id}/instructions/project", dependencies=[require_permission("lines.edit")], response_model=GlossaryInstructions,
             summary="Set this drama's project instructions",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_project_instructions(payload: GlossaryInstructionsUpdate, drama_id: int = Path(ge=1)):
    return glossary_service.set_project_instructions(drama_id, payload.text)


@router.post("/dramas/{drama_id}/instructions/series", dependencies=[require_permission("lines.edit")], response_model=GlossaryInstructions,
             summary="Set the series instructions (drama must belong to a series)",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_series_instructions(payload: GlossaryInstructionsUpdate, drama_id: int = Path(ge=1)):
    return glossary_service.set_series_instructions(drama_id, payload.text)


# --- Route batch 2C: glossary from the attached novel -------------------------
# The run uses the drama's own translation_engine (default claude) on the
# owner's key, so the start route gates that engine with engines.paid. The
# job only proposes; the apply adds the named terms, matched by text.

@router.post("/dramas/{drama_id}/from-novel", dependencies=[require_permission("jobs.start")], response_model=NovelGlossaryRunResult,
             summary="Start proposing glossary terms from this drama's saved novel",
             responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                        404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def post_start_novel_glossary(request: Request, drama_id: int = Path(ge=1)):
    engine_name = glossary_service.novel_glossary_engine(drama_id)
    require_engines_allowed(request, engine_name)
    # Pass the checked name: the service refuses (409) if the stored engine
    # changed since, so the run can't switch to an engine the gate didn't see.
    return glossary_service.start_novel_glossary_run(drama_id, engine_name=engine_name)


@router.get("/dramas/{drama_id}/from-novel", dependencies=[require_permission("library.read")], response_model=NovelGlossaryStatus,
            summary="Status and (when done) the proposed terms of this drama's novel extraction",
            responses={404: {"model": ErrorResponse}})
def get_novel_glossary(drama_id: int = Path(ge=1)):
    return _status_body(glossary_service.get_novel_glossary_status(drama_id))


@router.post("/dramas/{drama_id}/from-novel/apply", dependencies=[require_permission("lines.edit")], response_model=NovelGlossaryApplyResult,
             summary="Add named proposals to the series glossary (overwrite needs confirm=true)",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_apply_novel_glossary(payload: GlossaryProposalsApplyRequest, drama_id: int = Path(ge=1)):
    return _apply(glossary_service.apply_novel_glossary, drama_id, payload)


@router.post("/dramas/{drama_id}/from-novel/cancel", dependencies=[require_permission("jobs.cancel")],
             response_model=JobCancelResult,
             summary="Cancel this drama's novel extraction, only if it is still run run_id",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_cancel_novel_glossary(payload: GlossaryRunCancelRequest, drama_id: int = Path(ge=1)):
    return glossary_service.cancel_novel_glossary_run(drama_id, payload.run_id)


def _status_body(s: dict) -> dict:
    return {"job_id": s["job_id"], "status": s["status"], "progress": s.get("progress"),
            "message": s.get("message") or "",
            "proposals": (s.get("result") or {}).get("proposals"), "run_id": s.get("run_id")}


def _apply(apply_fn, drama_id: int, payload: GlossaryProposalsApplyRequest) -> dict:
    if payload.overwrite_existing and not payload.confirm:
        raise InvalidInputError("Overwriting existing terms needs confirm=true.")
    overrides = {term: edit.model_dump(exclude_unset=True)
                 for term, edit in payload.overrides.items()}
    return apply_fn(drama_id, payload.terms, overwrite_existing=payload.overwrite_existing,
                    overrides=overrides, run_id=payload.run_id)


# --- Parity X10: glossary from the drama's source lines -----------------------
# Same gate and shape as from-novel: the drama's own engine, checked with
# engines.paid, passed on so the service refuses (409) if it changed since.

@router.post("/dramas/{drama_id}/from-lines", dependencies=[require_permission("jobs.start")], response_model=LinesGlossaryRunResult,
             summary="Start proposing glossary terms from this drama's source lines",
             responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                        404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def post_start_lines_glossary(request: Request, drama_id: int = Path(ge=1)):
    engine_name = glossary_service.lines_glossary_engine(drama_id)
    require_engines_allowed(request, engine_name)
    return glossary_service.start_lines_glossary_run(drama_id, engine_name=engine_name)


@router.get("/dramas/{drama_id}/from-lines", dependencies=[require_permission("library.read")], response_model=NovelGlossaryStatus,
            summary="Status and (when done) the proposed terms of this drama's lines extraction",
            responses={404: {"model": ErrorResponse}})
def get_lines_glossary(drama_id: int = Path(ge=1)):
    return _status_body(glossary_service.get_lines_glossary_status(drama_id))


@router.post("/dramas/{drama_id}/from-lines/apply", dependencies=[require_permission("lines.edit")], response_model=NovelGlossaryApplyResult,
             summary="Add named lines proposals to the series glossary (overwrite needs confirm=true)",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_apply_lines_glossary(payload: LinesGlossaryApplyRequest, drama_id: int = Path(ge=1)):
    return _apply(glossary_service.apply_lines_glossary, drama_id, payload)


@router.post("/dramas/{drama_id}/from-lines/cancel", dependencies=[require_permission("jobs.cancel")],
             response_model=JobCancelResult,
             summary="Cancel this drama's lines extraction, only if it is still run run_id",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_cancel_lines_glossary(payload: GlossaryRunCancelRequest, drama_id: int = Path(ge=1)):
    return glossary_service.cancel_lines_glossary_run(drama_id, payload.run_id)
