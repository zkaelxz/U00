"""What the service runs with and the text generated from it
(installer/service.py): the config and .env files, the WinSW XML, the port
rules and the Caddyfile.

Standard library only: this runs elevated from the admin folder.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from installer.service_layout import (
    APP_DISPLAY_NAME,
    APP_SERVICE,
    CADDY_DISPLAY_NAME,
    CADDY_SERVICE,
    ConfigRefused,
    Layout,
    ServiceError,
)

ADMIN_PORT = 8600              # api_config.DEFAULT_PORT
# The PC listener's own ports in RESERVED_PORTS: the service may take them,
# the household listener may not.
PC_LISTENER_PORTS = (ADMIN_PORT, ADMIN_PORT + 1)
# Fixed ports the household listener may not take: the PC listener's, the
# extension bridge's (page_server.DEFAULT_PORT) and 8601, the documented
# "pick another port". Static: settings_service.baihe_own_ports() differs.
EXTENSION_BRIDGE_PORT = 8756    # page_server.DEFAULT_PORT
RESERVED_PORTS = (ADMIN_PORT, ADMIN_PORT + 1, EXTENSION_BRIDGE_PORT)
DEFAULT_HOUSEHOLD_PORT = 8610
HTTPS_PORT = 443


LOG_ROLL_KB = 10240
LOG_KEEP_FILES = 5
APP_STOP_TIMEOUT = "15 sec"    # the server gives jobs 6 s, uvicorn up to 3 s more
CADDY_STOP_TIMEOUT = "10 sec"


TEMPLATE_LOG_TOKEN = "{$BAIHE_CADDY_LOG_DIR}/baihe-access.log"
GENERATED_HEADER = ("# Written by installer/service.py from deploy/caddy/Caddyfile.template.\n"
                    "# Don't edit it: enable-remote and every install write it again.\n")


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
    """The data folder's .env, parsed like settings_service.read_env_file
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
                "BAIHE_APPLY_PENDING": "1", "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"})
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
    Baihe's other ports (the extension bridge's), nor the
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
