"""
services/sources_tracking_service.py -- tracked-series upkeep for the API
(Discover/Sources/Live spec S-7): "Check now" and which drama a tracked
series auto-imports into.

`start_check_now` runs `sources.chapter_check.run_check_cycle` as the same
background job the scheduler uses (`sources_chapter_check`), so a manual
check and a scheduled one can never run side by side in this process, and
the cycle's own claim (`store.claim_check_cycle`) keeps a second process
from checking at the same time: that
cycle ends with `skipped: true` and checks nothing. The scheduler itself is
started by the API process (`api/background.py`), not here.

A check re-fetches each tracked series' chapter list through the adapter's
paced client and records new chapters as notifications. It downloads
nothing unless the `auto_queue_new_chapters` setting is on and the series
has a drama; then it starts that drama's per-drama import job
(`sourceimport_<id>`). A comic series
with "Save new chapters as CBZ" on (`set_tracked_save`) also has its new
chapters saved as CBZ files into the save folder, during the check.

The result (`GET /api/sources/jobs/sources_chapter_check/result`) is
{checked, new, errors {title: text}, queued [titles], saved [titles],
skipped?}, scrubbed
by `sources_search_service.get_job_result`.
"""

import background_jobs
import db
from services import ownership_service
from lib.errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from services.sources_registry_service import (require_link_editable, require_source,
                                               list_tracked)
from services import sources_search_service as search
from services.sources_search_service import clean_series_id
from services.sources_extension_service import require_not_extension_only
from sources import chapter_check, chapter_order, registry, store

CHECK_JOB_ID = chapter_check.CHECK_JOB_ID
COMIC_MEDIA_TYPES = ("manhua", "manga", "manhwa")
NOVEL_MEDIA_TYPES = ("novel",)


def start_check_now(local: bool = True) -> dict:
    """{job_id}. `local` False: no adapter opens a browser. 422 when nothing is tracked; 409 while a check runs."""
    if not store.list_tracked_series():
        raise InvalidInputError("No series is tracked yet. Open a series and track it first.")
    status = background_jobs.get_status(CHECK_JOB_ID)
    if status and status.get("status") in ("running", "queued"):
        raise ConflictError("A check is already running.", details={"job_id": CHECK_JOB_ID})
    if not chapter_check.start_check_now(allow_browser=local):
        raise ConflictError("A check is already running.", details={"job_id": CHECK_JOB_ID})
    return {"job_id": CHECK_JOB_ID}


def _check_media(source: str, drama: dict):
    """The same media rule as a chapter import: comic sources feed a
    manhua/manga/manhwa drama, text sources a novel drama."""
    cls = registry.adapter_classes().get(source)
    adapter = cls() if cls else None
    media = (drama.get("media_type") or "").lower()
    if adapter is not None and adapter.supports("get_pages"):
        if media not in COMIC_MEDIA_TYPES:
            raise InvalidInputError("Comic chapters import into a manhua, manga or manhwa title.")
    elif media not in NOVEL_MEDIA_TYPES:
        raise InvalidInputError("Novel chapters import into a novel title.")


def set_tracked_drama(source: str, series_id: str, drama_id, principal=None) -> list:
    """Points a tracked series' auto-import at `drama_id` (None clears it).
    Fetches nothing. 404 unknown source, untracked series, a missing drama
    or one the principal can't edit (the auto-import writes chapters into
    it), or a series currently linked to such a drama; 422 a drama of the
    wrong media type. `principal` None is auth off / the PC owner."""
    require_source(source)
    series_id = clean_series_id(series_id)
    if not any(r["source"] == source and r["series_id"] == series_id
               for r in store.list_tracked_series()):
        raise NotFoundError("That series isn't tracked.")
    require_link_editable(source, series_id, principal)
    if drama_id is not None:
        drama = db.get_drama(drama_id)
        if drama is None or not ownership_service.can_edit_drama(principal, drama_id):
            raise NotFoundError(f"No title with id {drama_id}.")
        _check_media(source, drama)
    if not store.set_tracked_drama(source, series_id, drama_id,
                                   linked_by_user_id=(principal or {}).get("user_id")):
        raise NotFoundError("That series isn't tracked.")
    return list_tracked(principal)


def set_tracked_save(source: str, series_id: str, save_cbz: bool, principal=None) -> list:
    """Turns saving a tracked comic series' new chapters as CBZ files on or
    off (the check saves them into the save folder; no drama is involved).
    404 unknown source, untracked series, or one linked to a drama the
    principal can't edit (as relinking it); 400 a source without pages."""
    cls = require_source(source)
    series_id = clean_series_id(series_id)
    require_link_editable(source, series_id, principal)
    if not cls().supports("get_pages"):
        raise UnsupportedOperationError("Only comic sources can save chapters as CBZ files.",
                                        details={"reason": "NOT_SUPPORTED"})
    if not store.set_tracked_save(source, series_id, bool(save_cbz),
                                  by_user_id=(principal or {}).get("user_id")):
        raise NotFoundError("That series isn't tracked.")
    return list_tracked(principal)


