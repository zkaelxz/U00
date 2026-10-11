"""Where Baihe Studio's service files live and which folders the service may be
given rights on (installer/service.py, docs/windows-installer-design.md,
"Boot service"): the install, data and admin-only folders, the checks that
refuse a risky folder, and removal of the admin folder.

Standard library only: this runs elevated from the admin folder.
"""

import os
import re
import secrets
import shutil
import stat
from pathlib import Path

APP_SERVICE = "BaiheStudio"
CADDY_SERVICE = "BaiheCaddy"
APP_DISPLAY_NAME = "Baihe Studio"
CADDY_DISPLAY_NAME = "Baihe Studio remote access (Caddy)"
ADMIN_FOLDER_NAME = "Baihe Studio Services"


STATE_FILE_NAME = "remote-access.json"
LOG_FILE_NAME = "service.log"
CONFIG_FILE_NAME = "config.json"


FILE_ATTRIBUTE_REPARSE_POINT = 0x400


# What may be in the data folder before the service is given write access
# to all of it: only what Baihe Studio itself keeps there.
DATA_FOLDER_ENTRIES = {"library", ".env", "model_cache", "launcher", "desktop.ini", "thumbs.db",
                       "baihe_trash"}


class ServiceError(Exception):
    """A plain-words reason a step failed (exit code 1)."""


class ConfigRefused(ServiceError):
    """Remote access isn't set up (or can't be) and nothing was changed
    (exit code 2)."""


def system_dir() -> str:
    """Windows' System32 folder, from Windows itself rather than from an
    environment variable the caller could set."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf)):
                return buf.value
        except Exception:
            pass
    return r"C:\Windows\System32"


def _system32(exe: str) -> str:
    return os.path.join(system_dir(), exe)


def _known_folder(folder_id: str, fallback_env: str, fallback: str) -> Path:
    """A Windows known folder, from Windows itself rather than from an
    environment variable the caller could set (the environment is used only
    where there is no Windows, i.e. the tests)."""
    if os.name == "nt":
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
            u = uuid.UUID(folder_id)
            guid = GUID(u.time_low, u.time_mid, u.time_hi_version,
                        (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                                          ctypes.byref(out)) == 0:
                try:
                    return Path(out.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)
        except Exception:
            pass
        raise ServiceError("Windows didn't say where its program folder is.")
    return Path(os.environ.get(fallback_env) or fallback)


FOLDERID_PROGRAM_FILES = "905E63B6-C1BF-494E-B29C-65B732D3D21A"
FOLDERID_PROGRAM_DATA = "62AB5D82-FDC1-4DC3-A9DD-070D1D495D97"
FOLDERID_PROGRAM_FILES_X86 = "7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E"


def program_files() -> Path:
    return _known_folder(FOLDERID_PROGRAM_FILES, "ProgramW6432", r"C:\Program Files")


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path))).rstrip("\\/")


def _inside_or_same(path, folder) -> bool:
    p, f = _norm(path), _norm(folder)
    return p == f or p.startswith(f + os.sep) or p.startswith(f + "/")


def _is_absolute_windows_path(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\")) or (
        os.name != "nt" and text.startswith("/"))


def folder_problem(path, what: str, forbidden=()) -> str:
    """'' if `path` is usable as a folder the service is given rights on,
    else why not. The rules Setup applies to the data folder
    (postinstall.validate_data_dir), plus: never a system folder, and no
    character WinSW would expand (%) or that could end a quoted argument."""
    text = str(path or "").strip()
    if not text or not _is_absolute_windows_path(text):
        return f"The {what} must be a full folder path."
    if any(c in text for c in '%"\r\n\t'):
        return f"The {what} has a character a service setting can't hold (% or a quote)."
    p = Path(text)
    if p.parent == p or re.fullmatch(r"[A-Za-z]:[\\/]?", text):
        return f"The {what} can't be a whole drive."
    for folder in forbidden:
        if folder and _inside_or_same(p, folder):
            return f"The {what} can't be inside {folder}."
    return ""


def system_folders() -> list:
    return [str(Path(system_dir()).parent), str(program_files()),
            str(_known_folder(FOLDERID_PROGRAM_FILES_X86, "ProgramFiles(x86)",
                              r"C:\Program Files (x86)")),
            str(_known_folder(FOLDERID_PROGRAM_DATA, "ProgramData", r"C:\ProgramData"))]


def data_folder_contents_problem(data) -> str:
    """'' if the data folder holds only Baihe Studio's own items (or is
    empty): the service gets write access to everything in it, so it must
    be a folder only Baihe Studio uses, never one shared with other files
    (Documents, a whole profile)."""
    try:
        names = [p.name for p in Path(data).iterdir()]
    except FileNotFoundError:
        return ""
    except OSError as e:
        return f"The data folder can't be read ({e.strerror})."
    others = sorted(n for n in names if n.lower() not in DATA_FOLDER_ENTRIES)
    if others:
        shown = ", ".join(others[:5]) + (", ..." if len(others) > 5 else "")
        return ("The data folder has files Baihe Studio didn't put there (" + shown + "). "
                "The background service may change everything in it, so it needs a folder "
                "of its own: run Setup again and choose one.")
    return ""


def is_reparse_point(path) -> bool:
    """True for a junction, symbolic link or other reparse point. Windows
    reports the attribute; elsewhere (the tests) a symbolic link stands in
    for it."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(st.st_mode)


