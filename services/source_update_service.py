"""
services/source_update_service.py -- "Update now" for a git working copy
started with start.bat, usable from the website by an admin.

It does not merge anything itself. start.bat already fetches and
fast-forwards origin/baihe-subtitler, reinstalls changed requirements and
rebuilds the screens when the app is not running, so an update here is: check
that start.bat would succeed (same rules, with a plain-words reason for each
refusal), stop this server cleanly, and let tools/restart_after_update.py run
start.bat once the process has exited. The next server boot compares the
commit it is on with the one that was promised and reports the outcome.

Installed copies (Setup, launcher) are not handled here: they update through
services/update_service.py and app_update_service.py.
"""

import os
import re
import subprocess
import sys
import threading
import time

import background_jobs
import db
import process_guard
from lib import proc
from services import app_update_service, auth_service, shutdown_service
from services import update_service as us
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, UnsupportedOperationError)

BRANCH = "baihe-subtitler"
STARTED_BY_ENV = "BAIHE_STARTED_BY_START_BAT"
PENDING_KEY = "source_update.pending"
RESULT_KEY = "source_update.last_result"
_START_BAT_STATE_ENV = frozenset({"BAIHE_UPDATE_OLD", "BAIHE_FORCE_DEPS", "BAIHE_BUILD_FRONTEND",
                                  "BAIHE_AUTO_BUILD"})
HELPER = os.path.join("tools", "restart_after_update.py")
GIT_TIMEOUT = 20
FETCH_TIMEOUT = 40
CHECK_MIN_INTERVAL = 5.0
MAX_CHANGES = 5
MAX_SUBJECT_CHARS = 100
# start.bat's own switches that turn its update off; the restart goes through
# start.bat, so they would turn this one off too.
_OFF_ENV = ("BAIHE_CI", "BAIHE_SERVER_ONLY", "BAIHE_NO_UPDATE")

NOT_GIT = "This copy isn't a git folder, so it can't update itself."
INSTALLED = "This copy was installed with Setup. Run the new Setup on the PC to update it."
NO_GIT = "Git isn't installed on this PC."
UNREADABLE = "This folder's git information couldn't be read."
WRONG_BRANCH = f"This copy isn't on the {BRANCH} branch."
EDITED = "Some of the program files in this folder were edited."
OFFLINE = "Couldn't reach GitHub. Try again later."
NOT_FF = "This copy has changes of its own that the update can't sit on top of."
TURNED_OFF = "Updates are turned off for this copy."
NOT_WINDOWS = "Updating from the website works on Windows only."
NOT_START_BAT = "Baihe wasn't started with start.bat, so it can't restart itself. Update it on the PC."
JOBS_RUNNING = "Finish or cancel the running jobs first: the update restarts Baihe."
NOT_CHECKED = "Check for updates first."
NOTHING_NEW = "Already up to date."
BUSY = "An update is already starting."
START_FAILED = "Baihe couldn't start the update. Nothing was changed."
UNCONFIRMED = "Confirm to update and restart Baihe."

_lock = threading.Lock()
_applying = False
_check = {"at": 0.0, "behind": None, "latest": None, "changes": [], "error": None}


def _root() -> str:
    return us._APP_DIR


def _short(sha) -> str:
    return (sha or "")[:10]


