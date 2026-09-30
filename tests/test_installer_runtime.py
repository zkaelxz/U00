"""Step 80b: the installed app's runtime scripts -- installer/launcher.py
(the Start-menu shortcut) and installer/postinstall.py (the install step).
No real server, browser, pip or Windows process calls: those are faked."""
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "installer"))

import launcher  # noqa: E402
import portable  # noqa: E402
import postinstall  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv(portable.DATA_DIR_ENV, str(d))
    return d


class TestServerEnv:
    def test_forces_loopback_and_sets_defaults(self):
        env = launcher.server_env({"BAIHE_API_HOST": "0.0.0.0", "PIP_USER": "1",
                                   "PIP_REQUIRE_VIRTUALENV": "true", "PATH": "x"})
        assert env["BAIHE_API_HOST"] == "127.0.0.1"
        assert env["BAIHE_API_ALLOW_KEY_WRITES"] == "1"
        assert env["BAIHE_API_PORT"] == "8600"
        assert env["PYTHONNOUSERSITE"] == "1"
        assert "PIP_USER" not in env and "PIP_REQUIRE_VIRTUALENV" not in env
        assert env["PATH"] == "x"

    def test_respects_explicit_choices(self):
        env = launcher.server_env({"BAIHE_API_ALLOW_KEY_WRITES": "0", "BAIHE_API_PORT": "8601"})
        assert env["BAIHE_API_ALLOW_KEY_WRITES"] == "0"
        assert launcher.port_from_env(env) == 8601

    @pytest.mark.parametrize("bad", ["abc", "0", "70000"])
    def test_bad_port(self, bad):
        with pytest.raises(launcher.LaunchError):
            launcher.port_from_env({"BAIHE_API_PORT": bad})

    def test_server_command(self):
        assert launcher.server_command("py.exe") == ["py.exe", "-s", "-m", "api"]


class TestBrowser:
    def test_edge_before_chrome(self):
        cands = launcher.browser_candidates({"ProgramFiles": "P", "ProgramFiles(x86)": "X",
                                             "LOCALAPPDATA": "L"})
        assert "msedge" in cands[0] and "chrome" in cands[-1]
        assert launcher.find_app_browser(cands, exists=lambda p: "chrome" in p) == cands[3]
        assert launcher.find_app_browser(cands, exists=lambda p: True) == cands[0]
        assert launcher.find_app_browser(cands, exists=lambda p: False) is None


class TestStopServer:
    def _pid(self, data_dir, pid="4242"):
        d = data_dir / "launcher"
        d.mkdir(parents=True, exist_ok=True)
        (d / launcher.PID_FILE_NAME).write_text(pid)
        return d / launcher.PID_FILE_NAME

    def test_kills_our_own_server(self, data_dir, tmp_path):
        py = str(tmp_path / "python" / "python.exe")
        pid_file = self._pid(data_dir)
        alive = {4242: py}
        killed = []

        def kill(pid):
            killed.append(pid)
            alive.pop(pid)
        msg = launcher.stop_server(py, image_of=alive.get, kill=kill, sleep=lambda s: None)
        assert killed == [4242]
        assert "Stopped" in msg
        assert not pid_file.exists()

    def test_leaves_a_reused_pid_alone(self, data_dir, tmp_path):
        pid_file = self._pid(data_dir)
        killed = []
        msg = launcher.stop_server(str(tmp_path / "python.exe"),
                                   image_of=lambda pid: r"C:\Windows\notepad.exe",
                                   kill=killed.append)
        assert killed == []
        assert "isn't running" in msg
        assert not pid_file.exists()

    def test_nothing_to_stop(self, data_dir, tmp_path):
        killed = []
        assert "isn't running" in launcher.stop_server(str(tmp_path / "python.exe"),
                                                       image_of=lambda p: None, kill=killed.append)
        self._pid(data_dir, "not a number")
        assert "isn't running" in launcher.stop_server(str(tmp_path / "python.exe"),
                                                       image_of=lambda p: None, kill=killed.append)
        assert killed == []


