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

import vocab_export


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
