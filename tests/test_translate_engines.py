"""
tests/test_translate_engines.py -- tests for translate_engines.py's
core logic: rate-limit backoff vs. fast-fail, crash-safe/resumable
batch translation, locale + glossary prompt construction, and cost
estimation. Uses mock engines instead of real API calls.
"""

import sys
import os
import time
import inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import translate_engines as te
from core import Line
from tests import fake_engine


class RateLimitError(Exception):
    status_code = 429


class GenericError(Exception):
    pass


class TestBackoffCancelAndDeadlines:
    def _sleeps(self, monkeypatch):
        slept = []
        monkeypatch.setattr("time.sleep", lambda sec: slept.append(sec))
        return slept

    def test_backoff_sleep_stops_when_the_job_is_cancelled(self, monkeypatch):
        slept = self._sleeps(monkeypatch)
        cancelled = {"v": False}
        calls = {"n": 0}

        def always_429():
            calls["n"] += 1
            raise RateLimitError("429")

        def check():
            return cancelled["v"] and True

        token = te._cancel_check_var.set(check)
        try:
            monkeypatch.setattr("time.sleep", lambda sec: (slept.append(sec),
                                                           cancelled.__setitem__("v", True)))
            with pytest.raises(te.TranslationCancelled):
                te.call_with_backoff(always_429, base_delay=30.0)
        finally:
            te._cancel_check_var.reset(token)
        assert calls["n"] == 1
        assert max(slept) <= te._SLEEP_SLICE_SECONDS  # never one long sleep

    def test_translate_run_passes_its_cancel_check_to_the_backoff(self, monkeypatch):
        self._sleeps(monkeypatch)
        cancelled = {"v": False}

        class Engine:
            def translate_batch(self, zh, ctx):
                cancelled["v"] = True
                raise RateLimitError("429")

        lines = [Line(idx=0, start=0, end=1, zh="a")]
        _, errors = te.translate_lines_with_engine(
            lines, Engine(), {}, cancel_check_cb=lambda: cancelled["v"])
        assert len(errors) == 1 and lines[0].en == ""

    def test_429_text_match_ignores_other_numbers_and_known_statuses(self):
        assert te._is_rate_limit_error(Exception("HTTP 429 Too Many Requests"))
        assert not te._is_rate_limit_error(Exception("failed at line 14290"))

        class Other(Exception):
            status_code = 500
        assert not te._is_rate_limit_error(Other("upstream said 429 somewhere"))

    def test_exhausted_fallback_chain_is_not_backed_off_again(self, monkeypatch):
        slept = self._sleeps(monkeypatch)
        monkeypatch.setattr(te, "_fallback_sleep", lambda s: None)
        calls = {"n": 0}

        class Always429:
            name = "x"
            model = "m"

            def translate_batch(self, zh, ctx):
                calls["n"] += 1
                raise RateLimitError("429")

        chain = te.FallbackEngine([Always429()], ["x"])
        with pytest.raises(RateLimitError):
            te.call_with_backoff(lambda: chain.translate_batch(["a"], {}))
        assert calls["n"] == 1 + te.FALLBACK_TRANSIENT_RETRIES  # not 6x that
        assert slept == []

    def test_daily_limit_stops_the_run_with_a_clear_error(self, monkeypatch):
        self._sleeps(monkeypatch)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = te.gemini_free_tier_limits_for(engine.model)["rpd"]
        now = time.monotonic()
        engine._free_tier_daily_request_times = [now] * limit
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        _, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert len(errors) == 1  # stopped after the first batch
        assert "daily request limit" in errors[0]["error"]


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

    def test_source_language_from_drama_meta_reaches_the_engine(self):
        """Regression test for a real bug: engines used
        to hardcode source_language="zh" regardless of the drama's actual
        source, so a Japanese/Korean drama silently mistranslated through
        either. context["source_language"] must reflect drama_meta."""
        seen_context = {}

        class RecordingEngine:
            supports_reference = False

            def translate_batch(self, zh_lines, context):
                seen_context.update(context)
                return [f"EN:{t}" for t in zh_lines]

        lines = [Line(idx=0, start=0, end=1, zh="a")]
        te.translate_lines_with_engine(lines, RecordingEngine(), {"source_language": "ja"})
        assert seen_context["source_language"] == "ja"

    def test_source_language_defaults_to_zh_when_missing(self):
        seen_context = {}

        class RecordingEngine:
            supports_reference = False

            def translate_batch(self, zh_lines, context):
                seen_context.update(context)
                return [f"EN:{t}" for t in zh_lines]

        lines = [Line(idx=0, start=0, end=1, zh="a")]
        te.translate_lines_with_engine(lines, RecordingEngine(), {})
        assert seen_context["source_language"] == "zh"

    def test_content_moderation_block_bisects_and_isolates_the_blocked_line(self, monkeypatch):
        """Step 31 item 3: a ContentModerationBlocked on the whole batch
        retries once by bisecting into two halves rather than leaving
        every line in the batch blank. With a 2-line batch this bisects
        all the way down to single lines, so the one that actually
        triggers the block ends up flagged alone while its batch-mate --
        genuinely fine, just unlucky enough to share a batch -- still
        gets translated."""
        monkeypatch.setattr("time.sleep", lambda *_: None)  # skip call_with_backoff's retry delay

        class BlockedOnContentEngine:
            supports_reference = False
            name = "blocktest"

            def __init__(self):
                self.calls = []

            def translate_batch(self, zh_lines, context):
                self.calls.append(list(zh_lines))
                if any("BLOCKED" in z for z in zh_lines):
                    raise te.ContentModerationBlocked("blocktest", "simulated safety block")
                return [f"EN:{z}" for z in zh_lines]

        lines = [Line(idx=0, start=0, end=1, zh="a BLOCKED passage"),
                 Line(idx=1, start=0, end=1, zh="a perfectly normal line")]
        engine = BlockedOnContentEngine()
        result, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)

        blocked_line, clean_line = result[0], result[1]
        assert blocked_line.flag == "content_blocked"
        assert "blocktest" in blocked_line.flag_note
        assert "simulated safety block" in blocked_line.flag_note
        assert not blocked_line.en
        assert clean_line.en == "EN:a perfectly normal line"  # not blanked out by the other line's block
        assert clean_line.flag is None
        assert len(errors) == 1
        # whole batch, then each bisected half -- narrowed down, not a full
        # per-line search on every batch. Each raising attempt appears
        # twice: call_with_backoff gives any non-rate-limit exception one
        # extra retry before letting it propagate.
        assert engine.calls == [
            ["a BLOCKED passage", "a perfectly normal line"],
            ["a BLOCKED passage", "a perfectly normal line"],
            ["a BLOCKED passage"],
            ["a BLOCKED passage"],
            ["a perfectly normal line"],
        ]


class TestBuildLlmInstructions:
    def test_locale_us_default(self):
        instructions = te.build_llm_instructions("", {})
        assert "American English" in instructions

    def test_locale_gb_uses_british_spelling_note(self):
        instructions = te.build_llm_instructions("", {}, locale="en-GB")
        assert "British English" in instructions
        assert "colour" in instructions

    def test_glossary_terms_included(self):
        glossary = [{"term_original": "拾", "term_translation": "Shi", "notes": "surname"}]
        instructions = te.build_llm_instructions("", {}, glossary_terms=glossary)
        assert "拾 -> Shi" in instructions
        assert "surname" in instructions

    def test_no_glossary_omits_glossary_section(self):
        instructions = te.build_llm_instructions("", {}, glossary_terms=None)
        assert "Series glossary" not in instructions

    def test_style_note_included_when_provided(self):
        instructions = te.build_llm_instructions("keep it warm", {})
        assert "keep it warm" in instructions

    def test_drama_metadata_included(self):
        instructions = te.build_llm_instructions(
            "", {"title_en": "My Drama", "author": "An Author"})
        assert "My Drama" in instructions
        assert "An Author" in instructions

    def test_defaults_to_chinese_when_source_language_unset(self):
        instructions = te.build_llm_instructions("", {})
        assert "Chinese baihe" in instructions

    def test_japanese_source_language_reaches_the_prompt(self):
        """Regression test for a real bug found during audit: the system
        prompt used to hardcode "Chinese baihe" regardless of the drama's
        actual source language, so a Japanese or Korean drama's own
        translator was told it was translating Chinese the whole time."""
        instructions = te.build_llm_instructions("", {"source_language": "ja"})
        assert "Japanese baihe" in instructions
        assert "Chinese baihe" not in instructions

    def test_korean_source_language_reaches_the_prompt(self):
        instructions = te.build_llm_instructions("", {"source_language": "ko"})
        assert "Korean baihe" in instructions

    def test_content_mode_reaches_the_prompt(self):
        instructions = te.build_llm_instructions(
            "", {"source_language": "zh", "content_mode": "novel_narration"})
        assert "novel" in instructions

    def test_streamer_vod_content_mode_reaches_the_prompt(self):
        instructions = te.build_llm_instructions(
            "", {"source_language": "ja", "content_mode": "streamer_vod"})
        assert "livestream VOD" in instructions
        assert "Japanese baihe" in instructions

    def test_baihe_tagged_genre_keeps_the_baihe_framing(self):
        instructions = te.build_llm_instructions("", {"genre": "baihe"})
        assert "baihe (GL/yuri)" in instructions

    def test_yuri_tagged_genre_keeps_the_baihe_framing(self):
        instructions = te.build_llm_instructions("", {"genre": "Yuri, slow burn"})
        assert "baihe (GL/yuri)" in instructions

    def test_a_different_genre_drops_the_baihe_framing(self):
        """Step 54: this exact framing used to be hardcoded into every
        translation prompt regardless of the project's actual content --
        a VTuber stream or a historical drama with no romantic content at
        all still got told it was baihe/yuri, risking an unwarranted
        romantic-interpretation bias."""
        instructions = te.build_llm_instructions("", {"genre": "historical"})
        assert "baihe (GL/yuri)" not in instructions
        assert "You are translating Chinese content" in instructions

    def test_a_genre_that_merely_contains_gl_as_a_substring_is_not_a_false_positive(self):
        instructions = te.build_llm_instructions("", {"genre": "tangled romance"})
        assert "baihe (GL/yuri)" not in instructions

    def test_upcoming_lines_included_when_provided(self):
        block = te.build_batch_context(upcoming_lines=["下一句话"])
        assert "下一句话" in block
        assert "AFTER this batch" in block

    def test_no_upcoming_lines_omits_the_section(self):
        assert "AFTER this batch" not in te.build_batch_context(upcoming_lines=None)

    def test_prompt_mentions_speaker_bracket_convention(self):
        # The model needs to be told what the [Name] prefix means and
        # that it shouldn't leak into the translation -- not just have
        # names silently appear in the numbered lines with no explanation.
        instructions = te.build_llm_instructions("", {})
        assert "[" in instructions and "speaking" in instructions.lower()

    def test_returns_object_shaped_json_instruction_not_array(self):
        """Regression test for the real zip()-misalignment bug: the old
        prompt asked for a bare JSON array (position-trust); the fix asks
        for an id-keyed object so a missing/extra/reordered response
        entry can only ever affect its own line."""
        instructions = te.build_llm_instructions("", {})
        assert "JSON object" in instructions
        assert "JSON array of strings" not in instructions

    def test_signature_has_no_unused_novel_reference_param(self):
        """Step 63: novel_reference used to be accepted but never read
        inside the function body -- the reference novel only ever
        reaches the prompt via build_stable_prompt()'s own novel_block."""
        params = list(inspect.signature(te.build_llm_instructions).parameters)
        assert "novel_reference" not in params

    def test_returns_a_single_string_not_a_tuple(self):
        """Step 63: the second return value (meta_block) was redundant --
        its content is already folded into the returned instructions
        string, and the one caller (build_stable_prompt) discarded it."""
        result = te.build_llm_instructions("", {})
        assert isinstance(result, str)

    def test_previous_episode_summary_reaches_the_prompt(self):
        """Step 74: the immediately preceding episode's stored running
        summary (db._DRAMA_SELECT's previous_episode_summary) reaches the
        stable translation prompt as fixed continuity context."""
        instructions = te.build_llm_instructions(
            "", {"previous_episode_summary": "Xiaoling found the letter in episode 1."})
        assert "Xiaoling found the letter in episode 1." in instructions

    def test_no_previous_episode_summary_omits_the_section(self):
        instructions = te.build_llm_instructions("", {})
        assert "Continuity from the previous episode" not in instructions


class TestBuildPreviousEpisodeSummaryBlock:
    def test_empty_when_unset(self):
        assert te.build_previous_episode_summary_block({}) == ""

    def test_empty_when_blank(self):
        assert te.build_previous_episode_summary_block({"previous_episode_summary": "   "}) == ""

    def test_includes_the_summary_text(self):
        block = te.build_previous_episode_summary_block(
            {"previous_episode_summary": "Wei confessed her secret."})
        assert "Wei confessed her secret." in block


