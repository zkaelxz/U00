"""
services/novel_chapters_service.py -- read-only view of the chapters saved
in a drama's raw novel (`raw_novel_context.txt`): the list, a bounded slice
of one chapter's text for the preview, and which chapters are already in
the text used for translation (`novel_narration_source.txt`).

Chapter boundaries come from the manifest the import pipeline writes
(sources/chapter_manifest.py). It is trusted only while it still matches
the file's size; otherwise (an old title, an upload, a hand edit) the file
is one "Unsplit text" block, so the panel never fails on odd data.

Bounds: a list page is at most MAX_PAGE rows, a text slice at most
MAX_SLICE_CHARS characters, and the file is only ever read in ranges, so
a many-megabyte novel is never returned or loaded whole. No path, URL or
stored filename is returned.

"In the translation text" is a content check: the chapter's first and last
characters both appear in the translation text. "Copy saved raw chapters
into the translation text" copies the file verbatim, so a copied chapter
always matches; text the owner pasted and later edited may not.

No FastAPI import.
"""
import os

import db
from dub_narration import NOVEL_SOURCE_FILENAME
from services.service_errors import InvalidInputError, NotFoundError
from sources import chapter_manifest as manifest

MAX_PAGE = 200
DEFAULT_PAGE = 100
MAX_SLICE_CHARS = 50_000
DEFAULT_SLICE_CHARS = 20_000
UNSPLIT_TITLE = "Unsplit text"
EARLIER_TITLE = "Earlier text (not split)"
_MATCH_CHARS = 200


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _blocks(drama_id: int, size: int) -> tuple:
    """(blocks, split): the file's pieces in order, each with its byte
    range, as the manifest gives them while it matches the file, else one
    whole-file block."""
    known = manifest.load(drama_id)
    if known and known["size"] == size and known["chapters"]:
        end = 0
        ok = True
        for c in known["chapters"]:
            ok = ok and c["start"] >= end and c["start"] + c["length"] <= size
            end = c["start"] + c["length"]
        if ok:
            blocks = []
            for c in known["chapters"]:
                blocks.append({**c, "title": c["title"] or (EARLIER_TITLE if c["unsplit"] else "")})
            return blocks, True
    return [{"title": UNSPLIT_TITLE, "source": "", "imported_at": "", "start": 0,
             "length": size, "chars": None, "unsplit": True}], False


def _translation_text(drama_id: int) -> str:
    path = os.path.join(db.DRAMAS_DIR, str(drama_id), NOVEL_SOURCE_FILENAME)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _in_translation(f, block: dict, translation: str) -> bool:
    if not translation or block["length"] <= 0:
        return False
    head = manifest.slice_text(f, block["start"], block["length"], 0, _MATCH_CHARS).strip()
    tail = manifest.tail_text(f, block["start"], block["length"], _MATCH_CHARS).strip()
    return bool(head) and head in translation and tail in translation


def _file_size(drama_id: int):
    path = manifest.raw_path(drama_id)
    return os.path.getsize(path) if os.path.isfile(path) else None


def list_chapters(drama_id: int, offset: int = 0, limit: int = DEFAULT_PAGE) -> dict:
    _require_drama(drama_id)
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise InvalidInputError(f"offset must be 0 or more and limit 1 to {MAX_PAGE}.")
    size = _file_size(drama_id)
    translation = _translation_text(drama_id)
    out = {"drama_id": drama_id, "present": size is not None, "size_bytes": size or 0,
           "split": False, "total": 0, "char_count": 0, "in_translation": 0,
           "translation_chars": len(translation), "offset": offset, "limit": limit,
           "chapters": []}
    if size is None:
        return out
    blocks, out["split"] = _blocks(drama_id, size)
    with open(manifest.raw_path(drama_id), "rb") as f:
        for b in blocks:
            if b["chars"] is None:
                b["chars"] = manifest.count_chars(f, b["start"], b["length"])
            b["in_translation"] = _in_translation(f, b, translation)
    out["total"] = len(blocks)
    out["char_count"] = sum(b["chars"] for b in blocks)
    out["in_translation"] = sum(1 for b in blocks if b["in_translation"])
    out["chapters"] = [
        {"number": n, "title": b["title"], "chars": b["chars"], "source": b["source"],
         "imported_at": b["imported_at"], "unsplit": b["unsplit"],
         "in_translation": b["in_translation"]}
        for n, b in enumerate(blocks, 1) if offset < n <= offset + limit]
    return out


def read_chapter(drama_id: int, number: int, offset: int = 0,
                 limit: int = DEFAULT_SLICE_CHARS) -> dict:
    """`limit` characters of chapter `number` (1-based) from character
    `offset`; next_offset is None once the chapter's end was returned."""
    _require_drama(drama_id)
    if offset < 0 or not 1 <= limit <= MAX_SLICE_CHARS:
        raise InvalidInputError(f"offset must be 0 or more and limit 1 to {MAX_SLICE_CHARS}.")
    size = _file_size(drama_id)
    blocks, _split = _blocks(drama_id, size) if size is not None else ([], False)
    if not 1 <= number <= len(blocks):
        raise NotFoundError("No such saved chapter.")
    b = blocks[number - 1]
    translation = _translation_text(drama_id)
    with open(manifest.raw_path(drama_id), "rb") as f:
        chars = b["chars"] if b["chars"] is not None else manifest.count_chars(
            f, b["start"], b["length"])
        text = manifest.slice_text(f, b["start"], b["length"], offset, limit)
        inside = _in_translation(f, b, translation)
    end = offset + len(text)
    return {"drama_id": drama_id, "number": number, "title": b["title"], "source": b["source"],
            "imported_at": b["imported_at"], "unsplit": b["unsplit"], "chars": chars,
            "in_translation": inside, "offset": offset, "text": text,
            "next_offset": end if end < chars else None}
