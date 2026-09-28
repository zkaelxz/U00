"""
services/service_errors.py -- the error vocabulary every application
service raises, so each UI can turn a failure into its own kind of
message without knowing which module raised it.

Streamlit shows these as `st.error(...)`; the FastAPI layer (`api/`)
maps each class to an HTTP status code and a stable `code` string (see
`api/error_handlers.py`). A service should raise one of these for any
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
