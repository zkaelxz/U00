"""
services/sources_import_service.py -- importing from a source into an
existing drama for the API (Discover/Sources/Live spec S-4, chapter import;
S-5, novel text from a pasted URL).

`start_chapter_import` takes chapter IDS only, never chapter objects or
URLs from the client: the job re-fetches the series' chapter list itself
(the adapter builds every URL) and keeps the requested ids, matched by id,
never by position. An id the list doesn't have is "not_found"; an id
already imported into this drama (sources.store.imported_chapters) is
"skipped", so re-importing is idempotent per (chapter, drama).

It never creates a drama (POST /api/dramas is `admin.library`; the client
creates one first). Writes are the pipeline's append-only ones: comic pages
into `<drama>/pages/` plus page rows, novel text appended to the raw-novel
file. No `db.save_lines`. The job id is per drama (`sourceimport_<id>`,
the same one pipeline.start_import claims for Streamlit and the
chapter-check auto-import) and is in background_jobs.DRAMA_JOB_PREFIXES so
a delete refuses while it runs; pipeline.add_page_images also locks and
creates page files exclusively on its own.

`start_url_import` (S-5, thin slice: novel text only) checks the pasted URL
in the request (sources_url_service.check_public_url), then the job runs
adaptive.import_novel and appends the text to the drama's raw-novel file.
The LLM fallback is off unless the request opted in (parity SO09: the
engine is built in the request by sources_extraction_service, key on the
PC). When the extraction needs review (low confidence, the request asked
for `review`, or Sources diagnostics mode is on) nothing is written: the
job opens a Review extraction for the drama instead (parity SO10,
sources_extraction_service.open_review). From another device the signed-in profile and the browser tier
are off.

`start_comic_url_import` (parity SO06) is the comic counterpart: the job
runs adaptive.import_comic (same opt-in LLM fallback) and adds the kept
pages to a manhua/manga/manhwa drama through pipeline.add_page_images, the
Scanlate upload path, under the shared page-upload rules
(services/page_import_limits.py): downloads go through the same paced,
guarded client (every redirect hop re-checked) with the per-image byte cap,
and a per-import budget (generic_import.DownloadBudget: at most
MAX_FILES_PER_IMPORT download attempts and MAX_IMPORT_BYTES in all; image or
generic binary content types; PNG/JPEG/WebP only and at most
MAX_IMAGE_PIXELS, both read from the header before any decode). Each kept
image then has its EXIF orientation applied and a webtoon strip is cut into
pages. An image over a cap is skipped with a reason, never failing the
chapter. The result lists the images left out and why (the Streamlit
"skipped as page furniture" list). A run from another device doesn't write
the site's shared "seen on other chapters" image memory (`remember`).
When the extraction needs review nothing is written (a review opens, as
for novel text).

Results live in this process only; read them with
sources_search_service.get_job_result (GET /api/sources/jobs/{id}/result).
Text is scrubbed, URLs reduced to scheme+host+path.
"""

import background_jobs
import db
from services import drama_service, ownership_service
from services import page_import_limits as limits
from services import sources_extraction_service as extraction
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from services.sources_registry_service import _import_supported, _scrub, safe_url
from services.sources_search_service import (IMPORT_JOB_PREFIX, MAX_ID_LEN, _enabled_source,
                                             _error_view, _JobFailed, _plain_text, _series_id,
                                             _start)
from services.sources_url_service import (check_public_url, fail_job, handoff_error,
                                          source_client)
from sources import adaptive, chapter_order, generic_import, ladder, pipeline, registry, store
from sources.generic_import import DownloadBudget
from sources.http import Cancelled
from sources.models import AccessTier

MAX_CHAPTERS = 200
MAX_SKIPPED_LISTED = 100
COMIC_MEDIA_TYPES = ("manhua", "manga", "manhwa")
NOVEL_MEDIA_TYPES = ("novel",)
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."


def import_job_id(drama_id: int) -> str:
    return f"{IMPORT_JOB_PREFIX}{int(drama_id)}"


