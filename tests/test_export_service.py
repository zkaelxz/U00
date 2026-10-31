"""
Tests for services/export_service.py: Migration Slice 12's read-only
Export-readiness summary, and Migration Slice 14's subtitle text
generation (generate_subtitle_text), both shared by the FastAPI
/api/export routes and the Streamlit Export tab.

The real scope guarantee this file exists to check: neither function
writes to the database, flags a line, or writes a file to disk --
generate_subtitle_text() only returns text for the caller to serve.
"""

import os
import sys
import pytest

from core import Line, lines_from_rows
from services import export_service, lines_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)


def _set_flag_now(db, did, line_id, flag, note):
    """Another writer (the Review page) saving a flag on one line."""
    ln = next(x for x in lines_from_rows(db.load_lines(did)) if x.id == line_id)
    ln.flag, ln.flag_note = flag, note
    db.save_lines(did, [ln], fields=("flag", "flag_note"))


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

    def test_auto_qc_count_uses_glossary_banned_terms(self, isolated_db):
        sid = isolated_db.create_series("QC series")
        isolated_db.upsert_glossary_term(
            sid, "沈清疑", "Shen Qingyi", banned_translations="Chen Qingyi")
        did = _drama(isolated_db, series_id=sid)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="沈清疑来了", en="Chen Qingyi is here"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        count = export_service.get_export_readiness(did)["auto_qc_issue_count"]
        assert count == 1
        assert export_service.run_auto_qc_flagging(did)["flagged"] == count

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

    def test_overlap_count_does_not_depend_on_the_export_format(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="a", en="One"),
            Line(idx=1, start=2.0, end=5.0, zh="b", en="Two"),
            Line(idx=2, start=4.0, end=6.0, zh="c", en="Three"),
        ])
        export_service.generate_ass_text(did)
        export_service.generate_subtitle_text(did, "vtt", "en")
        assert export_service.get_export_readiness(did)["overlap_count"] == 2

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

    def test_ass_and_vtt_keep_overlapping_cues_as_saved(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        ass = export_service.generate_ass_text(did)
        assert "0:00:00.00,0:00:03.00" in ass and "0:00:02.00,0:00:04.00" in ass
        vtt = export_service.generate_subtitle_text(did, "vtt", "en")
        assert "00:00:00.000 --> 00:00:03.000" in vtt and "00:00:02.000 --> 00:00:04.000" in vtt

    def test_srt_and_bilingual_srt_still_trim_the_same_overlap(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        for field in ("en", "bilingual"):
            text = export_service.generate_subtitle_text(did, "srt", field)
            assert "00:00:00,000 --> 00:00:02,000" in text
            assert "00:00:03,000" not in text

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


class TestFlagOverlappingLines:
    def test_flags_an_overlapping_pair(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        result = export_service.flag_overlapping_lines(did)
        assert result == {"flagged_count": 1}
        lines = {ln.idx: ln for ln in lines_from_rows(isolated_db.load_lines(did))}
        assert lines[0].flag == "timing_overlap"
        assert lines[1].flag is None or lines[1].flag == ""

    def test_no_overlap_is_a_zero_count_not_an_error(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello")])
        assert export_service.flag_overlapping_lines(did) == {"flagged_count": 0}

    def test_already_flagged_line_is_left_alone(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello",
                 flag="reading_speed", flag_note="pre-existing"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        result = export_service.flag_overlapping_lines(did)
        assert result == {"flagged_count": 0}

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.flag_overlapping_lines(999999)


    def test_a_flag_the_user_sets_while_checking_is_kept(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        first = isolated_db.load_lines(did)[0]["id"]
        real = export_service.subtitle_formats.clamp_overlaps

        def user_flags_meanwhile(lines):
            _set_flag_now(isolated_db, did, first, "uncertain_translation", "mine")
            return real(lines)
        monkeypatch.setattr(export_service.subtitle_formats, "clamp_overlaps",
                            user_flags_meanwhile)
        assert export_service.flag_overlapping_lines(did) == {"flagged_count": 0}
        row = isolated_db.load_lines(did)[0]
        assert (row["flag"], row["flag_note"]) == ("uncertain_translation", "mine")


    def test_the_next_line_moved_while_checking_is_not_flagged(self, isolated_db, monkeypatch):
        """The overlap is with the NEXT line: moving that one away while the
        check runs must stop the flag too."""
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=3.0, zh="你好", en="Hello"),
            Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
        ])
        second = isolated_db.load_lines(did)[1]["id"]
        real = export_service.subtitle_formats.clamp_overlaps

        def user_moves_the_next_line(lines):
            lines_service.patch_line(did, second, start=3.5, end=5.0)
            return real(lines)
        monkeypatch.setattr(export_service.subtitle_formats, "clamp_overlaps",
                            user_moves_the_next_line)
        assert export_service.flag_overlapping_lines(did) == {"flagged_count": 0}
        assert isolated_db.load_lines(did)[0]["flag"] is None


class TestFlagDenseLines:
    def test_english_shortened_while_checking_is_not_flagged(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="", en="word " * 60)])
        lid = isolated_db.load_lines(did)[0]["id"]
        real = export_service.subtitle_formats.flag_dense_lines

        def user_edits_meanwhile(lines, **kw):
            lines_service.patch_line(did, lid, en="Short.")
            return real(lines, **kw)
        monkeypatch.setattr(export_service.subtitle_formats, "flag_dense_lines",
                            user_edits_meanwhile)
        assert export_service.flag_dense_lines(did) == {"flagged_count": 0}
        row = isolated_db.load_lines(did)[0]
        assert (row["en"], row["flag"]) == ("Short.", None)

    def test_flags_a_dense_line(self, isolated_db):
        did = _drama(isolated_db)
        dense_text = "word " * 60  # far more than fits in 1 second on screen
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="", en=dense_text)])
        result = export_service.flag_dense_lines(did)
        assert result["flagged_count"] == 1

    def test_no_dense_lines_is_a_zero_count(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=5.0, zh="", en="Hi")])
        assert export_service.flag_dense_lines(did) == {"flagged_count": 0}

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.flag_dense_lines(999999)

    def test_the_titles_mode_is_honoured(self, isolated_db):
        text = "word " * 20  # 19.8 cps over 5 s: over 17, under 20
        for mode, count in (("normal", 1), ("relaxed", 0), ("off", 0)):
            did = _drama(isolated_db)
            export_service.set_reading_speed_mode(did, mode)
            isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=5.0, zh="", en=text)])
            assert export_service.flag_dense_lines(did) == {"flagged_count": count}, mode
            assert export_service.get_export_readiness(did)["dense_line_count"] == (
                1 if mode == "normal" else 0)


