"""
tests/test_translate_engines.py -- tests for the engine_backends package's
core logic: rate-limit backoff vs. fast-fail, crash-safe/resumable
batch translation, and locale + glossary prompt construction (providers,
side tasks and costs are in test_engine_providers, test_engine_llm_tasks
and test_engine_costs). Uses mock engines instead of real API calls.
"""

import sys
import os
import time
import inspect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine_backends import (
    translate_pipeline,
    reflect_pass,
    llm_tasks,
    engine_registry,
)
from engine_backends import shared as engine_shared
from engine_backends import line_text
from engine_backends import fallback as engine_fallback
from engine_backends import gemini as engine_gemini
from engine_backends import claude as engine_claude
from engine_backends import prompts as engine_prompts
from engine_backends import pricing as engine_pricing
from core import Line


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

        token = engine_shared._cancel_check_var.set(check)
        try:
            monkeypatch.setattr("time.sleep", lambda sec: (slept.append(sec),
                                                           cancelled.__setitem__("v", True)))
            with pytest.raises(engine_shared.TranslationCancelled):
                engine_shared.call_with_backoff(always_429, base_delay=30.0)
        finally:
            engine_shared._cancel_check_var.reset(token)
        assert calls["n"] == 1
        assert max(slept) <= engine_shared._SLEEP_SLICE_SECONDS  # never one long sleep

    def test_translate_run_passes_its_cancel_check_to_the_backoff(self, monkeypatch):
        self._sleeps(monkeypatch)
        cancelled = {"v": False}

        class Engine:
            def translate_batch(self, zh, ctx):
                cancelled["v"] = True
                raise RateLimitError("429")

        lines = [Line(idx=0, start=0, end=1, zh="a")]
        _, errors = translate_pipeline.translate_lines_with_engine(
            lines, Engine(), {}, cancel_check_cb=lambda: cancelled["v"])
        assert errors == [] and lines[0].en == ""  # a cancel is not a failed batch

    def test_429_text_match_ignores_other_numbers_and_known_statuses(self):
        assert engine_shared._is_rate_limit_error(Exception("HTTP 429 Too Many Requests"))
        assert not engine_shared._is_rate_limit_error(Exception("failed at line 14290"))

        class Other(Exception):
            status_code = 500
        assert not engine_shared._is_rate_limit_error(Other("upstream said 429 somewhere"))

    def test_exhausted_fallback_chain_is_not_backed_off_again(self, monkeypatch):
        slept = self._sleeps(monkeypatch)
        monkeypatch.setattr("engine_backends.fallback._fallback_sleep", lambda s: None)
        calls = {"n": 0}

        class Always429:
            name = "x"
            model = "m"

            def translate_batch(self, zh, ctx):
                calls["n"] += 1
                raise RateLimitError("429")

        chain = engine_fallback.FallbackEngine([Always429()], ["x"])
        with pytest.raises(RateLimitError):
            engine_shared.call_with_backoff(lambda: chain.translate_batch(["a"], {}))
        assert calls["n"] == 1 + engine_fallback.FALLBACK_TRANSIENT_RETRIES  # not 6x that
        assert slept == []

    def test_daily_limit_stops_the_run_with_a_clear_error(self, monkeypatch):
        self._sleeps(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = engine_gemini.gemini_free_tier_limits_for(engine.model)["rpd"]
        now = time.monotonic()
        engine._free_tier_daily_request_times = [now] * limit
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        _, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert len(errors) == 1  # stopped after the first batch
        assert "daily request limit" in errors[0]["error"]


class TestCallWithBackoff:
    def test_succeeds_immediately_when_no_error(self):
        result = engine_shared.call_with_backoff(lambda: "ok")
        assert result == "ok"

    def test_rate_limit_retries_and_eventually_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RateLimitError("429")
            return "success"

        result = engine_shared.call_with_backoff(flaky, base_delay=0.01, max_delay=0.05)
        assert result == "success"
        assert calls["n"] == 3

    def test_generic_error_fails_fast_after_one_retry(self):
        calls = {"n": 0}

        def always_broken():
            calls["n"] += 1
            raise GenericError("bad api key")

        start = time.time()
        try:
            engine_shared.call_with_backoff(always_broken, base_delay=0.01)
            assert False, "should have raised"
        except GenericError:
            pass
        elapsed = time.time() - start
        assert calls["n"] == 2  # one try + one quick retry, not 5 backoff attempts
        assert elapsed < 3  # should not have waited through exponential backoff

    def test_is_rate_limit_error_detects_status_code(self):
        assert engine_shared._is_rate_limit_error(RateLimitError("x")) is True

    @pytest.mark.parametrize("status", [503, 529])
    def test_overloaded_statuses_get_a_backoff_too(self, status):
        # The SDK clients never retry on their own, so this is the only retry
        # an overloaded provider gets.
        err = type("ServerError", (Exception,), {"status_code": status})("x")
        assert engine_shared._is_rate_limit_error(err) is True
        assert engine_shared._is_rate_limit_error(
            type("ServerError", (Exception,), {"status_code": 500})("x")) is False

    def test_is_rate_limit_error_rejects_generic_errors(self):
        assert engine_shared._is_rate_limit_error(GenericError("x")) is False

    def test_is_rate_limit_error_detects_429_in_message(self):
        assert engine_shared._is_rate_limit_error(Exception("HTTP 429 Too Many Requests")) is True


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
        result, errors = translate_pipeline.translate_lines_with_engine(lines, MockEngine(), {}, batch_size=2)
        assert errors == []
        assert all(ln.en for ln in result)

    @pytest.mark.parametrize("make, calls, largest", [
        (lambda: engine_gemini.GeminiEngine("fake-key", model="gemini-pro-latest"), 2, 60),
        (lambda: engine_claude.ClaudeEngine("sk-ant-fake", model="claude-sonnet-5-5"), 3, 30),
        (lambda: engine_claude.ClaudeEngine("sk-ant-fake", model="claude-haiku-4-5-20251001"), 3, 30),
    ])
    def test_unset_batch_size_follows_the_engines_default(self, make, calls, largest):
        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(70)]
        engine = make()
        sent = []
        engine.translate_batch = lambda zh_lines, context: (sent.append(len(zh_lines)),
                                                             [f"EN:{z}" for z in zh_lines])[1]
        translate_pipeline.translate_lines_with_engine(lines, engine, {})
        assert len(sent) == calls and max(sent) == largest

    def test_skips_already_translated_lines_by_default(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="already done"),
                 Line(idx=1, start=0, end=1, zh="b")]
        engine = MockEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10)
        assert lines[0].en == "already done"  # untouched
        assert lines[1].en == "EN:b"

    def test_force_retranslate_redoes_everything(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="old translation")]
        engine = MockEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10, force_retranslate=True)
        assert lines[0].en == "EN:a"  # overwritten

    def test_no_lines_need_translation_returns_immediately(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="done")]
        progress_calls = []
        result, errors = translate_pipeline.translate_lines_with_engine(
            lines, MockEngine(), {}, progress_cb=lambda f: progress_calls.append(f))
        assert errors == []
        assert progress_calls == [1.0]  # signals immediate completion

    def test_save_cb_called_after_every_batch(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        save_calls = []
        translate_pipeline.translate_lines_with_engine(
            lines, MockEngine(), {}, batch_size=2,
            save_cb=lambda ls: save_calls.append(sum(1 for l in ls if l.en)))
        assert save_calls == [2, 4, 6]  # incremental progress after each batch

    def test_batch_failure_is_isolated_others_still_translate(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        # batch_size=2 groups lines into (l0,l1), (l2,l3), (l4,l5) --
        # make the second batch persistently fail via its content
        engine = MockEngine(fail_on_zh_containing="l2")
        result, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        translated_count = sum(1 for l in result if l.en)
        assert translated_count == 4  # 2 of 3 batches succeeded (the l2/l3 batch failed)
        assert len(errors) == 1

    def test_resuming_after_partial_failure_only_retries_missing_lines(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine1 = MockEngine(fail_on_zh_containing="l2")  # second batch (l2,l3) fails
        translate_pipeline.translate_lines_with_engine(lines, engine1, {}, batch_size=2)
        untranslated_before = [l for l in lines if not l.en]
        assert len(untranslated_before) == 2

        engine2 = MockEngine()  # fresh engine, no failures this time
        translate_pipeline.translate_lines_with_engine(lines, engine2, {}, batch_size=2)
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
        translate_pipeline.translate_lines_with_engine(lines, RecordingEngine(), {"source_language": "ja"})
        assert seen_context["source_language"] == "ja"

    def test_source_language_defaults_to_zh_when_missing(self):
        seen_context = {}

        class RecordingEngine:
            supports_reference = False

            def translate_batch(self, zh_lines, context):
                seen_context.update(context)
                return [f"EN:{t}" for t in zh_lines]

        lines = [Line(idx=0, start=0, end=1, zh="a")]
        translate_pipeline.translate_lines_with_engine(lines, RecordingEngine(), {})
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
                    raise engine_shared.ContentModerationBlocked("blocktest", "simulated safety block")
                return [f"EN:{z}" for z in zh_lines]

        lines = [Line(idx=0, start=0, end=1, zh="a BLOCKED passage"),
                 Line(idx=1, start=0, end=1, zh="a perfectly normal line")]
        engine = BlockedOnContentEngine()
        result, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)

        blocked_line, clean_line = result[0], result[1]
        assert blocked_line.flag == "content_blocked"
        assert "blocktest" in blocked_line.flag_note
        assert "simulated safety block" in blocked_line.flag_note
        assert not blocked_line.en
        assert clean_line.en == "EN:a perfectly normal line"  # not blanked out by the other line's block
        assert clean_line.flag is None
        assert len(errors) == 1
        # whole batch, then each bisected half -- narrowed down, not a full
        # per-line search on every batch. A blocked attempt is sent once:
        # call_with_backoff does not retry a content block (it was billed,
        # and the same text is blocked again).
        assert engine.calls == [
            ["a BLOCKED passage", "a perfectly normal line"],
            ["a BLOCKED passage"],
            ["a perfectly normal line"],
        ]


class TestBuildLlmInstructions:
    def test_locale_us_default(self):
        instructions = engine_prompts.build_llm_instructions("", {})
        assert "American English" in instructions

    def test_locale_gb_uses_british_spelling_note(self):
        instructions = engine_prompts.build_llm_instructions("", {}, locale="en-GB")
        assert "British English" in instructions
        assert "colour" in instructions

    def test_glossary_terms_included(self):
        glossary = [{"term_original": "拾", "term_translation": "Shi", "notes": "surname"}]
        instructions = engine_prompts.build_llm_instructions("", {}, glossary_terms=glossary)
        assert "拾 -> Shi" in instructions
        assert "surname" in instructions

    def test_no_glossary_omits_glossary_section(self):
        instructions = engine_prompts.build_llm_instructions("", {}, glossary_terms=None)
        assert "Series glossary" not in instructions

    def test_style_note_included_when_provided(self):
        instructions = engine_prompts.build_llm_instructions("keep it warm", {})
        assert "keep it warm" in instructions

    def test_drama_metadata_included(self):
        instructions = engine_prompts.build_llm_instructions(
            "", {"title_en": "My Drama", "author": "An Author"})
        assert "My Drama" in instructions
        assert "An Author" in instructions

    def test_defaults_to_chinese_when_source_language_unset(self):
        instructions = engine_prompts.build_llm_instructions("", {})
        assert "Chinese baihe" in instructions

    def test_japanese_source_language_reaches_the_prompt(self):
        """Regression test for a real bug found during audit: the system
        prompt used to hardcode "Chinese baihe" regardless of the drama's
        actual source language, so a Japanese or Korean drama's own
        translator was told it was translating Chinese the whole time."""
        instructions = engine_prompts.build_llm_instructions("", {"source_language": "ja"})
        assert "Japanese baihe" in instructions
        assert "Chinese baihe" not in instructions

    def test_korean_source_language_reaches_the_prompt(self):
        instructions = engine_prompts.build_llm_instructions("", {"source_language": "ko"})
        assert "Korean baihe" in instructions

    def test_content_mode_reaches_the_prompt(self):
        instructions = engine_prompts.build_llm_instructions(
            "", {"source_language": "zh", "content_mode": "novel_narration"})
        assert "novel" in instructions

    def test_streamer_vod_content_mode_reaches_the_prompt(self):
        instructions = engine_prompts.build_llm_instructions(
            "", {"source_language": "ja", "content_mode": "streamer_vod"})
        assert "livestream VOD" in instructions
        assert "Japanese baihe" in instructions

    def test_baihe_tagged_genre_keeps_the_baihe_framing(self):
        instructions = engine_prompts.build_llm_instructions("", {"genre": "baihe"})
        assert "baihe (GL/yuri)" in instructions

    def test_yuri_tagged_genre_keeps_the_baihe_framing(self):
        instructions = engine_prompts.build_llm_instructions("", {"genre": "Yuri, slow burn"})
        assert "baihe (GL/yuri)" in instructions

    def test_a_different_genre_drops_the_baihe_framing(self):
        """Step 54: this exact framing used to be hardcoded into every
        translation prompt regardless of the project's actual content --
        a VTuber stream or a historical drama with no romantic content at
        all still got told it was baihe/yuri, risking an unwarranted
        romantic-interpretation bias."""
        instructions = engine_prompts.build_llm_instructions("", {"genre": "historical"})
        assert "baihe (GL/yuri)" not in instructions
        assert "You are translating Chinese content" in instructions

    def test_a_genre_that_merely_contains_gl_as_a_substring_is_not_a_false_positive(self):
        instructions = engine_prompts.build_llm_instructions("", {"genre": "tangled romance"})
        assert "baihe (GL/yuri)" not in instructions

    def test_upcoming_lines_included_when_provided(self):
        block = engine_prompts.build_batch_context(upcoming_lines=["下一句话"])
        assert "下一句话" in block
        assert "AFTER this batch" in block

    def test_no_upcoming_lines_omits_the_section(self):
        assert "AFTER this batch" not in engine_prompts.build_batch_context(upcoming_lines=None)

    def test_prompt_mentions_speaker_bracket_convention(self):
        # The model needs to be told what the [Name] prefix means and
        # that it shouldn't leak into the translation -- not just have
        # names silently appear in the numbered lines with no explanation.
        instructions = engine_prompts.build_llm_instructions("", {})
        assert "[" in instructions and "speaking" in instructions.lower()

    def test_returns_object_shaped_json_instruction_not_array(self):
        """Regression test for the real zip()-misalignment bug: the old
        prompt asked for a bare JSON array (position-trust); the fix asks
        for an id-keyed object so a missing/extra/reordered response
        entry can only ever affect its own line."""
        instructions = engine_prompts.build_llm_instructions("", {})
        assert "JSON object" in instructions
        assert "JSON array of strings" not in instructions

    def test_signature_has_no_unused_novel_reference_param(self):
        """Step 63: novel_reference used to be accepted but never read
        inside the function body -- the reference novel only ever
        reaches the prompt via build_stable_prompt()'s own novel_block."""
        params = list(inspect.signature(engine_prompts.build_llm_instructions).parameters)
        assert "novel_reference" not in params

    def test_returns_a_single_string_not_a_tuple(self):
        """Step 63: the second return value (meta_block) was redundant --
        its content is already folded into the returned instructions
        string, and the one caller (build_stable_prompt) discarded it."""
        result = engine_prompts.build_llm_instructions("", {})
        assert isinstance(result, str)

    def test_previous_episode_summary_reaches_the_prompt(self):
        """Step 74: the immediately preceding episode's stored running
        summary (db._DRAMA_SELECT's previous_episode_summary) reaches the
        stable translation prompt as fixed continuity context."""
        instructions = engine_prompts.build_llm_instructions(
            "", {"previous_episode_summary": "Xiaoling found the letter in episode 1."})
        assert "Xiaoling found the letter in episode 1." in instructions

    def test_no_previous_episode_summary_omits_the_section(self):
        instructions = engine_prompts.build_llm_instructions("", {})
        assert "Continuity from the previous episode" not in instructions


class TestBuildPreviousEpisodeSummaryBlock:
    def test_empty_when_unset(self):
        assert engine_prompts.build_previous_episode_summary_block({}) == ""

    def test_empty_when_blank(self):
        assert engine_prompts.build_previous_episode_summary_block({"previous_episode_summary": "   "}) == ""

    def test_includes_the_summary_text(self):
        block = engine_prompts.build_previous_episode_summary_block(
            {"previous_episode_summary": "Wei confessed her secret."})
        assert "Wei confessed her secret." in block


class TestRecentContextInPrompt:
    def test_recent_context_included_when_provided(self):
        block = engine_prompts.build_batch_context(recent_context=[("她昨天来了", "She came yesterday.")])
        assert "她昨天来了 -> She came yesterday." in block
        assert "continuity" in block.lower()

    def test_no_recent_context_omits_section(self):
        assert "continuity" not in engine_prompts.build_batch_context(recent_context=None).lower()

    def test_empty_list_also_omits_section(self):
        assert engine_prompts.build_batch_context(recent_context=[]) == ""


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
                            "speaker_labels": list(context.get("speaker_labels") or []),
                            "line_languages": context.get("line_languages")})
        return [f"EN:{z}" for z in zh_lines]


