"""
api/routers/series_routes.py -- the Series page, over
services/series_service.py.

    GET  /api/series                                    library.read  every visible series
    GET  /api/series/{series_id}                        library.read  titles and counts
    POST /api/series/{series_id}/rename                 lines.edit    editable series
    GET  /api/series/{series_id}/delete-preview         library.read  owner or admin
    POST /api/series/{series_id}/delete                 PC only       owner or admin
    GET  /api/series/{series_id}/copy-terms-preview     library.read  editable series
    POST /api/series/{series_id}/copy-terms             lines.edit    editable series

`require_permission`'s path guard answers 404 for a `{series_id}` the
caller can't see (and, for a POST, 403 for one they may not change); the
service checks the source series of a copy, which comes in the query or
body. Delete is PC only, like every other delete.
"""

from fastapi import APIRouter, Path, Query, Request

from api.auth import local_only, require_permission
from api.schemas import (ErrorResponse, SeriesCopyTermsPreview, SeriesCopyTermsRequest,
                         SeriesCopyTermsResult, SeriesDeletePreview, SeriesDeleteRequest,
                         SeriesDeleteResult, SeriesListResponse, SeriesPage,
                         SeriesRenameRequest, SeriesRenameResult)
from services import series_service as svc

router = APIRouter(prefix="/api/series", tags=["series"])

_ERRS = {403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_MAX_ID = 2**31 - 1


def _id():
    return Path(ge=1, le=_MAX_ID)


@router.get("", dependencies=[require_permission("library.read")],
            response_model=SeriesListResponse,
            summary="Every series the caller can see, with title and term counts")
def list_series(request: Request):
    return {"items": svc.list_series(principal=request.state.principal)}


@router.get("/{series_id}", dependencies=[require_permission("library.read")],
            response_model=SeriesPage, responses=_ERRS,
            summary="A series' titles (media type, line count) and glossary/people counts")
def get_series(request: Request, series_id: int = _id()):
    return svc.get_series_page(request.state.principal, series_id)


@router.post("/{series_id}/rename", dependencies=[require_permission("lines.edit")],
             response_model=SeriesRenameResult, responses=_ERRS,
             summary="Rename a series (409 if another series has that name, ignoring case)")
def rename_series(body: SeriesRenameRequest, request: Request, series_id: int = _id()):
    return svc.rename_series(request.state.principal, series_id, body.name)


@router.get("/{series_id}/delete-preview", dependencies=[require_permission("library.read")],
            response_model=SeriesDeletePreview, responses=_ERRS,
            summary="What deleting a series would delete and keep (its owner or an admin)")
def delete_preview(request: Request, series_id: int = _id()):
    return svc.delete_preview(request.state.principal, series_id)


@router.post("/{series_id}/delete", dependencies=[local_only()],
             response_model=SeriesDeleteResult, responses=_ERRS,
             summary="Delete a series; its titles are kept as No series (confirm=true, "
                     "confirm_text=DELETE unless empty; 409 while a job runs)")
def delete_series(body: SeriesDeleteRequest, request: Request, series_id: int = _id()):
    return svc.delete_series(request.state.principal, series_id, confirm=body.confirm,
                             confirm_text=body.confirm_text)


@router.get("/{series_id}/copy-terms-preview", dependencies=[require_permission("library.read")],
            response_model=SeriesCopyTermsPreview, responses=_ERRS,
            summary="How many glossary terms copying from another series would add or skip")
def copy_terms_preview(request: Request, series_id: int = _id(),
                       source_series_id: int = Query(ge=1, le=_MAX_ID)):
    return svc.copy_terms_preview(request.state.principal, series_id, source_series_id)


@router.post("/{series_id}/copy-terms", dependencies=[require_permission("lines.edit")],
             response_model=SeriesCopyTermsResult, responses=_ERRS,
             summary="Copy another series' glossary terms in; terms already there are skipped")
def copy_terms(body: SeriesCopyTermsRequest, request: Request, series_id: int = _id()):
    return svc.copy_terms(request.state.principal, series_id, body.source_series_id)
