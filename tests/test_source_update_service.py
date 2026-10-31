"""
services/source_update_service.py and /api/update/source*: the check follows
start.bat's own rules and names the reason for each refusal, apply needs a
check that found something newer, no running jobs and a confirm, and the web
route is reachable by an admin on the household listener but by nobody else.
git and the helper process are faked.
"""

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, shutdown_service
from services import source_update_service as su
from services import update_service as us
from services.service_errors import ConflictError, InvalidInputError, UnsupportedOperationError

OLD = "a" * 40
NEW = "b" * 40


class FakeGit:
    """Answers the git commands the service runs; `answers` overrides one by
    its first argument (or by 'fetch' for the fetch with -c options)."""

    def __init__(self, **answers):
        self.answers = {"rev-parse-head": (0, "baihe-subtitler"), "status": (0, ""), "head": (0, OLD),
                        "fetch": (0, ""), "fetch-head": (0, NEW), "merge-base": (0, ""),
                        "rev-list": (0, "2"), "log": (0, "Fix a bug\nAdd https://x.test/y a thing")}
        self.answers.update(answers)
        self.calls = []

    def __call__(self, *args, timeout=0):
        self.calls.append(args)
        if "fetch" in args:
            return self.answers["fetch"]
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return self.answers["rev-parse-head"]
        if args[:2] == ("rev-parse", "HEAD"):
            return self.answers["head"]
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return self.answers["fetch-head"]
        return self.answers.get(args[0], (0, ""))


@pytest.fixture
def checkout(tmp_path, monkeypatch, isolated_db):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(us, "_APP_DIR", str(tmp_path))
    monkeypatch.setattr(us, "_is_installed", lambda: False)
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setenv(su.STARTED_BY_ENV, "1")
    for name in su._OFF_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(su, "_check", {"at": 0.0, "behind": None, "latest": None, "changes": [],
                                       "error": None})
    monkeypatch.setattr(su, "_applying", False)
    git = FakeGit()
    monkeypatch.setattr(su, "_git", git)
    return git


def _checked(**kw):
    su._check.update(at=1.0, behind=2, latest="b" * 10, changes=["x"], error=None, **kw)


class TestCheck:
    def test_reports_waiting_commits_with_cleaned_subjects(self, checkout):
        s = su.check()
        assert s["behind"] == 2 and s["latest"] == "b" * 10 and s["current"] == "a" * 10
        assert s["can_apply"] and s["reason"] is None
        assert s["changes"] == ["Fix a bug", "Add a thing"]

    def test_up_to_date(self, checkout):
        checkout.answers["fetch-head"] = (0, OLD)
        s = su.check()
        assert s["behind"] == 0 and not s["can_apply"] and s["changes"] == []

    @pytest.mark.parametrize("answers,reason", [
        ({"rev-parse-head": (0, "feature")}, su.WRONG_BRANCH),
        ({"status": (0, " M start.bat")}, su.EDITED),
        ({"fetch": (128, "")}, su.OFFLINE),
        ({"fetch": (124, "")}, su.OFFLINE),
        ({"merge-base": (1, "")}, su.NOT_FF),
        ({"head": (1, "")}, su.UNREADABLE),
        ({"rev-parse-head": (None, "")}, su.NO_GIT),
    ])
    def test_each_refusal_has_its_reason(self, checkout, answers, reason):
        checkout.answers.update(answers)
        s = su.check()
        assert s["reason"] == reason and not s["can_apply"]

    def test_installed_copy_is_not_available(self, checkout, monkeypatch):
        monkeypatch.setattr(us, "_is_installed", lambda: True)
        s = su.check()
        assert s["available"] is False and s["reason"] == su.INSTALLED
        assert not any("fetch" in c for c in checkout.calls)

    def test_not_a_git_folder(self, checkout, tmp_path):
        (tmp_path / ".git").rmdir()
        assert su.status()["reason"] == su.NOT_GIT

    def test_not_started_by_start_bat(self, checkout, monkeypatch):
        monkeypatch.delenv(su.STARTED_BY_ENV)
        assert su.status()["reason"] == su.NOT_START_BAT

    @pytest.mark.parametrize("off", ["BAIHE_NO_UPDATE", "BAIHE_CI", "BAIHE_SERVER_ONLY"])
    def test_start_bat_switches_turn_it_off(self, checkout, monkeypatch, off):
        monkeypatch.setenv(off, "1")
        assert su.status()["reason"] == su.TURNED_OFF

    def test_noupdate_file_turns_it_off(self, checkout, tmp_path):
        (tmp_path / "NOUPDATE").write_text("")
        assert su.status()["reason"] == su.TURNED_OFF

    def test_close_checks_share_one_fetch(self, checkout):
        su.check()
        su.check()
        assert sum("fetch" in c for c in checkout.calls) == 1

    def test_not_windows_cannot_apply(self, checkout, monkeypatch):
        monkeypatch.setattr(us, "_is_windows", lambda: False)
        assert su.check()["reason"] == su.NOT_WINDOWS


