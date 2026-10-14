"""
services/reading_speed_dismissal_service.py -- remembers which reading-speed
flags the user dismissed, so the next translate run or "flag dense lines"
doesn't raise them again.

The flag is re-derived from the text and timing on every check, so only a
remembered dismissal keeps it away. A dismissal is keyed by (line id, hash of
the English text it was dismissed for): an edited or re-translated line has a
new hash and can be flagged again if it is still too fast. Lives in a sidecar
in the title's folder (like timing_check.json), so deleting or purging the
title removes it with the folder; no database column.
"""
import json
import os
import tempfile
import threading

import db
import subtitle_formats

_STATE_FILE = "reading_speed_dismissed.json"
# Oldest dismissals fall off first: a re-flagged line is a nuisance, an unbounded file is not.
_MAX_ENTRIES = 5000
# A real sidecar is ~100 KB at the cap; a backup import can bring any file.
_MAX_STATE_BYTES = 1024 * 1024
_lock = threading.Lock()


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), _STATE_FILE)


def _read(drama_id: int) -> list:
    """[(line_id, hash)], oldest first. Trusts nothing in the file."""
    path = _path(drama_id)
    try:
        if os.path.getsize(path) > _MAX_STATE_BYTES:
            return []
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return []
    raw = data.get("dismissed") if isinstance(data, dict) else None
    out = []
    for item in raw if isinstance(raw, list) else []:
        if (isinstance(item, list) and len(item) == 2 and isinstance(item[0], int)
                and not isinstance(item[0], bool) and isinstance(item[1], str)):
            out.append((item[0], item[1][:64]))
    return out[-_MAX_ENTRIES:]


def load_dismissed(drama_id: int) -> set:
    """{(line_id, hash)} for subtitle_formats.flag_dense_lines(dismissed=...)."""
    return set(_read(drama_id))


def flag_dense_lines(drama_id: int, lines, mode: str = None) -> int:
    """subtitle_formats.flag_dense_lines that leaves dismissed lines alone,
    at the title's own flagging mode unless one is given."""
    if mode is None:
        mode = subtitle_formats.flagging_mode_of(db.get_drama(drama_id))
    return subtitle_formats.flag_dense_lines(lines, mode=mode, dismissed=load_dismissed(drama_id))


def note_dismissed(drama_id: int, line_id: int, en_text: str) -> None:
    key = (line_id, subtitle_formats.text_hash(en_text))
    with _lock:
        entries = [e for e in _read(drama_id) if e[0] != line_id]
        entries.append(key)
        entries = entries[-_MAX_ENTRIES:]
        folder = os.path.dirname(_path(drama_id))
        os.makedirs(folder, exist_ok=True)
        # A temp name of its own: two processes can write this file at once.
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f"{_STATE_FILE}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"dismissed": [list(e) for e in entries]}, fh)
            os.replace(tmp, _path(drama_id))
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
