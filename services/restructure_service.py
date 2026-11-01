"""
services/restructure_service.py -- STRUCTURAL line
changes for one drama (add, delete, merge, split) and version-history restore,
plus the shared write guard the re-segmentation (services/resegmentation_service.py)
and re-split (services/resplit_service.py) modules build on.

Correctness rules:
- Every change loads the drama's lines FRESH from the database, checks the
  client's `expected_line_ids` (the drama's line ids, in order, as the
  client last saw them) against them -- any difference is a 409 with
  nothing written -- takes a history snapshot of the current lines, THEN
  writes.
- The write is one `db.save_lines(..., fields=None)` call over that fresh
  list (a single SQLite transaction). Lines are matched by permanent id:
  a kept line keeps its id, so its flag/flag_note/speaker/notes/emotion
  stay on it; a merged-away line's notes/emotion are re-pointed onto the
  line it merged into (`merged_ids`); a deleted/split-away line's notes
  and emotion are deleted with its row (never left orphaned).
- Refused (409) while any job is running on the drama, so a job's
  field-scoped result can't land on a line whose text just changed shape.

Atomicity: a per-drama lock is held across load -> check -> snapshot -> save
for every write in this module and in the re-segmentation and re-split
modules. Line edits (the line PATCH and notes routes) and other processes
don't take that lock, so the save itself re-checks, inside its
transaction, that the drama's lines are still exactly the ones the write
was computed from (`save_lines(expect_unchanged=...)`) and refuses with
nothing written if not. A write that removes lines and must not lose a
note or emotion tag added meanwhile also passes the ids it saw
(`known_note_ids`, `known_emotion_ids`).
The snapshot is its own transaction, so a refused or failed save leaves
only an extra snapshot. An undo's emotion-tag check still runs outside the
save's transaction.

No FastAPI import: plain dicts in and out. Messages never echo
line text, keys or paths.
"""
import contextlib
import dataclasses
import hashlib
import json
import threading
from typing import Optional

import core as core_module
import line_alignment
from segment_splitting import encode_line_words, line_word_index, line_words, span_words
import db
import resegment
from services import drama_service, translate_service
from services.lines_service import _number, _text
from services.review_lines_service import line_dict
from services.review_records_service import get_line_history_snapshot, lines_losing_notes
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services.ownership_service import require_drama as _require_drama

MAX_LINE_TEXT_CHARS = 2000
# expected_zh/expected_en are compared with the stored line, never written.
MAX_EXPECTED_TEXT_CHARS = 20000
MAX_SPEAKER_CHARS = 100
MAX_MERGE_LINES = 50

_locks_guard = threading.Lock()
_drama_locks = {}


def _drama_lock(drama_id: int) -> threading.Lock:
    with _locks_guard:
        return _drama_locks.setdefault(drama_id, threading.Lock())


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------

def _refuse_if_job_running(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this title -- wait for it "
                            "to finish or cancel it before restructuring lines.")


def _id_list(name: str, value) -> list:
    if not isinstance(value, (list, tuple)) or any(
            isinstance(i, bool) or not isinstance(i, int) for i in value):
        raise InvalidInputError(f"{name} must be a list of integer line ids.")
    if len(set(value)) != len(value):
        raise InvalidInputError(f"{name} must not repeat an id.")
    return list(value)


def _check_expected(current, expected_line_ids):
    if [ln.id for ln in current] != list(expected_line_ids):
        raise ConflictError("This title's lines changed since you loaded them -- reload and "
                            "try again.")


def lines_fingerprint(lines) -> str:
    """Hash of every field a history restore overwrites (and the ids). Handed
    back after a write so an undo can refuse when anything restorable was
    edited since, which the id-list check alone can't see."""
    rows = [(ln.id, ln.start, ln.end, ln.zh, ln.en, ln.speaker, bool(ln.speaker_manual),
             bool(ln.sfx), ln.flag, ln.flag_note or "", ln.lang, ln.dub_filename) for ln in lines]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _commit(drama_id: int, current, new, label: str, known_note_ids=None,
            known_emotion_ids=None) -> int:
    """Snapshot `current` (fresh from the DB), then full-sync `new`, refused
    (ConflictError, nothing saved) if the lines no longer match `current`
    or, with `known_note_ids` / `known_emotion_ids`, a line being removed gained a
    note or an emotion tag.
    Caller holds the drama lock and has already checked expected ids.
    Returns the snapshot's `history_id`, so the caller can offer an undo."""
    history_id = db.save_line_history_snapshot(drama_id, current, label)
    for i, ln in enumerate(new):
        ln.idx = i
    translate_service.save_synced(drama_id, new, expect_unchanged=current,
                                  known_note_ids=known_note_ids,
                                  known_emotion_ids=known_emotion_ids)
    return history_id


