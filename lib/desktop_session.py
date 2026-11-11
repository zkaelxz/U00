"""lib/desktop_session.py -- whether this process runs where a person's desktop is, not in a Windows service's Session 0."""
import sys


def has_desktop() -> bool:
    """False in Session 0 (a boot service), which has no desktop: a toast has nowhere to show and an
    installer window opens hidden and waits forever. True off Windows and when the session can't be read."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        session = ctypes.c_ulong()
        kernel32 = ctypes.windll.kernel32
        if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
            return True
        return session.value != 0
    except Exception:
        # A skipped notice or a refused install on a wrong guess is worse than trying.
        return True
