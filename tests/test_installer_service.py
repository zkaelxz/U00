"""installer/service.py: Baihe Studio as a Windows service, and Caddy for
remote access (docs/windows-installer-design.md, "Boot service").

Windows can't run here, so sc.exe, icacls and netsh are a fake that keeps the
service's state. These pin what the owner decided: the service starts at
boot as its own least-privilege account, listens on 127.0.0.1 (at the
port stored at install) and nothing else, is restarted after a failure, stops cleanly, and is put back
as it was when an install step fails or removed when an uninstall does not
complete. Caddy is installed disabled, only `enable-remote` turns it on,
Baihe's household listener stays on 127.0.0.1 behind it, and nothing here
ever adds a firewall rule: the owner does, by hand.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "installer"))

import service  # noqa: E402
from installer import service, service_cli, service_config, service_control, service_layout, setup_lock  # noqa: E402


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
            self.services[service_layout.APP_SERVICE] = {"state": state, "start": "AUTO_START"}
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
            return self._result(cmd, service_control.ERROR_SERVICE_DOES_NOT_EXIST)
        if verb in self.fail:
            return self._result(cmd, 1)
        if verb == "query":
            return self._result(cmd, 0, f"SERVICE_NAME: {name}\n        STATE              : 4  {svc['state']} \n")
        if verb == "qc":
            return self._result(cmd, 0, f"        START_TYPE         : 2   {svc['start']}\n")
        if verb == "stop":
            if svc["state"] == "STOPPED":
                return self._result(cmd, service_control.ERROR_SERVICE_NOT_ACTIVE)
            if any(o.get("depend") == name and o["state"] == "RUNNING"
                   for o in self.services.values()):
                return self._result(cmd, 1051)    # ERROR_DEPENDENT_SERVICES_RUNNING
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
                svc["start"] = {"auto": "AUTO_START", "disabled": "DISABLED",
                                "demand": "DEMAND_START"}[value]
            elif key == "depend=":
                svc["depend"] = value
            elif key == "obj=":
                svc["account"] = value
            return self._result(cmd)
        if verb in ("sidtype", "privs", "failure", "failureflag", "sdset"):
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
    return service_layout.Layout(root, data, tmp_path / "Program Files" / "Baihe Studio Services")


@pytest.fixture
def source(tmp_path):
    """Setup's extraction: {app}\\service."""
    src = tmp_path / "setup-service"
    for rel, text in (("helper/python/python.exe", "py"), ("helper/lib/installer/service.py", "new"),
                      ("helper/lib/installer/release_public_key.txt", "# key"),
                      ("wrapper/BaiheStudio.exe", "winsw"), ("wrapper/BaiheUpdate.exe", "winsw"),
                      ("caddy/BaiheCaddy.exe", "winsw"),
                      ("caddy/caddy.exe", "caddy")):
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    template = src / "helper" / "lib" / "deploy" / "caddy" / "Caddyfile.template"
    template.parent.mkdir(parents=True)
    template.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return src


def _services(layout, win, source=None, health=True, **kw):
    """`health` is True/False, or a function of the port probed."""
    probe = health if callable(health) else (lambda port: health)
    svc = service.Services(layout, win, health=probe, sleep=lambda s: None,
                           source=source, **kw)
    svc.tree_check = lambda root: None      # file owners can't be read off Windows
    svc.locked = []
    svc.lock_root = lambda root, sid: svc.locked.append((root, sid))
    return svc


def _xml_env(path):
    return {e.get("name"): e.get("value") for e in ET.parse(path).getroot().findall("env")}


