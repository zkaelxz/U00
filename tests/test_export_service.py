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

from core import Line, lines_from_rows
from services import export_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                      NotFoundError, UnsupportedOperationError)


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


class TestFlagDenseLines:
    def test_flags_a_dense_line(self, isolated_db):
        did = _drama(isolated_db)
        dense_text = "word " * 60  # far more than fits in 1 second on screen
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="", en=dense_text)])
        result = export_service.flag_dense_lines(did)
        assert result["flagged_count"] == 1

    def test_no_dense_lines_is_a_zero_count(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=5.0, zh="", en="Hi")])
        assert export_service.flag_dense_lines(did) == {"flagged_count": 0}

    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            export_service.flag_dense_lines(999999)


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
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "epub_io":
                raise ImportError("simulated missing ebooklib")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        did = self._novel_drama_with_lines(isolated_db)
        with pytest.raises(DependencyUnavailableError):
            export_service.generate_epub(did)


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
        clamped, _ = sf.clamp_overlaps(lines)
        return sf.lines_to_ass(clamped, style, field, title="D", **kw)

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

    def test_overlaps_clamped_like_srt(self, isolated_db):
        did = _ass_drama(isolated_db, [
            Line(idx=0, start=0.0, end=3.0, zh="a", en="One"),
            Line(idx=1, start=2.0, end=4.0, zh="b", en="Two"),
        ])
        text = export_service.generate_ass_text(did)
        assert "Dialogue: 0,0:00:00.00,0:00:02.00" in text
        assert "0:00:03.00" not in text

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
