"""The chapter check's cycle and scheduler: runs sources.chapter_check over every tracked series as a background job, then auto-imports or saves what it found."""

import threading
import time

import background_jobs
from jobs import job_launch
import db
from lib.errors import ConflictError
from engine_backends.shared import redact_for_storage

from sources import chapter_check, extension_marker, registry, store
from sources.models import SourceError

CHECK_JOB_ID = "sources_chapter_check"
# A cycle's claim expires after this long, so a process that died
# mid-cycle doesn't block checks forever. Well above a paced cycle's length.
CYCLE_LEASE_SECONDS = 2 * 3600
_scheduler_started = False
_scheduler_lock = threading.Lock()
_scheduler_stop = threading.Event()   # set by stop_scheduler (app shutdown)


def run_check_cycle(job_id: str | None = None, adapter_factory=None, scheduled: bool = False,
                    allow_browser: bool = True) -> dict:
    """One pass over every tracked series. `adapter_factory(name)` is
    injectable for tests; defaults to the registry.

    Safe to run from two processes that each run a scheduler: the cycle
    is claimed first (store.claim_check_cycle), and a cycle that can't
    claim returns {"skipped": True, ...} without checking anything. A
    `scheduled` cycle is also skipped when another process finished one
    within the interval since this one was found due.

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
    extension_only = extension_marker.marked_sources()
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
        if row["source"] in extension_only:
            # Not a failure and not a check: the person reads these through the extension.
            summary.setdefault("extension_only", []).append(row["title"])
            continue
        try:
            adapter = factory(row["source"])
            adapter.allow_browser = allow_browser and adapter.allow_browser
            new = chapter_check.check_series(adapter, row)
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
        if row.get("save_cbz"):
            _save_new(adapter, row, new, summary)
        if new and auto_queue and row.get("drama_id"):
            from sources.pipeline import start_import
            if db.get_drama(row["drama_id"]) is None or not _link_owner_can_edit(row):
                summary["errors"][row["title"]] = LINK_UNAVAILABLE
                store.mark_checked(row["source"], row["series_id"], error=LINK_UNAVAILABLE)
                continue
            try:
                queued = start_import(row["source"], row["series_id"], new, row["drama_id"],
                                      series_url=row.get("url") or "")
            except ConflictError as e:
                # The drama is being deleted or cleaned up right now. The new
                # chapters are already known, so a later check won't offer
                # them again: they wait in the retry list instead.
                error = redact_for_storage(str(e))[:300]
                summary["errors"][row["title"]] = error
                store.record_import_retry(
                    row["source"], row["series_id"], row["drama_id"],
                    [(c.chapter_id, redact_for_storage(c.title or ""), "not_attempted", error)
                     for c in new])
                store.mark_checked(row["source"], row["series_id"], error=error)
                continue
            if queued:
                summary["queued"].append(row["title"])
    store.set_setting("last_check_cycle", time.time())
    if job_id:
        background_jobs.set_result(job_id, summary)
    return summary


def _save_new(adapter, row, new, summary):
    """Saves a series' new chapters as CBZ files, plus the ones an earlier
    check failed to save: the chapters are marked known before this runs,
    so a failed save is kept in `save_pending` and retried each check until
    it is saved (or the site no longer lists it). A failure is reported in
    the summary like a check error; the chapters stay announced either way."""
    from services.sources_save_service import save_series_chapters
    ids = list(dict.fromkeys(store.save_pending_ids(row) + [str(c.chapter_id) for c in new]))
    if not ids:
        return
    try:
        rows, _ = save_series_chapters(adapter, row["source"], row["series_id"], ids,
                                       owner_user_id=row.get("save_cbz_by_user_id"))
    except Exception as e:
        store.set_save_pending(row["source"], row["series_id"], ids)
        summary["errors"][row["title"]] = f"Saving as CBZ: {redact_for_storage(str(e))[:300]}"
        return
    failed = [r for r in rows if r["outcome"] in ("failed", "not_attempted")]
    store.set_save_pending(row["source"], row["series_id"], [r["chapter_id"] for r in failed])
    if any(r["outcome"] == "saved" for r in rows):
        summary["saved"].append(row["title"])
    if failed:
        summary["errors"][row["title"]] = (f"Saving as CBZ: {len(failed)} chapter(s) not saved, "
                                           f"retried next check ({failed[0].get('error') or 'failed'})")


def check_due(now: float | None = None) -> bool:
    hours = float(store.get_setting("check_interval_hours") or 0)
    if hours <= 0:
        return False
    last = store.get_setting("last_check_cycle") or 0
    now = time.time() if now is None else now
    return now - float(last) >= hours * 3600


def start_check_now(scheduled: bool = False, allow_browser: bool = True) -> bool:
    return job_launch.start_job(CHECK_JOB_ID, run_check_cycle, CHECK_JOB_ID,
                                     scheduled=scheduled, allow_browser=allow_browser,
                                     description="Checking tracked series for new chapters",
                                     line_fields=())


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
