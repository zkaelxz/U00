"""Translation runs: thinking is off in the request, and hidden reasoning
never reaches a saved line."""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from engine_backends import translate_pipeline, openai_compat, shared
from core import Line
from engine_backends import local, thinking
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


def test_a_run_context_switches_thinking_off():
    assert _ctx()["reply_without_thinking"] is True


class TestDeepSeekRequest:
    def test_sends_the_off_form(self):
        engine, seen = _deepseek('{"1": "Hello."}')
        engine.translate_batch(["你好"], _ctx())
        assert seen[0]["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_reasoning_never_reaches_the_saved_text(self):
        reply = '<think>The speaker is {"1": "wrong"} maybe</think>{"1": "Hello."}'
        engine, _ = _deepseek(reply)
        assert engine.translate_batch(["你好"], _ctx()) == ["Hello."]

    def test_the_separate_reasoning_field_is_ignored(self):
        engine, _ = _deepseek('{"1": "Hello."}', reasoning='{"1": "Reasoning leaked."}')
        assert engine.translate_batch(["你好"], _ctx()) == ["Hello."]

    def test_a_reordered_reply_is_matched_by_id_not_position(self):
        engine, _ = _deepseek('{"2": "Two.", "1": "One."}')
        assert engine.translate_batch(["一", "二"], _ctx()) == ["One.", "Two."]

    def test_reasoning_tokens_are_already_inside_the_billed_output(self):
        usage = types.SimpleNamespace(
            prompt_tokens=100, completion_tokens=900, prompt_cache_hit_tokens=0,
            prompt_tokens_details=None,
            completion_tokens_details=types.SimpleNamespace(reasoning_tokens=700))
        engine, _ = _deepseek('{"1": "Hello."}', usage=usage)
        engine.translate_batch(["你好"], _ctx())
        # completion_tokens includes the reasoning; adding them again would
        # double-charge the run in the spend history.
        assert engine.last_usage["output_tokens"] == 900

    def test_sets_no_output_cap_so_reasoning_cannot_truncate_the_reply(self):
        engine, seen = _deepseek('{"1": "Hello"}')
        engine.translate_batch(["你好"], {**_ctx(), "line_ids": [1]})
        assert "max_tokens" not in seen[0] and "max_completion_tokens" not in seen[0]


class TestOllamaRequest:
    REPLY = {"message": {"content": '<think>try {"1": "no"}</think>{"1": "Hello."}',
                         "thinking": '{"1": "Reasoning leaked."}'}}

    def test_sends_the_off_form(self, monkeypatch):
        fake = _Ollama(self.REPLY)
        monkeypatch.setattr(local, "_ollama_chat", fake)
        local.OllamaEngine(model="gemma4:31b-cloud").translate_batch(["你好"], _ctx())
        assert fake.bodies[0]["think"] is False

    def test_reasoning_in_content_or_the_thinking_field_is_not_saved(self, monkeypatch):
        monkeypatch.setattr(local, "_ollama_chat", _Ollama(self.REPLY))
        out = local.OllamaEngine(model="gemma4:31b-cloud").translate_batch(["你好"], _ctx())
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


def test_every_batch_of_a_pipeline_run_is_off():
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"行{i}", en="") for i in range(3)]
    engine = _Capturing()
    translate_pipeline.translate_lines_with_engine(lines, engine, drama_meta={}, batch_size=2)
    assert len(engine.contexts) == 2
    assert all(c["reply_without_thinking"] is True for c in engine.contexts)
    assert [ln.en for ln in lines] == ["EN:行0", "EN:行1", "EN:行2"]


def test_a_run_that_still_sends_thinking_is_accepted_and_runs_off(local_client, monkeypatch):
    """The Translate "Think harder" option was removed: older clients still send
    `thinking`, and a title may still have the old choice saved."""
    from tests import fake_engine
    seen = []
    real = fake_engine.FakeEngine.translate_batch

    def capture(self, zh_lines, context):
        seen.append(context["reply_without_thinking"])
        return real(self, zh_lines, context)
    monkeypatch.setattr(fake_engine.FakeEngine, "translate_batch", capture)
    did = db.create_drama(title_zh="D")
    db.update_drama(did, translate_thinking=1)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    r = local_client.post(f"/api/translate-run/dramas/{did}/run", json={"engine": "fake", "thinking": True})
    assert r.status_code == 200
    wait_for_job(r.json()["job_id"], timeout=20.0)
    assert seen == [True]