class TestApply:
    @pytest.fixture
    def helper(self, checkout, monkeypatch):
        started = []
        monkeypatch.setattr(su, "_start_helper", lambda pid, port: started.append((pid, port)))
        stops = []
        monkeypatch.setattr(shutdown_service, "request_shutdown", lambda: stops.append(1) or {})
        monkeypatch.setattr(su.background_jobs, "active_job_ids", lambda: [])
        return SimpleNamespace(started=started, stops=stops)

    def test_needs_confirm(self, helper):
        _checked()
        with pytest.raises(InvalidInputError):
            su.apply(False)
        assert not helper.started

    def test_needs_a_check_first(self, helper):
        with pytest.raises(ConflictError, match="Check for updates first"):
            su.apply(True)

    def test_nothing_new(self, helper):
        su._check.update(at=1.0, behind=0, latest="a", changes=[], error=None)
        with pytest.raises(ConflictError, match="up to date"):
            su.apply(True)

    def test_refused_while_jobs_run(self, helper, monkeypatch):
        _checked()
        monkeypatch.setattr(su.background_jobs, "active_job_ids", lambda: ["j1"])
        with pytest.raises(ConflictError, match="running jobs"):
            su.apply(True)
        assert not helper.started and not helper.stops

    def test_refused_when_this_copy_cannot_restart(self, helper, monkeypatch):
        _checked()
        monkeypatch.delenv(su.STARTED_BY_ENV)
        with pytest.raises(UnsupportedOperationError, match="start.bat"):
            su.apply(True)

    def test_starts_the_helper_stops_the_server_and_audits(self, helper):
        _checked()
        out = su.apply(True, actor_id=None)
        assert out == {"started": True, "from_commit": "a" * 10}
        assert helper.started == [(os.getpid(), None)] and helper.stops == [1]
        assert db.get_app_setting(su.PENDING_KEY)["from"] == OLD
        assert any(r["action"] == "app.update" for r in db.auth_list_audit(10))

    def test_one_at_a_time(self, helper):
        _checked()
        su.apply(True)
        with pytest.raises(ConflictError, match="already starting"):
            su.apply(True)

    def test_a_failed_helper_start_changes_nothing(self, helper, monkeypatch):
        _checked()

        def boom(pid, port):
            raise OSError("no")
        monkeypatch.setattr(su, "_start_helper", boom)
        with pytest.raises(Exception, match="Nothing was changed"):
            su.apply(True)
        assert db.get_app_setting(su.PENDING_KEY) is None and not helper.stops
        monkeypatch.setattr(su, "_start_helper", lambda pid, port: None)
        assert su.apply(True)["started"]


