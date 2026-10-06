"""Tests for start_translate_run + POST /api/translate-run/dramas/{id}/run
(Migration Slice 40). Fully mocked: the offline test engine, no network."""
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api.server import create_app
from core import Line
from services import settings_service, translate_run_service as svc
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)


def _seed(texts):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                        for i, (z, en) in enumerate(texts)])
    return did


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def test_run_translates_only_empty_lines_and_keeps_manual_edits(isolated_db):
    did = _seed([("你好", ""), ("再见", "My hand edit"), ("谢谢", "")])
    out = svc.start_translate_run(did, engine_name="fake")
    assert out["job_id"] == f"translate_{did}" and out["target_line_count"] == 2
    assert _wait(out["job_id"])["status"] == "done"
    ens = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert ens["再见"] == "My hand edit"
    assert ens["你好"] and ens["谢谢"]


def test_line_ids_restrict_targets_and_write_is_by_id(isolated_db):
    did = _seed([("一", ""), ("二", ""), ("三", "")])
    rows = db.load_lines(did)
    out = svc.start_translate_run(did, engine_name="fake", line_ids=[rows[1]["id"]])
    assert out["target_line_count"] == 1
    _wait(out["job_id"])
    after = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert after["二"] and not after["一"] and not after["三"]


def test_write_is_field_scoped_en_only(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    calls = []
    real = db.save_lines

    def spy(d, ls, fields=None):
        calls.append(fields)
        return real(d, ls, fields=fields)
    monkeypatch.setattr(db, "save_lines", spy)
    _wait(svc.start_translate_run(did, engine_name="fake")["job_id"])
    # Translation writes `en`; finish_translation_run's density pass writes
    # only flag fields. Never a full sync (fields=None).
    assert calls and ("en",) in calls
    assert all(f in (("en",), ("flag", "flag_note")) for f in calls)


def test_force_retranslate_overwrites(isolated_db):
    did = _seed([("你好", "old")])
    out = svc.start_translate_run(did, engine_name="fake", force_retranslate=True)
    _wait(out["job_id"])
    assert db.load_lines(did)[0]["en"] != "old"


def test_errors(isolated_db, monkeypatch):
    with pytest.raises(NotFoundError):
        svc.start_translate_run(999)
    did = _seed([("你好", "")])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="nope")
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="fake", line_ids=[123456])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, engine_name="fake", locale="fr")
    done = _seed([("你好", "hi")])
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(done, engine_name="fake")
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
    with pytest.raises(DependencyUnavailableError) as exc:
        svc.start_translate_run(did, engine_name="claude")
    assert exc.value.args[0] == "No claude key is configured. Set one in Settings first."


def test_duplicate_start_conflicts(isolated_db):
    did = _seed([("你好", "")])
    background_jobs.start_job(f"translate_{did}", lambda: time.sleep(0.5))
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, engine_name="fake")


def test_secret_not_in_job_error(isolated_db, monkeypatch):
    did = _seed([("你好", "")])
    real = settings_service.resolve_key
    monkeypatch.setattr(
        settings_service, "resolve_key",
        lambda k, *a, **kw: "sk-ant-SECRETSECRETSECRET1234" if k == "claude" else real(k, *a, **kw))

    class Boom:
        name, model = "claude", "m"

        def translate_batch(self, *a, **k):
            raise RuntimeError("bad key sk-ant-SECRETSECRETSECRET1234")
    monkeypatch.setitem(translate_engines.ENGINES, "claude", lambda *a, **k: Boom())
    out = svc.start_translate_run(did, engine_name="claude")
    job = _wait(out["job_id"])
    assert "SECRETSECRET" not in repr(job)
    assert "SECRETSECRET" not in str(db.get_drama(did).get("last_translate_errors"))


def test_api_endpoint_status_codes(isolated_db):
    client = TestClient(create_app())
    assert client.post("/api/translate-run/dramas/999/run", json={}).status_code == 404
    did = _seed([("你好", "")])
    assert client.post(f"/api/translate-run/dramas/{did}/run",
                       json={"engine": "fake", "bogus": 1}).status_code == 422
    r = client.post(f"/api/translate-run/dramas/{did}/run", json={"engine": "fake"})
    assert r.status_code == 200 and r.json()["target_line_count"] == 1
    _wait(r.json()["job_id"])
    assert db.load_lines(did)[0]["en"]


def _spy_guidelines(monkeypatch):
    seen = []
    real = svc.translation_guide.build_style_guidelines

    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(svc.translation_guide, "build_style_guidelines", spy)
    return seen


def test_prompt_toggles_default_to_the_tab_widget_defaults(isolated_db, monkeypatch):
    seen = _spy_guidelines(monkeypatch)
    did = _seed([("你好", "")])
    _wait(svc.start_translate_run(did, engine_name="fake")["job_id"])
    assert seen[-1]["include_genre_notes"] is True
    assert seen[-1]["default_female_pronouns"] is False


def test_prompt_toggles_override_reach_the_guidelines(isolated_db, monkeypatch):
    seen = _spy_guidelines(monkeypatch)
    did = _seed([("你好", "")])
    _wait(svc.start_translate_run(did, engine_name="fake",
                                  default_female_pronouns=True,
                                  include_genre_notes=False)["job_id"])
    assert seen[-1]["include_genre_notes"] is False
    assert seen[-1]["default_female_pronouns"] is True


def test_api_passes_prompt_toggles_and_validates_them(isolated_db, monkeypatch):
    seen = _spy_guidelines(monkeypatch)
    client = TestClient(create_app())
    did = _seed([("你好", "")])
    assert client.post(f"/api/translate-run/dramas/{did}/run",
                       json={"engine": "fake",
                             "include_genre_notes": "maybe"}).status_code == 422
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"engine": "fake", "default_female_pronouns": True,
                          "include_genre_notes": False})
    assert r.status_code == 200, r.text
    _wait(r.json()["job_id"])
    assert seen[-1]["default_female_pronouns"] is True
    assert seen[-1]["include_genre_notes"] is False
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"engine": "fake", "force_retranslate": True})
    assert r.status_code == 200, r.text
    _wait(r.json()["job_id"])
    assert seen[-1]["default_female_pronouns"] is False
    assert seen[-1]["include_genre_notes"] is True


def test_batch_size_over_the_cap_is_refused_with_a_clear_message(isolated_db):
    did = _seed([("你好", "")])
    with pytest.raises(InvalidInputError, match="can't be more than 60"):
        svc.start_translate_run(did, engine_name="fake", batch_size=200)
    assert svc.start_translate_run(did, engine_name="fake", batch_size=60)["job_id"]


def test_empty_engine_reply_finishes_with_errors(isolated_db, monkeypatch):
    class Empty:
        model = "fake-model"

        def translate_batch(self, zh_lines, context):
            return [""] * len(zh_lines)

    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: Empty())
    did = _seed([("你好", ""), ("再见", "")])
    job = _wait(svc.start_translate_run(did, engine_name="fake")["job_id"])
    assert job["status"] == "done"
    assert len(job["result"]["errors"]) == 1
    assert [r["en"] or "" for r in db.load_lines(did)] == ["", ""]
    assert db.get_drama(did)["last_translate_errors"]
