"""
api/routers/drama_routes.py -- create a drama and edit its metadata
(Migration Slice 35).

Thin adapters over `services.drama_service`. POST for both writes,
matching every other mutation endpoint (no PATCH precedent). Update is a
partial update: only fields present in the JSON body are passed on
(`exclude_unset`), so an omitted field never means "set to None".
Delete (Slice 36) needs confirm=true and confirm_text=DELETE as query
params, like translate history's confirm gate. Cover upload, series rename/unassign and presets CRUD are out of
scope -- see services/drama_service.py.
"""

from fastapi import APIRouter, Path, Query

from api.routers.library_routes import _to_detail
from api.schemas import (DramaCreateRequest, DramaCreateResult, DramaDeleteResult,
                         DramaDetail, DramaMetadataUpdate, ErrorResponse)
from services import drama_service

router = APIRouter(prefix="/api/dramas", tags=["dramas"])


@router.post("", response_model=DramaCreateResult, status_code=201,
             summary="Create a drama (optionally with a series and/or preset)",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_drama(payload: DramaCreateRequest):
    result = drama_service.create_drama(**payload.model_dump())
    return DramaCreateResult(**_to_detail(result).model_dump(),
                             preset_defaults=result.get("preset_defaults"))


@router.post("/{drama_id}/metadata", response_model=DramaDetail,
             summary="Update a drama's metadata (partial update)",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_drama_metadata(payload: DramaMetadataUpdate, drama_id: int = Path(ge=1)):
    return _to_detail(drama_service.update_drama_metadata(
        drama_id, **payload.model_dump(exclude_unset=True)))


@router.delete("/{drama_id}", response_model=DramaDeleteResult,
               summary="Permanently delete a drama (requires confirm=true and confirm_text=DELETE)",
               responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                          422: {"model": ErrorResponse}})
def delete_drama(drama_id: int = Path(ge=1), confirm: bool = Query(False),
                 confirm_text: str = Query("")):
    return drama_service.delete_drama(drama_id, confirm=confirm, confirm_text=confirm_text)
