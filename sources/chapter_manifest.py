"""
sources/chapter_manifest.py -- which chapters sit where in a drama's
`raw_novel_context.txt`.

The raw-novel file is plain text: each imported chapter is appended as
`<title>\\n\\n<text>` after a blank line, with no marker that a later reader
could tell from the chapter's own paragraphs. So the import pipeline also
writes `raw_novel_chapters.json` beside it: per chapter the title, source
site and import time, plus the byte range its block occupies in the file.

The manifest is only a hint. It records the file's size after the last
chapter it knows about; a reader trusts it only while that still equals
the file's real size, so an upload, a paste or a hand edit (which change
the size, and also drop the manifest) can never make it point into the
wrong text. Old titles have no manifest and are read as one unsplit block.
Nothing here ever raises into the import: a manifest that can't be
written just leaves the chapters unlisted.

The text helpers read byte ranges with the same decoding the other
raw-novel readers use (UTF-8, replacement characters, universal
newlines), in chunks, so a multi-megabyte file is never held whole.
"""

import bisect
import io
import json
import logging
import os
import stat
import tempfile
import time
from typing import Optional

import db

log = logging.getLogger(__name__)

MANIFEST_FILENAME = "raw_novel_chapters.json"
RAW_NOVEL_FILENAME = "raw_novel_context.txt"
_VERSION = 1
_CHUNK = 64 * 1024
_MAX_TITLE = 200
# A UTF-8 character is at most 4 bytes; this many bytes always hold the last
# `n` characters of a block for n up to _TAIL_CHARS.
_TAIL_CHARS = 400
# A checkpoint is taken at a line end at least this many bytes after the
# previous one; 64 KiB keeps a 10M-character block to a few hundred entries.
_CHECKPOINT_BYTES = 64 * 1024
# A block with no line break for this long gets no further checkpoints
# rather than being held in memory.
_MAX_PENDING_BYTES = 8 * 1024 * 1024


def manifest_path(drama_id: int) -> str:
    return os.path.join(db.DRAMAS_DIR, str(int(drama_id)), MANIFEST_FILENAME)


def raw_path(drama_id: int) -> str:
    return os.path.join(db.DRAMAS_DIR, str(int(drama_id)), RAW_NOVEL_FILENAME)


_REPARSE_POINT = 0x400   # FILE_ATTRIBUTE_REPARSE_POINT: Windows junctions and symlinks


def safe_file(drama_id: int, filename: str):
    """(real path, lstat result) for `filename` in the drama's folder, or
    None. A link anywhere on the way (or a link as the file itself) counts
    as not present, so readers that return file contents can't be pointed
    outside the folder."""
    base = os.path.realpath(os.path.join(db.DRAMAS_DIR, str(int(drama_id))))
    joined = os.path.join(base, filename)
    real = os.path.realpath(joined)
    try:
        inside = os.path.commonpath([base, real]) == base
    except ValueError:   # different Windows drives
        inside = False
    if not inside or real != joined:
        return None
    try:
        st = os.lstat(real)
    except OSError:
        return None
    if (stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode)
            or getattr(st, "st_file_attributes", 0) & _REPARSE_POINT):
        return None
    return real, st


class _Range(io.RawIOBase):
    """Reads at most `length` bytes from the current position of `f`."""

    def __init__(self, f, length: int):
        self._f = f
        self._left = max(0, int(length))

    def readable(self):
        return True

    def readinto(self, buf):
        n = min(len(buf), self._left)
        if n <= 0:
            return 0
        data = self._f.read(n)
        buf[:len(data)] = data
        self._left -= len(data)
        return len(data)


def _open_text(f, start: int, length: int):
    f.seek(start)
    return io.TextIOWrapper(io.BufferedReader(_Range(f, length)), encoding="utf-8",
                            errors="replace", newline=None)


def slice_text(f, start: int, length: int, skip: int, take: int, checkpoints=None) -> str:
    """`take` characters after `skip` characters of the block at bytes
    [start, start+length) of the open binary file `f`. `checkpoints` (from
    index_block) lets the read begin near `skip` instead of at the block's
    start."""
    if checkpoints and skip > 0:
        at = bisect.bisect_right(checkpoints, (skip, float("inf"))) - 1
        if at >= 0:
            chars_before, bytes_before = checkpoints[at]
            start, length, skip = start + bytes_before, length - bytes_before, skip - chars_before
    text = _open_text(f, start, length)
    while skip > 0:
        got = text.read(min(skip, _CHUNK))
        if not got:
            return ""
        skip -= len(got)
    return text.read(max(0, take))


