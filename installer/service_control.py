"""Running Windows' own tools for the service (installer/service.py): the
sc.exe, icacls and netsh command lines, the runner that logs and limits
them, and the service state helpers built on them. Nothing here adds a
firewall rule: netsh is only ever asked to show one.

Standard library only: this runs elevated from the admin folder.
"""

import hashlib
import re
import struct
import subprocess
import time
from pathlib import Path

from installer.service_config import HTTPS_PORT
from installer.service_layout import APP_SERVICE, CADDY_SERVICE, Layout, ServiceError, _system32

# The rule the owner adds by hand (firewall_rule_command). This script only
# reads it, never adds it.
FIREWALL_RULE_NAME = "Baihe Studio remote access - Caddy HTTPS"
FIREWALL_PROFILES = "private,domain"


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


STATE_WAIT_SECONDS = 60


COMMAND_TIMEOUT = 120
# The inheritable grant on the data folder is applied to everything already
# in it (a library with media and a model cache can be large).
DATA_GRANT_TIMEOUT = 3600
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"


_STATES = ("STOPPED", "START_PENDING", "STOP_PENDING", "RUNNING", "CONTINUE_PENDING",
           "PAUSE_PENDING", "PAUSED")
_START_TYPES = ("BOOT_START", "SYSTEM_START", "AUTO_START", "DEMAND_START", "DISABLED")
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_NOT_ACTIVE = 1062


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


SC = _system32("sc.exe")
ICACLS = _system32("icacls.exe")
NETSH = _system32("netsh.exe")      # only ever `show rule`: see firewall_show_command
CMD = _system32("cmd.exe")


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
