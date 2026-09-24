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
