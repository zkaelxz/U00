"""
tests/test_subtitle_parse.py -- the SRT/VTT/ASS/LRC parsers, encoding
detection, validation, bilingual split, time-overlap matching, sidecar naming,
language-from-characters and LRC export. Pure functions: no database.
"""

import pytest

import subtitle_formats
import subtitle_parse as sp
import subtitle_sidecar as sc
from core import Line


def _texts(parsed):
    return [(c.start, c.end, c.text) for c in parsed.cues]


class TestSrt:
    def test_bom_crlf_tags_and_multiline(self):
        data = ("﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>\r\n{\\an8}world\r\n\r\n"
                "2\r\n00:00:03,000 --> 00:00:04,000\r\nBye\r\n").encode("utf-8")
        parsed = sp.parse_subtitle(data, "a.srt")
        assert parsed.format == "srt" and parsed.encoding == "utf-8-sig"
        assert _texts(parsed) == [(1.0, 2.5, "Hello\nworld"), (3.0, 4.0, "Bye")]
        assert parsed.problems == []

    def test_dot_milliseconds_and_missing_index_are_accepted(self):
        parsed = sp.parse_subtitle(b"00:00:01.5 --> 00:00:02.25\nx\n", "a.srt")
        assert _texts(parsed) == [(1.5, 2.25, "x")]

    def test_unreadable_time_range_is_an_error_with_its_line(self):
        with pytest.raises(sp.SubtitleParseError, match="Line 2"):
            sp.parse_subtitle(b"1\n00:00:01,000 --> oops\nx\n", "a.srt")


class TestVtt:
    def test_cue_settings_notes_style_identifier_and_entities(self):
        data = (b"WEBVTT - title\n\nNOTE a comment\nover two lines\n\nSTYLE\n::cue { color: red }\n\n"
                b"intro\n00:01.000 --> 00:02.000 align:start position:10%\n<v Bob>Hi &amp; bye</v>\nline2\n\n"
                b"00:00:03.000 --> 00:00:04.000\nX\n")
        parsed = sp.parse_subtitle(data)
        assert parsed.format == "vtt"
        assert _texts(parsed) == [(1.0, 2.0, "Hi & bye\nline2"), (3.0, 4.0, "X")]

    def test_requires_the_header(self):
        with pytest.raises(sp.SubtitleParseError, match="WEBVTT"):
            sp.parse_vtt("00:01.000 --> 00:02.000\nx\n")


class TestAss:
    ASS = ("[Script Info]\nTitle: t\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}Hello, {\\b1}world\\Nline2\n"
           "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}\n"
           "Comment: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,not shown\n"
           "Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,soft\\nbreak\\hhere\n")

    def test_tags_stripped_breaks_and_drawings_and_comments_ignored(self):
        parsed = sp.parse_subtitle(self.ASS.encode("utf-8"))
        assert parsed.format == "ass"
        assert _texts(parsed) == [(1.0, 2.5, "Hello, world\nline2"), (7.0, 8.0, "soft break here")]

    def test_ssa_without_a_format_line_uses_the_default_layout(self):
        ssa = "[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hi, there\n"
        assert _texts(sp.parse_subtitle(ssa.encode(), "a.ssa")) == [(1.0, 2.0, "hi, there")]

    def test_format_line_without_text_column_is_refused(self):
        bad = "[Events]\nFormat: Start, End, Style\nDialogue: 0:00:01.00,0:00:02.00,x\n"
        with pytest.raises(sp.SubtitleParseError, match="Start, End and Text"):
            sp.parse_subtitle(bad.encode())


class TestLrc:
    def test_multiple_stamps_offset_word_stamps_and_ends(self):
        data = ("[ti:Song]\n[offset:+500]\n[00:01.00][00:10.00]a <00:01.50>b\n[00:03.00]\n"
                "[00:05.00]c\n").encode("utf-8")
        parsed = sp.parse_subtitle(data)
        assert parsed.format == "lrc"
        # +500 ms offset shows the line half a second earlier; an empty stamp ends the previous line;
        # the last line gets the default length.
        assert _texts(parsed) == [(0.5, 2.5, "a b"), (4.5, 9.5, "c"), (9.5, 9.5 + sp.LRC_LAST_LINE_SECONDS, "a b")]

    def test_two_languages_on_one_stamp_become_one_two_line_cue(self):
        parsed = sp.parse_subtitle(b"[00:01.00]hello\n[00:01.00]bonjour\n[00:03.00]\n")
        assert _texts(parsed) == [(1.0, 3.0, "hello\nbonjour")]

    def test_colon_fraction_and_long_minutes(self):
        assert _texts(sp.parse_subtitle(b"[75:02:50]x\n")) == [(4502.5, 4506.5, "x")]