def _plain_id(value, what: str) -> str:
    """Same rule as a series id: no URL, slash, backslash, "@", ":", ".."
    or whitespace, so an id can never steer an adapter to another host."""
    text = _plain_text(value, what, MAX_ID_LEN)
    if (text[0] in "/\\" or any(c in text for c in "\\@:") or ".." in text
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text)):
        raise InvalidInputError(f"{what} must be a plain id.")
    return text


def _chapter_ids(values) -> list:
    if not isinstance(values, (list, tuple)):
        raise InvalidInputError("chapter_ids must be a list of chapter ids.")
    out, seen = [], set()
    for v in values:
        if not isinstance(v, str):
            raise InvalidInputError("chapter_ids must be a list of chapter ids.")
        cid = _plain_id(v, "chapter_id")
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    if not out:
        raise InvalidInputError("Pick at least one chapter.")
    if len(out) > MAX_CHAPTERS:
        raise InvalidInputError(f"At most {MAX_CHAPTERS} chapters per import.")
    return out


def _require_drama(drama_id, principal=None) -> dict:
    """A drama `principal` can't see (auth B2) is a 404 like a missing one."""
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("drama_id must be a positive integer.")
    drama = db.get_drama(drama_id)
    if drama is None or not ownership_service.can_edit_drama(principal, drama_id):
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _require_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY, details={"job_id": import_job_id(drama_id)})


# ---------------------------------------------------------------------------
# Chapter import (S-4)
# ---------------------------------------------------------------------------

def _outcome(row: dict) -> dict:
    out = {"chapter_id": str(row.get("chapter_id")), "title": _scrub(row.get("title") or "")}
    if row.get("skipped"):
        out["outcome"] = "skipped"
    elif row.get("ok"):
        out["outcome"] = "imported"
        if "pages" in row:
            out["pages"] = int(row["pages"])
        if "chars" in row:
            out["chars"] = int(row["chars"])
    else:
        out["outcome"] = "failed"
        out["error"] = _scrub(row.get("error") or "") or "Import failed."
    return out


def _import_result(chapters: list, cancelled: bool, handoff) -> dict:
    counts = {k: sum(1 for c in chapters if c["outcome"] == k)
              for k in ("imported", "skipped", "failed")}
    return {"kind": "chapter_import", "chapters": chapters,
            "imported_count": counts["imported"], "skipped_count": counts["skipped"],
            "failed_count": counts["failed"], "partial": counts["failed"] > 0,
            "cancelled": bool(cancelled), "handoff": handoff}


def _chapter_import_job(job_id: str, name: str, series_id: str, chapter_ids: list,
                        drama_id: int):
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    adapter = registry.get_adapter(name, cancel_check=cancel_check)
    try:
        ladder.check_terms(name, adapter.capabilities())
        background_jobs.update_progress(job_id, 0.01, "Loading the chapter list...")
        listed = adapter.get_chapters(series_id)
    except Cancelled:
        background_jobs.set_result(job_id, _import_result([], True, None))
        return
    except Exception as e:
        err = _error_view(e, name)
        background_jobs.set_result(job_id, {"kind": "chapter_import", "error": err})
        raise _JobFailed(err["message"]) from None
    by_id = {}
    for ch in listed:
        by_id.setdefault(str(ch.chapter_id), ch)
    requested = set(chapter_ids)
    wanted = [ch for ch in chapter_order.sort_chapters_grouped(list(by_id.values()))
              if str(ch.chapter_id) in requested]
    already = store.imported_chapter_ids(name, series_id, drama_id)
    skip = {c for c in chapter_ids if c in already}
    pipeline.run_import_job(job_id, name, wanted, drama_id, adapter=adapter, skip_ids=skip)
    raw = (background_jobs.get_status(job_id) or {}).get("result") or {}
    rows = {}
    for row in raw.get("chapters") or []:
        rows.setdefault(str(row.get("chapter_id")), row)
    chapters = []
    for ch in wanted:  # matched by chapter_id; a chapter never reached is left out
        row = rows.get(str(ch.chapter_id))
        if row is not None:
            chapters.append(_outcome(row))
    chapters += [{"chapter_id": c, "title": "", "outcome": "not_found"}
                 for c in chapter_ids if c not in by_id]
    handoff = None
    if raw.get("handoff"):
        h = raw["handoff"]
        handoff = {"reason": str(h.get("reason") or ""), "handoff": True,
                   "open_url": safe_url(h.get("url")),
                   "chapter_id": str(h.get("chapter_id") or "") or None}
    background_jobs.set_result(job_id, _import_result(chapters, raw.get("cancelled"), handoff))