class TestReadingSpeedMode:
    def test_default_is_normal_and_set_is_saved(self, isolated_db):
        did = _drama(isolated_db)
        assert export_service.get_reading_speed_mode(did) == {"mode": "normal"}
        assert export_service.set_reading_speed_mode(did, "relaxed") == {"mode": "relaxed"}
        assert export_service.get_reading_speed_mode(did) == {"mode": "relaxed"}

    def test_unknown_mode_and_unknown_drama_are_refused(self, isolated_db):
        did = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            export_service.set_reading_speed_mode(did, "strict")
        with pytest.raises(NotFoundError):
            export_service.set_reading_speed_mode(999999, "off")
        with pytest.raises(NotFoundError):
            export_service.get_reading_speed_mode(999999)

    def test_changing_the_mode_does_not_clear_existing_flags(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=5, zh="", en="word " * 20)])
        export_service.flag_dense_lines(did)
        export_service.set_reading_speed_mode(did, "off")
        assert isolated_db.load_lines(did)[0]["flag"] == "reading_speed"


class TestClearReadingSpeedFlags:
    def _lines(self):
        return [
            Line(idx=0, start=0, end=5, zh="a", en="one", flag="reading_speed", flag_note="fast"),
            Line(idx=1, start=5, end=9, zh="b", en="two", flag="uncertain_translation",
                 flag_note="check"),
            Line(idx=2, start=9, end=12, zh="c", en="three"),
            Line(idx=3, start=12, end=15, zh="d", en="four", flag="reading_speed_x", flag_note="n"),
        ]

    def test_clears_only_reading_speed_flags_and_snapshots_first(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, self._lines())
        result = export_service.clear_reading_speed_flags(did)
        assert result["cleared_count"] == 1 and result["flagged_count"] == 0
        rows = isolated_db.load_lines(did)
        assert [(r["flag"], r["flag_note"], r["en"]) for r in rows] == [
            (None, "", "one"), ("uncertain_translation", "check", "two"),
            (None, "", "three"), ("reading_speed_x", "n", "four")]
        history = isolated_db.list_line_history(did)
        assert [h["label"] for h in history] == ["before clearing reading-speed flags"]
        assert history[0]["id"] == result["history_id"]

    def test_the_snapshot_restores_the_flags(self, isolated_db):
        from services import restructure_service
        did = _drama(isolated_db)
        isolated_db.save_lines(did, self._lines())
        result = export_service.clear_reading_speed_flags(did)
        ids = [r["id"] for r in isolated_db.load_lines(did)]
        restructure_service.restore_version(did, result["history_id"], ids)
        assert isolated_db.load_lines(did)[0]["flag"] == "reading_speed"
        assert isolated_db.load_lines(did)[0]["flag_note"] == "fast"

    def test_nothing_to_clear_takes_no_snapshot(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=5, zh="a", en="x")])
        assert export_service.clear_reading_speed_flags(did) == {
            "cleared_count": 0, "flagged_count": 0, "history_id": None}
        assert isolated_db.list_line_history(did) == []

    def test_recheck_flags_again_at_the_current_mode(self, isolated_db):
        did = _drama(isolated_db)
        text = "word " * 20  # 19.8 cps over 5 s
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=5, zh="", en=text,
                                          flag="reading_speed", flag_note="old")])
        export_service.set_reading_speed_mode(did, "relaxed")
        result = export_service.clear_reading_speed_flags(did, recheck=True)
        assert (result["cleared_count"], result["flagged_count"]) == (1, 0)
        assert isolated_db.load_lines(did)[0]["flag"] is None
        export_service.set_reading_speed_mode(did, "normal")
        result = export_service.clear_reading_speed_flags(did, recheck=True)
        assert result["flagged_count"] == 1
        assert isolated_db.load_lines(did)[0]["flag"] == "reading_speed"

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.clear_reading_speed_flags(999999)

    def test_a_flag_the_user_sets_while_clearing_is_kept(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, self._lines())
        first = isolated_db.load_lines(did)[0]["id"]
        real = isolated_db.save_line_history_snapshot

        def user_flags_meanwhile(*a, **k):
            out = real(*a, **k)
            _set_flag_now(isolated_db, did, first, "uncertain_translation", "mine")
            return out
        monkeypatch.setattr(isolated_db, "save_line_history_snapshot", user_flags_meanwhile)
        result = export_service.clear_reading_speed_flags(did)
        assert result["cleared_count"] == 0
        row = isolated_db.load_lines(did)[0]
        assert (row["flag"], row["flag_note"]) == ("uncertain_translation", "mine")


