"""A translate run stops itself when the engine keeps failing or the provider
keeps rate-limiting, whichever engine it is, and keeps what it already saved."""
import pytest

import translate_engines as te
from core import Line
from engine_backends.fallback import FallbackEngine
from engine_backends.shared import ContentModerationBlocked, TranslationCancelled


class _Http(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


class _Scripted:
    """Answers each batch from a script: an exception instance is raised, anything else is the reply."""
    supports_reference = True
    model = "m"
    name = "scripted"

    def __init__(self, *script, cycle=False):
        self.script = list(script)
        self.cycle = cycle
        self.calls = 0

    def translate_batch(self, zh_lines, context):
        step = self.script[min(self.calls, len(self.script) - 1)] if not self.cycle else self.script[self.calls % len(self.script)]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return [f"en {z}" for z in zh_lines]


def _lines(n=12):
    return [Line(idx=i, start=i, end=i + 1, zh=f"zh{i}", en="", id=10 + i) for i in range(n)]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _run(engine, lines=None, **kw):
    lines = lines or _lines()
    saved = []
    out, errors = te.translate_lines_with_engine(
        lines, engine, {}, batch_size=1, check_replies=False,
        save_cb=lambda ls: saved.append([ln.en for ln in ls]), **kw)
    return out, errors, saved


class TestFailureStreak:
    def test_three_failed_batches_in_a_row_stop_the_run(self):
        engine = _Scripted(RuntimeError("boom one"))
        _, errors, _ = _run(engine)
        assert engine.calls == 3 * 2  # each batch gets one quick retry
        assert "3 batches in a row failed" in errors[-1]["error"]
        assert "boom one" in errors[-1]["error"]

    def test_a_success_resets_the_streak(self):
        # Two failed batches (each tried twice), then a success, again and again.
        boom = RuntimeError("x")
        engine = _Scripted(boom, boom, boom, boom, "ok", cycle=True)
        out, errors, _ = _run(engine, _lines(6))
        assert not any("in a row" in e["error"] for e in errors)
        assert engine.calls == 10 and [bool(ln.en) for ln in out] == [False, False, True] * 2

    def test_lines_saved_before_the_stop_are_kept(self):
        engine = _Scripted("ok", RuntimeError("down"))
        out, errors, saved = _run(engine)
        assert out[0].en == "en zh0"
        assert saved[-1][0] == "en zh0"
        assert all(ln.en == "" for ln in out[1:])

    def test_moderation_blocks_are_not_counted(self):
        engine = _Scripted(ContentModerationBlocked("claude", "policy"))
        out, errors, _ = _run(engine, _lines(6))
        assert [ln.flag for ln in out] == ["content_blocked"] * 6
        assert not any("in a row" in e["error"] for e in errors)

    def test_a_cancelled_batch_is_not_counted(self):
        _, errors, _ = _run(_Scripted(TranslationCancelled("cancelled")), _lines(6))
        assert not any("in a row" in e["error"] for e in errors)

    def test_the_reason_names_the_error_without_url_key_or_path(self):
        msg = ("HTTP 500 from https://api.example.com/v1/x?key=sk-ant-api03-abcdefghijklmnop123 "
               "reading /home/mai.tran/secret.txt")
        engine = _Scripted(RuntimeError(msg))
        _, errors, _ = _run(engine)
        text = errors[-1]["error"]
        assert "in a row" in text and "HTTP 500" in text
        for leak in ("https://", "api.example.com", "sk-ant", "/home/mai.tran"):
            assert leak not in text

    def test_a_fallback_chain_stops_only_when_every_engine_failed(self):
        first = _Scripted(_Http("auth", 401))
        second = _Scripted("ok")
        chain = FallbackEngine([first, second], ["a", "b"])
        out, errors, _ = _run(chain, _lines(6))
        assert all(ln.en for ln in out)
        assert not errors


class TestRateLimitBudget:
    def test_a_run_stops_when_backoff_waiting_passes_the_budget(self):
        slept = []
        # Every batch waits 30 s before it gets through, so no batch fails
        # and the failure streak never fires.
        slow = _Http("429 slow down", 429)
        engine = _Scripted(slow, slow, slow, slow, "ok", cycle=True)
        import engine_backends.shared as sh
        orig = sh._cancellable_sleep
        sh._cancellable_sleep = lambda s: slept.append(s)
        try:
            out, errors, _ = _run(engine, _lines(40))
        finally:
            sh._cancellable_sleep = orig
        assert sum(slept) <= 600
        assert "keeps rate-limiting you" in errors[-1]["error"]
        assert "switch engine" in errors[-1]["error"]
        assert 0 < sum(bool(ln.en) for ln in out) < 40
        assert out[0].en == "en zh0"

    def test_without_a_run_budget_backoff_is_unchanged(self):
        slept = []
        import engine_backends.shared as sh
        orig = sh._cancellable_sleep
        sh._cancellable_sleep = lambda s: slept.append(s)
        try:
            n = {"c": 0}

            def fn():
                n["c"] += 1
                if n["c"] < 3:
                    raise _Http("429", 429)
                return "ok"
            assert sh.call_with_backoff(fn) == "ok"
        finally:
            sh._cancellable_sleep = orig
        # The exponential 2 s, 4 s, each up to a quarter longer (jitter).
        assert 2.0 <= slept[0] <= 2.5 and 4.0 <= slept[1] <= 5.0 and len(slept) == 2
