"""
api/routers/glossary_routes.py -- series glossary terms, project/series
instructions and the read-only option catalogues (Migration Slice 46).

Terms belong to the drama's series (see services/glossary_service.py).
Deleting a term needs an explicit confirm=true, mirroring the Translate
tab's confirm checkbox and Slice 17's clear-history route.
"""

from typing import List

from fastapi import APIRouter, Path, Query
from api.auth import require_permission
from api.schemas import (ErrorResponse, GlossaryCatalogues, GlossaryDeleteResult,
                         GlossaryInstructions, GlossaryInstructionsUpdate, GlossaryTerm,
                         GlossaryTermUpsert)
from services import glossary_service

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
