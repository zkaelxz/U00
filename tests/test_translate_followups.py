"""Retry the failed lines of the last run, redo glossary misses with the
stronger engine, and say why no episode summary was written. Mocked engines."""
import json
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api.server import create_app
from api.api_config import ApiSettings
from core import Line
from engine_backends import glossary_check
from services import (episode_summary_service, failed_lines_service, glossary_flagged_service,
                      settings_service, translate_run_service)
from services.service_errors import ServiceError, UnsupportedOperationError
from tests import fake_engine

ERRORS = [{"batch_index": 0, "lines": [0, 2],
           "error": "2 of 3 lines got no translation (reply may have been cut off; try a smaller batch size)"}]


@pytest.fixture(autouse=True)
def _clean_jobs(monkeypatch):
    monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _wait(job_id):
    for _ in range(400):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _title(en=("older 0", "ok 1", "older 2", ""), errors=ERRORS, **drama):
    did = db.create_drama(title_zh="D", **drama)
    db.save_lines(did, [Line(idx=i, start=i * 5, end=i * 5 + 4, zh=f"句{i}", en=e)
                        for i, e in enumerate(en)])
    if errors is not None:
        db.update_drama(did, last_translate_errors=json.dumps(errors))
    return did


def _en(did):
    return [r["en"] for r in db.load_lines(did)]


# --- retry failed lines ---------------------------------------------------

def test_summary_separates_empty_lines_from_ones_holding_earlier_english(isolated_db):
    did = _title(en=("older 0", "ok 1", "", ""))
    assert failed_lines_service.summarize(did) == {
        "failed_untranslated_numbers": [3], "failed_kept_earlier_numbers": [1]}


def test_retry_translates_exactly_the_failed_lines_even_with_earlier_english(isolated_db):
    did = _title()
    out = failed_lines_service.start_retry(did, engine_name="fake")
    assert len(out["line_ids"]) == 2
    _wait(out["job_id"])
    assert _en(did) == ["[TEST] 句0", "ok 1", "[TEST] 句2", ""]


def test_a_cut_off_reply_retries_with_half_the_batch_size(isolated_db, monkeypatch):
    seen = []
    real = fake_engine.FakeEngine.translate_batch

    def capture(self, zh, context):
        seen.append(len(zh))
        return real(self, zh, context)
    monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", capture)
    did = _title(en=("a", "b", "c", "d"), errors=[{"batch_index": 0, "lines": [0, 1, 2, 3],
                                                    "error": "4 of 4 lines got no translation (reply may have been cut off)"}])
    _wait(failed_lines_service.start_retry(did, engine_name="fake", batch_size=4)["job_id"])
    assert seen == [2, 2]


def test_a_failure_that_is_not_a_cut_off_keeps_the_batch_size(isolated_db):
    did = _title(errors=[{"batch_index": 0, "lines": [0], "error": "timeout"}])
    assert failed_lines_service.retry_batch_size(db.get_drama(did), "fake", None, 8) == 8


def test_a_retried_line_leaves_the_notice_and_a_failed_one_stays(isolated_db, monkeypatch):
    real = fake_engine.FakeEngine.translate_batch

    def drop_first(self, zh, context):
        out = real(self, zh, context)
        return ["" if z == "句0" else o for z, o in zip(zh, out)]
    monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", drop_first)
    did = _title()
    _wait(failed_lines_service.start_retry(did, engine_name="fake")["job_id"])
    left = json.loads(db.get_drama(did)["last_translate_errors"])
    assert [e["lines"] for e in left] == [[0]]


def test_all_lines_retried_clears_the_record(isolated_db):
    did = _title()
    _wait(failed_lines_service.start_retry(did, engine_name="fake")["job_id"])
    assert db.get_drama(did)["last_translate_errors"] is None


def test_nothing_to_retry_is_refused(isolated_db):
    did = _title(errors=None)
    with pytest.raises(UnsupportedOperationError):
        failed_lines_service.start_retry(did, engine_name="fake")


def test_api_retry_route_and_config_fields(client):
    did = _title(en=("older 0", "ok 1", "", ""))
    cfg = client.get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["failed_untranslated_numbers"] == [3] and cfg["failed_kept_earlier_numbers"] == [1]
    r = client.post(f"/api/translate-run/dramas/{did}/retry-failed/run", json={"engine": "fake"})
    assert r.status_code == 200 and len(r.json()["line_ids"]) == 2
    _wait(r.json()["job_id"])
    assert client.post(f"/api/translate-run/dramas/{did}/retry-failed/run",
                       json={"engine": "fake"}).status_code == 400


# --- redo flagged glossary lines -----------------------------------------