def index_block(f, start: int, length: int) -> tuple:
    """(total characters, checkpoints) of the block in one pass. A
    checkpoint is (characters before, bytes before) at a point just after a
    newline byte: there the decoder holds no half character and no pending
    carriage return, so decoding from it gives the same characters as
    decoding from the block's start."""
    checkpoints = []
    total = done = 0
    buf = bytearray()
    f.seek(start)
    while done + len(buf) < length:
        data = f.read(min(_CHUNK, length - done - len(buf)))
        if not data:
            break
        buf += data
        if len(buf) >= _CHECKPOINT_BYTES:
            cut = buf.rfind(b"\n") + 1
            if cut:
                total += chars_of(bytes(buf[:cut]).decode("utf-8", errors="replace"))
                done += cut
                del buf[:cut]
                checkpoints.append((total, done))
            elif len(buf) > _MAX_PENDING_BYTES:
                return total + count_chars(f, start + done, length - done), checkpoints
    total += chars_of(bytes(buf).decode("utf-8", errors="replace"))
    return total, checkpoints


def count_chars(f, start: int, length: int) -> int:
    text = _open_text(f, start, length)
    total = 0
    while True:
        got = text.read(_CHUNK)
        if not got:
            return total
        total += len(got)


def tail_text(f, start: int, length: int, n: int = _TAIL_CHARS) -> str:
    """The last `n` characters of the block (newlines normalised)."""
    back = min(length, n * 4 + 4)
    f.seek(start + length - back)
    data = f.read(back)
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace",
                            newline=None).read()
    if back < length:
        text = text.lstrip("�")   # the cut may fall inside a character
    return text[-n:]


def load(drama_id: int) -> Optional[dict]:
    """The manifest as {"size": int, "chapters": [entry, ...]}, or None
    when it is missing or not shaped as expected."""
    try:
        found = safe_file(drama_id, MANIFEST_FILENAME)
        if found is None:
            return None
        with open(found[0], encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != _VERSION or not isinstance(data.get("size"), int):
            return None
        rows = data.get("chapters")
        if not isinstance(rows, list):
            return None
        chapters = []
        for row in rows:
            start, length, chars = row["start"], row["length"], row["chars"]
            if not all(isinstance(v, int) and v >= 0 for v in (start, length, chars)):
                return None
            chapters.append({
                "title": str(row.get("title") or "")[:_MAX_TITLE],
                "source": str(row.get("source") or "")[:60],
                "imported_at": str(row.get("imported_at") or "")[:20],
                "start": start, "length": length, "chars": chars,
                "unsplit": bool(row.get("unsplit"))})
        return {"size": data["size"], "chapters": chapters}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def drop(drama_id: int) -> bool:
    """Called when the raw novel is replaced or removed. False when the
    manifest is still there afterwards, so the caller can refuse to go on."""
    try:
        os.remove(manifest_path(drama_id))
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("Could not remove the chapter manifest for drama %s", drama_id)
        return False
    return True


def _save(drama_id: int, size: int, chapters: list) -> None:
    folder = os.path.dirname(manifest_path(drama_id))
    fd, tmp = tempfile.mkstemp(prefix=".chapters_", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": _VERSION, "size": size, "chapters": chapters}, f,
                      ensure_ascii=False)
        os.replace(tmp, manifest_path(drama_id))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def chars_of(text: str) -> int:
    """How many characters `text` reads back as (universal newlines)."""
    return len(text.replace("\r\n", "\n").replace("\r", "\n"))


def record(drama_id: int, *, pre_size: int, post_size: int, content_start: int,
           content_length: int, content_chars: int, title: str = "", source: str = "") -> None:
    """Notes a chapter just written at bytes [content_start, +content_length)
    of the raw-novel file, which was `pre_size` bytes before the write and
    is `post_size` after it. Text already in the file that the manifest
    doesn't account for becomes one leading unsplit entry."""
    try:
        _record(drama_id, pre_size, post_size, content_start, content_length, content_chars,
                title, source)
    except Exception:
        log.warning("Could not update the chapter manifest for drama %s", drama_id,
                    exc_info=True)


def _record(drama_id, pre_size, post_size, content_start, content_length, content_chars,
            title, source):
    known = load(drama_id)
    chapters = known["chapters"] if known and known["size"] == pre_size else None
    if chapters is None:
        # A retried chapter that an earlier attempt already recorded. Matched by
        # range alone: the pipeline's retry path can run after later writes
        # changed the file's size, and size-mismatch must not discard the list.
        if known and any(c["start"] == content_start and c["length"] == content_length
                         for c in known["chapters"]):
            return
        chapters = []
        if pre_size > 0:
            with open(raw_path(drama_id), "rb") as f:
                chars = count_chars(f, 0, pre_size)
            chapters.append({"title": "", "source": "", "imported_at": "", "start": 0,
                             "length": pre_size, "chars": chars, "unsplit": True})
    chapters.append({
        "title": (title or "")[:_MAX_TITLE], "source": (source or "")[:60],
        "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "start": content_start, "length": content_length, "chars": content_chars, "unsplit": False})
    _save(drama_id, post_size, chapters)
