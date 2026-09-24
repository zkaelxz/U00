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
                            "recent_context": list(context.get("recent_context") or [])})
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

        def fake_post(url, params=None, json=None):
            captured["url"] = url
            captured["params"] = params
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GeminiEngine("fake-key", model="gemini-flash-lite-latest")
        result = engine.translate_batch(["你好", "再见"], {})

        assert result == ["Hello.", "Goodbye."]
        assert engine.last_usage == {"input_tokens": 42, "output_tokens": 8}
        assert "gemini-flash-lite-latest" in captured["url"]
        assert captured["params"] == {"key": "fake-key"}
        assert "systemInstruction" in captured["json"]

    def test_recent_context_reaches_the_system_instruction(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}

        def fake_post(url, params=None, json=None):
            captured["json"] = json
            return FakeResponse()

        monkeypatch.setattr("requests.post", fake_post)
        engine = te.GeminiEngine("fake-key")
        engine.translate_batch(["x"], {"recent_context": [("她来了", "She came.")]})

        sys_text = captured["json"]["systemInstruction"]["parts"][0]["text"]
        assert "她来了 -> She came." in sys_text

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
