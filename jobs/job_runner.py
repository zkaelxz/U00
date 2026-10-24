"""jobs/job_runner.py -- runs a started job and handles how it ends.

The thread runner (_spawn) and the process watcher (_process_watcher) record
each run's outcome, free its GPU slot and promote the next queued GPU job;
also the failed-start path, dead-worker reconciliation and waiting for job
threads to exit. Deciding whether a job may start is jobs/job_launch.py's.
"""

import multiprocessing
import queue
import threading
import time
import traceback

import db
from engine_backends.redaction import redact_secrets
import gpu_probe
import job_process_result
from background_jobs import (CANCELLING_MESSAGE, JobCancelled, _db_cancel_requested, _forget_cancel_check,
                             _interrupted_by_stop_locked, is_cancel_requested, stage_ticker, update_progress)
from job_process_kill import _stop_process, kill_tree, reap_worker
from jobs import user_caps
from jobs.gpu_queue import (GPU_WAIT_MESSAGE, _gpu_queue, _gpu_slot_available_locked, _refresh_queue_messages_locked,
                            _release_gpu_slot, _run_finish_hooks, _running_gpu_job_count_locked)
from jobs.job_settings import _notify_job_finished, get_gpu_max_parallel
from jobs.job_table import _job_threads, _jobs, _lock, _mirror_locked, _warn, _workers


def _timing_start(job_id, thread_job=True):
    """(services/job_timing_service): a job's per-stage
    timing run starts when it actually runs. Never raises."""
    try:
        from services import job_timing_service
        token = job_timing_service.start_run(job_id)
        if thread_job:
            job_timing_service.set_current_job(job_id)
        return token
    except Exception:
        return None


def _timing_finish(job_id, token, thread_job=True):
    """`token` keeps a new run of the same job id, started the moment this
    one was marked finished, from being closed by this one."""
    try:
        from services import job_timing_service
        job_timing_service.finish_run(job_id, token=token)
        if thread_job:
            job_timing_service.set_current_job(None)
    except Exception:
        pass


def _failed_outcome_error(result, job_id=""):
    """The error text for a worker that returned instead of raising but
    reports a failure (jobs_service.FAILED_REASON_MESSAGES), else None. The
    job then ends as `error`, so the bell and the Jobs list say Failed, not
    Finished. Other failure reasons stay `done`: their stage panels read
    the done job's result for their own wording."""
    if not isinstance(result, dict):
        return None
    reason = result.get("failed_reason")
    if reason == "dependency_missing":
        return str(result.get("detail") or "A required component is not installed.")
    from services import jobs_service
    if reason == "model_download":
        text = jobs_service.model_download_message(job_id)
    elif reason in jobs_service.FAILED_REASON_MESSAGES:
        text = jobs_service.FAILED_REASON_MESSAGES[reason]
    else:
        return None
    detail = result.get("detail")
    # The same plain text the outcome line shows; scrubbed because the
    # detail comes from a download, model or provider error.
    return jobs_service.scrub_text(f"{text} {detail}" if detail else text)


def _soft_failure_text(result):
    """The reason text for a job that ended `done` but whose result says it
    failed (a reason not in FAILED_REASON_MESSAGES keeps the status for
    the stage panels), else None. Only the notifications read it: the bell
    and the desktop toast must say Failed where the Jobs list does."""
    from services import jobs_service
    outcome, message = jobs_service.derive_outcome("done", None, result)
    return jobs_service.scrub_text(message) if outcome == "failed" else None


def _done_note(result):
    """Short plain note a finished job adds to its bell text, or None."""
    from services import jobs_service
    return jobs_service.speaker_skip_text(result)


def _line_fields(run):
    """The line columns this run declared at launch (job_launch.start_job)."""
    return (run or {}).get("line_fields")


def _run_on_done(job_id, run, on_done, payload):
    with db.job_line_fields(job_id, _line_fields(run)):
        return on_done(job_id, payload)


