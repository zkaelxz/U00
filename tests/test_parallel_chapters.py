"""Chapter-parallel novel translation: order inside a chapter, usage and cap, eligibility."""
import threading
import time
from types import SimpleNamespace

import pytest

from engine_backends import translate_pipeline as tp
from engine_backends.fallback import FallbackEngine
from engine_backends.run_limits import TranslateRunStopped

CHAPTERS, PER_CHAPTER = 4, 6


class _Line:
    def __init__(self, idx, zh):
        self.idx, self.id, self.zh, self.en = idx, idx + 100, zh, ""
        self.speaker, self.flag, self.flag_note, self.orig = None, None, "", None
        self.start = self.end = 0.0


class _Engine:
    name = "claude"
    supports_reference = True

    def __init__(self):
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        # Shared by the copies each lane makes, so the peak counts calls across lanes.
        self.lock = threading.Lock()
        self.stats = {"active": 0, "peak": 0}
        self.calls = []

    def translate_batch(self, zh_lines, context):
        with self.lock:
            self.stats["active"] += 1
            self.stats["peak"] = max(self.stats["peak"], self.stats["active"])
        time.sleep(0.03)
        self.calls.append(list(zh_lines))
        self.last_usage = {"input_tokens": 10, "output_tokens": 5}
        with self.lock:
            self.stats["active"] -= 1
        return [f"EN {z}" for z in zh_lines]


def _novel():
    lines = []
    for c in range(CHAPTERS):
        for i in range(PER_CHAPTER):
            lines.append(_Line(len(lines), f"第{c}章" if i == 0 else f"c{c}l{i}"))
    return lines


@pytest.fixture
def novel(monkeypatch):
    monkeypatch.setattr(tp, "novel_paragraph_ends", lambda meta, lines: {ln.idx for ln in lines})
    monkeypatch.setattr(tp, "chapter_start_idxs",
                        lambda lines: {ln.idx for ln in lines if ln.zh.startswith("第")})
    monkeypatch.setattr(tp, "estimate_cost_for_engine", lambda *a, **k: 1.0)
    monkeypatch.setattr(tp, "novel_batch_chars", lambda engine: None)


def _run(engine, parallel, **kw):
    lines = _novel()
    saved = []
    usage = []
    _, errors = tp.translate_lines_with_engine(
        lines, engine, drama_meta={"id": 1}, batch_size=3, parallel_chapters=parallel,
        save_cb=lambda ls: saved.append(sum(1 for ln in ls if ln.en)),
        usage_cb=lambda *u: usage.append(u), **kw)
    return lines, errors, usage, saved


def test_chapters_run_side_by_side_and_every_line_is_translated(novel):
    engine = _Engine()
    lines, errors, usage, _ = _run(engine, 3)
    assert errors == []
    assert all(ln.en == f"EN {ln.zh}" for ln in lines)
    assert 1 < engine.stats["peak"] <= 3
    assert len(usage) == len(engine.calls)


def test_batches_inside_a_chapter_stay_in_order(novel):
    seen = {}
    lock = threading.Lock()
    engine = _Engine()
    original = engine.translate_batch

    def record(zh_lines, context):
        out = original(zh_lines, context)
        with lock:
            seen.setdefault(zh_lines[0][:2], []).append(zh_lines[0])
        return out

    engine.translate_batch = record
    _run(engine, 2)
    for chapter, firsts in seen.items():
        assert firsts == sorted(firsts, key=lambda z: (z[0] != "第", z))


def test_one_chapter_at_a_time_is_the_old_path(novel):
    engine = _Engine()
    _run(engine, 1)
    assert engine.stats["peak"] == 1


def test_cost_cap_is_passed_by_no_more_than_a_one_chapter_run_would(novel):
    engine = _Engine()
    lines, _errors, usage, _ = _run(engine, 3, cost_cap_usd=3.0)
    assert len(usage) == 3
    assert any(not ln.en for ln in lines)


def test_monthly_room_holds_back_batches_beside_those_in_flight(novel):
    engine = _Engine()
    usage_log = []
    lines = _novel()
    tp.translate_lines_with_engine(
        lines, engine, drama_meta={"id": 1}, batch_size=3, parallel_chapters=3,
        usage_cb=lambda *u: usage_log.append(u),
        month_cap_spent_cb=lambda: len(usage_log) >= 3,
        month_cap_left_cb=lambda: 3.0 - len(usage_log))
    assert len(usage_log) == 3


def test_each_lane_has_its_own_rate_limit_budget(novel):
    from engine_backends.run_limits import _rate_limit_budget_var
    budgets = []
    engine = _Engine()
    original = engine.translate_batch

    def record(zh_lines, context):
        budgets.append(id(_rate_limit_budget_var.get()))
        return original(zh_lines, context)

    engine.translate_batch = record
    _run(engine, 3)
    assert len(set(budgets)) == 3


def test_cancel_stops_every_lane(novel):
    engine = _Engine()
    lines, _e, usage, _ = _run(engine, 3, cancel_check_cb=lambda: True)
    assert usage == []


