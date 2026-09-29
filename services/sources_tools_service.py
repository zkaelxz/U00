"""
services/sources_tools_service.py -- the Sources page's remaining tools for
the API (feature inventory §8: SO02, SO03, SO08, SO16).

  SO02 `start_preflight`: "will this site work?" as the fixed-id job
       `sources_url_preflight`. sources.preflight.preflight fetches the page
       once through the same paced, capped SourceClient the URL preview uses
       (sources_url_service.source_client) after the same public-address
       check; from another device, static HTTP only.
  SO03 `preview_pasted` / `start_pasted_import`: continue after a
       verification page from the page source the person pasted. Parsing
       only; nothing is fetched (generic_import's USER_ASSISTED tier). The
       preview is synchronous (front_door.classify_html); the import is the
       drama's `sourceimport_<id>` job, like POST /api/sources/url/import.
  SO08 `start_identify_media`: media resources on a page nothing else
       recognizes (adaptive.identify_media), as the fixed-id job
       `sources_url_identify`. It fetches the page (as above) unless the
       pasted page source is given. No AI engine is used (SO09 is separate):
       an ambiguous page lists every resource for the person to pick. A
       resource's full URL is kept only for a job started at this PC (it is
       what the PC-only video download then takes); another device sees
       scheme+host+path.
  SO16 `recent_extractions`: the pasted-URL attempts from the shared
       access-attempt log, URLs reduced to scheme+host+path, text scrubbed.

Results are read with sources_search_service.get_job_result
(GET /api/sources/jobs/{job_id}/result), which knows these job ids.
"""

import threading
import uuid

import background_jobs
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)
from services.sources_import_service import (NOVEL_MEDIA_TYPES, _require_drama, _require_idle,
                                             _url_fail, import_job_id)
from services.sources_registry_service import _scrub, safe_url
from services.sources_search_service import _error_view, _JobFailed, _start
from services.sources_url_service import (check_public_url, fail_job, handoff_error,
                                          preview_view, source_client, without_urls)
from sources import adaptive, front_door, generic_import, pipeline, preflight
from sources.http import Cancelled

PREFLIGHT_JOB_ID = "sources_url_preflight"
IDENTIFY_JOB_ID = "sources_url_identify"
# A pasted page source, in bytes of the request body (the same cap a fetched
# pasted-URL page has, sources_url_service.PASTED_MAX_PAGE_BYTES).
MAX_PASTED_HTML_BYTES = 5_000_000
MAX_EXTRACTIONS = 50
_MEDIA_KINDS = ("video", "audio", "manifest", "embed", "subtitle")
_NO_TEXT = "No chapter text was found in the pasted page."
_UNREADABLE = "The page could not be read."


def job_ids() -> tuple:
    return (PREFLIGHT_JOB_ID, IDENTIFY_JOB_ID)


def _pasted_html(html) -> str:
    if not isinstance(html, str) or not html.strip():
        raise InvalidInputError("Paste the page's source first.")
    if len(html.encode("utf-8", "surrogatepass")) > MAX_PASTED_HTML_BYTES:
        raise InvalidInputError("The pasted page is too large (at most 5 MB).")
    return html


def _no_url(text) -> str:
    """Scrubbed text naming no URL at all (a pasted URL is never echoed)."""
    text = _scrub(text) if text else ""
    return without_urls({"message": text})["message"] if text else ""


def _lines(values) -> list:
    return [x for x in (_no_url(str(v)) for v in (values or [])) if x]


# ---------------------------------------------------------------------------
# SO02: preflight
# ---------------------------------------------------------------------------

def preflight_view(pf, url: str) -> dict:
    return {
        "kind": "url_preflight",
        "ok": bool(pf.ok),
        "verdict": _no_url(pf.verdict),
        "permitted": bool(pf.permitted),
        "reachable": bool(pf.reachable),
        "content_type": pf.content_type if pf.content_type in (
            front_door.VIDEO, front_door.NOVEL, front_door.COMIC) else front_door.UNKNOWN,
        "tier": _scrub(str(pf.tier or "")) or "",
        "adapter": pf.adapter or None,
        "title": _scrub(pf.title or "") or "",
        "text_chars": int(pf.text_chars or 0),
        "images": int(pf.images or 0),
        "confidence": _scrub(pf.confidence or "") or "",
        "warnings": _lines(pf.warnings),
        "lines": _lines(pf.lines),
        "display_url": safe_url(url),
    }


