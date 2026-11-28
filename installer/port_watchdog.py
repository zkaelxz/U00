"""Outside check that the app's admin port still answers, and restarts the BaiheStudio service when it doesn't; run once a minute by a scheduled task (installer/install_port_watchdog.ps1)."""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field

SERVICE = "BaiheStudio"
LOG_NAME = "watchdog.log"
STATE_NAME = "watchdog-state.json"
LOG_KEEP_BYTES = 512 * 1024
# job_records rows of a process that died are never closed, so only a recent
# update counts as a job that is really running.
JOB_FRESH_SECONDS = 600
START_GRACE_SECONDS = 120
# A first start of a new version may compile its .pyc files for minutes
# (installer/service_update.py HEALTH_WAIT_SECONDS is 300 s), and the service
# reads RUNNING from the moment its process exists, so a restart in that
# window would push an update into its rollback.
START_PROTECT_SECONDS = 360
UPDATE_SERVICE = "BaiheUpdate"
CREATE_NO_WINDOW = 0x08000000


@dataclass
class Config:
    failures_needed: int = 3
    max_restarts_per_hour: int = 2
    # Past this, a running job no longer holds the restart back: a port that
    # has been dead for an hour is worse than one job restarted.
    max_defer_seconds: int = 3600


@dataclass
class State:
    failures: int = 0
    outage_started: float | None = None
    quiet_until: float = 0.0
    restarts: list = field(default_factory=list)
    total_failures: int = 0
    total_restarts: int = 0
    total_skips: int = 0


def decide(state: State, now: float, cfg: Config, *, api_ok: bool, others_ok: bool,
           service_running: bool, job_running: bool, updating: bool = False,
           started_ago: float | None = None) -> str:
    """One minute's decision, changing `state`: OK, WAIT, DEFER_JOB, CAPPED,
    NOT_RUNNING, UPDATING, STARTING or RESTART. The caller does the restart.
    `started_ago` is the seconds since the service process started, if known."""
    if api_ok:
        state.failures = 0
        return "OK"
    if not service_running:
        # A stop by the owner or by an update is not an outage.
        state.failures = 0
        state.outage_started = None
        return "NOT_RUNNING"
    if updating or (started_ago is not None and started_ago < START_PROTECT_SECONDS):
        state.failures = 0
        state.outage_started = None
        state.total_skips += 1
        return "UPDATING" if updating else "STARTING"
    if now < state.quiet_until:
        return "WAIT"
    state.failures += 1
    state.total_failures += 1
    if state.outage_started is None:
        state.outage_started = now
    if state.failures < cfg.failures_needed:
        return "WAIT"
    # With every port dead the jobs are dead too; deferring protects nothing.
    if job_running and others_ok and now - state.outage_started < cfg.max_defer_seconds:
        return "DEFER_JOB"
    state.restarts = [t for t in state.restarts if now - t < 3600]
    if len(state.restarts) >= cfg.max_restarts_per_hour:
        return "CAPPED"
    state.restarts.append(now)
    state.total_restarts += 1
    state.failures = 0
    state.quiet_until = now + START_GRACE_SECONDS
    return "RESTART"


def port_answers(port: int) -> bool:
    # No proxy: a machine-wide proxy setting must not answer for loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def job_is_running(data_dir: str, now: float) -> bool:
    path = os.path.join(data_dir, "library", "library.db")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("SELECT COUNT(*) FROM job_records WHERE status IN ('queued', 'running') "
                               "AND updated_at > ?", (now - JOB_FRESH_SECONDS,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        # An unreadable database must not decide a restart either way; "no
        # job" keeps a dead port from staying dead for lack of an answer.
        return False
    return bool(row and row[0])


def _sc(*args: str) -> str:
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # By full path: this runs as SYSTEM.
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "sc.exe") if sys.platform == "win32" else "sc"
    out = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=flags)
    return out.stdout


def service_state() -> str:
    for word in ("RUNNING", "STOPPED", "START_PENDING", "STOP_PENDING"):
        if word in _sc("query", SERVICE):
            return word
    return "UNKNOWN"


def update_in_progress() -> bool:
    # BaiheUpdate runs only for an app update or a package install.
    out = _sc("query", UPDATE_SERVICE)
    # State words, not the "STATE" label, which sc translates.
    return any(word in out for word in ("RUNNING", "START_PENDING", "STOP_PENDING", "PAUSED", "CONTINUE_PENDING"))