class TestRunAutoQcFlagging:
    def test_flags_a_missing_number(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="他有三个孩子。", en="He has kids."),
        ])
        result = export_service.run_auto_qc_flagging(did)
        assert result["flagged"] == 1
        assert result["checked"] == 1

    def test_clean_line_is_not_flagged(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
        ])
        result = export_service.run_auto_qc_flagging(did)
        assert result == {"flagged": 0, "cleared": 0, "already_flagged": 0, "checked": 1}

    def test_series_glossary_names_are_used_when_drama_has_a_series(self, isolated_db):
        series_id = isolated_db.get_or_create_series("S")
        isolated_db.upsert_glossary_term(
            series_id, term_original="林晚晚", term_translation="Lin Wanwan",
            category="person_name")
        did = _drama(isolated_db, series_id=series_id)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="林晚晚来了。", en="She's here."),
        ])
        result = export_service.run_auto_qc_flagging(did)
        assert result["flagged"] == 1

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.run_auto_qc_flagging(999999)

    def test_english_fixed_while_checking_is_not_flagged(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="他有三个孩子。", en="He has kids."),
        ])
        lid = isolated_db.load_lines(did)[0]["id"]
        real = export_service.auto_qc.run_auto_qc

        def user_fixes_meanwhile(lines, *a, **k):
            lines_service.patch_line(did, lid, en="He has three kids.")
            return real(lines, *a, **k)
        monkeypatch.setattr(export_service.auto_qc, "run_auto_qc", user_fixes_meanwhile)
        result = export_service.run_auto_qc_flagging(did)
        assert result["flagged"] == 0
        row = isolated_db.load_lines(did)[0]
        assert (row["en"], row["flag"]) == ("He has three kids.", None)