class TestEncodings:
    def test_utf16_with_and_without_bom(self):
        text = "WEBVTT\n\n00:01.000 --> 00:02.000\n你好\n"
        assert sp.parse_subtitle(text.encode("utf-16")).encoding == "utf-16"
        plain = "1\n00:00:01,000 --> 00:00:02,000\nabc\n"
        parsed = sp.parse_subtitle(plain.encode("utf-16-le"))
        assert parsed.encoding == "utf-16-le" and parsed.cues[0].text == "abc"

    @pytest.mark.parametrize("language,codec,sample", [
        ("ja", "cp932", "こんにちは世界"), ("zh", "gb18030", "你好,世界"), ("ko", "cp949", "안녕하세요")])
    def test_legacy_cjk_is_read_with_the_titles_language_and_reported_as_a_guess(self, language, codec, sample):
        parsed = sp.parse_subtitle(f"[00:01.00]{sample}\n".encode(codec), language=language)
        assert parsed.encoding == codec and parsed.encoding_guessed
        assert parsed.cues[0].text == sample
        assert any(p.code == "encoding_guess" for p in parsed.problems)

    def test_forced_encoding_and_unknown_encoding(self):
        data = "[00:01.00]你好\n".encode("gb18030")
        assert sp.parse_subtitle(data, encoding="gb18030").cues[0].text == "你好"
        with pytest.raises(sp.SubtitleParseError, match="Unknown"):
            sp.parse_subtitle(data, encoding="nope")
        with pytest.raises(sp.SubtitleParseError, match="not valid"):
            sp.parse_subtitle(data, encoding="ascii")


class TestValidation:
    def test_overlap_order_zero_empty_and_absurd_are_reported_not_repaired(self):
        data = ("1\n00:00:05,000 --> 00:00:06,000\nlate\n\n"
                "2\n00:00:01,000 --> 00:00:03,000\nearly\n\n"
                "3\n00:00:02,000 --> 00:00:04,000\noverlaps\n\n"
                "4\n00:00:10,000 --> 00:00:10,000\nzero\n\n"
                "5\n00:00:11,000 --> 00:00:12,000\n\n\n"
                "6\n00:00:20,000 --> 00:40:00,000\nlong\n").encode()
        parsed = sp.parse_subtitle(data, "a.srt")
        codes = {p.code: p for p in parsed.problems}
        assert set(codes) == {"out_of_order", "overlap", "zero_length", "empty_text", "absurd_duration"}
        assert all(p.severity == "warning" for p in codes.values()) and not parsed.blocking
        assert "first at cue 2" in codes["out_of_order"].message
        # sorted by start, the empty cue dropped, times untouched
        assert [c.text for c in parsed.cues] == ["early", "overlaps", "late", "zero", "long"]
        assert parsed.cues[0].end == 3.0

    def test_negative_length_and_overlong_text_block_the_import(self):
        parsed = sp.parse_subtitle(b"1\n00:00:05,000 --> 00:00:04,000\nx\n", "a.srt")
        assert parsed.blocking and parsed.problems[0].code == "negative_length"
        long_text = "x" * (sp.MAX_CUE_TEXT_CHARS + 1)
        parsed = sp.parse_subtitle(f"1\n00:00:01,000 --> 00:00:02,000\n{long_text}\n".encode(), "a.srt")
        assert parsed.blocking and parsed.problems[0].code == "text_too_long"

    def test_limits_and_unusable_files(self):
        with pytest.raises(sp.SubtitleParseError, match="too large"):
            sp.parse_subtitle(b"x" * (sp.MAX_FILE_BYTES + 1))
        with pytest.raises(sp.SubtitleParseError, match="empty"):
            sp.parse_subtitle(b"  \n")
        with pytest.raises(sp.SubtitleParseError, match="look like"):
            sp.parse_subtitle(b"just words\n")
        rows = "".join(f"1\n00:00:00,000 --> 00:00:01,000\nx\n\n" for _ in range(sp.MAX_CUES + 1))
        with pytest.raises(sp.SubtitleParseError, match="cues"):
            sp.parse_subtitle(rows.encode(), "a.srt")

    def test_format_is_read_from_content_before_extension(self):
        assert sp.parse_subtitle(b"WEBVTT\n\n00:01.000 --> 00:02.000\nx\n", "misnamed.srt").format == "vtt"


