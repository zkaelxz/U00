"""Tests for segment timing: word timestamps, splitting long segments, sane times and fitting line edges to the detected speech."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import whisper_transcribe
import whisper_models
import segment_splitting
from segment_splitting import WORD_EDGE_PAD_END, WORD_EDGE_PAD_START



class TestTightenToWords:
    class W:
        def __init__(self, start, end):
            self.start, self.end = start, end

    def test_narrows_to_the_first_and_last_spoken_word(self):
        from segment_splitting import tighten_to_words
        words = [self.W(3.2, 3.6), self.W(3.6, 4.1), self.W(4.1, 4.4)]
        start, end = tighten_to_words(1.0, 8.0, words)
        assert (start, end) == (pytest.approx(3.2 - WORD_EDGE_PAD_START),
                                pytest.approx(4.4 + WORD_EDGE_PAD_END))

    def test_pad_stops_at_the_segment_edges(self):
        from segment_splitting import tighten_to_words
        words = [self.W(1.05, 1.5), self.W(7.9, 7.95)]
        assert tighten_to_words(1.0, 8.0, words) == (1.0, 8.0)

    def test_overlapping_pads_meet_in_the_middle(self):
        from segment_splitting import settle_overlaps
        segs = [{"start": 0.0, "end": 2.1, "text": "a"}, {"start": 1.9, "end": 4.0, "text": "b"}]
        settle_overlaps(segs)
        assert segs[0]["end"] == segs[1]["start"] == 2.0

    def test_never_widens_the_segment(self):
        from segment_splitting import tighten_to_words
        words = [self.W(0.5, 1.0), self.W(1.0, 9.0)]
        assert tighten_to_words(1.0, 8.0, words) == (1.0, 8.0)

    def test_keeps_the_segment_times_without_usable_words(self):
        from segment_splitting import tighten_to_words
        for words in (None, [], [object()], [self.W(2.0, 2.0)], "not words"):
            assert tighten_to_words(1.0, 8.0, words) == (1.0, 8.0)

    def test_transcribe_uses_word_times_and_asks_whisper_for_them(self):
        import sys, types
        import whisper_transcribe

        seen = {}

        class Seg:
            start, end, text = 0.0, 10.0, " hi "
            words = [self.W(4.0, 4.5), self.W(4.5, 5.0)]

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([Seg()]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: Model()
        sys.modules["faster_whisper"] = fake_fw
        whisper_models._whisper_model_cache.clear()

        assert whisper_transcribe.transcribe_for_timing("/fake/audio.mp3") == [
            {"start": pytest.approx(4.0 - WORD_EDGE_PAD_START),
             "end": pytest.approx(5.0 + WORD_EDGE_PAD_END), "text": "hi"}]
        assert seen["word_timestamps"] is True


class TestSplitLongSegments:
    @staticmethod
    def _seg(text, start=10.0, end=40.0, **extra):
        return {"start": start, "end": end, "text": text, **extra}

    @staticmethod
    def _check(seg, pieces):
        assert pieces[0]["start"] == seg["start"] and pieces[-1]["end"] == seg["end"]
        for a, b in zip(pieces, pieces[1:]):
            assert a["end"] == b["start"]
        assert all(p["start"] < p["end"] for p in pieces)
        assert "".join("".join(p["text"].split()) for p in pieces) == "".join(seg["text"].split())

    def test_splits_at_sentence_ends_and_keeps_text(self):
        seg = self._seg("好,你刚讲不要讲。哇,我先离开一下。好,OK。重来。哇,大家好哦!" * 3,
                        speaker="A")
        out = segment_splitting.split_long_segments([seg], max_seconds=8, max_cjk_chars=1000)
        assert len(out) > 3
        self._check(seg, out)
        assert all(p["speaker"] == "A" for p in out)
        assert all(p["end"] - p["start"] <= 8.0 + 1e-6 for p in out)
        assert all(p["text"][-1] in "。!" for p in out)

    def test_comma_fallback_for_one_long_sentence(self):
        seg = self._seg(",".join(["一二三四五六七八九十"] * 8) + "。", 0.0, 30.0)
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert len(out) > 1
        self._check(seg, out)

    def test_proportional_cuts_are_flagged_timing_uncertain(self):
        seg = self._seg(",".join(["一二三四五六七八九十"] * 8) + "。", 0.0, 30.0)
        out = segment_splitting.split_long_segments([seg], max_seconds=8, flag_estimated=True)
        assert len(out) > 1
        assert {p["flag"] for p in out} == {"timing_uncertain"}
        assert {p["flag_note"] for p in out} == {segment_splitting.PROPORTIONAL_SPLIT_NOTE}

    def test_proportional_cuts_keep_a_flag_the_segment_already_has(self):
        seg = {**self._seg(",".join(["一二三四五六七八九十"] * 8) + "。", 0.0, 30.0),
               "flag": "uncertain_translation", "flag_note": "check names"}
        out = segment_splitting.split_long_segments([seg], max_seconds=8, flag_estimated=True)
        assert len(out) > 1
        assert {(p["flag"], p["flag_note"]) for p in out} == {("uncertain_translation",
                                                               "check names")}

    def test_proportional_cuts_are_unflagged_unless_asked(self):
        seg = self._seg(",".join(["一二三四五六七八九十"] * 8) + "。", 0.0, 30.0)
        assert all("flag" not in p for p in segment_splitting.split_long_segments([seg], max_seconds=8))

    def test_no_punctuation_left_alone(self):
        seg = self._seg("一二三四五六七八九十" * 10)
        assert segment_splitting.split_long_segments([seg]) == [seg]

    def test_cjk_spaces_are_cut_points_when_no_punctuation(self):
        seg = self._seg(" ".join(["我們記得昨天早的時候呢"] * 6), 0.0, 26.0)
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert len(out) > 1
        self._check(seg, out)
        assert all(p["end"] - p["start"] <= 8.0 + 1e-6 for p in out)

    def test_space_fallback_respects_char_limit(self):
        seg = self._seg(" ".join(["一二三四五六七八九十"] * 7), 0.0, 6.0)
        out = segment_splitting.split_long_segments([seg], max_seconds=8, max_cjk_chars=40)
        assert len(out) > 1
        self._check(seg, out)
        assert all(len(segment_splitting._CJK_RE.findall(p["text"])) <= 40 for p in out)

    def test_space_fallback_keeps_latin_tokens_whole(self):
        seg = self._seg("這是一個很長的句子呢 Dormi Q&A 絕不NG的表單 " * 4, 0.0, 30.0)
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert len(out) > 1
        self._check(seg, out)
        for p in out:
            assert not p["text"].startswith(("Q&A", "A ")) and not p["text"].endswith(("Dormi", "Q&A "[:3]))
            assert "Dormi Q&A" in p["text"] or "Dormi" not in p["text"]

    def test_punctuated_line_not_cut_at_spaces(self):
        seg = self._seg("一二三 四五六。七八九 十一二。", 0.0, 12.0)
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert [p["text"] for p in out] == ["一二三 四五六。", "七八九 十一二。"]

    def test_spaceless_unpunctuated_line_still_whole(self):
        seg = self._seg("一二三四五六七八九十" * 10, 0.0, 30.0)
        assert segment_splitting.split_long_segments([seg]) == [seg]

    def test_short_line_untouched(self):
        seg = self._seg("好。你好。再见。", 0.0, 3.0)
        assert segment_splitting.split_long_segments([seg]) == [seg]

    def test_mixed_cjk_latin_japanese_korean(self):
        for text in ("今日はいい天気ですね。Let's go to the park. Really? 行きましょう!" * 2,
                     "안녕하세요. 오늘은 날씨가 좋네요? Okay, let's go. 갑시다!" * 2):
            seg = self._seg(text, 5.0, 25.0)
            out = segment_splitting.split_long_segments([seg], max_seconds=8)
            assert len(out) > 1
            self._check(seg, out)

    def test_decimal_point_is_not_a_sentence_end(self):
        seg = self._seg("Pi is 3.14159 and e is 2.71828 which is nice", 0.0, 30.0)
        assert segment_splitting.split_long_segments([seg], max_seconds=8) == [seg]


class TestWordTimestampsKept:
    """Whisper's word timings ride on the segments so split_long_segments can cut
    an unpunctuated line at a real pause."""

    @staticmethod
    def _words(text, step=0.25, pauses=None):
        """One word per character: {start, end, word}, `step` seconds each, with the
        extra silences in `pauses` ({char index: seconds}) inserted before a char."""
        out, t = [], 0.0
        for i, ch in enumerate(text):
            t += (pauses or {}).get(i, 0.0)
            out.append({"start": t, "end": t + step * 0.9, "word": ch})
            t += step
        return out

    @classmethod
    def _seg(cls, text, pauses=None, **extra):
        words = cls._words(text, pauses=pauses)
        return {"start": words[0]["start"], "end": words[-1]["end"], "text": text,
                "words": words, **extra}

    def test_transcribe_keeps_the_words_as_plain_dicts(self):
        import sys, types
        import whisper_transcribe

        class Word:
            def __init__(self, start, end, word):
                self.start, self.end, self.word, self.probability = start, end, word, 0.9

        class Seg:
            start, end, text = 0.0, 2.0, " 你好 "
            words = [Word(0.2, 0.6, "你"), Word(0.6, 1.0, "好")]

        class Model:
            def transcribe(self, audio_path, **kwargs):
                return iter([Seg()]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: Model()
        sys.modules["faster_whisper"] = fake_fw
        whisper_models._whisper_model_cache.clear()
        assert whisper_transcribe.transcribe_for_timing("/fake/audio.mp3") == [
            {"start": pytest.approx(0.2 - WORD_EDGE_PAD_START),
             "end": pytest.approx(1.0 + WORD_EDGE_PAD_END), "text": "你好",
             "words": [{"start": 0.2, "end": 0.6, "word": "你", "probability": 0.9},
                       {"start": 0.6, "end": 1.0, "word": "好", "probability": 0.9}]}]

    def test_fast_mode_gets_the_same_word_timestamps_kwarg(self):
        import sys, types
        import whisper_transcribe
        seen = {}

        class Seg:
            start, end, text, words = 0.0, 1.0, "hi", []

        class Pipeline:
            def __init__(self, model):
                pass

            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([Seg()]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: object()
        fake_fw.BatchedInferencePipeline = Pipeline
        sys.modules["faster_whisper"] = fake_fw
        whisper_models._whisper_model_cache.clear()
        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", fast_mode=True)
        assert seen["word_timestamps"] is True

    def test_a_line_with_no_punctuation_is_cut_at_the_real_pauses(self):
        text = "我今天去了公园然后看到很多人在那边跳舞我也跟着跳了一会儿觉得很开心" * 2
        # 66 chars at 0.25 s = 16.5 s, plus 1.6 s of real silences after chars 22 and 44
        seg = self._seg(text, pauses={22: 0.9, 44: 0.7})
        assert seg["end"] - seg["start"] > 17
        out = segment_splitting.split_long_segments([seg])
        assert [p["text"] for p in out] == [text[:22], text[22:44], text[44:]]
        assert "".join(p["text"] for p in out) == text
        words = seg["words"]
        pad_s, pad_e = segment_splitting.WORD_EDGE_PAD_START, segment_splitting.WORD_EDGE_PAD_END
        expected = [(max(words[0]["start"] - pad_s, seg["start"]), words[21]["end"] + pad_e),
                    (words[22]["start"] - pad_s, words[43]["end"] + pad_e),
                    (words[44]["start"] - pad_s, min(words[-1]["end"] + pad_e, seg["end"]))]
        assert [t for p in out for t in (p["start"], p["end"])] == pytest.approx(
            [t for pair in expected for t in pair])
        assert [p["words"] for p in out] == [words[:22], words[22:44], words[44:]]

    def test_a_17_second_unpunctuated_clip_with_four_pauses_is_cut(self):
        # about 17 s and 56 characters: 56 * 0.25 s = 14 s of speech plus 3 s of pauses
        text = "我今天去了公园然后看到很多人在那边跳舞我也跟着跳了一会儿觉得很开心真的太好玩了下次还想再来而且天气特别好大家都很高兴一直玩到天黑才回家"[:56]
        assert len(text) == 56
        seg = self._seg(text, pauses={14: 0.75, 28: 1.0, 42: 0.75, 49: 0.5})
        assert 16.5 < seg["end"] - seg["start"] < 17.5
        out = segment_splitting.split_long_segments([seg])
        assert len(out) >= 2 and "".join(p["text"] for p in out) == text
        assert all(p["end"] - p["start"] <= segment_splitting.SPLIT_MAX_SECONDS for p in out)

    def test_cuts_happen_only_while_pieces_are_still_too_long(self):
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={10: 0.5, 20: 0.9, 30: 0.5})
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert [p["text"] for p in out] == [text[:20], text[20:]]

    def test_pieces_near_the_middle_win_among_similar_pauses(self):
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={5: 0.9, 20: 0.8, 35: 0.9})
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert out[0]["text"] == text[:20]

    def test_punctuation_cuts_use_the_word_times(self):
        text = "我们先去吃饭。然后再去看电影吧。"
        seg = self._seg(text, pauses={7: 1.5})
        out = segment_splitting.split_long_segments([seg], max_seconds=3)
        assert [p["text"] for p in out] == ["我们先去吃饭。", "然后再去看电影吧。"]
        assert out[1]["start"] == pytest.approx(seg["words"][7]["start"] - segment_splitting.WORD_EDGE_PAD_START)
        assert out[0]["end"] == pytest.approx(seg["words"][6]["end"] + segment_splitting.WORD_EDGE_PAD_END)
        assert out[0]["end"] < out[1]["start"] - 1.1

    def test_mixed_latin_and_cjk_text_maps_exactly(self):
        words = [("我们", 0.0, 1.0), (" 用", 1.0, 1.5), (" Python", 1.5, 3.0), (" 写", 3.0, 4.0),
                 ("程序", 4.0, 5.5), (" 然后", 7.0, 8.5), (" 发布", 8.5, 10.0),
                 (" 到", 10.0, 11.0), (" GitHub", 11.0, 12.5)]
        text = "我们 用 Python 写程序 然后 发布 到 GitHub"
        seg = {"start": 0.0, "end": 12.5, "text": text,
               "words": [{"start": s, "end": e, "word": w} for w, s, e in words]}
        out = segment_splitting.split_long_segments([seg], max_seconds=8)
        assert [p["text"] for p in out] == ["我们 用 Python 写程序", "然后 发布 到 GitHub"]
        assert (out[0]["end"], out[1]["start"]) == pytest.approx((5.7, 6.9))

    def test_words_that_do_not_spell_the_text_leave_the_line_alone(self):
        text = "我今天去了公园然后看到很多人在那边跳舞我也跟着跳了一会儿觉得很开心" * 2
        seg = self._seg(text, pauses={22: 0.9, 44: 0.7})
        seg["words"][10]["word"] = "错"
        assert segment_splitting.split_long_segments([seg]) == [seg]
        seg["words"][10]["word"] = text[10]
        seg["text"] = text + "。"  # words no longer cover the text
        assert [p["text"] for p in segment_splitting.split_long_segments([seg])] == [seg["text"]]

    def test_a_punctuation_cut_inside_a_word_falls_back_to_the_estimate(self):
        text = "我们先去吃饭。然后再去看电影吧。" * 2
        seg = self._seg(text)
        seg["words"][6:8] = [{"start": seg["words"][6]["start"], "end": seg["words"][7]["end"],
                             "word": text[6:8]}]
        out = segment_splitting.split_long_segments([seg], max_seconds=3)
        assert len(out) > 1 and "words" not in out[0]
        assert out[0]["start"] == seg["start"] and out[-1]["end"] == seg["end"]

    def test_the_default_pause_is_035_within_its_bounds(self):
        assert segment_splitting.MIN_WORD_GAP_SECONDS == 0.35
        assert (segment_splitting.MIN_WORD_GAP_SECONDS_MIN, segment_splitting.MIN_WORD_GAP_SECONDS_MAX) == (0.1, 2.0)

    def test_the_pause_setting_decides_which_pauses_cut(self):
        text = "一二三四五六七八九十" * 7
        seg = self._seg(text, pauses={17: 0.30, 52: 0.50})

        def cuts(**kw):
            return [len(p["text"]) for p in segment_splitting.split_long_segments([seg], max_seconds=8, **kw)]
        assert cuts(min_pause=0.25) == [17, 35, 18]  # both pauses
        assert cuts(min_pause=0.35) == [52, 18]  # only the 0.50 s one
        assert cuts(min_pause=0.45) == [52, 18]
        assert cuts(min_pause=0.55) == [70]
        assert cuts() == [52, 18]  # the default is 0.35

    def test_pause_offsets_follow_the_setting(self):
        text = "一二三四五六七八九十" * 2
        index = segment_splitting._WordIndex.build(text, self._seg(text, pauses={5: 0.30, 12: 0.50})["words"])
        assert segment_splitting.pause_offsets(index, 0.25) == {5, 12}
        assert segment_splitting.pause_offsets(index, 0.4) == {12}
        assert segment_splitting.pause_offsets(index) == {12}

    def test_no_pause_long_enough_leaves_the_line_whole(self):
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={20: segment_splitting.MIN_WORD_GAP_SECONDS - 0.05})
        assert segment_splitting.split_long_segments([seg], max_seconds=8) == [seg]

    @staticmethod
    def _seg_with_exact_gap(text, gap):
        """Words that touch (end = next start) except `gap` before char 20; every
        time is a multiple of 1/16, exact in binary, so the gap is exact."""
        words, t = [], 0.0
        for i, ch in enumerate(text):
            t += gap if i == 20 else 0.0
            words.append({"start": t, "end": t + 0.25, "word": ch})
            t += 0.25
        return {"start": 0.0, "end": t, "text": text, "words": words}

    def test_pause_exactly_at_the_minimum_is_a_cut_point(self):
        text = "一二三四五六七八九十" * 4
        seg = self._seg_with_exact_gap(text, 0.25)  # a binary-exact gap, so >= is tested exactly
        assert len(segment_splitting.split_long_segments([seg], max_seconds=8, min_pause=0.25)) == 2

    def test_pause_just_under_the_minimum_is_not_a_cut_point(self):
        text = "一二三四五六七八九十" * 4
        seg = self._seg_with_exact_gap(text, 0.25 - 0.0625)
        assert segment_splitting.split_long_segments([seg], max_seconds=8, min_pause=0.25) == [seg]

    def test_floors_keep_a_stub_from_being_cut_off(self):
        text = "一二三四五六七八九十" * 4
        # the only long pauses leave 2 chars (0.5 s) on one side
        seg = self._seg(text, pauses={2: 1.0, 38: 1.0})
        assert segment_splitting.split_long_segments([seg], max_seconds=8) == [seg]

    def test_re_split_rules_apply_to_word_cuts(self):
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={10: 0.4, 25: 0.6})
        rules = segment_splitting.SplitRules(max_seconds=None, max_chars=25)
        out = segment_splitting.split_long_segments([seg], rules=rules)
        assert [p["text"] for p in out] == [text[:25], text[25:]]
        rules = segment_splitting.SplitRules(max_seconds=None, max_chars=12)
        out = segment_splitting.split_long_segments([seg], rules=rules)
        assert "".join(p["text"] for p in out) == text
        assert all(len(p["text"]) >= segment_splitting.MIN_PIECE_CJK_CHARS for p in out)

    def test_other_keys_are_copied_and_unused_words_stay_on_a_short_line(self):
        seg = self._seg("一二三四五", speaker="A")
        assert segment_splitting.split_long_segments([seg]) == [seg]
        long_seg = self._seg("一二三四五六七八九十" * 4, pauses={20: 0.9}, speaker="B")
        assert {p["speaker"] for p in segment_splitting.split_long_segments([long_seg], max_seconds=8)} == {"B"}

    def test_segments_without_words_behave_as_before(self):
        seg = {"start": 0.0, "end": 20.0, "text": "一二三四五六七八九十" * 10}
        assert segment_splitting.split_long_segments([seg]) == [seg]

    def test_hallucination_filter_and_coverage_ignore_the_extra_key(self):
        import whisper_hallucination
        from services.transcribe_service import coverage_warning
        segs = [self._seg("你好吗"), self._seg("你好吗"), self._seg("再见了")]
        assert whisper_hallucination.filter_hallucinated_segments(segs) == segs
        coverage_warning(segs, 100.0)

    def test_a_hundred_thousand_characters_is_not_quadratic(self):
        import time
        # 100k one-character words with a pause every 20 words, in one line
        text = "一二三四五六七八九十" * 10000
        pauses = {i: 0.5 for i in range(20, len(text), 20)}
        seg = self._seg(text, pauses=pauses)
        started = time.perf_counter()
        out = segment_splitting.split_long_segments([seg])
        elapsed = time.perf_counter() - started
        assert "".join(p["text"] for p in out) == text
        assert all(p["end"] - p["start"] <= 8.0 + 1e-6 for p in out)
        assert elapsed < 10.0

    def test_growing_pauses_do_not_peel_one_piece_at_a_time(self):
        import time
        # the biggest pause is always at the far end: a naive pick-the-largest scan is quadratic
        text = "一二三四五六七八九十" * 5000
        pauses = {i: 0.3 + i / 1e5 for i in range(10, len(text), 10)}
        seg = self._seg(text, pauses=pauses)
        started = time.perf_counter()
        out = segment_splitting.split_long_segments([seg])
        assert "".join(p["text"] for p in out) == text
        assert time.perf_counter() - started < 10.0

    def test_random_pauses_always_give_lossless_ordered_pieces(self):
        import random
        rng = random.Random(165)
        for _ in range(60):
            text = "".join(rng.choice("我你他是不了在有人这中大来上国个到说们为") for _ in range(rng.randint(30, 400)))
            pauses = {i: rng.choice([0.0, 0.0, 0.1, 0.3, 0.6, 1.2]) for i in range(1, len(text))}
            seg = self._seg(text, pauses=pauses)
            out = segment_splitting.split_long_segments([seg])
            assert "".join(p["text"] for p in out) == text
            assert all(a["end"] <= b["start"] for a, b in zip(out, out[1:]))
            assert all(p["start"] < p["end"] for p in out)
            assert sum(len(p["words"]) for p in out) == len(text)


class TestSanitizeSegmentTimes:
    def test_repairs_zero_negative_and_past_the_end_times(self):
        segs = [{"start": 1.0, "end": 1.0, "text": "a"}, {"start": 5.0, "end": 4.0, "text": "b"},
                {"start": 9.0, "end": 14.0, "text": "c"}, {"start": 12.0, "end": 13.0, "text": "tail"}]
        out = segment_splitting.sanitize_segment_times(segs, duration=10.0)
        assert [(s["start"], s["end"], s["text"]) for s in out] == [
            (1.0, 1.5, "a"), (5.0, 5.5, "b"), (9.0, 10.0, "c")]

    def test_an_out_of_order_segment_never_gets_a_negative_length(self):
        segs = [{"start": 10.0, "end": 12.0, "text": "a"}, {"start": 8.0, "end": 9.0, "text": "b"}]
        out = segment_splitting.settle_overlaps(segs)
        assert all(s["end"] >= s["start"] for s in out)


class TestExtendEndsToSpeech:
    def test_a_line_ending_inside_speech_grows_to_where_the_speech_stops(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 5.0, "end": 6.0, "text": "b"}]
        out = segment_splitting.extend_ends_to_speech(segs, [(1.0, 2.3), (5.0, 6.2)])
        assert [round(s["end"], 2) for s in out] == [2.3, 6.2]

    def test_growth_is_capped_and_never_passes_the_next_line(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "a"}, {"start": 1.2, "end": 3.0, "text": "b"}]
        out = segment_splitting.extend_ends_to_speech(segs, [(0.0, 9.0)])
        assert out[0]["end"] == 1.2 and out[1]["end"] == 3.5

    def test_a_line_ending_in_silence_keeps_its_end(self):
        segs = [{"start": 1.0, "end": 2.5, "text": "a"}]
        assert segment_splitting.extend_ends_to_speech(segs, [(1.0, 1.8)])[0]["end"] == 2.5

    @staticmethod
    def _levels(loud_until: float, quiet_from: float = None, quiet_s: float = 9.0, length: float = 4.0):
        """dB per 10 ms: -20 dB of speech, with -60 dB from quiet_from for quiet_s."""
        levels = [-20.0] * int(round(length / 0.01))
        if quiet_from is not None:
            for j in range(int(round(quiet_from / 0.01)), min(int(round((quiet_from + quiet_s) / 0.01)), len(levels))):
                levels[j] = -60.0
        return levels

    def test_growth_stops_where_the_audio_goes_quiet_inside_a_longer_span(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        out = segment_splitting.extend_ends_to_speech(segs, [(0.0, 9.0)], levels=self._levels(2.2, quiet_from=2.2))
        assert out[0]["end"] == pytest.approx(2.2)

    def test_a_dip_shorter_than_the_quiet_hold_does_not_stop_growth(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        levels = self._levels(2.2, quiet_from=2.2, quiet_s=segment_splitting.SPEECH_END_QUIET_S / 2)
        out = segment_splitting.extend_ends_to_speech(segs, [(1.0, 2.4)], levels=levels)
        assert out[0]["end"] == pytest.approx(2.4)

    def test_audio_that_never_goes_quiet_grows_as_without_levels(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 5.0, "end": 6.0, "text": "b"}]
        out = segment_splitting.extend_ends_to_speech(segs, [(1.0, 2.3), (5.0, 6.2)],
                                                      levels=self._levels(9.0, length=7.0))
        assert [round(s["end"], 2) for s in out] == [2.3, 6.2]

    def test_quiet_already_at_the_end_keeps_the_end(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        out = segment_splitting.extend_ends_to_speech(segs, [(1.0, 2.4)], levels=self._levels(1.9, quiet_from=1.95))
        assert out[0]["end"] == 2.0


def test_loudness_levels_follow_the_audio_in_ten_millisecond_steps():
    import numpy as np
    import vad_segments
    sr = 16000
    t = np.arange(sr) / sr
    audio = np.concatenate([0.5 * np.sin(2 * np.pi * 440 * t), np.zeros(sr)]).astype(np.float32)
    levels = vad_segments.levels_db(audio, sr)
    assert len(levels) == 200
    assert levels[50] == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=0.5)
    assert levels[150] < -100


def test_whisper_line_ends_stop_where_the_audio_goes_quiet(monkeypatch):
    import types
    import numpy as np
    import vad_segments
    sr = 16000
    audio = np.concatenate([0.3 * np.ones(int(2.2 * sr)), np.zeros(int(0.8 * sr)), 0.3 * np.ones(sr)]).astype(np.float32)
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(decode_audio=lambda path, sampling_rate: audio))
    monkeypatch.setattr(vad_segments, "speech_spans", lambda audio, sr, **kw: [vad_segments.Span(0.0, 4.0)])
    segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
    out = whisper_transcribe._fit_to_speech(segs, "audio.wav")
    assert out[0]["end"] == pytest.approx(2.2, abs=0.02)


class TestTrimStartsToSpeech:
    def test_a_line_starting_in_silence_starts_just_before_its_speech(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        out = segment_splitting.trim_starts_to_speech(segs, [(1.3, 2.0)])
        assert out[0]["start"] == pytest.approx(1.3 - WORD_EDGE_PAD_START)

    def test_the_trim_is_capped(self):
        segs = [{"start": 1.0, "end": 3.0, "text": "a"}]
        out = segment_splitting.trim_starts_to_speech(segs, [(2.5, 3.0)])
        assert out[0]["start"] == pytest.approx(1.0 + segment_splitting.SPEECH_START_MAX_TRIM)

    def test_a_line_starting_in_speech_or_holding_none_keeps_its_start(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 3.0, "end": 4.0, "text": "b"}]
        out = segment_splitting.trim_starts_to_speech(segs, [(0.5, 1.5), (4.5, 5.0)])
        assert [s["start"] for s in out] == [1.0, 3.0]

    def test_a_line_starting_in_speech_keeps_its_start_even_with_more_speech_after_a_pause(self):
        segs = [{"start": 1.0, "end": 3.0, "text": "a"}]
        assert segment_splitting.trim_starts_to_speech(segs, [(0.8, 1.2), (1.6, 3.0)])[0]["start"] == 1.0

    def test_speech_starting_within_the_pad_moves_nothing(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        assert segment_splitting.trim_starts_to_speech(segs, [(1.05, 2.0)])[0]["start"] == 1.0

    def test_trimming_twice_changes_nothing_more(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        once = segment_splitting.trim_starts_to_speech(segs, [(1.3, 2.0)])[0]["start"]
        assert segment_splitting.trim_starts_to_speech(segs, [(1.3, 2.0)])[0]["start"] == once


def test_whisper_lines_are_fitted_to_the_detected_speech_at_both_edges(monkeypatch):
    import types
    import vad_segments
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(decode_audio=lambda path, sampling_rate: [0.0]))
    monkeypatch.setattr(vad_segments, "speech_spans", lambda audio, sr, **kw: [
        vad_segments.Span(1.4, 2.3), vad_segments.Span(4.0, 5.0)])
    segs = [{"start": 1.0, "end": 2.0, "text": "a"}, {"start": 4.0, "end": 4.6, "text": "b"}]
    out = whisper_transcribe._fit_to_speech(segs, "audio.wav")
    assert [(round(s["start"], 2), round(s["end"], 2)) for s in out] == [(1.3, 2.3), (4.0, 5.0)]


def test_difficult_audio_extends_ends_but_keeps_starts(monkeypatch):
    import types
    import vad_segments
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(decode_audio=lambda path, sampling_rate: [0.0]))
    monkeypatch.setattr(vad_segments, "speech_spans", lambda audio, sr, **kw: [vad_segments.Span(1.4, 2.3)])
    segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
    out = whisper_transcribe._fit_to_speech(segs, "audio.wav", trim_starts=False)
    assert (out[0]["start"], round(out[0]["end"], 2)) == (1.0, 2.3)


def test_whisper_lines_keep_their_times_without_the_detector(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
    assert whisper_transcribe._fit_to_speech(segs, "audio.wav") == [{"start": 1.0, "end": 2.0, "text": "a"}]
