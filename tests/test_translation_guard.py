"""Merged / shifted / untranslated replies that still carry every id, and
Reflect's cached system block. Mocked engines, no network."""
import json
from types import SimpleNamespace as NS

import pytest

import translate_engines as te
from core import Line
from engine_backends import translation_guard as guard
from engine_backends.claude import ClaudeEngine
from engine_backends.llm_tasks import call_llm_json

# 14 CJK characters each: about 7 English words expected.
SOURCES = ["今天晚上我们一起去吃饭吧", "明天早上我要去公司开会啊", "你昨天为什么没有给我打电话",
           "这个周末我们去海边玩好吗", "我真的不知道他到底在想什么", "请你把这份文件交给王经理吧"]
GOOD = ["Let's all go out for dinner together tonight.", "I have to go to the office for a meeting tomorrow.",
        "Why didn't you call me yesterday, not even once?", "Shall we go and play at the beach this weekend?",
        "I honestly have no idea what he is thinking.", "Please hand this document over to Manager Wang."]
FLAG = "uncertain_translation"


def _lines(n=6, sources=None):
    sources = sources or SOURCES
    return [Line(idx=i, start=i, end=i + 1, zh=sources[i], id=100 + i) for i in range(n)]


class Scripted:
    """Answers each call from `script` (a list of {line id: text}); a call past
    the script, or an id the script leaves out, gets GOOD text for that id."""
    name = "scripted"
    supports_reference = True
    model = "m"

    def __init__(self, *script, usage=None):
        self.script = list(script)
        self.calls = []
        self.usage = usage

    def translate_batch(self, zh_lines, context):
        ids = list(context["line_ids"])
        self.calls.append(ids)
        reply = self.script.pop(0) if self.script else {}
        if self.usage:
            self.last_usage = dict(self.usage)
        return [reply.get(i, GOOD[i - 100]) for i in ids]


def _run(engine, lines, **kw):
    return te.translate_lines_with_engine(lines, engine, {}, batch_size=kw.pop("batch_size", 6), **kw)


def _flagged(lines):
    return {ln.id for ln in lines if ln.flag}


class TestChecks:
    def test_a_clean_batch_has_no_suspects(self):
        assert guard.suspect_positions(SOURCES, GOOD) == {}

    def test_one_line_taking_its_neighbours_text_is_caught(self):
        replies = list(GOOD)
        replies[1] = GOOD[1] + " " + GOOD[2] + " Why didn't you call?"
        replies[2] = "Why?"
        assert set(guard.suspect_positions(SOURCES, replies)) >= {1, 2}

    def test_leftover_source_script_is_caught_but_not_quoted_or_glossary_text(self):
        assert guard.leftover_source_script("Let's go eat 吃饭 now")
        assert not guard.leftover_source_script('He said "吃饭" to me')
        assert not guard.leftover_source_script("Master 白鹤 arrived", {"白鹤"})

    def test_short_identical_replies_for_different_lines_are_fine(self):
        assert guard.duplicate_positions(["嗯", "哦"], ["I see.", "I see."]) == set()

    def test_four_word_identical_replies_for_near_identical_sources_are_fine(self):
        assert guard.duplicate_positions(["銃が無かった", "銃は無かった"],
                                         ["- There was no gun.", "- There was no gun."]) == set()

    def test_five_word_identical_replies_for_different_lines_are_still_caught(self):
        assert guard.duplicate_positions(["今日は寒い", "明日は暑い"],
                                         ["It is very cold today.", "It is very cold today."]) == {0, 1}

    def test_a_short_reply_inside_a_longer_one_is_fine(self):
        assert guard.duplicate_positions(
            ["彼はレッドジョンだ", "どうして彼がレッドジョンだと分かった"],
            ["He's Red John.", "How did you know he was Red John?"]) == set()

    def test_a_long_reply_swallowed_by_a_longer_one_is_still_caught(self):
        long_reply = "We have to leave this place right now."
        assert guard.duplicate_positions(
            ["早く出よう", "何を言っているの"],
            [long_reply, "Why are you saying that? " + long_reply]) == {0, 1}

    def test_allowed_runs_come_from_the_glossary_and_guidelines(self):
        runs = guard.allowed_source_runs([{"term_original": "白鹤", "term_translation": "Baihe"}],
                                         "- 青云 -> Qingyun")
        assert runs == {"白鹤", "青云"}


