"""
sources/chapter_check.py -- scheduled checks for new chapters on tracked
series (Step 23 item 5), the same simple shape as Mihon's library update:
re-fetch each tracked title's chapter list, diff by chapter id against
what's already known, and tell the person about anything new.

It notifies; it does not download. Auto-queueing new chapters for import
is a separate opt-in setting, off by default. Every check goes through
the adapter's own paced client, so a check cycle hits a source no harder
than a manual chapter-list fetch would.
"""

import threading
import time

import background_jobs

from . import ladder, registry, store
from .models import SourceError

CHECK_JOB_ID = "sources_chapter_check"
# A cycle's claim expires after this long, so a process that died
# mid-cycle doesn't block checks forever. Well above a paced cycle's length.
CYCLE_LEASE_SECONDS = 2 * 3600
_scheduler_started = False
_scheduler_lock = threading.Lock()


def check_series(adapter, row: dict) -> list:
    """Returns the ChapterInfo list of chapters that are new since the last
    check, and records them (known + a notification each)."""
    ladder.check_terms(adapter.name, adapter.capabilities())
    chapters = adapter.get_chapters(row["series_id"])
    known = store.known_chapter_ids(row["source"], row["series_id"])
    new = [c for c in chapters if c.chapter_id not in known]
    if new:
        # Only what this call actually recorded: another process checking
        # the same series at the same moment gets the rest.
        new = store.record_new_chapters(row["source"], row["series_id"], new)
    store.mark_checked(row["source"], row["series_id"])
    return new


def run_check_cycle(job_id: str = None, adapter_factory=None, scheduled: bool = False) -> dict:
    """One pass over every tracked series. `adapter_factory(name)` is
    injectable for tests; defaults to the registry.

    Safe to run from two processes (the API and Streamlit each run a
    scheduler): the cycle is claimed first (store.claim_check_cycle), and a
    cycle that can't claim returns {"skipped": True, ...} without checking
    anything. A `scheduled` cycle is also skipped when another process
    finished one within the interval since this one was found due."""
    now = time.time()
    min_gap = float(store.get_setting("check_interval_hours") or 0) * 3600 if scheduled else 0.0
    token = store.claim_check_cycle(now, CYCLE_LEASE_SECONDS, min_gap)
    if token is None:
        summary = {"checked": 0, "new": 0, "errors": {}, "queued": [], "skipped": True}
        if job_id:
            background_jobs.set_result(job_id, summary)
        return summary
    try:
        return _run_claimed_cycle(job_id, adapter_factory)
    finally:
        store.release_check_cycle(token)


def _run_claimed_cycle(job_id, adapter_factory) -> dict:
    factory = adapter_factory or (lambda name: registry.get_adapter(name))
    rows = store.list_tracked_series()
    summary = {"checked": 0, "new": 0, "errors": {}, "queued": []}
    auto_queue = bool(store.get_setting("auto_queue_new_chapters"))
    for i, row in enumerate(rows, start=1):
        if job_id:
            if background_jobs.is_cancel_requested(job_id):
                break
            background_jobs.update_progress(job_id, (i - 1) / max(len(rows), 1),
                                            f"Checking {row['title']} ({i}/{len(rows)})")
        if not registry.is_enabled(row["source"]):
            continue
        try:
            adapter = factory(row["source"])
            new = check_series(adapter, row)
        except SourceError as e:
            summary["errors"][row["title"]] = f"{e.reason.value}: {e}"
            store.mark_checked(row["source"], row["series_id"], error=str(e)[:300])
            continue
        except Exception as e:
            summary["errors"][row["title"]] = f"{type(e).__name__}: {e}"
            store.mark_checked(row["source"], row["series_id"], error=str(e)[:300])
            continue
        summary["checked"] += 1
        summary["new"] += len(new)
        if new and auto_queue and row.get("drama_id"):
            from .pipeline import start_import
            if start_import(row["source"], row["series_id"], new, row["drama_id"]):
                summary["queued"].append(row["title"])
    store.set_setting("last_check_cycle", time.time())
    if job_id:
        background_jobs.set_result(job_id, summary)
    return summary


def check_due(now: float = None) -> bool:
    hours = float(store.get_setting("check_interval_hours") or 0)
    if hours <= 0:
        return False
    last = store.get_setting("last_check_cycle") or 0
    now = time.time() if now is None else now
    return now - float(last) >= hours * 3600


def start_check_now(scheduled: bool = False) -> bool:
    return background_jobs.start_job(CHECK_JOB_ID, run_check_cycle, CHECK_JOB_ID,
                                     scheduled=scheduled,
                                     description="Checking tracked series for new chapters")


def ensure_scheduler_started(poll_seconds: float = 300.0):
    """Starts (once per process) a daemon thread that kicks off a check
    cycle whenever the configured interval has elapsed. Interval 0 = off."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def loop():
        while True:
            try:
                if store.list_tracked_series() and check_due():
                    start_check_now(scheduled=True)
            except Exception:
                pass    # a transient DB/lock hiccup shouldn't kill the scheduler
            time.sleep(poll_seconds)

    threading.Thread(target=loop, daemon=True, name="sources-chapter-scheduler").start()
