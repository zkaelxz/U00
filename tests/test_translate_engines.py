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

    def test_source_language_from_drama_meta_reaches_the_engine(self):
        """Regression test for a real bug: DeepLEngine/GoogleEngine used
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

    def test_defaults_to_chinese_when_source_language_unset(self):
        instructions, _ = te.build_llm_instructions("", {}, None)
        assert "Chinese baihe" in instructions

    def test_japanese_source_language_reaches_the_prompt(self):
        """Regression test for a real bug found during audit: the system
        prompt used to hardcode "Chinese baihe" regardless of the drama's
        actual source language, so a Japanese or Korean drama's own
        translator was told it was translating Chinese the whole time."""
        instructions, _ = te.build_llm_instructions("", {"source_language": "ja"}, None)
        assert "Japanese baihe" in instructions
        assert "Chinese baihe" not in instructions

    def test_korean_source_language_reaches_the_prompt(self):
        instructions, _ = te.build_llm_instructions("", {"source_language": "ko"}, None)
        assert "Korean baihe" in instructions

    def test_content_mode_reaches_the_prompt(self):
        instructions, _ = te.build_llm_instructions(
            "", {"source_language": "zh", "content_mode": "novel_narration"}, None)
        assert "novel" in instructions

    def test_streamer_vod_content_mode_reaches_the_prompt(self):
        instructions, _ = te.build_llm_instructions(
            "", {"source_language": "ja", "content_mode": "streamer_vod"}, None)
        assert "livestream VOD" in instructions
        assert "Japanese baihe" in instructions

    def test_upcoming_lines_included_when_provided(self):
        instructions, _ = te.build_llm_instructions(
            "", {}, None, upcoming_lines=["下一句话"])
        assert "下一句话" in instructions
        assert "AFTER this batch" in instructions

    def test_no_upcoming_lines_omits_the_section(self):
        instructions, _ = te.build_llm_instructions("", {}, None, upcoming_lines=None)
        assert "AFTER this batch" not in instructions

    def test_prompt_mentions_speaker_bracket_convention(self):
        # The model needs to be told what the [Name] prefix means and
        # that it shouldn't leak into the translation -- not just have
        # names silently appear in the numbered lines with no explanation.
        instructions, _ = te.build_llm_instructions("", {}, None)
        assert "[" in instructions and "speaking" in instructions.lower()

    def test_returns_object_shaped_json_instruction_not_array(self):
        """Regression test for the real zip()-misalignment bug: the old
        prompt asked for a bare JSON array (position-trust); the fix asks
        for an id-keyed object so a missing/extra/reordered response
        entry can only ever affect its own line."""
        instructions, _ = te.build_llm_instructions("", {}, None)
        assert "JSON object" in instructions
        assert "JSON array of strings" not in instructions


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
        instructions, _ = te.build_llm_instructions(
            "", {}, None, recent_context=[("她昨天来了", "She came yesterday.")])
        assert "她昨天来了 -> She came yesterday." in instructions
        assert "continuity" in instructions.lower()

    def test_no_recent_context_omits_section(self):
        instructions, _ = te.build_llm_instructions("", {}, None, recent_context=None)
        assert "continuity" not in instructions.lower()

    def test_empty_list_also_omits_section(self):
        instructions, _ = te.build_llm_instructions("", {}, None, recent_context=[])
        assert "continuity" not in instructions.lower()


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
        result = te._build_numbered_lines([1, 2], ["你好", "再见"])
        assert result == "1. 你好\n2. 再见"

    def test_known_speaker_is_prefixed_in_brackets(self):
        result = te._build_numbered_lines([1, 2], ["你好", "再见"], speaker_names=["Xiaoling", None])
        assert result == "1. [Xiaoling] 你好\n2. 再见"

    def test_ids_need_not_start_at_one(self):
        # Used for retrying only the missing ids from a partial response --
        # their ORIGINAL batch ids must be preserved, not renumbered.
        result = te._build_numbered_lines([3, 5], ["a", "b"])
        assert result == "3. a\n5. b"


class TestParseIdKeyedJson:
    def test_parses_a_well_formed_object(self):
        result = te._parse_id_keyed_json('{"1": "Hello.", "2": "Hi."}', [1, 2])
        assert result == {"1": "Hello.", "2": "Hi."}

    def test_ignores_unexpected_extra_ids(self):
        result = te._parse_id_keyed_json('{"1": "Hello.", "99": "bogus"}', [1, 2])
        assert result == {"1": "Hello."}

    def test_missing_ids_are_simply_absent_from_the_result(self):
        result = te._parse_id_keyed_json('{"1": "Hello."}', [1, 2])
        assert result == {"1": "Hello."}

    def test_shuffled_key_order_is_still_correctly_matched_by_id(self):
        result = te._parse_id_keyed_json('{"2": "Second.", "1": "First."}', [1, 2])
        assert result == {"1": "First.", "2": "Second."}

    def test_falls_back_to_positional_array_for_a_noncompliant_model_when_lengths_match(self):
        """The positional-array fallback is only safe when the array's
        length exactly matches what was asked for -- see the short-list
        test below for why a mismatched length must NOT be guessed at
        positionally."""
        result = te._parse_id_keyed_json('["Hello.", "Hi."]', [1, 2])
        assert result == {"1": "Hello.", "2": "Hi."}

    def test_short_positional_array_is_rejected_not_misassigned(self):
        """Regression test for a real bug: ["A", "C"] for ids [1, 2, 3]
        used to zip positionally into {"1": "A", "2": "C"} -- silently
        putting line 3's translation ("C") on line 2's id. A length
        mismatch has no reliable position-to-id mapping at all, so it
        must come back empty and let the retry path re-request the
        missing ones instead."""
        result = te._parse_id_keyed_json('["A", "C"]', [1, 2, 3])
        assert result == {}

    def test_long_positional_array_is_also_rejected(self):
        result = te._parse_id_keyed_json('["A", "B", "C"]', [1, 2])
        assert result == {}

    def test_markdown_fences_are_stripped(self):
        result = te._parse_id_keyed_json('```json\n{"1": "Hello."}\n```', [1])
        assert result == {"1": "Hello."}

    def test_malformed_json_returns_empty_not_raises(self):
        assert te._parse_id_keyed_json("not json at all", [1, 2]) == {}

    def test_non_string_values_are_treated_as_missing(self):
        """{"1": null, "2": ["x"]} used to pass straight through, leaving
        ln.en set to None or a list. Any non-string value must be
        dropped so the id is treated as missing and gets retried."""
        result = te._parse_id_keyed_json('{"1": null, "2": ["x"], "3": "Hi."}', [1, 2, 3])
        assert result == {"3": "Hi."}

    def test_non_string_values_in_a_positional_array_are_also_dropped(self):
        result = te._parse_id_keyed_json('["Hi.", null]', [1, 2])
        assert result == {"1": "Hi."}

    def test_prose_wrapped_around_the_json_object_is_tolerated(self):
        """Small local models often add commentary around the JSON --
        e.g. "Here you go:\\n{...}". The first JSON value anywhere in the
        text should be extracted rather than requiring the whole
        response to be nothing but JSON."""
        result = te._parse_id_keyed_json('Here you go:\n{"1": "Hello."}\nHope that helps!', [1])
        assert result == {"1": "Hello."}

    def test_prose_wrapped_around_a_positional_array_is_tolerated(self):
        result = te._parse_id_keyed_json('Sure, here it is: ["Hello.", "Hi."]', [1, 2])
        assert result == {"1": "Hello.", "2": "Hi."}


class TestRequestTranslationsWithRetry:
    def test_a_complete_well_ordered_response_needs_no_retry(self):
        calls = []
        def call_model(numbered):
            calls.append(numbered)
            return '{"1": "A.", "2": "B."}'
        result = te._request_translations_with_retry(["a", "b"], None, call_model)
        assert result == ["A.", "B."]
        assert len(calls) == 1

    def test_shuffled_response_order_still_lands_on_the_right_line(self):
        """The core fix this replaces: the old code trusted array POSITION,
        so a shuffled/reordered response silently misassigned lines. Id-
        keyed lookup means shuffled key order in the raw response can't
        do that anymore."""
        def call_model(numbered):
            return '{"3": "Third.", "1": "First.", "2": "Second."}'
        result = te._request_translations_with_retry(["a", "b", "c"], None, call_model)
        assert result == ["First.", "Second.", "Third."]

    def test_missing_lines_are_retried_and_recovered(self):
        calls = []
        def call_model(numbered):
            calls.append(numbered)
            if len(calls) == 1:
                return '{"1": "First."}'  # line 2 missing this round
            return '{"2": "Second."}'  # retry recovers it
        result = te._request_translations_with_retry(["a", "b"], None, call_model, max_retries=1)
        assert result == ["First.", "Second."]
        assert len(calls) == 2
        assert "2. b" in calls[1]  # retry only re-sent the missing line
        assert "1. a" not in calls[1]

    def test_still_missing_after_retries_exhausted_leaves_that_line_blank_only(self):
        def call_model(numbered):
            return '{"1": "First."}'  # line 2 never comes back, ever
        result = te._request_translations_with_retry(["a", "b"], None, call_model, max_retries=1)
        assert result == ["First.", ""]  # only the genuinely-missing line is blank

    def test_extra_unexpected_ids_in_the_response_are_ignored(self):
        def call_model(numbered):
            return '{"1": "First.", "2": "Second.", "47": "bogus extra"}'
        result = te._request_translations_with_retry(["a", "b"], None, call_model)
        assert result == ["First.", "Second."]

    def test_speaker_names_reach_the_numbered_lines_sent_to_the_model(self):
        captured = {}
        def call_model(numbered):
            captured["numbered"] = numbered
            return '{"1": "Hi."}'
        te._request_translations_with_retry(["你好"], ["Xiaoling"], call_model)
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
                        {"text": '["Hello.", "Goodbye."]'}]}}],
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
        assert engine.last_usage == {"input_tokens": 42, "output_tokens": 8}
        assert "gemini-flash-lite-latest" in captured["url"]
        # Key goes in a header, never the URL/query string -- a
        # raise_for_status() failure's message includes the URL, and that
        # message can end up stored/shown; a key in params would leak.
        assert captured["headers"] == {"x-goog-api-key": "fake-key"}
        assert "key=" not in captured["url"]
        assert "systemInstruction" in captured["json"]

    def test_recent_context_reaches_the_system_instruction(self, monkeypatch):
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
        assert "她来了 -> She came." in sys_text

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
        assert engine.last_usage == {"input_tokens": 0, "output_tokens": 0}


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


class TestDeepLEngine:
    """Regression coverage for a real bug: source_language was hardcoded
    to "ZH" regardless of the drama's actual source language, so a
    Japanese or Korean drama translated through DeepL silently told
    DeepL its audio was Chinese the whole time."""

    def _install_fake_deepl(self, monkeypatch):
        import sys, types
        fake_module = types.ModuleType("deepl")
        captured = {}

        class FakeResult:
            def __init__(self, text):
                self.text = text

        class FakeTranslator:
            def __init__(self, api_key):
                captured["api_key"] = api_key

            def translate_text(self, texts, source_lang, target_lang):
                captured["source_lang"] = source_lang
                captured["target_lang"] = target_lang
                return [FakeResult(f"EN:{t}") for t in texts]

        fake_module.Translator = FakeTranslator
        monkeypatch.setitem(sys.modules, "deepl", fake_module)
        return captured

    def test_defaults_to_chinese_source(self, monkeypatch):
        captured = self._install_fake_deepl(monkeypatch)
        engine = te.DeepLEngine("fake-key")
        result = engine.translate_batch(["你好"], {})
        assert result == ["EN:你好"]
        assert captured["source_lang"] == "ZH"

    def test_japanese_source_language_reaches_deepl(self, monkeypatch):
        captured = self._install_fake_deepl(monkeypatch)
        engine = te.DeepLEngine("fake-key")
        engine.translate_batch(["こんにちは"], {"source_language": "ja"})
        assert captured["source_lang"] == "JA"

    def test_korean_source_language_reaches_deepl(self, monkeypatch):
        captured = self._install_fake_deepl(monkeypatch)
        engine = te.DeepLEngine("fake-key")
        engine.translate_batch(["안녕"], {"source_language": "ko"})
        assert captured["source_lang"] == "KO"

    def test_a_single_result_is_normalized_to_a_list(self, monkeypatch):
        """DeepL's SDK returns a bare TextResult (not a list) when given
        a single-element input list -- confirmed real behavior, not
        hypothetical, hence the isinstance check in the engine itself."""
        import sys, types

        class FakeResult:
            def __init__(self, text):
                self.text = text

        class FakeTranslator:
            def __init__(self, api_key):
                pass

            def translate_text(self, texts, source_lang, target_lang):
                return FakeResult("EN:solo")  # bare object, not a list

        fake_module = types.ModuleType("deepl")
        fake_module.Translator = FakeTranslator
        monkeypatch.setitem(sys.modules, "deepl", fake_module)

        engine = te.DeepLEngine("fake-key")
        result = engine.translate_batch(["solo"], {})
        assert result == ["EN:solo"]


class TestGoogleEngine:
    """Same regression coverage as TestDeepLEngine, for GoogleEngine."""

    def test_defaults_to_chinese_source(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"data": {"translations": [{"translatedText": "EN:你好"}]}}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GoogleEngine("fake-key")
        result = engine.translate_batch(["你好"], {})
        assert result == ["EN:你好"]
        assert captured["json"]["source"] == "zh"
        # Key goes in a header, never the URL/query string -- see the
        # matching Gemini test above for why.
        assert captured["headers"] == {"X-Goog-Api-Key": "fake-key"}

    def test_japanese_source_language_reaches_google(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"data": {"translations": [{"translatedText": "EN:x"}]}}

        monkeypatch.setattr("requests.post",
                             lambda url, headers=None, json=None, timeout=None:
                                 captured.update(json=json) or FakeResponse())
        engine = te.GoogleEngine("fake-key")
        engine.translate_batch(["x"], {"source_language": "ja"})
        assert captured["json"]["source"] == "ja"

    def test_korean_source_language_reaches_google(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"data": {"translations": [{"translatedText": "EN:x"}]}}

        monkeypatch.setattr("requests.post",
                             lambda url, headers=None, json=None, timeout=None:
                                 captured.update(json=json) or FakeResponse())
        engine = te.GoogleEngine("fake-key")
        engine.translate_batch(["x"], {"source_language": "ko"})
        assert captured["json"]["source"] == "ko"


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

    def test_test_offline_engine_declines_without_crashing(self):
        engine = te.TestOfflineEngine()
        assert engine.client is None  # by design
        result = te.call_llm_json(engine, "prompt", fallback="[]")
        assert result == "[]"

    def test_ollama_engine_declines_without_crashing(self):
        engine = te.OllamaEngine()
        assert not hasattr(engine, "client")
        result = te.call_llm_json(engine, "prompt", fallback="{}")
        assert result == "{}"

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
        assert result == []

    def test_no_translated_lines_returns_empty(self):
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]  # untranslated
        result = te.check_consistency_llm(lines, MockLlm())
        assert result == []


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
    """tag_speakers_llm used to request a plain positional JSON array and
    zip/extend it onto zh_chunks by position -- the same misassignment
    bug class as translation itself. Now id-keyed, same mechanism as
    translate_batch's own fix."""

    def test_well_formed_object_response_assigns_labels_by_id(self):
        engine = SequentialClaudeShapedEngine(['{"1": "Xiaoling", "2": "Narrator"}'])
        labels = te.tag_speakers_llm(["你好", "那天下着雨。"], engine)
        assert labels == ["Xiaoling", "Narrator"]

    def test_a_response_missing_one_id_is_retried_and_lands_on_the_right_chunk(self):
        engine = SequentialClaudeShapedEngine([
            '{"1": "Xiaoling"}',  # id 2 missing from the first response
            '{"2": "Yun"}',       # retry supplies it
        ])
        labels = te.tag_speakers_llm(["你好", "你也好"], engine)
        assert labels == ["Xiaoling", "Yun"]

    def test_still_missing_after_retry_defaults_to_narrator_not_blank(self):
        engine = SequentialClaudeShapedEngine(['{"1": "Xiaoling"}', '{}'])
        labels = te.tag_speakers_llm(["你好", "你也好"], engine)
        assert labels == ["Xiaoling", "Narrator"]

    def test_pure_mt_engine_returns_all_narrator(self):
        class PureMT:
            supports_reference = False
        labels = te.tag_speakers_llm(["a", "b"], PureMT())
        assert labels == ["Narrator", "Narrator"]


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
