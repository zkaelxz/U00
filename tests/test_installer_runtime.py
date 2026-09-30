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
        monkeypatch.setattr(launcher, "show_message", lambda text, headless=False: shown.append(text))
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


class _Proc:
    def __init__(self, pid=999, exited=False):
        self.pid, self._exited = pid, exited

    def poll(self):
        return 1 if self._exited else None


class TestStartServer:
    def test_runs_python_m_api_from_the_app_dir(self, data_dir, monkeypatch):
        seen = {}

        def fake_popen(cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs
            return _Proc()
        monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
        launcher.start_server("py", {"BAIHE_API_HOST": "127.0.0.1"}, headless=True)
        assert seen["cmd"] == ["py", "-s", "-m", "api"]
        assert seen["kwargs"]["cwd"] == str(launcher.APP_DIR)

    def test_records_the_pid_only_of_a_running_server(self, data_dir):
        pid_file = data_dir / "launcher" / launcher.PID_FILE_NAME
        assert launcher.record_pid(_Proc(111, exited=True)) is False
        assert not pid_file.exists()
        assert launcher.record_pid(_Proc(222)) is True
        assert pid_file.read_text().split() == ["222", "8600"]


class TestStartLock:
    def test_second_launcher_waits_instead_of_starting(self, data_dir):
        assert launcher.acquire_start_lock() is True
        assert launcher.acquire_start_lock() is False
        launcher.release_start_lock()
        assert launcher.acquire_start_lock() is True
        launcher.release_start_lock()

    def test_a_stale_lock_is_taken_over(self, data_dir):
        assert launcher.acquire_start_lock() is True
        future = lambda: 10 ** 12  # noqa: E731
        assert launcher.acquire_start_lock(now=future) is True
        launcher.release_start_lock()

    def test_launch_while_another_start_is_in_progress(self, data_dir, monkeypatch):
        assert launcher.acquire_start_lock() is True
        waited, opened = [], []
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)
        monkeypatch.setattr(launcher, "port_open", lambda port: True)   # the first server binding
        monkeypatch.setattr(launcher, "start_server", lambda *a: pytest.fail("started a second server"))
        monkeypatch.setattr(launcher, "wait_for_health", lambda port, proc=None: waited.append(proc) or True)
        monkeypatch.setattr(launcher, "open_window", opened.append)
        assert launcher.launch() == 0
        assert waited == [None] and opened

    def test_launch_records_pid_and_releases_the_lock(self, data_dir, monkeypatch):
        monkeypatch.setattr(launcher, "health_ok", lambda port, timeout=1.0: False)
        monkeypatch.setattr(launcher, "port_open", lambda port: False)
        monkeypatch.setattr(launcher, "start_server", lambda py, env, headless: _Proc(4321))
        monkeypatch.setattr(launcher, "wait_for_health", lambda port, proc=None: True)
        assert launcher.launch(headless=True) == 0
        assert (data_dir / "launcher" / launcher.PID_FILE_NAME).read_text().split() == ["4321", "8600"]
        assert not (data_dir / "launcher" / launcher.START_LOCK_NAME).exists()

    def test_headless_errors_go_to_stderr(self, capsys):
        launcher.show_message("boom", headless=True)
        assert "boom" in capsys.readouterr().err


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
    def test_pip_env_drops_every_pip_setting_and_config_file(self):
        env = postinstall.pip_env({"PIP_INDEX_URL": "https://evil", "PIP_USER": "1",
                                   "PIP_REQUIRE_VIRTUALENV": "1", "pip_no_binary": ":all:",
                                   "PIP_CONFIG_FILE": r"C:\pip.ini", "PATH": "x"})
        assert not [k for k in env if k.upper().startswith("PIP_")
                    and k not in ("PIP_CONFIG_FILE", "PIP_DISABLE_PIP_VERSION_CHECK")]
        assert env["PIP_CONFIG_FILE"] == os.devnull
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
        assert boot[2] == "-c" and "runpy.run_module('pip'" in boot[3]
        assert boot[4].endswith("pip-26.2-py3-none-any.whl")
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
        assert postinstall.run(wheels, str(data), python_exe="py", app_dir=app, runner=runner,
                               restrict=lambda *a: False) == 0
        assert (app / "INSTALLED").read_text(encoding="utf-8-sig").splitlines()[0] == str(data)
        log = (data / "launcher" / "install.log").read_text(encoding="utf-8")
        assert "Install finished OK." in log
        assert cmds[0][2] == "-c" and "run_module('pip'" in cmds[0][3] and cmds[1][2:4] == ["-m", "pip"]
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

    def test_marker_is_written_even_if_the_data_folder_cant_be_created(self, tmp_path, monkeypatch):
        app, wheels, data = self._layout(tmp_path)
        real_mkdir = Path.mkdir

        def failing_mkdir(self, *a, **k):
            if str(self).startswith(str(data)):
                raise PermissionError("denied")
            return real_mkdir(self, *a, **k)
        monkeypatch.setattr(Path, "mkdir", failing_mkdir)
        with pytest.raises(postinstall.PostInstallError) as e:
            postinstall.run(wheels, str(data), python_exe="py", app_dir=app,
                            runner=lambda *a: pytest.fail("ran pip"))
        assert e.value.code == 2
        assert (app / "INSTALLED").is_file()

    def test_check_setup_failure_is_not_fatal(self, tmp_path):
        app, wheels, data = self._layout(tmp_path)

        def runner(cmd, log, env, cwd):
            return 1 if cmd[-1].endswith("check_setup.py") else 0
        assert postinstall.run(wheels, str(data), python_exe="py", app_dir=app, runner=runner) == 0

    def test_main_bad_data_dir(self, tmp_path, capsys):
        assert postinstall.main(["--wheels", str(tmp_path), "--data-dir", "relative"]) == 2
        assert "full path" in capsys.readouterr().err


