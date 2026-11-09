"""services/title_copy_service.py -- Copies a title (row, media files, lines, speakers, comic pages) into an independent test copy."""

import contextlib
import logging
import os
import shutil

import db
from services import drama_service, ownership_service
from lib.errors import ConflictError, ServiceError

log = logging.getLogger(__name__)

COPY_TAG = "Test copy"
# Not carried over: identity and timestamps are new; links back to the
# original would let the extension or a sync treat the copy as the same title.
_NOT_COPIED = ("id", "created_at", "updated_at", "notion_page_id", "source_url",
               "source_url_full", "episode_number", "last_translate_errors",
               "owner_user_id", "is_private", "series_id")
# Per-title tables copied row for row. Line ids are new, so tables that point
# at a line id (notes, emotions, reading history) are left out.
_CHILD_TABLES = ("lines", "characters", "pages")


def _columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _folder_bytes(folder: str) -> int:
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(folder)
               for f in files if os.path.isfile(os.path.join(root, f)))


def _copy_name(source: dict) -> str:
    return f"{source.get('title_en') or source.get('title_zh') or 'Untitled'} (copy)"


def _tags(source: dict) -> str:
    tags = [t.strip() for t in (source.get("custom_tags") or "").split(",") if t.strip()]
    if COPY_TAG.lower() not in (t.lower() for t in tags):
        tags.append(COPY_TAG)
    return ", ".join(tags)


def _series_for_copy(principal, source: dict, owner_user_id):
    """Keeps the original's series (so the glossary applies) unless the
    caller may not file a title there."""
    series_id = source.get("series_id")
    if series_id is None:
        return None
    try:
        ownership_service.check_series_assignment(principal, series_id, owner_user_id)
    except ServiceError:
        return None
    return series_id


def _copy_rows(conn, source_id: int, new_id: int) -> None:
    for table in _CHILD_TABLES:
        cols = [c for c in _columns(conn, table) if c not in ("id", "drama_id")]
        names = ", ".join(cols)
        conn.execute(f"INSERT INTO {table} (drama_id, {names}) "
                     f"SELECT ?, {names} FROM {table} WHERE drama_id = ? ORDER BY id",
                     (new_id, source_id))


def _copy_folder(source_id: int, new_id: int) -> None:
    src = os.path.join(db.DRAMAS_DIR, str(source_id))
    if not os.path.isdir(src):
        return
    free = shutil.disk_usage(db.DRAMAS_DIR).free
    if _folder_bytes(src) * 1.1 > free:
        raise ConflictError("Not enough free disk space to copy this title's files.")
    # Real copies, never hard links: a tool that rewrites a file in place
    # would otherwise change the original through the copy.
    shutil.copytree(src, os.path.join(db.DRAMAS_DIR, str(new_id)), dirs_exist_ok=True)


def duplicate_title(source_id: int, principal=None) -> dict:
    """Makes "<name> (copy)" tagged "Test copy" with its own copy of the
    original's files, lines, speakers and pages, in the original's series.
    Refused while a job is running on the original. The copy starts with no
    jobs, and the original is only read. On any failure the copy is removed."""
    ownership_service.require_visible(principal, "drama", source_id)
    if drama_service.job_running_for_drama(source_id):
        raise ConflictError("A job is running on this title. Wait for it to finish, "
                            "then copy it.")
    owned = ownership_service.new_item_defaults(principal)
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM dramas WHERE id = ?", (source_id,)).fetchone()
    source = dict(row)
    fields = {k: v for k, v in source.items() if k not in _NOT_COPIED}
    fields.update(owned, title_en=_copy_name(source), custom_tags=_tags(source))
    series_id = _series_for_copy(principal, source, owned["owner_user_id"])
    if series_id is not None:
        fields["series_id"] = series_id
    try:
        new_id = db.create_drama(**fields)
    except db.DramaFolderConflict as exc:
        raise ConflictError(str(exc)) from None
    try:
        _copy_folder(source_id, new_id)
        with contextlib.closing(db.get_conn()) as conn:
            _copy_rows(conn, source_id, new_id)
            conn.commit()
    except Exception:
        db.delete_drama(new_id)
        raise
    from services import library_service
    return library_service.get_library_drama(new_id)
