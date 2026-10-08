"""
api/routers/glossary_routes.py -- series glossary terms, project/series
instructions and the read-only option catalogues.

Terms belong to the drama's series (see services/glossary_service.py).
Deleting a term needs an explicit confirm=true, like the
clear-history route.

Glossary-from-novel: start (engines.paid-gated on the
drama's engine), status with the proposals, and apply by term text.

The same trio exists for the drama's source lines (from-lines);
both applies take optional per-term edits (overrides). The pre-translate
review reuses these routes plus the translate-run start.

Each status carries the run's run_id; an apply sends it back and gets 409
(nothing written) when the held run is another one. Required on from-lines,
optional on from-novel for older callers.

Import a glossary file's text (JSON body, nothing
stored as a file, so lines.edit like the other term writes; overwriting
existing terms needs confirm=true and, until network zones exist, the PC),
export as CSV, and bulk delete by id.
"""

from typing import List

from fastapi import APIRouter, Path, Query, Request, Response
from api.auth import is_auth_enabled, is_local_request, require_engines_allowed, require_permission
from api.schemas import (ErrorResponse, GlossaryBulkDeleteRequest, GlossaryBulkDeleteResult,
                         GlossaryCatalogues, GlossaryDismissals, GlossaryDismissRequest,
                         GlossaryDismissResult, GlossaryImportRequest,
                         GlossaryImportResult, GlossaryInstructions, GlossaryInstructionsUpdate,
                         GlossaryProposalsApplyRequest, GlossaryRunCancelRequest, GlossaryTerm,
                         GlossaryTermUpsert, JobCancelResult, LinesGlossaryApplyRequest,
                         LinesGlossaryRunResult, NovelGlossaryApplyResult, NovelGlossaryRunResult,
                         NovelGlossaryStatus)
from services import glossary_service
from services.service_errors import ForbiddenError, InvalidInputError

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


@router.post("/dramas/{drama_id}/terms/bulk-delete", dependencies=[require_permission("lines.edit")],
             response_model=GlossaryBulkDeleteResult,
             summary="Delete several glossary terms by id (requires confirm=true)",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_bulk_delete_terms(payload: GlossaryBulkDeleteRequest, drama_id: int = Path(ge=1)):
    return glossary_service.bulk_delete_glossary_terms(drama_id, payload.term_ids,
                                                       confirm=payload.confirm)


@router.post("/dramas/{drama_id}/import", dependencies=[require_permission("lines.edit")],
             response_model=GlossaryImportResult,
             summary="Import glossary text (CSV, TSV or JSON) into the series glossary",
             responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                        404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_import_glossary(payload: GlossaryImportRequest, request: Request,
                         drama_id: int = Path(ge=1)):
    # Overwriting is the LAN exception in docs/remote-access-decision.md; with
    # no LAN zone yet (only PC vs not-PC) it stays at the PC.
    if payload.overwrite_existing and is_auth_enabled(request.app) and not is_local_request(request):
        raise ForbiddenError("Replacing existing glossary terms is only allowed at the PC.")
    if payload.overwrite_existing and not payload.confirm:
        raise InvalidInputError("Overwriting existing terms needs confirm=true.")
    return glossary_service.import_glossary_text(
        drama_id, payload.text, filename=payload.filename,
        overwrite_existing=payload.overwrite_existing)


@router.get("/dramas/{drama_id}/export.csv", dependencies=[require_permission("library.read")],
            summary="The series glossary as CSV (download)",
            responses={404: {"model": ErrorResponse}})
def get_glossary_csv(drama_id: int = Path(ge=1)):
    return Response(content=glossary_service.glossary_csv(drama_id),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="glossary_{drama_id}.csv"'})


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


# --- Glossary from the attached novel -------------------------
# The run uses the drama's own translation_engine (default claude) on the
# owner's key, so the start route gates that engine with engines.paid. The
# job only proposes; the apply adds the named terms, matched by text.

@router.post("/dramas/{drama_id}/from-novel", dependencies=[require_permission("jobs.start")], response_model=NovelGlossaryRunResult,
             summary="Start proposing glossary terms from this drama's saved novel",
             responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                        404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def post_start_novel_glossary(request: Request, drama_id: int = Path(ge=1),
                              fresh: bool = Query(False, description=(
                                  "Ignore replies cached by an earlier run"))):
    engine_name = glossary_service.novel_glossary_engine(drama_id)
    require_engines_allowed(request, engine_name)
    # Pass the checked name: the service refuses (409) if the stored engine
    # changed since, so the run can't switch to an engine the gate didn't see.
    return glossary_service.start_novel_glossary_run(drama_id, engine_name=engine_name,
                                                     fresh=fresh)


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


# --- Ignore list: proposals the user rejected, per series ---------------------
# The same permission as the apply: ignoring a proposal edits what the
# glossary workflow shows, like adding one does.

@router.get("/dramas/{drama_id}/dismissals", dependencies=[require_permission("library.read")],
            response_model=GlossaryDismissals,
            summary="Glossary proposals ignored for this drama's series",
            responses={404: {"model": ErrorResponse}})
def get_glossary_dismissals(drama_id: int = Path(ge=1)):
    return glossary_service.list_glossary_dismissals(drama_id)


@router.post("/dramas/{drama_id}/dismissals", dependencies=[require_permission("lines.edit")],
             response_model=GlossaryDismissResult,
             summary="Ignore proposed terms so they are not proposed again",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_dismiss_glossary_proposals(payload: GlossaryDismissRequest, drama_id: int = Path(ge=1)):
    return glossary_service.dismiss_glossary_proposals(drama_id, payload.terms)


@router.post("/dramas/{drama_id}/dismissals/restore", dependencies=[require_permission("lines.edit")],
             response_model=GlossaryDismissResult,
             summary="Take terms off the ignore list",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_restore_glossary_proposals(payload: GlossaryDismissRequest, drama_id: int = Path(ge=1)):
    return glossary_service.restore_glossary_proposals(drama_id, payload.terms)


# --- Glossary from the drama's source lines -----------------------
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