def _flag(did, idxs):
    lines = [core_line for core_line in db.load_line_objects(did)]
    for ln in lines:
        if ln.idx in idxs:
            ln.flag, ln.flag_note = glossary_check.GLOSSARY_FLAG, "Glossary: x missing"
    db.save_lines(did, lines, fields=("flag", "flag_note"))


def test_redo_uses_the_stronger_engine_on_flagged_lines_only(isolated_db, monkeypatch):
    did = _title(en=("Buki", "fine", "Buki", ""), errors=None)
    _flag(did, {0, 2})
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get, {}))
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: "fake")
    assert glossary_flagged_service.config_fields(did) == {
        "glossary_flagged_count": 2, "glossary_redo_engine": "fake"}
    out = glossary_flagged_service.start_redo(did, "fake")
    assert len(out["line_ids"]) == 2
    _wait(out["job_id"])
    assert _en(did) == ["[TEST] 句0", "fine", "[TEST] 句2", ""]


def _with(real, overrides):
    return lambda key, *a, **k: overrides[key] if key in overrides else real(key, *a, **k)


def test_redo_refuses_without_a_stronger_engine_or_after_it_changed(isolated_db, monkeypatch):
    did = _title(en=("Buki", "", "", ""), errors=None)
    _flag(did, {0})
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: None)
    with pytest.raises(UnsupportedOperationError):
        glossary_flagged_service.start_redo(did, "fake")
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: "fake")
    with pytest.raises(UnsupportedOperationError):
        glossary_flagged_service.start_redo(did, "claude")


def test_redo_with_no_flagged_lines_is_refused(isolated_db, monkeypatch):
    did = _title(errors=None)
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: "fake")
    with pytest.raises(UnsupportedOperationError):
        glossary_flagged_service.start_redo(did, "fake")


# --- episode summary ------------------------------------------------------

def test_a_run_with_no_summary_engine_says_why(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_run_service, "pick_summary_engine", lambda *a, **k: (None, None))
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get,
                                                       {"episode_summary_engine": "claude"}))
    real_key = translate_run_service.translate_service.resolve_api_key
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key",
                        lambda n: None if n == "claude" else real_key(n))
    did = _title(en=("", "", "", ""), errors=None)
    job = _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert "no claude key" in job["result"]["summary_skipped"]
    assert "choose an engine in Settings" in job["result"]["summary_skipped"]


def test_ollama_down_is_named_when_the_summary_comes_back_empty(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get,
                                                       {"episode_summary_engine": "ollama"}))
    did = _title(en=("", "", "", ""), errors=None)
    job = _wait(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert job["result"]["summary_skipped"] == (
        "Summary skipped: Ollama not reachable; choose an engine in Settings.")


def test_a_free_only_caller_is_told_the_summary_engine_is_paid(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get,
                                                       {"episode_summary_engine": "claude"}))
    assert "paid engine" in episode_summary_service.no_engine_reason(allow_paid=False)


def test_no_reason_when_the_summary_exists(isolated_db):
    did = _title(errors=None)
    db.update_drama(did, episode_summary="They met.")
    assert episode_summary_service.missing_after_run_reason(did, "ollama", False) == ""


def test_generate_writes_nothing_and_logs_usage(isolated_db, monkeypatch):
    from engine_backends import llm_tasks
    did = _title(errors=None)
    calls = []

    def fake_generate(lines, engine, usage_cb=None):
        calls.append(len(lines))
        return "They met at the lake."
    monkeypatch.setattr(llm_tasks, "generate_episode_summary", fake_generate)
    out = episode_summary_service.generate(did, "fake")
    assert out["summary"] == "They met at the lake." and calls == [4]
    assert not (db.get_drama(did).get("episode_summary") or "")


def test_generate_with_nothing_translated_or_an_empty_reply_says_so(isolated_db, monkeypatch):
    did = _title(en=("", "", "", ""), errors=None)
    with pytest.raises(UnsupportedOperationError):
        episode_summary_service.generate(did, "fake")
    full = _title(errors=None)
    with pytest.raises(ServiceError, match="returned no summary"):
        episode_summary_service.generate(full, "fake")


def test_api_summary_options_and_generate(client, monkeypatch):
    from engine_backends import llm_tasks
    monkeypatch.setattr(llm_tasks, "generate_episode_summary", lambda lines, engine, usage_cb=None: "S.")
    did = _title(errors=None)
    opts = client.get(f"/api/translate-run/dramas/{did}/episode-summary").json()
    assert "fake" in opts["engines"] and opts["engine"]
    r = client.post(f"/api/translate-run/dramas/{did}/episode-summary/generate", json={"engine": "fake"})
    assert r.status_code == 200 and r.json()["summary"] == "S."
