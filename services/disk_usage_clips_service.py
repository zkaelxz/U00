"""
services/disk_usage_clips_service.py -- unused voice clips: list the files in
a title's voice_refs/ that no speaker points to, and move chosen ones to
Baihe's Trash folder through the same checks and rename as a Clear in
services/disk_usage_service.py. UI-free; raises services/service_errors.
"""

import hashlib
import hmac
import os
import re
import secrets
import stat
import time

import db
from services import disk_usage_service as dus
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

# Only the two kinds of file the app writes into a title's voice_refs/
# (uploads and picked candidates) are ever offered. Voice-bank copies are
# written to the title's root folder instead, so they are not scanned. Nothing in
# a response names a file or a path: a clip is addressed by an id that only
# this process can compute, so a client can't ask for a file it wasn't shown.

VOICE_REFS_DIRNAME = "voice_refs"
MAX_CLIP_BATCH = 500
_CLIP_NAME_RE = re.compile(
    r"clone_ref_[0-9a-f]{32}\.(wav|mp3|m4a|flac|ogg)|clone_pick_[0-9a-f]{32}\.wav",
    re.IGNORECASE)
_CLIP_ID_RE = re.compile(r"[0-9a-f]{32}")
_CLIP_KEY = secrets.token_bytes(32)     # ids die with the process; the list is fetched again


