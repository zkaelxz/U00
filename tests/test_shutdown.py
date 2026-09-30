"""Step 80b: stopping stops everything -- services/shutdown_service.py (the
clean stop), POST /api/system/shutdown, `python -m api`'s wiring, and
process_guard.py (the kill-on-close Job Object and the console-close
handler). No real server exits and no Windows call is made: the stopper,
the jobs and kernel32 are fakes."""
import os
import threading

import pytest
from fastapi.testclient import TestClient

import background_jobs
import page_fetch
import page_server
import process_guard
from api.api_config import ApiSettings
from api.server import create_app
from services import shutdown_service
from sources import chapter_check

TOKEN = "x" * 43
PATH = "/api/system/shutdown"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(shutdown_service, "_token", "")
    monkeypatch.setattr(shutdown_service, "_started", False)
    monkeypatch.setattr(shutdown_service, "_began", False)
    monkeypatch.setattr(shutdown_service, "_stopper", None)
    monkeypatch.setattr(shutdown_service, "_background_stopper", None)
    monkeypatch.delenv(shutdown_service.TOKEN_ENV, raising=False)
    # A stop sets these process-wide flags; keep them to this test.
    monkeypatch.setattr(page_fetch, "_SHUTDOWN", threading.Event())
    monkeypatch.setattr(chapter_check, "_scheduler_stop", threading.Event())
    monkeypatch.setattr(page_server, "_server", None)
    monkeypatch.setattr(page_server, "_server_started", False)


def _client(auth="off", remote=False):
    app = create_app(ApiSettings(auth_mode=auth))
    if remote:
        return TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _post(client, token=TOKEN, **headers):
    h = {"X-Baihe-Local": "1"}
    if token is not None:
        h["X-Baihe-Shutdown-Token"] = token
    h.update(headers)
    return client.post(PATH, json={}, headers=h)


class TestService:
    def test_disabled_without_a_long_enough_token(self, monkeypatch):
        assert not shutdown_service.enabled()
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, "short")
        assert not shutdown_service.enabled()
        assert not shutdown_service.token_matches("short")
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        assert shutdown_service.enabled()

    def test_token_leaves_the_environment(self, monkeypatch):
        # Processes the server starts must not inherit it.
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        shutdown_service.take_token_from_environment()
        assert shutdown_service.TOKEN_ENV not in os.environ
        assert shutdown_service.enabled() and shutdown_service.token_matches(TOKEN)

    def test_token_match(self, monkeypatch):
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        assert shutdown_service.token_matches(TOKEN)
        for bad in (None, "", TOKEN + "y", "y" * 43, 123):
            assert not shutdown_service.token_matches(bad)

    def test_cancels_jobs_through_the_normal_path_then_stops(self, monkeypatch):
        from services import jobs_service
        from services.service_errors import NotFoundError
        cancelled, flagged, stopped = [], [], []
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["a", "b"])

        def cancel(job_id, principal=None):
            if job_id == "b":
                raise NotFoundError("not mirrored")
            cancelled.append(job_id)
        monkeypatch.setattr(jobs_service, "cancel_job", cancel)
        monkeypatch.setattr(background_jobs, "request_cancel", flagged.append)
        monkeypatch.setattr(background_jobs, "cancel_queued", lambda j: True)
        monkeypatch.setattr(shutdown_service, "wait_for_jobs", lambda ids, timeout: True)
        shutdown_service.register_stopper(lambda: stopped.append(True))

        class Now:
            def __init__(self, target, args, daemon, name):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)
        monkeypatch.setattr(shutdown_service.threading, "Thread", Now)
        assert shutdown_service.request_shutdown() == {"status": "stopping", "cancelled_jobs": 2}
        assert cancelled == ["a"] and flagged == ["b"] and stopped == [True]
        # A second request changes nothing.
        assert shutdown_service.request_shutdown()["cancelled_jobs"] == 0
        assert stopped == [True]

    def test_wait_for_jobs(self, monkeypatch):
        states = {"a": [{"status": "running"}, {"status": "running"}, {"status": "cancelled"}]}
        monkeypatch.setattr(background_jobs, "get_status", lambda j: states[j].pop(0))
        assert shutdown_service.wait_for_jobs(["a"], timeout=10, sleep=lambda s: None) is True
        clock = iter([0, 0, 100])
        monkeypatch.setattr(background_jobs, "get_status", lambda j: {"status": "running"})
        assert shutdown_service.wait_for_jobs(["a"], timeout=5, sleep=lambda s: None,
                                              now=lambda: next(clock)) is False

    def test_active_job_ids(self, monkeypatch):
        monkeypatch.setattr(background_jobs, "_jobs", {
            "x": {"status": "running"}, "y": {"status": "queued"}, "z": {"status": "done"}})
        assert background_jobs.active_job_ids() == ["x", "y"]


