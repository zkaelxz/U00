"""
services/novel_chapter_move_service.py -- move a saved novel chapter to another position: in
the raw-novel file, the chapter list and the title's lines together.

The lines of each chapter (novel_line_chapters_service) are moved as a block and keep their ids,
so their translations, notes and checked marks go with them; only their positions change. The
move is refused unless every saved chapter is found in the lines in order from the first line,
the file has no unsplit text, and the manifest matches the file byte for byte, because anything
less would have to guess where a chapter's lines end. The lines are saved first, from a history
snapshot, and put back if the file swap fails (the file is restored from the `.before-edit`
copies the rename also uses). A move is refused while an interrupted import still holds a retry
offset into the file, which a reorder would leave pointing at the wrong text.

Returns the numbers and booleans only; never a path. No FastAPI import.
"""
import filecmp
import hashlib
import json
import logging
import os
import shutil
import tempfile

import db
import novel_text
from core import line_from_row
from lib import file_lock
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import (drama_service, media_upload_service, novel_chapter_edit_service as edit,
                      novel_line_chapters_service as mapping, ownership_service)
from sources import chapter_manifest as manifest
from sources import pipeline, store

_NOT_ALIGNED = ("The chapters and the lines do not line up (a chapter's title line is missing or "
                "out of order, or a line was edited), so the chapters can't be moved safely. Restore "
                "the lines from the line history, or undo the edits, and try again.")
_STUCK = ("A chapter move on this title was interrupted and can't be finished or undone safely "
          "(its lines or text changed afterwards, or its record of the move is damaged). Restore the lines from the line history "
          "(\u201cbefore moving a chapter\u201d) and ask for help clearing the stopped move.")
JOURNAL_FILENAME = ".chapter-move.json"
_LOCK_FILENAME = ".chapter-move.lock"
_JOURNAL_KEYS = {"old_ids": list, "moved_ids": list, "old_sha256": str, "new_sha256": str,
                 "old_manifest": str, "new_manifest": str, "temp_files": list,
                 "narration_is_copy": bool}
_TIMED = ("These lines have their own times, so reordering chapters would put them out of step "
          "with the audio. Only untimed novel lines can be reordered.")


def move_chapter(drama_id: int, number: int, to_number: int) -> dict:
    ownership_service.require_drama(drama_id)
    with media_upload_service.folder_write(drama_id):
        if drama_service.job_running_for_drama(drama_id):
            raise ConflictError(edit._BUSY)
        with pipeline._page_lock(drama_id), _move_lock(drama_id):
            if not _recover(drama_id):
                raise ConflictError(_STUCK)
            return _move_locked(drama_id, number, to_number)


def _contiguous(chapters: list, sep: int) -> bool:
    end = 0
    for c in chapters:
        if c["unsplit"] or c["start"] != end:
            return False
        end = c["start"] + c["length"] + sep
    return True


def _new_order(count: int, number: int, to_number: int) -> list:
    order = list(range(1, count + 1))
    order.remove(number)
    order.insert(to_number - 1, number)
    return order


def _moved_lines(rows: list, starts: list, order: list) -> list:
    """The saved lines regrouped by chapter in `order`, renumbered from 0. Their times are their
    positions (start == idx, as the novel splitter writes them; _untimed refuses any others), so
    they are renumbered with them."""
    bounds = {n: (pos, starts[i + 1][2] if i + 1 < len(starts) else len(rows))
              for i, (n, _t, pos) in enumerate(starts)}
    out = []
    for n in order:
        first, end = bounds[n]
        for row in rows[first:end]:
            ln = line_from_row(row)
            ln.idx = len(out)
            ln.start, ln.end = float(ln.idx), float(ln.idx) + 1.0
            out.append(ln)
    return out


def _untimed(rows: list) -> bool:
    """True when every line's time is just its position, i.e. nothing was timed to audio."""
    return all(r.get("start") == float(r["idx"]) and r.get("end") == float(r["idx"]) + 1.0
               for r in rows)