class TestContextWindow:
    def test_first_batch_has_no_recent_context(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=6)
        assert engine.calls[0]["recent_context"] == []

    def test_later_batch_sees_earlier_translated_lines_as_context(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=6)
        # Second batch (l2, l3) should see the first batch's translations as context.
        assert engine.calls[1]["recent_context"] == [("l0", "EN:l0"), ("l1", "EN:l1")]

    def test_context_window_caps_how_far_back_it_looks(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=1)
        # Third batch (l4, l5) should only see the single immediately-preceding line.
        assert engine.calls[2]["recent_context"] == [("l3", "EN:l3")]

    def test_zero_disables_context_entirely(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window=0)
        assert all(call["recent_context"] == [] for call in engine.calls)

    def test_already_translated_lines_before_the_run_are_used_as_context_too(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0", en="Pre-existing translation."),
                 Line(idx=1, start=0, end=1, zh="l1")]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10, context_window=6)
        assert engine.calls[0]["recent_context"] == [("l0", "Pre-existing translation.")]


class TestLookaheadContext:
    def test_last_batch_has_no_upcoming_lines(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=3)
        assert engine.calls[1]["upcoming_lines"] == []

    def test_earlier_batch_sees_the_next_lines_raw_text(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=3)
        # First batch (l0, l1) should see l2, l3 as raw upcoming text.
        assert engine.calls[0]["upcoming_lines"] == ["l2", "l3"]

    def test_lookahead_is_capped_by_context_window_ahead(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(6)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=1)
        assert engine.calls[0]["upcoming_lines"] == ["l2"]

    def test_zero_disables_lookahead(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(4)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, context_window_ahead=0)
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
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=3, context_window_ahead=1)
        # target_lines = [l0, l2, l3, l4] (l1 skipped) -- first batch is
        # [l0, l2, l3], whose real next line is l4, not l3 (still part
        # of this very batch).
        assert engine.calls[0]["zh_lines"] == ["l0", "l2", "l3"]
        assert engine.calls[0]["upcoming_lines"] == ["l4"]