class TestRoute:
    @pytest.fixture(autouse=True)
    def _no_real_shutdown(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(shutdown_service, "request_shutdown",
                            lambda: self.calls.append(1) or {"status": "stopping", "cancelled_jobs": 3})

    def test_absent_without_the_launcher_token(self):
        assert _post(_client()).status_code == 404
        assert self.calls == []

    def test_accepts_the_right_token(self, monkeypatch):
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        r = _post(_client())
        assert r.status_code == 202
        assert r.json() == {"status": "stopping", "cancelled_jobs": 3}
        assert self.calls == [1]

    @pytest.mark.parametrize("token", [None, "", "y" * 43])
    def test_refuses_a_wrong_or_missing_token(self, monkeypatch, token):
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        assert _post(_client(), token=token).status_code == 403
        assert self.calls == []

    def test_at_the_pc_with_auth_on_needs_no_session(self, monkeypatch):
        # The launcher has no session; a PC-only route lets it through.
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        assert _post(_client(auth="on")).status_code == 202

    def test_pc_only(self, monkeypatch):
        monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
        # Not from the PC (auth on, remote): refused before anything happens.
        assert _post(_client(auth="on", remote=True)).status_code in (401, 403)
        # A cross-site "simple" request (no JSON, no X-Baihe-Local) is refused too.
        r = _client().post(PATH, content=b"x", headers={"Content-Type": "text/plain",
                                                       "X-Baihe-Shutdown-Token": TOKEN})
        assert r.status_code == 403
        assert self.calls == []


class TestCleanShutdown:
    def _record(self, monkeypatch, active=("a",)):
        from services import jobs_service
        order = []
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: list(active))
        monkeypatch.setattr(background_jobs, "cancel_queued", lambda j: None)
        monkeypatch.setattr(jobs_service, "cancel_job",
                            lambda j, principal=None: order.append(("cancel", j)))
        monkeypatch.setattr(chapter_check, "stop_scheduler", lambda: order.append("scheduler"))
        monkeypatch.setattr(page_fetch, "request_shutdown", lambda: order.append("browsers"))
        monkeypatch.setattr(page_server, "stop_server", lambda: order.append("page_server"))
        monkeypatch.setattr(shutdown_service, "wait_for_jobs",
                            lambda ids, timeout: order.append(("wait", tuple(ids), timeout)))
        shutdown_service.register_background_stopper(lambda: order.append("poller"))
        return order

    def test_order_no_new_work_then_cancel_wait_and_stop_services(self, monkeypatch):
        order = self._record(monkeypatch)
        shutdown_service.clean_shutdown(timeout=2.5)
        assert order == ["scheduler", "poller", "browsers", ("cancel", "a"),
                         ("wait", ("a",), 2.5), "page_server"]

    def test_only_the_first_stop_waits(self, monkeypatch):
        # After the route's stop, `python -m api`'s own call doesn't cancel
        # or wait again; it still makes sure the services are stopped.
        order = self._record(monkeypatch)
        shutdown_service.clean_shutdown()
        order.clear()
        shutdown_service.clean_shutdown()
        assert order == ["page_server"]

    def test_route_stop_runs_the_same_steps_then_stops_the_server(self, monkeypatch):
        order = self._record(monkeypatch)
        shutdown_service.register_stopper(lambda: order.append("uvicorn"))

        class Now:
            def __init__(self, target, args, daemon, name):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)
        monkeypatch.setattr(shutdown_service.threading, "Thread", Now)
        assert shutdown_service.request_shutdown(timeout=4)["cancelled_jobs"] == 1
        assert order == ["scheduler", "poller", "browsers", ("cancel", "a"),
                         ("wait", ("a",), 4), "page_server", "uvicorn"]

    def test_a_failing_step_doesnt_stop_the_rest(self, monkeypatch):
        order = self._record(monkeypatch)

        def boom():
            raise RuntimeError("scheduler")
        monkeypatch.setattr(chapter_check, "stop_scheduler", boom)
        monkeypatch.setattr(page_server, "stop_server", boom)
        shutdown_service.clean_shutdown()
        assert "browsers" in order and ("cancel", "a") in order


