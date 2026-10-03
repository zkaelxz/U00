"""
api/routers/saved_comics_routes.py -- the save folder for chapters saved as
CBZ files, and reading them in the app. Thin over
services/sources_save_service.py (the folder) and
services/saved_comics_service.py (the library).

- GET  /folder          local_only    where saves go (a path on this PC)
- POST /folder          local_only    pick it ("" = the default)
- POST /folder/open     local_only    open it in the PC's file manager
- GET  /series          library.read  every saved series
- GET  /chapters        library.read  one series' chapters
- GET  /pages           library.read  one chapter's pages, prev/next chapter
- GET  /page            media.stream  one page image

Series and chapters are named by source, series and chapter names (query
parameters), never by a path; the service refuses anything that isn't one
existing name under the save folder.
"""

from typing import List

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from api.auth import local_only, require_permission
from api.saved_comics_schemas import (SavedChapterList, SavedChapterPages, SavedComicsFolder,
                                      SavedComicsFolderUpdate, SavedComicsOpened, SavedSeries)
from api.schemas import ErrorResponse
from services import saved_comics_service as library
from services import sources_save_service as saves

router = APIRouter(prefix="/api/saved-comics", tags=["saved-comics"])

_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_NAME = {"min_length": 1, "max_length": library.MAX_NAME_LEN}


@router.get("/folder", dependencies=[local_only()], response_model=SavedComicsFolder,
            summary="PC only: the folder chapters saved as CBZ go to")
def get_folder():
    return saves.get_save_folder()


@router.post("/folder", dependencies=[local_only()], response_model=SavedComicsFolder,
             summary="PC only: pick the folder chapters saved as CBZ go to (\"\" = default)",
             responses={422: {"model": ErrorResponse}})
def post_folder(body: SavedComicsFolderUpdate):
    return saves.set_save_folder(body.folder)


@router.post("/folder/open", dependencies=[local_only()], response_model=SavedComicsOpened,
             summary="PC only: open the save folder in this PC's file manager",
             responses={400: {"model": ErrorResponse}})
def post_open_folder():
    return saves.open_save_folder()


@router.get("/series", dependencies=[require_permission("library.read")],
            response_model=List[SavedSeries], summary="Every series with chapters saved as CBZ")
def get_series():
    return library.list_series()


@router.get("/chapters", dependencies=[require_permission("library.read")],
            response_model=SavedChapterList, summary="One saved series' chapters", responses=_ERRS)
def get_chapters(source: str = Query(**_NAME), series: str = Query(**_NAME)):
    return library.list_chapters(source, series)


@router.get("/pages", dependencies=[require_permission("library.read")],
            response_model=SavedChapterPages,
            summary="One saved chapter's pages (sizes) and the chapters around it",
            responses=_ERRS)
def get_pages(source: str = Query(**_NAME), series: str = Query(**_NAME),
              chapter: str = Query(**_NAME)):
    return library.chapter_pages(source, series, chapter)


@router.get("/page", dependencies=[require_permission("media.stream")],
            summary="One page image of a saved chapter",
            responses={200: {"content": {"image/webp": {}, "image/jpeg": {}, "image/png": {}}},
                       **_ERRS})
def get_page(request: Request, source: str = Query(**_NAME), series: str = Query(**_NAME),
             chapter: str = Query(**_NAME), page: int = Query(ge=1, le=library.MAX_PAGES)):
    data, media_type, mtime = library.page_image(source, series, chapter, page)
    etag = f'"{int(mtime * 1000):x}-{page}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache",
               "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)
