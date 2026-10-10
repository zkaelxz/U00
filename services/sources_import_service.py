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
the same one pipeline.start_import claims for the chapter-check
auto-import) and is in background_jobs.DRAMA_JOB_PREFIXES so
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
are off. With `follow_pages` above 1 the job also follows each page's
next-chapter link (novel_follow.follow_novel: same client, host and checks,
each followed address re-checked as public) and always opens a review of
the pages it read instead of writing; the person then imports the pages
they keep, in order. Nothing is recorded per page: like a one-page URL
import, a pasted-URL chain has no chapter ids to mark imported or retry.

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
chapter. The result lists the images left out and why ("skipped as page
furniture"). A run from another device reads the
site's shared "seen on other chapters" image memory but doesn't add to it
(`learn`). One comic import runs at a time in this process
(`start_comic_job`, 409 otherwise), and its pages are prepared and written
one image at a time, each download dropped once written
(sources_extraction_service.write_pages), so at most one import's download
budget plus one image's prepared pages are held in memory; a review's
images are kept on disk.
When the extraction needs review nothing is written (a review opens, as
for novel text).

Results live in this process only; read them with
sources_search_service.get_job_result (GET /api/sources/jobs/{id}/result).
Text is scrubbed, URLs reduced to scheme+host+path.
"""

import threading
from urllib.parse import urlsplit

import background_jobs
import db
from services import drama_service, ownership_service
from services import page_import_limits as limits
from services import sources_extraction_service as extraction
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from services.sources_extension_service import require_url_not_extension_only
from services.sources_registry_service import (import_supported, require_source, scrub,
                                              safe_url)
from services.sources_search_service import (IMPORT_JOB_PREFIX, MAX_ID_LEN, enabled_source,
                                             error_view, JobFailed, plain_text, clean_series_id,
                                             start_job)
from services.sources_url_service import (check_public_url, fail_job, handoff_error,
                                          source_client)
from sources import adaptive, chapter_order, generic_import, ladder, novel_follow, pipeline, registry, store
from sources.generic_import import DownloadBudget
from sources.http import Cancelled
from sources.models import AccessTier, ChallengeDetected, TermsProhibited

MAX_CHAPTERS = 200
MAX_SKIPPED_LISTED = 100
MAX_FOLLOW_PAGES = novel_follow.MAX_FOLLOW_PAGES
FOLLOW_STOPS = novel_follow.FOLLOW_STOPS
COMIC_MEDIA_TYPES = ("manhua", "manga", "manhwa")
NOVEL_MEDIA_TYPES = ("novel",)
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."
_COMIC_BUSY = ("Another comic import is running. One runs at a time; wait for it to "
               "finish, then try again.")
_COMIC_SLOT_LOCK = threading.Lock()
_comic_job_id = None


def start_comic_job(job_id: str, target, *args, description: str) -> dict:
    """Starts a job that holds comic image bytes (the pasted-URL comic
    import, a reviewed comic import): one at a time in this process, 409
    while another runs, so at most one import's budget
    (page_import_limits.MAX_IMPORT_BYTES) is held in memory."""
    global _comic_job_id
    with _COMIC_SLOT_LOCK:
        if _comic_job_id:
            st = background_jobs.get_status(_comic_job_id)
            if st and st.get("status") in ("running", "queued"):
                raise ConflictError(_COMIC_BUSY, details={"job_id": _comic_job_id})
        started = start_job(job_id, target, *args, description=description)
        _comic_job_id = job_id
        return started


def import_job_id(drama_id: int) -> str:
    return f"{IMPORT_JOB_PREFIX}{int(drama_id)}"


def _plain_id(value, what: str) -> str:
    """Same rule as a series id: no URL, slash, backslash, "@", ":", ".."
    or whitespace, so an id can never steer an adapter to another host."""
    text = plain_text(value, what, MAX_ID_LEN)
    if (text[0] in "/\\" or any(c in text for c in "\\@:") or ".." in text
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text)):
        raise InvalidInputError(f"{what} must be a plain id.")
    return text


def parse_chapter_ids(values) -> list:
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


def require_drama(drama_id, principal=None) -> dict:
    """A drama `principal` can't see (auth B2) is a 404 like a missing one."""
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("drama_id must be a positive integer.")
    drama = db.get_drama(drama_id)
    if drama is None or not ownership_service.can_edit_drama(principal, drama_id):
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def require_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_BUSY, details={"job_id": import_job_id(drama_id)})