class TestScatteredLinesGetTheirOwnNeighbours:
    """Re-translate selected (a target_ids run) sends far-apart lines in one batch; each
    line needs the lines around it, not only those around the batch's first and last."""

    def _lines(self, n=20):
        return [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"E{i}", id=100 + i) for i in range(n)]

    def test_each_chosen_line_sees_its_own_neighbours_in_story_order(self):
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(
            self._lines(), engine, {}, batch_size=10, context_window=6, context_window_ahead=3,
            force_retranslate=True, target_ids={102, 110, 117})
        assert len(engine.calls) == 1
        call = engine.calls[0]
        assert call["zh_lines"] == ["l2", "l10", "l17"]
        assert [zh for zh, _ in call["recent_context"]] == [
            "l0", "l1", "l3", "l7", "l8", "l9", "l11", "l14", "l15", "l16"]
        assert ("l9", "E9") in call["recent_context"]
        assert call["upcoming_lines"] == ["l18", "l19"]

    def test_a_contiguous_batch_gets_the_same_context_as_before(self):
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(
            self._lines(), engine, {}, batch_size=10, context_window=2, context_window_ahead=1,
            force_retranslate=True, target_ids={105, 106, 107})
        assert engine.calls[0]["recent_context"] == [("l3", "E3"), ("l4", "E4")]
        assert engine.calls[0]["upcoming_lines"] == ["l8"]

    def test_zero_windows_still_mean_no_context(self):
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(
            self._lines(), engine, {}, batch_size=10, context_window=0, context_window_ahead=0,
            force_retranslate=True, target_ids={102, 110})
        assert engine.calls[0]["recent_context"] == [] and engine.calls[0]["upcoming_lines"] == []

    def test_the_prompt_calls_them_lines_around_the_batch(self):
        block = engine_prompts.build_batch_context(recent_context=[("l1", "E1")], upcoming_lines=["l5"],
                                                   around=True)
        assert "around the ones in this batch" in block and "immediately before" not in block
        assert "near this batch" in block and "AFTER this batch" not in block


