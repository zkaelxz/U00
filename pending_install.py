"""
pending_install.py -- an install queued for the next start, and the apply step
that runs it before the server imports anything.

On Windows pip cannot replace a compiled file (cv2.pyd, numpy, torch, ...) that
the running server has loaded: it fails with "Access is denied" after it has
already removed the old version. So an install that would replace a loaded
package is written here instead, and `python -m pending_install` (run by
start.bat and the installed launcher before `python -m api`) performs it while
nothing is loaded.

Standard library only, and no import of the app: this process must not itself
load numpy or cv2, or it would hold the very files pip is about to replace. The
registry lookups and the redaction run in short-lived child processes
(pending_install_child.py) that exit before pip starts.

The pending file holds package keys only. The apply step re-derives the pip
command from the registry, so a tampered or hand-written file cannot add an
argument, an index URL or a version; the stored SHA-256 additionally catches a
damaged or edited file. It is not a signature: anyone who can write the data
folder could recompute it, which is why the file is never trusted for more than
names the registry already offers.
"""

import hashlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import threading
import time

import portable
from lib.proc import run_captured, stream_tree

SCHEMA = 1
PIP_SECONDS = 40 * 60
RESTORE_SECONDS = 15 * 60
OVERALL_SECONDS = 60 * 60       # the server starts anyway after this
CHILD_SECONDS = 120
WATCHDOG_MARGIN = 90
TAIL_LINES = 40
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.!_-]{0,63}$")      # no "+": local builds aren't on PyPI
LOCK_TOUCH_SECONDS = 30
# Mirrors diagnostics.PIP_INSTALL_FLAGS, which this stdlib-only module cannot import;
# a test keeps the two equal.
_PIP_FLAGS = ("--no-cache-dir", "--disable-pip-version-check")
_FILES = {"pending": "pending.json", "result": "result.json", "lock": "apply.lock"}


def state_dir() -> str:
    return os.path.join(portable.data_dir(), "pending_install")


