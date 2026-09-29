"""
api/routers/drama_routes.py -- create a drama and edit its metadata
(Migration Slice 35).

Thin adapters over `services.drama_service`. POST for both writes,
matching every other mutation endpoint (no PATCH precedent). Update is a
partial update: only fields present in the JSON body are passed on
(`exclude_unset`), so an omitted field never means "set to None".
Delete (Slice 36) needs confirm=true and confirm_text=DELETE as query
params, like translate history's confirm gate. Cover art (inventory P14):
upload is PC-only (local_only, uploads are PC-only) and checked/re-encoded by
services/cover_art_service.py; reading it is library.read. Series
rename/unassign and presets CRUD are out of scope -- see
services/drama_service.py.
"""

import os

from fastapi import APIRouter, File, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse
from api.auth import local_only, require_permission
from api.routers.library_routes import _to_detail
from api.schemas import (CoverArtResult, DramaCreateRequest, DramaCreateResult,
                         DramaDeleteResult, DramaDetail, DramaMetadataUpdate, ErrorResponse)
from services import cover_art_service, drama_service

router = APIRouter(prefix="/api/dramas", tags=["dramas"])


@router.post("", dependencies=[require_permission("admin.library")], response_model=DramaCreateResult, status_code=201,
             summary="Create a drama (optionally with a series and/or preset)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_drama(payload: DramaCreateRequest, request: Request):
    result = drama_service.create_drama(**payload.model_dump(),
                                        principal=request.state.principal)
    return DramaCreateResult(**_to_detail(result).model_dump(),
                             preset_defaults=result.get("preset_defaults"))


@router.post("/{drama_id}/metadata", dependencies=[require_permission("admin.library")], response_model=DramaDetail,
             summary="Update a drama's metadata (partial update)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_drama_metadata(payload: DramaMetadataUpdate, request: Request,
                        drama_id: int = Path(ge=1)):
    return _to_detail(drama_service.update_drama_metadata(
        drama_id, principal=request.state.principal,
        **payload.model_dump(exclude_unset=True)))


@router.post("/{drama_id}/cover", dependencies=[local_only()], response_model=CoverArtResult,
             summary="Set or replace the cover art (PNG/JPEG/WebP, max 10 MB; metadata stripped)",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def post_cover(drama_id: int = Path(ge=1), file: UploadFile = File(...)):
    return cover_art_service.save_cover(drama_id, file.file)


@router.get("/{drama_id}/cover", dependencies=[require_permission("library.read")],
            summary="The cover art image", responses={404: {"model": ErrorResponse}})
def get_cover(drama_id: int = Path(ge=1)):
    path, media_type = cover_art_service.cover_file(drama_id)
    return FileResponse(path, media_type=media_type, content_disposition_type="inline",
                        filename=f"cover_{drama_id}{os.path.splitext(path)[1]}",
                        headers={"Cache-Control": "private, no-cache",
                                 "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.delete("/{drama_id}", dependencies=[local_only()], response_model=DramaDeleteResult, response_model_exclude_none=True,
               summary="Permanently delete a drama (requires confirm=true and confirm_text=DELETE)",
               responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                          422: {"model": ErrorResponse}})
def delete_drama(drama_id: int = Path(ge=1), confirm: bool = Query(False),
                 confirm_text: str = Query("")):
    return drama_service.delete_drama(drama_id, confirm=confirm, confirm_text=confirm_text)