def start_chapter_import(name, series_id, chapter_ids, drama_id, principal=None) -> dict:
    """Starts `sourceimport_<drama_id>`. 404 unknown source or drama; 400
    source off or unable to import; 422 bad ids or the drama's media type
    doesn't match (comic sources need manhua/manga/manhwa, text sources a
    novel); 409 while any job runs for the drama."""
    name = str(name or "")
    cls = _enabled_source(name)
    series_id = _series_id(series_id)
    ids = _chapter_ids(chapter_ids)
    adapter = cls()
    if not _import_supported(adapter) or not adapter.supports("get_chapters"):
        raise UnsupportedOperationError("This source can't import chapters.",
                                        details={"reason": "NOT_SUPPORTED"})
    drama = _require_drama(drama_id, principal)
    media = (drama.get("media_type") or "").lower()
    if adapter.supports("get_pages"):
        if media not in COMIC_MEDIA_TYPES:
            raise InvalidInputError("Comic chapters import into a manhua, manga or manhwa "
                                    "drama. Pick one of those, or create one first.")
    elif media not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel chapters import into a novel drama. Pick one, "
                                "or create one first.")
    _require_idle(drama_id)
    job_id = import_job_id(drama_id)
    return _start(job_id, _chapter_import_job, job_id, name, series_id, ids, drama_id,
                  description=f"Import {len(ids)} chapter(s) from {name}")


# ---------------------------------------------------------------------------
# Novel text from a pasted URL (S-5)
# ---------------------------------------------------------------------------

_NO_TEXT = "No chapter text was found on that page."


def _url_fail(job_id: str, err: dict):
    fail_job(job_id, "url_import", err)


def _url_import_job(job_id: str, url: str, drama_id: int, local: bool, engine=None,
                    review: bool = False):
    background_jobs.update_progress(job_id, 0.1, "Reading the page...")
    try:
        res, report = adaptive.import_novel(url, engine=engine, client=source_client(url, job_id),
                                            allow_signed_in=local, allow_browser=local,
                                            hold_profiles=not local)
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
    why = extraction.review_reason(report, review)
    if why or not text.strip():
        opened = extraction.open_review(drama_id, "novel", url, getattr(res.ladder, "html", ""),
                                        report.data, report, why or extraction.WHY_LOW_CONFIDENCE,
                                        pc_only=_signed_in(res.ladder))
        background_jobs.set_result(job_id, {"kind": "url_import", "needs_review": True,
                                            "char_count": len(text), "review_open": opened})
        return
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 0.9, "Saving the text...")
    extraction.drop_review(drama_id)
    pipeline.save_novel_text(drama_id, text, append=True, heading=res.title)
    background_jobs.set_result(job_id, {"kind": "url_import", "needs_review": False,
                                        "char_count": len(text), "review_open": False})


def start_url_import(url, drama_id, local: bool = True, principal=None,
                     ai_engine: str = None, review: bool = False) -> dict:
    """Starts `sourceimport_<drama_id>`: novel text from one pasted URL,
    appended to a novel drama's raw-novel text. `ai_engine` (a name from
    sources_extraction_service.resolve_ai_engine_name, None = off) is the
    LLM fallback. 422 bad/private URL or not a novel drama; 503 the host
    doesn't resolve or the engine has no key; 404 no drama; 409 while a job
    runs for the drama."""
    url = check_public_url(url)
    drama = _require_drama(drama_id, principal)
    if (drama.get("media_type") or "").lower() not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel text imports into a novel drama. Pick one, "
                                "or create one first.")
    _require_idle(drama_id)
    engine = extraction.build_ai_engine(ai_engine)
    job_id = import_job_id(drama_id)
    return _start(job_id, _url_import_job, job_id, url, drama_id, bool(local), engine,
                  bool(review), description="Import novel text from a pasted URL")