class TestEstimateCost:
    def test_known_model_computes_nonzero_cost(self):
        cost = te.estimate_cost("claude-sonnet-5", 1_000_000, 500_000)
        assert cost > 0

    def test_unknown_model_returns_zero_not_crash(self):
        cost = te.estimate_cost("some-made-up-model-xyz", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens_zero_cost(self):
        assert te.estimate_cost("claude-sonnet-5", 0, 0) == 0.0

    def test_gemini_models_have_pricing(self):
        for model in te.GEMINI_MODELS:
            cost = te.estimate_cost(model, 1_000_000, 1_000_000)
            assert cost > 0, f"{model} should have real pricing, not silently cost $0"

    def test_cost_scales_with_token_count(self):
        small = te.estimate_cost("claude-sonnet-5", 1000, 500)
        large = te.estimate_cost("claude-sonnet-5", 10000, 5000)
        assert large > small
        assert abs(large - small * 10) < 0.0001  # linear scaling


class TestRecentContextInPrompt:
    def test_recent_context_included_when_provided(self):
        block = te.build_batch_context(recent_context=[("她昨天来了", "She came yesterday.")])
        assert "她昨天来了 -> She came yesterday." in block
        assert "continuity" in block.lower()

    def test_no_recent_context_omits_section(self):
        assert "continuity" not in te.build_batch_context(recent_context=None).lower()

    def test_empty_list_also_omits_section(self):
        assert te.build_batch_context(recent_context=[]) == ""


class ContextCapturingEngine:
    """Records the `context` dict it was called with on every batch, so
    tests can inspect exactly what recent_context looked like at each
    point in a multi-batch run."""
    supports_reference = True
    name = "context_capture"

    def __init__(self):
        self.calls = []

    def translate_batch(self, zh_lines, context):
        self.calls.append({"zh_lines": list(zh_lines),
                            "recent_context": list(context.get("recent_context") or []),
                            "upcoming_lines": list(context.get("upcoming_lines") or []),
                            "speaker_labels": list(context.get("speaker_labels") or [])})
        return [f"EN:{z}" for z in zh_lines]


class TestContextWindow:
    def test_first_batch_has_no_recent_context(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=6)
        assert engine.calls[0]["recent_context"] == []

    def test_later_batch_sees_earlier_translated_lines_as_context(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=6)
        # Second batch (l2, l3) should see the first batch's translations as context.
        assert engine.calls[1]["recent_context"] == [("l0", "EN:l0"), ("l1", "EN:l1")]

    def test_context_window_caps_how_far_back_it_looks(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=1)
        # Third batch (l4, l5) should only see the single immediately-preceding line.
        assert engine.calls[2]["recent_context"] == [("l3", "EN:l3")]

    def test_zero_disables_context_entirely(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=0)
        assert all(call["recent_context"] == [] for call in engine.calls)

    def test_already_translated_lines_before_the_run_are_used_as_context_too(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0", en="Pre-existing translation."),
                 Line(idx=1, start=0, end=1, zh="l1")]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10, context_window=6)
        assert engine.calls[0]["recent_context"] == [("l0", "Pre-existing translation.")]

    def test_existing_mock_engine_tests_unaffected_by_new_default(self):
        """context_window now defaults to 6 -- confirms that doesn't break
        an engine that ignores the context dict's extra keys entirely."""
        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(5)]
        result, errors = te.translate_lines_with_engine(lines, MockEngine(), {}, batch_size=2)
        assert errors == []
        assert all(ln.en for ln in result)


class TestLookaheadContext:
    def test_last_batch_has_no_upcoming_lines(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=3)
        assert engine.calls[1]["upcoming_lines"] == []

    def test_earlier_batch_sees_the_next_lines_raw_text(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=3)
        # First batch (l0, l1) should see l2, l3 as raw upcoming text.
        assert engine.calls[0]["upcoming_lines"] == ["l2", "l3"]

    def test_lookahead_is_capped_by_context_window_ahead(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=1)
        assert engine.calls[0]["upcoming_lines"] == ["l2"]

    def test_zero_disables_lookahead(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=0)
        assert all(call["upcoming_lines"] == [] for call in engine.calls)

    def test_upcoming_excludes_lines_already_in_a_noncontiguous_batch(self):
        """Regression test for a real bug: when force_retranslate=False
        (the default) skips an already-translated line in the middle,
        the batch is no longer a contiguous slice of `lines` -- the old
        `last_pos = first_pos + len(batch) - 1` arithmetic assumed it
        always was, and could land back inside the batch itself. That
        showed the model one of ITS OWN lines as "upcoming" (context
        only, don't translate this) while asking it to translate that
        very line right now in the same batch."""
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(5)]
        lines[1].en = "already translated"  # skipped: force_retranslate defaults to False
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=3, context_window_ahead=1)
        # target_lines = [l0, l2, l3, l4] (l1 skipped) -- first batch is
        # [l0, l2, l3], whose real next line is l4, not l3 (still part
        # of this very batch).
        assert engine.calls[0]["zh_lines"] == ["l0", "l2", "l3"]
        assert engine.calls[0]["upcoming_lines"] == ["l4"]


class TestSpeakerNamesReachTheBatch:
    def test_known_character_name_is_resolved_from_speaker_label(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0", speaker="SPEAKER_00")]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10,
                                        character_names={"SPEAKER_00": "Xiaoling"})
        assert engine.calls[0]["speaker_labels"] == ["Xiaoling"]

    def test_unknown_speaker_label_resolves_to_none_not_the_raw_label(self):
        """A raw diarization id like SPEAKER_00 isn't a name -- showing it
        to the model as if it were would just be confusing noise."""
        lines = [Line(idx=0, start=0, end=1, zh="l0", speaker="SPEAKER_00")]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10, character_names={})
        assert engine.calls[0]["speaker_labels"] == [None]

    def test_line_with_no_speaker_at_all_resolves_to_none(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0", speaker=None)]
        engine = ContextCapturingEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10,
                                        character_names={"SPEAKER_00": "Xiaoling"})
        assert engine.calls[0]["speaker_labels"] == [None]


class TestBuildNumberedLines:
    def test_lines_with_no_speaker_names_have_no_prefix(self):
        result = te.build_numbered_lines([1, 2], ["你好", "再见"])
        assert result == "1. 你好\n2. 再见"

    def test_known_speaker_is_prefixed_in_brackets(self):
        result = te.build_numbered_lines([1, 2], ["你好", "再见"], speaker_names=["Xiaoling", None])
        assert result == "1. [Xiaoling] 你好\n2. 再见"

    def test_ids_need_not_start_at_one(self):
        # Used for retrying only the missing ids from a partial response --
        # their ORIGINAL batch ids must be preserved, not renumbered.
        result = te.build_numbered_lines([3, 5], ["a", "b"])
        assert result == "3. a\n5. b"


class TestParseIdKeyedJson:
    def test_parses_a_well_formed_object(self):
        result = te.parse_id_keyed_json('{"1": "Hello.", "2": "Hi."}', [1, 2])
        assert result == {"1": "Hello.", "2": "Hi."}

    def test_ignores_unexpected_extra_ids(self):
        result = te.parse_id_keyed_json('{"1": "Hello.", "99": "bogus"}', [1, 2])
        assert result == {"1": "Hello."}

    def test_missing_ids_are_simply_absent_from_the_result(self):
        result = te.parse_id_keyed_json('{"1": "Hello."}', [1, 2])
        assert result == {"1": "Hello."}

    def test_shuffled_key_order_is_still_correctly_matched_by_id(self):
        result = te.parse_id_keyed_json('{"2": "Second.", "1": "First."}', [1, 2])
        assert result == {"1": "First.", "2": "Second."}

    def test_a_same_length_array_is_malformed_not_matched_by_position(self):
        """CLAUDE.md rule: never match AI results back to lines by list
        position. Even a same-length array can be reordered, so it's
        treated as a malformed reply (empty) and the retry path re-asks."""
        result = te.parse_id_keyed_json('["Hello.", "Hi."]', [1, 2])
        assert result == {}

    def test_short_positional_array_is_rejected_not_misassigned(self):
        """Regression test for a real bug: ["A", "C"] for ids [1, 2, 3]
        used to zip positionally into {"1": "A", "2": "C"} -- silently
        putting line 3's translation ("C") on line 2's id. A length
        mismatch has no reliable position-to-id mapping at all, so it
        must come back empty and let the retry path re-request the
        missing ones instead."""
        result = te.parse_id_keyed_json('["A", "C"]', [1, 2, 3])
        assert result == {}

    def test_long_positional_array_is_also_rejected(self):
        result = te.parse_id_keyed_json('["A", "B", "C"]', [1, 2])
        assert result == {}

    def test_markdown_fences_are_stripped(self):
        result = te.parse_id_keyed_json('```json\n{"1": "Hello."}\n```', [1])
        assert result == {"1": "Hello."}

    def test_malformed_json_returns_empty_not_raises(self):
        assert te.parse_id_keyed_json("not json at all", [1, 2]) == {}

    def test_non_string_values_are_treated_as_missing(self):
        """{"1": null, "2": ["x"]} used to pass straight through, leaving
        ln.en set to None or a list. Any non-string value must be
        dropped so the id is treated as missing and gets retried."""
        result = te.parse_id_keyed_json('{"1": null, "2": ["x"], "3": "Hi."}', [1, 2, 3])
        assert result == {"3": "Hi."}

    def test_prose_wrapped_around_the_json_object_is_tolerated(self):
        """Small local models often add commentary around the JSON --
        e.g. "Here you go:\\n{...}". The first JSON value anywhere in the
        text should be extracted rather than requiring the whole
        response to be nothing but JSON."""
        result = te.parse_id_keyed_json('Here you go:\n{"1": "Hello."}\nHope that helps!', [1])
        assert result == {"1": "Hello."}

    def test_prose_wrapped_around_an_array_is_still_malformed(self):
        result = te.parse_id_keyed_json('Sure, here it is: ["Hello.", "Hi."]', [1, 2])
        assert result == {}


class TestRequestTranslationsWithRetry:
    def test_a_complete_well_ordered_response_needs_no_retry(self):
        calls = []
        def call_model(numbered):
            calls.append(numbered)
            return '{"1": "A.", "2": "B."}'
        result = te.request_translations_with_retry(["a", "b"], None, call_model)
        assert result == ["A.", "B."]
        assert len(calls) == 1

    def test_shuffled_response_order_still_lands_on_the_right_line(self):
        """The core fix this replaces: the old code trusted array POSITION,
        so a shuffled/reordered response silently misassigned lines. Id-
        keyed lookup means shuffled key order in the raw response can't
        do that anymore."""
        def call_model(numbered):
            return '{"3": "Third.", "1": "First.", "2": "Second."}'
        result = te.request_translations_with_retry(["a", "b", "c"], None, call_model)
        assert result == ["First.", "Second.", "Third."]

    def test_missing_lines_are_retried_and_recovered(self):
        calls = []
        def call_model(numbered):
            calls.append(numbered)
            if len(calls) == 1:
                return '{"1": "First."}'  # line 2 missing this round
            return '{"2": "Second."}'  # retry recovers it
        result = te.request_translations_with_retry(["a", "b"], None, call_model, max_retries=1)
        assert result == ["First.", "Second."]
        assert len(calls) == 2
        assert "2. b" in calls[1]  # retry only re-sent the missing line
        assert "1. a" not in calls[1]

    def test_still_missing_after_retries_exhausted_leaves_that_line_blank_only(self):
        def call_model(numbered):
            return '{"1": "First."}'  # line 2 never comes back, ever
        result = te.request_translations_with_retry(["a", "b"], None, call_model, max_retries=1)
        assert result == ["First.", ""]  # only the genuinely-missing line is blank

    def test_extra_unexpected_ids_in_the_response_are_ignored(self):
        def call_model(numbered):
            return '{"1": "First.", "2": "Second.", "47": "bogus extra"}'
        result = te.request_translations_with_retry(["a", "b"], None, call_model)
        assert result == ["First.", "Second."]

    def test_speaker_names_reach_the_numbered_lines_sent_to_the_model(self):
        captured = {}
        def call_model(numbered):
            captured["numbered"] = numbered
            return '{"1": "Hi."}'
        te.request_translations_with_retry(["你好"], ["Xiaoling"], call_model)
        assert "[Xiaoling]" in captured["numbered"]


class TestTranslationLengthMismatchSafety:
    """The exit condition for this milestone: a misbehaving engine must
    never cause a translation to land on the wrong line."""

    class WrongCountEngine:
        def __init__(self, translations):
            self.translations = translations

        def translate_batch(self, zh_lines, context):
            return self.translations

    def test_too_few_translations_leaves_the_whole_batch_untranslated_not_shifted(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(3)]
        engine = self.WrongCountEngine(["EN:l0", "EN:l1"])  # one short
        result, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=3)

        assert len(errors) == 1
        # None of the lines got ANY translation -- specifically, l2's real
        # translation was never silently assigned to l1 or vice versa.
        assert all(ln.en == "" for ln in result)

    def test_too_many_translations_leaves_the_whole_batch_untranslated_not_shifted(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(2)]
        engine = self.WrongCountEngine(["EN:l0", "EN:l1", "EN:extra"])
        result, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)

        assert len(errors) == 1
        assert all(ln.en == "" for ln in result)

    def test_correct_count_still_assigns_normally(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(2)]
        engine = self.WrongCountEngine(["EN:l0", "EN:l1"])
        result, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)

        assert errors == []
        assert result[0].en == "EN:l0"
        assert result[1].en == "EN:l1"

    def test_a_later_batch_with_the_right_count_is_unaffected_by_an_earlier_bad_one(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]

        class MixedEngine:
            def __init__(self):
                self.call_count = 0

            def translate_batch(self, zh_lines, context):
                self.call_count += 1
                if self.call_count == 1:
                    return ["only one"]  # batch of 2, wrong count
                return [f"EN:{z}" for z in zh_lines]

        result, errors = te.translate_lines_with_engine(lines, MixedEngine(), {}, batch_size=2)

        assert len(errors) == 1
        assert result[0].en == "" and result[1].en == ""       # first batch: left blank
        assert result[2].en == "EN:l2" and result[3].en == "EN:l3"  # second batch: correct