def _spawn(job_id, target, args, kwargs, gpu_touching=False, run=None):
    def runner():
        import applog
        logger = applog.get_logger()
        logger.info(f"job {job_id} started")
        _timing = _timing_start(job_id)
        try:
            with db.job_line_fields(job_id, _line_fields(run)):
                target(*args, **kwargs)
            _description = _owner = _reason = None
            _with_errors = False
            _outcome = "done"
            _recorded = False
            with _lock:
                if _still_running_locked(job_id, run):
                    _recorded = True
                    _result = _jobs[job_id].get("result")
                    _with_errors = isinstance(_result, dict) and bool(_result.get("errors"))
                    _failed = _failed_outcome_error(_result, job_id)
                    _jobs[job_id]["status"] = "error" if _failed else "done"
                    if _failed:
                        _outcome = "error"
                        _reason = _failed
                        _jobs[job_id]["error"] = _failed
                        _jobs[job_id]["message"] = ""
                    else:
                        _jobs[job_id]["progress"] = 1.0
                        _finish_message_locked(_jobs[job_id])
                        _reason = _soft_failure_text(_result)
                        if _reason:
                            _outcome = "error"
                        else:
                            _reason = _done_note(_result)
                    _jobs[job_id]["finished_at"] = time.time()
                    _description = _jobs[job_id].get("description")
                    _owner = _jobs[job_id].get("owner_user_id")
                    _mirror_locked(job_id)
            logger.info(f"job {job_id} finished")
            # A run that was cleared, or replaced by a new one, tells no one.
            if _recorded:
                _notify_job_finished(_description, _outcome, job_id=job_id, owner_user_id=_owner,
                                     with_errors=_with_errors, reason=_reason)
        except JobCancelled:
            with _lock:
                _mark_cancelled_locked(job_id, run)
            logger.info(f"job {job_id} cancelled")
        except BaseException as exc:
            # BaseException too: a SystemExit from job code would otherwise
            # leave the job "running" forever. Not re-raised: it would only
            # end this thread, which ends here anyway, and KeyboardInterrupt
            # is only ever delivered to the main thread.
            error_msg = redact_secrets(f"{type(exc).__name__}: {exc}")
            tb = redact_secrets(traceback.format_exc())
            _description = _owner = None
            _recorded = False
            with _lock:
                if _still_running_locked(job_id, run):
                    _recorded = True
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["error"] = error_msg
                    _jobs[job_id]["traceback"] = tb
                    _jobs[job_id]["finished_at"] = time.time()
                    _description = _jobs[job_id].get("description")
                    _owner = _jobs[job_id].get("owner_user_id")
                    _mirror_locked(job_id)
            logger.error(f"job {job_id} failed: {error_msg}\n{tb}")
            if _recorded:
                _notify_job_finished(_description, "error", job_id=job_id, owner_user_id=_owner)
        finally:
            _timing_finish(job_id, _timing)
            _forget_cancel_check(job_id, run)
            _release_gpu_slot(job_id, gpu_touching, run)
            _promote_next_queued_gpu_job()

    _start_job_thread(runner, f"job:{job_id}", worker_for=job_id, worker_run=run)


def _finish_message_locked(job):
    """Caller holds _lock. A cancel that arrived while the job was finishing
    leaves the job `done` (the work completed), so it must stop saying
    "Cancelling..." in the persisted row."""
    if job.get("message") == CANCELLING_MESSAGE:
        job["message"] = FINISHED_MESSAGE


def _still_running_locked(job_id, run=None) -> bool:
    """Caller holds _lock. A worker records its outcome only over "running":
    a job already ended (cancelled while queued, or marked interrupted by
    reconcile_dead_workers) keeps that state when a late worker returns.
    `run`: the worker's own `_jobs` dict, so a run cleared while it worked
    never records its outcome over a new run started under the same id."""
    job = _jobs.get(job_id)
    return (job is not None and (run is None or job is run)
            and job.get("status") == "running")


