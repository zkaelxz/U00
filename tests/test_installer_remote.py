"""Tests for the installer's remote-access side: Caddy on or off, enabling remote access, choosing and setting ports, and staying on loopback."""

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))

import service  # noqa: E402
from installer import service, service_cli, service_config, service_control, service_layout, setup_lock  # noqa: E402
from tests.test_installer_service import (  # noqa: F401  (layout and source are fixtures)
    SIGN_IN, TEMPLATE, FakeWindows, _services, _xml_env, layout, source,
)


class TestCaddyInstalledOff:
    def test_fresh_install_creates_caddy_disabled_and_stopped(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        caddy = win.services["BaiheCaddy"]
        assert caddy["state"] == "STOPPED" and caddy["start"] == "DISABLED"
        assert caddy["account"] == "NT SERVICE\\BaiheCaddy" and caddy["depend"] == "BaiheStudio"
        assert "SeImpersonatePrivilege" not in caddy["privs"][0].split("/")
        assert not layout.caddyfile.exists() and not layout.state_file.exists()
        assert layout.caddy_exe.read_text(encoding="utf-8") == "caddy"
        assert layout.template.is_file()

    def test_the_update_helper_is_a_demand_start_system_service_only_the_app_can_start(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        helper = win.services["BaiheUpdate"]
        assert helper["start"] == "DEMAND_START" and helper["state"] == "STOPPED"
        assert helper["account"] == "LocalSystem"       # it replaces files and stops the other service
        assert "failure" not in helper                  # a one-shot: never restarted after exiting
        sddl = helper["sdset"][0]
        app_sid = service_control.service_sid("BaiheStudio")
        # The app's account may query and start it (LC, RP, RC) and nothing else.
        assert f"(A;;LCRPRC;;;{app_sid})" in sddl
        assert sddl.count(app_sid) == 1
        xml = ET.parse(layout.wrapper_xml("BaiheUpdate")).getroot()
        assert xml.findtext("startmode") == "Manual"
        assert xml.findtext("executable") == str(layout.helper_python)
        assert xml.findtext("arguments").endswith("apply-app-update")
        assert xml.findtext("arguments").startswith("-I -S ")

    def test_the_update_helper_runs_only_from_the_admin_folder(self, layout, source):
        _services(layout, FakeWindows(), source).install()
        xml = ET.parse(layout.wrapper_xml("BaiheUpdate")).getroot()
        admin = str(layout.admin)
        assert xml.findtext("executable").startswith(admin)
        assert str(layout.helper_script) in xml.findtext("arguments")
        assert str(layout.root) not in ET.tostring(xml, encoding="unicode")

    def test_an_install_without_the_release_key_is_refused(self, layout, source):
        (source / "helper" / "lib" / "installer" / "release_public_key.txt").unlink()
        with pytest.raises(service_layout.ServiceError, match="incomplete"):
            _services(layout, FakeWindows(), source).install()

    def test_uninstall_removes_the_update_helper_too(self, layout, source):
        win = FakeWindows()
        svcs = _services(layout, win, source)
        svcs.install()
        svcs.uninstall()
        assert "BaiheUpdate" not in win.services

    def test_household_listener_stays_closed(self, layout, source):
        _services(layout, FakeWindows(), source).install()
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == "" and env["BAIHE_API_HOST"] == "127.0.0.1"

    def test_caddy_runs_as_its_own_account_with_private_folders(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        sid = "*" + service_control.service_sid("BaiheCaddy")
        grants = {c[1]: c[2:] for c in win.commands("icacls.exe")}
        assert grants[str(layout.caddy_storage)][0] == "/inheritance:r"
        assert f"{sid}:(OI)(CI)M" in grants[str(layout.caddy_storage)]
        assert f"{sid}:(OI)(CI)M" in grants[str(layout.caddy_logs)]
        assert grants[str(layout.caddy_dir)][:2] == ["/grant", f"{sid}:(OI)(CI)RX"]
        # No write access to Caddy's binary or the template.
        assert str(layout.helper) not in grants

    def test_a_failed_install_removes_both_services(self, layout, source):
        win = FakeWindows()
        with pytest.raises(service_layout.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False).install()
        assert win.services == {} and not layout.caddy_dir.exists()

    def test_uninstall_removes_caddy_and_its_certificates(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        (layout.caddy_storage / "certificates").mkdir(parents=True, exist_ok=True)
        svc.uninstall()
        assert win.services == {} and not layout.admin.exists()


class TestEnableRemote:
    @pytest.fixture
    def on(self, layout, source):
        win = FakeWindows()
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, win, source, port_check=lambda port: port == 443,
                        is_baihe=lambda port, domain: True)
        svc.install()
        return svc, win

    def test_refused_without_sign_in_settings_and_changes_nothing(self, layout, source, capsys):
        win = FakeWindows()
        svc = _services(layout, win, source, port_check=lambda p: False, is_baihe=lambda p, d: True)
        svc.install()
        calls = len(win.calls)
        xml = layout.wrapper_xml("BaiheStudio").read_text(encoding="utf-8")
        assert service_cli.main(["enable-remote"], services=svc, admin=True) == 2
        assert "BAIHE_GOOGLE_CLIENT_ID" in capsys.readouterr().err
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert layout.wrapper_xml("BaiheStudio").read_text(encoding="utf-8") == xml
        assert not layout.caddyfile.exists() and not layout.state_file.exists()
        assert [c for c in win.calls[calls:] if c[1] != "query"] == []

    @pytest.mark.parametrize("url,word", [
        ("http://baihe.example.com", "https://"),
        ("https://192.168.1.5", "domain"),
        ("https://localhost", "domain"),
        ("https://baihe.example.com:8443", "port"),
        # What the server's own normalize_public_url refuses, refused here
        # first: the server would start without the household listener.
        ("https://baihe.example.com/app", "just https://"),
        ("https://user@baihe.example.com", "just https://"),
        ("https://baihe.example.com/?x=1", "just https://"),
        ("https://b\u00fccher.example", "xn--"),
    ])
    def test_public_url_must_be_an_https_dns_name_on_443(self, layout, source, url, word):
        (layout.data / ".env").write_text(SIGN_IN.replace("https://baihe.example.com", url),
                                          encoding="utf-8")
        svc = _services(layout, FakeWindows(), source, port_check=lambda p: False)
        svc.install()
        with pytest.raises(service_layout.ConfigRefused, match=word):
            svc.enable_remote()

    @pytest.mark.parametrize("port", [80, 8600, 8601, 8756, 70000, 0])
    def test_household_port_rules(self, layout, source, port):
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, FakeWindows(), source, port_check=lambda p: False)
        svc.install()
        with pytest.raises(service_layout.ConfigRefused, match="household port"):
            svc.enable_remote(port)

    def test_a_port_in_use_is_refused(self, layout, source):
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, FakeWindows(), source, port_check=lambda p: True)
        svc.install()
        with pytest.raises(service_layout.ConfigRefused, match="already in use"):
            svc.enable_remote(8610)

    def test_enable_turns_on_caddy_and_the_loopback_household_listener(self, on, layout):
        svc, win = on
        message = svc.enable_remote(8610)
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == "8610" and env["BAIHE_API_HOST"] == "127.0.0.1"
        text = layout.caddyfile.read_text(encoding="utf-8")
        assert "baihe.example.com {" in text and "reverse_proxy 127.0.0.1:8610 " in text
        assert "respond @pc_only 404" in text and "admin off" in text
        assert "not-a-real-secret" not in text and "{$" not in text
        assert service_config.read_config(layout.config_file)
        assert svc.remote_state() == {"household_port": 8610}
        assert "baihe.example.com" in message

    def test_enable_again_while_remote_is_on_restarts_both(self, on, layout):
        svc, win = on
        svc.enable_remote(8610)
        svc.enable_remote(8611)
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert svc.remote_state() == {"household_port": 8611}

    def test_enable_grants_only_the_caddy_folders(self, on, layout):
        svc, win = on
        before = len(win.commands("icacls.exe"))
        svc.enable_remote(8610)
        touched = {c[1] for c in win.commands("icacls.exe")[before:]}
        assert touched == {str(layout.caddy_dir), str(layout.caddy_storage), str(layout.caddy_logs)}

    def test_enable_never_adds_a_firewall_rule_and_tells_the_owner_how(self, on, layout):
        svc, win = on
        message = svc.enable_remote(8610)
        # netsh is only ever asked to show a rule (the fake enforces it).
        assert all(c[3] == "show" for c in win.commands("netsh.exe"))
        command = service_control.firewall_rule_command(layout)
        assert command in message
        assert command.startswith("netsh advfirewall firewall add rule ")
        assert "protocol=TCP" in command and "localport=443" in command
        assert f'program="{layout.caddy_exe}"' in command
        assert "profile=private,domain" in command and "Public" not in command
        assert "router" in message and "DNS" in message

    def test_once_the_owner_added_the_rule_the_message_says_so(self, on):
        svc, win = on
        win.rule = True
        assert "exists" in svc.enable_remote(8610)

    def test_a_household_listener_that_is_not_baihe_undoes_it(self, layout, source):
        win = FakeWindows()
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, win, source, port_check=lambda p: False, is_baihe=lambda p, d: False)
        svc.install()
        with pytest.raises(service_layout.ServiceError, match="isn't Baihe"):
            svc.enable_remote(8610)
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == ""
        assert not layout.caddyfile.exists() and not layout.state_file.exists()

    def test_caddy_that_does_not_answer_undoes_it(self, layout, source):
        win = FakeWindows()
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, win, source, port_check=lambda p: False, is_baihe=lambda p, d: True)
        svc.install()
        with pytest.raises(service_layout.ServiceError, match="port 443"):
            svc.enable_remote(8610)
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == ""

    def test_disable_closes_everything_and_says_how_to_remove_the_rule(self, on, layout):
        svc, win = on
        svc.enable_remote(8610)
        win.rule = True
        message = svc.disable_remote()
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == ""
        assert not layout.caddyfile.exists() and svc.remote_state() == {}
        assert "delete rule" in message
        assert all(c[3] == "show" for c in win.commands("netsh.exe"))

    def test_an_update_keeps_remote_access_on(self, on, layout, source):
        svc, win = on
        svc.enable_remote(8610)
        for name in win.services:
            win.services[name]["state"] = "STOPPED"     # Setup stopped them
        message = svc.install()
        assert "remote access is on" in message
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == "8610"
        assert layout.caddyfile.is_file()

    def test_an_update_turns_it_off_when_the_household_listener_does_not_start(
            self, on, layout, source):
        # The server skips a household listener whose settings it refuses and
        # keeps the PC listener: /api/health answers, the household app doesn't.
        svc, win = on
        svc.enable_remote(8610)
        svc.is_baihe = lambda port, domain: False
        message = svc.install()
        assert "turned off" in message and "household listener" in message
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == ""
        assert svc.remote_state() == {} and not layout.caddyfile.exists()

    def test_a_failed_update_puts_remote_access_back_as_it_was(self, on, layout, source):
        svc, win = on
        svc.enable_remote(8610)
        with pytest.raises(service_layout.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False, port_check=lambda p: True,
                      is_baihe=lambda p, d: True).install()
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == "8610"
        assert svc.remote_state() == {"household_port": 8610}

    def test_an_update_turns_it_off_when_the_settings_are_gone(self, on, layout):
        svc, win = on
        svc.enable_remote(8610)
        (layout.data / ".env").write_text("", encoding="utf-8")
        message = svc.install()
        assert "turned off" in message
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == ""
        assert svc.remote_state() == {}

    def test_not_installed_as_a_service(self, layout, source):
        svc = _services(layout, FakeWindows(), source)
        with pytest.raises(service_layout.ServiceError, match="isn't installed"):
            svc.enable_remote()

    def test_status_reports_remote_and_the_rule(self, on):
        svc, win = on
        svc.enable_remote(8610)
        out = svc.status()
        assert "BaiheCaddy: RUNNING" in out and "household port 8610" in out
        assert "none (this script never adds it)" in out
        win.rule = True
        assert "present" in svc.status()

    def test_uninstall_reports_a_leftover_rule_without_touching_it(self, on):
        svc, win = on
        svc.enable_remote(8610)
        win.rule = True
        assert "delete rule" in svc.uninstall()
        assert all(c[3] == "show" for c in win.commands("netsh.exe"))


