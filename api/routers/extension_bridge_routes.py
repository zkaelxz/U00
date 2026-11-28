"""
api/routers/extension_bridge_routes.py -- the browser extension's routes for
a household member's own computer, behind a per-device token
(`Authorization: Bearer baihe_dt_...`, never a session or cookie).

The same work as the PC-only bridge in `services/page_server.py` (that module's
functions are called, so there is one pipeline), with the caller's rights:
a title is sent into only when the member can edit it, the title list shows
only what they can see, the monthly spending cap applies, and the only URLs
fetched are the picture addresses of a no-save Quick read (`fetch_url`), public
hosts only (services/quick_read_fetch.py), plus the video link of `/video` and the stream link of `/live`, which the PC's
yt-dlp downloads or captures for Quick video translate (public hosts only, never the saved logins). The PC's own extension keeps using
the loopback bridge with the shared token.

Answers keep the bridge's `{"error": text}` shape so the extension reads
both the same way. Bodies are counted as they arrive and capped at the
bridge's own limit, since the proxy's small default body cap is lifted for
`/api/ext-bridge/pages` and `/page`.
"""

import asyncio
import contextlib
import json

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from services import page_server
from api import after_import_plan
from api.auth import engine_for_caller, holds, require_cloud_model_allowed, require_device_token, require_engines_allowed
from lib.capped_request import BodyTooLarge, capped, check_content_length
from lib.errors import InvalidInputError
from services import extension_bridge_service, quick_read_runs
from services import extension_novel_service as novel
from services import extension_title_service as titles
from services import (grab_last_page_service, live_service, model_readiness_service, page_run_service,
                      quick_video_service,
                      sources_tracking_service, translate_run_service)
from engine_backends.shared import redact_secrets
from lib.errors import (ConflictError, DependencyUnavailableError, ForbiddenError, NotFoundError,
                        RateLimitedError, ServiceError, UnsupportedOperationError)
from starlette.exceptions import HTTPException as StarletteHTTPException

router = APIRouter(prefix="/api/ext-bridge", tags=["extension"])

_NO_STORE = {"Cache-Control": "no-store"}


def _answer(status: int, payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=_NO_STORE)


def _refusal(error: page_server.EndpointError) -> JSONResponse:
    return _answer(error.status, error.answer())


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
        translate_run_service.refuse_when_cap_spent(
            name, False, page_server.get_translation_config().get("model"))
    except ServiceError as e:
        raise page_server.EndpointError(429, e.message) from None


def _images(payload: dict, single: bool, principal, write=None) -> dict | None:
    images = payload.get("images")
    extension_bridge_service.validate_image_batch(single, images)
    _refuse_when_cap_spent()
    if write is not None:
        return extension_bridge_service.streamed_lines(payload, images, write, principal=principal)
    return extension_bridge_service.run_images(payload, images, principal=principal)


# A caller that asks for it hears a line this often while its pages wait their turn, so a long
# queue is not mistaken for a dead PC by the phone's read timeout. After the limit with nothing
# real to send the stream ends, and the caller asks again for what it did not get.
_HEARTBEAT_SECONDS = 15
_HEARTBEAT_LIMIT_SECONDS = 20 * 60