def _job_running(job_id: str) -> bool:
    status = background_jobs.get_status(job_id)
    return bool(status and status.get("status") in ("running", "queued"))


def _move_plan(source: str, series_id: str, new_source: str, new_series_id: str, principal) -> dict:
    """Validates a move and works out what it would do. Reads only this
    process's finished `sources_series_<new_source>` result for the new series
    (the same "open the series first" rule as tracking), and the old series'
    stored chapters, so a dead old site is never contacted."""
    series_id, new_series_id = clean_series_id(series_id), clean_series_id(new_series_id)
    row = next((r for r in store.list_tracked_series()
                if r["source"] == source and r["series_id"] == series_id), None)
    if row is None:
        raise NotFoundError("That series isn't tracked.")
    require_link_editable(source, series_id, principal)
    require_source(new_source)
    require_not_extension_only(new_source)
    if (source, series_id) == (new_source, new_series_id):
        raise InvalidInputError("Pick a different series to move to.")
    if any(r["source"] == new_source and r["series_id"] == new_series_id
           for r in store.list_tracked_series()):
        raise ConflictError("That series is already tracked.", details={"reason": "ALREADY_TRACKED"})
    drama_id = row.get("drama_id")
    drama = db.get_drama(drama_id) if drama_id is not None else None
    if drama is not None:
        _check_media(new_source, drama)
    status = background_jobs.get_status(search.SERIES_JOB_PREFIX + new_source) or {}
    result = status.get("result") if status.get("status") == "done" else None
    if (not isinstance(result, dict) or result.get("series_id") != new_series_id
            or result.get("error")):
        raise ConflictError("Open the series first, so its chapters can be compared.",
                            details={"reason": "SERIES_NOT_LOADED"})
    new_chapters = result["chapters"]
    imported = store.imported_chapter_ids(source, series_id, drama_id) if drama else set()
    # Chapters imported before the series was tracked may have no stored title; they
    # can't be matched, so they stay listed as only on the old source.
    old = store.known_chapters(source, series_id)
    seen = {c["chapter_id"] for c in old}
    old += [{"chapter_id": cid, "title": ""} for cid in sorted(imported - seen)]
    pairs, old_only, new_only = chapter_order.match_chapters(
        [c["title"] or "" for c in old], [c.get("title") or "" for c in new_chapters])
    saved = lambda i: old[i]["chapter_id"] in imported  # noqa: E731
    info = result.get("info") or {}
    return {
        "row": row, "drama_id": drama_id if drama else None,
        "title": row["title"] if row["title"] and row["title"] != series_id
        else info.get("title") or new_series_id,
        "url": info.get("url") or "",
        "known": [(str(new_chapters[j]["chapter_id"]), new_chapters[j].get("title") or "")
                  for _, j in pairs],
        "imported_ids": [str(new_chapters[j]["chapter_id"]) for i, j in pairs if saved(i)],
        "preview": {
            "source": new_source, "series_id": new_series_id, "drama_id": drama_id if drama else None,
            "matched": [{"title": new_chapters[j].get("title") or "", "saved": saved(i)}
                        for i, j in pairs],
            "only_old": [{"title": old[i]["title"] or old[i]["chapter_id"], "saved": saved(i)}
                         for i in old_only],
            "new": [{"title": new_chapters[j].get("title") or ""} for j in new_only],
        },
    }


def preview_move(source: str, series_id: str, new_source: str, new_series_id: str,
                 principal=None) -> dict:
    """Which chapters of a tracked series match the open series on another
    source: `matched` (kept as they are), `only_old` (stay saved, no longer
    listed by the site) and `new` (what the next check would announce)."""
    return search.scrub_any(_move_plan(source, series_id, new_source, new_series_id,
                                       principal)["preview"])


def move_tracked(source: str, series_id: str, new_source: str, new_series_id: str,
                 principal=None) -> list:
    """Points a tracked series at the open series on another source, keeping its
    title, saved chapters and translated pages: no chapter, page or import record
    is deleted or overwritten. Refused (409) while a check or the title's import
    is running. A failure changes nothing (one transaction)."""
    plan = _move_plan(source, series_id, new_source, new_series_id, principal)
    if _job_running(CHECK_JOB_ID) or (
            plan["drama_id"] is not None
            and _job_running(f"{search.IMPORT_JOB_PREFIX}{plan['drama_id']}")):
        raise ConflictError("A check or an import is running. Try again when it has finished.")
    refused = store.move_tracked_series(source, clean_series_id(series_id), new_source,
                                        clean_series_id(new_series_id), plan["title"],
                                        plan["url"], plan["known"], plan["imported_ids"])
    if refused == "taken":
        raise ConflictError("That series is already tracked.", details={"reason": "ALREADY_TRACKED"})
    if refused:
        raise NotFoundError("That series isn't tracked.")
    return list_tracked(principal)
