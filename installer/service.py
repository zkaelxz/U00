"""
installer/service.py -- runs the installed Baihe Studio as Windows services
(docs/windows-installer-design.md, "Boot services").

Two services, each wrapped by WinSW (a copy of the same pinned binary,
named after the service and reading the .xml next to it):

- BaiheStudio: `python -s -m api` on 127.0.0.1:8600, started at boot,
  restarted after a failure, stopped with Ctrl+C so the server runs its
  clean stop (running jobs are cancelled).
- BaiheCaddy: the bundled Caddy for household access. Installed disabled.
  Only `enable-remote` starts it, and only when remote access is
  configured (the same checks `python -m api` runs before it opens the
  household listener).

Each runs as its own virtual account (NT SERVICE\\<name>), never as
LocalSystem, with the privilege list cut down to what it needs.

Everything this runs with administrator rights, and everything Caddy
keeps, lives in an admin-only folder, %ProgramFiles%\\Baihe Studio
Services: this script with the few modules it imports and the Caddy
template (helper\\lib), a copy of the bundled interpreter without
site-packages whose ._pth has no `import site` (helper\\python), the two
WinSW wrappers and their .xml, caddy.exe and the Caddyfile, Caddy's
certificates (caddy-data) and logs (caddy-logs), and the remote-access
state. The per-user program folder can be changed by any process running
as the user, and the data folder by the BaiheStudio service, so nothing
there is run elevated, and elevated steps touch the data folder only to
grant and revoke the service's access to it. Setup seeds the admin folder
from its own fresh extraction; every later command runs from the admin
copy:

    "%ProgramFiles%\\Baihe Studio Services\\helper\\python\\python.exe" -I -S
        "%ProgramFiles%\\Baihe Studio Services\\helper\\lib\\installer\\service.py" <command>

    --install-root DIR --data-dir DIR install
                                     (Setup) create or refresh both
    uninstall                        stop and remove both, the firewall rule,
                                     the service's permissions, the admin folder
    stop                             stop both (Setup, before an update)
    enable-remote [--household-port 8610]
    disable-remote
    status

Everything but `status` needs an administrator prompt. Nothing here opens a
router port or asks the router to open one: `enable-remote` adds a single
Windows Firewall rule (inbound TCP 443, for the bundled caddy.exe only, on
the private and domain network profiles), and `disable-remote` and
`uninstall` remove it. Forwarding a port on the router stays the owner's
own step (docs/household-access.md).

Standard library only, plus api.api_config (standard library too).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

APP_SERVICE = "BaiheStudio"
CADDY_SERVICE = "BaiheCaddy"
APP_DISPLAY_NAME = "Baihe Studio"
CADDY_DISPLAY_NAME = "Baihe Studio remote access (Caddy)"
ADMIN_FOLDER_NAME = "Baihe Studio Services"
ADMIN_PORT = 8600
EXTENSION_PORT = 8756          # page_server.DEFAULT_PORT
DEFAULT_HOUSEHOLD_PORT = 8610
HTTP_PORT = 80                 # Caddy's redirect listener; no firewall rule opens it
HTTPS_PORT = 443
FIREWALL_RULE_NAME = "Baihe Studio remote access - Caddy HTTPS"
FIREWALL_PROFILES = "private,domain"
STATE_FILE_NAME = "remote-access.json"
LOG_FILE_NAME = "service.log"
CONFIG_FILE_NAME = "config.json"
# What each service keeps of the privileges Windows gives a service
# account: passing through folders it can't list (the data folder is
# usually inside a user profile), creating global objects and adjusting
# its working set. Everything else goes, SeImpersonatePrivilege above all:
# code running with it can turn itself into LocalSystem.
SERVICE_PRIVILEGES = ("SeChangeNotifyPrivilege", "SeCreateGlobalPrivilege",
                      "SeIncreaseWorkingSetPrivilege")
# Restart after 10 s, 30 s, then every 60 s; the count resets after a
# day without a failure.
FAILURE_RESET_SECONDS = 86400
FAILURE_ACTIONS = "restart/10000/restart/30000/restart/60000"
LOG_ROLL_KB = 10240
LOG_KEEP_FILES = 5
APP_STOP_TIMEOUT = "15 sec"    # the server gives jobs 6 s, uvicorn up to 3 s more
CADDY_STOP_TIMEOUT = "10 sec"
STATE_WAIT_SECONDS = 60
HEALTH_WAIT_SECONDS = 180      # a first start compiles every .pyc
CADDY_WAIT_SECONDS = 30
COMMAND_TIMEOUT = 120
# The inheritable grant on the data folder is applied to everything already
# in it (a library with media and a model cache can be large).
DATA_GRANT_TIMEOUT = 3600
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
TEMPLATE_LOG_TOKEN = "{$BAIHE_CADDY_LOG_DIR}/baihe-access.log"
GENERATED_HEADER = ("# Written by installer/service.py from deploy/caddy/Caddyfile.template.\n"
                    "# Don't edit it: enable-remote and every install write it again.\n")
# What may be in the data folder before the service is given write access
# to all of it: only what Baihe Studio itself keeps there.
DATA_FOLDER_ENTRIES = {"library", ".env", "model_cache", "launcher", "desktop.ini", "thumbs.db"}
# The server's own settings: every one is written into the service's
# environment, so a machine-wide variable (say BAIHE_API_HOUSEHOLD_PORT)
# can never change what the service runs. "" means the app's default.
API_ENV_NAMES = ("BAIHE_API_HOST", "BAIHE_API_PORT", "BAIHE_API_ENV", "BAIHE_API_CORS_ORIGINS",
                 "BAIHE_API_ALLOW_KEY_WRITES", "BAIHE_API_SERVE_FRONTEND", "BAIHE_API_AUTH",
                 "BAIHE_API_COOKIE_SECURE", "BAIHE_API_BACKGROUND", "BAIHE_API_HOUSEHOLD_PORT",
                 "BAIHE_API_SESSION_IDLE_DAYS", "BAIHE_API_SESSION_MAX_DAYS",
                 "BAIHE_GOOGLE_CLIENT_ID", "BAIHE_GOOGLE_CLIENT_SECRET", "BAIHE_PUBLIC_URL",
                 "BAIHE_SHUTDOWN_TOKEN", "BAIHE_PROCESS_GROUP_NAME", "BAIHE_PORTABLE")

# A public DNS name: letters, digits and hyphens (punycode for anything
# else), at least one dot, and a last label that starts with a letter, so
# an IP address or a bare name like "localhost" never matches.
_DOMAIN_RE = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                        r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
_STATES = ("STOPPED", "START_PENDING", "STOP_PENDING", "RUNNING", "CONTINUE_PENDING",
           "PAUSE_PENDING", "PAUSED")
_START_TYPES = ("BOOT_START", "SYSTEM_START", "AUTO_START", "DEMAND_START", "DISABLED")
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_NOT_ACTIVE = 1062

EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_NOT_ADMIN = 3


class ServiceError(Exception):
    """A plain-words reason a step failed (exit code 1)."""


class ConfigRefused(ServiceError):
    """Remote access isn't configured, or not safely (exit code 2)."""


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
NETSH = _system32("netsh.exe")
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
    """'' if `path` is usable as a folder these services are given rights
    on, else why not. The rules Setup applies to the data folder
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


def read_config(config_file) -> dict:
    try:
        data = json.loads(Path(config_file).read_text(encoding="utf-8"))
        return {"install_root": str(data["install_root"]), "data_dir": str(data["data_dir"])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


# ---------------------------------------------------------------- WinSW XML

def _winsw_xml(fields: list, env: dict, log_dir: Path) -> str:
    root = ET.Element("service")
    for tag, text in fields:
        ET.SubElement(root, tag).text = text
    for name, value in env.items():
        ET.SubElement(root, "env", {"name": name, "value": value})
    ET.SubElement(root, "logpath").text = str(log_dir)
    log = ET.SubElement(root, "log", {"mode": "roll-by-size"})
    ET.SubElement(log, "sizeThreshold").text = str(LOG_ROLL_KB)
    ET.SubElement(log, "keepFiles").text = str(LOG_KEEP_FILES)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


def app_service_env(data_dir, household_port: int = 0, allow_key_writes: bool = False) -> dict:
    """The server's environment. Every setting the server reads is written
    out ("" = the app's default), so nothing machine-wide leaks in:
    loopback only, port 8600, the data folder the service was given rights
    on, no user site-packages, and the service-mode flag (`python -m api`
    then keeps this PC's own listener up if the household settings stop
    passing). The engine-key form is off unless the data folder's .env
    turns it on: other accounts on this PC can reach 127.0.0.1:8600 while
    the service runs. The household listener only while remote access is
    on."""
    env = {name: "" for name in API_ENV_NAMES}
    env.update({"BAIHE_API_HOST": "127.0.0.1", "BAIHE_API_PORT": str(ADMIN_PORT),
                "BAIHE_API_AUTH": "off", "BAIHE_API_ENV": "production",
                "BAIHE_API_ALLOW_KEY_WRITES": "1" if allow_key_writes else "0",
                "BAIHE_DATA_DIR": str(data_dir), "BAIHE_SERVICE": "1",
                "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"})
    if household_port:
        env["BAIHE_API_HOUSEHOLD_PORT"] = str(int(household_port))
    return env


def app_service_xml(layout: Layout, household_port: int = 0, allow_key_writes: bool = False) -> str:
    fields = [
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
    ]
    env = app_service_env(layout.data, household_port, allow_key_writes)
    return _winsw_xml(fields, env, layout.app_logs)


def caddy_service_xml(layout: Layout) -> str:
    # Caddy keeps its certificates and ACME account key under
    # XDG_DATA_HOME, in a folder only it and administrators can open.
    fields = [
        ("id", CADDY_SERVICE),
        ("name", CADDY_DISPLAY_NAME),
        ("description", "HTTPS for household devices, forwarding to Baihe Studio's "
                        "household listener. Off unless remote access is enabled."),
        ("executable", str(layout.caddy_exe)),
        ("arguments", f'run --config "{layout.caddyfile}" --adapter caddyfile'),
        ("workingdirectory", str(layout.caddy_storage)),
        ("startmode", "Manual"),
        ("stopparentprocessfirst", "true"),
        ("stoptimeout", CADDY_STOP_TIMEOUT),
    ]
    env = {"XDG_DATA_HOME": str(layout.caddy_storage), "XDG_CONFIG_HOME": str(layout.caddy_storage)}
    return _winsw_xml(fields, env, layout.caddy_logs)


# ------------------------------------------------- remote access: checking

def read_env_file(path) -> dict:
    """The data folder's .env, parsed like settings_service._read_env_file
    (utf-8-sig, comments and blank lines skipped, surrounding quotes
    stripped); {} if it's missing or unreadable. The service sees only this
    file: variables set with setx live in one user's environment. It can be
    changed by the service account, so every value used from it is checked
    before it reaches a service setting or the Caddyfile."""
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


def household_port_problem(port) -> str:
    if (not isinstance(port, int) or not 1024 <= port <= 65535
            or port in (ADMIN_PORT, ADMIN_PORT + 1, EXTENSION_PORT)):
        return ("The household port must be a free port from 1024 to 65535 other than "
                "8600, 8601 and 8756.")
    return ""


def remote_access_config(env_values: dict, household_port: int) -> dict:
    """{"domain", "household_port"} if remote access can be turned on, or
    ConfigRefused naming what's missing (never a value). The household
    listener's own startup checks decide first (`api_config`: sign-in set
    up, a loopback-only port that isn't 8600 or 8756, https public URL);
    then Caddy's: the public URL must be a DNS name on the default https
    port, since Caddy answers on 443 and gets the certificate for that
    name."""
    from api import api_config
    missing = [n for n in api_config.SIGN_IN_ENV_NAMES if not (env_values.get(n) or "").strip()]
    if missing:
        raise ConfigRefused(
            "Remote access isn't set up: " + ", ".join(missing) + " must be set in the data "
            "folder's .env first (docs/household-access.md). Nothing was changed.")
    environ = {"BAIHE_API_HOST": "127.0.0.1", "BAIHE_API_PORT": str(ADMIN_PORT),
               "BAIHE_API_HOUSEHOLD_PORT": str(household_port)}
    environ.update({n: env_values[n] for n in api_config.SIGN_IN_ENV_NAMES})
    try:
        settings = api_config.load_settings(environ)
        api_config.check_bind_safety(settings)
        api_config.check_household_bind_safety(settings)
    except ValueError as e:
        raise ConfigRefused(str(e).rstrip(".") + ". Nothing was changed.")
    problem = household_port_problem(settings.household_port)
    if problem:
        raise ConfigRefused(problem + " Nothing was changed.")
    parts = urlsplit(settings.public_url)
    if parts.scheme != "https":
        raise ConfigRefused("BAIHE_PUBLIC_URL must start with https:// for remote access. "
                            "Nothing was changed.")
    if parts.port not in (None, HTTPS_PORT):
        raise ConfigRefused("BAIHE_PUBLIC_URL must not name a port: Caddy answers on the "
                            "https port, 443. Nothing was changed.")
    domain = (parts.hostname or "").lower()
    if not _DOMAIN_RE.fullmatch(domain):
        raise ConfigRefused("BAIHE_PUBLIC_URL must name your domain, like "
                            "https://baihe.example.com (not an IP address or localhost). "
                            "Nothing was changed.")
    return {"domain": domain, "household_port": settings.household_port}


def render_caddyfile(template_text: str, domain: str, household_port: int, log_dir) -> str:
    """The template with its three settings written in, so the Caddy
    service needs no environment variables. It holds no secret: Google
    sign-in is Baihe's, never Caddy's. Refuses a template that no longer
    turns Caddy's admin endpoint off or that needs a setting this doesn't
    fill in, and values that could change the file's meaning."""
    if not _DOMAIN_RE.fullmatch(domain or ""):
        raise ConfigRefused("The domain isn't a valid DNS name.")
    problem = household_port_problem(household_port)
    if problem:
        raise ConfigRefused(problem)
    log = str(log_dir).replace("\\", "/")
    if not log or any(c in log for c in '"{}`\r\n\t'):
        raise ServiceError("Caddy's log folder has a character a Caddyfile can't hold.")
    if not re.search(r"^\s*admin off\s*$", template_text, re.M):
        raise ServiceError("The Caddy template no longer turns Caddy's admin endpoint off; "
                           "refusing to use it.")
    if template_text.count(TEMPLATE_LOG_TOKEN) != 1:
        raise ServiceError("The Caddy template's access log line has changed; "
                           "update installer/service.py with it.")
    text = template_text.replace(TEMPLATE_LOG_TOKEN, f'"{log}/baihe-access.log"')
    text = text.replace("{$BAIHE_DOMAIN}", domain)
    text = text.replace("{$BAIHE_API_HOUSEHOLD_PORT}", str(household_port))
    leftover = sorted(set(re.findall(r"\{\$[A-Za-z0-9_]+\}", text)))
    if leftover:
        raise ServiceError("The Caddy template uses settings this script doesn't fill in: "
                           + ", ".join(leftover))
    if f"reverse_proxy 127.0.0.1:{household_port} " not in text:
        raise ServiceError("The Caddy template no longer forwards to the household listener.")
    return GENERATED_HEADER + text


# ------------------------------------------------------ running commands

class Runner:
    """Runs a command with a time limit and logs it (commands and exit
    codes only: nothing here ever carries a secret). The log is in the
    admin folder, never in a folder a service account can change."""

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
                           "library\\logs\\service in the data folder, or caddy-logs in "
                           f"%ProgramFiles%\\{ADMIN_FOLDER_NAME}.")


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