class TestServiceSid:
    def test_known_value(self):
        # Windows' own TrustedInstaller service SID.
        assert (service_control.service_sid("TrustedInstaller")
                == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")

    def test_matches_the_workflow_check(self):
        wf = (ROOT / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
        assert service_control.service_sid("BaiheStudio") in wf


class TestWinswXml:
    def test_service(self, layout):
        root = ET.fromstring(service_config.app_service_xml(layout))
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

    def test_only_loopback_on_the_default_port_whatever_the_machine_sets(self, layout):
        env = _xml_env_from(service_config.app_service_xml(layout))
        assert env["BAIHE_API_HOST"] == "127.0.0.1" and env["BAIHE_API_PORT"] == str(service_config.ADMIN_PORT)
        assert env["BAIHE_API_AUTH"] == "off" and env["BAIHE_API_ENV"] == "production"
        # Every other server setting is written out empty, household listener included.
        assert env["BAIHE_API_HOUSEHOLD_PORT"] == ""
        assert set(service_config.API_ENV_NAMES) <= set(env)
        assert env["BAIHE_DATA_DIR"] == str(layout.data)

    def test_key_form_is_off_unless_env_file_opts_in(self, layout):
        assert _xml_env_from(service_config.app_service_xml(layout))["BAIHE_API_ALLOW_KEY_WRITES"] == "0"
        on = _xml_env_from(service_config.app_service_xml(layout, allow_key_writes=True))
        assert on["BAIHE_API_ALLOW_KEY_WRITES"] == "1"

    def test_paths_are_escaped(self, tmp_path):
        lay = service_layout.Layout(tmp_path / "A & <B>", tmp_path / "d", tmp_path / "admin")
        assert ET.fromstring(service_config.app_service_xml(lay)).findtext("executable") == str(lay.python_exe)


def _xml_env_from(text):
    return {e.get("name"): e.get("value") for e in ET.fromstring(text).findall("env")}


class TestEnvFile:
    def test_matches_the_apps_own_parser(self, tmp_path):
        from env_reader import read_env_file
        path = tmp_path / ".env"
        path.write_bytes("\ufeff# comment\n\nA=1\nB = 'two'\nC=\"th=ree\"\nnot a pair\n".encode("utf-8"))
        assert service_config.read_env_file(path) == read_env_file(str(path)) == {
            "A": "1", "B": "two", "C": "th=ree"}

    def test_a_utf16_file_matches_the_apps_own_parser(self, tmp_path):
        from env_reader import read_env_file
        path = tmp_path / ".env"
        path.write_bytes("BAIHE_API_HOUSEHOLD_PORT=8601\r\n".encode("utf-16"))
        assert service_config.read_env_file(path) == read_env_file(str(path)) == {
            "BAIHE_API_HOUSEHOLD_PORT": "8601"}

    def test_a_utf32_file_is_not_read_as_utf16(self, tmp_path):
        path = tmp_path / ".env"
        path.write_bytes("BAIHE_API_HOUSEHOLD_PORT=8601\n".encode("utf-32"))
        (key,) = service_config.read_env_file(path)
        # Unreadable either way, but the UTF-8 path (U+FFFD for the BOM) is what
        # the app's settings code uses for this file too.
        assert key.startswith("\ufffd")

    def test_missing_file(self, tmp_path):
        assert service_config.read_env_file(tmp_path / "none") == {}


class TestFolders:
    @pytest.mark.parametrize("path", ["relative", "C:\\", "C:\\a%b", 'C:\\a"b', ""])
    def test_bad_folders(self, path):
        assert service_layout.folder_problem(path, "folder")

    def test_data_folder_with_foreign_files_is_refused(self, tmp_path):
        (tmp_path / "library").mkdir()
        (tmp_path / "Documents").mkdir()
        assert "Documents" in service_layout.data_folder_contents_problem(tmp_path)

    def test_data_folder_with_only_baihe_items_is_fine(self, layout):
        (layout.data / ".env").write_text("A=1", encoding="utf-8")
        assert service_layout.data_folder_contents_problem(layout.data) == ""

    def test_what_the_app_itself_writes_there_is_not_foreign(self, layout):
        # A queued Diagnostics install or the downloaded browser used to make
        # the next Setup refuse the service ("files Baihe Studio didn't put there").
        import os

        import app_tools
        import browser_support
        import pending_install
        from voice import voice_venv
        from services import android_app_service, sources_save_service, vdb_names_service
        for name in (os.path.basename(pending_install.state_dir()), browser_support.FOLDER_NAME,
                     app_tools.FOLDER_NAME, sources_save_service.SAVE_DIRNAME, voice_venv.VENVS_DIRNAME,
                     android_app_service.FOLDER_NAME):
            (layout.data / name).mkdir()
        (layout.data / vdb_names_service.FILE_NAME).write_text("{}", encoding="utf-8")
        assert service_layout.data_folder_contents_problem(layout.data) == ""

    def test_every_top_level_folder_named_in_the_source_is_allowed(self):
        """A folder the app writes straight under the data folder but the
        installer doesn't list makes Setup refuse the data folder on the next
        upgrade. Only literal names can be found this way; one built from a
        constant is covered by the test above."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        joined = re.compile(r"""portable\.data_dir\(\),\s*["']([^"'/\\]+)["']""")
        allowed = {n.lower() for n in service_layout.DATA_FOLDER_ENTRIES | service_layout.APP_STATE_ENTRIES}
        missing = {}
        for path in [*root.glob("*.py"), *(root / "services").glob("*.py")]:
            for name in joined.findall(path.read_text(encoding="utf-8")):
                if name.lower() not in allowed:
                    missing.setdefault(name, path.name)
        assert not missing, f"not in installer/service_layout.py: {missing}"

    def test_copies_kept_by_a_restore_or_reset_are_not_foreign(self, layout):
        for name in ("library.pre_restore_1700000000", "library.pre_reset_1700000000",
                     ".restore_staging_abc123"):
            (layout.data / name).mkdir()
        assert service_layout.data_folder_contents_problem(layout.data) == ""

    def test_the_lock_file_a_crashed_restore_left_is_not_foreign(self, layout):
        (layout.data / "library.restore-lock").write_bytes(b"")
        assert service_layout.data_folder_contents_problem(layout.data) == ""

    def test_a_folder_only_named_like_a_kept_copy_is_foreign(self, layout):
        for name in ("library.pre_restore_my files", "library.pre_reset_", ".restore_staging_old copy"):
            (layout.data / name).mkdir()
            assert name in service_layout.data_folder_contents_problem(layout.data)
            (layout.data / name).rmdir()

    def test_links_are_refused(self, tmp_path):
        (tmp_path / "real").mkdir()
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
        with pytest.raises(service_layout.ServiceError, match="link"):
            service_layout.refuse_reparse_point(tmp_path / "link")


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
        assert service_config.read_config(layout.config_file) == {"install_root": str(layout.root),
                                                           "data_dir": str(layout.data)}
        assert ET.parse(layout.wrapper_xml("BaiheStudio")).getroot().findtext("id") == "BaiheStudio"
        assert not (layout.admin / "helper.old").exists()

    def test_permissions_are_granted_by_service_sid(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        sid = "*" + service_control.service_sid("BaiheStudio")
        grants = {}
        for c in win.commands("icacls.exe"):
            grants.setdefault(c[1], c[2:])      # the first call per folder is the grant
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
        assert sum(1 for c in win.calls if c[1] == "create") == 3      # once each, not again

    def test_failure_on_a_fresh_install_leaves_nothing(self, layout, source):
        win = FakeWindows()
        with pytest.raises(service_layout.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False).install()
        assert win.services == {}
        assert not layout.helper.exists() and not layout.service_dir.exists()

    def test_failure_on_an_update_puts_the_old_service_back(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.services["BaiheStudio"]["state"] = "STOPPED"
        (source / "helper" / "lib" / "installer" / "service.py").write_text("broken", encoding="utf-8")
        with pytest.raises(service_layout.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False).install()
        assert layout.helper_script.read_text(encoding="utf-8") == "new"
        assert not (layout.admin / "helper.old").exists()
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_a_rollback_whose_old_folder_cant_come_back_leaves_the_service_stopped(
            self, layout, source, monkeypatch):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.services["BaiheStudio"]["state"] = "STOPPED"
        real_rename = Path.rename

        def locked(self, target):
            if self.name == "helper.old":
                raise PermissionError(5, "Access is denied")
            return real_rename(self, target)
        monkeypatch.setattr(Path, "rename", locked)
        with pytest.raises(service_layout.ServiceError, match="doesn't answer"):
            _services(layout, win, source, health=False).install()
        assert win.services["BaiheStudio"]["state"] == "STOPPED"

    def test_a_failed_copy_on_an_update_starts_the_old_service_again(self, layout, source,
                                                                      monkeypatch):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.services["BaiheStudio"]["state"] = "STOPPED"     # Setup stopped it
        real_copytree = service.shutil.copytree

        def full_disk(src, dest, *args, **kw):
            if Path(dest).name == "caddy":
                raise OSError(28, "No space left on device")
            return real_copytree(src, dest, *args, **kw)
        monkeypatch.setattr(service.shutil, "copytree", full_disk)
        with pytest.raises(OSError):
            _services(layout, win, source).install()
        assert layout.helper_script.read_text(encoding="utf-8") == "new"
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_a_failed_copy_whose_old_folder_cant_come_back_leaves_the_service_stopped(
            self, layout, source, monkeypatch):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.services["BaiheStudio"]["state"] = "STOPPED"     # Setup stopped it
        real_copytree, real_rename = service.shutil.copytree, Path.rename

        def full_disk(src, dest, *args, **kw):
            if Path(dest).name == "caddy":
                raise OSError(28, "No space left on device")
            return real_copytree(src, dest, *args, **kw)

        def locked(self, target):
            if self.name == "helper.old":
                raise PermissionError(5, "Access is denied")
            return real_rename(self, target)
        monkeypatch.setattr(service.shutil, "copytree", full_disk)
        monkeypatch.setattr(Path, "rename", locked)
        with pytest.raises(OSError, match="No space"):
            _services(layout, win, source).install()
        assert not layout.helper.exists()
        assert win.services["BaiheStudio"]["state"] == "STOPPED"
        assert any("Couldn't put" in line for line in win.logs)

    def test_a_folder_that_cant_be_put_back_is_logged_not_raised_over_the_cause(
            self, layout, source, monkeypatch):
        win = FakeWindows()
        _services(layout, win, source).install()
        real_rename = Path.rename

        def locked(self, target):
            if self.name == "helper.old":
                raise PermissionError(5, "Access is denied")
            return real_rename(self, target)
        monkeypatch.setattr(Path, "rename", locked)
        (layout.admin / "helper.old").mkdir()
        svc = _services(layout, win, source)
        svc._restore([(layout.admin / "helper", layout.admin / "helper.old")])
        assert any("Couldn't put" in line and "Access is denied" in line for line in win.logs)

    def test_configuration_failure_deletes_a_service_it_created(self, layout, source):
        win = FakeWindows()
        win.fail.add("privs")
        with pytest.raises(service_layout.ServiceError, match="Configuring"):
            _services(layout, win, source).install()
        assert win.services == {}

    def test_permission_failure_rolls_back(self, layout, source):
        win = FakeWindows()
        win.fail.add("icacls")
        with pytest.raises(service_layout.ServiceError, match="permissions"):
            _services(layout, win, source).install()
        assert win.services == {} and not layout.helper.exists()
        assert not layout.caddy_dir.exists()

    def test_refuses_an_incomplete_setup_folder(self, layout, source):
        (source / "caddy" / "caddy.exe").unlink()
        (source / "caddy" / "BaiheCaddy.exe").unlink()
        (source / "caddy").rmdir()
        win = FakeWindows()
        with pytest.raises(service_layout.ServiceError, match="incomplete"):
            _services(layout, win, source).install()
        assert win.services == {}

    def test_refuses_a_data_folder_with_foreign_files(self, layout, source):
        (layout.data / "Documents").mkdir()
        win = FakeWindows()
        with pytest.raises(service_layout.ServiceError, match="Documents"):
            _services(layout, win, source).install()
        assert {c[1] for c in win.calls} <= {"query"}

    def test_a_refused_data_folder_starts_the_stopped_service_again(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        win.services["BaiheStudio"]["state"] = "STOPPED"
        (layout.data / "Documents").mkdir()
        with pytest.raises(service_layout.ServiceError, match="Documents"):
            svc.install()
        assert win.services["BaiheStudio"]["state"] == "RUNNING"

    def test_refuses_when_another_install_owns_the_service(self, layout, source, tmp_path):
        other = tmp_path / "other"
        (other / "app").mkdir(parents=True)
        (other / "app" / "INSTALLED").write_text("x", encoding="utf-8")
        layout.helper.mkdir(parents=True)
        layout.config_file.write_text(
            '{"install_root": %s, "data_dir": "d"}' % __import__("json").dumps(str(other)),
            encoding="utf-8")
        win = FakeWindows(installed=True, state="STOPPED")    # Setup stopped it
        with pytest.raises(service_layout.ServiceError, match="Another Baihe Studio install"):
            _services(layout, win, source).install()
        assert win.services["BaiheStudio"]["state"] == "RUNNING"


class TestStopAndUninstall:
    def test_stop(self, layout):
        win = FakeWindows(installed=True, state="RUNNING")
        _services(layout, win).stop()
        assert win.services["BaiheStudio"]["state"] == "STOPPED"

    def test_a_plain_stop_leaves_the_install_folder_locked(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        svc.stop()
        assert layout.lock_marker.is_file()

    def test_stop_for_update_unlocks_the_install_folder_for_setup(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        svc.stop(for_update=True)
        assert not layout.lock_marker.exists()
        assert win.commands("icacls.exe")[-1][1:] == [str(layout.root), "/reset", "/T", "/L"]

    def test_install_locks_the_install_folder_to_administrators(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        # The root's whole permission list is replaced in one step before any owner changes,
        # and the reset never names the root itself (that would turn its inheritance back on).
        assert svc.locked == [(layout.root, service_control.service_sid("BaiheStudio"))]
        icacls = [c for c in win.commands("icacls.exe") if "/setowner" in c or "/reset" in c][:2]
        assert icacls[0][1:] == [str(layout.root), "/setowner", "*S-1-5-32-544", "/T", "/L"]
        assert icacls[1][1] == str(layout.root / "*") and icacls[1][2] == "/reset"
        assert layout.lock_marker.is_file()

    def test_the_root_permission_list_has_no_inheritance_and_no_users_write(self):
        from installer import service_lock
        sddl = service_lock.root_sddl("S-1-5-80-1")
        assert sddl.startswith("D:P(")      # protected: nothing from the parent
        assert service_lock.write_access_problem("O:BA" + sddl, {"SY", "BA", "S-1-5-80-1"},
                                                 protected=True) is None

    def test_a_lock_that_leaves_a_user_owned_file_fails_the_install(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.tree_check = lambda root: "x.dll is still owned by a user after locking"
        with pytest.raises(service_layout.ServiceError, match="still owned by a user"):
            svc.install()
        assert not layout.lock_marker.exists()
        assert win.services == {}

    def test_a_failed_lock_unlocks_again_and_rolls_the_install_back(self, layout, source):
        win = FakeWindows()
        real = win.__call__

        def fail_on_lock(cmd, timeout=120):
            if "/setowner" in [str(c) for c in cmd]:
                return win._result(cmd, 1)
            return real(cmd, timeout)

        fail_on_lock.log = win.log
        with pytest.raises(service_layout.ServiceError, match="Locking the install folder"):
            service.Services(layout, fail_on_lock, health=lambda p: True, sleep=lambda s: None,
                             source=source).install()
        assert not layout.lock_marker.exists()
        assert win.services == {}

    def test_uninstall_unlocks_the_install_folder_so_the_uninstaller_can_delete_it(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        svc.uninstall()
        assert [str(layout.root), "/reset", "/T", "/L"] in [c[1:] for c in win.commands("icacls.exe")]

    def test_stop_when_not_installed(self, layout):
        assert "Stopped" in _services(layout, FakeWindows()).stop()

    def test_uninstall_removes_service_permissions_and_admin_folder_keeps_data(self, layout, source):
        win = FakeWindows()
        svc = _services(layout, win, source)
        svc.install()
        svc.uninstall()
        assert win.services == {}
        removed = [c[1:] for c in win.commands("icacls.exe") if "/remove:g" in c]
        sid = "*" + service_control.service_sid("BaiheStudio")
        assert [str(layout.root), "/remove:g", sid, "/L"] in removed
        assert [str(layout.data), "/remove:g", sid, "/L"] in removed
        assert not layout.admin.exists()
        assert (layout.data / "library" / "library.db").is_file()

    def test_uninstall_by_another_install_leaves_the_owners_service(self, layout, source, tmp_path):
        win = FakeWindows()
        _services(layout, win, source).install()
        (layout.root / "app").mkdir()
        (layout.root / "app" / "INSTALLED").write_text("x", encoding="utf-8")
        calls = len(win.calls)
        other = tmp_path / "Other Baihe"
        message = _services(layout, win).uninstall(install_root=str(other))
        assert "Left in place" in message and str(layout.root) in message
        assert len(win.calls) == calls
        assert "BaiheStudio" in win.services and layout.admin.exists()

    def test_uninstall_by_the_owner_or_over_a_gone_owner_removes_it(self, layout, source, tmp_path):
        win = FakeWindows()
        _services(layout, win, source).install()
        (layout.root / "app").mkdir()
        (layout.root / "app" / "INSTALLED").write_text("x", encoding="utf-8")
        _services(layout, win).uninstall(install_root=str(layout.root))
        assert win.services == {} and not layout.admin.exists()
        # The owning install was uninstalled without removing it: nobody owns
        # it, so whichever install asks takes it away.
        _services(layout, win, source).install()
        (layout.root / "app" / "INSTALLED").unlink()
        _services(layout, win).uninstall(install_root=str(tmp_path / "Other Baihe"))
        assert win.services == {}

    def test_the_cli_passes_the_asking_install_to_uninstall(self, layout, source, tmp_path):
        win = FakeWindows()
        _services(layout, win, source).install()
        (layout.root / "app").mkdir()
        (layout.root / "app" / "INSTALLED").write_text("x", encoding="utf-8")
        other = str(tmp_path / "Other Baihe")
        assert service_cli.main(["--install-root", other, "uninstall"],
                                services=_services(layout, win), admin=True) == 0
        assert "BaiheStudio" in win.services

    def test_a_junction_data_folder_is_refused_before_anything_changes(self, layout, source, tmp_path):
        win = FakeWindows()
        _services(layout, win, source).install()
        calls = len(win.calls)
        real = tmp_path / "real"
        real.mkdir()
        layout.data.rename(tmp_path / "moved")
        layout.data.symlink_to(real, target_is_directory=True)
        with pytest.raises(service_layout.ServiceError, match="link"):
            _services(layout, win).uninstall()
        assert len(win.calls) == calls and "BaiheStudio" in win.services and layout.admin.exists()

    def test_a_failed_revoke_is_reported_after_the_rest_is_removed(self, layout, source):
        win = FakeWindows()
        _services(layout, win, source).install()
        win.fail.add("icacls")
        with pytest.raises(service_layout.ServiceError, match="still on that folder"):
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
        with pytest.raises(service_layout.ServiceError, match="Stopping"):
            _services(layout, win).uninstall()
        assert "BaiheStudio" in win.services and layout.admin.exists()
        assert not win.commands("icacls.exe")

    def test_a_failed_removal_starts_the_service_again(self, layout):
        win = FakeWindows(installed=True, state="RUNNING")
        win.fail.add("delete")
        with pytest.raises(service_layout.ServiceError, match="Removing"):
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
        monkeypatch.setattr(service_layout, "_delete_on_reboot", scheduled.append)
        return scheduled

    def test_removes_everything(self, tmp_path):
        folder = self._folder(tmp_path)
        service_layout.remove_admin_folder(folder)
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
        monkeypatch.setattr(service_layout.shutil, "rmtree", rmtree)
        service_layout.remove_admin_folder(folder)
        assert not folder.exists()
        park = [p for p in folder.parent.iterdir() if p.name.startswith(".baihe-removing-")]
        assert len(park) == 1 and len(park[0].name) == len(".baihe-removing-") + 16
        assert scheduled == [park[0] / "0-python.exe", park[0]]

    def test_a_park_name_that_already_exists_is_refused(self, tmp_path, monkeypatch):
        folder = self._folder(tmp_path)
        (tmp_path / "evil").mkdir()
        (folder.parent / ".baihe-removing-fixed").symlink_to(tmp_path / "evil", target_is_directory=True)
        monkeypatch.setattr(service_layout.secrets, "token_hex", lambda n: "fixed")
        scheduled = self._lock(monkeypatch, folder / "service.log")
        service_layout.remove_admin_folder(folder)
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
        monkeypatch.setattr(service_layout, "_delete_on_reboot", scheduled.append)
        service_layout.remove_admin_folder(folder)
        # One file is scheduled in place (its rename failed); the other was parked.
        other = next(p for p in (folder / "a.dll", folder / "b.dll") if p != first[0])
        assert scheduled == [first[0]] and other not in scheduled


class TestFirstInstallLog:
    def test_the_log_exists_after_a_failed_first_install(self, tmp_path, monkeypatch):
        admin = tmp_path / "Program Files" / "Baihe Studio Services"
        admin.parent.mkdir()
        src = tmp_path / "Setup" / "service" / "helper" / "lib" / "installer"
        src.mkdir(parents=True)
        monkeypatch.setattr(service_layout, "program_files", lambda: admin.parent)
        monkeypatch.setattr(service_cli, "program_files", lambda: admin.parent)
        monkeypatch.setattr(service_cli, "APP_DIR", src.parent)
        monkeypatch.setattr(service_layout, "system_folders", lambda: [])
        root, data = tmp_path / "app", tmp_path / "data"
        root.mkdir()
        args = type("A", (), {"command": "install", "install_root": str(root), "data_dir": str(data)})()
        services = service_cli.build_services(args)
        services.run.log("service.py install")
        assert (admin / service_layout.LOG_FILE_NAME).is_file()


class TestKnownFolders:
    def test_the_admin_copy_must_be_where_windows_says(self, tmp_path, monkeypatch):
        monkeypatch.setattr(service_layout, "program_files", lambda: tmp_path / "Program Files")
        monkeypatch.setattr(service_cli, "program_files", lambda: tmp_path / "Program Files")
        copy = tmp_path / "elsewhere" / "helper" / "lib"
        (copy / "installer").mkdir(parents=True)
        (tmp_path / "elsewhere" / "helper" / service_layout.CONFIG_FILE_NAME).write_text(
            '{"install_root": "C:\\\\a", "data_dir": "C:\\\\b"}', encoding="utf-8")
        monkeypatch.setattr(service_cli, "APP_DIR", copy)
        args = type("A", (), {"command": "uninstall"})()
        with pytest.raises(service_layout.ServiceError, match="isn't in"):
            service_cli.build_services(args)

    def test_windows_is_asked_not_the_environment(self):
        src = (ROOT / "installer" / "service_layout.py").read_text(encoding="utf-8")
        code = src.split("def _known_folder", 1)[1].split("FOLDERID_PROGRAM_FILES =", 1)[0]
        assert "SHGetKnownFolderPath" in code and code.count("os.environ") == 1


class TestMain:
    def test_needs_administrator_rights(self, layout):
        win = FakeWindows(installed=True, state="RUNNING")
        assert service_cli.main(["stop"], services=_services(layout, win), admin=False) == 3
        assert win.calls == []

    def test_status_needs_no_rights(self, layout, capsys):
        win = FakeWindows(installed=True, state="RUNNING")
        assert service_cli.main(["status"], services=_services(layout, win), admin=False) == 0
        out = capsys.readouterr().out
        assert "BaiheStudio: RUNNING, start AUTO_START" in out
        assert "BaiheCaddy: not installed" in out and "Remote access: off" in out

    def test_status_when_not_installed(self, layout, capsys):
        assert service_cli.main(["status"], services=_services(layout, FakeWindows()), admin=False) == 0
        assert "not installed" in capsys.readouterr().out

    def test_failure_exit_code(self, layout, source, capsys):
        assert service_cli.main(["install"], services=_services(layout, FakeWindows(), source, health=False),
                            admin=True) == 1
        assert "ERROR:" in capsys.readouterr().err

    def test_no_other_commands(self):
        with pytest.raises(SystemExit):
            service_cli.main(["open-firewall"], admin=True)

    def test_only_on_windows(self, monkeypatch):
        monkeypatch.setattr(service_cli.os, "name", "posix")
        assert service_cli.main(["status"]) == 1


class FakeMutexes:
    """Named mutexes: `existing` holds every name some process has open."""

    def __init__(self, existing=()):
        self.existing = set(existing)

    def create(self, name):
        if name in self.existing:
            raise setup_lock.SetupRunning(name)
        self.existing.add(name)
        return name

    def close(self, handle):
        self.existing.discard(handle)


class SessionZeroMutexes(FakeMutexes):
    """A service's session has one namespace: a plain name is the Global\\ one."""

    def shares_global_namespace(self):
        return True

    def create(self, name):
        return super().create(name.removeprefix("Global\\"))


class TestSetupLock:
    """set-port, enable-remote and disable-remote hold Setup's own mutex
    names while they run and are refused while anyone else holds them;
    install, stop and uninstall (which Setup runs while holding them) are
    not."""

    LOCKED = (["set-port", "8711"], ["enable-remote"], ["disable-remote"])

    def _installed(self, layout, source, remote=False):
        win = FakeWindows()
        if remote:
            (layout.data / ".env").write_text(SIGN_IN, encoding="utf-8")
        svc = _services(layout, win, source, is_baihe=lambda p, d: True,
                        port_check=lambda p: p == 443)
        svc.install()
        if remote:
            svc.enable_remote(8610)
        return svc, win

    def _snapshot(self, layout):
        return {p: p.read_bytes() for p in layout.admin.rglob("*") if p.is_file()}

    def test_the_names_are_setups(self):
        iss = (ROOT / "installer" / "baihe.iss").read_text(encoding="utf-8")
        names = re.search(r"^SetupMutex=(.*)$", iss, re.M).group(1).strip()
        assert tuple(n.strip() for n in names.split(",")) == setup_lock.SETUP_MUTEX_NAMES

    @pytest.mark.parametrize("argv", LOCKED, ids=lambda a: a[0])
    @pytest.mark.parametrize("name", ["BaiheStudioSetupMutex", "Global\\BaiheStudioSetupMutex"])
    def test_refused_while_setup_runs(self, layout, source, argv, name, capsys):
        svc, win = self._installed(layout, source, remote=True)
        before, calls = self._snapshot(layout), len(win.calls)
        mutexes = FakeMutexes({name})
        assert service_cli.main(argv, services=svc, admin=True, mutexes=mutexes) == 2
        assert "Setup is running" in capsys.readouterr().err
        assert self._snapshot(layout) == before
        assert win.calls[calls:] == []
        # Setup's name is still there; anything this command made is gone.
        assert mutexes.existing == {name}

    @pytest.mark.parametrize("argv", LOCKED, ids=lambda a: a[0])
    def test_held_while_the_command_runs_and_released_after(self, layout, source, argv,
                                                             monkeypatch):
        svc, win = self._installed(layout, source, remote=True)
        mutexes, seen = FakeMutexes(), []
        method = argv[0].replace("-", "_")
        real = getattr(svc, method)

        def recording(*a):
            seen.append(set(mutexes.existing))
            return real(*a)
        monkeypatch.setattr(svc, method, recording)
        assert service_cli.main(argv, services=svc, admin=True, mutexes=mutexes) == 0
        assert seen == [set(setup_lock.SETUP_MUTEX_NAMES)]
        assert mutexes.existing == set()

    def test_released_after_a_failure(self, layout, source):
        svc, win = self._installed(layout, source)
        svc.health = lambda p: p != 8711
        mutexes = FakeMutexes()
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True, mutexes=mutexes) == 1
        assert mutexes.existing == set()

    def test_released_after_ctrl_c(self, layout, source):
        svc, win = self._installed(layout, source)
        held = []

        def health(port):
            if port == 8711:
                held.append(set(mutexes.existing))
                raise KeyboardInterrupt
            return True
        svc.health = health
        mutexes = FakeMutexes()
        with pytest.raises(KeyboardInterrupt):
            service_cli.main(["set-port", "8711"], services=svc, admin=True, mutexes=mutexes)
        assert held == [set(setup_lock.SETUP_MUTEX_NAMES)]
        assert mutexes.existing == set()
        assert service_config.stored_api_port(layout.config_file) == service_config.ADMIN_PORT

    def test_a_second_command_is_refused_while_one_runs(self, layout, source, capsys):
        svc, win = self._installed(layout, source)
        mutexes = FakeMutexes()
        with setup_lock.setup_lock(mutexes):
            before = self._snapshot(layout)
            assert service_cli.main(["set-port", "8711"], services=svc, admin=True,
                                mutexes=mutexes) == 2
            assert "try again when it has finished" in capsys.readouterr().err
            assert self._snapshot(layout) == before
        assert mutexes.existing == set()
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True, mutexes=mutexes) == 0
        assert svc.api_port() == 8711

    @pytest.mark.parametrize("argv", [["install"], ["stop"], ["uninstall"], ["status"]],
                             ids=lambda a: a[0])
    def test_setups_own_steps_and_status_do_not_wait_for_it(self, layout, source, argv):
        svc, win = self._installed(layout, source)
        if argv == ["install"]:
            for name in win.services:
                win.services[name]["state"] = "STOPPED"     # Setup stopped them
        mutexes = FakeMutexes(setup_lock.SETUP_MUTEX_NAMES)
        assert service_cli.main(argv, services=svc, admin=True, mutexes=mutexes) == 0
        assert mutexes.existing == set(setup_lock.SETUP_MUTEX_NAMES)

    def test_a_refused_app_update_is_logged_with_the_name_and_leaves_a_result(self, layout, source):
        svc, win = self._installed(layout, source)
        recorded = []
        svc.record_update_refused = lambda: recorded.append(True)
        lines = []
        svc.run.log = lines.append
        mutexes = FakeMutexes({setup_lock.SETUP_MUTEX_NAMES[1]})
        assert service_cli.main(["apply-app-update"], services=svc, admin=True, mutexes=mutexes) == 2
        assert recorded == [True]
        assert any("Global\\BaiheStudioSetupMutex" in line for line in lines)

    def test_a_service_session_does_not_collide_with_its_own_two_names(self):
        mutexes = SessionZeroMutexes()
        with setup_lock.setup_lock(mutexes):
            assert mutexes.existing == {"BaiheStudioSetupMutex"}
        assert mutexes.existing == set()

    def test_a_service_session_is_still_kept_out_by_an_open_setup(self):
        mutexes = SessionZeroMutexes({"BaiheStudioSetupMutex"})
        with pytest.raises(setup_lock.SetupRunning):
            with setup_lock.setup_lock(mutexes):
                pass

    def test_a_mutex_error_changes_nothing(self, layout, source, capsys):
        svc, win = self._installed(layout, source)
        before, calls = self._snapshot(layout), len(win.calls)

        class Broken(FakeMutexes):
            def create(self, name):
                raise OSError("no mutex")
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True, mutexes=Broken()) == 1
        assert "no mutex" in capsys.readouterr().err
        assert self._snapshot(layout) == before and win.calls[calls:] == []

    @pytest.mark.skipif(sys.platform == "win32", reason="the non-Windows stand-in")
    def test_a_no_op_off_windows(self, layout, source):
        assert isinstance(setup_lock.default_mutexes(), setup_lock.NoMutexes)
        svc, win = self._installed(layout, source)
        assert service_cli.main(["set-port", "8711"], services=svc, admin=True) == 0
        assert svc.api_port() == 8711


