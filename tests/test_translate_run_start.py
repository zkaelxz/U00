"""Tests for start_translate_run + POST /api/translate-run/dramas/{id}/run
(Migration Slice 40). Fully mocked: the offline test engine, no network."""
import time

import pytest
from fastapi.testclient import TestClient

from jobs import job_launch, job_runner, job_table
import db
from engine_backends import engine_registry
from api.server import create_app
from core import Line
from services import settings_service, translate_run_service as svc
from lib.errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)
from tests.job_helpers import wait_for_job


def _seed(texts):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                        for i, (z, en) in enumerate(texts)])
    return did



def test_run_translates_only_empty_lines_and_keeps_manual_edits(isolated_db):
    did = _seed([("你好", ""), ("再见", "My hand edit"), ("谢谢", "")])
    out = svc.start_translate_run(did, engine_name="fake")
    assert out["job_id"] == f"translate_{did}" and out["target_line_count"] == 2
    assert wait_for_job(out["job_id"])["status"] == "done"
    ens = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert ens["再见"] == "My hand edit"
    assert ens["你好"] and ens["谢谢"]


def test_english_typed_while_the_run_works_is_kept(isolated_db, monkeypatch):
    from tests.fake_engine import FakeEngine
    did = _seed([("你好", ""), ("谢谢", "")])
    real = FakeEngine.translate_batch

    def user_types_then_translate(self, zh_lines, context):
        [ln] = [ln for ln in db.load_line_objects(did) if ln.zh == "谢谢"]
        if not ln.en:
            ln.en = "Typed while it ran"
            db.save_lines(did, [ln], fields=("en",))
        return real(self, zh_lines, context)
    monkeypatch.setattr(FakeEngine, "translate_batch", user_types_then_translate)
    job = wait_for_job(svc.start_translate_run(did, engine_name="fake")["job_id"])
    assert job["status"] == "done"
    rows = {r["zh"]: r for r in db.load_lines(did)}
    assert rows["谢谢"]["en"] == "Typed while it ran"
    assert rows["你好"]["en"].startswith("[TEST]")
    assert job["result"]["kept_your_edit"] == [rows["谢谢"]["id"]]
    # The version the run saved holds the kept edit, so restoring it later
    # doesn't bring the machine English back over it.
    [version] = db.list_translation_versions(did)
    saved = {ln["zh"]: ln["en"] for ln in db.get_translation_version(version["id"])["lines"]}
    assert saved["谢谢"] == "Typed while it ran"


def test_refused_force_start_takes_no_undo_point(isolated_db, monkeypatch):
    did = _seed([("你好", "old")])
    monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: False)
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, engine_name="fake", force_retranslate=True)
    assert db.list_line_history(did) == []


def test_force_retranslate_keeps_an_undo_point_of_the_old_english(isolated_db):
    did = _seed([("你好", "old")])
    wait_for_job(svc.start_translate_run(did, engine_name="fake", force_retranslate=True)["job_id"])
    [snap] = db.list_line_history(did)
    assert snap["label"] == "before force re-translate"
    assert [ln["en"] for ln in db.get_line_history_snapshot(snap["id"])] == ["old"]


def test_line_ids_restrict_targets_and_write_is_by_id(isolated_db):
    did = _seed([("一", ""), ("二", ""), ("三", "")])
    rows = db.load_lines(did)
    out = svc.start_translate_run(did, engine_name="fake", line_ids=[rows[1]["id"]])
    assert out["target_line_count"] == 1
    wait_for_job(out["job_id"])
    after = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert after["二"] and not after["一"] and not after["三"]


def test_write_is_field_scoped_en_only(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    calls = []
    real = db.save_lines

    def spy(d, ls, fields=None, **kw):
        calls.append(fields)
        return real(d, ls, fields=fields, **kw)
    monkeypatch.setattr(db, "save_lines", spy)
    wait_for_job(svc.start_translate_run(did, engine_name="fake")["job_id"])
    # Translation writes `en`; finish_translation_run's density pass writes
    # only flag fields. Never a full sync (fields=None).
    assert calls and ("en",) in calls
    assert all(f in (("en",), ("flag", "flag_note")) for f in calls)


def test_force_retranslate_overwrites(isolated_db):
    did = _seed([("你好", "old")])
    out = svc.start_translate_run(did, engine_name="fake", force_retranslate=True)
    wait_for_job(out["job_id"])
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
    assert exc.value.args[0] == "No claude key is configured. Add it in Settings."


def test_duplicate_start_conflicts(isolated_db):
    did = _seed([("你好", "")])
    job_launch.start_job(f"translate_{did}", lambda: time.sleep(0.5))
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
    monkeypatch.setitem(engine_registry.ENGINES, "claude", lambda *a, **k: Boom())
    out = svc.start_translate_run(did, engine_name="claude")
    job = wait_for_job(out["job_id"])
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
    wait_for_job(r.json()["job_id"])
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
    wait_for_job(svc.start_translate_run(did, engine_name="fake")["job_id"])
    assert seen[-1]["include_genre_notes"] is True
    assert seen[-1]["default_female_pronouns"] is False


def test_prompt_toggles_override_reach_the_guidelines(isolated_db, monkeypatch):
    seen = _spy_guidelines(monkeypatch)
    did = _seed([("你好", "")])
    wait_for_job(svc.start_translate_run(did, engine_name="fake",
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
    wait_for_job(r.json()["job_id"])
    assert seen[-1]["default_female_pronouns"] is True
    assert seen[-1]["include_genre_notes"] is False
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"engine": "fake", "force_retranslate": True})
    assert r.status_code == 200, r.text
    wait_for_job(r.json()["job_id"])
    # Omitted toggles reuse what the previous run saved for the title.
    assert seen[-1]["default_female_pronouns"] is True
    assert seen[-1]["include_genre_notes"] is False
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"engine": "fake", "force_retranslate": True,
                          "default_female_pronouns": False, "include_genre_notes": True})
    assert r.status_code == 200, r.text
    wait_for_job(r.json()["job_id"])
    assert seen[-1]["default_female_pronouns"] is False
    assert seen[-1]["include_genre_notes"] is True