def undo_handle(drama_id: int, history_id: int) -> dict:
    """What a client needs to undo the write that just finished. Caller holds
    the drama lock, so the fingerprint is of exactly what that write left."""
    return {"history_id": history_id,
            "lines_fingerprint": lines_fingerprint(db.load_line_objects(drama_id))}


@contextlib.contextmanager
def exclusive_write(drama_id: int):
    """The structural-write guard (drama exists, no job running, drama lock
    held) for a writer that saves only some fields and so can't use
    structural_write's full sync."""
    _require_drama(drama_id)
    _refuse_if_job_running(drama_id)
    with _drama_lock(drama_id):
        yield


def note_ids(drama_id: int) -> set:
    return {n["id"] for n in db.list_translation_notes(drama_id)}


def structural_write(drama_id: int, expected_line_ids, label: str, build,
                     with_words: bool = False, keep_new_notes: bool = False):
    """load fresh -> check ids -> build(current) -> snapshot -> save, under
    the drama lock. build returns (new_lines, result_line_objects).
    with_words loads the lines' stored word timings for a build that cuts or joins text.
    keep_new_notes refuses the save if a line it removes gained a note or
    emotion tag after the lines were loaded."""
    _require_drama(drama_id)
    expected_line_ids = _id_list("expected_line_ids", expected_line_ids)
    _refuse_if_job_running(drama_id)
    with _drama_lock(drama_id):
        # Read before the lines, so a note added after either read is unknown.
        known = note_ids(drama_id) if keep_new_notes else None
        known_emotions = db.line_emotion_ids(drama_id) if keep_new_notes else None
        current = db.load_line_objects(drama_id, with_words=with_words)
        _check_expected(current, expected_line_ids)
        # build works on copies so the snapshot sees the untouched lines
        work = [dataclasses.replace(ln, orig=dict(ln.orig), merged_ids=[]) for ln in current]
        new_lines, touched = build(work)
        history_id = _commit(drama_id, current, new_lines, label, known_note_ids=known,
                             known_emotion_ids=known_emotions)
        handle = undo_handle(drama_id, history_id)
    return {"line_ids": [ln.id for ln in new_lines],
            "lines": [line_dict(ln) for ln in touched], **handle}


def _index_of(lines, line_id) -> int:
    for i, ln in enumerate(lines):
        if ln.id == line_id:
            return i
    raise NotFoundError(f"No line with id {line_id} in this title.")


# ---------------------------------------------------------------------------
# Add / delete / merge / split
# ---------------------------------------------------------------------------

def add_line(drama_id: int, expected_line_ids, *, after_line_id: Optional[int] = None,
             start=None, end=None, zh: str = "", en: str = "",
             speaker: Optional[str] = None) -> dict:
    """Inserts a new line after `after_line_id` (None = at the start)."""
    start, end = _number("start", start), _number("end", end)
    if end <= start:
        raise InvalidInputError("end must be after start.")
    zh = _text("zh", zh, MAX_LINE_TEXT_CHARS)
    en = _text("en", en, MAX_LINE_TEXT_CHARS)
    if speaker is not None:
        speaker = _text("speaker", speaker, MAX_SPEAKER_CHARS).strip() or None

    def build(lines):
        pos = 0 if after_line_id is None else _index_of(lines, after_line_id) + 1
        new = core_module.Line(idx=pos, start=start, end=end, zh=zh, en=en, speaker=speaker,
                               speaker_manual=speaker is not None)
        return lines[:pos] + [new] + lines[pos:], [new]
    return structural_write(drama_id, expected_line_ids, "before add line", build)