class TestMergedPair:
    def test_one_reask_repairs_a_merged_pair(self):
        engine = Scripted({101: GOOD[1] + " " + GOOD[2] + " Why didn't you call?", 102: "Why?"})
        lines = _lines()
        _run(engine, lines)
        assert engine.calls == [[100, 101, 102, 103, 104, 105], [101, 102]]
        assert [ln.en for ln in lines] == GOOD
        assert _flagged(lines) == set()

    def test_still_merged_after_the_reask_is_kept_and_flagged(self):
        merged = {101: GOOD[1] + " " + GOOD[2] + " Why didn't you call?", 102: "Why?"}
        engine = Scripted(merged, merged)
        lines = _lines()
        _run(engine, lines)
        assert len(engine.calls) == 2
        assert lines[1].en == merged[101]  # the reply is kept, not blanked
        assert _flagged(lines) == {101, 102}
        assert all(ln.flag == FLAG and ln.flag_note == "check: may be merged or shifted"
                   for ln in lines if ln.id in (101, 102))


class TestShiftedPair:
    def test_swapped_neighbours_are_reasked(self):
        a_text = "Let's all go out for dinner together tonight and then go and see a film."  # 15 words
        b_text = "I have to go to the office tomorrow."  # 8 words
        lines = _lines()
        lines[0].zh = "今天晚上我们一起去吃饭然后再去看一场电影吧大家觉得怎么样"  # 28 characters
        lines[1].zh = "明天早上我要去公司开会啊"  # 12 characters
        engine = Scripted({100: b_text, 101: a_text}, {100: a_text, 101: b_text})
        _run(engine, lines)
        assert engine.calls[1] == [100, 101]
        assert (lines[0].en, lines[1].en) == (a_text, b_text)
        assert _flagged(lines) == set()


class TestDuplicates:
    def test_identical_replies_for_different_lines_are_reasked(self):
        engine = Scripted({103: GOOD[0]})
        lines = _lines()
        _run(engine, lines)
        assert engine.calls == [[100, 101, 102, 103, 104, 105], [100, 103]]
        assert [ln.en for ln in lines] == GOOD


class TestLeftoverScript:
    def test_untranslated_text_is_reasked(self):
        engine = Scripted({102: "Why didn't you 给我打电话 yesterday?"})
        lines = _lines()
        _run(engine, lines)
        assert engine.calls[1] == [102]
        assert lines[2].en == GOOD[2]

    def test_a_title_glossary_term_kept_in_source_script_is_not_a_problem(self):
        engine = Scripted({102: "Why didn't you call 白鹤 yesterday?"})
        lines = _lines()
        _run(engine, lines, glossary_terms=[{"term_original": "白鹤", "term_translation": "白鹤"}])
        assert len(engine.calls) == 1
        assert _flagged(lines) == set()


class TestBoundedSpend:
    def test_a_clean_batch_makes_no_extra_call(self):
        engine = Scripted()
        _run(engine, _lines())
        assert len(engine.calls) == 1

    def test_the_reask_goes_at_most_one_halving_level_and_never_loops(self):
        bad = {i: "Nonsense 句子 here" for i in range(100, 106)}
        engine = Scripted(bad, bad, bad, bad, bad, bad)
        lines = _lines()
        _run(engine, lines)
        # one request for the batch, then its six suspects in two halves of three
        assert [len(c) for c in engine.calls] == [6, 3, 3]
        assert _flagged(lines) == {ln.id for ln in lines}

    def test_reasks_are_logged_as_usage(self):
        usage = []
        engine = Scripted({102: "Why didn't you 给我打电话 yesterday?"},
                          usage={"input_tokens": 10, "output_tokens": 5})
        _run(engine, _lines(), usage_cb=lambda *a: usage.append(a))
        assert len(engine.calls) == 2 and len(usage) == 2

    def test_a_reached_cost_cap_stops_the_reasks(self):
        engine = Scripted({102: "Why didn't you 给我打电话 yesterday?"},
                          usage={"input_tokens": 1_000_000, "output_tokens": 0})
        engine.model = "claude-sonnet-5"
        lines = _lines()
        _run(engine, lines, cost_cap_usd=0.5, check_replies=True)
        assert len(engine.calls) == 1
        assert lines[2].flag == FLAG  # kept and flagged instead

    def test_a_cancel_stops_the_reasks(self):
        engine = Scripted({102: "Why didn't you 给我打电话 yesterday?"})
        _run(engine, _lines(), cancel_check_cb=lambda: len(engine.calls) >= 1)
        assert len(engine.calls) == 1

    def test_a_failing_reask_keeps_the_original_and_flags_it(self):
        class Failing(Scripted):
            def translate_batch(self, zh_lines, context):
                if self.calls:
                    self.calls.append(None)
                    raise RuntimeError("boom sk-ant-api03-abcdefghijklmnop")
                return super().translate_batch(zh_lines, context)
        engine = Failing({102: "Why didn't you 给我打电话 yesterday?"})
        lines = _lines()
        _, errors = _run(engine, lines)
        assert lines[2].flag == FLAG and lines[2].en.startswith("Why didn't you 给我")
        assert errors == []

    def test_reflect_and_echo_engines_are_not_checked(self):
        class Echo(Scripted):
            checks_replies = False
        engine = Echo({102: "Why didn't you 给我打电话 yesterday?"})
        _run(engine, _lines())
        assert len(engine.calls) == 1


