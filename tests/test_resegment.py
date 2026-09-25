"""
tests/test_resegment.py -- Step 6c, meaning-based subtitle re-segmentation.

Whisper's line boundaries come from silence gaps, not meaning. These pin
the three things that make splitting safe: a line is only ever cut at a
meaningful boundary (never mid-word, never just by length), the LLM pass
can only choose WHERE to cut and never what the text says, and saving the
result clears translations/notes/flags only on the lines that actually
changed.
"""
import json
import os
import queue
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import resegment as rs
from core import Line

RUN_ON_A = "我今天早上去了市场买了很多新鲜的蔬菜和水果"      # 21 chars
RUN_ON_B = "但是回家的路上突然下起了大雨所以我全身都湿透了"    # 23 chars
RUN_ON = RUN_ON_A + RUN_ON_B                                   # 44 > 32, no punctuation


def _every_char_bounds(text, language, chinese_script="simplified"):
    return set(range(len(text) + 1))


def _no_bounds(text, language, chinese_script="simplified"):
    return None


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class ScriptedEngine:
    """Claude-shaped fake (client.messages.create) that returns the given
    answers in order -- one per call."""
    supports_reference = True
    model = "fake-model"

    def __init__(self, answers):
        self.client = self
        self.messages = self
        self.answers = list(answers)
        self.prompts = []

    def create(self, model, max_tokens, messages):
        self.prompts.append(messages[0]["content"])
        answer = self.answers.pop(0) if self.answers else "{}"
        return type("Resp", (), {"content": [_Block(answer)]})()


def _marked(*parts):
    return json.dumps({"text": "[br]".join(parts)}, ensure_ascii=False)


