"""Bulk and CLI translation: thinking is off in the request unless the run
asks for it, hidden reasoning never reaches a saved line, and the choice is
remembered per title."""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from engine_backends import translate_pipeline, openai_compat, shared
from core import Line
from engine_backends import local, thinking
from services import (translate_run_service as run,
                      translate_thinking_service as tts)
from tests.job_helpers import wait_for_job


pytestmark = pytest.mark.usefixtures("isolated_db")


def _ctx(**kw):
    return translate_pipeline.build_translation_context(types.SimpleNamespace(supports_reference=True), {}, **kw)


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
    engine = openai_compat.DeepSeekEngine.__new__(openai_compat.DeepSeekEngine)
    engine.model = "deepseek-flash"
    engine.last_usage = shared._empty_usage()
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

    @pytest.mark.parametrize("model", ["gemma4:31b-cloud", "gemma4:31b-cloud"])
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
        out = local.OllamaEngine(model="gemma4:31b-cloud").translate_batch(["你好"], _ctx(thinking=True))
        assert out == ["Hello."]



class _Capturing:
    name = "fake"
    model = "fake"
    supports_reference = True
    checks_replies = False  # echoes the source script back

    def __init__(self):
        self.contexts = []

    def translate_batch(self, lines, context):
        self.contexts.append(context)
        return [f"EN:{ln}" for ln in lines]


@pytest.mark.parametrize("asked", [False, True])
def test_the_pipeline_hands_the_choice_to_every_batch(asked):
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"行{i}", en="") for i in range(3)]
    engine = _Capturing()
    translate_pipeline.translate_lines_with_engine(lines, engine, drama_meta={}, batch_size=2, thinking=asked)
    assert len(engine.contexts) == 2
    assert all(c["reply_with_thinking"] is asked and c["reply_without_thinking"] is not asked
               for c in engine.contexts)
    assert [ln.en for ln in lines] == ["EN:行0", "EN:行1", "EN:行2"]


def test_a_default_pipeline_run_is_off():
    lines = [Line(idx=0, start=0, end=1, zh="行", en="")]
    engine = _Capturing()
    translate_pipeline.translate_lines_with_engine(lines, engine, drama_meta={})
    assert engine.contexts[0]["reply_without_thinking"] is True


class TestThinkHarderRemoved:
    """The Translate "Think harder" option was removed: a saved or sent choice is read as off."""

    def test_a_saved_choice_is_not_read(self, isolated_db):
        did = db.create_drama(title_zh="D")
        db.update_drama(did, translate_thinking=1)
        assert thinking.title_thinking({"id": did}) is False
        assert tts.get_title_choice(did) is False

    def test_a_run_ignores_the_request_and_saves_nothing(self, isolated_db, monkeypatch):
        from tests import fake_engine
        seen = []
        real = fake_engine.FakeEngine.translate_batch

        def capture(self, zh_lines, context):
            seen.append(context.get("reply_with_thinking"))
            return real(self, zh_lines, context)
        monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", capture)
        did = db.create_drama(title_zh="D")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
        started = tts.start_with_thinking(run.start_translate_run, did, True, True, engine_name="fake")
        wait_for_job(started["job_id"], timeout=20.0)
        assert seen == [False] and started["thinking"] is False
        assert not db.get_drama(did).get("translate_thinking")

    def test_the_estimate_is_never_a_lower_bound(self, isolated_db):
        out = tts.annotate_estimate({"engine": "deepseek", "free": False}, 1, True)
        assert out["thinking"] is False and out["estimate_is_lower_bound"] is False


def test_the_deepseek_request_sets_no_output_cap_so_reasoning_cannot_truncate_the_reply():
    engine, seen = _deepseek('{"1": "Hello"}')
    engine.translate_batch(["你好"], {**_ctx(thinking=True), "line_ids": [1]})
    assert "max_tokens" not in seen[0] and "max_completion_tokens" not in seen[0]