def service_started_ago(now: float) -> float | None:
    """Seconds since the BaiheStudio service process started, or None when
    that can't be read (then only the BaiheUpdate check protects a start)."""
    if sys.platform != "win32":
        return None
    try:
        return _process_age(now)
    except Exception:
        return None


def _process_age(now: float) -> float | None:
    import ctypes
    from ctypes import wintypes
    m = re.search(r"PID\s*:\s*(\d+)", _sc("queryex", SERVICE))
    if not m or int(m.group(1)) == 0:
        return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Without these, a handle is passed as a 32-bit int and breaks on 64-bit Windows.
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, int(m.group(1)))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, ended, kernel, user))):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return now - (ticks / 1e7 - 11644473600)  # FILETIME is 100 ns since 1601
    finally:
        k32.CloseHandle(handle)


def restart_service() -> str:
    _sc("stop", SERVICE)
    deadline = time.time() + 60
    while time.time() < deadline and service_state() != "STOPPED":
        time.sleep(2)
    _sc("start", SERVICE)
    return service_state()


def load_state(path: str) -> State:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return State(**{k: v for k, v in data.items() if k in State.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return State()


def save_state(path: str, state: State) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(asdict(state), fh)
    os.replace(tmp, path)


def write_log(folder: str, line: str) -> None:
    path = os.path.join(folder, LOG_NAME)
    try:
        if os.path.getsize(path) > LOG_KEEP_BYTES:
            os.replace(path, path + ".old")
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line + "\n")


def run_once(args, now: float | None = None) -> str:
    now = time.time() if now is None else now
    state_path = os.path.join(args.state_dir, STATE_NAME)
    state = load_state(state_path)
    cfg = Config(args.failures, args.max_restarts_per_hour, args.max_defer_minutes * 60)
    api_ok = port_answers(args.api_port)
    others_ok = any(port_answers(p) for p in args.other_ports)
    running = service_state() == "RUNNING"
    job = False if api_ok else job_is_running(args.data_dir, now)
    was_down_since = state.outage_started
    updating = False if api_ok else update_in_progress()
    started_ago = None if api_ok or not running else service_started_ago(now)
    action = decide(state, now, cfg, api_ok=api_ok, others_ok=others_ok, service_running=running, job_running=job,
                    updating=updating, started_ago=started_ago)
    counts = (f"failures_seen={state.total_failures} restarts_done={state.total_restarts} skips={state.total_skips} "
              f"api_port={args.api_port} other_ports_answer={others_ok} job_running={job}")
    if action == "OK":
        if was_down_since is not None:
            write_log(args.state_dir, f"RECOVERED {int(now - was_down_since)}s after the first failed check; {counts}")
        state.outage_started = None
    elif action == "RESTART":
        if args.dry_run:
            # A trial must not use up the hourly cap or inflate the counts.
            state.restarts.pop()
            state.total_restarts -= 1
            state.quiet_until = 0.0
            write_log(args.state_dir, f"DRY-RUN would restart {SERVICE}; {counts}")
        else:
            write_log(args.state_dir, f"RESTARTING {SERVICE} after {cfg.failures_needed} failed checks; {counts}")
            write_log(args.state_dir, f"service state after restart: {restart_service()}")
    elif action in ("UPDATING", "STARTING"):
        write_log(args.state_dir, f"{action}: restart skipped; {counts}")
    elif action != "WAIT" or state.failures >= 1:
        write_log(args.state_dir, f"{action} (check {state.failures} of {cfg.failures_needed}); {counts}")
    save_state(state_path, state)
    return action


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", required=True, help="the folder that holds library\\")
    p.add_argument("--state-dir", required=True, help="admin-only folder for the log and state")
    p.add_argument("--api-port", type=int, default=8600)
    p.add_argument("--other-ports", type=int, nargs="*", default=[8610, 8756])
    p.add_argument("--failures", type=int, default=3)
    p.add_argument("--max-restarts-per-hour", type=int, default=2)
    p.add_argument("--max-defer-minutes", type=int, default=60)
    p.add_argument("--dry-run", action="store_true", help="log what it would do; restart nothing")
    args = p.parse_args(argv)
    os.makedirs(args.state_dir, exist_ok=True)
    run_once(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
