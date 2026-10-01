"""
installer/service.py -- runs the installed Baihe Studio as a Windows service
(docs/windows-installer-design.md, "Boot service").

Two services, each wrapped by a copy of the pinned WinSW binary named after
it (it answers the Service Control Manager and reads the .xml next to it):

- BaiheStudio: `python -s -m api` on 127.0.0.1 and nothing else, at the
  app's default port (api_config.DEFAULT_PORT) or the one chosen with
  `install --port`, started at boot, restarted after a failure, stopped with
  Ctrl+C so the server runs its clean stop.
- BaiheCaddy: the bundled Caddy, the only component that faces the internet
  (HTTPS certificates and rate limits). Installed disabled and stopped;
  only `enable-remote`, run by the owner, turns it on. Caddy forwards to
  Baihe's household listener, which listens on 127.0.0.1 only: Baihe never
  exposes its own API to the network.

Each runs as its own virtual account (NT SERVICE\\<name>), never
LocalSystem, with the privilege list cut down to what it needs.

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

    --install-root DIR --data-dir DIR install [--port N]
                          (Setup) create or refresh the service and start it;
                          undone if it fails. --port (Setup passes the
                          user's BAIHE_API_PORT, if set) is the port of a
                          fresh install; an update keeps the stored port
                          and ignores it (set-port changes it)
    set-port N            move the installed service to port N (the
                          Start-menu item "Baihe Studio service" runs it);
                          refused, changing nothing, if N can't be used,
                          and put back on the old port if N doesn't answer
    stop                  stop it (Setup, before an update)
    uninstall             stop and remove both, take BaiheStudio's permissions
                          off the per-user folders, remove the admin folder
    enable-remote [--household-port 8610]
                          the owner's opt-in: the household listener
                          (127.0.0.1 only), the Caddyfile and the Caddy
                          service; refused, changing nothing, unless sign-in
                          and the public https name are set in the data
                          folder's .env
    disable-remote        Caddy stopped and disabled, the household listener
                          closed
    status                both services' state and every port Baihe uses

Everything but `status` needs an administrator prompt. Nothing here adds a
Windows Firewall rule or does anything on the router or with DNS: Windows
blocks inbound connections to Caddy until the owner adds the rule printed by
`enable-remote` and `status` (firewall_rule_command), forwarding port 443 on
the router is the owner's own step, and so is the domain name
(docs/household-access.md).

Standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
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

APP_SERVICE = "BaiheStudio"
CADDY_SERVICE = "BaiheCaddy"
APP_DISPLAY_NAME = "Baihe Studio"
CADDY_DISPLAY_NAME = "Baihe Studio remote access (Caddy)"
ADMIN_FOLDER_NAME = "Baihe Studio Services"
ADMIN_PORT = 8600              # api_config.DEFAULT_PORT
# The PC listener's own ports in RESERVED_PORTS: the service may take them,
# the household listener may not.
PC_LISTENER_PORTS = (ADMIN_PORT, ADMIN_PORT + 1)
# Ports that are Baihe's own (settings_service.baihe_own_ports): Streamlit's,
# the PC listener's, the extension bridge's (page_server.DEFAULT_PORT), and
# 8601, the documented "pick another port" for the PC listener. The
# household listener may take none of them.
EXTENSION_BRIDGE_PORT = 8756    # page_server.DEFAULT_PORT
RESERVED_PORTS = (8501, ADMIN_PORT, ADMIN_PORT + 1, EXTENSION_BRIDGE_PORT)
DEFAULT_HOUSEHOLD_PORT = 8610
HTTPS_PORT = 443
# The rule the owner adds by hand (firewall_rule_command). This script only
# reads it, never adds it.
FIREWALL_RULE_NAME = "Baihe Studio remote access - Caddy HTTPS"
FIREWALL_PROFILES = "private,domain"
STATE_FILE_NAME = "remote-access.json"
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
CADDY_STOP_TIMEOUT = "10 sec"
CADDY_WAIT_SECONDS = 30
# The household listener starts after the PC one; a bad setting only skips it
# (the PC listener still starts), so it is checked on its own.
HOUSEHOLD_WAIT_SECONDS = 30
STATE_WAIT_SECONDS = 60
HEALTH_WAIT_SECONDS = 180      # a first start compiles every .pyc
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
# can never change what the service runs or add a listener. "" means the
# app's default.
API_ENV_NAMES = ("BAIHE_API_HOST", "BAIHE_API_PORT", "BAIHE_API_ENV", "BAIHE_API_CORS_ORIGINS",
                 "BAIHE_API_ALLOW_KEY_WRITES", "BAIHE_API_SERVE_FRONTEND", "BAIHE_API_AUTH",
                 "BAIHE_API_COOKIE_SECURE", "BAIHE_API_BACKGROUND", "BAIHE_API_HOUSEHOLD_PORT",
                 "BAIHE_API_SESSION_IDLE_DAYS", "BAIHE_API_SESSION_MAX_DAYS",
                 "BAIHE_GOOGLE_CLIENT_ID", "BAIHE_GOOGLE_CLIENT_SECRET", "BAIHE_PUBLIC_URL",
                 "BAIHE_SHUTDOWN_TOKEN", "BAIHE_PROCESS_GROUP_NAME", "BAIHE_PORTABLE")

# A public DNS name: labels of letters, digits and hyphens, at least one dot,
# and a last label that starts with a letter, so an IP address or a bare name
# like "localhost" never matches. ASCII only, as the server requires
# (browsers send the punycode form).
_DOMAIN_RE = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                        r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
SIGN_IN_ENV_NAMES = ("BAIHE_GOOGLE_CLIENT_ID", "BAIHE_GOOGLE_CLIENT_SECRET", "BAIHE_PUBLIC_URL")

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
    """Remote access isn't set up (or can't be) and nothing was changed
    (exit code 2)."""


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
NETSH = _system32("netsh.exe")      # only ever `show rule`: see firewall_show_command
CMD = _system32("cmd.exe")


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


def read_config(config_file) -> dict:
    try:
        data = json.loads(Path(config_file).read_text(encoding="utf-8"))
        return {"install_root": str(data["install_root"]), "data_dir": str(data["data_dir"])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def stored_api_port(config_file) -> int:
    """The service's port, as `install` stored it in the admin-only
    config.json; the default if none is stored (a service installed before
    the port could be chosen) or it isn't valid. Never taken from the data
    folder's .env or the environment: the service account can change the
    one, and any process the other."""
    try:
        port = json.loads(Path(config_file).read_text(encoding="utf-8")).get("api_port", ADMIN_PORT)
    except (OSError, ValueError, AttributeError):
        return ADMIN_PORT
    return ADMIN_PORT if api_port_problem(port) else port


def _replace_text(path, text: str) -> None:
    # Written beside it and swapped in, so a crash never leaves half a file.
    path = Path(path)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


def app_service_env(data_dir, household_port: int = 0, allow_key_writes: bool = False,
                    api_port: int = ADMIN_PORT) -> dict:
    """The server's environment. Every setting the server reads is written
    out ("" = the app's default), so nothing machine-wide leaks in:
    loopback only, the port stored at install, the data folder the service
    was given rights on, no user site-packages. The engine-key form is off
    unless the data folder's .env turns it on: other accounts on this PC can
    reach the service's port while it runs. The household listener (which
    the server itself binds to 127.0.0.1) only while remote access is on, and
    only from here: a machine-wide variable or the .env can't open it."""
    env = {name: "" for name in API_ENV_NAMES}
    env.update({"BAIHE_API_HOST": "127.0.0.1", "BAIHE_API_PORT": str(int(api_port)),
                "BAIHE_API_AUTH": "off", "BAIHE_API_ENV": "production",
                "BAIHE_API_ALLOW_KEY_WRITES": "1" if allow_key_writes else "0",
                "BAIHE_DATA_DIR": str(data_dir),
                "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"})
    if household_port:
        env["BAIHE_API_HOUSEHOLD_PORT"] = str(int(household_port))
    return env


