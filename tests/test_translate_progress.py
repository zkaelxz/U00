"""Progress messages and fractions of translate_lines_with_engine (normal and
Reflect mode), the retry-wait notice, and backward compatibility of the
single-argument progress_cb."""
import json

import pytest

import translate_engines as te
from core import Line


class _Rate(Exception):
    status_code = 429


class _Plain:
    supports_reference = True
    model = "m"

    def translate_batch(self, zh_lines, context):
        return [f"en {z}" for z in zh_lines]


class _Reflect:
    """Answers every pass with an id-keyed JSON for ids 1..n of the prompt's batch."""
    supports_reference = True
    model = "m"

    def __init__(self, fail_first=None):
        self.client = self
        self.messages = self
        self.calls = 0
        self.fail_first = fail_first

    def create(self, model, max_tokens, messages):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise self.fail_first
        n = len(self.last_zh)
        block = type("B", (), {"type": "text", "text": json.dumps(
            {str(i): f"t{i}" for i in range(1, n + 1)})})()
        return type("R", (), {"content": [block]})()


def _lines(n):
    return [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(n)]


def _run(engine, n=5, batch_size=2, **kw):
    events = []
    lines = _lines(n)
    if isinstance(engine, _Reflect):
        engine.last_zh = ["x", "y"][:batch_size]
    te.translate_lines_with_engine(
        lines, engine, {}, batch_size=batch_size,
        detail_cb=lambda f, m: events.append((f, m)), **kw)
    return lines, events


def _assert_sane(events):
    fracs = [f for f, _ in events]
    assert fracs == sorted(fracs)
    assert max(fracs) == 1.0 and all(0.0 <= f <= 1.0 for f in fracs)


def test_normal_mode_messages_and_fractions():
    _, events = _run(_Plain())
    _assert_sane(events)
    msgs = [m for _, m in events]
    assert msgs[0] == "Batch 1 of 3"
    assert "Batch 3 of 3" in msgs and msgs[-1] == "Batch 3 of 3 done"
    assert events[-1][0] == 1.0


def test_reflect_mode_reports_each_pass(monkeypatch):
    # Both batches hold two lines, so the engine's fixed answer covers each.
    _, events = _run(_Reflect(), n=4, reflect=True)
    _assert_sane(events)
    msgs = [m for _, m in events]
    assert "Batch 1 of 2, pass 1 of 3 (draft)" in msgs
    assert "Batch 1 of 2, pass 2 of 3 (critique)" in msgs
    assert "Batch 2 of 2, pass 3 of 3 (rewrite)" in msgs
    pass2 = next(f for f, m in events if "pass 2 of 3" in m and "Batch 1" in m)
    assert pass2 == pytest.approx((0 + 1 / 3) / 2)


def test_old_single_argument_progress_cb_still_works():
    seen = []
    te.translate_lines_with_engine(_lines(5), _Plain(), {}, batch_size=2, progress_cb=seen.append)
    assert seen == [pytest.approx(1 / 3), pytest.approx(2 / 3), 1.0]


def test_nothing_to_translate_reports_done():
    _, events = _run(_Plain(), n=0)
    assert events == [(1.0, "Nothing to translate")]


def test_backoff_hook_reports_wait_without_error_text(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    secret = "sk-ABCDEFGHIJKLMNOP1234"
    engine = _Reflect(fail_first=_Rate(f"429 https://x.test/v1?key={secret}"))
    _, events = _run(engine, n=2, reflect=True)
    waits = [m for _, m in events if "Engine busy" in m]
    assert waits and "waiting 2 s to retry (attempt 2 of 5)" in waits[0]
    assert all(secret not in m and "http" not in m for _, m in events)
    _assert_sane(events)


def test_call_with_backoff_on_wait_receives_numbers_only(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    got, state = [], {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise _Rate("429")
        return "ok"

    assert te.call_with_backoff(flaky, on_wait=lambda *a: got.append(a)) == "ok"
    assert got == [(2.0, 2, 5), (4.0, 3, 5)]


def test_cancelled_run_stops_without_done_message():
    cancelled = {"v": False}
    events = []

    class Engine(_Plain):
        def translate_batch(self, zh_lines, context):
            cancelled["v"] = True   # cancel arrives while batch 1 runs
            return super().translate_batch(zh_lines, context)

    te.translate_lines_with_engine(
        _lines(6), Engine(), {}, batch_size=2, cancel_check_cb=lambda: cancelled["v"],
        detail_cb=lambda f, m: events.append((f, m)))
    msgs = [m for _, m in events]
    assert msgs == ["Batch 1 of 3"]
    assert not any(m.endswith("done") for m in msgs)
