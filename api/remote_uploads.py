"""
api/remote_uploads.py -- uploads from away from the PC (owner decision
2026-10-21): only an admin may, only while the PC's remote-uploads switch is
on (settings_service.remote_uploads_on), and the Settings upload limit
applies before the body is read. The upload routes declare
`media.upload`; deletes and the routes that make the PC fetch or run
something stay local_only(). Rules: docs/remote-access-permissions-core.md.
"""

import logging

from starlette.requests import Request

from services import auth_service

log = logging.getLogger(__name__)

UPLOAD_PERMISSION = auth_service.UPLOAD_PERMISSION
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
    return dict(principal, permissions=[p for p in principal["permissions"]
                                        if p != UPLOAD_PERMISSION])


def upload_matchers(app) -> list:
    """(compiled path regex, methods) for every route that needs media.upload."""
    from starlette.routing import compile_path
    from api.auth import iter_route_declarations
    return [(compile_path(path)[0], methods)
            for _r, path, methods, decls in iter_route_declarations(app)
            if any(kind == "permission" and UPLOAD_PERMISSION in label.split(" + ")
                   for kind, label in decls)]


def capped_upload(request: Request, receive):
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
    limit = media_upload_service.max_upload_bytes() + _FORM_OVERHEAD
    try:
        check_content_length(request, limit, "the upload", media_upload_service.too_large_message(
            limit - _FORM_OVERHEAD))
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
    member, or an admin while the switch is off), then applies
    capped_upload. At the PC it does nothing."""

    def __init__(self, app, upload_fn):
        self.app = app
        self._upload_fn = upload_fn
        self._upload = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._is_upload(scope):
            return await self.app(scope, receive, send)
        from api.auth import _GENERIC_403, _authenticate, _json_refusal, is_local_request
        from lib.errors import ForbiddenError, UnauthenticatedError
        request = Request(scope, receive)
        if is_local_request(request):
            return await self.app(scope, receive, send)
        try:
            principal = _authenticate(request)
        except (UnauthenticatedError, ForbiddenError) as exc:
            status = 401 if isinstance(exc, UnauthenticatedError) else 403
            return await _json_refusal(status, exc.code, exc.message)(scope, receive, send)
        if UPLOAD_PERMISSION not in principal["permissions"]:
            return await _json_refusal(403, "forbidden", _GENERIC_403)(scope, receive, send)
        refusal, receive = capped_upload(request, receive)
        if refusal is not None:
            return await refusal(scope, receive, send)
        await self._audit(principal, scope)
        return await self.app(scope, receive, send)

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

    def _is_upload(self, scope) -> bool:
        if self._upload is None:
            self._upload = self._upload_fn()
        path, method = scope.get("path", ""), scope.get("method", "")
        return any(rx.match(path) and method in methods for rx, methods in self._upload)
