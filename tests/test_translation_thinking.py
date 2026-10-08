"""Bulk and CLI translation: thinking is off in the request unless the run
asks for it, hidden reasoning never reaches a saved line, and the choice is
remembered per title."""
import json
import os
import sys
import types

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import translate_engines as te
from core import Line
from engine_backends import local, thinking
from services import (line_provenance_service, translate_run_service as run,
                      translate_thinking_service as tts)


def _ctx(**kw):
    return te.build_translation_context(types.SimpleNamespace(supports_reference=True), {}, **kw)


class _Ollama:
    """Stands in for local._ollama_chat and records every request body."""

    def __init__(self, reply):
        self.bodies = []
        self.reply = reply

    def __call__(self, base_url, payload):
        self.bodies.append(payload)
        return self.reply


@pytest.fixture(autouse=True)
def _fresh_think_memory():
    thinking._think_refused.clear()
    yield
    thinking._think_refused.clear()


def _deepseek(content, usage=None, reasoning="Let me think: {\"1\": \"wrong\"}"):
    engine = te.DeepSeekEngine.__new__(te.DeepSeekEngine)
    engine.model = "deepseek-flash"
    engine.last_usage = te._empty_usage()
    seen = []
    message = types.SimpleNamespace(content=content, refusal=None, reasoning_content=reasoning)
    resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=usage)

    class _Completions:
        def create(self, **kwargs):
            seen.append(kwargs)
            return resp
    engine.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    return engine, seen


class TestContext:
    def test_a_run_context_asks_for_no_thinking_by_default(self):
        ctx = _ctx()
        assert ctx["reply_without_thinking"] is True and ctx["reply_with_thinking"] is False

    def test_thinking_on_flips_both(self):
        ctx = _ctx(thinking=True)
        assert ctx["reply_without_thinking"] is False and ctx["reply_with_thinking"] is True

    def test_off_wins_if_both_flags_are_set(self):
        assert thinking.thinking_choice({"reply_without_thinking": True,
                                         "reply_with_thinking": True}) is False


