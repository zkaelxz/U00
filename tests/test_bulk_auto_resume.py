"""The opt-in "resume interrupted translation batches when the app starts"
setting (bulk.auto_resume). Fully mocked: the fake batch provider from
test_translate_run_bulk_reflect, no network, no real keys."""
import time

from fastapi.testclient import TestClient

import background_jobs
import bulk_translate as bt
import db
from api.server import create_app
from services import notification_service, settings_service
from services import translate_run_service as svc
from services import translate_service
from tests.test_translate_run_bulk_reflect import (  # noqa: F401
    _current, _env, _fake_engines, _seed)


def _pending_batch(did, status="submitted", engine="claude"):
    rows = db.load_lines(did)
    return db.create_bulk_job(did, engine, "m", status, [(rows[0]["id"], "k", "h", "")])


def _wait_idle(bulk_ids, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if not any(background_jobs.is_running(bt.poll_job_id(b)) for b in bulk_ids):
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


def test_off_by_default_touches_nothing(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    bid = _pending_batch(did)
    calls = []
    monkeypatch.setattr(bt, "resume_pending", lambda *a, **k: calls.append(a) or {})
    assert settings_service.get_bulk_auto_resume() is False
    assert settings_service.get_settings_overview()["bulk_auto_resume"] is False
    assert svc.resume_interrupted_at_startup() == {"enabled": False, "resumed": 0, "skipped": 0}
    assert calls == []
    assert db.get_bulk_job(bid)["status"] == "submitted"
    assert not background_jobs.is_running(bt.poll_job_id(bid))


def test_on_resumes_only_interrupted_batches(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    live = _pending_batch(did)
    done = _pending_batch(did, status="applied")
    gone = _pending_batch(did, status="cancelled")
    settings_service.set_settings({"bulk_auto_resume": True})
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 1, "skipped": 0}
    assert background_jobs.is_running(bt.poll_job_id(live))
    bt.cancel_bulk_job(live)
    _wait_idle([live])
    assert db.get_bulk_job(done)["status"] == "applied"
    assert db.get_bulk_job(gone)["status"] == "cancelled"
    assert not background_jobs.is_running(bt.poll_job_id(done))


def test_missing_key_is_skipped_and_noted(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: "")
    bid = _pending_batch(did)
    settings_service.set_settings({"bulk_auto_resume": True})
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 0, "skipped": 1}
    assert not background_jobs.is_running(bt.poll_job_id(bid))
    assert db.get_bulk_job(bid)["status"] == "submitted"
    assert any("no API key" in t for t in _notes())


def test_used_up_spending_cap_is_skipped_and_noted(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    bid = _pending_batch(did)
    settings_service.set_settings({"bulk_auto_resume": True})
    monkeypatch.setattr(svc, "month_cap_usd", lambda: 5.0)
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **k: 9.0)
    assert svc.resume_interrupted_at_startup() == {"enabled": True, "resumed": 0, "skipped": 1}
    assert not background_jobs.is_running(bt.poll_job_id(bid))
    assert any("spending cap" in t for t in _notes())


def test_nothing_starts_while_a_restore_holds_the_library(isolated_db, monkeypatch):
    did = _setup(monkeypatch)
    bid = _pending_batch(did)
    settings_service.set_settings({"bulk_auto_resume": True})
    assert background_jobs.acquire_exclusive("restore")
    try:
        assert svc.resume_interrupted_at_startup()["resumed"] == 0
    finally:
        background_jobs.release_exclusive()
    assert not background_jobs.is_running(bt.poll_job_id(bid))
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
    monkeypatch.setattr(bt, "resume_pending", boom)
    assert svc.resume_interrupted_at_startup()["resumed"] == 0
    assert logged and secret not in logged[0]


def test_startup_hook_runs_it_and_survives_its_failure(isolated_db, monkeypatch):
    from api import background
    from sources import chapter_check
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda: None)
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
    assert client.get("/api/settings").json()["bulk_auto_resume"] is False
    assert client.post("/api/settings", json={"bulk_auto_resume": True}).json()["bulk_auto_resume"] is True
    assert client.post("/api/settings", json={"bulk_auto_resume": "yes"}).status_code == 422