class TestGenerateEpub:
    def _novel_drama_with_lines(self, isolated_db, **fields):
        fields.setdefault("content_mode", "novel_narration")
        did = _drama(isolated_db, **fields)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好世界", en="Hello world"),
        ])
        return did

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.generate_epub(999999)

    def test_non_novel_drama_raises_unsupported_operation(self, isolated_db):
        did = self._novel_drama_with_lines(isolated_db, content_mode="audio_drama")
        with pytest.raises(UnsupportedOperationError):
            export_service.generate_epub(did)

    def test_unknown_field_is_invalid_input(self, isolated_db):
        did = self._novel_drama_with_lines(isolated_db)
        with pytest.raises(InvalidInputError):
            export_service.generate_epub(did, field="bilingual")

    def test_missing_ebooklib_raises_dependency_unavailable(self, isolated_db, monkeypatch):
        # ebooklib is imported inside epub_io.export_epub, so block that package itself.
        monkeypatch.setitem(sys.modules, "ebooklib", None)
        did = self._novel_drama_with_lines(isolated_db)
        with pytest.raises(DependencyUnavailableError) as exc:
            export_service.generate_epub(did)
        assert "ebooklib" in str(exc.value) and "Diagnostics" in str(exc.value)
        assert exc.value.details == {"reason": "missing_package", "package": "ebooklib"}

    def test_a_missing_dependency_of_ebooklib_is_named_not_blamed_on_ebooklib(
            self, isolated_db, monkeypatch):
        import epub_io

        def boom(*a, **k):  # what `from ebooklib import epub` raises when lxml is absent
            raise ImportError("No module named 'lxml'", name="lxml.etree")

        monkeypatch.setattr(epub_io, "export_epub", boom)
        did = self._novel_drama_with_lines(isolated_db)
        with pytest.raises(DependencyUnavailableError) as exc:
            export_service.generate_epub(did)
        assert exc.value.details == {"reason": "missing_package", "package": "lxml"}
        assert "lxml" in str(exc.value) and "ebooklib" not in str(exc.value)

    def test_an_unrelated_import_error_is_not_reported_as_a_missing_install(
            self, isolated_db, monkeypatch):
        import epub_io

        def boom(*a, **k):
            raise ImportError("broken", name="baihe_own_module")

        monkeypatch.setattr(epub_io, "export_epub", boom)
        did = self._novel_drama_with_lines(isolated_db)
        with pytest.raises(ImportError):
            export_service.generate_epub(did)


class TestGenerateEpubOutput:
    """generate_epub with epub_io.export_epub stubbed, so these run core-only."""

    def _novel(self, isolated_db, lines):
        did = _drama(isolated_db, content_mode="novel_narration")
        isolated_db.save_lines(did, lines)
        return did

    def test_an_untranslated_novel_is_refused_not_exported_empty(self, isolated_db):
        did = self._novel(isolated_db, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
        with pytest.raises(ConflictError):
            export_service.generate_epub(did)
        with pytest.raises(ConflictError):
            export_service.generate_epub(self._novel(isolated_db, []), field="zh")

    def test_two_downloads_at_once_each_get_their_own_book(self, isolated_db, monkeypatch):
        import epub_io
        did = self._novel(isolated_db, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        nested = {}

        def fake_export(lines, title, author, out_path, field="en", **kw):
            if field == "en":   # the original-text download lands while this one is building
                nested["zh"] = export_service.generate_epub(did, field="zh")
            with open(out_path, "wb") as f:
                f.write(field.encode())
        monkeypatch.setattr(epub_io, "export_epub", fake_export)

        assert export_service.generate_epub(did, field="en") == b"en"
        assert nested["zh"] == b"zh"
        assert not [n for n in os.listdir(isolated_db.drama_dir(did)) if "epub" in n]


class TestGenerateEpubWithEbooklib:
    """Only runs if ebooklib is actually installed -- see epub_io.py's
    own note that it's an optional extra."""

    @pytest.fixture(autouse=True)
    def _require_ebooklib(self):
        pytest.importorskip("ebooklib")

    def _novel_drama_with_lines(self, isolated_db, **fields):
        fields.setdefault("content_mode", "novel_narration")
        did = _drama(isolated_db, **fields)
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="你好世界", en="Hello world"),
        ])
        return did

    def test_generates_real_epub_bytes(self, isolated_db):
        did = self._novel_drama_with_lines(isolated_db, title_en="My Novel")
        data = export_service.generate_epub(did)
        assert isinstance(data, bytes)
        assert data[:2] == b"PK"  # EPUB is a zip container

    def test_zh_field_selects_source_text(self, isolated_db):
        did = self._novel_drama_with_lines(isolated_db)
        data = export_service.generate_epub(did, field="zh")
        assert isinstance(data, bytes)
        assert len(data) > 0


