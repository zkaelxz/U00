"""installer/service.py: Baihe Studio and Caddy as Windows services
(docs/windows-installer-design.md, "Boot services").

Windows can't run here, so sc.exe, icacls and netsh are a fake that keeps
the services' and the firewall rule's state. These pin what the owner
decided: the app service starts at boot as its own least-privilege account;
the Caddy service is installed disabled; remote access is refused (and
nothing changes) until sign-in and the public URL are configured; the
Caddyfile holds no secret and keeps Caddy's admin endpoint off; the only
firewall rule is inbound TCP 443 for caddy.exe on the private and domain
profiles, removed by disable-remote and uninstall; and nothing asks the
router to open anything.
"""

import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))

import service  # noqa: E402

TEMPLATE = ROOT / "deploy" / "caddy" / "Caddyfile.template"
SECRET = "gocspx-test-client-secret-value"
GOOD_ENV = (f"BAIHE_GOOGLE_CLIENT_ID=123-abc.apps.googleusercontent.com\n"
            f"BAIHE_GOOGLE_CLIENT_SECRET={SECRET}\n"
            f"BAIHE_PUBLIC_URL=https://Baihe.Example.com\n")


def _exe_name(cmd0) -> str:
    return re.split(r"[\\/]", str(cmd0))[-1].lower()


