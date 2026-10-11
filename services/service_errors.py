"""Compatibility front door: the error classes live in `lib/errors.py`.
Kept because several hundred call sites import them from here; new code
imports `lib.errors` directly."""
from lib.errors import (  # noqa: F401
    ConflictError,
    DependencyUnavailableError,
    ExtensionOnlyError,
    ForbiddenError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    RateLimitedError,
    ServiceError,
    UnauthenticatedError,
    UnsupportedOperationError,
)
