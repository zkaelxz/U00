"""
api/error_handlers.py -- one JSON error shape for every failure.

Every non-2xx response from this API has the body

    {"error": {"code": "<stable_code>", "message": "<human text>",
               "details": <optional structured extra>}}

so the React client (and later the browser extension) can branch on
`code` without parsing prose. The codes, and where they come from:

| HTTP | code                    | raised by                                   |
|------|-------------------------|---------------------------------------------|
| 422  | validation_error        | FastAPI request validation, `InvalidInputError` |
| 404  | not_found               | `NotFoundError`, unknown route              |
| 400  | unsupported_operation   | `UnsupportedOperationError`                 |
| 409  | conflict                | `ConflictError`                             |
| 503  | dependency_unavailable  | `DependencyUnavailableError`                |
| 500  | application_error       | any other `ServiceError`                    |
| 500  | internal_error          | anything unexpected (a bug)                 |

What never reaches a client: a traceback, an exception's raw text for
an unexpected error (it can carry a path or a key), or the offending
input value on a validation error. Unexpected errors are logged in full
to the app's own log file (`applog`) instead, where they already go for
the Streamlit UI. Known messages are still passed through
`translate_engines.redact_secrets` in case a service slipped one in.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError, ServiceError, UnsupportedOperationError)

_STATUS_BY_ERROR = (
    (InvalidInputError, 422),
    (NotFoundError, 404),
    (UnsupportedOperationError, 400),
    (ConflictError, 409),
    (DependencyUnavailableError, 503),
    (ServiceError, 500),
)

_CODE_BY_HTTP_STATUS = {404: "not_found", 405: "unsupported_operation"}


def _redact(text: str) -> str:
    # Imported lazily: translate_engines is a large module and nothing
    # else in the API needs it at import time.
    from translate_engines import redact_secrets
    return redact_secrets(text)


def error_body(code: str, message: str, details=None) -> dict:
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


async def _service_error(request: Request, exc: ServiceError):
    status = next(s for cls, s in _STATUS_BY_ERROR if isinstance(exc, cls))
    return JSONResponse(status_code=status,
                        content=error_body(exc.code, _redact(exc.message), exc.details))


async def _validation_error(request: Request, exc: RequestValidationError):
    # Only where and what -- never the rejected input itself, which could
    # be anything a caller pasted (including a key).
    details = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
    return JSONResponse(status_code=422,
                        content=error_body("validation_error", "The request is invalid.", details))


async def _http_error(request: Request, exc: StarletteHTTPException):
    code = _CODE_BY_HTTP_STATUS.get(exc.status_code, "application_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(status_code=exc.status_code, content=error_body(code, message),
                        headers=getattr(exc, "headers", None))


async def _unexpected_error(request: Request, exc: Exception):
    try:
        import applog
        applog.get_logger().exception("API: unhandled error on %s %s",
                                      request.method, request.url.path)
    except Exception:
        pass
    return JSONResponse(status_code=500, content=error_body(
        "internal_error", "Something went wrong inside Baihe. Details are in the app log."))


def install_error_handlers(app: FastAPI):
    app.add_exception_handler(ServiceError, _service_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
