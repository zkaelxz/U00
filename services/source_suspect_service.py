"""Review's "Check source text": flags lines whose transcription looks off
(source_suspect.py has the rules) and lists them with the line each could merge into.

Computed from the stored lines alone and written as flag/flag_note only, by
line id, as a compare-and-set: a line whose flag, text or timing changed
since it was read keeps that edit and isn't counted. A line that
already carries another flag keeps it; this check clears only its own flag once
it no longer applies. Merging goes through the existing restructure merge.
"""
import core as core_module
import db
from services.service_errors import NotFoundError
from source_suspect import SUSPECT_FLAG, find_suspects


def _load(drama_id: int):
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return drama, core_module.lines_from_rows(db.load_lines(drama_id))


def check_source_text(drama_id: int) -> dict:
    """Returns {"flagged_count": lines newly flagged, "cleared_count": own flags
    removed because the text no longer looks off}."""
    drama, lines = _load(drama_id)
    suspects = find_suspects(lines, drama.get("source_language") or "zh")
    flagged, cleared = [], []
    for ln in lines:
        if ln.id in suspects and not ln.flag:
            ln.flag, ln.flag_note = SUSPECT_FLAG, suspects[ln.id]
            flagged.append(ln.id)
        elif ln.flag == SUSPECT_FLAG and ln.id not in suspects:
            ln.flag, ln.flag_note = None, ""
            cleared.append(ln.id)
    kept = set()
    if flagged or cleared:
        kept = db.save_lines(drama_id, lines, fields=("flag", "flag_note"), only_if_unchanged=True,
                             guard_fields=("zh", "start", "end", "speaker"))
    return {"flagged_count": sum(1 for i in flagged if i not in kept),
            "cleared_count": sum(1 for i in cleared if i not in kept)}


def list_suspects(drama_id: int) -> dict:
    """The lines currently carrying the flag. `next_line_id` is the line after
    it in the title, the one "Merge with next" would join (None for the last)."""
    _, lines = _load(drama_id)
    items = [{"line_id": ln.id, "idx": ln.idx, "zh": ln.zh, "note": ln.flag_note,
              "next_line_id": lines[pos + 1].id if pos + 1 < len(lines) else None,
              "next_zh": lines[pos + 1].zh if pos + 1 < len(lines) else ""}
             for pos, ln in enumerate(lines) if ln.flag == SUSPECT_FLAG]
    return {"count": len(items), "items": items}
