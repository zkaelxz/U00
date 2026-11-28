"""The opt-in "resume interrupted translation batches when the app starts"
setting (bulk.auto_resume). Fully mocked: the fake batch provider from
test_translate_run_bulk_reflect, no network, no real keys."""
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
from jobs import job_launch
from bulk import bulk_poll
import db
from api.server import create_app
from services import notification_service, settings_service
from services import translate_run_service as svc
from services import translate_service
from tests.test_translate_run_bulk_reflect import (  # noqa: F401
    _current, _env, _fake_engines, _seed)

pytestmark = pytest.mark.usefixtures("free_engine")


def _pending_batch(did, status="submitted", engine="echo"):
    rows = db.load_lines(did)
    return db.create_bulk_job(did, engine, "m", status, [(rows[0]["id"], "k", "h", "")])


def _wait_idle(bulk_ids, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if not any(background_jobs.is_running(bulk_poll.poll_job_id(b)) for b in bulk_ids):
            return
        time.sleep(0.02)
    raise AssertionError("pollers did not stop")


def _notes():
    return [e["text"] for e in notification_service.list_recent(None)]


def _setup(monkeypatch):
    notification_service.reset_for_tests()
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch, ended=False)   # the batch stays pending
    return did


def test_on_by_default(isolated_db):
    assert settings_service.get("bulk_auto_resume") is True


def test_turned_off_touches_nothing(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    settings_service.set_settings({"bulk_auto_resume": False})
    bid = _pending_batch(did)
    calls = []
    monkeypatch.setattr(bulk_poll, "resume_pending", lambda *a, **k: calls.append(a) or {})
    assert settings_service.get("bulk_auto_resume") is False
    assert settings_service.get_settings_overview()["bulk_auto_resume"] is False
    assert svc.resume_interrupted_at_startup() == {"enabled": False, "resumed": 0, "skipped": 0}
    assert calls == []
    assert db.get_bulk_job(bid)["status"] == "submitted"
    assert not background_jobs.is_running(bulk_poll.poll_job_id(bid))


def test_on_resumes_only_interrupted_batches(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    live = _pending_batch(did)
    done = _pending_batch(did, status="applied")
    gone = _pending_batch(did, status="cancelled")
    settings_service.set_settings({"bulk_auto_resume": True})
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 1, "skipped": 0}
    assert background_jobs.is_running(bulk_poll.poll_job_id(live))
    bulk_poll.cancel_bulk_job(live)
    _wait_idle([live])
    assert db.get_bulk_job(done)["status"] == "applied"
    assert db.get_bulk_job(gone)["status"] == "cancelled"
    assert not background_jobs.is_running(bulk_poll.poll_job_id(done))


def test_missing_key_is_skipped_and_noted(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: "")
    bid = _pending_batch(did)
    settings_service.set_settings({"bulk_auto_resume": True})
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 0, "skipped": 1}
    assert not background_jobs.is_running(bulk_poll.poll_job_id(bid))
    assert db.get_bulk_job(bid)["status"] == "submitted"
    assert any("no API key" in t for t in _notes())


def test_a_paid_engine_is_held_for_the_person_and_noted(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    paid = _pending_batch(did, engine="claude")
    free = _pending_batch(did, engine="echo")
    settings_service.set_settings({"bulk_auto_resume": True})
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 1, "skipped": 1}
    assert not background_jobs.is_running(bulk_poll.poll_job_id(paid))
    assert db.get_bulk_job(paid)["status"] == "submitted"
    assert any("paid engines wait" in t for t in _notes())
    bulk_poll.cancel_bulk_job(free)
    _wait_idle([free])


