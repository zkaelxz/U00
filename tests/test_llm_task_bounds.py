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


def test_deepseek_gets_max_tokens_and_thinking_off():
    engine, comp = _engine("deepseek")
    with llm_tasks.bounded_llm_calls("j0", lambda: False):
        llm_tasks.call_llm_json(engine, "p", max_tokens=4000)
    (kw,) = comp.calls
    assert kw["max_tokens"] == 4000
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kw["timeout"] == llm_tasks.LLM_TASK_REQUEST_TIMEOUT


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