def _fail_start(job_id, gpu_touching, exc, proc=None, result_queue=None, run=None):
    """The job was marked "running" but its thread or process could not be
    started (thread limit, fork or pickling failure). Without this the
    record stays "running" forever: the id can't be restarted, a restore
    or reset is refused and the GPU lock is never released. The process
    and its result queue are closed so their pipe fds aren't leaked."""
    import applog
    error_msg = redact_secrets(f"Could not start the job: {type(exc).__name__}: {exc}")
    if proc is not None:
        try:
            if proc.is_alive():
                _stop_process(proc)
        except Exception:
            pass
        try:
            proc.close()
        except Exception:
            pass
    if result_queue is not None:
        try:
            result_queue.close()
            result_queue.discard()
        except Exception:
            pass
    with _lock:
        job = _jobs.get(job_id)
        # "queued": a promoted process job whose Process could not even be
        # built is still its queued run.
        if job is not None and (job.get("status") == "running" or (
                job.get("status") == "queued" and run is not None and job is run)):
            job["status"] = "error"
            job["error"] = error_msg
            job["finished_at"] = time.time()
            _mirror_locked(job_id)
    applog.get_logger().error(f"job {job_id} failed to start: {error_msg}")
    _release_gpu_slot(job_id, gpu_touching, run)
    _promote_next_queued_gpu_job()


def _start_job_thread(target, name, *args, worker_for=None, worker_run=None, **kwargs):
    def run():
        try:
            target(*args, **kwargs)
        finally:
            with _lock:
                _job_threads.discard(thread)

    thread = threading.Thread(target=run, daemon=True, name=name)
    thread.baihe_job_id = worker_for
    # Started under the lock so wait_for_job_threads never sees (and tries
    # to join) a thread that is registered but not yet started.
    with _lock:
        thread.start()
        _job_threads.add(thread)
        # Only over its own run: one cleared or replaced before this thread
        # started must not have the new run's worker slot.
        if worker_for is not None and worker_for in _jobs and (
                worker_run is None or _jobs[worker_for] is worker_run):
            _workers[worker_for] = (_jobs[worker_for], thread)


WORKER_LOST_MESSAGE = "Interrupted: the job's worker stopped without reporting back."


def reconcile_dead_workers() -> list:
    """Marks every running job whose worker thread has exited as failed
    (WORKER_LOST_MESSAGE), releasing its GPU slot, so it can't read as
    running forever. A worker always records an outcome before it exits,
    so this only catches one that died without doing so. Returns the ids."""
    lost = []
    with _lock:
        for job_id, (job, thread) in list(_workers.items()):
            if _jobs.get(job_id) is not job:
                _workers.pop(job_id, None)
                continue
            if job.get("status") != "running" or thread.is_alive():
                continue
            job["status"] = "error"
            job["error"] = WORKER_LOST_MESSAGE
            job["detail_state"] = "lost"
            job["finished_at"] = time.time()
            _mirror_locked(job_id)
            lost.append((job_id, job))
    for job_id, job in lost:
        proc = job.get("process")
        try:
            if proc is not None and proc.is_alive():
                _stop_process(proc)
        except Exception as exc:
            _warn(f"job {job_id}: could not stop its process", exc)
        _release_gpu_slot(job_id, job.get("gpu_touching"), job)
    if lost:
        _promote_next_queued_gpu_job()
    return [job_id for job_id, _ in lost]


def wait_for_job_threads(timeout: float, job_ids=None) -> bool:
    """Joins every job thread that has not exited, including one whose job
    already reads as finished or was cleared. True once none is left,
    False if some thread outlived `timeout` seconds. Call it with no job
    running or queued (under acquire_exclusive), before replacing the
    database, or it waits on real work. job_ids: only those jobs' threads."""
    deadline = time.monotonic() + timeout
    with _lock:
        threads = [t for t in _job_threads if t is not threading.current_thread()
                   and (job_ids is None or getattr(t, "baihe_job_id", None) in job_ids)]
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    import live_whisper
    return not any(t.is_alive() for t in threads) and (
        job_ids is not None or live_whisper.wait_for_outstanding(deadline - time.monotonic()))


