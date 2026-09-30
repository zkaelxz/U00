"""
process_guard.py -- ties every process the installed app's server starts
(ffmpeg, yt-dlp, Playwright's Chromium, lncrawl, pip, ...) to the server's
own lifetime on Windows (Step 80b: "Stop Baihe" stops everything).

The installed launcher (installer/launcher.py) passes a per-install name
in BAIHE_PROCESS_GROUP_NAME. `python -m api` then puts itself into a
named Windows Job Object with "kill on job close": child processes join
the job automatically, and when the server process ends for any reason
(a clean stop, a forced one, or its window being closed) Windows ends every
process still in the job. `launcher.py --stop` can also end the whole job by
name as its last resort. Nothing here runs outside Windows or without that
variable, so a source checkout is unchanged.

Standard library only (ctypes): the launcher uses it too.
"""

import hashlib
import os

GROUP_NAME_ENV = "BAIHE_PROCESS_GROUP_NAME"

_JOB_OBJECT_ASSIGN_PROCESS = 0x0001
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

_job_handle = None   # kept open for the life of the process, on purpose


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
    return k


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


def contain_children(name=None) -> bool:
    """Puts this process into the named kill-on-close job (see the module
    docstring). `name` defaults to BAIHE_PROCESS_GROUP_NAME. Returns True
    if it's in place; False (and changes nothing) outside Windows, without
    a name, or if Windows refuses -- the app still runs, it just loses the
    guarantee, which is why this never raises."""
    global _job_handle
    name = name or os.environ.get(GROUP_NAME_ENV, "")
    if os.name != "nt" or not name or _job_handle is not None:
        return _job_handle is not None
    try:
        import ctypes
        k = _kernel32()
        job = k.CreateJobObjectW(None, name)
        if not job:
            return False
        info = _extended_limit_info(_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        if not (k.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                          ctypes.byref(info), ctypes.sizeof(info))
                and k.AssignProcessToJobObject(job, k.GetCurrentProcess())):
            k.CloseHandle(job)
            return False
        _job_handle = job
        return True
    except Exception:
        return False


def terminate_group(name) -> bool:
    """Ends every process still in the named job (this install's server and
    everything it started). True if the job existed and was ended; False
    if there is no such job (nothing running) or not on Windows."""
    if os.name != "nt" or not name:
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
    if os.name != "nt" or not name:
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