def create_service_command(name: str, wrapper: Path, display: str) -> list:
    # sc.exe, not the wrapper's own `install`, and disabled until fully
    # configured: a failed install never leaves a service that starts at
    # boot as LocalSystem.
    return [SC, "create", name, "binPath=", _quoted(wrapper), "start=", "disabled",
            "DisplayName=", display]


def configure_service_commands(name: str, wrapper: Path) -> list:
    """sc.exe settings applied on every install, before the start type:
    the wrapper in the admin folder, its own virtual account, a cut-down
    privilege list, and restart after a failure (also when it exits with an
    error)."""
    cmds = [
        [SC, "config", name, "binPath=", _quoted(wrapper)],
        [SC, "sidtype", name, "unrestricted"],
        [SC, "config", name, "obj=", service_account(name)],
        [SC, "privs", name, "/".join(SERVICE_PRIVILEGES)],
        [SC, "failure", name, "reset=", str(FAILURE_RESET_SECONDS), "actions=", FAILURE_ACTIONS],
        [SC, "failureflag", name, "1"],
    ]
    if name == CADDY_SERVICE:
        # Stopping Baihe Studio's service stops Caddy too, so Caddy doesn't
        # forward to a household port something else might then take.
        cmds.append([SC, "config", name, "depend=", APP_SERVICE])
    return cmds