# ---------------------------------------------------------------------------
# Chapter import (S-4)
# ---------------------------------------------------------------------------

def _outcome(row: dict) -> dict:
    out = {"chapter_id": str(row.get("chapter_id")), "title": scrub(row.get("title") or "")}
    if row.get("skipped"):
        out["outcome"] = "skipped"
    elif row.get("ok"):
        out["outcome"] = "imported"
        if "pages" in row:
            out["pages"] = int(row["pages"])
        if "chars" in row:
            out["chars"] = int(row["chars"])
    elif row.get("needs_ai"):
        out["outcome"] = "needs_ai"
        out["error"] = pipeline.NEEDS_AI_TEXT
    else:
        out["outcome"] = "failed"
        out["error"] = scrub(row.get("error") or "") or "Import failed."
    return out


_NOT_ATTEMPTED = "Not attempted: the import stopped before this chapter."
_PARTLY = ("Stopped by an unexpected error while saving this chapter; it may be partly "
           "imported -- check the drama before retrying it.")


def _import_result(chapters: list, cancelled: bool, handoff) -> dict:
    counts = {k: sum(1 for c in chapters if c["outcome"] == k)
              for k in ("imported", "skipped", "failed", "not_attempted")}
    retry = [c["chapter_id"] for c in chapters
             if c["outcome"] in store.RETRY_STATUSES and c.get("retryable", True)]
    return {"kind": "chapter_import", "chapters": chapters,
            "imported_count": counts["imported"], "skipped_count": counts["skipped"],
            "failed_count": counts["failed"], "not_attempted_count": counts["not_attempted"],
            "partial": (counts["failed"] > 0 or counts["not_attempted"] > 0
                        or any(c["outcome"] == "needs_ai" for c in chapters)),
            "retry_chapter_ids": retry,
            "cancelled": bool(cancelled), "handoff": handoff}


def _save_manifest(name: str, series_id: str, drama_id: int, chapters: list):
    """Remembers which chapters failed or were never attempted
    (redacted text only, as shown in the result), so the retry survives a
    reload or a restart. Best effort: the import itself already happened."""
    try:
        store.record_import_retry(
            name, series_id, drama_id,
            [(c["chapter_id"], c.get("title") or "",
              c["outcome"] if c.get("retryable", True) else "partial", c.get("error") or "")
             for c in chapters if c["outcome"] in store.MANIFEST_STATUSES],
            # A chapter no longer on the site can't be retried either.
            [c["chapter_id"] for c in chapters
             if c["outcome"] in ("imported", "skipped", "not_found")])
    except Exception:
        import applog
        applog.get_logger().warning("Could not save the import retry manifest", exc_info=True)


def _chapter_outcomes(raw: dict, wanted: list, missing_ids: list, skip=(),
                      crashed: bool = False) -> list:
    """The pipeline's rows matched to the wanted chapters by chapter_id
    (never by position). A wanted chapter with no row is "not_attempted":
    the job stopped first (browser check, terms, cancel, an error). After an
    unexpected error (`crashed`), the first such chapter that wasn't skipped
    is the one it interrupted, possibly mid-write: "failed", not retryable."""
    rows = {}
    for row in raw.get("chapters") or []:
        rows.setdefault(str(row.get("chapter_id")), row)
    chapters = []
    in_flight = crashed
    for ch in wanted:
        row = rows.get(str(ch.chapter_id))
        if row is not None:
            chapters.append(_outcome(row))
        elif in_flight and str(ch.chapter_id) not in skip:
            in_flight = False
            chapters.append({"chapter_id": str(ch.chapter_id), "title": scrub(ch.title or ""),
                             "outcome": "failed", "error": _PARTLY, "retryable": False})
        else:
            chapters.append({"chapter_id": str(ch.chapter_id), "title": scrub(ch.title or ""),
                             "outcome": "not_attempted", "error": _NOT_ATTEMPTED})
    return chapters + [{"chapter_id": c, "title": "", "outcome": "not_found"}
                       for c in missing_ids]


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
        if isinstance(e, (ChallengeDetected, TermsProhibited)):
            # Stopped before any chapter: every requested one is not attempted.
            _save_manifest(name, series_id, drama_id,
                           [{"chapter_id": c, "title": "", "outcome": "not_attempted",
                             "error": _NOT_ATTEMPTED} for c in chapter_ids])
        err = error_view(e, name)
        background_jobs.set_result(job_id, {"kind": "chapter_import", "error": err})
        raise JobFailed(err["message"]) from None
    by_id = {}
    for ch in listed:
        by_id.setdefault(str(ch.chapter_id), ch)
    requested = set(chapter_ids)
    wanted = [ch for ch in chapter_order.reading_order(adapter, by_id.values())
              if str(ch.chapter_id) in requested]
    already = store.imported_chapter_ids(name, series_id, drama_id)
    skip = {c for c in chapter_ids if c in already}
    try:
        pipeline.run_import_job(
            job_id, name, wanted, drama_id, adapter=adapter, skip_ids=skip,
            on_layout_changed=lambda ch, url, html: extraction.stash_layout_page(
                drama_id, name, series_id, ch.chapter_id, url, html, ch.title))
    except Exception:
        # An unexpected error (e.g. an unreadable page image) still leaves
        # the chapters that failed or never ran in the retry manifest.
        raw = (background_jobs.get_status(job_id) or {}).get("result") or {}
        _save_manifest(name, series_id, drama_id,
                       _chapter_outcomes(raw, wanted, [], skip=skip, crashed=True))
        raise
    raw = (background_jobs.get_status(job_id) or {}).get("result") or {}
    chapters = _chapter_outcomes(raw, wanted, [c for c in chapter_ids if c not in by_id])
    handoff = None
    if raw.get("handoff"):
        h = raw["handoff"]
        handoff = {"reason": str(h.get("reason") or ""), "handoff": True,
                   "open_url": safe_url(h.get("url")),
                   "chapter_id": str(h.get("chapter_id") or "") or None}
    _save_manifest(name, series_id, drama_id, chapters)
    background_jobs.set_result(job_id, _import_result(chapters, raw.get("cancelled"), handoff))


