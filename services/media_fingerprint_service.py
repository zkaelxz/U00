"""services/media_fingerprint_service.py -- tells whether two titles were made from the same file, and lists duplicate titles.

A title's `media_hash` is the SHA-256 of the file it was made from, set when the file is uploaded.
Titles from before that column existed are hashed by the scan job (`start_scan`).
"""

import hashlib
import os

import background_jobs
import db
from jobs import job_launch
from lib.errors import ConflictError
from lib.safe_paths import plain_file_in
from services import drama_service, ownership_service

SCAN_JOB_ID = "duplicate_scan"
_CHUNK = 1024 * 1024
MAX_GROUPS = 200

_SAME_FILE_VISIBLE = "That exact file is already in your Library as “{title}”."
# A title the caller can't see is not named, so a private title never leaks through a refused upload.
_SAME_FILE_HIDDEN = "That exact file is already in the Library under another title."


def hash_file(path, chunk=_CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def new_hasher():
    return hashlib.sha256()


def refuse_duplicate(media_hash, drama_id, principal=None) -> None:
    """Raises ConflictError when another title was made from this very file; the same title
    re-uploading its own file is allowed (it is a replace the caller already confirmed)."""
    if not media_hash:
        return
    others = [row for row in _all_dramas() if row.get("media_hash") == media_hash and row["id"] != drama_id]
    if not others:
        return
    visible = {row["id"] for row in db.list_dramas(visible_to=ownership_service.visible_to_filter(principal))}
    shown = next((row for row in others if row["id"] in visible), None)
    if shown is None:
        raise ConflictError(_SAME_FILE_HIDDEN, details={"reason": "duplicate_media"})
    name = shown.get("title_en") or shown.get("title_zh") or f"title {shown['id']}"
    raise ConflictError(_SAME_FILE_VISIBLE.format(title=name), details={"reason": "duplicate_media",
                                                                      "title_id": shown["id"]})


def _all_dramas():
    return db.list_dramas(visible_to=None)


def _title(row) -> str:
    return row.get("title_en") or row.get("title_zh") or ""


def duplicate_groups(principal=None) -> dict:
    """Groups of titles the caller can see that look like one title added twice:
    {"groups": [{"same": "file" | "link" | "name", "items": [{"id", "title"}]}], "unhashed": n},
    where unhashed counts visible titles with media the scan has not hashed yet."""
    rows = db.list_dramas(visible_to=ownership_service.visible_to_filter(principal))
    buckets = {}
    unhashed = 0
    for row in rows:
        keys = []
        if row.get("media_hash"):
            keys.append(("file", row["media_hash"]))
        elif row.get("audio_filename") or row.get("source_video_filename"):
            unhashed += 1
        link = drama_service._link_key(row.get("source_url"))
        if link:
            keys.append(("link", link))
        for name in {drama_service._name_key(row.get("title_en")), drama_service._name_key(row.get("title_zh"))}:
            if name:
                keys.append(("name", name))
        for key in keys:
            buckets.setdefault(key, []).append(row)
    groups, seen = [], set()
    for (same, _), members in sorted(buckets.items(), key=lambda kv: ("file", "link", "name").index(kv[0][0])):
        ids = tuple(sorted({m["id"] for m in members}))
        if len(ids) < 2 or (same, ids) in seen:
            continue
        seen.add((same, ids))
        by_id = {m["id"]: m for m in members}
        groups.append({"same": same, "items": [{"id": i, "title": _title(by_id[i])} for i in ids]})
    return {"groups": groups[:MAX_GROUPS], "unhashed": unhashed}


def _media_path(row):
    ddir = db.drama_dir(row["id"])
    for field in ("source_video_filename", "audio_filename"):
        path = plain_file_in(ddir, row.get(field))
        if path and os.path.isfile(path):
            return path
    return None


def _scan_job(job_id):
    todo = [row for row in _all_dramas() if not row.get("media_hash") and _media_path(row)]
    for index, row in enumerate(todo):
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(job_id, index / max(len(todo), 1), f"Checking files ({index + 1}/{len(todo)})...")
        path = _media_path(row)
        try:
            digest = hash_file(path) if path else None
        except OSError:
            continue  # a file in use or gone is left for the next scan
        if digest:
            db.set_media_hash(row["id"], digest)
    background_jobs.update_progress(job_id, 1.0, "Done.")


def start_scan() -> dict:
    started = job_launch.start_job(SCAN_JOB_ID, _scan_job, SCAN_JOB_ID,
                                   description="Looking for duplicate titles", line_fields=())
    if not started:
        raise ConflictError("A duplicate check is already running.")
    return {"job_id": SCAN_JOB_ID, "started": True}
