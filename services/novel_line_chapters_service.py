"""
services/novel_line_chapters_service.py -- which lines of a novel title belong to which
saved chapter, derived from the lines and the raw-novel chapter manifest.

Lines are cut paragraph by paragraph from the text that was built from the saved
chapters, and each chapter is saved with its title as its first paragraph, so a
chapter starts at the line whose text is that title. Nothing is stored: the mapping is
recomputed from the current lines, so editing, merging or re-splitting lines can never
leave a stale chapter mark behind. When the manifest is missing or does not cover the
text (an upload or an old title), chapter headings in the lines are used instead.

Read-only; returns counts and titles, never text or paths. No FastAPI import.
"""
import bisect
import re

import db
import novel_text
from services import novel_chapters_service, ownership_service


def norm(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def starts_from_manifest(drama_id: int, norm_lines: list) -> list:
    """[(chapter number, title, first line position)] for each saved chapter whose
    title line was found, in order. A chapter whose title line is not found (an
    untitled one, or its title line was edited) is skipped without moving the scan, so
    one missing title does not lose the chapters after it."""
    raw = novel_chapters_service._raw_file(drama_id)
    if raw is None:
        return []
    blocks, split = novel_chapters_service.file_blocks(drama_id, raw[2])
    if not split:
        return []
    positions = {}
    for pos, text in enumerate(norm_lines):
        positions.setdefault(text, []).append(pos)
    starts, cursor = [], 0
    for number, block in enumerate(blocks, 1):
        title = norm(block["title"])
        found = positions.get(title) if title else None
        if not found:
            continue
        at = bisect.bisect_left(found, cursor)
        if at == len(found):
            continue
        starts.append((number, block["title"], found[at]))
        cursor = found[at] + 1
    return starts


def _starts_from_headings(rows: list) -> list:
    """A heading line starts a chapter only when prose follows it before the next
    heading, so a contents page does not become one chapter per title."""
    starts, number, pending = [], 0, None
    for pos, row in enumerate(rows):
        text = (row.get("zh") or "").strip()
        if novel_text.is_heading_text(text):
            if pending is None:
                pending = (pos, text)
        elif text and pending is not None:
            number += 1
            starts.append((number, pending[1], pending[0]))
            pending = None
    return starts


def line_chapters(drama_id: int) -> dict:
    ownership_service.require_drama(drama_id)
    rows = db.load_lines(drama_id)
    starts = starts_from_manifest(drama_id, [norm(r.get("zh")) for r in rows])
    source = "saved"
    if not starts:
        starts, source = _starts_from_headings(rows), "headings"
    chapters = []
    for n, (number, title, first) in enumerate(starts):
        end = starts[n + 1][2] if n + 1 < len(starts) else len(rows)
        part = rows[first:end]
        chapters.append({
            "number": number, "title": title, "first_idx": rows[first]["idx"],
            "line_count": len(part),
            "translated": sum(1 for r in part if (r.get("en") or "").strip()),
            "reviewed": sum(1 for r in part if r.get("reviewed"))})
    return {"drama_id": drama_id, "total_lines": len(rows),
            "source": source if chapters else "", "chapters": chapters}