def delete_line(drama_id: int, line_id: int, expected_line_ids, confirm: bool = False) -> dict:
    """Deletes one line (its notes and emotion tag with it). Needs
    confirm=True, like the other destructive deletes."""
    if confirm is not True:
        raise InvalidInputError("Deleting a line needs confirm=true.")

    def build(lines):
        i = _index_of(lines, line_id)
        return lines[:i] + lines[i + 1:], []
    return structural_write(drama_id, expected_line_ids, "before delete line", build)


def merge_lines(drama_id: int, line_ids, expected_line_ids) -> dict:
    """Merges 2+ ADJACENT lines (given in order) into the first: text joined
    as core.merge_adjacent_short_lines joins it, end = last line's end, the
    first line keeps its id/speaker; its flag, else the first merged line's
    flag, is kept. Its lang stays only when every merged line shares it. The others' notes/emotions move onto it (the first
    line's own win on a conflict)."""
    line_ids = _id_list("line_ids", line_ids)
    if not 2 <= len(line_ids) <= MAX_MERGE_LINES:
        raise InvalidInputError(f"line_ids must name 2 to {MAX_MERGE_LINES} lines.")

    def build(lines):
        first = _index_of(lines, line_ids[0])
        if [ln.id for ln in lines[first:first + len(line_ids)]] != line_ids:
            for lid in line_ids:
                _index_of(lines, lid)  # 404 for an unknown id first
            raise InvalidInputError("line_ids must be adjacent lines, in order.")
        head, rest = lines[first], lines[first + 1:first + len(line_ids)]
        # Joined only when every line's words are valid and in time order; else dropped.
        words = line_words(head)
        for ln in rest:
            more = line_words(ln)
            words = (words + more if words and more and more[0]["start"] >= words[-1]["start"]
                     else None)
        for ln in rest:
            head.zh = line_alignment._join_source(head.zh, ln.zh)
            head.en = (head.en.rstrip() + " " + ln.en.strip()).strip()
            if len(head.zh) > MAX_LINE_TEXT_CHARS or len(head.en) > MAX_LINE_TEXT_CHARS:
                raise InvalidInputError(
                    f"The merged line would pass {MAX_LINE_TEXT_CHARS} characters.")
            if not head.flag and ln.flag:
                head.flag, head.flag_note = ln.flag, ln.flag_note
            head.merged_ids = list(head.merged_ids) + [ln.id]
            if ln.lang != head.lang:
                head.lang = None
        head.end = max(head.end, rest[-1].end)
        head.word_timings = encode_line_words(head.zh, words) if words else None
        return lines[:first + 1] + lines[first + len(line_ids):], [head]
    return structural_write(drama_id, expected_line_ids, "before merge", build, with_words=True)


