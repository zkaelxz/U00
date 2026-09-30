"""
api/routers/web_search_routes.py -- the optional web-search fallback
(roadmap item 114). See services/web_search_service.py.

`status` and `search` are `library.read`, like the title search they back
up (POST /api/sources/search): they send a query to the owner's own
SearXNG server and return links; nothing is fetched from those links,
nothing is written. The settings and the test are local_only(); changing
the server address (where the app sends requests) also takes the engine
endpoint-URL gate (`_require_local_admin`) and confirm=true.
"""

from fastapi import APIRouter, Request

from api.auth import local_only, require_permission
from api.routers.settings_routes import _require_confirm, _require_local_admin
from api.schemas import ErrorResponse
from api.web_search_schemas import (WebSearchConfig, WebSearchConfigUpdate, WebSearchRequest,
                                    WebSearchResults, WebSearchStatus, WebSearchTestResult)
from services import web_search_service

router = APIRouter(prefix="/api/web-search", tags=["web-search"])

_ERRS = {409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.get("/status", dependencies=[require_permission("library.read")],
            response_model=WebSearchStatus,
            summary="Whether the web-search fallback is on (no address)")
def get_status():
    return web_search_service.status()


@router.post("/search", dependencies=[require_permission("library.read")],
             response_model=WebSearchResults,
             summary="Search the web through the configured SearXNG server (links only; "
                     "409 while off)", responses=_ERRS)
def post_search(payload: WebSearchRequest):
    return web_search_service.search(payload.query)


@router.get("/config", dependencies=[local_only()], response_model=WebSearchConfig,
            summary="PC only: the web-search fallback settings")
def get_config():
    return web_search_service.get_config()


@router.post("/config", dependencies=[local_only()], response_model=WebSearchConfig,
             summary="PC only: turn the fallback on or off; the address also needs the "
                     "key-write gate and confirm",
             responses={422: {"model": ErrorResponse}})
def set_config(payload: WebSearchConfigUpdate, request: Request):
    if payload.base_url is not None:  # where requests go: same gate as endpoint URLs
        _require_local_admin(request)
        _require_confirm(payload.confirm)
    return web_search_service.set_config(enabled=payload.enabled, base_url=payload.base_url)


@router.post("/test", dependencies=[local_only()], response_model=WebSearchTestResult,
             summary="PC only: check the SearXNG address and its JSON format", responses=_ERRS)
def test_connection():
    return web_search_service.test_connection()