def _lines_match_text(raw: str, chapters: list, rows: list, starts: list) -> bool:
    """True when each chapter's lines, joined, are exactly that chapter's saved text (ignoring
    white space, which the line splitter drops). Finding a title line is not enough: a body line
    equal to a later chapter's title would cut a chapter early."""
    def squeeze(text: str) -> str:
        return "".join(text.split())

    with open(raw, "rb") as src:
        for i, (n, _title, first) in enumerate(starts):
            end = starts[i + 1][2] if i + 1 < len(starts) else len(rows)
            c = chapters[n - 1]
            src.seek(c["start"])
            text = src.read(c["length"]).decode("utf-8", errors="replace")
            if squeeze(text) != "".join(squeeze(r.get("zh") or "") for r in rows[first:end]):
                return False
    return True


def _as_read(rows: list) -> list:
    """The lines as first read, for putting them back: without `orig`, so every field is
    written (a line whose values equal its `orig` would otherwise be skipped)."""
    out = []
    for r in rows:
        ln = line_from_row(r)
        ln.orig = None
        out.append(ln)
    return out


def _write_moved(raw: str, folder: str, chapters: list, order: list, sep: bytes):
    """Writes the reordered raw file and its manifest to temp files; returns their paths."""
    fd, tmp_raw = tempfile.mkstemp(prefix=".novel_", suffix=".part", dir=folder)
    fd2, tmp_man = tempfile.mkstemp(prefix=".chapters_", suffix=".part", dir=folder)
    os.close(fd2)
    try:
        rows, at = [], 0
        with open(raw, "rb") as src, os.fdopen(fd, "wb") as out:
            for k, n in enumerate(order):
                c = chapters[n - 1]
                if k:
                    out.write(sep)
                    at += len(sep)
                edit._copy_range(src, out, c["start"], c["length"])
                rows.append({**c, "start": at})
                at += c["length"]
            out.flush()
            os.fsync(out.fileno())
        with open(tmp_man, "w", encoding="utf-8") as f:
            f.write(manifest.serialise(at, rows))
    except BaseException:
        for tmp in (tmp_raw, tmp_man):
            if os.path.exists(tmp):
                os.remove(tmp)
        raise
    return tmp_raw, tmp_man


def _checked(drama_id: int, number: int, to_number: int):
    found = manifest.safe_file(drama_id, manifest.RAW_NOVEL_FILENAME)
    known = manifest.load(drama_id)
    if found is None or known is None or known["size"] != found[1].st_size:
        raise ConflictError("The saved chapters are not split, so they can't be moved.")
    chapters = known["chapters"]
    if not (1 <= number <= len(chapters) and 1 <= to_number <= len(chapters)):
        raise NotFoundError("No such saved chapter.")
    if number == to_number:
        raise InvalidInputError("The chapter is already there.")
    sep = pipeline._as_written("\n\n")
    if not _contiguous(chapters, len(sep)) or sum(
            c["length"] for c in chapters) + len(sep) * (len(chapters) - 1) != found[1].st_size:
        raise ConflictError("The saved text has parts outside the chapter list, so the chapters "
                            "can't be moved without changing it.")
    if store.has_text_retry_marker(drama_id):
        raise ConflictError("A chapter import for this title was interrupted and still has a retry "
                            "waiting, which would point at the wrong text after a move. Finish or "
                            "clear that retry first.")
    return found[0], chapters, sep


