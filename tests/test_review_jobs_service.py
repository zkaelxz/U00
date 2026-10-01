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
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
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
    assert calls == []  # conditional per-row writes, never a save_lines sync


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


def test_flag_keeps_manual_flag_change_made_during_job(monkeypatch):
    did = _seed()
    rows = db.load_lines(did)

    def fake_flag(lines, engine, **kw):
        # the user clears/sets flags by hand while the job runs
        db.save_lines(did, [Line(id=rows[0]["id"], idx=0, start=0, end=1, zh="你好",
                                 flag="manual", flag_note="mine")],
                      fields=("flag", "flag_note"))
        lines[0].flag, lines[0].flag_note = "uncertain", "job"
        lines[1].flag, lines[1].flag_note = "uncertain", "job"
    monkeypatch.setattr(translate_engines, "flag_uncertain_lines", fake_flag)
    job = _wait(svc.start_flag_review(did, engine_name="claude")["job_id"])
    out = db.load_lines(did)
    assert (out[0]["flag"], out[0]["flag_note"]) == ("manual", "mine")
    assert (out[1]["flag"], out[1]["flag_note"]) == ("uncertain", "job")
    assert job["result"]["flagged_count"] == 2


def test_flag_keeps_manual_flag_change_made_just_before_the_write(monkeypatch):
    """B-02 leftover: the user's edit lands after the job's last read but
    before its write; the write is conditional on the start value, so the
    user's flag survives."""
    did = _seed()
    rows = db.load_lines(did)

    def user_edit():
        db.update_line_fields_if(did, rows[0]["id"], {"flag": "manual", "flag_note": "mine"},
                                 {"flag": "", "flag_note": ""})

    for name in ("save_lines", "update_lines_fields_if_many"):
        real = getattr(db, name)

        def wrapped(*a, _real=real, **k):
            user_edit()
            return _real(*a, **k)
        monkeypatch.setattr(db, name, wrapped)

    def fake_flag(lines, engine, **kw):
        lines[0].flag, lines[0].flag_note = "uncertain", "job"
        lines[1].flag, lines[1].flag_note = "uncertain", "job"
    monkeypatch.setattr(translate_engines, "flag_uncertain_lines", fake_flag)
    job = _wait(svc.start_flag_review(did, engine_name="claude")["job_id"])
    out = db.load_lines(did)
    assert (out[0]["flag"], out[0]["flag_note"]) == ("manual", "mine")
    assert (out[1]["flag"], out[1]["flag_note"]) == ("uncertain", "job")
    assert job["result"]["flagged_count"] == 2


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


def test_fix_flagged_sends_default_locale_and_style_note(monkeypatch):
    from services import settings_service
    seen = []

    class Spy(FakeEngine):
        def translate_batch(self, texts, ctx=None):
            seen.append(ctx)
            return super().translate_batch(texts, ctx)
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: Spy())
    prefs = {"default_locale": "en-GB", "default_style_note": "Keep honorifics."}
    real = settings_service.get_preference
    monkeypatch.setattr(settings_service, "get_preference",
                        lambda name: prefs.get(name, real(name)))
    did = _seed((("你好", "old", "uncertain"),))
    _wait(svc.start_fix_flagged(did, engine_name="claude")["job_id"])
    assert seen and seen[0]["locale"] == "en-GB" and seen[0]["style_note"] == "Keep honorifics."


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
        svc.start_emotion_tagging(did, engine_name="nllb")
    with pytest.raises(UnsupportedOperationError):
        svc.start_flag_review(_seed((("你好", "", None),)), engine_name="claude")
    with pytest.raises(InvalidInputError):
        svc.start_fix_flagged(did, engine_name="claude", job_cost_cap_usd=-1)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
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
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
    r = client.post(f"/api/review-jobs/dramas/{did}/emotion", json={"engine": "claude"})
    assert r.status_code == 503


# ---- B-05: cancel between batches ------------------------------------------

def _cancel_then_check(job_id):
    def fake(lines, engine, **kw):
        background_jobs.request_cancel(job_id)
        kw["cancel_check"]()        # the next batch boundary raises
        raise AssertionError("cancel_check did not stop the run")
    return fake


def test_flag_cancel_skips_save(monkeypatch):
    did = _seed()
    calls = _spy_save_lines(monkeypatch)
    monkeypatch.setattr(translate_engines, "flag_uncertain_lines",
                        _cancel_then_check(f"flag_{did}"))
    job = _wait(svc.start_flag_review(did, engine_name="claude")["job_id"])
    assert job["status"] == "cancelled"
    assert calls == []


def test_consistency_cancel_skips_save(monkeypatch):
    did = _seed()
    saved = []
    monkeypatch.setattr(db, "save_consistency_issues", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(translate_engines, "check_consistency_llm",
                        _cancel_then_check(f"consistency_{did}"))
    job = _wait(svc.start_consistency_check(did, engine_name="claude")["job_id"])
    assert job["status"] == "cancelled" and saved == []


def test_notes_cancel_skips_save(monkeypatch):
    did = _seed()
    saved = []
    monkeypatch.setattr(db, "save_translation_notes", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(translation_guide, "generate_translation_notes_llm",
                        _cancel_then_check(f"notes_{did}"))
    job = _wait(svc.start_translation_notes(did, engine_name="claude")["job_id"])
    assert job["status"] == "cancelled" and saved == []


def test_emotion_cancel_stops_between_batches_and_skips_save(monkeypatch):
    """B-05 leftover: the emotion job checks cancel before every LLM batch."""
    did = _seed(rows=tuple((f"行{i}", "", None) for i in range(45)))  # 2 batches of 40
    calls, saved = [], []

    def fake_llm(engine, prompt, **kw):
        calls.append(prompt)
        background_jobs.request_cancel(f"emotion_{did}")
        return "[]"
    monkeypatch.setattr(emotion, "call_llm_json", fake_llm)
    monkeypatch.setattr(db, "save_emotions", lambda *a, **k: saved.append(a))
    job = _wait(svc.start_emotion_tagging(did, engine_name="claude")["job_id"])
    assert job["status"] == "cancelled"
    assert len(calls) == 1 and saved == []


def test_flag_count_reflects_hand_edits_on_lines_the_job_left_alone(monkeypatch):
    """A flag the user cleared (or set) by hand on a line the job didn't
    change is counted as it is saved, not as the job first read it."""
    did = _seed(rows=(("你好", "hello", "uncertain"), ("再见", "bye", None)))
    rows = db.load_lines(did)

    def fake_flag(lines, engine, **kw):
        db.update_line_fields_if(did, rows[0]["id"], {"flag": None, "flag_note": ""},
                                 {"flag": "uncertain", "flag_note": ""})
    monkeypatch.setattr(translate_engines, "flag_uncertain_lines", fake_flag)
    job = _wait(svc.start_flag_review(did, engine_name="claude")["job_id"])
    assert db.load_lines(did)[0]["flag"] is None
    assert job["result"]["flagged_count"] == 0
