"""
job_process_kill.py -- stopping a background job's child process: terminate
then kill, and killing a worker's process group. kill_tree lives in
lib/proc_kill.py; both are re-exported by background_jobs, which is where
callers and tests reach these names (kill_tree here adds the app-log warning).
"""

import os

from lib import proc_kill


def _warn_via_jobs(what, exc):
    # Imported late: background_jobs imports this module at load time.
    import background_jobs
    background_jobs._warn(what, exc)


def _stop_process(proc):
    """terminate, then kill if the child ignores SIGTERM (e.g. stuck in a
    CUDA call): the GPU slot is released right after, so a surviving
    child would hold VRAM with nothing tracking it."""
    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)


def kill_tree(proc):
    return proc_kill.kill_tree(proc, warn=_warn_via_jobs)


def _kill_worker_group(proc):
    """POSIX: SIGKILLs the process group a kill_whole_tree worker leads
    (start_own_process_group), whether or not the worker is still alive.
    A worker that never made its group leaves no group with its pid, and
    the server's own group is never targeted."""
    import signal
    pid = getattr(proc, "pid", None)
    try:
        if pid is not None and pid != os.getpgrp():
            os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception as exc:
        _warn_via_jobs(f"could not kill process group {pid}", exc)