def _restore_manifest(drama_id: int, folder: str) -> None:
    path = manifest.manifest_path(drama_id)
    fd, tmp = tempfile.mkstemp(prefix=".chapters_", suffix=".part", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(path + edit.BACKUP_SUFFIX, tmp)
        edit._swap_in(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _roll_back(drama_id: int, raw: str, folder: str, raw_swapped: bool, manifest_removed: bool,
               lines_saved: bool, moved: list, rows: list) -> None:
    """Undoes whatever a failed move had done. Each step runs on its own, so a file that cannot
    be restored does not stop the lines being put back. Raises only when something is left
    half-done, saying which part."""
    stuck = []
    raw_back = True
    if raw_swapped:
        try:
            edit._restore_raw(raw, folder)
        except Exception:
            raw_back = False
            stuck.append("The saved text could not be put back in its old order; its earlier copy "
                         "is kept beside it as a .before-edit file.")
    # The old chapter list is only valid for the old text, so it is put back only once that is
    # back; without it the chapters read as one unsplit text, which is safe.
    if manifest_removed and raw_back:
        try:
            _restore_manifest(drama_id, folder)
        except Exception:
            stuck.append("The chapter list could not be put back; the chapters show as one "
                         "unsplit text until the text is imported again.")
    if lines_saved:
        try:
            db.save_lines(drama_id, _as_read(rows), expect_unchanged=moved)
        except Exception:
            stuck.append("The lines could not be put back in their old order. Restore them from "
                         "the line history (\u201cbefore moving a chapter\u201d).")
    if stuck:
        raise ConflictError("The move failed and was only partly undone. " + " ".join(stuck))


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _journal_path(drama_id: int) -> str:
    return os.path.join(db.DRAMAS_DIR, str(int(drama_id)), JOURNAL_FILENAME)


def _move_lock(drama_id: int):
    """Held from a move's first write to its journal's removal, and by recovery. The page lock
    is per process; this one is the operating system's, so a second copy of the app starting
    up waits for a live move instead of undoing it, and a dead holder never keeps it."""
    return file_lock.hold(os.path.join(db.DRAMAS_DIR, str(int(drama_id)), _LOCK_FILENAME))


def _read_journal(path: str):
    """The journal, or None when it can't be read or isn't shaped as this module writes it."""
    try:
        with open(path, encoding="utf-8") as f:
            journal = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(journal, dict) or any(
            not isinstance(journal.get(k), t) for k, t in _JOURNAL_KEYS.items()):
        return None
    return journal


def _write_durably(path: str, text: str) -> None:
    """Writes `text` to `path` through a temp file in the same folder, so a stop leaves the old
    file or the new one, never half of one."""
    fd, tmp = tempfile.mkstemp(prefix=".move_", suffix=".part", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_journal(drama_id: int, raw: str, rows: list, moved: list, tmp_raw: str,
                   tmp_man: str, narration_is_copy: bool) -> None:
    with open(manifest.manifest_path(drama_id), encoding="utf-8") as f:
        old_manifest = f.read()
    with open(tmp_man, encoding="utf-8") as f:
        new_manifest = f.read()
    _write_durably(_journal_path(drama_id), json.dumps({
        "old_ids": [r["id"] for r in rows], "moved_ids": [ln.id for ln in moved],
        "old_sha256": _sha256(raw), "new_sha256": _sha256(tmp_raw),
        "old_manifest": old_manifest, "new_manifest": new_manifest,
        "temp_files": [os.path.basename(tmp_raw), os.path.basename(tmp_man)],
        "narration_is_copy": narration_is_copy}))


def _in_order(rows: list, ids: list) -> list:
    """The lines put in the order of `ids` and renumbered: a move changes only positions, so
    this is all it takes to put the lines back."""
    by_id = {r["id"]: r for r in rows}
    out = []
    for i, lid in enumerate(ids):
        ln = line_from_row(by_id[lid])
        ln.idx, ln.start, ln.end = i, float(i), float(i) + 1.0
        out.append(ln)
    return out


def _recover(drama_id: int) -> bool:
    """Finishes or undoes a move a hard kill stopped, from its journal. The lines are saved
    before the text is swapped, so the text tells which side the move stopped on: the old text
    means the lines go back to the old order, the new text means only the chapter list (and the
    translation copy) is left to write. True when there was nothing to do or it was done; False
    (journal kept) when the lines or the text are in neither state, which needs a person."""
    path = _journal_path(drama_id)
    if not os.path.exists(path):
        return True
    journal = _read_journal(path)
    if journal is None:
        return False
    raw = manifest.raw_path(drama_id)
    folder = os.path.dirname(raw)
    man = manifest.manifest_path(drama_id)
    rows = db.load_lines(drama_id)
    ids = [r["id"] for r in rows]
    text = _sha256(raw) if os.path.isfile(raw) else None
    if text == journal["old_sha256"] and ids in (journal["old_ids"], journal["moved_ids"]):
        if ids != journal["old_ids"]:
            db.save_lines(drama_id, _in_order(rows, journal["old_ids"]),
                          expect_unchanged=[line_from_row(r) for r in rows])
        # Only missing, never stale: the list is removed before the text is swapped.
        if not os.path.exists(man):
            _write_durably(man, journal["old_manifest"])
    elif text == journal["new_sha256"] and ids == journal["moved_ids"]:
        if not os.path.exists(man):
            _write_durably(man, journal["new_manifest"])
        narration = os.path.join(folder, novel_text.NOVEL_SOURCE_FILENAME)
        if journal["narration_is_copy"] and not (
                os.path.isfile(narration) and filecmp.cmp(raw, narration, shallow=False)):
            edit._copy_to_translation_text(raw, narration, folder)
    else:
        return False
    for name in journal["temp_files"]:
        tmp = os.path.join(folder, os.path.basename(name))
        if os.path.exists(tmp):
            os.remove(tmp)
    os.remove(path)
    return True


def recover_interrupted_moves() -> list:
    """Run at start, before any request: finishes or undoes every chapter move a hard kill
    stopped. Returns the ids of the titles put right; one that can't be is logged and left
    for move_chapter to refuse. Waits for a move another copy of the app is running."""
    fixed = []
    try:
        names = os.listdir(db.DRAMAS_DIR)
    except OSError:
        return fixed
    for name in names:
        if not name.isdigit() or not os.path.exists(_journal_path(int(name))):
            continue
        try:
            with _move_lock(int(name)):
                # A move another copy of the app was running has finished while this waited.
                if not os.path.exists(_journal_path(int(name))):
                    continue
                if _recover(int(name)):
                    fixed.append(int(name))
                    continue
        except Exception:
            logging.getLogger(__name__).warning("Interrupted chapter move on title %s could not be "
                                                "put right", name, exc_info=True)
            continue
        logging.getLogger(__name__).warning("Interrupted chapter move on title %s left as it "
                                            "is: its lines or text changed since, or its "
                                            "journal is damaged", name)
    return sorted(fixed)


def _remove_journal(drama_id: int) -> None:
    if os.path.exists(_journal_path(drama_id)):
        os.remove(_journal_path(drama_id))


def _move_locked(drama_id: int, number: int, to_number: int) -> dict:
    raw, chapters, sep = _checked(drama_id, number, to_number)
    rows = db.load_lines(drama_id)
    starts = mapping.starts_from_manifest(drama_id, [mapping.norm(r.get("zh")) for r in rows])
    if ([n for n, _t, _p in starts] != list(range(1, len(chapters) + 1))
            or starts[0][2] != 0):
        raise ConflictError(_NOT_ALIGNED)
    if not _untimed(rows):
        raise ConflictError(_TIMED)
    if not _lines_match_text(raw, chapters, rows, starts):
        raise ConflictError(_NOT_ALIGNED)
    folder = os.path.dirname(raw)
    order = _new_order(len(chapters), number, to_number)
    originals = [line_from_row(r) for r in rows]
    moved = _moved_lines(rows, starts, order)
    narration = os.path.join(folder, novel_text.NOVEL_SOURCE_FILENAME)
    narration_is_copy = os.path.isfile(narration) and filecmp.cmp(raw, narration, shallow=False)

    tmp_raw, tmp_man = _write_moved(raw, folder, chapters, order, sep)
    lines_saved = raw_swapped = manifest_removed = False
    try:
        _write_journal(drama_id, raw, rows, moved, tmp_raw, tmp_man, narration_is_copy)
        db.save_line_history_snapshot(drama_id, originals, "before moving a chapter")
        db.save_lines(drama_id, moved, expect_unchanged=originals)
        lines_saved = True
        for path in (raw, manifest.manifest_path(drama_id)):
            shutil.copyfile(path, path + edit.BACKUP_SUFFIX)
        # A move keeps the file's size, which is what makes the old chapter list look valid, so
        # it is dropped first: a stop between the two swaps then reads as unsplit text, never
        # as the old chapters over the new text.
        os.remove(manifest.manifest_path(drama_id))
        manifest_removed = True
        edit._swap_in(tmp_raw, raw)
        raw_swapped = True
        edit._swap_in(tmp_man, manifest.manifest_path(drama_id))
    except BaseException:
        for tmp in (tmp_raw, tmp_man):
            if os.path.exists(tmp):
                os.remove(tmp)
        _roll_back(drama_id, raw, folder, raw_swapped, manifest_removed, lines_saved, moved, rows)
        _remove_journal(drama_id)
        raise
    translation_updated = narration_is_copy and edit._copy_to_translation_text(raw, narration, folder)
    _remove_journal(drama_id)
    return {"drama_id": drama_id, "number": number, "to_number": to_number,
            "lines_moved": len(moved), "translation_text_updated": bool(translation_updated),
            "translation_text_stale": os.path.isfile(narration) and not translation_updated}
