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
    def test_default_is_off_and_a_bad_file_reads_as_off(self, isolated_db):
        did = db.create_drama(title_zh="D")
        assert tts.get_title_choice(did) is False
        with open(os.path.join(db.drama_dir(did), tts.FILENAME), "w") as fh:
            fh.write("{not json")
        assert tts.get_title_choice(did) is False

    def test_an_explicit_choice_is_remembered_and_used_when_not_asked(self, isolated_db):
        did = db.create_drama(title_zh="D")
        assert tts.run_setting(did, ["deepseek"], True, remember=True) is True
        assert tts.get_title_choice(did) is True
        assert tts.run_setting(did, ["deepseek"], None) is True
        assert tts.run_setting(did, ["deepseek"], False, remember=True) is False
        assert tts.run_setting(did, ["deepseek"], None) is False

    def test_engines_without_a_switch_and_reflect_never_think(self, isolated_db):
        did = db.create_drama(title_zh="D")
        assert tts.run_setting(did, ["claude"], True) is False
        assert tts.run_setting(did, ["deepseek"], True, reflect=True) is False
        assert tts.run_setting(did, ["claude", "ollama"], True) is True

    def test_the_config_reports_the_remembered_choice_and_the_engines(self, isolated_db):
        did = db.create_drama(title_zh="D")
        tts.save_title_choice(did, True)
        cfg = run.get_translate_config(did)
        assert cfg["title_thinking"] is True
        assert cfg["thinking_switch_engines"] == ["deepseek", "ollama"]


def test_the_estimate_is_a_lower_bound_only_when_thinking_costs_money(isolated_db):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好世界" * 20, en="")])
    off = run.estimate_translate_cost(did, "deepseek")
    on = run.estimate_translate_cost(did, "deepseek", thinking=True)
    assert on["thinking"] is True and on["estimate_is_lower_bound"] is True
    assert off["thinking"] is False and off["estimate_is_lower_bound"] is False
    assert on["estimated_usd"] == off["estimated_usd"]
    assert run.estimate_translate_cost(did, "claude", thinking=True)["thinking"] is False
    assert run.estimate_translate_cost(did, "ollama", thinking=True)["estimate_is_lower_bound"] is False


def test_provenance_keeps_the_default_hash_and_separates_a_thinking_run(isolated_db):
    def settings_of(**kw):
        seen = {}
        orig = line_provenance_service.tracker
        line_provenance_service.tracker = lambda *a, settings=None, **k: seen.update(s=settings)
        try:
            line_provenance_service.translate_run_tracker(1, [], te.ENGINES["fake"]("k"), "fake", None,
                                                           locale="en-US", **kw)
        finally:
            line_provenance_service.tracker = orig
        return seen["s"]
    default = settings_of()
    assert "thinking" not in default and settings_of(thinking=False) == default
    assert settings_of(thinking=True)["thinking"] is True


def test_start_run_passes_thinking_to_the_job_and_remembers_it(isolated_db, monkeypatch):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    started = {}
    monkeypatch.setattr(run.translate_service, "resolve_api_key", lambda name: "k")
    monkeypatch.setattr(te, "get_engine", lambda *a, **k: _Capturing())
    monkeypatch.setattr(run.background_jobs, "start_job",
                        lambda job_id, fn, *a, **kw: started.update(kw=kw) or True)
    monkeypatch.setattr(run, "pick_summary_engine", lambda **kw: (None, None))
    res = run.start_translate_run(did, engine_name="deepseek", thinking=True)
    assert res["thinking"] is True and started["kw"]["thinking"] is True
    assert tts.get_title_choice(did) is True
    res = run.start_translate_run(did, engine_name="deepseek")
    assert res["thinking"] is True  # not asked: the title's choice
    res = run.start_translate_run(did, engine_name="claude")
    assert res["thinking"] is False
    res = run.start_translate_run(did, engine_name="deepseek", thinking=False)
    assert res["thinking"] is False and tts.get_title_choice(did) is False


@pytest.mark.parametrize("thinking_on", [False, True])
def test_the_deepseek_off_peak_job_carries_the_choice(isolated_db, monkeypatch, thinking_on):
    did = db.create_drama(title_zh="D")
    captured = {}
    monkeypatch.setattr(run.bulk_translate, "schedule_offpeak_translation",
                        lambda drama_id, lines, engine_name, model, args, **kw: captured.update(args=args) or 7)
    submit = run._bulk_submitter(did, {}, _Capturing(), "deepseek", False, None, None, "", "", "en-US",
                                 "audio_drama", 6, 3, 20, False, None, None, thinking_on)
    assert submit() == 7 and captured["args"]["thinking"] is thinking_on
