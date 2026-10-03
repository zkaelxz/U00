"""
sources/chapter_check.py -- scheduled checks for new chapters on tracked
series (Step 23 item 5), the same simple shape as Mihon's library update:
re-fetch each tracked title's chapter list, diff by chapter id against
what's already known, and tell the person about anything new.

It notifies; it does not download. Auto-queueing new chapters for import
is a separate opt-in setting, off by default, and so is saving a series'
new chapters as CBZ files (per series, `save_cbz`), done within the cycle. Every check goes through
the adapter's own paced client, so a check cycle hits a source no harder
than a manual chapter-list fetch would.
"""

import threading
import time

import background_jobs
import db
from translate_engines import redact_for_storage

from . import http, ladder, registry, store
from .models import SourceError

CHECK_JOB_ID = "sources_chapter_check"
# A cycle's claim expires after this long, so a process that died
# mid-cycle doesn't block checks forever. Well above a paced cycle's length.
CYCLE_LEASE_SECONDS = 2 * 3600
# Saved chapter-list validators older than this are ignored and the list
# is fetched in full, so a server that wrongly keeps answering 304 can't
# hide new chapters for longer than this.
VALIDATOR_MAX_AGE_SECONDS = 7 * 24 * 3600
_scheduler_started = False
_scheduler_lock = threading.Lock()
_scheduler_stop = threading.Event()   # set by stop_scheduler (app shutdown)


def check_series(adapter, row: dict) -> list:
    """Returns the ChapterInfo list of chapters that are new since the last
    check, and records them (known + a notification each).

    Step 106: the chapter list is fetched as a conditional re-poll. When
    the last poll was one plain GET that returned an ETag or Last-Modified,
    this poll sends them back; a 304 means nothing changed, so the list is
    neither downloaded nor parsed."""
    ladder.check_terms(adapter.name, adapter.capabilities())
    source, series_id = row["source"], row["series_id"]
    saved = store.poll_validators(source, series_id, max_age=VALIDATOR_MAX_AGE_SECONDS)
    with http.conditional_poll(**saved) as poll:
        try:
            chapters = adapter.get_chapters(series_id)
        except http.NotModified:
            chapters = None
        finally:
            if poll.untrusted_304:
                # The validated URL now redirects somewhere that answered
                # 304: forget the validators so the next poll is a full fetch.
                try:
                    store.save_poll_validators(source, series_id, None)
                except Exception:
                    pass
    if poll.not_modified:
        # Even if an adapter swallowed NotModified and returned something,
        # the one request it made said "unchanged".
        store.mark_checked(source, series_id)
        return []
    known = store.known_chapter_ids(source, series_id)
    new = [c for c in chapters if c.chapter_id not in known]
    if new:
        # Only what this call actually recorded: another process checking
        # the same series at the same moment gets the rest.
        new = store.record_new_chapters(source, series_id, new)
    # Saved only after the new chapters are recorded, so a 304 next time
    # can never hide a chapter this poll saw. Best effort: failing here
    # must not stop the auto-import of chapters already recorded (the old
    # validators then just get a 200 next time).
    try:
        store.save_poll_validators(source, series_id, poll.validators())
    except Exception:
        pass
    store.mark_checked(source, series_id)
    return new


def run_check_cycle(job_id: str = None, adapter_factory=None, scheduled: bool = False,
                    allow_browser: bool = True) -> dict:
    """One pass over every tracked series. `adapter_factory(name)` is
    injectable for tests; defaults to the registry.

    Safe to run from two processes (the API and Streamlit each run a
    scheduler): the cycle is claimed first (store.claim_check_cycle), and a
    cycle that can't claim returns {"skipped": True, ...} without checking
    anything. A `scheduled` cycle is also skipped when another process
    finished one within the interval since this one was found due.

    `allow_browser=False` (a manual check from another device) keeps every
    adapter from launching a browser on this PC; scheduled cycles are local."""
    now = time.time()
    min_gap = float(store.get_setting("check_interval_hours") or 0) * 3600 if scheduled else 0.0
    token = store.claim_check_cycle(now, CYCLE_LEASE_SECONDS, min_gap)
    if token is None:
        summary = {"checked": 0, "new": 0, "errors": {}, "queued": [], "skipped": True}
        if job_id:
            background_jobs.set_result(job_id, summary)
        return summary
    try:
        return _run_claimed_cycle(job_id, adapter_factory, allow_browser)
    finally:
        store.release_check_cycle(token)


# One generic text for every skipped auto-import: tracked series are listed
# household-wide, so it must not say whether the drama was deleted, went
# private or its linker lost access.
LINK_UNAVAILABLE = "Auto-import skipped: the linked drama is not available."