# ---------------------------------------------------------------- Slice 27: ASS

import subtitle_formats as sf


def _ass_drama(db, lines=None, **fields):
    did = _drama(db, **fields)
    db.save_lines(did, lines or [
        Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello", speaker="A"),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye", speaker="B"),
    ])
    return did


class TestGetAssStyleOptions:
    def test_matches_module_constants(self):
        opts = export_service.get_ass_style_options()
        assert opts["presets"] == sf.ASS_PRESETS
        assert opts["fonts"] == sf.FONT_CHOICES
        assert opts["alignments"] == sf.ALIGNMENTS
        assert opts["default_preset"] in sf.ASS_PRESETS


class TestGenerateAssText:
    def _expected(self, db, did, style, field="en", **kw):
        lines = lines_from_rows(db.load_lines(did))
        return sf.lines_to_ass(lines, style, field, title="D", **kw)

    def test_en_matches_real_formatter(self, isolated_db):
        did = _ass_drama(isolated_db)
        text = export_service.generate_ass_text(did, "en", per_speaker_colors=False)
        assert text.startswith("[Script Info]")
        assert text == self._expected(isolated_db, did, sf.ASS_PRESETS["Clean"],
                                      speaker_colors={}, speaker_names={})
        assert "Hello" in text and "你好" not in text

    def test_zh_and_bilingual(self, isolated_db):
        did = _ass_drama(isolated_db)
        zh = export_service.generate_ass_text(did, "zh", per_speaker_colors=False)
        bi = export_service.generate_ass_text(did, "bilingual", per_speaker_colors=False)
        assert "你好" in zh and "Hello" not in zh
        assert "你好" in bi and "Hello" in bi

    def test_per_speaker_default_and_supplied_colors(self, isolated_db):
        did = _ass_drama(isolated_db)
        text = export_service.generate_ass_text(did)
        assert "Style: Speaker 1" in text and "Style: Speaker 2" in text
        text = export_service.generate_ass_text(
            did, speaker_colors={"A": "#123456", "B": "#654321"})
        assert sf.ass_color("#123456") in text and sf.ass_color("#654321") in text

    def test_per_speaker_off_has_no_speaker_styles(self, isolated_db):
        did = _ass_drama(isolated_db)
        text = export_service.generate_ass_text(
            did, speaker_colors={"A": "#123456"}, per_speaker_colors=False)
        assert "Style: Speaker" not in text

    def test_overrides_beat_preset_and_missing_keys_fall_back(self, isolated_db):
        did = _ass_drama(isolated_db)
        text = export_service.generate_ass_text(
            did, style={"font": "Meiryo", "size": 40}, preset="Streamer clip",
            per_speaker_colors=False)
        expected = dict(sf.ASS_PRESETS["Streamer clip"], font="Meiryo", size=40,
                        sfx_alignment=None, notes_alignment=None)
        assert text == self._expected(isolated_db, did, expected,
                                      speaker_colors={}, speaker_names={})
        assert "Meiryo,40" in text and "Arial Black" not in text

    def test_overlaps_are_kept_unlike_srt(self, isolated_db):
        did = _ass_drama(isolated_db, [
            Line(idx=0, start=0.0, end=3.0, zh="a", en="One"),
            Line(idx=1, start=2.0, end=4.0, zh="b", en="Two"),
        ])
        text = export_service.generate_ass_text(did)
        assert "Dialogue: 0,0:00:00.00,0:00:03.00" in text
        assert "Dialogue: 0,0:00:02.00,0:00:04.00" in text

    def test_wrap_applied(self, isolated_db):
        long_text = "This is a very long English subtitle line that should wrap onto more lines"
        did = _ass_drama(isolated_db, [Line(idx=0, start=0.0, end=5.0, zh="", en=long_text)])
        assert "\\N" not in export_service.generate_ass_text(did)
        assert "\\N" in export_service.generate_ass_text(did, wrap_chars_en=20)

    def test_speaker_names_used(self, isolated_db):
        did = _ass_drama(isolated_db)
        isolated_db.upsert_character(did, "A", character_name="Alice")
        assert ",Alice,0,0,0,," in export_service.generate_ass_text(did)

    def test_notes_inline_and_separate(self, isolated_db):
        did = _ass_drama(isolated_db)
        isolated_db.save_translation_notes(did, [
            {"line_idx": 0, "term": "Hello", "note_type": "cultural", "note": "a greeting"}])
        assert "a greeting" not in export_service.generate_ass_text(did)
        inline = export_service.generate_ass_text(did, include_notes=True)
        assert "a greeting" in inline and "Style: Notes" not in inline
        sep = export_service.generate_ass_text(
            did, include_notes=True, notes_as_separate_line=True)
        assert "Style: Notes" in sep
        assert any(l.startswith("Dialogue:") and ",Notes," in l and "a greeting" in l
                   for l in sep.splitlines())

    @pytest.mark.parametrize("kwargs", [
        {"field": "nope"},
        {"preset": "Nope"},
        {"style": {"primary": "red"}},
        {"style": {"primary": "#FFF"}},
        {"style": {"outline": "#GGGGGG"}},
        {"style": {"font": ""}},
        {"style": {"font": "  "}},
        {"style": {"size": 5}},
        {"style": {"size": 99}},
        {"style": {"size": "24"}},
        {"style": {"outline_width": 11}},
        {"style": {"shadow": 6}},
        {"style": {"shadow": -1}},
        {"style": {"bold": "yes"}},
        {"style": {"alignment": "middle"}},
        {"style": {"alignment": None}},
        {"style": {"sfx_alignment": "middle"}},
        {"style": {"notes_alignment": 2}},
        {"style": {"bogus": 1}},
        {"speaker_colors": {"A": "blue"}},
        {"notes_as_separate_line": True},
        {"wrap_chars_en": -1},
        {"wrap_chars_source": 1.5},
    ])
    def test_validation_errors(self, isolated_db, kwargs):
        did = _ass_drama(isolated_db)
        with pytest.raises(InvalidInputError):
            export_service.generate_ass_text(did, **kwargs)

    def test_error_does_not_echo_value(self, isolated_db):
        did = _ass_drama(isolated_db)
        with pytest.raises(InvalidInputError) as ei:
            export_service.generate_ass_text(did, style={"primary": "SECRETVALUE"})
        assert "SECRETVALUE" not in str(ei.value)

    def test_valid_alignments_accepted(self, isolated_db):
        did = _ass_drama(isolated_db)
        text = export_service.generate_ass_text(
            did, style={"alignment": "top-left", "sfx_alignment": "top-right",
                        "notes_alignment": None})
        assert text.startswith("[Script Info]")

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.generate_ass_text(99999)


