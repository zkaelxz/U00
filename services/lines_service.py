"""
services/lines_service.py -- the Review stage's per-line WRITES (migration
slice 43), keyed by permanent `Line.id`: Save edits, Dismiss flag,
find-and-replace Apply, translation-memory Accept, translation-note
add/delete -- never through a browser-session line list.

The rule this replaces: `db.save_lines(..., fields=None)` is a FULL SYNC --
called from a stale list it resurrects lines another writer merged away and
deletes ones just added. Every write here is field-scoped
(`fields=(...)` is always a tuple), so it only UPDATEs the named columns on
rows that still exist; it can never insert or delete a line.

Concurrency: a caller may send `expected` -- the old values it saw -- and a
mismatch with the current database value raises ConflictError (nothing is
written). When `expected` is sent, the compare and the write are ONE
conditional UPDATE (`db.update_line_fields_if`), so a change landing between
a read and the write can't be overwritten.

Explicitly out of scope: merge/split/delete lines, restore original text,
LLM tools, bulk modes other than setting lines' language. No FastAPI
import: plain dicts in and out.
Error messages never echo user text, and no path or key is returned.
"""
import math

import bulk_translate
import core as core_module
import db
import timing_drift
import translation_guide
from services import timing_check_service, translate_service
from services.review_lines_service import line_dict
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_LINE_TEXT_CHARS = 2000
MAX_SPEAKER_CHARS = 100
MAX_TERM_CHARS = 500
MAX_NOTE_CHARS = 2000
MAX_MATCHES = 1000
MAX_LANG_LINES = 10000
_EXPECTABLE = ("start", "end", "zh", "en", "speaker", "sfx", "lang")


