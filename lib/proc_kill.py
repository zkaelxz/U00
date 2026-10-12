"""lib/proc_kill.py -- killing a child process together with everything it started."""

import logging
import os
import subprocess

# The app's logger by name (applog owns its file handler and secret filter,
# but lib can't import it). Only the exception type is logged.
_log = logging.getLogger("baihe")


def kill_tree(proc, warn=None):
    """Kills proc and everything it started (it runs in its own process
    group/session -- see run_cancellable), so a wrapper script's ffmpeg
    grandchild can't keep the pipes open. `warn(what, exc)` reports a swallowed
    failure; the default only logs the exception type."""
    warn = warn or (lambda what, exc: _log.warning("%s: %s", what, type(exc).__name__))
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass   # the group already exited
    except Exception as exc:
        warn(f"could not kill process tree {proc.pid}", exc)
    try:
        proc.kill()
    except Exception as exc:
        warn(f"could not kill process {proc.pid}", exc)


# ---------------------------------------------------------------------------
# Telling a process apart from a later one that reuses its pid. A pid alone
# names whatever runs under it now, so a dead server's notes can only be used
# to stop a process whose start time and command still match what was noted.
# ---------------------------------------------------------------------------

_STOP_WAIT_SECONDS = 5.0


def _digest(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _linux_identity(pid: int) -> dict | None:
    """Raises FileNotFoundError / ProcessLookupError when no such process."""
    with open(f"/proc/{pid}/stat", "rb") as fh:
        stat_line = fh.read()
    # The command name in parentheses may itself contain spaces or ")".
    fields = stat_line[stat_line.rindex(b")") + 2:].split()
    start_ticks = fields[19].decode()          # field 22, starttime since boot
    with open("/proc/sys/kernel/random/boot_id", "rb") as fh:
        boot = fh.read().strip().decode()
    with open(f"/proc/{pid}/cmdline", "rb") as fh:
        cmdline = fh.read()
    return {"start": f"{boot}:{start_ticks}", "cmd": _digest(cmdline)}


def _win_kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD))
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.WaitForSingleObject.restype = wintypes.DWORD
    return k


def _win_identity_from_handle(k, handle) -> dict | None:
    import ctypes
    import ntpath
    from ctypes import wintypes
    times = [wintypes.FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    size = wintypes.DWORD(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return None
    image = ntpath.basename(buf.value).lower()
    return {"start": str(created), "cmd": _digest(image.encode("utf-8"))}


_WIN_QUERY = 0x1000         # PROCESS_QUERY_LIMITED_INFORMATION
_WIN_TERMINATE = 0x0001
_WIN_SYNCHRONIZE = 0x00100000
_WIN_STILL_ACTIVE = 259


def process_identity(pid: int) -> dict | None:
    """{"start", "cmd"} for a running process: its start time and a digest of
    what it runs (Linux: the full command line; Windows: the executable's
    name). None when the process is gone or the platform can't tell, and a
    process without an identity is never stopped by stop_if_same."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if os.name == "nt":
            k = _win_kernel32()
            handle = k.OpenProcess(_WIN_QUERY, False, pid)
            if not handle:
                return None
            try:
                return _win_identity_from_handle(k, handle)
            finally:
                k.CloseHandle(handle)
        if os.path.isdir("/proc/self"):
            return _linux_identity(pid)
    except Exception:
        return None
    return None


def stop_if_same(pid: int, identity: dict, wait: float = _STOP_WAIT_SECONDS) -> str:
    """Kills `pid` only if it is still the process `identity` describes.

    "gone": nothing runs under that pid, or a different process does.
    "stopped": it matched, was killed and has exited.
    "kept": it may still be the recorded process but could not be checked or
    stopped (unsupported platform, no permission, no race-free handle), so
    whatever it is writing must be left alone.

    The check and the kill use one handle (a Windows process handle, a Linux
    pidfd), so the pid can't be handed to another process between them.
    Never raises: a corrupt note or an unexpected OS error answers "kept"."""
    try:
        pid = int(pid)
        if not 0 < pid <= _MAX_PID or pid == os.getpid() or not isinstance(identity, dict):
            return "kept"
        if os.name == "nt":
            return _win_stop_if_same(pid, identity, wait)
        if os.path.isdir("/proc/self"):
            return _linux_stop_if_same(pid, identity, wait)
    except Exception as exc:
        _log.warning("could not check leftover process: %s", type(exc).__name__)
    return "kept"


_MAX_PID = 2**31 - 1


def _pidfd_exited(pidfd, wait_ms: int = 0) -> bool:
    import select
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)     # readable once the process has exited
    return bool(poller.poll(wait_ms))


def _linux_stop_if_same(pid, identity, wait):
    import signal
    pidfd = None
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return "gone"
    except (AttributeError, OSError):
        pidfd = None    # no pidfd: the identity can still prove it is gone
    try:
        try:
            now = _linux_identity(pid)
        except (FileNotFoundError, ProcessLookupError):
            return "gone"
        except Exception:
            return "kept"
        if now != identity:
            return "gone"
        if pidfd is None:
            return "kept"
        try:
            # A noted process leads its own session (run_cancellable's
            # start_new_session) and a session leader can't leave its group,
            # so while it is alive group `pid` is exactly its tree: a wrapper's
            # grandchild goes too instead of writing into a removed folder.
            if os.getpgid(pid) == pid and not _pidfd_exited(pidfd):
                os.killpg(pid, signal.SIGKILL)
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
        except ProcessLookupError:
            pass    # already ended by the group kill
        except OSError as exc:
            _log.warning("could not stop leftover process %s: %s", pid, type(exc).__name__)
            return "kept"
        return "stopped" if _pidfd_exited(pidfd, int(wait * 1000)) else "kept"
    finally:
        if pidfd is not None:
            os.close(pidfd)


def _win_stop_if_same(pid, identity, wait):
    import ctypes
    from ctypes import wintypes
    try:
        k = _win_kernel32()
    except Exception:
        return "kept"
    handle = k.OpenProcess(_WIN_QUERY | _WIN_TERMINATE | _WIN_SYNCHRONIZE, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER: no process has this pid. Anything else
        # (access denied) may still be the recorded process.
        return "gone" if ctypes.get_last_error() == 87 else "kept"
    try:
        code = wintypes.DWORD()
        if k.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _WIN_STILL_ACTIVE:
            return "gone"
        now = _win_identity_from_handle(k, handle)
        if now is None:
            return "kept"
        if now != identity:
            return "gone"
        if not k.TerminateProcess(handle, 1):
            _log.warning("could not stop leftover process %s", pid)
            return "kept"
        return "stopped" if k.WaitForSingleObject(handle, int(wait * 1000)) == 0 else "kept"
    finally:
        k.CloseHandle(handle)