MERGED = {101: GOOD[1] + " " + GOOD[2] + " Why didn't you call?", 102: "Why?"}


def _merged_run(model, **kw):
    engine = Scripted(MERGED)
    engine.model = model
    _run(engine, _lines(), **kw)
    return engine


class TestEngineAwareDefault:
    @pytest.mark.parametrize("model", [
        "claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"])
    def test_claude_runs_no_guard_by_default(self, model):
        assert len(_merged_run(model).calls) == 1

    def test_gemini_pro_runs_no_guard_but_flash_does(self):
        from engine_backends.gemini import GeminiEngine
        for model, calls in (("gemini-pro-latest", 1), ("gemini-flash-lite-latest", 2)):
            engine = GeminiEngine("key", model)
            scripted = Scripted(MERGED)
            engine.translate_batch = scripted.translate_batch
            _run(engine, _lines())
            assert len(scripted.calls) == calls

    @pytest.mark.parametrize("model", [
        "deepseek-chat", "gemini-flash-lite-latest", "gemini-flash-latest", "qwen3:8b",
        "some-unknown-model", ""])
    def test_other_engines_and_unknown_models_keep_the_reask(self, model):
        assert len(_merged_run(model).calls) == 2

    def test_registry_classifies_by_engine_name_and_instance(self):
        assert not te.default_reply_guard("claude")
        assert not te.default_reply_guard("gemini", "gemini-pro-latest")
        assert te.default_reply_guard("gemini", "gemini-flash-latest")
        assert te.default_reply_guard("deepseek")
        assert te.default_reply_guard("ollama", "llama3")

    def test_an_explicit_choice_wins_both_ways(self):
        assert len(_merged_run("claude-sonnet-5-5", check_replies=True).calls) == 2
        assert len(_merged_run("deepseek-chat", check_replies=False).calls) == 1

    def test_an_engine_that_cannot_be_checked_stays_unchecked_even_when_forced(self):
        class Echo(Scripted):
            checks_replies = False
        engine = Echo(MERGED)
        _run(engine, _lines(), check_replies=True)
        assert len(engine.calls) == 1


class TestMatchedById:
    def _engine(self, replies_by_call):
        calls = []

        def create(**kw):
            calls.append(kw)
            reply = replies_by_call[len(calls) - 1]
            body = json.dumps(dict(reversed(list(reply.items()))), ensure_ascii=False)
            return NS(content=[NS(type="text", text=body)], stop_reason="end_turn",
                      usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                               cache_creation_input_tokens=0))
        engine = ClaudeEngine("sk-ant-fake")
        engine.client = NS(messages=NS(create=create))
        return engine, calls

    def test_a_reordered_reply_lands_on_the_right_lines(self):
        first = {str(100 + i): GOOD[i] for i in range(6)}
        first["101"] = "Why didn't you 给我打电话 yesterday?"
        first["104"] = "Another 句子 left over."
        redo = {"101": GOOD[1], "104": GOOD[4]}
        engine, calls = self._engine([first, redo])
        lines = _lines()
        _run(engine, lines, check_replies=True)
        assert len(calls) == 2
        asked = calls[1]["messages"][0]["content"]
        assert "101. " in asked and "104. " in asked and "100. " not in asked
        assert [ln.en for ln in lines] == GOOD