def _git(*args, timeout=GIT_TIMEOUT):
    """(returncode, stdout) of one git command in the app folder. A missing
    git is returncode None. Never prompts for credentials."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        r = proc.run_captured(["git", *args], timeout=timeout, cwd=_root(), env=env)
    except OSError:
        return None, ""
    if r.timed_out:
        return 124, ""
    return r.returncode, (r.stdout or "").strip()


def _subject(text) -> str:
    text = us._URL_RE.sub("", us._CONTROL_RE.sub(" ", text))
    return " ".join(text.split())[:MAX_SUBJECT_CHARS]


def _local_problem():
    """Why an update can't run on this copy, or None. Local checks only, in
    the order start.bat applies its own."""
    if us._is_installed():
        return INSTALLED
    if not os.path.isdir(os.path.join(_root(), ".git")):
        return NOT_GIT
    if os.environ.get(STARTED_BY_ENV) != "1":
        return NOT_START_BAT
    if (any(os.environ.get(name) for name in _OFF_ENV)
            or os.path.exists(os.path.join(_root(), "NOUPDATE"))):
        return TURNED_OFF
    code, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if code is None:
        return NO_GIT
    if code != 0 or not branch:
        return UNREADABLE
    if branch != BRANCH:
        return WRONG_BRANCH
    code, dirty = _git("status", "--porcelain", "--untracked-files=no")
    if code != 0:
        return UNREADABLE
    return EDITED if dirty else None


def _head():
    code, sha = _git("rev-parse", "HEAD")
    return sha if code == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def check() -> dict:
    """Fetches origin/baihe-subtitler and records how far behind this copy
    is. Changes nothing in the working tree. Repeated calls within a few
    seconds answer from the last one."""
    with _lock:
        fresh = time.monotonic() - _check["at"] < CHECK_MIN_INTERVAL and _check["at"] > 0
    if not fresh:
        _run_check()
    return status()


def _run_check() -> None:
    result = {"at": time.monotonic(), "behind": None, "latest": None, "changes": [], "error": None}
    problem = _local_problem()
    if problem is None:
        old = _head()
        code, _ = _git("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=15",
                       "fetch", "--quiet", "origin", BRANCH, timeout=FETCH_TIMEOUT)
        new = None
        if code == 0:
            code2, sha = _git("rev-parse", "FETCH_HEAD")
            new = sha if code2 == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None
        if old is None:
            problem = UNREADABLE
        elif new is None:
            problem = OFFLINE
        elif new == old:
            result.update(behind=0, latest=_short(new))
        elif _git("merge-base", "--is-ancestor", old, new)[0] != 0:
            problem = NOT_FF
        else:
            _, count = _git("rev-list", "--count", f"{old}..{new}")
            _, log = _git("log", f"-n{MAX_CHANGES}", "--format=%s", f"{old}..{new}")
            result.update(behind=int(count) if count.isdigit() else None, latest=_short(new),
                          changes=[s for s in (_subject(line) for line in log.splitlines()) if s])
    result["error"] = problem
    with _lock:
        _check.update(result)


def _settle_pending(head) -> None:
    """The first boot after an update: records whether the promised commit
    arrived. Not in the server that asked for it, which is still running."""
    pending = db.get_app_setting(PENDING_KEY, None)
    if not isinstance(pending, dict) or pending.get("pid") == os.getpid():
        return
    # start.bat fetches again, so it may land on a commit newer than the one
    # the check saw: any move off the old commit is an update.
    if head and head != pending.get("from"):
        message = f"Updated to {_short(head)}."
    else:
        reason = _local_problem() or "The update didn't finish"
        message = f"Not updated: {reason.rstrip('.')}. Still on {_short(head)}."
    db.set_app_setting(RESULT_KEY, {"message": message, "at": time.time()})
    db.set_app_setting(PENDING_KEY, None)


def status() -> dict:
    head = _head()
    problem = _local_problem()
    if problem != INSTALLED and problem != NOT_GIT:
        _settle_pending(head)
    result = db.get_app_setting(RESULT_KEY, None)
    with _lock:
        c = dict(_check)
        applying = _applying
    behind = c["behind"] if c["at"] else None
    reason = problem or c["error"]
    if not reason and not us._is_windows():
        reason = NOT_WINDOWS
    return {
        "available": problem not in (INSTALLED, NOT_GIT),
        "current": _short(head) or None,
        "checked": bool(c["at"]),
        "behind": behind,
        "latest": c["latest"],
        "changes": list(c["changes"]) if behind else [],
        "reason": reason,
        "can_apply": bool(not reason and behind and not applying),
        "applying": applying,
        "last_result": result.get("message") if isinstance(result, dict) else None,
        "last_result_at": result.get("at") if isinstance(result, dict) else None,
    }


def _start_helper(pid, port) -> None:
    command = [app_update_service._helper_python(), os.path.join(_root(), HELPER),
               "--root", _root(), "--pid", str(pid)]
    if port:
        command += ["--port", str(port)]
    # The leftovers of the run that last started this server would make the
    # next start.bat skip its fetch and report instead.
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in us._SETUP_ENV_DROP | _START_BAT_STATE_ENV}
    # Outside the server's Job Object: the helper outlives this process.
    with process_guard.breakaway_allowed():
        subprocess.Popen(command, cwd=_root(), env=env,
                         creationflags=(us._CREATE_BREAKAWAY_FROM_JOB | us._CREATE_NEW_PROCESS_GROUP
                                        if sys.platform == "win32" else 0),
                         close_fds=True)


def apply(confirm, actor_id=None) -> dict:
    """Stops this server cleanly and has start.bat update and start it again.
    One at a time. Needs a check that found something newer."""
    global _applying
    if confirm is not True:
        raise InvalidInputError(UNCONFIRMED)
    with _lock:
        if _applying:
            raise ConflictError(BUSY)
        _applying = True
    try:
        return _apply(actor_id)
    except BaseException:
        with _lock:
            _applying = False
        raise


def _apply(actor_id) -> dict:
    if not us._is_windows():
        raise UnsupportedOperationError(NOT_WINDOWS)
    problem = _local_problem()
    if problem:
        raise UnsupportedOperationError(problem)
    with _lock:
        checked, behind, error = _check["at"], _check["behind"], _check["error"]
    if not checked or error:
        raise ConflictError(error or NOT_CHECKED)
    if not behind:
        raise ConflictError(NOTHING_NEW)
    if background_jobs.active_job_ids():
        raise ConflictError(JOBS_RUNNING)
    head = _head()
    if head is None:
        raise ConflictError(UNREADABLE)
    db.set_app_setting(PENDING_KEY, {"from": head, "pid": os.getpid(), "at": time.time()})
    try:
        _start_helper(os.getpid(), app_update_service._server_port())
    except OSError:
        db.set_app_setting(PENDING_KEY, None)
        raise DependencyUnavailableError(START_FAILED) from None
    auth_service.write_audit(actor_id, "app.update", f"from {_short(head)}")
    shutdown_service.request_shutdown()
    return {"started": True, "from_commit": _short(head)}
