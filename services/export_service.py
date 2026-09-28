"""
services/export_service.py -- Migration Slice 12: the read-only "export
readiness" summary for one drama's Export stage, shared by the (later)
FastAPI /api/export route and the Streamlit Export tab
(`tabs/workspace_tab.py`'s `with tab_export:` block, lines ~5655-5774).

Scope decision (from the task handoff): this is a summary only -- line
and translation counts, and issue counts from the same read-only checks
the Streamlit tab already runs (`subtitle_formats.clamp_overlaps`,
`auto_qc.find_issues`, `subtitle_formats.dense_lines`). It never flags a
line, never writes to the database, and never generates a subtitle file.
Those all mutate state or produce a file and are out of scope for this
slice.

No Streamlit or FastAPI import: plain function, plain dict in, plain
dict out, so a CLI or another service could call it too.
"""

import auto_qc
import core as core_module
import db
import subtitle_formats
from services.service_errors import NotFoundError


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
