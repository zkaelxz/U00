"""One shape for a GPU job that runs in its own process, so Cancel kills the
process instead of waiting for a model call that has no safe point to stop at.

Parent side: run_in_child() runs the worker in a spawned child (the whole
process tree killed on cancel or timeout) with a scratch folder removed when it
ends.
Worker side: run_worker() is what every such worker runs inside: own process
group, a watchdog that ends the process at the deadline, scratch as the temp
folder. Kept free of the services that use it so the spawned child imports only
what it needs."""

import multiprocessing
import os
import queue
import shutil
import tempfile
import threading
import time

import background_jobs
import job_process_kill
import job_process_result
import storage
from translate_engines import redact_secrets

TIMEOUT_MESSAGE = "This job took too long and was stopped."
STOPPING_MESSAGE = "Waiting for the worker to stop"
# The child's watchdog is the deadline; the parent's, this much later, is
# only a backstop for a child stuck before its watchdog starts (a hung import).
PARENT_GRACE_S = 30


def run_worker(body, timeout_s, scratch_dir, result_queue, args=(), on_timeout=None):
    """Runs body(*args, scratch_dir, result_queue) as a process-job worker. body
    puts the ("ok", ...) result itself; an exception it raises is sent as
    ("error", type name, redacted message). on_timeout is the item sent when
    the deadline passes first (default: an error item with TIMEOUT_MESSAGE).

    The watchdog is a thread in this process: the stuck call (a ctranslate2
    future wait, a CUDA kernel) releases the GIL, so the watchdog can still
    report and end the process. It closes and joins the queue's feeder thread
    before os._exit, or the item would die in the buffer and the parent would
    see a worker that vanished."""
    background_jobs.start_own_process_group()
    timeout_item = on_timeout or ("error", "TimeoutError", TIMEOUT_MESSAGE)

    def give_up():
        result_queue.put(timeout_item)
        result_queue.close()
        result_queue.join_thread()
        os._exit(0)

    watchdog = threading.Timer(timeout_s, give_up)
    watchdog.daemon = True
    watchdog.start()
    try:
        os.makedirs(scratch_dir, exist_ok=True)
        # Library code that makes temp files lands them in the job's folder,
        # which the parent removes however the run ends.
        tempfile.tempdir = scratch_dir
        body(*args, scratch_dir, result_queue)
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))
    finally:
        watchdog.cancel()


def process_entry(body, timeout_s, scratch_dir, *args_and_queue):
    """The process target run_in_child starts: top level and plain arguments so
    it pickles under spawn. args_and_queue ends with the result queue."""
    *args, result_queue = args_and_queue
    run_worker(body, timeout_s, scratch_dir, result_queue, tuple(args))


class ChildFailed(Exception):
    """run_in_child: the worker raised, ran out of time or died. The message
    is plain and redacted, safe to store in a job error."""


def run_in_child(job_id, body, args, *, timeout_s, poll_s=0.25, on_item=None):
    """For a job that stays a thread job (its next stage needs the parent: the
    database, a translation engine) but whose GPU stage must be killable.
    Runs body(*args, scratch_dir, result_queue) in a spawned child under the
    job's own GPU slot and returns the "ok" result. Blocks, applying the
    child's progress to the job; raises background_jobs.JobCancelled as soon
    as Cancel is seen, after the child and what it started are gone, and
    ChildFailed for an error, the deadline or a child that died silently.

    A body that puts ("item", x) for each unit of work it finishes has
    on_item(x) called in the parent as they arrive, so a caller keeps what was
    done when the run ends early by cancel or timeout.

    timeout_s is enforced by the child's own watchdog, which reports it. The
    parent gives up only PARENT_GRACE_S later, as a backstop for a child stuck
    before its watchdog starts, so the GPU slot can stay held up to that much
    past timeout_s; once timeout_s passes the job's progress text says it is
    waiting for the worker to stop."""
    context = multiprocessing.get_context("spawn")
    channel = job_process_result.wrap_queue(context.Queue(), job_id)
    scratch_dir = storage.new_workdir(job_id)
    proc = context.Process(
        target=process_entry, args=(body, timeout_s, scratch_dir, *args, channel),
        daemon=True)
    clean = False
    try:
        # Spawn, not Linux's default fork: a forked child of a process that
        # has already initialised CUDA cannot use the GPU.
        proc.start()
        worker_deadline = time.monotonic() + timeout_s
        result = _await_child(job_id, proc, channel, poll_s, worker_deadline,
                              worker_deadline + PARENT_GRACE_S, on_item)
        clean = True
        return result
    finally:
        if not clean:
            # Cancel must not wait out reap_worker's exit grace.
            job_process_kill.kill_tree(proc)
        job_process_kill.reap_worker(proc, True)
        channel.discard()
        shutil.rmtree(scratch_dir, ignore_errors=True)


def _await_child(job_id, proc, channel, poll_s, worker_deadline, deadline, on_item):
    gone = False
    told_stopping = False
    while True:
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        now = time.monotonic()
        if now > deadline:
            raise ChildFailed(TIMEOUT_MESSAGE)
        if now > worker_deadline and not told_stopping:
            told_stopping = True
            status = background_jobs.get_status(job_id) or {}
            background_jobs.update_progress(job_id, status.get("progress") or 0.0,
                                            STOPPING_MESSAGE)
        try:
            item = channel.get(timeout=poll_s)
        except queue.Empty:
            if proc.is_alive():
                continue
            # The feeder thread can land its last item just after the exit.
            if gone:
                raise ChildFailed(background_jobs.WORKER_LOST_MESSAGE)
            gone = True
            continue
        except OSError:
            raise ChildFailed(job_process_result.RESULT_FILE_ERROR) from None
        if background_jobs._apply_progress_item(job_id, item):
            continue
        if item and item[0] == "item":
            if on_item is not None:
                on_item(item[1])
            continue
        if item and item[0] == "ok":
            return item[1]
        if item and item[0] == "error":
            raise ChildFailed(item[2])
        raise ChildFailed(background_jobs.WORKER_LOST_MESSAGE)
