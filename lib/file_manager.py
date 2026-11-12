"""
lib/file_manager.py -- opening a folder in this PC's file manager.

Callers pass a folder they resolved server side, never a path from a client:
the folder opens on the PC's own screen, so only PC-only routes reach this.
"""

import subprocess
import sys
import threading
import time

_REPEAT_WINDOW_S = 3.0
_lock = threading.Lock()
_last_opened: dict[str, float] = {}


class NoDesktopError(OSError):
    """The server has no signed-in desktop to open a window on."""


def _has_desktop() -> bool:
    """False for a Windows service (session 0): a window opened there is never
    shown, and the shell launch can stall Explorer on the real desktop."""
    if not sys.platform.startswith("win"):
        return True
    import ctypes
    import os
    session = ctypes.c_ulong()
    try:
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return True
    return not ok or session.value != 0


def open_folder(folder: str):
    """Raises NoDesktopError when the server runs without a desktop, and
    OSError when the PC has no file manager to open it with. A repeat for the
    same folder within a few seconds is ignored so a double click can't pile
    up Explorer windows."""
    if not _has_desktop():
        raise NoDesktopError("no desktop session")
    now = time.monotonic()
    with _lock:
        if now - _last_opened.get(folder, -_REPEAT_WINDOW_S) < _REPEAT_WINDOW_S:
            return
        _last_opened[folder] = now
    if sys.platform.startswith("win"):
        # Explorer directly, not ShellExecute: the "open" verb on a folder is
        # whatever shell extension or file-manager replacement claims it.
        argv = ["explorer.exe", folder]
    else:
        argv = ["open" if sys.platform == "darwin" else "xdg-open", folder]
    # No shell; the file manager outlives this call (Explorer exits non-zero even on success).
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=not sys.platform.startswith("win"))
    except OSError:
        # A failed launch must not make the retry look like a double click and
        # report success without opening anything.
        with _lock:
            _last_opened.pop(folder, None)
        raise
