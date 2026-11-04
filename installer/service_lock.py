"""Makes the per-user install folder admin-owned (users read and run, nothing more) so the
LocalSystem updater never writes into a folder an unelevated process could swap under it.

Standard library only: this runs elevated from the admin folder.
"""

import contextlib
import os
import re
from pathlib import Path

from installer.service_control import (ADMINISTRATORS_SID, DATA_GRANT_TIMEOUT,
                                       SYSTEM_SID, _icacls, service_sid)
from installer.service_layout import (APP_SERVICE, FILE_ATTRIBUTE_REPARSE_POINT, Layout,
                                      ServiceError, _norm)

USERS_SID = "S-1-5-32-545"
OWNER_SIDS = frozenset({ADMINISTRATORS_SID, SYSTEM_SID})


# The folder's whole DACL in one piece: no entry of the user's survives, the
# owner's implicit right to rewrite permissions is cut to reading (OW), and
# nothing is inherited from the parent. FRFX is read and run.
def root_sddl(app_sid: str) -> str:
    return (f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FRFX;;;BU)"
            f"(A;OICI;FRFX;;;{app_sid})(A;OICI;RC;;;OW)")


def set_root_security(root, app_sid: str) -> None:
    """Replaces the root's DACL atomically (protected, so inheritance is off).
    Not done with icacls, whose /grant:r replaces only the accounts it names
    and so leaves a user entry already on the folder in place. The owner is
    changed afterwards, by icacls, which enables the privilege that needs."""
    if os.name != "nt":
        raise ServiceError("Locking the install folder works only on Windows.")
    import ctypes
    advapi32, kernel32 = ctypes.windll.advapi32, ctypes.windll.kernel32
    descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    present, defaulted = ctypes.c_int(), ctypes.c_int()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            root_sddl(app_sid), 1, ctypes.byref(descriptor), None):
        raise ServiceError("Locking the install folder: the permissions text was refused.")
    try:
        if not advapi32.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present),
                                                  ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ServiceError("Locking the install folder: the permissions can't be read.")
        # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        code = advapi32.SetNamedSecurityInfoW(str(root), 1, 0x4 | 0x80000000, None, None, dacl, None)
        if code != 0:
            raise ServiceError(f"Locking the install folder: Windows refused the permissions "
                               f"(error {code}).")
    finally:
        kernel32.LocalFree(descriptor)


def lock_commands(layout: Layout) -> list:
    """(command, timeout) pairs run after set_root_security: the owner of every
    file changes (an owner can always rewrite permissions), then the
    children's own entries go. The reset names `root\\*`, never the root,
    which would turn inheritance back on."""
    root = layout.root
    return [
        (_icacls(root, "/setowner", f"*{ADMINISTRATORS_SID}", "/T"), DATA_GRANT_TIMEOUT),
        (_icacls(os.path.join(str(root), "*"), "/reset", "/T"), DATA_GRANT_TIMEOUT),
    ]


_RIGHT_BITS = {"FA": 0x1f01ff, "FR": 0x120089, "FW": 0x120116, "FX": 0x1200a0, "GA": 0x10000000,
               "GR": 0x80000000, "GW": 0x40000000, "GX": 0x20000000, "RC": 0x20000, "SD": 0x10000,
               "WD": 0x40000, "WO": 0x80000, "RP": 0x10, "WP": 0x20, "CC": 0x1, "DC": 0x2,
               "LC": 0x4, "SW": 0x8, "LO": 0x80, "DT": 0x40, "CR": 0x100}
_READ_RUN_BITS = 0x1200a9 | 0x80000000 | 0x20000000
_ALLOW_TYPES = {"A", "OA", "XA", "ZA"}
# A deny entry only takes rights away.
_DENY_TYPES = {"D", "OD", "XD"}