class TestChosenPort:
    """The service's own port: the app's default unless `install --port`
    chose another, stored only in the admin folder's config.json."""

    def _svc(self, layout, win, source, probed=None, health=True, in_use=(), **kw):
        def probe(port):
            if probed is not None:
                probed.append(port)
            return health(port) if callable(health) else health
        return _services(layout, win, source, health=probe,
                         port_check=lambda p: p in in_use or p == 443, **kw)

    def _stored(self, layout):
        return __import__("json").loads(layout.config_file.read_text(encoding="utf-8"))["api_port"]

    def _snapshot(self, layout):
        return {p: p.read_bytes() for p in layout.admin.rglob("*") if p.is_file()}

    def test_default_is_the_apps_port(self, layout, source):
        probed = []
        self._svc(layout, FakeWindows(), source, probed).install()
        assert self._stored(layout) == service_config.ADMIN_PORT
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == str(service_config.ADMIN_PORT)
        assert set(probed) == {service_config.ADMIN_PORT}

    def test_a_chosen_port_is_stored_written_and_probed(self, layout, source, capsys):
        probed, win = [], FakeWindows()
        svc = self._svc(layout, win, source, probed)
        message = svc.install("8611")
        assert self._stored(layout) == 8611 and svc.api_port() == 8611
        root = ET.parse(layout.wrapper_xml("BaiheStudio")).getroot()
        env = {e.get("name"): e.get("value") for e in root.findall("env")}
        assert env["BAIHE_API_PORT"] == "8611" and env["BAIHE_API_HOST"] == "127.0.0.1"
        assert "127.0.0.1:8611" in root.findtext("description")
        assert set(probed) == {8611} and "127.0.0.1:8611" in message
        assert service_cli.main(["status"], services=svc, admin=False) == 0
        assert "Baihe Studio's port: http://127.0.0.1:8611" in capsys.readouterr().out

    def test_main_passes_the_port_to_install(self, layout, source):
        svc = self._svc(layout, FakeWindows(), source)
        assert service_cli.main(["install", "--port", "8611"], services=svc, admin=True) == 0
        assert svc.api_port() == 8611

    def _legacy(self, layout):
        """config.json as a service from before the port was stored left it."""
        config = json.loads(layout.config_file.read_text(encoding="utf-8"))
        del config["api_port"]
        layout.config_file.write_text(json.dumps(config), encoding="utf-8")

    BAD = ["80", "1023", "70000", "0", "-1", "abc", "86 11", "", "8756", "8610"]

    @pytest.mark.parametrize("bad", BAD)
    def test_a_bad_port_on_a_fresh_install_leaves_nothing(self, layout, source, bad, capsys):
        win = FakeWindows()
        svc = self._svc(layout, win, source)
        assert service_cli.main(["install", "--port", bad], services=svc, admin=True) == 2
        assert "Nothing was changed" in capsys.readouterr().err
        assert win.services == {} and not layout.helper.exists()

    @pytest.mark.parametrize("bad", BAD)
    def test_a_bad_port_over_a_legacy_config_is_refused_and_changes_nothing(
            self, layout, source, bad, capsys):
        win = FakeWindows()
        svc = self._svc(layout, win, source)
        svc.install()
        self._legacy(layout)
        for name in win.services:
            win.services[name]["state"] = "STOPPED"     # Setup stopped them
        before, calls = self._snapshot(layout), len(win.calls)
        assert service_cli.main(["install", "--port", bad], services=svc, admin=True) == 2
        assert "Nothing was changed" in capsys.readouterr().err
        assert self._snapshot(layout) == before
        assert {c[1] for c in win.calls[calls:]} <= {"query", "qc", "start"}
        # The services Setup stopped run again as they were.
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"     # it is disabled
        assert svc.api_port() == service_config.ADMIN_PORT

    def test_a_legacy_config_takes_the_port(self, layout, source):
        win = FakeWindows()
        svc = self._svc(layout, win, source)
        svc.install()
        self._legacy(layout)
        svc.install("8611")
        assert self._stored(layout) == 8611
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8611"

    @pytest.mark.parametrize("requested", ["8622", "abc", "8756", "8611"])
    def test_an_update_keeps_the_stored_port_and_ignores_port(self, layout, source, requested,
                                                              capsys):
        win = FakeWindows()
        self._svc(layout, win, source).install("8611")
        for name in win.services:
            win.services[name]["state"] = "STOPPED"     # Setup stopped them
        probed = []
        svc = self._svc(layout, win, source, probed, in_use=(8611, 8622))
        # Not even checked on an update, so an unusable BAIHE_API_PORT
        # doesn't make Setup's service step fail.
        assert service_cli.main(["install", "--port", requested], services=svc, admin=True) == 0
        assert "127.0.0.1:8611" in capsys.readouterr().out
        assert self._stored(layout) == 8611 and set(probed) == {8611}
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8611"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert any("Kept the stored port 8611; use set-port" in line for line in win.logs)

    def test_the_household_port_in_use_is_refused(self, layout, source):
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        win = FakeWindows()
        svc = self._svc(layout, win, source, is_baihe=lambda p, d: True)
        svc.install()
        svc.enable_remote(8612)
        self._legacy(layout)
        before = self._snapshot(layout)
        with pytest.raises(service_layout.ConfigRefused, match="8612"):
            svc.install("8612")
        assert self._snapshot(layout) == before

    def test_a_port_another_program_holds_is_refused(self, layout, source):
        win = FakeWindows()
        svc = self._svc(layout, win, source, in_use=(8611,))
        with pytest.raises(service_layout.ConfigRefused, match="already in use"):
            svc.install("8611")
        assert win.services == {} and not layout.helper.exists()

    def test_an_update_keeps_the_stored_port_whatever_env_says(self, layout, source, monkeypatch):
        win = FakeWindows()
        svc = self._svc(layout, win, source)
        svc.install("8611")
        # The service account can change .env and any process the
        # environment; neither decides the service's port.
        (layout.data / ".env").write_text("BAIHE_API_PORT=8622\n", encoding="utf-8")
        monkeypatch.setenv("BAIHE_API_PORT", "8633")
        probed = []
        self._svc(layout, win, source, probed).install()
        assert self._stored(layout) == 8611 and set(probed) == {8611}
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8611"
        assert "http://127.0.0.1:8611" in svc.status()

    def test_a_failed_port_change_keeps_remote_access_on(self, layout, source):
        win = FakeWindows()
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = self._svc(layout, win, source, is_baihe=lambda p, d: True)
        svc.install()
        svc.enable_remote(8610)
        self._legacy(layout)
        for name in ("BaiheCaddy", "BaiheStudio"):
            win.services[name]["state"] = "STOPPED"          # Setup stopped both
        with pytest.raises(service_layout.ServiceError, match="/api/health"):
            self._svc(layout, win, source, health=lambda p: p != 8612,
                      is_baihe=lambda p, d: True).install("8612")
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        assert svc.remote_state() == {"household_port": 8610}

    def test_a_new_port_that_does_not_answer_rolls_back_to_the_old_one(self, layout, source):
        win = FakeWindows()
        self._svc(layout, win, source).install()
        self._legacy(layout)
        before = layout.config_file.read_bytes()
        win.services["BaiheStudio"]["state"] = "STOPPED"     # Setup stopped it
        with pytest.raises(service_layout.ServiceError, match="127.0.0.1:8612/api/health"):
            self._svc(layout, win, source, health=lambda p: p != 8612).install("8612")
        assert layout.config_file.read_bytes() == before
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8600"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert not (layout.admin / "helper.old").exists()

    def test_remote_access_keeps_the_stored_port(self, layout, source):
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        win = FakeWindows()
        svc = self._svc(layout, win, source, is_baihe=lambda p, d: True)
        svc.install("8611")
        with pytest.raises(service_layout.ConfigRefused, match="household port"):
            svc.enable_remote(8611)
        svc.enable_remote(8610)
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_PORT"] == "8611" and env["BAIHE_API_HOUSEHOLD_PORT"] == "8610"
        svc.disable_remote()
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8611"

    def test_the_default_household_port_is_never_the_services(self):
        assert service_config.api_port_problem(service_config.DEFAULT_HOUSEHOLD_PORT)
        assert service_config.api_port_problem(service_config.ADMIN_PORT) == ""
        # The documented other port for the PC listener (reserved from the
        # household listener for that reason).
        assert service_config.api_port_problem(service_config.ADMIN_PORT + 1) == ""
        assert service_config.household_port_problem(8611, api_port=8611)
        assert service_config.household_port_problem(8611) == ""

    @pytest.mark.parametrize("text", ["", "{", '{"api_port": "8611"}', '{"api_port": 8756}',
                                      '{"api_port": true}', "[]"])
    def test_an_unusable_stored_port_means_the_default(self, tmp_path, text):
        path = tmp_path / "config.json"
        path.write_text(text, encoding="utf-8")
        assert service_config.stored_api_port(path) == service_config.ADMIN_PORT
        assert service_config.stored_api_port(tmp_path / "missing.json") == service_config.ADMIN_PORT