def load(drama_id: int, line_id: int):
    """(drama, all_lines, the line with this id). NotFoundError for an
    unknown drama, or a line id that isn't this drama's (merged away,
    never existed, or another drama's -- all the same 404)."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    lines = db.load_line_objects(drama_id)
    for ln in lines:
        if ln.id == line_id:
            return drama, lines, ln
    raise NotFoundError(f"No line with id {line_id} in this title.")


def _reload_dict(drama_id: int, line_id: int) -> dict:
    for ln in db.load_line_objects(drama_id):
        if ln.id == line_id:
            return line_dict(ln)
    raise NotFoundError(f"No line with id {line_id} in this title.")


def _number(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidInputError(f"{name} must be a number.")
    if value < 0:
        raise InvalidInputError(f"{name} must not be negative.")
    return float(value)


def _text(name: str, value, cap: int) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be text.")
    if len(value) > cap:
        raise InvalidInputError(f"{name} is too long (max {cap} characters).")
    return value


def _same(field: str, current, wanted) -> bool:
    if field in ("start", "end"):
        return (isinstance(wanted, (int, float)) and not isinstance(wanted, bool)
                and abs(float(current) - float(wanted)) < 1e-6)
    if field == "sfx":
        return bool(current) == bool(wanted)
    if field == "speaker":
        return (current or "") == (wanted or "")
    return (current or "") == (wanted or "")


def patch_line(drama_id: int, line_id: int, *, start=None, end=None, zh=None, en=None,
               speaker=None, sfx=None, lang=None, expected=None) -> dict:
    """Applies only the fields passed (None = leave alone; speaker "" clears
    it; lang "" sets the title's language) and writes exactly those columns.
    Editing `en` on a flagged line clears its flag/flag_note (editing
    addresses it); changing the speaker sets `speaker_manual`. `expected` maps field -> the
    old value the client saw; any mismatch is a 409 with nothing written.
    A changed `en` also records an edit sample and translation memory, as
    Save edits does. Returns the line as stored afterwards."""
    passed = {k: v for k, v in (("start", start), ("end", end), ("zh", zh), ("en", en),
                                ("speaker", speaker), ("sfx", sfx)) if v is not None}
    if lang is not None:
        passed["lang"] = core_module.normalize_line_lang(lang)
    if not passed:
        raise InvalidInputError("Pass at least one field to change.")
    if "start" in passed:
        passed["start"] = _number("start", passed["start"])
    if "end" in passed:
        passed["end"] = _number("end", passed["end"])
    if "zh" in passed:
        _text("zh", passed["zh"], MAX_LINE_TEXT_CHARS)
    if "en" in passed:
        _text("en", passed["en"], MAX_LINE_TEXT_CHARS)
    if "speaker" in passed:
        passed["speaker"] = _text("speaker", passed["speaker"], MAX_SPEAKER_CHARS).strip()
    if "sfx" in passed and not isinstance(passed["sfx"], bool):
        raise InvalidInputError("sfx must be true or false.")
    if expected is not None:
        if not isinstance(expected, dict):
            raise InvalidInputError("expected must be an object.")
        for k in expected:
            if k not in _EXPECTABLE:
                raise InvalidInputError("expected may only name start, end, zh, en, speaker, sfx or lang.")

    drama, _, ln = load(drama_id, line_id)

    if expected:
        stale = [k for k, v in expected.items() if not _same(k, getattr(ln, k), v)]
        if stale:
            raise ConflictError("This line changed since you loaded it.",
                                details={"fields": sorted(stale)})

    new_start = passed.get("start", ln.start)
    new_end = passed.get("end", ln.end)
    if ("start" in passed or "end" in passed) and new_end <= new_start:
        raise InvalidInputError("end must be after start.")

    before_en = ln.en or ""
    fields = []
    for k in ("start", "end", "zh", "en", "sfx", "lang"):
        if k in passed:
            setattr(ln, k, passed[k])
            fields.append(k)
    if "speaker" in passed:
        new_speaker = passed["speaker"] or None
        fields.append("speaker")
        if new_speaker != ln.speaker:
            ln.speaker = new_speaker
            ln.speaker_manual = True
            fields.append("speaker_manual")
    en_changed = "en" in passed and passed["en"].strip() != before_en.strip()
    if en_changed and ln.flag:
        ln.flag, ln.flag_note = None, ""
        fields += ["flag", "flag_note"]

    if expected:
        values = {f: db.line_value(ln, f) for f in fields}
        if not db.update_line_fields_if(drama_id, line_id, values, expected):
            # Changed (or removed) between the read above and this write.
            _, _, fresh = load(drama_id, line_id)
            stale = [k for k, v in expected.items() if not _same(k, getattr(fresh, k), v)]
            raise ConflictError("This line changed since you loaded it.",
                                details={"fields": sorted(stale or expected)})
    else:
        db.save_lines(drama_id, [ln], fields=tuple(fields))

    # Filling in a blank line's source text opens a new untranslated line too.
    if en_changed or "zh" in passed:
        translate_service.sync_translation_status(drama_id)
    if en_changed:
        if before_en and ln.en:
            db.record_edit_sample(drama_id, ln.zh, before_en, ln.en)
        if drama.get("series_id") and ln.en.strip():
            db.record_translation_memory(drama["series_id"], ln.zh, ln.en)
    return _reload_dict(drama_id, line_id)


def set_lines_lang(drama_id: int, lang, *, line_ids=None, speaker=None) -> dict:
    """Sets the spoken language of the named lines, or of every line of one
    speaker (exactly one of the two), writing only `lang`. None or "" sets
    the title's language. An id that isn't this drama's line (merged away,
    never existed) is skipped, not an error, as find-and-replace does.
    Returns {updated, line_ids, skipped_ids}."""
    lang = core_module.normalize_line_lang(lang)
    if (line_ids is None) == (speaker is None):
        raise InvalidInputError("Pass either line_ids or speaker.")
    if line_ids is not None:
        if not isinstance(line_ids, (list, tuple)) or any(
                isinstance(i, bool) or not isinstance(i, int) for i in line_ids):
            raise InvalidInputError("line_ids must be a list of integers.")
        if not line_ids:
            raise InvalidInputError("line_ids must not be empty.")
        if len(line_ids) > MAX_LANG_LINES:
            raise InvalidInputError(f"Too many lines (max {MAX_LANG_LINES}).")
    else:
        speaker = _text("speaker", speaker, MAX_SPEAKER_CHARS).strip()
        if not speaker:
            raise InvalidInputError("speaker must not be empty.")
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    lines = db.load_line_objects(drama_id)
    if line_ids is not None:
        by_id = {ln.id: ln for ln in lines}
        wanted = list(dict.fromkeys(line_ids))
        targets = [by_id[i] for i in wanted if i in by_id]
        skipped = [i for i in wanted if i not in by_id]
    else:
        targets = [ln for ln in lines if (ln.speaker or "") == speaker]
        skipped = []
    changed = [ln for ln in targets if ln.lang != lang]
    for ln in changed:
        ln.lang = lang
    if changed:
        db.save_lines(drama_id, changed, fields=("lang",))
    return {"updated": len(changed), "line_ids": [ln.id for ln in targets],
            "skipped_ids": skipped}


def dismiss_flag(drama_id: int, line_id: int) -> dict:
    """Clears a line's flag and flag note (only those two columns).
    Idempotent: an unflagged line is returned unchanged."""
    _, _, ln = load(drama_id, line_id)
    was_timing_drift = ln.flag == timing_drift.TIMING_DRIFT_FLAG
    ln.flag, ln.flag_note = None, ""
    db.save_lines(drama_id, [ln], fields=("flag", "flag_note"))
    if was_timing_drift:
        # The flag is re-derived on every check, so only a remembered dismissal keeps it away.
        try:
            timing_check_service.note_dismissed(drama_id, line_id)
        except Exception as exc:
            # The flag is already cleared; a sidecar that can't be written must not turn this into a 500.
            import applog
            applog.get_logger().warning(f"timing dismissal not remembered for drama {drama_id}: "
                                        f"{type(exc).__name__}")
    return _reload_dict(drama_id, line_id)


def apply_find_replace(drama_id: int, matches) -> dict:
    """Applies a previewed find-and-replace. Each match is
    {id, old_text, new_text}; a line whose current translation no longer
    equals old_text (edited since Preview) -- or that no longer exists --
    is skipped as stale instead of being overwritten. Writes only `en`,
    then corrects translation memory. Returns
    {applied, stale, applied_ids, stale_ids}."""
    if not isinstance(matches, (list, tuple)):
        raise InvalidInputError("matches must be a list.")
    if len(matches) > MAX_MATCHES:
        raise InvalidInputError(f"Too many matches (max {MAX_MATCHES}).")
    wanted = {}
    for m in matches:
        if not isinstance(m, dict) or not isinstance(m.get("id"), int) or isinstance(m.get("id"), bool):
            raise InvalidInputError("Each match needs an integer id.")
        _text("old_text", m.get("old_text"), MAX_LINE_TEXT_CHARS)
        _text("new_text", m.get("new_text"), MAX_LINE_TEXT_CHARS)
        if m["id"] in wanted:
            raise InvalidInputError("Each line id may appear only once.")
        wanted[m["id"]] = (m["old_text"], m["new_text"])

    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    by_id = {ln.id: ln for ln in db.load_line_objects(drama_id)}
    changed, applied_ids, stale_ids, pairs = [], [], [], []
    for lid, (old_text, new_text) in wanted.items():
        ln = by_id.get(lid)
        if ln is None or ln.en != old_text:
            stale_ids.append(lid)
            continue
        ln.en = new_text
        changed.append(ln)
        applied_ids.append(lid)
        pairs.append((old_text, new_text))
    if changed:
        db.save_lines(drama_id, changed, fields=("en",))
        translate_service.sync_translation_status(drama_id)
        if drama.get("series_id"):
            for old_text, new_text in pairs:
                db.update_translation_memory_after_replace(drama["series_id"], old_text, new_text)
    return {"applied": len(applied_ids), "stale": len(stale_ids),
            "applied_ids": applied_ids, "stale_ids": stale_ids}


def accept_tm_suggestion(drama_id: int, line_id: int, entry_id: int, expected_en: str) -> dict:
    """Sets the line's translation to a translation-memory entry's text
    (writes only `en`) and counts the use. The entry must belong to this
    drama's series -- another series' entry is the same 404 as a missing one.
    `expected_en` is the English the caller saw: the write is one
    compare-and-set, so a line edited since is a 409 with nothing written."""
    if not isinstance(expected_en, str):
        raise InvalidInputError("expected_en must be text.")
    drama, _, ln = load(drama_id, line_id)
    series_id = drama.get("series_id")
    entry = next((e for e in (db.list_translation_memory(series_id) if series_id else [])
                  if e["id"] == entry_id), None)
    if entry is None:
        raise NotFoundError(f"No translation-memory entry with id {entry_id} for this title.")
    if not db.update_line_fields_if(drama_id, line_id, {"en": entry["translation"]},
                                    {"en": expected_en}):
        raise ConflictError("This line changed since you loaded it.",
                            details={"fields": ["en"]})
    db.bump_translation_memory_use(entry["id"])
    translate_service.sync_translation_status(drama_id)
    return _reload_dict(drama_id, line_id)


def add_note(drama_id: int, line_id: int, term: str, note_type: str, note: str) -> dict:
    """Adds (or, for the same line+term, updates) a translation note on a
    line of this drama."""
    term = _text("term", term, MAX_TERM_CHARS).strip()
    note = _text("note", note, MAX_NOTE_CHARS).strip()
    if not term or not note:
        raise InvalidInputError("term and note must not be empty.")
    if note_type not in translation_guide.NOTE_TYPES:
        raise InvalidInputError(f"note_type must be one of {sorted(translation_guide.NOTE_TYPES)}.")
    _, _, ln = load(drama_id, line_id)
    db.save_translation_notes(drama_id, [{"line_id": ln.id, "line_idx": ln.idx, "term": term,
                                          "note_type": note_type, "note": note}])
    for n in db.list_translation_notes(drama_id):
        if n["line_id"] == ln.id and n["term"] == term:
            return {k: n[k] for k in ("id", "line_id", "line_idx", "term", "note_type", "note")}
    raise NotFoundError("The note could not be found after saving.")


def delete_note(drama_id: int, note_id: int) -> dict:
    """Deletes one translation note. `db.delete_translation_note` takes only
    an id, so ownership is checked here: another drama's note is a 404."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    if not any(n["id"] == note_id for n in db.list_translation_notes(drama_id)):
        raise NotFoundError(f"No note with id {note_id} in this title.")
    db.delete_translation_note(note_id)
    return {"deleted": True, "note_id": note_id}
