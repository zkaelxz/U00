"""
api/routers/extension_bridge_routes.py -- the browser extension's routes for
a household member's own computer, behind a per-device token
(`Authorization: Bearer baihe_dt_...`, never a session or cookie).

The same work as the PC-only bridge in `page_server.py` (that module's
functions are called, so there is one pipeline), with the caller's rights:
a title is sent into only when the member can edit it, the title list shows
only what they can see, the monthly spending cap applies, and nothing is
fetched from a URL the extension sends. The PC's own extension keeps using
the loopback bridge with the shared token.

Answers keep the bridge's `{"error": text}` shape so the extension reads
both the same way. Bodies are counted as they arrive and capped at the
bridge's own limit, since the proxy's small default body cap is lifted for
`/api/ext-bridge/pages` and `/page`.
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import page_server
from api.auth import require_device_token, require_engines_allowed
from lib.capped_request import BodyTooLarge, capped, check_content_length
from lib.errors import InvalidInputError
from services import extension_bridge_service
from services import extension_novel_service as novel
from services import extension_title_service as titles
from services import translate_run_service
from lib.errors import ForbiddenError, ServiceError
from starlette.exceptions import HTTPException as StarletteHTTPException

router = APIRouter(prefix="/api/ext-bridge", tags=["extension"])

_NO_STORE = {"Cache-Control": "no-store"}


def _answer(status: int, payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=_NO_STORE)


def _refusal(error: page_server.EndpointError) -> JSONResponse:
    return _answer(error.status, {"error": error.message})


async def _json_body(request: Request) -> dict:
    cap = page_server.MAX_BODY_BYTES
    try:
        check_content_length(request, cap, "the request",
                             f"request is larger than {cap // (1024 * 1024)}MB",
                             "a valid Content-Length is required")
        body = await capped(request, cap).body()
    except InvalidInputError as e:
        raise page_server.EndpointError(400, e.message) from None
    except StarletteHTTPException as e:
        raise page_server.EndpointError(413, e.detail) from None
    except BodyTooLarge:
        raise page_server.EndpointError(413, "request is larger than "
                                             f"{cap // (1024 * 1024)}MB") from None
    if not body:
        raise page_server.EndpointError(400, "the request body was empty")
    return extension_bridge_service.parse_json(body)


def _refuse_when_cap_spent() -> None:
    name = extension_bridge_service.engine_name_if_ready()
    if not name:
        return
    try:
        translate_run_service.refuse_when_cap_spent(name)
    except ServiceError as e:
        raise page_server.EndpointError(429, e.message) from None


def _images(payload: dict, single: bool, principal) -> dict:
    images = payload.get("images")
    extension_bridge_service.validate_image_batch(single, images)
    _refuse_when_cap_spent()
    return extension_bridge_service.run_images(payload, images, principal=principal)


def _text(payload: dict, principal) -> dict:
    _refuse_when_cap_spent()
    return extension_bridge_service.run_text(payload, principal=principal)


def _require_engine_allowed(request: Request) -> None:
    """The engine is the owner's, set at the PC: a member without engines.paid
    may use it only when it is a free one (an Ollama cloud tag counts as paid)."""
    name = extension_bridge_service.engine_name_if_ready()
    if name:
        require_engines_allowed(request, name,
                                model=page_server.get_translation_config().get("model"))


async def _handle(request: Request, work, uses_engine: bool = False):
    try:
        if uses_engine:
            _require_engine_allowed(request)
        payload = await _json_body(request)
        return _answer(200, await run_in_threadpool(work, payload))
    except page_server.EndpointError as e:
        return _refusal(e)
    except ForbiddenError as e:
        return _refusal(page_server.EndpointError(403, e.message))
    except Exception:
        # A raw traceback can carry paths; the detail stays in the log.
        import applog
        applog.get_logger().warning("Extension bridge request failed", exc_info=True)
        return _refusal(page_server.EndpointError(500, "the app failed to answer this request"))


@router.get("/health", dependencies=[require_device_token()],
            summary="Device token: the titles this person can send into")
async def get_health(request: Request):
    principal = request.state.principal
    return _answer(200, await run_in_threadpool(extension_bridge_service.health_payload, principal))


@router.post("/page", dependencies=[require_device_token()],
             summary="Device token: translate one page image")
async def post_page(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _images(payload, True, principal), uses_engine=True)


@router.post("/pages", dependencies=[require_device_token()],
             summary="Device token: translate several page images")
async def post_pages(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _images(payload, False, principal), uses_engine=True)


@router.post("/text", dependencies=[require_device_token()],
             summary="Device token: translate a block of page text")
async def post_text(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _text(payload, principal), uses_engine=True)


@router.post("/novel", dependencies=[require_device_token()],
             summary="Device token: save a web-novel page's text as a chapter")
async def post_novel(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: novel.save_from_bridge(payload, principal=principal))


@router.post("/title", dependencies=[require_device_token()],
             summary="Device token: find or create the title for a page")
async def post_title(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: titles.resolve_from_bridge(payload, principal=principal))
