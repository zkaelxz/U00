"""
Tests for services/export_service.py: Migration Slice 12's read-only
Export-readiness summary, and Migration Slice 14's subtitle text
generation (generate_subtitle_text), both shared by the FastAPI
/api/export routes and the Streamlit Export tab.

The real scope guarantee this file exists to check: neither function
writes to the database, flags a line, or writes a file to disk --
generate_subtitle_text() only returns text for the caller to serve.
"""

import pytest

from core import Line
from services import export_service
from services.service_errors import InvalidInputError, NotFoundError


def _drama(db, **fields):
    fields.setdefault("title_en", "D")
    return db.create_drama(**fields)


class TestGetExportReadiness:
    def test_drama_with_no_lines_is_all_zero(self, isolated_db):
        did = _drama(isolated_db)
        result = export_service.get_export_readiness(did)
        assert result == {
            "drama_id": did,
            "total_lines": 0,
            "zh_filled": 0,
            "en_filled": 0,
            "fully_translated": False,
            "test_mode_output": False,
            "overlap_count": 0,
            "auto_qc_issue_count": 0,
            "dense_line_count": 0,
        }

    def test_partial_translation_counts_are_correct(self, isolated_db):
        did = _drama(isolated_db)
        lines = [
            Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en=""),
            Line(idx=2, start=4.0, end=6.0, zh="", en="Untranslated source"),
        ]
        isolated_db.save_lines(did, lines)
        result = export_service.get_export_readiness(did)
        assert result["total_lines"] == 3
        assert result["zh_filled"] == 2
        assert result["en_filled"] == 2
        assert result["fully_translated"] is False

    def test_fully_translated_drama(self, isolated_db):
        did = _drama(isolated_db)
        lines = [
            Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ]
        isolated_db.save_lines(did, lines)
        result = export_service.get_export_readiness(did)
        assert result["fully_translated"] is True

    def test_overlapping_lines_are_counted(self, isolated_db):
        did = _drama(isolated_db)
        lines = [
            # Line 0 runs past line 1's start -- a real overlap.
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ]
        isolated_db.save_lines(did, lines)
        result = export_service.get_export_readiness(did)
        assert result["overlap_count"] == 1

    def test_test_mode_output_flag_reflects_translation_engine(self, isolated_db):
        did = _drama(isolated_db, translation_engine="test_offline")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        result = export_service.get_export_readiness(did)
        assert result["test_mode_output"] is True

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.get_export_readiness(999999)


class TestGenerateSubtitleText:
    def _drama_with_lines(self, isolated_db, **fields):
        did = _drama(isolated_db, **fields)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        return did

    def test_srt_english(self, isolated_db):
        did = self._drama_with_lines(isolated_db)
        text = export_service.generate_subtitle_text(did, "srt", "en")
        assert "Hello" in text
        assert "00:00:00,000 --> 00:00:02,000" in text
        assert "你好" not in text

    def test_srt_bilingual(self, isolated_db):
        did = self._drama_with_lines(isolated_db)
        text = export_service.generate_subtitle_text(did, "srt", "bilingual")
        assert "Hello" in text
        assert "你好" in text

    def test_vtt_starts_with_webvtt_header(self, isolated_db):
        did = self._drama_with_lines(isolated_db)
        text = export_service.generate_subtitle_text(did, "vtt", "en")
        assert text.startswith("WEBVTT")
        assert "Hello" in text

    def test_overlapping_lines_are_trimmed_before_export(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        text = export_service.generate_subtitle_text(did, "srt", "en")
        # clamp_overlaps trims line 0's end to line 1's start (2.0) --
        # the raw, untrimmed 3.0 end must not appear in the export.
        assert "00:00:00,000 --> 00:00:03,000" not in text
        assert "00:00:00,000 --> 00:00:02,000" in text

    def test_wrap_chars_applies_to_srt(self, isolated_db):
        did = _drama(isolated_db)
        long_text = "This is a very long English subtitle line that should wrap onto more than one line"
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=5.0, zh="", en=long_text)])
        wrapped = export_service.generate_subtitle_text(
            did, "srt", "en", wrap_chars_en=20)
        unwrapped = export_service.generate_subtitle_text(did, "srt", "en")
        assert wrapped != unwrapped
        assert "\n" in wrapped.split("\n\n")[0]

    def test_include_notes_appends_inline_note(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        isolated_db.save_translation_notes(did, [
            {"line_idx": 0, "term": "Hello", "note_type": "cultural", "note": "a greeting"},
        ])
        with_notes = export_service.generate_subtitle_text(did, "srt", "en", include_notes=True)
        without_notes = export_service.generate_subtitle_text(did, "srt", "en", include_notes=False)
        assert with_notes != without_notes
        assert "a greeting" in with_notes
        assert "a greeting" not in without_notes

    def test_unknown_format_is_invalid_input(self, isolated_db):
        did = self._drama_with_lines(isolated_db)
        with pytest.raises(InvalidInputError):
            export_service.generate_subtitle_text(did, "ass", "en")

    def test_unknown_field_is_invalid_input(self, isolated_db):
        did = self._drama_with_lines(isolated_db)
        with pytest.raises(InvalidInputError):
            export_service.generate_subtitle_text(did, "srt", "not_a_field")

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.generate_subtitle_text(999999, "srt", "en")

    def test_never_writes_to_the_database(self, isolated_db):
        did = self._drama_with_lines(isolated_db)
        before = isolated_db.load_lines(did)
        export_service.generate_subtitle_text(did, "srt", "en", include_notes=True)
        after = isolated_db.load_lines(did)
        assert before == after