def test_a_lane_failure_is_recorded_with_its_batch_index(novel):
    engine = _Engine()
    original = engine.translate_batch

    def boom(zh_lines, context):
        if zh_lines[0] == "c1l3":
            raise ValueError("bad reply")
        return original(zh_lines, context)

    engine.translate_batch = boom
    lines, errors, _u, _s = _run(engine, 2)
    assert [e["batch_index"] for e in errors] == sorted(e["batch_index"] for e in errors)
    assert errors and "c1l3" not in [ln.en for ln in lines]


@pytest.mark.parametrize("engine,ok", [
    (SimpleNamespace(name="claude"), True),
    (SimpleNamespace(name="gemini", free_tier=False), True),
    (SimpleNamespace(name="gemini", free_tier=True), False),
    (SimpleNamespace(name="ollama", model="translategemma:27b"), False),
    (SimpleNamespace(name="ollama", model="gemma4:31b-cloud"), True),
    (SimpleNamespace(name="ollama"), False),
])
def test_only_paid_cloud_engines_run_in_parallel(engine, ok):
    assert tp.can_run_in_parallel(engine) is ok


def test_a_fallback_chain_is_not_parallel():
    assert tp.can_run_in_parallel(FallbackEngine.__new__(FallbackEngine)) is False


def test_a_lane_saves_only_its_own_batch(novel):
    engine = _Engine()
    saved = []
    lines = _novel()
    tp.translate_lines_with_engine(
        lines, engine, drama_meta={"id": 1}, batch_size=3, parallel_chapters=2,
        save_cb=lambda ls: saved.append(len(ls)))
    assert saved and max(saved) <= 3


def test_many_chapters_do_not_nest_the_stop_check(novel, monkeypatch):
    chapters = 1200
    monkeypatch.setattr(tp, "chapter_start_idxs", lambda ls: {ln.idx for ln in ls if ln.zh.startswith("第")})
    lines = [_Line(i, f"第{i}章") for i in range(chapters)]
    engine = _Engine()
    engine.translate_batch = lambda zh, ctx: [f"EN {z}" for z in zh]
    _, errors = tp.translate_lines_with_engine(
        lines, engine, drama_meta={"id": 1}, batch_size=1, parallel_chapters=2,
        stop_before_batch=lambda: False)
    assert errors == [] and all(ln.en for ln in lines)


def test_lane_threads_get_the_job_thread_scope(novel):
    entered = []

    class Scope:
        def __enter__(self):
            entered.append(1)

        def __exit__(self, *exc):
            return False

    _run(_Engine(), 2, lane_thread_scope=Scope)
    assert len(entered) == 2


def test_a_capped_run_still_runs_side_by_side_after_the_first_batch(novel):
    engine = _Engine()
    _run(engine, 3, cost_cap_usd=100.0)
    assert engine.stats["peak"] > 1


def test_a_stop_in_one_lane_sends_nothing_more_from_the_others(novel):
    engine = _Engine()
    original = engine.translate_batch
    calls = []

    def fail_once(zh_lines, context):
        calls.append(1)
        if len(calls) == 1:
            raise TranslateRunStopped("quota")
        return original(zh_lines, context)

    engine.translate_batch = fail_once
    _run(engine, 3, cost_cap_usd=100.0)
    assert len(calls) <= 3  # only the batches already in flight when it failed


def test_a_batch_that_raises_gives_its_slot_back(novel):
    engine = _Engine()
    original = engine.translate_batch
    state = {"n": 0}

    def flaky(zh_lines, context):
        state["n"] += 1
        if state["n"] == 1:
            raise ValueError("bad reply")
        return original(zh_lines, context)

    engine.translate_batch = flaky
    lines, _errors, _u, _s = _run(engine, 2, cost_cap_usd=100.0)
    assert all(ln.en for ln in lines)


def test_cancel_while_waiting_for_room_ends_the_run(novel):
    cancelled = {"on": False}
    engine = _Engine()
    original = engine.translate_batch

    def slow(zh_lines, context):
        cancelled["on"] = True
        return original(zh_lines, context)

    engine.translate_batch = slow
    lines, _e, _u, _s = _run(engine, 3, cost_cap_usd=100.0, cancel_check_cb=lambda: cancelled["on"])
    assert any(not ln.en for ln in lines)


def test_month_cap_left_reads_the_cap_and_the_spend(monkeypatch):
    from services import settings_service, translate_run_service, workspace_job_service as wjs
    monkeypatch.setattr(translate_run_service, "engine_cap_applies", lambda *a, **k: True)
    monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 4.0)
    engine = SimpleNamespace(model="m")
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 10.0)
    assert wjs._month_cap_left("claude", engine)() == 6.0
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 3.0)
    assert wjs._month_cap_left("claude", engine)() == 0.0
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda: 0)
    assert wjs._month_cap_left("claude", engine)() is None
    monkeypatch.setattr(translate_run_service, "engine_cap_applies", lambda *a, **k: False)
    assert wjs._month_cap_left("claude", engine) is None