class TestDeepSeekRequest:
    def test_off_form_by_default_and_on_form_when_toggled(self):
        engine, seen = _deepseek('{"1": "Hello."}')
        engine.translate_batch(["你好"], _ctx())
        engine.translate_batch(["你好"], _ctx(thinking=True))
        assert seen[0]["extra_body"] == {"thinking": {"type": "disabled"}}
        assert seen[1]["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_reasoning_never_reaches_the_saved_text(self):
        reply = '<think>The speaker is {"1": "wrong"} maybe</think>{"1": "Hello."}'
        engine, _ = _deepseek(reply)
        assert engine.translate_batch(["你好"], _ctx(thinking=True)) == ["Hello."]

    def test_the_separate_reasoning_field_is_ignored(self):
        engine, _ = _deepseek('{"1": "Hello."}', reasoning='{"1": "Reasoning leaked."}')
        assert engine.translate_batch(["你好"], _ctx(thinking=True)) == ["Hello."]

    def test_a_reordered_reply_is_matched_by_id_not_position(self):
        engine, _ = _deepseek('{"2": "Two.", "1": "One."}')
        assert engine.translate_batch(["一", "二"], _ctx(thinking=True)) == ["One.", "Two."]

    def test_reasoning_tokens_are_already_inside_the_billed_output(self):
        usage = types.SimpleNamespace(
            prompt_tokens=100, completion_tokens=900, prompt_cache_hit_tokens=0,
            prompt_tokens_details=None,
            completion_tokens_details=types.SimpleNamespace(reasoning_tokens=700))
        engine, _ = _deepseek('{"1": "Hello."}', usage=usage)
        engine.translate_batch(["你好"], _ctx(thinking=True))
        # completion_tokens includes the reasoning; adding them again would
        # double-charge the run in the spend history.
        assert engine.last_usage["output_tokens"] == 900


class TestOllamaRequest:
    REPLY = {"message": {"content": '<think>try {"1": "no"}</think>{"1": "Hello."}',
                         "thinking": '{"1": "Reasoning leaked."}'}}

    @pytest.mark.parametrize("model", ["gemma4:12b", "qwen3.5:9b"])
    def test_off_form_by_default_and_on_form_when_toggled(self, monkeypatch, model):
        fake = _Ollama(self.REPLY)
        monkeypatch.setattr(local, "_ollama_chat", fake)
        engine = local.OllamaEngine(model=model)
        engine.translate_batch(["你好"], _ctx())
        engine.translate_batch(["你好"], _ctx(thinking=True))
        assert fake.bodies[0]["think"] is False
        assert fake.bodies[1]["think"] is True

    def test_reasoning_in_content_or_the_thinking_field_is_not_saved(self, monkeypatch):
        monkeypatch.setattr(local, "_ollama_chat", _Ollama(self.REPLY))
        out = local.OllamaEngine(model="gemma4:12b").translate_batch(["你好"], _ctx(thinking=True))
        assert out == ["Hello."]

    def test_a_model_without_thinking_is_retried_without_the_field(self, monkeypatch):
        bodies = []

        def chat(base_url, payload):
            bodies.append(payload)
            if "think" in payload:
                resp = requests.Response()
                resp.status_code = 400
                resp._content = b'"llama3.1" does not support thinking'
                raise requests.HTTPError("400", response=resp)
            return {"message": {"content": '{"1": "Hello."}'}}
        monkeypatch.setattr(local, "_ollama_chat", chat)
        engine = local.OllamaEngine(model="llama3.1")
        assert engine.translate_batch(["你好"], _ctx(thinking=True)) == ["Hello."]
        assert "think" not in bodies[-1]


class _Capturing:
    name = "fake"
    model = "fake"
    supports_reference = True

    def __init__(self):
        self.contexts = []

    def translate_batch(self, lines, context):
        self.contexts.append(context)
        return [f"EN:{ln}" for ln in lines]


@pytest.mark.parametrize("asked", [False, True])
def test_the_pipeline_hands_the_choice_to_every_batch(asked):
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"行{i}", en="") for i in range(3)]
    engine = _Capturing()
    te.translate_lines_with_engine(lines, engine, drama_meta={}, batch_size=2, thinking=asked)
    assert len(engine.contexts) == 2
    assert all(c["reply_with_thinking"] is asked and c["reply_without_thinking"] is not asked
               for c in engine.contexts)
    assert [ln.en for ln in lines] == ["EN:行0", "EN:行1", "EN:行2"]


def test_a_default_pipeline_run_is_off():
    lines = [Line(idx=0, start=0, end=1, zh="行", en="")]
    engine = _Capturing()
    te.translate_lines_with_engine(lines, engine, drama_meta={})
    assert engine.contexts[0]["reply_without_thinking"] is True


