"""
services/sources_search_service.py -- Sources search and series/chapter
listing for the API (Discover/Sources/Live spec, slice S-3).

Both operations make paced network requests from this PC, so each runs as
a background job (defaults are a 3-8s gap plus occasional 30-90s session
breaks per source) and the caller polls `get_job_result`.

Inputs are source names, a search query and series ids only -- never URLs;
adapters build their own URLs. Everything returned is plain dicts with URLs
reduced to scheme+host+path and free text scrubbed of secrets and paths
(the helpers in services/sources_registry_service.py).

ToS/robots enforcement is OFF by user decision (Step 90; spec Q1), but each
fetch still goes through `ladder.check_terms(...)` (registry.multi_search
calls it per source; the series job calls it before fetching), so turning
that one function back on covers the API too.

Results live only in this process's in-memory job table
(`background_jobs.get_status(...)["result"]`): after a restart, or from
another process, `get_job_result` answers 404.
"""

import threading

import background_jobs
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError, ServiceError,
                                     UnsupportedOperationError)
from services.sources_registry_service import _require_source, _scrub, _scrub_any, safe_url
from sources import chapter_order, ladder, registry
from sources.http import Cancelled, ResponseRefused
from sources.models import (ChallengeDetected, ContentHidden, NotSupportedError, SourceError,
                            SourceUnavailable, TermsProhibited)

SEARCH_JOB_ID = "sources_search"
SERIES_JOB_PREFIX = "sources_series_"
# Chapter import (S-4) and pasted-URL novel import (S-5), one per drama.
IMPORT_JOB_PREFIX = "sourceimport_"
# Paste-a-URL preview (S-5), one at a time in the process.
URL_PREVIEW_JOB_ID = "sources_url_preview"
MAX_QUERY_LEN = 200
MAX_ID_LEN = 200


class _JobFailed(Exception):
    """Raised by a job after it stored its structured error, so the job
    ends as "error" with an already-scrubbed message."""


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

def _error_view(exc, source: str = None) -> dict:
    """A SourceError (or anything else) as {status, code, message, details}."""
    msg = _scrub(str(exc)) or type(exc).__name__
    if isinstance(exc, TermsProhibited):
        return {"status": 400, "code": UnsupportedOperationError.code, "message": msg,
                "details": {"reason": "TOS_PROHIBITED"}}
    if isinstance(exc, ContentHidden):
        return {"status": 400, "code": UnsupportedOperationError.code, "message": msg,
                "details": {"reason": "CONTENT_HIDDEN", "hint": "adult_toggle", "source": source}}
    if isinstance(exc, NotSupportedError):
        return {"status": 400, "code": UnsupportedOperationError.code, "message": msg,
                "details": {"reason": "NOT_SUPPORTED"}}
    if isinstance(exc, ChallengeDetected):
        return {"status": 409, "code": ConflictError.code, "message": msg,
                "details": {"reason": exc.reason.value, "handoff": True,
                            "open_url": safe_url(exc.url)}}
    if isinstance(exc, SourceUnavailable):
        return {"status": 503, "code": DependencyUnavailableError.code, "message": msg,
                "details": {"reason": exc.reason.value, "retry_after": exc.retry_after}}
    if isinstance(exc, Cancelled):
        return {"status": 409, "code": ConflictError.code, "message": "Cancelled.",
                "details": {"reason": "CANCELLED"}}
    if isinstance(exc, ResponseRefused):
        status = exc.status if exc.status in _CLASS_BY_STATUS else 500
        return {"status": status, "code": _CLASS_BY_STATUS.get(status, ServiceError).code,
                "message": str(exc), "details": {"reason": "RESPONSE_REFUSED"}}
    if isinstance(exc, SourceError):
        return {"status": 500, "code": ServiceError.code, "message": msg,
                "details": {"reason": exc.reason.value}}
    return {"status": 500, "code": ServiceError.code,
            "message": f"{type(exc).__name__}: {msg}", "details": None}


_CLASS_BY_STATUS = {400: UnsupportedOperationError, 404: NotFoundError, 409: ConflictError,
                    422: InvalidInputError, 503: DependencyUnavailableError}


def _raise_error_view(err: dict):
    cls = _CLASS_BY_STATUS.get(err.get("status"), ServiceError)
    raise cls(err.get("message") or "The source request failed.", details=err.get("details"))


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------

