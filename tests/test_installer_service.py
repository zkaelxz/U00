"""installer/service.py: Baihe Studio as a Windows service, and Caddy for
remote access (docs/windows-installer-design.md, "Boot service").

Windows can't run here, so sc.exe, icacls and netsh are a fake that keeps the
service's state. These pin what the owner decided: the service starts at
boot as its own least-privilege account, listens on 127.0.0.1:8600 and
nothing else, is restarted after a failure, stops cleanly, and is put back
as it was when an install step fails or removed when an uninstall does not
complete. Caddy is installed disabled, only `enable-remote` turns it on,
Baihe's household listener stays on 127.0.0.1 behind it, and nothing here
ever adds a firewall rule: the owner does, by hand.
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
SIGN_IN = ("BAIHE_GOOGLE_CLIENT_ID=id.apps.googleusercontent.com\n"
           "BAIHE_GOOGLE_CLIENT_SECRET=not-a-real-secret\n"
           "BAIHE_PUBLIC_URL=https://baihe.example.com\n")


def _exe_name(cmd0) -> str:
    return re.split(r"[\\/]", str(cmd0))[-1].lower()


class FakeWindows:
    """sc.exe and icacls, with state."""

    def __init__(self, installed=False, state="STOPPED"):
        self.services = {}
        if installed:
            self.services[service.APP_SERVICE] = {"state": state, "start": "AUTO_START"}
        self.calls = []
        self.logs = []
        self.fail = set()       # (verb) sc verbs, or "icacls", that exit 1
        self.rule = False       # the owner's firewall rule exists

    def log(self, text):
        self.logs.append(text)

    def _result(self, cmd, rc=0, out=""):
        return subprocess.CompletedProcess(cmd, rc, out, "")

    def __call__(self, cmd, timeout=120):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        exe = _exe_name(cmd[0])
        if exe == "icacls.exe":
            return self._result(cmd, 1 if "icacls" in self.fail else 0)
        if exe == "netsh.exe":
            # Only reading the owner's rule is allowed; never adding one.
            assert cmd[1:4] == ["advfirewall", "firewall", "show"], cmd
            return self._result(cmd, 0 if self.rule else 1)
        assert exe == "sc.exe", cmd
        verb, name = cmd[1], cmd[2]
        svc = self.services.get(name)
        if verb == "create":
            self.services[name] = {"state": "STOPPED", "start": "DISABLED", "account": "LocalSystem"}
            return self._result(cmd)
        if svc is None:
            return self._result(cmd, service.ERROR_SERVICE_DOES_NOT_EXIST)
        if verb in self.fail:
            return self._result(cmd, 1)
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
            if svc["start"] == "DISABLED":
                return self._result(cmd, 1058)
            svc["state"] = "RUNNING"
            return self._result(cmd)
        if verb == "delete":
            del self.services[name]
            return self._result(cmd)
        if verb == "config":
            key, value = cmd[3], cmd[4]
            if key == "start=":
                svc["start"] = {"auto": "AUTO_START", "disabled": "DISABLED"}[value]
            elif key == "depend=":
                svc["depend"] = value
            elif key == "obj=":
                svc["account"] = value
            return self._result(cmd)
        if verb in ("sidtype", "privs", "failure", "failureflag"):
            svc[verb] = cmd[3:]
            return self._result(cmd)
        raise AssertionError(f"unexpected sc command {cmd}")

    def commands(self, exe):
        return [c for c in self.calls if _exe_name(c[0]) == exe]


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "Baihe Studio"
    data = tmp_path / "data folder"
    (root / "python").mkdir(parents=True)
    (root / "python" / "python.exe").write_bytes(b"MZ")
    (data / "library").mkdir(parents=True)
    (data / "library" / "library.db").write_bytes(b"db")
    return service.Layout(root, data, tmp_path / "Program Files" / "Baihe Studio Services")


@pytest.fixture
def source(tmp_path):
    """Setup's extraction: {app}\\service."""
    src = tmp_path / "setup-service"
    for rel, text in (("helper/python/python.exe", "py"), ("helper/lib/installer/service.py", "new"),
                      ("wrapper/BaiheStudio.exe", "winsw"), ("caddy/BaiheCaddy.exe", "winsw"),
                      ("caddy/caddy.exe", "caddy")):
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    template = src / "helper" / "lib" / "deploy" / "caddy" / "Caddyfile.template"
    template.parent.mkdir(parents=True)
    template.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return src