class TestTitleChoice:
    def test_default_is_off_and_unset_is_null(self, isolated_db):
        did = db.create_drama(title_zh="D")
        assert tts.get_title_choice(did) is False
        assert db.get_drama(did)["translate_thinking"] is None

    def test_the_choice_lives_in_the_drama_row_not_a_file(self, isolated_db):
        did = db.create_drama(title_zh="D")
        tts.save_title_choice(did, True)
        assert db.get_drama(did)["translate_thinking"] == 1
        assert not os.path.exists(os.path.join(db.DRAMAS_DIR, str(did)))

    def test_an_explicit_choice_is_remembered_and_none_leaves_it(self, isolated_db):
        did = db.create_drama(title_zh="D")
        tts.save_title_choice(did, True)
        assert tts.get_title_choice(did) is True
        tts.save_title_choice(did, None)
        assert tts.get_title_choice(did) is True
        tts.save_title_choice(did, False)
        assert tts.get_title_choice(did) is False

    def test_an_unknown_drama_is_left_alone(self, isolated_db):
        tts.save_title_choice(9999, True)
        assert tts.get_title_choice(9999) is False

    def test_a_run_context_follows_the_title_unless_told(self, isolated_db):
        did = db.create_drama(title_zh="D")
        tts.save_title_choice(did, True)
        engine = types.SimpleNamespace(supports_reference=True)
        assert te.build_translation_context(engine, {"id": did})["reply_with_thinking"] is True
        assert te.build_translation_context(engine, {"id": did}, thinking=False)["reply_with_thinking"] is False

    def test_the_cli_flag_is_saved_with_the_other_toggles(self, isolated_db):
        import argparse
        did = db.create_drama(title_zh="D")
        parser = te.think_flag(argparse.ArgumentParser())
        assert parser.parse_args([]).thinking is None
        run.save_style_toggles(did, thinking=parser.parse_args(["--thinking"]).thinking)
        assert tts.get_title_choice(did) is True
        run.save_style_toggles(did, thinking=parser.parse_args([]).thinking)
        assert tts.get_title_choice(did) is True
        run.save_style_toggles(did, thinking=parser.parse_args(["--no-thinking"]).thinking)
        assert tts.get_title_choice(did) is False

    def test_engines_without_a_switch_and_reflect_never_think(self):
        assert tts.effective(["claude"], True) is False
        assert tts.effective(["deepseek"], True, reflect=True) is False
        assert tts.effective(["claude", "ollama"], True) is True

    def test_the_config_reports_the_remembered_choice_and_the_engines(self, isolated_db):
        did = db.create_drama(title_zh="D")
        tts.save_title_choice(did, True)
        assert tts.config_fields(did) == {"thinking_switch_engines": ["deepseek", "ollama"],
                                          "title_thinking": True}


def test_the_estimate_is_a_lower_bound_only_when_thinking_costs_money(isolated_db):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好世界" * 20, en="")])
    est = run.estimate_translate_cost(did, "deepseek")
    off = tts.annotate_estimate(est, did, None)
    on = tts.annotate_estimate(est, did, True)
    assert on["thinking"] is True and on["estimate_is_lower_bound"] is True
    assert off["thinking"] is False and off["estimate_is_lower_bound"] is False
    assert on["estimated_usd"] == off["estimated_usd"]
    assert tts.annotate_estimate(run.estimate_translate_cost(did, "claude"), did, True)["thinking"] is False
    ollama = tts.annotate_estimate(run.estimate_translate_cost(did, "ollama"), did, True)
    assert ollama["thinking"] is True and ollama["estimate_is_lower_bound"] is False


def test_provenance_keeps_the_default_hash_and_separates_a_thinking_run(isolated_db, monkeypatch):
    did = db.create_drama(title_zh="D")
    seen = []
    monkeypatch.setattr(line_provenance_service, "tracker",
                        lambda *a, settings=None, **k: seen.append(settings))

    def settings_for(engine_choice, **kw):
        line_provenance_service.translate_run_tracker(
            did, [], types.SimpleNamespace(), engine_choice, None, locale="en-US", **kw)
        return seen[-1](engine_choice)
    default = settings_for("deepseek")
    assert "thinking" not in default
    tts.save_title_choice(did, True)
    assert settings_for("deepseek")["thinking"] is True
    assert "thinking" not in settings_for("claude")
    assert "thinking" not in settings_for("deepseek", reflect=True)


def test_a_thinking_fallback_gets_the_thinking_hash_and_a_claude_primary_does_not(isolated_db, monkeypatch):
    did = db.create_drama(title_zh="D")
    seen = []
    monkeypatch.setattr(line_provenance_service, "tracker",
                        lambda *a, settings=None, **k: seen.append(settings))
    line_provenance_service.translate_run_tracker(
        did, [], types.SimpleNamespace(), "claude", None, thinking=True, locale="en-US")
    for_engine = seen[-1]
    assert "thinking" not in for_engine("claude")
    assert for_engine("deepseek")["thinking"] is True


