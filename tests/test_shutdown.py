"""Step 80b: the installed app's clean stop -- services/shutdown_service.py,
POST /api/system/shutdown and process_guard.py (the Job Object that takes
the server's child processes down with it). No real server exits and no
Windows call is made: the stopper and the jobs are fakes."""
import os

import pytest
from fastapi.testclient import TestClient

import background_jobs
import process_guard
from api.api_config import ApiSettings
from api.server import create_app
from services import shutdown_service

TOKEN = "x" * 43
PATH = "/api/system/shutdown"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(shutdown_service, "_token", "")
    monkeypatch.setattr(shutdown_service, "_started", False)
    monkeypatch.setattr(shutdown_service, "_stopper", None)
    monkeypatch.delenv(shutdown_service.TOKEN_ENV, raising=False)


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


class TestProcessGuard:
    def test_group_name_is_per_install_and_private(self, tmp_path):
        a = process_guard.group_name_for(tmp_path / "A")
        b = process_guard.group_name_for(tmp_path / "B")
        assert a != b and a.startswith("Local\\BaiheStudio-")
        assert str(tmp_path) not in a
        assert process_guard.group_name_for(tmp_path / "A" / ".." / "A") == a

    @pytest.mark.skipif(os.name == "nt", reason="the no-op path is for other systems")
    def test_no_op_off_windows(self, monkeypatch):
        monkeypatch.setenv(process_guard.GROUP_NAME_ENV, "Local\\BaiheStudio-test")
        assert process_guard.contain_children() is False
        assert process_guard.terminate_group("Local\\BaiheStudio-test") is False
        assert process_guard.add_process_to_group("Local\\BaiheStudio-test", os.getpid()) is False

    def test_nothing_without_a_name(self, monkeypatch):
        monkeypatch.delenv(process_guard.GROUP_NAME_ENV, raising=False)
        monkeypatch.setattr(process_guard, "_job_handle", None)
        assert process_guard.contain_children() is False
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


def test_python_m_api_registers_the_stopper_and_the_job(monkeypatch):
    import api.__main__ as main_mod
    import uvicorn
    seen = {}

    class FakeServer:
        should_exit = False

        def __init__(self, config):
            seen["config"] = config

        def run(self):
            seen["ran"] = True
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setenv(shutdown_service.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(process_guard.GROUP_NAME_ENV, "Local\\BaiheStudio-test")
    monkeypatch.setattr(process_guard, "contain_children", lambda: seen.setdefault("contained", True))
    monkeypatch.setenv("BAIHE_API_HOST", "127.0.0.1")
    monkeypatch.delenv("BAIHE_API_ENV", raising=False)
    main_mod._serve()
    assert seen["ran"] and seen["contained"]
    # Neither value is passed on to the processes the server starts.
    assert shutdown_service.TOKEN_ENV not in os.environ
    assert process_guard.GROUP_NAME_ENV not in os.environ
    assert shutdown_service.token_matches(TOKEN)
    assert seen["config"].timeout_graceful_shutdown == 3
    assert shutdown_service._stopper is not None
    shutdown_service._stopper()   # sets should_exit on the real server object


def test_ctrl_c_is_a_normal_stop(monkeypatch):
    import api.__main__ as main_mod
    import uvicorn

    class FakeServer:
        should_exit = False

        def __init__(self, config):
            pass

        def run(self):
            raise KeyboardInterrupt
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(process_guard, "contain_children", lambda: False)
    monkeypatch.setenv("BAIHE_API_HOST", "127.0.0.1")
    monkeypatch.delenv("BAIHE_API_ENV", raising=False)
    main_mod._serve()   # no traceback, like uvicorn.run


def test_queued_jobs_leave_the_queue_first(monkeypatch):
    from services import jobs_service
    order = []
    monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["q", "r"])
    monkeypatch.setattr(background_jobs, "cancel_queued", lambda j: order.append(("unqueue", j)))
    monkeypatch.setattr(jobs_service, "cancel_job", lambda j, principal=None: order.append(("cancel", j)))
    shutdown_service.cancel_all_jobs()
    assert order == [("unqueue", "q"), ("unqueue", "r"), ("cancel", "q"), ("cancel", "r")]
