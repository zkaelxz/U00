"""
installer/service.py -- runs the installed Baihe Studio as a Windows service
(docs/windows-installer-design.md, "Boot service").

One service, BaiheStudio: `python -s -m api` on 127.0.0.1:8600 and nothing
else, started at boot, restarted after a failure, stopped with Ctrl+C so the
server runs its clean stop. A copy of the pinned WinSW binary, named after
the service, answers the Service Control Manager and reads the .xml next to
it. The service runs as its own virtual account (NT SERVICE\\BaiheStudio),
never LocalSystem, with the privilege list cut down to what it needs.

Everything this runs with administrator rights lives in an admin-only
folder, %ProgramFiles%\\Baihe Studio Services: this script with a copy of the
bundled interpreter that has no site-packages and no `import site`
(helper\\), the WinSW wrapper and its .xml (service\\), and the log. The
per-user program folder can be changed by any process running as the user,
and the data folder by the service, so nothing there is run elevated.
Setup seeds the admin folder from its own fresh extraction (Setup's
{app}\\service, which has the same helper\\ layout); every later command
runs from the admin copy:

    "%ProgramFiles%\\Baihe Studio Services\\helper\\python\\python.exe" -I -S
        "%ProgramFiles%\\Baihe Studio Services\\helper\\lib\\installer\\service.py" <command>

    --install-root DIR --data-dir DIR install
                          (Setup) create or refresh the service and start it;
                          undone if it fails
    stop                  stop it (Setup, before an update)
    uninstall             stop and remove it, take its permissions off the
                          per-user folders, remove the admin folder
    status

Everything but `status` needs an administrator prompt. This opens no other
port, adds no firewall rule and does nothing on the router.

Standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

APP_DIR = Path(__file__).resolve().parent.parent

APP_SERVICE = "BaiheStudio"
APP_DISPLAY_NAME = "Baihe Studio"
ADMIN_FOLDER_NAME = "Baihe Studio Services"
ADMIN_PORT = 8600
LOG_FILE_NAME = "service.log"
CONFIG_FILE_NAME = "config.json"
# What the service keeps of the privileges Windows gives a service account:
# passing through folders it can't list (the data folder is usually inside
# a user profile), creating global objects and adjusting its working set.
# Everything else goes, SeImpersonatePrivilege above all: code running with
# it can turn itself into LocalSystem.
SERVICE_PRIVILEGES = ("SeChangeNotifyPrivilege", "SeCreateGlobalPrivilege",
                      "SeIncreaseWorkingSetPrivilege")
# Restart after 10 s, 30 s, then every 60 s; the count resets after a
# day without a failure.
FAILURE_RESET_SECONDS = 86400
FAILURE_ACTIONS = "restart/10000/restart/30000/restart/60000"
LOG_ROLL_KB = 10240
LOG_KEEP_FILES = 5
APP_STOP_TIMEOUT = "15 sec"    # the server gives jobs 6 s, uvicorn up to 3 s more
STATE_WAIT_SECONDS = 60
HEALTH_WAIT_SECONDS = 180      # a first start compiles every .pyc
COMMAND_TIMEOUT = 120
# The inheritable grant on the data folder is applied to everything already
# in it (a library with media and a model cache can be large).
DATA_GRANT_TIMEOUT = 3600
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
# What may be in the data folder before the service is given write access
# to all of it: only what Baihe Studio itself keeps there.
DATA_FOLDER_ENTRIES = {"library", ".env", "model_cache", "launcher", "desktop.ini", "thumbs.db"}
# The server's own settings: every one is written into the service's
# environment, so a machine-wide variable (say BAIHE_API_HOUSEHOLD_PORT)
# can never change what the service runs or add a listener. "" means the
# app's default.
API_ENV_NAMES = ("BAIHE_API_HOST", "BAIHE_API_PORT", "BAIHE_API_ENV", "BAIHE_API_CORS_ORIGINS",
                 "BAIHE_API_ALLOW_KEY_WRITES", "BAIHE_API_SERVE_FRONTEND", "BAIHE_API_AUTH",
                 "BAIHE_API_COOKIE_SECURE", "BAIHE_API_BACKGROUND", "BAIHE_API_HOUSEHOLD_PORT",
                 "BAIHE_API_SESSION_IDLE_DAYS", "BAIHE_API_SESSION_MAX_DAYS",
                 "BAIHE_GOOGLE_CLIENT_ID", "BAIHE_GOOGLE_CLIENT_SECRET", "BAIHE_PUBLIC_URL",
                 "BAIHE_SHUTDOWN_TOKEN", "BAIHE_PROCESS_GROUP_NAME", "BAIHE_PORTABLE")

_STATES = ("STOPPED", "START_PENDING", "STOP_PENDING", "RUNNING", "CONTINUE_PENDING",
           "PAUSE_PENDING", "PAUSED")
_START_TYPES = ("BOOT_START", "SYSTEM_START", "AUTO_START", "DEMAND_START", "DISABLED")
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_NOT_ACTIVE = 1062

EXIT_FAILED = 1
EXIT_NOT_ADMIN = 3


class ServiceError(Exception):
    """A plain-words reason a step failed (exit code 1)."""


def service_sid(name: str) -> str:
    """The SID Windows derives for a service's virtual account
    (NT SERVICE\\<name>): S-1-5-80- and the SHA-1 of the upper-cased name
    in UTF-16LE, as five little-endian numbers. Used for permissions, so
    they work whatever the language of Windows and before the service
    exists."""
    digest = hashlib.sha1(name.upper().encode("utf-16-le")).digest()
    return "S-1-5-80-" + "-".join(str(n) for n in struct.unpack("<5I", digest))


def service_account(name: str) -> str:
    return f"NT SERVICE\\{name}"


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


SC = _system32("sc.exe")
ICACLS = _system32("icacls.exe")
CMD = _system32("cmd.exe")


def program_files() -> Path:
    return Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles")
                or r"C:\Program Files")


# ---------------------------------------------------------------- folders

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
    env = os.environ
    return [str(Path(system_dir()).parent), str(program_files()),
            env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            env.get("ProgramData", r"C:\ProgramData")]


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
        self.service_dir = admin / "service"
        self.wrapper = self.service_dir / f"{APP_SERVICE}.exe"
        self.wrapper_xml = self.wrapper.with_suffix(".xml")
        self.log_file = admin / LOG_FILE_NAME

    def check(self) -> None:
        problem = (folder_problem(self.root, "install folder", [self.admin])
                   or folder_problem(self.data, "data folder",
                                     [self.root, self.admin, *system_folders()]))
        if problem:
            raise ServiceError(problem)


def read_config(config_file) -> dict:
    try:
        data = json.loads(Path(config_file).read_text(encoding="utf-8"))
        return {"install_root": str(data["install_root"]), "data_dir": str(data["data_dir"])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def read_env_file(path) -> dict:
    """The data folder's .env, parsed like settings_service._read_env_file
    (utf-8-sig, comments and blank lines skipped, surrounding quotes
    stripped); {} if it's missing or unreadable. The service account can
    change this file, so only a value compared to a fixed string is used."""
    env = {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError):
        return {}
    return env


# ---------------------------------------------------------------- WinSW XML

def app_service_env(data_dir, allow_key_writes: bool = False) -> dict:
    """The server's environment. Every setting the server reads is written
    out ("" = the app's default), so nothing machine-wide leaks in:
    loopback only, port 8600, no household listener, the data folder the
    service was given rights on, no user site-packages. The engine-key form
    is off unless the data folder's .env turns it on: other accounts on this
    PC can reach 127.0.0.1:8600 while the service runs."""
    env = {name: "" for name in API_ENV_NAMES}
    env.update({"BAIHE_API_HOST": "127.0.0.1", "BAIHE_API_PORT": str(ADMIN_PORT),
                "BAIHE_API_AUTH": "off", "BAIHE_API_ENV": "production",
                "BAIHE_API_ALLOW_KEY_WRITES": "1" if allow_key_writes else "0",
                "BAIHE_DATA_DIR": str(data_dir),
                "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"})
    return env


def app_service_xml(layout: Layout, allow_key_writes: bool = False) -> str:
    root = ET.Element("service")
    for tag, text in [
        ("id", APP_SERVICE),
        ("name", APP_DISPLAY_NAME),
        ("description", "Baihe Studio's server for this PC (http://127.0.0.1:8600)."),
        ("executable", str(layout.python_exe)),
        ("arguments", "-s -m api"),
        ("workingdirectory", str(layout.app)),
        ("startmode", "Automatic"),
        # Ctrl+C goes to python first, so the server's clean stop runs;
        # its Job Object then ends ffmpeg, the browser and the rest.
        ("stopparentprocessfirst", "true"),
        ("stoptimeout", APP_STOP_TIMEOUT),
    ]:
        ET.SubElement(root, tag).text = text
    for name, value in app_service_env(layout.data, allow_key_writes).items():
        ET.SubElement(root, "env", {"name": name, "value": value})
    ET.SubElement(root, "logpath").text = str(layout.app_logs)
    log = ET.SubElement(root, "log", {"mode": "roll-by-size"})
    ET.SubElement(log, "sizeThreshold").text = str(LOG_ROLL_KB)
    ET.SubElement(log, "keepFiles").text = str(LOG_KEEP_FILES)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


# ------------------------------------------------------ running commands

class Runner:
    """Runs a command with a time limit and logs it (commands and exit
    codes only: nothing here ever carries a secret). The log is in the
    admin folder, never in a folder the service account can change."""

    def __init__(self, log_path=None):
        self.log_path = log_path

    def log(self, text: str) -> None:
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def __call__(self, cmd, timeout: float = COMMAND_TIMEOUT):
        self.log("$ " + subprocess.list2cmdline([str(c) for c in cmd]))
        result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                                errors="replace", timeout=timeout)
        tail = (result.stdout + result.stderr).strip()[-600:]
        self.log(f"  exit {result.returncode}" + (f": {tail}" if tail else ""))
        return result


def _check(result, what: str):
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip()[-300:]
        raise ServiceError(f"{what} failed (exit code {result.returncode}). {tail}".strip())
    return result


def query_state(name: str, run):
    """The service's state (RUNNING, STOPPED...), or None if it isn't
    installed."""
    result = run([SC, "query", name])
    if result.returncode == ERROR_SERVICE_DOES_NOT_EXIST:
        return None
    _check(result, f"Reading the {name} service's state")
    m = re.search(r":\s*\d+\s+(" + "|".join(_STATES) + r")\b", result.stdout)
    return m.group(1) if m else "UNKNOWN"


def query_start_type(name: str, run):
    result = run([SC, "qc", name])
    if result.returncode == ERROR_SERVICE_DOES_NOT_EXIST:
        return None
    _check(result, f"Reading the {name} service's settings")
    m = re.search(r":\s*\d+\s+(" + "|".join(_START_TYPES) + r")\b", result.stdout)
    return m.group(1) if m else "UNKNOWN"


def wait_for_state(name: str, want: str, run, timeout: float = STATE_WAIT_SECONDS,
                   sleep=time.sleep) -> bool:
    for _ in range(int(timeout)):
        if query_state(name, run) == want:
            return True
        sleep(1)
    return query_state(name, run) == want


def stop_service(name: str, run, sleep=time.sleep) -> None:
    state = query_state(name, run)
    if state in (None, "STOPPED"):
        return
    result = run([SC, "stop", name])
    if result.returncode not in (0, ERROR_SERVICE_NOT_ACTIVE):
        _check(result, f"Stopping the {name} service")
    if not wait_for_state(name, "STOPPED", run, sleep=sleep):
        raise ServiceError(f"The {name} service didn't stop within {STATE_WAIT_SECONDS} s.")


def start_service(name: str, run, sleep=time.sleep) -> None:
    result = run([SC, "start", name])
    if result.returncode not in (0, ERROR_SERVICE_ALREADY_RUNNING):
        _check(result, f"Starting the {name} service")
    if not wait_for_state(name, "RUNNING", run, sleep=sleep):
        raise ServiceError(f"The {name} service didn't start. Its log is in "
                           "library\\logs\\service in the data folder.")


def delete_service(name: str, run, sleep=time.sleep) -> None:
    if query_state(name, run) is None:
        return
    _check(run([SC, "delete", name]), f"Removing the {name} service")
    # Windows removes it once the last handle to it closes.
    for _ in range(STATE_WAIT_SECONDS):
        if query_state(name, run) is None:
            return
        sleep(1)
    raise ServiceError(f"The {name} service is marked for removal but still there; "
                       "restart Windows to finish removing it.")


def _quoted(path) -> str:
    # The quotes are part of the value: an unquoted path with spaces in a
    # service's command line is run as C:\Program.exe first.
    return f'"{path}"'


def create_service_command(wrapper: Path) -> list:
    # sc.exe, not the wrapper's own `install`, and disabled until fully
    # configured: a failed install never leaves a service that starts at
    # boot as LocalSystem.
    return [SC, "create", APP_SERVICE, "binPath=", _quoted(wrapper), "start=", "disabled",
            "DisplayName=", APP_DISPLAY_NAME]


def configure_service_commands(wrapper: Path) -> list:
    """sc.exe settings applied on every install, before the start type:
    the wrapper in the admin folder, its own virtual account, a cut-down
    privilege list, and restart after a failure (also when it exits with an
    error)."""
    return [
        [SC, "config", APP_SERVICE, "binPath=", _quoted(wrapper)],
        [SC, "sidtype", APP_SERVICE, "unrestricted"],
        [SC, "config", APP_SERVICE, "obj=", service_account(APP_SERVICE)],
        [SC, "privs", APP_SERVICE, "/".join(SERVICE_PRIVILEGES)],
        [SC, "failure", APP_SERVICE, "reset=", str(FAILURE_RESET_SECONDS),
         "actions=", FAILURE_ACTIONS],
        [SC, "failureflag", APP_SERVICE, "1"],
    ]


def _icacls(path, *args) -> list:
    # /L: act on a link itself, never on what it points to.
    return [ICACLS, path, *args, "/L"]


def grant_commands(layout: Layout) -> list:
    """(command, timeout) pairs: icacls grants, by SID. BaiheStudio may
    read and run the per-user install folder and its wrapper, and change
    the data folder. It gets no write access to anything this script, its
    wrapper or the interpreter that runs it is loaded from."""
    sid = "*" + service_sid(APP_SERVICE)
    return [
        (_icacls(layout.root, "/grant", f"{sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
        (_icacls(layout.data, "/grant", f"{sid}:(OI)(CI)M"), DATA_GRANT_TIMEOUT),
        (_icacls(layout.service_dir, "/grant", f"{sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
    ]


def revoke_commands(layout: Layout) -> list:
    """Takes BaiheStudio back out of the two per-user folders (the admin
    folder is deleted)."""
    sid = "*" + service_sid(APP_SERVICE)
    return [(_icacls(layout.root, "/remove:g", sid), COMMAND_TIMEOUT),
            (_icacls(layout.data, "/remove:g", sid), DATA_GRANT_TIMEOUT)]


# ------------------------------------------------------------- checks

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(port: int = ADMIN_PORT) -> bool:
    try:
        with _LOOPBACK_OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_until(check, seconds: float, sleep=time.sleep) -> bool:
    for _ in range(int(seconds)):
        if check():
            return True
        sleep(1)
    return check()


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def remove_folder_later(folder: Path) -> None:
    """Deletes what's left of the admin folder (this script's own
    interpreter) once this process has exited: a detached cmd.exe from
    System32 waits a few seconds, then removes it. Only administrators can
    change that folder, so nothing in it can have been swapped for a link."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([CMD, "/d", "/c", f'ping -n 5 127.0.0.1 >nul & rmdir /s /q "{folder}"'],
                     creationflags=flags, close_fds=True, cwd=system_dir())


