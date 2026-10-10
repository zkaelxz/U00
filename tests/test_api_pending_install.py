"""Queue, preview, cancel and report an install that waits for restart
(services/pending_install_service.py). The plan is faked; no pip."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import pending_install
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import pending_install_service as svc

REMOTE = "https://baihe.example.com"
BASE = "/api/diagnostics/pending-install"


def make_plan(keys, mode="restart", blocked=(), confirm=(), before=None, loaded=()):
    return {"keys": list(keys), "available": True, "mode": mode,
            "changes": [{"name": "numpy", "from": "2.5.3", "to": "2.3.5", "kind": "downgrade"}],
            "loaded": list(loaded), "blocked": list(blocked), "needs_confirm": list(confirm),
            "summary": ["Change numpy 2.5.3 -> 2.3.5 (downgrade)."], "installs": [],
            "before": before if before is not None else {"numpy": "2.5.3"}, "note": None}


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    state = {"plan": make_plan(["paddleocr"]), "calls": 0, "jobs": False}

    def build_plan(keys, jobs_running=False):
        state["calls"] += 1
        state["jobs_seen"] = jobs_running
        return dict(state["plan"], keys=list(keys))
    monkeypatch.setattr(svc.install_plan, "build_plan", build_plan)
    monkeypatch.setattr(svc, "_jobs_running", lambda: state["jobs"])
    svc._CACHE.update(keys=None, plan=None, at=0.0)
    return state


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def queue(client, packages=("paddleocr",), **kw):
    return client.post(f"{BASE}/queue", json={"packages": list(packages), "confirm": True, **kw})


def test_plan_shows_the_owner_a_plain_list(client):
    r = client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]})
    b = r.json()
    assert r.status_code == 200 and b["mode"] == "restart"
    assert b["summary"] == ["Change numpy 2.5.3 -> 2.3.5 (downgrade)."]
    assert b["changes"] == [{"name": "numpy", "from_version": "2.5.3", "to_version": "2.3.5",
                             "kind": "downgrade"}]


def test_queue_writes_keys_only_and_status_reports_it(client, env, tmp_path):
    r = queue(client, ["paddleocr", "paddlepaddle"], accept_risk=True)
    assert r.status_code == 200 and r.json()["queued"] is True
    doc = json.loads((tmp_path / "pending_install" / "pending.json").read_text())
    assert doc["payload"]["packages"] == ["paddleocr", "paddlepaddle"]
    assert set(doc["payload"]) == {"schema", "packages", "created", "before"}
    s = client.get(BASE).json()
    assert s["packages"] == ["paddleocr", "paddlepaddle"] and s["before"] == {"numpy": "2.5.3"}
    assert s["result"] is None and s["applying"] is False and s["problem"] is None


def test_a_downgrade_of_something_the_app_needs_is_refused_until_accepted(client, env):
    env["plan"] = make_plan(["paddleocr"], confirm=["numpy 2.5.3 -> 2.3.5 is a downgrade of a package Baihe itself needs."])
    r = queue(client)
    assert r.status_code == 409
    assert "downgrade" in json.dumps(r.json())
    assert client.get(BASE).json()["packages"] == []
    assert queue(client, accept_risk=True).json()["queued"] is True


def test_a_blocked_plan_is_never_queued_even_if_the_risk_is_accepted(client, env):
    env["plan"] = make_plan(["paddleocr"], blocked=["This would put two OpenCV packages side by side."])
    r = queue(client, accept_risk=True)
    assert r.status_code == 422
    assert client.get(BASE).json()["packages"] == []


def test_a_plan_that_is_safe_now_is_not_queued(client, env):
    env["plan"] = make_plan(["paddleocr"], mode="now")
    b = queue(client).json()
    assert b == {**b, "queued": False, "install_now": True}
    assert client.get(BASE).json()["packages"] == []


def test_queue_needs_confirm(client):
    r = client.post(f"{BASE}/queue", json={"packages": ["paddleocr"]})
    assert r.status_code == 422


def test_unknown_or_hostile_package_names_are_refused(client, env):
    for name in ("evil", "--index-url=http://x", "paddleocr; rm -rf /", "lightnovel-crawler"):
        r = queue(client, [name])
        assert r.status_code in (404, 422), name
    assert client.get(BASE).json()["packages"] == [] and env["calls"] == 0


def test_request_cannot_carry_pip_arguments_or_versions(client):
    r = client.post(f"{BASE}/queue", json={"packages": ["paddleocr"], "confirm": True,
                                           "args": ["--index-url", "http://x"], "version": "9"})
    assert r.status_code == 422


def test_a_second_queue_adds_to_the_first(client, env):
    queue(client, ["paddleocr"], accept_risk=True)
    queue(client, ["paddlepaddle"], accept_risk=True)
    assert client.get(BASE).json()["packages"] == ["paddleocr", "paddlepaddle"]


def test_running_job_is_passed_to_the_plan(client, env):
    env["jobs"] = True
    client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]})
    assert env["jobs_seen"] is True


def test_cancel_before_restart_removes_it(client):
    queue(client, accept_risk=True)
    assert client.post(f"{BASE}/cancel", json={}).json() == {"cancelled": True}
    assert client.get(BASE).json()["packages"] == []
    assert client.post(f"{BASE}/cancel", json={}).json() == {"cancelled": False}
    assert pending_install.read_pending() == (None, None)


def test_cancel_is_refused_while_the_install_is_running(client, monkeypatch):
    queue(client, accept_risk=True)
    monkeypatch.setattr(pending_install, "apply_running", lambda: True)
    assert client.post(f"{BASE}/cancel", json={}).status_code == 409
    assert pending_install.read_pending()[0] is not None


def test_outcome_is_shown_then_dismissed(client):
    pending_install._write_json("result", {
        "status": "failed", "packages": ["paddleocr"], "message": "The install did not work. Nothing else was changed.",
        "tail": ["ERROR: nope"], "restored": [], "restore_failed": [], "finished": 5})
    s = client.get(BASE).json()
    assert s["result"]["status"] == "failed" and "did not work" in s["result"]["message"]
    client.post(f"{BASE}/dismiss", json={})
    assert client.get(BASE).json()["result"] is None


def test_a_modified_file_is_reported_in_plain_words(client, tmp_path):
    queue(client, accept_risk=True)
    path = tmp_path / "pending_install" / "pending.json"
    doc = json.loads(path.read_text())
    doc["payload"]["packages"] = ["evil"]
    path.write_text(json.dumps(doc))
    s = client.get(BASE).json()
    assert s["packages"] == [] and "changed after Baihe wrote it" in s["problem"]


def test_responses_carry_no_paths_urls_or_secrets(client, tmp_path):
    queue(client, accept_risk=True)
    text = json.dumps([client.get(BASE).json(),
                       client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]}).json()])
    assert str(tmp_path) not in text and "http" not in text and "\\" not in text


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_household_users_and_remote_admins_can_neither_see_nor_cancel_it(env):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    kid = auth_service.create_session(auth_service.add_user("kid@example.com")["id"])
    admin = auth_service.create_session(auth_service.grant_admin_local("a@example.com")["id"])
    local = TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    routes = (("get", BASE, None), ("post", f"{BASE}/plan", {"packages": ["paddleocr"]}),
              ("post", f"{BASE}/queue", {"packages": ["paddleocr"], "confirm": True}),
              ("post", f"{BASE}/cancel", {}), ("post", f"{BASE}/dismiss", {}))
    for method, path, body in routes:
        for who in (None, kid, admin):
            headers = _h(who) if who else {}
            r = getattr(remote, method)(path, headers=headers, **({"json": body} if body is not None else {}))
            assert r.status_code in (401, 403), (path, r.status_code)
        r = getattr(local, method)(path, headers={"X-Forwarded-For": "1.2.3.4"},
                                   **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path
    assert pending_install.read_pending() == (None, None)


def test_the_merged_queue_is_planned_as_a_whole(client, env, monkeypatch):
    clash = "This would put two OpenCV packages side by side."
    seen = []

    def build_plan(keys, jobs_running=False):
        seen.append(list(keys))
        blocked = [clash] if {"paddleocr", "cv2"} <= set(keys) else []
        return make_plan(keys, blocked=blocked, confirm=("x",) if len(keys) > 1 else ())
    monkeypatch.setattr(svc.install_plan, "build_plan", build_plan)
    assert queue(client, ["paddleocr"]).status_code == 200
    r = queue(client, ["cv2"], accept_risk=True)
    assert r.status_code == 422 or r.status_code == 409
    assert ["paddleocr", "cv2"] in seen
    assert client.get(BASE).json()["packages"] == ["paddleocr"]


def test_accepting_a_risk_is_required_against_the_merged_plan(client, env, monkeypatch):
    monkeypatch.setattr(svc.install_plan, "build_plan", lambda keys, jobs_running=False: make_plan(
        keys, confirm=("numpy goes down.",) if len(keys) > 1 else ()))
    assert queue(client, ["paddleocr"]).status_code == 200
    assert queue(client, ["paddlepaddle"]).status_code == 409
    assert queue(client, ["paddlepaddle"], accept_risk=True).status_code == 200


def test_a_preview_is_refused_while_the_install_hold_is_taken(client, env):
    import background_jobs
    assert background_jobs.acquire_exclusive("Dependency install")
    try:
        r = client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]})
    finally:
        background_jobs.release_exclusive()
    assert r.status_code == 409 and env["calls"] == 0


def test_a_second_preview_is_refused_while_one_runs(client, env):
    assert svc._PREVIEW_LOCK.acquire(blocking=False)
    try:
        assert client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]}).status_code == 409
    finally:
        svc._PREVIEW_LOCK.release()


@pytest.mark.parametrize("bad", ["--pre", "a b", "x" * 81, ""])
def test_package_keys_are_shape_checked(client, bad):
    assert client.post(f"{BASE}/plan", json={"packages": [bad]}).status_code == 422
