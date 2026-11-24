"""Retry the failed lines of the last run, redo glossary misses with the
stronger engine, and say why no episode summary was written. Mocked engines."""
import json

import pytest

pytest.importorskip("fastapi")

import db
from engine_backends import local
from engine_backends import translate_pipeline as engine_translate_pipeline
from core import Line
from engine_backends import glossary_check
from services import (episode_summary_service, failed_lines_service, glossary_flagged_service,
                      line_provenance_service, lines_service, profile_service, settings_service,
                      translate_run_service)
from lib.errors import InvalidInputError, ServiceError, UnsupportedOperationError
from tests import fake_engine
from tests.job_helpers import wait_for_job

ERRORS = [{"batch_index": 0, "lines": [0, 2],
           "error": "2 of 3 lines got no translation (reply may have been cut off; try a smaller batch size)"}]


@pytest.fixture(autouse=True)
def _clean_jobs(monkeypatch):
    monkeypatch.setattr(local, "check_ollama_reachable", lambda url: False)


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


def test_redo_with_line_ids_redoes_exactly_those_lines(isolated_db, monkeypatch):
    did = _title(en=("Buki", "fine", "Buki", ""), errors=None)
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: "fake")
    ids = [ln.id for ln in db.load_line_objects(did)]
    out = glossary_flagged_service.start_redo(did, "fake", line_ids=[ids[1]])
    assert out["line_ids"] == [ids[1]]
    wait_for_job(out["job_id"], timeout=20.0)
    assert _en(did) == ["Buki", "[TEST] 句1", "Buki", ""]
    with pytest.raises(InvalidInputError):
        glossary_flagged_service.start_redo(did, "fake", line_ids=[ids[3]])  # untranslated
    with pytest.raises(InvalidInputError):
        glossary_flagged_service.start_redo(did, "fake", line_ids=[10**6])


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


# --- redo doubtful lines (source mismatch, AI-uncertain) -------------------

def _flag_as(did, flags):
    """flags: {idx: flag key}. A translate run sets these flags, and it records the
    English it wrote, so the flagged lines get that record too."""
    lines = db.load_line_objects(did)
    for ln in lines:
        if ln.idx in flags:
            ln.flag, ln.flag_note = flags[ln.idx], "note"
    db.save_lines(did, lines, fields=("flag", "flag_note"))
    line_provenance_service.record(did, {ln.id: (ln.zh, ln.en) for ln in lines if ln.idx in flags},
                                   "claude", "m", "1", "")


def _flags(did):
    return [r["flag"] or "" for r in db.load_lines(did)]


def _stronger(monkeypatch, engine="fake"):
    from services import stronger_engine_service as sts
    monkeypatch.setattr(sts, "stronger_engine_or_none", lambda drama_id: engine)


