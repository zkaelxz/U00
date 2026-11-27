"""
api/remote_uploads.py -- uploads from away from the PC (owner decision
2026-10-21): an admin may only while the PC's remote-uploads switch is
on (settings_service.remote_uploads_on); a member may send audio/video into
their own titles only with the opt-in `media.upload_member` right (owner
decision 2026-11-27), held to the smaller member limit. The Settings upload
limit applies before the body is read. The upload routes declare
`media.upload` (`media.upload_member` for the two media routes); deletes and the routes that make the PC fetch or run
something stay local_only(). Rules: docs/remote-access-permissions-core.md.
"""

import logging
import tempfile
import threading

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from services import auth_service

log = logging.getLogger(__name__)

UPLOAD_PERMISSION = auth_service.UPLOAD_PERMISSION
MEMBER_UPLOAD_PERMISSION = auth_service.MEMBER_UPLOAD_PERMISSION
# Room for the multipart framing and the small form fields next to the file.
_FORM_OVERHEAD = 1024 * 1024


def away_principal(request: Request, principal):
    """The principal as this request may use it: away from the PC a
    media.upload is dropped unless the switch is on. At the PC it is kept.
    Applies on both listeners, so the old single-listener sign-in setup is
    covered too. None stays None. The other admin-only permissions are not
    behind this switch."""
    from api.auth import is_local_request
    if (principal is None or UPLOAD_PERMISSION not in principal["permissions"]
            or is_local_request(request)):
        return principal
    from services import settings_service
    if settings_service.remote_uploads_on():
        return principal
    # An admin holds the member upload right through the admin flag, so it goes with
    # media.upload; a member's own grant is not behind the switch.
    return dict(principal, permissions=[p for p in principal["permissions"]
                                        if p not in (UPLOAD_PERMISSION, MEMBER_UPLOAD_PERMISSION)])


def _matchers(app, permissions) -> list:
    from starlette.routing import compile_path
    from api.auth import iter_route_declarations
    return [(compile_path(path)[0], methods)
            for _r, path, methods, decls in iter_route_declarations(app)
            if any(kind == "permission" and set(permissions) & set(label.split(" + "))
                   for kind, label in decls)]


def upload_matchers(app) -> list:
    """(compiled path regex, methods) for every route that needs media.upload
    or the member upload right."""
    return _matchers(app, (UPLOAD_PERMISSION, MEMBER_UPLOAD_PERMISSION))


def member_upload_matchers(app) -> list:
    """The upload routes a member holding the member upload right may use."""
    return _matchers(app, (MEMBER_UPLOAD_PERMISSION,))