class TestOutcome:
    def test_the_asking_server_does_not_judge_its_own_update(self, checkout):
        db.set_app_setting(su.PENDING_KEY, {"from": OLD, "pid": os.getpid()})
        assert su.status()["last_result"] is None

    def test_a_moved_head_is_an_update(self, checkout):
        db.set_app_setting(su.PENDING_KEY, {"from": OLD, "pid": os.getpid() + 1})
        checkout.answers["head"] = (0, NEW)
        s = su.status()
        assert s["last_result"] == f"Updated to {'b' * 10}."
        assert db.get_app_setting(su.PENDING_KEY) is None

    def test_an_unmoved_head_says_why(self, checkout):
        db.set_app_setting(su.PENDING_KEY, {"from": OLD, "pid": os.getpid() + 1})
        checkout.answers["status"] = (0, " M a.py")
        assert su.status()["last_result"] == f"Not updated: {su.EDITED[:-1]}. Still on {'a' * 10}."


class TestRoutes:
    def _client(self, host="127.0.0.1", listener=None):
        settings = ApiSettings(serve_frontend=False, household_port=8601, public_url="https://baihe.example.com",
                               google_client_id="cid", google_client_secret="sec")
        app = create_app(settings, listener=listener) if listener else create_app(settings)
        return TestClient(app, base_url=f"http://{host}", client=("127.0.0.1", 5000),
                          raise_server_exceptions=False)

    def _session(self, admin):
        email = "a@example.com" if admin else "m@example.com"
        user = auth_service.grant_admin_local(email) if admin else auth_service.add_user(email)
        s = auth_service.create_session(user["id"], "pytest", "127.0.0.1")
        return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                api_auth.CSRF_HEADER: s["csrf_token"]}

    def test_admin_on_the_household_listener_can_use_it(self, checkout):
        c = self._client("baihe.example.com", listener="household")
        r = c.get("/api/update/source", headers=self._session(True))
        assert r.status_code == 200, r.text
        assert r.json()["current"] == "a" * 10
        assert "http" not in r.text and str(us._APP_DIR) not in r.text

    def test_a_member_is_refused(self, checkout):
        c = self._client("baihe.example.com", listener="household")
        h = self._session(False)
        for method, path in (("get", "/api/update/source"), ("post", "/api/update/source/check"),
                             ("post", "/api/update/source/apply")):
            kwargs = {"json": {"confirm": True}} if path.endswith("apply") else {}
            assert getattr(c, method)(path, headers=h, **kwargs).status_code == 403

    def test_signed_out_is_refused(self, checkout):
        c = self._client("baihe.example.com", listener="household")
        assert c.get("/api/update/source").status_code == 401

    def test_apply_without_confirm_is_refused(self, checkout):
        c = self._client("baihe.example.com", listener="household")
        r = c.post("/api/update/source/apply", json={}, headers=self._session(True))
        assert r.status_code == 400 or r.status_code == 422

    def test_the_permission_is_not_grantable_to_members(self):
        assert auth_service.UPDATE_PERMISSION in auth_service.ADMIN_FLAG_PERMISSIONS
        assert auth_service.UPDATE_PERMISSION not in auth_service.MEMBER_PERMISSIONS