class TestAssInjection:
    EVIL = "Arial\n[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,PWNED"

    def test_font_rejected_in_service(self, isolated_db):
        did = _ass_drama(isolated_db)
        with pytest.raises(InvalidInputError) as e:
            export_service.generate_ass_text(did, style={"font": self.EVIL})
        assert "PWNED" not in str(e.value)

    def test_formatter_sanitises_font_title_and_speaker(self):
        lines = [Line(idx=0, start=0.0, end=2.0, zh="", en="Hi", speaker=self.EVIL)]
        style = dict(sf.ASS_PRESETS["Clean"], font=self.EVIL)
        text = sf.lines_to_ass(lines, style, "en", title=self.EVIL,
                               speaker_colors={self.EVIL: "#123456"})
        clean = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "en")
        assert len(text.split("\n")) == len(clean.split("\n")) + 1  # extra speaker style only
        assert "\r" not in text
        assert [l for l in text.split("\n") if l.startswith("[Events]")] == ["[Events]"]
        assert not any(l.startswith("Dialogue: 0,0:00:00.00,0:00:01.00,Default") for l in text.split("\n"))


class TestSpeakerColorsCap:
    def test_too_many_or_long(self, isolated_db):
        did = _ass_drama(isolated_db)
        with pytest.raises(InvalidInputError):
            export_service.generate_ass_text(
                did, speaker_colors={f"S{i}": "#123456" for i in range(201)})
        with pytest.raises(InvalidInputError):
            export_service.generate_ass_text(did, speaker_colors={"x" * 101: "#123456"})
        export_service.generate_ass_text(
            did, speaker_colors={f"S{i}": "#123456" for i in range(200)})


