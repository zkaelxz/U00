"""Tests for services/review_jobs_service.py + /api/review-jobs (Migration
Slice 44). Fully mocked: a fake engine and stubbed LLM helpers, no network."""
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import emotion
import translate_engines
import translation_guide
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import review_jobs_service as svc, translate_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)

SECRET = "sk-ant-SECRET1234567890abcdef"


class FakeEngine:
    model = "fake-model"
    supports_reference = True
    last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, ctx=None):
        return [f"EN:{t}" for t in texts]


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(translate_service, "_resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    yield
    background_jobs.clear_all_jobs()


@pytest.fixture
def client():
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _seed(rows=(("你好", "hello", None), ("再见", "bye", None))):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en, flag=fl)
                        for i, (z, en, fl) in enumerate(rows)])
    return did


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _spy_save_lines(monkeypatch):
    calls = []
    real = db.save_lines

    def spy(d, ls, fields=None):
        calls.append(fields)
        return real(d, ls, fields=fields)
    monkeypatch.setattr(db, "save_lines", spy)
    return calls


def test_flag_writes_flag_fields_only_by_id(monkeypatch):
    did = _seed()
    calls = _spy_save_lines(monkeypatch)

    def fake_flag(lines, engine, **kw):
        lines[1].flag, lines[1].flag_note = "uncertain", "check"
    monkeypatch.setattr(translate_engines, "flag_uncertain_lines", fake_flag)
    out = svc.start_flag_review(did, engine_name="claude")
    assert out["job_id"] == f"flag_{did}" and out["kind"] == "flag"
    job = _wait(out["job_id"])
    assert job["status"] == "done" and job["result"]["flagged_count"] == 1
    rows = db.load_lines(did)
    assert rows[1]["flag"] == "uncertain" and rows[0]["flag"] is None
    assert calls == [("flag", "flag_note")]


def test_flag_does_not_overwrite_concurrent_user_edit(monkeypatch):
    did = _seed()
    rows = db.load_lines(did)

    def fake_flag(lines, engine, **kw):
        # the user edits line 0's translation while the job runs
        db.save_lines(did, [Line(id=rows[0]["id"], idx=0, start=0, end=1, zh="你好", en="mine")],
                      fields=("en",))
        lines[0].flag = "uncertain"
    monkeypatch.setattr(translate_engines, "flag_uncertain_lines", fake_flag)
    _wait(svc.start_flag_review(did, engine_name="claude")["job_id"])
    row = db.load_lines(did)[0]
    assert row["en"] == "mine" and row["flag"] == "uncertain"


def test_consistency_saves_issues(monkeypatch):
    did = _seed()
    monkeypatch.setattr(translate_engines, "check_consistency_llm",
                        lambda lines, engine, **kw: ([{"term": "X", "variants": ["a", "b"],
                                                       "note": "n"}], 0, 1))
    out = svc.start_consistency_check(did, engine_name="claude")
    assert _wait(out["job_id"])["result"]["issue_count"] == 1
    assert db.load_consistency_issues(did)[0]["term"] == "X"


def test_emotion_saved_by_line_id(monkeypatch):
    did = _seed()
    seen = {}

    def fake_detect(lines, engine, use_audio_cues=False, **kw):
        seen["cues"] = use_audio_cues
        return {1: {"emotion": "sad", "intensity": 0.5}}
    monkeypatch.setattr(emotion, "detect_emotions", fake_detect)
    out = svc.start_emotion_tagging(did, engine_name="claude", use_audio_cues=True)
    assert _wait(out["job_id"])["status"] == "done"
    assert seen["cues"] is True
    assert list(db.load_emotions(did)) == [1]


def test_notes_saved(monkeypatch):
    did = _seed()
    monkeypatch.setattr(translation_guide, "generate_translation_notes_llm",
                        lambda lines, engine, **kw: [{"line_idx": 0, "term": "你好",
                                                      "note_type": "idiom", "note": "hi"}])
    out = svc.start_translation_notes(did, engine_name="claude")
    assert _wait(out["job_id"])["result"]["note_count"] == 1
    assert len(db.list_translation_notes(did)) == 1


def test_fix_flagged_retranslates_and_clears_flag(monkeypatch):
    did = _seed((("你好", "old", "uncertain"), ("再见", "keep", None)))
    calls = _spy_save_lines(monkeypatch)
    out = svc.start_fix_flagged(did, engine_name="claude")
    job = _wait(out["job_id"])
    assert job["result"]["fixed_count"] == 1 and job["result"]["total_flagged"] == 1
    rows = db.load_lines(did)
    assert rows[0]["en"] == "EN:你好" and rows[0]["flag"] is None
    assert rows[1]["en"] == "keep"
    assert calls == [("zh", "en", "flag", "flag_note")]


def test_fix_flagged_needs_flagged_lines():
    with pytest.raises(UnsupportedOperationError):
        svc.start_fix_flagged(_seed(), engine_name="claude")


def test_job_error_is_redacted(monkeypatch):
    did = _seed()

    def boom(lines, engine, **kw):
        raise RuntimeError(f"auth failed key={SECRET}")
    monkeypatch.setattr(translate_engines, "check_consistency_llm", boom)
    job = _wait(svc.start_consistency_check(did, engine_name="claude")["job_id"])
    assert job["status"] == "error" and SECRET not in (job["error"] or "")


def test_duplicate_start_conflict(monkeypatch):
    did = _seed()
    monkeypatch.setattr(background_jobs, "is_running", lambda job_id: True)
    with pytest.raises(ConflictError):
        svc.start_consistency_check(did, engine_name="claude")


def test_validation_errors(monkeypatch):
    did = _seed()
    with pytest.raises(NotFoundError):
        svc.start_flag_review(9999)
    with pytest.raises(InvalidInputError):
        svc.start_flag_review(did, engine_name="nope")
    with pytest.raises(UnsupportedOperationError):
        svc.start_emotion_tagging(did, engine_name="deepl")
    with pytest.raises(UnsupportedOperationError):
        svc.start_flag_review(_seed((("你好", "", None),)), engine_name="claude")
    with pytest.raises(InvalidInputError):
        svc.start_fix_flagged(did, engine_name="claude", job_cost_cap_usd=-1)
    monkeypatch.setattr(translate_service, "_resolve_api_key", lambda name, env_path=None: None)
    with pytest.raises(DependencyUnavailableError):
        svc.start_translation_notes(did, engine_name="claude")


def test_http_statuses(client, monkeypatch):
    did = _seed()
    monkeypatch.setattr(translate_engines, "check_consistency_llm",
                        lambda lines, engine, **kw: ([], 0, 1))
    r = client.post(f"/api/review-jobs/dramas/{did}/consistency", json={"engine": "claude"})
    assert r.status_code == 200 and r.json()["job_id"] == f"consistency_{did}"
    _wait(r.json()["job_id"])
    assert client.post("/api/review-jobs/dramas/9999/flag", json={}).status_code == 404
    assert client.post(f"/api/review-jobs/dramas/{did}/flag",
                       json={"engine": "nope"}).status_code == 422
    assert client.post(f"/api/review-jobs/dramas/{did}/notes",
                       json={"key": "x"}).status_code == 422
    assert client.post(f"/api/review-jobs/dramas/{did}/fix-flagged", json={}).status_code == 400
    monkeypatch.setattr(translate_service, "_resolve_api_key", lambda name, env_path=None: None)
    r = client.post(f"/api/review-jobs/dramas/{did}/emotion", json={"engine": "claude"})
    assert r.status_code == 503