# ---------------------------------------------------------------------------
# Comic pages from a pasted URL (parity SO06)
# ---------------------------------------------------------------------------

_NO_PAGES = "No comic pages were found on that page."


def _signed_in(lr) -> bool:
    """Whether the page was read through the saved signed-in browser."""
    return getattr(lr, "tier", None) == AccessTier.AUTHENTICATED_BROWSER.value


def skipped_view(candidates) -> list:
    """The images left out, and why (scheme+host+path only, signed path
    segments blanked, scrubbed)."""
    return [{"display_url": extraction.display_url(c.url), "reason": _scrub(c.reject_reason or "") or "not a page"}
            for c in list(candidates)[:MAX_SKIPPED_LISTED]]


def _comic_result(needs_review: bool, pages_added: int, rejected, review_open=False) -> dict:
    rejected = list(rejected)
    return {"kind": "comic_import", "needs_review": needs_review, "pages_added": pages_added,
            "skipped": skipped_view(rejected), "skipped_count": len(rejected),
            "review_open": bool(review_open)}


def _comic_url_import_job(job_id: str, url: str, drama_id: int, local: bool, engine=None,
                          review: bool = False):
    background_jobs.update_progress(job_id, 0.1, "Reading the page and checking each image...")
    budget = DownloadBudget(limits.MAX_FILES_PER_IMPORT, limits.MAX_IMPORT_BYTES,
                            max_image_pixels=limits.MAX_IMAGE_PIXELS,
                            allowed_formats=limits.ALLOWED_IMAGE_TYPES)
    client = source_client(url, job_id)
    client.max_image_bytes = limits.MAX_IMAGE_BYTES
    try:
        res, report = adaptive.import_comic(url, engine=engine, client=client,
                                            allow_signed_in=local, allow_browser=local,
                                            budget=budget, hold_profiles=not local,
                                            remember=local)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except generic_import.NoContentFound:
        fail_job(job_id, "comic_import", {"status": 422, "code": InvalidInputError.code,
                                          "message": _NO_PAGES,
                                          "details": {"reason": "NO_CONTENT"}})
    except Exception as e:
        fail_job(job_id, "comic_import", _error_view(e))
    if res.ladder is not None and getattr(res.ladder, "handoff", None):
        fail_job(job_id, "comic_import", handoff_error(res.ladder.handoff, url))
    why = extraction.review_reason(report, review)
    if why or not res.images:
        opened = extraction.open_review(drama_id, "comic", url, getattr(res.ladder, "html", ""),
                                        report.data, report, why or extraction.WHY_LOW_CONFIDENCE,
                                        candidates=list(res.images) + list(res.rejected),
                                        pc_only=_signed_in(res.ladder))
        background_jobs.set_result(job_id, _comic_result(True, 0, res.rejected, opened))
        return
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 0.9, "Adding the pages...")
    extraction.drop_review(drama_id)
    pages, skipped = extraction.prepare_pages(res.images)
    n = pipeline.add_page_images(drama_id, pages)
    background_jobs.set_result(job_id, _comic_result(False, n, list(skipped) + list(res.rejected)))


def start_comic_url_import(url, drama_id, local: bool = True, principal=None,
                           ai_engine: str = None, review: bool = False) -> dict:
    """Starts `sourceimport_<drama_id>`: the page images of one pasted comic
    chapter URL, added to a manhua/manga/manhwa drama's pages (Scanlate).
    Same checks and errors as start_url_import."""
    url = check_public_url(url)
    drama = _require_drama(drama_id, principal)
    if (drama.get("media_type") or "").lower() not in COMIC_MEDIA_TYPES:
        raise InvalidInputError("Comic pages import into a manhua, manga or manhwa drama. "
                                "Pick one of those, or create one first.")
    _require_idle(drama_id)
    engine = extraction.build_ai_engine(ai_engine)
    job_id = import_job_id(drama_id)
    return _start(job_id, _comic_url_import_job, job_id, url, drama_id, bool(local), engine,
                  bool(review), description="Import comic pages from a pasted URL")
