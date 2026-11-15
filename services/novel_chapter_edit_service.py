"""
services/novel_chapter_edit_service.py -- rename a saved novel chapter: its title in the
raw-novel file and the chapter list, and the matching title line in the title's lines.

A chapter is saved as `<title>\\n\\n<text>` and found again in the lines by that title line
(novel_line_chapters_service), so a rename has to change all three together or the chapter
stops matching. The raw file and its manifest are rewritten through temp files and swapped in
under the title's page lock, with the previous pair kept beside them as `.before-edit`.
The manifest is only trusted while it matches the file's size (sources/chapter_manifest.py),
so a manifest that is missing or out of date is refused, never guessed at.
A failed temp write changes nothing, and a failed swap restores the raw file and the line,
so the chapter list never turns unsplit; a rename is refused while an interrupted import
still holds a retry offset into the file.

Returns the number, the new title and booleans only; never a path. No FastAPI import.
"""
import filecmp
import os
import shutil
import tempfile

import db
import novel_text
from core import line_from_row
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import (drama_service, extension_novel_service, media_upload_service,
                      novel_line_chapters_service, ownership_service)
from sources import chapter_manifest as manifest
from sources import store
from sources import pipeline

_BUSY = "A job is running for this title. Wait for it to finish or cancel it."
_NOT_TITLED = ("This chapter's text does not start with its title, so it can't be renamed "
               "without changing the text. Edit the saved text instead.")
_CHUNK = 1 << 20
BACKUP_SUFFIX = ".before-edit"


def _copy_range(src, dst, start: int, length: int) -> None:
    src.seek(start)
    left = length
    while left > 0:
        data = src.read(min(_CHUNK, left))
        if not data:
            raise ConflictError("The saved text changed while it was being edited; try again.")
        dst.write(data)
        left -= len(data)


def _swap_in(tmp: str, dest: str) -> None:
    os.replace(tmp, dest)


def rename_chapter(drama_id: int, number: int, title) -> dict:
    ownership_service.require_drama(drama_id)
    new_title = extension_novel_service.one_line_name(title, manifest._MAX_TITLE)
    if not new_title:
        raise InvalidInputError("Give the chapter a name.")
    with media_upload_service.folder_write(drama_id):
        if drama_service.job_running_for_drama(drama_id):
            raise ConflictError(_BUSY)
        with pipeline._page_lock(drama_id):
            return _rename_locked(drama_id, number, new_title)