class FakeWindows:
    """sc.exe, the WinSW wrappers, icacls and netsh, with state."""

    def __init__(self, services=None):
        self.services = dict(services or {})   # name -> {"state", "start", "account", ...}
        self.firewall = []                      # the add-rule commands in force
        self.calls = []
        self.logs = []
        self.fail_start = set()

    def log(self, text):
        self.logs.append(text)

    def _result(self, cmd, rc=0, out=""):
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def __call__(self, cmd, timeout=120):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        exe = _exe_name(cmd[0])
        if exe in ("baihestudio.exe", "baihecaddy.exe"):
            name = "BaiheStudio" if exe == "baihestudio.exe" else "BaiheCaddy"
            assert cmd[1] == "install"
            self.services[name] = {"state": "STOPPED", "start": "AUTO_START", "account": "LocalSystem"}
            return self._result(cmd)
        if exe == "sc.exe":
            return self._sc(cmd)
        if exe == "icacls.exe":
            return self._result(cmd)
        if exe == "netsh.exe":
            return self._netsh(cmd)
        raise AssertionError(f"unexpected command {cmd}")

    def _sc(self, cmd):
        verb, name = cmd[1], cmd[2]
        svc = self.services.get(name)
        if svc is None:
            return self._result(cmd, service.ERROR_SERVICE_DOES_NOT_EXIST)
        if verb == "query":
            return self._result(cmd, 0, f"SERVICE_NAME: {name}\n        STATE              : 4  {svc['state']} \n")
        if verb == "qc":
            return self._result(cmd, 0, f"        START_TYPE         : 2   {svc['start']}\n")
        if verb == "stop":
            if svc["state"] == "STOPPED":
                return self._result(cmd, service.ERROR_SERVICE_NOT_ACTIVE)
            svc["state"] = "STOPPED"
            return self._result(cmd)
        if verb == "start":
            if svc["start"] == "DISABLED" or name in self.fail_start:
                return self._result(cmd, 1058)
            svc["state"] = "RUNNING"
            return self._result(cmd)
        if verb == "delete":
            del self.services[name]
            return self._result(cmd)
        if verb == "config":
            key, value = cmd[3], cmd[4]
            if key == "start=":
                svc["start"] = {"auto": "AUTO_START", "disabled": "DISABLED",
                                "demand": "DEMAND_START"}[value]
            elif key == "obj=":
                svc["account"] = value
            return self._result(cmd)
        if verb in ("sidtype", "privs", "failure", "failureflag"):
            svc[verb] = cmd[3:]
            return self._result(cmd)
        raise AssertionError(f"unexpected sc command {cmd}")

    def _netsh(self, cmd):
        assert cmd[1:4] == ["advfirewall", "firewall", cmd[3]]
        verb = cmd[3]
        if verb == "add":
            self.firewall.append(cmd)
            return self._result(cmd)
        if verb == "delete":
            had = bool(self.firewall)
            self.firewall = []
            return self._result(cmd, 0 if had else 1)
        if verb == "show":
            return self._result(cmd, 0 if self.firewall else 1)
        raise AssertionError(f"unexpected netsh command {cmd}")

    # helpers for assertions
    def commands(self, exe):
        return [c for c in self.calls if _exe_name(c[0]) == exe]

    def changing_calls(self):
        """Every call except reading a service's state or the rule."""
        return [c for c in self.calls
                if not (_exe_name(c[0]) == "sc.exe" and c[1] in ("query", "qc"))
                and not (_exe_name(c[0]) == "netsh.exe" and c[3] == "show")]


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "Baihe Studio"
    data = tmp_path / "data folder"
    for rel in ("python/python.exe", "service/BaiheStudio.exe", "caddy/BaiheCaddy.exe",
                "caddy/caddy.exe"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MZ")
    (root / "python" / "Lib" / "site-packages").mkdir(parents=True)
    tpl = root / "app" / "deploy" / "caddy" / "Caddyfile.template"
    tpl.parent.mkdir(parents=True)
    tpl.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    (data / "library").mkdir(parents=True)
    (data / "library" / "library.db").write_bytes(b"db")
    return service.Layout(root, data)


def _services(layout, win, health=True, ports=None):
    ports = {8610, 443} if ports is None else ports
    return service.Services(layout, win, health=lambda: health,
                            port_check=lambda p: p in ports, sleep=lambda s: None)


def _installed():
    return {"BaiheStudio": {"state": "RUNNING", "start": "AUTO_START"},
            "BaiheCaddy": {"state": "STOPPED", "start": "DISABLED"}}


def _env(layout, text=GOOD_ENV):
    layout.env_file.write_text(text, encoding="utf-8")


def _xml_env(path):
    root = ET.parse(path).getroot()
    return {e.get("name"): e.get("value") for e in root.findall("env")}


class TestServiceSid:
    def test_known_value(self):
        # Windows' own TrustedInstaller service SID.
        assert (service.service_sid("TrustedInstaller")
                == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")

    def test_case_insensitive_and_matches_the_workflow_check(self):
        sid = service.service_sid("BaiheStudio")
        assert sid == service.service_sid("baihestudio")
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        assert sid in wf


class TestWinswXml:
    def test_app_service(self, layout):
        root = ET.fromstring(service.app_service_xml(layout))
        assert root.findtext("id") == "BaiheStudio"
        assert root.findtext("executable") == str(layout.python_exe)
        assert root.findtext("arguments") == "-s -m api"
        assert root.findtext("startmode") == "Automatic"
        assert root.findtext("stopparentprocessfirst") == "true"
        assert root.findtext("logpath") == str(layout.data / "library" / "logs" / "service")
        log = root.find("log")
        assert log.get("mode") == "roll-by-size"
        assert int(log.findtext("keepFiles")) >= 1 and int(log.findtext("sizeThreshold")) >= 1024
        env = {e.get("name"): e.get("value") for e in root.findall("env")}
        assert env["BAIHE_API_HOST"] == "127.0.0.1"
        assert env["BAIHE_API_PORT"] == "8600"
        assert env["BAIHE_API_ALLOW_KEY_WRITES"] == "1"
        assert "BAIHE_API_HOUSEHOLD_PORT" not in env
        # The account is set by sc.exe, never a password in the XML.
        assert root.find("serviceaccount") is None

    def test_household_port_only_when_remote_access_is_on(self, layout):
        root = ET.fromstring(service.app_service_xml(layout, 8610))
        env = {e.get("name"): e.get("value") for e in root.findall("env")}
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == "8610"

    def test_caddy_service(self, layout):
        root = ET.fromstring(service.caddy_service_xml(layout))
        assert root.findtext("id") == "BaiheCaddy"
        assert root.findtext("executable") == str(layout.caddy_exe)
        assert root.findtext("arguments") == f'run --config "{layout.caddyfile}" --adapter caddyfile'
        env = {e.get("name"): e.get("value") for e in root.findall("env")}
        assert env == {"XDG_DATA_HOME": str(layout.caddy_storage),
                       "XDG_CONFIG_HOME": str(layout.caddy_storage)}
        assert root.findtext("logpath") == str(layout.caddy_logs)

    def test_paths_are_escaped(self, tmp_path):
        lay = service.Layout(tmp_path / "A & <B>", tmp_path / "d")
        root = ET.fromstring(service.app_service_xml(lay))
        assert root.findtext("executable") == str(lay.python_exe)


class TestEnvFile:
    def test_matches_the_apps_own_parser(self, tmp_path):
        from services.settings_service import _read_env_file
        path = tmp_path / ".env"
        path.write_bytes("\ufeff# comment\n\nA=1\nB = 'two'\nC=\"th=ree\"\nnot a pair\n".encode("utf-8"))
        assert service.read_env_file(path) == _read_env_file(str(path)) == {
            "A": "1", "B": "two", "C": "th=ree"}

    def test_missing_file(self, tmp_path):
        assert service.read_env_file(tmp_path / "none") == {}


def _values(**overrides):
    values = {"BAIHE_GOOGLE_CLIENT_ID": "123-abc.apps.googleusercontent.com",
              "BAIHE_GOOGLE_CLIENT_SECRET": SECRET,
              "BAIHE_PUBLIC_URL": "https://baihe.example.com"}
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


class TestRemoteAccessConfig:
    def test_ok(self):
        assert service.remote_access_config(_values(BAIHE_PUBLIC_URL="https://Baihe.Example.com"),
                                            8610) == {"domain": "baihe.example.com",
                                                      "household_port": 8610}

    @pytest.mark.parametrize("missing", ["BAIHE_GOOGLE_CLIENT_ID", "BAIHE_GOOGLE_CLIENT_SECRET",
                                         "BAIHE_PUBLIC_URL"])
    def test_each_sign_in_setting_is_required(self, missing):
        with pytest.raises(service.ConfigRefused) as e:
            service.remote_access_config(_values(**{missing: None}), 8610)
        assert missing in str(e.value) and "Nothing was changed" in str(e.value)
        assert SECRET not in str(e.value)

    def test_nothing_configured(self):
        with pytest.raises(service.ConfigRefused, match="BAIHE_GOOGLE_CLIENT_ID, "
                                                        "BAIHE_GOOGLE_CLIENT_SECRET, BAIHE_PUBLIC_URL"):
            service.remote_access_config({}, 8610)

    @pytest.mark.parametrize("url,port,match", [
        ("http://baihe.example.com", 8610, "https"),
        ("https://baihe.example.com:8443", 8610, "port"),
        ("https://localhost", 8610, "domain"),
        ("https://203.0.113.5", 8610, "domain"),
        ("https://baihe.example.com/app", 8610, "just https"),
        ("https://baihe.example.com", 8600, "must differ"),
        ("https://baihe.example.com", 8756, "extension"),
        ("https://baihe.example.com", 0, "1-65535, got 0. Nothing"),
        ("https://baihe.example.com", 70000, "1-65535"),
    ])
    def test_refusals_never_echo_the_secret(self, url, port, match):
        with pytest.raises(service.ConfigRefused, match=match) as e:
            service.remote_access_config(_values(BAIHE_PUBLIC_URL=url), port)
        assert SECRET not in str(e.value)


class TestRenderCaddyfile:
    LOG_DIR = r"C:\Users\A B\AppData\Local\Baihe Studio\caddy\logs"

    def _render(self, **kw):
        args = {"template_text": TEMPLATE.read_text(encoding="utf-8"),
                "domain": "baihe.example.com", "household_port": 8610, "log_dir": self.LOG_DIR}
        args.update(kw)
        return service.render_caddyfile(**args)

    def test_fills_in_every_setting(self):
        text = self._render()
        assert text.startswith(service.GENERATED_HEADER)
        assert "{$" not in text
        assert re.search(r"^baihe\.example\.com \{$", text, re.M)
        assert "reverse_proxy 127.0.0.1:8610 {" in text
        assert ('output file "C:/Users/A B/AppData/Local/Baihe Studio/caddy/logs/baihe-access.log" {'
                in text)

    def test_admin_endpoint_stays_off_and_never_the_admin_port(self):
        text = self._render()
        assert re.search(r"^\s*admin off\s*$", text, re.M)
        assert "127.0.0.1:8600" not in text and ":8756" not in text

    @pytest.mark.parametrize("domain", ["localhost", "1.2.3.4", "a..b.com", "evil.com {",
                                        "x.com\nadmin localhost:2019", "-bad.example.com", ""])
    def test_refuses_a_bad_domain(self, domain):
        with pytest.raises(service.ConfigRefused):
            self._render(domain=domain)

    @pytest.mark.parametrize("port", [8600, 8756, 80, 443, 0, 70000, "8610"])
    def test_refuses_ports_that_clash(self, port):
        with pytest.raises(service.ConfigRefused):
            self._render(household_port=port)

    @pytest.mark.parametrize("log_dir", ['C:\\a"b', "C:\\a{b}", "C:\\a\nb", ""])
    def test_refuses_a_log_folder_that_changes_the_file(self, log_dir):
        with pytest.raises(service.ServiceError):
            self._render(log_dir=log_dir)

    def test_refuses_a_template_with_the_admin_endpoint_on(self):
        text = TEMPLATE.read_text(encoding="utf-8").replace("admin off", "admin localhost:2019")
        with pytest.raises(service.ServiceError, match="admin endpoint"):
            self._render(template_text=text)

    def test_refuses_a_template_needing_another_setting(self):
        text = TEMPLATE.read_text(encoding="utf-8") + "\n# {$BAIHE_DNS_TOKEN}\n"
        with pytest.raises(service.ServiceError, match="BAIHE_DNS_TOKEN"):
            self._render(template_text=text)

    def test_refuses_a_template_pointing_elsewhere(self):
        text = TEMPLATE.read_text(encoding="utf-8").replace(
            "reverse_proxy 127.0.0.1:{$BAIHE_API_HOUSEHOLD_PORT}", "reverse_proxy 127.0.0.1:8600")
        with pytest.raises(service.ServiceError, match="household listener"):
            self._render(template_text=text)


class TestInstall:
    def test_fresh_install(self, layout):
        win = FakeWindows()
        message = _services(layout, win).install()
        assert "starts with Windows" in message
        app, caddy = win.services["BaiheStudio"], win.services["BaiheCaddy"]
        assert app["state"] == "RUNNING" and app["start"] == "AUTO_START"
        assert caddy["state"] == "STOPPED" and caddy["start"] == "DISABLED"
        for name, svc in win.services.items():
            assert svc["account"] == f"NT SERVICE\\{name}"
            assert svc["sidtype"] == ["unrestricted"]
            privs = svc["privs"][0].split("/")
            assert "SeChangeNotifyPrivilege" in privs
            assert "SeImpersonatePrivilege" not in privs and "SeAssignPrimaryTokenPrivilege" not in privs
            assert "restart/" in " ".join(svc["failure"])
        assert not win.firewall
        assert not [c for c in win.commands("netsh.exe") if c[3] == "add"]
        assert not layout.caddyfile.exists()
        assert "BAIHE_API_HOUSEHOLD_PORT" not in _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert layout.wrapper_xml("BaiheCaddy").is_file()

    def test_permissions_are_granted_by_service_sid(self, layout):
        win = FakeWindows()
        _services(layout, win).install()
        grants = win.commands("icacls.exe")
        app_sid = "*" + service.service_sid("BaiheStudio")
        caddy_sid = "*" + service.service_sid("BaiheCaddy")
        by_path = {}
        for cmd in grants:
            by_path.setdefault(cmd[1], []).append(" ".join(cmd[2:]))
        assert f"/grant {app_sid}:(OI)(CI)M" in by_path[str(layout.data)]
        assert f"/grant {app_sid}:(OI)(CI)RX" in by_path[str(layout.root)]
        assert f"/grant {caddy_sid}:(OI)(CI)RX" in by_path[str(layout.caddy_dir)]
        # Caddy gets nothing on the data folder itself (so not .env), and
        # its key folder is Caddy, SYSTEM and Administrators only.
        assert not any(caddy_sid in g for g in by_path[str(layout.data)])
        storage = by_path[str(layout.caddy_storage)][0]
        assert "/inheritance:r" in storage and caddy_sid in storage and app_sid not in storage

    def test_update_keeps_remote_access_when_still_configured(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        layout.state_file.write_text('{"household_port": 8610}', encoding="utf-8")
        message = _services(layout, win).install()
        assert "remote access is on" in message
        assert win.services["BaiheCaddy"]["state"] == "RUNNING"
        assert win.services["BaiheCaddy"]["start"] == "AUTO_START"
        assert len(win.firewall) == 1
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == "8610"

    def test_update_turns_remote_access_off_when_no_longer_configured(self, layout):
        win = FakeWindows(_installed())
        win.firewall = [["netsh", "advfirewall", "firewall", "add"]]
        layout.state_file.write_text('{"household_port": 8610}', encoding="utf-8")
        message = _services(layout, win).install()
        assert "Remote access was turned off" in message
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert not win.firewall and not layout.state_file.exists()
        assert "BAIHE_API_HOUSEHOLD_PORT" not in _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_fails_when_the_server_never_answers(self, layout):
        with pytest.raises(service.ServiceError, match="doesn't answer"):
            _services(layout, FakeWindows(), health=False).install()

    def test_refuses_an_incomplete_install(self, layout):
        layout.caddy_exe.unlink()
        win = FakeWindows()
        with pytest.raises(service.ServiceError, match="caddy.exe"):
            _services(layout, win).install()
        assert not win.changing_calls()


class TestEnableRemote:
    def test_refused_without_config_and_nothing_changes(self, layout):
        win = FakeWindows(_installed())
        before = service.app_service_xml(layout)
        layout.wrapper_xml("BaiheStudio").write_text(before, encoding="utf-8")
        with pytest.raises(service.ConfigRefused, match="BAIHE_PUBLIC_URL"):
            _services(layout, win).enable_remote()
        assert not win.changing_calls()
        assert not layout.caddyfile.exists() and not layout.state_file.exists()
        assert layout.wrapper_xml("BaiheStudio").read_text(encoding="utf-8") == before
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"

    def test_refused_when_the_app_service_isnt_installed(self, layout):
        _env(layout)
        with pytest.raises(service.ServiceError, match="isn't installed as a service"):
            _services(layout, FakeWindows()).enable_remote()

    def test_turns_it_on(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        message = _services(layout, win).enable_remote(8610)
        assert "baihe.example.com" in message and "router" in message
        assert win.services["BaiheCaddy"] == {**win.services["BaiheCaddy"], "state": "RUNNING",
                                              "start": "AUTO_START", "account": "NT SERVICE\\BaiheCaddy"}
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_HOUSEHOLD_PORT"] == "8610"
        assert layout.state_file.read_text(encoding="utf-8").strip() == '{"household_port": 8610}'
        caddyfile = layout.caddyfile.read_text(encoding="utf-8")
        assert "reverse_proxy 127.0.0.1:8610 {" in caddyfile and SECRET not in caddyfile
        assert re.search(r"^baihe\.example\.com \{$", caddyfile, re.M)

    def test_the_one_firewall_rule(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        _services(layout, win).enable_remote()
        assert len(win.firewall) == 1
        rule = win.firewall[0]
        assert rule[3:5] == ["add", "rule"]
        assert f"name={service.FIREWALL_RULE_NAME}" in rule
        for part in ("dir=in", "action=allow", "protocol=TCP", "localport=443",
                     "profile=private,domain", f"program={layout.caddy_exe}"):
            assert part in rule, part
        assert not any("public" in p.lower() or p.startswith("remoteip") for p in rule)

    def test_secret_never_reaches_a_command_or_the_log(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        _services(layout, win).enable_remote()
        assert not any(SECRET in " ".join(c) for c in win.calls)
        for path in (layout.wrapper_xml("BaiheStudio"), layout.wrapper_xml("BaiheCaddy"),
                     layout.caddyfile, layout.state_file):
            assert SECRET not in path.read_text(encoding="utf-8")

    def test_undone_when_caddy_doesnt_answer(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        with pytest.raises(service.ServiceError, match="port 443"):
            _services(layout, win, ports={8610}).enable_remote()
        assert not win.firewall
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert not layout.state_file.exists() and not layout.caddyfile.exists()
        assert "BAIHE_API_HOUSEHOLD_PORT" not in _xml_env(layout.wrapper_xml("BaiheStudio"))

    def test_undone_when_caddy_wont_start(self, layout):
        win = FakeWindows(_installed())
        win.fail_start.add("BaiheCaddy")
        _env(layout)
        with pytest.raises(service.ServiceError):
            _services(layout, win).enable_remote()
        assert not win.firewall and win.services["BaiheCaddy"]["start"] == "DISABLED"


class TestDisableRemote:
    def test_turns_it_off(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        svc = _services(layout, win)
        svc.enable_remote()
        svc.disable_remote()
        assert win.services["BaiheCaddy"]["state"] == "STOPPED"
        assert win.services["BaiheCaddy"]["start"] == "DISABLED"
        assert not win.firewall
        assert not layout.state_file.exists() and not layout.caddyfile.exists()
        assert "BAIHE_API_HOUSEHOLD_PORT" not in _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert win.services["BaiheStudio"]["state"] == "RUNNING"


class TestUninstall:
    def test_removes_services_rule_and_permissions_keeps_data(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        svc = _services(layout, win)
        svc.install()
        svc.enable_remote()
        (layout.caddy_storage / "acme-key.pem").write_text("key", encoding="utf-8")
        svc.uninstall()
        assert win.services == {} and not win.firewall
        removed = [c for c in win.commands("icacls.exe") if "/remove:g" in c]
        for name in ("BaiheStudio", "BaiheCaddy"):
            sid = "*" + service.service_sid(name)
            assert [str(layout.data), "/remove:g", sid] in [c[1:] for c in removed]
        assert [str(layout.caddy_home), "/reset", "/T", "/C"] in [c[1:] for c in win.commands("icacls.exe")]
        for path in (layout.wrapper_xml("BaiheStudio"), layout.wrapper_xml("BaiheCaddy"),
                     layout.caddyfile, layout.state_file):
            assert not path.exists()
        assert (layout.data / "library" / "library.db").is_file()
        assert layout.env_file.is_file()
        assert (layout.caddy_storage / "acme-key.pem").is_file()

    def test_clean_uninstall_deletes_caddys_folder(self, layout):
        win = FakeWindows(_installed())
        layout.caddy_storage.mkdir(parents=True)
        (layout.caddy_storage / "acme-key.pem").write_text("key", encoding="utf-8")
        _services(layout, win).uninstall(purge_caddy_data=True)
        assert not layout.caddy_home.exists()
        assert (layout.data / "library" / "library.db").is_file()

    def test_nothing_installed(self, layout):
        win = FakeWindows()
        _services(layout, win).uninstall()
        assert win.services == {}


class TestMain:
    def test_refusal_exit_code(self, layout, capsys):
        win = FakeWindows(_installed())
        assert service.main(["enable-remote"], services=_services(layout, win), admin=True) == 2
        assert "BAIHE_PUBLIC_URL" in capsys.readouterr().err
        assert any("refused" in line for line in win.logs)

    def test_needs_administrator_rights(self, layout):
        win = FakeWindows(_installed())
        _env(layout)
        assert service.main(["enable-remote"], services=_services(layout, win), admin=False) == 3
        assert win.calls == []

    def test_status_needs_no_rights(self, layout, capsys):
        win = FakeWindows(_installed())
        assert service.main(["status"], services=_services(layout, win), admin=False) == 0
        out = capsys.readouterr().out
        assert "BaiheStudio: RUNNING, start AUTO_START" in out
        assert "BaiheCaddy: STOPPED, start DISABLED" in out
        assert "Remote access: off" in out and "none" in out

    def test_failure_exit_code(self, layout, capsys):
        assert service.main(["install"], services=_services(layout, FakeWindows(), health=False),
                            admin=True) == 1
        assert "ERROR:" in capsys.readouterr().err

    def test_only_on_windows(self, monkeypatch):
        monkeypatch.setattr(service.os, "name", "posix")
        assert service.main(["status"]) == 1


class TestNoExternalReachability:
    """Nothing here may make the PC reachable from outside by itself."""

    SOURCE = (ROOT / "installer" / "service.py").read_text(encoding="utf-8")

    @pytest.mark.parametrize("needle", ["upnp", "nat-pmp", "natpmp", "addportmapping",
                                        "miniupnp", "igd", "0.0.0.0", "profile=public",
                                        "profile=any"])
    def test_no_router_or_wide_open_code(self, needle):
        assert needle not in self.SOURCE.lower()

    def test_only_one_firewall_rule_and_only_port_443(self):
        assert self.SOURCE.count('"add", "rule"') == 1
        assert service.FIREWALL_PROFILES == "private,domain"
        assert service.HTTPS_PORT == 443
        assert re.findall(r"localport=\{?(\w+)", self.SOURCE) == ["HTTPS_PORT"]

    def test_app_service_is_loopback_only(self):
        assert service.app_service_env()["BAIHE_API_HOST"] == "127.0.0.1"
        assert service.app_service_env(8610)["BAIHE_API_HOST"] == "127.0.0.1"

    def test_every_command_has_a_time_limit(self):
        assert "timeout=timeout" in self.SOURCE and "timeout=2)" in self.SOURCE
