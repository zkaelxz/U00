"""services/cover_backfill_service.py -- one pass over the library that gives a cover to every
title without one, from what the title already holds (a frame of its video, page one of a comic)."""
import contextlib
import json
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
SITE_JOB_ID = "cover_site_refresh"
_COMIC_TYPES = ("manhua", "manga", "manhwa")
# Titles whose page named no cover-shaped picture: asking again would only repeat the request and
# starve the titles behind them under the per-press cap. The per-title button still retries them.
_KEPT_FILE = ".site_cover_kept.json"
# Titles asked of their sites per press, so one press never turns into a crawl; the pacing of each
# source's own client spaces the requests within it.
SITE_REFRESH_CAP = 30


def _missing_cover_ids() -> list:
    return [d["id"] for d in db.list_dramas() if not d.get("cover_art_filename")]


def _run(job_id: str) -> None:
    ids = _missing_cover_ids()
    filled = 0
    for i, drama_id in enumerate(ids):
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, i / max(len(ids), 1),
                                        f"Making covers ({i} of {len(ids)})...")
        # The network is left out: a pass over the whole library must not call every site it ever
        # came from. Site pictures are fetched only on request (start_site_refresh).
        if cover_art_service.fill_missing_cover(drama_id, from_network=False):
            filled += 1
    with contextlib.suppress(OSError):
        open(os.path.join(db.LIBRARY_DIR, _MARKER), "w").close()
    background_jobs.set_result(job_id, {"checked": len(ids), "filled": filled})


def start_backfill() -> dict:
    """Starts the pass; `started` is False when one is already running."""
    started = start_job(JOB_ID, _run, JOB_ID, line_fields=(),
                        description="Make covers for titles that have none")
    return {"job_id": JOB_ID, "started": bool(started)}


def start_backfill_once() -> None:
    """The pass after an update: only until one full pass has finished. Never raises."""
    with contextlib.suppress(Exception):
        if not os.path.exists(os.path.join(db.LIBRARY_DIR, _MARKER)):
            start_backfill()


def _kept_ids() -> set:
    try:
        with open(os.path.join(db.LIBRARY_DIR, _KEPT_FILE), encoding="utf-8") as f:
            return {int(i) for i in json.load(f)}
    except (OSError, ValueError, TypeError):
        return set()


def _site_candidates() -> list:
    """Comic titles with a stored link whose cover is missing or still page one, oldest first."""
    kept = _kept_ids()
    ids = []
    for d in db.list_dramas():
        if d["id"] not in kept and (d.get("media_type") or "").lower() in _COMIC_TYPES \
                and (d.get("source_url_full") or d.get("source_url")):
            if not d.get("cover_art_filename") or cover_art_service.cover_is_first_page(d["id"]):
                ids.append(d["id"])
    return sorted(ids)


def _refresh_site_covers(job_id: str) -> None:
    ids = _site_candidates()
    todo = ids[:SITE_REFRESH_CAP]
    counts: dict = {}
    per_site: dict = {}
    newly_kept: set = set()
    for i, drama_id in enumerate(todo):
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, i / max(len(todo), 1),
                                        f"Fetching site covers ({i} of {len(todo)})...")
        drama = db.get_drama(drama_id) or {}
        link = (drama.get("source_url_full") or drama.get("source_url") or "").strip()
        client = cover_art_service.paced_client(link)
        outcome = cover_art_service.refresh_site_cover(drama_id, client, only_first_page=True)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "kept":
            newly_kept.add(drama_id)
        # Source names are the app's own adapter names or "generic"; no link or host is logged.
        per_site[client.source] = per_site.get(client.source, 0) + 1
    if newly_kept:
        with contextlib.suppress(OSError):
            with open(os.path.join(db.LIBRARY_DIR, _KEPT_FILE), "w", encoding="utf-8") as f:
                json.dump(sorted(_kept_ids() | newly_kept), f)
    applog.get_logger().info(
        f"site covers: {counts.get('fetched', 0)} fetched, {counts.get('kept', 0)} kept, "
        f"{counts.get('unreachable', 0)} unreachable, {counts.get('skipped', 0)} skipped; "
        f"requests per source {per_site}; {len(ids) - len(todo)} left for the next press")
    background_jobs.set_result(job_id, {"checked": len(todo), "fetched": counts.get("fetched", 0),
                                        "left": len(ids) - len(todo)})


def start_site_refresh() -> dict:
    """Starts the opt-in pass that fetches site pictures for comics still showing page one."""
    started = start_job(SITE_JOB_ID, _refresh_site_covers, SITE_JOB_ID, line_fields=(),
                        description="Fetch covers from the comics' own sites")
    return {"job_id": SITE_JOB_ID, "started": bool(started)}
