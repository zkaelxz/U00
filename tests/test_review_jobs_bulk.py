"""Parity R49: the Review-stage AI checks in bulk (half price) mode, through
services/review_jobs_service.py and /api/review-jobs with `bulk: true`.
Fully mocked: a fake batch provider, no network."""
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import bulk_providers
import bulk_translate
import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import (review_jobs_service as svc, settings_service, translate_run_service,
                      translate_service)
from services.service_errors import ConflictError, UnsupportedOperationError


class FakeEngine:
    model = "fake-model"


class FakeProvider:
    """Answers every request at once. reply(prompt_line_ids) -> text; the
    results come back in reverse order, so matching by position would put
    them on the wrong lines."""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.cancelled = []

    def build_prompt_request(self, key, prompt, max_tokens=3000):
        return {"custom_id": key, "prompt": prompt}

    def submit(self, requests_):
        self.requests = list(requests_)
        return "batch-1"

    def poll(self, batch_id):
        return "ended"

    def results(self, batch_id):
        for r in reversed(self.requests):
            yield r["custom_id"], self.reply(r), {}, None

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    yield
    background_jobs.clear_all_jobs()


def _provider(monkeypatch, reply):
    prov = FakeProvider(reply)
    monkeypatch.setattr(bulk_providers, "make_provider", lambda choice, engine: prov)
    return prov


def _seed(n=4):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"中{i}", en=f"en{i}")
                        for i in range(n)])
    return did


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_bulk_flag_applies_by_line_id_with_field_scoped_write(monkeypatch):
    did = _seed()
    ids = [r["id"] for r in db.load_lines(did)]
    target = ids[2]
    # one request per batch; flag line `target` by its permanent id
    prov = _provider(monkeypatch, lambda r: json.dumps(
        [{"line_idx": target, "reason": "uncertain_translation", "note": "look"}]))
    calls = []
    real = db.save_lines
    monkeypatch.setattr(db, "save_lines",
                        lambda d, ls, fields=None: (calls.append(fields), real(d, ls, fields=fields))[1])
    monkeypatch.setitem(svc._BULK_SUBMIT, "flag",
                        lambda *a, **k: _real_flag(*a, batch_size=1, **k))
    out = svc.start_flag_review(did, engine_name="claude", bulk=True)
    assert out["job_id"] == f"bulk_flag_{did}" and out["bulk"] is True
    assert _wait(out["job_id"])["status"] == "done"
    rows = {r["id"]: r for r in db.load_lines(did)}
    assert rows[target]["flag"] == "uncertain_translation" and rows[target]["flag_note"] == "look"
    assert all(rows[i]["flag"] is None for i in ids if i != target)
    assert calls and all(f == ("flag", "flag_note") for f in calls)
    assert len(prov.requests) == 4
    job = db.list_bulk_jobs(did)[0]
    assert job["kind"] == "flag" and job["status"] == "applied"


_real_flag = bulk_translate.submit_bulk_flag


def test_bulk_notes_and_consistency_and_emotion_submit_their_kind(monkeypatch):
    did = _seed(2)
    ids = [r["id"] for r in db.load_lines(did)]
    _provider(monkeypatch, lambda r: json.dumps(
        [{"line_idx": ids[1], "term": "t", "note": "n", "note_type": "cultural"}]))
    _wait(svc.start_translation_notes(did, engine_name="claude", bulk=True)["job_id"])
    assert db.list_bulk_jobs(did)[0]["kind"] == "translation_notes"
    assert [n["line_id"] for n in db.list_translation_notes(did)] == [ids[1]]

    _provider(monkeypatch, lambda r: "[]")
    _wait(svc.start_consistency_check(did, engine_name="gemini", bulk=True,
                                      gemini_free_tier=False)["job_id"])
    assert db.list_bulk_jobs(did)[0]["kind"] == "consistency"

    seen = {}

    def fake_emotion(*a, **k):
        seen.update(k)
        return _real_emotion(*a, **k)
    monkeypatch.setitem(svc._BULK_SUBMIT, "emotion", fake_emotion)
    _wait(svc.start_emotion_tagging(did, engine_name="claude", bulk=True,
                                    use_audio_cues=False)["job_id"])
    assert db.list_bulk_jobs(did)[0]["kind"] == "emotion" and seen["use_audio_cues"] is False