class TestSpeakerNamesReachTheBatch:
    def test_known_character_name_is_resolved_from_speaker_label(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0", speaker="SPEAKER_00")]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10,
                                        character_names={"SPEAKER_00": "Xiaoling"})
        assert engine.calls[0]["speaker_labels"] == ["Xiaoling"]

    def test_unknown_speaker_label_resolves_to_none_not_the_raw_label(self):
        """A raw diarization id like SPEAKER_00 isn't a name -- showing it
        to the model as if it were would just be confusing noise."""
        lines = [Line(idx=0, start=0, end=1, zh="l0", speaker="SPEAKER_00")]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10, character_names={})
        assert engine.calls[0]["speaker_labels"] == [None]

    def test_line_with_no_speaker_at_all_resolves_to_none(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0", speaker=None)]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10,
                                        character_names={"SPEAKER_00": "Xiaoling"})
        assert engine.calls[0]["speaker_labels"] == [None]


class TestPerLineLanguage:
    def test_only_lines_in_another_language_are_tagged(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", lang=None),
                 Line(idx=1, start=1, end=2, zh="b", lang="ko"),
                 Line(idx=2, start=2, end=3, zh="c", lang="ja")]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {"source_language": "ja"}, batch_size=10)
        assert engine.calls[0]["line_languages"] == [None, "ko", None]

    def test_single_language_title_sends_no_language_context(self):
        lines = [Line(idx=0, start=0, end=1, zh="a")]
        engine = ContextCapturingEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10)
        assert engine.calls[0]["line_languages"] is None

    def test_english_lines_are_copied_not_sent_to_the_engine(self):
        lines = [Line(idx=0, start=0, end=1, zh="hello", lang="en"),
                 Line(idx=1, start=1, end=2, zh="你好")]
        engine = ContextCapturingEngine()
        saved = []
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10,
                                       save_cb=lambda ls: saved.append([ln.en for ln in ls]))
        assert engine.calls[0]["zh_lines"] == ["你好"]
        assert lines[0].en == "hello" and lines[1].en == "EN:你好"
        assert saved[-1] == ["hello", "EN:你好"]

    def test_all_english_titles_still_save_the_copies(self):
        lines = [Line(idx=0, start=0, end=1, zh="hello", lang="en")]
        engine = ContextCapturingEngine()
        saved = []
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, save_cb=lambda ls: saved.append(1))
        assert engine.calls == [] and saved and lines[0].en == "hello"

    def test_tagged_line_languages_only_marks_other_languages(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", lang="ja"),
                 Line(idx=1, start=1, end=2, zh="b", lang="ko"),
                 Line(idx=2, start=2, end=3, zh="c")]
        assert line_text.tagged_line_languages(lines, "ja") == [None, "ko", None]
        assert line_text.tagged_line_languages([lines[0], lines[2]], "ja") is None
        assert line_text.tagged_source_texts(lines, "ja") == ["a", "(spoken in Korean) b", "c"]

    def test_numbered_lines_tag_the_spoken_language(self):
        result = line_text.build_numbered_lines([1, 2], ["a", "b"], languages=[None, "ko"])
        assert result == "1. a\n2. (spoken in Korean) b"

    def test_request_retry_prompt_carries_the_tag(self):
        seen = []
        engine_shared.request_translations_with_retry(
            ["a", "b"], None, lambda text: seen.append(text) or '{"1": "x", "2": "y"}',
            line_languages=[None, "ja"])
        assert "2. (spoken in Japanese) b" in seen[0] and "1. a" in seen[0]

    def test_reflect_prompts_carry_the_tag(self):
        prompts = []

        class Reflect:
            supports_reference = True
            name = "r"

            def complete(self, *a, **k):
                raise AssertionError

        import engine_backends.reflect_pass as tp
        orig = tp.call_llm_json
        tp.call_llm_json = lambda eng, prompt, **k: prompts.append(prompt) or '{"1": "x"}'
        try:
            reflect_pass.reflect_translate_batch(Reflect(), ["a"], {"line_languages": ["ko"], "drama_meta": {}})
        finally:
            tp.call_llm_json = orig
        assert "(spoken in Korean) a" in prompts[0]


