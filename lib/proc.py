"""
lib/proc.py -- the one way to run a long external command whose
output is read (pip, pytest, venv creation, winget).

The child gets its own process group, so a timeout or a cancel kills the
whole tree (job_process_kill.kill_tree), not just the parent. Output is read
on helper threads, so a grandchild that survives the kill and keeps a pipe
open can hold the caller for at most `drain_seconds`, never forever; the
plain subprocess.run(capture_output=True) can, because on Windows it waits
for the pipe again after the kill with no limit.

`cancel` is an optional callable polled about twice a second, so a cancel
lands even while the child prints nothing.

tests/test_static_analysis.py bans capturing subprocess calls elsewhere
except an explicit allow-list of short probes.
"""

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass

from lib.proc_kill import kill_tree

KILL_DRAIN_SECONDS = 5.0
_POLL_SECONDS = 0.5
# A runaway child must not fill memory: run_captured keeps the newest output.
_CAPTURE_LIMIT_CHARS = 4_000_000
_EOF = object()


def _group_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _child_env(env: dict, utf8_env: bool) -> dict:
    """The output is always decoded as UTF-8, but a child Python on Windows
    writes piped text in the ANSI code page unless told otherwise, which
    corrupts non-ASCII paths (a user name like 张三) the parent parses."""
    base = dict(os.environ if env is None else env)
    if utf8_env:
        base.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return base


def _events(cmd: list, timeout: float, drain_seconds: float, cancel, merge_stderr: bool,
            cwd, env, warn=None, utf8_env: bool = True):
    """Yields ("out" | "err", line) per output line, then
    ("end", {"returncode", "timed_out", "cancelled"}). returncode is None if
    the child could not be reaped."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", bufsize=1, cwd=cwd, env=_child_env(env, utf8_env),
        **_group_kwargs())
    items = queue.Queue()

    def reader(stream, tag):
        try:
            for line in stream:
                items.put((tag, line))
        except (OSError, ValueError):
            pass
        finally:
            items.put((tag, _EOF))

    streams = [(proc.stdout, "out")] + ([] if merge_stderr else [(proc.stderr, "err")])
    for stream, tag in streams:
        threading.Thread(target=reader, args=(stream, tag), daemon=True,
                         name=f"proc-{tag}").start()
    open_streams = len(streams)

    deadline = time.monotonic() + timeout
    timed_out = cancelled = False
    stop_by = None
    try:
        while open_streams:
            now = time.monotonic()
            if stop_by is None:
                if cancel is not None and cancel():
                    cancelled = True
                elif now >= deadline:
                    timed_out = True
                if cancelled or timed_out:
                    kill_tree(proc, warn)
                    stop_by = now + drain_seconds
                elif proc.poll() is not None:
                    stop_by = now + drain_seconds     # exited; finish reading
            if stop_by is not None and now >= stop_by:
                break
            limit = deadline if stop_by is None else stop_by
            try:
                tag, line = items.get(timeout=max(0.01, min(_POLL_SECONDS, limit - now)))
            except queue.Empty:
                continue
            if line is _EOF:
                open_streams -= 1
                continue
            yield tag, line
    finally:
        # Also reached when the caller stops iterating early.
        # A same-group grandchild can outlive the parent and still hold a pipe
        # open; killpg reaches it after the parent is reaped, so callers that
        # clean up afterwards (browser install) don't race with it.
        if proc.poll() is None or open_streams:
            kill_tree(proc, warn)
        try:
            returncode = proc.wait(timeout=drain_seconds)
        except subprocess.TimeoutExpired:
            returncode = None
    yield "end", {"returncode": returncode, "timed_out": timed_out, "cancelled": cancelled}


def stream_tree(cmd: list, timeout: float, drain_seconds: float = KILL_DRAIN_SECONDS,
                cwd: str = None, env: dict = None, cancel=None, warn=None):
    """Yields {"line"} per line of combined stdout/stderr, then
    {"returncode", "timed_out", "cancelled"}. The tree is killed when
    `timeout` passes, when `cancel()` turns true, or when the caller stops
    iterating. `warn(what, exc)` reports a swallowed kill failure (see
    lib.proc_kill.kill_tree)."""
    events = _events(cmd, timeout, drain_seconds, cancel, True, cwd, env, warn)
    try:
        for tag, payload in events:
            if tag == "end":
                yield payload
            else:
                yield {"line": payload.rstrip("\n")}
    finally:
        events.close()      # kills the tree now, not whenever the generator is collected


@dataclass
class CapturedRun:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool


def run_captured(cmd: list, timeout: float, drain_seconds: float = KILL_DRAIN_SECONDS,
                 cwd: str = None, env: dict = None, cancel=None, warn=None,
                 utf8_env: bool = True) -> CapturedRun:
    """subprocess.run(capture_output=True, text=True) with the tree-kill and
    bounded drain of stream_tree. Does not raise on timeout; check
    `timed_out`. Each stream keeps its newest _CAPTURE_LIMIT_CHARS.
    `utf8_env=False` leaves the child's Python I/O encoding alone."""
    chunks = {"out": [], "err": []}
    sizes = {"out": 0, "err": 0}
    end = None
    for tag, payload in _events(cmd, timeout, drain_seconds, cancel, False, cwd, env, warn,
                                utf8_env):
        if tag == "end":
            end = payload
            continue
        chunks[tag].append(payload)
        sizes[tag] += len(payload)
        while sizes[tag] > _CAPTURE_LIMIT_CHARS and len(chunks[tag]) > 1:
            sizes[tag] -= len(chunks[tag].pop(0))
    return CapturedRun(end["returncode"], "".join(chunks["out"]), "".join(chunks["err"]),
                       end["timed_out"], end["cancelled"])