def _promote_next_queued_gpu_job():
    """Called whenever a GPU-touching job/slot finishes -- starts the next
    queued GPU-touching job, if the GPU is actually free and anything is
    still waiting. Skips (and drops) queue entries that were cleared out
    from under the queue in the meantime. Dispatches to the thread-based
    or process-based starter depending on how that entry was queued.

    The GPU can also be free-in-this-process but still held
    cross-process (another process) -- checked via _gpu_slot_available_locked,
    same as start_job/start_process_job. If that's the case, this leaves
    the entry at the head of the queue and returns rather than popping it;
    there's no cross-process notification when the other process later releases it,
    so a still-queued job resumes the next time some other job's finish
    triggers this function again, not immediately -- an accepted latency
    for this same soft, best-effort guard, not a correctness gap (the GPU
    is never actually shared, it just may sit idle a bit before the queued
    job notices)."""
    dropped = []
    try:
        _promote_one_queued_gpu_job(dropped)
    finally:
        _run_finish_hooks(dropped)


def _next_promotable_entry_locked(dropped):
    """Caller holds _lock. Drops entries whose job was cleared or ended
    (their finish hooks go to `dropped`) and returns the first one left
    whose starter is under their running cap (jobs/user_caps.py), else
    None. A held entry keeps its place in line and can't deadlock: it
    becomes promotable when one of its starter's running jobs ends, and
    every job's end runs a promotion."""
    for entry in list(_gpu_queue):
        job = _jobs.get(entry["job_id"])
        if job is None or job["status"] != "queued":
            _gpu_queue[:] = [e for e in _gpu_queue if e is not entry]
            if entry.get("on_finish") is not None:
                dropped.append((entry["job_id"], entry["on_finish"]))
            continue
        if not user_caps.held(_jobs, entry):
            return entry
    return None


def _promote_one_queued_gpu_job(dropped):
    """_promote_next_queued_gpu_job's body. Appends the finish hooks of the
    entries it drops, and of a promoted process job that fails to start, to
    `dropped`, which the caller runs once _lock is released."""
    while True:
        if _gpu_queue:
            gpu_probe.prefetch()
        with _lock:
            if not _gpu_queue or _running_gpu_job_count_locked(None) >= get_gpu_max_parallel():
                return
            entry = _next_promotable_entry_locked(dropped)
            if entry is None:
                return
            job_id = entry["job_id"]
            if not _gpu_slot_available_locked(job_id, entry["description"]):
                return
            _gpu_queue[:] = [e for e in _gpu_queue if e is not entry]
            _refresh_queue_messages_locked()
            if entry.get("kind") == "process":
                on_done = entry.get("on_done")
                on_finish = entry.get("on_finish")
                kill_whole_tree = entry.get("kill_whole_tree", False)
                run = _jobs[job_id]
                proc = result_queue = None
                build_error = None
                try:
                    proc, result_queue = _register_process_job(
                        job_id, entry["target"], entry["args"],
                        run["gpu_touching"], entry["description"],
                        run.get("owner_user_id"), start_method=entry.get("start_method"),
                        run_settings=run.get("run_settings"),
                        initial_result=run.get("result"), line_fields=_line_fields(run))
                    run = _jobs[job_id]
                except Exception as exc:
                    # mp.Queue()/Process() can fail (no fds, no /dev/shm)
                    # after the GPU row was claimed and the entry popped:
                    # handled below like a failed start, so the job ends,
                    # its row is released and its on_finish runs once.
                    build_error = exc
                break
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["message"] = "Starting..."
            _jobs[job_id]["started_at"] = time.time()
            _mirror_locked(job_id)
            target, args, kwargs = entry["target"], entry["args"], entry["kwargs"]
            run = _jobs[job_id]
            break
    try:
        if entry.get("kind") == "process":
            if build_error is not None:
                raise build_error
            proc.start()
            _start_job_thread(_process_watcher, f"job-watcher:{job_id}",
                              job_id, proc, result_queue, True, on_done=on_done,
                              on_finish=on_finish, kill_whole_tree=kill_whole_tree, run=run,
                              worker_for=job_id, worker_run=run)
        else:
            _spawn(job_id, target, args, kwargs, gpu_touching=True, run=run)
    except Exception as exc:
        # Runs in another job's finishing thread: record it, don't raise.
        if entry.get("kind") == "process":
            _fail_start(job_id, True, exc, proc, result_queue, run=run)
            if on_finish is not None:
                dropped.append((job_id, on_finish))
        else:
            _fail_start(job_id, True, exc, run=run)