class TestStoppableServices:
    def test_page_server_stops(self, monkeypatch):
        calls = []

        class FakeServer:
            def shutdown(self):
                calls.append("shutdown")

            def server_close(self):
                calls.append("close")
        monkeypatch.setattr(page_server, "_server", FakeServer())
        monkeypatch.setattr(page_server, "_server_started", True)
        assert page_server.stop_server() is True
        assert calls == ["shutdown", "close"] and not page_server.server_running()
        assert page_server.stop_server() is False

    def test_chapter_scheduler_stops(self, monkeypatch):
        threads, polls = [], []
        real_thread = threading.Thread

        def record(*a, **k):
            t = real_thread(*a, **k)
            threads.append(t)
            return t
        monkeypatch.setattr(chapter_check, "_scheduler_started", False)
        monkeypatch.setattr(chapter_check.threading, "Thread", record)
        monkeypatch.setattr(chapter_check.store, "list_tracked_series", lambda: polls.append(1) or [])
        chapter_check.ensure_scheduler_started(poll_seconds=0.01)
        for _ in range(200):
            if polls:
                break
            threading.Event().wait(0.01)
        chapter_check.stop_scheduler()
        threads[0].join(2)
        assert polls and not threads[0].is_alive()

    def test_no_new_browser_once_shutting_down(self):
        page_fetch.request_shutdown()
        with pytest.raises(RuntimeError, match="shutting down"):
            page_fetch._require_playwright()

    def test_sign_in_window_wait_ends_on_shutdown(self, monkeypatch):
        # Waits however long the person takes (timeouts just loop), but an
        # app shutdown ends the wait so the window closes on its own thread.
        class Timeout(Exception):
            pass
        monkeypatch.setattr(page_fetch, "_playwright_timeout_error", lambda: Timeout)

        class Ctx:
            waits = 0

            def wait_for_event(self, name, timeout):
                assert (name, timeout) == ("close", page_fetch.LOGIN_POLL_MS)
                Ctx.waits += 1
                if Ctx.waits == 3:
                    page_fetch.request_shutdown()
                raise Timeout()
        page_fetch._wait_for_close(Ctx())
        assert Ctx.waits == 3

    def test_sign_in_window_wait_ends_when_the_person_closes_it(self):
        class Ctx:
            waits = 0

            def wait_for_event(self, name, timeout):
                Ctx.waits += 1
        page_fetch._wait_for_close(Ctx())
        assert Ctx.waits == 1


class FakeKernel32:
    """Records the Win32 calls process_guard makes."""

    def __init__(self, create=101, set_info=1, assign=1):
        self.calls = []
        self._create, self._set_info, self._assign = create, set_info, assign
        self.handler = None

    def CreateJobObjectW(self, attrs, name):
        self.calls.append(("create", name))
        return self._create

    def SetInformationJobObject(self, job, klass, info, size):
        import ctypes
        flags = ctypes.cast(info, ctypes.POINTER(type(process_guard._extended_limit_info(0))))[0] \
            .BasicLimitInformation.LimitFlags
        self.calls.append(("limits", job, klass, flags))
        return self._set_info

    def GetCurrentProcess(self):
        return -1

    def AssignProcessToJobObject(self, job, proc):
        self.calls.append(("assign", job, proc))
        return self._assign

    def CloseHandle(self, h):
        self.calls.append(("close", h))
        return 1

    def OpenJobObjectW(self, access, inherit, name):
        self.calls.append(("open", name))
        return 202 if name == "Local\\BaiheStudio-running" else 0

    def TerminateJobObject(self, job, code):
        self.calls.append(("terminate", job))
        return 1

    def SetConsoleCtrlHandler(self, handler, add):
        self.handler = handler
        self.calls.append(("ctrl-handler", add))
        return 1