# ---------------------------------------------------------- Step 80b stop/clean


class TestStopSequence:
    def _setup(self, data_dir, pid="4242", port="8600", token="t" * 43):
        d = data_dir / "launcher"
        d.mkdir(parents=True, exist_ok=True)
        (d / launcher.PID_FILE_NAME).write_text(f"{pid}\n{port}\n")
        if token is not None:
            (d / launcher.TOKEN_FILE_NAME).write_text(token)
        return d

    def test_clean_shutdown_first(self, data_dir, tmp_path):
        d = self._setup(data_dir, port="8601")
        py = str(tmp_path / "python.exe")
        alive = {4242: py}
        asked, killed, ended = [], [], []

        def shutdown(port, token):
            asked.append((port, token))
            alive.pop(4242)      # the server exits by itself
            return True
        msg = launcher.stop_server(py, image_of=alive.get, kill=killed.append, sleep=lambda s: None,
                                   shutdown=shutdown, end_group=lambda n: ended.append(n) or False)
        assert asked == [(8601, "t" * 43)]
        assert killed == []
        assert ended == [launcher.group_name()]      # leftover sweep always runs
        assert "clean shutdown" in msg
        assert not (d / launcher.PID_FILE_NAME).exists()
        assert not (d / launcher.TOKEN_FILE_NAME).exists()

    def test_forced_when_the_server_doesnt_stop_in_time(self, data_dir, tmp_path):
        self._setup(data_dir)
        py = str(tmp_path / "python.exe")
        alive = {4242: py}
        killed = []

        def kill(pid):
            killed.append(pid)
            alive.pop(pid)
        msg = launcher.stop_server(py, image_of=alive.get, kill=kill, sleep=lambda s: None,
                                   shutdown=lambda port, token: True, end_group=lambda n: False,
                                   grace=1)
        assert killed == [4242]
        assert "ended" in msg

    def test_forced_without_a_token(self, data_dir, tmp_path):
        self._setup(data_dir, token=None)
        py = str(tmp_path / "python.exe")
        alive = {4242: py}
        asked, killed = [], []
        launcher.stop_server(py, image_of=alive.get, kill=lambda p: (killed.append(p), alive.pop(p)),
                             sleep=lambda s: None, shutdown=lambda *a: asked.append(a) or True,
                             end_group=lambda n: False)
        assert asked == [] and killed == [4242]

    def test_never_signals_a_process_that_isnt_ours(self, data_dir, tmp_path):
        self._setup(data_dir)
        asked, killed = [], []
        msg = launcher.stop_server(str(tmp_path / "python.exe"),
                                   image_of=lambda pid: r"C:\Windows\notepad.exe",
                                   kill=killed.append, shutdown=lambda *a: asked.append(a) or True,
                                   end_group=lambda n: False)
        assert asked == [] and killed == []
        assert "isn't running" in msg

    def test_leftovers_are_ended_even_without_a_server(self, data_dir, tmp_path):
        msg = launcher.stop_server(str(tmp_path / "python.exe"), image_of=lambda p: None,
                                   kill=lambda p: pytest.fail("killed"), end_group=lambda n: True)
        assert "left running" in msg