def _register_process_job(job_id, target, args, gpu_touching, description, owner_user_id=None,
                          start_method=None, run_settings=None,
                          initial_result=None, line_fields=None):
    """Caller must already hold _lock. Builds the Process and its result
    queue and records the job dict entry, but doesn't call proc.start()
    itself -- constructing a Process is cheap, but actually starting one
    (forking/spawning a real OS process) shouldn't happen while holding
    _lock, so callers start it themselves right after releasing the
    lock. Returns (proc, result_queue) for that. start_method None keeps
    the platform's default context."""
    mp = multiprocessing.get_context(start_method) if start_method else multiprocessing
    result_queue = job_process_result.wrap_queue(mp.Queue(), job_id)
    proc = mp.Process(target=target, args=(*args, result_queue), daemon=True)
    _jobs[job_id] = _new_entry("running", "process", gpu_touching, description, owner_user_id,
                               run_settings, result=initial_result, process=proc,
                               line_fields=line_fields)
    _mirror_locked(job_id)
    return proc, result_queue


def _new_entry(status, kind, gpu_touching, description, owner_user_id, run_settings,
               result=None, **extra) -> dict:
    """A new run's `_jobs` entry, "queued" or "running". A process job passes
    process= (None while queued)."""
    return {
        "status": status, "progress": 0.0,
        "message": GPU_WAIT_MESSAGE if status == "queued" else "Starting...",
        "error": None, "started_at": time.time(), "finished_at": None,
        "cancel_requested": False, "result": result,
        "gpu_touching": gpu_touching, "description": description, "kind": kind,
        "owner_user_id": owner_user_id, "run_settings": run_settings, **extra,
    }


def _apply_progress_item(job_id, item, stage=None) -> bool:
    """True if `item` was a ("progress", frac, message) or ("stage", frac,
    message) tuple (applied to the job, with the message secret-redacted);
    False for anything else, which is the worker's final result tuple.
    `stage` holds the watcher's running stage_ticker under "ticker": any
    progress or stage item stops it, a stage item with a message starts a
    new one."""
    if not (isinstance(item, tuple) and item and item[0] in ("progress", "stage")):
        return False
    try:
        kind, frac, message = item
        message = redact_secrets(str(message or ""))
        if stage is not None and stage.get("ticker") is not None:
            stage.pop("ticker").stop()
        if kind == "progress":
            update_progress(job_id, float(frac), message)
        elif message and stage is not None:
            stage["ticker"] = stage_ticker(job_id, message, frac=float(frac)).start()
    except Exception:
        pass   # malformed progress must never kill the watcher
    return True


CANCELLED_MESSAGE = "Cancelled."
FINISHED_MESSAGE = "Finished."


def _mark_cancelled_locked(job_id, run=None):
    """Caller holds _lock. A process job's subprocess was stopped (or its
    result dropped) because of a cancel: the record stops saying
    "Cancelling..."."""
    if _still_running_locked(job_id, run):
        job = _jobs[job_id]
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        if job.get("cancel_requested"):
            job["message"] = CANCELLED_MESSAGE
        _interrupted_by_stop_locked(job)
        _mirror_locked(job_id)