def test_doubtful_count_takes_mismatch_and_ai_uncertain_flags_only(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch", 1: "name_uncertain", 2: "glossary_missing", 3: "timing_drift"})
    _stronger(monkeypatch)
    fields = glossary_flagged_service.doubtful_config_fields(did)
    assert fields["doubtful_flagged_count"] == 2
    assert glossary_flagged_service.config_fields(did)["glossary_flagged_count"] == 0


def test_doubtful_redo_rewrites_those_lines_and_clears_their_old_flags(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch", 1: "uncertain_translation", 2: "timing_drift"})
    _stronger(monkeypatch)
    out = glossary_flagged_service.start_redo(did, "fake", kind="doubtful")
    assert len(out["line_ids"]) == 2
    wait_for_job(out["job_id"], timeout=20.0)
    assert _en(did) == ["[TEST] 句0", "[TEST] 句1", "c", "d"]
    # The flags described the English that was replaced; an unrelated flag stays.
    assert _flags(did) == ["", "", "timing_drift", ""]
    assert glossary_flagged_service.doubtful_config_fields(did)["doubtful_flagged_count"] == 0


def test_doubtful_redo_keeps_the_flag_when_the_english_comes_back_unchanged(isolated_db, monkeypatch):
    did = _title(en=("[TEST] 句0", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "slang_idiom", 1: "slang_idiom"})
    _stronger(monkeypatch)
    out = glossary_flagged_service.start_redo(did, "fake", kind="doubtful")
    wait_for_job(out["job_id"], timeout=20.0)
    assert _flags(did)[:2] == ["slang_idiom", ""]


def test_doubtful_lines_a_person_checked_are_left_out(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch", 1: "source_mismatch"})
    first = db.load_line_objects(did)[0]
    first.reviewed = True
    db.save_lines(did, [first], fields=("reviewed",))
    _stronger(monkeypatch)
    assert glossary_flagged_service.doubtful_config_fields(did)["doubtful_flagged_count"] == 1


def test_a_plain_redo_keeps_other_flags(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {1: "uncertain_translation"})
    _stronger(monkeypatch)
    ids = [ln.id for ln in db.load_line_objects(did)]
    out = glossary_flagged_service.start_redo(did, "fake", line_ids=[ids[1]])
    wait_for_job(out["job_id"], timeout=20.0)
    assert _flags(did)[1] == "uncertain_translation"


def test_doubtful_redo_with_nothing_flagged_or_with_line_ids_is_refused(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _stronger(monkeypatch)
    with pytest.raises(UnsupportedOperationError):
        glossary_flagged_service.start_redo(did, "fake", kind="doubtful")
    with pytest.raises(InvalidInputError):
        glossary_flagged_service.start_redo(did, "fake", kind="doubtful", line_ids=[1])


def test_doubtful_estimate_is_reported_when_the_engine_can_be_priced(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch"})
    _stronger(monkeypatch)
    seen = {}

    def fake_estimate(drama_id, **kw):
        seen.update(kw)
        return {"estimated_usd": 0.12, "free": False}
    monkeypatch.setattr(translate_run_service, "estimate_translate_cost", fake_estimate)
    monkeypatch.setattr(translate_run_service, "run_model_and_batch_size",
                        lambda drama_id, engine: ("title-model", 7))
    fields = glossary_flagged_service.doubtful_config_fields(did)
    assert fields == {"doubtful_flagged_count": 1, "doubtful_redo_estimate_usd": 0.12,
                      "doubtful_redo_estimate_is_lower_bound": False,
                      "doubtful_redo_monthly_refusal": False, "doubtful_redo_above_cap": False}
    assert seen["force_retranslate"] is True and len(seen["line_ids"]) == 1
    # Priced as the redo will run: the title's own model and batch size, not the engine defaults.
    assert seen["model"] == "title-model" and seen["batch_size"] == 7


def test_the_estimate_uses_the_model_and_batch_size_of_the_titles_last_run(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None, translation_engine="claude")
    profile_service.remember_run(did, db.get_drama(did), on_title_engine=True, model=None,
                                 style_preset="audio_drama", style_note="", locale="en-US", batch_size=13,
                                 context_window=10, context_window_ahead=6)
    assert translate_run_service.run_model_and_batch_size(did, "claude")[1] == 13


def test_a_failing_estimate_leaves_the_count_and_no_price(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch"})
    _stronger(monkeypatch)

    def broken(drama_id, **kw):
        raise KeyError("pricing table")
    monkeypatch.setattr(translate_run_service, "estimate_translate_cost", broken)
    fields = glossary_flagged_service.doubtful_config_fields(did)
    assert fields["doubtful_flagged_count"] == 1 and fields["doubtful_redo_estimate_usd"] is None


def test_lines_fixed_with_find_and_replace_are_left_out_of_the_doubtful_redo(isolated_db, monkeypatch):
    did = _title(en=("a cat", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch", 1: "source_mismatch"})
    _stronger(monkeypatch)
    first = db.load_line_objects(did)[0]
    lines_service.apply_find_replace(did, [{"id": first.id, "old_text": "a cat", "new_text": "a dog"}])
    assert _flags(did)[0] == "source_mismatch"  # the fix keeps the flag ...
    assert glossary_flagged_service.doubtful_config_fields(did)["doubtful_flagged_count"] == 1
    out = glossary_flagged_service.start_redo(did, "fake", kind="doubtful")
    wait_for_job(out["job_id"], timeout=20.0)
    assert _en(did)[:2] == ["a dog", "[TEST] 句1"]  # ... but the redo never overwrites it


def _paid_cap(monkeypatch, cap_usd, spend):
    real = settings_service.resolve_key
    values = {"monthly_cap_usd": str(cap_usd), "claude": "sk-ant-test"}
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: values[k] if k in values else real(k, *a, **kw))
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: spend)


def test_a_used_up_monthly_cap_refuses_the_doubtful_redo_and_says_so_first(isolated_db, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {0: "source_mismatch"})
    _stronger(monkeypatch, "claude")
    _paid_cap(monkeypatch, 5.0, 5.0)
    fields = glossary_flagged_service.doubtful_config_fields(did)
    assert fields["doubtful_redo_monthly_refusal"] is True
    with pytest.raises(UnsupportedOperationError):
        glossary_flagged_service.start_redo(did, "claude", kind="doubtful")
    assert _en(did)[0] == "a"


def test_api_doubtful_redo(local_client, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {3: "slang_idiom"})
    _stronger(monkeypatch)
    cfg = local_client.get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["doubtful_flagged_count"] == 1
    r = local_client.post(f"/api/translate-run/dramas/{did}/glossary-flagged/run",
                          json={"kind": "doubtful"})
    assert r.status_code == 200 and len(r.json()["line_ids"]) == 1
    wait_for_job(r.json()["job_id"], timeout=20.0)
    assert _en(did)[3] == "[TEST] 句3" and _flags(did)[3] == ""


def test_api_redo_refuses_an_unknown_kind(local_client, monkeypatch):
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {3: "slang_idiom"})
    _stronger(monkeypatch)
    r = local_client.post(f"/api/translate-run/dramas/{did}/glossary-flagged/run", json={"kind": "everything"})
    assert r.status_code == 422
    assert _en(did)[3] == "d"


def test_api_doubtful_redo_on_a_paid_engine_needs_engines_paid(isolated_db, monkeypatch):
    from fastapi.testclient import TestClient
    from api import auth as api_auth
    from api.api_config import ApiSettings
    from api.server import create_app
    from services import auth_service
    did = _title(en=("a", "b", "c", "d"), errors=None)
    _flag_as(did, {3: "slang_idiom"})
    _stronger(monkeypatch, "claude")
    u = auth_service.add_user("member@example.com")
    auth_service.grant_permission(u["id"], "jobs.start")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    client = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                        raise_server_exceptions=False)
    r = client.post(f"/api/translate-run/dramas/{did}/glossary-flagged/run", json={"kind": "doubtful"},
                    headers={"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                             api_auth.CSRF_HEADER: s["csrf_token"]})
    assert r.status_code == 403
    assert _en(did)[3] == "d"


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


def test_ollama_down_with_an_empty_summary_is_a_dependency_error_not_a_500(isolated_db, monkeypatch):
    from lib.errors import DependencyUnavailableError
    did = _title(en=("a", "b", "c", "d"), errors=None)
    monkeypatch.setattr(episode_summary_service.line_ai_service, "engine_for",
                        lambda *a, **k: (object(), "ollama"))
    monkeypatch.setattr(episode_summary_service.llm_tasks, "generate_episode_summary", lambda lines, engine, **k: "")
    monkeypatch.setattr(episode_summary_service.translate_run_service, "ollama_reachable", lambda: False)
    with pytest.raises(DependencyUnavailableError, match="Ollama not reachable"):
        episode_summary_service.generate(did, "ollama")


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

    def fake_generate(lines, engine, usage_cb=None, character_names=None):
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
    monkeypatch.setattr(llm_tasks, "generate_episode_summary", lambda lines, engine, usage_cb=None, character_names=None: "S.")
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
    _, errors = engine_translate_pipeline.translate_lines_with_engine(
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


class _CutOffWhenBig(fake_engine.FakeEngine):
    """Returns empty text for every line of a batch bigger than `limit`."""

    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.sizes = []

    def translate_batch(self, zh, context):
        self.sizes.append(len(zh))
        if len(zh) > self.limit:
            return ["" for _ in zh]
        return [f"T:{z}" for z in zh]


def _run_big_batch(engine, n=8):
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"z{i}", id=70 + i) for i in range(n)]
    _, errors = engine_translate_pipeline.translate_lines_with_engine(
        lines, engine, drama_meta={"source_language": "zh"}, context_window=0, context_window_ahead=0)
    return lines, errors


def test_a_cut_off_batch_is_retried_in_smaller_pieces(isolated_db):
    engine = _CutOffWhenBig(limit=4)
    lines, errors = _run_big_batch(engine)
    assert errors == []
    assert all(ln.en == f"T:z{i}" for i, ln in enumerate(lines))
    assert max(engine.sizes[1:]) <= 4


def test_lines_still_empty_after_the_retry_are_reported(isolated_db):
    engine = _CutOffWhenBig(limit=0)
    lines, errors = _run_big_batch(engine)
    assert [ln.en for ln in lines] == [""] * 8
    assert errors and "got no translation" in errors[0]["error"]
    assert len(engine.sizes) <= 1 + 2 + 4  # first ask plus two halving levels