class TestBilingualAndMatching:
    def _cues(self):
        return sp.parse_subtitle(
            b"1\n00:00:01,000 --> 00:00:02,000\n\xe4\xbd\xa0\xe5\xa5\xbd\nHello\n\n"
            b"2\n00:00:03,000 --> 00:00:04,000\nsolo\n\n", "a.srt").cues

    def test_split_order_and_unsplit_cues(self):
        source, translation, unsplit = sp.split_bilingual(self._cues())
        assert [c.text for c in source] == ["你好", "solo"]
        assert [c.text for c in translation] == ["Hello", ""] and unsplit == 1
        source, translation, _ = sp.split_bilingual(self._cues(), translation_first=True)
        assert source[0].text == "Hello" and translation[0].text == "你好"

    def test_looks_bilingual_needs_most_cues_to_have_two_lines(self):
        two = [sp.Cue(0, 1, "a\nb"), sp.Cue(1, 2, "c\nd")]
        assert sp.looks_bilingual(two) and not sp.looks_bilingual(self._cues())

    def test_match_by_time_overlap_over_half_not_by_position(self):
        lines = [Line(idx=0, start=0, end=4, zh="a"), Line(idx=1, start=4, end=8, zh="b"),
                 Line(idx=2, start=8, end=12, zh="c")]
        cues = [sp.Cue(0.5, 1.5, "in a"), sp.Cue(2.0, 3.5, "also a"),
                sp.Cue(2.0, 6.0, "half and half"),       # exactly 50% in each line: matches neither
                sp.Cue(8.5, 11.5, "in c"), sp.Cue(20, 21, "after everything"),
                sp.Cue(5.0, 5.0, "zero length inside b")]
        matched = sp.match_cues_to_lines(cues, lines)
        assert {k: [c.text for c in v] for k, v in matched.items()} == {
            0: ["in a", "also a"], 1: ["zero length inside b"], 2: ["in c"]}


class TestSidecars:
    NAMES = ["track.srt", "track.ja.vtt", "track.mp3.vtt", "track.en.srt", "Track.zh-Hans.ass",
             "other.srt", "track.final.srt", "track.mp3", "track.txt", "track.mp3.ko.lrc"]

    def test_accepted_patterns_only(self):
        got = [c.name for c in sc.rank_sidecars("track.mp3", self.NAMES)]
        assert set(got) == {"track.srt", "track.ja.vtt", "track.mp3.vtt", "track.en.srt",
                            "Track.zh-Hans.ass", "track.mp3.ko.lrc"}

    def test_ranking_prefers_the_titles_language_then_unlabelled(self):
        got = sc.rank_sidecars("track.mp3", self.NAMES, prefer_language="ja")
        assert got[0].name == "track.ja.vtt"
        assert [c.exact for c in got[1:3]] == [True, True]  # unlabelled before other languages
        assert got[-1].language in {"en", "ko", "zh"}

    def test_full_width_names_match(self):
        assert [c.name for c in sc.rank_sidecars("ｔｒａｃｋ.mp3", ["track.srt"])] == ["track.srt"]

    @pytest.mark.parametrize("text,expected", [
        ("你好世界", "zh"), ("こんにちは、世界の皆さん", "ja"), ("안녕하세요", "ko"),
        ("hello there friends", "latin"), ("12 !!", None), ("日本語のテキスト and some english", "ja")])
    def test_language_from_characters(self, text, expected):
        assert sc.detect_language(text) == expected


class TestLrcExport:
    def _lines(self):
        return [Line(idx=0, start=1.0, end=2.0, zh="一", en="one\ntwo"),
                Line(idx=1, start=2.0, end=3.0, zh="二", en="three"),
                Line(idx=2, start=61.5, end=62.0, zh="三", en="")]

    def test_stamps_and_gap_markers(self):
        out = subtitle_formats.lines_to_lrc(self._lines(), "en")
        # contiguous cues need no end stamp; a gap or the last cue gets an empty one; empty text is skipped
        assert out == "[00:01.00]one two\n[00:02.00]three\n[00:03.00]\n"

    def test_bilingual_is_two_lines_under_one_stamp(self):
        out = subtitle_formats.lines_to_lrc(self._lines()[:1], "bilingual")
        assert out == "[00:01.00]one two\n[00:01.00]一\n[00:02.00]\n"

    def test_round_trip_through_the_parser(self):
        lines = [Line(idx=0, start=1.0, end=2.5, zh="", en="alpha"), Line(idx=1, start=4.0, end=5.0, zh="", en="beta")]
        parsed = sp.parse_subtitle(subtitle_formats.lines_to_lrc(lines, "en").encode())
        assert _texts(parsed) == [(1.0, 2.5, "alpha"), (4.0, 5.0, "beta")]

    def test_a_minute_boundary_carries(self):
        assert subtitle_formats._lrc_ts(59.999) == "[01:00.00]"