def _clip_id(drama_id: int, name: str) -> str:
    return hmac.new(_CLIP_KEY, f"{drama_id}\0{name}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _ref_key(stored) -> str:
    """How a stored clip name is compared: its last part only, ignoring case.
    Deliberately looser than the name voice_refs/ holds (a bare name, a
    'voice_refs/' prefix and a Windows separator all match), because a clip
    that is wrongly kept costs a few MB and one that is wrongly offered costs
    a voice."""
    return str(stored).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _clips_held_by_undo(drama_id: int) -> set:
    """Last-name-part keys (see _ref_key) of clips a live "merge two speakers"
    undo record can bring back after the merge cleared the source's Characters
    row: until the undo expires or is used, no row points at them, yet undoing
    would link them again."""
    return {_ref_key(c) for c in db.live_speaker_merge_undo_clips(drama_id, time.time())}


def _clip_still_unused(drama_id: int, name: str) -> bool:
    """Fresh answer, read when asked. False on any doubt, including an
    unreadable database."""
    from services import voice_clone_service
    try:
        if voice_clone_service._clip_reading_job_active(drama_id):
            return False
        key = name.casefold()
        if key in _clips_held_by_undo(drama_id):
            return False
        return not any(_ref_key(r.get("ref_audio_filename") or "") == key
                       for r in db.list_characters(drama_id) if r.get("ref_audio_filename"))
    except Exception:
        return False


def _unused_clips_of(drama_id: int, ctx: dus._Ctx):
    """[{name, parts, size, mtime}] for one title's unreferenced clips, or
    None when a job that reads clips is running for it. Empty when the folder
    is missing, a link, or can't be read."""
    from services import voice_clone_service
    lib = os.path.dirname(os.path.abspath(db.LIBRARY_DIR))
    folder = os.path.join(db.DRAMAS_DIR, str(drama_id), VOICE_REFS_DIRNAME)
    try:
        folder_parts = dus.split_rel(os.path.relpath(os.path.abspath(folder), lib))
        dus._resolve(folder_parts)
        if voice_clone_service._clip_reading_job_active(drama_id):
            return None
        used = {_ref_key(r["ref_audio_filename"]) for r in db.list_characters(drama_id)
                if r.get("ref_audio_filename")} | _clips_held_by_undo(drama_id)
        entries = list(os.scandir(os.path.join(ctx.root, *folder_parts)))
    except (ServiceError, OSError, ValueError):
        return []
    out = []
    for e in entries:
        if not _CLIP_NAME_RE.fullmatch(e.name) or e.name.casefold() in used:
            continue
        try:
            st = e.stat(follow_symlinks=False)
            parts = dus.split_rel("/".join(folder_parts + [e.name]))
        except (OSError, ServiceError):
            continue
        if stat.S_ISREG(st.st_mode) and not dus._is_link_stat(st):
            out.append({"name": e.name, "parts": parts, "size": st.st_size, "mtime": st.st_mtime})
    return out


def _unused_clip_index() -> tuple:
    """({id: (drama_id, name, parts, size)}, titles, titles_in_use), where
    `titles` is [(title, [clip, ...])] for every title with something to
    offer. One pass over the library; the same code serves list and remove."""
    ctx = dus._Ctx()
    index, titles, in_use = {}, [], 0
    for drama in db.list_dramas():
        found = _unused_clips_of(drama["id"], ctx)
        if found is None:
            in_use += 1
            continue
        for c in found:
            c["id"] = _clip_id(drama["id"], c["name"])
            index[c["id"]] = (drama["id"], c["name"], c["parts"], c["size"])
        if found:
            titles.append((drama.get("title_en") or drama.get("title_zh") or "",
                           sorted(found, key=lambda c: c["name"])))
    return index, titles, in_use


def unused_voice_clips() -> dict:
    """Clips in each title's voice_refs/ that no speaker points to, with no
    file name or path in the result. Titles with a dub, narration or
    audiobook job running are left out and counted in `titles_in_use`."""
    _, titles, in_use = _unused_clip_index()
    shown, total_bytes, total_count = [], 0, 0
    for title, clips in titles:
        rows = []
        for c in clips:
            rows.append({"id": c["id"], "file_type": os.path.splitext(c["name"])[1][1:].lower(),
                         "size_bytes": c["size"], "modified_at": dus._iso(c["mtime"])})
        shown.append({"title": title, "size_bytes": sum(c["size"] for c in clips), "clips": rows})
        total_bytes += shown[-1]["size_bytes"]
        total_count += len(rows)
    return {"titles": shown, "total_bytes": total_bytes, "total_count": total_count,
            "titles_in_use": in_use, "busy_reason": dus.busy_reason()}


def trash_unused_voice_clips(clips, confirm=False) -> dict:
    """Moves the named clips (each with the size that was shown) into Trash,
    where they can be restored. Every clip is looked up and checked again
    under the library hold, so one that was picked for a speaker, changed, or
    whose title started a clip-reading job since the list is skipped, not
    moved. A busy library or a failed move stops the batch: the error's details carry
    moved_count, moved_bytes and skipped for what was done before it."""
    dus._require_confirm(confirm)
    if not isinstance(clips, list) or not clips or len(clips) > MAX_CLIP_BATCH:
        raise InvalidInputError(f"Choose between 1 and {MAX_CLIP_BATCH} clips.")
    wanted = []
    for c in clips:
        cid, size = (c or {}).get("id"), (c or {}).get("expected_size_bytes")
        if (not isinstance(cid, str) or not _CLIP_ID_RE.fullmatch(cid) or isinstance(size, bool)
                or not isinstance(size, int) or size < 0):
            raise InvalidInputError("That isn't a clip from the list.")
        wanted.append((cid, size))
    if len({cid for cid, _ in wanted}) != len(wanted):
        raise InvalidInputError("A clip is listed twice.")
    from services import voice_clone_service
    moved_bytes, moved, skipped = 0, 0, []
    with dus._changing("Disk usage voice clips"):
        if dus._root_blocker(dus._root()):
            # Checked here so the clips aren't reported as "changed": every
            # path is protected when the data folder is a drive root or a
            # home folder, and the person needs to hear that.
            raise InvalidInputError(dus.ROOT_TOO_BROAD)
        index, _titles, _in_use = _unused_clip_index()
        try:
            for cid, expected in wanted:
                hit = index.get(cid)
                if hit is None:
                    skipped.append({"id": cid, "reason": "no_longer_unused"})
                    continue
                drama_id, name, parts, _size = hit
                try:
                    parts, real, item = dus._inspect_for_change("/".join(parts), "moving it")
                    if item["kind"] != "file" or item["size_bytes"] != expected:
                        raise ConflictError(dus.CHANGED, details={"reason": "changed"})
                    # The title's clip lock makes "no speaker points at it" and
                    # the rename one step against a pick or upload.
                    with voice_clone_service.clip_lock(drama_id):
                        dus._move_to_trash(parts, real, item,
                                       still_ok=lambda d=drama_id, n=name: _clip_still_unused(d, n))
                except ConflictError as exc:
                    if (exc.details or {}).get("reason") != "changed":
                        raise
                    skipped.append({"id": cid, "reason": "changed"})
                    continue
                except (NotFoundError, InvalidInputError):
                    skipped.append({"id": cid, "reason": "changed"})
                    continue
                moved += 1
                moved_bytes += item["size_bytes"]
        except ServiceError as exc:
            # What was already moved stays moved; say so with the failure.
            raise type(exc)(exc.message, details={
                **(exc.details or {}), "moved_count": moved, "moved_bytes": moved_bytes,
                "skipped": skipped}) from None
    return {"moved_count": moved, "moved_bytes": moved_bytes, "skipped": skipped}
