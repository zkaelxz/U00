"""
api/routers/private_pages_routes.py -- opens a self-contained HTML page the
owner put in the data folder's private-pages folder, e.g. a grading page with
private clips inside.

    GET /private/{name}.html   pages.private   one .html file from that folder

Admin only (also from away). No listing, no sub-folders, and the name is
matched against a strict pattern. The page is sent with the Reader page's
policy: sandboxed with no network and an opaque origin, so its scripts can't
reach the app's session or API, and never cached. A small stand-in for localStorage
(kept in window.name) is added so a page can still keep its marks across a reload.
"""

from fastapi import APIRouter
from fastapi.responses import Response

from api.auth import require_permission
from api.security_headers import PRIVATE_PAGE_HEADERS
from api.schemas import error_responses
from services import auth_service, private_pages_service

router = APIRouter(tags=["private"])


@router.get("/private/{name}", dependencies=[require_permission(auth_service.PRIVATE_PAGES_PERMISSION)],
            include_in_schema=False, responses=error_responses(404))
def open_page(name: str):
    return Response(private_pages_service.page_bytes(name), media_type="text/html; charset=utf-8",
                    headers=dict(PRIVATE_PAGE_HEADERS))
