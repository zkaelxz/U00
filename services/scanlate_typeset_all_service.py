"""services/scanlate_typeset_all_service.py -- the library-wide "Typeset all comics again" job: one title after another, each through the title's own Scanlate render job.

Free: no OCR, no AI call and no detection. Regions, English and box edits are
read from the database and only the page images are drawn again. It never goes
through scanlate_run_service, so the paid "Keep the text already read" mode
cannot be reached from here.
"""
import time

import background_jobs
import db
from lib.errors import ConflictError, UnsupportedOperationError
from jobs import job_launch
from services import library_admin_service
from services import scanlate_render_service as render_svc

JOB_ID = "scanlate_typeset_all"
COMIC_MEDIA_TYPES = ("manga", "manhwa", "manhua")
_POLL_SECONDS = 1.0
_sleep = time.sleep


def comic_titles() -> list:
    return [d for media in COMIC_MEDIA_TYPES for d in db.list_dramas(media_type=media)]


def _wait_for(jid: str, cancel) -> dict:
    """The finished state of a title's render job. A cancel here stops that job too."""
    while True:
        job = background_jobs.get_status(jid) or {}
        if job.get("status") not in ("running", "queued"):
            return job
        if cancel():
            background_jobs.request_cancel(jid)
        _sleep(_POLL_SECONDS)


def _typeset_title(drama_id: int, cancel) -> tuple:
    """(outcome, pages redrawn, pages failed); outcome is done, no_text, busy or failed."""
    try:
        started = render_svc.start_render(drama_id)
    except UnsupportedOperationError:
        return "no_text", 0, 0
    except ConflictError:
        return "busy", 0, 0
    job = _wait_for(started["job_id"], cancel)
    result = job.get("result") or {}
    if job.get("status") != "done":
        return "failed", 0, 0
    return "done", int(result.get("rendered", 0)), int(result.get("failed", 0))


def _summary(counts: dict) -> str:
    parts = [f"{counts['titles_done']} title(s) typeset, {counts['pages_redrawn']} page(s) redrawn"]
    if counts["pages_failed"]:
        parts.append(f"{counts['pages_failed']} page(s) failed")
    if counts["titles_failed"]:
        parts.append(f"{counts['titles_failed']} title(s) failed")
    if counts["titles_no_text"]:
        parts.append(f"{counts['titles_no_text']} title(s) have no translations")
    if counts["titles_busy"]:
        parts.append(f"{counts['titles_busy']} title(s) were busy and skipped")
    return ", ".join(parts) + "."


def _job(jid: str, drama_ids: list):
    cancel = lambda: background_jobs.is_cancel_requested(jid)  # noqa: E731
    counts = {"kind": "typeset_all", "titles_total": len(drama_ids), "titles_done": 0,
              "titles_no_text": 0, "titles_busy": 0, "titles_failed": 0,
              "pages_redrawn": 0, "pages_failed": 0}
    key = {"done": "titles_done", "no_text": "titles_no_text", "busy": "titles_busy",
           "failed": "titles_failed"}
    for n, drama_id in enumerate(drama_ids):
        if cancel():
            background_jobs.set_result(jid, counts)
            raise background_jobs.JobCancelled(jid)
        background_jobs.update_progress(jid, n / len(drama_ids),
                                        f"Typesetting title {n + 1} of {len(drama_ids)}")
        outcome, redrawn, failed = _typeset_title(drama_id, cancel)
        counts[key[outcome]] += 1
        counts["pages_redrawn"] += redrawn
        counts["pages_failed"] += failed
    background_jobs.set_result(jid, counts)
    background_jobs.update_progress(jid, 1.0, _summary(counts))


def start_typeset_all() -> dict:
    """Starts the job over every comic title that has text regions. 409 while one runs."""
    library_admin_service.refuse_during_maintenance("typeset")
    ids = [d["id"] for d in comic_titles() if _has_regions(d["id"])]
    if not ids:
        raise UnsupportedOperationError("No comic has translations yet, so there is nothing to typeset.")
    # Not gpu_touching: this job only starts each title's render job, which is.
    if not job_launch.start_job(JOB_ID, _job, JOB_ID, ids,
                                description="Typeset all comics again", line_fields=()):
        raise ConflictError("Typeset all comics is already running.")
    return {"job_id": JOB_ID}


def _has_regions(drama_id: int) -> bool:
    return bool(db.list_bubbles_for_drama(drama_id))