def start_chapter_import(name, series_id, chapter_ids, drama_id, principal=None) -> dict:
    """Starts `sourceimport_<drama_id>`. 404 unknown source or drama; 400
    source off or unable to import; 422 bad ids or the drama's media type
    doesn't match (comic sources need manhua/manga/manhwa, text sources a
    novel); 409 while any job runs for the drama."""
    name = str(name or "")
    cls = enabled_source(name)
    series_id = clean_series_id(series_id)
    ids = parse_chapter_ids(chapter_ids)
    adapter = cls()
    if not import_supported(adapter) or not adapter.supports("get_chapters"):
        raise UnsupportedOperationError("This source can't import chapters.",
                                        details={"reason": "NOT_SUPPORTED"})
    drama = require_drama(drama_id, principal)
    media = (drama.get("media_type") or "").lower()
    if adapter.supports("get_pages"):
        if media not in COMIC_MEDIA_TYPES:
            raise InvalidInputError("Comic chapters import into a manhua, manga or manhwa "
                                    "drama. Pick one of those, or create one first.")
    elif media not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel chapters import into a novel drama. Pick one, "
                                "or create one first.")
    require_idle(drama_id)
    job_id = import_job_id(drama_id)
    return start_job(job_id, _chapter_import_job, job_id, name, series_id, ids, drama_id,
                  description=f"Import {len(ids)} chapter(s) from {name}")


_RECOVER_FAILED = "The AI could not read this page either; nothing was imported."


def _ai_recover_job(job_id: str, name: str, series_id: str, chapter_id: str, drama_id: int,
                    engine):
    """Reads one chapter page the adapter could not (the page kept when it
    failed, else one paced fetch) with the AI extraction, at most one AI call,
    and always opens the review for the person: nothing is written here."""
    cancel_check = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    kept = extraction.take_layout_page(drama_id, name, series_id, chapter_id)
    try:
        if kept is None:
            background_jobs.update_progress(job_id, 0.1, "Fetching the chapter page...")
            adapter = registry.get_adapter(name, cancel_check=cancel_check)
            ladder.check_terms(name, adapter.capabilities())
            ch = next((c for c in adapter.get_chapters(series_id)
                       if str(c.chapter_id) == chapter_id), None)
            if ch is None or not ch.url:
                url_fail(job_id, {"status": 404, "code": NotFoundError.code,
                                   "message": "That chapter is no longer listed on the source."})
            resp = adapter.client.get(ch.url)
            kept = (resp.url or ch.url, resp.text, ch.title)
        url, html, title = kept
        background_jobs.update_progress(job_id, 0.5, "Asking the AI engine to find the text...")
        report = adaptive.ExtractionReport(url, "novel", hold_profiles=True)
        data, report = adaptive.extract_novel(html, url, engine, report=report)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except JobFailed:
        raise
    except Exception as e:
        url_fail(job_id, error_view(e, name))
    if data is None:
        url_fail(job_id, {"status": 422, "code": InvalidInputError.code,
                           "message": _RECOVER_FAILED})
    opened = extraction.open_review(
        drama_id, "novel", url, html, data, report, extraction.WHY_RECOVERY,
        recovery={"source": name, "series_id": series_id, "chapter_id": chapter_id,
                  "title": title})
    background_jobs.set_result(job_id, {
        "kind": "url_import", "needs_review": True, "char_count": len(data.get("content") or ""),
        "review_open": opened, "llm_calls": int(report.llm_calls)})