def _client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings()), headers={"X-Baihe-Local": "1"})


# The Translate form's run body, as buildRunBody sends it.
UI_RUN_BODY = {
    "engine": "deepseek", "model": "deepseek-flash", "style_preset": "audio_drama",
    "style_note": "", "locale": "en-US", "force_retranslate": False,
    "context_window": 6, "context_window_ahead": 3, "batch_size": 20,
    "thinking": True, "default_female_pronouns": False, "include_genre_notes": True,
}
UI_AFFECTED_BODY = {
    **{k: v for k, v in UI_RUN_BODY.items() if k != "force_retranslate"},
    "line_ids": [1], "preview_hash": "abc", "include_hand_edited": False, "term_ids": [1],
}


def test_the_ui_bodies_are_accepted_by_both_run_routes(isolated_db, monkeypatch):
    from services import glossary_retranslate_service as gls
    did = db.create_drama(title_zh="D")
    seen = {}
    monkeypatch.setattr(run, "start_translate_run",
                        lambda drama_id, **kw: seen.update(run=kw) or {
                            "job_id": "j", "drama_id": drama_id, "engine": "deepseek",
                            "target_line_count": 1, "reflect": False})
    monkeypatch.setattr(gls, "start_affected_retranslate",
                        lambda drama_id, *a, **kw: seen.update(glossary=kw) or {
                            "job_id": "j", "drama_id": drama_id, "engine": "deepseek",
                            "target_line_count": 1, "reflect": False, "line_ids": [1],
                            "skipped_hand_edited_count": 0})
    client = _client()
    assert client.post(f"/api/translate-run/dramas/{did}/run", json=UI_RUN_BODY).status_code == 200
    resp = client.post(f"/api/translate-run/dramas/{did}/glossary-affected/run", json=UI_AFFECTED_BODY)
    assert resp.status_code == 200, resp.text
    assert seen["run"]["thinking"] is True and seen["glossary"]["thinking"] is True


def _fake_run(did, thinking=None, remember=True):
    return tts.start_with_thinking(run.start_translate_run, did, thinking, remember,
                                   engine_name="fake")


@pytest.fixture
def run_contexts(monkeypatch):
    """The reply_with_thinking value of every batch the fake engine translates."""
    from tests import fake_engine
    seen = []
    real = fake_engine.FakeEngine.translate_batch

    def capture(self, zh_lines, context):
        seen.append(context.get("reply_with_thinking"))
        return real(self, zh_lines, context)
    monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", capture)
    return seen


def _wait_job(job_id):
    import time
    import background_jobs
    for _ in range(400):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _drama_with_lines():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    return did


def test_a_run_uses_its_own_choice_and_saves_it_once_accepted(isolated_db, run_contexts):
    did = _drama_with_lines()
    started = _fake_run(did, thinking=True)
    _wait_job(started["job_id"])
    assert run_contexts == [True] and tts.get_title_choice(did) is True
    assert started["thinking"] is False  # the fake engine has no switch


def test_a_refused_request_leaves_the_saved_choice_alone(isolated_db, monkeypatch):
    import background_jobs
    from services.service_errors import ConflictError
    did = _drama_with_lines()
    tts.save_title_choice(did, False)
    monkeypatch.setattr(background_jobs, "is_running", lambda job_id: True)
    with pytest.raises(ConflictError):
        _fake_run(did, thinking=True)
    assert tts.get_title_choice(did) is False
    client = _client()
    resp = client.post(f"/api/translate-run/dramas/{did}/run",
                       json={"engine": "fake", "thinking": True})
    assert resp.status_code == 409
    assert tts.get_title_choice(did) is False


def test_a_caller_without_paid_engines_cannot_make_thinking_stick_for_paid_runs(
        isolated_db, run_contexts):
    did = _drama_with_lines()
    started = _fake_run(did, thinking=True, remember=False)
    _wait_job(started["job_id"])
    assert run_contexts == [True]  # this run still thinks
    assert tts.get_title_choice(did) is False


