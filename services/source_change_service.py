"""
services/source_change_service.py -- Review's answers to lines flagged
`source_changed` (their source text changed after they were translated; see
source_change.py): which lines they are, "Keep English" for them, and
"Retranslate these", a normal translate run over exactly those lines.

No FastAPI import: plain dicts in and out. Nothing returns line text.
"""
import db
import source_change
from services import translate_run_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_LINES = 10000


def _flagged(ln) -> bool:
    return ln.flag == source_change.SOURCE_CHANGED_FLAG


def _lines_by_id(drama_id: int) -> dict:
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return {ln.id: ln for ln in db.load_line_objects(drama_id)}


def _id_list(line_ids) -> list:
    if (not isinstance(line_ids, (list, tuple)) or not line_ids
            or any(isinstance(i, bool) or not isinstance(i, int) for i in line_ids)):
        raise InvalidInputError("line_ids must be a non-empty list of integers.")
    if len(line_ids) > MAX_LINES:
        raise InvalidInputError(f"Too many lines (max {MAX_LINES}).")
    return list(dict.fromkeys(line_ids))


def get_source_changed(drama_id: int) -> dict:
    """The lines flagged `source_changed`, in line order: {count, line_ids}."""
    ids = [ln.id for ln in _lines_by_id(drama_id).values() if _flagged(ln)]
    return {"count": len(ids), "line_ids": ids}


def keep_english(drama_id: int, line_ids) -> dict:
    """"Keep English": clears the `source_changed` flag on the given lines,
    leaving their text and English as they are. An id that isn't this
    drama's or isn't flagged `source_changed` any more is skipped. Each
    clear is a compare-and-set on the line as just read, so a source edit
    landing in between keeps its flag. Returns {cleared, line_ids, skipped_ids}."""
    wanted = _id_list(line_ids)
    by_id = _lines_by_id(drama_id)
    items = [(lid, {"flag": None, "flag_note": ""},
              {"flag": source_change.SOURCE_CHANGED_FLAG, "zh": by_id[lid].zh,
               "en": by_id[lid].en})
             for lid in wanted if lid in by_id and _flagged(by_id[lid])]
    missed = set(db.update_lines_fields_if_many(drama_id, items))
    cleared = [lid for lid, _v, _e in items if lid not in missed]
    return {"cleared": len(cleared), "line_ids": cleared,
            "skipped_ids": [lid for lid in wanted if lid not in set(cleared)]}


def start_retranslate(drama_id: int, line_ids, **run_options) -> dict:
    """"Retranslate these": translate_run_service.start_translate_run, with
    the caller's run options, over exactly the given lines still flagged
    `source_changed`. A line whose English is edited while the run works
    keeps the edit (own_lines_only, with the English read here as
    expected_en). ConflictError when none of them is flagged any more."""
    wanted = _id_list(line_ids)
    by_id = _lines_by_id(drama_id)
    if not set(wanted) <= set(by_id):
        raise InvalidInputError("line_ids must be this title's line ids.")
    ids = sorted(lid for lid in wanted if _flagged(by_id[lid]))
    if not ids:
        raise ConflictError('None of these lines is marked "source changed" any more.',
                            details={"reason": "stale_preview"})
    return translate_run_service.start_translate_run(
        drama_id, line_ids=ids, force_retranslate=True, own_lines_only=True,
        expected_en={lid: by_id[lid].en or "" for lid in ids}, **run_options)
