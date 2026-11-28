"""Who the current API request (or queue start) acts for: the per-request principal holder."""

import contextlib
import contextvars

# Set by api.server's ActingPrincipalMiddleware and filled by api.auth's
# dependencies. The holder is a dict (not the principal itself) because
# FastAPI runs sync dependencies and handlers in threadpool copies of the
# request's context: a set() there wouldn't reach the handler, but a
# mutation of the shared holder does. Threads a job starts get a fresh
# context, so a job started by a job has no owner.
_ACTING: contextvars.ContextVar[dict | None] = contextvars.ContextVar("baihe_acting_principal", default=None)


def bind_request():
    """Start a request's holder; returns the token for unbind_request."""
    return _ACTING.set({})


def unbind_request(token) -> None:
    _ACTING.reset(token)


@contextlib.contextmanager
def as_app():
    """Code in the block acts for nobody, as a background thread does: a job it starts has
    no starter, so no member's job caps count it."""
    token = _ACTING.set(None)
    try:
        yield
    finally:
        _ACTING.reset(token)


def note_acting_principal(principal) -> None:
    holder = _ACTING.get()
    if holder is not None:
        holder["principal"] = principal


def acting_principal():
    """The principal of the API request (or queue start) this code runs
    for, or None: the local owner, auth off, or a background thread. A job
    that needs its starter's principal captures it here before it starts."""
    holder = _ACTING.get()
    return holder.get("principal") if holder else None


def acting_user_id():
    """The signed-in user this code runs for, or None (the local owner,
    auth off, a background thread)."""
    principal = acting_principal()
    return None if principal is None else principal.get("user_id")


def acting_device():
    """A coarse name for the device this request came from ("Chrome on
    Android", "Browser extension"), or None when unknown: auth off, a
    background thread, or a queued item started later."""
    principal = acting_principal()
    if not principal:
        return None
    if principal.get("device_token_id") is not None:
        return "Browser extension"
    label = principal.get("device_label")
    return label if label and label != "Unknown device" else None