class TestServiceMenuScript:
    """installer/service_menu.ps1 can't run here; these pin what its
    elevated runs may contain."""

    @pytest.fixture
    def ps1(self):
        return (ROOT / "installer" / "service_menu.ps1").read_text(encoding="utf-8")

    def test_ascii_only(self, ps1):
        # Windows PowerShell reads a file without a BOM in the ANSI code page.
        assert ps1.isascii()

    def test_elevates_only_the_admin_folders_copy(self, ps1):
        assert '$Admin = Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "Baihe Studio Services"' in ps1
        assert '$Self = Join-Path $Admin "helper\\lib\\installer\\service_menu.ps1"' in ps1
        assert "$PSScriptRoot" not in ps1
        elevations = re.findall(r"-Verb RunAs -ArgumentList \(\s*'([^)]*)\)", ps1)
        assert elevations == ["-NoProfile -ExecutionPolicy Bypass -File \"' + $Self + '\"'"]
        # The elevated menu refuses to run from any other copy.
        assert "[string]::Equals($PSCommandPath, $Self" in ps1

    def test_runs_the_admin_folders_python_with_fixed_words_and_checked_numbers(self, ps1):
        assert '$Python = Join-Path $Admin "helper\\python\\python.exe"' in ps1
        assert "-cmatch '\\A[0-9]{1,5}\\z'" in ps1
        calls = re.findall(r"Invoke-Service @\(([^)]*)\)", ps1)
        assert calls and all(re.fullmatch(r'"[a-z-]+"(, "--household-port")?(, \[string\]\$n)?', c)
                             for c in calls), calls
        assert {re.match(r'"([a-z-]+)"', c).group(1) for c in calls} == {
            "status", "set-port", "enable-remote", "disable-remote"}
        # Every number reaches service.py only through Get-CheckedPort (or the default).
        assert ps1.count("Get-CheckedPort $answer") == 2

    def test_warns_that_the_variable_does_not_move_the_service(self, ps1):
        assert "BAIHE_API_PORT environment variable does NOT change" in ps1
        assert "neither does running Setup again" in ps1

    def test_starts_no_program_but_its_own_elevation(self, ps1):
        # The elevated menu prints the log folder rather than opening
        # explorer.exe with administrator rights.
        assert ps1.count("Start-Process") == 1 and "-Verb RunAs" in ps1
        assert "explorer.exe" not in ps1.lower() and "Invoke-Item" not in ps1