def security_sddl(path):
    """The owner and DACL of `path` as SDDL text, or None when unreadable."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        advapi32, kernel32 = ctypes.windll.advapi32, ctypes.windll.kernel32
        descriptor = ctypes.c_void_p()
        # SE_FILE_OBJECT; OWNER | DACL
        if advapi32.GetNamedSecurityInfoW(str(path), 1, 0x1 | 0x4, None, None, None, None,
                                          ctypes.byref(descriptor)) != 0:
            return None
        try:
            text, size = wintypes.LPWSTR(), wintypes.ULONG()
            if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor, 1, 0x1 | 0x4, ctypes.byref(text), ctypes.byref(size)):
                return None
            try:
                return text.value
            finally:
                kernel32.LocalFree(text)
        finally:
            kernel32.LocalFree(descriptor)
    except Exception:
        return None


def write_access_problem(sddl, trusted, protected=False):
    """None when no account outside `trusted` can do more than read and run
    (or, with `protected`, when the DACL also ignores its parent); else why.
    The owner is checked apart: this reads the DACL, because an ACE already on
    the folder (a user, Everyone, Authenticated Users) survives an icacls grant."""
    if not sddl:
        return "its permissions can't be read"
    dacl = sddl.partition("D:")[2].partition("S:")[0]
    # A NULL DACL (no entries at all) grants everyone full control.
    if "NO_ACCESS_CONTROL" in dacl or "(" not in dacl:
        return "it has no permission list, which lets everyone write"
    if dacl.count("(") != dacl.count(")"):
        return "its permission list can't be read"
    if protected and "P" not in dacl.partition("(")[0]:
        return "it still inherits permissions from its parent folder"
    for ace in re.findall(r"\(([^)]*)\)", dacl):
        fields = ace.split(";")
        if len(fields) < 6:
            return "an entry can't be read"
        if fields[0] in _DENY_TYPES or fields[5] in trusted:
            continue
        if fields[0] not in _ALLOW_TYPES:
            return "an entry has a type that can't be checked"
        rights = fields[2]
        if rights.lower().startswith("0x"):
            try:
                bits = int(rights, 16)
            except ValueError:
                return "an entry's rights can't be read"
        else:
            bits, pairs = 0, [rights[i:i + 2] for i in range(0, len(rights), 2)]
            if any(p not in _RIGHT_BITS for p in pairs):
                return "an entry's rights can't be read"
            for pair in pairs:
                bits |= _RIGHT_BITS[pair]
        if bits & ~_READ_RUN_BITS:
            return f"{fields[5]} can write, change permissions or delete"
    return None


def trusted_trustees(layout: Layout):
    return frozenset({"SY", "BA", SYSTEM_SID, ADMINISTRATORS_SID, service_sid(APP_SERVICE)})


def tree_problem(root, owner=None, sddl=None, trusted=frozenset({"SY", "BA"})):
    """None when everything under `root` (the root too) is owned by
    administrators or SYSTEM, grants no one else more than read and run, and
    contains no link or hard-linked file; else the first offender. A folder
    that can't be listed counts too. Run after the lock commands, so a file the
    user kept, or a junction planted while they ran, fails the install."""
    owner = owner_sid if owner is None else owner
    sddl = security_sddl if sddl is None else sddl
    errors = []
    for folder, dirs, files in os.walk(root, onerror=errors.append):
        for name in [""] + dirs + files:
            path = os.path.join(folder, name) if name else folder
            try:
                info = os.lstat(path)
            except OSError:
                return f"{path} can't be read."
            if getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                return f"{path} is a link, which the locked install folder can't contain."
            if name in files and info.st_nlink > 1:
                return f"{path} has more than one hard link."
            if owner(path) not in OWNER_SIDS:
                return f"{path} is still owned by a user after locking the install folder."
            problem = write_access_problem(sddl(path), trusted, protected=path == str(root))
            if problem:
                return f"{path}: {problem} after locking the install folder."
    if errors:
        return f"{errors[0].filename} can't be listed."
    return None


def ancestors(path):
    """`path` and every folder above it, outermost first."""
    chain = [Path(path)] + list(Path(path).parents)
    return list(reversed(chain))


def _open_held(path):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_WRITE (no delete, so it can't
    # be renamed, removed or re-pointed); OPEN_EXISTING;
    # FILE_FLAG_BACKUP_SEMANTICS (a directory) | FILE_FLAG_OPEN_REPARSE_POINT
    handle = kernel32.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ServiceError(f"{path} can't be held open, so the install folder can't be trusted "
                           "during an update.")
    return handle


def _close_held(handle):
    import ctypes
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


@contextlib.contextmanager
def hold_directory(path, is_windows=None, open_held=None, close_held=None):
    """Keeps `path` and every folder above it open without delete sharing
    while the updater works. The updater's later steps are by path, and the
    folders above the install folder (the default sits under the user's
    profile) belong to the user, who could otherwise swap one for a junction
    after the checks, or rename the locked folder away and plant a look-alike.
    Held outermost first, then checked for links: with all of them held the
    path can't resolve anywhere else. Does nothing off Windows."""
    if (os.name == "nt") if is_windows is None else is_windows:
        open_held, close_held = open_held or _open_held, close_held or _close_held
    else:
        yield
        return
    held = []
    try:
        for folder in ancestors(path):
            held.append(open_held(folder))
        for folder in ancestors(path):
            if getattr(os.lstat(folder), "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                raise ServiceError(f"{folder} is a link, so the install folder can't be trusted "
                                   "during an update.")
        yield
    finally:
        for handle in reversed(held):
            close_held(handle)


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


def locked_problem(layout: Layout, owner=owner_sid, sddl=None):
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
    read = security_sddl if sddl is None else sddl
    trusted = trusted_trustees(layout)
    for path in (layout.root, Path(layout.root) / "app", Path(layout.root) / "manifest.json"):
        if os.path.lexists(path):
            problem = write_access_problem(read(path), trusted, protected=path == layout.root)
            if problem:
                return (f"{path}: {problem}, so the install folder can't be trusted. "
                        "Run the full installer to lock it again.")
    return None
