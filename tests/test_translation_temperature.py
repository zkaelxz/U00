"""A translation temperature is sent only by the engines that accept one, and only when a Lab arm set it."""
import types

from engine_backends import translate_pipeline, openai_compat, shared
from engine_backends import gemini as engine_gemini
from engine_backends import claude as engine_claude
from engine_backends import local
from engine_backends.temperature import TEMPERATURE_ENGINES, temperature_of


def _ctx(**kw):
    return translate_pipeline.build_translation_context(types.SimpleNamespace(supports_reference=True), {}, **kw)


def _deepseek():
    engine = openai_compat.DeepSeekEngine.__new__(openai_compat.DeepSeekEngine)
    engine.model = "deepseek-flash"
    engine.last_usage = shared._empty_usage()
    seen = []
    message = types.SimpleNamespace(content='{"1": "Hello."}', refusal=None)
    resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=None)

    class _Completions:
        def create(self, **kwargs):
            seen.append(kwargs)
            return resp
    engine.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    return engine, seen


def test_a_title_context_carries_no_temperature():
    assert _ctx()["temperature"] is None


def test_only_engines_that_accept_one_are_asked():
    assert set(TEMPERATURE_ENGINES) == {"deepseek", "gemini", "ollama"}
    assert temperature_of({"temperature": 0.3}, "claude") is None
    assert temperature_of({"temperature": 0.3}, "openai") is None
    assert temperature_of({"temperature": 0.0}, "gemini") == 0.0


def test_deepseek_sends_it_only_when_set():
    engine, seen = _deepseek()
    engine.translate_batch(["你好"], _ctx())
    engine.translate_batch(["你好"], _ctx(temperature=0.3))
    assert "temperature" not in seen[0]
    assert seen[1]["temperature"] == 0.3


def test_gemini_puts_it_in_the_generation_config_only_when_set():
    engine = engine_gemini.GeminiEngine("key")
    assert "generationConfig" not in engine.build_request_body(_ctx(), "1. x")
    assert engine.build_request_body(_ctx(temperature=0.3), "1. x")["generationConfig"] == {"temperature": 0.3}


def test_ollama_puts_it_in_the_options_only_when_set(monkeypatch):
    bodies = []

    def fake(base_url, payload):
        bodies.append(payload)
        return {"message": {"content": '{"1": "Hello."}'}}
    monkeypatch.setattr(local, "_ollama_chat", fake)
    engine = local.OllamaEngine(model="gemma4:12b")
    engine.translate_batch(["你好"], _ctx())
    engine.translate_batch(["你好"], _ctx(temperature=0.3))
    assert "temperature" not in bodies[0]["options"]
    assert bodies[1]["options"]["temperature"] == 0.3


def test_claude_requests_never_carry_one(monkeypatch):
    monkeypatch.setattr("engine_backends.claude.make_anthropic_client", lambda key: object())
    params = engine_claude.ClaudeEngine("key").build_request_params(_ctx(temperature=0.3), "1. x")
    assert "temperature" not in params
