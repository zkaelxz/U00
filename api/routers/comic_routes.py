"""
api/routers/comic_routes.py -- the comic viewer's read API. Shares the
/api/scanlate prefix with scanlate_routes.py. Thin over
services/comic_view_service.py:

- GET  /dramas/{id}/pages                        library.read
- GET/HEAD /dramas/{id}/pages/{pid}/image        media.stream
- GET  /dramas/{id}/pages/{pid}/regions          lines.read
- GET  /dramas/{id}/progress                     library.read
- POST /dramas/{id}/progress                     lines.edit
- POST /dramas/{id}/pages/visibility             lines.edit

Images go through Starlette's FileResponse (Range, HEAD, ETag,
Last-Modified); this module adds the If-None-Match 304 that FileResponse
does not do. Content type comes from the file's magic bytes, the
download name is generic, and every refusal is one generic 404.
"""

from typing import Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import FileResponse, Response

from api.auth import require_permission
from api.comic_schemas import (ComicPageList, ComicPageRegions, ComicProgress, ComicProgressRequest,
                              ComicVisibilityRequest, ComicVisibilityResult)
from api.schemas import ErrorResponse
from services import comic_chapters_service, comic_view_service

router = APIRouter(prefix="/api/scanlate", tags=["comic"])

_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_CACHE = "private, no-cache"
_EXT = {"image/png": "png", "image/jpeg": "jpg"}


@router.get("/dramas/{drama_id}/pages", dependencies=[require_permission("library.read")],
            response_model=ComicPageList,
            summary="A drama's page list for the comic viewer (no paths or file names)",
            responses=_ERRS)
def get_pages(drama_id: int = Path(ge=1)):
    return comic_view_service.list_pages(drama_id)


def _etag_matches(header: str, etag: str) -> bool:
    for tag in header.split(","):
        tag = tag.strip()
        if tag == "*" or tag.removeprefix("W/") == etag:
            return True
    return False


_STREAM = [require_permission("media.stream")]


@router.head("/dramas/{drama_id}/pages/{page_id}/image", dependencies=_STREAM,
             include_in_schema=False)
@router.get("/dramas/{drama_id}/pages/{page_id}/image", dependencies=_STREAM,
            summary="One page image (original or typeset), content type from its magic bytes",
            responses={200: {"content": {"image/png": {}, "image/jpeg": {}}},
                       304: {"description": "Not modified"}, **_ERRS})
def get_page_image(request: Request, drama_id: int = Path(ge=1), page_id: int = Path(ge=1),
                   variant: Literal["original", "rendered"] = Query("original")):
    found = comic_view_service.resolve_page_image(drama_id, page_id, variant)
    name = f"page_{found['ordinal']}.{_EXT[found['content_type']]}"
    resp = FileResponse(found["path"], media_type=found["content_type"], filename=name,
                        content_disposition_type="inline", stat_result=found["stat"],
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": _CACHE})
    inm = request.headers.get("if-none-match")
    if inm and _etag_matches(inm, resp.headers["etag"]):
        return Response(status_code=304, headers={
            "ETag": resp.headers["etag"], "Last-Modified": resp.headers["last-modified"],
            "Cache-Control": _CACHE, "X-Content-Type-Options": "nosniff"})
    return resp


@router.get("/dramas/{drama_id}/pages/{page_id}/regions",
            dependencies=[require_permission("lines.read")], response_model=ComicPageRegions,
            summary="A page's visible text regions in reading order (skipped and SFX dropped)",
            responses=_ERRS)
def get_page_regions(drama_id: int = Path(ge=1), page_id: int = Path(ge=1)):
    return comic_view_service.get_page_regions(drama_id, page_id)


@router.get("/dramas/{drama_id}/progress", dependencies=[require_permission("library.read")],
            response_model=ComicProgress, summary="The comic resume page", responses=_ERRS)
def get_progress(drama_id: int = Path(ge=1)):
    return comic_view_service.get_progress(drama_id)


@router.post("/dramas/{drama_id}/progress", dependencies=[require_permission("lines.edit")],
             response_model=ComicProgress,
             summary="Record the page being read (last_page and percent only)", responses=_ERRS)
def post_progress(body: ComicProgressRequest, drama_id: int = Path(ge=1)):
    return comic_view_service.save_progress(drama_id, body.page)


@router.post("/dramas/{drama_id}/pages/visibility", dependencies=[require_permission("lines.edit")],
             response_model=ComicVisibilityResult,
             summary="Hide or restore pages that are not part of the story (credits, promos)",
             responses=_ERRS)
def post_visibility(body: ComicVisibilityRequest, drama_id: int = Path(ge=1)):
    return comic_chapters_service.set_visibility(
        drama_id, body.hidden, page_ids=body.page_ids, chapter_id=body.chapter_id,
        edge=body.edge, count=body.count)
