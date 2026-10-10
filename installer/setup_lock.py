"""
installer/setup_lock.py -- the named mutexes that keep Setup and the owner's
service commands from running at the same time (docs/windows-installer-design.md,
"Boot service"). Split out of installer/service.py, which takes the lock around
set-port, enable-remote and disable-remote.

Standard library only: service.py runs elevated from the admin folder's
interpreter, which has no site-packages.
"""

import contextlib
import os


# Setup's SetupMutex names, exactly as in installer/baihe.iss. Setup refuses
# to start if it can open either one, and creates both (the plain name in its
# session's namespace, the Global\ one machine-wide) for as long as it runs.
SETUP_MUTEX_NAMES = ("BaiheStudioSetupMutex", "Global\\BaiheStudioSetupMutex")
SETUP_RUNNING_MESSAGE = ("Setup is running, or another set-port, enable-remote or disable-remote; "
                         "try again when it has finished. Nothing was changed.")
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
# Setup runs unelevated (PrivilegesRequired=lowest) and checks with
# OpenMutex(SYNCHRONIZE), so everyone gets SYNCHRONIZE; only SYSTEM and
# Administrators get full access.
SETUP_MUTEX_SDDL = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100000;;;WD)"


class SetupRunning(Exception):
    """One of Setup's mutex names already exists: Setup, or another locked
    command, is running."""


class WindowsMutexes:
    """Named mutexes through kernel32. A mutex exists while any process has
    a handle to it; nobody waits on or owns these, existing is the lock."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]

        self._ctypes = ctypes
        self._attributes_type = SecurityAttributes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
        self._kernel32.LocalFree.restype = ctypes.c_void_p
        convert = self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                            ctypes.c_void_p)
        convert.restype = wintypes.BOOL

    def create(self, name: str):
        """A handle to a new mutex called `name`; SetupRunning if it already
        exists (or exists and may not be opened from here)."""
        ctypes = self._ctypes
        descriptor = ctypes.c_void_p()
        if not self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                SETUP_MUTEX_SDDL, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = self._attributes_type(ctypes.sizeof(self._attributes_type),
                                               descriptor.value, False)
            handle = self._kernel32.CreateMutexW(ctypes.byref(attributes), False, name)
            error = ctypes.get_last_error()
        finally:
            self._kernel32.LocalFree(descriptor)
        if not handle:
            if error == ERROR_ACCESS_DENIED:
                raise SetupRunning(name)
            raise ctypes.WinError(error)
        if error == ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            raise SetupRunning(name)
        return handle

    def close(self, handle) -> None:
        self._kernel32.CloseHandle(handle)


class NoMutexes:
    """Elsewhere than Windows there is no Setup to keep out."""

    def create(self, name: str):
        return None

    def close(self, handle) -> None:
        pass


def default_mutexes():
    return WindowsMutexes() if os.name == "nt" else NoMutexes()


@contextlib.contextmanager
def setup_lock(mutexes):
    """Holds every one of Setup's mutex names until the block ends (also on
    an error or Ctrl+C); SetupRunning, holding none, if any already exists."""
    handles = []
    try:
        for name in SETUP_MUTEX_NAMES:
            handles.append(mutexes.create(name))
        yield
    finally:
        for handle in reversed(handles):
            mutexes.close(handle)
