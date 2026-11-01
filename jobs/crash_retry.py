"""jobs/crash_retry.py -- running a process job once more on the CPU after its
GPU worker crashed.

A Python-level GPU failure (CUDA out of memory, a missing kernel) already
falls back to the CPU inside the worker. A native fault in CUDA or a model
library (an access violation, exit code 3221225477 on Windows) kills the
worker before Python can report anything, so nobody is left to do that. A
launcher that passes cpu_retry_args to start_process_job gets one more run of
the same target with those args (use_gpu off) when, and only when, its worker
died that way without sending a result.
"""

import multiprocessing
import signal

import job_process_result

# The result dict of a run that succeeded on the CPU after a crash carries the
# reason under this key; the job's on_done turns it into its usual
# "ran on the CPU" notice.
REASON_KEY = "gpu_crash_retry"

_CRASH_SIGNALS = {getattr(signal, name) for name in
                  ("SIGSEGV", "SIGBUS", "SIGILL", "SIGFPE", "SIGABRT") if hasattr(signal, name)}
# Windows reports an unhandled native exception as its NTSTATUS code, which
# multiprocessing gives as an unsigned exit code: every error status has the
# top two bits set (0xC0000005 access violation, 0xC0000409 stack overrun,
# 0xC000001D illegal instruction).
_NTSTATUS_ERROR = 0xC0000000
# STATUS_CONTROL_C_EXIT: Ctrl-C or the console window closing, a stop by the
# user rather than a fault.
_NTSTATUS_CONTROL_C_EXIT = 0xC000013A


def is_native_crash(exitcode) -> bool:
    """True for a worker killed by a native fault. Not for a normal exit, a
    Python error, SIGKILL or SIGTERM (a cancel, the OS out-of-memory killer):
    the CPU would not fix those."""
    if exitcode is None:
        return False
    if exitcode < 0:
        return -exitcode in _CRASH_SIGNALS
    return exitcode >= _NTSTATUS_ERROR and exitcode != _NTSTATUS_CONTROL_C_EXIT


def crash_reason(exitcode) -> str:
    return f"GPU worker crashed, exit code {exitcode}"


def plan(target, cpu_args, start_method):
    """What the watcher needs to start the CPU run, or None for a job with no
    CPU retry. cpu_args may be a function returning the args, called when the
    CPU run starts: for args that depend on what the crashed run left behind
    (the checkpoints a transcription stored before it died)."""
    if cpu_args is None:
        return None
    args = cpu_args if callable(cpu_args) else tuple(cpu_args)
    return {"target": target, "args": args, "start_method": start_method}


def build_process(job_id, retry):
    """(process, result queue) for the CPU run; not started."""
    args = retry["args"]
    args = tuple(args()) if callable(args) else args
    method = retry.get("start_method")
    mp = multiprocessing.get_context(method) if method else multiprocessing
    result_queue = job_process_result.wrap_queue(mp.Queue(), job_id)
    proc = mp.Process(target=retry["target"], args=(*args, result_queue), daemon=True)
    return proc, result_queue


def mark_result(outcome, reason):
    """The worker's final item with the crash reason added to an "ok" dict."""
    if reason and outcome and outcome[0] == "ok" and isinstance(outcome[1], dict):
        return ("ok", {**outcome[1], REASON_KEY: reason})
    return outcome


def as_gpu_fallback(result):
    """For a job with no notice of its own: the crash reason a CPU rerun's
    result carries, moved to gpu_fallback, which the Jobs list and the
    results already turn into their "ran on the CPU" notice."""
    if not isinstance(result, dict) or REASON_KEY not in result:
        return result
    result = dict(result)
    crash = result.pop(REASON_KEY)
    if crash:
        result["gpu_fallback"] = crash
    return result
