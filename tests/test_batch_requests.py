"""How many requests one translate batch makes, and what stops them. No network."""
import json
from types import SimpleNamespace

import pytest

from engine_backends import translate_pipeline
from core import Line
from engine_backends import llm_tasks, shared
from engine_backends.shared import TranslationCancelled, TruncatedReply

LINES = [f"句{i}" for i in range(1, 9)]


def _ids(numbered):
    return [int(p.split(".", 1)[0]) for p in numbered.splitlines() if p[:1].isdigit()]


def _answer(ids):
    return json.dumps({str(i): f"E{i}" for i in ids})


class TestSiblingBatchesKeepTheirOwnRetry:
    def test_a_missing_line_is_asked_again_when_a_sibling_batch_is_split(self):
        # Line 1-8: reply is cut off after 1-2; the halves [3..5] and [6..8]
        # then answer 3-4 and 6-7 only. 5 and 8 are missing without a cut-off
        # and must be asked for again, not dropped.
        replies = [
            TruncatedReply('{"1":"E1","2":"E2",'),
            _answer([3, 4]), _answer([6, 7]),
            _answer([5, 8]),
        ]
        asked = []

        def call(numbered):
            asked.append(_ids(numbered))
            return replies.pop(0)

        out = shared.request_translations_with_retry(LINES, None, call)
        assert out == [f"E{i}" for i in range(1, 9)]
        assert asked[-1] == [5, 8]

    def test_a_retry_after_a_cut_off_is_smaller_than_the_batch_that_was_cut_off(self):
        sizes = []

        def call(numbered):
            ids = _ids(numbered)
            sizes.append(len(ids))
            if len(sizes) == 1:
                return TruncatedReply('{"1":"E1",')
            return "{}"  # nothing usable, no cut-off

        shared.request_translations_with_retry(LINES, None, call)
        assert sizes[0] == 8
        assert max(sizes[1:]) < 8

    def test_a_fully_cut_off_batch_is_bounded(self):
        calls = []

        def call(numbered):
            calls.append(1)
            return TruncatedReply("")

        out = shared.request_translations_with_retry(LINES, None, call)
        assert out == [""] * 8
        assert len(calls) <= shared.MAX_REQUESTS_PER_BATCH

    def test_the_deadline_covers_the_most_a_batch_can_send(self):
        engine = SimpleNamespace(name="claude")
        assert llm_tasks.batch_deadline_for(engine) >= (
            shared.MAX_REQUESTS_PER_BATCH * llm_tasks._request_timeout_for(engine))


class TestCancelStopsQueuedRequests:
    def test_no_request_is_sent_after_cancel(self):
        calls = []
        cancelled = []

        def call(numbered):
            calls.append(1)
            cancelled.append(True)  # Cancel arrives during the first request
            return TruncatedReply('{"1":"E1",')

        token = shared._cancel_check_var.set(lambda: bool(cancelled))
        try:
            with pytest.raises(TranslationCancelled):
                shared.request_translations_with_retry(LINES, None, call)
        finally:
            shared._cancel_check_var.reset(token)
        assert len(calls) == 1


class _FlakyEngine:
    """Bills one request, then fails the batch with a 429 the first time."""
    name = "flaky"
    model = "m"
    supports_reference = False

    def __init__(self):
        self.attempts = 0
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        self.attempts += 1
        self.last_usage = {"input_tokens": 100, "output_tokens": 10,
                           "cache_read_tokens": 0, "cache_write_tokens": 0}
        if self.attempts == 1:
            err = RuntimeError("429 slow down")
            err.status_code = 429
            raise err
        return [f"E{i}" for i, _ in enumerate(zh_lines)]


def test_usage_billed_by_a_failed_attempt_is_counted(monkeypatch):
    monkeypatch.setattr("engine_backends.shared._cancellable_sleep", lambda s: None)
    seen = []
    engine = _FlakyEngine()
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", id=10 + i) for i in range(2)]
    translate_pipeline.translate_lines_with_engine(
        lines, engine, {}, batch_size=2,
        usage_cb=lambda inp, out, *rest: seen.append((inp, out)))
    assert engine.attempts == 2
    assert seen == [(100, 10), (100, 10)]  # the failed attempt and the one that worked
