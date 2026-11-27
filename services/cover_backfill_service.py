"""services/cover_backfill_service.py -- one pass over the library that gives a cover to every
title without one, from what the title already holds (a frame of its video, page one of a comic)."""
import contextlib
import os

import background_jobs
import db
from jobs.job_launch import start_job
from services import cover_art_service

JOB_ID = "cover_backfill"
# Written once a full pass has finished, so the pass after an update runs a single time instead of
# retrying every unreadable video at each start.
_MARKER = ".covers_backfilled"


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
        # The network is left out: a title's own picture is cheap, and a pass over the whole
        # library must not call every site it ever came from.
        if cover_art_service.fill_missing_cover(drama_id, from_network=False):
            filled += 1
    background_jobs.set_result(job_id, {"checked": len(ids), "filled": filled})
    with contextlib.suppress(OSError):
        open(os.path.join(db.LIBRARY_DIR, _MARKER), "w").close()


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
