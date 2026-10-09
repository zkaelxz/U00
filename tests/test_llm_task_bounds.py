"""call_llm_json on an OpenAI-compatible client, and its bounded-call scope:
request parameters per engine, a total deadline, cancel, and retry notices."""

import threading
import time
from types import SimpleNamespace

import pytest

from engine_backends import llm_tasks
from engine_backends.shared import TranslationCancelled


def _reply(text="[]"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))


class _Completions:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.behaviour(len(self.calls))


def _engine(name, behaviour=lambda n: _reply()):
    comp = _Completions(behaviour)
    return SimpleNamespace(name=name, model="m",
                           client=SimpleNamespace(chat=SimpleNamespace(completions=comp))), comp


@pytest.fixture
def no_abandoned():
    llm_tasks._abandoned.clear()
    yield
    llm_tasks._abandoned.clear()


def test_deepseek_gets_max_tokens_and_timeout_in_a_bounded_call():
    engine, comp = _engine("deepseek")
    with llm_tasks.bounded_llm_calls("j0", lambda: False):
        llm_tasks.call_llm_json(engine, "p", max_tokens=4000)
    (kw,) = comp.calls
    assert kw["max_tokens"] == 4000
    assert kw["timeout"] == llm_tasks.LLM_TASK_REQUEST_TIMEOUT
    assert "extra_body" not in kw  # thinking stays the title's choice


def test_thinking_is_off_only_when_the_bounded_call_asks():
    engine, comp = _engine("deepseek")
    with llm_tasks.bounded_llm_calls("j0", lambda: False, no_thinking=True):
        llm_tasks.call_llm_json(engine, "p")
    assert comp.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    other, ocomp = _engine("someother")
    with llm_tasks.bounded_llm_calls("j0", lambda: False, no_thinking=True):
        llm_tasks.call_llm_json(other, "p")
    assert "extra_body" not in ocomp.calls[0]


def test_unbounded_deepseek_call_keeps_default_timeout_and_thinking():
    engine, comp = _engine("deepseek")
    llm_tasks.call_llm_json(engine, "p", max_tokens=500)
    (kw,) = comp.calls
    assert "timeout" not in kw and "extra_body" not in kw


def test_deepseek_max_tokens_is_clamped():
    engine, comp = _engine("deepseek")
    llm_tasks.call_llm_json(engine, "p", max_tokens=10000)
    assert comp.calls[0]["max_tokens"] == llm_tasks.DEEPSEEK_MAX_OUTPUT_TOKENS
    other, ocomp = _engine("someother")
    llm_tasks.call_llm_json(other, "p", max_tokens=10000)
    assert ocomp.calls[0]["max_tokens"] == 10000


def test_claude_keeps_its_client_timeout_and_a_deadline_past_it():
    from engine_backends.shared import SDK_REQUEST_TIMEOUT
    seen = []

    class Messages:
        def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")])
    claude = SimpleNamespace(name="claude", model="m", client=SimpleNamespace(messages=Messages()))
    with llm_tasks.bounded_llm_calls("jc", lambda: False):
        llm_tasks.call_llm_json(claude, "p")
    assert "timeout" not in seen[0]
    scope = llm_tasks._BoundedScope("jc", lambda: False, None, False)
    assert llm_tasks._deadline_for(scope, claude) > SDK_REQUEST_TIMEOUT
    assert llm_tasks._deadline_for(scope, _engine("deepseek")[0]) == llm_tasks.LLM_TASK_DEADLINE_SECONDS


def test_ollama_deadline_follows_its_own_chat_timeout():
    from engine_backends.local import OllamaEngine, ollama_chat_timeout
    scope = llm_tasks._BoundedScope("jo", lambda: False, None, False)
    for model in ("qwen3:8b", "gemma4:31b"):
        engine = OllamaEngine(model=model)
        assert llm_tasks._deadline_for(scope, engine) >= ollama_chat_timeout(model)
    explicit = llm_tasks._BoundedScope("jo", lambda: False, 5, False)
    assert llm_tasks._deadline_for(explicit, OllamaEngine(model="qwen3:8b")) == 5