class TestBuildNumberedLines:
    def test_lines_with_no_speaker_names_have_no_prefix(self):
        result = line_text.build_numbered_lines([1, 2], ["你好", "再见"])
        assert result == "1. 你好\n2. 再见"

    def test_a_break_inside_a_source_line_cannot_start_another_numbered_row(self):
        result = line_text.build_numbered_lines([1, 2], ["你好\n2. 假的\r\n再见", "真的"])
        assert result == "1. 你好 2. 假的 再见\n2. 真的"
        assert len(result.splitlines()) == 2

    def test_a_line_with_no_source_text_still_gets_its_row(self):
        assert line_text.build_numbered_lines([1, 2], [None, "再见"]) == "1. \n2. 再见"

    def test_known_speaker_is_prefixed_in_brackets(self):
        result = line_text.build_numbered_lines([1, 2], ["你好", "再见"], speaker_names=["Xiaoling", None])
        assert result == "1. [Xiaoling] 你好\n2. 再见"

    def test_ids_need_not_start_at_one(self):
        # Used for retrying only the missing ids from a partial response --
        # their ORIGINAL batch ids must be preserved, not renumbered.
        result = line_text.build_numbered_lines([3, 5], ["a", "b"])
        assert result == "3. a\n5. b"


class TestParseIdKeyedJson:
    def test_parses_a_well_formed_object(self):
        result = engine_shared.parse_id_keyed_json('{"1": "Hello.", "2": "Hi."}', [1, 2])
        assert result == {"1": "Hello.", "2": "Hi."}

    def test_ignores_unexpected_extra_ids(self):
        result = engine_shared.parse_id_keyed_json('{"1": "Hello.", "99": "bogus"}', [1, 2])
        assert result == {"1": "Hello."}

    def test_missing_ids_are_simply_absent_from_the_result(self):
        result = engine_shared.parse_id_keyed_json('{"1": "Hello."}', [1, 2])
        assert result == {"1": "Hello."}

    def test_shuffled_key_order_is_still_correctly_matched_by_id(self):
        result = engine_shared.parse_id_keyed_json('{"2": "Second.", "1": "First."}', [1, 2])
        assert result == {"1": "First.", "2": "Second."}

    def test_a_same_length_array_is_malformed_not_matched_by_position(self):
        """CLAUDE.md rule: never match AI results back to lines by list
        position. Even a same-length array can be reordered, so it's
        treated as a malformed reply (empty) and the retry path re-asks."""
        result = engine_shared.parse_id_keyed_json('["Hello.", "Hi."]', [1, 2])
        assert result == {}

    def test_short_positional_array_is_rejected_not_misassigned(self):
        """Regression test for a real bug: ["A", "C"] for ids [1, 2, 3]
        used to zip positionally into {"1": "A", "2": "C"} -- silently
        putting line 3's translation ("C") on line 2's id. A length
        mismatch has no reliable position-to-id mapping at all, so it
        must come back empty and let the retry path re-request the
        missing ones instead."""
        result = engine_shared.parse_id_keyed_json('["A", "C"]', [1, 2, 3])
        assert result == {}

    def test_long_positional_array_is_also_rejected(self):
        result = engine_shared.parse_id_keyed_json('["A", "B", "C"]', [1, 2])
        assert result == {}

    def test_markdown_fences_are_stripped(self):
        result = engine_shared.parse_id_keyed_json('```json\n{"1": "Hello."}\n```', [1])
        assert result == {"1": "Hello."}

    def test_malformed_json_returns_empty_not_raises(self):
        assert engine_shared.parse_id_keyed_json("not json at all", [1, 2]) == {}

    def test_non_string_values_are_treated_as_missing(self):
        """{"1": null, "2": ["x"]} used to pass straight through, leaving
        ln.en set to None or a list. Any non-string value must be
        dropped so the id is treated as missing and gets retried."""
        result = engine_shared.parse_id_keyed_json('{"1": null, "2": ["x"], "3": "Hi."}', [1, 2, 3])
        assert result == {"3": "Hi."}

    def test_prose_wrapped_around_the_json_object_is_tolerated(self):
        """Small local models often add commentary around the JSON --
        e.g. "Here you go:\\n{...}". The first JSON value anywhere in the
        text should be extracted rather than requiring the whole
        response to be nothing but JSON."""
        result = engine_shared.parse_id_keyed_json('Here you go:\n{"1": "Hello."}\nHope that helps!', [1])
        assert result == {"1": "Hello."}

    def test_prose_wrapped_around_an_array_is_still_malformed(self):
        result = engine_shared.parse_id_keyed_json('Sure, here it is: ["Hello.", "Hi."]', [1, 2])
        assert result == {}

    @pytest.mark.parametrize("recover", [False, True])
    def test_items_that_name_their_own_id_are_matched_by_that_id(self, recover):
        reply = ('{"translations": [{"id": 2, "source": "嗨", "translation": "Hi."}, '
                 '{"id": "1", "text": "Hello."}, {"id": 9, "text": "stray"}, {"text": "no id"}]}')
        assert engine_shared.parse_id_keyed_json(reply, [1, 2], recover=recover) == {"1": "Hello.", "2": "Hi."}
        bare = '```json\n[{"id": 3, "en": "Bye."}, {"id": 1, "zh": "你好", "en": "Hello."}]\n```'
        assert engine_shared.parse_id_keyed_json(bare, [1, 3], recover=recover) == {"1": "Hello.", "3": "Bye."}

    def test_an_item_with_only_its_source_text_is_not_a_translation(self):
        assert engine_shared.parse_id_keyed_json('[{"id": 1, "source": "你好"}]', [1]) == {}
        assert engine_shared.parse_id_keyed_json('{"id": 1, "zh": "你好"}', [1]) == {}
        assert engine_shared.parse_id_keyed_json('[{"id": 1, "chinese": "你好"}, {"id": 2, "line": "嗨"}]', [1, 2]) == {}

    def test_items_inside_a_broken_object_are_not_read(self):
        reply = '{"1": "Hello" "2": "Hi", "lines": [{"id": 2, "text": "你好"}]}'
        assert engine_shared.parse_id_keyed_json(reply, [1, 2]) == {}


