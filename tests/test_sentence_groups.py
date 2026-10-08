"""Translate-by-sentence: grouping, prompt shape, id-keyed distribution,
retry/fallback, the deterministic splitter and the OFF-is-unchanged guarantee.
The "model" is a scripted function behind the real request/retry path."""
import json

import pytest

import sentence_groups as sg
import translate_engines
from core import Line
from engine_backends.shared import request_translations_with_retry
from engine_backends.translate_pipeline import SCENE_BREAK_GAP_SECONDS


def L(idx, zh, start, end, id=None, **kw):
    return Line(idx=idx, start=start, end=end, zh=zh, id=id if id is not None else idx + 100, **kw)


def fragments():
    # One sentence in three parts, then a finished sentence.
    return [L(0, "我今天去了", 0.0, 1.0), L(1, "商店", 1.2, 1.8), L(2, "买东西。", 2.0, 3.0),
            L(3, "好的。", 3.2, 4.0)]


class TestIsSentenceFinal:
    @pytest.mark.parametrize("text", ["好。", "真的？", "なに!", "それは…", "他说“好”", "「行」", "好。」", "Fine."])
    def test_final(self, text):
        assert sg.is_sentence_final(text)

    @pytest.mark.parametrize("text", ["我今天去了", "然后，", "and then", "", "  "])
    def test_not_final(self, text):
        assert not sg.is_sentence_final(text)


class TestGroupFragments:
    def sizes(self, lines, target=None):
        return [len(g) for g in sg.group_fragments(target or lines, lines)]

    def test_groups_unfinished_fragments_and_stops_at_final_punctuation(self):
        assert self.sizes(fragments()) == [3, 1]

    def test_gap_constant_stays_below_the_scene_break(self):
        assert sg.MAX_GAP_SECONDS < SCENE_BREAK_GAP_SECONDS

    def test_large_gap_starts_a_new_group(self):
        lines = fragments()
        lines[1].start, lines[1].end = 1.0 + sg.MAX_GAP_SECONDS + 0.1, 3.0
        lines[2].start, lines[2].end = 3.1, 4.0
        assert self.sizes(lines)[:2] == [1, 2]

    def test_different_speaker_or_language_breaks_a_group(self):
        lines = fragments()
        lines[1].speaker = "B"
        assert self.sizes(lines) == [1, 1, 1, 1]
        lines = fragments()
        lines[2].lang = "ja"
        assert self.sizes(lines) == [2, 1, 1]

    def test_no_speaker_on_both_sides_still_groups(self):
        assert all(ln.speaker is None for ln in fragments())
        assert self.sizes(fragments())[0] == 3

    @pytest.mark.parametrize("field,value", [("flag", "uncertain"), ("sfx", True), ("zh", "  ")])
    def test_flagged_sfx_or_blank_lines_are_never_grouped(self, field, value):
        lines = fragments()
        setattr(lines[1], field, value)
        assert self.sizes(lines)[0] == 1

    def test_line_cap(self):
        lines = [L(i, "字", i * 0.5, i * 0.5 + 0.4) for i in range(9)]
        assert self.sizes(lines) == [sg.MAX_GROUP_LINES, sg.MAX_GROUP_LINES, 1]

    def test_char_cap(self):
        lines = [L(i, "字" * 25, i * 1.0, i * 1.0 + 0.9) for i in range(3)]
        assert self.sizes(lines) == [2, 1]

    def test_duration_cap(self):
        lines = [L(i, "字", i * 5.0, i * 5.0 + 4.5) for i in range(3)]
        assert self.sizes(lines) == [2, 1]

    def test_a_line_that_is_not_a_target_is_never_pulled_in(self):
        lines = fragments()
        lines[1].en = "already translated"
        target = [ln for ln in lines if not ln.en]
        assert self.sizes(lines, target) == [1, 1, 1]