class TestRedactSecrets:
    """A raise_for_status() failure's message includes the request URL --
    if a key was ever put there (or shows up in an Authorization header
    dump), that message is what gets stored on dramas.last_translate_errors
    and shown in the UI. redact_secrets() is the safety net for that,
    on top of sending keys as headers rather than URL params in the
    first place."""

    @pytest.mark.parametrize("token", ["ntn_" + "A1b2C3d4" * 6, "secret_" + "A1b2C3d4" * 5])
    def test_redacts_a_bare_notion_token(self, token):
        text = f"HTTPError for Notion: token {token} was rejected"
        out = te.redact_secrets(text)
        assert token not in out and "[REDACTED]" in out

    def test_leaves_short_secret_and_ntn_words_alone(self):
        text = "secret_key not set; ntn_status=ok; secret_santa"
        assert te.redact_secrets(text) == text

    def test_redacts_a_key_query_param(self):
        text = ("400 Client Error: Bad Request for url: "
                "https://generativelanguage.googleapis.com/v1beta/models/x:"
                "generateContent?key=AIzaSyFAKESECRETVALUE12345")
        assert "AIzaSyFAKESECRETVALUE12345" not in te.redact_secrets(text)

    def test_redacts_a_bare_google_key_anywhere_in_the_text(self):
        text = "something failed near AIzaSyFAKESECRETVALUE12345 during the request"
        assert "AIzaSyFAKESECRETVALUE12345" not in te.redact_secrets(text)

    def test_redacts_an_sk_style_key(self):
        text = "Incorrect API key provided: sk-abcdefghijklmnopqrstuvwxyz"
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in te.redact_secrets(text)

    def test_redacts_an_authorization_header_dump(self):
        text = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234 -- request failed"
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in te.redact_secrets(text)

    def test_leaves_ordinary_error_text_unchanged(self):
        text = "429 Too Many Requests -- rate limited, retry later"
        assert te.redact_secrets(text) == text

    def test_handles_empty_and_none(self):
        assert te.redact_secrets("") == ""
        assert te.redact_secrets(None) is None


class TestTranslateLinesWithEngineRedactsBatchErrors:
    def test_a_failing_batch_stores_a_redacted_error_not_the_raw_one(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0")]

        class LeakyEngine:
            def translate_batch(self, zh_lines, context):
                raise RuntimeError(
                    "400 Client Error: Bad Request for url: https://generativelanguage."
                    "googleapis.com/v1beta/models/x:generateContent?key=AIzaSyFAKEKEY999")

        _, errors = te.translate_lines_with_engine(lines, LeakyEngine(), {}, batch_size=1)

        assert len(errors) == 1
        assert "AIzaSyFAKEKEY999" not in errors[0]["error"]
        assert "400 Client Error" in errors[0]["error"]  # the rest of the message survives


class TestGeminiEngine:
    def test_translate_batch_parses_response_and_records_usage(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "candidates": [{"content": {"parts": [
                        {"text": '{"1": "Hello.", "2": "Goodbye."}'}]}}],
                    "usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 8},
                }

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest")
        result = engine.translate_batch(["你好", "再见"], {})

        assert result == ["Hello.", "Goodbye."]
        assert engine.last_usage == {"input_tokens": 42, "output_tokens": 8,
                                     "cache_read_tokens": 0, "cache_write_tokens": 0}
        assert "gemini-flash-lite-latest" in captured["url"]
        # Key goes in a header, never the URL/query string -- a
        # raise_for_status() failure's message includes the URL, and that
        # message can end up stored/shown; a key in params would leak.
        assert captured["headers"] == {"x-goog-api-key": "fake-key"}
        assert "key=" not in captured["url"]
        assert "systemInstruction" in captured["json"]

    def test_recent_context_reaches_the_user_message_not_the_system_instruction(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GeminiEngine("fake-key")
        engine.translate_batch(["x"], {"recent_context": [("她来了", "She came.")]})

        sys_text = captured["json"]["systemInstruction"]["parts"][0]["text"]
        user_text = captured["json"]["contents"][0]["parts"][0]["text"]
        assert "她来了 -> She came." in user_text
        assert "她来了" not in sys_text

    def test_speaker_labels_reach_the_actual_numbered_lines_sent(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"1": "Hi."}'}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GeminiEngine("fake-key")
        result = engine.translate_batch(["你好"], {"speaker_labels": ["Xiaoling"]})

        user_text = captured["json"]["contents"][0]["parts"][0]["text"]
        assert "[Xiaoling] 你好" in user_text
        assert result == ["Hi."]

    def test_pronouns_in_the_speaker_label_reach_the_exact_line(self, monkeypatch):
        """Step 1e: translate_lines_with_engine's character_names values
        now carry pronouns (see translation_guide.build_speaker_labels),
        so the translator sees them on the line itself."""
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"1": "Hi."}'}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        lines = [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")]
        te.translate_lines_with_engine(
            lines, te.GeminiEngine("fake-key"), drama_meta={},
            character_names={"SPEAKER_00": "Xiaoling (they/them)"})

        user_text = captured["json"]["contents"][0]["parts"][0]["text"]
        assert "[Xiaoling (they/them)] 你好" in user_text

    def test_a_response_missing_one_id_is_retried_before_giving_up(self, monkeypatch):
        call_count = {"n": 0}

        class FakeResponse:
            def __init__(self, text):
                self._text = text
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeResponse('{"1": "First."}')  # line 2 missing
            return FakeResponse('{"2": "Second."}')  # retry recovers it

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GeminiEngine("fake-key")
        result = engine.translate_batch(["a", "b"], {})

        assert result == ["First.", "Second."]
        assert call_count["n"] == 2

    def test_missing_usage_metadata_does_not_crash(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}
                # No usageMetadata key at all.

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key")
        engine.translate_batch(["x"], {})
        assert engine.last_usage == {"input_tokens": 0, "output_tokens": 0,
                                     "cache_read_tokens": 0, "cache_write_tokens": 0}

    def test_empty_candidates_with_prompt_feedback_raises_content_moderation_blocked(
            self, monkeypatch):
        """Step 31 item 1, shape one: blocked before generation even
        started -- no `candidates` key at all, the real reason sitting in
        `promptFeedback.blockReason` instead. Used to raise a bare
        IndexError from the old `data["candidates"][0]...` indexing."""
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"promptFeedback": {"blockReason": "SAFETY"}}

        monkeypatch.setattr("time.sleep", lambda *_: None)
        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key")
        with pytest.raises(te.ContentModerationBlocked) as exc_info:
            engine.translate_batch(["a graphic passage"], {})
        assert exc_info.value.engine == "gemini"
        assert exc_info.value.reason == "SAFETY"

    def test_safety_finish_reason_with_no_content_raises_content_moderation_blocked(
            self, monkeypatch):
        """Step 31 item 1, shape two: a candidate came back, but with
        finishReason SAFETY/PROHIBITED_CONTENT and no `content` key --
        used to raise a bare KeyError from `candidates[0]["content"]...`."""
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"finishReason": "PROHIBITED_CONTENT"}]}

        monkeypatch.setattr("time.sleep", lambda *_: None)
        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key")
        with pytest.raises(te.ContentModerationBlocked) as exc_info:
            engine.translate_batch(["a graphic passage"], {})
        assert exc_info.value.engine == "gemini"
        assert exc_info.value.reason == "PROHIBITED_CONTENT"