def start_ai_recover(name, chapter_id, series_id, drama_id, engine_name, confirm,
                     principal=None) -> dict:
    """Starts `sourceimport_<drama_id>`: one AI-assisted read of a chapter
    whose page layout changed, ending in a Review extraction (the review's
    import writes the chapter). `engine_name` is already checked by the
    route (resolve_ai_engine_name, `engines.paid`). 422 without `confirm`,
    bad ids or a source that is not a novel one; 404 source/drama; 409 a
    review is open for the drama, the chapter is already imported, or a job
    runs for it; 503 the engine has no key."""
    if confirm is not True:
        raise InvalidInputError("Confirm the AI call to continue.")
    name = str(name or "")
    cls = enabled_source(name)
    series_id = clean_series_id(series_id)
    chapter_id = _plain_id(chapter_id, "chapter_id")
    adapter = cls()
    if not import_supported(adapter) or adapter.supports("get_pages") \
            or not adapter.supports("get_chapters"):
        raise InvalidInputError("AI recovery is for sources that import novel chapters.")
    drama = require_drama(drama_id, principal)
    if (drama.get("media_type") or "").lower() not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel chapters import into a novel drama.")
    if extraction.review_open(drama_id):
        raise ConflictError("Finish or close the extraction review for this drama first.")
    if chapter_id in store.imported_chapter_ids(name, series_id, drama_id):
        raise ConflictError("That chapter is already imported into this drama.")
    require_idle(drama_id)
    engine = extraction.build_ai_engine(engine_name)
    job_id = import_job_id(drama_id)
    return start_job(job_id, _ai_recover_job, job_id, name, series_id, chapter_id, drama_id,
                  engine, description=f"AI recovery of one chapter from {name}")


def _drama_created(drama: dict):
    """The drama's created_at (UTC ISO text in library.db) as epoch
    seconds, or None if missing or unreadable."""
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(str(drama.get("created_at") or ""))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def get_import_state(name, series_id, drama_id, principal=None) -> dict:
    """What the chapter picker marks before an import -- the
    chapters of this series already imported into this drama, and the ones
    the last imports left failed or not attempted (the "Retry failed
    chapters (N)" set). Reads sources.db only; fetches nothing. 404 unknown
    source or a drama the principal can't edit; 422 bad ids."""
    name = str(name or "")
    require_source(name)
    series_id = clean_series_id(series_id)
    drama = require_drama(drama_id, principal)
    imported = sorted(store.imported_chapter_ids(name, series_id, drama_id))
    # Rows older than the drama belong to an earlier library whose drama had
    # the same id (a library reset starts ids at 1 again; sources.db stays).
    born = _drama_created(drama)
    retry = [{"chapter_id": r["chapter_id"], "title": scrub(r["title"] or ""),
              "status": r["status"], "error": scrub(r["error"] or "")}
             for r in store.import_retry_rows(name, series_id, drama_id)
             if (born is None or float(r["updated_at"]) >= born)
             and r["status"] in store.MANIFEST_STATUSES]
    return {"source": name, "series_id": series_id, "drama_id": drama_id,
            "imported_chapter_ids": imported, "retry": retry,
            "retry_count": sum(1 for r in retry if r["status"] in store.RETRY_STATUSES)}


# ---------------------------------------------------------------------------
# Novel text from a pasted URL (S-5)
# ---------------------------------------------------------------------------

_NO_TEXT = "No chapter text was found on that page."


def url_fail(job_id: str, err: dict):
    fail_job(job_id, "url_import", err)


def _is_public(url: str) -> bool:
    try:
        check_public_url(url)
    except (InvalidInputError, DependencyUnavailableError):
        return False
    return True