class TestRestartHelper:
    def test_no_start_bat_means_nothing_is_started(self, tmp_path, monkeypatch):
        from tools import restart_after_update as r
        started = []
        monkeypatch.setattr(r.subprocess, "Popen", lambda *a, **k: started.append(a))
        assert r.main(["--root", str(tmp_path), "--pid", "1"]) == 2 and not started

    def test_a_server_that_never_exits_is_left_alone(self, tmp_path, monkeypatch):
        from tools import restart_after_update as r
        (tmp_path / "start.bat").write_text("")
        started = []
        monkeypatch.setattr(r.subprocess, "Popen", lambda *a, **k: started.append(a))
        monkeypatch.setattr(r, "process_gone", lambda pid, seconds: False)
        assert r.main(["--root", str(tmp_path), "--pid", "1"]) == 3 and not started

    def test_runs_start_bat_after_the_exit_and_free_port(self, tmp_path, monkeypatch):
        from tools import restart_after_update as r
        (tmp_path / "start.bat").write_text("")
        order = []
        monkeypatch.setattr(r, "process_gone", lambda pid, seconds: order.append("exit") or True)
        monkeypatch.setattr(r, "port_free", lambda port, seconds: order.append("port") or True)
        seen = {}
        monkeypatch.setattr(r.subprocess, "Popen", lambda cmd, **k: (order.append(cmd[-1]), seen.update(k)))
        assert r.main(["--root", str(tmp_path), "--pid", "1", "--port", "8600"]) == 0
        assert order == ["exit", "port", str(tmp_path / "start.bat")]
        assert seen["env"]["BAIHE_NO_APP_WINDOW"] == "1"

    def test_a_failing_start_bat_exits_and_leaves_its_output_in_a_file(self, tmp_path, monkeypatch):
        from tools import restart_after_update as r
        (tmp_path / "start.bat").write_text("")
        monkeypatch.setattr(r, "process_gone", lambda pid, seconds: True)
        seen = {}
        monkeypatch.setattr(r.subprocess, "Popen", lambda cmd, **k: seen.update(k))
        assert r.main(["--root", str(tmp_path), "--pid", "1"]) == 0
        assert seen["env"]["BAIHE_NOPAUSE"] == "1"
        assert seen["stdout"].name == str(tmp_path / su.HELPER_LOG)
        assert su.HELPER_LOG == r.LOG_NAME

    def test_every_pause_in_start_bat_respects_nopause(self):
        text = open(os.path.join(os.path.dirname(__file__), "..", "start.bat"), encoding="utf-8").read()
        bare = [n for n, line in enumerate(text.splitlines(), 1) if line.strip().lower() == "pause"]
        assert bare == []

    def test_start_bat_opens_no_window_for_an_update_restart(self):
        text = open(os.path.join(os.path.dirname(__file__), "..", "start.bat"), encoding="utf-8").read()
        body = text[text.index("\n:open_window\n"):]
        assert body.index("if defined BAIHE_NO_APP_WINDOW") < body.index("msedge.exe")

    def test_the_server_starts_the_helper_outside_its_job(self, checkout, monkeypatch):
        seen = {}
        monkeypatch.setattr(su.subprocess, "Popen", lambda cmd, **k: seen.update(cmd=cmd, **k))
        monkeypatch.setattr(su.app_update_service, "_helper_python", lambda: "py")
        monkeypatch.setenv("BAIHE_SHUTDOWN_TOKEN", "t" * 40)
        monkeypatch.setattr(su.sys, "platform", "win32")
        su._start_helper(42, 8600)
        assert seen["cmd"][0] == "py" and seen["cmd"][1].endswith(os.path.join("tools", "restart_after_update.py"))
        assert seen["cmd"][-4:] == ["--pid", "42", "--port", "8600"]
        assert seen["creationflags"] & us._CREATE_BREAKAWAY_FROM_JOB
        assert "BAIHE_SHUTDOWN_TOKEN" not in seen["env"]

    def test_start_bat_leftovers_are_not_passed_on(self, checkout, monkeypatch):
        seen = {}
        monkeypatch.setattr(su.subprocess, "Popen", lambda cmd, **k: seen.update(**k))
        monkeypatch.setattr(su.app_update_service, "_helper_python", lambda: "py")
        for name in ("BAIHE_UPDATE_OLD", "BAIHE_FORCE_DEPS", "BAIHE_BUILD_FRONTEND", "BAIHE_AUTO_BUILD"):
            monkeypatch.setenv(name, "1")
        su._start_helper(42, None)
        assert not {k for k in seen["env"] if k.startswith("BAIHE_")} & set(su._START_BAT_STATE_ENV)
