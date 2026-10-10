"""Worst-case bounds: an AI call that never returns ends at its deadline with a
readable error, Cancel ends a Translate mid-batch, and a request that outlives
its job is never followed by a second one."""

import threading
import time
from types import SimpleNamespace

import pytest

import translate_engines
from core import Line
from engine_backends import llm_tasks
from engine_backends.shared import SDK_REQUEST_TIMEOUT


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


def _deepseek_json_engine(reply="{}"):
    """A DeepSeek-shaped engine whose chat client answers every prompt."""
    class Completions:
        def __init__(self):
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    comp = Completions()
    engine = SimpleNamespace(name="deepseek", model="m", supports_reference=True,
                             client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))
    return engine, comp


def _record_deadlines(monkeypatch):
    seen = []
    original = llm_tasks._run_bounded

    def spy(scope, fn, engine=None, deadline=None, on_late_result=None):
        seen.append(deadline if deadline is not None else llm_tasks._deadline_for(scope, engine))
        return original(scope, fn, engine, deadline, on_late_result)
    monkeypatch.setattr(llm_tasks, "_run_bounded", spy)
    return seen


def test_reflect_pass_in_a_translate_scope_gets_the_per_request_budget(monkeypatch):
    # With thinking on DeepSeek keeps the connection alive while it reasons, so a
    # healthy pass can take the whole client timeout; the default 180 s would cut it.
    seen = _record_deadlines(monkeypatch)
    engine, comp = _deepseek_json_engine()
    translate_engines.translate_lines_with_engine(_lines(2), engine, drama_meta={"id": 3},
                                                  batch_size=2, reflect=True)
    assert comp.calls  # the passes really went through call_llm_json
    assert seen and all(d >= SDK_REQUEST_TIMEOUT for d in seen)
    assert "extra_body" not in comp.calls[0]  # the title's thinking choice is kept


def test_default_deepseek_deadline_still_applies_outside_a_translate_scope():
    scope = llm_tasks._BoundedScope(None, lambda: False, None, False)
    engine, _ = _deepseek_json_engine()
    assert llm_tasks._deadline_for(scope, engine) == llm_tasks.LLM_TASK_DEADLINE_SECONDS
    assert llm_tasks.request_deadline_for(engine) == SDK_REQUEST_TIMEOUT + llm_tasks._DEADLINE_MARGIN_SECONDS


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds


class _TimeoutError(Exception):
    pass


def test_fallback_chain_window_covers_every_attempt_on_a_hung_engine(monkeypatch):
    """FallbackEngine retries a transient error on the same engine before it
    switches, so the chain's deadline must hold all of those attempts and
    still leave the next engine its own window. Timed on a fake clock."""
    from engine_backends import fallback
    clock = _FakeClock()
    monkeypatch.setattr(fallback, "_fallback_sleep", clock.advance)
    per_attempt = llm_tasks._BATCH_REQUESTS * SDK_REQUEST_TIMEOUT

    class Hung:
        name = "claude"
        last_usage = {}

        def translate_batch(self, zh_lines, context):
            clock.advance(per_attempt)  # every request runs to its client timeout
            raise _TimeoutError("read timed out")

    class Slow:
        name = "deepseek"
        last_usage = {}

        def translate_batch(self, zh_lines, context):
            clock.advance(per_attempt)
            return ["ok"] * len(zh_lines)

    chain = translate_engines.FallbackEngine([Hung(), Slow()], ["claude", "deepseek"])
    assert chain.translate_batch(["a"], {}) == ["ok"]
    assert chain.active == 1
    assert clock.now <= llm_tasks.batch_deadline_for(chain)
    # The old "one window per engine" budget would have ended the call before
    # the chain ever reached the second engine.
    assert clock.now > 2 * llm_tasks.batch_deadline_for(Slow())


def test_usage_of_an_abandoned_batch_is_still_logged(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 0.3)
    logged = []

    class Billed(HangingEngine):
        def translate_batch(self, zh_lines, context):
            out = super().translate_batch(zh_lines, context)
            self.last_usage = {"input_tokens": 11, "output_tokens": 7}
            return out

    engine = Billed()
    _, errors = _run(engine, usage_cb=lambda inp, out, *rest: logged.append((inp, out)))
    assert "did not answer within" in errors[0]["error"] and logged == []
    engine.release.set()
    for worker in llm_tasks._abandoned_jobless["deepseek"]:
        worker.join(5)
    assert logged == [(11, 7)]  # the request was billed although its result was dropped


def test_cancel_during_a_split_batch_does_not_start_the_second_half():
    cancelled = []

    class Blocks:
        name = "claude"
        model = "m"
        supports_reference = True
        calls = 0

        def translate_batch(self, zh_lines, context):
            self.calls += 1
            if self.calls == 1:
                raise translate_engines.ContentModerationBlocked("claude", "policy")
            cancelled.append(1)  # Cancel lands while the first half is in flight
            raise translate_engines.TranslationCancelled("cancelled")

    engine = Blocks()
    _, errors = translate_engines.translate_lines_with_engine(
        _lines(4), engine, drama_meta={}, batch_size=4,
        cancel_check_cb=lambda: bool(cancelled))
    assert engine.calls == 2 and errors == []


def test_jobless_cap_is_per_engine_and_a_running_call_holds_its_slot(monkeypatch):
    monkeypatch.setattr(llm_tasks, "batch_deadline_for", lambda engine: 5)
    monkeypatch.setattr(llm_tasks, "_MAX_ABANDONED_JOBLESS", 1)
    other = HangingEngine()
    other.name = "claude"
    started = threading.Event()

    class Holds(HangingEngine):
        def translate_batch(self, zh_lines, context):
            started.set()
            return super().translate_batch(zh_lines, context)

    first = Holds()
    outcome = {}

    def run_first():
        try:
            outcome["first"] = llm_tasks.call_batch_bounded(
                first, lambda: first.translate_batch(["a"], {}))
        except Exception as exc:
            outcome["first"] = exc
    t = threading.Thread(target=run_first)
    t.start()
    assert started.wait(5)
    # Still in flight, not abandoned: its slot is already taken, so a second
    # call on the same engine is refused instead of racing it for the slot.
    second = HangingEngine()
    with pytest.raises(llm_tasks.LLMTaskTimeout, match="still finishing"):
        llm_tasks.call_batch_bounded(second, lambda: second.translate_batch(["a"], {}))
    assert second.calls == 0
    # Another engine has its own slots.
    other.release.set()
    assert llm_tasks.call_batch_bounded(other, lambda: other.translate_batch(["a"], {})) == ["x"]
    first.release.set()
    t.join(5)
    assert outcome["first"] == ["x"]