def _follow_import_job(job_id: str, url: str, drama_id: int, local: bool, engine,
                       follow_pages: int):
    """Reads the pasted page and the pages its next links lead to into a
    review for the drama; writes nothing to the drama."""
    background_jobs.update_progress(job_id, 0.05, "Reading the page...")

    def progress(done: int, cap: int):
        background_jobs.update_progress(job_id, 0.05 + 0.9 * done / cap,
                                        f"Reading page {done + 1} of up to {cap}...")
    try:
        chain = novel_follow.follow_novel(
            url, follow_pages, engine=engine, client=source_client(url, job_id),
            allow_signed_in=local, allow_browser=local, hold_profiles=not local,
            url_check=_is_public, progress=progress,
            cancel_check=lambda: background_jobs.is_cancel_requested(job_id))
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except generic_import.NoContentFound:
        url_fail(job_id, {"status": 422, "code": InvalidInputError.code, "message": _NO_TEXT,
                           "details": {"reason": "NO_CONTENT"}})
    except Exception as e:
        url_fail(job_id, error_view(e))
    first = chain.first
    if first.ladder is not None and getattr(first.ladder, "handoff", None):
        url_fail(job_id, handoff_error(first.ladder.handoff, url))
    report = chain.report
    why = extraction.review_reason(report, False) or extraction.WHY_FOLLOWED
    signed_in = _signed_in(first.ladder) or any(
        p.tier == AccessTier.AUTHENTICATED_BROWSER.value for p in chain.pages)
    opened = extraction.open_review(drama_id, "novel", url, getattr(first.ladder, "html", ""),
                                    report.data, report, why, pc_only=signed_in,
                                    chain=chain.pages[1:], follow_stop=chain.stop)
    background_jobs.set_result(job_id, {
        "kind": "url_import", "needs_review": True,
        "char_count": sum(len(p.text or "") for p in chain.pages), "review_open": opened,
        "pages_found": len(chain.pages), "follow_stop": chain.stop})


def _url_import_job(job_id: str, url: str, drama_id: int, local: bool, engine=None,
                    review: bool = False, follow_pages: int = 1):
    if follow_pages > 1:
        _follow_import_job(job_id, url, drama_id, local, engine, follow_pages)
        return
    background_jobs.update_progress(job_id, 0.1, "Reading the page...")
    try:
        res, report = adaptive.import_novel(url, engine=engine, client=source_client(url, job_id),
                                            allow_signed_in=local, allow_browser=local,
                                            hold_profiles=not local)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except generic_import.NoContentFound:
        url_fail(job_id, {"status": 422, "code": InvalidInputError.code, "message": _NO_TEXT,
                           "details": {"reason": "NO_CONTENT"}})
    except Exception as e:
        url_fail(job_id, error_view(e))
    if res.ladder is not None and getattr(res.ladder, "handoff", None):
        url_fail(job_id, handoff_error(res.ladder.handoff, url))
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
                     ai_engine: str = None, review: bool = False,
                     follow_pages: int = 1) -> dict:
    """Starts `sourceimport_<drama_id>`: novel text from one pasted URL,
    appended to a novel drama's raw-novel text. `ai_engine` (a name from
    sources_extraction_service.resolve_ai_engine_name, None = off) is the
    LLM fallback. `follow_pages` above 1: read up to that many pages by
    following next-chapter links, into a review (nothing written). 422
    bad/private URL, `follow_pages` out of range or not a novel drama; 503
    the host doesn't resolve or the engine has no key; 404 no drama; 409
    while a job runs for the drama."""
    if (isinstance(follow_pages, bool) or not isinstance(follow_pages, int)
            or not 1 <= follow_pages <= MAX_FOLLOW_PAGES):
        raise InvalidInputError(f"Follow between 1 and {MAX_FOLLOW_PAGES} pages.")
    url = check_public_url(url)
    require_url_not_extension_only(url)
    drama = require_drama(drama_id, principal)
    if (drama.get("media_type") or "").lower() not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel text imports into a novel drama. Pick one, "
                                "or create one first.")
    require_idle(drama_id)
    engine = extraction.build_ai_engine(ai_engine)
    job_id = import_job_id(drama_id)
    return start_job(job_id, _url_import_job, job_id, url, drama_id, bool(local), engine,
                  bool(review), follow_pages, description="Import novel text from a pasted URL")


# ---------------------------------------------------------------------------
# Comic pages from a pasted URL (parity SO06)
# ---------------------------------------------------------------------------

_NO_PAGES = "No comic pages were found on that page."
_BILIBILI_MANGA_HINT = (
    " Bilibili Manga only shows a chapter's images to a signed-in reader for locked or paid "
    "chapters, and it loads them as you scroll. Sign in to Bilibili Manga from the Sources "
    "page, or save the chapter page from your own browser and import that file.")


