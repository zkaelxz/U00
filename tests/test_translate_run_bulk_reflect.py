"""Tests for Reflect and bulk modes of start_translate_run +
POST /api/translate-run/dramas/{id}/run (Migration Slice 41). Fully mocked:
a fake batch provider, a fake Reflect helper, no network, no real keys."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
import bulk_translate as bt
import db
import translate_engines as te
from api.server import create_app
from core import Line
from services import translate_run_service as svc
from services import translate_service
from services.service_errors import (ConflictError, InvalidInputError,
                                      UnsupportedOperationError)


def _seed(texts, engine="claude"):
    did = db.create_drama(title_zh="D", translation_engine=engine)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                        for i, (z, en) in enumerate(texts)])
    return did


def _wait(job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {[(j['status'], j['last_error']) for j in db.list_bulk_jobs()]}")


class FakeProvider:
    """Answers each request id-keyed, keys reversed so nothing lines up by
    position. ended=False keeps the batch pending (for the cancel test)."""
    submitted, cancelled, _jobs = [], [], {}  # shared: the job builds more than one

    def __init__(self, drama_id, ended=True):
        self.drama_id, self.ended = drama_id, ended

    def build_request(self, key, context, numbered):
        return {"key": key}

    def build_prompt_request(self, key, prompt, max_tokens=3000):
        return {"key": key}

    def submit(self, requests_):
        job = next(j for j in db.list_bulk_jobs(self.drama_id) if j["status"] == "submitting")
        batch_id = f"batch_{len(self.submitted)}"
        self.submitted.append(requests_)
        self._jobs[batch_id] = job
        return batch_id

    def poll(self, batch_id):
        return "ended" if self.ended else "pending"

    def results(self, batch_id):
        job = self._jobs[batch_id]
        tag = job.get("stage") or "tr"
        by_key = {}
        for r in db.list_bulk_job_lines(job["id"]):
            by_key.setdefault(r["request_key"], []).append(r["line_id"])
        for key, ids in reversed(list(by_key.items())):
            payload = {str(lid): f"{tag}-{lid}" for lid in reversed(ids)}
            yield key, json.dumps(payload), {"input_tokens": 0}, None

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    background_jobs.clear_all_jobs()
    FakeProvider.submitted, FakeProvider.cancelled, FakeProvider._jobs = [], [], {}
    monkeypatch.setattr(bt, "POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: "sk-fake")
    monkeypatch.setattr(svc, "pick_summary_engine", lambda *a, **k: (None, None))
    yield
    background_jobs.clear_all_jobs()


def _fake_engines(monkeypatch, ended=True):
    engine = te.ClaudeEngine("sk-ant-fake")
    monkeypatch.setattr(te, "get_engine", lambda *a, **k: engine)
    monkeypatch.setattr(bt, "make_provider",
                        lambda choice, e: FakeProvider(_current["did"], ended=ended))
    return engine


_current = {}


def test_bulk_translation_applies_by_line_id_and_keeps_hand_edits(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "My edit"), ("三", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    out = svc.start_translate_run(did, bulk=True)
    assert out["job_id"] == f"bulk_translate_{did}" and out["bulk"] and out["target_line_count"] == 2
    job = _wait(out["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["status"] == "applied"
    rows = {r["zh"]: r for r in db.load_lines(did)}
    assert rows["一"]["en"] == f"tr-{rows['一']['id']}"
    assert rows["三"]["en"] == f"tr-{rows['三']['id']}"
    assert rows["二"]["en"] == "My edit"


def test_bulk_reflect_follows_all_three_stages_inside_the_job(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    out = svc.start_translate_run(did, bulk=True, reflect=True)
    job = _wait(out["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["stage"] == "expressive" and job["result"]["status"] == "applied"
    stages = sorted(j["stage"] for j in db.list_bulk_jobs(did))
    assert stages == ["expressive", "faithful", "reflect"]
    for r in db.load_lines(did):
        assert r["en"] == f"expressive-{r['id']}"


def test_cancel_cancels_the_pending_batch(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch, ended=False)
    out = svc.start_translate_run(did, bulk=True)
    for _ in range(200):
        if FakeProvider.submitted:
            break
        time.sleep(0.02)
    background_jobs.request_cancel(out["job_id"])
    _wait(out["job_id"])
    bulk = db.list_bulk_jobs(did)[0]
    assert bulk["status"] == "cancelled"
    assert FakeProvider.cancelled == [bulk["provider_batch_id"]]
    assert db.load_lines(did)[0]["en"] == ""


def test_pending_bulk_job_is_a_conflict(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    rows = db.load_lines(did)
    db.create_bulk_job(did, "claude", "m", "submitted", [(rows[0]["id"], "k", "h", "")])
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, bulk=True)


def test_bulk_estimate_above_cap_is_refused(isolated_db, monkeypatch):
    did = _seed([("你好" * 50, "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, bulk=True, job_cost_cap_usd=1e-9)
    assert db.list_bulk_jobs(did) == []


def test_mode_validation(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, reflect=True,
                                fallback_chain=[{"engine": "gemini", "model": None}])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, bulk=True, line_ids=[db.load_lines(did)[0]["id"]])
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="nllb", reflect=True)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="ollama", bulk=True)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="gemini", bulk=True, gemini_free_tier=True)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="deepseek", bulk=True, reflect=True)


def test_reflect_live_run_uses_reflect_helper_and_saves_notes(isolated_db, monkeypatch):
    did = _seed([("你好", ""), ("再见", "keep")])
    seen = []

    def fake_reflect(engine, zh_lines, context, usage_cb=None, max_retries=1):
        seen.append(list(context["line_ids"]))
        return [f"R:{z}" for z in zh_lines], ["crit" for _ in zh_lines]
    monkeypatch.setattr(te, "reflect_translate_batch", fake_reflect)
    out = svc.start_translate_run(did, engine_name="fake", reflect=True)
    assert out["reflect"] and not out["bulk"] and out["job_id"] == f"translate_{did}"
    assert _wait(out["job_id"])["status"] == "done"
    ens = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert ens == {"你好": "R:你好", "再见": "keep"}
    assert len(seen) == 1 and len(seen[0]) == 1
    assert any(n.get("note") == "crit" for n in db.list_translation_notes(did))


def test_api_reflect_and_bulk_codes(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    client = TestClient(create_app(), headers={"X-Baihe-Local": "1"})
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"engine": "nllb", "reflect": True})
    assert r.status_code == 400
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"bulk": True, "fallback_chain": [{"engine": "gemini"}]})
    assert r.status_code == 422
    r = client.post(f"/api/translate-run/dramas/{did}/run", json={"bulk": True})
    assert r.status_code == 200 and r.json()["bulk"] is True
    assert _wait(r.json()["job_id"])["status"] == "done"
    assert client.post("/api/translate-run/dramas/999/bulk/resume").status_code == 404


def test_resume_starts_poller_for_pending_job(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch, ended=False)
    rows = db.load_lines(did)
    bid = db.create_bulk_job(did, "claude", "m", "submitted", [(rows[0]["id"], "k", "h", "")])
    out = svc.resume_bulk_translations(did)
    assert out == {"drama_id": did, "jobs": [{"bulk_job_id": bid, "state": "polling"}]}
    bt.cancel_bulk_job(bid)