# The watcher's steps return this when the job was cancelled or cleared and
# must record nothing further.
_STOPPED = object()


def _process_watcher(job_id, proc, result_queue, gpu_touching=False, poll_interval=0.3,
                     on_done=None, on_finish=None, kill_whole_tree=False, run=None):
    """Runs in this (the main) process, not the child -- a
    multiprocessing.Process can't write back into this process's _jobs
    dict itself (separate memory space), so this polls proc.is_alive()
    and this job's cancel_requested flag, and is the one place that
    actually calls proc.terminate() for a real, non-cooperative stop.
    Also terminates the process if its job record is cleared out from
    under it while still running (clear_job() on a thread-based job can
    only leave it running invisibly; here, holding the real Process
    object, cleanup can be immediate and complete instead)."""
    import applog
    logger = applog.get_logger()
    _timing = _timing_start(job_id, thread_job=False)
    stage = {}
    reaped = False
    try:
        outcome = _poll_for_outcome(job_id, proc, result_queue, poll_interval, stage,
                                    kill_whole_tree, run, logger)
        if outcome is _STOPPED:
            return
        if outcome is None:
            outcome = _drain_after_exit(job_id, result_queue, stage)
        _stop_stage_ticker(stage)
        # Before the job's final status: "done" must mean the worker is gone.
        reap_worker(proc, kill_whole_tree)
        reaped = True
        hook_error = None
        result = outcome[1] if outcome and outcome[0] == "ok" else None
        if outcome and outcome[0] == "ok" and on_done is not None:
            hooked = _run_completion_hook(job_id, run, on_done, outcome[1], logger)
            if hooked is _STOPPED:
                return
            returned, hook_error = hooked
            if returned is not None:
                result = returned
        finished = _record_final_status(job_id, run, proc, outcome, result, hook_error, logger)
        if finished is None:
            return
        _description, _status, _owner, _reason = finished
        _notify_job_finished(_description, _status, job_id=job_id, owner_user_id=_owner,
                             reason=_reason)
    except Exception as exc:
        _record_watcher_failure(job_id, proc, run, exc, logger)
    finally:
        _end_watch(job_id, proc, result_queue, gpu_touching, on_finish, kill_whole_tree, run,
                   stage, reaped, _timing)


def _stop_stage_ticker(stage):
    if stage.get("ticker") is not None:
        stage.pop("ticker").stop()


def _stop_requested(job_id, run):
    """(should_stop, was_cleared): the job asked to stop, or its record was
    cleared (and maybe replaced by a new run of this id)."""
    with _lock:
        job = _jobs.get(job_id)
        if run is not None and job is not run:
            job = None   # cleared, and maybe replaced by a new run of this id
        should_stop = job is None or job.get("cancel_requested")
    if not should_stop and _db_cancel_requested(job_id):
        should_stop = True
    return should_stop, job is None


def _stop_for_cancel(job_id, proc, kill_whole_tree, run, was_cleared, logger):
    # terminate()/join() happen OUTSIDE the lock -- join can block
    # for real seconds, and nothing else here should have to wait
    # on that (another job's update_progress, a UI's get_status).
    if kill_whole_tree:
        kill_tree(proc)
        # SIGKILL is async; "cancelled" must mean the worker is gone.
        proc.join(timeout=5)
    else:
        _stop_process(proc)
    with _lock:
        _mark_cancelled_locked(job_id, run)
    logger.info(f"job {job_id} {'cleared' if was_cleared else 'cancelled'} "
               f"(subprocess terminated)")


