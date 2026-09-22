"""
tests/test_translate_engines.py -- tests for translate_engines.py's
core logic: rate-limit backoff vs. fast-fail, crash-safe/resumable
batch translation, locale + glossary prompt construction, and cost
estimation. Uses mock engines instead of real API calls.
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import translate_engines as te
from core import Line


class RateLimitError(Exception):
    status_code = 429


class GenericError(Exception):
    pass


class TestCallWithBackoff:
    def test_succeeds_immediately_when_no_error(self):
        result = te.call_with_backoff(lambda: "ok")
        assert result == "ok"

    def test_rate_limit_retries_and_eventually_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RateLimitError("429")
            return "success"

        result = te.call_with_backoff(flaky, base_delay=0.01, max_delay=0.05)
        assert result == "success"
        assert calls["n"] == 3

    def test_generic_error_fails_fast_after_one_retry(self):
        calls = {"n": 0}

        def always_broken():
            calls["n"] += 1
            raise GenericError("bad api key")

        start = time.time()
        try:
            te.call_with_backoff(always_broken, base_delay=0.01)
            assert False, "should have raised"
        except GenericError:
            pass
        elapsed = time.time() - start
        assert calls["n"] == 2  # one try + one quick retry, not 5 backoff attempts
        assert elapsed < 3  # should not have waited through exponential backoff

    def test_is_rate_limit_error_detects_status_code(self):
        assert te._is_rate_limit_error(RateLimitError("x")) is True

    def test_is_rate_limit_error_rejects_generic_errors(self):
        assert te._is_rate_limit_error(GenericError("x")) is False

    def test_is_rate_limit_error_detects_429_in_message(self):
        assert te._is_rate_limit_error(Exception("HTTP 429 Too Many Requests")) is True


class MockEngine:
    """A fake engine that translates by prefixing 'EN:', for testing the
    batching/retry/resume logic without a real API.

    fail_on_zh_containing: if set, any translate_batch call where a
    line's zh text contains this substring always fails (both the
    original attempt and call_with_backoff's one built-in retry) --
    simulating a persistently broken batch, as opposed to a one-off
    transient blip that a retry would paper over. Using content rather
    than a call counter to decide when to fail keeps this correct even
    though call_with_backoff may call translate_batch more than once
    per logical batch."""
    supports_reference = False
    name = "mock"

    def __init__(self, fail_on_zh_containing=None):
        self.fail_on_zh_containing = fail_on_zh_containing
        self.call_count = 0

    def translate_batch(self, zh_lines, context):
        self.call_count += 1
        if self.fail_on_zh_containing and any(self.fail_on_zh_containing in z for z in zh_lines):
            raise GenericError("simulated persistent failure")
        return [f"EN:{z}" for z in zh_lines]


class TestTranslateLinesWithEngine:
    def test_translates_all_lines_on_success(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(5)]
        result, errors = te.translate_lines_with_engine(lines, MockEngine(), {}, batch_size=2)
        assert errors == []
        assert all(ln.en for ln in result)

    def test_skips_already_translated_lines_by_default(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="already done"),
                 Line(idx=1, start=0, end=1, zh="b")]
        engine = MockEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10)
        assert lines[0].en == "already done"  # untouched
        assert lines[1].en == "EN:b"

    def test_force_retranslate_redoes_everything(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="old translation")]
        engine = MockEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10, force_retranslate=True)
        assert lines[0].en == "EN:a"  # overwritten

    def test_no_lines_need_translation_returns_immediately(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="done")]
        progress_calls = []
        result, errors = te.translate_lines_with_engine(
            lines, MockEngine(), {}, progress_cb=lambda f: progress_calls.append(f))
        assert errors == []
        assert progress_calls == [1.0]  # signals immediate completion

    def test_save_cb_called_after_every_batch(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        save_calls = []
        te.translate_lines_with_engine(
            lines, MockEngine(), {}, batch_size=2,
            save_cb=lambda ls: save_calls.append(sum(1 for l in ls if l.en)))
        assert save_calls == [2, 4, 6]  # incremental progress after each batch

    def test_batch_failure_is_isolated_others_still_translate(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        # batch_size=2 groups lines into (l0,l1), (l2,l3), (l4,l5) --
        # make the second batch persistently fail via its content
        engine = MockEngine(fail_on_zh_containing="l2")
        result, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        translated_count = sum(1 for l in result if l.en)
        assert translated_count == 4  # 2 of 3 batches succeeded (the l2/l3 batch failed)
        assert len(errors) == 1

    def test_resuming_after_partial_failure_only_retries_missing_lines(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine1 = MockEngine(fail_on_zh_containing="l2")  # second batch (l2,l3) fails
        te.translate_lines_with_engine(lines, engine1, {}, batch_size=2)
        untranslated_before = [l for l in lines if not l.en]
        assert len(untranslated_before) == 2

        engine2 = MockEngine()  # fresh engine, no failures this time
        te.translate_lines_with_engine(lines, engine2, {}, batch_size=2)
        assert all(l.en for l in lines)
        # engine2 should only have been called once (just the missing 2 lines,
        # not re-translating the 2 that already succeeded)
        assert engine2.call_count == 1


class TestBuildLlmInstructions:
    def test_locale_us_default(self):
        instructions, _ = te.build_llm_instructions("", {}, None)
        assert "American English" in instructions

    def test_locale_gb_uses_british_spelling_note(self):
        instructions, _ = te.build_llm_instructions("", {}, None, locale="en-GB")
        assert "British English" in instructions
        assert "colour" in instructions

    def test_glossary_terms_included(self):
        glossary = [{"term_original": "拾", "term_translation": "Shi", "notes": "surname"}]
        instructions, _ = te.build_llm_instructions("", {}, None, glossary_terms=glossary)
        assert "拾 -> Shi" in instructions
        assert "surname" in instructions

    def test_no_glossary_omits_glossary_section(self):
        instructions, _ = te.build_llm_instructions("", {}, None, glossary_terms=None)
        assert "Series glossary" not in instructions

    def test_style_note_included_when_provided(self):
        instructions, _ = te.build_llm_instructions("keep it warm", {}, None)
        assert "keep it warm" in instructions

    def test_drama_metadata_included(self):
        instructions, meta_block = te.build_llm_instructions(
            "", {"title_en": "My Drama", "author": "An Author"}, None)
        assert "My Drama" in instructions
        assert "An Author" in instructions


class TestEstimateCost:
    def test_known_model_computes_nonzero_cost(self):
        cost = te.estimate_cost("claude-sonnet-5", 1_000_000, 500_000)
        assert cost > 0

    def test_unknown_model_returns_zero_not_crash(self):
        cost = te.estimate_cost("some-made-up-model-xyz", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens_zero_cost(self):
        assert te.estimate_cost("claude-sonnet-5", 0, 0) == 0.0

    def test_cost_scales_with_token_count(self):
        small = te.estimate_cost("claude-sonnet-5", 1000, 500)
        large = te.estimate_cost("claude-sonnet-5", 10000, 5000)
        assert large > small
        assert abs(large - small * 10) < 0.0001  # linear scaling


class TestCheckConsistencyLlm:
    def test_pure_mt_engine_returns_empty_list_not_crash(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        result = te.check_consistency_llm(lines, PureMT())
        assert result == []

    def test_no_translated_lines_returns_empty(self):
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]  # untranslated
        result = te.check_consistency_llm(lines, MockLlm())
        assert result == []


class TestOfflineTestEngine:
    """The free dry-run engine exists so the pipeline can be validated with
    no API key, no network, and no spend. These guard that promise."""

    def test_registered_as_an_engine(self):
        assert "test_offline" in te.ENGINES

    def test_works_with_no_api_key(self):
        engine = te.get_engine("test_offline")
        assert engine is not None
        engine2 = te.get_engine("test_offline", None)
        assert engine2 is not None

    def test_translates_every_line_without_network(self):
        lines = [Line(idx=i, start=float(i), end=float(i) + 1, zh=f"第{i}句") for i in range(6)]
        engine = te.get_engine("test_offline")
        _, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert errors == []
        assert all(ln.en for ln in lines)

    def test_output_is_obviously_placeholder(self):
        # Must never be mistakable for a real translation.
        lines = [Line(idx=0, start=0, end=1, zh="真实对白")]
        engine = te.get_engine("test_offline")
        te.translate_lines_with_engine(lines, engine, {})
        assert lines[0].en.startswith("[TEST]")

    def test_costs_nothing(self):
        assert te.estimate_cost("test-offline", 10_000_000, 10_000_000) == 0.0

    def test_records_usage_so_dashboard_path_is_exercised(self):
        lines = [Line(idx=0, start=0, end=1, zh="测试")]
        engine = te.get_engine("test_offline")
        te.translate_lines_with_engine(lines, engine, {})
        assert engine.last_usage["input_tokens"] > 0

    def test_long_lines_are_truncated_in_placeholder(self):
        lines = [Line(idx=0, start=0, end=1, zh="字" * 200)]
        engine = te.get_engine("test_offline")
        te.translate_lines_with_engine(lines, engine, {})
        assert len(lines[0].en) < 100

    def test_has_no_sdk_client_so_llm_features_decline_cleanly(self):
        # Free-form LLM helpers check for .client; None must not crash them.
        engine = te.get_engine("test_offline")
        assert engine.client is None
        assert te.check_consistency_llm([Line(idx=0, start=0, end=1, zh="a", en="b")], engine) == []


class TestResumeAfterCrash:
    """End-to-end resume behaviour: a run that dies partway through should
    leave completed work saved, and restarting should translate only what's
    still missing rather than redoing (and re-paying for) everything."""

    def test_partial_progress_is_saved_when_a_batch_fails(self):
        class DropsOnContent(Exception):
            pass

        class FlakyEngine:
            supports_reference = False
            def translate_batch(self, zh_lines, context):
                if any("line4" in z or "line5" in z for z in zh_lines):
                    raise DropsOnContent("connection dropped")
                return [f"EN:{z}" for z in zh_lines]

        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(10)]
        te.translate_lines_with_engine(lines, FlakyEngine(), {}, batch_size=2)
        done = sum(1 for l in lines if l.en)
        assert done == 8

    def test_restarting_only_retranslates_the_missing_lines(self):
        class DropsOnContent(Exception):
            pass

        class FlakyEngine:
            supports_reference = False
            def translate_batch(self, zh_lines, context):
                if any("line4" in z or "line5" in z for z in zh_lines):
                    raise DropsOnContent("connection dropped")
                return [f"EN:{z}" for z in zh_lines]

        class FreshEngine:
            supports_reference = False
            def __init__(self):
                self.calls = 0
            def translate_batch(self, zh_lines, context):
                self.calls += 1
                return [f"EN:{z}" for z in zh_lines]

        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(10)]
        te.translate_lines_with_engine(lines, FlakyEngine(), {}, batch_size=2)

        fresh = FreshEngine()
        _, errors = te.translate_lines_with_engine(lines, fresh, {}, batch_size=2)

        assert all(l.en for l in lines)
        assert errors == []
        assert fresh.calls == 1  # only the one missing batch, not all five
