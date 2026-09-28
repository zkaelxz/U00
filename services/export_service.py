"""
services/export_service.py -- Export-stage services for one drama, shared
by the FastAPI /api/export routes and the Streamlit Export tab
(`tabs/workspace_tab.py`'s `with tab_export:` block, lines ~5655-5900).

Migration Slice 12 (read-only readiness summary), Slice 14 (subtitle text
generation, pure/no writes), Slice 15 (the three flagging actions), and
Slice 18 (EPUB export, novel-narration dramas only) are all here. Every
flagging function writes ONLY the flag/flag_note fields
(`db.save_lines(..., fields=("flag", "flag_note"))`) -- a field-scoped
write that can't clobber a concurrent edit to a line's text/timing/
speaker, the same discipline every other background-job write in this
app follows (see root CLAUDE.md's "A background job must not silently
overwrite another job's work"). What's still deliberately out of scope,
each its own separate slice: ASS export (needs the interactive per-drama
style state `_subtitle_style_fragment` builds in Streamlit, no API
contract for it yet) and audiobook/burned-in-video export (each its own
subprocess dependency, ffmpeg in particular).

No Streamlit or FastAPI import: plain functions, plain dicts/bytes in and
out, so a CLI or another service could call them too.
"""
import os
from typing import Optional

import auto_qc
import core as core_module
import db
import subtitle_formats
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                      NotFoundError, UnsupportedOperationError)

_EPUB_FIELDS = ("en", "zh")

_SUBTITLE_FORMATS = ("srt", "vtt")
_SUBTITLE_FIELDS = ("en", "zh", "bilingual")


def _load_drama_and_lines(drama_id: int):
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    lines = core_module.lines_from_rows(db.load_lines(drama_id))
    return drama, lines


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


def flag_overlapping_lines(drama_id: int) -> dict:
    """Flags every currently-overlapping, not-yet-flagged line for review
    (subtitle_formats.OVERLAP_FLAG) -- the same action as the Export
    tab's own "Flag overlapping lines for review" button. A line already
    flagged for some other reason is left alone, matching the tab's own
    `not ln.flag` check. Writes only if there's something new to flag.
    Raises NotFoundError for an unknown drama id. Returns
    {"flagged_count": int} -- 0 is not an error, just nothing to do."""
    _, lines = _load_drama_and_lines(drama_id)

    _, overlaps = subtitle_formats.clamp_overlaps(lines)
    next_start = {a.idx: b.start for a, b in zip(lines, lines[1:])}
    unflagged = [ln for ln in lines if ln.idx in overlaps and not ln.flag]
    for ln in unflagged:
        ln.flag = subtitle_formats.OVERLAP_FLAG
        ln.flag_note = subtitle_formats.overlap_note(ln, next_start[ln.idx])

    if unflagged:
        db.save_lines(drama_id, lines, fields=("flag", "flag_note"))
    return {"flagged_count": len(unflagged)}


def flag_dense_lines(drama_id: int) -> dict:
    """Flags every line too dense to read in its on-screen time
    (subtitle_formats.flag_dense_lines) -- the same action as the Export
    tab's own "Flag these for review" button under the dense-line
    warning. A line already flagged for some other reason is left alone.
    Raises NotFoundError for an unknown drama id. Returns
    {"flagged_count": int}."""
    _, lines = _load_drama_and_lines(drama_id)

    newly_flagged = subtitle_formats.flag_dense_lines(lines)
    if newly_flagged:
        db.save_lines(drama_id, lines, fields=("flag", "flag_note"))
    return {"flagged_count": newly_flagged}


def run_auto_qc_flagging(drama_id: int) -> dict:
    """Runs Auto QC's factual-detail check over this drama's lines and
    updates flags in place -- the same action as the Export tab's own
    "Flag these for review" button under the Auto QC warning
    (tabs/workspace_tab.py's `_run_auto_qc`, reused here rather than
    duplicated: same glossary-name-list/banned-terms inputs from
    db.list_glossary_terms/db.list_series_characters). Raises
    NotFoundError for an unknown drama id. Returns
    {"flagged", "cleared", "already_flagged", "checked"} -- see
    auto_qc.run_auto_qc's own docstring for exactly what each counts."""
    drama, lines = _load_drama_and_lines(drama_id)

    series_id = drama.get("series_id")
    glossary_terms = db.list_glossary_terms(series_id) if series_id else []
    names = auto_qc.build_name_list(
        glossary_terms, db.list_series_characters(series_id) if series_id else [])
    banned_terms = auto_qc.build_banned_terms(glossary_terms)

    result = auto_qc.run_auto_qc(lines, names, banned_terms)
    if result["flagged"] or result["cleared"]:
        db.save_lines(drama_id, lines, fields=("flag", "flag_note"))
    return result


def generate_epub(drama_id: int, field: str = "en") -> bytes:
    """Exports one novel-narration drama's lines as an .epub -- the same
    action as the Export tab's own "Generate EPUB" button
    (`epub_io.export_epub`). Read-only from the caller's point of view
    (returns bytes to serve as a download); internally it does write the
    .epub to the drama's own directory as `translated.epub`, same as the
    Streamlit tab already does, so a resolved [[IMG:...]] placeholder's
    `epub_images` cache stays in the usual place.

    field: "en" for the translation, "zh" for the raw source text.

    Raises NotFoundError for an unknown drama id, UnsupportedOperationError
    if the drama isn't in novel-narration mode (this only makes sense for
    novel content, not audio/video dramas), InvalidInputError for an
    unknown field, and DependencyUnavailableError if `ebooklib` isn't
    installed (`pip install ebooklib`)."""
    if field not in _EPUB_FIELDS:
        raise InvalidInputError(f"Unknown EPUB field {field!r}.")

    drama, lines = _load_drama_and_lines(drama_id)
    if drama.get("content_mode") != "novel_narration":
        raise UnsupportedOperationError(
            f"Drama {drama_id} isn't in novel-narration mode -- EPUB export only "
            "applies to novel content.")

    try:
        import epub_io
    except ImportError as exc:
        raise DependencyUnavailableError(
            "The `ebooklib` package isn't installed. Run `pip install ebooklib` "
            "to enable EPUB export.") from exc

    ddir = db.drama_dir(drama_id)
    out_path = os.path.join(ddir, "translated.epub")
    title = drama.get("title_en") or drama.get("title_zh") or "Untitled"
    epub_io.export_epub(lines, title, drama.get("author", ""), out_path, field=field,
                        images_dir=os.path.join(ddir, "epub_images"))

    with open(out_path, "rb") as f:
        return f.read()