class _WatchedStream(StreamingResponse):
    """Calls `on_close()` however the answer ends, including when the caller goes away before
    the body was first read."""

    def __init__(self, *args, on_close, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_close()


async def _streamed(work, heartbeat: bool = False, run_id: str = "") -> StreamingResponse:
    """Runs `work(write)` on a worker thread and answers with the lines it writes as they come.
    The answer starts only with the first line, so a refusal raised before it (a bad batch, the
    spending cap, no such title) is re-raised here and becomes the usual error answer. With
    `heartbeat`, the answer starts after `_HEARTBEAT_SECONDS` at the latest and a refusal after
    that comes as an error line instead. With `run_id`, a Quick read run is told when its answer
    is lost before the end (extension_bridge_service.stream_closed)."""
    loop = asyncio.get_running_loop()
    ended = False

    stream = extension_bridge_service.stream_opened(run_id) if run_id else 0

    def closed(lost: bool):
        if run_id:
            extension_bridge_service.stream_closed(run_id, stream, lost)
    lines: asyncio.Queue = asyncio.Queue()

    def put(item):
        # A stream that ended at the heartbeat limit may be gone before the worker finishes.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(lines.put_nowait, item)

    def run():
        try:
            work(lambda line: put(("line", line)))
        except BaseException as e:   # handed to the request, which decides what the caller sees
            put(("error", e))
        finally:
            put(("end", None))

    async def next_item():
        """The next queued item, or None when a heartbeat is due."""
        if not heartbeat:
            return await lines.get()
        try:
            return await asyncio.wait_for(lines.get(), _HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            return None

    asyncio.ensure_future(run_in_threadpool(run))
    try:
        first = await next_item()
    except BaseException:
        closed(lost=True)
        raise
    if first is not None and first[0] == "error":
        closed(lost=False)
        raise first[1]

    async def body():
        nonlocal ended
        item = first
        quiet_since = loop.time()
        while True:
            if item is None:
                if loop.time() - quiet_since > _HEARTBEAT_LIMIT_SECONDS:
                    return
                yield b'{"waiting": true}\n'
            elif item[0] == "end":
                ended = True
                return
            elif item[0] == "line":
                quiet_since = loop.time()
                yield json.dumps(item[1]).encode("utf-8") + b"\n"
            else:
                import applog
                applog.get_logger().warning("Extension bridge stream failed", exc_info=item[1])
                refusal = (item[1] if isinstance(item[1], page_server.EndpointError)
                           else page_server.EndpointError(500, "the app failed to answer this request"))
                yield json.dumps({**refusal.answer(), "status": refusal.status}).encode("utf-8") + b"\n"
            item = await next_item()

    return _WatchedStream(body(), media_type=extension_bridge_service.STREAM_CONTENT_TYPE,
                          headers=_NO_STORE, on_close=lambda: closed(lost=not ended))


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


# `extension.send` only lets a member use the extension; what the extension writes into a
# title is still governed by the permissions the People screen gives for it, so taking
# `lines.edit` or `jobs.start` away must stop the extension too.
_WRITE = ("lines.edit",)
_ENGINE_WRITE = ("lines.edit", "jobs.start")


def _require_held(request: Request, permissions) -> None:
    if not all(holds(request, p) for p in permissions):
        raise ForbiddenError("Not allowed.")


async def _handle(request: Request, work, uses_engine: bool = False, needs=(), streamed=None):
    """`streamed(payload, write)`: the work for a request that asks for `stream`."""
    try:
        _require_held(request, needs)
        if uses_engine:
            _require_engine_allowed(request)
        payload = await _json_body(request)
        if streamed is not None and payload.get("stream") is True:
            return await _streamed(lambda write: streamed(payload, write),
                                   heartbeat=payload.get("heartbeat") is True,
                                   run_id=extension_bridge_service.stream_run_id(payload))
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


@router.get("/sites", dependencies=[require_device_token()],
            summary="Device token: the source sites the phone's Grab screen can open")
async def get_sites():
    return _answer(200, await run_in_threadpool(extension_bridge_service.sites_payload))


@router.post("/page", dependencies=[require_device_token()],
             summary="Device token: translate one page image")
async def post_page(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _images(payload, True, principal), uses_engine=True,
                         needs=_ENGINE_WRITE)


@router.post("/pages", dependencies=[require_device_token()],
             summary="Device token: translate several page images")
async def post_pages(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _images(payload, False, principal), uses_engine=True,
                         needs=_ENGINE_WRITE,
                         streamed=lambda payload, write: _images(payload, False, principal, write))


@router.post("/text", dependencies=[require_device_token()],
             summary="Device token: translate a block of page text")
async def post_text(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _text(payload, principal), uses_engine=True,
                         needs=_ENGINE_WRITE)


@router.post("/novel", dependencies=[require_device_token()],
             summary="Device token: save a web-novel page's text as a chapter")
async def post_novel(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: novel.save_from_bridge(payload, principal=principal),
                         needs=_WRITE)


@router.post("/title", dependencies=[require_device_token()],
             summary="Device token: find or create the title for a page")
async def post_title(request: Request):
    principal = request.state.principal
    # Naming a new title is one AI call on the owner's extension engine, so a member who could
    # not send a page through that engine (engine rights, or `jobs.start`) gets the title without it.
    may_spend = holds(request, "jobs.start")
    if may_spend:
        try:
            _require_engine_allowed(request)
        except ForbiddenError:
            may_spend = False
    return await _handle(request, lambda payload: titles.resolve_from_bridge(
        payload, principal=principal, translate_title=may_spend), needs=_WRITE)


@router.post("/capture-summary", dependencies=[require_device_token()],
             summary="Device token: keep a finished capture's summary line in the app log")
async def post_capture_summary(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: extension_bridge_service.record_capture_summary(
        payload, principal=principal), needs=_WRITE)


@router.post("/report", dependencies=[require_device_token()],
             summary="Device token: send a Quick read page that came out wrong to the PC's reports")
async def post_report(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: extension_bridge_service.record_page_report(
        payload, principal=principal), needs=("library.read",))


@router.post("/title-progress", dependencies=[require_device_token()],
             summary="Device token: how far the translating of a title the caller saved to has got")
async def post_title_progress(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _title_progress(payload, principal), needs=("jobs.start",))


def _drama_id(payload: dict) -> int:
    drama_id = payload.get("drama_id")
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or not 1 <= drama_id < 2**63:
        raise page_server.EndpointError(400, "drama_id is required")
    return drama_id


def _title_progress(payload: dict, principal) -> dict:
    drama_id = _drama_id(payload)
    try:
        return page_run_service.phone_progress(principal, drama_id)
    except NotFoundError as e:
        raise page_server.EndpointError(404, e.message) from None


@router.post("/title-translate", dependencies=[require_device_token()],
             summary="Device token: queue the translating of a novel title the caller saved chapters to")
async def post_title_translate(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _title_translate(request, payload, principal),
                         needs=_ENGINE_WRITE)


def _title_translate(request: Request, payload: dict, principal) -> dict:
    drama_id = _drama_id(payload)
    new_chapters = payload.get("new_chapters")
    if new_chapters is not None and (isinstance(new_chapters, bool) or not isinstance(new_chapters, int)
                                     or not 1 <= new_chapters <= 500):
        raise page_server.EndpointError(400, "new_chapters must be a whole number from 1 to 500")
    try:
        if page_run_service.after_import_kind(principal, drama_id) != "novel":
            raise page_server.EndpointError(422, "Only a novel title translates after a phone save.")
        plan = after_import_plan.novel_plan(request, drama_id)
        return page_run_service.start_after_import(
            principal, drama_id, plan.engine, plan.lines, at_pc=False, model=plan.model, names=plan.names,
            follow_import=False, add_chapters=True, new_chapters=new_chapters)
    except ServiceError as e:
        # 409 is the only answer the phone keeps quiet about (already running, or no new chapters to translate).
        status = (404 if isinstance(e, NotFoundError) else 403 if isinstance(e, ForbiddenError)
                  else 409 if isinstance(e, ConflictError)
                  else 503 if isinstance(e, DependencyUnavailableError) else 422)
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None


@router.post("/follow", dependencies=[require_device_token()],
             summary="Device token: watch the series of a Grab page for new chapters")
async def post_follow(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _follow(payload, principal), needs=("sources.import",))


def _follow(payload: dict, principal) -> dict:
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise page_server.EndpointError(400, "url is required")
    try:
        return sources_tracking_service.follow_from_page(url, principal=principal)
    except ServiceError as e:
        status = 422 if isinstance(e, InvalidInputError) else 404 if isinstance(e, NotFoundError) else 409
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None


@router.post("/last-page", dependencies=[require_device_token()],
             summary="Device token: remember the page this account is reading on a site")
async def post_last_page(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _remember_page(payload, principal))


@router.post("/last-pages", dependencies=[require_device_token()],
             summary="Device token: the page this account last read on each site")
async def post_last_pages(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: grab_last_page_service.places(_account(principal)))


def _account(principal) -> int:
    # user 0 means "the PC" in sources.db, so a token without an account must not land there.
    user_id = (principal or {}).get("user_id")
    if not user_id:
        raise page_server.EndpointError(403, "Not allowed.")
    return int(user_id)


def _remember_page(payload: dict, principal) -> dict:
    try:
        return grab_last_page_service.remember(_account(principal), payload.get("url"), payload.get("title"))
    except InvalidInputError as e:
        raise page_server.EndpointError(422, e.message) from None


@router.post("/cancel", dependencies=[require_device_token()],
             summary="Device token: stop working on the rest of a batch of pages")
async def post_cancel(request: Request):
    return await _handle(request, extension_bridge_service.cancel_run)


@router.post("/quick-reads", dependencies=[require_device_token()],
             summary="Device token: the Quick read runs the caller has working on the PC")
async def post_quick_reads(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: {"runs": quick_read_runs.list_runs(principal=principal)})


@router.post("/quick-reads/drop", dependencies=[require_device_token()],
             summary="Device token: drop one Quick read run (run_id) or all of the caller's (no run_id)")
async def post_quick_reads_drop(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _drop_quick_reads(payload, principal))


def _drop_quick_reads(payload: dict, principal) -> dict:
    run_id = payload.get("run_id")
    if run_id is None:
        return quick_read_runs.drop_all(principal=principal)
    try:
        dropped = quick_read_runs.drop_run(str(run_id), principal=principal)
    except NotFoundError as e:
        raise page_server.EndpointError(404, e.message) from None
    return {"dropped_runs": 1, "dropped_pages": dropped["dropped_pages"]}


_VIDEO_STATUS = ((InvalidInputError, 422), (NotFoundError, 404), (ConflictError, 409),
                 (DependencyUnavailableError, 503), (UnsupportedOperationError, 400),
                 (RateLimitedError, 429))
_VIDEO_NEEDS = ("media.import_url", "jobs.start")


def _send_video(payload: dict, principal, holds_paid: bool) -> dict:
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise page_server.EndpointError(400, "url is required")
    try:
        started = quick_video_service.start(principal, url.strip(), None, False,
                                            holds_paid=holds_paid, at_pc=False)
    except ForbiddenError:
        raise
    except ServiceError as e:
        # Plain refusals (no profile set, bad link, title limit) are for the person to read.
        status = next((code for cls, code in _VIDEO_STATUS if isinstance(e, cls)), 500)
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None
    return {"drama_id": started["drama_id"]}


@router.post("/video", dependencies=[require_device_token()],
             summary="Device token: send a video page's link to Quick video translate")
async def post_video(request: Request):
    principal = request.state.principal
    holds_paid = holds(request, "engines.paid")
    return await _handle(request, lambda payload: _send_video(payload, principal, holds_paid),
                         needs=_VIDEO_NEEDS)


def _start_live(request: Request, payload: dict, principal) -> dict:
    """Starts Live on a stream page's link with the PC's own defaults: the form's language
    default Whisper model and chunking, the engine a member may use, no delayed player and
    no saved logins, as for any other device."""
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise page_server.EndpointError(400, "url is required")
    options = live_service.form_options()
    language = payload.get("source_language") or options["defaults"]["source_language"]
    if not isinstance(language, str):
        raise page_server.EndpointError(422, "source_language must be a language code")
    whisper_size = options["language_whisper_defaults"].get(language) or options["defaults"]["whisper_size"]
    chunk = live_service.LANGUAGE_CHUNK_DEFAULTS.get(language, {})
    engine, model = engine_for_caller(request, None, live_service.default_engine(), None)
    require_engines_allowed(request, engine, model=model)
    require_cloud_model_allowed(request, (engine, model))
    try:
        model_readiness_service.require_ready(False, model_readiness_service.for_whisper_size, whisper_size)
        started = live_service.start_session(
            url.strip(), source_language=language, whisper_size=whisper_size,
            segment_seconds=chunk.get("segment_seconds", options["defaults"]["segment_seconds"]),
            overlap_seconds=chunk.get("overlap_seconds", options["defaults"]["overlap_seconds"]),
            engine=engine, model=model, use_gpu=live_service.gpu_available(),
            use_saved_cookies=False, principal=principal)
    except ForbiddenError:
        raise
    except ServiceError as e:
        status = next((code for cls, code in _VIDEO_STATUS if isinstance(e, cls)), 500)
        raise page_server.EndpointError(status, redact_secrets(e.message)) from None
    return {"session_id": started["session_id"], "joined": bool(started.get("joined"))}


@router.post("/live", dependencies=[require_device_token()],
             summary="Device token: start Live captions on a stream page's link")
async def post_live(request: Request):
    principal = request.state.principal
    return await _handle(request, lambda payload: _start_live(request, payload, principal),
                         needs=("media.import_url",))
