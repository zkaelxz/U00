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
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from services.sources_registry_service import (require_link_editable, require_source,
                                               list_tracked)
from services.sources_search_service import clean_series_id
from sources import chapter_check, registry, store

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
    if not store.set_tracked_save(source, series_id, bool(save_cbz)):
        raise NotFoundError("That series isn't tracked.")
    return list_tracked(principal)