class TestSetPort:
    """`set-port N`: the way to move an installed service, from the admin
    copy; refused with nothing changed, or put back if the new port doesn't
    answer."""

    def _installed(self, layout, source, remote=False, in_use=(), probed=None, health=None):
        """An installed service (remote access on if asked), and a Services
        whose health probe answers on every port `health` allows (all, by
        default) and records the ports it probed."""
        win = FakeWindows()
        if remote:
            (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")

        def probe(port):
            if probed is not None:
                probed.append(port)
            return True if health is None else health(port)
        svc = _services(layout, win, source, health=probe, is_baihe=lambda p, d: True,
                        port_check=lambda p: p in in_use or p == 443)
        svc.install()
        if remote:
            svc.enable_remote(8610)
        return svc, win

    def _snapshot(self, layout):
        return {p: p.read_bytes() for p in layout.admin.rglob("*") if p.is_file()}

    def test_moves_the_service_and_writes_both_places(self, layout, source, capsys):
        probed = []
        svc, win = self._installed(layout, source, probed=probed)
        del probed[:]
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True) == 0
        assert "http://127.0.0.1:8711" in capsys.readouterr().out
        assert service_config.stored_api_port(layout.config_file) == 8711
        config = service_config.read_config(layout.config_file)
        assert config["install_root"] == str(layout.root) and config["data_dir"] == str(layout.data)
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8711"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert set(probed) == {8711}
        assert not layout.config_file.with_name("config.json.new").exists()

    def test_with_remote_on_caddy_stops_first_and_starts_after(self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        calls = len(win.calls)
        message = svc.set_port("8711")
        verbs = [(c[1], c[2]) for c in win.calls[calls:] if c[1] in ("stop", "start")]
        assert verbs == [("stop", "BaiheCaddy"), ("stop", "BaiheStudio"),
                         ("start", "BaiheStudio"), ("start", "BaiheCaddy")]
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_PORT"] == "8711" and env["BAIHE_API_HOUSEHOLD_PORT"] == "8610"
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert svc.remote_state() == {"household_port": 8610}
        assert "Remote access is still on" in message

    @pytest.mark.parametrize("bad", ["80", "1023", "70000", "0", "-1", "abc", "87 11", "",
                                     "8756", "8610", "٨٧١١"])
    def test_a_bad_port_is_refused_and_changes_nothing(self, layout, source, bad, capsys):
        svc, win = self._installed(layout, source)
        before, calls = self._snapshot(layout), len(win.calls)
        assert service_cli.main(["set-port", bad], services=svc, admin=True) == 2
        assert "Nothing was changed" in capsys.readouterr().err
        assert self._snapshot(layout) == before
        assert {c[1] for c in win.calls[calls:]} <= {"query"}
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_the_household_port_is_refused(self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        svc.enable_remote(8612)
        before, calls = self._snapshot(layout), len(win.calls)
        with pytest.raises(service_layout.ConfigRefused, match="8612"):
            svc.set_port("8612")
        assert self._snapshot(layout) == before
        assert {c[1] for c in win.calls[calls:]} <= {"query"}

    def test_a_port_another_program_holds_is_refused(self, layout, source):
        svc, win = self._installed(layout, source, in_use=(8711,))
        before = self._snapshot(layout)
        with pytest.raises(service_layout.ConfigRefused, match="already in use"):
            svc.set_port("8711")
        assert self._snapshot(layout) == before and svc.api_port() == service_config.ADMIN_PORT

    def test_the_same_port_changes_nothing(self, layout, source):
        svc, win = self._installed(layout, source)
        before, calls = self._snapshot(layout), len(win.calls)
        assert "already uses port 8600" in svc.set_port("8600")
        assert self._snapshot(layout) == before
        assert {c[1] for c in win.calls[calls:]} <= {"query"}

    def test_refused_while_the_launcher_runs_a_server_on_the_service_port(self, layout, source):
        # The service is stopped and something answers /api/health on its
        # port: the launcher's own server on the same data folder.
        svc, win = self._installed(layout, source)
        win.services["BaiheStudio"]["state"] = "STOPPED"
        before = self._snapshot(layout)
        with pytest.raises(service_layout.ConfigRefused, match="Stop Baihe Studio"):
            svc.set_port("8711")
        assert self._snapshot(layout) == before
        assert win.services["BaiheStudio"]["state"] == "STOPPED"

    def test_a_new_port_that_does_not_answer_is_put_back(self, layout, source, capsys):
        probed = []
        svc, win = self._installed(layout, source, probed=probed, health=lambda p: p != 8711)
        before = self._snapshot(layout)
        del probed[:]
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True) == 1
        err = capsys.readouterr().err
        assert "127.0.0.1:8711/api/health" in err and "back on port 8600" in err
        assert self._snapshot(layout) == before
        assert service_config.stored_api_port(layout.config_file) == service_config.ADMIN_PORT
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8600"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert probed[-1] == service_config.ADMIN_PORT

    def test_a_failure_with_remote_on_restores_caddy_too(self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        svc.health = lambda p: p != 8711
        before = self._snapshot(layout)
        with pytest.raises(service_layout.ServiceError, match="back on port 8600"):
            svc.set_port("8711")
        assert self._snapshot(layout) == before
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_PORT"] == "8600" and env["BAIHE_API_HOUSEHOLD_PORT"] == "8610"

    def test_a_failed_put_back_says_so(self, layout, source):
        svc, win = self._installed(layout, source)
        svc.health = lambda p: False
        with pytest.raises(service_layout.ServiceError, match="also failed"):
            svc.set_port("8711")
        # The old port is stored again whatever happened to the service.
        assert service_config.stored_api_port(layout.config_file) == service_config.ADMIN_PORT

    def test_ctrl_c_during_the_health_wait_puts_everything_back(self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        before = self._snapshot(layout)

        def health(port):
            if port == 8711:
                raise KeyboardInterrupt
            return True
        svc.health = health
        with pytest.raises(KeyboardInterrupt):
            svc.set_port("8711")
        assert self._snapshot(layout) == before
        assert service_config.stored_api_port(layout.config_file) == service_config.ADMIN_PORT
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_PORT"] == "8600" and env["BAIHE_API_HOUSEHOLD_PORT"] == "8610"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"

    def test_a_stopped_caddy_set_to_start_with_windows_counts_as_on(self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        win.services["BaiheCaddy"]["state"] = "STOPPED"      # stopped by hand, or crashed
        message = svc.set_port("8711")
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        assert svc.remote_state() == {"household_port": 8610}
        assert "Remote access is still on" in message

    def test_remote_access_is_turned_off_if_the_household_listener_does_not_come_back(
            self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        svc.is_baihe = lambda port, domain: False
        message = svc.set_port("8711")
        assert "Remote access was turned off" in message and "still on" not in message
        assert service_config.stored_api_port(layout.config_file) == 8711
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_PORT"] == "8711" and not env.get("BAIHE_API_HOUSEHOLD_PORT")
        assert svc.remote_state() == {} and not layout.caddyfile.exists()
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_remote_access_is_turned_off_if_its_settings_no_longer_pass(self, layout, source):
        svc, win = self._installed(layout, source, remote=True)
        (layout.data / ".env").write_text("", encoding="utf-8")
        message = svc.set_port("8711")
        assert "Remote access was turned off" in message and "BAIHE_GOOGLE_CLIENT_ID" in message
        assert svc.remote_state() == {}
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_refused_when_the_service_is_not_installed(self, layout, source, capsys):
        svc = _services(layout, FakeWindows(), source)
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True) == 2
        err = capsys.readouterr().err
        assert "isn't installed as a service" in err and "BAIHE_API_PORT" in err
        assert not layout.admin.exists()

    def test_needs_administrator_rights(self, layout, source):
        svc, win = self._installed(layout, source)
        calls = len(win.calls)
        assert service_cli.main(["set-port", "8711"], services=svc, admin=False) == 3
        assert win.calls[calls:] == []

    def test_an_update_keeps_the_port_set_port_chose(self, layout, source):
        svc, win = self._installed(layout, source)
        svc.set_port("8711")
        for name in win.services:
            win.services[name]["state"] = "STOPPED"     # Setup stopped them
        svc.install()
        assert svc.api_port() == 8711
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_PORT"] == "8711"

    def test_help_lists_every_command(self, capsys):
        with pytest.raises(SystemExit):
            service_cli.main(["--help"])
        out = capsys.readouterr().out
        for command in ("status", "set-port N", "enable-remote", "disable-remote", "stop",
                        "install", "uninstall", r"helper\lib\installer\service.py"):
            assert command in out, command


class TestStatusPorts:
    def test_lists_every_port_baihe_uses(self, layout, source):
        svc = _services(layout, FakeWindows(), source, port_check=lambda p: p == 443,
                        is_baihe=lambda p, d: True)
        svc.install("8611")
        out = svc.status()
        block = out.split("Ports Baihe Studio uses on this PC:\n", 1)[1]
        assert f"Baihe Studio's port: http://127.0.0.1:8611 (stored in {layout.config_file})" in block
        assert "127.0.0.1:8756" in block
        # "HTTPS: 443", not "443": pytest's temporary folder can be pytest-443.
        assert "Household listener" not in block and "HTTPS: 443" not in block
        assert "set-port" in block and "BAIHE_API_PORT" in block
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc.enable_remote(8610)
        block = svc.status().split("Ports Baihe Studio uses on this PC:\n", 1)[1]
        assert "Household listener: 127.0.0.1:8610" in block and "HTTPS: 443 (Caddy" in block

    def test_says_when_the_port_is_the_default(self, layout, source):
        svc = _services(layout, FakeWindows(), source)
        svc.install()
        layout.config_file.write_text('{"install_root": "x", "data_dir": "y"}', encoding="utf-8")
        assert "http://127.0.0.1:8600 (the default; no port is stored)" in svc.status()


class TestPortsMatchTheApp:
    def test_the_pc_listener_port_is_the_apps_default(self):
        from api import api_config
        from services import page_server
        assert service_config.ADMIN_PORT == api_config.DEFAULT_PORT
        assert service_config.EXTENSION_BRIDGE_PORT == page_server.DEFAULT_PORT
        assert page_server.DEFAULT_PORT in service_config.RESERVED_PORTS

    def test_the_household_port_may_not_be_one_of_baihes_own(self, monkeypatch, tmp_path):
        from services import settings_service as ss
        monkeypatch.setattr(ss, "default_env_path", lambda: str(tmp_path / ".env"))
        for name in (ss.API_PORT_ENV, ss.HOUSEHOLD_PORT_ENV):
            monkeypatch.delenv(name, raising=False)
        for port in ss.baihe_own_ports():
            assert service_config.household_port_problem(port)
        assert service_config.household_port_problem(service_config.DEFAULT_HOUSEHOLD_PORT) == ""

    def test_the_household_port_reaches_the_server_only_through_the_service_env(self, layout):
        # The service is where the port is set: .env and machine-wide values
        # are blanked, and the server reads the port from its environment.
        env = service_config.app_service_env(layout.data, household_port=8610)
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == "8610" and env["BAIHE_API_HOST"] == "127.0.0.1"
        from api import api_config
        settings = api_config.load_settings({k: v for k, v in env.items() if v})
        assert settings.household_port == 8610 and settings.port == service_config.ADMIN_PORT

    def test_the_service_applies_a_queued_install_before_serving(self, layout, monkeypatch):
        # No launcher sits in front of the service, so api/__main__ does it.
        env = service_config.app_service_env(layout.data)
        assert env["BAIHE_APPLY_PENDING"] == "1"
        import api.__main__ as entry
        import pending_install
        calls = []
        monkeypatch.setattr(pending_install, "apply", lambda echo=False: calls.append("apply"))
        monkeypatch.setattr(entry, "_serve", lambda: calls.append("serve"))
        monkeypatch.setenv("BAIHE_APPLY_PENDING", "1")
        entry.main([])
        assert calls == ["apply", "serve"]
        calls.clear()
        monkeypatch.delenv("BAIHE_APPLY_PENDING")
        entry.main([])
        assert calls == ["serve"]


class TestUninstallNeverFollowsCaddysFolders:
    def test_a_link_caddy_made_in_its_folder_is_removed_not_followed(self, layout, source, tmp_path):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("x", encoding="utf-8")
        (layout.caddy_storage / "link").symlink_to(outside, target_is_directory=True)
        svc.uninstall()
        assert (outside / "keep.txt").is_file() and not layout.admin.exists()

    def test_a_caddy_folder_that_stays_leaves_the_admin_folder(self, layout, source, monkeypatch):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        real_rmtree = service.shutil.rmtree

        def rmtree(path, *a, **k):
            if Path(path) == layout.caddy_logs:
                return None
            return real_rmtree(path, *a, **k)
        monkeypatch.setattr(service.shutil, "rmtree", rmtree)
        with pytest.raises(service_layout.ServiceError, match="run uninstall again"):
            svc.uninstall()
        assert win.services == {} and layout.admin.exists()


class TestRenderCaddyfile:
    TEXT = TEMPLATE.read_text(encoding="utf-8")

    def _render(self, text=None, domain="baihe.example.com", port=8610):
        return service_config.render_caddyfile(self.TEXT if text is None else text, domain, port,
                                        Path("C:/Program Files/Baihe Studio Services/caddy-logs"))

    def test_renders_the_real_template(self):
        out = self._render()
        assert out.startswith(service_config.GENERATED_HEADER)
        assert "baihe-access.log" in out and "rate_limit" in out

    def test_keeps_the_pc_only_refusals(self):
        out = self._render()
        for needle in ("respond @pc_only 404", "respond @pc_only_post 404",
                       "respond @pc_only_delete 404", "respond @unclean_path 400"):
            assert needle in out

    @pytest.mark.parametrize("old", ["admin off", "respond @pc_only 404"])
    def test_refuses_a_template_that_lost_a_safeguard(self, old):
        with pytest.raises(service_layout.ServiceError):
            self._render(self.TEXT.replace(old, "# removed"))

    def test_refuses_a_template_that_forwards_elsewhere(self):
        with pytest.raises(service_layout.ServiceError, match="household listener"):
            self._render(self.TEXT.replace("127.0.0.1:{$BAIHE_API_HOUSEHOLD_PORT}", "0.0.0.0:8600"))

    @pytest.mark.parametrize("domain", ["", "a b.example.com", "x.example.com\nrespond 200", "localhost"])
    def test_refuses_a_domain_that_could_change_the_file(self, domain):
        with pytest.raises(service_layout.ConfigRefused):
            self._render(domain=domain)


class TestNothingBeyondLoopback:
    """Nothing here adds a listener, firewall rule, certificate or router
    change. Caddy is started only by `enable-remote`."""

    MODULES = ("service", "service_checks", "service_cli", "service_config", "service_control",
               "service_layout")
    SOURCES = [(ROOT / "installer" / f"{name}.py").read_text(encoding="utf-8") for name in MODULES]
    SOURCE = "\n".join(SOURCES)
    CODE_BY_MODULE = ["\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
                      for text in SOURCES]
    CODE = "\n".join(CODE_BY_MODULE)

    @pytest.mark.parametrize("needle", ["upnp", "nat-pmp", "natpmp", "addportmapping", "0.0.0.0",
                                        "ddns", "dyndns", "certbot", "cloudflare"])
    def test_absent(self, needle):
        # each module's docstring may say what it does not do
        code = "\n".join(module.split('"""', 2)[2] for module in self.CODE_BY_MODULE)
        assert needle not in code.lower()

    def test_netsh_is_only_asked_to_show(self):
        assert re.findall(r"NETSH[^\n]*", self.CODE) == [
            'NETSH = _system32("netsh.exe")      # only ever `show rule`: see firewall_show_command',
            'NETSH, "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"]']

    def test_the_add_rule_command_is_text_nothing_runs(self):
        assert "firewall_rule_command(" in self.CODE
        runs = re.findall(r"self\.run\(([^\n]*)", self.CODE)
        assert not any("firewall_rule_command" in r for r in runs)

    def test_standard_library_only(self):
        sibling = Path(setup_lock.__file__).read_text(encoding="utf-8")
        modules = set(re.findall(r"^(?:from|import) (\w+)", self.SOURCE + sibling, re.M))
        assert modules - {"installer"} <= set(sys.stdlib_module_names)
        # service_cli is imported inside the __main__ guard (it imports Services from service).
        assert set(re.findall(r"^from (installer\.\w+)", self.SOURCE, re.M)) == {
            "installer.service", "installer.service_checks", "installer.service_config",
            "installer.service_control", "installer.service_layout", "installer.service_lock",
            "installer.setup_lock"}

    def test_every_command_has_a_time_limit(self):
        assert "timeout=timeout" in self.SOURCE and "timeout=2)" in self.SOURCE
