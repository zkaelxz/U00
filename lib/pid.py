"""Whether a process id belongs to a running process.

Standard library only: pending_install and the installer launcher run before
the app's dependencies are importable.
"""
import os

_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
MAX_PID = 0xFFFFFFFF


def _windows_kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
