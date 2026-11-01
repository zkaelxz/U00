"""
services/disk_usage_title_sizes.py -- the per-title split the Disk usage view
shows when it lists library/dramas: how much of each title's folder is
originals, raw transcripts and regenerable files, plus how much its
line-history snapshots take in the database.

Kinds, by file (the first that matches wins; everything else is left out of
all four, so they need not add up to the folder's size):
- regenerable: what the storage scan presets may remove (the same rule as
  the item's own `regenerable` label, disk_usage_measure._regenerable);
- raw transcripts: the raw_transcript run files, plain or compressed;
- originals: the media and novel files the title's row names, the raw novel
  text, the replaced media kept in kept_media/ and the page scans in pages/.
A title pinned with "Keep original files" is the exception: its originals
(keep_original_service.kept_paths) are never removed by the presets, so
they count as originals (or raw transcripts) even when a cleanup pattern
matches their name.

The walk never enters or counts a symlink, a Windows junction or a folder on
another volume, has its own entry and time limit (`complete` is False when
it was cut short), and returns numbers only.
"""

import contextlib
import os
import stat
import time

import db
import raw_transcript
from services import disk_usage_guard as guard
from services import disk_usage_measure as measure
from services import keep_original_service
from services.delete_service import RAW_NOVEL_FILENAME
from services.media_upload_service import KEPT_DIRNAME

MAX_ENTRIES = 500_000
MAX_SECONDS = 10.0
_ORIGINAL_DIRS = (KEPT_DIRNAME, "pages")


_NAME_COLUMNS = ("audio_filename", "source_video_filename", "novel_reference_filename")


def _title_rows() -> dict:
    """{drama id: {its file-name columns and keep_original}}."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(f"SELECT id, {', '.join(_NAME_COLUMNS)}, keep_original "
                            "FROM dramas").fetchall()
    return {r[0]: dict(zip(_NAME_COLUMNS + ("keep_original",), r[1:])) for r in rows}


def _history_bytes() -> dict:
    with contextlib.closing(db.get_conn()) as conn:
        # LENGTH of a bare BLOB column is read from the row header, so the
        # packed snapshots (nearly all of them) aren't loaded; only the few
        # newest plain-text ones are cast to count bytes rather than characters.
        rows = conn.execute(
            "SELECT drama_id, SUM(CASE WHEN typeof(snapshot_json) = 'blob' "
            "THEN LENGTH(snapshot_json) ELSE LENGTH(CAST(snapshot_json AS BLOB)) END) "
            "FROM line_history GROUP BY drama_id").fetchall()
    return {r[0]: r[1] or 0 for r in rows}


class _Limit:
    def __init__(self):
        self.left = MAX_ENTRIES
        self.deadline = time.monotonic() + MAX_SECONDS

    def tick(self) -> bool:
        self.left -= 1
        return self.left >= 0 and time.monotonic() <= self.deadline


def _kind(title_parts: tuple, rel: tuple, names: set, kept: set):
    if os.path.join(*rel) in kept:
        return ("raw_transcript_bytes" if len(rel) == 1 and raw_transcript.is_run_name(rel[0])
                else "originals_bytes")
    if measure._regenerable(title_parts + rel, False) or (
            len(rel) > 1 and measure._regenerable(title_parts + rel[:1], True)):
        return "regenerable_bytes"
    if len(rel) == 1 and raw_transcript.is_run_name(rel[0]):
        return "raw_transcript_bytes"
    if (len(rel) == 1 and (rel[0] in names or rel[0] == RAW_NOVEL_FILENAME)) or (
            len(rel) > 1 and rel[0] in _ORIGINAL_DIRS):
        return "originals_bytes"
    return None


def _measure(path: str, title_parts: tuple, names: set, kept: set, limit: _Limit) -> dict:
    sizes = {"originals_bytes": 0, "raw_transcript_bytes": 0, "regenerable_bytes": 0}
    complete = True
    try:
        root_dev = os.lstat(path).st_dev
    except OSError:
        return {**sizes, "complete": False}
    stack: list[tuple[str, tuple[str, ...]]] = [(path, ())]
    while stack and complete:
        cur, rel = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            complete = False
            break
        with it:
            for entry in it:
                if not limit.tick():
                    complete = False
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    complete = False
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                if guard._is_link_stat(st) or (is_dir and measure._device_differs(st, root_dev)):
                    continue
                sub = rel + (entry.name,)
                if is_dir:
                    stack.append((entry.path, sub))
                    continue
                kind = _kind(title_parts, sub, names, kept)
                if kind:
                    sizes[kind] += st.st_size
    return {**sizes, "complete": complete}


def annotate(parts, folder: str, items: list) -> None:
    """Adds `title_sizes` to each title folder in `items` when `parts` is
    library/dramas; leaves every other listing as it is."""
    lib_name = os.path.basename(db.LIBRARY_DIR).lower()
    if [p.lower() for p in parts] != [lib_name, "dramas"]:
        return
    titles = [i for i in items
              if i["kind"] == "folder" and not i["is_link"] and i["name"].isdecimal()
              and i["name"].isascii()]
    if not titles:
        return
    rows, history = _title_rows(), _history_bytes()
    limit = _Limit()
    for item in titles:
        did = int(item["name"])
        row = rows.get(did) or {}
        names = {row[c] for c in _NAME_COLUMNS if row.get(c)}
        kept = ({os.path.normpath(p) for p in keep_original_service.kept_paths(did, row)}
                if keep_original_service.is_pinned(row) else set())
        sizes = _measure(os.path.join(folder, item["name"]), tuple(parts) + (item["name"],),
                         names, kept, limit)
        item["title_sizes"] = {**sizes, "history_bytes": history.get(did, 0)}
