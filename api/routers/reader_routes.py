"""
api/routers/reader_routes.py -- Migration Slice 4: the Reader's page
HTML, served for a sandboxed iframe.

Read-only, like library_routes.py: parse/validate, call
`services.reader_service`, shape the result per `api/schemas.py`. Never
makes a live dictionary lookup or writes to the database -- see
`services/reader_service.py`'s own docstring for the scope decision
that drew that line. A fresh/paid lookup is a separate, explicit action
(not yet exposed here -- Streamlit's own Reader tab button is still the
only way to trigger one as of this slice).
"""

from fastapi import APIRouter, Path, Query
from api.auth import require_permission
from api.schemas import ErrorResponse, ReaderPageResponse
from services import reader_service

router = APIRouter(prefix="/api/reader", tags=["reader"])


@router.get("/dramas/{drama_id}/page", dependencies=[require_permission("library.read")], response_model=ReaderPageResponse,
            summary="One page of a drama's Reader view, definitions from cache only",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def get_reader_page(
        drama_id: int = Path(ge=1),
        page: int = Query(1, ge=1),
        chapter_size: int = Query(reader_service.DEFAULT_CHAPTER_SIZE, ge=10, le=200,
                                  description="Lines per page, matching the Reader tab's own "
                                              "'Lines per page' control."),
        theme: str = Query("light", description="One of reader.py's THEMES keys."),
        font_size: int = Query(22, ge=10, le=48),
        line_height: float = Query(2.4, ge=1.0, le=4.0),
        max_width: int = Query(1200, ge=400, le=2400),
        font: str = Query("system", description="One of reader.py's FONT_STACKS keys.")):
    return reader_service.get_reader_page(
        drama_id, page=page, chapter_size=chapter_size, theme=theme, font_size=font_size,
        line_height=line_height, max_width=max_width, font=font)