class TestRequestTranslationsWithRetry:
    def test_a_complete_well_ordered_response_needs_no_retry(self):
        calls = []
        def call_model(numbered):
            calls.append(numbered)
            return '{"1": "A.", "2": "B."}'
        result = engine_shared.request_translations_with_retry(["a", "b"], None, call_model)
        assert result == ["A.", "B."]
        assert len(calls) == 1

    def test_shuffled_response_order_still_lands_on_the_right_line(self):
        """The core fix this replaces: the old code trusted array POSITION,
        so a shuffled/reordered response silently misassigned lines. Id-
        keyed lookup means shuffled key order in the raw response can't
        do that anymore."""
        def call_model(numbered):
            return '{"3": "Third.", "1": "First.", "2": "Second."}'
        result = engine_shared.request_translations_with_retry(["a", "b", "c"], None, call_model)
        assert result == ["First.", "Second.", "Third."]

    def test_missing_lines_are_retried_and_recovered(self):
        calls = []
        def call_model(numbered):
            calls.append(numbered)
            if len(calls) == 1:
                return '{"1": "First."}'  # line 2 missing this round
            return '{"2": "Second."}'  # retry recovers it
        result = engine_shared.request_translations_with_retry(["a", "b"], None, call_model, max_retries=1)
        assert result == ["First.", "Second."]
        assert len(calls) == 2
        assert "2. b" in calls[1]  # retry only re-sent the missing line
        assert "1. a" not in calls[1]

    def test_still_missing_after_retries_exhausted_leaves_that_line_blank_only(self):
        def call_model(numbered):
            return '{"1": "First."}'  # line 2 never comes back, ever
        result = engine_shared.request_translations_with_retry(["a", "b"], None, call_model, max_retries=1)
        assert result == ["First.", ""]  # only the genuinely-missing line is blank

    def test_extra_unexpected_ids_in_the_response_are_ignored(self):
        def call_model(numbered):
            return '{"1": "First.", "2": "Second.", "47": "bogus extra"}'
        result = engine_shared.request_translations_with_retry(["a", "b"], None, call_model)
        assert result == ["First.", "Second."]

    def test_speaker_names_reach_the_numbered_lines_sent_to_the_model(self):
        captured = {}
        def call_model(numbered):
            captured["numbered"] = numbered
            return '{"1": "Hi."}'
        engine_shared.request_translations_with_retry(["你好"], ["Xiaoling"], call_model)
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
        result, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=3)

        assert len(errors) == 1
        # None of the lines got ANY translation -- specifically, l2's real
        # translation was never silently assigned to l1 or vice versa.
        assert all(ln.en == "" for ln in result)

    def test_too_many_translations_leaves_the_whole_batch_untranslated_not_shifted(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(2)]
        engine = self.WrongCountEngine(["EN:l0", "EN:l1", "EN:extra"])
        result, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)

        assert len(errors) == 1
        assert all(ln.en == "" for ln in result)

    def test_correct_count_still_assigns_normally(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(2)]
        engine = self.WrongCountEngine(["EN:l0", "EN:l1"])
        result, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)

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

        result, errors = translate_pipeline.translate_lines_with_engine(lines, MixedEngine(), {}, batch_size=2)

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
        out = engine_shared.redact_secrets(text)
        assert token not in out and "[REDACTED]" in out

    def test_leaves_short_secret_and_ntn_words_alone(self):
        text = "secret_key not set; ntn_status=ok; secret_santa"
        assert engine_shared.redact_secrets(text) == text

    def test_redacts_a_key_query_param(self):
        text = ("400 Client Error: Bad Request for url: "
                "https://generativelanguage.googleapis.com/v1beta/models/x:"
                "generateContent?key=AIzaSyFAKESECRETVALUE12345")
        assert "AIzaSyFAKESECRETVALUE12345" not in engine_shared.redact_secrets(text)

    def test_redacts_a_bare_google_key_anywhere_in_the_text(self):
        text = "something failed near AIzaSyFAKESECRETVALUE12345 during the request"
        assert "AIzaSyFAKESECRETVALUE12345" not in engine_shared.redact_secrets(text)

    def test_redacts_an_sk_style_key(self):
        text = "Incorrect API key provided: sk-abcdefghijklmnopqrstuvwxyz"
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in engine_shared.redact_secrets(text)

    def test_redacts_an_authorization_header_dump(self):
        text = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234 -- request failed"
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in engine_shared.redact_secrets(text)

    def test_leaves_ordinary_error_text_unchanged(self):
        text = "429 Too Many Requests -- rate limited, retry later"
        assert engine_shared.redact_secrets(text) == text

    def test_handles_empty_and_none(self):
        assert engine_shared.redact_secrets("") == ""
        assert engine_shared.redact_secrets(None) is None