@pytest.mark.parametrize("how", ["cancel", "deadline"])
def test_abandoning_an_ollama_call_closes_its_request(no_abandoned, monkeypatch, how):
    from engine_backends import local
    from engine_backends.local import OllamaEngine
    closed = threading.Event()

    def fake_chat(base_url, payload):
        check = local.abort_check_var.get()
        assert check is not None
        while not check():
            time.sleep(0.02)
        closed.set()
        raise TranslationCancelled("cancelled")
    monkeypatch.setattr(llm_tasks, "_ollama_chat", fake_chat)
    flag = []
    threading.Timer(0.2, lambda: flag.append(1)).start()
    with llm_tasks.bounded_llm_calls(
            "jo2", lambda: bool(flag) if how == "cancel" else False,
            deadline=60 if how == "cancel" else 0.3):
        with pytest.raises((TranslationCancelled, llm_tasks.LLMTaskTimeout)):
            llm_tasks.call_llm_json(OllamaEngine(model="qwen3:8b"), "p")
    assert closed.wait(3)
    assert "jo2" not in llm_tasks._abandoned  # unwound, so a retry isn't refused


def test_other_openai_compatible_engines_get_no_thinking_field():
    engine, comp = _engine("someother")
    llm_tasks.call_llm_json(engine, "p", max_tokens=900)
    (kw,) = comp.calls
    assert kw["max_tokens"] == 900 and "extra_body" not in kw


def test_call_that_never_returns_ends_at_the_deadline(no_abandoned):
    release = threading.Event()
    engine, _ = _engine("deepseek", lambda n: release.wait(10) and _reply())
    started = time.monotonic()
    with llm_tasks.bounded_llm_calls("j1", lambda: False, deadline=0.5):
        with pytest.raises(llm_tasks.LLMTaskTimeout, match="did not answer within"):
            llm_tasks.call_llm_json(engine, "p")
    assert time.monotonic() - started < 3
    release.set()


def test_cancel_ends_the_wait_promptly(no_abandoned):
    release = threading.Event()
    engine, _ = _engine("deepseek", lambda n: release.wait(10) and _reply())
    cancel = threading.Timer(0.3, lambda: flag.append(1))
    flag = []
    cancel.start()
    started = time.monotonic()
    with llm_tasks.bounded_llm_calls("j2", lambda: bool(flag), deadline=60):
        with pytest.raises(TranslationCancelled):
            llm_tasks.call_llm_json(engine, "p")
    assert time.monotonic() - started < 3
    release.set()


def test_one_abandoned_call_per_job_blocks_a_second(no_abandoned):
    release = threading.Event()
    engine, comp = _engine("deepseek", lambda n: release.wait(10) and _reply())
    with llm_tasks.bounded_llm_calls("j3", lambda: False, deadline=0.3):
        with pytest.raises(llm_tasks.LLMTaskTimeout):
            llm_tasks.call_llm_json(engine, "p")
        with pytest.raises(llm_tasks.LLMTaskTimeout, match="still finishing"):
            llm_tasks.call_llm_json(engine, "p")
    assert len(comp.calls) == 1
    release.set()


def test_retry_is_reported_and_late_result_is_fine(no_abandoned):
    def flaky(n):
        if n == 1:
            raise RuntimeError("boom")
        return _reply("[1]")
    engine, comp = _engine("deepseek", flaky)
    waits = []
    with llm_tasks.bounded_llm_calls("j4", lambda: False,
                                     on_wait=lambda *a: waits.append(a), deadline=30):
        assert llm_tasks.call_llm_json(engine, "p") == "[1]"
    assert len(comp.calls) == 2 and waits == [(1, 2, 5)]
