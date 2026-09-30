"""
installer/service.py -- runs the installed Baihe Studio as Windows services
(docs/windows-installer-design.md, "Boot services").

Two services, each wrapped by WinSW (a copy of the same pinned binary,
named after the service and reading the .xml next to it):

- BaiheStudio (service\\BaiheStudio.exe): `python -s -m api` on
  127.0.0.1:8600, started at boot, restarted after a failure, stopped with
  Ctrl+C so the server runs its clean stop (running jobs are cancelled).
- BaiheCaddy (caddy\\BaiheCaddy.exe): the bundled Caddy for household
  access. Installed disabled. Only `enable-remote` starts it, and only
  when remote access is configured (the same checks `python -m api` runs
  before it opens the household listener).

Each runs as its own virtual account (NT SERVICE\\<name>), never as
LocalSystem, with the privilege list cut down to what it needs.

    service.py install                  create or refresh both (Setup)
    service.py uninstall [--purge-caddy-data]
                                        stop and remove both, the firewall
                                        rule and the accounts' permissions
                                        (the uninstaller)
    service.py stop                     stop both (Setup, before an update)
    service.py enable-remote [--household-port 8610]
    service.py disable-remote
    service.py status

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

import portable  # noqa: E402 -- needs APP_DIR on sys.path first

APP_SERVICE = "BaiheStudio"
CADDY_SERVICE = "BaiheCaddy"
ADMIN_PORT = 8600
EXTENSION_PORT = 8756          # page_server.DEFAULT_PORT
DEFAULT_HOUSEHOLD_PORT = 8610
HTTP_PORT = 80                 # Caddy's redirect listener; no firewall rule opens it
HTTPS_PORT = 443
FIREWALL_RULE_NAME = "Baihe Studio remote access - Caddy HTTPS"
FIREWALL_PROFILES = "private,domain"
STATE_FILE_NAME = "remote-access.json"
LOG_FILE_NAME = "service.log"
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
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
TEMPLATE_LOG_TOKEN = "{$BAIHE_CADDY_LOG_DIR}/baihe-access.log"
GENERATED_HEADER = ("# Written by installer/service.py from deploy/caddy/Caddyfile.template.\n"
                    "# Don't edit it: enable-remote and every install write it again.\n")

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


def _system32(exe: str) -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", exe)


SC = _system32("sc.exe")
ICACLS = _system32("icacls.exe")
NETSH = _system32("netsh.exe")


class Layout:
    """Where everything is, from the install folder and the data folder."""

    def __init__(self, install_root, data_dir):
        root = Path(install_root)
        data = Path(data_dir)
        self.root = root
        self.app = root / "app"
        self.python_exe = root / "python" / "python.exe"
        self.site_packages = root / "python" / "Lib" / "site-packages"
        self.scripts = root / "python" / "Scripts"
        self.service_dir = root / "service"
        self.caddy_dir = root / "caddy"
        self.caddy_exe = self.caddy_dir / "caddy.exe"
        self.caddyfile = self.caddy_dir / "Caddyfile"
        self.state_file = self.service_dir / STATE_FILE_NAME
        self.template = self.app / "deploy" / "caddy" / "Caddyfile.template"
        self.data = data
        self.env_file = data / ".env"
        self.app_logs = data / "library" / "logs" / "service"
        self.caddy_home = data / "caddy"
        self.caddy_storage = self.caddy_home / "data"
        self.caddy_logs = self.caddy_home / "logs"
        self.log_file = data / "launcher" / LOG_FILE_NAME

    def wrapper(self, name: str) -> Path:
        return (self.service_dir if name == APP_SERVICE else self.caddy_dir) / f"{name}.exe"

    def wrapper_xml(self, name: str) -> Path:
        return self.wrapper(name).with_suffix(".xml")


def default_layout() -> Layout:
    return Layout(APP_DIR.parent, portable.data_dir())


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


def app_service_env(household_port: int = 0) -> dict:
    """The server's environment, as the installed launcher sets it:
    loopback only (forced), port 8600, the PC-only key form on, no user
    site-packages. The household listener only while remote access is on."""
    env = {"BAIHE_API_HOST": "127.0.0.1", "BAIHE_API_PORT": str(ADMIN_PORT),
           "BAIHE_API_ALLOW_KEY_WRITES": "1", "PYTHONNOUSERSITE": "1",
           "PYTHONUNBUFFERED": "1"}
    if household_port:
        env["BAIHE_API_HOUSEHOLD_PORT"] = str(int(household_port))
    return env


def app_service_xml(layout: Layout, household_port: int = 0) -> str:
    fields = [
        ("id", APP_SERVICE),
        ("name", "Baihe Studio"),
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
    return _winsw_xml(fields, app_service_env(household_port), layout.app_logs)


def caddy_service_xml(layout: Layout) -> str:
    # Caddy keeps its certificates and ACME account key under
    # XDG_DATA_HOME, a folder only it and administrators can open.
    fields = [
        ("id", CADDY_SERVICE),
        ("name", "Baihe Studio remote access (Caddy)"),
        ("description", "HTTPS for household devices, forwarding to Baihe Studio's "
                        "household listener. Off unless remote access is enabled."),
        ("executable", str(layout.caddy_exe)),
        ("arguments", f'run --config "{layout.caddyfile}" --adapter caddyfile'),
        ("workingdirectory", str(layout.caddy_home)),
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
    file: variables set with setx live in one user's environment."""
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
    if (not isinstance(household_port, int) or not 1 <= household_port <= 65535
            or household_port in (ADMIN_PORT, EXTENSION_PORT, HTTP_PORT, HTTPS_PORT)):
        raise ConfigRefused("The household port must be a free port other than 8600, 8756, "
                            "80 or 443 (Caddy's own ports).")
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
    codes only: nothing here ever carries a secret)."""

    def __init__(self, log_path=None):
        self.log_path = log_path

    def log(self, text: str) -> None:
        if not self.log_path:
            return
        try:
            Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def __call__(self, cmd, timeout: float = 120):
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
        raise ServiceError(f"The {name} service didn't start. Its log is in the data folder "
                           "(library\\logs\\service or caddy\\logs).")


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


def configure_service_commands(name: str, start: str) -> list:
    """sc.exe settings applied on every install, whatever WinSW set:
    the start type, its own virtual account, a cut-down privilege list,
    and restart after a failure (also when it exits with an error)."""
    return [
        [SC, "config", name, "start=", start],
        [SC, "sidtype", name, "unrestricted"],
        [SC, "config", name, "obj=", service_account(name)],
        [SC, "privs", name, "/".join(SERVICE_PRIVILEGES)],
        [SC, "failure", name, "reset=", str(FAILURE_RESET_SECONDS), "actions=", FAILURE_ACTIONS],
        [SC, "failureflag", name, "1"],
    ]


def grant_commands(layout: Layout) -> list:
    """icacls grants, by SID. BaiheStudio: read and run the program folder,
    change the bundled Python's packages (Diagnostics' Install buttons run
    pip inside the server) and the data folder. BaiheCaddy: read and run
    its own program folder and change its own folder in the data folder;
    nothing else there (no .env). Caddy's certificates and ACME key sit in
    caddy\\data, limited to Caddy, SYSTEM and Administrators."""
    app_sid = "*" + service_sid(APP_SERVICE)
    caddy_sid = "*" + service_sid(CADDY_SERVICE)
    return [
        [ICACLS, layout.root, "/grant", f"{app_sid}:(OI)(CI)RX"],
        [ICACLS, layout.site_packages, "/grant", f"{app_sid}:(OI)(CI)M"],
        [ICACLS, layout.scripts, "/grant", f"{app_sid}:(OI)(CI)M"],
        [ICACLS, layout.data, "/grant", f"{app_sid}:(OI)(CI)M"],
        [ICACLS, layout.caddy_dir, "/grant", f"{caddy_sid}:(OI)(CI)RX"],
        [ICACLS, layout.caddy_home, "/grant", f"{caddy_sid}:(OI)(CI)M"],
        [ICACLS, layout.caddy_storage, "/inheritance:r",
         "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F",
         "/grant:r", f"*{ADMINISTRATORS_SID}:(OI)(CI)F",
         "/grant:r", f"{caddy_sid}:(OI)(CI)M"],
    ]


def revoke_commands(layout: Layout, purge_caddy_data: bool) -> list:
    """Takes the two accounts back out of every folder they were granted.
    Caddy's folder goes back to the data folder's own permissions (so its
    owner can open it again), unless it's being deleted."""
    cmds = []
    for sid in (service_sid(APP_SERVICE), service_sid(CADDY_SERVICE)):
        for path in (layout.root, layout.site_packages, layout.scripts, layout.data,
                     layout.caddy_dir):
            cmds.append([ICACLS, path, "/remove:g", f"*{sid}"])
    if not purge_caddy_data:
        cmds.append([ICACLS, layout.caddy_home, "/reset", "/T", "/C"])
    return cmds


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


