"""
services/browser_install_service.py -- the opt-in "Install browser support"
action in Diagnostics: downloads Playwright's Chromium into the app's own
browser folder (browser_support.app_browsers_folder) so JavaScript-heavy
source sites (baihehub, Fanjiao) can be read on a PC with no Chrome or Edge.

It runs `python -m playwright install chromium` as a background job. The
command is fixed (no argument comes from a request); the only environment
change is PLAYWRIGHT_BROWSERS_PATH, so nothing lands in the user's global
Playwright cache. The download happens inside that subprocess, so its
timeout is the guard. Status and the last result carry booleans, sizes in
MB and fixed or redacted text, never a path.
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

import background_jobs
import browser_support
import diagnostics
import translate_engines
from services import diagnostics_gaps_service as gaps
from services.service_errors import ConflictError

BROWSER_JOB_ID = "browser_install"
INSTALL_TIMEOUT_SECONDS = 15 * 60
MIN_FREE_MB = 300
# Playwright prints a progress bar per download, so a handful of lines tell
# what happened without keeping a whole run's output.
OUTPUT_TAIL_LINES = 12
OUTPUT_LINE_CHARS = 200
KILL_DRAIN_SECONDS = 5.0
# Unset so a user who skips downloads for their own projects doesn't make
# this install silently do nothing.
_ENV_DROPPED = ("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",)
_PERCENT = re.compile(r"(\d{1,3})%")

_STATE_LOCK = threading.Lock()
_RESULT = {"last": None}


class AlreadyAvailable(gaps.AdminActionRefused, ConflictError):
    pass


def _clean(text) -> str:
    """Secrets out first, then filesystem paths (the install folder shows
    up in Playwright's own output)."""
    out = translate_engines.redact_secrets("" if text is None else str(text))
    return diagnostics.redact_for_support(out)[:OUTPUT_LINE_CHARS]


def _job_view(job_id: str):
    job = background_jobs.get_status(job_id)
    if not job:
        return None
    return {"status": job.get("status"), "progress": float(job.get("progress") or 0.0),
            "message": _clean(job.get("message") or ""),
            "error": _clean(job.get("error")) if job.get("error") else None}


def _playwright_importable() -> bool:
    import importlib.util
    return importlib.util.find_spec("playwright") is not None


def _system_browser_found() -> bool:
    import page_fetch
    return bool(page_fetch.find_system_browser() or page_fetch._explicit_browser())


def _free_mb() -> int:
    folder = browser_support.app_browsers_folder()
    while folder and not os.path.exists(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return int(shutil.disk_usage(folder).free // (1024 * 1024))


def _refusal():
    """Plain-English reason the install can't start now, or None."""
    if not _playwright_importable():
        return "Install the playwright package first (Diagnostics > Packages)."
    if _system_browser_found():
        return "Chrome or Edge is already installed, so this is not needed."
    if browser_support.app_chromium_present():
        return "Browser support is already installed."
    if _free_mb() < MIN_FREE_MB:
        return f"Not enough free disk space (about {MIN_FREE_MB} MB needed)."
    return None


def get_status() -> dict:
    with _STATE_LOCK:
        last = dict(_RESULT["last"]) if _RESULT["last"] else None
    return {"playwright_installed": _playwright_importable(),
            "app_browser_installed": browser_support.app_chromium_present(),
            "system_browser_found": _system_browser_found(),
            "free_mb": _free_mb(), "required_mb": MIN_FREE_MB,
            "refusal": _refusal(), "job_id": BROWSER_JOB_ID,
            "job": _job_view(BROWSER_JOB_ID), "last_result": last}


def start_install(confirm: bool = False) -> dict:
    """PC only (the route is local_only()). 422 unconfirmed, or without the
    playwright package, or with too little disk; 409 while any job runs or
    when a browser is already available."""
    gaps.guard(confirm)
    reason = _refusal()
    if reason:
        if reason.startswith(("Chrome", "Browser support")):
            raise AlreadyAvailable(reason)
        raise gaps.AdminActionNotPossible(reason)
    if not background_jobs.start_job(BROWSER_JOB_ID, _install_job,
                                     description="Installing browser support"):
        raise gaps.AdminActionJobsRunning(
            "A browser install or a library restore is already running; try again when it ends.")
    return {"job_id": BROWSER_JOB_ID, "started": True}


def _command() -> list:
    return [sys.executable, "-m", "playwright", "install", "chromium"]


def _environment(folder: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _ENV_DROPPED}
    env[browser_support.BROWSERS_ENV] = folder
    return env


def _run(cmd: list, env: dict, timeout: float, on_line, cancelled):
    """Runs cmd in its own process group, feeding each output line to
    on_line. Returns (returncode, timed_out, was_cancelled). The tree is
    killed on timeout, cancel or any exit from here, and every wait after
    that is bounded so a grandchild holding the pipe can't hang the job."""
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, env=env, **group)
    lines, eof = queue.Queue(), object()

    def _reader():
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(eof)
    threading.Thread(target=_reader, daemon=True, name="browser-install-output").start()

    deadline = time.monotonic() + timeout
    timed_out = was_cancelled = False
    try:
        while True:
            if cancelled():
                was_cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            try:
                item = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is eof:
                break
            on_line(item.rstrip("\n"))
    finally:
        if proc.poll() is None:
            background_jobs.kill_tree(proc)
        try:
            returncode = proc.wait(timeout=KILL_DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            returncode = None
    return returncode, timed_out, was_cancelled


def _remove_partial(folder: str):
    # The install is refused when a complete one exists, so whatever is here
    # now is leftovers of this run; a half-unpacked build would otherwise
    # look present to has_browser_program.
    shutil.rmtree(folder, ignore_errors=True)


def _store(ok: bool, message: str, tail) -> dict:
    result = {"ok": ok, "message": message, "output_tail": list(tail)}
    with _STATE_LOCK:
        _RESULT["last"] = result
    return result


def _install_job():
    folder = browser_support.app_browsers_folder()
    tail = deque(maxlen=OUTPUT_TAIL_LINES)
    best = [0.05]

    def on_line(raw):
        line = _clean(raw.strip())
        if not line:
            return
        tail.append(line)
        m = _PERCENT.search(line)
        if m:
            best[0] = max(best[0], min(int(m.group(1)), 100) / 100 * 0.9)
        background_jobs.update_progress(BROWSER_JOB_ID, best[0], "Downloading the browser…")

    background_jobs.update_progress(BROWSER_JOB_ID, 0.02, "Starting the browser download…")
    try:
        os.makedirs(folder, exist_ok=True)
        code, timed_out, was_cancelled = _run(
            _command(), _environment(folder), INSTALL_TIMEOUT_SECONDS, on_line,
            lambda: background_jobs.is_cancel_requested(BROWSER_JOB_ID))
    except Exception:      # noqa: BLE001 -- never echo an exception (paths)
        _remove_partial(folder)
        _store(False, "The browser install could not start.", tail)
        raise RuntimeError("The browser install could not start.") from None

    if was_cancelled:
        _remove_partial(folder)
        _store(False, "Cancelled.", tail)
        raise background_jobs.JobCancelled()
    if timed_out:
        message = "The download took too long and was stopped. Check your connection and try again."
    elif code != 0:
        message = "The browser download failed; see the output."
    elif not browser_support.app_chromium_present():
        message = "The download finished but no browser was found afterwards."
    else:
        _store(True, "Browser support installed.", tail)
        background_jobs.set_result(BROWSER_JOB_ID, {"status": "ok", "detail": "Browser support installed."})
        return
    _remove_partial(folder)
    _store(False, message, tail)
    background_jobs.set_result(BROWSER_JOB_ID, {"status": "failed", "detail": message})
    raise RuntimeError(message)
