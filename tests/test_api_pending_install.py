"""Queue, preview, cancel and report an install that waits for restart
(services/pending_install_service.py). The plan is faked; no pip."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import pending_install
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import pending_install_service as svc
from tests.api_helpers import session_headers

REMOTE = "https://baihe.example.com"
BASE = "/api/diagnostics/pending-install"


def make_plan(keys, mode="restart", blocked=(), confirm=(), before=None, loaded=()):
    return {"keys": list(keys), "available": True, "mode": mode,
            "changes": [{"name": "numpy", "from": "2.5.3", "to": "2.3.5", "kind": "downgrade"}],
            "loaded": list(loaded), "blocked": list(blocked), "needs_confirm": list(confirm),
            "summary": ["Change numpy 2.5.3 -> 2.3.5 (downgrade)."], "installs": [],
            "before": before if before is not None else {"numpy": "2.5.3"}, "note": None, "swap": []}


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


def test_a_downgrade_of_something_the_app_needs_is_refused_until_accepted(client, env, monkeypatch):
    # The refused first try caches the plan, so the retry re-checks what is in use.
    monkeypatch.setattr(svc.install_plan, "_is_windows", lambda: True)
    monkeypatch.setattr(svc.install_plan, "dist_in_use", lambda name: name == "numpy")
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
            headers = session_headers(who) if who else {}
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
        blocked = [clash] if {"paddleocr", "paddlepaddle"} <= set(keys) else []
        return make_plan(keys, blocked=blocked, confirm=("x",) if len(keys) > 1 else ())
    monkeypatch.setattr(svc.install_plan, "build_plan", build_plan)
    assert queue(client, ["paddleocr"]).status_code == 200
    r = queue(client, ["paddlepaddle"], accept_risk=True)
    assert r.status_code == 422 or r.status_code == 409
    assert ["paddleocr", "paddlepaddle"] in seen
    assert client.get(BASE).json()["packages"] == ["paddleocr"]


def test_accepting_a_risk_is_required_against_the_merged_plan(client, env, monkeypatch):
    monkeypatch.setattr(svc.install_plan, "build_plan", lambda keys, jobs_running=False: make_plan(
        keys, confirm=("numpy goes down.",) if len(keys) > 1 else ()))
    assert queue(client, ["paddleocr"]).status_code == 200
    assert queue(client, ["paddlepaddle"]).status_code == 409
    assert queue(client, ["paddlepaddle"], accept_risk=True).status_code == 200


def test_a_preview_is_refused_while_the_install_hold_is_taken(client, env):
    from jobs import job_launch
    assert job_launch.acquire_exclusive("Dependency install")
    try:
        r = client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]})
    finally:
        job_launch.release_exclusive()
    assert r.status_code == 409 and env["calls"] == 0


def test_queueing_is_refused_while_the_install_hold_is_taken(client, env, tmp_path):
    from jobs import job_launch
    assert job_launch.acquire_exclusive("Dependency install")
    try:
        r = queue(client)
    finally:
        job_launch.release_exclusive()
    assert r.status_code == 409 and env["calls"] == 0
    assert not (tmp_path / "pending_install" / "pending.json").exists()


def test_a_second_preview_is_refused_while_one_runs(client, env):
    assert svc._PREVIEW_LOCK.acquire(blocking=False)
    try:
        assert client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]}).status_code == 409
    finally:
        svc._PREVIEW_LOCK.release()


def test_queueing_is_refused_while_a_preview_runs(client, env, tmp_path):
    assert svc._PREVIEW_LOCK.acquire(blocking=False)
    try:
        assert queue(client).status_code == 409
    finally:
        svc._PREVIEW_LOCK.release()
    assert env["calls"] == 0 and not (tmp_path / "pending_install" / "pending.json").exists()


def test_queueing_rechecks_the_install_hold_once_it_has_the_preview_lock(client, env, monkeypatch,
                                                                       tmp_path):
    held = iter([False, True])
    monkeypatch.setattr(svc.job_launch, "exclusive_held", lambda: next(held, True))
    assert queue(client).status_code == 409
    assert env["calls"] == 0 and not (tmp_path / "pending_install" / "pending.json").exists()


@pytest.mark.parametrize("bad", ["--pre", "a b", "x" * 81, ""])
def test_package_keys_are_shape_checked(client, bad):
    assert client.post(f"{BASE}/plan", json={"packages": [bad]}).status_code == 422


def test_queue_asks_what_is_in_use_again_instead_of_trusting_the_preview(client, env, monkeypatch):
    # Previewed while numpy was idle; a job has since imported it.
    monkeypatch.setattr(svc.install_plan, "_is_windows", lambda: True)
    inuse = set()
    monkeypatch.setattr(svc.install_plan, "dist_in_use", lambda name: name in inuse)
    env["plan"] = make_plan(["paddleocr"], mode="now")
    assert client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]}).json()["mode"] == "now"
    inuse.add("numpy")
    env["calls"] = 0
    b = queue(client).json()
    assert env["calls"] == 0                      # the cached dry run is still used
    assert b["queued"] is True and b["plan"]["loaded"] == ["numpy"]


def test_a_no_preview_plan_can_still_be_queued_from_the_cache(client, env, monkeypatch):
    # The real no-preview shape (Windows): restart, a note, nothing to confirm.
    monkeypatch.setattr(svc.install_plan, "_is_windows", lambda: True)
    monkeypatch.setattr(svc.install_plan, "dist_in_use", lambda name: False)
    env["plan"] = {"keys": ["paddleocr"], "available": False, "mode": "restart", "installs": [],
                   "changes": [], "loaded": [], "blocked": [], "needs_confirm": [],
                   "summary": [], "before": {}, "swap": [],
                   "note": "Couldn't preview what will change: no network. "
                           "It will install when you restart Baihe."}
    assert client.post(f"{BASE}/plan", json={"packages": ["paddleocr"]}).json()["mode"] == "restart"
    b = queue(client).json()
    assert b["queued"] is True and b["install_now"] is False
    assert "install when you restart" in b["plan"]["note"]


# --- queue the outdated-package update, then restart to apply it ----------------

@pytest.fixture
def outdated(env, monkeypatch, tmp_path):
    from services import diagnostics_installs_service as installs
    seen = {}
    monkeypatch.setattr(installs.packages_svc, "outdated_upgrade_specs",
                        lambda: (["sentencepiece", "nagisa"], ["sentencepiece==0.2.3", "nagisa==0.3.2"]))

    def build_plan(keys, jobs_running=False, specs=None):
        seen["specs"] = specs
        return dict(env["plan"], keys=list(keys))
    monkeypatch.setattr(installs.install_plan, "build_plan", build_plan)
    return seen


def queue_outdated(client, **kw):
    return client.post("/api/diagnostics/dependencies/upgrade-outdated/queue", json={"confirm": True, **kw})


def test_the_outdated_update_is_queued_pinned_to_the_shown_versions(client, outdated, tmp_path):
    r = queue_outdated(client, accept_risk=True)
    assert r.status_code == 200 and r.json()["queued"] is True
    assert outdated["specs"] == ["sentencepiece==0.2.3", "nagisa==0.3.2"]
    doc = json.loads((tmp_path / "pending_install" / "pending.json").read_text())
    assert doc["payload"]["packages"] == ["sentencepiece", "nagisa"]
    assert doc["payload"]["pins"] == {"sentencepiece": "0.2.3", "nagisa": "0.3.2"}


def test_the_outdated_queue_needs_confirm_and_is_refused_when_blocked_or_risky(client, outdated, env):
    assert client.post("/api/diagnostics/dependencies/upgrade-outdated/queue", json={}).status_code == 422
    env["plan"] = make_plan([], blocked=["No."])
    assert queue_outdated(client).status_code in (409, 422)
    env["plan"] = make_plan([], confirm=["Downgrades numpy."])
    assert queue_outdated(client).status_code == 409
    assert pending_install.read_pending() == (None, None)


def test_the_outdated_queue_does_not_overwrite_a_waiting_install(client, outdated):
    assert queue_outdated(client, accept_risk=True).status_code == 200
    assert queue_outdated(client, accept_risk=True).status_code == 409


def _restart_env(monkeypatch, problem=None, jobs=()):
    started = []
    monkeypatch.setattr(svc.source_update_service, "restart_problem", lambda: problem)
    monkeypatch.setattr(svc.source_update_service, "restart_through_start_bat",
                        lambda actor, why: started.append(why))
    monkeypatch.setattr(svc.background_jobs, "active_job_ids", lambda: list(jobs))
    return started


def test_restart_runs_when_something_is_queued_and_no_job_is_running(client, env, monkeypatch):
    started = _restart_env(monkeypatch)
    queue(client, accept_risk=True)
    r = client.post(f"{BASE}/restart", json={"confirm": True})
    assert r.status_code == 202 and r.json() == {"restarting": True, "stopped_jobs": []}
    assert len(started) == 1


def test_restart_refuses_without_confirm_or_without_a_queued_install(client, env, monkeypatch):
    started = _restart_env(monkeypatch)
    assert client.post(f"{BASE}/restart", json={"confirm": True}).status_code == 409
    queue(client, accept_risk=True)
    assert client.post(f"{BASE}/restart", json={}).status_code == 422
    assert started == []


def test_restart_never_stops_a_job_unless_told_to(client, env, monkeypatch):
    started = _restart_env(monkeypatch, jobs=["translate_5"])
    queue(client, accept_risk=True)
    assert client.get(BASE).json()["restart"]["running_jobs"] == ["translate_5"]
    r = client.post(f"{BASE}/restart", json={"confirm": True})
    assert r.status_code == 409 and "translate_5" in r.text and started == []
    r = client.post(f"{BASE}/restart", json={"confirm": True, "stop_jobs": True})
    assert r.status_code == 202 and r.json()["stopped_jobs"] == ["translate_5"] and len(started) == 1


def test_restart_refuses_a_job_in_another_process(client, env, monkeypatch):
    started = _restart_env(monkeypatch)
    env["jobs"] = True
    queue(client, accept_risk=True)
    r = client.post(f"{BASE}/restart", json={"confirm": True, "stop_jobs": True})
    assert r.status_code == 409 and started == []


def test_restart_says_why_a_copy_cannot_restart_itself(client, env, monkeypatch):
    started = _restart_env(monkeypatch, problem="This copy was installed with Setup.")
    queue(client, accept_risk=True)
    assert client.get(BASE).json()["restart"]["available"] is False
    r = client.post(f"{BASE}/restart", json={"confirm": True})
    assert r.status_code >= 400 and "Setup" in r.text and started == []


# --- the boot service restarts itself for a queued install ------------------------------

def _service_env(monkeypatch, capable=True, jobs=()):
    asked = []
    monkeypatch.setattr(svc.source_update_service, "runs_as_service", lambda: True)
    monkeypatch.setattr(svc.app_update_service, "updater_present", lambda: True)
    monkeypatch.setattr(svc.app_update_service, "updater_restarts_for_packages", lambda: capable)
    monkeypatch.setattr(svc.app_update_service, "request_package_restart", lambda: asked.append(1))
    monkeypatch.setattr(svc.background_jobs, "active_job_ids", lambda: list(jobs))
    return asked


def test_the_service_restart_button_asks_the_helper(client, env, monkeypatch):
    asked = _service_env(monkeypatch)
    queue(client, accept_risk=True)
    info = client.get(BASE).json()["restart"]
    assert info["available"] is True and info["automatic"] is True
    assert client.post(f"{BASE}/restart", json={"confirm": True}).status_code == 202
    assert asked == [1]


def test_an_older_helper_says_to_run_setup_once(client, env, monkeypatch):
    asked = _service_env(monkeypatch, capable=False)
    queue(client, accept_risk=True)
    info = client.get(BASE).json()["restart"]
    assert info["available"] is False and "Setup" in info["reason"]
    assert client.post(f"{BASE}/restart", json={"confirm": True}).status_code >= 400 and asked == []


def test_the_automatic_restart_waits_for_the_cancel_grace_and_for_idle(client, env, monkeypatch):
    asked = _service_env(monkeypatch)
    queue(client, accept_risk=True)
    created = svc.pending_install.read_pending()[0]["created"]
    svc.auto_apply_tick(now=created + 10)
    assert asked == []
    monkeypatch.setattr(svc.background_jobs, "active_job_ids", lambda: ["live_1"])
    svc.auto_apply_tick(now=created + svc.AUTO_GRACE_SECONDS + 1)
    assert asked == []
    monkeypatch.setattr(svc.background_jobs, "active_job_ids", lambda: [])
    env["jobs"] = True
    svc.auto_apply_tick(now=created + svc.AUTO_GRACE_SECONDS + 2)
    assert asked == []
    env["jobs"] = False
    svc.auto_apply_tick(now=created + svc.AUTO_GRACE_SECONDS + 3)
    svc.auto_apply_tick(now=created + svc.AUTO_GRACE_SECONDS + 60)
    assert asked == [1]


def test_the_automatic_restart_does_nothing_without_the_service_helper(client, env, monkeypatch):
    asked = _service_env(monkeypatch, capable=False)
    queue(client, accept_risk=True)
    svc.auto_apply_tick(now=10**10)
    assert asked == []


def test_queueing_is_refused_for_a_locked_folder_unless_the_service_helper_installs(client, env, monkeypatch):
    from services import diagnostics_installs_service as installs
    monkeypatch.setattr(installs, "_writable", lambda folder: False)
    monkeypatch.setattr(svc, "service_can_restart", lambda: False)
    r = client.post(f"{BASE}/queue", json={"packages": ["paddleocr"], "confirm": True, "accept_risk": True})
    assert r.status_code >= 400 and "locked" in r.text
    assert svc.pending_install.read_pending()[0] is None
    monkeypatch.setattr(svc, "service_can_restart", lambda: True)
    r = client.post(f"{BASE}/queue", json={"packages": ["paddleocr"], "confirm": True, "accept_risk": True})
    assert r.status_code == 200 and r.json()["queued"] is True


def test_a_queue_the_helper_already_handled_is_removed_and_not_asked_for_again(client, env, monkeypatch):
    asked = _service_env(monkeypatch)
    queue(client, accept_risk=True)
    created = svc.pending_install.read_pending()[0]["created"]
    monkeypatch.setattr(svc.app_update_service, "read_package_restart_result",
                        lambda: {"outcome": "ok", "queued_created": created, "downtime_seconds": 30})
    svc.auto_apply_tick(now=created + 10**6)
    assert asked == [] and svc.pending_install.read_pending()[0] is None
    assert client.get(BASE).json()["last_restart"]["outcome"] == "ok"


def test_the_last_restart_line_goes_away_when_dismissed_and_status_is_read_only(client, env, monkeypatch):
    queue(client, accept_risk=True)
    created = svc.pending_install.read_pending()[0]["created"]
    last = {"outcome": "install_failed", "queued_created": created, "at": 123, "restored": 1}
    monkeypatch.setattr(svc.app_update_service, "read_package_restart_result", lambda: last)
    assert client.get(BASE).json()["last_restart"]["outcome"] == "install_failed"
    assert svc.pending_install.read_pending()[0] is not None     # reading never settles the queue
    assert client.post(f"{BASE}/dismiss", json={}).status_code == 200
    assert client.get(BASE).json()["last_restart"] is None


def test_the_helper_capability_is_read_from_the_module_that_holds_the_code(tmp_path, monkeypatch):
    lib = tmp_path / "Baihe Studio Services" / "helper" / "lib" / "installer"
    lib.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    aus = svc.app_update_service
    assert aus.updater_restarts_for_packages() is False
    (lib / "app_swap.py").write_text("def base_accepts(manifest, installed):\n    pass\n")
    assert aus._updater_takes_wheels() is True and aus.updater_restarts_for_packages() is False
    (lib / "service_update.py").write_text("def restart_for_packages(layout, run, port):\n    pass\n")
    assert aus.updater_restarts_for_packages() is True
