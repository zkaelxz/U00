"""
api/json_body_cap.py -- the size cap on every request body but a multipart upload.

Upload routes have their own caps (lib.capped_request, RemoteUploadGate); a
JSON route had none, so one request could make the server buffer and parse
a body of any size. FastAPI reads a body-model route's whole body before it
looks at the Content-Type, so the cap can't trust that header to say JSON.
"""

from starlette.requests import Request

from lib.capped_request import BodyTooLarge

# The largest legitimate JSON body is a pasted novel (Reader text allows 8M
# characters, up to 3 bytes each in UTF-8) plus its envelope.
MAX_JSON_BODY_BYTES = 32 * 1024 * 1024
TOO_LARGE_MESSAGE = "That request is too large. The most the server accepts as JSON is 32 MB."


def json_body_matchers(app) -> list:
    """(compiled path regex, methods) for every route that takes a JSON body
    model. FastAPI reads such a route's whole body whatever its Content-Type,
    so a multipart label must not let a body past the cap there."""
    from fastapi import params
    from starlette.routing import compile_path
    from api.auth import iter_route_declarations
    matchers = []
    for route, path, methods, _decls in iter_route_declarations(app):
        body = getattr(getattr(route, "dependant", None), "body_params", None) or ()
        if body and not any(isinstance(f.field_info, params.Form) for f in body):
            matchers.append((compile_path(path)[0], methods))
    return matchers


def _is_multipart(scope) -> bool:
    for name, value in scope.get("headers", ()):
        if name == b"content-type":
            return value.split(b";", 1)[0].strip().lower() == b"multipart/form-data"
    return False


class JsonBodyCapGate:
    """Pure-ASGI middleware: refuses a body over MAX_JSON_BODY_BYTES with
    413, by its Content-Length before any of it is read and by the bytes
    that actually arrive, since the header can under-report (chunked bodies
    have none). Multipart uploads keep their routes' own caps, except on a
    route that takes a JSON body model (`json_routes_fn`)."""

    def __init__(self, app, limit: int = MAX_JSON_BODY_BYTES, json_routes_fn=None):
        self.app = app
        self.limit = limit
        self._json_routes_fn = json_routes_fn
        self._json_routes = None

    def _takes_json(self, scope) -> bool:
        if self._json_routes is None:
            self._json_routes = self._json_routes_fn() if self._json_routes_fn else []
        path, method = scope.get("path", ""), scope.get("method", "")
        return any(rx.match(path) and method in methods for rx, methods in self._json_routes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (_is_multipart(scope) and not self._takes_json(scope)):
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        try:
            declared = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            declared = 0
        if declared > self.limit:
            return await self._refuse(scope, receive, send)
        seen, started = 0, False

        async def counting():
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise BodyTooLarge()
            return message

        async def tracking(message):
            nonlocal started
            started = True
            await send(message)

        try:
            await self.app(scope, counting, tracking)
        except BodyTooLarge:
            # Past the first response byte a status can't change; the
            # connection is dropped by the error instead.
            if started:
                raise
            await self._refuse(scope, receive, send)

    async def _refuse(self, scope, receive, send):
        from api.auth import _json_refusal
        await _json_refusal(413, "too_large", TOO_LARGE_MESSAGE)(scope, receive, send)
