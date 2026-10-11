"""The vocab CSV is opened in spreadsheets, so words from imported text must not run as formulas."""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vocab_export


class TestCsvFormulaInjection:
    def test_cells_a_spreadsheet_would_run_are_quoted(self):
        rows = [{"word": "=cmd|' /C calc'!A0", "reading": None, "definitions": ["+1+1", "-2"]},
                {"word": "@SUM(A1)", "reading": None, "definitions": ["\tx"]},
                {"word": "你好", "reading": "ni3 hao3", "definitions": ["hello"]}]
        cells = list(csv.reader(io.StringIO(vocab_export.export_vocab_csv(rows))))[1:]
        assert cells[0] == ["'=cmd|' /C calc'!A0", "'+1+1; -2"]
        assert cells[1] == ["'@SUM(A1)", "'\tx"]
        assert cells[2] == ["你好 (ni3 hao3)", "hello"]