def test_batch_size_over_the_cap_is_refused_with_a_clear_message(isolated_db):
    did = _seed([("你好", "")])
    with pytest.raises(InvalidInputError, match="can't be more than 60"):
        svc.start_translate_run(did, engine_name="fake", batch_size=200)
    job_id = svc.start_translate_run(did, engine_name="fake", batch_size=60)["job_id"]
    # The job thread must not outlive isolated_db's library folder.
    assert job_runner.wait_for_job_threads(5, [job_id])


def test_empty_engine_reply_finishes_with_errors(isolated_db, monkeypatch):
    class Empty:
        model = "fake-model"

        def translate_batch(self, zh_lines, context):
            return [""] * len(zh_lines)

    monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: Empty())
    did = _seed([("你好", ""), ("再见", "")])
    job = wait_for_job(svc.start_translate_run(did, engine_name="fake")["job_id"])
    assert job["status"] == "done"
    assert len(job["result"]["errors"]) == 1
    assert [r["en"] or "" for r in db.load_lines(did)] == ["", ""]
    assert db.get_drama(did)["last_translate_errors"]


def _settings(job_id):
    import json
    return json.loads(db.get_job_record(job_id)["result_json"])["run_settings"]


def test_run_settings_name_the_engine_and_show_the_resolved_toggle_defaults(isolated_db):
    did = _seed([("你好", "")])
    out = svc.start_translate_run(did, engine_name="fake")
    wait_for_job(out["job_id"])
    settings = _settings(out["job_id"])
    assert settings["engine"] == "fake"
    assert settings["pronoun_hint"] is False and settings["genre_notes"] is True
    assert settings["bulk"] is False and settings["reflect"] is False


def test_a_refused_second_start_does_not_change_the_live_jobs_settings(isolated_db):
    did = _seed([("你好", ""), ("再见", "")])
    job_id = f"translate_{did}"
    job_table._jobs[job_id] = {
        "status": "running", "progress": 0.0, "message": "", "error": None,
        "cancel_requested": False, "result": None, "run_settings": {"batch_size": 3}}
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, engine_name="fake", batch_size=7)
    assert job_table._jobs[job_id]["run_settings"] == {"batch_size": 3}


def test_a_new_run_shows_its_own_settings_not_the_previous_runs(isolated_db):
    did = _seed([("你好", ""), ("再见", "")])
    first = svc.start_translate_run(did, engine_name="fake", batch_size=7)
    wait_for_job(first["job_id"])
    assert _settings(first["job_id"])["batch_size"] == 7
    second = svc.start_translate_run(did, engine_name="fake", force_retranslate=True,
                                     batch_size=9)
    wait_for_job(second["job_id"])
    assert _settings(second["job_id"])["batch_size"] == 9


def test_live_run_is_refused_over_lines_a_bulk_batch_is_translating(isolated_db):
    did = _seed([("你好", ""), ("谢谢", "")])
    first, second = [r["id"] for r in db.load_lines(did)]
    db.create_bulk_job(did, "claude", "m", "submitted", [(first, "k", "h", "")])
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, engine_name="fake")
    # Lines the batch does not cover are free to translate live.
    out = svc.start_translate_run(did, engine_name="fake", line_ids=[second])
    assert wait_for_job(out["job_id"])["status"] == "done"


def test_live_run_is_allowed_beside_a_finished_or_non_translate_bulk_job(isolated_db):
    did = _seed([("你好", "")])
    [lid] = [r["id"] for r in db.load_lines(did)]
    db.create_bulk_job(did, "claude", "m", "applied", [(lid, "k", "h", "")])
    db.create_bulk_job(did, "claude", "m", "submitted", [(lid, "k2", "h", "")], kind="flag")
    assert wait_for_job(svc.start_translate_run(did, engine_name="fake")["job_id"])["status"] == "done"


def test_live_run_is_refused_beside_a_pending_reflect_batch(isolated_db):
    did = _seed([("你好", "")])
    [lid] = [r["id"] for r in db.load_lines(did)]
    db.create_bulk_job(did, "claude", "m", "submitted", [(lid, "k", "h", "")], kind="reflect")
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, engine_name="fake")