class TestBatchAlignment:
    def test_a_group_cut_by_a_boundary_moves_whole_into_the_earlier_batch(self):
        lines = fragments()
        groups = sg.group_fragments(lines, lines)
        batches = sg.align_batches_to_groups([lines[:2], lines[2:]], groups)
        assert [[ln.idx for ln in b] for b in batches] == [[0, 1, 2], [3]]

    def test_an_emptied_batch_is_dropped(self):
        lines = fragments()[:3]
        groups = sg.group_fragments(lines, lines)
        assert len(sg.align_batches_to_groups([lines[:2], lines[2:]], groups)) == 1

    def test_complete_groups_ignores_partial_ones(self):
        lines = fragments()
        groups = sg.group_fragments(lines, lines)
        assert sg.complete_groups(lines[:2], groups) == []
        assert len(sg.complete_groups(lines, groups)) == 1


class TestSplitTranslation:
    def test_splits_in_proportion_to_source_length(self):
        pieces = sg.split_translation("I went to the store to buy things today", [5, 2, 3])
        assert len(pieces) == 3 and all(pieces)
        assert " ".join(pieces) == "I went to the store to buy things today"
        assert len(pieces[0].split()) >= len(pieces[1].split())

    def test_prefers_a_break_after_a_comma(self):
        pieces = sg.split_translation("When I got there, nobody was home", [1, 1])
        assert pieces == ["When I got there,", "nobody was home"]

    def test_every_piece_is_non_empty_even_for_skewed_weights(self):
        pieces = sg.split_translation("a b c d", [100, 1, 1, 1])
        assert pieces == ["a", "b", "c", "d"]

    def test_too_few_words_returns_none(self):
        assert sg.split_translation("Hello", [1, 1]) is None
        assert sg.split_translation("", [1, 1]) is None

    def test_words_are_never_cut(self):
        text = "extraordinarily unremarkable circumstances"
        assert sg.split_translation(text, [3, 3]) is not None
        assert " ".join(sg.split_translation(text, [3, 3])) == text


class TestSettleGroup:
    zh = ["我今天去了", "商店", "买东西"]

    def test_distinct_complete_pieces_are_kept(self):
        assert sg.settle_group(self.zh, ["I went", "to the store", "to shop"]) == [
            "I went", "to the store", "to shop"]

    def test_whole_sentence_in_one_piece_is_split(self):
        out = sg.settle_group(self.zh, ["I went to the store to shop", "", ""])
        assert out and all(out)

    def test_sentence_repeated_in_every_piece_is_split(self):
        s = "I went to the store to shop"
        out = sg.settle_group(self.zh, [s, s, s])
        assert out and " ".join(out) == s

    def test_partial_distribution_falls_back(self):
        assert sg.settle_group(self.zh, ["I went", "", "to shop"]) is None
        assert sg.settle_group(self.zh, ["", "", ""]) is None

    def test_unsplittable_repeat_falls_back(self):
        assert sg.settle_group(self.zh, ["Hmm", "", ""]) is None

    def test_identical_source_fragments_may_have_identical_pieces(self):
        assert sg.settle_group(["啊", "啊"], ["Ah", "Ah"]) == ["Ah", "Ah"]


class TestRenderGrouped:
    def test_header_note_and_numbered_parts(self):
        text = sg.render_grouped(
            [1, 2, 3], {1: "1. 你好", 2: "2. 世界", 3: "3. 好。"}, {1: [1, 2], 2: [1, 2]},
            {1: "你好", 2: "世界", 3: "好。"})
        assert text.startswith(sg.SENTENCE_NOTE)
        assert text.endswith("Sentence in 2 parts: 你好世界\n1. 你好\n2. 世界\n3. 好。")

    def test_no_note_when_nothing_is_grouped(self):
        assert sg.render_grouped([1], {1: "1. a"}, {}, {1: "a"}) == "1. a"

    def test_korean_pieces_are_joined_with_a_space(self):
        assert sg.join_fragments(["저는 오늘", "가게에 갔어요"]) == "저는 오늘 가게에 갔어요"