class TestTreeProblem:
    GOOD = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FRFX;;;BU)(A;OICI;RC;;;OW)"

    def _check(self, root, **kw):
        from installer import service_lock
        kw.setdefault("owner", lambda p: "S-1-5-32-544")
        kw.setdefault("sddl", lambda p: self.GOOD)
        return service_lock.tree_problem(root, **kw)

    def test_a_locked_tree_is_fine(self, tmp_path):
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "f").write_text("x")
        assert self._check(str(tmp_path)) is None

    def test_a_user_owned_entry_is_named(self, tmp_path):
        (tmp_path / "f").write_text("x")
        got = self._check(str(tmp_path), owner=lambda p: "S-1-5-21-1-2-3-1001" if p.endswith("f")
                          else "S-1-5-18")
        assert "still owned by a user" in got

    def test_an_entry_that_lets_a_user_write_is_named_even_when_owned_by_admins(self, tmp_path):
        (tmp_path / "f").write_text("x")
        bad = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;AU)"
        got = self._check(str(tmp_path), sddl=lambda p: bad if p.endswith("f") else self.GOOD)
        assert "can write" in got

    def test_an_unprotected_root_is_named(self, tmp_path):
        got = self._check(str(tmp_path), sddl=lambda p: "O:BAD:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")
        assert "inherits permissions" in got

    def test_a_hard_linked_file_is_named(self, tmp_path):
        (tmp_path / "f").write_text("x")
        os.link(tmp_path / "f", tmp_path / "g")
        assert "hard link" in self._check(str(tmp_path))

    def test_a_folder_that_cannot_be_listed_is_named(self, tmp_path, monkeypatch):
        (tmp_path / "locked").mkdir()
        real = os.scandir

        def scandir(path):
            if str(path).endswith("locked"):
                raise PermissionError(13, "denied", str(path))
            return real(path)

        monkeypatch.setattr(os, "scandir", scandir)
        assert "can't be listed" in self._check(str(tmp_path))

    def test_a_link_is_refused(self, tmp_path, monkeypatch):
        (tmp_path / "junction").mkdir()
        real = os.lstat
        monkeypatch.setattr(os, "lstat", lambda p: type("S", (), {
            "st_file_attributes": 0x400 if str(p).endswith("junction") else 0,
            "st_nlink": 1})() if "junction" in str(p) else real(p))
        assert "is a link" in self._check(str(tmp_path))


