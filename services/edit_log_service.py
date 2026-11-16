"""The edit log: records source-text corrections and counts corrections (English and
source) per engine, model, prompt version, flag and mark."""

import contextlib
import logging
from collections import Counter

import db
import raw_transcript
from services.review_lines_service import SHAKY_BELOW

log = logging.getLogger(__name__)

PERSON = "person"
COMPARE = "compare"
RETRANSCRIBE = "retranscribe"
RETRANSCRIBE_MANY = "retranscribe_many"
NAME_FIX = "name_fix"

_NONE = "(none)"


def _heard_by(raw, ln, before_zh):
    """The transcription that heard `before_zh`, only while it is still that run's text:
    a line already corrected once is a person's text, not the engine's."""
    if not raw or raw_transcript.original_text_for_line(raw, ln) != before_zh:
        return None, None
    return raw.get("backend") or None, raw.get("model") or None


def record_source_edits(drama_id: int, changes, path: str) -> int:
    """Logs source-text changes already saved. changes: [(line as it was before the
    write, new zh)]. Never raises: the edit is saved, and losing a log row must not
    turn it into an error. Returns how many rows were stored."""
    changes = [(ln, after) for ln, after in changes if (ln.zh or "") != (after or "")]
    if not changes:
        return 0
    try:
        try:
            raw = raw_transcript.load_latest(db.drama_dir(drama_id))
        except (OSError, ValueError):
            raw = None
        rows = []
        for ln, after in changes:
            before = ln.zh or ""
            engine, model = _heard_by(raw, ln, before)
            rows.append({"line_id": ln.id, "path": path, "before_zh": before,
                         "after_zh": after or "", "engine": engine, "model": model,
                         "confidence": ln.confidence, "flag": ln.flag, "mark": ln.mark})
        return db.record_source_edits(drama_id, rows)
    except Exception:
        log.exception("Could not log source edits for drama %s", drama_id)
        return 0


def _counts(rows, key) -> dict:
    return dict(Counter((r.get(key) or _NONE) for r in rows).most_common())


def _confidence_band(value) -> str:
    if value is None:
        return _NONE
    return "low" if value < SHAKY_BELOW else "not low"


def _flag_rates(rows, flagged_now: set) -> dict:
    """How often a flagged line was corrected against an unflagged one. A line counts as
    flagged when it is flagged now or a correction found it flagged (the correction
    cleared it); lines whose flag was cleared some other way count as unflagged."""
    corrected = {r["line_id"] for r in rows if r.get("line_id") is not None}
    corrected_flagged = {r["line_id"] for r in rows
                         if r.get("line_id") is not None and r.get("flag")}
    flagged = flagged_now | corrected_flagged
    return {"flagged_lines": len(flagged),
            "flagged_corrected": len(corrected & flagged),
            "unflagged_corrected": len(corrected - flagged)}


def _current_lines():
    """(line count, ids of lines flagged now) over the whole library."""
    with contextlib.closing(db.get_conn()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
        flagged = {r[0] for r in conn.execute(
            "SELECT id FROM lines WHERE flag IS NOT NULL AND flag != ''")}
    return total, flagged


def corrections_report() -> dict:
    """Read-only counts over the whole library. English corrections come from
    edit_samples; source corrections are a person's only, with machine fixes counted
    by path apart from them."""
    english = db.list_edit_samples(limit=-1)
    source_all = db.list_source_edits(limit=-1)
    source = [r for r in source_all if r.get("path") == PERSON]
    total_lines, flagged_now = _current_lines()
    return {"line_count": total_lines,
           "english": {"corrections": len(english),
                       "linked_to_a_line": sum(r.get("line_id") is not None for r in english),
                       "from_deleted_titles": sum(r.get("drama_id") is None for r in english),
                       **{f"by_{k}": _counts(english, k)
                          for k in ("engine", "model", "prompt_version", "flag", "mark")},
                       "flag_rates": _flag_rates(english, flagged_now)},
           "source": {"corrections": len(source),
                      "from_deleted_titles": sum(r.get("drama_id") is None for r in source),
                      **{f"by_{k}": _counts(source, k) for k in ("engine", "model", "flag", "mark")},
                      "by_confidence": dict(Counter(_confidence_band(r.get("confidence"))
                                                    for r in source).most_common()),
                      "flag_rates": _flag_rates(source, flagged_now),
                      "machine_fixes_by_path": _counts(
                          [r for r in source_all if r.get("path") != PERSON], "path")}}