class ScriptedEngine:
    """An instruction-following engine on the real shared request path;
    `reply(prompt_lines)` plays the model."""
    supports_reference = True

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def translate_batch(self, zh_lines, context):
        def call_model(numbered):
            self.prompts.append(numbered)
            return self.reply(numbered)
        return request_translations_with_retry(
            zh_lines, context.get("speaker_labels"), call_model,
            line_ids=context.get("line_ids"), engine_name="fake",
            line_languages=context.get("line_languages"),
            sentence_groups=context.get("sentence_groups"))


def numbered_ids(prompt):
    return [int(ln.split(".")[0]) for ln in prompt.splitlines()
            if ln.split(".")[0].isdigit() and ". " in ln]


def distribute_reply(table):
    def reply(prompt):
        return json.dumps({str(i): table[i] for i in numbered_ids(prompt) if i in table})
    return reply


def run(engine, lines, **kw):
    return translate_engines.translate_lines_with_engine(
        lines, engine, {"title_en": "T"}, **kw)


class TestPipeline:
    def test_distributes_by_fragment_id_and_leaves_everything_else_alone(self):
        lines = fragments()
        before = [(ln.start, ln.end, ln.zh, ln.speaker, ln.id, ln.flag) for ln in lines]
        engine = ScriptedEngine(distribute_reply(
            {100: "I went", 101: "to the store", 102: "to shop.", 103: "Okay."}))
        _, errors = run(engine, lines, translate_by_sentence=True)
        assert errors == []
        assert [ln.en for ln in lines] == ["I went", "to the store", "to shop.", "Okay."]
        assert before == [(ln.start, ln.end, ln.zh, ln.speaker, ln.id, ln.flag) for ln in lines]
        assert len(engine.prompts) == 1
        assert "Sentence in 3 parts: 我今天去了商店买东西" in engine.prompts[0]
        assert sg.SENTENCE_NOTE in engine.prompts[0]

    def test_reordered_reply_still_lands_by_id(self):
        lines = fragments()
        reply = lambda p: json.dumps({"103": "Okay.", "102": "to shop.", "100": "I went",
                                       "101": "to the store"})
        run(ScriptedEngine(reply), lines, translate_by_sentence=True)
        assert [ln.en for ln in lines] == ["I went", "to the store", "to shop.", "Okay."]

    def test_missing_fragment_is_retried_with_its_whole_sentence(self):
        lines = fragments()
        calls = []

        def reply(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps({"100": "I went", "102": "to shop.", "103": "Okay."})
            return json.dumps({"101": "to the store"})
        run(ScriptedEngine(reply), lines, translate_by_sentence=True)
        assert [ln.en for ln in lines] == ["I went", "to the store", "to shop.", "Okay."]
        assert "Sentence in 3 parts" in calls[1] and "103." not in calls[1]

    def test_unusable_group_falls_back_to_the_normal_path_for_that_group_only(self):
        lines = fragments()

        def reply(prompt):
            if "Sentence in" in prompt:  # grouped request: fragment 101 never comes back
                return json.dumps({"100": "I went", "102": "to shop.", "103": "Okay."})
            return json.dumps({str(i): f"plain {i}" for i in numbered_ids(prompt)})
        engine = ScriptedEngine(reply)
        _, errors = run(engine, lines, translate_by_sentence=True)
        assert errors == []
        assert [ln.en for ln in lines] == ["plain 100", "plain 101", "plain 102", "Okay."]
        assert "Sentence in" not in engine.prompts[-1] and numbered_ids(engine.prompts[-1]) == [100, 101, 102]

    def test_whole_sentence_in_one_id_is_split_deterministically(self):
        lines = fragments()[:3]
        reply = distribute_reply({100: "I went to the store to shop", 101: "", 102: ""})
        _, errors = run(ScriptedEngine(reply), lines, translate_by_sentence=True)
        assert errors == []
        assert all(ln.en for ln in lines)
        assert " ".join(ln.en for ln in lines) == "I went to the store to shop"

    def test_already_translated_neighbour_is_not_touched_or_grouped(self):
        lines = fragments()
        lines[1].en = "kept"
        engine = ScriptedEngine(distribute_reply({100: "A", 102: "B", 103: "C"}))
        run(engine, lines, translate_by_sentence=True)
        assert lines[1].en == "kept"
        assert "Sentence in" not in engine.prompts[0]

    def test_batch_boundary_does_not_cut_a_sentence(self):
        lines = fragments()
        engine = ScriptedEngine(distribute_reply(
            {100: "a", 101: "b", 102: "c", 103: "d"}))
        run(engine, lines, translate_by_sentence=True, batch_size=2)
        assert numbered_ids(engine.prompts[0]) == [100, 101, 102]

    def test_saved_choice_on_the_title_is_used_when_not_passed(self):
        lines = fragments()
        engine = ScriptedEngine(distribute_reply({100: "a", 101: "b", 102: "c", 103: "d"}))
        translate_engines.translate_lines_with_engine(
            lines, engine, {"title_en": "T", "translate_by_sentence": 1})
        assert "Sentence in" in engine.prompts[0]

    def test_inert_for_reflect_and_for_engines_that_dont_follow_instructions(self):
        engine = ScriptedEngine(distribute_reply({}))
        engine.supports_reference = False
        engine.translate_batch = lambda zh, ctx: [f"t{i}" for i, _ in enumerate(zh)]
        lines = fragments()
        run(engine, lines, translate_by_sentence=True)
        assert [ln.en for ln in lines] == ["t0", "t1", "t2", "t3"]


class TestOffIsUnchanged:
    def prompts(self, **kw):
        lines = fragments()
        engine = ScriptedEngine(distribute_reply({100: "a", 101: "b", 102: "c", 103: "d"}))
        translate_engines.translate_lines_with_engine(
            lines, engine, kw.pop("meta", {"title_en": "T"}), **kw)
        return engine.prompts

    def test_off_sends_the_plain_numbered_list(self):
        assert self.prompts() == [
            "100. 我今天去了\n101. 商店\n102. 买东西。\n103. 好的。"]

    def test_unset_false_and_zero_are_all_identical(self):
        base = self.prompts()
        assert self.prompts(translate_by_sentence=False) == base
        assert self.prompts(meta={"title_en": "T", "translate_by_sentence": 0}) == base
        assert self.prompts(meta={"title_en": "T", "translate_by_sentence": None}) == base

    def test_on_with_nothing_to_group_is_identical_to_off(self):
        lines = [L(0, "好。", 0.0, 1.0), L(1, "行。", 5.0, 6.0)]
        engine_on = ScriptedEngine(distribute_reply({100: "a", 101: "b"}))
        engine_off = ScriptedEngine(distribute_reply({100: "a", 101: "b"}))
        translate_engines.translate_lines_with_engine(
            lines, engine_on, {}, translate_by_sentence=True)
        translate_engines.translate_lines_with_engine(
            [L(0, "好。", 0.0, 1.0), L(1, "行。", 5.0, 6.0)], engine_off, {})
        assert engine_on.prompts == engine_off.prompts


def test_provider_prompt_carries_the_sentence_block(monkeypatch):
    """The grouped block reaches the user message of a real engine's request."""
    from engine_backends.prompts import build_batch_user_message
    block = sg.render_grouped([1, 2], {1: "1. 你", 2: "2. 好"}, {1: [1, 2], 2: [1, 2]},
                              {1: "你", 2: "好"})
    message = build_batch_user_message({}, block)
    assert message.startswith("Translate these lines:\n\n" + sg.SENTENCE_NOTE)