@pytest.fixture
def win32(monkeypatch):
    """process_guard as on Windows, with kernel32 faked."""
    k = FakeKernel32()
    monkeypatch.setattr(process_guard, "_is_windows", lambda: True)
    monkeypatch.setattr(process_guard, "_kernel32", lambda: k)
    monkeypatch.setattr(process_guard, "_handler_type", lambda: (lambda fn: fn))
    monkeypatch.setattr(process_guard, "_job_handle", None)
    monkeypatch.setattr(process_guard, "_console_handler", None)
    monkeypatch.delenv(process_guard.GROUP_NAME_ENV, raising=False)
    return k


class TestProcessGuard:
    def test_group_name_is_per_install_and_private(self, tmp_path):
        a = process_guard.group_name_for(tmp_path / "A")
        b = process_guard.group_name_for(tmp_path / "B")
        assert a != b and a.startswith("Local\\BaiheStudio-")
        assert str(tmp_path) not in a
        assert process_guard.group_name_for(tmp_path / "A" / ".." / "A") == a

    def test_no_op_off_windows(self, monkeypatch):
        monkeypatch.setattr(process_guard, "_is_windows", lambda: False)
        monkeypatch.setattr(process_guard, "_kernel32", lambda: pytest.fail("called Win32"))
        monkeypatch.setattr(process_guard, "_job_handle", None)
        monkeypatch.setattr(process_guard, "_console_handler", None)
        monkeypatch.setenv(process_guard.GROUP_NAME_ENV, "Local\\BaiheStudio-test")
        assert process_guard.contain_children() is False
        assert process_guard.create_kill_on_close_job(555) is None
        assert process_guard.install_console_close_handler(lambda: None) is False
        assert process_guard.terminate_group("Local\\BaiheStudio-test") is False
        assert process_guard.add_process_to_group("Local\\BaiheStudio-test", os.getpid()) is False

    def test_is_windows_means_win32(self, monkeypatch):
        monkeypatch.setattr(process_guard.sys, "platform", "linux")
        assert process_guard._is_windows() is False
        monkeypatch.setattr(process_guard.sys, "platform", "win32")
        assert process_guard._is_windows() is True

    def test_start_bat_gets_an_anonymous_kill_on_close_job(self, win32):
        # No launcher, no name: the API process still goes into a job, so
        # closing start.bat's window ends ffmpeg, Node and Chromium with it.
        assert process_guard.contain_children() is True
        assert win32.calls == [("create", None), ("limits", 101, 9, 0x2000), ("assign", 101, -1)]
        assert process_guard._job_handle == 101
        # Once per process; the handle stays open (closing it ends the job).
        assert process_guard.contain_children() is True
        assert len(win32.calls) == 3

    def test_the_launcher_names_the_job(self, win32, monkeypatch):
        monkeypatch.setenv(process_guard.GROUP_NAME_ENV, "Local\\BaiheStudio-abc")
        assert process_guard.contain_children() is True
        assert win32.calls[0] == ("create", "Local\\BaiheStudio-abc")

    @pytest.mark.parametrize("fail", ["create", "set_info", "assign"])
    def test_a_refusal_changes_nothing(self, win32, fail):
        setattr(win32, "_" + fail, 0)
        assert process_guard.contain_children() is False
        assert process_guard._job_handle is None
        if fail != "create":
            assert ("close", 101) in win32.calls
        assert not any(c[0] == "assign" for c in win32.calls) or fail == "assign"

    def test_one_childs_own_job(self, win32):
        # lncrawl_service's per-run job: the same kill-on-close job, holding
        # that child (it nests inside the server's).
        assert process_guard.create_kill_on_close_job(555) == 101
        assert win32.calls == [("create", None), ("limits", 101, 9, 0x2000), ("assign", 101, 555)]
        assert process_guard._job_handle is None

    def test_console_close_runs_the_clean_stop(self, win32):
        stops = []
        assert process_guard.install_console_close_handler(lambda: stops.append(1)) is True
        assert ("ctrl-handler", True) in win32.calls
        handler = win32.handler
        assert handler(process_guard.CTRL_CLOSE_EVENT) is True
        assert handler(process_guard.CTRL_SHUTDOWN_EVENT) is True
        assert stops == [1, 1]
        # Ctrl+C / Ctrl+Break stay Python's (a normal uvicorn stop), and a
        # logoff event may be another user's: passed on, nothing run.
        for event in (0, 1, 5):
            assert handler(event) is False
        assert stops == [1, 1]
        # Registered once.
        assert process_guard.install_console_close_handler(lambda: None) is True
        assert win32.calls.count(("ctrl-handler", True)) == 1

    def test_console_close_survives_a_failing_stop(self):
        def boom():
            raise RuntimeError("x")
        assert process_guard.handle_console_event(process_guard.CTRL_CLOSE_EVENT, boom) is True

    def test_terminate_group_only_touches_the_named_job(self, win32):
        assert process_guard.terminate_group("Local\\BaiheStudio-running") is True
        assert ("terminate", 202) in win32.calls and ("close", 202) in win32.calls
        assert process_guard.terminate_group("Local\\BaiheStudio-other") is False
        assert process_guard.terminate_group("") is False

    def test_extended_limit_info_layout(self):
        import ctypes
        info = process_guard._extended_limit_info(process_guard._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        assert info.BasicLimitInformation.LimitFlags == 0x2000
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on 64-bit Windows
        # (wintypes.DWORD is 8 bytes on Linux, so the size only means
        # something there).
        if os.name == "nt" and ctypes.sizeof(ctypes.c_void_p) == 8:
            assert ctypes.sizeof(info) == 144


def _fake_uvicorn(monkeypatch, run):
    import uvicorn
    seen = {}

    class FakeServer:
        should_exit = False

        def __init__(self, config):
            seen["config"] = config

        def run(self):
            seen["ran"] = True
            run()
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setenv("BAIHE_API_HOST", "127.0.0.1")
    monkeypatch.delenv("BAIHE_API_ENV", raising=False)
    return seen


def test_python_m_api_registers_the_stopper_the_job_and_the_close_handler(monkeypatch):
    import api.__main__ as main_mod
    from api import background
    seen = _fake_uvicorn(monkeypatch, lambda: None)
    handlers, cleaned = [], []
    monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(process_guard.GROUP_NAME_ENV, "Local\\BaiheStudio-test")
    monkeypatch.setattr(process_guard, "contain_children", lambda: seen.setdefault("contained", True))
    monkeypatch.setattr(process_guard, "install_console_close_handler", handlers.append)
    monkeypatch.setattr(shutdown_service, "clean_shutdown",
                        lambda timeout=shutdown_service.JOB_GRACE_SECONDS: cleaned.append(timeout))
    main_mod._serve()
    assert seen["ran"] and seen["contained"]
    # Neither value is passed on to the processes the server starts.
    assert shutdown_service.TOKEN_ENV not in os.environ
    assert process_guard.GROUP_NAME_ENV not in os.environ
    assert shutdown_service.token_matches(TOKEN)
    assert seen["config"].timeout_graceful_shutdown == 3
    assert shutdown_service._stopper is not None
    assert shutdown_service._background_stopper is background.stop_gpu_queue_poller
    shutdown_service._stopper()   # sets should_exit on the real server object
    # After uvicorn returns, the clean stop runs.
    assert cleaned == [shutdown_service.JOB_GRACE_SECONDS]
    # Closing the console window runs it within Windows' ~5 s.
    handlers[0]()
    assert cleaned[-1] == shutdown_service.CONSOLE_GRACE_SECONDS


def test_ctrl_c_is_a_normal_stop_and_still_cleans_up(monkeypatch):
    import api.__main__ as main_mod

    def interrupt():
        raise KeyboardInterrupt
    _fake_uvicorn(monkeypatch, interrupt)
    cleaned = []
    monkeypatch.setattr(process_guard, "contain_children", lambda: False)
    monkeypatch.setattr(process_guard, "install_console_close_handler", lambda fn: False)
    monkeypatch.setattr(shutdown_service, "clean_shutdown", lambda timeout=0: cleaned.append(1))
    main_mod._serve()   # no traceback, like uvicorn.run
    assert cleaned == [1]


def test_queued_jobs_leave_the_queue_first(monkeypatch):
    from services import jobs_service
    order = []
    monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["q", "r"])
    monkeypatch.setattr(background_jobs, "cancel_queued", lambda j: order.append(("unqueue", j)))
    monkeypatch.setattr(jobs_service, "cancel_job", lambda j, principal=None: order.append(("cancel", j)))
    shutdown_service.cancel_all_jobs()
    assert order == [("unqueue", "q"), ("unqueue", "r"), ("cancel", "q"), ("cancel", "r")]