# ---------------------------------------------------------- operations

class Services:
    """The commands, against a layout, a command runner and checks that
    tests replace."""

    def __init__(self, layout: Layout, run, health=health_ok, port_check=port_answers,
                 sleep=time.sleep):
        self.layout, self.run = layout, run
        self.health, self.port_check, self.sleep = health, port_check, sleep

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
        self.layout.wrapper_xml(APP_SERVICE).write_text(
            app_service_xml(self.layout, household_port), encoding="utf-8")

    def _check_files(self, names) -> None:
        missing = [str(p) for p in names if not Path(p).is_file()]
        if missing:
            raise ServiceError("Missing from the install (run Setup again to repair it): "
                               + ", ".join(missing))

    def _create_service(self, name: str, start: str) -> None:
        if query_state(name, self.run) is None:
            _check(self.run([self.layout.wrapper(name), "install"]),
                   f"Creating the {name} service")
        for cmd in configure_service_commands(name, start):
            _check(self.run(cmd), f"Configuring the {name} service")

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

    # -- commands

    def install(self) -> str:
        """Creates both services, or refreshes them after an update, and
        starts Baihe Studio's. Caddy stays disabled unless remote access
        was enabled before and is still configured."""
        lay = self.layout
        self._check_files([lay.python_exe, lay.wrapper(APP_SERVICE), lay.wrapper(CADDY_SERVICE),
                           lay.caddy_exe, lay.template])
        for folder in (lay.scripts, lay.app_logs, lay.caddy_logs, lay.caddy_storage):
            folder.mkdir(parents=True, exist_ok=True)
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
        for cmd in grant_commands(lay):
            _check(self.run(cmd), "Setting folder permissions")
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
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        if query_state(CADDY_SERVICE, self.run) is not None:
            _check(self.run([SC, "config", CADDY_SERVICE, "start=", "disabled"]),
                   "Disabling the Caddy service")
        remove_firewall_rule(self.run)
        self.layout.caddyfile.unlink(missing_ok=True)

    def stop(self) -> str:
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        return "Stopped Baihe Studio's services."

    def uninstall(self, purge_caddy_data: bool = False) -> str:
        """Stops and removes both services, the firewall rule, the two
        accounts' permissions and the generated files. User data is kept,
        except Caddy's own folder with --purge-caddy-data (a clean
        uninstall): only administrators and the Caddy service can open its
        certificates, so the uninstaller itself couldn't delete them."""
        lay = self.layout
        stop_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        stop_service(APP_SERVICE, self.run, sleep=self.sleep)
        remove_firewall_rule(self.run)
        delete_service(CADDY_SERVICE, self.run, sleep=self.sleep)
        delete_service(APP_SERVICE, self.run, sleep=self.sleep)
        for cmd in revoke_commands(lay, purge_caddy_data):
            if Path(cmd[1]).exists():
                self.run(cmd)
        if purge_caddy_data and lay.caddy_home.exists():
            shutil.rmtree(lay.caddy_home, ignore_errors=True)
        for path in (lay.wrapper_xml(APP_SERVICE), lay.wrapper_xml(CADDY_SERVICE),
                     lay.caddyfile, lay.state_file):
            path.unlink(missing_ok=True)
        return "Removed Baihe Studio's services and the remote-access firewall rule."

    def enable_remote(self, household_port: int = DEFAULT_HOUSEHOLD_PORT) -> str:
        """Turns household access on: the household listener in Baihe
        Studio's service, the Caddyfile, the firewall rule and the Caddy
        service. Checks everything first and changes nothing if remote
        access isn't configured; undoes its changes if a step fails."""
        lay = self.layout
        if query_state(APP_SERVICE, self.run) is None:
            raise ServiceError("Baihe Studio isn't installed as a service. Run Setup again with "
                               "\"Run Baihe Studio in the background\" ticked.")
        self._check_files([lay.caddy_exe, lay.wrapper(CADDY_SERVICE), lay.template])
        config = self._checked_config(household_port)
        try:
            self._write_state(config["household_port"])
            self._write_app_xml(config["household_port"])
            self._restart_app()
            if not wait_until(lambda: self.port_check(config["household_port"]), 30,
                              sleep=self.sleep):
                raise ServiceError("The household listener didn't open on "
                                   f"127.0.0.1:{config['household_port']}.")
            lay.caddyfile.write_text(config["caddyfile"], encoding="utf-8")
            lay.wrapper_xml(CADDY_SERVICE).write_text(caddy_service_xml(lay), encoding="utf-8")
            for folder in (lay.caddy_logs, lay.caddy_storage):
                folder.mkdir(parents=True, exist_ok=True)
            self._create_service(CADDY_SERVICE, "auto")
            for cmd in grant_commands(lay):
                _check(self.run(cmd), "Setting folder permissions")
            remove_firewall_rule(self.run)
            _check(self.run(firewall_add_command(lay)), "Adding the firewall rule for port 443")
            start_service(CADDY_SERVICE, self.run, sleep=self.sleep)
            if not wait_until(lambda: self.port_check(HTTPS_PORT), CADDY_WAIT_SECONDS,
                              sleep=self.sleep):
                raise ServiceError("Caddy started but doesn't answer on port 443 (is another "
                                   "program using it?). Its log is in caddy\\logs in the data folder.")
        except Exception:
            try:
                self.disable_remote()
            except Exception as undo:
                self.run.log(f"Undoing enable-remote also failed: {undo}")
            raise
        return (f"Remote access is on for {config['domain']}: Caddy answers on port 443 "
                "(firewall: private and domain networks only). Nothing was changed on your "
                "router; forwarding port 443 there is your own step (docs/household-access.md).")

    def disable_remote(self) -> str:
        """Turns household access off: Caddy stopped and disabled, the
        firewall rule removed, Baihe Studio's service restarted without the
        household listener."""
        self._turn_off_caddy()
        self._write_state(0)
        if query_state(APP_SERVICE, self.run) is not None:
            self._write_app_xml(0)
            self._restart_app()
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


