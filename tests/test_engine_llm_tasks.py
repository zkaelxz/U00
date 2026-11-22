"""Tests for the LLM side tasks: JSON calls, consistency checks, episode summaries, flagging, speaker tags and pacing rewrites."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine_backends import llm_tasks
from engine_backends import line_text
from engine_backends import gemini as engine_gemini
from engine_backends import local as engine_local
from core import Line
from tests import fake_engine
from tests.http_fakes import StreamedBody, patch_post
from tests.test_translate_engines import _FakeBlock


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

        class FakeResponse(StreamedBody):
            usage = FakeUsage()
            content = [_FakeBlock('{"ok": true}')]

        class FakeClaudeLike:
            model = "fake-claude"

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                return FakeResponse()

        result = llm_tasks.call_llm_json(
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

        class FakeResponse(StreamedBody):
            usage = FakeUsage()
            choices = [FakeChoice()]

        class FakeOpenAiLike:
            model = "fake-deepseek"

            def __init__(self):
                self.client = self
                self.chat = self
                self.completions = self

            def create(self, model, messages, max_tokens):
                return FakeResponse()

        result = llm_tasks.call_llm_json(
            FakeOpenAiLike(), "prompt",
            usage_cb=lambda inp, out: captured_usage.update(input=inp, output=out))
        assert result == "[1, 2, 3]"
        assert captured_usage == {"input": 20, "output": 7}

    def test_gemini_engine_is_not_silently_skipped(self, monkeypatch):
        captured_usage = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "candidates": [{"content": {"parts": [{"text": "real answer"}]}}],
                    "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 3},
                }

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_gemini.GeminiEngine("fake-key")

        result = llm_tasks.call_llm_json(
            engine, "prompt",
            usage_cb=lambda inp, out: captured_usage.update(input=inp, output=out))
        assert result == "real answer"
        assert captured_usage == {"input": 15, "output": 3}

    def test_free_tier_gemini_is_throttled_here_too(self, monkeypatch):
        """call_llm_json's Gemini branch is a second, separate call site
        from translate_batch -- confirms the free-tier throttle applies
        there as well, not just to translation."""
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=True)
        calls = []
        monkeypatch.setattr(engine, "_throttle_for_free_tier", lambda: calls.append(1))

        llm_tasks.call_llm_json(engine, "prompt")
        assert calls == [1]

    def test_test_offline_engine_declines_without_crashing(self):
        engine = fake_engine.FakeEngine()
        assert engine.client is None  # by design
        result = llm_tasks.call_llm_json(engine, "prompt", fallback="[]")
        assert result == "[]"

    def test_ollama_engine_is_not_silently_skipped(self, monkeypatch):
        """Regression test for the real Step 1d bug: OllamaEngine has no
        .client and isn't GeminiEngine, so it used to fall straight
        through to the bare fallback -- meaning flagging, consistency,
        emotion detection, translation notes, speaker tagging and the
        pacing rewrite all silently did nothing at all with Ollama."""
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": "real answer"},
                        "prompt_eval_count": 12, "eval_count": 4}

        def fake_post(url, json=None, timeout=None, stream=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()
        patch_post(monkeypatch, fake_post)

        engine = engine_local.OllamaEngine()
        usage = {}
        result = llm_tasks.call_llm_json(engine, "prompt",
                                   usage_cb=lambda inp, out: usage.update(input=inp, output=out))

        assert result == "real answer"
        assert usage == {"input": 12, "output": 4}
        assert captured["url"] == f"{engine.base_url}/api/chat"
        assert captured["json"]["format"] == "json"
        assert captured["json"]["options"]["num_ctx"] >= engine_local.OLLAMA_MIN_NUM_CTX
        assert captured["timeout"] is not None

    def test_an_engine_with_no_recognized_shape_raises_a_clear_error(self):
        """A translation-only engine (no .client,
        not Gemini/Ollama/test_offline) used to silently return the bare
        fallback here too -- the same "looks like it worked, did
        nothing" failure mode as the Ollama bug above, just for a
        different set of engines. Now raises instead of pretending to
        have produced a real (empty) result."""
        class FakeTranslationOnlyEngine:
            name = "fake_mt"

        with pytest.raises(RuntimeError, match="fake_mt can't run this feature"):
            llm_tasks.call_llm_json(FakeTranslationOnlyEngine(), "prompt", fallback="[]")

    def test_a_malformed_gemini_response_returns_fallback(self, monkeypatch):
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": []}  # no content at all

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_gemini.GeminiEngine("fake-key")
        result = llm_tasks.call_llm_json(engine, "prompt", fallback="fallback-value")
        assert result == "fallback-value"


class TestCheckConsistencyLlm:
    def test_pure_mt_engine_returns_empty_list_not_crash(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        result = llm_tasks.check_consistency_llm(lines, PureMT())
        assert result == ([], 0, 0)

    def test_no_translated_lines_returns_empty(self):
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]  # untranslated
        result = llm_tasks.check_consistency_llm(lines, MockLlm())
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
        issues, failed_batches, total_batches = llm_tasks.check_consistency_llm(
            lines, FlakyEngine(), batch_size=1)
        assert failed_batches == 1
        assert total_batches == 2
        assert len(issues) == 1  # the batch that succeeded still contributes its own issue

    def test_a_batch_with_no_usable_response_is_also_counted_as_failed(self, monkeypatch):
        """call_llm_json returns its `fallback` (None, here) rather than
        raising when a provider's response can't be parsed at all -- that
        has to be counted the same as an outright exception."""
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": []}  # no content at all

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        issues, failed_batches, total_batches = llm_tasks.check_consistency_llm(
            lines, engine_gemini.GeminiEngine("fake-key"))
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
        assert llm_tasks.generate_episode_summary(lines, PureMT()) == ""

    def test_no_translated_lines_returns_empty(self):
        class MockLlm:
            supports_reference = True
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]  # untranslated
        assert llm_tasks.generate_episode_summary(lines, MockLlm()) == ""

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
        summary = llm_tasks.generate_episode_summary(lines, engine)
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
        llm_tasks.generate_episode_summary(lines, engine)
        assert engine.call_count == 1

    def test_a_short_episode_is_sent_whole_and_a_long_one_keeps_its_opening_and_ending(self):
        from engine_backends import llm_feature_prompts as prompts
        short = [Line(idx=i, start=i, end=i + 1, zh="x", en=f"Short line {i}.") for i in range(100)]
        whole = prompts.build_episode_summary_prompt(short)
        assert "Short line 0." in whole and "Short line 99." in whole
        assert "left out" not in whole

        long = [Line(idx=i, start=i, end=i + 1, zh="x", en=f"Long line number {i}. " + "w" * 40)
                for i in range(2000)]
        cut = prompts.build_episode_summary_prompt(long)
        assert "Long line number 0." in cut and "Long line number 1999." in cut
        assert "Long line number 1000." not in cut
        assert "the middle of the episode is left out" in cut
        assert len(cut) < prompts.EPISODE_SUMMARY_SOURCE_CHARS + 2000

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
        assert llm_tasks.generate_episode_summary(lines, FakeEngine()) == ""

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
        assert llm_tasks.generate_episode_summary(lines, FailingEngine()) == ""

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
        llm_tasks.generate_episode_summary(lines, FakeEngine(), usage_cb=lambda i, o, **k: calls.append((i, o)))
        assert calls == [(100, 20)]


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
        llm_tasks.flag_uncertain_lines(lines, PureMT())
        assert lines[0].flag is None

    def test_no_translated_lines_is_a_noop(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="")]
        engine = FakeFlaggingEngine(flags_for={0: ("uncertain_translation", "x")})
        llm_tasks.flag_uncertain_lines(lines, engine)
        assert lines[0].flag is None
        assert engine.call_count == 0

    def test_flags_only_the_lines_the_model_names(self):
        lines = [Line(idx=0, start=0, end=1, zh="她昨天来了", en="She came yesterday."),
                 Line(idx=1, start=1, end=2, zh="你好", en="Hello.")]
        engine = FakeFlaggingEngine(flags_for={0: ("ambiguous_reference", "'她' unresolved")})
        llm_tasks.flag_uncertain_lines(lines, engine, batch_size=10)
        assert lines[0].flag == "ambiguous_reference"
        assert lines[0].flag_note == "'她' unresolved"
        assert lines[1].flag is None  # not named by the model -- stays clean

    def test_unknown_reason_falls_back_to_uncertain_translation(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        engine = FakeFlaggingEngine(flags_for={0: ("made_up_reason_xyz", "note")})
        llm_tasks.flag_uncertain_lines(lines, engine, batch_size=10)
        assert lines[0].flag == "uncertain_translation"

    def test_progress_cb_called_once_per_batch(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        seen = []
        llm_tasks.flag_uncertain_lines(lines, FakeFlaggingEngine(), batch_size=3, progress_cb=seen.append)
        assert seen == [0.25, 0.5, 0.75, 1.0]

    def test_engine_failure_on_one_batch_does_not_raise(self):
        class BrokenEngine(FakeFlaggingEngine):
            def create(self, model, max_tokens, messages):
                raise RuntimeError("simulated API failure")
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        # Must not raise -- a flagging pass failing shouldn't block the rest
        # of the review workflow, same reasoning as check_consistency_llm.
        llm_tasks.flag_uncertain_lines(lines, BrokenEngine())
        assert lines[0].flag is None

    def test_already_flagged_line_can_be_reflagged_on_a_later_run(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="b", flag="name_uncertain", flag_note="old")]
        engine = FakeFlaggingEngine(flags_for={0: ("slang_idiom", "new note")})
        llm_tasks.flag_uncertain_lines(lines, engine, batch_size=10)
        assert lines[0].flag == "slang_idiom"
        assert lines[0].flag_note == "new note"


class _RawReplyFlagEngine(FakeFlaggingEngine):
    def __init__(self, raw):
        super().__init__()
        self.raw = raw
        self.prompts = []

    def create(self, model, max_tokens, messages):
        self.prompts.append(messages[0]["content"])
        return type("Resp", (), {"content": [_FakeBlock(self.raw)]})()


class TestFlagPassDefects:
    @pytest.mark.parametrize("raw_id", ["[1]", "1", " 1 ", 1, "[ 1 ]"])
    def test_line_idx_formats_all_land_on_the_right_line(self, raw_id):
        import json as _json
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A"),
                 Line(idx=1, start=1, end=2, zh="b", en="B")]
        raw = _json.dumps([{"line_idx": raw_id, "reason": "slang_idiom", "note": "n"}])
        llm_tasks.flag_uncertain_lines(lines, _RawReplyFlagEngine(raw), batch_size=10)
        assert lines[0].flag is None
        assert lines[1].flag == "slang_idiom"

    def test_unparsable_entry_is_skipped_not_fatal(self):
        import json as _json
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A")]
        raw = _json.dumps([{"line_idx": "first", "reason": "slang_idiom"},
                           {"line_idx": [0], "reason": "slang_idiom"},
                           {"line_idx": True, "reason": "slang_idiom"},
                           {"line_idx": "[0]", "reason": "name_uncertain", "note": "ok"}])
        llm_tasks.flag_uncertain_lines(lines, _RawReplyFlagEngine(raw), batch_size=10)
        assert lines[0].flag == "name_uncertain"

    @pytest.mark.parametrize("system_flag", ["timing_uncertain", "content_blocked"])
    def test_system_flag_and_note_survive_the_model(self, system_flag):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A", flag=system_flag, flag_note="app note"),
                 Line(idx=1, start=1, end=2, zh="b", en="B")]
        engine = FakeFlaggingEngine(flags_for={0: ("slang_idiom", "model"), 1: ("slang_idiom", "model")})
        llm_tasks.flag_uncertain_lines(lines, engine, batch_size=10)
        assert (lines[0].flag, lines[0].flag_note) == (system_flag, "app note")
        assert (lines[1].flag, lines[1].flag_note) == ("slang_idiom", "model")

    @pytest.mark.parametrize("lang,name", [("zh", "Chinese"), ("ja", "Japanese"),
                                           ("ko", "Korean")])
    def test_prompts_name_the_source_language(self, lang, name):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A")]
        assert f"Below are {name} source lines" in llm_tasks.build_flag_prompt(lines, source_language=lang)
        assert f"Below are {name} source lines" in llm_tasks.build_consistency_prompt(
            lines, source_language=lang)

    @pytest.mark.parametrize("value,expected", [(2.0, 2), ("2", 2), (" 2 ", 2), ("[2]", 2),
                                                (2, 2), (2.5, None), (True, None), ("x", None),
                                                ([2], None), (None, None)])
    def test_parse_line_id(self, value, expected):
        assert line_text.parse_line_id(value) == expected

    def test_system_flagged_lines_are_not_sent_to_the_model(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A", flag="timing_uncertain"),
                 Line(idx=1, start=1, end=2, zh="b", en="B")]
        engine = _RawReplyFlagEngine("[]")
        llm_tasks.flag_uncertain_lines(lines, engine)
        assert "[0]" not in engine.prompts[0] and "[1]" in engine.prompts[0]

    def test_only_system_flagged_lines_makes_no_model_call(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A", flag="content_blocked")]
        engine = _RawReplyFlagEngine("[]")
        llm_tasks.flag_uncertain_lines(lines, engine)
        assert engine.prompts == []

    def test_stats_count_unparsable_entries_and_batches(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A")]
        stats = {}
        llm_tasks.flag_uncertain_lines(lines, _RawReplyFlagEngine('[{"line_idx": "x"}, {"line_idx": 0}]'),
                                stats=stats)
        assert stats == {"unparsable_entries": 1, "failed_batches": 0, "total_batches": 1}

    def test_unknown_language_is_neutral_and_never_chinese(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A")]
        for prompt in (llm_tasks.build_flag_prompt(lines), llm_tasks.build_consistency_prompt(lines, "xx")):
            assert "Chinese" not in prompt

    def test_source_language_reaches_the_flag_prompt(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="A")]
        engine = _RawReplyFlagEngine("[]")
        llm_tasks.flag_uncertain_lines(lines, engine, source_language="ja")
        assert "Japanese source lines" in engine.prompts[0]


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
        labels = llm_tasks.tag_speakers_by_id({1: "你好", 2: "那天下着雨。"}, engine)
        assert labels == {1: "Xiaoling", 2: "Narrator"}

    def test_a_response_missing_one_id_is_retried_and_lands_on_the_right_chunk(self):
        engine = SequentialClaudeShapedEngine([
            '{"1": "Xiaoling"}',  # id 2 missing from the first response
            '{"2": "Yun"}',       # retry supplies it
        ])
        labels = llm_tasks.tag_speakers_by_id({1: "你好", 2: "你也好"}, engine)
        assert labels == {1: "Xiaoling", 2: "Yun"}

    def test_still_missing_after_retry_defaults_to_narrator_not_blank(self):
        engine = SequentialClaudeShapedEngine(['{"1": "Xiaoling"}', '{}'])
        labels = llm_tasks.tag_speakers_by_id({1: "你好", 2: "你也好"}, engine)
        assert labels == {1: "Xiaoling", 2: "Narrator"}

    def test_reordered_and_extra_ids_land_on_their_own_chunks(self):
        engine = SequentialClaudeShapedEngine(['{"3": "Cara", "99": "Ghost", "1": "Ann", "2": "Narrator"}'])
        labels = llm_tasks.tag_speakers_by_id({1: "a", 2: "b", 3: "c"}, engine)
        assert labels == {1: "Ann", 2: "Narrator", 3: "Cara"}

    def test_non_contiguous_ids_are_keyed_not_positional(self):
        engine = SequentialClaudeShapedEngine(['{"7": "Ann"}', '{"4": "Bob"}'])
        labels = llm_tasks.tag_speakers_by_id({4: "a", 7: "b"}, engine)
        assert labels == {4: "Bob", 7: "Ann"}

    def test_pure_mt_engine_returns_all_narrator(self):
        class PureMT:
            supports_reference = False
        labels = llm_tasks.tag_speakers_by_id({1: "a", 2: "b"}, PureMT())
        assert labels == {1: "Narrator", 2: "Narrator"}

    def test_ollama_engine_gets_real_labels_not_a_silent_all_narrator(self, monkeypatch):
        """Step 1d exit condition: with a fake Ollama server, every
        call_llm_json-backed feature returns a real, correctly shaped
        result -- not the old silent fallback (which for this particular
        feature happened to look like a plausible "Narrator" label,
        making the bug easy to miss without a test like this one)."""
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": '{"1": "Xiaoling", "2": "Narrator"}'}}

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_local.OllamaEngine()
        labels = llm_tasks.tag_speakers_by_id({1: "你好", 2: "那天下着雨。"}, engine)
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
        llm_tasks.rewrite_for_pacing_llm(lines, engine)
        assert lines[0].en == "Short one."
        assert lines[1].en == "Short two."

    def test_a_missing_id_leaves_that_lines_en_untouched(self):
        lines = [Line(idx=0, start=0, end=1, zh="z0", en="Original one."),
                 Line(idx=1, start=0, end=1, zh="z1", en="Original two.")]
        engine = SequentialClaudeShapedEngine([
            '{"1": "Rewritten one."}',  # id 2 missing from the first response
            '{}',                        # retry also comes back empty
        ])
        llm_tasks.rewrite_for_pacing_llm(lines, engine)
        assert lines[0].en == "Rewritten one."
        assert lines[1].en == "Original two."  # untouched, not blanked or misassigned

    def test_pure_mt_engine_is_a_noop(self):
        class PureMT:
            supports_reference = False
        lines = [Line(idx=0, start=0, end=1, zh="z0", en="Original.")]
        llm_tasks.rewrite_for_pacing_llm(lines, PureMT())
        assert lines[0].en == "Original."

    def test_empty_list_is_a_noop(self):
        class Engine:
            supports_reference = True
        assert llm_tasks.rewrite_for_pacing_llm([], Engine()) == []


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
            llm_tasks.flag_uncertain_lines(lines, engine, batch_size=3, cancel_check=self._stop_after(1))
        assert engine.call_count == 1

    def test_consistency_stops_between_batches(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        engine = FakeFlaggingEngine()
        with pytest.raises(_Stop):
            llm_tasks.check_consistency_llm(lines, engine, batch_size=3, cancel_check=self._stop_after(2))
        assert engine.call_count == 2

    def test_notes_stops_between_batches(self):
        import translation_notes
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(10)]
        engine = FakeFlaggingEngine()
        with pytest.raises(_Stop):
            translation_notes.generate_translation_notes_llm(lines, engine, batch_size=3,
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
    monkeypatch.setattr("engine_backends.llm_tasks.call_llm_json", boom)
    lines = [Line(idx=i, start=0, end=1, zh=f"l{i}", en=f"L{i}") for i in range(4)]
    llm_tasks.flag_uncertain_lines(lines, FakeFlaggingEngine(), batch_size=2)
    assert seen == ["flag check failed for 2 of 2 batches; their lines were not checked"]


def test_a_fallback_chain_asks_the_engine_it_is_on(monkeypatch):
    from engine_backends import fallback, llm_tasks
    from engine_backends.gemini import GeminiEngine

    class Chain(fallback.FallbackEngine):
        def __init__(self, engine):
            self.engines, self.choices, self.active = [engine], ["gemini"], 0

    engine = GeminiEngine.__new__(GeminiEngine)
    engine.model = "gemini-x"
    engine.prompt_body = lambda prompt, system=None: {"p": prompt}
    engine.generate = lambda body: ({"candidates": [{"content": {"parts": [{"text": "{\"1\": \"ok\"}"}]}, "finishReason": "STOP"}]},
                                    {"input_tokens": 1, "output_tokens": 1})
    assert llm_tasks.call_llm_json(Chain(engine), "q", fallback="{}") == '{"1": "ok"}'