def _preflight_job(job_id: str, url: str, local: bool):
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    background_jobs.update_progress(job_id, 0.1, "Checking the site...")
    try:
        pf = preflight.preflight(url, client=source_client(url, job_id),
                                 allow_signed_in=local, allow_browser=local,
                                 cancel_check=cancel_check)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except Exception as e:
        fail_job(job_id, "url_preflight", _error_view(e))
    background_jobs.set_result(job_id, preflight_view(pf, url))


def start_preflight(url, local: bool = True) -> dict:
    """Starts `sources_url_preflight` after the public-address check. One
    fetch; writes nothing. 422 bad/private URL, 503 no DNS, 409 running."""
    url = check_public_url(url)
    return _start(PREFLIGHT_JOB_ID, _preflight_job, PREFLIGHT_JOB_ID, url, bool(local),
                  description="Sources site check")


# ---------------------------------------------------------------------------
# SO03: continue from pasted page source
# ---------------------------------------------------------------------------

def preview_pasted(url, html) -> dict:
    """What the pasted page is, as the URL preview's result shape. Parses
    only: nothing is fetched and nothing is written."""
    url = check_public_url(url)
    html = _pasted_html(html)
    p = front_door.classify_html(url, html)
    view = preview_view(p, url)
    view["pasted"] = True
    return view


def _pasted_import_job(job_id: str, url: str, html: str, drama_id: int):
    background_jobs.update_progress(job_id, 0.1, "Reading the pasted page...")
    try:
        res, report = adaptive.import_novel(url, engine=None, client=source_client(url, job_id),
                                            user_html=html, allow_signed_in=False,
                                            allow_browser=False)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except generic_import.NoContentFound:
        _url_fail(job_id, {"status": 422, "code": InvalidInputError.code, "message": _NO_TEXT,
                           "details": {"reason": "NO_CONTENT"}})
    except Exception as e:
        _url_fail(job_id, _error_view(e))
    if res.ladder is not None and getattr(res.ladder, "handoff", None):
        _url_fail(job_id, handoff_error(res.ladder.handoff, url))
    text = res.text or ""
    if report.needs_review or not text.strip():
        background_jobs.set_result(job_id, {"kind": "url_import", "needs_review": True,
                                            "char_count": len(text)})
        return
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 0.9, "Saving the text...")
    pipeline.save_novel_text(drama_id, text, append=True, heading=res.title)
    background_jobs.set_result(job_id, {"kind": "url_import", "needs_review": False,
                                        "char_count": len(text)})


def start_pasted_import(url, html, drama_id, principal=None) -> dict:
    """Starts `sourceimport_<drama_id>`: the chapter text in the pasted page
    source, appended to a novel drama's raw-novel text (as
    start_url_import, but read from the paste instead of the site)."""
    url = check_public_url(url)
    html = _pasted_html(html)
    drama = _require_drama(drama_id, principal)
    if (drama.get("media_type") or "").lower() not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel text imports into a novel drama. Pick one, "
                                "or create one first.")
    _require_idle(drama_id)
    job_id = import_job_id(drama_id)
    return _start(job_id, _pasted_import_job, job_id, url, html, drama_id,
                  description="Import novel text from a pasted page")


# ---------------------------------------------------------------------------
# SO08: identify media on an unknown page
# ---------------------------------------------------------------------------