def _restore_raw(raw: str, folder: str) -> None:
    """Puts the backup of the raw file back, through a temp file so a failure here
    cannot leave a half-written file."""
    fd, tmp = tempfile.mkstemp(prefix=".novel_", suffix=".part", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(raw + BACKUP_SUFFIX, tmp)
        _swap_in(tmp, raw)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _set_line_title(drama_id: int, pos: int, lines: list, old: str, title: str) -> bool:
    """Writes `title` into line `pos` only while it still holds `old` (the lines are
    read just before, and a person editing in Review may have changed it); field-scoped
    so nothing else on the line is touched."""
    row = lines[pos]
    if novel_line_chapters_service.norm(row.get("zh")) != novel_line_chapters_service.norm(old):
        return False
    ln = line_from_row(row)
    ln.zh = title
    # Checked against the value read above inside the save, so an edit made in Review since
    # then is kept (the id comes back unwritten) instead of overwritten.
    return not db.save_lines(drama_id, [ln], fields=("zh",), only_if_unchanged=True)


def _copy_to_translation_text(raw: str, narration: str, folder: str) -> bool:
    """Best effort: the rename itself is done, so a failure here only leaves the translation
    text with the old title, as before the rename. No temp file is left behind."""
    fd, tmp = tempfile.mkstemp(prefix=".novel_", suffix=".part", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(raw, tmp)
        _swap_in(tmp, narration)
        return True
    except OSError:
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_renamed(raw: str, folder: str, size: int, chapters: list, number: int, first: bytes,
                   old_line: str, new_title: str, drama_id: int):
    """Writes the renamed raw file and its manifest to temp files (nothing is swapped in
    yet) and returns (raw temp, manifest temp). The temps are removed if writing fails."""
    block = chapters[number - 1]
    new_first = new_title.encode("utf-8")
    delta = len(new_first) - len(first)
    fd, tmp_raw = tempfile.mkstemp(prefix=".novel_", suffix=".part", dir=folder)
    fd2, tmp_man = tempfile.mkstemp(prefix=".chapters_", suffix=".part", dir=folder)
    os.close(fd2)
    try:
        with open(raw, "rb") as src, os.fdopen(fd, "wb") as out:
            _copy_range(src, out, 0, block["start"])
            out.write(new_first)
            _copy_range(src, out, block["start"] + len(first), size - block["start"] - len(first))
            out.flush()
            os.fsync(out.fileno())
        rows = []
        for n, c in enumerate(chapters, 1):
            row = dict(c)
            if n == number:
                row["title"] = new_title[:manifest._MAX_TITLE]
                row["length"] += delta
                row["chars"] += len(new_title) - len(old_line)
            elif n > number:
                row["start"] += delta
            rows.append(row)
        with open(tmp_man, "w", encoding="utf-8") as f:
            f.write(manifest.serialise(size + delta, rows))
    except BaseException:
        for tmp in (tmp_raw, tmp_man):
            if os.path.exists(tmp):
                os.remove(tmp)
        raise
    return tmp_raw, tmp_man


def _rename_locked(drama_id: int, number: int, new_title: str) -> dict:
    found = manifest.safe_file(drama_id, manifest.RAW_NOVEL_FILENAME)
    known = manifest.load(drama_id)
    if found is None or known is None or known["size"] != found[1].st_size:
        raise ConflictError("The saved chapters are not split, so one can't be renamed.")
    chapters = known["chapters"]
    if not 1 <= number <= len(chapters):
        raise NotFoundError("No such saved chapter.")
    block = chapters[number - 1]
    if block["unsplit"] or not block["title"]:
        raise ConflictError("This part of the text has no chapter title to rename.")
    raw, size = found[0], found[1].st_size

    with open(raw, "rb") as f:
        f.seek(block["start"])
        head = f.read(min(block["length"], manifest._MAX_TITLE * 4 + 8))
    line_end = head.find(b"\n")
    first = head if line_end < 0 else head[:line_end]
    if line_end < 0 and block["length"] > len(head):
        raise ConflictError(_NOT_TITLED)
    first = first.rstrip(b"\r")
    old_line = first.decode("utf-8", errors="replace")
    if extension_novel_service.one_line_name(old_line) != extension_novel_service.one_line_name(block["title"]):
        raise ConflictError(_NOT_TITLED)
    if extension_novel_service.one_line_name(old_line) == new_title:
        return {"drama_id": drama_id, "number": number, "title": new_title,
                "line_updated": False, "translation_text_updated": False}
    if store.has_text_retry_marker(drama_id):
        raise ConflictError("A chapter import for this title was interrupted and still has a "
                            "retry waiting, which would point at the wrong text after a rename. "
                            "Finish or clear that retry first.")

    folder = os.path.dirname(raw)
    narration = os.path.join(folder, novel_text.NOVEL_SOURCE_FILENAME)
    # A translation text that is a verbatim copy of the raw chapters is kept in step; a
    # hand-edited one is left alone, since a rewrite would lose the edits.
    narration_is_copy = os.path.isfile(narration) and filecmp.cmp(raw, narration, shallow=False)

    # The renamed files are written to temps before the line changes, so a failure there
    # (disk full, or the text changing underneath) leaves the line and the files as they were.
    tmp_raw, tmp_man = _write_renamed(raw, folder, size, chapters, number, first, old_line,
                                      new_title, drama_id)
    title_pos = None
    line_updated = False
    raw_swapped = False
    try:
        # The line goes before the swap: nothing the reader sees has changed yet if its save
        # fails, and it is put back if the swap fails.
        lines = db.load_lines(drama_id)
        mapped = novel_line_chapters_service.starts_from_manifest(
            drama_id, [novel_line_chapters_service.norm(r.get("zh")) for r in lines])
        title_pos = next((pos for n, _t, pos in mapped if n == number), None)
        line_updated = (title_pos is not None
                        and _set_line_title(drama_id, title_pos, lines, old_line, new_title))
        for path in (raw, manifest.manifest_path(drama_id)):
            shutil.copyfile(path, path + BACKUP_SUFFIX)
        _swap_in(tmp_raw, raw)
        raw_swapped = True
        _swap_in(tmp_man, manifest.manifest_path(drama_id))
    except BaseException:
        for tmp in (tmp_raw, tmp_man):
            if os.path.exists(tmp):
                os.remove(tmp)
        # The old manifest is still in place; the old raw file has to match it again, or
        # the list would read as unsplit.
        if raw_swapped:
            _restore_raw(raw, folder)
        if line_updated and title_pos is not None:
            try:
                _set_line_title(drama_id, title_pos, db.load_lines(drama_id), new_title, old_line)
            except Exception as put_back:
                raise ConflictError(
                    f"The rename failed and its line could not be put back. Set that line's text "
                    f"back to \u201c{old_line}\u201d in Review.") from put_back
        raise

    translation_updated = False
    if narration_is_copy:
        translation_updated = _copy_to_translation_text(raw, narration, folder)
    return {"drama_id": drama_id, "number": number, "title": new_title,
            "line_updated": line_updated, "translation_text_updated": translation_updated}