def main(argv=None, services=None, admin=None) -> int:
    parser = argparse.ArgumentParser(description="Baihe Studio's Windows services.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="create or refresh both services and start Baihe Studio's")
    uninstall = sub.add_parser("uninstall", help="stop and remove both services")
    uninstall.add_argument("--purge-caddy-data", action="store_true",
                           help="also delete Caddy's certificates and logs (clean uninstall)")
    sub.add_parser("stop", help="stop both services")
    enable = sub.add_parser("enable-remote", help="turn household access through Caddy on")
    enable.add_argument("--household-port", type=int, default=DEFAULT_HOUSEHOLD_PORT)
    sub.add_parser("disable-remote", help="turn household access off")
    sub.add_parser("status", help="show both services' state")
    args = parser.parse_args(argv)

    if services is None:
        if os.name != "nt":
            print("Baihe Studio's services exist only on Windows.", file=sys.stderr)
            return EXIT_FAILED
        if not portable.is_installed():
            print("This isn't an installed copy of Baihe Studio (no app\\INSTALLED). "
                  "Run Setup again to repair it.", file=sys.stderr)
            return EXIT_FAILED
        layout = default_layout()
        services = Services(layout, Runner(layout.log_file))
    if args.command != "status" and not (is_admin() if admin is None else admin):
        print("This needs administrator rights: run it from an administrator Command Prompt "
              "or PowerShell.", file=sys.stderr)
        return EXIT_NOT_ADMIN
    run = services.run
    if hasattr(run, "log"):
        run.log(f"service.py {args.command}")
    try:
        if args.command == "install":
            message = services.install()
        elif args.command == "uninstall":
            message = services.uninstall(purge_caddy_data=args.purge_caddy_data)
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