class TestTranslateLinesWithEngineRedactsBatchErrors:
    def test_a_failing_batch_stores_a_redacted_error_not_the_raw_one(self):
        lines = [Line(idx=0, start=0, end=1, zh="l0")]

        class LeakyEngine:
            def translate_batch(self, zh_lines, context):
                raise RuntimeError(
                    "400 Client Error: Bad Request for url: https://generativelanguage."
                    "googleapis.com/v1beta/models/x:generateContent?key=AIzaSyFAKEKEY999")

        _, errors = translate_pipeline.translate_lines_with_engine(lines, LeakyEngine(), {}, batch_size=1)

        assert len(errors) == 1
        assert "AIzaSyFAKEKEY999" not in errors[0]["error"]
        assert "400 Client Error" in errors[0]["error"]  # the rest of the message survives


class TestDisplayUrlEncodedTokens:
    @pytest.mark.parametrize("path", ["/dl/AbC%2Bxyz%3D" + "q" * 28 + "/file", "/dl/" + "A%2D" * 20 + "/f"])
    def test_percent_encoded_token_segment_is_dropped(self, path):
        assert engine_shared.display_url(f"https://cdn.example{path}") == "https://cdn.example/"

    def test_ordinary_encoded_name_is_kept(self):
        assert engine_shared.display_url("https://cdn.example/a%20b/c.html") == "https://cdn.example/a%20b/c.html"


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class TestOfflineTestEngine:
    """The free dry-run engine exists so the pipeline can be validated with
    no API key, no network, and no spend. These guard that promise."""

    def test_registered_as_an_engine(self):
        assert "fake" in engine_registry.ENGINES

    def test_works_with_no_api_key(self):
        engine = engine_registry.get_engine("fake")
        assert engine is not None
        engine2 = engine_registry.get_engine("fake", None)
        assert engine2 is not None

    def test_translates_every_line_without_network(self):
        lines = [Line(idx=i, start=float(i), end=float(i) + 1, zh=f"第{i}句") for i in range(6)]
        engine = engine_registry.get_engine("fake")
        _, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert errors == []
        assert all(ln.en for ln in lines)

    def test_output_is_obviously_placeholder(self):
        # Must never be mistakable for a real translation.
        lines = [Line(idx=0, start=0, end=1, zh="真实对白")]
        engine = engine_registry.get_engine("fake")
        translate_pipeline.translate_lines_with_engine(lines, engine, {})
        assert lines[0].en.startswith("[TEST]")

    def test_costs_nothing(self):
        assert engine_pricing.estimate_cost("test-offline", 10_000_000, 10_000_000) == 0.0

    def test_records_usage_so_dashboard_path_is_exercised(self):
        lines = [Line(idx=0, start=0, end=1, zh="测试")]
        engine = engine_registry.get_engine("fake")
        translate_pipeline.translate_lines_with_engine(lines, engine, {})
        assert engine.last_usage["input_tokens"] > 0

    def test_long_lines_are_truncated_in_placeholder(self):
        lines = [Line(idx=0, start=0, end=1, zh="字" * 200)]
        engine = engine_registry.get_engine("fake")
        translate_pipeline.translate_lines_with_engine(lines, engine, {})
        assert len(lines[0].en) < 100

    def test_has_no_sdk_client_so_llm_features_decline_cleanly(self):
        # Free-form LLM helpers check for .client; None must not crash them.
        engine = engine_registry.get_engine("fake")
        assert engine.client is None
        # Step 55: a decline-cleanly response is still a batch that couldn't
        # actually be checked -- counted as failed, not silently zeroed out.
        assert llm_tasks.check_consistency_llm(
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
        translate_pipeline.translate_lines_with_engine(lines, FlakyEngine(), {}, batch_size=2)
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
        translate_pipeline.translate_lines_with_engine(lines, FlakyEngine(), {}, batch_size=2)

        fresh = FreshEngine()
        _, errors = translate_pipeline.translate_lines_with_engine(lines, fresh, {}, batch_size=2)

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
        self.prompts = []  # system + user text: everything the model is shown
        self.systems = []
        self.users = []

    def create(self, model, max_tokens, messages, system=None):
        system_text = "".join(b["text"] for b in system or [])
        self.systems.append(system)
        self.users.append(messages[0]["content"])
        self.prompts.append(system_text + messages[0]["content"])
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
        translations, critiques = reflect_pass.reflect_translate_batch(
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
        reflect_pass.reflect_translate_batch(
            engine, ["你好"], {"novel_reference": "Xiaoling always calls her 'sis'."})
        for prompt in engine.prompts:
            assert "Xiaoling always calls her 'sis'." in prompt
            assert "REFERENCE NOVEL TRANSLATION" in prompt

    def test_no_reference_block_when_none_is_given(self):
        engine = _ScriptedReflectEngine([
            '{"1": "Draft."}', '{"1": "critique"}', '{"1": "Final."}'])
        reflect_pass.reflect_translate_batch(engine, ["你好"], {})
        for prompt in engine.prompts:
            assert "REFERENCE NOVEL TRANSLATION" not in prompt

    def test_each_pass_asks_for_something_different(self):
        engine = _ScriptedReflectEngine([
            '{"1": "Draft."}', '{"1": "critique"}', '{"1": "Final."}'])
        reflect_pass.reflect_translate_batch(engine, ["你好"], {})
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
        translations, critiques = reflect_pass.reflect_translate_batch(
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
        translations, critiques = reflect_pass.reflect_translate_batch(engine, ["你好", "再见"], {})
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
        translations, critiques = reflect_pass.reflect_translate_batch(engine, ["你好"], {})
        assert translations == ["Draft one."]
        assert critiques == ["a critique"]

    def test_a_draft_with_no_critique_is_not_sent_to_the_reflection_pass(self):
        engine = _ScriptedReflectEngine(['{"1": ""}', '{}', '{"1": ""}'])
        # An empty draft (id came back with "" -- treated as no usable draft
        # since "" isn't kept by _parse_id_keyed_json in the first place;
        # simulate directly missing instead):
        engine2 = _ScriptedReflectEngine(['{}', '{}', '{}'])
        translations, critiques = reflect_pass.reflect_translate_batch(engine2, ["你好"], {})
        assert len(engine2.prompts) == 2  # pass 1 (x1, no retry-worthy content) + pass 3; pass 2 skipped
        assert translations == [""]
        assert critiques == [""]

    def test_requires_an_llm_capable_engine(self):
        class TranslationOnlyEngine:
            supports_reference = False
        with pytest.raises(RuntimeError):
            reflect_pass.reflect_translate_batch(TranslationOnlyEngine(), ["你好"], {})


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
        translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=10, reflect=True,
            notes_cb=captured_notes.append)
        assert [ln.en for ln in lines] == ["Final one.", "Final two."]
        assert len(captured_notes) == 1  # one notes_cb call, for this one batch
        notes = captured_notes[0]
        assert notes == [{"line_idx": 0, "term": "", "note_type": "reflection", "note": "a bit stiff"}]

    def test_reflect_false_never_touches_reflect_translate_batch(self, monkeypatch):
        called = []
        monkeypatch.setattr("engine_backends.translate_pipeline.reflect_translate_batch", lambda *a, **k: called.append(1))

        class PlainEngine:
            supports_reference = True
            def translate_batch(self, zh_lines, context):
                return [f"EN:{z}" for z in zh_lines]
        lines = [Line(idx=0, start=0, end=1, zh="你好")]
        translate_pipeline.translate_lines_with_engine(lines, PlainEngine(), {}, batch_size=10, reflect=False)
        assert called == []
        assert lines[0].en == "EN:你好"

    def test_notes_cb_is_not_called_when_nothing_needed_a_critique(self):
        engine = _ScriptedReflectEngine(['{"1": "Draft."}', '{}', '{"1": "Final."}'])
        lines = [Line(idx=0, start=0, end=1, zh="你好")]
        captured = []
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=10, reflect=True,
                                       notes_cb=captured.append)
        assert captured == []


class _EmptyReplyEngine:
    """translate_batch pads missing ids with "", as request_translations_with_retry does."""
    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)

    def translate_batch(self, zh_lines, context):
        return self.replies.pop(0)


