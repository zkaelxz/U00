"""
services/sources_url_service.py -- the Sources "paste any URL" preview for
the API (Discover/Sources/Live spec S-5), plus the one pasted-URL check the
URL routes share.

`check_public_url` runs in the request, before any job or fetch: http(s)
only, no userinfo, at most 2000 characters, and every resolved address
public (lib.url_guard). Errors are fixed strings: a pasted URL
can carry a signed token, so it is never echoed.

`start_preview` wraps sources.front_door.preview in the fixed-id job
`sources_url_preview` (409 while one runs). From another device the
signed-in profile and the browser tier are off (static HTTP only). Its
result lives only in this process (GET /api/sources/jobs/{id}/result):
type, route, platform, title, chapter, counts and notes, all scrubbed, with
`display_url` reduced to scheme+host+path. The fetched HTML and the
ladder record are never returned. A verification page stops it with the
409 hand-off view.
"""

import re
from urllib.parse import urlsplit

import background_jobs
from lib import url_guard
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError)
from services.sources_extension_service import require_url_not_extension_only
from services.sources_registry_service import scrub, safe_url
from services.sources_search_service import (URL_PREVIEW_JOB_ID, error_view, JobFailed,
                                             start_job)
from sources import front_door, generic_import
from sources.http import Cancelled

MAX_URL_LEN = 2000
# Tighter than sources.http's defaults: a pasted URL is anyone's page, and
# R1/R2 are reachable from another device (security review MED-1).
PASTED_MAX_PAGE_BYTES = 5_000_000
PASTED_MAX_IMAGE_BYTES = 15_000_000
PASTED_REQUEST_DEADLINE = 60.0
_BAD_URL = "Paste a public http:// or https:// web address."
_NOT_PUBLIC = "That address is not a public web address, so it was not opened."
_NO_RESOLVE = "The address could not be resolved. Check the URL and your connection."
_CONTENT_TYPES = (front_door.VIDEO, front_door.NOVEL, front_door.COMIC)
HANDOFF_MESSAGE = ("The site showed a verification page, so Baihe stopped; it never tries to "
                   "get past one. Open the site in your own browser and complete it, then try "
                   "again, or paste the page source you saved from your browser.")


def check_public_url(url) -> str:
    """The stripped URL, or InvalidInputError (422) / DependencyUnavailableError
    (503, host doesn't resolve). Never echoes the URL."""
    if not isinstance(url, str):
        raise InvalidInputError(_BAD_URL)
    text = url.strip()
    if not text or len(text) > MAX_URL_LEN or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise InvalidInputError(_BAD_URL)
    try:
        parts = urlsplit(text)
        host = parts.hostname
        parts.port  # noqa: B018 -- raises ValueError on a bad port
    except ValueError:
        raise InvalidInputError(_BAD_URL) from None
    if parts.scheme.lower() not in ("http", "https") or not host or "@" in parts.netloc:
        raise InvalidInputError(_BAD_URL)
    try:
        url_guard.resolve_public(text)
    except url_guard.URLResolveError:
        raise DependencyUnavailableError(_NO_RESOLVE) from None
    except url_guard.UnsafeURLError:
        raise InvalidInputError(_NOT_PUBLIC) from None
    return text


def source_client(url: str, job_id: str):
    """The paced SourceClient a pasted-URL fetch runs through (the matching
    adapter's source name, or the generic one), cancellable from the job,
    under the pasted-URL body caps and deadline (checked between chunks)."""
    client = generic_import.http_client(None, url)
    client.max_page_bytes = PASTED_MAX_PAGE_BYTES
    client.max_image_bytes = PASTED_MAX_IMAGE_BYTES
    client.request_deadline = PASTED_REQUEST_DEADLINE
    client.cancel_check = lambda: background_jobs.is_cancel_requested(job_id)
    return client


def handoff_error(handoff: dict, url: str) -> dict:
    return {"status": 409, "code": ConflictError.code, "message": HANDOFF_MESSAGE,
            "details": {"reason": str((handoff or {}).get("reason") or ""), "handoff": True,
                        "open_url": safe_url(url)}}


_ANY_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def without_urls(err: dict) -> dict:
    """An error view whose message names no URL at all (not even the
    scheme+host+path form _scrub keeps): a pasted URL is never echoed."""
    out = dict(err)
    out["message"] = _ANY_URL.sub("[url]", str(out.get("message") or "")) or "The request failed."
    return out


def fail_job(job_id: str, kind: str, err: dict):
    err = without_urls(err)
    background_jobs.set_result(job_id, {"kind": kind, "error": err})
    raise JobFailed(err["message"])


def _route(p) -> str:
    if p.adapter and p.chapter_id:
        return "chapter"
    if p.adapter and p.series_id:
        return "series"
    if p.content_type == front_door.VIDEO:
        return "video"
    return "page"


def _int_or_none(value):
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def preview_view(p, url: str) -> dict:
    return {
        "kind": "url_preview",
        "content_type": p.content_type if p.content_type in _CONTENT_TYPES else front_door.UNKNOWN,
        "route": _route(p),
        "platform": scrub(p.platform or "") or "",
        "title": scrub(p.title or "") or "",
        "chapter": scrub(p.chapter or "") or "",
        "chapter_id": scrub(str(p.chapter_id)) if p.chapter_id else None,
        "language": p.language or "",
        "chapter_count": _int_or_none(p.chapter_count),
        "adapter": p.adapter or None,
        "series_id": scrub(str(p.series_id)) if p.series_id else None,
        "text_length": _int_or_none(p.text_length),
        "image_count": _int_or_none(p.image_count),
        "notes": [scrub(str(n)) for n in (p.notes or [])],
        "display_url": safe_url(url),
    }


def _preview_job(job_id: str, url: str, local: bool):
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    background_jobs.update_progress(job_id, 0.1, "Looking at the page...")
    try:
        p = front_door.preview(url, client=source_client(url, job_id),
                               allow_signed_in=local, allow_browser=local,
                               cancel_check=cancel_check)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except Exception as e:
        fail_job(job_id, "url_preview", error_view(e))
    lr = p.ladder
    if lr is not None and getattr(lr, "handoff", None):
        fail_job(job_id, "url_preview", handoff_error(lr.handoff, url))
    background_jobs.set_result(job_id, preview_view(p, url))


def start_preview(url, local: bool = True) -> dict:
    """Starts `sources_url_preview` after the public-address check. `local`
    (a request at this PC) allows the signed-in profile and the browser."""
    url = check_public_url(url)
    require_url_not_extension_only(url)
    return start_job(URL_PREVIEW_JOB_ID, _preview_job, URL_PREVIEW_JOB_ID, url, bool(local),
                  description="Sources URL preview")