def test_may_remember_needs_paid_engines_or_an_all_free_chain():
    assert tts.may_remember(True, "claude", None)
    assert tts.may_remember(False, "ollama", "fake")
    assert not tts.may_remember(False, "ollama", "deepseek")
    assert not tts.may_remember(False, None)


def test_the_route_does_not_save_for_a_caller_without_paid_engines(isolated_db, monkeypatch):
    import api.routers.translate_run_routes as routes
    did = _drama_with_lines()
    seen = {}
    monkeypatch.setattr(run, "start_translate_run",
                        lambda drama_id, **kw: seen.update(kw) or {
                            "job_id": "j", "drama_id": drama_id, "engine": "ollama",
                            "target_line_count": 1, "reflect": False})
    client = _client()
    body = {"engine": "ollama", "thinking": True}
    monkeypatch.setattr(routes, "holds_paid_engines", lambda request: False)
    assert client.post(f"/api/translate-run/dramas/{did}/run", json=body).status_code == 200
    assert seen["thinking"] is True and tts.get_title_choice(did) is True  # all-free chain
    tts.save_title_choice(did, False)
    assert client.post(f"/api/translate-run/dramas/{did}/run",
                       json={**body, "engine": None}).status_code == 200
    assert seen["thinking"] is True and tts.get_title_choice(did) is False  # this run only


def test_the_config_route_reports_the_remembered_choice(isolated_db):
    did = db.create_drama(title_zh="D")
    cfg = _client().get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["title_thinking"] is False and cfg["thinking_switch_engines"] == ["deepseek", "ollama"]


def test_the_provenance_follows_the_runs_own_choice(isolated_db, monkeypatch):
    did = db.create_drama(title_zh="D")
    seen = []
    monkeypatch.setattr(line_provenance_service, "tracker",
                        lambda *a, settings=None, **k: seen.append(settings))
    line_provenance_service.translate_run_tracker(
        did, [], types.SimpleNamespace(), "deepseek", None, thinking=True, locale="en-US")
    assert seen[-1]("deepseek")["thinking"] is True and tts.get_title_choice(did) is False


def test_saving_never_raises(isolated_db, monkeypatch):
    did = db.create_drama(title_zh="D")
    monkeypatch.setattr(db, "update_drama", lambda *a, **k: (_ for _ in ()).throw(OSError("no disk")))
    tts.save_title_choice(did, True)


def test_the_deepseek_request_sets_no_output_cap_so_reasoning_cannot_truncate_the_reply():
    engine, seen = _deepseek('{"1": "Hello"}')
    engine.translate_batch(["你好"], {**_ctx(thinking=True), "line_ids": [1]})
    assert "max_tokens" not in seen[0] and "max_completion_tokens" not in seen[0]


def test_the_deepseek_off_peak_job_reads_the_title_when_it_runs(isolated_db):
    did = db.create_drama(title_zh="D")
    tts.save_title_choice(did, True)
    engine = types.SimpleNamespace(supports_reference=True)
    assert te.build_translation_context(engine, db.get_drama(did))["reply_with_thinking"] is True


def test_a_glossary_retranslate_run_gets_the_same_thinking_value(isolated_db, monkeypatch):
    from services import glossary_retranslate_service as gls
    did = _drama_with_lines()
    monkeypatch.setattr(gls, "_affected", lambda *a: ([], [{"id": 1, "hand_edited": False, "en": ""}], "h"))
    monkeypatch.setattr(db, "load_lines", lambda d: [{"id": 1}])
    seen = {}
    monkeypatch.setattr(run, "start_translate_run",
                        lambda drama_id, **kw: seen.update(kw) or {"job_id": "j", "engine": "fake"})
    out = tts.start_with_thinking(gls.start_affected_retranslate, did, True, False, [1], "h",
                                  engine_name="fake")
    assert seen["thinking"] is True and out["thinking"] is False
    assert tts.get_title_choice(did) is False