def test_each_batch_gets_one_log_line_saying_what_the_restart_did(isolated_db, monkeypatch):
    from tests.test_jobs_store import app_log_lines
    logged = app_log_lines(monkeypatch, "Bulk translation batch")
    did = _setup(monkeypatch)
    paid = _pending_batch(did, engine="claude")
    free = _pending_batch(did, engine="echo")
    settings_service.set_settings({"bulk_auto_resume": True})
    svc.resume_interrupted_at_startup()
    assert sorted(logged) == sorted([
        f"Bulk translation batch {paid} (title #{did}, submitted) after the restart: "
        "left pending: paid engines wait until you press Resume.",
        f"Bulk translation batch {free} (title #{did}, submitted) after the restart: resumed.",
    ])
    bulk_poll.cancel_bulk_job(free)
    _wait_idle([free])
    logged.clear()
    settings_service.set_settings({"bulk_auto_resume": False})
    svc.resume_interrupted_at_startup()
    assert logged == [f"Bulk translation batch {paid} (title #{did}, submitted) after the restart: "
                      "left pending: automatic resume is off."]


def test_nothing_starts_while_a_restore_holds_the_library(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    bid = _pending_batch(did)
    settings_service.set_settings({"bulk_auto_resume": True})
    assert job_launch.acquire_exclusive("restore")
    try:
        assert svc.resume_interrupted_at_startup()["resumed"] == 0
    finally:
        job_launch.release_exclusive()
    assert not background_jobs.is_running(bulk_poll.poll_job_id(bid))
    assert db.get_bulk_job(bid)["status"] == "submitted"


def test_startup_error_is_swallowed_and_logged_redacted(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    _pending_batch(did)
    settings_service.set_settings({"bulk_auto_resume": True})
    secret = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
    logged = []
    import applog

    class Log:
        def warning(self, fmt, *args):
            logged.append(fmt % args)
    monkeypatch.setattr(applog, "get_logger", lambda: Log())

    def boom(*a, **k):
        raise RuntimeError(f"failed with key {secret}")
    monkeypatch.setattr(bulk_poll, "resume_pending", boom)
    assert svc.resume_interrupted_at_startup()["resumed"] == 0
    assert logged and secret not in logged[0]


def test_startup_hook_runs_it_and_survives_its_failure(isolated_db, monkeypatch):
    from api import background
    from services import chapter_check_service
    monkeypatch.setattr(chapter_check_service, "ensure_scheduler_started", lambda: None)
    seen = []
    monkeypatch.setattr(svc, "resume_interrupted_at_startup", lambda: seen.append(1))
    monkeypatch.setattr(background, "_started", None)
    background.start_background_services()
    assert seen == [1]

    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(svc, "resume_interrupted_at_startup", boom)
    monkeypatch.setattr(background, "_started", None)
    assert background.start_background_services()["chapter_scheduler"] is True
    monkeypatch.setattr(background, "_started", None)


def test_settings_route_writes_the_toggle(isolated_db):
    client = TestClient(create_app(), headers={"X-Baihe-Local": "1"})
    assert client.get("/api/settings").json()["bulk_auto_resume"] is True
    assert client.post("/api/settings", json={"bulk_auto_resume": False}).json()["bulk_auto_resume"] is False
    assert client.post("/api/settings", json={"bulk_auto_resume": True}).json()["bulk_auto_resume"] is True
    assert client.post("/api/settings", json={"bulk_auto_resume": "yes"}).status_code == 422


def test_a_job_whose_package_is_missing_is_skipped_and_noted(isolated_db, monkeypatch):
    from engine_backends import engine_registry
    from lib.errors import MissingPackageError
    did = _setup(monkeypatch)

    def get_engine(name, *a, **k):
        raise MissingPackageError("requests")
    monkeypatch.setattr(engine_registry, "get_engine", get_engine)
    broken = _pending_batch(did, engine="echo")
    settings_service.set_settings({"bulk_auto_resume": True})
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 0, "skipped": 1}
    assert not background_jobs.is_running(bulk_poll.poll_job_id(broken))
    assert db.get_bulk_job(broken)["status"] == "submitted"
    assert any("package is not installed" in t for t in _notes())
