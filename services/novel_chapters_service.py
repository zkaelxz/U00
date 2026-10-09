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

Files are opened only through chapter_manifest.safe_file, so a link
planted in the drama folder reads as "not present" instead of leaking
another file. Per-file results (character counts, the translation text,
chapter matches) are cached on (path, size, mtime) so paging through a big
title doesn't redo the work, and only the rows of the requested page are
checked.

"In the translation text" is a content check: the chapter's first and last
characters both appear in the translation text. "Copy saved raw chapters
into the translation text" copies the file verbatim, so a copied chapter
always matches; text the owner pasted and later edited may not.

No FastAPI import.
"""
import threading

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
_CACHE_ENTRIES = 4096
_CHECKPOINT_MIN_OFFSET = 20_000   # below this a slice is cheap to decode directly
_TEXT_CACHE_ENTRIES = 2   # up to MAX_TEXT_CHARS each

_cache_lock = threading.Lock()
_index_cache: dict = {}
_match_cache: dict = {}
_text_cache: dict = {}


def _remember(cache: dict, key, value, limit: int) -> None:
    with _cache_lock:
        if len(cache) >= limit:
            cache.pop(next(iter(cache)))
        cache[key] = value


def _file_key(found):
    real, st = found
    return real, st.st_size, st.st_mtime_ns


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


def _translation(drama_id: int):
    """(cache key, text) of the translation text; (None, "") when absent.
    Read at most once per file version."""
    found = manifest.safe_file(drama_id, NOVEL_SOURCE_FILENAME)
    if found is None:
        return None, ""
    key = _file_key(found)
    with _cache_lock:
        text = _text_cache.get(key)
    if text is None:
        try:
            with open(found[0], encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return None, ""
        _remember(_text_cache, key, text, _TEXT_CACHE_ENTRIES)
    return key, text


def _in_translation(f, block: dict, raw_key, tkey, translation: str) -> bool:
    if not translation or block["length"] <= 0:
        return False
    key = (raw_key, tkey, block["start"], block["length"])
    with _cache_lock:
        known = _match_cache.get(key)
    if known is not None:
        return known
    head = manifest.slice_text(f, block["start"], block["length"], 0, _MATCH_CHARS).strip()
    tail = manifest.tail_text(f, block["start"], block["length"], _MATCH_CHARS).strip()
    found = bool(head) and head in translation and tail in translation
    _remember(_match_cache, key, found, _CACHE_ENTRIES)
    return found


def _index(f, block: dict, raw_key) -> tuple:
    """(characters, checkpoints) of a block, built in one pass per file
    version; the checkpoints keep a deep slice from re-decoding the text
    before it."""
    key = (raw_key, block["start"], block["length"])
    with _cache_lock:
        known = _index_cache.get(key)
    if known is None:
        known = manifest.index_block(f, block["start"], block["length"])
        _remember(_index_cache, key, known, _CACHE_ENTRIES)
    return known


def _chars(f, block: dict, raw_key) -> int:
    if block["chars"] is not None:
        return block["chars"]
    return _index(f, block, raw_key)[0]


def _raw_file(drama_id: int):
    """(path, key, size) of the raw novel, or None when absent."""
    found = manifest.safe_file(drama_id, manifest.RAW_NOVEL_FILENAME)
    if found is None:
        return None
    return found[0], _file_key(found), found[1].st_size


def list_chapters(drama_id: int, offset: int = 0, limit: int = DEFAULT_PAGE) -> dict:
    _require_drama(drama_id)
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise InvalidInputError(f"offset must be 0 or more and limit 1 to {MAX_PAGE}.")
    raw = _raw_file(drama_id)
    tkey, translation = _translation(drama_id)
    out = {"drama_id": drama_id, "present": raw is not None,
           "size_bytes": raw[2] if raw else 0,
           "split": False, "total": 0, "char_count": 0, "in_translation": 0,
           "translation_chars": len(translation), "offset": offset, "limit": limit,
           "chapters": []}
    if raw is None:
        return out
    path, raw_key, size = raw
    blocks, out["split"] = _blocks(drama_id, size)
    out["total"] = len(blocks)
    page = blocks[offset:offset + limit]
    with open(path, "rb") as f:
        # Counting every block would decode the whole file when there is no
        # manifest; with one, the stored counts make the total free.
        out["char_count"] = sum(_chars(f, b, raw_key) for b in blocks)
        rows = []
        for n, b in enumerate(page, offset + 1):
            inside = _in_translation(f, b, raw_key, tkey, translation)
            rows.append({"number": n, "title": b["title"], "chars": _chars(f, b, raw_key),
                         "source": b["source"], "imported_at": b["imported_at"],
                         "unsplit": b["unsplit"], "in_translation": inside})
    out["chapters"] = rows
    out["in_translation"] = sum(1 for r in rows if r["in_translation"])
    return out


def read_chapter(drama_id: int, number: int, offset: int = 0,
                 limit: int = DEFAULT_SLICE_CHARS) -> dict:
    """`limit` characters of chapter `number` (1-based) from character
    `offset`; next_offset is None once the chapter's end was returned."""
    _require_drama(drama_id)
    if offset < 0 or not 1 <= limit <= MAX_SLICE_CHARS:
        raise InvalidInputError(f"offset must be 0 or more and limit 1 to {MAX_SLICE_CHARS}.")
    raw = _raw_file(drama_id)
    blocks, _split = _blocks(drama_id, raw[2]) if raw else ([], False)
    if not 1 <= number <= len(blocks):
        raise NotFoundError("No such saved chapter.")
    path, raw_key, _size = raw
    b = blocks[number - 1]
    tkey, translation = _translation(drama_id)
    with open(path, "rb") as f:
        chars = _chars(f, b, raw_key)
        if offset >= chars:
            text = ""
        elif offset < _CHECKPOINT_MIN_OFFSET:
            text = manifest.slice_text(f, b["start"], b["length"], offset, limit)
        else:
            text = manifest.slice_text(f, b["start"], b["length"], offset, limit,
                                       _index(f, b, raw_key)[1])
        inside = _in_translation(f, b, raw_key, tkey, translation)
    end = offset + len(text)
    return {"drama_id": drama_id, "number": number, "title": b["title"], "source": b["source"],
            "imported_at": b["imported_at"], "unsplit": b["unsplit"], "chars": chars,
            "in_translation": inside, "offset": offset, "text": text,
            "next_offset": end if end < chars else None}
