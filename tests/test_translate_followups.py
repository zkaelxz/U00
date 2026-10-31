"""Retry the failed lines of the last run, redo glossary misses with the
stronger engine, and say why no episode summary was written. Mocked engines."""
import json

import pytest

pytest.importorskip("fastapi")

import db
import translate_engines
from core import Line
from engine_backends import glossary_check
from services import (episode_summary_service, failed_lines_service, glossary_flagged_service,
                      settings_service, translate_run_service)
from lib.errors import ServiceError, UnsupportedOperationError
from tests import fake_engine
from tests.job_helpers import wait_for_job

ERRORS = [{"batch_index": 0, "lines": [0, 2],
           "error": "2 of 3 lines got no translation (reply may have been cut off; try a smaller batch size)"}]


@pytest.fixture(autouse=True)
def _clean_jobs(monkeypatch):
    monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)


def _title(en=("older 0", "ok 1", "older 2", ""), errors=ERRORS, legacy=False, **drama):
    did = db.create_drama(title_zh="D", **drama)
    db.save_lines(did, [Line(idx=i, start=i * 5, end=i * 5 + 4, zh=f"句{i}", en=e)
                        for i, e in enumerate(en)])
    if errors is not None:
        by_idx = {r["idx"]: r["id"] for r in db.load_lines(did)}
        # The pipeline records ids beside indexes; `legacy` is a record from before that.
        errors = errors if legacy else [{**e, "line_ids": [by_idx[i] for i in e["lines"]]} for e in errors]
        db.update_drama(did, last_translate_errors=json.dumps(errors))
    return did


def _en(did):
    return [r["en"] for r in db.load_lines(did)]


# --- retry failed lines ---------------------------------------------------

def test_summary_separates_empty_lines_from_ones_holding_earlier_english(isolated_db):
    did = _title(en=("older 0", "ok 1", "", ""))
    assert failed_lines_service.summarize(did) == {
        "failed_untranslated_numbers": [3], "failed_kept_earlier_numbers": [1], "failed_retryable": True}


def test_retry_translates_exactly_the_failed_lines_even_with_earlier_english(isolated_db):
    did = _title()
    out = failed_lines_service.start_retry(did, engine_name="fake")
    assert len(out["line_ids"]) == 2
    wait_for_job(out["job_id"], timeout=20.0)
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
    wait_for_job(failed_lines_service.start_retry(did, engine_name="fake", batch_size=4)["job_id"], timeout=20.0)
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
    wait_for_job(failed_lines_service.start_retry(did, engine_name="fake")["job_id"], timeout=20.0)
    left = json.loads(db.get_drama(did)["last_translate_errors"])
    assert [e["lines"] for e in left] == [[0]]


def test_all_lines_retried_clears_the_record(isolated_db):
    did = _title()
    wait_for_job(failed_lines_service.start_retry(did, engine_name="fake")["job_id"], timeout=20.0)
    assert db.get_drama(did)["last_translate_errors"] is None


def test_nothing_to_retry_is_refused(isolated_db):
    did = _title(errors=None)
    with pytest.raises(UnsupportedOperationError):
        failed_lines_service.start_retry(did, engine_name="fake")


def test_api_retry_route_and_config_fields(local_client):
    did = _title(en=("older 0", "ok 1", "", ""))
    cfg = local_client.get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["failed_untranslated_numbers"] == [3] and cfg["failed_kept_earlier_numbers"] == [1]
    r = local_client.post(f"/api/translate-run/dramas/{did}/retry-failed/run", json={"engine": "fake"})
    assert r.status_code == 200 and len(r.json()["line_ids"]) == 2
    wait_for_job(r.json()["job_id"], timeout=20.0)
    assert local_client.post(f"/api/translate-run/dramas/{did}/retry-failed/run",
                       json={"engine": "fake"}).status_code == 400


# --- redo flagged glossary lines -----------------------------------------

def _flag(did, idxs):
    lines = [core_line for core_line in db.load_line_objects(did)]
    for ln in lines:
        if ln.idx in idxs:
            ln.flag, ln.flag_note = glossary_check.GLOSSARY_FLAG, "Glossary: x missing"
    db.save_lines(did, lines, fields=("flag", "flag_note"))


