"""
lib/errors.py -- the error vocabulary every application
service raises, so each UI can turn a failure into its own kind of
message without knowing which module raised it.

The FastAPI layer (`api/`) maps each class to an HTTP status code and a
stable `code` string (see `api/error_handlers.py`). A service should raise one of these for any
failure it *expects* (bad input, missing record, optional package not
installed); anything else is a bug and surfaces as a generic internal
error rather than leaking its traceback to a client.

`message` is shown to people, so it must never contain an API key, a
token, or a filesystem path -- the API layer runs it through
`engine_backends.shared.redact_secrets` as a second line of defence, but the
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


class NewerDatabaseError(ServiceError):
    """library.db was last opened by a newer Baihe than this one, so this
    build would misread columns it doesn't know. Raised at startup, where it
    is printed and logged; it names the backups folder, so it is not meant
    for an API response."""

    code = "newer_database"


class UnfinishedRestoreError(ServiceError):
    """The library folder is missing and several restores left a previous
    library aside, so which one to use can't be decided. Raised at startup,
    where it is printed and logged."""

    code = "unfinished_restore"


class UnsupportedOperationError(ServiceError):
    """Well-formed, but not something this record/engine can do (e.g.
    a manga project asked to transcribe audio). HTTP 400
    `unsupported_operation`."""

    code = "unsupported_operation"


class ExtensionOnlyError(UnsupportedOperationError):
    """The person marked this source as working only through the browser
    extension, so the app does not try to read it itself. HTTP 400
    `extension_only`."""

    code = "extension_only"


class DependencyUnavailableError(ServiceError):
    """An optional package, model or external tool the operation needs
    isn't installed/reachable -- the same situation Diagnostics reports.
    HTTP 503 `dependency_unavailable`."""

    code = "dependency_unavailable"


class MissingPackageError(DependencyUnavailableError):
    """A Python package an engine or tool needs isn't installed. The message
    is fixed text and the package name rides in `details`, so a UI can name
    the cause (not "add a key") without any path or traceback reaching it."""

    MESSAGE = ("A tool or package this needs is not installed or not reachable. "
               "See Diagnostics.")

    def __init__(self, package: str):
        super().__init__(self.MESSAGE, details={"reason": "missing_package", "package": package})


class MissingKeyError(DependencyUnavailableError):
    """An engine needs an API key (or a service a token) that isn't set. Same
    503 as any missing dependency, but `details` lets a UI say "add the key"
    instead of "install something"."""

    def __init__(self, engine: str, kind: str = "key"):
        super().__init__(f"No {engine} {kind} is configured. Add it in Settings.",
                         details={"reason": f"no_{kind}", "engine": engine})


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


# What a bare exception class says when its message is empty (a timeout, an assertion, a
# plain RuntimeError()): the class name alone tells a person nothing, and the frontend strips it.
_EMPTY_MESSAGE_HINTS = {
    "TimeoutError": "Something took too long and was stopped.",
    "MemoryError": "The PC ran out of memory.",
    "ConnectionError": "A connection closed unexpectedly.",
    "ConnectionResetError": "A connection closed unexpectedly.",
    "BrokenPipeError": "A connection closed unexpectedly.",
    "EOFError": "A file or stream ended earlier than expected.",
    "OSError": "A file or device could not be used.",
    "PermissionError": "A file or folder could not be used (permission).",
}
_EMPTY_MESSAGE_DEFAULT = "It stopped without saying why."


def exception_text(exc_type: str, message: str) -> str:
    """"ValueError: bad value", or for an empty message a plain sentence in its place,
    so the Jobs list never has only a class name to show."""
    if str(message).strip():
        return f"{exc_type}: {message}"
    hint = _EMPTY_MESSAGE_HINTS.get(exc_type, _EMPTY_MESSAGE_DEFAULT)
    return f"{exc_type}: {hint} Details are in the app log."