def _link_owner_can_edit(row) -> bool:
    """The cycle has no request principal, so a link made by a user imports
    only while that user could still make it: a drama shared at link time
    may since have gone private, the user been removed or lost the
    sources.import permission the link needed. A NULL owner
    (auth off / the PC owner made the link) imports as before."""
    uid = row.get("linked_by_user_id")
    if uid is None:
        return True
    from services import auth_service, ownership_service
    principal = auth_service.member_principal(uid)
    return (principal is not None and "sources.import" in principal["permissions"]
            and ownership_service.can_edit_drama(principal, row["drama_id"]))


def _run_claimed_cycle(job_id, adapter_factory, allow_browser: bool = True) -> dict:
    factory = adapter_factory or (lambda name: registry.get_adapter(name))
    rows = store.list_tracked_series()
    summary = {"checked": 0, "new": 0, "errors": {}, "queued": [], "saved": []}
    auto_queue = bool(store.get_setting("auto_queue_new_chapters"))
    for i, row in enumerate(rows, start=1):
        if job_id:
            if background_jobs.is_cancel_requested(job_id):
                break
            background_jobs.update_progress(job_id, (i - 1) / max(len(rows), 1),
                                            f"Checking {row['title']} ({i}/{len(rows)})")
        if row["source"] in registry.REMOVED_SOURCES:
            summary["errors"][row["title"]] = registry.SOURCE_REMOVED
            store.mark_checked(row["source"], row["series_id"], error=registry.SOURCE_REMOVED)
            continue
        if not registry.is_enabled(row["source"]):
            continue
        try:
            adapter = factory(row["source"])
            adapter.allow_browser = allow_browser and adapter.allow_browser
            new = check_series(adapter, row)
        except SourceError as e:
            summary["errors"][row["title"]] = f"{e.reason.value}: {e}"
            store.mark_checked(row["source"], row["series_id"], error=redact_for_storage(str(e))[:300])
            continue
        except Exception as e:
            summary["errors"][row["title"]] = f"{type(e).__name__}: {e}"
            store.mark_checked(row["source"], row["series_id"], error=redact_for_storage(str(e))[:300])
            continue
        summary["checked"] += 1
        summary["new"] += len(new)
        if new and row.get("save_cbz"):
            _save_new(adapter, row, new, summary)
        if new and auto_queue and row.get("drama_id"):
            from .pipeline import start_import
            if db.get_drama(row["drama_id"]) is None or not _link_owner_can_edit(row):
                summary["errors"][row["title"]] = LINK_UNAVAILABLE
                store.mark_checked(row["source"], row["series_id"], error=LINK_UNAVAILABLE)
                continue
            if start_import(row["source"], row["series_id"], new, row["drama_id"]):
                summary["queued"].append(row["title"])
    store.set_setting("last_check_cycle", time.time())
    if job_id:
        background_jobs.set_result(job_id, summary)
    return summary


def _save_new(adapter, row, new, summary):
    """Saves a series' new chapters as CBZ files. A failure is reported in
    the summary like a check error; the chapters stay announced either way."""
    from services.sources_save_service import save_series_chapters
    try:
        rows, _ = save_series_chapters(adapter, row["source"], row["series_id"],
                                       [c.chapter_id for c in new])
    except Exception as e:
        summary["errors"][row["title"]] = f"Saving as CBZ: {redact_for_storage(str(e))[:300]}"
        return
    failed = [r for r in rows if r["outcome"] in ("failed", "not_attempted")]
    if any(r["outcome"] == "saved" for r in rows):
        summary["saved"].append(row["title"])
    if failed:
        summary["errors"][row["title"]] = (f"Saving as CBZ: {len(failed)} chapter(s) not saved"
                                           f" ({failed[0].get('error') or 'failed'})")


def check_due(now: float = None) -> bool:
    hours = float(store.get_setting("check_interval_hours") or 0)
    if hours <= 0:
        return False
    last = store.get_setting("last_check_cycle") or 0
    now = time.time() if now is None else now
    return now - float(last) >= hours * 3600


def start_check_now(scheduled: bool = False, allow_browser: bool = True) -> bool:
    return background_jobs.start_job(CHECK_JOB_ID, run_check_cycle, CHECK_JOB_ID,
                                     scheduled=scheduled, allow_browser=allow_browser,
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
        while not _scheduler_stop.is_set():
            try:
                if store.list_tracked_series() and check_due():
                    start_check_now(scheduled=True)
            except Exception:
                pass    # a transient DB/lock hiccup shouldn't kill the scheduler
            _scheduler_stop.wait(poll_seconds)

    threading.Thread(target=loop, daemon=True, name="sources-chapter-scheduler").start()


def stop_scheduler() -> None:
    """Stops the scheduler for good in this process (the app's clean
    shutdown, services/shutdown_service.py): it starts no more checks."""
    _scheduler_stop.set()