class TestWrapIsLanguageAgnostic:
    """Export wraps by the caller's per-field caps and each line's own text, not by a
    language default, so a mixed-language title needs no per-line handling."""

    def test_each_line_wraps_by_its_own_script_under_one_source_cap(self, isolated_db):
        did = _drama(isolated_db)
        ja = "今日はとても良い天気ですね、散歩に行きましょう"
        en = "It is a lovely day today so let us go for a walk"
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh=ja, lang="ja"),
            Line(idx=1, start=2.0, end=4.0, zh=en, lang="en")])
        text = export_service.generate_subtitle_text(did, "srt", "zh", wrap_chars_source=20)
        cues = [c.split("\n")[2:] for c in text.strip().split("\n\n")]
        assert all(len(part) <= 20 for part in cues[0]) and len(cues[0]) > 1
        assert all(len(part) <= 20 for part in cues[1]) and " ".join(cues[1]) == en

    def test_no_cap_leaves_every_language_unwrapped(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh="長い" * 30, lang="ja")])
        text = export_service.generate_subtitle_text(did, "srt", "zh")
        assert text.strip().split("\n")[2] == "長い" * 30


class TestSourceWrapUsesTheTitlesLanguage:
    TEXT = "あいうえおかきくけこさしすせそたちつてと"

    @pytest.mark.parametrize("fmt", ["srt", "vtt", "lrc", "ass"])
    def test_every_format_breaks_the_source_with_the_drama_language(self, isolated_db, monkeypatch, fmt):
        import segment
        seen = []

        def tiers(text, language, script="simplified"):
            seen.append((language, script))
            return [{10}]
        monkeypatch.setattr(segment, "line_break_tiers", tiers)
        did = _drama(isolated_db, source_language="ja", chinese_script="traditional")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=2.0, zh=self.TEXT, en="Hi")])
        if fmt == "ass":
            text = export_service.generate_ass_text(did, "zh", wrap_chars_source=16)
        else:
            text = export_service.generate_subtitle_export(did, fmt, "zh", wrap_chars_source=16)["text"]
        assert self.TEXT[:10] in text and self.TEXT[:16] not in text
        assert set(seen) == {("ja", "traditional")}

    def test_burn_preview_ass_carries_the_language(self, monkeypatch):
        import segment
        from services import media_playback_service
        monkeypatch.setattr(segment, "line_break_tiers", lambda *a, **k: [{10}])
        line = Line(idx=0, start=0.0, end=2.0, zh=self.TEXT, en="")
        _, _, ass = media_playback_service.burn_preview_ass(
            [line], line, {"wrap_chars": {"en": 42, "zh": 16}, "language": "ja"})
        assert self.TEXT[:10] + "\\N" in ass