def _plain_text(value, what: str, max_len: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidInputError(f"{what} is required.")
    if len(text) > max_len:
        raise InvalidInputError(f"{what} is too long (at most {max_len} characters).")
    low = text.lower()
    if "://" in low or low.startswith("www."):
        raise InvalidInputError(f"{what} must be a name or id, not a URL.")
    return text


def _series_id(value) -> str:
    """Adapters append a series id to their own base URL, and some urljoin
    it (52shuku), so "//other.host/x" would change the host. Refuse
    anything that could: a leading slash or backslash, backslashes, "@",
    ":", ".." segments, whitespace and control characters."""
    text = _plain_text(value, "series_id", MAX_ID_LEN)
    if (text[0] in "/\\" or any(c in text for c in "\\@:") or ".." in text
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text)):
        raise InvalidInputError("series_id must be a plain id.")
    return text


def _enabled_source(name: str):
    cls = _require_source(name)
    if not registry.is_enabled(name):
        raise UnsupportedOperationError("That source is switched off.")
    return cls


def _start(job_id: str, target, *args, description: str):
    status = background_jobs.get_status(job_id)
    if status and status.get("status") in ("running", "queued"):
        raise ConflictError("A request like this is already running.",
                            details={"job_id": job_id})
    if status:
        background_jobs.clear_job(job_id)
    if not background_jobs.start_job(job_id, target, *args, description=description):
        raise ConflictError("A request like this is already running.",
                            details={"job_id": job_id})
    return {"job_id": job_id}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class _Recording:
    """Wraps an adapter for registry.multi_search so the exception behind
    each per-source failure is kept (multi_search itself keeps only text)."""

    def __init__(self, adapter, errors: dict):
        self._a = adapter
        self._errors = errors
        self.name = adapter.name

    def supports(self, method):
        return self._a.supports(method)

    def capabilities(self):
        return self._a.capabilities()

    def search(self, query):
        try:
            return self._a.search(query)
        except Exception as e:
            self._errors[self.name] = e
            raise


def _search_job(job_id: str, query: str, names):
    cancelled = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    adapters = registry.enabled_adapters(cancel_check=cancelled)
    if names is not None:
        adapters = [a for a in adapters if a.name in names]
    raised = {}
    background_jobs.update_progress(job_id, 0.1, f"Searching {len(adapters)} source(s)...")
    res = registry.multi_search(query, adapters=[_Recording(a, raised) for a in adapters])
    was_cancelled = bool(cancelled())
    errors = {}
    for name, text in res.errors.items():
        exc = raised.get(name)
        if isinstance(exc, Cancelled) or (exc is None and was_cancelled):
            continue
        errors[name] = _error_view(exc, name) if exc is not None else {
            "status": 500, "code": ServiceError.code, "message": _scrub(text), "details": None}
    background_jobs.set_result(job_id, {
        "kind": "search",
        "query": _scrub(query),
        "cancelled": was_cancelled,
        "results": [{
            "title": _scrub(m.title),
            "key": m.key,
            "sources": list(m.sources),
            "entries": [{"source": e.source, "series_id": str(e.series_id),
                         "title": _scrub(e.title), "url": safe_url(e.url),
                         "cover_url": safe_url(e.cover_url)} for e in m.entries],
        } for m in res.results],
        "errors": errors,
        "per_source_counts": dict(res.per_source_counts),
    })


def start_search(query, sources=None) -> dict:
    """Starts the fixed-id `sources_search` job. 409 if one is running; a
    finished one is cleared first. `sources` (names) limits the search to
    those enabled sources; unknown names are 404, switched-off ones 400."""
    query = _plain_text(query, "The search text", MAX_QUERY_LEN)
    names = None
    if sources is not None:
        names = set()
        for n in sources:
            _enabled_source(str(n))
            names.add(str(n))
        if not names:
            raise InvalidInputError("Pick at least one source, or leave the list out.")
    return _start(SEARCH_JOB_ID, _search_job, SEARCH_JOB_ID, query, names,
                  description="Sources search")


# ---------------------------------------------------------------------------
# Series and chapters
# ---------------------------------------------------------------------------