# ---------------------------------------------------------- operations

class Services:
    """The commands, against a layout, a command runner and checks that
    tests replace."""

    def __init__(self, layout: Layout, run, health=health_ok, sleep=time.sleep, source=None,
                 running_from_admin=False, remove_later=remove_folder_later):
        self.layout, self.run, self.health, self.sleep = layout, run, health, sleep
        self.source = Path(source) if source else None
        self.running_from_admin = running_from_admin
        self.remove_later = remove_later

    def _swap_in_admin_files(self) -> list:
        """Replaces helper\\ and service\\ in the admin folder with Setup's
        fresh extraction (`source`), keeping what was there as *.old, and
        records the two per-user folders. Returns the (new, old) pairs for
        _restore or _discard. Refuses if another install on this PC owns
        the service."""
        lay = self.layout
        if self.source is None:
            raise ServiceError("`install` is run by Setup (it needs Setup's files).")
        refuse_reparse_point(lay.admin)
        old_config = read_config(lay.config_file)
        if (old_config and _norm(old_config["install_root"]) != _norm(lay.root)
                and (Path(old_config["install_root"]) / "app" / "INSTALLED").is_file()):
            raise ServiceError(
                f"Another Baihe Studio install on this PC ({old_config['install_root']}) runs "
                "the background service. Uninstall it, or untick the service in this Setup.")
        sources = {"helper": self.source / "helper", "service": self.source / "wrapper"}
        for src in sources.values():
            if not src.is_dir():
                raise ServiceError(f"Setup's files are incomplete: {src} is missing.")
        lay.admin.mkdir(parents=True, exist_ok=True)
        swapped = []
        try:
            for sub, src in sources.items():
                dest, old = lay.admin / sub, lay.admin / f"{sub}.old"
                shutil.rmtree(old, ignore_errors=True)
                if dest.exists():
                    dest.rename(old)
                swapped.append((dest, old))
                shutil.copytree(src, dest, symlinks=True)
            lay.config_file.write_text(json.dumps({"install_root": str(lay.root),
                                                   "data_dir": str(lay.data)}, indent=2) + "\n",
                                       encoding="utf-8")
        except Exception:
            self._restore(swapped)
            raise
        return swapped

    @staticmethod
    def _restore(swapped) -> None:
        for dest, old in swapped:
            shutil.rmtree(dest, ignore_errors=True)
            if old.exists():
                old.rename(dest)

    @staticmethod
    def _discard(swapped) -> None:
        for _, old in swapped:
            shutil.rmtree(old, ignore_errors=True)

    def _create_service(self) -> bool:
        """Creates the service disabled if it doesn't exist, configures it,
        and only then sets its start type. Returns True if this call
        created it."""
        created = query_state(APP_SERVICE, self.run) is None
        if created:
            _check(self.run(create_service_command(self.layout.wrapper)),
                   f"Creating the {APP_SERVICE} service")
        try:
            for cmd in configure_service_commands(self.layout.wrapper):
                _check(self.run(cmd), f"Configuring the {APP_SERVICE} service")
            _check(self.run([SC, "config", APP_SERVICE, "start=", "auto"]),
                   f"Setting the {APP_SERVICE} service's start")
        except ServiceError:
            if created:
                self.run([SC, "delete", APP_SERVICE])
            raise
        return created

    def _grant(self) -> None:
        lay = self.layout
        for folder in (lay.root, lay.data):
            refuse_reparse_point(folder)
        self.run.log("Granting folder permissions (the data folder can take a while).")
        for cmd, timeout in grant_commands(lay):
            _check(self.run(cmd, timeout=timeout), "Setting folder permissions")

    def _start_and_wait(self) -> None:
        start_service(APP_SERVICE, self.run, sleep=self.sleep)
        if not wait_until(self.health, HEALTH_WAIT_SECONDS, sleep=self.sleep):
            raise ServiceError("Baihe Studio's service started, but http://127.0.0.1:8600/api/health "
                               "doesn't answer. Its log is in library\\logs\\service in the data folder.")

    def install(self) -> str:
        """Seeds the admin folder, creates the service or refreshes it after
        an update, and starts it. If anything fails, the admin files that
        were there before are put back, a service this call created is
        removed, and one that existed is started again (Setup stopped it)."""
        lay = self.layout
        lay.check()
        problem = data_folder_contents_problem(lay.data)
        if problem:
            raise ServiceError(problem)
        if not lay.python_exe.is_file():
            raise ServiceError(f"Missing (run Setup again to repair the install): {lay.python_exe}")
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        swapped = self._swap_in_admin_files()
        created = False
        try:
            missing = [str(p) for p in (lay.wrapper, lay.helper_python, lay.helper_script)
                       if not p.is_file()]
            if missing:
                raise ServiceError("Setup's files are incomplete: " + ", ".join(missing))
            allow = read_env_file(lay.env_file).get("BAIHE_API_ALLOW_KEY_WRITES", "") == "1"
            lay.wrapper_xml.write_text(app_service_xml(lay, allow), encoding="utf-8")
            created = self._create_service()
            self._grant()
            self._start_and_wait()
        except Exception:
            self._roll_back(swapped, created)
            raise
        self._discard(swapped)
        return "Baihe Studio's service is running and starts with Windows."

    def _roll_back(self, swapped, created: bool) -> None:
        try:
            stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        except Exception:
            pass
        if created:
            try:
                delete_service(APP_SERVICE, self.run, sleep=self.sleep)
            except Exception:
                pass
        self._restore(swapped)
        if not created:
            try:
                start_service(APP_SERVICE, self.run, sleep=self.sleep)
            except Exception:
                pass

    def stop(self) -> str:
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        return "Stopped Baihe Studio's service."

    def uninstall(self) -> str:
        """Stops and removes the service, takes it off the per-user
        folders, and removes the admin folder. If the service can't be
        stopped, nothing is changed; if it can't be removed, it is started
        again. The data folder's contents are left alone."""
        lay = self.layout
        was_running = query_state(APP_SERVICE, self.run) == "RUNNING"
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        try:
            delete_service(APP_SERVICE, self.run, sleep=self.sleep)
        except ServiceError:
            if was_running:
                try:
                    start_service(APP_SERVICE, self.run, sleep=self.sleep)
                except ServiceError:
                    pass
            raise
        for cmd, timeout in revoke_commands(lay):
            if Path(cmd[1]).exists():
                refuse_reparse_point(cmd[1])
                self.run(cmd, timeout=timeout)
        if lay.admin.exists():
            # rmtree unlinks a junction instead of following it. The running
            # interpreter, or a wrapper Windows still holds, is removed later.
            shutil.rmtree(lay.service_dir, ignore_errors=True)
            if not self.running_from_admin:
                shutil.rmtree(lay.admin, ignore_errors=True)
            if lay.admin.exists():
                self.remove_later(lay.admin)
        return "Removed Baihe Studio's service."

    def status(self) -> str:
        state = query_state(APP_SERVICE, self.run)
        start = query_start_type(APP_SERVICE, self.run) if state else None
        return f"{APP_SERVICE}: {state or 'not installed'}" + (f", start {start}" if start else "")


