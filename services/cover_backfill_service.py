"""services/cover_backfill_service.py -- one pass over the library that gives a cover to every
title without one, from what the title already holds (a frame of its video, page one of a comic)."""
import contextlib
import os

import applog
import background_jobs
import db
from jobs.job_launch import start_job
from services import cover_art_service

JOB_ID = "cover_backfill"
# Written once a full pass has finished, so the pass after an update runs a single time instead of
# retrying every unreadable video at each start.
_MARKER = ".covers_backfilled"
# Separate, so a library that already had the first pass still gets one pass for site pictures.
_SITE_MARKER = ".site_covers_refreshed"
_COMIC_TYPES = ("manhua", "manga", "manhwa")


def _missing_cover_ids() -> list:
    return [d["id"] for d in db.list_dramas() if not d.get("cover_art_filename")]


def _site_candidates() -> list:
    return [d["id"] for d in db.list_dramas() if (d.get("media_type") or "").lower() in _COMIC_TYPES
            and (d.get("source_url_full") or d.get("source_url"))]


def _refresh_site_covers(job_id: str) -> None:
    """Gives comic titles the picture their site shows for the series, in place of page one. A cover
    the person chose is kept; so is page one when the site gives nothing usable."""
    ids = _site_candidates()
    fetched = fallbacks = failed = unreachable = 0
    for i, drama_id in enumerate(ids):
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, i / max(len(ids), 1),
                                        f"Fetching site covers ({i} of {len(ids)})...")
        drama = db.get_drama(drama_id) or {}
        if drama.get("cover_art_filename") and not cover_art_service.cover_is_first_page(drama_id):
            continue
        link = (drama.get("source_url_full") or drama.get("source_url") or "").strip()
        picture = cover_art_service.site_cover_url(link)
        if picture is None:
            unreachable += 1
        elif not picture:
            fallbacks += 1
        elif cover_art_service.fetch_site_cover(drama_id, picture, link, replace=True):
            fetched += 1
        else:
            failed += 1
    applog.get_logger().info(
        f"site covers: {fetched} fetched, {fallbacks} kept page one (no picture on the page), "
        f"{failed} failed, {unreachable} pages unreachable, of {len(ids)} comic titles with a link")
    # Left unmarked when nothing could be reached (the PC offline), so the next start tries again.
    if unreachable < len(ids) or not ids:
        with contextlib.suppress(OSError):
            open(os.path.join(db.LIBRARY_DIR, _SITE_MARKER), "w").close()


def _run(job_id: str) -> None:
    ids = _missing_cover_ids()
    filled = 0
    for i, drama_id in enumerate(ids):
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, i / max(len(ids), 1),
                                        f"Making covers ({i} of {len(ids)})...")
        # The network is left out here: a title's own picture is cheap. Only comic titles with a
        # saved link go to their site, in the pass below.
        if cover_art_service.fill_missing_cover(drama_id, from_network=False):
            filled += 1
    with contextlib.suppress(OSError):
        open(os.path.join(db.LIBRARY_DIR, _MARKER), "w").close()
    _refresh_site_covers(job_id)
    background_jobs.set_result(job_id, {"checked": len(ids), "filled": filled})


def start_backfill() -> dict:
    """Starts the pass; `started` is False when one is already running."""
    started = start_job(JOB_ID, _run, JOB_ID, line_fields=(),
                        description="Make covers for titles that have none")
    return {"job_id": JOB_ID, "started": bool(started)}


def start_backfill_once() -> None:
    """The pass after an update: only until one full pass has finished. Never raises."""
    with contextlib.suppress(Exception):
        if not all(os.path.exists(os.path.join(db.LIBRARY_DIR, m)) for m in (_MARKER, _SITE_MARKER)):
            start_backfill()
