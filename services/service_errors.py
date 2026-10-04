"""
services/service_errors.py -- the error vocabulary every application
service raises, so each UI can turn a failure into its own kind of
message without knowing which module raised it.

The FastAPI layer (`api/`) maps each class to an HTTP status code and a
stable `code` string (see `api/error_handlers.py`). A service should raise one of these for any
failure it *expects* (bad input, missing record, optional package not
installed); anything else is a bug and surfaces as a generic internal
error rather than leaking its traceback to a client.

`message` is shown to people, so it must never contain an API key, a
token, or a filesystem path -- the API layer runs it through
`translate_engines.redact_secrets` as a second line of defence, but the
first line is not putting one there.
"""


class ServiceError(Exception):
    """A known, user-explainable failure. Maps to HTTP 500
    `application_error` when nothing more specific applies."""

    code = "application_error"

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidInputError(ServiceError):
    """The caller asked for something malformed (an unknown filter value,
    an out-of-range number). HTTP 422 `validation_error`."""

    code = "validation_error"


class NotFoundError(ServiceError):
    """The record the caller named doesn't exist. HTTP 404 `not_found`."""

    code = "not_found"


class UnsupportedOperationError(ServiceError):
    """Well-formed, but not something this record/engine can do (e.g.
    a manga project asked to transcribe audio). HTTP 400
    `unsupported_operation`."""

    code = "unsupported_operation"


class DependencyUnavailableError(ServiceError):
    """An optional package, model or external tool the operation needs
    isn't installed/reachable -- the same situation Diagnostics reports.
    HTTP 503 `dependency_unavailable`."""

    code = "dependency_unavailable"


class ConflictError(ServiceError):
    """The request is well-formed and the record exists, but the record's
    current state won't allow it right now -- e.g. a job is already
    running for this drama, so a second start would collide with it.
    HTTP 409 `conflict`."""

    code = "conflict"


class ForbiddenError(ServiceError):
    """The caller is understood but this action is not allowed for them
    (e.g. a local-only operation asked for remotely). HTTP 403 `forbidden`.
    Keep the message generic so it leaks no reason."""

    code = "forbidden"


class RateLimitedError(ServiceError):
    """Too many requests of this kind right now (e.g. a burst of network
    job starts). HTTP 429 `rate_limited`."""

    code = "rate_limited"


class UnauthenticatedError(ServiceError):
    """No valid session. HTTP 401 `unauthenticated`. Generic message only."""

    code = "unauthenticated"