def build_services(args) -> Services:
    """From the admin copy (its config.json names the two folders, so
    nothing in the user's environment or the user-writable program folder
    decides where rights are granted) or, for Setup's `install`, from
    Setup's extraction with the folders Setup passes."""
    admin = program_files() / ADMIN_FOLDER_NAME
    config_file = APP_DIR.parent / CONFIG_FILE_NAME
    running_from_admin = config_file.is_file()
    if running_from_admin:
        if args.command == "install":
            raise ServiceError("`install` is run by Setup; run Setup again instead.")
        config = read_config(config_file)
        if not config:
            raise ServiceError(f"{config_file} is unreadable; run Setup again.")
        root, data = config["install_root"], config["data_dir"]
    else:
        if args.command != "install":
            raise ServiceError(f"Run this from the installed copy in {admin}\\helper.")
        root, data = args.install_root, args.data_dir
        if not root or not data:
            raise ServiceError("Setup passes --install-root and --data-dir.")
    layout = Layout(root, data, admin)
    layout.check()
    source = None if running_from_admin else APP_DIR.parent.parent
    return Services(layout, Runner(layout.log_file if running_from_admin or layout.admin.is_dir()
                                   else None),
                    source=source, running_from_admin=running_from_admin)


def main(argv=None, services=None, admin=None) -> int:
    parser = argparse.ArgumentParser(description="Baihe Studio's Windows service.")
    parser.add_argument("--install-root", help="the per-user install folder (Setup)")
    parser.add_argument("--data-dir", help="the data folder (Setup)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="create or refresh the service and start it")
    sub.add_parser("uninstall", help="stop and remove the service and the admin folder")
    sub.add_parser("stop", help="stop the service")
    sub.add_parser("status", help="show the service's state")
    args = parser.parse_args(argv)

    if args.command != "status" and not (is_admin() if admin is None else admin):
        print("This needs administrator rights: run it from an administrator Command Prompt "
              "or PowerShell.", file=sys.stderr)
        return EXIT_NOT_ADMIN
    if services is None:
        if os.name != "nt":
            print("Baihe Studio's service exists only on Windows.", file=sys.stderr)
            return EXIT_FAILED
        try:
            services = build_services(args)
        except ServiceError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return EXIT_FAILED
    run = services.run
    if hasattr(run, "log"):
        run.log(f"service.py {args.command}")
    try:
        message = getattr(services, args.command)()
    except (ServiceError, OSError, subprocess.SubprocessError) as e:
        if hasattr(run, "log"):
            run.log(f"failed: {e}")
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_FAILED
    if hasattr(run, "log"):
        run.log(message.replace("\n", "; "))
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