class TestReflectCachedInstructions:
    class Claude:
        supports_reference = True
        model = "claude-sonnet-5"

        def __init__(self):
            self.client = self
            self.messages = self
            self.requests = []

        def create(self, **kw):
            self.requests.append(kw)
            n = len(self.requests)
            text = {1: '{"1": "Draft."}', 2: '{"1": "awkward"}'}.get(n, '{"1": "Final."}')
            return NS(content=[NS(type="text", text=text)],
                      usage=NS(input_tokens=5, output_tokens=5, cache_read_input_tokens=900,
                               cache_creation_input_tokens=100))

    def test_the_instructions_are_a_cached_system_block_on_every_pass(self):
        engine, usage = self.Claude(), []
        te.reflect_translate_batch(engine, ["你好"], {}, usage_cb=lambda *a: usage.append(a))
        assert len(engine.requests) == 3
        for req in engine.requests:
            assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
            assert "translator" in req["system"][0]["text"].lower() or len(req["system"][0]["text"]) > 200
            assert req["system"][0]["text"] not in req["messages"][0]["content"]
        assert len({req["system"][0]["text"] for req in engine.requests}) == 1
        # cache fields reach usage logging: the whole prompt is input + read + write
        assert usage[0] == (1005, 5, 900, 100)

    def test_the_model_sees_the_same_text_as_the_single_string_prompt(self):
        engine = self.Claude()
        te.reflect_translate_batch(engine, ["你好"], {})
        req = engine.requests[0]
        instructions = req["system"][0]["text"]
        old = te.build_reflect_faithful_prompt(instructions, "", [1], {1: "你好"})
        assert instructions + "\n\n" + req["messages"][0]["content"] == old

    def test_the_prompt_text_saved_per_request(self):
        """Instructions are the bulk of each request; moving them to the cached
        system block means passes 2 and 3 and every later batch re-read them at
        the cache-read rate instead of paying full input price for them."""
        engine = self.Claude()
        te.reflect_translate_batch(engine, ["你好"], {})
        system = len(engine.requests[0]["system"][0]["text"])
        user = len(engine.requests[0]["messages"][0]["content"])
        assert system > user  # the repeated part is now the larger share of each request

    def test_one_pass_returns_the_draft_with_a_single_request(self):
        engine = self.Claude()
        assert te.reflect_translate_batch(engine, ["你好"], {}, passes=1) == (["Draft."], [""])
        assert len(engine.requests) == 1

    def test_the_default_is_still_three_passes(self):
        engine = self.Claude()
        te.reflect_translate_batch(engine, ["你好"], {})
        assert len(engine.requests) == 3

    def test_a_pass_count_other_than_one_or_three_is_refused(self):
        with pytest.raises(ValueError):
            te.reflect_translate_batch(self.Claude(), ["你好"], {}, passes=2)

    def test_call_llm_json_without_system_is_unchanged(self):
        engine = self.Claude()
        call_llm_json(engine, "hi")
        assert "system" not in engine.requests[0]


class TestReaskFailuresAndForcedRetranslate:
    def test_a_reask_that_hits_the_daily_limit_is_recorded_and_stops_the_run(self):
        class Limited(Scripted):
            def translate_batch(self, zh_lines, context):
                if self.calls:
                    raise te.FreeTierDailyLimitReached("daily limit")
                return super().translate_batch(zh_lines, context)
        engine = Limited({102: "Why didn't you 给我打电话 yesterday?"})
        lines = _lines(6) + [Line(idx=10 + i, start=10 + i, end=11 + i, zh=SOURCES[i], id=200 + i)
                             for i in range(6)]
        _, errors = _run(engine, lines)
        assert len(errors) == 1 and errors[0]["batch_index"] == 0
        assert errors[0]["lines"] == [lines[2].idx] and "daily limit" in errors[0]["error"]
        assert all(not ln.en for ln in lines[6:])  # the run stopped; the error says why

    def test_force_retranslate_leaves_a_line_the_reply_omitted_alone(self):
        class Omits(Scripted):
            def translate_batch(self, zh_lines, context):
                out = super().translate_batch(zh_lines, context)
                return ["" if i == 102 else t for i, t in zip(context["line_ids"], out)]
        engine = Omits()
        lines = _lines()
        lines[2].en = "My own hand-edited English 吗"  # would look suspect to the checks
        _run(engine, lines, force_retranslate=True)
        assert lines[2].en == "My own hand-edited English 吗" and not lines[2].flag
        assert len(engine.calls) == 1
