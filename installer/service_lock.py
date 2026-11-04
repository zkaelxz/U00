"""Makes the per-user install folder admin-owned (users read and run, nothing more) so the
LocalSystem updater never writes into a folder an unelevated process could swap under it.

Standard library only: this runs elevated from the admin folder.
"""

import os
from pathlib import Path

from installer.service_control import (ADMINISTRATORS_SID, COMMAND_TIMEOUT, DATA_GRANT_TIMEOUT,
                                       SYSTEM_SID, _icacls, service_sid)
from installer.service_layout import APP_SERVICE, Layout, _norm

USERS_SID = "S-1-5-32-545"
OWNER_SIDS = frozenset({ADMINISTRATORS_SID, SYSTEM_SID})


def lock_commands(layout: Layout) -> list:
    """(command, timeout) pairs. The owner of every file must change too: an
    owner can always rewrite the permissions of what it owns. The reset before
    the final grant drops explicit entries on children, so everything below
    inherits exactly the root's entries."""
    root = layout.root
    app_sid = "*" + service_sid(APP_SERVICE)
    return [
        (_icacls(root, "/setowner", f"*{ADMINISTRATORS_SID}", "/T"), DATA_GRANT_TIMEOUT),
        (_icacls(root, "/reset", "/T"), DATA_GRANT_TIMEOUT),
        (_icacls(root, "/inheritance:r",
                 "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F",
                 "/grant:r", f"*{ADMINISTRATORS_SID}:(OI)(CI)F",
                 "/grant:r", f"*{USERS_SID}:(OI)(CI)RX",
                 "/grant:r", f"{app_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
    ]


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
