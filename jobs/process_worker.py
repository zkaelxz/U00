"""jobs/process_worker.py -- what a process-job worker calls inside its child.

Reporting progress and stages back to the parent's watcher through the
result queue, leading its own process group so cancel can kill the whole
tree, and exiting when its parent (the server) has died.
"""

import multiprocessing
import os
import threading
import time
import traceback


def report_progress(result_queue, frac: float, message: str = ""):
    """For a process-job worker (start_process_job's `target`): sends an
    intermediate ("progress", fraction, message) tuple to the parent's
    watcher, which applies it to the job like update_progress(). Best
    effort -- never raises into the worker. The final ("ok"/"error", ...)
    tuple protocol is unchanged. Ends a worker whose parent is gone (see
    exit_if_parent_gone)."""
    exit_if_parent_gone()
    try:
        result_queue.put(("progress", frac, message))
    except Exception:
        pass


def report_stage(result_queue, message: str, frac: float = 0.0):
    """For a process-job worker: starts a stage that cannot report progress
    (a model download/load). The parent shows it with a stage_ticker
    (elapsed time, the no-progress note, the longer stall allowance) until
    the worker's next progress or stage item; an empty message just ends
    the stage. Best effort, like report_progress."""
    exit_if_parent_gone()
    try:
        result_queue.put(("stage", frac, message))
    except Exception:
        pass


def error_item(exc: BaseException) -> tuple:
    """The final ("error", type name, message, traceback) item for a worker
    whose body raised. The parent shows the type and message and writes the
    traceback to the server log, so the line that failed is not lost with
    the child. Message and traceback are secret-redacted here, since a
    provider's error can echo a key back."""
    from engine_backends.redaction import redact_secrets
    try:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        tb = ""
    # The result channel (job_process_result) caps the size.
    return ("error", type(exc).__name__, redact_secrets(str(exc)), redact_secrets(tb))


# Set in a worker by start_own_process_group: the process that started it
# (multiprocessing.parent_process(), None outside a multiprocessing child)
# and its pid, so the worker can tell that it has been orphaned.
_worker_parent = None
_worker_parent_pid = None
# How often the worker's watchdog thread looks for a dead parent: an
# orphaned worker (its server killed) would otherwise keep running, and
# keep its GPU memory, with nobody left to read its result.
PARENT_CHECK_INTERVAL = 2.0


def start_own_process_group():
    """For a kill_whole_tree=True process-job worker, called first: on POSIX the
    worker leads a new process group, so a cancel's kill_tree also reaches
    the processes it starts (ffmpeg, ffprobe). A no-op on Windows, where
    kill_tree walks the process tree instead. Never raises.

    Leaving the parent's process group also takes the worker out of reach
    of a terminal's hang-up or Ctrl+C, so when the parent dies without
    cancelling it (killed, out of memory, terminal closed) nothing else
    would stop it. The worker ends itself once its parent is gone
    (exit_if_parent_gone): at every progress or stage report and from a
    watchdog thread, which also covers a long call that reports nothing.
    The parent comes from multiprocessing.parent_process(), which the
    child gets at start-up, so a parent killed during the worker's spawn
    start-up and imports is still noticed; os.getppid() read here would
    already be the reaper then. Not PR_SET_PDEATHSIG: Linux sends that when
    the parent *thread* that started the worker exits, and a queued job is
    started from another job's watcher thread, which ends right after."""
    global _worker_parent, _worker_parent_pid
    try:
        parent = multiprocessing.parent_process()
        _worker_parent = parent
        _worker_parent_pid = parent.pid if parent is not None else os.getppid()
        threading.Thread(target=_parent_watchdog, daemon=True, name="parent-watchdog").start()
    except Exception:
        pass
    if os.name == "nt":
        return
    try:
        os.setsid()
    except Exception:
        pass


def in_job_worker() -> bool:
    """True in a process-job worker that called start_own_process_group."""
    return _worker_parent_pid is not None


def exit_if_parent_gone():
    """In a worker that called start_own_process_group: when its parent has
    died, kills the worker's own process group (itself and what it started)
    and exits. A no-op anywhere else. Never raises: report_progress and
    report_stage call it outside their own try."""
    if not _parent_gone():
        return
    if os.name != "nt":
        try:
            if os.getpgid(0) == os.getpid():
                import signal
                os.killpg(0, signal.SIGKILL)
        except Exception:
            pass
    os._exit(1)


def _parent_gone() -> bool:
    """The parent's sentinel (parent_process().is_alive()) when there is
    one, and on POSIX also a changed os.getppid() (re-parented to a reaper).
    Not getppid on Windows, where it never changes. Errors read as alive."""
    if _worker_parent_pid is None:
        return False
    try:
        if _worker_parent is not None and not _worker_parent.is_alive():
            return True
    except Exception:
        pass
    if os.name == "nt":
        return False
    try:
        return os.getppid() != _worker_parent_pid
    except Exception:
        return False


def _parent_watchdog():
    while _worker_parent_pid is not None:
        time.sleep(PARENT_CHECK_INTERVAL)
        exit_if_parent_gone()