def app_service_xml(layout: Layout, household_port: int = 0, allow_key_writes: bool = False,
                    api_port: int = ADMIN_PORT) -> str:
    fields = [
        ("id", APP_SERVICE),
        ("name", APP_DISPLAY_NAME),
        ("description", f"Baihe Studio's server for this PC (http://127.0.0.1:{int(api_port)})."),
        ("executable", str(layout.python_exe)),
        ("arguments", "-s -m api"),
        ("workingdirectory", str(layout.app)),
        ("startmode", "Automatic"),
        # Ctrl+C goes to python first, so the server's clean stop runs;
        # its Job Object then ends ffmpeg, the browser and the rest.
        ("stopparentprocessfirst", "true"),
        ("stoptimeout", APP_STOP_TIMEOUT),
    ]
    env = app_service_env(layout.data, household_port, allow_key_writes, api_port)
    return _winsw_xml(fields, env, layout.app_logs)


def caddy_service_xml(layout: Layout) -> str:
    # Caddy keeps its certificates and ACME account key under XDG_DATA_HOME,
    # in a folder only it and administrators can open.
    fields = [
        ("id", CADDY_SERVICE),
        ("name", CADDY_DISPLAY_NAME),
        ("description", "HTTPS for household devices, forwarding to Baihe Studio's "
                        "household listener on 127.0.0.1. Off unless remote access is enabled."),
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

def _is_port(port) -> bool:
    return isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535


def household_port_problem(port, api_port: int = ADMIN_PORT) -> str:
    """'' if `port` can be the household listener's: none of Baihe's own
    ports, nor the service's (api_port)."""
    taken = sorted(set(RESERVED_PORTS) | {api_port})
    if not _is_port(port) or port in taken:
        return ("The household port must be a free port from 1024 to 65535 other than "
                + ", ".join(str(p) for p in taken) + ".")
    return ""


def api_port_problem(port, household_ports=()) -> str:
    """'' if `port` can be the service's own (the PC listener's): none of
    Baihe's other ports (Streamlit's, the extension bridge's), nor the
    household port remote access uses or would use by default."""
    taken = sorted((set(RESERVED_PORTS) - set(PC_LISTENER_PORTS))
                   | {DEFAULT_HOUSEHOLD_PORT} | set(household_ports))
    if not _is_port(port) or port in taken:
        return ("The service's port must be a free port from 1024 to 65535 other than "
                + ", ".join(str(p) for p in taken) + ".")
    return ""


def remote_access_config(env_values: dict, household_port, api_port: int = ADMIN_PORT) -> dict:
    """{"domain", "household_port"} if remote access can be turned on, or
    ConfigRefused naming what's missing (never a value). Sign-in must be set
    up and the public URL must be just https:// and a DNS name on the
    default port (Caddy answers on 443 and gets the certificate for that
    name). These are the server's own household-listener rules
    (api_config.normalize_public_url, check_household_bind_safety) and more,
    checked here first because the server no longer refuses to start over
    them: it skips the household listener and keeps the PC one running.
    enable-remote then checks that the household listener answers."""
    missing = [n for n in SIGN_IN_ENV_NAMES if not (env_values.get(n) or "").strip()]
    if missing:
        raise ConfigRefused(
            "Remote access isn't set up: " + ", ".join(missing) + " must be set in the data "
            "folder's .env first (docs/household-access.md). Nothing was changed.")
    problem = household_port_problem(household_port, api_port)
    if problem:
        raise ConfigRefused(problem + " Nothing was changed.")
    try:
        parts = urlsplit(env_values["BAIHE_PUBLIC_URL"].strip())
        port = parts.port
    except ValueError:
        raise ConfigRefused("BAIHE_PUBLIC_URL isn't a valid address. Nothing was changed.")
    if parts.scheme != "https":
        raise ConfigRefused("BAIHE_PUBLIC_URL must start with https:// for remote access. "
                            "Nothing was changed.")
    if "@" in parts.netloc or parts.path.strip("/") or parts.query or parts.fragment:
        raise ConfigRefused("BAIHE_PUBLIC_URL must be just https://your-domain (no path, "
                            "query or user name). Nothing was changed.")
    if port not in (None, HTTPS_PORT):
        raise ConfigRefused("BAIHE_PUBLIC_URL must not name a port: Caddy answers on the "
                            "https port, 443. Nothing was changed.")
    domain = (parts.hostname or "").lower()
    if not _DOMAIN_RE.fullmatch(domain):
        raise ConfigRefused("BAIHE_PUBLIC_URL must name your domain, like "
                            "https://baihe.example.com (not an IP address or localhost; a "
                            "non-ASCII name in its xn-- form). Nothing was changed.")
    return {"domain": domain, "household_port": household_port}


def render_caddyfile(template_text: str, domain: str, household_port: int, log_dir) -> str:
    """The template with its three settings written in, so the Caddy
    service needs no environment variables. It holds no secret: Google
    sign-in is Baihe's, never Caddy's. Refuses a template that no longer
    turns Caddy's admin endpoint off, keeps the refusal of PC-only routes or
    forwards only to the household port, and values that could change the
    file's meaning."""
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
    if "respond @pc_only 404" not in template_text:
        raise ServiceError("The Caddy template no longer refuses PC-only routes; "
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
        # A stop through the service manager stops Caddy first. If Baihe's
        # process exits on its own, Caddy keeps running until the restart.
        cmds.append([SC, "config", name, "depend=", APP_SERVICE])
    return cmds


def _icacls(path, *args) -> list:
    # /L: act on a link itself, never on what it points to.
    return [ICACLS, path, *args, "/L"]


def grant_commands(layout: Layout, caddy_only: bool = False) -> list:
    """(command, timeout) pairs: icacls grants, by SID. BaiheStudio: read
    and run the per-user install folder and its wrapper, change the data
    folder. It gets no write access to anything this script or its own
    interpreter loads code from. BaiheCaddy: read and run its wrapper and
    caddy.exe, change its own two folders in the admin folder, which
    inherit nothing: its certificates and ACME key (caddy-data) and its
    logs (caddy-logs) are Caddy, SYSTEM and Administrators only. caddy_only
    skips the BaiheStudio grants, for enabling remote access."""
    app_sid = "*" + service_sid(APP_SERVICE)
    caddy_sid = "*" + service_sid(CADDY_SERVICE)
    private = ("/inheritance:r", "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F",
               "/grant:r", f"*{ADMINISTRATORS_SID}:(OI)(CI)F",
               "/grant:r", f"{caddy_sid}:(OI)(CI)M")
    app_grants = [
        (_icacls(layout.root, "/grant", f"{app_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
        (_icacls(layout.data, "/grant", f"{app_sid}:(OI)(CI)M"), DATA_GRANT_TIMEOUT),
        (_icacls(layout.service_dir, "/grant", f"{app_sid}:(OI)(CI)RX"), COMMAND_TIMEOUT),
    ]
    return ([] if caddy_only else app_grants) + [
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


def firewall_rule_command(layout: Layout) -> str:
    """The command the owner runs by hand, in an administrator prompt, to let
    connections from the private and domain networks reach Caddy on port
    443. Only text: this script never runs it."""
    return (f'netsh advfirewall firewall add rule name="{FIREWALL_RULE_NAME}" dir=in '
            f'action=allow protocol=TCP localport={HTTPS_PORT} program="{layout.caddy_exe}" '
            f'profile={FIREWALL_PROFILES} enable=yes')


def firewall_delete_command_text() -> str:
    return f'netsh advfirewall firewall delete rule name="{FIREWALL_RULE_NAME}"'


def firewall_show_command() -> list:
    # Read-only: this script looks for the owner's rule, never changes one.
    return [NETSH, "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"]


def remote_reminder(layout: Layout, rule_present: bool) -> str:
    """What the owner still does by hand."""
    if rule_present:
        return (f"The Windows Firewall rule '{FIREWALL_RULE_NAME}' exists. Forwarding port 443 "
                "on your router to this PC is your own step.")
    return ("Windows Firewall still blocks connections to Caddy. To allow them from your "
            "private and domain networks, run this in an administrator prompt:\n  "
            + firewall_rule_command(layout)
            + "\nThen forward TCP port 443 on your router to this PC. This script does "
              "neither, and does nothing with DNS (docs/household-access.md).")


# ------------------------------------------------------------- checks

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health_ok(port: int) -> bool:
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
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


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


# ---------------------------------------------------------- operations

class Services:
    """The commands, against a layout, a command runner and checks that
    tests replace."""

    def __init__(self, layout: Layout, run, health=health_ok, port_check=port_answers,
                 is_baihe=household_is_baihe, sleep=time.sleep, source=None):
        self.layout, self.run = layout, run
        self.health, self.port_check, self.is_baihe = health, port_check, is_baihe
        self.sleep = sleep
        self.source = Path(source) if source else None

    def api_port(self) -> int:
        """The service's own port, from the admin-only config.json."""
        return stored_api_port(self.layout.config_file)

    def _stored_port_value(self):
        """The integer api_port config.json holds, usable or not, or None
        (no config, an unreadable one, or one from before the port was
        stored)."""
        try:
            raw = json.loads(self.layout.config_file.read_text(encoding="utf-8")).get("api_port")
        except (OSError, ValueError, AttributeError):
            return None
        return raw if type(raw) is int else None

    # -- remote-access state (the household port while it is on)

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
            app_service_xml(self.layout, household_port, allow, self.api_port()), encoding="utf-8")

    def _checked_config(self, household_port) -> dict:
        config = remote_access_config(read_env_file(self.layout.env_file), household_port,
                                      self.api_port())
        config["caddyfile"] = render_caddyfile(
            self.layout.template.read_text(encoding="utf-8"), config["domain"],
            config["household_port"], self.layout.caddy_logs)
        return config

    def _rule_present(self) -> bool:
        return self.run(firewall_show_command()).returncode == 0

    def _household_up(self, config: dict) -> bool:
        return wait_until(lambda: self.is_baihe(config["household_port"], config["domain"]),
                          HOUSEHOLD_WAIT_SECONDS, sleep=self.sleep)

    def _swap_in_admin_files(self, api_port: int) -> list:
        """Replaces helper\\, service\\ and caddy\\ in the admin folder with
        Setup's fresh extraction (`source`), keeping what was there as *.old,
        and records the two per-user folders and the service's port in
        helper\\config.json (so a rollback puts the old port back with the
        old files). Caddy's certificates, logs and
        the remote-access state are not touched. Returns the (new, old)
        pairs for _restore or _discard. Refuses if another install on this PC
        owns the service."""
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
        sources = {"helper": self.source / "helper", "service": self.source / "wrapper",
                   "caddy": self.source / "caddy"}
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
                                                   "data_dir": str(lay.data),
                                                   "api_port": api_port}, indent=2) + "\n",
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

    def _create_service(self, name: str, start: str) -> bool:
        """Creates the service disabled if it doesn't exist, configures it,
        and only then sets its start type. Returns True if this call
        created it."""
        wrapper = self.layout.wrapper(name)
        created = query_state(name, self.run) is None
        if created:
            display = APP_DISPLAY_NAME if name == APP_SERVICE else CADDY_DISPLAY_NAME
            _check(self.run(create_service_command(name, wrapper, display)),
                   f"Creating the {name} service")
        try:
            for cmd in configure_service_commands(name, wrapper):
                _check(self.run(cmd), f"Configuring the {name} service")
            _check(self.run([SC, "config", name, "start=", start]),
                   f"Setting the {name} service's start")
        except ServiceError:
            if created:
                self.run([SC, "delete", name])
            raise
        return created

    def _grant(self, caddy_only: bool = False) -> None:
        lay = self.layout
        for folder in (() if caddy_only else (lay.root, lay.data)):
            refuse_reparse_point(folder)
        for folder in (lay.caddy_storage, lay.caddy_logs):
            refuse_reparse_point(folder)
            folder.mkdir(exist_ok=True)
        self.run.log("Granting folder permissions" + ("." if caddy_only else
                     " (the data folder can take a while)."))
        for cmd, timeout in grant_commands(lay, caddy_only):
            _check(self.run(cmd, timeout=timeout), "Setting folder permissions")

    def _start_and_wait(self) -> None:
        start_service(APP_SERVICE, self.run, sleep=self.sleep)
        port = self.api_port()
        if not wait_until(lambda: self.health(port), HEALTH_WAIT_SECONDS, sleep=self.sleep):
            raise ServiceError(f"Baihe Studio's service started, but http://127.0.0.1:{port}/api/health "
                               "doesn't answer. Its log is in library\\logs\\service in the data folder.")

    def _restart_app(self) -> None:
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        self._start_and_wait()

    def _turn_off_caddy(self) -> None:
        """Disables Caddy before stopping it, so a stop that hangs never
        leaves it set to start at boot, then removes its Caddyfile. Raises
        after trying every step."""
        errors = []
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

    def _chosen_api_port(self, requested) -> int:
        """The port `install` or `set-port` gives the service: `requested`
        (Setup's --port, set-port's N) once checked, else the one it has.
        Refused, before anything changes, if it isn't a usable port or
        another program holds it."""
        current = self.api_port()
        if requested is None:
            return current
        try:
            text = str(requested).strip()
            port = int(text) if re.fullmatch(r"[0-9]{1,5}", text) else None
        except ValueError:
            port = None
        household = {self.remote_state().get("household_port", 0)} - {0}
        problem = api_port_problem(port, household)
        if problem:
            raise ConfigRefused(f"{problem} Nothing was changed: the service keeps port {current}.")
        if port != current and self.port_check(port):
            raise ConfigRefused(f"Port {port} on this PC is already in use by another program. "
                                f"Nothing was changed: the service keeps port {current}.")
        return port

    def _start_again(self) -> None:
        """Starts the services Setup stopped before an install that was
        refused, as they were set up: best effort."""
        for name in (APP_SERVICE, CADDY_SERVICE):
            try:
                if (query_state(name, self.run) == "STOPPED"
                        and query_start_type(name, self.run) == "AUTO_START"):
                    start_service(name, self.run, sleep=self.sleep)
            except Exception:
                pass

    def install(self, api_port=None) -> str:
        """Seeds the admin folder, creates both services or refreshes them
        after an update, and starts Baihe Studio's on the port config.json
        stores. `api_port` (Setup's --port) is used only when none is stored
        (a fresh install, or a service from before the port was stored; the
        default without it); an update keeps the stored one, which only
        set_port changes. Caddy is created disabled and stays off unless the
        owner enabled remote access before, its settings still pass and the
        household listener comes up. An `api_port` that would be used but
        can't be is refused before anything changes. If anything fails,
        the admin files that were there before are put back (with the old
        port), services this call created are removed, and ones that existed
        are started again (Setup stopped them)."""
        lay = self.layout
        lay.check()
        problem = data_folder_contents_problem(lay.data)
        if problem:
            raise ServiceError(problem)
        if not lay.python_exe.is_file():
            raise ServiceError(f"Missing (run Setup again to repair the install): {lay.python_exe}")
        # An update keeps the stored port: Setup passes BAIHE_API_PORT on
        # every run, and a variable set (or left) for the launcher must not
        # move a service whose port set-port chose. Only a fresh install, or
        # a config from before the port was stored, takes --port; it is
        # checked only then.
        if api_port is not None and self._stored_port_value() is not None:
            self.run.log(f"Kept the stored port {self.api_port()}; use set-port to change it "
                         "(--port is used only on a fresh install).")
            api_port = None
        try:
            port_to_use = self._chosen_api_port(api_port)
        except ConfigRefused:
            self._start_again()
            raise
        # Setup has already stopped the services, so "was on" is also "set to
        # start with Windows".
        caddy_was_running = (query_state(CADDY_SERVICE, self.run) == "RUNNING"
                             or query_start_type(CADDY_SERVICE, self.run) == "AUTO_START")
        old_household = self.remote_state().get("household_port", 0)
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        swapped = self._swap_in_admin_files(port_to_use)
        created = []
        try:
            missing = [str(p) for p in (lay.wrapper(APP_SERVICE), lay.wrapper(CADDY_SERVICE),
                                        lay.caddy_exe, lay.template, lay.helper_python,
                                        lay.helper_script) if not p.is_file()]
            if missing:
                raise ServiceError("Setup's files are incomplete: " + ", ".join(missing))
            port = self.remote_state().get("household_port", 0)
            config, note = None, ""
            if port:
                try:
                    config = self._checked_config(port)
                except ServiceError as e:
                    note = f" Remote access was turned off: {e}"
                    port = 0
            self._write_app_xml(port)
            lay.wrapper_xml(CADDY_SERVICE).write_text(caddy_service_xml(lay), encoding="utf-8")
            # Caddy stays disabled here; it is set to start with Windows only
            # once the household listener has answered.
            for name, start in ((APP_SERVICE, "auto"), (CADDY_SERVICE, "disabled")):
                if self._create_service(name, start):
                    created.append(name)
            self._grant()
            if not config:
                self._write_state(0)
            self._start_and_wait()
            if config and not self._household_up(config):
                note = (" Remote access was turned off: Baihe Studio's household listener "
                        "didn't start (its log is in library\\logs\\service in the data folder).")
                port = 0
                self._write_state(0)
                self._write_app_xml(0)
                self._restart_app()
            elif config:
                lay.caddyfile.write_text(config["caddyfile"], encoding="utf-8")
                _check(self.run([SC, "config", CADDY_SERVICE, "start=", "auto"]),
                       f"Setting the {CADDY_SERVICE} service's start")
                start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        except Exception:
            self._roll_back(swapped, created, caddy_was_running, old_household)
            raise
        self._discard(swapped)
        where = f" on http://127.0.0.1:{port_to_use}"
        if port:
            return f"Baihe Studio's service is running{where}; remote access is on." + note
        return f"Baihe Studio's service is running{where} and starts with Windows." + note

    def _roll_back(self, swapped, created: list, caddy_was_running: bool,
                   old_household: int = 0) -> None:
        for name in (CADDY_SERVICE, APP_SERVICE):
            try:
                stop_service(name, self.run, sleep=self.sleep)
            except Exception:
                pass
        for name in (CADDY_SERVICE, APP_SERVICE):
            if name in created:
                try:
                    delete_service(name, self.run, sleep=self.sleep)
                except Exception:
                    pass
        self._restore(swapped)
        try:
            self._write_state(old_household)
        except Exception:
            pass
        for name in (APP_SERVICE, CADDY_SERVICE):
            if name not in created and (name == APP_SERVICE or caddy_was_running):
                try:
                    if name == CADDY_SERVICE:
                        # This run set it disabled until the household
                        # listener answered; it was on before.
                        self.run([SC, "config", name, "start=", "auto"])
                    start_service(name, self.run, sleep=self.sleep)
                except Exception:
                    pass

    def set_port(self, requested) -> str:
        """Moves the installed service to port `requested`: stored in the
        admin-only config.json and written into the service definition, then
        the service is restarted (Caddy, which depends on it, stopped first
        and started after if remote access is on) and /api/health is checked
        on the new port. A port that can't be used is refused before anything
        changes; if the new port doesn't answer, or the command is
        interrupted, the old config, definition and services are put back.
        If remote access is on and the household listener doesn't come back,
        remote access is turned off, as an update does."""
        lay = self.layout
        if query_state(APP_SERVICE, self.run) is None:
            raise ConfigRefused(
                "Baihe Studio isn't installed as a service, so there is no service port to "
                f"change. The Start-menu launcher uses BAIHE_API_PORT, or {ADMIN_PORT} when it "
                "isn't set. Nothing was changed.")
        current = self.api_port()
        port = self._chosen_api_port(requested)
        if port == current:
            return f"Baihe Studio's service already uses port {port}. Nothing was changed."
        # With the service stopped, the launcher may be running its own
        # server on the service's port and data folder: moving the service
        # now would start a second server on the same library.
        if query_state(APP_SERVICE, self.run) == "STOPPED" and self.health(current):
            raise ConfigRefused(
                f"Baihe Studio is running on port {current} without the service (started from "
                "the Start menu). Stop it with \"Stop Baihe Studio\" first. Nothing was changed: "
                f"the service keeps port {current}.")
        old_config = lay.config_file.read_text(encoding="utf-8")
        try:
            config = json.loads(old_config)
        except ValueError:
            config = None
        if not isinstance(config, dict):
            raise ServiceError(f"{lay.config_file} is unreadable; run Setup again.")
        xml = lay.wrapper_xml(APP_SERVICE)
        old_xml = xml.read_text(encoding="utf-8")
        household = self.remote_state().get("household_port", 0)
        # Remote access is on if Caddy runs or is set to start with Windows
        # (stopped by hand, or crashed), as install decides it.
        caddy_was_on = (query_state(CADDY_SERVICE, self.run) == "RUNNING"
                        or query_start_type(CADDY_SERVICE, self.run) == "AUTO_START")
        old_caddyfile = (lay.caddyfile.read_text(encoding="utf-8")
                         if caddy_was_on and lay.caddyfile.is_file() else None)
        note = ""
        try:
            _replace_text(lay.config_file, json.dumps(dict(config, api_port=port), indent=2) + "\n")
            self._write_app_xml(household)
            stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            self._restart_app()
            if caddy_was_on:
                remote = None
                if household:
                    try:
                        remote = self._checked_config(household)
                    except ServiceError as e:
                        note = f" Remote access was turned off: {e}"
                if remote and self._household_up(remote):
                    start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
                else:
                    note = note or (" Remote access was turned off: Baihe Studio's household "
                                    "listener didn't start (its log is in library\\logs\\service "
                                    "in the data folder).")
                    self._turn_off_caddy()
                    self._write_state(0)
                    self._write_app_xml(0)
                    self._restart_app()
        # BaseException: a Ctrl+C in the health wait must still put the old
        # port back, or the stored port and the service definition disagree
        # with a stopped service.
        except BaseException as failure:
            for name in (CADDY_SERVICE, APP_SERVICE):
                try:
                    stop_service(name, self.run, sleep=self.sleep)
                except Exception:
                    pass
            try:
                _replace_text(lay.config_file, old_config)
                _replace_text(xml, old_xml)
                self._write_state(household)
                if old_caddyfile is not None:
                    lay.caddyfile.write_text(old_caddyfile, encoding="utf-8")
                self._start_and_wait()
                if caddy_was_on:
                    _check(self.run([SC, "config", CADDY_SERVICE, "start=", "auto"]),
                           f"Setting the {CADDY_SERVICE} service's start")
                    start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            except Exception as undo:
                if not isinstance(failure, Exception):
                    self.run.log(f"putting port {current} back after an interruption failed: {undo}")
                    raise failure
                raise ServiceError(f"{failure} Putting port {current} back also failed: {undo} "
                                   "Run Setup again to repair the service.") from failure
            if not isinstance(failure, Exception):
                raise
            raise ServiceError(f"{failure} The service is back on port {current}.") from failure
        message = (f"Baihe Studio's service now runs on http://127.0.0.1:{port}; the Start-menu "
                   "launcher uses this port too.")
        if caddy_was_on and not note:
            message += " Remote access is still on."
        return message + note

    def stop(self) -> str:
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        return "Stopped Baihe Studio's services."

    def uninstall(self) -> str:
        """Stops and removes both services, takes BaiheStudio off the
        per-user folders, and removes the admin folder (Caddy's certificates
        and logs with it). If a service can't be stopped, nothing is
        changed; if the app's can't be removed, it is started again. The
        data folder's contents are left alone. A firewall rule the owner
        added is reported, not removed: it names caddy.exe, which is gone."""
        lay = self.layout
        # Checked before anything changes, so a link is never found halfway.
        for folder in (lay.root, lay.data):
            refuse_reparse_point(folder)
        rule = self._rule_present()
        was_running = query_state(APP_SERVICE, self.run) == "RUNNING"
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        try:
            delete_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            delete_service(APP_SERVICE, self.run, sleep=self.sleep)
        except ServiceError:
            if was_running:
                try:
                    start_service(APP_SERVICE, self.run, sleep=self.sleep)
                except ServiceError:
                    pass
            raise
        problems = []
        for cmd, timeout in revoke_commands(lay):
            if Path(cmd[1]).exists():
                result = self.run(cmd, timeout=timeout)
                if result.returncode != 0:
                    problems.append(f"taking the service off {cmd[1]} (exit code {result.returncode})")
        left = []
        if lay.admin.exists():
            # Caddy can write to its two folders, so they go first, with
            # rmtree, which removes a junction without following it; the
            # walk in remove_admin_folder never goes where they pointed.
            for folder in (lay.caddy_storage, lay.caddy_logs):
                shutil.rmtree(folder, ignore_errors=True)
            left = [str(f) for f in (lay.caddy_storage, lay.caddy_logs) if os.path.lexists(f)]
            if not left:
                remove_admin_folder(lay.admin)
        errors = []
        if problems:
            errors.append("The services are removed, but " + " and ".join(problems)
                          + " failed; its permission is still on that folder.")
        if left:
            errors.append(" and ".join(left) + f" couldn't be removed, so {lay.admin} is left "
                          "in place; run uninstall again.")
        if errors:
            raise ServiceError(" ".join(errors))
        message = "Removed Baihe Studio's services."
        if rule:
            message += (f" The firewall rule '{FIREWALL_RULE_NAME}' is still there; remove it with:"
                        f"\n  {firewall_delete_command_text()}")
        return message

    def enable_remote(self, household_port: int = DEFAULT_HOUSEHOLD_PORT) -> str:
        """The owner's opt-in: the household listener in Baihe Studio's
        service (127.0.0.1 only), the Caddyfile and the Caddy service. Checks
        everything first and changes nothing if remote access isn't
        configured or the port is taken; undoes its changes if a step fails.
        It adds no firewall rule: it says which one the owner adds."""
        lay = self.layout
        if query_state(APP_SERVICE, self.run) is None:
            raise ServiceError("Baihe Studio isn't installed as a service. Run Setup again with "
                               "\"Run Baihe Studio in the background\" ticked.")
        missing = [str(p) for p in (lay.caddy_exe, lay.wrapper(CADDY_SERVICE), lay.template)
                   if not p.is_file()]
        if missing:
            raise ServiceError("Missing (run Setup again to repair the install): "
                               + ", ".join(missing))
        config = self._checked_config(household_port)
        port = config["household_port"]
        if self.remote_state().get("household_port") != port and self.port_check(port):
            raise ConfigRefused(f"Port {port} on this PC is already in use by another program; "
                                "choose another with --household-port. Nothing was changed.")
        try:
            self._write_state(port)
            self._write_app_xml(port)
            # Caddy depends on Baihe's service, so a running Caddy (remote
            # access already on) must stop before the restart.
            stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            self._restart_app()
            if not self._household_up(config):
                raise ServiceError(f"Baihe Studio's household listener doesn't answer on "
                                   f"127.0.0.1:{port} (or what answers there isn't Baihe "
                                   "Studio). Its log is in library\\logs\\service in the data "
                                   "folder.")
            lay.caddyfile.write_text(config["caddyfile"], encoding="utf-8")
            lay.wrapper_xml(CADDY_SERVICE).write_text(caddy_service_xml(lay), encoding="utf-8")
            self._create_service(CADDY_SERVICE, "auto")
            self._grant(caddy_only=True)
            start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            if not wait_until(lambda: self.port_check(HTTPS_PORT), CADDY_WAIT_SECONDS,
                              sleep=self.sleep):
                raise ServiceError("Caddy started but doesn't answer on port 443 (is another "
                                   "program using it?). Its log is in caddy-logs in "
                                   f"%ProgramFiles%\\{ADMIN_FOLDER_NAME}.")
        except Exception as failure:
            try:
                self.disable_remote()
            except Exception as undo:
                raise ServiceError(f"{failure} Undoing it also failed: {undo} Run "
                                   "disable-remote again.") from failure
            raise
        return (f"Remote access is on for {config['domain']}: Caddy is running and forwards to "
                f"Baihe's household listener on 127.0.0.1:{port}. Caddy will ask for its "
                "certificate once your name points here and port 443 reaches this PC.\n"
                + remote_reminder(lay, self._rule_present()))

    def disable_remote(self) -> str:
        """Turns household access off: Caddy disabled, then stopped, and
        Baihe Studio's service restarted without the household listener.
        Reports a failure after doing every step it can."""
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
        message = "Remote access is off: Caddy is stopped and disabled."
        if self._rule_present():
            message += (f" The firewall rule '{FIREWALL_RULE_NAME}' is still there; remove it with:"
                        f"\n  {firewall_delete_command_text()}")
        return message

    def status(self) -> str:
        lines = []
        for name in (APP_SERVICE, CADDY_SERVICE):
            state = query_state(name, self.run)
            start = query_start_type(name, self.run) if state else None
            lines.append(f"{name}: {state or 'not installed'}"
                         + (f", start {start}" if start else ""))
        port = self.remote_state().get("household_port")
        lines.append(f"Remote access: {'on, household port ' + str(port) if port else 'off'}")
        lines.append(f"Firewall rule '{FIREWALL_RULE_NAME}': "
                     + ("present" if self._rule_present() else "none (this script never adds it)"))
        api_port = self.api_port()
        origin = (f"stored in {self.layout.config_file}" if self._stored_port_value() == api_port
                  else "the default; no port is stored")
        lines += ["Ports Baihe Studio uses on this PC:",
                  f"  Baihe Studio's port: http://127.0.0.1:{api_port} ({origin})"]
        if port:
            lines += [f"  Household listener: 127.0.0.1:{port} (remote access; Caddy forwards to it)",
                      f"  HTTPS: {HTTPS_PORT} (Caddy, remote access)"]
        lines += [f"  Browser-extension bridge: 127.0.0.1:{EXTENSION_BRIDGE_PORT}",
                  "Change the service's port with \"Baihe Studio service\" in the Start menu (or "
                  "set-port N in an administrator prompt). Changing or deleting BAIHE_API_PORT, "
                  "or running Setup again with it set, doesn't change it."]
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
        if _norm(APP_DIR.parent.parent) != _norm(admin):
            raise ServiceError(f"This copy isn't in {admin}; run Setup again.")
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
    if not running_from_admin:
        # So a failed first install leaves the log Setup points the user at.
        refuse_reparse_point(admin)
        admin.mkdir(exist_ok=True)
    source = None if running_from_admin else APP_DIR.parent.parent
    return Services(layout, Runner(layout.log_file), source=source)


# What `--help` lists (and docs/windows-installer-design.md, "Commands").
COMMANDS_HELP = r"""The Start-menu item "Baihe Studio service" runs these for you. By hand, in an
administrator prompt (status needs no administrator rights):

  "%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S
      "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" COMMAND

commands:
  status                    both services, every port Baihe uses, remote access, the firewall rule
  set-port N                move the service to port N (put back if N doesn't answer)
  enable-remote [--household-port N]
                            household access through Caddy (sign-in settings in .env first)
  disable-remote            household access off: Caddy stopped and disabled
  stop                      stop both services
  install [--port N]        (Setup) create or refresh the service and start it; --port
                            only on a fresh install (an update keeps the stored port)
  uninstall                 (the uninstaller) remove both services and the admin folder
"""


def main(argv=None, services=None, admin=None) -> int:
    parser = argparse.ArgumentParser(description="Baihe Studio's Windows service.",
                                     epilog=COMMANDS_HELP,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install-root", help="the per-user install folder (Setup)")
    parser.add_argument("--data-dir", help="the data folder (Setup)")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install", help="create or refresh both services and start Baihe Studio's")
    # Checked by install only when it will be used (a bad value is then
    # refused with exit code 2, nothing changed), not by argparse, so the
    # services Setup stopped start again and an update ignores it.
    install.add_argument("--port", help="a fresh install's port (an update keeps the stored one)")
    set_port = sub.add_parser("set-port", help="move the installed service to another port")
    # Checked by set_port (refused with exit code 2, nothing changed), like --port.
    set_port.add_argument("port", help="the new port, 1024 to 65535")
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
        if args.command == "enable-remote":
            message = services.enable_remote(args.household_port)
        elif args.command == "install":
            message = services.install(args.port)
        elif args.command == "set-port":
            message = services.set_port(args.port)
        else:
            message = getattr(services, args.command.replace("-", "_"))()
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