class TestLaunch:
    def test_already_running_just_opens_a_window(self, data_dir, monkeypatch):
        opened, started = [], []
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: True)
        monkeypatch.setattr(launcher, "start_server", lambda *a: started.append(a))
        monkeypatch.setattr(launcher, "open_window", opened.append)
        assert launcher.launch() == 0
        assert started == [] and opened == ["http://127.0.0.1:8600/"]

    def test_port_taken_by_something_else(self, data_dir, monkeypatch):
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)
        monkeypatch.setattr(launcher, "port_open", lambda port: True)
        with pytest.raises(launcher.LaunchError, match="already using port"):
            launcher.launch()

    def test_starts_waits_and_opens(self, data_dir, monkeypatch):
        calls = []
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)
        monkeypatch.setattr(launcher, "port_open", lambda port: False)
        monkeypatch.setattr(launcher, "start_server",
                            lambda py, env, headless: calls.append(("start", env["BAIHE_API_HOST"], headless)))
        monkeypatch.setattr(launcher, "wait_for_health", lambda port, proc: True)
        monkeypatch.setattr(launcher, "open_window", lambda url: calls.append(("open", url)))
        assert launcher.launch() == 0
        assert calls == [("start", "127.0.0.1", False), ("open", "http://127.0.0.1:8600/")]

    def test_headless_never_opens_a_window(self, data_dir, monkeypatch):
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)
        monkeypatch.setattr(launcher, "port_open", lambda port: False)
        monkeypatch.setattr(launcher, "start_server", lambda py, env, headless: None)
        monkeypatch.setattr(launcher, "wait_for_health", lambda port, proc: True)
        monkeypatch.setattr(launcher, "open_window", lambda url: pytest.fail("opened a window"))
        assert launcher.launch(headless=True) == 0

    def test_server_never_answers(self, data_dir, monkeypatch):
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)
        monkeypatch.setattr(launcher, "port_open", lambda port: False)
        monkeypatch.setattr(launcher, "start_server", lambda py, env, headless: None)
        monkeypatch.setattr(launcher, "wait_for_health", lambda port, proc: False)
        with pytest.raises(launcher.LaunchError, match="server.log"):
            launcher.launch(headless=True)

    def test_main_reports_errors_without_raising(self, data_dir, monkeypatch):
        shown = []
        monkeypatch.setattr(launcher, "launch", lambda headless: (_ for _ in ()).throw(
            launcher.LaunchError("nope")))
        monkeypatch.setattr(launcher, "show_message", shown.append)
        assert launcher.main([]) == 1
        assert shown == ["nope"]


class TestWaitForHealth:
    def test_stops_waiting_when_the_server_exits(self, monkeypatch):
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)

        class Dead:
            def poll(self):
                return 1
        slept = []
        assert launcher.wait_for_health(8600, Dead(), tries=50, sleep=slept.append) is False
        assert slept == []

    def test_answers_after_a_while(self, monkeypatch):
        answers = iter([False, False, True])
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: next(answers))
        assert launcher.wait_for_health(8600, None, tries=5, sleep=lambda s: None) is True


