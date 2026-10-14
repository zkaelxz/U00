"""
lib/capped_request.py -- the size checks every upload route runs before it
reads a request body.

A chunked body is refused (its size is unknown until it has all arrived),
the Content-Length is checked against the route's cap before any of the
body is read, and the body stream itself is counted, because the header can
under-report what follows (h11 lets a Content-Length plus Transfer-Encoding
body through). Each route keeps its own messages; the status codes are
fixed here: 422 for a missing length, a chunked body or an unreadable form,
413 past the cap.
"""

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request

from lib.errors import InvalidInputError

UPLOAD_NEEDS_LENGTH = "An upload needs a Content-Length."


class BodyTooLarge(Exception):
    pass


def capped(request: Request, limit: int) -> Request:
    """The same request with a receive channel that counts body bytes and
    stops once more than `limit` arrive, independent of any header."""
    receive, seen = request.receive, 0

    async def counting():
        nonlocal seen
        message = await receive()
        if message.get("type") == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise BodyTooLarge()
        return message

    return Request(request.scope, counting)


def check_content_length(request: Request, cap: int, noun: str, too_large: str,
                         missing: str = UPLOAD_NEEDS_LENGTH):
    """Refuses a chunked body, a missing or unreadable Content-Length (422)
    and one over `cap` (413), before any of the body is read."""
    if "transfer-encoding" in request.headers:
        raise InvalidInputError(f"Send {noun} with a Content-Length, not chunked.")
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise InvalidInputError(missing) from None
    if length > cap:
        raise StarletteHTTPException(413, too_large)


async def read_capped_form(request: Request, cap: int, noun: str, too_large: str,
                           bad_form: str, **form_kwargs):
    """The multipart form, read under `cap` after check_content_length; a
    form the parser refuses is InvalidInputError(bad_form). `form_kwargs`
    (max_files, max_fields, max_part_size) go to Request.form. The caller
    closes the form."""
    check_content_length(request, cap, noun, too_large)
    try:
        return await capped(request, cap).form(**form_kwargs)
    except BodyTooLarge:
        raise StarletteHTTPException(413, too_large) from None
    except (MultiPartException, StarletteHTTPException):
        # Starlette re-raises the parser's MultiPartException as a 400.
        raise InvalidInputError(bad_form) from None
