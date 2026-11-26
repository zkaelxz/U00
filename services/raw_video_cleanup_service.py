"""services/raw_video_cleanup_service.py -- old source videos of finished titles: list them and move chosen ones to Trash.

Only a title that has translated lines (so the subtitles are kept), is not pinned by Keep original, was not made
by reading the video itself (hardsub OCR), and has no job running is offered, and only once its video is old
enough. The extracted audio, lines and exports stay. Nothing leaves this module except through the Trash, which can
restore it, and nothing is moved without confirm=true and the size the person was shown. UI-free; raises lib/errors.
"""

import hashlib
import hmac
import os
import secrets
import stat
import time

import db
from lib.errors import ConflictError, InvalidInputError, NotFoundError, ServiceError
from services import disk_usage_guard as guard
from services import disk_usage_hold as hold
from services import disk_usage_measure as measure
from services import disk_usage_service as dus
from services import disk_usage_trash_store as store
from services import drama_service, keep_original_service, ownership_service

DEFAULT_MIN_AGE_DAYS = 30
MAX_BATCH = 200
_KEY = secrets.token_bytes(32)  # ids die with the process; the list is fetched again


def _video_id(drama_id: int, name: str) -> str:
    return hmac.new(_KEY, f"{drama_id}\0{name}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _offerable(row, min_age_days: int, now: float, ctx):
    """(parts, size, mtime) when this title's source video may be offered, else None."""
    name = row.get("source_video_filename")
    # The extracted audio is what stays; a video with no audio beside it is still the title's only media.
    if not name or not row.get("audio_filename") or keep_original_service.is_pinned(row) or row.get("transcript_mode") == "hardsub_ocr":
        return None
    if not db.drama_has_translated_lines(row["id"]) or drama_service.job_running_for_drama(row["id"]):
        return None
    lib = os.path.dirname(os.path.abspath(db.LIBRARY_DIR))
    path = os.path.join(db.drama_dir(row["id"]), name)
    try:
        parts = guard.split_rel(os.path.relpath(os.path.abspath(path), lib))
        guard._resolve(parts)
        st = os.lstat(os.path.join(ctx.root, *parts))
    except (ServiceError, OSError, ValueError):
        return None
    if not stat.S_ISREG(st.st_mode) or guard._is_link_stat(st) or now - st.st_mtime < min_age_days * 86400:
        return None
    return parts, st.st_size, st.st_mtime


def _index(min_age_days: int):
    ctx, now, index, rows = guard._Ctx(), time.time(), {}, []
    # None: the PC window, which on Guest lists no private title.
    for row in db.list_dramas(visible_to=ownership_service.visible_to_filter(None)):
        found = _offerable(row, min_age_days, now, ctx)
        if found is None:
            continue
        parts, size, mtime = found
        vid = _video_id(row["id"], row["source_video_filename"])
        index[vid] = (row["id"], row["source_video_filename"], parts)
        rows.append({"id": vid, "title": row.get("title_en") or row.get("title_zh") or "", "size_bytes": size,
                     "modified_at": measure._iso(mtime)})
    rows.sort(key=lambda r: -r["size_bytes"])
    return index, rows


def _check_age(min_age_days):
    if isinstance(min_age_days, bool) or not isinstance(min_age_days, int) or not 0 <= min_age_days <= 3650:
        raise InvalidInputError("Choose an age between 0 and 3650 days.")


def list_raw_videos(min_age_days: int = DEFAULT_MIN_AGE_DAYS) -> dict:
    _check_age(min_age_days)
    _, rows = _index(min_age_days)
    return {"videos": rows, "total_bytes": sum(r["size_bytes"] for r in rows), "min_age_days": min_age_days,
            "busy_reason": hold.busy_reason()}


def _still_offerable(drama_id: int, name: str) -> bool:
    row = db.get_drama(drama_id)
    return bool(row and row.get("source_video_filename") == name and not keep_original_service.is_pinned(row)
                and not drama_service.job_running_for_drama(drama_id))


def trash_raw_videos(videos, min_age_days: int = DEFAULT_MIN_AGE_DAYS, confirm=False) -> dict:
    """Moves the named videos (each with the size shown) into Trash and clears the title's video name, so
    the title no longer claims a file it does not have. Each is looked up and checked again under the library
    hold; one that changed, got pinned or whose title started a job since the list is skipped. A failed
    move stops the batch; the error's details carry what was moved before it."""
    hold._require_confirm(confirm)
    _check_age(min_age_days)
    if not isinstance(videos, list) or not videos or len(videos) > MAX_BATCH:
        raise InvalidInputError(f"Choose between 1 and {MAX_BATCH} videos.")
    wanted = []
    for v in videos:
        vid, size = (v or {}).get("id"), (v or {}).get("expected_size_bytes")
        if not isinstance(vid, str) or len(vid) != 32 or isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise InvalidInputError("That isn't a video from the list.")
        wanted.append((vid, size))
    if len({vid for vid, _ in wanted}) != len(wanted):
        raise InvalidInputError("A video is listed twice.")
    moved, moved_bytes, skipped = 0, 0, []
    with hold._changing("Disk usage raw videos"):
        if guard._root_blocker(guard._root()):
            raise InvalidInputError(guard.ROOT_TOO_BROAD)
        index, _rows = _index(min_age_days)
        try:
            for vid, expected in wanted:
                hit = index.get(vid)
                if hit is None:
                    skipped.append({"id": vid, "reason": "no_longer_offered"})
                    continue
                drama_id, name, parts = hit
                try:
                    parts, real, item = dus._inspect_for_change("/".join(parts), "moving it")
                    if item["kind"] != "file" or item["size_bytes"] != expected:
                        raise ConflictError(store.CHANGED, details={"reason": "changed"})
                    store._move_to_trash(parts, real, item, still_ok=lambda d=drama_id, n=name: _still_offerable(d, n))
                except ConflictError as exc:
                    if (exc.details or {}).get("reason") != "changed":
                        raise
                    skipped.append({"id": vid, "reason": "changed"})
                    continue
                except (NotFoundError, InvalidInputError):
                    skipped.append({"id": vid, "reason": "changed"})
                    continue
                db.clear_source_video(drama_id, name)
                moved += 1
                moved_bytes += item["size_bytes"]
        except ServiceError as exc:
            raise type(exc)(exc.message, details={**(exc.details or {}), "moved_count": moved,
                                                  "moved_bytes": moved_bytes, "skipped": skipped}) from None
    return {"moved_count": moved, "moved_bytes": moved_bytes, "skipped": skipped}
