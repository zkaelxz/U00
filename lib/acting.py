"""Who the current API request (or queue start) acts for: the per-request principal holder."""

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