class TestGeminiFreeTierThrottle:
    """Step 1d item 4 added RPM-only throttling; Step 1f extends it to all
    three real limits Google actually enforces -- RPM and RPD are
    per-model (Flash: 10/250, Flash-Lite: 15/1000), TPM (250k) is shared
    across every model. GeminiEngine paces itself client-side against
    whichever ceiling is closest. Uses a fake monotonic clock so a test
    doesn't actually take a minute (or a day) to run -- only the
    *decision* to wait, and for how long, is under test, not real
    wall-clock sleeping."""

    def _fake_clock(self, monkeypatch):
        state = {"now": 0.0, "slept": []}

        def fake_monotonic():
            return state["now"]

        def fake_sleep(seconds):
            state["slept"].append(seconds)
            state["now"] += seconds

        monkeypatch.setattr("time.monotonic", fake_monotonic)
        monkeypatch.setattr("time.sleep", fake_sleep)
        return state

    def test_a_paid_key_is_never_throttled(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = te.GeminiEngine("fake-key", free_tier=False)
        for _ in range(30):
            engine._throttle_for_free_tier()
        assert state["slept"] == []

    def test_flash_and_flash_lite_have_different_real_limits(self):
        assert te.gemini_free_tier_limits_for("gemini-flash-latest") == {"rpm": 10, "rpd": 250}
        assert te.gemini_free_tier_limits_for("gemini-flash-lite-latest") == {"rpm": 15, "rpd": 1000}
        assert te.gemini_free_tier_limits_for("gemini-3.1-flash-lite") == {"rpm": 15, "rpd": 1000}

    def test_free_tier_stays_at_or_under_the_per_minute_limit(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = te.gemini_free_tier_limits_for(engine.model)["rpm"]
        for _ in range(limit * 3):
            engine._throttle_for_free_tier()
            state["now"] += 0.01  # each call takes negligible real time

        # However long that took, no trailing 60s window (measured from any
        # actual request) contains more than the limit.
        times = sorted(engine._free_tier_request_times)
        for t in times:
            in_window = sum(1 for other in times if t - 60 < other <= t)
            assert in_window <= limit, (
                f"{in_window} requests fell within 60s of t={t:.2f} -- over the {limit} rpm limit")

    def test_the_request_past_the_per_minute_limit_waits(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = te.gemini_free_tier_limits_for(engine.model)["rpm"]
        for _ in range(limit):
            engine._throttle_for_free_tier()
        assert state["slept"] == []  # all within the same instant: no wait needed

        engine._throttle_for_free_tier()
        assert state["slept"] == [60.0]  # the next one has to wait out the window

    def test_rpd_paces_a_job_even_well_under_the_per_minute_limit(self, monkeypatch):
        """RPD is a real, separate ceiling from RPM -- a job well under its
        per-minute limit can still have used up its whole day's budget."""
        state = self._fake_clock(monkeypatch)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = te.gemini_free_tier_limits_for(engine.model)["rpd"]
        engine._free_tier_daily_request_times = [0.0] * limit

        # Waiting out the daily window would take hours: stop with a clear
        # message instead of sleeping.
        with pytest.raises(te.FreeTierDailyLimitReached, match="daily request limit"):
            engine._throttle_for_free_tier()
        assert state["slept"] == []

    def test_tpm_paces_a_job_when_past_requests_used_the_shared_budget(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine._free_tier_token_counts = [(0.0, te.GEMINI_FREE_TIER_TPM)]

        engine._throttle_for_free_tier()
        assert state["slept"] == [60.0]

    def test_tpm_does_not_pace_a_job_under_the_shared_budget(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine._free_tier_token_counts = [(0.0, 10)]

        engine._throttle_for_free_tier()
        assert state["slept"] == []


class TestGeminiRateStatus:
    """Step 1f: a visible rate-status indicator -- real header-reported
    quota when Google's response includes it, self-tracked RPM/RPD/TPM
    against the static table otherwise."""

    def _post_with_headers(self, monkeypatch, headers):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "{\"1\": \"Hi.\"}"}]}}],
                        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

        resp = FakeResponse()
        resp.headers = headers

        def fake_post(url, headers=None, json=None, timeout=None):
            return resp

        monkeypatch.setattr("requests.post", fake_post)

    def test_reads_real_header_values_when_present(self, monkeypatch):
        self._post_with_headers(monkeypatch, {
            "x-ratelimit-limit-requests": "15",
            "x-ratelimit-remaining-requests": "14",
            "x-ratelimit-remaining-tokens": "249990",
            "x-ratelimit-reset-requests": "4s",
        })
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        assert engine.rate_status["source"] == "header"
        assert engine.rate_status["limit_requests"] == "15"
        assert engine.rate_status["remaining_requests"] == "14"
        assert engine.rate_status["remaining_tokens"] == "249990"

    def test_falls_back_to_the_self_tracked_counter_when_headers_are_absent(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        assert engine.rate_status["source"] == "estimated"
        assert engine.rate_status["rpm_used"] == 1
        assert engine.rate_status["rpm_limit"] == 15
        assert engine.rate_status["rpd_limit"] == 1000
        assert engine.rate_status["tpm_used"] == 15  # 10 input + 5 output
        assert engine.rate_status["tpm_limit"] == te.GEMINI_FREE_TIER_TPM

    def test_no_status_yet_for_a_paid_engine_that_never_throttles(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = te.GeminiEngine("fake-key", free_tier=False)
        engine.translate_batch(["你好"], {})
        assert engine.rate_status is None

    def test_rate_status_text_is_none_before_any_request(self):
        engine = te.GeminiEngine("fake-key", free_tier=True)
        assert te.gemini_rate_status_text(engine) is None

    def test_rate_status_text_shows_all_three_dimensions(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        text = te.gemini_rate_status_text(engine)
        assert "1/15" in text
        assert "1/1000" in text
        assert "15/250,000" in text

    def test_progress_message_appends_rate_status_for_free_tier_gemini(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        message = te.progress_message_with_rate_status(engine, 0.5)
        assert message.startswith("Translating... 50%")
        assert "req/min" in message

    def test_progress_message_is_plain_for_a_non_free_tier_engine(self):
        engine = te.GeminiEngine("fake-key", free_tier=False)
        assert te.progress_message_with_rate_status(engine, 0.5) == "Translating... 50%"


class TestGeminiProGoneFromFreeTier:
    """Step 1f item 4: Gemini Pro was removed from the free tier entirely
    in April 2026 -- a free-tier key can no longer reach it at all."""

    def test_pro_is_the_flagged_unavailable_model(self):
        assert "gemini-pro-latest" in te.GEMINI_FREE_TIER_UNAVAILABLE_MODELS

    def test_flash_and_flash_lite_are_not_flagged(self):
        assert "gemini-flash-latest" not in te.GEMINI_FREE_TIER_UNAVAILABLE_MODELS
        assert "gemini-flash-lite-latest" not in te.GEMINI_FREE_TIER_UNAVAILABLE_MODELS


def _deepseek_engine_with_fake_client(message_content, refusal=None):
    """Builds a real DeepSeekEngine without going through __init__ (which
    imports the `openai` package -- not installed in this core-only test
    environment, same reasoning as _claude_engine_with_fake_client above
    not needing one for `anthropic`, which IS installed here)."""
    engine = te.DeepSeekEngine.__new__(te.DeepSeekEngine)
    engine.model = "deepseek-fake"
    engine.last_usage = te._empty_usage()

    message = type("Message", (), {"content": message_content, "refusal": refusal})()
    choice = type("Choice", (), {"message": message})()
    resp = type("Resp", (), {"choices": [choice], "usage": None})()

    class _Completions:
        def create(self, **kwargs):
            return resp
    engine.client = type("Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()})()
    return engine


class TestDeepSeekEngine:
    def test_null_content_with_refusal_raises_content_moderation_blocked(self, monkeypatch):
        """Step 31 item 1: the OpenAI-compatible refusal shape -- content
        is None/empty and a separate `refusal` field explains why. Used
        to raise a bare AttributeError from the old `.content.strip()`
        (None has no .strip())."""
        monkeypatch.setattr("time.sleep", lambda *_: None)
        engine = _deepseek_engine_with_fake_client(
            message_content=None, refusal="This request violates our usage policies.")
        with pytest.raises(te.ContentModerationBlocked) as exc_info:
            engine.translate_batch(["a graphic passage"], {})
        assert exc_info.value.engine == "deepseek"
        assert exc_info.value.reason == "This request violates our usage policies."

    def test_ordinary_response_still_translates_normally(self):
        engine = _deepseek_engine_with_fake_client(message_content='{"1": "Hello."}')
        result = engine.translate_batch(["你好"], {})
        assert result == ["Hello."]


class TestOllamaEngine:
    """Regression coverage for a real gap: Ollama's own default context
    window can be as small as 2-4k tokens, and a prompt longer than it
    gets silently TRUNCATED FROM THE START (system instructions/glossary/
    reference novel) with no error at all. translate_batch now always
    sends an explicit options.num_ctx sized from the actual prompt, with
    a floor, plus a `format` JSON schema pairing with the id-keyed
    parsing from Step 1."""

    def _fake_response(self, captured, text='{"1": "Hello."}'):
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": text}}

        def fake_post(url, json=None, timeout=None):
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()
        return fake_post

    def test_sends_a_format_json_schema_for_structured_output(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("requests.post", self._fake_response(captured))
        engine = te.OllamaEngine()
        engine.translate_batch(["你好"], {})
        assert captured["json"]["format"] == te._OLLAMA_ID_KEYED_JSON_SCHEMA

    def test_num_ctx_is_never_below_the_floor_for_a_short_prompt(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("requests.post", self._fake_response(captured))
        engine = te.OllamaEngine()
        engine.translate_batch(["你好"], {})
        assert captured["json"]["options"]["num_ctx"] >= te.OLLAMA_MIN_NUM_CTX

    def test_num_ctx_grows_with_a_much_longer_prompt(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("requests.post", self._fake_response(captured))
        engine = te.OllamaEngine()
        long_glossary = [{"term_original": "x" * 200, "term_translation": "y"} for _ in range(200)]
        engine.translate_batch(["你好"], {"glossary_terms": long_glossary})
        assert captured["json"]["options"]["num_ctx"] > te.OLLAMA_MIN_NUM_CTX

    def test_override_can_raise_num_ctx_above_the_estimate(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("requests.post", self._fake_response(captured))
        engine = te.OllamaEngine()
        engine.translate_batch(["你好"], {"ollama_num_ctx_override": 100_000})
        assert captured["json"]["options"]["num_ctx"] == 100_000

    def test_override_below_the_estimate_is_not_used(self, monkeypatch):
        """A manual override smaller than what the prompt actually needs
        would silently reintroduce the exact truncation bug this exists
        to prevent -- the larger of the two must always win."""
        captured = {}
        monkeypatch.setattr("requests.post", self._fake_response(captured))
        engine = te.OllamaEngine()
        engine.translate_batch(["你好"], {"ollama_num_ctx_override": 1})
        assert captured["json"]["options"]["num_ctx"] >= te.OLLAMA_MIN_NUM_CTX

    def test_request_has_a_timeout(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("requests.post", self._fake_response(captured))
        engine = te.OllamaEngine()
        engine.translate_batch(["你好"], {})
        assert captured["timeout"] is not None


class TestOllamaUnavailableErrors:
    def test_translate_batch_and_call_llm_json_share_the_mapping(self, monkeypatch):
        import requests

        def refuse(url, json=None, timeout=None):
            raise requests.ConnectionError(f"refused {url}")
        monkeypatch.setattr("requests.post", refuse)
        engine = te.OllamaEngine(base_url="http://10.1.2.3:11434")
        with pytest.raises(te.OllamaUnavailableError) as batch:
            engine.translate_batch(["你好"], {})
        with pytest.raises(te.OllamaUnavailableError) as free_form:
            te.call_llm_json(engine, "hi")
        for info in (batch, free_form):
            assert info.value.reason == "ollama_unreachable"
            assert "10.1.2.3" not in str(info.value)


class TestOllamaReachability:
    """Regression coverage for a real gap: Ollama is exempted from the
    API-key check entirely (workspace_tab.py's _needs_key), with nothing
    in its place -- clicking Translate against a stopped local server
    used to start a background job that only failed once
    translate_batch's own 300s timeout expired. check_ollama_reachable()
    is a cheap up-front health check the UI uses to disable that button
    instead."""

    def setup_method(self):
        te._ollama_reachability_cache.clear()

    def _fake_get(self, ok=True, raises=None):
        captured = {}

        def fake_get(url, timeout=None):
            captured["url"] = url
            captured["timeout"] = timeout
            if raises:
                raise raises
            return type("Resp", (), {"ok": ok})()
        return fake_get, captured

    def test_true_when_the_server_responds_ok(self, monkeypatch):
        fake_get, captured = self._fake_get(ok=True)
        monkeypatch.setattr("requests.get", fake_get)
        assert te.check_ollama_reachable("http://localhost:11434") is True
        assert captured["url"] == "http://localhost:11434/api/tags"
        assert captured["timeout"] is not None

    def test_false_when_the_server_responds_with_an_error_status(self, monkeypatch):
        fake_get, _ = self._fake_get(ok=False)
        monkeypatch.setattr("requests.get", fake_get)
        assert te.check_ollama_reachable("http://localhost:11434") is False

    def test_false_when_the_connection_fails(self, monkeypatch):
        fake_get, _ = self._fake_get(raises=ConnectionError("refused"))
        monkeypatch.setattr("requests.get", fake_get)
        assert te.check_ollama_reachable("http://localhost:11434") is False

    def test_result_is_cached_briefly_not_rechecked_every_call(self, monkeypatch):
        fake_get, _ = self._fake_get(ok=True)
        calls = {"n": 0}
        def counting_get(url, timeout=None):
            calls["n"] += 1
            return fake_get(url, timeout=timeout)
        monkeypatch.setattr("requests.get", counting_get)

        te.check_ollama_reachable("http://localhost:11434")
        te.check_ollama_reachable("http://localhost:11434")
        te.check_ollama_reachable("http://localhost:11434")

        assert calls["n"] == 1

    def test_a_different_base_url_is_cached_separately(self, monkeypatch):
        monkeypatch.setattr("requests.get", self._fake_get(ok=True)[0])
        te.check_ollama_reachable("http://localhost:11434")
        # A second, different URL must still be checked fresh, not
        # short-circuited by the first URL's cache entry.
        fake_get_down, _ = self._fake_get(ok=False)
        monkeypatch.setattr("requests.get", fake_get_down)
        assert te.check_ollama_reachable("http://otherhost:9999") is False

    def test_trailing_slash_in_base_url_is_normalized(self, monkeypatch):
        fake_get, captured = self._fake_get(ok=True)
        monkeypatch.setattr("requests.get", fake_get)
        te.check_ollama_reachable("http://localhost:11434/")
        assert captured["url"] == "http://localhost:11434/api/tags"


class TestNLLBEngine:
    """NLLBEngine: fully local/offline MT via Meta's NLLB-200. transformers
    is a real installed dependency in this environment, but downloading an
    actual model isn't something a test suite should do -- transformers.pipeline
    is faked at that boundary, the same way GeminiEngine's tests fake
    requests.post rather than hitting a real API."""

    def _install_fake_pipeline(self, monkeypatch):
        import sys, types
        captured = {}

        class FakePipeline:
            def __init__(self, task, model, src_lang, tgt_lang):
                captured["task"] = task
                captured["model"] = model
                captured["src_lang"] = src_lang
                captured["tgt_lang"] = tgt_lang

            def __call__(self, texts):
                return [{"translation_text": f"EN:{t}"} for t in texts]

        fake_module = types.ModuleType("transformers")
        fake_module.pipeline = lambda task, model, src_lang, tgt_lang: FakePipeline(
            task, model, src_lang, tgt_lang)
        monkeypatch.setitem(sys.modules, "transformers", fake_module)
        return captured

    def setup_method(self):
        te._nllb_pipeline_cache.clear()

    def test_translates_and_defaults_to_chinese_simplified(self, monkeypatch):
        captured = self._install_fake_pipeline(monkeypatch)
        engine = te.NLLBEngine()
        result = engine.translate_batch(["你好", "再见"], {})
        assert result == ["EN:你好", "EN:再见"]
        assert captured["src_lang"] == "zho_Hans"
        assert captured["tgt_lang"] == "eng_Latn"
        assert captured["model"] == "facebook/nllb-200-distilled-600M"

    def test_japanese_source_language_maps_to_nllb_code(self, monkeypatch):
        captured = self._install_fake_pipeline(monkeypatch)
        engine = te.NLLBEngine()
        engine.translate_batch(["こんにちは"], {"source_language": "ja"})
        assert captured["src_lang"] == "jpn_Jpan"

    def test_korean_source_language_maps_to_nllb_code(self, monkeypatch):
        captured = self._install_fake_pipeline(monkeypatch)
        engine = te.NLLBEngine()
        engine.translate_batch(["안녕"], {"source_language": "ko"})
        assert captured["src_lang"] == "kor_Hang"

    def test_custom_model_size_is_used(self, monkeypatch):
        captured = self._install_fake_pipeline(monkeypatch)
        engine = te.NLLBEngine(model="facebook/nllb-200-distilled-1.3B")
        engine.translate_batch(["你好"], {})
        assert captured["model"] == "facebook/nllb-200-distilled-1.3B"

    def test_needs_no_api_key(self):
        # Must not raise/require anything -- api_key is accepted but unused.
        engine = te.NLLBEngine(api_key=None)
        assert engine.model_name == "facebook/nllb-200-distilled-600M"

    def test_pipeline_is_cached_per_model_and_language(self, monkeypatch):
        import sys, types
        build_calls = []

        class FakePipeline:
            def __call__(self, texts):
                return [{"translation_text": f"EN:{t}"} for t in texts]

        def fake_pipeline_factory(task, model, src_lang, tgt_lang):
            build_calls.append((model, src_lang))
            return FakePipeline()

        fake_module = types.ModuleType("transformers")
        fake_module.pipeline = fake_pipeline_factory
        monkeypatch.setitem(sys.modules, "transformers", fake_module)

        engine = te.NLLBEngine()
        engine.translate_batch(["a"], {"source_language": "zh"})
        engine.translate_batch(["b"], {"source_language": "zh"})
        engine.translate_batch(["c"], {"source_language": "ja"})

        assert len(build_calls) == 2  # zh built once and reused; ja built separately

    def test_is_registered_in_engines_and_notes(self):
        assert te.ENGINES["nllb"] is te.NLLBEngine
        assert "nllb" in te.ENGINE_NOTES

    def test_defaults_to_english_target(self, monkeypatch):
        captured = self._install_fake_pipeline(monkeypatch)
        te.NLLBEngine().translate_batch(["你好"], {})
        assert captured["tgt_lang"] == "eng_Latn"

    def test_step_26b_english_source_and_chinese_target_reach_nllb(self, monkeypatch):
        captured = self._install_fake_pipeline(monkeypatch)
        engine = te.NLLBEngine()
        engine.translate_batch(["Hello"], {"source_language": "en", "target_language": "zh"})
        assert captured["src_lang"] == "eng_Latn"
        assert captured["tgt_lang"] == "zho_Hans"

    def test_step_26b_reverse_pipeline_is_cached_separately_from_forward(self, monkeypatch):
        """The (model, source, target) pair, not just source, decides
        which cached pipeline is reused -- otherwise English -> Chinese
        would collide with the existing Chinese -> English pipeline."""
        import sys, types
        build_calls = []

        class FakePipeline:
            def __call__(self, texts):
                return [{"translation_text": f"OUT:{t}"} for t in texts]

        fake_module = types.ModuleType("transformers")
        fake_module.pipeline = lambda task, model, src_lang, tgt_lang: (
            build_calls.append((src_lang, tgt_lang)) or FakePipeline())
        monkeypatch.setitem(sys.modules, "transformers", fake_module)

        engine = te.NLLBEngine()
        engine.translate_batch(["你好"], {"source_language": "zh", "target_language": "en"})
        engine.translate_batch(["Hello"], {"source_language": "en", "target_language": "zh"})
        assert len(build_calls) == 2
        assert ("zho_Hans", "eng_Latn") in build_calls
        assert ("eng_Latn", "zho_Hans") in build_calls


class TestFreeEngineLabelling:
    """Step 1d item 4: every free-to-use option is clearly labelled as
    such (test_offline, ollama, nllb always; gemini only
    when the per-session "free-tier key" setting is on), and paid
    engines keep their normal descriptions."""

    def test_free_engines_set_matches_the_roadmap_table(self):
        assert te.FREE_ENGINES == {"fake", "ollama", "nllb"}

    def test_gemini_is_not_unconditionally_free(self):
        # Gemini reuses one engine for free and paid keys -- whether a
        # given key is free depends on a setting, not the engine itself.
        assert "gemini" not in te.FREE_ENGINES

    def test_every_free_engine_note_is_marked(self):
        for name in te.FREE_ENGINES:
            assert "🧪" in te.ENGINE_NOTES[name]
            assert te.engine_picker_label(name) == te.ENGINE_NOTES[name]

    def test_paid_engine_notes_are_unmarked(self):
        for name in ("claude", "deepseek"):
            assert "🧪" not in te.ENGINE_NOTES[name]

    def test_gemini_label_is_plain_by_default(self):
        assert te.engine_picker_label("gemini") == te.ENGINE_NOTES["gemini"]
        assert "🧪" not in te.engine_picker_label("gemini", gemini_free_tier=False)

    def test_gemini_label_switches_when_free_tier_is_on(self):
        label = te.engine_picker_label("gemini", gemini_free_tier=True)
        assert "🧪" in label
        assert str(te.GEMINI_FREE_TIER_LIMITS["flash"]["rpm"]) in label
        assert str(te.GEMINI_FREE_TIER_LIMITS["flash-lite"]["rpm"]) in label
        assert "Pro" in label

    def test_free_tier_flag_never_changes_other_engines_labels(self):
        for name in te.ENGINES:
            if name == "gemini":
                continue
            assert te.engine_picker_label(name, gemini_free_tier=True) == te.ENGINE_NOTES[name]


class TestGetEngineFreeTierPassthrough:
    def test_free_tier_reaches_the_constructed_gemini_engine(self):
        engine = te.get_engine("gemini", "fake-key", free_tier=True)
        assert isinstance(engine, te.GeminiEngine)
        assert engine.free_tier is True

    def test_defaults_to_not_free_tier(self):
        engine = te.get_engine("gemini", "fake-key")
        assert engine.free_tier is False

    def test_free_tier_kwarg_is_ignored_for_non_gemini_engines(self):
        # Every other engine class's __init__ has no free_tier parameter --
        # this must not raise a TypeError just because the caller always
        # passes the kwarg.
        engine = te.get_engine("fake", free_tier=True)
        assert not hasattr(engine, "free_tier")

    def test_free_tier_reaches_gemini_even_with_an_explicit_model(self):
        engine = te.get_engine("gemini", "fake-key", "gemini-pro-latest", free_tier=True)
        assert engine.model == "gemini-pro-latest"
        assert engine.free_tier is True


class TestGetEngineOllamaBaseUrlPassthrough:
    """Step 5b item 1: the Ollama base URL configured in Settings wasn't
    actually reaching OllamaEngine -- get_engine() silently dropped it, so
    translation always talked to the hardcoded http://localhost:11434
    default no matter what was configured."""

    def test_base_url_reaches_the_constructed_ollama_engine(self):
        engine = te.get_engine("ollama", None, base_url="http://gpu-box:11434")
        assert isinstance(engine, te.OllamaEngine)
        assert engine.base_url == "http://gpu-box:11434"

    def test_defaults_to_ollamas_own_default_when_not_configured(self):
        engine = te.get_engine("ollama")
        assert engine.base_url == "http://localhost:11434"

    def test_base_url_reaches_ollama_even_with_an_explicit_model(self):
        engine = te.get_engine("ollama", None, "qwen2.5:14b", base_url="http://gpu-box:11434")
        assert engine.model == "qwen2.5:14b"
        assert engine.base_url == "http://gpu-box:11434"

    def test_base_url_kwarg_is_ignored_for_non_ollama_engines(self):
        # Every other engine class's __init__ has no base_url parameter --
        # this must not raise a TypeError just because the caller always
        # passes the kwarg.
        engine = te.get_engine("fake", base_url="http://gpu-box:11434")
        assert not hasattr(engine, "base_url")


class TestEstimateCostForEngine:
    """Step 1d item 5: cost shows $0 for a free-tier Gemini call, not the
    paid per-token rate its model name would normally look up."""

    def test_free_tier_gemini_is_zero_regardless_of_tokens(self):
        engine = te.GeminiEngine("fake-key", free_tier=True)
        assert te.estimate_cost_for_engine(engine, 1_000_000, 1_000_000) == 0.0

    def test_paid_gemini_matches_plain_estimate_cost(self):
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=False)
        expected = te.estimate_cost("gemini-flash-lite-latest", 1000, 500)
        assert te.estimate_cost_for_engine(engine, 1000, 500) == expected

    def test_an_engine_with_no_free_tier_attribute_falls_back_normally(self):
        engine = te.ClaudeEngine.__new__(te.ClaudeEngine)  # no free_tier attr at all
        engine.model = "claude-sonnet-5"
        expected = te.estimate_cost("claude-sonnet-5", 1000, 500)
        assert te.estimate_cost_for_engine(engine, 1000, 500) == expected


class TestCallLlmJson:
    """call_llm_json() is the shared single-prompt call used by every
    non-translation LLM feature (emotion tagging, translation notes,
    glossary extraction, story tools, line tools, Q&A, etc). Regression
    coverage for two real bugs found by reading every one of its former
    per-file duplicates: none of them handled GeminiEngine's shape at
    all (no .client attribute -- it calls Gemini's REST endpoint
    directly), so picking Gemini silently made these features return
    nothing; and test_offline's .client is None by design, but the old
    code's hasattr(engine, "client") check is True either way, so it
    took the OpenAI-shaped branch and crashed on None.chat instead of
    declining -- meaning the app's own "try it free" engine crashed the
    moment you clicked most of these features.
    """

    def test_claude_shaped_engine_and_usage_cb(self):
        captured_usage = {}

        class FakeUsage:
            input_tokens = 10
            output_tokens = 5

        class FakeResponse:
            usage = FakeUsage()
            content = [_FakeBlock('{"ok": true}')]

        class FakeClaudeLike:
            model = "fake-claude"

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                return FakeResponse()

        result = te.call_llm_json(
            FakeClaudeLike(), "prompt",
            usage_cb=lambda inp, out: captured_usage.update(input=inp, output=out))
        assert result == '{"ok": true}'
        assert captured_usage == {"input": 10, "output": 5}

    def test_openai_shaped_engine_and_usage_cb(self):
        captured_usage = {}

        class FakeUsage:
            prompt_tokens = 20
            completion_tokens = 7

        class FakeChoice:
            class message:
                content = "[1, 2, 3]"

        class FakeResponse:
            usage = FakeUsage()
            choices = [FakeChoice()]

        class FakeOpenAiLike:
            model = "fake-deepseek"

            def __init__(self):
                self.client = self
                self.chat = self
                self.completions = self

            def create(self, model, messages):
                return FakeResponse()

        result = te.call_llm_json(
            FakeOpenAiLike(), "prompt",
            usage_cb=lambda inp, out: captured_usage.update(input=inp, output=out))
        assert result == "[1, 2, 3]"
        assert captured_usage == {"input": 20, "output": 7}

    def test_gemini_engine_is_not_silently_skipped(self, monkeypatch):
        captured_usage = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "candidates": [{"content": {"parts": [{"text": "real answer"}]}}],
                    "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 3},
                }

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key")

        result = te.call_llm_json(
            engine, "prompt",
            usage_cb=lambda inp, out: captured_usage.update(input=inp, output=out))
        assert result == "real answer"
        assert captured_usage == {"input": 15, "output": 3}

    def test_free_tier_gemini_is_throttled_here_too(self, monkeypatch):
        """call_llm_json's Gemini branch is a second, separate call site
        from translate_batch -- confirms the free-tier throttle applies
        there as well, not just to translation."""
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key", free_tier=True)
        calls = []
        monkeypatch.setattr(engine, "_throttle_for_free_tier", lambda: calls.append(1))

        te.call_llm_json(engine, "prompt")
        assert calls == [1]

    def test_test_offline_engine_declines_without_crashing(self):
        engine = fake_engine.FakeEngine()
        assert engine.client is None  # by design
        result = te.call_llm_json(engine, "prompt", fallback="[]")
        assert result == "[]"

    def test_ollama_engine_is_not_silently_skipped(self, monkeypatch):
        """Regression test for the real Step 1d bug: OllamaEngine has no
        .client and isn't GeminiEngine, so it used to fall straight
        through to the bare fallback -- meaning flagging, consistency,
        emotion detection, translation notes, speaker tagging and the
        pacing rewrite all silently did nothing at all with Ollama."""
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": "real answer"},
                        "prompt_eval_count": 12, "eval_count": 4}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()
        monkeypatch.setattr("requests.post", fake_post)

        engine = te.OllamaEngine()
        usage = {}
        result = te.call_llm_json(engine, "prompt",
                                   usage_cb=lambda inp, out: usage.update(input=inp, output=out))

        assert result == "real answer"
        assert usage == {"input": 12, "output": 4}
        assert captured["url"] == f"{engine.base_url}/api/chat"
        assert captured["json"]["format"] == "json"
        assert captured["json"]["options"]["num_ctx"] >= te.OLLAMA_MIN_NUM_CTX
        assert captured["timeout"] is not None

    def test_an_engine_with_no_recognized_shape_raises_a_clear_error(self):
        """NLLB (translation-only, no .client,
        not Gemini/Ollama/test_offline) used to silently return the bare
        fallback here too -- the same "looks like it worked, did
        nothing" failure mode as the Ollama bug above, just for a
        different set of engines. Now raises instead of pretending to
        have produced a real (empty) result."""
        class FakeTranslationOnlyEngine:
            name = "nllb"

        with pytest.raises(RuntimeError, match="nllb can't run this feature"):
            te.call_llm_json(FakeTranslationOnlyEngine(), "prompt", fallback="[]")

    def test_a_malformed_gemini_response_returns_fallback(self, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": []}  # no content at all

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key")
        result = te.call_llm_json(engine, "prompt", fallback="fallback-value")
        assert result == "fallback-value"


class TestCheckConsistencyLlm:
    def test_pure_mt_engine_returns_empty_list_not_crash(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        result = te.check_consistency_llm(lines, PureMT())
        assert result == ([], 0, 0)

    def test_no_translated_lines_returns_empty(self):
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]  # untranslated
        result = te.check_consistency_llm(lines, MockLlm())
        assert result == ([], 0, 0)

    def test_a_failed_batch_is_counted_not_silently_dropped(self, isolated_db, monkeypatch):
        """Step 55: check_consistency_llm used to `except Exception: continue`
        with no trace at all -- a run that failed on every batch looked
        identical, from the caller's side, to one that genuinely found
        nothing. The batch that fails is now counted, and the batch after
        it still runs and still contributes its own issues."""
        monkeypatch.setattr("time.sleep", lambda *_: None)  # skip call_with_backoff's real retry delay

        class FlakyEngine:
            """Always fails on batch 1's own line, even across
            call_with_backoff's internal retry -- a plain call-counter
            would let the retry attempt land on batch 2 instead."""
            supports_reference = True
            model = "fake-model"

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                if "a -> b" in messages[0]["content"]:
                    raise RuntimeError("simulated API failure")
                text = '[{"term": "term", "variants": ["a", "b"], "note": "n"}]'
                return type("Resp", (), {"content": [_FakeBlock(text)]})()

        lines = [Line(idx=0, start=0, end=1, zh="a", en="b"),
                 Line(idx=1, start=1, end=2, zh="c", en="d")]
        issues, failed_batches, total_batches = te.check_consistency_llm(
            lines, FlakyEngine(), batch_size=1)
        assert failed_batches == 1
        assert total_batches == 2
        assert len(issues) == 1  # the batch that succeeded still contributes its own issue

    def test_a_batch_with_no_usable_response_is_also_counted_as_failed(self, monkeypatch):
        """call_llm_json returns its `fallback` (None, here) rather than
        raising when a provider's response can't be parsed at all -- that
        has to be counted the same as an outright exception."""
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": []}  # no content at all

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        issues, failed_batches, total_batches = te.check_consistency_llm(
            lines, te.GeminiEngine("fake-key"))
        assert issues == []
        assert failed_batches == 1
        assert total_batches == 1


class TestGenerateEpisodeSummary:
    """Step 74: one LLM call per finished episode producing a short
    running summary, fed forward into the next episode's translation
    prompt as fixed continuity context."""

    def test_pure_mt_engine_returns_empty_string_not_crash(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        assert te.generate_episode_summary(lines, PureMT()) == ""

    def test_no_translated_lines_returns_empty(self):
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]  # untranslated
        assert te.generate_episode_summary(lines, MockLlm()) == ""

    def test_generates_one_summary_from_a_valid_response(self):
        class FakeEngine:
            supports_reference = True
            model = "fake-model"

            def __init__(self):
                self.client = self
                self.messages = self
                self.call_count = 0

            def create(self, model, max_tokens, messages):
                self.call_count += 1
                text = '{"summary": "Xiaoling found the letter and confronted Wei."}'
                return type("Resp", (), {"content": [_FakeBlock(text)]})()

        lines = [Line(idx=0, start=0, end=1, zh="a", en="Hello."),
                 Line(idx=1, start=1, end=2, zh="b", en="Goodbye.")]
        engine = FakeEngine()
        summary = te.generate_episode_summary(lines, engine)
        assert summary == "Xiaoling found the letter and confronted Wei."

    def test_called_once_per_episode_not_once_per_batch(self):
        """The whole point of Step 74's design: this is a fixed,
        once-per-episode cost, unlike the per-batch translation calls --
        a many-line episode still results in exactly one LLM call."""
        class FakeEngine:
            supports_reference = True
            model = "fake-model"

            def __init__(self):
                self.client = self
                self.messages = self
                self.call_count = 0

            def create(self, model, max_tokens, messages):
                self.call_count += 1
                return type("Resp", (), {"content": [_FakeBlock('{"summary": "ok"}')]})()

        lines = [Line(idx=i, start=i, end=i + 1, zh=f"line {i}", en=f"Line {i}.")
                 for i in range(50)]
        engine = FakeEngine()
        te.generate_episode_summary(lines, engine)
        assert engine.call_count == 1

    def test_non_dict_response_returns_empty_string(self):
        class FakeEngine:
            supports_reference = True
            model = "fake-model"

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                return type("Resp", (), {"content": [_FakeBlock('["not", "an", "object"]')]})()

        lines = [Line(idx=0, start=0, end=1, zh="a", en="Hi.")]
        assert te.generate_episode_summary(lines, FakeEngine()) == ""

    def test_engine_failure_declines_quietly(self):
        class FailingEngine:
            supports_reference = True
            model = "fake-model"

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                raise RuntimeError("simulated API failure")

        lines = [Line(idx=0, start=0, end=1, zh="a", en="Hi.")]
        assert te.generate_episode_summary(lines, FailingEngine()) == ""

    def test_usage_cb_invoked_on_success(self):
        class FakeEngine:
            supports_reference = True
            model = "fake-model"

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                resp = type("Resp", (), {"content": [_FakeBlock('{"summary": "ok"}')],
                                          "usage": type("U", (), {"input_tokens": 100,
                                                                   "output_tokens": 20})()})()
                return resp

        calls = []
        lines = [Line(idx=0, start=0, end=1, zh="a", en="Hi.")]
        te.generate_episode_summary(lines, FakeEngine(), usage_cb=lambda i, o, **k: calls.append((i, o)))
        assert calls == [(100, 20)]


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeFlaggingEngine:
    """Claude-shaped fake, matching what flag_uncertain_lines dispatches
    on. flags_for maps a line_idx to a (reason, note) tuple to flag; every
    other line in the batch is left unflagged, mirroring how a real model
    should behave (most lines need no flag at all)."""
    supports_reference = True
    model = "fake-model"

    def __init__(self, flags_for=None):
        self.client = self
        self.messages = self
        self.flags_for = flags_for or {}
        self.call_count = 0

    def create(self, model, max_tokens, messages):
        self.call_count += 1
        import re as _re, json as _json
        prompt = messages[0]["content"]
        idxs = [int(m) for m in _re.findall(r"\[(\d+)\]", prompt)]
        out = []
        for i in idxs:
            if i in self.flags_for:
                reason, note = self.flags_for[i]
                out.append({"line_idx": i, "reason": reason, "note": note})
        return type("Resp", (), {"content": [_FakeBlock(_json.dumps(out))]})()


class TestFlagUncertainLines:
    def test_pure_mt_engine_leaves_lines_unflagged(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        te.flag_uncertain_lines(lines, PureMT())
        assert lines[0].flag is None

    def test_no_translated_lines_is_a_noop(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]
        engine = FakeFlaggingEngine(flags_for={0: ("uncertain_translation", "x")})
        te.flag_uncertain_lines(lines, engine)
        assert lines[0].flag is None
        assert engine.call_count == 0

    def test_flags_only_the_lines_the_model_names(self):
        lines = [Line(idx=0, start=0, end=1, zh="她昨天来了", en="She came yesterday."),
                 Line(idx=1, start=1, end=2, zh="你好", en="Hello.")]
        engine = FakeFlaggingEngine(flags_for={0: ("ambiguous_reference", "'她' unresolved")})
        te.flag_uncertain_lines(lines, engine, batch_size=10)
        assert lines[0].flag == "ambiguous_reference"
        assert lines[0].flag_note == "'她' unresolved"
        assert lines[1].flag is None  # not named by the model -- stays clean

    def test_unknown_reason_falls_back_to_uncertain_translation(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        engine = FakeFlaggingEngine(flags_for={0: ("made_up_reason_xyz", "note")})
        te.flag_uncertain_lines(lines, engine, batch_size=10)
        assert lines[0].flag == "uncertain_translation"

    def test_progress_cb_called_once_per_batch(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        seen = []
        te.flag_uncertain_lines(lines, FakeFlaggingEngine(), batch_size=3, progress_cb=seen.append)
        assert seen == [0.25, 0.5, 0.75, 1.0]

    def test_engine_failure_on_one_batch_does_not_raise(self):
        class BrokenEngine(FakeFlaggingEngine):
            def create(self, model, max_tokens, messages):
                raise RuntimeError("simulated API failure")
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        # Must not raise -- a flagging pass failing shouldn't block the rest
        # of the review workflow, same reasoning as check_consistency_llm.
        te.flag_uncertain_lines(lines, BrokenEngine())
        assert lines[0].flag is None

    def test_already_flagged_line_can_be_reflagged_on_a_later_run(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b", flag="name_uncertain", flag_note="old")]
        engine = FakeFlaggingEngine(flags_for={0: ("slang_idiom", "new note")})
        te.flag_uncertain_lines(lines, engine, batch_size=10)
        assert lines[0].flag == "slang_idiom"
        assert lines[0].flag_note == "new note"


class SequentialClaudeShapedEngine:
    """Claude-shaped fake returning canned raw response strings in order,
    one per call -- used to test the id-keyed retry mechanism directly
    (a first response missing an id, followed by a retry response)."""
    supports_reference = True
    model = "fake-model"

    def __init__(self, responses):
        self.client = self
        self.messages = self
        self.responses = list(responses)
        self.call_count = 0

    def create(self, model, max_tokens, messages):
        text = self.responses[self.call_count]
        self.call_count += 1
        return type("Resp", (), {"content": [_FakeBlock(text)]})()


class TestTagSpeakersLlm:
    """tag_speakers_by_id (was tag_speakers_llm) used to request a plain positional JSON array and
    zip/extend it onto zh_chunks by position -- the same misassignment
    bug class as translation itself. Now id-keyed, same mechanism as
    translate_batch's own fix."""

    def test_well_formed_object_response_assigns_labels_by_id(self):
        engine = SequentialClaudeShapedEngine(['{"1": "Xiaoling", "2": "Narrator"}'])
        labels = te.tag_speakers_by_id({1: "你好", 2: "那天下着雨。"}, engine)
        assert labels == {1: "Xiaoling", 2: "Narrator"}

    def test_a_response_missing_one_id_is_retried_and_lands_on_the_right_chunk(self):
        engine = SequentialClaudeShapedEngine([
            '{"1": "Xiaoling"}',  # id 2 missing from the first response
            '{"2": "Yun"}',       # retry supplies it
        ])
        labels = te.tag_speakers_by_id({1: "你好", 2: "你也好"}, engine)
        assert labels == {1: "Xiaoling", 2: "Yun"}

    def test_still_missing_after_retry_defaults_to_narrator_not_blank(self):
        engine = SequentialClaudeShapedEngine(['{"1": "Xiaoling"}', '{}'])
        labels = te.tag_speakers_by_id({1: "你好", 2: "你也好"}, engine)
        assert labels == {1: "Xiaoling", 2: "Narrator"}

    def test_reordered_and_extra_ids_land_on_their_own_chunks(self):
        engine = SequentialClaudeShapedEngine(['{"3": "Cara", "99": "Ghost", "1": "Ann", "2": "Narrator"}'])
        labels = te.tag_speakers_by_id({1: "a", 2: "b", 3: "c"}, engine)
        assert labels == {1: "Ann", 2: "Narrator", 3: "Cara"}

    def test_non_contiguous_ids_are_keyed_not_positional(self):
        engine = SequentialClaudeShapedEngine(['{"7": "Ann"}', '{"4": "Bob"}'])
        labels = te.tag_speakers_by_id({4: "a", 7: "b"}, engine)
        assert labels == {4: "Bob", 7: "Ann"}

    def test_pure_mt_engine_returns_all_narrator(self):
        class PureMT:
            supports_reference = False
        labels = te.tag_speakers_by_id({1: "a", 2: "b"}, PureMT())
        assert labels == {1: "Narrator", 2: "Narrator"}

    def test_ollama_engine_gets_real_labels_not_a_silent_all_narrator(self, monkeypatch):
        """Step 1d exit condition: with a fake Ollama server, every
        call_llm_json-backed feature returns a real, correctly shaped
        result -- not the old silent fallback (which for this particular
        feature happened to look like a plausible "Narrator" label,
        making the bug easy to miss without a test like this one)."""
        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": '{"1": "Xiaoling", "2": "Narrator"}'}}

        monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
        engine = te.OllamaEngine()
        labels = te.tag_speakers_by_id({1: "你好", 2: "那天下着雨。"}, engine)
        assert labels == {1: "Xiaoling", 2: "Narrator"}


class TestRewriteForPacingLlm:
    """rewrite_for_pacing_llm used to request a plain positional JSON
    array and zip() it onto the batch by position -- same bug class.
    Now id-keyed: a missing id leaves that line's existing .en
    untouched rather than a wrong rewrite landing on it."""

    def test_rewrites_lines_by_id(self):
        lines = [Line(idx=0, start=0, end=1, zh="z0", en="A very long original line."),
                 Line(idx=1, start=0, end=1, zh="z1", en="Another very long line.")]
        engine = SequentialClaudeShapedEngine(['{"1": "Short one.", "2": "Short two."}'])
        te.rewrite_for_pacing_llm(lines, engine)
        assert lines[0].en == "Short one."
        assert lines[1].en == "Short two."

    def test_a_missing_id_leaves_that_lines_en_untouched(self):
        lines = [Line(idx=0, start=0, end=1, zh="z0", en="Original one."),
                 Line(idx=1, start=0, end=1, zh="z1", en="Original two.")]
        engine = SequentialClaudeShapedEngine([
            '{"1": "Rewritten one."}',  # id 2 missing from the first response
            '{}',                        # retry also comes back empty
        ])
        te.rewrite_for_pacing_llm(lines, engine)
        assert lines[0].en == "Rewritten one."
        assert lines[1].en == "Original two."  # untouched, not blanked or misassigned

    def test_pure_mt_engine_is_a_noop(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="z0", en="Original.")]
        te.rewrite_for_pacing_llm(lines, PureMT())
        assert lines[0].en == "Original."

    def test_empty_list_is_a_noop(self):
        class Engine:
            supports_reference = True
        assert te.rewrite_for_pacing_llm([], Engine()) == []


class TestOfflineTestEngine:
    """The free dry-run engine exists so the pipeline can be validated with
    no API key, no network, and no spend. These guard that promise."""

    def test_registered_as_an_engine(self):
        assert "fake" in te.ENGINES

    def test_works_with_no_api_key(self):
        engine = te.get_engine("fake")
        assert engine is not None
        engine2 = te.get_engine("fake", None)
        assert engine2 is not None

    def test_translates_every_line_without_network(self):
        lines = [Line(idx=i, start=float(i), end=float(i) + 1, zh=f"第{i}句") for i in range(6)]
        engine = te.get_engine("fake")
        _, errors = te.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert errors == []
        assert all(ln.en for ln in lines)

    def test_output_is_obviously_placeholder(self):
        # Must never be mistakable for a real translation.
        lines = [Line(idx=0, start=0, end=1, zh="真实对白")]
        engine = te.get_engine("fake")
        te.translate_lines_with_engine(lines, engine, {})
        assert lines[0].en.startswith("[TEST]")

    def test_costs_nothing(self):
        assert te.estimate_cost("test-offline", 10_000_000, 10_000_000) == 0.0

    def test_records_usage_so_dashboard_path_is_exercised(self):
        lines = [Line(idx=0, start=0, end=1, zh="测试")]
        engine = te.get_engine("fake")
        te.translate_lines_with_engine(lines, engine, {})
        assert engine.last_usage["input_tokens"] > 0

    def test_long_lines_are_truncated_in_placeholder(self):
        lines = [Line(idx=0, start=0, end=1, zh="字" * 200)]
        engine = te.get_engine("fake")
        te.translate_lines_with_engine(lines, engine, {})
        assert len(lines[0].en) < 100

    def test_has_no_sdk_client_so_llm_features_decline_cleanly(self):
        # Free-form LLM helpers check for .client; None must not crash them.
        engine = te.get_engine("fake")
        assert engine.client is None
        # Step 55: a decline-cleanly response is still a batch that couldn't
        # actually be checked -- counted as failed, not silently zeroed out.
        assert te.check_consistency_llm(
            [Line(idx=0, start=0, end=1, zh="a", en="b")], engine) == ([], 1, 1)


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


class _ScriptedReflectEngine:
    """Claude-shaped fake for reflect_translate_batch -- returns the given
    JSON responses in order (one per call) and records the raw prompt
    text sent each time, so a test can check what each of the three
    passes actually asked for."""
    supports_reference = True
    model = "fake-model"

    def __init__(self, responses):
        self.client = self
        self.messages = self
        self.responses = list(responses)
        self.prompts = []

    def create(self, model, max_tokens, messages):
        self.prompts.append(messages[0]["content"])
        text = self.responses[len(self.prompts) - 1]
        return type("Resp", (), {"content": [_FakeBlock(text)]})()


class TestReflectTranslateBatch:
    """Step 7: Reflect mode's three-pass pipeline (faithfulness, reflection,
    expressiveness) -- id-keyed at every pass, like Step 1's translate_batch,
    not VideoLingo's own SequenceMatcher fuzzy matching."""

    def test_three_calls_per_batch(self):
        engine = _ScriptedReflectEngine([
            '{"1": "Draft one.", "2": "Draft two."}',
            '{"1": "reads awkwardly out loud"}',
            '{"1": "Final one.", "2": "Final two."}',
        ])
        translations, critiques = te.reflect_translate_batch(
            engine, ["你好", "再见"], {"line_ids": None})
        assert len(engine.prompts) == 3
        assert translations == ["Final one.", "Final two."]
        assert critiques == ["reads awkwardly out loud", ""]  # line 2's draft needed no critique

    def test_novel_reference_reaches_every_pass(self):
        """Step 9e: reflect_translate_batch used to call
        build_llm_instructions() directly, which never actually inserts
        novel_reference anywhere -- only build_stable_prompt()'s own
        novel_block does that. The instructions text told the model to
        consult "the reference novel translation below," but nothing was
        ever below it. Now goes through build_stable_prompt() like the
        normal (non-Reflect) path already does."""
        engine = _ScriptedReflectEngine([
            '{"1": "Draft."}', '{"1": "critique"}', '{"1": "Final."}'])
        te.reflect_translate_batch(
            engine, ["你好"], {"novel_reference": "Xiaoling always calls her 'sis'."})
        for prompt in engine.prompts:
            assert "Xiaoling always calls her 'sis'." in prompt
            assert "REFERENCE NOVEL TRANSLATION" in prompt

    def test_no_reference_block_when_none_is_given(self):
        engine = _ScriptedReflectEngine([
            '{"1": "Draft."}', '{"1": "critique"}', '{"1": "Final."}'])
        te.reflect_translate_batch(engine, ["你好"], {})
        for prompt in engine.prompts:
            assert "REFERENCE NOVEL TRANSLATION" not in prompt

    def test_each_pass_asks_for_something_different(self):
        engine = _ScriptedReflectEngine([
            '{"1": "Draft."}', '{"1": "critique"}', '{"1": "Final."}'])
        te.reflect_translate_batch(engine, ["你好"], {})
        assert "FAITHFULNESS" in engine.prompts[0]
        assert "REFLECTION" in engine.prompts[1]
        assert "Draft." in engine.prompts[1]  # the reflection pass sees pass 1's own draft
        assert "EXPRESSIVENESS" in engine.prompts[2]
        assert "critique" in engine.prompts[2]  # the rewrite pass sees the critique

    def test_permanent_line_ids_are_kept_through_every_pass(self):
        engine = _ScriptedReflectEngine([
            '{"101": "Draft one.", "205": "Draft two."}',
            '{"101": "a critique"}',
            '{"101": "Final one.", "205": "Final two."}',
        ])
        translations, critiques = te.reflect_translate_batch(
            engine, ["你好", "再见"], {"line_ids": [101, 205]})
        assert translations == ["Final one.", "Final two."]
        assert critiques == ["a critique", ""]
        for prompt in engine.prompts:
            assert "101." in prompt and "205." in prompt  # ids, not 1-based positions

    def test_a_missing_id_in_one_pass_only_affects_that_lines_result(self):
        # Line 2's faithfulness draft never comes back (even after the
        # built-in retry) -- it must not be sent to reflection at all, and
        # line 1's result must be completely unaffected.
        engine = _ScriptedReflectEngine([
            '{"1": "Draft one."}',       # pass 1, first attempt: line 2 missing
            '{}',                        # pass 1, retry: still missing
            '{"1": "a critique"}',       # pass 2: only line 1 was ever asked about
            '{"1": "Final one."}',       # pass 3: line 2 has nothing to rewrite
        ])
        translations, critiques = te.reflect_translate_batch(engine, ["你好", "再见"], {})
        assert translations == ["Final one.", ""]
        assert critiques == ["a critique", ""]
        # Confirms line 2 was never put in front of the reflection pass:
        assert "再见" not in engine.prompts[2]

    def test_final_translation_falls_back_to_the_faithful_draft(self):
        # Expressiveness never returns a rewrite for line 1, even after its
        # own built-in retry -- its draft translation is used as-is rather
        # than losing the line entirely.
        engine = _ScriptedReflectEngine([
            '{"1": "Draft one."}', '{"1": "a critique"}', '{}', '{}'])
        translations, critiques = te.reflect_translate_batch(engine, ["你好"], {})
        assert translations == ["Draft one."]
        assert critiques == ["a critique"]

    def test_a_draft_with_no_critique_is_not_sent_to_the_reflection_pass(self):
        engine = _ScriptedReflectEngine(['{"1": ""}', '{}', '{"1": ""}'])
        # An empty draft (id came back with "" -- treated as no usable draft
        # since "" isn't kept by _parse_id_keyed_json in the first place;
        # simulate directly missing instead):
        engine2 = _ScriptedReflectEngine(['{}', '{}', '{}'])
        translations, critiques = te.reflect_translate_batch(engine2, ["你好"], {})
        assert len(engine2.prompts) == 2  # pass 1 (x1, no retry-worthy content) + pass 3; pass 2 skipped
        assert translations == [""]
        assert critiques == [""]

    def test_requires_an_llm_capable_engine(self):
        class TranslationOnlyEngine:
            supports_reference = False
        with pytest.raises(RuntimeError):
            te.reflect_translate_batch(TranslationOnlyEngine(), ["你好"], {})


class TestReflectModeInTranslateLinesWithEngine:
    """The integration surface run_translate_job actually uses: reflect=True
    routes each batch through reflect_translate_batch instead of
    engine.translate_batch, and notes_cb receives the reflection critiques
    in translation_guide's translation-notes shape."""

    def test_reflect_mode_writes_translations_and_notes(self):
        engine = _ScriptedReflectEngine([
            '{"11": "Draft one.", "22": "Draft two."}',
            '{"11": "a bit stiff"}',
            '{"11": "Final one.", "22": "Final two."}',
        ])
        lines = [Line(idx=0, start=0, end=1, zh="你好", id=11),
                 Line(idx=1, start=1, end=2, zh="再见", id=22)]
        captured_notes = []
        te.translate_lines_with_engine(
            lines, engine, {}, batch_size=10, reflect=True,
            notes_cb=captured_notes.append)
        assert [ln.en for ln in lines] == ["Final one.", "Final two."]
        assert len(captured_notes) == 1  # one notes_cb call, for this one batch
        notes = captured_notes[0]
        assert notes == [{"line_idx": 0, "term": "", "note_type": "reflection", "note": "a bit stiff"}]

    def test_reflect_false_never_touches_reflect_translate_batch(self, monkeypatch):
        called = []
        monkeypatch.setattr(te, "reflect_translate_batch", lambda *a, **k: called.append(1))

        class PlainEngine:
            supports_reference = True
            def translate_batch(self, zh_lines, context):
                return [f"EN:{z}" for z in zh_lines]
        lines = [Line(idx=0, start=0, end=1, zh="你好")]
        te.translate_lines_with_engine(lines, PlainEngine(), {}, batch_size=10, reflect=False)
        assert called == []
        assert lines[0].en == "EN:你好"

    def test_notes_cb_is_not_called_when_nothing_needed_a_critique(self):
        engine = _ScriptedReflectEngine(['{"1": "Draft."}', '{}', '{"1": "Final."}'])
        lines = [Line(idx=0, start=0, end=1, zh="你好")]
        captured = []
        te.translate_lines_with_engine(lines, engine, {}, batch_size=10, reflect=True,
                                       notes_cb=captured.append)
        assert captured == []


class TestEstimateReflectModeCost:
    def test_costs_roughly_three_times_a_normal_estimate(self):
        engine = te.ClaudeEngine.__new__(te.ClaudeEngine)
        engine.model = "claude-sonnet-5"
        lines = ["你好世界" * 10] * 5
        chars = sum(len(z) for z in lines)
        input_tokens = int(chars / 3.5) + 300
        output_tokens = int(chars / 2.5)
        expected_single = te.estimate_cost_for_engine(engine, input_tokens, output_tokens)
        assert te.estimate_reflect_mode_cost(engine, lines) == pytest.approx(expected_single * 3)

    def test_free_tier_gemini_reflect_estimate_is_zero(self):
        engine = te.GeminiEngine("fake-key", free_tier=True)
        assert te.estimate_reflect_mode_cost(engine, ["你好"] * 20) == 0.0


# ---------------------------------------------------------------------------
# Step 9: prompt caching -- the stable part of the prompt must be
# byte-identical across every batch of one drama, with everything that
# changes per batch after it.
# ---------------------------------------------------------------------------

class _FakeClaudeUsage:
    def __init__(self, input_tokens=100, output_tokens=20, cache_read=0, cache_write=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


class _FakeClaudeMessages:
    """Records every messages.create call and answers with each requested
    line id translated, so no batch ever needs a retry."""
    def __init__(self, usage=None):
        self.calls = []
        self.usage = usage or _FakeClaudeUsage()

    def create(self, **kwargs):
        import json as _json
        import re as _re
        self.calls.append(kwargs)
        user = kwargs["messages"][0]["content"]
        numbered = user.split("Translate these lines:\n\n", 1)[1]
        ids = _re.findall(r"^(\d+)\. ", numbered, flags=_re.M)
        text = _json.dumps({i: f"EN{i}" for i in ids})

        class _Block:
            type = "text"
        block = _Block()
        block.text = text

        class _Resp:
            pass
        resp = _Resp()
        resp.content = [block]
        resp.usage = self.usage
        return resp


def _claude_engine_with_fake_client(usage=None):
    engine = te.ClaudeEngine("sk-ant-fake")
    engine.client = type("C", (), {})()
    engine.client.messages = _FakeClaudeMessages(usage)
    return engine


def _six_lines():
    return [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", id=100 + i) for i in range(6)]


class TestStablePromptPrefix:
    def _run_claude(self, **kw):
        engine = _claude_engine_with_fake_client()
        lines = _six_lines()
        te.translate_lines_with_engine(
            lines, engine, {"title_en": "Drama"}, batch_size=2,
            novel_reference="Reference novel text.", glossary_terms=[
                {"term_original": "苏杉", "term_translation": "Su Shan"}],
            style_guidelines="Keep it warm.", **kw)
        return engine.client.messages.calls, lines

    def test_claude_system_blocks_are_byte_identical_across_batches(self):
        calls, lines = self._run_claude()
        assert len(calls) == 3
        systems = [repr(c["system"]) for c in calls]
        assert systems[0] == systems[1] == systems[2]
        assert [ln.en for ln in lines] == [f"EN{100 + i}" for i in range(6)]

    def test_per_batch_context_lives_in_the_user_message_only(self):
        calls, _ = self._run_claude()
        # Batch 2 sees batch 1's translations (look-back) and batch 3's
        # source (look-ahead) -- both must be in the user message, and
        # neither may leak into the cached system prompt.
        second_user = calls[1]["messages"][0]["content"]
        assert "第0句 -> EN100" in second_user
        assert "第4句" in second_user.split("Translate these lines:")[0]
        system_text = "".join(b["text"] for b in calls[1]["system"])
        assert "第0句" not in system_text and "第4句" not in system_text
        assert calls[0]["messages"][0]["content"] != calls[1]["messages"][0]["content"]

    def test_the_last_stable_block_carries_cache_control(self):
        calls, _ = self._run_claude()
        system = calls[0]["system"]
        assert system[-1].get("cache_control") == {"type": "ephemeral"}
        assert "Reference novel text." in system[-1]["text"]
        assert all("cache_control" not in b for b in system[:-1])

    def test_without_a_novel_reference_the_instructions_block_is_cached(self):
        blocks = te.build_claude_system_blocks({"drama_meta": {}})
        assert len(blocks) == 1
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_gemini_system_instruction_is_byte_identical_across_batches(self, monkeypatch):
        import json as _json
        import re as _re
        bodies = []

        class FakeResponse:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                user = self.body["contents"][0]["parts"][0]["text"]
                ids = _re.findall(r"^(\d+)\. ", user.split("Translate these lines:\n\n", 1)[1],
                                  flags=_re.M)
                return {"candidates": [{"content": {"parts": [
                    {"text": _json.dumps({i: "x" for i in ids})}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            bodies.append(json)
            return FakeResponse(json)

        monkeypatch.setattr("requests.post", fake_post)
        te.translate_lines_with_engine(_six_lines(), te.GeminiEngine("k"), {}, batch_size=2,
                                       novel_reference="Ref.")
        assert len(bodies) == 3
        assert bodies[0]["systemInstruction"] == bodies[1]["systemInstruction"] == \
            bodies[2]["systemInstruction"]


class TestBoundedNovelReference:
    """Step 50: build_stable_prompt() used to append context['novel_reference']
    to every batch's prompt whole and unbounded, however long the reference
    novel was -- crowding out instructions/dialogue, inflating cost, or
    exceeding a provider's context limit outright. It's now selected per
    batch by _select_relevant_novel_passages(), bounded to
    te.NOVEL_REFERENCE_BUDGET_CHARS, and scored for relevance against the
    batch's own speaker names / glossary hits rather than always sending the
    novel's opening passage."""

    def test_a_reference_at_or_under_budget_is_returned_whole(self):
        ref = "Short reference, well under budget."
        assert te._select_relevant_novel_passages(ref, budget_chars=1000) == ref

    def test_a_long_reference_with_no_relevance_signal_is_still_bounded(self):
        ref = "\n\n".join(f"Filler paragraph {i}. " * 20 for i in range(10))
        assert len(ref) > 500
        excerpt = te._select_relevant_novel_passages(ref, budget_chars=500)
        assert len(excerpt) <= 500
        assert excerpt != ref
        # Falls back to the beginning, still bounded, not silently empty --
        # there's no relevance signal to rank by here.
        assert excerpt.startswith("Filler paragraph 0.")

    def test_selects_the_paragraph_matching_the_batchs_speaker_name(self):
        paragraphs = [
            "Opening chapter, sets the scene, no character named yet. " * 5,
            "A quiet scene about the weather, nothing relevant here. " * 5,
            "Xiaoling smiled and said something warm to her friend. " * 5,
        ]
        ref = "\n\n".join(paragraphs)
        excerpt = te._select_relevant_novel_passages(
            ref, speaker_labels=["Xiaoling"], budget_chars=200)
        assert "Xiaoling" in excerpt
        assert "Opening chapter" not in excerpt

    def test_selects_the_paragraph_matching_a_glossary_terms_english_side(self):
        paragraphs = [
            "Opening chapter, sets the scene, nothing relevant here. " * 5,
            "Su Shan walked quietly through the garden at dusk. " * 5,
        ]
        ref = "\n\n".join(paragraphs)
        excerpt = te._select_relevant_novel_passages(
            ref, batch_source_lines=["苏杉说了什么"],
            glossary_terms=[{"term_original": "苏杉", "term_translation": "Su Shan"}],
            budget_chars=200)
        assert "Su Shan" in excerpt
        assert "Opening chapter" not in excerpt

    def test_build_stable_prompt_records_which_excerpt_a_batch_actually_saw(self):
        """Step 50 item 3: the passages a batch actually saw are recorded
        on its own context, the same pattern as speaker_labels/line_ids,
        so a later "why did this line translate this way" check has
        something to look at."""
        context = {"drama_meta": {}, "novel_reference": "Short reference, under budget.",
                  "batch_source_lines": ["你好"]}
        _, novel_block = te.build_stable_prompt(context)
        assert context["novel_reference_excerpt_used"] == "Short reference, under budget."
        assert "Short reference, under budget." in novel_block

    def test_translate_lines_with_engine_sends_a_bounded_relevant_excerpt_per_batch(self):
        engine = _claude_engine_with_fake_client()
        lines = [
            Line(idx=0, start=0, end=1, zh="第0句", id=100, speaker="A"),
            Line(idx=1, start=1, end=2, zh="第1句", id=101, speaker="B"),
        ]
        paragraphs = [
            "Opening chapter, sets the scene, no character named yet. " * 40,
            "Filler about the weather that nobody asked for. " * 40,
            "Xiaoling walked into the room and smiled warmly. " * 40,
            "Bo Wen sat quietly by the window, deep in thought. " * 40,
        ]
        ref = "\n\n".join(paragraphs)
        assert len(ref) > te.NOVEL_REFERENCE_BUDGET_CHARS
        te.translate_lines_with_engine(
            lines, engine, {}, batch_size=1, novel_reference=ref,
            character_names={"A": "Xiaoling", "B": "Bo Wen"})
        calls = engine.client.messages.calls
        assert len(calls) == 2
        system_0 = "".join(b["text"] for b in calls[0]["system"])
        system_1 = "".join(b["text"] for b in calls[1]["system"])
        # Each batch gets the passage naming ITS OWN speaker, not the
        # novel's opening chapter every time, and not the whole file.
        assert "Xiaoling walked into the room" in system_0
        assert "Bo Wen sat quietly" not in system_0
        assert "Bo Wen sat quietly" in system_1
        assert "Xiaoling walked into the room" not in system_1
        assert len(system_0) < len(ref) and len(system_1) < len(ref)


class TestCacheUsageAccounting:
    def test_claude_usage_counts_cached_tokens_as_part_of_the_prompt(self):
        u = te.claude_usage(_FakeClaudeUsage(input_tokens=50, output_tokens=10,
                                             cache_read=900, cache_write=0))
        assert u == {"input_tokens": 950, "output_tokens": 10,
                     "cache_read_tokens": 900, "cache_write_tokens": 0}

    def test_gemini_usage_reads_cached_content_token_count(self):
        u = te.gemini_usage({"promptTokenCount": 1000, "candidatesTokenCount": 5,
                             "cachedContentTokenCount": 800})
        assert u["input_tokens"] == 1000 and u["cache_read_tokens"] == 800

    def test_cache_reads_cost_less_than_uncached_input(self):
        full = te.estimate_cost("claude-sonnet-5", 1_000_000, 0)
        cached = te.estimate_cost("claude-sonnet-5", 1_000_000, 0, cache_read_tokens=1_000_000)
        assert cached == pytest.approx(full * te.CACHE_READ_PRICE_FACTOR)

    def test_cache_writes_cost_more_than_uncached_input(self):
        full = te.estimate_cost("claude-sonnet-5", 1_000_000, 0)
        written = te.estimate_cost("claude-sonnet-5", 1_000_000, 0, cache_write_tokens=1_000_000)
        assert written == pytest.approx(full * te.CACHE_WRITE_PRICE_FACTOR)

    def test_usage_cb_receives_cache_tokens_per_batch(self):
        engine = _claude_engine_with_fake_client(
            _FakeClaudeUsage(input_tokens=10, output_tokens=5, cache_read=90))
        seen = []
        te.translate_lines_with_engine(_six_lines(), engine, {}, batch_size=3,
                                       usage_cb=lambda *a: seen.append(a))
        assert seen == [(100, 5, 90, 0), (100, 5, 90, 0)]


# ---------------------------------------------------------------------------
# Step 9: spending caps
# ---------------------------------------------------------------------------

class _PricedEngine:
    """Every batch reports 1M input tokens on claude-sonnet-5 -- $2.00 a
    batch at PRICING_PER_MILLION_TOKENS, so cap arithmetic is exact."""
    name = "claude"
    supports_reference = True
    model = "claude-sonnet-5"

    def __init__(self):
        self.calls = 0
        self.last_usage = {}

    def translate_batch(self, zh_lines, context):
        self.calls += 1
        self.last_usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        return [f"EN:{z}" for z in zh_lines]


class TestCostCap:
    def _lines(self, n=6):
        return [Line(idx=i, start=i, end=i + 1, zh=f"l{i}") for i in range(n)]

    def test_stops_after_the_batch_that_reaches_the_cap_and_keeps_finished_lines(self):
        lines, engine, seen = self._lines(), _PricedEngine(), []
        _, errors = te.translate_lines_with_engine(
            lines, engine, {}, batch_size=2, cost_cap_usd=3.0, cap_cb=seen.append)
        assert engine.calls == 2
        assert [ln.en for ln in lines] == ["EN:l0", "EN:l1", "EN:l2", "EN:l3", "", ""]
        assert seen == [pytest.approx(4.0)]
        assert errors == []

    def test_no_cap_runs_everything(self):
        lines, engine = self._lines(), _PricedEngine()
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert engine.calls == 3 and all(ln.en for ln in lines)

    def test_reaching_the_cap_on_the_last_batch_is_not_reported_as_a_stop(self):
        lines, engine, seen = self._lines(4), _PricedEngine(), []
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, cost_cap_usd=4.0,
                                       cap_cb=seen.append)
        assert engine.calls == 2 and all(ln.en for ln in lines)
        assert seen == []

    def test_a_free_tier_engine_never_hits_a_cap(self):
        lines, engine = self._lines(), _PricedEngine()
        engine.free_tier = True
        te.translate_lines_with_engine(lines, engine, {}, batch_size=2, cost_cap_usd=0.01)
        assert engine.calls == 3


class TestResolveCostCap:
    def test_no_caps_means_no_cap(self):
        assert te.resolve_cost_cap(None, None, 5.0) == (None, None)
        assert te.resolve_cost_cap(0, 0, 5.0) == (None, None)

    def test_job_cap_alone(self):
        assert te.resolve_cost_cap(2.5, None, 100.0) == (2.5, None)

    def test_monthly_remaining_alone(self):
        assert te.resolve_cost_cap(None, 10.0, 7.5) == (pytest.approx(2.5), None)

    def test_the_tighter_of_the_two_wins(self):
        assert te.resolve_cost_cap(1.0, 10.0, 7.5)[0] == 1.0
        assert te.resolve_cost_cap(5.0, 10.0, 7.5)[0] == pytest.approx(2.5)

    def test_monthly_cap_used_up_refuses(self):
        cap, refusal = te.resolve_cost_cap(5.0, 10.0, 10.0)
        assert cap is None
        assert "already used up" in refusal


class TestEstimateTranslationCost:
    def test_reflect_estimate_is_three_times_a_normal_run(self):
        engine = _PricedEngine()
        lines = ["你好世界" * 50] * 10
        assert te.estimate_reflect_mode_cost(engine, lines) == pytest.approx(
            3 * te.estimate_translation_cost(engine, lines))

    def test_free_tier_estimates_zero(self):
        engine = _PricedEngine()
        engine.free_tier = True
        assert te.estimate_translation_cost(engine, ["你好"] * 100) == 0.0


class TestGemini31FlashLite:
    """Step 9: Gemini 3.1 Flash-Lite is selectable, never the default."""

    def test_selectable_but_not_the_default(self):
        keys = list(te.GEMINI_MODELS)
        assert "gemini-3.1-flash-lite" in keys
        assert keys[0] == "gemini-flash-lite-latest"
        assert te.GeminiEngine("k").model == "gemini-flash-lite-latest"

    def test_priced_at_the_cheaper_rate(self):
        assert te.PRICING_PER_MILLION_TOKENS["gemini-3.1-flash-lite"] == {"input": 0.25, "output": 1.50}
        assert te.estimate_cost("gemini-3.1-flash-lite", 1_000_000, 1_000_000) == pytest.approx(1.75)

    def test_reaches_the_api_with_the_right_model_id(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"1": "Hi."}'}]}}]}

        monkeypatch.setattr("requests.post", lambda url, headers=None, json=None, timeout=None:
                            captured.update(url=url) or FakeResponse())
        engine = te.get_engine("gemini", "k", "gemini-3.1-flash-lite")
        assert engine.translate_batch(["你好"], {}) == ["Hi."]
        assert captured["url"].endswith("/models/gemini-3.1-flash-lite:generateContent")


class _Stop(Exception):
    pass


class TestCancelBetweenBatches:
    """B-05: cancel_check runs before every batch, so a raise stops the run
    without sending the remaining batches."""

    def _stop_after(self, n):
        seen = []

        def check():
            if len(seen) >= n:
                raise _Stop()
            seen.append(1)
        return check

    def test_flag_stops_between_batches(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        engine = FakeFlaggingEngine()
        with pytest.raises(_Stop):
            te.flag_uncertain_lines(lines, engine, batch_size=3, cancel_check=self._stop_after(1))
        assert engine.call_count == 1

    def test_consistency_stops_between_batches(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        engine = FakeFlaggingEngine()
        with pytest.raises(_Stop):
            te.check_consistency_llm(lines, engine, batch_size=3, cancel_check=self._stop_after(2))
        assert engine.call_count == 2

    def test_notes_stops_between_batches(self):
        import translation_guide as tg
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        engine = FakeFlaggingEngine()
        with pytest.raises(_Stop):
            tg.generate_translation_notes_llm(lines, engine, batch_size=3,
                                              cancel_check=self._stop_after(0))
        assert engine.call_count == 0


def test_failed_flag_batches_are_logged_not_silently_clean(monkeypatch):
    import applog
    seen = []

    class Log:
        def warning(self, msg, *args):
            seen.append(msg % args)
    monkeypatch.setattr(applog, "get_logger", lambda: Log())

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(te, "call_llm_json", boom)
    lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(4)]
    te.flag_uncertain_lines(lines, FakeFlaggingEngine(), batch_size=2)
    assert seen == ["flag check failed for 2 of 2 batches; their lines were not checked"]