def _services(layout, win, source=None, health=True, **kw):
    return service.Services(layout, win, health=lambda: health, sleep=lambda s: None,
                            source=source, **kw)


def _xml_env(path):
    return {e.get("name"): e.get("value") for e in ET.parse(path).getroot().findall("env")}


class TestServiceSid:
    def test_known_value(self):
        # Windows' own TrustedInstaller service SID.
        assert (service.service_sid("TrustedInstaller")
                == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")

    def test_matches_the_workflow_check(self):
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        assert service.service_sid("BaiheStudio") in wf


class TestWinswXml:
    def test_service(self, layout):
        root = ET.fromstring(service.app_service_xml(layout))
        assert root.findtext("id") == "BaiheStudio"
        assert root.findtext("executable") == str(layout.python_exe)
        assert root.findtext("arguments") == "-s -m api"
        assert root.findtext("workingdirectory") == str(layout.app)
        assert root.findtext("startmode") == "Automatic"
        assert root.findtext("stopparentprocessfirst") == "true"
        assert root.findtext("logpath") == str(layout.data / "library" / "logs" / "service")
        log = root.find("log")
        assert log.get("mode") == "roll-by-size" and int(log.findtext("keepFiles")) >= 1
        # The account is set by sc.exe, never a password in the XML.
        assert root.find("serviceaccount") is None

    def test_only_loopback_8600_whatever_the_machine_sets(self, layout):
        env = _xml_env_from(service.app_service_xml(layout))
        assert env["BAIHE_API_HOST"] == "127.0.0.1" and env["BAIHE_API_PORT"] == "8600"
        assert env["BAIHE_API_AUTH"] == "off" and env["BAIHE_API_ENV"] == "production"
        # Every other server setting is written out empty, household listener included.
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == ""
        assert set(service.API_ENV_NAMES) <= set(env)
        assert env["BAIHE_DATA_DIR"] == str(layout.data)

    def test_key_form_is_off_unless_env_file_opts_in(self, layout):
        assert _xml_env_from(service.app_service_xml(layout))["BAIHE_API_ALLOW_KEY_WRITES"] == "0"
        on = _xml_env_from(service.app_service_xml(layout, allow_key_writes=True))
        assert on["BAIHE_API_ALLOW_KEY_WRITES"] == "1"

    def test_paths_are_escaped(self, tmp_path):
        lay = service.Layout(tmp_path / "A & <B>", tmp_path / "d", tmp_path / "admin")
        assert ET.fromstring(service.app_service_xml(lay)).findtext("executable") == str(lay.python_exe)


def _xml_env_from(text):
    return {e.get("name"): e.get("value") for e in ET.fromstring(text).findall("env")}


class TestEnvFile:
    def test_matches_the_apps_own_parser(self, tmp_path):
        from services.settings_service import _read_env_file
        path = tmp_path / ".env"
        path.write_bytes("\ufeff# comment\n\nA=1\nB = 'two'\nC=\"th=ree\"\nnot a pair\n".encode("utf-8"))
        assert service.read_env_file(path) == _read_env_file(str(path)) == {
            "A": "1", "B": "two", "C": "th=ree"}

    def test_missing_file(self, tmp_path):
        assert service.read_env_file(tmp_path / "none") == {}


class TestFolders:
    @pytest.mark.parametrize("path", ["relative", "C:\\", "C:\\a%b", 'C:\\a"b', ""])
    def test_bad_folders(self, path):
        assert service.folder_problem(path, "folder")

    def test_data_folder_with_foreign_files_is_refused(self, tmp_path):
        (tmp_path / "library").mkdir()
        (tmp_path / "Documents").mkdir()
        assert "Documents" in service.data_folder_contents_problem(tmp_path)

    def test_data_folder_with_only_baihe_items_is_fine(self, layout):
        (layout.data / ".env").write_text("A=1", encoding="utf-8")
        assert service.data_folder_contents_problem(layout.data) == ""

    def test_links_are_refused(self, tmp_path):
        (tmp_path / "real").mkdir()
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
        with pytest.raises(service.ServiceError, match="link"):
            service.refuse_reparse_point(tmp_path / "link")


class TestInstall:
    def test_fresh_install(self, layout, source):
        win = FakeWindows()
        message = _services(layout, win, source).install()
        assert "starts with Windows" in message
        svc = win.services["BaiheStudio"]
        assert svc["state"] == "RUNNING" and svc["start"] == "AUTO_START"
        assert svc["account"] == "NT SERVICE\\BaiheStudio" and svc["sidtype"] == ["unrestricted"]
        privs = svc["privs"][0].split("/")
        assert "SeChangeNotifyPrivilege" in privs
        assert "SeImpersonatePrivilege" not in privs and "SeAssignPrimaryTokenPrivilege" not in privs
        assert svc["failure"] == ["reset=", "86400", "actions=",
                                  "restart/10000/restart/30000/restart/60000"]
        assert svc["failureflag"] == ["1"]

    def test_runs_from_the_admin_folder(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        create = next(c for c in win.calls if c[1] == "create" and c[2] == "BaiheStudio")
        assert create[create.index("binPath=") + 1] == f'"{layout.wrapper("BaiheStudio")}"'
        assert create[create.index("start=") + 1] == "disabled"     # until configured
        assert layout.wrapper("BaiheStudio").read_text(encoding="utf-8") == "winsw"
        assert layout.helper_script.read_text(encoding="utf-8") == "new"
        assert service.read_config(layout.config_file) == {"install_root": str(layout.root),
                                                           "data_dir": str(layout.data)}
        assert ET.parse(layout.wrapper_xml("BaiheStudio")).getroot().findtext("id") == "BaiheStudio"
        assert not (layout.admin / "helper.old").exists()

    def test_permissions_are_granted_by_service_sid(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        sid = "*" + service.service_sid("BaiheStudio")
        grants = {c[1]: c[2:] for c in win.commands("icacls.exe")}
        assert grants[str(layout.root)][:2] == ["/grant", f"{sid}:(OI)(CI)RX"]
        assert grants[str(layout.data)][:2] == ["/grant", f"{sid}:(OI)(CI)M"]
        assert grants[str(layout.service_dir)][:2] == ["/grant", f"{sid}:(OI)(CI)RX"]
        # No write access to anything the service or its helper is run from.
        assert str(layout.helper) not in grants and str(layout.admin) not in grants
        assert all(c[-1] == "/L" for c in win.commands("icacls.exe"))

    def test_update_replaces_files_and_keeps_the_key_form_setting(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        (layout.data / ".env").write_text("BAIHE_API_ALLOW_KEY_WRITES=1\n", encoding="utf-8")
        (source / "helper" / "lib" / "installer" / "service.py").write_text("newer", encoding="utf-8")
        win.services["BaiheStudio"]["state"] = "STOPPED"     # Setup stopped it
        _services(layout, win, source).install()
        assert layout.helper_script.read_text(encoding="utf-8") == "newer"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert _xml_env(layout.wrapper_xml("BaiheStudio"))["BAIHE_API_ALLOW_KEY_WRITES"] == "1"
        assert sum(1 for c in win.calls if c[1] == "create") == 2      # once each, not again

    def test_failure_on_a_fresh_install_leaves_nothing(self, layout, source):
        win = FakeWindows()
        with pytest.raises(service.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False).install()
        assert win.services == {}
        assert not layout.helper.exists() and not layout.service_dir.exists()

    def test_failure_on_an_update_puts_the_old_service_back(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.services["BaiheStudio"]["state"] = "STOPPED"
        (source / "helper" / "lib" / "installer" / "service.py").write_text("broken", encoding="utf-8")
        with pytest.raises(service.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False).install()
        assert layout.helper_script.read_text(encoding="utf-8") == "new"
        assert not (layout.admin / "helper.old").exists()
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_configuration_failure_deletes_a_service_it_created(self, layout, source):
        win = FakeWindows()
        win.fail.add("privs")
        with pytest.raises(service.ServiceError, match="Configuring"):
            _services(layout, win, source).install()
        assert win.services == {}

    def test_permission_failure_rolls_back(self, layout, source):
        win = FakeWindows()
        win.fail.add("icacls")
        with pytest.raises(service.ServiceError, match="permissions"):
            _services(layout, win, source).install()
        assert win.services == {} and not layout.helper.exists()
        assert not layout.caddy_dir.exists()

    def test_refuses_an_incomplete_setup_folder(self, layout, source):
        (source / "caddy" / "caddy.exe").unlink()
        (source / "caddy" / "BaiheCaddy.exe").unlink()
        (source / "caddy").rmdir()
        win = FakeWindows()
        with pytest.raises(service.ServiceError, match="incomplete"):
            _services(layout, win, source).install()
        assert win.services == {}

    def test_refuses_a_data_folder_with_foreign_files(self, layout, source):
        (layout.data / "Documents").mkdir()
        win = FakeWindows()
        with pytest.raises(service.ServiceError, match="Documents"):
            _services(layout, win, source).install()
        assert not win.calls

    def test_refuses_when_another_install_owns_the_service(self, layout, source, tmp_path):
        other = tmp_path / "other"
        (other / "app").mkdir(parents=True)
        (other / "app" / "INSTALLED").write_text("x", encoding="utf-8")
        layout.helper.mkdir(parents=True)
        layout.config_file.write_text(
            '{"install_root": %s, "data_dir": "d"}' % __import__("json").dumps(str(other)),
            encoding="utf-8")
        with pytest.raises(service.ServiceError, match="Another Baihe Studio install"):
            _services(layout, FakeWindows(), source).install()


class TestStopAndUninstall:
    def test_stop(self, layout):
        win = FakeWindows(installed=True, state="RUNNING")
        _services(layout, win).stop()
        assert win.services["BaiheStudio"]["state"] == "STOPPED"

    def test_stop_when_not_installed(self, layout):
        assert "Stopped" in _services(layout, FakeWindows()).stop()

    def test_uninstall_removes_service_permissions_and_admin_folder_keeps_data(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        svc.uninstall()
        assert win.services == {}
        removed = [c[1:] for c in win.commands("icacls.exe") if "/remove:g" in c]
        sid = "*" + service.service_sid("BaiheStudio")
        assert [str(layout.root), "/remove:g", sid, "/L"] in removed
        assert [str(layout.data), "/remove:g", sid, "/L"] in removed
        assert not layout.admin.exists()
        assert (layout.data / "library" / "library.db").is_file()

    def test_a_junction_data_folder_is_refused_before_anything_changes(self, layout, source, tmp_path):
        win = FakeWindows()
        _services(layout, win, source).install()
        calls = len(win.calls)
        real = tmp_path / "real"
        real.mkdir()
        layout.data.rename(tmp_path / "moved")
        layout.data.symlink_to(real, target_is_directory=True)
        with pytest.raises(service.ServiceError, match="link"):
            _services(layout, win).uninstall()
        assert len(win.calls) == calls and "BaiheStudio" in win.services and layout.admin.exists()

    def test_a_failed_revoke_is_reported_after_the_rest_is_removed(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.fail.add("icacls")
        with pytest.raises(service.ServiceError, match="still on that folder"):
            _services(layout, win).uninstall()
        assert win.services == {} and not layout.admin.exists()

    def test_nothing_installed(self, layout):
        win = FakeWindows()
        _services(layout, win).uninstall()
        assert win.services == {}

    def test_a_service_that_wont_stop_changes_nothing(self, layout, source):
        win = FakeWindows(installed=True, state="RUNNING")
        win.fail.add("stop")
        layout.admin.mkdir(parents=True)
        with pytest.raises(service.ServiceError, match="Stopping"):
            _services(layout, win).uninstall()
        assert "BaiheStudio" in win.services and layout.admin.exists()
        assert not win.commands("icacls.exe")

    def test_a_failed_removal_starts_the_service_again(self, layout):
        win = FakeWindows(installed=True, state="RUNNING")
        win.fail.add("delete")
        with pytest.raises(service.ServiceError, match="Removing"):
            _services(layout, win).uninstall()
        assert win.services["BaiheStudio"]["state"] == "RUNNING"
        assert not win.commands("icacls.exe")


class TestRemoveAdminFolder:
    def _folder(self, tmp_path):
        folder = tmp_path / "Program Files" / "Baihe Studio Services"
        (folder / "helper" / "python").mkdir(parents=True)
        (folder / "helper" / "python" / "python.exe").write_text("x", encoding="utf-8")
        (folder / "service.log").write_text("x", encoding="utf-8")
        return folder

    def _lock(self, monkeypatch, locked):
        real_unlink = Path.unlink

        def unlink(self, *a, **k):
            if self == locked:
                raise PermissionError("in use")
            return real_unlink(self, *a, **k)
        monkeypatch.setattr(Path, "unlink", unlink)
        scheduled = []
        monkeypatch.setattr(service, "_delete_on_reboot", scheduled.append)
        return scheduled

    def test_removes_everything(self, tmp_path):
        folder = self._folder(tmp_path)
        service.remove_admin_folder(folder)
        assert list(folder.parent.iterdir()) == []

    def test_a_loaded_file_is_parked_beside_the_folder_and_only_it_is_scheduled(self, tmp_path, monkeypatch):
        folder = self._folder(tmp_path)
        scheduled = self._lock(monkeypatch, folder / "helper" / "python" / "python.exe")
        real_rmtree = service.shutil.rmtree

        def rmtree(path, *a, **k):
            # As on Windows, the parked (still loaded) file keeps its folder.
            if ".baihe-removing-" in str(path):
                return None
            return real_rmtree(path, *a, **k)
        monkeypatch.setattr(service.shutil, "rmtree", rmtree)
        service.remove_admin_folder(folder)
        assert not folder.exists()
        park = [p for p in folder.parent.iterdir() if p.name.startswith(".baihe-removing-")]
        assert len(park) == 1 and len(park[0].name) == len(".baihe-removing-") + 16
        assert scheduled == [park[0] / "0-python.exe", park[0]]

    def test_a_park_name_that_already_exists_is_refused(self, tmp_path, monkeypatch):
        folder = self._folder(tmp_path)
        (tmp_path / "evil").mkdir()
        (folder.parent / ".baihe-removing-fixed").symlink_to(tmp_path / "evil", target_is_directory=True)
        monkeypatch.setattr(service.secrets, "token_hex", lambda n: "fixed")
        scheduled = self._lock(monkeypatch, folder / "service.log")
        service.remove_admin_folder(folder)
        assert list((tmp_path / "evil").iterdir()) == []       # nothing moved into it
        assert scheduled == [folder / "service.log"]           # only the exact path


class TestLaterFilesStillGoToThePark:
    def test_a_failed_first_rename_doesnt_send_later_files_to_their_old_paths(self, tmp_path, monkeypatch):
        folder = tmp_path / "Program Files" / "Baihe Studio Services"
        folder.mkdir(parents=True)
        for name in ("a.dll", "b.dll"):
            (folder / name).write_text("x", encoding="utf-8")
        real_unlink, real_rename, scheduled = Path.unlink, Path.rename, []

        def unlink(self, *a, **k):
            if self.suffix == ".dll":
                raise PermissionError("in use")
            return real_unlink(self, *a, **k)

        first = []

        def rename(self, target):
            if self.suffix == ".dll" and not first:
                first.append(self)
                raise PermissionError("busy")
            return real_rename(self, target)
        monkeypatch.setattr(Path, "unlink", unlink)
        monkeypatch.setattr(Path, "rename", rename)
        monkeypatch.setattr(service, "_delete_on_reboot", scheduled.append)
        service.remove_admin_folder(folder)
        # One file is scheduled in place (its rename failed); the other was parked.
        other = next(p for p in (folder / "a.dll", folder / "b.dll") if p != first[0])
        assert scheduled == [first[0]] and other not in scheduled


class TestFirstInstallLog:
    def test_the_log_exists_after_a_failed_first_install(self, tmp_path, monkeypatch):
        admin = tmp_path / "Program Files" / "Baihe Studio Services"
        admin.parent.mkdir()
        src = tmp_path / "Setup" / "service" / "helper" / "lib" / "installer"
        src.mkdir(parents=True)
        monkeypatch.setattr(service, "program_files", lambda: admin.parent)
        monkeypatch.setattr(service, "APP_DIR", src.parent)
        monkeypatch.setattr(service, "system_folders", lambda: [])
        root, data = tmp_path / "app", tmp_path / "data"
        root.mkdir()
        args = type("A", (), {"command": "install", "install_root": str(root), "data_dir": str(data)})()
        services = service.build_services(args)
        services.run.log("service.py install")
        assert (admin / service.LOG_FILE_NAME).is_file()


class TestKnownFolders:
    def test_the_admin_copy_must_be_where_windows_says(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service, "program_files", lambda: tmp_path / "Program Files")
        copy = tmp_path / "elsewhere" / "helper" / "lib"
        (copy / "installer").mkdir(parents=True)
        (tmp_path / "elsewhere" / "helper" / service.CONFIG_FILE_NAME).write_text(
            '{"install_root": "C:\\\\a", "data_dir": "C:\\\\b"}', encoding="utf-8")
        monkeypatch.setattr(service, "APP_DIR", copy)
        args = type("A", (), {"command": "uninstall"})()
        with pytest.raises(service.ServiceError, match="isn't in"):
            service.build_services(args)

    def test_windows_is_asked_not_the_environment(self):
        src = (ROOT / "installer" / "service.py").read_text(encoding="utf-8")
        code = src.split("def _known_folder", 1)[1].split("FOLDERID_PROGRAM_FILES =", 1)[0]
        assert "SHGetKnownFolderPath" in code and code.count("os.environ") == 1


class TestMain:
    def test_needs_administrator_rights(self, layout):
        win = FakeWindows(installed=True, state="RUNNING")
        assert service.main(["stop"], services=_services(layout, win), admin=False) == 3
        assert win.calls == []

    def test_status_needs_no_rights(self, layout, capsys):
        win = FakeWindows(installed=True, state="RUNNING")
        assert service.main(["status"], services=_services(layout, win), admin=False) == 0
        out = capsys.readouterr().out
        assert "BaiheStudio: RUNNING, start AUTO_START" in out
        assert "BaiheCaddy: not installed" in out and "Remote access: off" in out

    def test_status_when_not_installed(self, layout, capsys):
        assert service.main(["status"], services=_services(layout, FakeWindows()), admin=False) == 0
        assert "not installed" in capsys.readouterr().out

    def test_failure_exit_code(self, layout, source, capsys):
        assert service.main(["install"], services=_services(layout, FakeWindows(), source, health=False),
                            admin=True) == 1
        assert "ERROR:" in capsys.readouterr().err

    def test_no_other_commands(self):
        with pytest.raises(SystemExit):
            service.main(["open-firewall"], admin=True)

    def test_only_on_windows(self, monkeypatch):
        monkeypatch.setattr(service.os, "name", "posix")
        assert service.main(["status"]) == 1


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

    def test_household_listener_stays_closed(self, layout, source):
        _services(layout, FakeWindows(), source).install()
        env = _xml_env(layout.wrapper_xml("BaiheStudio"))
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == "" and env["BAIHE_API_HOST"] == "127.0.0.1"

    def test_caddy_runs_as_its_own_account_with_private_folders(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        sid = "*" + service.service_sid("BaiheCaddy")
        grants = {c[1]: c[2:] for c in win.commands("icacls.exe")}
        assert grants[str(layout.caddy_storage)][0] == "/inheritance:r"
        assert f"{sid}:(OI)(CI)M" in grants[str(layout.caddy_storage)]
        assert f"{sid}:(OI)(CI)M" in grants[str(layout.caddy_logs)]
        assert grants[str(layout.caddy_dir)][:2] == ["/grant", f"{sid}:(OI)(CI)RX"]
        # No write access to Caddy's binary or the template.
        assert str(layout.helper) not in grants

    def test_a_failed_install_removes_both_services(self, layout, source):
        win = FakeWindows()
        with pytest.raises(service.ServiceError, match="doesn't answer"):
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
        assert service.main(["enable-remote"], services=svc, admin=True) == 2
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
        with pytest.raises(service.ConfigRefused, match=word):
            svc.enable_remote()

    @pytest.mark.parametrize("port", [80, 8501, 8600, 8601, 8756, 70000, 0])
    def test_household_port_rules(self, layout, source, port):
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, FakeWindows(), source, port_check=lambda p: False)
        svc.install()
        with pytest.raises(service.ConfigRefused, match="household port"):
            svc.enable_remote(port)

    def test_a_port_in_use_is_refused(self, layout, source):
        (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, FakeWindows(), source, port_check=lambda p: True)
        svc.install()
        with pytest.raises(service.ConfigRefused, match="already in use"):
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
        assert service.read_config(layout.config_file)
        assert svc.remote_state() == {"household_port": 8610}
        assert "baihe.example.com" in message

    def test_enable_never_adds_a_firewall_rule_and_tells_the_owner_how(self, on, layout):
        svc, win = on
        message = svc.enable_remote(8610)
        # netsh is only ever asked to show a rule (the fake enforces it).
        assert all(c[3] == "show" for c in win.commands("netsh.exe"))
        command = service.firewall_rule_command(layout)
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
        with pytest.raises(service.ServiceError, match="isn't Baihe"):
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
        with pytest.raises(service.ServiceError, match="port 443"):
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
        with pytest.raises(service.ServiceError, match="doesn't answer"):
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
        with pytest.raises(service.ServiceError, match="isn't installed"):
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


class TestPortsMatchTheApp:
    def test_the_pc_listener_port_is_the_apps_default(self):
        from api import api_config
        import page_server
        assert service.ADMIN_PORT == api_config.DEFAULT_PORT
        assert page_server.DEFAULT_PORT in service.RESERVED_PORTS

    def test_the_household_port_may_not_be_one_of_baihes_own(self, monkeypatch, tmp_path):
        from services import settings_service as ss
        monkeypatch.setattr(ss, "_default_env_path", lambda: str(tmp_path / ".env"))
        for name in (ss.API_PORT_ENV, ss.HOUSEHOLD_PORT_ENV):
            monkeypatch.delenv(name, raising=False)
        for port in ss.baihe_own_ports():
            assert service.household_port_problem(port)
        assert service.household_port_problem(service.DEFAULT_HOUSEHOLD_PORT) == ""

    def test_the_household_port_reaches_the_server_only_through_the_service_env(self, layout):
        # The service is where the port is set: .env and machine-wide values
        # are blanked, and the server reads the port from its environment.
        env = service.app_service_env(layout.data, household_port=8610)
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == "8610" and env["BAIHE_API_HOST"] == "127.0.0.1"
        from api import api_config
        settings = api_config.load_settings({k: v for k, v in env.items() if v})
        assert settings.household_port == 8610 and settings.port == service.ADMIN_PORT


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
        with pytest.raises(service.ServiceError, match="run uninstall again"):
            svc.uninstall()
        assert win.services == {} and layout.admin.exists()


class TestRenderCaddyfile:
    TEXT = TEMPLATE.read_text(encoding="utf-8")

    def _render(self, text=None, domain="baihe.example.com", port=8610):
        return service.render_caddyfile(self.TEXT if text is None else text, domain, port,
                                        Path("C:/Program Files/Baihe Studio Services/caddy-logs"))

    def test_renders_the_real_template(self):
        out = self._render()
        assert out.startswith(service.GENERATED_HEADER)
        assert "baihe-access.log" in out and "rate_limit" in out

    def test_keeps_the_pc_only_refusals(self):
        out = self._render()
        for needle in ("respond @pc_only 404", "respond @pc_only_post 404",
                       "respond @pc_only_delete 404", "respond @unclean_path 400"):
            assert needle in out

    @pytest.mark.parametrize("old", ["admin off", "respond @pc_only 404"])
    def test_refuses_a_template_that_lost_a_safeguard(self, old):
        with pytest.raises(service.ServiceError):
            self._render(self.TEXT.replace(old, "# removed"))

    def test_refuses_a_template_that_forwards_elsewhere(self):
        with pytest.raises(service.ServiceError, match="household listener"):
            self._render(self.TEXT.replace("127.0.0.1:{$BAIHE_API_HOUSEHOLD_PORT}", "0.0.0.0:8600"))

    @pytest.mark.parametrize("domain", ["", "a b.example.com", "x.example.com\nrespond 200", "localhost"])
    def test_refuses_a_domain_that_could_change_the_file(self, domain):
        with pytest.raises(service.ConfigRefused):
            self._render(domain=domain)


class TestNothingBeyondLoopback:
    """Nothing here adds a listener, firewall rule, certificate or router
    change. Caddy is started only by `enable-remote`."""

    SOURCE = (ROOT / "installer" / "service.py").read_text(encoding="utf-8")
    CODE = "\n".join(line for line in SOURCE.splitlines() if not line.lstrip().startswith("#"))

    @pytest.mark.parametrize("needle", ["upnp", "nat-pmp", "natpmp", "addportmapping", "0.0.0.0",
                                        "ddns", "dyndns", "certbot", "cloudflare"])
    def test_absent(self, needle):
        code = self.CODE.split('"""', 2)[2]      # the module docstring may say what it does not do
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
        modules = set(re.findall(r"^(?:from|import) (\w+)", self.SOURCE, re.M))
        assert modules <= set(sys.stdlib_module_names)

    def test_every_command_has_a_time_limit(self):
        assert "timeout=timeout" in self.SOURCE and "timeout=2)" in self.SOURCE