def _series_job(job_id: str, name: str, series_id: str):
    cancelled = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    adapter = registry.get_adapter(name, cancel_check=cancelled)
    try:
        ladder.check_terms(name, adapter.capabilities())
        background_jobs.update_progress(job_id, 0.2, "Loading the series...")
        info = adapter.get_series(series_id) if adapter.supports("get_series") else None
        background_jobs.update_progress(job_id, 0.6, "Loading the chapter list...")
        chapters = chapter_order.sort_chapters_grouped(adapter.get_chapters(series_id))
    except Exception as e:
        err = _error_view(e, name)
        background_jobs.set_result(job_id, {"kind": "series", "source": name,
                                            "series_id": series_id, "error": err})
        raise _JobFailed(err["message"]) from None
    background_jobs.set_result(job_id, {
        "kind": "series",
        "source": name,
        "series_id": series_id,
        "info": None if info is None else {
            "title": _scrub(info.title), "url": safe_url(info.url),
            "cover_url": safe_url(info.cover_url),
            "authors": [_scrub(a) for a in (info.authors or [])],
            "description": _scrub(info.description), "genres": [_scrub(g) for g in (info.genres or [])],
            "status": info.status, "content_type": info.content_type, "language": info.language,
        },
        "chapters": [{"chapter_id": str(c.chapter_id), "title": _scrub(c.title),
                      "group": _scrub(c.group or ""), "url": safe_url(c.url)} for c in chapters],
    })


def start_series(name, series_id) -> dict:
    """Starts `sources_series_<name>`: the series info plus its chapters in
    chapter_order.sort_chapters_grouped order."""
    name = str(name or "")
    cls = _enabled_source(name)
    series_id = _series_id(series_id)
    if not cls().supports("get_chapters"):
        raise UnsupportedOperationError("This source can't list chapters.",
                                        details={"reason": "NOT_SUPPORTED"})
    job_id = SERIES_JOB_PREFIX + name
    # Start and record together, so a poll never pairs this run with the
    # previous run's series.
    with _IDENTITY_LOCK:
        started = _start(job_id, _series_job, job_id, name, series_id,
                         description=f"Sources series ({name})")
        _SERIES_IDENTITY[job_id] = (name, series_id)
    return started


# job_id -> (source, series_id) of the latest series run started here, so a
# running/queued poll can say which series it is for (ids only, no text).
# Written and read under _IDENTITY_LOCK together with the job status.
_SERIES_IDENTITY: dict = {}
_IDENTITY_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def _is_ours(job_id: str, local: bool = False) -> bool:
    from services import sources_signin_service as signin
    from sources.chapter_check import CHECK_JOB_ID
    if job_id in (SEARCH_JOB_ID, URL_PREVIEW_JOB_ID, CHECK_JOB_ID):
        return True
    if signin.is_pc_only_job(job_id):
        # Sign-in and tier-test outcomes are for the owner at the PC.
        return local
    if job_id.startswith(SERIES_JOB_PREFIX):
        return len(job_id) > len(SERIES_JOB_PREFIX)
    return (job_id.startswith(IMPORT_JOB_PREFIX)
            and job_id[len(IMPORT_JOB_PREFIX):].isdigit())


def get_job_result(job_id, local: bool = False) -> dict:
    """{job_id, status, progress, message, result}. 404 when the job is not
    resident in this process (or is a PC-only sign-in/tier-test job and the
    request is not `local`); a failed job raises its mapped error (503
    with retry_after, 409 handoff, 400 terms/hidden/unsupported)."""
    job_id = str(job_id or "")
    with _IDENTITY_LOCK:
        status = background_jobs.get_status(job_id) if _is_ours(job_id, local) else None
        started_for = _SERIES_IDENTITY.get(job_id)
    if not status:
        raise NotFoundError("No such Sources job in this app session.")
    result = status.get("result")
    if status.get("status") == "error":
        err = (result or {}).get("error") if isinstance(result, dict) else None
        _raise_error_view(err or {"status": 500, "message": _scrub(status.get("error"))})
    done = status.get("status") == "done"
    out = {
        "job_id": job_id,
        "status": status.get("status"),
        "progress": status.get("progress"),
        "message": status.get("message"),
        "result": result if done else None,
    }
    if job_id.startswith(SERIES_JOB_PREFIX):
        ident = started_for
        if isinstance(result, dict) and result.get("source"):
            ident = (result.get("source"), result.get("series_id"))
        if ident:
            out["source"], out["series_id"] = ident
    return _scrub_any(out)


def known_chapter_ids(series_result: dict) -> list:
    """Chapter ids to record as already known when a series starts being
    tracked from this fetched result, so the first check doesn't announce
    (or auto-import) the whole back catalogue. Accepts either the
    `get_job_result` payload or its inner `result`. Not wired to tracking."""
    r = series_result or {}
    if isinstance(r.get("result"), dict):
        r = r["result"]
    if r.get("kind") != "series" or r.get("error") or not isinstance(r.get("chapters"), list):
        raise InvalidInputError("That isn't a finished series result.")
    seen, out = set(), []
    for c in r["chapters"]:
        cid = str((c or {}).get("chapter_id") or "")
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out