class TestRuleBasedSplit:
    def test_run_on_asr_segment_splits_at_a_genuine_clause_boundary(self):
        pytest.importorskip("jieba")
        bounds = rs.word_boundaries(RUN_ON, "zh")
        spans = rs.rule_split_spans(RUN_ON, "zh", rs.max_line_chars("zh"), bounds)
        pieces = [RUN_ON[s:e] for s, e in spans]
        assert pieces == [RUN_ON_A, RUN_ON_B]  # split right before 但是, not at 32 chars
        assert all(s in bounds for s, _ in spans)  # never mid-word

    def test_normal_segment_passes_through_unchanged(self):
        ln = Line(idx=0, start=1.0, end=2.0, zh="你好。我是小明。", en="Hi. I'm Xiaoming.",
                  flag="needs_review", id=7)
        new_lines, changed = rs.resegment_lines([ln], "zh", boundaries_fn=_every_char_bounds)
        assert changed == []
        assert len(new_lines) == 1
        out = new_lines[0]
        assert (out.zh, out.en, out.flag, out.id, out.start, out.end) == \
               ("你好。我是小明。", "Hi. I'm Xiaoming.", "needs_review", 7, 1.0, 2.0)

    def test_sentence_ends_are_preferred_over_commas(self):
        text = "他说他明天会来，可是我不太相信他。因为他上次也是这么说的，结果根本没有出现"
        spans = rs.rule_split_spans(text, "zh", 32, None)
        assert text[spans[0][0]:spans[0][1]] == "他说他明天会来，可是我不太相信他。"

    def test_closing_punctuation_stays_with_its_sentence(self):
        text = "「我不知道他在哪里……」她小声地说，然后转身离开了房间再也没有回来过了"
        spans = rs.rule_split_spans(text, "zh", 32, None)
        assert text[spans[0][0]:spans[0][1]] == "「我不知道他在哪里……」"

    def test_no_one_or_two_character_stubs(self):
        text = "啊，" + "我" * 40
        spans = rs.rule_split_spans(text, "zh", 32, _every_char_bounds(text, "zh"))
        assert spans == [(0, len(text))]  # the only cut would leave a 1-char stub

    def test_a_long_line_with_no_meaningful_boundary_is_left_whole(self):
        text = "我" * 45  # no punctuation, no connective -- only a length-based cut would split it
        ln = Line(idx=0, start=0.0, end=9.0, zh=text)
        new_lines, changed = rs.resegment_lines([ln], "zh", boundaries_fn=_every_char_bounds)
        assert changed == []
        assert [l.zh for l in new_lines] == [text]

    def test_connective_inside_a_longer_word_is_not_a_boundary(self):
        text = "他怎么也说不出一个所以然来他怎么也说不出一个所以然来他怎么也说不出"
        # Pretend the segmenter found 所以然 as one word: offsets of 所以 are
        # not both boundaries, so there's nothing to split before.
        start = text.index("所以然")
        bounds = set(range(len(text) + 1)) - {start + 1, start + 2}
        bounds -= {text.index("所以然", start + 1) + 1, text.index("所以然", start + 1) + 2}
        assert rs.rule_split_spans(text, "zh", 32, bounds) == [(0, len(text))]

    def test_missing_segmenter_means_no_word_boundaries_not_a_crash(self, monkeypatch):
        import segment

        def _missing(*a, **k):
            raise ImportError("No module named 'jieba'")
        monkeypatch.setattr(segment, "segment_words", _missing)
        assert rs.word_boundaries(RUN_ON, "zh") is None

    def test_connectives_need_a_segmenter(self):
        # Without word boundaries, a connective can't be confirmed as a whole
        # word -- so no connective split rather than a guessed one.
        spans = rs.rule_split_spans(RUN_ON, "zh", 32, None)
        assert spans == [(0, len(RUN_ON))]

    def test_japanese_space_marks_a_clause_break(self):
        text = "今日はとても天気が良かったので公園まで歩いて行きました 帰りに友達と会ってカフェでお茶を飲みました"
        spans = rs.rule_split_spans(text, "ja", rs.max_line_chars("ja"), None)
        assert [text[s:e].strip() for s, e in spans] == [
            "今日はとても天気が良かったので公園まで歩いて行きました",
            "帰りに友達と会ってカフェでお茶を飲みました"]

    def test_korean_spaces_are_word_breaks_not_clause_breaks(self):
        text = "오늘은 날씨가 정말 좋아서 우리는 공원에 갔어요 그리고 저녁에는 친구들과 함께 맛있는 음식을 먹었어요"
        bounds = rs.word_boundaries(text, "ko")  # space-delimited: no kiwipiepy needed
        spans = rs.rule_split_spans(text, "ko", rs.max_line_chars("ko"), bounds)
        pieces = [text[s:e].strip() for s, e in spans]
        assert pieces[0] == "오늘은 날씨가 정말 좋아서 우리는 공원에 갔어요"
        assert pieces[1].startswith("그리고")

    def test_input_lines_are_never_modified(self):
        pytest.importorskip("jieba")
        ln = Line(idx=3, start=10.0, end=20.0, zh=RUN_ON, en="old", id=11)
        rs.resegment_lines([ln], "zh")
        assert (ln.idx, ln.zh, ln.en, ln.id, ln.start, ln.end) == (3, RUN_ON, "old", 11, 10.0, 20.0)