def split_line(drama_id: int, line_id: int, expected_line_ids, *, at_char: int,
               expected_zh: str, at_time=None, en_at_char: Optional[int] = None,
               expected_en: Optional[str] = None) -> dict:
    """Splits one line's source text at character offset `at_char`. The
    first piece keeps the line's id (so its flag, notes and emotion stay
    on it); the second is a new line with the same speaker/sfx/lang and no
    flag. Its translation stays whole on the first piece unless
    `en_at_char` splits it too. The cut time is `at_time` (strictly inside
    the line), else the second piece's first word start when the line has
    valid stored word timings and the cut falls between words, else
    proportional to piece length. `expected_zh` must equal
    the line's current text (409 otherwise), since the offset refers to it;
    `expected_en`, when sent, does the same for `en_at_char`."""
    for name, v in (("at_char", at_char), ("en_at_char", en_at_char)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise InvalidInputError(f"{name} must be an integer.")
    _text("expected_zh", expected_zh, MAX_EXPECTED_TEXT_CHARS)
    if expected_en is not None:
        _text("expected_en", expected_en, MAX_EXPECTED_TEXT_CHARS)
    if at_time is not None:
        at_time = _number("at_time", at_time)

    def build(lines):
        i = _index_of(lines, line_id)
        ln = lines[i]
        if ln.zh != expected_zh or (expected_en is not None and (ln.en or "") != expected_en):
            raise ConflictError("This line's text changed since you loaded it.")
        if not 0 < at_char < len(ln.zh):
            raise InvalidInputError("at_char must fall inside the line's text.")
        if en_at_char is not None and not 0 < en_at_char < len(ln.en):
            raise InvalidInputError("en_at_char must fall inside the line's translation.")
        pieces = [ln.zh[:at_char], ln.zh[at_char:]]
        if at_time is None:
            cut = resegment.split_times(ln, pieces)[0]
        elif ln.start < at_time < ln.end:
            cut = at_time
        else:
            raise InvalidInputError("at_time must fall strictly inside the line's timing.")
        en_first, en_second = ln.en, ""
        if en_at_char is not None:
            en_first, en_second = ln.en[:en_at_char].rstrip(), ln.en[en_at_char:].strip()
        index = line_word_index(ln)
        words = ([span_words(index, 0, at_char, pieces[0]),
                  span_words(index, at_char, len(ln.zh), pieces[1])]
                 if index is not None else [None, None])
        second = core_module.Line(idx=0, start=cut, end=ln.end, zh=pieces[1], en=en_second,
                                  speaker=ln.speaker, speaker_manual=ln.speaker_manual,
                                  sfx=ln.sfx, lang=ln.lang, word_timings=words[1])
        ln.zh, ln.en, ln.end, ln.word_timings = pieces[0], en_first, cut, words[0]
        return lines[:i + 1] + [second] + lines[i + 1:], [ln, second]
    return structural_write(drama_id, expected_line_ids, "before split", build, with_words=True)


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

def restore_version(drama_id: int, history_id: int, expected_line_ids,
                    expected_fingerprint: Optional[str] = None,
                    expected_notes_removed: Optional[int] = None) -> dict:
    """Restores a history snapshot over the current lines, after taking a
    "before restore" snapshot (so the restore itself can be undone). Lines
    are matched by permanent id (core.restore_saved_lines / adopt_ids):
    a line whose id still exists keeps its notes/emotion; flag,
    flag note and SFX mark come from the snapshot (or, for a snapshot saved
    before those were recorded, stay as the line has them now); a line
    merged/deleted since gets a fresh id and nothing is reattached by
    position. Refused while a job
    runs on the drama. No confirm field.

    `expected_fingerprint` (an undo of the write that returned it) makes the
    restore refuse, with nothing written, when any restorable field of the
    current lines differs from what that write left: ids alone would let a
    restore overwrite a text edit saved since. It also refuses when a line the
    restore would remove has a note or emotion tag, which the full sync would
    delete (a reading position is only unlinked, so it doesn't block).

    `expected_notes_removed` (a Records restore) is how many lines with a
    note or emotion tag the user was warned the restore removes: more than
    that now (one added since the warning) refuses with nothing written."""
    get_line_history_snapshot(drama_id, history_id)  # 404 unless it's this drama's
    # the raw rows: the read above returns dub_filename as a bare basename
    rows = db.get_line_history_snapshot(history_id)
    if rows is None:
        raise NotFoundError(f"No history snapshot {history_id} for title {drama_id}.")

    def build(lines):
        # Line edits don't take the drama lock; one saved after this check is
        # caught by the save's own check (_commit).
        if expected_fingerprint is not None and lines_fingerprint(lines) != expected_fingerprint:
            raise ConflictError("The lines were edited since that change -- restore from "
                                "Records, Line history instead.")
        # The fingerprint covers line fields only; a line the restore removes
        # takes its notes and emotion tag with it. `reason` lets a client tell
        # this apart from an edit, since a Records restore would delete them.
        if expected_fingerprint is not None and lines_losing_notes(drama_id, rows, lines):
            raise ConflictError("A line this undo would remove has a note or emotion tag "
                                "saved since -- nothing was changed.",
                                details={"reason": "notes_on_removed_lines"})
        if (expected_notes_removed is not None
                and len(lines_losing_notes(drama_id, rows, lines)) > expected_notes_removed):
            raise ConflictError("A line this restore would remove has a note or emotion tag "
                                "saved since -- nothing was changed.",
                                details={"reason": "notes_on_removed_lines"})
        return core_module.restore_saved_lines(rows, lines), []
    checks_notes = expected_fingerprint is not None or expected_notes_removed is not None
    out = structural_write(drama_id, expected_line_ids, "before restore", build,
                           keep_new_notes=checks_notes)
    return {"history_id": history_id, "line_ids": out["line_ids"]}