def _poll_for_outcome(job_id, proc, result_queue, poll_interval, stage, kill_whole_tree, run,
                      logger):
    """While the worker runs: applies its progress items and stops it on a
    cancel. Returns its final item, None if it exited before one was read,
    or _STOPPED after a cancel stopped it."""
    while True:
        should_stop, was_cleared = _stop_requested(job_id, run)
        if should_stop:
            _stop_for_cancel(job_id, proc, kill_whole_tree, run, was_cleared, logger)
            return _STOPPED
        # Drain the queue continuously while the process is
        # still alive, not only after it exits. A child that has put()
        # more onto the queue than fits in one OS pipe buffer (~64KB on
        # Linux) cannot exit until this side actually reads from it --
        # waiting on proc.is_alive() first deadlocks both sides forever
        # on any result past that size (real at this app's own scale:
        # any drama with roughly 250+ lines, in dub/re-segment/diarize/
        # auto-tune's subprocess workers, all of which return one item
        # per line/turn/segment). Reusing poll_interval as the get()
        # timeout keeps the same cancel-latency and CPU-use profile the
        # previous plain time.sleep(poll_interval) had.
        try:
            item = result_queue.get(timeout=poll_interval)
        except queue.Empty:
            pass
        else:
            if _apply_progress_item(job_id, item, stage):
                continue
            return item
        if not proc.is_alive():
            return None


def _drain_after_exit(job_id, result_queue, stage):
    """The worker exited before its final item was read: the item (or None
    if it never arrives), applying any progress items queued ahead of it."""
    while True:
        try:
            # Not get_nowait(): multiprocessing.Queue.put() hands the
            # pickled item to an internal feeder thread rather than
            # writing it synchronously, so a process that exits right
            # after put()ing its result can have already exited
            # (proc.is_alive() already False when this runs) before
            # that item is actually readable from this end -- a real,
            # documented race, not just a test timing quirk. A short
            # blocking get gives it time to land.
            item = result_queue.get(timeout=1)
        except queue.Empty:
            return None
        if not _apply_progress_item(job_id, item, stage):
            if item is not None:
                return item


def _run_completion_hook(job_id, run, on_done, payload, logger):
    """(on_done's return value, hook error text or None), or _STOPPED when
    the run was cleared or cancelled and records nothing more."""
    with _lock:
        if job_id not in _jobs or (run is not None and _jobs[job_id] is not run):
            return _STOPPED
    # A cancel that arrived while the subprocess was finishing: the
    # job applies nothing.
    if is_cancel_requested(job_id):
        with _lock:
            _mark_cancelled_locked(job_id, run)
        logger.info(f"job {job_id} cancelled (after its subprocess finished)")
        return _STOPPED
    # Outside the lock: the hook does real DB/file work.
    try:
        return _run_on_done(job_id, run, on_done, payload), None
    except JobCancelled:
        # The hook applied its work, then a stop began (see
        # _apply_transcription): cancelled, nothing is rolled back.
        with _lock:
            _mark_cancelled_locked(job_id, run)
        return _STOPPED
    except Exception as exc:
        hook_error = redact_secrets(f"{type(exc).__name__}: {exc}")
        logger.error(f"job {job_id} on_done hook failed: {hook_error}",
                     exc_info=True)
        return None, hook_error


def _record_final_status(job_id, run, proc, outcome, result, hook_error, logger):
    """Records how the run ended, if it is still this run and "running".
    Returns (description, status to notify, owner, reason) for the finish
    notification, or None when nothing was recorded."""
    _jobs_reason = None
    _notify_failed = False
    with _lock:
        if not _still_running_locked(job_id, run):
            return None
        job = _jobs[job_id]
        if hook_error is not None:
            job["status"] = "error"
            job["error"] = f"Completion hook failed: {hook_error}"
            job["finished_at"] = time.time()
        elif outcome and outcome[0] == "ok":
            _jobs_reason, _notify_failed = _record_ok_locked(job_id, job, result, logger)
        elif outcome and outcome[0] == "error":
            # process_worker.error_item adds the worker's traceback; older
            # three-item workers have none.
            _, exc_type, msg, *worker_tb = outcome
            # The worker's message can carry a key (a provider's error
            # echoing it back): redact before storing or logging.
            error_msg = redact_secrets(f"{exc_type}: {msg}")
            job["status"] = "error"
            job["error"] = error_msg
            job["finished_at"] = time.time()
            tb_text = redact_secrets(str(worker_tb[0])) if worker_tb and worker_tb[0] else ""
            if tb_text:
                job["traceback"] = tb_text
            logger.error(f"job {job_id} failed: {error_msg}"
                         + (f"\nworker traceback:\n{tb_text}" if tb_text else ""))
        else:
            job["status"] = "error"
            job["error"] = (
                f"Subprocess exited unexpectedly (exit code {proc.exitcode}) without "
                "reporting a result.")
            job["finished_at"] = time.time()
            logger.error(f"job {job_id} subprocess died with no result "
                        f"(exit code {proc.exitcode})")
        _mirror_locked(job_id)
        _final_status = job["status"]
        return (job.get("description"), "error" if _notify_failed else _final_status,
                job.get("owner_user_id"), _jobs_reason)