class TestEmptyTranslationsAreErrors:
    def _lines(self, en=""):
        return [Line(idx=i, start=0, end=1, zh=f"l{i}", en=en) for i in range(3)]

    def test_all_empty_reply_records_error_and_writes_nothing(self):
        lines = self._lines(en="old")
        engine = _EmptyReplyEngine([["", "  ", ""]])
        _, errors = translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=3, force_retranslate=True)
        assert len(errors) == 1
        assert errors[0]["lines"] == [0, 1, 2]
        assert "3 of 3 lines got no translation" in errors[0]["error"]
        assert "smaller batch size" in errors[0]["error"]
        assert [l.en for l in lines] == ["old"] * 3

    def test_partial_reply_keeps_good_lines(self):
        lines = self._lines()
        engine = _EmptyReplyEngine([["One.", "", "Three."]])
        _, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=3)
        assert [l.en for l in lines] == ["One.", "", "Three."]
        assert errors[0]["lines"] == [1]
        assert "1 of 3 lines" in errors[0]["error"]

    def test_reflect_truncated_reply_records_error(self):
        lines = self._lines()
        engine = _ScriptedReflectEngine(['{"1": "Dra', '{"1": "Cri', '{"1": "Fin'])
        _, errors = translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=3, reflect=True)
        assert len(errors) == 1
        assert "got no translation" in errors[0]["error"]
        assert all(l.en == "" for l in lines)


class TestLoadJsonReply:
    @pytest.mark.parametrize("reply, expected", [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n[1, 2]\n```', [1, 2]),
        ('  \n{"a": 1}\n  ', {"a": 1}),
    ])
    def test_strips_a_markdown_fence_and_parses(self, reply, expected):
        from engine_backends.shared import load_json_reply
        assert load_json_reply(reply, None) == expected

    @pytest.mark.parametrize("reply", ["", "not json", '{"a": ', "Here you go: {}"])
    def test_unparseable_reply_returns_the_default(self, reply):
        from engine_backends.shared import load_json_reply
        default = {"kept": True}
        assert load_json_reply(reply, default) is default


def test_a_content_block_is_not_retried(monkeypatch):
    """A provider's content block was billed, and the same text is blocked again."""
    calls = []

    def blocked():
        calls.append(1)
        raise engine_shared.ContentModerationBlocked("gemini", "SAFETY")

    monkeypatch.setattr("engine_backends.shared._cancellable_sleep", lambda s: None)
    with pytest.raises(engine_shared.ContentModerationBlocked):
        engine_shared.call_with_backoff(blocked)
    assert calls == [1]
