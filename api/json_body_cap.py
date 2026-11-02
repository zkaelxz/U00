"""
api/json_body_cap.py -- the size cap on ordinary JSON request bodies.

Upload routes have their own caps (lib.capped_request, RemoteUploadGate); a
JSON route had none, so one request could make the server buffer and parse
a body of any size.
"""

from starlette.requests import Request

from lib.capped_request import BodyTooLarge

# The largest legitimate JSON body is a pasted novel (Reader text allows 8M
# characters, up to 3 bytes each in UTF-8) plus its envelope.
MAX_JSON_BODY_BYTES = 32 * 1024 * 1024
TOO_LARGE_MESSAGE = "That request is too large. The most the server accepts as JSON is 32 MB."


def _is_json(scope) -> bool:
    for name, value in scope.get("headers", ()):
        if name == b"content-type":
            kind = value.split(b";", 1)[0].strip().lower()
            return kind == b"application/json" or kind.endswith(b"+json")
    return False


class JsonBodyCapGate:
    """Pure-ASGI middleware: refuses a JSON body over MAX_JSON_BODY_BYTES
    with 413, by its Content-Length before any of it is read and by the
    bytes that actually arrive, since the header can under-report (chunked
    bodies have none). Other content types keep their routes' own caps."""

    def __init__(self, app, limit: int = MAX_JSON_BODY_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _is_json(scope):
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