def _record_ok_locked(job_id, job, result, logger):
    """Caller holds _lock. The worker returned a result: `error` when it
    reports a failure, else `done`. Returns (notification reason,
    whether the notification says failed)."""
    job["result"] = result
    job["finished_at"] = time.time()
    _failed = _failed_outcome_error(result, job_id)
    if _failed:
        job["status"] = "error"
        job["error"] = _failed
        job["message"] = ""
        logger.error(f"job {job_id} failed: {_failed}")
        return _failed, False
    job["status"] = "done"
    job["progress"] = 1.0
    _finish_message_locked(job)
    _jobs_reason = _soft_failure_text(result)
    _notify_failed = bool(_jobs_reason)
    _jobs_reason = _jobs_reason or _done_note(result)
    logger.info(f"job {job_id} finished")
    return _jobs_reason, _notify_failed


def _record_watcher_failure(job_id, proc, run, exc, logger):
    # A complete message that fails to unpickle, a broken queue or any
    # other watcher failure: without this the job stays "running"
    # forever.
    error_msg = redact_secrets(f"{type(exc).__name__}: {exc}")
    logger.error(f"job {job_id} watcher failed: {error_msg}")
    try:
        _stop_process(proc)
    except Exception:
        pass
    with _lock:
        if _still_running_locked(job_id, run):
            job = _jobs[job_id]
            job["status"] = "error"
            job["error"] = f"Lost contact with the job's process: {error_msg}"
            job["finished_at"] = time.time()
            _mirror_locked(job_id)


def _end_watch(job_id, proc, result_queue, gpu_touching, on_finish, kill_whole_tree, run,
               stage, reaped, timing):
    """The watcher's cleanup on every path, in this order."""
    _stop_stage_ticker(stage)
    # The cancel and watcher-failure paths get here unreaped: no zombie,
    # and the worker is ended before the GPU slot and finish hook are
    # released. Then close the queue's pipe fds.
    if not reaped:
        reap_worker(proc, kill_whole_tree)
    try:
        result_queue.close()
        result_queue.discard()
    except Exception:
        pass
    _timing_finish(job_id, timing, thread_job=False)
    _forget_cancel_check(job_id, run)
    _release_gpu_slot(job_id, gpu_touching, run)
    try:
        _promote_next_queued_gpu_job()
    finally:
        # Last: removing a large temp folder must not hold the GPU slot.
        if on_finish is not None:
            _run_finish_hooks([(job_id, on_finish)])


def recheck_gpu_queue():
    """Public entry point for _promote_next_queued_gpu_job(), for callers
    outside this module. Nothing here polls the GPU queue on its own (see
    _gpu_slot_available_locked's own docstring) -- a job queued because
    nvidia-smi showed the GPU busy with something Baihe didn't start
    otherwise only gets re-checked the next time some *other* GPU-touching
    job finishes. The Running Jobs panel's auto-refresh calls this on every
    tick specifically to close that gap while it's open."""
    _promote_next_queued_gpu_job()