def _icacls(path, *args) -> list:
    # /L: act on a link itself, never on what it points to.
    return [ICACLS, path, *args, "/L"]


def grant_commands(layout: Layout) -> list:
    """(command, timeout) pairs: icacls grants, by SID. BaiheStudio: read
    and run the per-user install folder and its wrapper, change the data
    folder. It gets no write access to anything this script or its own
    interpreter loads code from. BaiheCaddy: read and run its wrapper and
    caddy.exe, change its own two folders in the admin folder, which
    inherit nothing: its certificates and ACME key (caddy-data) and its
    logs (caddy-logs) are Caddy, SYSTEM and Administrators only. Neither
    account can rename or replace anything elevated steps act on."""
    app_sid = "*" + service_sid(APP_SERVICE)
    caddy_sid = "*" + service_sid(CADDY_SERVICE)
    private = ("/inheritance:r", "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F",
               "/grant:r", f"*{ADMINISTRATORS_SID}:(OI)(CI)F",
               "/grant:r", f"{caddy_sid}:(OI)(CI)M")
    return [
        (_icacls(layout.root, "/grant", f"{app_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
        (_icacls(layout.data, "/grant", f"{app_sid}:(OI)(CI)M"), DATA_GRANT_TIMEOUT),
        (_icacls(layout.service_dir, "/grant", f"{app_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
        (_icacls(layout.caddy_dir, "/grant", f"{caddy_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
        (_icacls(layout.caddy_storage, *private), COMMAND_TIMEOUT),
        (_icacls(layout.caddy_logs, *private), COMMAND_TIMEOUT),
    ]


def revoke_commands(layout: Layout) -> list:
    """Takes BaiheStudio back out of the two per-user folders (the admin
    folder is deleted)."""
    sid = "*" + service_sid(APP_SERVICE)
    return [(_icacls(layout.root, "/remove:g", sid), COMMAND_TIMEOUT),
            (_icacls(layout.data, "/remove:g", sid), DATA_GRANT_TIMEOUT)]


def firewall_add_command(layout: Layout) -> list:
    return [NETSH, "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE_NAME}",
            "dir=in", "action=allow", "protocol=TCP", f"localport={HTTPS_PORT}",
            f"program={layout.caddy_exe}", f"profile={FIREWALL_PROFILES}", "enable=yes"]


def firewall_delete_command() -> list:
    return [NETSH, "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE_NAME}"]


def firewall_show_command() -> list:
    return [NETSH, "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"]


def remove_firewall_rule(run) -> None:
    # netsh exits 1 when there is no such rule; that's the goal anyway.
    run(firewall_delete_command())
    if run(firewall_show_command()).returncode == 0:
        raise ServiceError(f"The firewall rule '{FIREWALL_RULE_NAME}' couldn't be removed.")


# ------------------------------------------------------------- checks

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(port: int = ADMIN_PORT) -> bool:
    try:
        with _LOOPBACK_OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def household_is_baihe(port: int, domain: str) -> bool:
    """True if what listens on the household port is Baihe's household app:
    it answers /api/meta for the public name (any other Host is refused
    there) with app "Baihe Studio". A check against a wrong program, not
    proof against a deliberate impostor."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/meta", headers={"Host": domain})
    try:
        with _LOOPBACK_OPENER.open(req, timeout=2) as r:
            return r.status == 200 and json.loads(r.read(4096)).get("app") == "Baihe Studio"
    except Exception:
        return False


def port_answers(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


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

    def __init__(self, layout: Layout, run, health=health_ok, port_check=port_answers,
                 is_baihe=household_is_baihe, sleep=time.sleep, source=None,
                 running_from_admin=False, remove_later=remove_folder_later):
        self.layout, self.run = layout, run
        self.health, self.port_check, self.is_baihe = health, port_check, is_baihe
        self.sleep = sleep
        self.source = Path(source) if source else None
        self.running_from_admin = running_from_admin
        self.remove_later = remove_later

    # -- state

    def remote_state(self) -> dict:
        try:
            data = json.loads(self.layout.state_file.read_text(encoding="utf-8"))
            port = int(data.get("household_port", 0))
            return {"household_port": port} if port else {}
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def _write_state(self, household_port: int) -> None:
        if household_port:
            self.layout.state_file.write_text(
                json.dumps({"household_port": household_port}) + "\n", encoding="utf-8")
        else:
            self.layout.state_file.unlink(missing_ok=True)

    def _write_app_xml(self, household_port: int) -> None:
        allow = read_env_file(self.layout.env_file).get("BAIHE_API_ALLOW_KEY_WRITES", "") == "1"
        self.layout.wrapper_xml(APP_SERVICE).write_text(
            app_service_xml(self.layout, household_port, allow), encoding="utf-8")

    def _check_files(self, names) -> None:
        missing = [str(p) for p in names if not Path(p).is_file()]
        if missing:
            raise ServiceError("Missing (run Setup again to repair the install): "
                               + ", ".join(missing))

    def _grant(self) -> None:
        lay = self.layout
        for folder in (lay.root, lay.data):
            refuse_reparse_point(folder)
        for folder in (lay.caddy_storage, lay.caddy_logs):
            folder.mkdir(exist_ok=True)
        self.run.log("Granting folder permissions (the data folder can take a while).")
        for cmd, timeout in grant_commands(lay):
            _check(self.run(cmd, timeout=timeout), "Setting folder permissions")

    def _create_service(self, name: str, start: str) -> None:
        """Creates the service disabled if it doesn't exist, configures it,
        and only then sets its start type. A service this call created is
        deleted again if configuring it fails."""
        wrapper = self.layout.wrapper(name)
        created = False
        if query_state(name, self.run) is None:
            display = APP_DISPLAY_NAME if name == APP_SERVICE else CADDY_DISPLAY_NAME
            _check(self.run(create_service_command(name, wrapper, display)),
                   f"Creating the {name} service")
            created = True
        try:
            for cmd in configure_service_commands(name, wrapper):
                _check(self.run(cmd), f"Configuring the {name} service")
            _check(self.run([SC, "config", name, "start=", start]),
                   f"Setting the {name} service's start")
        except ServiceError:
            if created:
                self.run([SC, "delete", name])
            raise

    def _checked_config(self, household_port: int) -> dict:
        config = remote_access_config(read_env_file(self.layout.env_file), household_port)
        text = render_caddyfile(self.layout.template.read_text(encoding="utf-8"),
                                config["domain"], config["household_port"],
                                self.layout.caddy_logs)
        config["caddyfile"] = text
        return config

    def _restart_app(self) -> None:
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        start_service(APP_SERVICE, self.run, sleep=self.sleep)
        if not wait_until(self.health, HEALTH_WAIT_SECONDS, sleep=self.sleep):
            raise ServiceError("Baihe Studio's service started, but http://127.0.0.1:8600/api/health "
                               "doesn't answer. Its log is in library\\logs\\service in the data folder.")

    def _refresh_admin_copy(self) -> None:
        """Replaces helper\\, service\\ and caddy\\ in the admin folder with
        Setup's fresh extraction (`source`), and records the two per-user
        folders. Caddy's certificates, logs and the remote-access state are
        kept. Refuses if another install on this PC owns the services."""
        lay = self.layout
        if self.source is None:
            raise ServiceError("`install` is run by Setup (it needs Setup's files).")
        refuse_reparse_point(lay.admin)
        old = read_config(lay.config_file)
        if (old and _norm(old["install_root"]) != _norm(lay.root)
                and (Path(old["install_root"]) / "app" / "INSTALLED").is_file()):
            raise ServiceError(
                f"Another Baihe Studio install on this PC ({old['install_root']}) runs the "
                "background service. Uninstall it, or untick the service in this Setup.")
        lay.admin.mkdir(parents=True, exist_ok=True)
        for sub in ("helper", "service", "caddy"):
            src = self.source / sub
            if not src.is_dir():
                raise ServiceError(f"Setup's files are incomplete: {src} is missing.")
            dest = lay.admin / sub
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, symlinks=True)
        lay.config_file.write_text(json.dumps({"install_root": str(lay.root),
                                               "data_dir": str(lay.data)}, indent=2) + "\n",
                                   encoding="utf-8")

    # -- commands

    def install(self) -> str:
        """Seeds the admin folder, creates both services or refreshes them
        after an update, and starts Baihe Studio's. Caddy stays disabled
        unless remote access was enabled before and is still configured."""
        lay = self.layout
        lay.check()
        problem = data_folder_contents_problem(lay.data)
        if problem:
            raise ServiceError(problem)
        self._check_files([lay.python_exe])
        self._refresh_admin_copy()
        self._check_files([lay.wrapper(APP_SERVICE), lay.wrapper(CADDY_SERVICE), lay.caddy_exe,
                           lay.template, lay.helper_python, lay.helper_script])
        port = self.remote_state().get("household_port", 0)
        config = None
        note = ""
        if port:
            try:
                config = self._checked_config(port)
            except ServiceError as e:
                note = f" Remote access was turned off: {e}"
                port = 0
        self._write_app_xml(port)
        lay.wrapper_xml(CADDY_SERVICE).write_text(caddy_service_xml(lay), encoding="utf-8")
        self._create_service(APP_SERVICE, "auto")
        self._create_service(CADDY_SERVICE, "auto" if config else "disabled")
        self._grant()
        if config:
            lay.caddyfile.write_text(config["caddyfile"], encoding="utf-8")
            remove_firewall_rule(self.run)
            _check(self.run(firewall_add_command(lay)), "Adding the firewall rule for port 443")
        else:
            self._turn_off_caddy()
            self._write_state(0)
        self._restart_app()
        if config:
            start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            return "Baihe Studio's service is running; remote access is on." + note
        return "Baihe Studio's service is running and starts with Windows." + note

    def _turn_off_caddy(self) -> None:
        """Closes the firewall rule and disables Caddy before stopping it,
        each step on its own, so a stop that hangs never leaves the port
        open or Caddy set to start at boot. Raises after trying them all."""
        errors = []
        try:
            remove_firewall_rule(self.run)
        except Exception as e:
            errors.append(f"removing the firewall rule: {e}")
        try:
            if query_state(CADDY_SERVICE, self.run) is not None:
                _check(self.run([SC, "config", CADDY_SERVICE, "start=", "disabled"]),
                       "Disabling the Caddy service")
        except Exception as e:
            errors.append(f"disabling Caddy: {e}")
        try:
            stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        except Exception as e:
            errors.append(f"stopping Caddy: {e}")
        self.layout.caddyfile.unlink(missing_ok=True)
        if errors:
            raise ServiceError("Turning Caddy off didn't fully work (" + "; ".join(errors) + ").")

    def stop(self) -> str:
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        return "Stopped Baihe Studio's services."

    def uninstall(self) -> str:
        """Removes the firewall rule first, then stops and removes both
        services, BaiheStudio's permissions on the per-user folders, and
        the admin folder (Caddy's certificates and logs with it). The data
        folder's contents are left alone."""
        lay = self.layout
        remove_firewall_rule(self.run)
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        delete_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        delete_service(APP_SERVICE, self.run, sleep=self.sleep)
        for cmd, timeout in revoke_commands(lay):
            if Path(cmd[1]).exists():
                refuse_reparse_point(cmd[1])
                self.run(cmd, timeout=timeout)
        if lay.admin.exists():
            # rmtree unlinks a junction Caddy made in its folders instead of
            # following it; only the running interpreter is left for later.
            for sub in ("caddy-data", "caddy-logs", "caddy", "service"):
                shutil.rmtree(lay.admin / sub, ignore_errors=True)
            for name in (STATE_FILE_NAME, LOG_FILE_NAME):
                (lay.admin / name).unlink(missing_ok=True)
            if self.running_from_admin:
                self.remove_later(lay.admin)
            else:
                shutil.rmtree(lay.admin, ignore_errors=True)
        return "Removed Baihe Studio's services and the remote-access firewall rule."

    def enable_remote(self, household_port: int = DEFAULT_HOUSEHOLD_PORT) -> str:
        """Turns household access on: the household listener in Baihe
        Studio's service, the Caddyfile, the firewall rule and the Caddy
        service. Checks everything first and changes nothing if remote
        access isn't configured or the port is taken; undoes its changes if
        a step fails."""
        lay = self.layout
        if query_state(APP_SERVICE, self.run) is None:
            raise ServiceError("Baihe Studio isn't installed as a service. Run Setup again with "
                               "\"Run Baihe Studio in the background\" ticked.")
        self._check_files([lay.caddy_exe, lay.wrapper(CADDY_SERVICE), lay.template])
        config = self._checked_config(household_port)
        port = config["household_port"]
        if self.remote_state().get("household_port") != port and self.port_check(port):
            raise ConfigRefused(f"Port {port} on this PC is already in use by another program; "
                                "choose another with --household-port. Nothing was changed.")
        try:
            self._write_state(port)
            self._write_app_xml(port)
            self._restart_app()
            if not wait_until(lambda: self.is_baihe(port, config["domain"]), 30,
                              sleep=self.sleep):
                raise ServiceError(f"What answers on 127.0.0.1:{port} isn't Baihe Studio's "
                                   "household listener.")
            lay.caddyfile.write_text(config["caddyfile"], encoding="utf-8")
            lay.wrapper_xml(CADDY_SERVICE).write_text(caddy_service_xml(lay), encoding="utf-8")
            self._create_service(CADDY_SERVICE, "auto")
            self._grant()
            remove_firewall_rule(self.run)
            _check(self.run(firewall_add_command(lay)), "Adding the firewall rule for port 443")
            start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            if not wait_until(lambda: self.port_check(HTTPS_PORT), CADDY_WAIT_SECONDS,
                              sleep=self.sleep):
                raise ServiceError("Caddy started but doesn't answer on port 443 (is another "
                                   f"program using it?). Its log is in caddy-logs in "
                                   f"%ProgramFiles%\\{ADMIN_FOLDER_NAME}.")
        except Exception as failure:
            try:
                self.disable_remote()
            except Exception as undo:
                raise ServiceError(f"{failure} Undoing it also failed: {undo} Run "
                                   "disable-remote again.") from failure
            raise
        return (f"Remote access is on for {config['domain']}: Caddy answers on port 443 "
                "(firewall: private and domain networks only). Nothing was changed on your "
                "router; forwarding port 443 there is your own step (docs/household-access.md).")

    def disable_remote(self) -> str:
        """Turns household access off: the firewall rule removed and Caddy
        disabled first, then stopped; Baihe Studio's service restarted
        without the household listener. Reports a failure after doing
        every step it can."""
        errors = []
        try:
            self._turn_off_caddy()
        except ServiceError as e:
            errors.append(str(e))
        self._write_state(0)
        if query_state(APP_SERVICE, self.run) is not None:
            self._write_app_xml(0)
            try:
                self._restart_app()
            except ServiceError as e:
                errors.append(str(e))
        if errors:
            raise ServiceError(" ".join(errors))
        return "Remote access is off: Caddy is stopped and disabled, and the firewall rule is removed."

    def status(self) -> str:
        lines = []
        for name in (APP_SERVICE, CADDY_SERVICE):
            state = query_state(name, self.run)
            start = query_start_type(name, self.run) if state else None
            lines.append(f"{name}: {state or 'not installed'}" + (f", start {start}" if start else ""))
        port = self.remote_state().get("household_port")
        lines.append(f"Remote access: {'on, household port ' + str(port) if port else 'off'}")
        rule = self.run(firewall_show_command()).returncode == 0
        lines.append(f"Firewall rule for port 443: {'present' if rule else 'none'}")
        return "\n".join(lines)


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
            raise ServiceError("Run this from the installed copy in "
                               f"{admin}\\helper (see docs/household-access.md).")
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
    parser = argparse.ArgumentParser(description="Baihe Studio's Windows services.")
    parser.add_argument("--install-root", help="the per-user install folder (Setup)")
    parser.add_argument("--data-dir", help="the data folder (Setup)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="create or refresh both services and start Baihe Studio's")
    sub.add_parser("uninstall", help="stop and remove both services and the admin folder")
    sub.add_parser("stop", help="stop both services")
    enable = sub.add_parser("enable-remote", help="turn household access through Caddy on")
    enable.add_argument("--household-port", type=int, default=DEFAULT_HOUSEHOLD_PORT)
    sub.add_parser("disable-remote", help="turn household access off")
    sub.add_parser("status", help="show both services' state")
    args = parser.parse_args(argv)

    if args.command != "status" and not (is_admin() if admin is None else admin):
        print("This needs administrator rights: run it from an administrator Command Prompt "
              "or PowerShell.", file=sys.stderr)
        return EXIT_NOT_ADMIN
    if services is None:
        if os.name != "nt":
            print("Baihe Studio's services exist only on Windows.", file=sys.stderr)
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
        if args.command == "install":
            message = services.install()
        elif args.command == "uninstall":
            message = services.uninstall()
        elif args.command == "stop":
            message = services.stop()
        elif args.command == "enable-remote":
            message = services.enable_remote(args.household_port)
        elif args.command == "disable-remote":
            message = services.disable_remote()
        else:
            message = services.status()
    except ConfigRefused as e:
        if hasattr(run, "log"):
            run.log(f"refused: {e}")
        print(str(e), file=sys.stderr)
        return EXIT_REFUSED
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