_real_emotion = bulk_translate.submit_bulk_emotion


@pytest.mark.parametrize("engine,free", [("deepseek", False), ("ollama", False),
                                         ("gemini", True)])
def test_bulk_needs_a_paid_batch_engine(monkeypatch, engine, free):
    did = _seed()
    with pytest.raises(UnsupportedOperationError):
        svc.start_flag_review(did, engine_name=engine, gemini_free_tier=free, bulk=True)


def test_bulk_refused_when_monthly_cap_spent(monkeypatch):
    did = _seed()
    _provider(monkeypatch, lambda r: "[]")
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 1.0)
    monkeypatch.setattr(db, "get_month_spend", lambda: 5.0)
    with pytest.raises(UnsupportedOperationError):
        svc.start_consistency_check(did, engine_name="claude", bulk=True)
    assert db.list_bulk_jobs(did) == []


def test_pending_bulk_of_same_kind_conflicts(monkeypatch):
    did = _seed()
    db.create_bulk_job(did, "claude", "m", "submitted", [], kind="flag")
    with pytest.raises(ConflictError):
        svc.start_flag_review(did, engine_name="claude", bulk=True)
    # another kind is fine
    _provider(monkeypatch, lambda r: "[]")
    _wait(svc.start_consistency_check(did, engine_name="claude", bulk=True)["job_id"])


def test_bulk_job_visible_as_drama_job():
    from services import ownership_service
    assert ownership_service.drama_id_of_job("bulk_flag_7") == 7
    assert ownership_service.drama_id_of_job("bulk_notes_7") == 7


def test_submission_error_is_redacted(monkeypatch):
    did = _seed()
    prov = _provider(monkeypatch, lambda r: "[]")

    def boom(requests_):
        raise RuntimeError("bad key sk-ant-SECRET1234567890abcdef")
    prov.submit = boom
    job = _wait(svc.start_flag_review(did, engine_name="claude", bulk=True)["job_id"])
    assert job["status"] == "error"
    assert "SECRET1234567890" not in json.dumps(job, default=str)
    assert "SECRET1234567890" not in (db.list_bulk_jobs(did)[0]["last_error"] or "")


def test_api_bulk_flag_and_fix_flagged_refuses_bulk(monkeypatch):
    did = _seed()
    _provider(monkeypatch, lambda r: "[]")
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.post(f"/api/review-jobs/dramas/{did}/flag", json={"engine": "claude", "bulk": True})
    assert r.status_code == 200, r.text
    assert r.json()["bulk"] is True and r.json()["job_id"] == f"bulk_flag_{did}"
    _wait(r.json()["job_id"])
    listed = c.get(f"/api/translate-run/dramas/{did}/bulk")
    assert listed.status_code == 200
    assert [j["kind"] for j in listed.json()["jobs"]] == ["flag"]
    r = c.post(f"/api/review-jobs/dramas/{did}/fix-flagged", json={"engine": "claude", "bulk": True})
    assert r.status_code == 422
    r = c.post(f"/api/review-jobs/dramas/{did}/notes", json={"engine": "deepseek", "bulk": True})
    assert r.status_code == 400
    r = c.post(f"/api/review-jobs/dramas/{did}/consistency", json={"engine": "claude", "bulk": "yes"})
    assert r.status_code == 422


def test_tab_glue_uses_the_service(monkeypatch):
    """The Workspace tab's Bulk checkbox now goes through submit_bulk_review."""
    did = _seed()
    prov = _provider(monkeypatch, lambda r: "[]")
    started = []
    monkeypatch.setattr(bulk_translate, "start_poller", lambda *a, **k: started.append(a) or True)
    bulk_id, p = svc.submit_bulk_review("translation_notes", did, FakeEngine(), "claude")
    assert p is prov and db.get_bulk_job(bulk_id)["kind"] == "translation_notes"
