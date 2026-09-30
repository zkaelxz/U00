"""
process_guard.py -- ties every process the API server starts (ffmpeg,
yt-dlp, Playwright's Node driver and its Chromium, lncrawl, pip, ...) to the
server's own lifetime on Windows ("Stop Baihe" stops everything, and so does
closing the server's window).

`python -m api` calls contain_children() at startup: the server puts itself
into a Windows Job Object with "kill on job close". Child processes join the
job automatically, and when the server process ends for any reason (a clean
stop, a forced one, its console window being closed, a crash) Windows ends
every process still in the job -- and only those, never another program.
This works the same for start.bat and for the installed launcher.

The installed launcher (installer/launcher.py) also passes a per-install name
in BAIHE_PROCESS_GROUP_NAME, so its `--stop` can end the whole job by name
(terminate_group) as its last resort. Without a name (start.bat) the job is
anonymous.

install_console_close_handler() runs the app's clean shutdown when the
server's console window is closed or Windows shuts down, before Windows ends
the process (it allows about 5 seconds).

Everything here is a no-op returning False outside Windows
(sys.platform != "win32"), and never raises. Standard library only (ctypes):
the launcher uses it too.
"""

import hashlib
import os
import sys

GROUP_NAME_ENV = "BAIHE_PROCESS_GROUP_NAME"

_JOB_OBJECT_ASSIGN_PROCESS = 0x0001
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

# Console events that end the process once the handler returns. Ctrl+C and
# Ctrl+Break are left to Python/uvicorn (a normal stop); CTRL_LOGOFF_EVENT is
# sent for any user's logoff, not necessarily this one's, so it's ignored.
CTRL_CLOSE_EVENT = 2
CTRL_SHUTDOWN_EVENT = 6
_CLOSING_EVENTS = (CTRL_CLOSE_EVENT, CTRL_SHUTDOWN_EVENT)

_job_handle = None       # kept open for the life of the process, on purpose
_console_handler = None  # the ctypes callback; must stay referenced


def _is_windows() -> bool:
    return sys.platform == "win32"


def group_name_for(install_root) -> str:
    """The job's name for one install folder: per-user session namespace
    (Local\\), unique per install path, nothing personal in it."""
    digest = hashlib.sha256(os.path.normcase(os.path.realpath(str(install_root)))
                            .encode("utf-8")).hexdigest()[:16]
    return f"Local\\BaiheStudio-{digest}"


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    k.OpenJobObjectW.restype = wintypes.HANDLE
    k.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                          wintypes.DWORD)
    k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.SetConsoleCtrlHandler.argtypes = (wintypes.LPVOID, wintypes.BOOL)
    return k


def _handler_type():
    """The HANDLER_ROUTINE callback type: BOOL WINAPI (DWORD)."""
    import ctypes
    from ctypes import wintypes
    return ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)


def _extended_limit_info(flags):
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = flags
    return info


def create_kill_on_close_job(name=None, kernel32=None):
    """A new Job Object (named, or anonymous if `name` is empty) whose
    processes all end when its last handle closes. Returns the handle, or
    None if Windows refused (or not on Windows). The caller owns it."""
    if not _is_windows():
        return None
    try:
        import ctypes
        k = kernel32 or _kernel32()
        job = k.CreateJobObjectW(None, name or None)
        if not job:
            return None
        info = _extended_limit_info(_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        if not k.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                         ctypes.byref(info), ctypes.sizeof(info)):
            k.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def contain_children(name=None) -> bool:
    """Puts this process into a kill-on-close job (see the module
    docstring): named `name`, else BAIHE_PROCESS_GROUP_NAME, else
    anonymous. Returns True if it's in place; False (and changes nothing)
    outside Windows or if Windows refuses -- the app still runs, it just
    loses the guarantee, which is why this never raises."""
    global _job_handle
    if not _is_windows() or _job_handle is not None:
        return _job_handle is not None
    name = name or os.environ.get(GROUP_NAME_ENV, "")
    try:
        k = _kernel32()
        job = create_kill_on_close_job(name, kernel32=k)
        if not job:
            return False
        if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            k.CloseHandle(job)
            return False
        _job_handle = job
        return True
    except Exception:
        return False


def handle_console_event(event, on_close) -> bool:
    """The console control handler's logic: for a closing window or a
    system shutdown, run `on_close` (the clean shutdown) and answer True
    (handled; Windows then ends the process, and the job its children).
    Anything else is passed on (False), so Ctrl+C stays a normal stop."""
    if event not in _CLOSING_EVENTS:
        return False
    try:
        on_close()
    except Exception:
        pass
    return True


def install_console_close_handler(on_close) -> bool:
    """Registers handle_console_event(event, on_close) with
    SetConsoleCtrlHandler. True if it's registered."""
    global _console_handler
    if not _is_windows() or _console_handler is not None:
        return _console_handler is not None
    try:
        callback = _handler_type()(lambda event: bool(handle_console_event(event, on_close)))
        if not _kernel32().SetConsoleCtrlHandler(callback, True):
            return False
        _console_handler = callback
        return True
    except Exception:
        return False


def terminate_group(name) -> bool:
    """Ends every process still in the named job (this install's server and
    everything it started). True if the job existed and was ended; False
    if there is no such job (nothing running) or not on Windows."""
    if not _is_windows() or not name:
        return False
    try:
        k = _kernel32()
        job = k.OpenJobObjectW(_JOB_OBJECT_TERMINATE | _JOB_OBJECT_QUERY, False, name)
        if not job:
            return False
        try:
            return bool(k.TerminateJobObject(job, 1))
        finally:
            k.CloseHandle(job)
    except Exception:
        return False


def add_process_to_group(name, pid) -> bool:
    """Puts process `pid` into the named job. The smoke test uses it to
    stand in for a long-running ffmpeg the server started."""
    if not _is_windows() or not name:
        return False
    try:
        k = _kernel32()
        job = k.OpenJobObjectW(_JOB_OBJECT_ASSIGN_PROCESS | _JOB_OBJECT_QUERY, False, name)
        if not job:
            return False
        try:
            proc = k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
            if not proc:
                return False
            try:
                return bool(k.AssignProcessToJobObject(job, proc))
            finally:
                k.CloseHandle(proc)
        finally:
            k.CloseHandle(job)
    except Exception:
        return False