class TestStartServer:
    def test_records_the_pid_and_runs_python_m_api(self, data_dir, monkeypatch):
        seen = {}

        class P:
            pid = 999

        def fake_popen(cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs
            return P()
        monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
        launcher.start_server("py", {"BAIHE_API_HOST": "127.0.0.1"}, headless=True)
        assert seen["cmd"] == ["py", "-s", "-m", "api"]
        assert seen["kwargs"]["cwd"] == str(launcher.APP_DIR)
        assert (data_dir / "launcher" / launcher.PID_FILE_NAME).read_text() == "999"


# ---------------------------------------------------------------- postinstall


class TestValidateDataDir:
    def test_ok(self, tmp_path):
        assert postinstall.validate_data_dir(str(tmp_path / "data"), tmp_path / "app") == \
            Path(os.path.abspath(tmp_path / "data"))

    @pytest.mark.parametrize("bad", ["", "relative/path"])
    def test_needs_a_full_path(self, tmp_path, bad):
        with pytest.raises(postinstall.PostInstallError) as e:
            postinstall.validate_data_dir(bad, tmp_path / "app")
        assert e.value.code == 2

    def test_not_a_drive_root(self, tmp_path):
        with pytest.raises(postinstall.PostInstallError, match="whole drive"):
            postinstall.validate_data_dir(os.path.abspath(os.sep), tmp_path / "app")

    @pytest.mark.parametrize("rel", ["app", "app/data", "app/app/library"])
    def test_not_inside_the_install_folder(self, tmp_path, rel):
        with pytest.raises(postinstall.PostInstallError, match="inside the install folder"):
            postinstall.validate_data_dir(str(tmp_path / rel), tmp_path / "app")

    def test_a_sibling_with_a_shared_prefix_is_fine(self, tmp_path):
        postinstall.validate_data_dir(str(tmp_path / "app-data"), tmp_path / "app")


class TestMarker:
    def test_portable_reads_what_postinstall_writes(self, tmp_path, monkeypatch):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        data = tmp_path / "Dữ liệu"   # non-ASCII survives (utf-8 with BOM)
        marker = postinstall.write_marker(app_dir, data)
        assert marker.read_bytes().startswith(b"\xef\xbb\xbf")
        monkeypatch.setattr(portable, "_INSTALLED_MARKER_PATH", str(marker))
        monkeypatch.delenv(portable.DATA_DIR_ENV, raising=False)
        assert portable.data_dir() == str(data)


class TestPipEnvAndCommands:
    def test_pip_env_drops_redirecting_settings(self):
        env = postinstall.pip_env({"PIP_INDEX_URL": "https://evil", "PIP_USER": "1",
                                   "PIP_REQUIRE_VIRTUALENV": "1", "PATH": "x"})
        for name in ("PIP_INDEX_URL", "PIP_USER", "PIP_REQUIRE_VIRTUALENV"):
            assert name not in env
        assert env["PYTHONNOUSERSITE"] == "1" and env["PATH"] == "x"

    def test_commands_are_offline_and_isolated(self, tmp_path):
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        (wheels / "pip-26.2-py3-none-any.whl").write_bytes(b"")
        app = tmp_path / "app"
        app.mkdir()
        (app / "constraints.txt").write_text("")
        boot = postinstall.bootstrap_pip_command("py", wheels)
        core = postinstall.core_install_command("py", wheels, app)
        for cmd in (boot, core):
            assert cmd[:2] == ["py", "-s"]
            assert "--no-index" in cmd and "--find-links" in cmd
        assert boot[2].endswith(os.path.join("pip-26.2-py3-none-any.whl", "pip"))
        assert core[core.index("-r") + 1] == str(app / "requirements-core.txt")
        assert core[core.index("-c") + 1] == str(app / "constraints.txt")
        (app / "constraints.lock.txt").write_text("")
        core = postinstall.core_install_command("py", wheels, app)
        assert core[core.index("-c") + 1] == str(app / "constraints.lock.txt")

    def test_no_pip_wheel(self, tmp_path):
        with pytest.raises(postinstall.PostInstallError) as e:
            postinstall.bootstrap_pip_command("py", tmp_path)
        assert e.value.code == 3


class TestRun:
    def _layout(self, tmp_path):
        root = tmp_path / "Baihe Studio"
        app = root / "app"
        app.mkdir(parents=True)
        wheels = tmp_path / "wheels"
        wheels.mkdir()
        (wheels / "pip-26-py3-none-any.whl").write_bytes(b"")
        return app, wheels, tmp_path / "data"

    def test_success(self, tmp_path):
        app, wheels, data = self._layout(tmp_path)
        cmds = []

        def runner(cmd, log, env, cwd):
            cmds.append(cmd)
            return 0
        assert postinstall.run(wheels, str(data), python_exe="py", app_dir=app, runner=runner) == 0
        assert (app / "INSTALLED").read_text(encoding="utf-8-sig").splitlines()[0] == str(data)
        log = (data / "launcher" / "install.log").read_text(encoding="utf-8")
        assert "Install finished OK." in log
        assert "pip" in cmds[0][2] and cmds[1][2:4] == ["-m", "pip"]
        assert cmds[2][2] == "-c"
        assert cmds[3][-1].endswith("check_setup.py")

    @pytest.mark.parametrize("fail_at,code", [(0, 3), (1, 4), (2, 5)])
    def test_failures_stop_with_their_code(self, tmp_path, fail_at, code):
        app, wheels, data = self._layout(tmp_path)
        count = {"n": 0}

        def runner(cmd, log, env, cwd):
            n = count["n"]
            count["n"] += 1
            return 1 if n == fail_at else 0
        with pytest.raises(postinstall.PostInstallError) as e:
            postinstall.run(wheels, str(data), python_exe="py", app_dir=app, runner=runner)
        assert e.value.code == code
        assert count["n"] == fail_at + 1
        # The marker is written first, so a half-finished install still
        # never puts the library in the program folder.
        assert (app / "INSTALLED").is_file()

    def test_check_setup_failure_is_not_fatal(self, tmp_path):
        app, wheels, data = self._layout(tmp_path)

        def runner(cmd, log, env, cwd):
            return 1 if cmd[-1].endswith("check_setup.py") else 0
        assert postinstall.run(wheels, str(data), python_exe="py", app_dir=app, runner=runner) == 0

    def test_main_bad_data_dir(self, tmp_path, capsys):
        assert postinstall.main(["--wheels", str(tmp_path), "--data-dir", "relative"]) == 2
        assert "full path" in capsys.readouterr().err
