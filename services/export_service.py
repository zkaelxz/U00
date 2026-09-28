"""
services/export_service.py -- Export-stage services for one drama, shared
by the FastAPI /api/export routes and the Streamlit Export tab
(`tabs/workspace_tab.py`'s `with tab_export:` block, lines ~5655-5900).

Migration Slice 12 (read-only readiness summary) and Migration Slice 14
(subtitle text generation) are both here. Neither writes to the
database, flags a line, or writes a file to disk -- Slice 14's
generate_subtitle_text() returns plain text the caller can serve as a
download; it never touches the filesystem itself. What's still
deliberately out of scope, each its own separate slice: ASS export
(needs the interactive per-drama style state `_subtitle_style_fragment`
builds in Streamlit, no API contract for it yet), EPUB/audiobook/
burned-in-video export (each its own subprocess/library dependency), and
the flagging actions on the readiness page (a write, not a read).

No Streamlit or FastAPI import: plain functions, plain dicts in, plain
values out, so a CLI or another service could call them too.
"""
from typing import Optional

import auto_qc
import core as core_module
import db
import subtitle_formats
from services.service_errors import InvalidInputError, NotFoundError

_SUBTITLE_FORMATS = ("srt", "vtt")
_SUBTITLE_FIELDS = ("en", "zh", "bilingual")


def get_export_readiness(drama_id: int) -> dict:
    """Read-only export-readiness summary for one drama: line/translation
    counts plus counts of the same issues the Export tab's own checks
    surface (timing overlaps, Auto QC mismatches, reading-speed-dense
    lines). Raises NotFoundError for an unknown drama id. A drama with no
    lines yet returns all-zero/false counts rather than an error."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    rows = db.load_lines(drama_id)
    lines = core_module.lines_from_rows(rows)

    total_lines = len(lines)
    zh_filled = sum(1 for ln in lines if ln.zh.strip())
    en_filled = sum(1 for ln in lines if ln.en.strip())
    fully_translated = total_lines > 0 and en_filled == total_lines

    _, overlaps = subtitle_formats.clamp_overlaps(lines)
    qc_issues = auto_qc.find_issues(lines)
    dense = subtitle_formats.dense_lines(lines)

    return {
        "drama_id": drama_id,
        "total_lines": total_lines,
        "zh_filled": zh_filled,
        "en_filled": en_filled,
        "fully_translated": fully_translated,
        "test_mode_output": drama.get("translation_engine") == "test_offline",
        "overlap_count": len(overlaps),
        "auto_qc_issue_count": len(qc_issues),
        "dense_line_count": len(dense),
    }


def generate_subtitle_text(drama_id: int, fmt: str, field: str,
                           include_notes: bool = False,
                           wrap_chars_en: Optional[int] = None,
                           wrap_chars_source: Optional[int] = None) -> str:
    """Generates SRT or VTT subtitle text for one drama -- pure and
    read-only: never writes to the database, never flags a line, never
    writes a file to disk. The caller decides what to do with the
    returned text (e.g. serve it as a download).

    fmt: "srt" or "vtt". field: "en", "zh", or "bilingual" (both formats
    support all three -- see subtitle_formats.lines_to_vtt/core.
    lines_to_srt/lines_to_bilingual_srt). Overlapping cues are trimmed
    first (subtitle_formats.clamp_overlaps), matching what
    tabs/workspace_tab.py's own Export stage does before any download,
    so the two paths never disagree about what "the export" contains.

    include_notes folds in this drama's saved translation notes
    (db.list_translation_notes), appended inline the same way the
    Streamlit tab's own "Include translation notes inline" checkbox
    does -- ASS's separate-note-line option is not modeled here (see
    this module's own docstring for why ASS itself is out of scope).
    wrap_chars_en/wrap_chars_source optionally cap characters per line
    (subtitle_formats.wrap_lines); None on either side leaves that
    language unwrapped.

    Raises NotFoundError for an unknown drama id, InvalidInputError for
    an unknown fmt/field.
    """
    if fmt not in _SUBTITLE_FORMATS:
        raise InvalidInputError(f"Unknown subtitle format {fmt!r}.")
    if field not in _SUBTITLE_FIELDS:
        raise InvalidInputError(f"Unknown subtitle field {field!r}.")

    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    rows = db.load_lines(drama_id)
    lines = core_module.lines_from_rows(rows)
    export_lines, _ = subtitle_formats.clamp_overlaps(lines)

    wrap_chars = None
    if wrap_chars_en is not None or wrap_chars_source is not None:
        wrap_chars = {"en": wrap_chars_en, "zh": wrap_chars_source}

    notes_by_idx = None
    if include_notes:
        from translation_guide import group_notes_by_line
        notes = db.list_translation_notes(drama_id)
        notes_by_idx = group_notes_by_line(notes) if notes else None

    if fmt == "vtt":
        return subtitle_formats.lines_to_vtt(export_lines, field, notes_by_idx, wrap_chars)

    wrapped = subtitle_formats.wrap_lines(export_lines, wrap_chars)
    if field == "bilingual":
        return core_module.lines_to_bilingual_srt(wrapped, notes_by_idx=notes_by_idx)
    return core_module.lines_to_srt(wrapped, field, notes_by_idx=notes_by_idx)