class TempRoom:
    """Bytes still to arrive for the uploads from away in flight. A File()
    route spools its body to the temp folder before any handler checks free
    space, so each upload is refused up front unless the temp drive can hold
    its Content-Length on top of what the others in flight have yet to write.
    What they have already written shows in the drive's free space."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = 0

    def reserve(self, length: int) -> None:
        """InvalidInputError (the upload's own no-room refusal) when it won't fit."""
        from services import media_upload_service
        with self._lock:
            media_upload_service.check_room_for(tempfile.gettempdir(), self._pending + length)
            self._pending += length

    def release(self, length: int) -> None:
        with self._lock:
            self._pending -= length


temp_room = TempRoom()


def capped_upload(request: Request, receive, principal=None):
    """(refusal or None, receive). Some upload routes take the file as a
    FastAPI File(), which spools the whole body to disk before the handler
    checks its size, so a chunked body or a missing Content-Length is
    refused (422), a longer one too (413), and the body is counted as it
    arrives, since the header can under-report it; past the limit the
    request reads as a client disconnect."""
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from api.auth import _json_refusal
    from lib.capped_request import check_content_length
    from lib.errors import InvalidInputError
    from services import media_upload_service
    limit = media_upload_service.upload_limit_bytes(principal) + _FORM_OVERHEAD
    try:
        check_content_length(request, limit, "the upload", media_upload_service.too_large_message(
            limit - _FORM_OVERHEAD, media_upload_service.is_member_caller(principal)))
    except InvalidInputError as exc:
        return _json_refusal(422, exc.code, exc.message), receive
    except StarletteHTTPException as exc:
        return _json_refusal(413, "too_large", exc.detail), receive
    seen = 0

    async def counting():
        nonlocal seen
        message = await receive()
        if message.get("type") == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                return {"type": "http.disconnect"}
        return message

    return None, counting


class RemoteUploadGate:
    """Pure-ASGI middleware, installed with auth on, inside EarlyAuthGate
    (which has already refused a request with no valid session or CSRF
    token). For an upload route reached from away from the PC it refuses,
    before the body is read, a caller who may not upload here (403: a
    member without the right, or an admin while the switch is off), then applies
    capped_upload and temp_room. At the PC it does nothing."""

    def __init__(self, app, upload_fn, member_upload_fn=None):
        self.app = app
        self._upload_fn = upload_fn
        self._member_upload_fn = member_upload_fn or (lambda: [])
        self._upload = None
        self._member_upload = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._is_upload(scope):
            return await self.app(scope, receive, send)
        from api.auth import _GENERIC_403, _authenticate, _json_refusal, is_local_request
        from lib.errors import ForbiddenError, InvalidInputError, UnauthenticatedError
        request = Request(scope, receive)
        if is_local_request(request):
            return await self.app(scope, receive, send)
        try:
            # Off the event loop for the same reason as EarlyAuthGate.
            principal = await run_in_threadpool(_authenticate, request)
        except (UnauthenticatedError, ForbiddenError) as exc:
            status = 401 if isinstance(exc, UnauthenticatedError) else 403
            return await _json_refusal(status, exc.code, exc.message)(scope, receive, send)
        needed = MEMBER_UPLOAD_PERMISSION if self._is_member_route(scope) else UPLOAD_PERMISSION
        if needed not in principal["permissions"]:
            return await _json_refusal(403, "forbidden", _GENERIC_403)(scope, receive, send)
        refusal, receive = capped_upload(request, receive, principal)
        if refusal is not None:
            return await refusal(scope, receive, send)
        # capped_upload checked it is a number; never negative, or it would free others' room.
        length = max(0, int(request.headers["content-length"]))
        try:
            temp_room.reserve(length)
        except InvalidInputError as exc:
            return await _json_refusal(422, exc.code, exc.message)(scope, receive, send)
        left = length

        async def counted():
            nonlocal left
            message = await receive()
            if message.get("type") == "http.request":
                arrived = min(left, len(message.get("body", b"")))
                left -= arrived
                temp_room.release(arrived)
            return message

        try:
            await self._audit(principal, scope)
            return await self.app(scope, counted, send)
        finally:
            temp_room.release(left)

    @staticmethod
    async def _audit(principal, scope) -> None:
        """One activity-log row per upload accepted from away, written before the
        body is read so a stolen admin cookie leaves a trace even if the upload
        fails. The path names the title; the file's name is never logged."""
        from starlette.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(auth_service.write_audit, principal.get("user_id"),
                                    "upload.away", f"{scope.get('method', '')} {scope.get('path', '')}")
        except Exception:
            log.warning("could not write the away-upload audit row", exc_info=True)

    def _is_member_route(self, scope) -> bool:
        if self._member_upload is None:
            self._member_upload = self._member_upload_fn()
        path, method = scope.get("path", ""), scope.get("method", "")
        return any(rx.match(path) and method in methods for rx, methods in self._member_upload)

    def _is_upload(self, scope) -> bool:
        if self._upload is None:
            self._upload = self._upload_fn()
        path, method = scope.get("path", ""), scope.get("method", "")
        return any(rx.match(path) and method in methods for rx, methods in self._upload)