class TestShutdownRequest:
    def test_posts_token_as_json_with_the_local_header(self, monkeypatch):
        seen = {}

        class Resp:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            seen["url"], seen["headers"], seen["data"] = req.full_url, dict(req.header_items()), req.data
            seen["method"], seen["timeout"] = req.get_method(), timeout
            return Resp()
        monkeypatch.setattr(launcher.urllib.request, "urlopen", fake_urlopen)
        assert launcher.request_clean_shutdown(8601, "tok") is True
        assert seen["url"] == "http://127.0.0.1:8601/api/system/shutdown"
        assert seen["method"] == "POST" and seen["data"] == b"{}" and seen["timeout"] == 5.0
        headers = {k.lower(): v for k, v in seen["headers"].items()}
        assert headers["x-baihe-shutdown-token"] == "tok"
        assert headers["x-baihe-local"] == "1"
        assert headers["content-type"] == "application/json"

    def test_failure_is_false(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("refused")
        monkeypatch.setattr(launcher.urllib.request, "urlopen", boom)
        assert launcher.request_clean_shutdown(8600, "tok") is False


class TestServerStartToken:
    def test_fresh_token_passed_to_the_server_and_kept_privately(self, data_dir, monkeypatch):
        seen = {}

        def fake_popen(cmd, **kwargs):
            seen["env"] = kwargs["env"]
            return _Proc()
        monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
        launcher.start_server("py", {"A": "1"}, headless=True)
        token = (data_dir / "launcher" / launcher.TOKEN_FILE_NAME).read_text()
        assert len(token) >= 32 and seen["env"][launcher.SHUTDOWN_TOKEN_ENV] == token
        assert seen["env"]["A"] == "1"
        if os.name != "nt":
            assert oct((data_dir / "launcher" / launcher.TOKEN_FILE_NAME).stat().st_mode & 0o777) == "0o600"
        launcher.start_server("py", {}, headless=True)
        assert (data_dir / "launcher" / launcher.TOKEN_FILE_NAME).read_text() != token

    def test_server_env_names_this_installs_process_group(self):
        import process_guard
        env = launcher.server_env({})
        assert env[process_guard.GROUP_NAME_ENV] == launcher.group_name()
        assert env[process_guard.GROUP_NAME_ENV].startswith("Local\\BaiheStudio-")

    def test_console_title_is_best_effort(self):
        # Off Windows (or with no console to attach to) it just says no.
        assert launcher.set_console_title(1, "x", tries=1, sleep=lambda s: None) in (True, False)


class TestDataDirLockdown:
    class _Log:
        def __init__(self):
            self.text = ""

        def write(self, s):
            self.text += s

    def _run(self, calls, sid_out='"pc\\\\kae","S-1-5-21-1-2-3-1001"\r\n', rc=0):
        class R:
            def __init__(self, out="", code=0):
                self.stdout, self.returncode = out, code

        def run(cmd, **kw):
            calls.append(cmd)
            return R(sid_out) if cmd[0] == "whoami" else R(code=rc)
        return run

    def test_new_folder_outside_the_profile_is_limited_to_this_account(self, tmp_path):
        calls, log = [], self._Log()
        ok = postinstall.restrict_new_data_dir(tmp_path / "D-Baihe", True, log, run=self._run(calls),
                                               profile=str(tmp_path / "Users" / "kae"), is_windows=True)
        assert ok is True
        icacls = calls[-1]
        assert icacls[:3] == ["icacls", str(tmp_path / "D-Baihe"), "/inheritance:r"]
        assert "*S-1-5-21-1-2-3-1001:(OI)(CI)F" in icacls
        assert "*S-1-5-18:(OI)(CI)F" in icacls and "*S-1-5-32-544:(OI)(CI)F" in icacls
        assert "Limited the data folder" in log.text

    @pytest.mark.parametrize("created,inside,windows", [(False, False, True), (True, True, True),
                                                        (True, False, False)])
    def test_left_alone(self, tmp_path, created, inside, windows):
        calls = []
        profile = tmp_path / "Users" / "kae"
        data = profile / "AppData" / "Local" / "Baihe Studio" if inside else tmp_path / "D-Baihe"
        assert postinstall.restrict_new_data_dir(data, created, self._Log(), run=self._run(calls),
                                                 profile=str(profile), is_windows=windows) is False
        assert calls == []

    def test_no_sid_means_no_change(self, tmp_path):
        calls, log = [], self._Log()
        assert postinstall.restrict_new_data_dir(tmp_path / "D", True, log, run=self._run(calls, sid_out=""),
                                                 profile=str(tmp_path / "P"), is_windows=True) is False
        assert [c[0] for c in calls] == ["whoami"]


class TestCreatedFlag:
    def test_marker_records_a_setup_created_folder(self, tmp_path, monkeypatch):
        app = tmp_path / "app"
        app.mkdir()
        m = postinstall.write_marker(app, tmp_path / "data", created=True)
        lines = m.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == str(tmp_path / "data")
        assert postinstall.CREATED_FLAG_LINE in lines
        # portable.py still reads the path from it.
        monkeypatch.setattr(portable, "_INSTALLED_MARKER_PATH", str(m))
        monkeypatch.delenv(portable.DATA_DIR_ENV, raising=False)
        assert portable.data_dir() == str(tmp_path / "data")
        assert postinstall.CREATED_FLAG_LINE not in postinstall.write_marker(app, tmp_path / "d").read_text(
            encoding="utf-8-sig")

    def test_main_passes_the_flag(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(postinstall, "run", lambda w, d, created=False: seen.update(created=created) or 0)
        assert postinstall.main(["--wheels", "w", "--data-dir", str(tmp_path), "--data-dir-created"]) == 0
        assert seen["created"] is True
