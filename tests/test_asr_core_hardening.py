"""Regressions from the core / ASR hostile review: slicing, SRT text, novel text,
dictionary, overlaps and ffmpeg error text."""
import subprocess
import types

import pytest

import audio_extract
import dictionary
import subtitle_formats
from core import Line, lines_to_bilingual_srt, lines_to_srt
from novel_text import chunk_novel_text, load_novel_text_for_context


class TestExtractAudioSlice:
    def test_seeks_on_the_input_side_and_cuts_by_duration(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.setdefault("cmd", cmd))
        audio_extract.extract_audio_slice("in.m4a", 21600.0, 21603.5, "out.wav")
        cmd = seen["cmd"]
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-t") + 1] == "3.5"
        assert "-to" not in cmd
        assert cmd[cmd.index("-i") - 1] != "-ss"

    def test_failure_text_has_no_paths(self, monkeypatch):
        def boom(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)
        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(subprocess.CalledProcessError) as err:
            audio_extract.extract_audio_slice("/secret/media/in.m4a", 1.0, 2.0, "/tmp/x/out.wav")
        assert "/secret" not in str(err.value) and "out.wav" not in str(err.value)

    def test_timeout_text_has_no_paths(self, monkeypatch):
        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 5)
        monkeypatch.setattr(subprocess, "run", slow)
        with pytest.raises(subprocess.TimeoutExpired) as err:
            audio_extract.extract_audio_from_video("/secret/v.mp4", "/tmp/x/a.wav")
        assert "/secret" not in str(err.value)


class TestSrtBlankLines:
    def test_blank_lines_inside_a_bilingual_cue_do_not_end_it(self):
        ln = Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello\n\n")
        cue = lines_to_bilingual_srt([ln])
        assert cue.endswith("Hello\n你好\n") and "\n\n" not in cue.strip()

    def test_blank_lines_inside_a_single_language_cue(self):
        ln = Line(idx=0, start=0.0, end=1.0, zh="", en="One\n\n\nTwo")
        assert "One\nTwo" in lines_to_srt([ln], "en")


class TestChunkNovelTextLatin:
    def test_english_and_korean_split_on_ascii_sentence_ends(self):
        para = " ".join(["This is a sentence."] * 40)
        chunks = chunk_novel_text(para, max_chars=100)
        assert len(chunks) > 1 and all(len(c) <= 100 for c in chunks)
        assert " ".join(chunks) == para

    def test_decimal_numbers_are_not_sentence_ends(self):
        para = "It cost 3.5 dollars. " * 20
        assert all("3.5" in c for c in chunk_novel_text(para, max_chars=60))

    def test_text_without_any_sentence_end_is_hard_wrapped(self):
        assert all(len(c) <= 50 for c in chunk_novel_text("字" * 500, max_chars=50))


class TestNovelEncoding:
    @pytest.mark.parametrize("encoding,text", [
        ("gb18030", "他走进了房间，看见桌上有一封信。"),
        ("cp932", "彼は部屋に入り、机の上の手紙を見た。"),
        ("utf-8-sig", "그는 방에 들어갔다."),
    ])
    def test_decodes_common_encodings(self, encoding, text):
        assert load_novel_text_for_context(text.encode(encoding), "n.txt") == text

    def test_undecodable_file_fails_loudly(self):
        with pytest.raises(ValueError):
            load_novel_text_for_context(b"\xff\xfe\xfd\x81", "n.txt")


class TestCedictTraditional:
    def test_traditional_headword_is_found(self, monkeypatch):
        monkeypatch.setattr(dictionary, "_cedict_cache", None)
        monkeypatch.setattr(dictionary, "_ensure_cedict", lambda: None)
        import os
        path = os.path.join(os.path.dirname(dictionary.CEDICT_PATH), "cedict_trad_test.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("說 说 [shuo1] /to speak/to say/\n")
        monkeypatch.setattr(dictionary, "CEDICT_PATH", path)
        try:
            assert dictionary.lookup_cedict("說")["pinyin"] == "shuo1"
            assert dictionary.lookup_cedict("说")["pinyin"] == "shuo1"
        finally:
            monkeypatch.setattr(dictionary, "_cedict_cache", None)
            os.unlink(path)


class TestClampOverlapsUnsorted:
    def test_retimed_line_listed_out_of_order_does_not_zero_its_neighbour(self):
        a = Line(idx=0, start=10.0, end=12.0, zh="a", en="a")
        b = Line(idx=1, start=2.0, end=4.0, zh="b", en="b")  # retimed earlier than a
        out, clamped = subtitle_formats.clamp_overlaps([a, b])
        assert clamped == []
        assert (out[0].start, out[0].end) == (10.0, 12.0)