class TestLlmPass:
    LONG = "我" * 20 + "你" * 20  # no rule can split this

    def test_llm_breaks_are_mapped_onto_the_original_text(self):
        engine = ScriptedEngine([_marked("我" * 20, "你" * 20)])
        ln = Line(idx=0, start=0.0, end=8.0, zh=self.LONG)
        new_lines, changed = rs.resegment_lines([ln], "zh", engine=engine,
                                                boundaries_fn=_every_char_bounds)
        assert [l.zh for l in new_lines] == ["我" * 20, "你" * 20]
        assert len(changed) == 1

    def test_a_small_change_in_the_answer_never_reaches_the_result(self):
        # The model "corrected" one character; the break is still usable, but
        # the pieces must be the ORIGINAL characters, not the model's.
        engine = ScriptedEngine([_marked("我" * 20, "您" + "你" * 19)])
        spans = rs.llm_split_spans(self.LONG, engine, "zh", 32)
        assert [self.LONG[s:e] for s, e in spans] == ["我" * 20, "你" * 20]

    def test_reworded_answer_is_rejected_and_retried(self):
        reworded = _marked("完全不同的内容" * 3, "另一段改写过的文字" * 2)
        engine = ScriptedEngine([reworded, _marked("我" * 20, "你" * 20)])
        spans = rs.llm_split_spans(self.LONG, engine, "zh", 32)
        assert len(engine.prompts) == 2
        assert [self.LONG[s:e] for s, e in spans] == ["我" * 20, "你" * 20]

    def test_gives_up_after_three_bad_answers_and_keeps_the_line_whole(self):
        bad = _marked("完全不同的内容" * 3, "另一段改写过的文字" * 2)
        engine = ScriptedEngine([bad, "not json", json.dumps({"text": self.LONG})])
        ln = Line(idx=0, start=0.0, end=8.0, zh=self.LONG, en="kept", id=4)
        new_lines, changed = rs.resegment_lines([ln], "zh", engine=engine,
                                                boundaries_fn=_every_char_bounds)
        assert len(engine.prompts) == rs.LLM_MAX_ATTEMPTS
        assert changed == []
        assert [(l.zh, l.en, l.id) for l in new_lines] == [(self.LONG, "kept", 4)]

    def test_a_break_inside_a_word_is_rejected(self):
        text = "我" * 20 + "你" * 20
        bounds = set(range(len(text) + 1)) - {20}  # 20 falls inside a "word"
        assert rs.match_llm_breaks(text, "[br]".join(["我" * 20, "你" * 20]), bounds) is None

    def test_llm_is_only_asked_about_lines_the_rules_could_not_split(self):
        pytest.importorskip("jieba")
        engine = ScriptedEngine([])
        rs.resegment_lines([Line(idx=0, start=0, end=9, zh=RUN_ON),
                            Line(idx=1, start=9, end=10, zh="短句。")], "zh", engine=engine)
        assert engine.prompts == []

    def test_prompt_asks_for_break_markers_only(self):
        prompt = rs._llm_prompt(self.LONG, "zh", 32)
        assert "[br]" in prompt
        assert "Do NOT change" in prompt


class TestTiming:
    def test_timing_is_continuous_across_a_split(self):
        pytest.importorskip("jieba")
        ln = Line(idx=0, start=10.0, end=20.0, zh=RUN_ON)
        new_lines, _ = rs.resegment_lines([ln], "zh")
        assert new_lines[0].start == 10.0
        assert new_lines[-1].end == 20.0
        for a, b in zip(new_lines, new_lines[1:]):
            assert a.end == b.start
            assert a.start < a.end

    def test_without_segments_time_is_split_by_length(self):
        cuts = rs.split_times(Line(idx=0, start=0.0, end=10.0, zh="x"), ["一二三", "四五六七八九十"])
        assert cuts == [pytest.approx(3.0)]

    def test_uses_the_stored_transcription_segments_when_they_line_up(self):
        # The speaker says the first half quickly (10-12s) and the second
        # slowly (12-20s): length-proportional timing would put the cut near
        # 14.8s, the real alignment puts it at 12s.
        ln = Line(idx=0, start=10.0, end=20.0, zh=RUN_ON)
        segments = [{"start": 10.0, "end": 12.0, "text": RUN_ON_A},
                    {"start": 12.0, "end": 20.0, "text": RUN_ON_B}]
        cuts = rs.split_times(ln, [RUN_ON_A, RUN_ON_B], segments)
        assert cuts == [pytest.approx(12.0, abs=0.1)]

    def test_unusable_segments_fall_back_to_length(self):
        ln = Line(idx=0, start=10.0, end=20.0, zh=RUN_ON)
        segments = [{"start": 30.0, "end": 40.0, "text": "别的地方"}]  # doesn't overlap the line
        cuts = rs.split_times(ln, ["一二三", "四五六七八九十"], segments)
        assert cuts == [pytest.approx(13.0)]