def _path(kind: str) -> str:
    return os.path.join(state_dir(), _FILES[kind])


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _write_json(kind: str, doc: dict) -> None:
    os.makedirs(state_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=state_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, sort_keys=True)
        os.replace(tmp, _path(kind))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_json(kind: str):
    try:
        with open(_path(kind), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _remove(kind: str) -> None:
    try:
        os.remove(_path(kind))
    except OSError:
        pass


def write_pending(keys, before: dict = None, now=time.time) -> dict:
    """Queue `keys` (registry package keys) for the next start. `before` maps
    dists the plan replaces to their current versions; it is shown to the
    owner, while the restore uses a fresh snapshot taken at apply time."""
    payload = {"schema": SCHEMA, "packages": list(keys), "created": int(now()),
               "before": dict(before or {})}
    _write_json("pending", {"payload": payload, "sha256": digest(payload)})
    _remove("result")
    return payload


def read_pending():
    """(payload, None) for a pending install that checks out, (None, None)
    when nothing is queued, (None, reason) for a file that must not run."""
    doc = _read_json("pending")
    if doc is None:
        return (None, None) if not os.path.exists(_path("pending")) else (None, "unreadable")
    payload = doc.get("payload")
    if not isinstance(payload, dict) or doc.get("sha256") != digest(payload):
        return None, "modified"
    keys = payload.get("packages")
    before = payload.get("before")
    # A re-hashed file can carry any types; the status page must read it as
    # "invalid" rather than fail on them.
    if (payload.get("schema") != SCHEMA or not isinstance(keys, list) or not keys
            or not all(isinstance(k, str) and _NAME_RE.match(k) for k in keys)
            or not isinstance(payload.get("created"), int) or isinstance(payload.get("created"), bool)
            or not isinstance(before, dict)
            or not all(isinstance(n, str) and isinstance(v, str) for n, v in before.items())):
        return None, "invalid"
    return payload, None


def cancel() -> bool:
    existed = os.path.exists(_path("pending"))
    _remove("pending")
    return existed


def read_result():
    return _read_json("result")


def clear_result() -> None:
    _remove("result")


# Windows byte-range locks are mandatory, so the lock sits on a byte far past the
# JSON: status() and the launcher must still be able to read the pid.
_LOCK_BYTE = 1 << 30
_LOCK = {"fd": None}        # the open apply.lock this process holds the OS lock on
_OWN_START = []


def _kernel32():
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def _win_pid_running(pid: int, kernel32, get_last_error) -> bool:
    import ctypes
    handle = kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return get_last_error() == 5                        # access denied: it exists
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True         # the call failed: unknown, and wrongly calling it dead runs pip twice
        return code.value == 259                            # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_running(pid: int) -> bool:
    if not 0 < pid <= 0xFFFFFFFF:
        return False            # no real process has it; ctypes and os.kill would raise
    if os.name == "nt":
        import ctypes
        return _win_pid_running(pid, _kernel32(), ctypes.get_last_error)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True                                             # EPERM: owned by someone else, still running
    except (OSError, OverflowError):
        return False
    return True


def _process_start(pid: int):
    """An opaque start time for `pid`, or None where it can't be read. A reused
    pid has a different one, so a lock written by a dead process isn't kept alive
    by whatever got its number."""
    if not 0 < pid <= 0xFFFFFFFF:
        return None
    if os.name == "nt":
        import ctypes
        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, *rest = [ctypes.c_ulonglong() for _ in range(4)]
            ok = kernel32.GetProcessTimes(handle, ctypes.byref(created), *map(ctypes.byref, rest))
            return created.value if ok and created.value else None
        finally:
            kernel32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            # Field 22 (starttime); the command name in field 2 may contain spaces and ")".
            return int(f.read().rsplit(b")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid: int, started=None) -> bool:
    if not _pid_running(pid):
        return False
    if not isinstance(started, int) or isinstance(started, bool):
        return True             # older lock files and the launcher's readers carry no start time
    now = _process_start(pid)
    return now is None or now == started


def _record_live(doc, mtime: float) -> bool:
    """The lock file's own word on whether its writer still runs. Only consulted
    when no process holds the OS lock (a crashed start, or a writer without one)."""
    if time.time() - mtime >= OVERALL_SECONDS + 120:
        return False
    # After a crash the lock is fresh for the next hour; its pid says nobody holds it.
    # A lock without a readable pid keeps the age rule alone.
    pid = (doc if isinstance(doc, dict) else {}).get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool):
        return _pid_alive(pid, doc.get("started"))
    return True


def _os_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _close_held(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)      # closing alone frees it only "eventually"
    except OSError:
        pass
    os.close(fd)


def _try_hold(create: bool):
    """(fd, busy): an fd holding the OS lock on apply.lock, or busy=True when
    another open handle (any process) holds it."""
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | (os.O_CREAT if create else 0)
    for _ in range(3):
        try:
            fd = os.open(_path("lock"), flags)
        except OSError:
            return None, False
        if not _os_lock(fd):
            os.close(fd)
            return None, True
        if os.name == "nt":
            return fd, False    # Windows can't delete a file another handle has open
        try:
            if os.path.samestat(os.fstat(fd), os.stat(_path("lock"))):
                return fd, False
        except OSError:
            pass
        _close_held(fd)         # the holder unlinked it before we locked it: try the new file
    return None, False


def _write_held(fd: int) -> None:
    if not _OWN_START:
        _OWN_START.append(_process_start(os.getpid()))
    doc = {"pid": os.getpid(), "time": int(time.time())}
    if _OWN_START[0] is not None:
        doc["started"] = _OWN_START[0]
    data = json.dumps(doc).encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, data)
    os.ftruncate(fd, len(data))


def _release_lock() -> None:
    """Frees and removes the lock this process holds; a lock it doesn't hold is
    another start's and is left alone."""
    fd = _LOCK.pop("fd", None)
    _LOCK["fd"] = None
    if fd is None:
        return
    if os.name == "nt":
        _close_held(fd)         # Windows refuses to delete a file with an open handle
        _remove("lock")         # and refuses while a newer start holds it, so this can't take theirs
    else:
        _remove("lock")         # unlinked while still held, so no newer start's file is deleted
        _close_held(fd)


def apply_running() -> bool:
    """True while a live apply holds the lock (a stale one doesn't count)."""
    try:
        mtime = os.path.getmtime(_path("lock"))
    except OSError:
        return False
    if time.time() - mtime >= OVERALL_SECONDS + 120:
        return False
    fd, busy = _try_hold(create=False)
    if busy:
        return True
    if fd is not None:
        _close_held(fd)
    return _record_live(_read_json("lock") or {}, mtime)


def status() -> dict:
    """What Diagnostics shows: the queued install, the last outcome, and
    whether an apply is running right now."""
    payload, problem = read_pending()
    result = read_result()
    if result and result.get("status") == "running" and not apply_running():
        result = {"status": "interrupted", "packages": result.get("packages", []),
                  "message": "The install did not finish: Baihe was closed while it ran.",
                  "tail": [], "restored": [], "restore_failed": []}
    return {"pending": payload, "problem": problem, "result": result,
            "applying": apply_running()}


# --- running things -------------------------------------------------------

# The watchdog sets "stop" so run_capture's cancel poll kills pip's whole tree.
# It is never cleared: a later pip (the restore) would be abandoned by the
# watchdog's os._exit half-way, which is the state this module exists to avoid.
_CURRENT = {"stop": False}


def run_capture(argv: list, timeout: float, echo: bool = False):
    """(returncode, last lines, timed_out). No shell; the tree is killed on
    timeout so pip's own children can't outlive the budget."""
    lines, end = [], {"returncode": None, "timed_out": False}
    touched = [time.monotonic()]

    def cancel():
        # Polled even while pip is silent, unlike the output lines; status()
        # reads a stale lock as a crashed apply, so a live one keeps it fresh.
        if time.monotonic() - touched[0] >= LOCK_TOUCH_SECONDS:
            touched[0] = time.monotonic()
            try:
                fd = _LOCK["fd"]
                if fd is not None and not _CURRENT["stop"]:     # the watchdog may be closing it
                    _write_held(fd)             # Windows can't utime a file with an open handle
                else:
                    os.utime(_path("lock"))
            except OSError:
                pass
        return _CURRENT["stop"]

    for event in stream_tree(argv, timeout=max(1.0, timeout), cancel=cancel):
        if "line" in event:
            lines.append(event["line"])
            del lines[:-200]
            if echo:
                print(event["line"], flush=True)
        else:
            end = event
    return end["returncode"], lines, end["timed_out"]


def _python() -> list:
    # -s as the launcher starts the server, so pip sees the same site-packages.
    return [sys.executable] + (["-s"] if sys.flags.no_user_site else [])


def child_json(*args, stdin_text: str = None):
    """Runs pending_install_child in a fresh interpreter and returns its last
    JSON line, or None. lib.proc gives a child no stdin, so `stdin_text`
    travels as a temp file whose path is the last argument."""
    text_path = None
    try:
        if stdin_text is not None:
            os.makedirs(state_dir(), exist_ok=True)
            fd, text_path = tempfile.mkstemp(prefix="redact-", suffix=".txt", dir=state_dir())
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(stdin_text)
        argv = _python() + ["-m", "pending_install_child", *args] + ([text_path] if text_path else [])
        proc = run_captured(argv, CHILD_SECONDS, cwd=os.path.dirname(os.path.abspath(__file__)))
    except OSError:
        return None
    finally:
        if text_path:
            try:
                os.remove(text_path)
            except OSError:
                pass
    for line in reversed(proc.stdout.splitlines()):
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        return doc if isinstance(doc, dict) else None
    return None


def _argv_accepted(argv) -> bool:
    """The child prints the command on its stdout, where any stray JSON line in
    a module it imports would win; run only a registry-shaped `pip install`."""
    if (not isinstance(argv, list) or len(argv) < 5 or argv[0] != sys.executable
            or argv[1:4] != ["-m", "pip", "install"]
            or not all(isinstance(a, str) for a in argv)):
        return False
    allowed_dirs = [os.path.normcase(os.path.realpath(d))
                    for d in (state_dir(), tempfile.gettempdir(),
                              os.path.dirname(os.path.abspath(__file__)))]
    rest, i = argv[4:], 0
    while i < len(rest):
        arg = rest[i]
        if arg in _PIP_FLAGS or _NAME_RE.match(arg):
            i += 1
        elif arg == "-c" and i + 1 < len(rest):
            path = os.path.normcase(os.path.realpath(rest[i + 1]))
            if not any(os.path.dirname(path) == d for d in allowed_dirs):
                return False
            i += 2
        else:
            return False
    return True


def snapshot() -> dict:
    """{canonical dist name: version} of everything installed (metadata only)."""
    out = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            out[re.sub(r"[-_.]+", "-", name).lower()] = dist.version
    return out


def _redacted_tail(lines: list) -> list:
    text = "\n".join(lines[-TAIL_LINES:])
    doc = child_json("redact", stdin_text=text)
    if doc and isinstance(doc.get("lines"), list):
        return [str(x) for x in doc["lines"]][-TAIL_LINES:]
    return ["(pip's output is hidden: it could not be cleaned of paths and names)"]


# --- the apply step -------------------------------------------------------

def _left_by_live_start(fd: int) -> bool:
    """Read while holding the OS lock, so whoever wrote it no longer holds it."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096)
        mtime = os.fstat(fd).st_mtime
    except OSError:
        return False
    if not raw.strip():
        return False            # a start that died between creating the file and writing it
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        doc = {}
    if isinstance(doc, dict) and doc.get("pid") == os.getpid():
        return False            # ours, from an earlier apply in this process
    return _record_live(doc, mtime)


def _acquire_lock(wait_until: float, sleep=time.sleep) -> bool:
    # The OS lock, not the file, decides: two starts after a crash can both read
    # a dead pid, but only one can hold the lock, and nobody deletes a held file.
    os.makedirs(state_dir(), exist_ok=True)
    while True:
        fd, _busy = _try_hold(create=True)
        if fd is not None:
            if not _left_by_live_start(fd):
                _write_held(fd)
                _LOCK["fd"] = fd
                return True
            _close_held(fd)
        if time.time() >= wait_until:
            return False
        sleep(1.0)


def wait_while_running(until: float, echo: bool = False, sleep=time.sleep) -> bool:
    """Waits for a live apply (another start's) to finish. True when none is
    running, False when `until` came first. The pending file is removed before
    pip starts, so "nothing queued" does not mean "nothing happening"."""
    if not apply_running():
        return True
    if echo:
        print("Another start is installing packages. Waiting for it to finish...", flush=True)
    while apply_running():
        if time.time() >= until:
            return False
        sleep(1.0)
    return True


def _restore(before: dict, now_versions: dict, budget: float, echo: bool):
    """Puts back every package whose version changed or that went missing.
    Returns (restored, failed) dist names."""
    wanted = {n: v for n, v in before.items()
              if now_versions.get(n) != v}
    safe = {n: v for n, v in wanted.items() if _NAME_RE.match(n) and _VERSION_RE.match(v)}
    failed = sorted(set(wanted) - set(safe))
    if not safe:
        return [], failed
    argv = _python() + ["-m", "pip", "install", "--no-deps", "--no-cache-dir",
                        "--disable-pip-version-check",
                        *[f"{n}=={v}" for n, v in sorted(safe.items())]]
    rc, _lines, timed_out = run_capture(argv, budget, echo)
    after = snapshot()
    ok = [n for n, v in safe.items() if after.get(n) == v]
    return sorted(ok), sorted(failed + [n for n in safe if n not in ok])


def _finish(result: dict, lines: list) -> dict:
    result["tail"] = _redacted_tail(lines)
    result["finished"] = int(time.time())
    _write_json("result", result)
    return result


def apply(echo: bool = False, wait_seconds: float = OVERALL_SECONDS) -> dict:
    """Runs the queued install, if any. Never raises and never leaves the
    pending file behind; the outcome is in result.json. Returns a short
    summary dict for the caller and tests."""
    started = time.time()
    if not os.path.exists(_path("pending")):
        if not wait_while_running(started + wait_seconds, echo):
            return {"ran": False, "locked": True}
        return {"ran": False}
    if not _acquire_lock(started + wait_seconds):
        return {"ran": False, "locked": True}
    deadline = time.time() + OVERALL_SECONDS     # the lock wait is not part of the install's budget
    try:
        payload, problem = read_pending()
        if payload is None:
            if problem is None:
                return {"ran": False}      # the apply we waited for ran it; keep its outcome
            _remove("pending")
            return {"ran": True, **_finish({
                "status": "refused", "packages": [], "restored": [], "restore_failed": [],
                "message": "A queued install was not run because its file did not check out. "
                           "Nothing was changed."}, [])}
        keys = payload["packages"]
        _remove("pending")        # a crash must not re-run it on every start
        _write_json("result", {"status": "running", "packages": keys, "tail": [],
                               "message": "Installing the packages you queued...",
                               "restored": [], "restore_failed": []})
        plan = child_json("derive", ",".join(keys))
        if not plan or not plan.get("ok"):
            return {"ran": True, **_finish({
                "status": "refused", "packages": keys, "restored": [], "restore_failed": [],
                "message": (plan or {}).get("message") or
                           "The queued install was not run: the package list was not accepted. "
                           "Nothing was changed."}, [])}
        argv = plan.get("argv")
        if not _argv_accepted(argv):
            return {"ran": True, **_finish({
                "status": "refused", "packages": keys, "restored": [], "restore_failed": [],
                "message": "The queued install was not run: the package list was not accepted. "
                           "Nothing was changed."}, [])}
        before = snapshot()
        # Saved before pip runs so the watchdog, which can exit mid-restore, can still say what was at risk.
        _write_json("result", {"status": "running", "packages": keys, "before": before, "tail": [],
                               "message": "Installing the packages you queued...",
                               "restored": [], "restore_failed": []})
        argv[1:1] = _python()[1:]
        if echo:
            print("Installing the packages you queued: " + ", ".join(keys), flush=True)
        try:
            rc, lines, timed_out = run_capture(argv, min(PIP_SECONDS, deadline - time.time()), echo)
        finally:
            for path in plan.get("temp_files", []):
                try:
                    os.remove(path)
                except OSError:
                    pass
        if rc == 0 and not timed_out:
            return {"ran": True, **_finish({
                "status": "ok", "packages": keys, "restored": [], "restore_failed": [],
                "message": "Installed " + ", ".join(keys) + " when Baihe started."}, lines)}
        if _CURRENT["stop"]:
            restored, failed = [], []        # the watchdog is about to exit; a restore would be cut off
        else:
            restored, failed = _restore(before, snapshot(),
                                        min(RESTORE_SECONDS, max(60.0, deadline - time.time())), echo)
        if timed_out:
            head = "The install did not finish in time and was stopped. "
        else:
            head = "The install did not work. "
        if failed:
            tail = ("Some packages could not be put back: " + ", ".join(failed) +
                    ". Install them again from Packages in Diagnostics.")
        elif restored:
            tail = "Your earlier packages were put back."
        else:
            tail = "Nothing else was changed."
        return {"ran": True, **_finish({
            "status": "timed_out" if timed_out else "failed", "packages": keys,
            "restored": restored, "restore_failed": failed, "message": head + tail}, lines)}
    finally:
        _release_lock()


def _watchdog_result() -> dict:
    """The timed_out result, keeping what the running result recorded: the exit
    can land mid-restore, so the owner needs to know which packages were at risk."""
    running = read_result()
    if not (running and running.get("status") == "running"):
        running = {}
    packages, before = running.get("packages"), running.get("before")
    return {"status": "timed_out", "tail": [], "restored": [], "restore_failed": [],
            "packages": packages if isinstance(packages, list) else [],
            "before": before if isinstance(before, dict) else {},
            "message": "The install did not finish in time. Baihe started without waiting "
                       "for it, so package versions may have been changed. Check Packages "
                       "in Diagnostics.",
            "finished": int(time.time())}


def main(argv=None) -> int:
    """Always 0: a failed or slow install must not stop the server starting."""
    def watchdog():
        _CURRENT["stop"] = True
        time.sleep(2)       # the cancel poll runs about twice a second
        try:
            _write_json("result", _watchdog_result())
        except OSError:
            pass
        _release_lock()
        os._exit(0)
    timer = threading.Timer(OVERALL_SECONDS + WATCHDOG_MARGIN, watchdog)
    timer.daemon = True
    timer.start()
    try:
        apply(echo=True)
    except Exception as exc:        # the server start comes first
        print(f"Queued install skipped: {type(exc).__name__}", flush=True)
        _release_lock()
    finally:
        timer.cancel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