class TestHoldDirectory:
    def test_every_folder_above_is_held_outermost_first_and_released_in_reverse(self, tmp_path):
        from installer import service_lock
        events = []
        target = tmp_path / "Programs" / "Baihe Studio"
        target.mkdir(parents=True)
        with service_lock.hold_directory(target, is_windows=True,
                                         open_held=lambda p: events.append(("open", p)) or str(p),
                                         close_held=lambda h: events.append(("close", h))):
            events.append(("work", None))
        opened = [e[1] for e in events if e[0] == "open"]
        assert opened[-1] == target and opened[0] == Path(target.anchor)
        assert opened == sorted(opened, key=lambda p: len(p.parts))
        assert events[len(opened)] == ("work", None)
        assert [str(e[1]) for e in events if e[0] == "close"] == [str(p) for p in reversed(opened)]

    def test_a_link_among_the_folders_above_refuses_and_still_releases(self, tmp_path, monkeypatch):
        from installer import service_lock
        target = tmp_path / "Programs" / "Baihe Studio"
        target.mkdir(parents=True)
        closed = []
        real = os.lstat
        monkeypatch.setattr(os, "lstat", lambda p: type("S", (), {"st_file_attributes": 0x400})()
                            if str(p).endswith("Programs") else real(p))
        with pytest.raises(service_layout.ServiceError, match="is a link"):
            with service_lock.hold_directory(target, is_windows=True,
                                             open_held=lambda p: str(p), close_held=closed.append):
                pytest.fail("must not run")
        assert closed       # nothing stays held

    def test_a_folder_that_cannot_be_opened_stops_it_and_releases_the_rest(self, tmp_path):
        from installer import service_lock
        closed = []

        def open_held(p):
            if p == tmp_path:
                raise service_layout.ServiceError("can't be held open")
            return str(p)

        with pytest.raises(service_layout.ServiceError, match="held open"):
            with service_lock.hold_directory(tmp_path, is_windows=True, open_held=open_held,
                                             close_held=closed.append):
                pytest.fail("must not run")
        assert len(closed) == len(service_lock.ancestors(tmp_path)) - 1