def test_redo_uses_the_stronger_engine_on_flagged_lines_only(isolated_db, monkeypatch):
    sid = db.get_or_create_series("S")
    db.upsert_glossary_term(sid, "句0", "Zero Name", aliases=None, banned_translations=None)
    db.upsert_glossary_term(sid, "句2", "Two Name", aliases=None, banned_translations=None)
    did = _title(en=("Buki", "fine", "Buki", ""), errors=None)
    db.update_drama(did, series_id=sid)
    _flag(did, {0, 2})
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get, {}))
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: "fake")
    assert glossary_flagged_service.config_fields(did) == {
        "glossary_flagged_count": 2, "glossary_redo_engine": "fake"}
    out = glossary_flagged_service.start_redo(did, "fake")
    assert len(out["line_ids"]) == 2
    wait_for_job(out["job_id"], timeout=20.0)
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
    job = wait_for_job(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"], timeout=20.0)
    assert "no claude key" in job["result"]["summary_skipped"]
    assert "choose an engine in Settings" in job["result"]["summary_skipped"]


def test_ollama_down_is_named_when_the_summary_comes_back_empty(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get,
                                                       {"episode_summary_engine": "ollama"}))
    did = _title(en=("", "", "", ""), errors=None)
    job = wait_for_job(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"], timeout=20.0)
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


def test_api_summary_options_and_generate(local_client, monkeypatch):
    from engine_backends import llm_tasks
    monkeypatch.setattr(llm_tasks, "generate_episode_summary", lambda lines, engine, usage_cb=None: "S.")
    did = _title(errors=None)
    opts = local_client.get(f"/api/translate-run/dramas/{did}/episode-summary").json()
    assert "fake" in opts["engines"] and opts["engine"]
    r = local_client.post(f"/api/translate-run/dramas/{did}/episode-summary/generate", json={"engine": "fake"})
    assert r.status_code == 200 and r.json()["summary"] == "S."


# --- review fixes ----------------------------------------------------------

def test_a_failed_batch_records_line_ids_next_to_indexes(isolated_db):
    from engine_backends import translate_pipeline as tp  # noqa: F401
    did = _title(en=("", "", "", ""), errors=None)

    class Broken(fake_engine.FakeEngine):
        def translate_batch(self, zh, context):
            raise ValueError("boom")
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"z{i}", id=50 + i) for i in range(2)]
    _, errors = translate_engines.translate_lines_with_engine(
        lines, Broken(), drama_meta={"source_language": "zh"}, context_window=0, context_window_ahead=0)
    assert errors[0]["lines"] == [0, 1] and errors[0]["line_ids"] == [50, 51]


def _ids(did):
    return [r["id"] for r in db.load_lines(did)]


def test_retry_after_a_merge_touches_only_the_recorded_ids(isolated_db):
    from services import restructure_service
    did = _title(en=("a", "b", "c", "d"), errors=None)
    ids = _ids(did)
    # line 3 (id ids[3]) failed while it was at index 3
    db.update_drama(did, last_translate_errors=json.dumps(
        [{"batch_index": 0, "lines": [3], "line_ids": [ids[3]], "error": "got no translation"}]))
    restructure_service.merge_lines(did, [ids[0], ids[1]], expected_line_ids=ids)
    now = {r["id"]: r["idx"] for r in db.load_lines(did)}
    assert now[ids[3]] == 2          # the index moved
    assert failed_lines_service.summarize(did)["failed_kept_earlier_numbers"] == [3]
    out = failed_lines_service.start_retry(did, engine_name="fake")
    assert out["line_ids"] == [ids[3]]
    wait_for_job(out["job_id"], timeout=20.0)
    by_id = {r["id"]: r["en"] for r in db.load_lines(did)}
    assert by_id[ids[3]] == "[TEST] 句3"
    assert by_id[ids[2]] == "c"


def test_an_old_record_without_ids_shows_the_notice_but_cannot_retry(isolated_db, local_client):
    did = _title(legacy=True)
    assert failed_lines_service.summarize(did)["failed_retryable"] is False
    with pytest.raises(UnsupportedOperationError, match="older"):
        failed_lines_service.start_retry(did, engine_name="fake")
    assert local_client.get(f"/api/translate-run/dramas/{did}/config").json()["failed_retryable"] is False


def test_prune_matches_by_id_and_keeps_entries_without_ids(isolated_db):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    ids = _ids(did)
    db.update_drama(did, last_translate_errors=json.dumps([
        {"batch_index": 0, "lines": [0, 1], "line_ids": [ids[0], ids[1]], "error": "x"},
        {"batch_index": 1, "lines": [2], "error": "legacy"}]))
    lines = [Line(idx=0, start=0, end=1, zh="z", en="new", id=ids[0]),
             Line(idx=1, start=1, end=2, zh="z", en="b", id=ids[1])]
    failed_lines_service.prune_resolved(did, lines, {ids[0], ids[1]}, {ids[0]: "a", ids[1]: "b"}, [])
    left = json.loads(db.get_drama(did)["last_translate_errors"])
    assert left[0]["line_ids"] == [ids[1]] and left[0]["lines"] == [1] and left[1]["lines"] == [2]


def test_redo_skips_lines_whose_english_was_fixed_by_hand(isolated_db, monkeypatch):
    sid = db.get_or_create_series("S")
    db.upsert_glossary_term(sid, "句0", "Zero Name", aliases=None, banned_translations=None)
    db.upsert_glossary_term(sid, "句2", "Two Name", aliases=None, banned_translations=None)
    did = _title(en=("Zero Name here", "b", "wrong", ""), errors=None)
    db.update_drama(did, series_id=sid)
    _flag(did, {0, 2})
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: "fake")
    assert glossary_flagged_service.config_fields(did)["glossary_flagged_count"] == 1
    out = glossary_flagged_service.start_redo(did, "fake")
    assert len(out["line_ids"]) == 1
    wait_for_job(out["job_id"], timeout=20.0)
    assert _en(did)[0] == "Zero Name here"


def test_a_bad_summary_engine_setting_gives_a_reason_not_a_failure(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "get", _with(settings_service.get,
                                                       {"episode_summary_engine": "no-such-engine"}))
    assert "Summary skipped" in episode_summary_service.no_engine_reason(allow_paid=True)
    monkeypatch.setattr(episode_summary_service.translate_service, "resolve_api_key",
                        lambda n: (_ for _ in ()).throw(RuntimeError("x")))
    assert "Summary skipped" in episode_summary_service.no_engine_reason(allow_paid=True)


def test_prune_and_dismiss_share_one_lock(isolated_db):
    assert failed_lines_service.translate_run_service.ERRORS_LOCK is translate_run_service.ERRORS_LOCK
