"""Makes the per-user install folder admin-owned (users read and run, nothing more) so the
LocalSystem updater never writes into a folder an unelevated process could swap under it.

Standard library only: this runs elevated from the admin folder.
"""

import contextlib
import os
from pathlib import Path

from installer.service_control import (ADMINISTRATORS_SID, COMMAND_TIMEOUT, DATA_GRANT_TIMEOUT,
                                       SYSTEM_SID, _icacls, service_sid)
from installer.service_layout import (APP_SERVICE, FILE_ATTRIBUTE_REPARSE_POINT, Layout,
                                      ServiceError, _norm)

USERS_SID = "S-1-5-32-545"
OWNER_SIDS = frozenset({ADMINISTRATORS_SID, SYSTEM_SID})


def lock_commands(layout: Layout) -> list:
    """(command, timeout) pairs, in an order that never leaves a window: the
    root stops inheriting and loses the user's write first, which also strips
    it from everything that inherits (the change propagates before icacls
    returns), so nothing the user does afterwards creates entries it owns.
    Only then are owners changed (an owner can always rewrite permissions) and
    explicit entries on children dropped. The reset names `root\\*`, never the
    root itself, which would turn inheritance back on."""
    root = layout.root
    app_sid = "*" + service_sid(APP_SERVICE)
    return [
        (_icacls(root, "/inheritance:r",
                 "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F",
                 "/grant:r", f"*{ADMINISTRATORS_SID}:(OI)(CI)F",
                 "/grant:r", f"*{USERS_SID}:(OI)(CI)RX",
                 "/grant:r", f"{app_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
        (_icacls(root, "/setowner", f"*{ADMINISTRATORS_SID}", "/T"), DATA_GRANT_TIMEOUT),
        (_icacls(os.path.join(str(root), "*"), "/reset", "/T"), DATA_GRANT_TIMEOUT),
    ]


def tree_problem(root, owner=None):
    """None when everything under `root` (the root too) is owned by
    administrators or SYSTEM and nothing is a link; else the first offender.
    Run after the lock commands, so a file the user kept ownership of, or a
    junction planted while they ran, fails the install instead of staying
    as a way back in."""
    owner = owner_sid if owner is None else owner
    for folder, dirs, files in os.walk(root):
        for name in [""] + dirs + files:
            path = os.path.join(folder, name) if name else folder
            try:
                attributes = os.lstat(path).st_file_attributes
            except (OSError, AttributeError):
                attributes = 0
            if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                return f"{path} is a link, which the locked install folder can't contain."
            if owner(path) not in OWNER_SIDS:
                return f"{path} is still owned by a user after locking the install folder."
    return None


@contextlib.contextmanager
def hold_directory(path):
    """Keeps `path` open without FILE_SHARE_DELETE while the updater works, so
    a user who owns the parent folder (the default install folder is under
    their profile) can't rename the locked folder away and plant a look-alike
    full of links between the updater's checks and its writes. Raises
    ServiceError when it can't be opened; does nothing off Windows."""
    if os.name != "nt":
        yield
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_WRITE; OPEN_EXISTING;
    # FILE_FLAG_BACKUP_SEMANTICS (a directory) | FILE_FLAG_OPEN_REPARSE_POINT
    handle = kernel32.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ServiceError(f"The install folder {path} can't be held open, so it can't be "
                           "trusted during an update.")
    try:
        yield
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def unlock_commands(layout: Layout) -> list:
    """Back to the permissions the folder's parent gives, which is what Setup
    (running as the user) needs to replace files."""
    return [(_icacls(layout.root, "/reset", "/T"), DATA_GRANT_TIMEOUT)]


def marker_text(layout: Layout) -> str:
    return _norm(layout.root)


def write_marker(layout: Layout) -> None:
    layout.lock_marker.write_text(marker_text(layout), encoding="utf-8")


def remove_marker(layout: Layout) -> None:
    try:
        layout.lock_marker.unlink()
    except FileNotFoundError:
        pass


def owner_sid(path):
    """The owner of `path` as a SID string, or None when it can't be read
    (anything but Windows included), so a check on it fails closed."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        advapi32, kernel32 = ctypes.windll.advapi32, ctypes.windll.kernel32
        owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
        # SE_FILE_OBJECT = 1, OWNER_SECURITY_INFORMATION = 1
        if advapi32.GetNamedSecurityInfoW(str(path), 1, 1, ctypes.byref(owner), None, None, None,
                                          ctypes.byref(descriptor)) != 0:
            return None
        try:
            text = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(owner, ctypes.byref(text)):
                return None
            try:
                return text.value
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.LocalFree(descriptor)
    except Exception:
        return None


def locked_problem(layout: Layout, owner=owner_sid):
    """None when Setup's lock is in place, else why it is not. The marker
    says the lock finished (it is removed before Setup unlocks), the owner
    check says nobody has taken the folder back since."""
    try:
        marked = layout.lock_marker.read_text(encoding="utf-8") == marker_text(layout)
    except (OSError, UnicodeDecodeError):
        marked = False
    if not marked:
        return ("The install folder isn't locked to administrators yet. Run the full installer "
                "once to lock it.")
    for path in (layout.root, Path(layout.root) / "app", Path(layout.root) / "manifest.json"):
        if os.path.lexists(path) and owner(path) not in OWNER_SIDS:
            return (f"{path} is not owned by administrators, so it can't be trusted. "
                    "Run the full installer to lock the install folder again.")
    return None
