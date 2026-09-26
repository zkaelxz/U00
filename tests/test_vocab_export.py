"""
tests/test_vocab_export.py -- tests for vocab_export.py's CSV export
(the .apkg export needs the optional genanki dependency, so it's
tested separately with a skip-if-missing guard).
"""

import sys
import os
import csv
import io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import vocab_export

genanki = pytest.importorskip("genanki")


class _FakeLine:
    def __init__(self, idx, zh, en, start, end):
        self.idx, self.zh, self.en, self.start, self.end = idx, zh, en, start, end


class TestExportVocabCsv:
    def test_produces_valid_csv_with_header(self):
        rows = [{"word": "你好", "reading": "ni3 hao3", "definitions": ["hello", "hi"]}]
        csv_text = vocab_export.export_vocab_csv(rows)
        reader = csv.reader(io.StringIO(csv_text))
        header = next(reader)
        assert header == ["Front", "Back"]

    def test_front_includes_word_and_reading(self):
        rows = [{"word": "你好", "reading": "ni3 hao3", "definitions": ["hello"]}]
        csv_text = vocab_export.export_vocab_csv(rows)
        assert "你好" in csv_text
        assert "ni3 hao3" in csv_text

    def test_back_joins_multiple_definitions(self):
        rows = [{"word": "好", "reading": "hao3", "definitions": ["good", "well", "fine"]}]
        csv_text = vocab_export.export_vocab_csv(rows)
        assert "good; well; fine" in csv_text

    def test_missing_reading_does_not_crash(self):
        rows = [{"word": "test", "reading": None, "definitions": ["a definition"]}]
        csv_text = vocab_export.export_vocab_csv(rows)
        assert "test" in csv_text

    def test_empty_list_produces_header_only(self):
        csv_text = vocab_export.export_vocab_csv([])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 1  # just the header


def _read_apkg(path):
    """Returns (list of notes, each a list of field strings; media file
    count) from a real .apkg written by genanki, for asserting on."""
    import zipfile
    import sqlite3
    import json
    import tempfile

    with zipfile.ZipFile(path) as z:
        media = json.loads(z.read("media").decode("utf-8"))
        with tempfile.NamedTemporaryFile(suffix=".anki2", delete=False) as tmp:
            tmp.write(z.read("collection.anki2"))
            db_path = tmp.name
    try:
        conn = sqlite3.connect(db_path)
        flds = [r[0] for r in conn.execute("SELECT flds FROM notes").fetchall()]
        conn.close()
    finally:
        os.unlink(db_path)
    notes = [f.split("\x1f") for f in flds]
    return notes, len(media)


class TestExportVocabApkgSentence:
    def _rows_and_lines(self):
        rows = [{"id": 1, "word": "你好", "reading": "ni3 hao3", "definitions": ["hello"],
                  "first_seen_line_idx": 0}]
        lines = [_FakeLine(0, "你好，世界", "Hello, world", 1.0, 3.0)]
        return rows, lines

    def test_word_only_export_unchanged(self, tmp_path):
        rows = [{"word": "你好", "reading": "ni3 hao3", "definitions": ["hello", "hi"]}]
        out_path = str(tmp_path / "word_only.apkg")
        vocab_export.export_vocab_apkg(rows, "Test Deck", out_path)
        notes, media_count = _read_apkg(out_path)
        assert len(notes) == 1
        assert "你好" in notes[0][0] and "ni3 hao3" in notes[0][0]
        assert media_count == 0

    def test_sentence_card_back_has_translation_definition_and_reading(self, tmp_path):
        rows, lines = self._rows_and_lines()
        out_path = str(tmp_path / "sentence.apkg")
        vocab_export.export_vocab_apkg_sentence(rows, lines, "Test Deck", out_path, audio_path=None)
        notes, _ = _read_apkg(out_path)
        assert len(notes) == 1
        front, back = notes[0]
        assert "你好，世界" in front
        assert "Hello, world" in back
        assert "hello" in back
        assert "ni3 hao3" in back

    def test_no_audio_path_omits_audio_cleanly(self, tmp_path):
        rows, lines = self._rows_and_lines()
        out_path = str(tmp_path / "sentence_no_audio.apkg")
        vocab_export.export_vocab_apkg_sentence(rows, lines, "Test Deck", out_path, audio_path=None)
        notes, media_count = _read_apkg(out_path)
        assert media_count == 0
        assert "[sound:" not in notes[0][0]

    def test_embeds_audio_clip_when_audio_path_given(self, tmp_path, monkeypatch):
        rows, lines = self._rows_and_lines()
        fake_audio = tmp_path / "source.wav"
        fake_audio.write_bytes(b"fake audio bytes")

        monkeypatch.setattr(
            vocab_export, "_extract_audio_slice",
            lambda audio_path, start, end, out_path: open(out_path, "wb").write(b"clip"))

        out_path = str(tmp_path / "sentence_audio.apkg")
        vocab_export.export_vocab_apkg_sentence(
            rows, lines, "Test Deck", out_path, audio_path=str(fake_audio))
        notes, media_count = _read_apkg(out_path)
        assert media_count == 1
        assert "[sound:" in notes[0][0]

    def test_skips_rows_whose_line_cannot_be_found(self, tmp_path):
        rows = [{"id": 1, "word": "你好", "reading": None, "definitions": [],
                  "first_seen_line_idx": 99}]
        out_path = str(tmp_path / "sentence_missing_line.apkg")
        vocab_export.export_vocab_apkg_sentence(rows, [], "Test Deck", out_path, audio_path=None)
        notes, _ = _read_apkg(out_path)
        assert notes == []
