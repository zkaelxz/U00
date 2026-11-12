"""Whether a process id belongs to a running process, and when it started.

Standard library only: pending_install and the installer launcher run before
the app's dependencies are importable.
"""
import os

_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
MAX_PID = 0xFFFFFFFF
# FILETIME counts 100 ns ticks from 1601-01-01; Unix time starts in 1970.
_FILETIME_UNIX_OFFSET = 11_644_473_600


def _windows_kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def _windows_pid_alive(pid: int, kernel32, get_last_error) -> bool:
    """The Windows decision with kernel32 injected so it can be tested. A denied
    OpenProcess means the process exists; a failed GetExitCodeProcess reads
    alive, because calling a live process dead lets a second installer or a
    job takeover run on top of it."""
    import ctypes
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """True if a process with this pid exists. A pid no OS accepts (zero,
    negative, wider than a DWORD) is dead; any probe failure that does not
    say "no such process" reads alive."""
    if not 0 < pid <= MAX_PID:
        return False
    if os.name == "nt":
        import ctypes
        return _windows_pid_alive(pid, _windows_kernel32(), ctypes.get_last_error)
    # Windows must never reach os.kill: signal 0 is not a probe there and ends the process.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OverflowError:
        return False        # inside a DWORD but past this platform's pid_t
    except OSError:
        return True         # PermissionError: it exists, owned by someone else
    return True


def _windows_started_at(pid: int, kernel32) -> float | None:
    """Unix time the process holding `pid` was created, with kernel32
    injected so it can be tested; None when Windows won't say."""
    from ctypes import byref
    from ctypes.wintypes import FILETIME
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetProcessTimes(handle, byref(created), byref(exited), byref(kernel),
                                        byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 10_000_000 - _FILETIME_UNIX_OFFSET if ticks else None
    finally:
        kernel32.CloseHandle(handle)


def _linux_started_at(pid: int, proc_root: str = "/proc") -> float | None:
    try:
        with open(f"{proc_root}/{pid}/stat", encoding="ascii", errors="replace") as fh:
            stat = fh.read()
        with open(f"{proc_root}/stat", encoding="ascii", errors="replace") as fh:
            btime = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        # The command name (field 2) is in parentheses and may itself hold
        # spaces or ")", so fields are counted from the last ")": starttime,
        # field 22, is the 20th after it.
        ticks = int(stat[stat.rindex(")") + 2:].split()[19])
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def process_started_at(pid: int) -> float | None:
    """Unix time the process now holding `pid` started, or None when it is
    unknown (no such process, access refused, or a platform with no cheap
    standard-library probe)."""
    if not 0 < pid <= MAX_PID:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = _windows_kernel32()
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        return _windows_started_at(pid, k32)
    if os.path.isdir("/proc/self"):
        return _linux_started_at(pid)
    return None
