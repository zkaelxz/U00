"""Worst-case bounds: an AI call that never returns ends at its deadline with a
readable error, Cancel ends a Translate mid-batch, and a request that outlives
its job is never followed by a second one."""

import threading
import time

import pytest

import translate_engines
from core import Line
from engine_backends import llm_tasks


class HangingEngine:
    name = "deepseek"
    model = "m"
    supports_reference = True

    def __init__(self):
        self.calls = 0
        self.release = threading.Event()

    def translate_batch(self, zh_lines, context):
        self.calls += 1
        self.release.wait(15)
        return ["x"] * len(zh_lines)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm_tasks._abandoned.clear()
    llm_tasks._abandoned_jobless.clear()
    yield
    llm_tasks._abandoned.clear()
    llm_tasks._abandoned_jobless.clear()


def _lines(n=4):
    return [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句") for i in range(n)]


def _run(engine, **kw):
    return translate_engines.translate_lines_with_engine(
        _lines(), engine, drama_meta={}, batch_size=2, **kw)


def test_hung_batch_ends_at_the_deadline_and_stops_the_run(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 0.5)
    engine = HangingEngine()
    started = time.monotonic()
    _, errors = _run(engine)
    engine.release.set()
    assert time.monotonic() - started < 5
    assert len(errors) == 1 and "did not answer within" in errors[0]["error"]
    assert engine.calls == 1  # the second batch never sent a request


def test_cancel_ends_a_translate_mid_batch_quickly():
    engine = HangingEngine()
    cancelled = []
    threading.Timer(0.3, lambda: cancelled.append(1)).start()
    started = time.monotonic()
    with llm_tasks.bounded_llm_calls("job-c", lambda: bool(cancelled)):
        _, errors = _run(engine, cancel_check_cb=lambda: bool(cancelled))
    engine.release.set()
    assert time.monotonic() - started < 5
    assert errors == []  # a cancel is not a failed batch
    assert engine.calls == 1


def test_no_second_request_while_the_abandoned_one_lives(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 0.3)
    engine = HangingEngine()
    with llm_tasks.bounded_llm_calls("job-r", lambda: False):
        with pytest.raises(llm_tasks.LLMTaskTimeout):
            llm_tasks.call_batch_bounded(engine, lambda: engine.translate_batch(["a"], {}))
        with pytest.raises(llm_tasks.LLMTaskTimeout, match="still finishing"):
            llm_tasks.call_batch_bounded(engine, lambda: engine.translate_batch(["a"], {}))
    assert engine.calls == 1
    engine.release.set()


def test_jobless_calls_cap_the_abandoned_workers(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 0.2)
    monkeypatch.setattr(llm_tasks, "_MAX_ABANDONED_JOBLESS", 1)
    engines = [HangingEngine() for _ in range(2)]
    for engine in engines[:-1]:
        with pytest.raises(llm_tasks.LLMTaskTimeout, match="did not answer"):
            llm_tasks.call_batch_bounded(engine, lambda e=engine: e.translate_batch(["a"], {}))
    with pytest.raises(llm_tasks.LLMTaskTimeout, match="still finishing"):
        llm_tasks.call_batch_bounded(engines[-1], lambda: engines[-1].translate_batch(["a"], {}))
    assert engines[-1].calls == 0
    for engine in engines:
        engine.release.set()


def test_call_llm_json_has_a_deadline_without_any_scope(monkeypatch):
    monkeypatch.setattr(llm_tasks, "LLM_TASK_DEADLINE_SECONDS", 0.4)
    release = threading.Event()

    class Completions:
        def create(self, **kw):
            release.wait(15)

    engine = type("E", (), {"name": "deepseek", "model": "m", "client": type(
        "C", (), {"chat": type("Chat", (), {"completions": Completions()})()})()})()
    started = time.monotonic()
    with pytest.raises(llm_tasks.LLMTaskTimeout, match="did not answer within"):
        llm_tasks.call_llm_json(engine, "p")
    assert time.monotonic() - started < 5
    release.set()


def test_nested_call_inside_a_bounded_worker_runs_directly(monkeypatch):
    seen = []
    engine = type("E", (), {"name": "x", "model": "m", "client": None})()

    def outer():
        seen.append(threading.current_thread().name)
        return llm_tasks.call_llm_json(engine, "p", fallback="fb")

    assert llm_tasks.call_batch_bounded(engine, outer) == "fb"
    assert seen[0].startswith("llm-task-")



def test_translate_run_is_keyed_by_its_drama(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 0.3)
    engine = HangingEngine()
    translate_engines.translate_lines_with_engine(_lines(), engine, drama_meta={"id": 7})
    assert "translate:7" in llm_tasks._abandoned
    engine.release.set()


def test_bound_batches_keeps_usage_and_ends_a_hung_call(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 0.3)

    class Engine(HangingEngine):
        def translate_batch(self, zh_lines, context):
            self.last_usage = {"input_tokens": 3}
            return super().translate_batch(zh_lines, context)

    engine = llm_tasks.bound_batches(Engine())
    with pytest.raises(llm_tasks.LLMTaskTimeout):
        engine.translate_batch(["a"], {})
    assert engine.last_usage == {"input_tokens": 3}  # set on the engine itself
    engine.release.set()
