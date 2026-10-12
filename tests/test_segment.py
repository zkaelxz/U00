"""
tests/test_segment.py -- segment.py's Traditional Chinese support.

jieba's dictionary is built for Simplified Chinese and segments Traditional
text poorly. segment_zh(chinese_script="traditional") converts to
Simplified with OpenCC just to find word boundaries, then slices the
ORIGINAL Traditional text using those lengths, so the returned words are
always in the source script -- these tests run against the real jieba/
OpenCC (both installed in this environment), not mocks, since the whole
point is verifying the actual segmentation/slicing logic works, not just
that it's wired up.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
pytest.importorskip("jieba")  # requirements-optional.txt, not core --
                               # skip cleanly without it rather than error
                               # when segment.py lazily imports it

import segment


class TestSegmentZhTraditional:
    def test_traditional_text_segments_into_traditional_words(self):
        # Generic sentence, "We're going to the library to read today" --
        # not sourced from any copyrighted work.
        text = "我們今天要去圖書館看書"
        result = segment.segment_zh(text, chinese_script="traditional")
        words = [w for w, _reading in result]
        assert "".join(words) == text  # every character accounted for, in order
        assert "我們" in words   # Traditional form, not simplified "我们"
        assert "圖書館" in words  # Traditional form, not simplified "图书馆"

    def test_traditional_and_simplified_of_same_sentence_segment_the_same_way(self):
        traditional = "我們今天要去圖書館看書"
        simplified = "我们今天要去图书馆看书"
        trad_words = [w for w, _r in segment.segment_zh(traditional, chinese_script="traditional")]
        simp_words = [w for w, _r in segment.segment_zh(simplified, chinese_script="simplified")]
        assert len(trad_words) == len(simp_words)
        assert [len(w) for w in trad_words] == [len(w) for w in simp_words]

    def test_pinyin_is_returned_for_traditional_words(self):
        result = segment.segment_zh("你好", chinese_script="traditional")
        words_with_readings = {w: r for w, r in result}
        assert words_with_readings.get("你好") is not None

    def test_default_chinese_script_is_simplified(self):
        # No chinese_script arg at all -- must not raise, must behave like
        # the pre-existing simplified-only behavior.
        result = segment.segment_zh("你好")
        assert result[0][0] == "你好" or "".join(w for w, _ in result) == "你好"

    def test_empty_string_does_not_crash(self):
        assert segment.segment_zh("", chinese_script="traditional") == []


class TestSegmentAndAnnotateThreadsChineseScript:
    def test_chinese_script_param_reaches_segment_zh(self):
        result = segment.segment_and_annotate("我們去了圖書館", "zh", chinese_script="traditional")
        words = [w for w, _r in result]
        assert "".join(words) == "我們去了圖書館"

    def test_japanese_ignores_chinese_script_param(self, monkeypatch):
        # Must not raise just because chinese_script was passed for a
        # non-Chinese language -- it's simply irrelevant there. Mocked
        # rather than requiring the real sudachipy/pykakasi install --
        # this is testing segment_and_annotate's routing, not segment_ja
        # itself (which has no chinese_script param to receive it anyway).
        monkeypatch.setattr(segment, "segment_ja", lambda text: [(text, None)])
        result = segment.segment_and_annotate("こんにちは", "ja", chinese_script="traditional")
        assert result == [("こんにちは", None)]

    def test_korean_ignores_chinese_script_param(self, monkeypatch):
        monkeypatch.setattr(segment, "segment_ko", lambda text: [(text, None)])
        result = segment.segment_and_annotate("안녕하세요", "ko", chinese_script="traditional")
        assert result == [("안녕하세요", None)]


class TestVeryLongLine:
    def test_a_long_unbroken_line_segments_in_linear_time_and_keeps_its_text(self):
        import time
        text = "很" * 200_000
        start = time.monotonic()
        words = [w for w, _ in segment.segment_zh(text)]
        # jieba alone needs ~minutes on this input; 30 s only fails a quadratic walk.
        assert time.monotonic() - start < 30
        assert "".join(words) == text

    def test_chunking_prefers_sentence_ends_so_words_stay_whole(self):
        text = ("我们今天要去图书馆看书。" * 400)
        chunks = segment.split_long_text(text, limit=500)
        assert "".join(chunks) == text
        assert all(c.endswith("。") for c in chunks)


class _FakeBudoux:
    """Stands in for the budoux module: parse() returns fixed chunks."""

    def __init__(self, chunks):
        self._chunks = chunks

    def load_default_japanese_parser(self):
        return self

    def parse(self, _text):
        return self._chunks


class TestLineBreakOffsets:
    @pytest.fixture(autouse=True)
    def _fresh_parser(self, monkeypatch):
        monkeypatch.setattr(segment, "_budoux_ja", None)

    def test_japanese_offsets_are_budoux_chunk_ends_plus_sudachi_words(self, monkeypatch):
        text = "今日はとても天気がいい"
        monkeypatch.setitem(sys.modules, "budoux", _FakeBudoux(["今日は", "とても", "天気が", "いい"]))
        monkeypatch.setattr(segment, "segment_words", lambda t, lang, script="simplified": ["今日", "は", "とても天気", "が", "いい"])
        tiers = segment.line_break_tiers(text, "ja")
        assert tiers[0] == {3, 6, 9}  # BudouX first; the end of the text is not a break
        assert segment.line_break_offsets(text, "ja") == {2, 3, 6, 8, 9}

    def test_offsets_before_punctuation_and_small_kana_are_dropped(self, monkeypatch):
        text = "行ってきます,いいよ。そうょ"
        monkeypatch.setitem(sys.modules, "budoux", _FakeBudoux(["行って", "きます", ",", "いいよ", "。", "そう", "ょ"]))
        monkeypatch.setattr(segment, "segment_words", lambda *a, **k: [text])
        offsets = segment.line_break_offsets(text, "ja")
        assert offsets == {3, 7, 11}
        assert not any(text[o] in "。,ょ" for o in offsets)

    def test_japanese_negation_endings_do_not_start_a_line(self, monkeypatch):
        text = "食べないそれなら"
        monkeypatch.setitem(sys.modules, "budoux", _FakeBudoux([text]))
        monkeypatch.setattr(segment, "segment_words", lambda *a, **k: ["食べ", "ない", "それ", "なら"])
        assert segment.line_break_offsets(text, "ja") == {4}

    def test_budoux_missing_falls_back_to_sudachi(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "budoux", None)  # import raises ImportError
        monkeypatch.setattr(segment, "segment_words", lambda *a, **k: ["今日", "は"])
        assert segment.line_break_offsets("今日は", "ja") == {2}

    def test_no_segmenter_installed_is_none(self, monkeypatch):
        def missing(*_a, **_k):
            raise ImportError
        monkeypatch.setitem(sys.modules, "budoux", None)
        monkeypatch.setattr(segment, "segment_words", missing)
        assert segment.line_break_offsets("今日は", "ja") is None
        assert segment.line_break_offsets("今天", "zh") is None

    def test_chunks_that_do_not_reassemble_are_ignored(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "budoux", _FakeBudoux(["違う"]))
        monkeypatch.setattr(segment, "segment_words", lambda *a, **k: ["今日", "は"])
        assert segment.line_break_tiers("今日は", "ja") == [{2}]

    def test_korean_is_none(self):
        assert segment.line_break_offsets("안녕하세요 여러분", "ko") is None

    def test_chinese_uses_word_boundaries_with_the_script(self, monkeypatch):
        seen = []

        def words(text, language, script="simplified"):
            seen.append(script)
            return ["我們", "今天", "去"]
        monkeypatch.setattr(segment, "segment_words", words)
        assert segment.line_break_offsets("我們今天去", "zh", "traditional") == {2, 4}
        assert seen == ["traditional"]
