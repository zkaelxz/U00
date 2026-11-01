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
    GET  /api/series/{series_id}/defaults               library.read  default title settings
    POST /api/series/{series_id}/defaults               lines.edit    editable series
    GET  /api/series/{series_id}/defaults/apply-preview library.read  editable series
    POST /api/series/{series_id}/defaults/apply         lines.edit    editable series

`require_permission`'s path guard answers 404 for a `{series_id}` the
caller can't see (and, for a POST, 403 for one they may not change); the
service checks the source series of a copy, which comes in the query or
body. Delete is PC only, like every other delete.
"""

from typing import Any, Literal

from fastapi import APIRouter, Path, Query, Request, Response

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.auth import local_only, require_permission
from api.schemas import (SeriesCopyTermsPreview, SeriesCopyTermsRequest,
                         SeriesCopyTermsResult, SeriesDefaults, SeriesDefaultsApplyRequest,
                         SeriesDefaultsApplyResult, SeriesDefaultsPreview, SeriesDefaultsUpdate,
                         SeriesDeletePreview, SeriesDeleteRequest, SeriesDeleteResult,
                         SeriesListResponse, SeriesPage, SeriesRenameRequest, SeriesRenameResult,
                         SeriesTmxImportRequest, SeriesTmxImportResult, error_responses)
from lib.capped_request import BodyTooLarge, capped, check_content_length
from lib.errors import InvalidInputError
from services import series_defaults_service as defaults_svc
from services import series_service as svc
from services import tm_service

router = APIRouter(prefix="/api/series", tags=["series"])

_ERRS = error_responses(403, 404, 409, 422)
# error_responses is untyped, so a route's own `responses` literal is annotated to satisfy the type checker.
_TMX_ERRS: dict[int | str, dict[str, Any]] = {**_ERRS}
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


@router.get("/{series_id}/defaults", dependencies=[require_permission("library.read")],
            response_model=SeriesDefaults, responses=_ERRS,
            summary="A series' default title settings and the settings that can be set")
def get_defaults(request: Request, series_id: int = _id()):
    return defaults_svc.get_defaults(request.state.principal, series_id)


@router.post("/{series_id}/defaults", dependencies=[require_permission("lines.edit")],
             response_model=SeriesDefaults, responses=_ERRS,
             summary="Replace a series' default title settings ({} clears them)")
def set_defaults(body: SeriesDefaultsUpdate, request: Request, series_id: int = _id()):
    return defaults_svc.set_defaults(request.state.principal, series_id, body.settings)


@router.get("/{series_id}/defaults/apply-preview", dependencies=[require_permission("library.read")],
            response_model=SeriesDefaultsPreview, responses=_ERRS,
            summary="Which titles and settings applying the defaults would change")
def apply_defaults_preview(request: Request, series_id: int = _id(),
                           mode: str = Query("fill", pattern="^(fill|overwrite)$")):
    return defaults_svc.apply_preview(request.state.principal, series_id, mode)


@router.post("/{series_id}/defaults/apply", dependencies=[require_permission("lines.edit")],
             response_model=SeriesDefaultsApplyResult, responses=_ERRS,
             summary="Apply the defaults to every title in the series (409 while a job runs)")
def apply_defaults(body: SeriesDefaultsApplyRequest, request: Request, series_id: int = _id()):
    return defaults_svc.apply_defaults(request.state.principal, series_id, body.mode)


@router.get("/{series_id}/translation-memory.tmx", dependencies=[require_permission("library.read")],
            responses={200: {"content": {"application/x-tmx+xml": {}}}, **_TMX_ERRS},
            summary="Download the series' translation memory as a TMX file")
def export_tmx(request: Request, series_id: int = _id(),
               source_language: Literal["zh", "ja", "ko"] = Query("zh")):
    text = tm_service.export_tmx(request.state.principal, series_id, source_language)
    return Response(content=text, media_type="application/x-tmx+xml",
                    headers={"Content-Disposition": f'attachment; filename="series-{series_id}-memory.tmx"'})


# The body is JSON, so each TMX character can take up to six bytes once escaped; the cap is that for the 5 MB text limit plus the wrapper.
_TMX_BODY_CAP = 6 * 5_000_000 + 4096
_TMX_TOO_LARGE = "This TMX file is too large."
_TMX_BODY = {"requestBody": {"required": True, "content": {"application/json": {"schema": {
    "type": "object", "required": ["tmx"], "additionalProperties": False,
    "properties": {"tmx": {"type": "string", "description": "The TMX file's text."}}}}}}}


@router.post("/{series_id}/translation-memory/import", dependencies=[require_permission("lines.edit")],
             response_model=SeriesTmxImportResult, responses=_TMX_ERRS, openapi_extra=_TMX_BODY,
             summary="Add translation-memory pairs from a TMX file; sources already stored are kept")
async def import_tmx(request: Request, series_id: int = _id()):
    # Read here, not as a typed body, so the size is checked before any of it is read.
    check_content_length(request, _TMX_BODY_CAP, "the file", _TMX_TOO_LARGE)
    try:
        raw = await capped(request, _TMX_BODY_CAP).body()
    except BodyTooLarge:
        raise StarletteHTTPException(413, _TMX_TOO_LARGE) from None
    try:
        body = SeriesTmxImportRequest.model_validate_json(raw)
    except ValidationError:
        raise InvalidInputError("Send a JSON object with a tmx text.") from None
    return await run_in_threadpool(tm_service.import_tmx, request.state.principal, series_id, body.tmx)