_BILIBILI_NEEDS_BROWSER = (
    " Bilibili Manga builds its pages with scripts, so the images only appear in a real browser. "
    "The browser extension's 'Capture whole chapter' works from your own browser.")
_PLAYWRIGHT_MISSING = (
    " The browser step could not run because the Playwright package is not installed. "
    "Install it in Diagnostics > Packages (group 'Novels & reader', 'Novel sources from "
    "websites'), then try again. No browser download is needed when Chrome or Edge is installed.")
_BROWSER_MISSING = (
    " The browser step could not run because no Chrome or Edge was found on this computer. "
    "Install one of them, then try again.")
_NO_BROWSER_TRIED = (
    " No browser was used for this request; import it from the PC the app runs on, or save "
    "the chapter page from your own browser and import that file.")


def _bilibili_manga_cause(browser_tier: str) -> str:
    """Names why Bilibili Manga's client-rendered page showed no images: a
    browser that couldn't start comes first, since sign-in only matters once
    the page actually rendered."""
    if browser_tier == ladder.MISSING_PLAYWRIGHT:
        return _BILIBILI_NEEDS_BROWSER + _PLAYWRIGHT_MISSING
    if browser_tier == ladder.MISSING_BROWSER:
        return _BILIBILI_NEEDS_BROWSER + _BROWSER_MISSING
    if browser_tier == "ran":
        return _BILIBILI_MANGA_HINT
    return _BILIBILI_NEEDS_BROWSER + _NO_BROWSER_TRIED


def _no_pages_error(exc, url) -> dict:
    """The 422 for a page with no usable images, saying why (the report's
    reason and each tier's line, scrubbed) instead of only the generic text."""
    report = getattr(exc, "report", None)
    reason = scrub((getattr(report, "reason", "") or "").strip())[:300]
    lines = [scrub(x)[:300] for x in (getattr(report, "access_lines", None) or [])][:10]
    why = f" Why: {reason}" if reason else ""
    message = _NO_PAGES + why
    if (urlsplit(url or "").hostname or "").lower().endswith("manga.bilibili.com"):
        cause = _bilibili_manga_cause(getattr(report, "browser_tier", ""))
        # A browser that couldn't start is the cause, so it leads; the generic
        # "no image tags" reason is only a symptom of it.
        message = _NO_PAGES + cause + why if cause != _BILIBILI_MANGA_HINT else message + cause
    return {"status": 422, "code": InvalidInputError.code, "message": message,
            "details": {"reason": "NO_CONTENT", "diagnostic": lines}}


def _signed_in(lr) -> bool:
    """Whether the page was read through the saved signed-in browser."""
    return getattr(lr, "tier", None) == AccessTier.AUTHENTICATED_BROWSER.value


def skipped_view(candidates) -> list:
    """The images left out, and why (scheme+host+path only, signed path
    segments blanked, scrubbed)."""
    return [{"display_url": extraction.display_url(c.url), "reason": scrub(c.reject_reason or "") or "not a page"}
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
                                            learn=local)
    except Cancelled:
        raise background_jobs.JobCancelled(job_id) from None
    except generic_import.NoContentFound as e:
        fail_job(job_id, "comic_import", _no_pages_error(e, url))
    except Exception as e:
        fail_job(job_id, "comic_import", error_view(e))
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
    n, skipped = extraction.write_pages(drama_id, ((c, c.content) for c in res.images), job_id)
    background_jobs.set_result(job_id, _comic_result(False, n, list(skipped) + list(res.rejected)))


def start_comic_url_import(url, drama_id, local: bool = True, principal=None,
                           ai_engine: str = None, review: bool = False) -> dict:
    """Starts `sourceimport_<drama_id>`: the page images of one pasted comic
    chapter URL, added to a manhua/manga/manhwa drama's pages (Scanlate).
    Same checks and errors as start_url_import."""
    url = check_public_url(url)
    require_url_not_extension_only(url)
    drama = require_drama(drama_id, principal)
    if (drama.get("media_type") or "").lower() not in COMIC_MEDIA_TYPES:
        raise InvalidInputError("Comic pages import into a manhua, manga or manhwa drama. "
                                "Pick one of those, or create one first.")
    require_idle(drama_id)
    engine = extraction.build_ai_engine(ai_engine)
    job_id = import_job_id(drama_id)
    return start_comic_job(job_id, _comic_url_import_job, job_id, url, drama_id, bool(local),
                           engine, bool(review), description="Import comic pages from a pasted URL")
