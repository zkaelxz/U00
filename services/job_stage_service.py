"""
job_stage_service.py -- names what a running thread job is doing right now, so its
status reads as a stage ("Chunk 3: translating with qwen3:8b") instead of a
bare spinner, and says plainly when a step is slow or can't be interrupted.

The worker is usually blocked inside the very call being described, so the
slow-step note and the cancel text are applied when the status is read
(annotate), not written by the worker.
"""
import threading
import time

import background_jobs

_timers = {}   # job_id -> the pending slow-note timer of its current stage


def set_stage(job_id: str, message: str, cancel_message: str = None,
              slow_after: float = None, slow_note: str = None):
    """Called from inside the job's thread.

    cancel_message: shown instead of the generic cancelling text once a
    cancel is requested, for a stage that cannot be interrupted (what is
    awaited, and roughly how long).
    slow_after/slow_note: once the stage has run slow_after seconds, the
    status gains slow_note ("{secs}" is the elapsed seconds). A change event
    is pushed at that moment too, since a page that listens instead of
    polling would otherwise not hear about it."""
    with background_jobs._lock:
        job = background_jobs._jobs.get(job_id)
        if job is None:
            return
        since = time.time()
        job["stage"] = {"since": since, "cancel_message": cancel_message,
                        "slow_after": slow_after, "slow_note": slow_note}
        job["progress_at"] = since
        # While cancelling, annotate() keeps showing the cancel text.
        if not job.get("cancel_requested"):
            job["message"] = message
        background_jobs._mirror_locked(job_id)
        background_jobs._emit_change(job_id)
    previous = _timers.pop(job_id, None)
    if previous is not None:
        previous.cancel()
    if slow_after is not None and slow_note:
        def announce():
            with background_jobs._lock:
                current = background_jobs._jobs.get(job_id)
                still_here = bool(current and (current.get("stage") or {}).get("since") == since)
            if still_here:
                background_jobs._emit_change(job_id)
        timer = threading.Timer(slow_after + 0.5, announce)
        timer.daemon = True
        _timers[job_id] = timer
        timer.start()


def annotate(job):
    """A get_status snapshot with the stage's cancel text, or its slow note,
    applied to the message. Returns the snapshot (None stays None)."""
    stage = (job or {}).get("stage")
    if not stage or job.get("status") != "running":
        return job
    if job.get("cancel_requested"):
        if stage.get("cancel_message"):
            job["message"] = stage["cancel_message"]
        return job
    elapsed = time.time() - stage["since"]
    if stage.get("slow_note") and stage.get("slow_after") is not None and elapsed >= stage["slow_after"]:
        note = stage["slow_note"].replace("{secs}", str(int(elapsed)))
        job["message"] = f"{job.get('message') or ''} {note}".strip()
    return job