def refuse_reparse_point(path) -> None:
    """Refuses a folder that is a junction or link. Elevated icacls calls
    also pass /L, so they act on a link itself, never on its target."""
    if is_reparse_point(path):
        raise ServiceError(f"{path} is a link to another folder (a junction or symbolic "
                           "link); refusing to change permissions through it.")


class Layout:
    """Where everything is: the per-user install, the data folder and the
    admin-only folder."""

    def __init__(self, install_root, data_dir, admin_root):
        root, data, admin = Path(install_root), Path(data_dir), Path(admin_root)
        self.root = root
        self.app = root / "app"
        self.python_exe = root / "python" / "python.exe"
        self.data = data
        self.env_file = data / ".env"
        self.app_logs = data / "library" / "logs" / "service"
        self.admin = admin
        self.helper = admin / "helper"
        self.helper_python = self.helper / "python" / "python.exe"
        self.helper_script = self.helper / "lib" / "installer" / "service.py"
        self.config_file = self.helper / CONFIG_FILE_NAME
        self.template = self.helper / "lib" / "deploy" / "caddy" / "Caddyfile.template"
        self.service_dir = admin / "service"
        self.caddy_dir = admin / "caddy"
        self.caddy_exe = self.caddy_dir / "caddy.exe"
        self.caddyfile = self.caddy_dir / "Caddyfile"
        self.caddy_storage = admin / "caddy-data"
        self.caddy_logs = admin / "caddy-logs"
        self.state_file = admin / STATE_FILE_NAME
        self.log_file = admin / LOG_FILE_NAME

    def wrapper(self, name: str) -> Path:
        return (self.service_dir if name == APP_SERVICE else self.caddy_dir) / f"{name}.exe"

    def wrapper_xml(self, name: str) -> Path:
        return self.wrapper(name).with_suffix(".xml")

    def check(self) -> None:
        problem = (folder_problem(self.root, "install folder", [self.admin])
                   or folder_problem(self.data, "data folder",
                                     [self.root, self.admin, *system_folders()]))
        if problem:
            raise ServiceError(problem)


def _delete_on_reboot(path) -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.MoveFileExW(str(path), None, 0x4)   # MOVEFILE_DELAY_UNTIL_REBOOT
        except Exception:
            pass


def remove_admin_folder(folder: Path) -> None:
    """Removes the admin folder, including the interpreter this script is
    running from. Windows won't delete a file that is loaded, but it does
    let one be renamed, so a file that can't be deleted is moved into a new
    folder next to the admin folder in Program Files (only administrators
    can create or change anything there), which is deleted with the rest, or
    at the next restart for what is still loaded. Only the exact paths
    parked here are ever scheduled for deletion."""
    park = folder.parent / f".baihe-removing-{secrets.token_hex(8)}"
    parked = []
    park_made = False
    # Bottom-up, so files go before the folders that hold them.
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not is_reparse_point(path):
            continue
        try:
            path.unlink()
        except OSError:
            try:
                if not park_made:
                    park_made = True
                    os.mkdir(park)            # must be new; never an existing name
                    refuse_reparse_point(park)
                target = park / f"{len(parked)}-{path.name}"
                path.rename(target)
                parked.append(target)
            except (OSError, ServiceError):
                _delete_on_reboot(path)
    shutil.rmtree(folder, ignore_errors=True)
    if parked:
        shutil.rmtree(park, ignore_errors=True)
        for target in parked:
            if target.exists():
                _delete_on_reboot(target)
        if park.exists():
            _delete_on_reboot(park)
