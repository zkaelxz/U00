"""
Tests for services/export_service.py -- Migration Slice 12's read-only
Export-readiness summary, shared by the (later) FastAPI /api/export route
and the Streamlit Export tab.

The real scope guarantee this file exists to check: the service is
read-only. It must never flag a line, never write to the database, and
never generate a subtitle file -- it only reports counts.
"""

import pytest

from core import Line
from services import export_service
from services.service_errors import NotFoundError


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