def _is_web(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _resource_view(r: dict) -> dict:
    full = str(r.get("resource_url") or "")
    return {
        "index": int(r.get("index") or 0),
        "kind": str(r.get("kind") or ""),
        "role": str(r.get("role") or ""),
        "language": _scrub(r.get("language")) or None,
        "label": _no_url(r.get("label")) or None,
        "display_url": safe_url(full) if _is_web(full) else "",
        # Only an http(s) resource can go to the video download.
        "downloadable": _is_web(full) and r.get("kind") != "subtitle",
    }


def identify_view(data, report, run_id: str) -> dict:
    resources = [_resource_view(r) for r in ((data or {}).get("resources") or [])
                 if r.get("kind") in _MEDIA_KINDS]
    return {
        "kind": "media_identify",
        "run_id": run_id,
        "found": bool(resources),
        "needs_review": bool(report.needs_review),
        "reason": _no_url(report.reason),
        "protection": [_scrub(str(p)) for p in ((data or {}).get("protection")
                                                 or report.protection or [])],
        "resources": resources,
    }


# The last identify run's full resource URLs, by index: {"run_id", "urls"}.
# Kept out of the job result (which any caller who can see the job reads,
# with URLs cut to scheme+host+path); only resource_url() hands one out,
# and its route is PC-only, like the video download it feeds.
_RESOURCES = {"run_id": None, "urls": {}}
_RESOURCES_LOCK = threading.Lock()


def resource_url(run_id, index) -> dict:
    """The full URL of one resource from the latest identify run. 404 when
    the run was replaced (or never ran in this process) or the index has
    no downloadable resource."""
    if isinstance(index, bool) or not isinstance(index, int):
        raise InvalidInputError("index must be an integer.")
    with _RESOURCES_LOCK:
        current, urls = _RESOURCES["run_id"], dict(_RESOURCES["urls"])
    if not run_id or run_id != current or index not in urls:
        raise NotFoundError("That resource is no longer listed; identify the page again.")
    return {"run_id": current, "index": index, "resource_url": urls[index]}


def _identify_job(job_id: str, url: str, html, local: bool):
    run_id = uuid.uuid4().hex[:12]
    with _RESOURCES_LOCK:
        _RESOURCES.update(run_id=None, urls={})
    background_jobs.update_progress(job_id, 0.1, "Looking for media on the page...")
    try:
        if html is None:
            lr = generic_import.fetch_page(url, source_client(url, job_id), None,
                                           allow_signed_in=local, allow_browser=local)
            if getattr(lr, "handoff", None):
                fail_job(job_id, "media_identify", handoff_error(lr.handoff, url))
            if not lr.ok or not lr.html:
                fail_job(job_id, "media_identify",
                         {"status": 503, "code": DependencyUnavailableError.code,
                          "message": _UNREADABLE, "details": {"reason": "UNREACHABLE"}})
            html = lr.html
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        data, report = adaptive.identify_media(url, html, engine=None)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except (background_jobs.JobCancelled, _JobFailed):
        raise
    except Exception as e:
        fail_job(job_id, "media_identify", _error_view(e))
    urls = {int(r.get("index") or 0): str(r.get("resource_url"))
            for r in ((data or {}).get("resources") or [])
            if r.get("kind") in _MEDIA_KINDS and r.get("kind") != "subtitle"
            and _is_web(str(r.get("resource_url") or ""))}
    with _RESOURCES_LOCK:
        _RESOURCES.update(run_id=run_id, urls=urls)
    background_jobs.set_result(job_id, identify_view(data, report, run_id))


def start_identify_media(url, html=None, local: bool = True) -> dict:
    """Starts `sources_url_identify`. With `html` (pasted page source) no
    request is made. Identifies only; downloads and writes nothing."""
    url = check_public_url(url)
    if html is not None:
        html = _pasted_html(html)
    return _start(IDENTIFY_JOB_ID, _identify_job, IDENTIFY_JOB_ID, url, html, bool(local),
                  description="Sources identify media")


# ---------------------------------------------------------------------------
# SO16: pasted-URL diagnostics
# ---------------------------------------------------------------------------

def _access_view(access: dict) -> dict:
    access = access if isinstance(access, dict) else {}
    return {
        "authentication": _scrub(access.get("authentication")) or None,
        "entitlement": _scrub(access.get("entitlement")) or None,
        "technical_protection": _scrub(access.get("technical_protection")) or None,
        "protection_detail": [_scrub(str(x)) for x in (access.get("protection_detail") or [])],
    }


def recent_extractions(limit: int = 15) -> list:
    """Newest first: what each pasted-URL import/identify attempt did."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EXTRACTIONS:
        raise InvalidInputError(f"limit must be 1 to {MAX_EXTRACTIONS}.")
    out = []
    for a in adaptive.recent_extractions(limit=limit):
        conf = a.get("confidence") if isinstance(a.get("confidence"), dict) else {}
        out.append({
            "url": safe_url(a.get("url")),
            "created_at": a.get("created_at"),
            "content_type": _scrub(a.get("content_type")) or "",
            "headline": _scrub(a.get("headline")) or "",
            "tier": _scrub(a.get("tier")) or None,
            "extraction_tier": _scrub(a.get("extraction_tier")) or None,
            "llm_calls": int(a.get("llm_calls") or 0),
            "cache_hit": bool(a.get("cache_hit")),
            "profile": _scrub(adaptive.describe_profile(a.get("profile") or {})) or "",
            "confidence": _scrub(conf.get("bucket")) or None,
            "access": _access_view(a.get("access")) if a.get("access") else None,
            "resource_types": [_scrub(str(x)) for x in (a.get("resource_types") or [])],
            "reason": _scrub(a.get("reason")) or "",
            "lines": [_scrub(str(x)) for x in (a.get("lines") or [])],
        })
    return out
