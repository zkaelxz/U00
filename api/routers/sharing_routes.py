"""
api/routers/sharing_routes.py -- who can see each drama and series, over
services/ownership_service.py.

    GET  /api/sharing/items                          admin.library  every item, paged
    POST /api/sharing/dramas/{drama_id}/private      lines.edit     owner or admin
    POST /api/sharing/series/{series_id}/private     lines.edit     owner or admin
    GET  /api/sharing/share-by-default               library.read   the caller's own
    POST /api/sharing/share-by-default               lines.edit     the caller's own

The item flips name the item in the path, so `require_permission`'s path
guard answers 404 for one the caller can't see; `set_private` then answers
403 for a visible item they don't own and 409 for a drama in a series or a
series holding someone else's drama. Owners are shown by display name only.
"""

from fastapi import APIRouter, Query, Request

from api.auth import require_permission
from api.schemas import ErrorResponse
from api.sharing_schemas import (SetPrivateRequest, SetPrivateResult, SharingList,
                                 ShareByDefault)
from services import ownership_service

router = APIRouter(prefix="/api/sharing", tags=["sharing"])

_FLIP_ERRS = {403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
              409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


@router.get("/items", dependencies=[require_permission("admin.library")],
            response_model=SharingList, responses={422: {"model": ErrorResponse}},
            summary="Every series and drama with its owner and Shared/Private flag (admins)")
def get_items(request: Request, offset: int = Query(0, ge=0, lt=2**31),
              limit: int = Query(100, ge=1, le=ownership_service.SHARING_PAGE_MAX)):
    return ownership_service.list_sharing(request.state.principal, offset=offset, limit=limit)


@router.post("/dramas/{drama_id}/private", dependencies=[require_permission("lines.edit")],
             response_model=SetPrivateResult, responses=_FLIP_ERRS,
             summary="Make a drama private or shared (its owner or an admin)")
def set_drama_private(drama_id: int, body: SetPrivateRequest, request: Request):
    return ownership_service.set_private(request.state.principal, "drama", drama_id, body.private)


@router.post("/series/{series_id}/private", dependencies=[require_permission("lines.edit")],
             response_model=SetPrivateResult, responses=_FLIP_ERRS,
             summary="Make a series and its dramas private or shared (its owner or an admin)")
def set_series_private(series_id: int, body: SetPrivateRequest, request: Request):
    return ownership_service.set_private(request.state.principal, "series", series_id,
                                         body.private)


@router.get("/share-by-default", dependencies=[require_permission("library.read")],
            response_model=ShareByDefault,
            summary="Whether new items the caller creates are shared with the household")
def get_share_by_default(request: Request):
    return {"share_by_default": ownership_service.get_share_by_default(request.state.principal)}


@router.post("/share-by-default", dependencies=[require_permission("lines.edit")],
             response_model=ShareByDefault, responses={422: {"model": ErrorResponse}},
             summary="Share new items the caller creates with the household, or keep them private")
def set_share_by_default(body: ShareByDefault, request: Request):
    share = ownership_service.set_share_by_default(request.state.principal, body.share_by_default)
    return {"share_by_default": share}
