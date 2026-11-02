"""
api/json_body_cap.py -- the size cap on every request body but a multipart upload
to a route that takes one.

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


def multipart_matchers(app) -> list:
    """(compiled path regex, methods) for every route that takes a multipart
    body: a Form()/File() parameter, or a body it reads by hand and documents
    as multipart/form-data. FastAPI or a dependency reads any other route's
    whole body whatever its Content-Type, so only these may skip the cap."""
    from fastapi import params
    from starlette.routing import compile_path
    from api.auth import iter_route_declarations
    matchers = []
    for route, path, methods, _decls in iter_route_declarations(app):
        body = getattr(getattr(route, "dependant", None), "body_params", None) or ()
        declared = ((getattr(route, "openapi_extra", None) or {}).get("requestBody") or {}).get(
            "content") or {}
        if any(isinstance(f.field_info, params.Form) for f in body) \
                or "multipart/form-data" in declared:
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
    have none). A multipart body keeps its route's own cap only on a route
    that takes one (`multipart_routes_fn`)."""

    def __init__(self, app, limit: int = MAX_JSON_BODY_BYTES, multipart_routes_fn=None):
        self.app = app
        self.limit = limit
        self._multipart_routes_fn = multipart_routes_fn
        self._multipart_routes = None

    def _takes_multipart(self, scope) -> bool:
        if self._multipart_routes is None:
            self._multipart_routes = (self._multipart_routes_fn() if self._multipart_routes_fn
                                      else [])
        path, method = scope.get("path", ""), scope.get("method", "")
        return any(rx.match(path) and method in methods for rx, methods in self._multipart_routes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (_is_multipart(scope) and self._takes_multipart(scope)):
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
