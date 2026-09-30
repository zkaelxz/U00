"""installer/service.py: Baihe Studio as a Windows service
(docs/windows-installer-design.md, "Boot service").

Windows can't run here, so sc.exe and icacls are a fake that keeps the
service's state. These pin what the owner decided: the service starts at
boot as its own least-privilege account, listens on 127.0.0.1:8600 and
nothing else, is restarted after a failure, stops cleanly, and is put back
as it was when an install step fails or removed when an uninstall does not
complete.
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
                      ("wrapper/BaiheStudio.exe", "winsw")):
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
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
        create = next(c for c in win.calls if c[1] == "create")
        assert create[create.index("binPath=") + 1] == f'"{layout.wrapper}"'
        assert create[create.index("start=") + 1] == "disabled"     # until configured
        assert layout.wrapper.read_text(encoding="utf-8") == "winsw"
        assert layout.helper_script.read_text(encoding="utf-8") == "new"
        assert service.read_config(layout.config_file) == {"install_root": str(layout.root),
                                                           "data_dir": str(layout.data)}
        assert ET.parse(layout.wrapper_xml).getroot().findtext("id") == "BaiheStudio"
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
        assert _xml_env(layout.wrapper_xml)["BAIHE_API_ALLOW_KEY_WRITES"] == "1"
        assert sum(1 for c in win.calls if c[1] == "create") == 1

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

    def test_refuses_an_incomplete_setup_folder(self, layout, source):
        (source / "wrapper" / "BaiheStudio.exe").unlink()
        (source / "wrapper").rmdir()
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
        assert "BaiheStudio: RUNNING, start AUTO_START" in capsys.readouterr().out

    def test_status_when_not_installed(self, layout, capsys):
        assert service.main(["status"], services=_services(layout, FakeWindows()), admin=False) == 0
        assert "not installed" in capsys.readouterr().out

    def test_failure_exit_code(self, layout, source, capsys):
        assert service.main(["install"], services=_services(layout, FakeWindows(), source, health=False),
                            admin=True) == 1
        assert "ERROR:" in capsys.readouterr().err

    def test_no_other_commands(self):
        with pytest.raises(SystemExit):
            service.main(["enable-remote"], admin=True)

    def test_only_on_windows(self, monkeypatch):
        monkeypatch.setattr(service.os, "name", "posix")
        assert service.main(["status"]) == 1


class TestNothingBeyondLoopback:
    """The service adds no listener, firewall rule, certificate or router change."""

    SOURCE = (ROOT / "installer" / "service.py").read_text(encoding="utf-8")

    @pytest.mark.parametrize("needle", ["netsh", "firewall", "upnp", "nat-pmp", "natpmp",
                                        "addportmapping", "caddy", "0.0.0.0", "certificate", "dns"])
    def test_absent(self, needle):
        code = "\n".join(line for line in self.SOURCE.splitlines()
                         if not line.lstrip().startswith("#"))
        # The module docstring may say what it does not do.
        code = code.split('"""', 2)[2]
        assert needle not in code.lower()

    def test_standard_library_only(self):
        modules = set(re.findall(r"^(?:from|import) (\w+)", self.SOURCE, re.M))
        assert modules <= set(sys.stdlib_module_names)

    def test_every_command_has_a_time_limit(self):
        assert "timeout=timeout" in self.SOURCE and "timeout=2)" in self.SOURCE