class TestVideoFrameAspect:
    def _probe(self, monkeypatch, streams):
        monkeypatch.setattr(export_service.media_inspect, "run_ffprobe",
                            lambda path, timeout=None: {"streams": streams})

    def test_landscape_portrait_and_anamorphic(self, monkeypatch):
        self._probe(monkeypatch, [{"codec_type": "video", "width": 1920, "height": 1080}])
        assert export_service.video_frame_aspect("v.mp4") == pytest.approx(16 / 9)
        self._probe(monkeypatch, [{"codec_type": "video", "width": 1080, "height": 1920}])
        assert export_service.video_frame_aspect("v.mp4") == pytest.approx(9 / 16)
        # A 720x480 DVD frame shown at 16:9.
        self._probe(monkeypatch, [{"codec_type": "video", "width": 720, "height": 480,
                                   "sample_aspect_ratio": "32:27"}])
        assert export_service.video_frame_aspect("v.mp4") == pytest.approx(16 / 9)

    def test_a_rotated_phone_video_is_portrait(self, monkeypatch):
        self._probe(monkeypatch, [{"codec_type": "video", "width": 1920, "height": 1080,
                                   "side_data_list": [{"rotation": -90}]}])
        assert export_service.video_frame_aspect("v.mp4") == pytest.approx(9 / 16)
        self._probe(monkeypatch, [{"codec_type": "video", "width": 1920, "height": 1080,
                                   "tags": {"rotate": "90"}}])
        assert export_service.video_frame_aspect("v.mp4") == pytest.approx(9 / 16)

    def test_cover_art_is_skipped_and_unreadable_is_none(self, monkeypatch):
        self._probe(monkeypatch, [{"codec_type": "video", "width": 500, "height": 500,
                                   "disposition": {"attached_pic": 1}}])
        assert export_service.video_frame_aspect("a.m4a") is None
        self._probe(monkeypatch, [{"codec_type": "video", "width": 0, "height": 1080}])
        assert export_service.video_frame_aspect("v.mp4") is None

        def missing(path, timeout=None):
            raise export_service.media_inspect.ProbeError("ffprobe isn't installed.", "missing")
        monkeypatch.setattr(export_service.media_inspect, "run_ffprobe", missing)
        assert export_service.video_frame_aspect("v.mp4") is None


class TestAssDownloadUsesTheTitlesVideo:
    LONG_ZH = "他说：「这件事我们明天再谈吧，今天太晚了。」然后转身离开了房间。"

    def _title(self, db, **files):
        did = _drama(db, source_language="zh", **files)
        db.save_lines(did, [Line(idx=0, start=0.0, end=5.0, zh=self.LONG_ZH, en="Hi")])
        for name in files.values():
            with open(os.path.join(db.drama_dir(did), name), "wb") as f:
                f.write(b"media")
        return did

    def _rows(self, ass):
        dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
        return dialogue.split(",", 9)[9].split("\\N")

    def test_a_portrait_source_video_shortens_the_rows(self, isolated_db, monkeypatch):
        seen = []
        monkeypatch.setattr(export_service, "video_frame_aspect",
                            lambda path: seen.append(os.path.basename(path)) or 9 / 16)
        did = self._title(isolated_db, source_video_filename="clip.mp4")
        rows = self._rows(export_service.generate_ass_text(did, "zh"))
        assert seen == ["clip.mp4"]
        sf = export_service.subtitle_formats
        portrait = sf.ass_cjk_row_limit(sf.ASS_PRESETS["Clean"], 9 / 16)
        assert "".join(rows) == self.LONG_ZH and all(len(r) <= portrait for r in rows)
        # A shape the caller already measured is used as is.
        seen.clear()
        wide = self._rows(export_service.generate_ass_text(did, "zh", frame_aspect=None))
        assert seen == [] and max(map(len, wide)) > portrait

    def test_an_audio_only_title_is_not_probed(self, isolated_db, monkeypatch):
        monkeypatch.setattr(export_service, "video_frame_aspect",
                            lambda path: pytest.fail("audio was probed"))
        did = self._title(isolated_db, audio_filename="a.m4a")
        rows = self._rows(export_service.generate_ass_text(did, "zh"))
        assert 6 < max(map(len, rows)) <= 15
        bare = self._title(isolated_db)
        assert export_service.generate_ass_text(bare, "zh")
        # A video name whose file is gone does not fall back to the audio file.
        isolated_db.update_drama(did, source_video_filename="gone.mp4")
        assert export_service.generate_ass_text(did, "zh")
