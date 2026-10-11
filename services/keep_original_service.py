"""
services/keep_original_service.py -- the per-title "Keep original files"
pin (`dramas.keep_original`).

A title's originals are what the app can't make again: the source audio
and video, the uploaded novel reference and raw novel text, every raw
transcript and the page scans. For a pinned title the storage-cleanup
presets never remove them (storage.py gets them as its keep-list), the
Trash says so before a permanent delete, and the library export zip
carries them in the title's `originals/` folder.

The pin itself is written through drama_service.update_drama_metadata
(whitelisted like the other metadata toggles, same permission).
"""

import os
import re
import stat
import zipfile

import db
import raw_transcript
import storage
from services.novel_files_service import RAW_NOVEL_FILENAME

_FILENAME_COLUMNS = ("audio_filename", "source_video_filename", "novel_reference_filename")


def is_pinned(drama) -> bool:
    return bool((drama or {}).get("keep_original"))


def _regular_file_inside(base: str, rel) -> str:
    """The full path of `rel` when it is a regular file inside `base` with
    no link on the way, else "" (a stored name is never trusted blindly)."""
    if not rel or not isinstance(rel, str) or "\x00" in rel or os.path.isabs(rel):
        return ""
    joined = os.path.normpath(os.path.join(base, rel))
    if os.path.realpath(joined) != joined or not joined.startswith(base + os.sep):
        return ""
    try:
        st = os.lstat(joined)
    except OSError:
        return ""
    return joined if stat.S_ISREG(st.st_mode) else ""


def original_files(drama_id: int, drama=None) -> list:
    """(path relative to the drama folder, full path) of each original
    that is on disk, whether or not the title is pinned."""
    drama = drama if drama is not None else (db.get_drama(drama_id) or {})
    base = os.path.realpath(os.path.join(db.DRAMAS_DIR, str(drama_id)))   # never created here
    names = [drama.get(c) for c in _FILENAME_COLUMNS] + [RAW_NOVEL_FILENAME]
    names += [os.path.basename(p) for p in raw_transcript.list_raw_transcripts(base)]
    names += [p.get("filename") for p in db.list_pages(drama_id)]
    out, seen = [], set()
    for name in names:
        full = _regular_file_inside(base, name)
        if full and full not in seen:
            seen.add(full)
            out.append((os.path.relpath(full, base), full))
    return out


def kept_paths(drama_id: int, drama=None) -> list:
    """Relative paths storage cleanup must leave alone: the originals of a
    pinned title, nothing for any other."""
    drama = drama if drama is not None else (db.get_drama(drama_id) or {})
    return [rel for rel, _full in original_files(drama_id, drama)] if is_pinned(drama) else []


def scan_library() -> dict:
    """storage.scan_library_storage over every title, pinned originals not
    counted as reclaimable."""
    dramas = {d["id"]: d for d in db.list_dramas()}
    return storage.scan_library_storage(db.LIBRARY_DIR, list(dramas),
                                        keep_for=lambda did: kept_paths(did, dramas[did]))


def clean_title_storage(drama_id: int, categories) -> dict:
    """storage.clean_drama_storage for one title, its pinned originals kept."""
    return storage.clean_drama_storage(os.path.join(db.DRAMAS_DIR, str(drama_id)), categories,
                                       keep=kept_paths(drama_id))


def trashed_from_pinned_title(parts) -> bool:
    """A Trash entry whose original place (data-folder relative parts) is
    in a pinned title's folder, or is that folder."""
    parts = list(parts or ())
    if (len(parts) < 3 or parts[0].lower() != os.path.basename(db.LIBRARY_DIR).lower()
            or parts[1].lower() != "dramas" or not re.fullmatch(r"[0-9]{1,9}", parts[2])):
        return False    # a stray folder name must not break the Trash list
    return is_pinned(db.get_drama(int(parts[2])))


def export_title_files(zf: zipfile.ZipFile, drama_id: int, drama: dict, folder: str) -> None:
    """A title's files in the library export beyond its subtitles: the
    mixed dub track, and for a pinned title its originals under
    `originals/` (stored uncompressed: media is already compressed and
    deflating a long video only costs time)."""
    dub_path = os.path.join(db.DRAMAS_DIR, str(drama_id), "dub_track.wav")
    if os.path.isfile(dub_path) and not os.path.islink(dub_path):
        zf.write(dub_path, f"{folder}/dub_track.wav")
    if not is_pinned(drama):
        return
    for rel, full in original_files(drama_id, drama):
        zf.write(full, f"{folder}/originals/{rel.replace(os.sep, '/')}",
                 compress_type=zipfile.ZIP_STORED)