class TestSavingOnlyClearsChangedLines:
    """The guardrail's data half: after re-segmenting a drama that's
    already translated, only the lines whose boundaries changed lose their
    translation, flag, notes and emotion tag. Everything else keeps its
    permanent id and all of its data."""

    def test_untouched_lines_keep_translation_notes_and_flags(self, isolated_db):
        pytest.importorskip("jieba")
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好。", en="Hello.", flag="needs_review",
                 flag_note="check"),
            Line(idx=1, start=2.0, end=12.0, zh=RUN_ON, en="A long run-on translation.",
                 flag="mistranslation", flag_note="wrong", speaker="A"),
            Line(idx=2, start=12.0, end=14.0, zh="再见。", en="Bye."),
        ])
        isolated_db.save_translation_notes(did, [
            {"line_idx": 0, "term": "你好", "note_type": "cultural", "note": "greeting"},
            {"line_idx": 1, "term": "市场", "note_type": "cultural", "note": "market"},
        ])
        isolated_db.save_emotions(did, {1: {"emotion": "sad", "intensity": 0.7, "note": ""}})
        before = isolated_db.load_line_objects(did)
        kept_ids = (before[0].id, before[2].id)

        new_lines, changed = rs.resegment_lines(before, "zh")
        assert [ln.id for ln, _ in changed] == [before[1].id]
        isolated_db.save_lines(did, new_lines)

        after = isolated_db.load_line_objects(did)
        assert [l.zh for l in after] == ["你好。", RUN_ON_A, RUN_ON_B, "再见。"]
        assert (after[0].id, after[3].id) == kept_ids
        assert (after[0].en, after[0].flag, after[0].flag_note) == ("Hello.", "needs_review", "check")
        assert after[3].en == "Bye."
        for piece in after[1:3]:
            assert (piece.en, piece.flag) == ("", None)
            assert piece.speaker == "A"
            assert piece.id not in kept_ids and piece.id != before[1].id
        notes = isolated_db.list_translation_notes(did)
        assert [(n["term"], n["line_idx"]) for n in notes] == [("你好", 0)]
        assert isolated_db.load_emotions(did) == {}


class TestResegmentSubprocessWorker:
    """Step 4e: resegment_subprocess_worker() is the entry point
    background_jobs.start_process_job() runs in its own OS process for
    the local-Ollama LLM pass, so a real mid-run Cancel can terminate it.
    Confirmed the lowest-risk of the three Step 4e cases to hard-stop:
    resegment_lines is a pure in-memory computation with no DB/file write
    of its own -- its result only ever reaches the caller as a preview.
    Tested here as a plain function call against the same fakes used
    elsewhere in this file -- background_jobs.py's own tests cover the
    actual multiprocessing.Process/cancel machinery."""

    def test_matches_a_direct_resegment_lines_call_on_success(self):
        # resegment_subprocess_worker doesn't expose boundaries_fn (the
        # real UI call site never overrides it either) -- so both calls
        # here go through the real word_boundaries(), which needs jieba.
        pytest.importorskip("jieba")
        ln = Line(idx=0, start=0.0, end=8.0, zh=TestLlmPass.LONG, id=1)

        direct_new_lines, direct_changed = rs.resegment_lines(
            [ln], "zh", engine=ScriptedEngine([_marked("我" * 20, "你" * 20)]))

        result_queue = queue.Queue()
        rs.resegment_subprocess_worker(
            [ln], "zh", ScriptedEngine([_marked("我" * 20, "你" * 20)]), None, "simplified",
            result_queue)
        outcome = result_queue.get_nowait()

        assert outcome[0] == "ok"
        assert [l.zh for l in outcome[1]["lines"]] == [l.zh for l in direct_new_lines]
        assert outcome[1]["changed"] == [(c.id, c.idx, c.zh, pieces) for c, pieces in direct_changed]

    def test_usage_calls_are_collected_for_the_caller_to_log(self):
        class FakeUsage:
            input_tokens = 10
            output_tokens = 5

        class FakeResponse:
            usage = FakeUsage()
            content = [_Block(_marked("我" * 20, "你" * 20))]

        class FakeClaudeLike:
            model = "fake-claude"
            supports_reference = True

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, model, max_tokens, messages):
                return FakeResponse()

        ln = Line(idx=0, start=0.0, end=8.0, zh=TestLlmPass.LONG)
        result_queue = queue.Queue()
        rs.resegment_subprocess_worker(
            [ln], "zh", FakeClaudeLike(), None, "simplified", result_queue)
        outcome = result_queue.get_nowait()

        assert outcome[0] == "ok"
        assert outcome[1]["usage_calls"] == [(10, 5)]

    def test_reports_an_exception_instead_of_raising(self):
        def _boom(*a, **k):
            raise RuntimeError("resegment exploded")

        ln = Line(idx=0, start=0.0, end=8.0, zh=TestLlmPass.LONG)
        result_queue = queue.Queue()
        import unittest.mock
        with unittest.mock.patch.object(rs, "resegment_lines", _boom):
            rs.resegment_subprocess_worker([ln], "zh", None, None, "simplified", result_queue)
        outcome = result_queue.get_nowait()

        assert outcome == ("error", "RuntimeError", "resegment exploded")
