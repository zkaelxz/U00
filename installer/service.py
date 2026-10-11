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

Everything but `status` needs an administrator prompt. set-port,
enable-remote and disable-remote hold Setup's own mutex (SetupMutex in
installer/baihe.iss) while they run, so Setup won't start until they finish,
and each is refused (exit code 2, nothing changed) while Setup or another of
them holds it. install, stop and uninstall don't take it: Setup runs them
while it holds it.

Nothing here adds a
Windows Firewall rule or does anything on the router or with DNS: Windows
blocks inbound connections to Caddy until the owner adds the rule printed by
`enable-remote` and `status` (firewall_rule_command), forwarding port 443 on
the router is the owner's own step, and so is the domain name
(docs/household-access.md).

Standard library only.
"""

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from installer.service_checks import health_ok, household_is_baihe, port_answers, wait_until
from installer.service_config import (
    ADMIN_PORT,
    DEFAULT_HOUSEHOLD_PORT,
    EXTENSION_BRIDGE_PORT,
    HTTPS_PORT,
    _replace_text,
    api_port_problem,
    app_service_xml,
    caddy_service_xml,
    read_config,
    read_env_file,
    remote_access_config,
    render_caddyfile,
    stored_api_port,
)
from installer.service_control import (
    FIREWALL_RULE_NAME,
    SC,
    _check,
    configure_service_commands,
    create_service_command,
    delete_service,
    firewall_delete_command_text,
    firewall_show_command,
    grant_commands,
    query_start_type,
    query_state,
    remote_reminder,
    revoke_commands,
    start_service,
    stop_service,
)
from installer.service_layout import (
    ADMIN_FOLDER_NAME,
    APP_DISPLAY_NAME,
    APP_SERVICE,
    CADDY_DISPLAY_NAME,
    CADDY_SERVICE,
    ConfigRefused,
    Layout,
    ServiceError,
    _norm,
    data_folder_contents_problem,
    refuse_reparse_point,
    remove_admin_folder,
)


CADDY_WAIT_SECONDS = 30
# The household listener starts after the PC one; a bad setting only skips it
# (the PC listener still starts), so it is checked on its own.
HOUSEHOLD_WAIT_SECONDS = 30
HEALTH_WAIT_SECONDS = 180      # a first start compiles every .pyc


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


if __name__ == "__main__":
    # Imported here: service_cli imports Services from this module.
    from installer.service_cli import main

    sys.exit(main())
