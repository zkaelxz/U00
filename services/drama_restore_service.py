"""
services/drama_restore_service.py -- copy one drama (and its series, media
and child rows) out of a backup file into the live library: the
single-drama restore from an automatic backup copy, and the row/media
copy that backup_import_service reuses for importing from a file.

The copy picker, the manifest and the SQLite extraction stay in
auto_backup_service, which this module imports (never the reverse).
No FastAPI import.
"""

import contextlib
import datetime
import json
import logging
import os
import shutil
import sqlite3
import zipfile
import zlib

import db
import segment_splitting
import sensitivity_preset
import storage
from db import fsync_dir as _fsync_dir
from services import delete_service
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.auto_backup_service import (RESTORE_CONFIRM_TEXT, _BAD_SNAPSHOT, _pick_copy,
                                          _read_manifest, _snapshot_lock, extract_db,
                                          job_running, ro_uri)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

# What a single-drama restore extracts is capped per member (the whole-zip
# upload caps don't apply to the app's own snapshot; see _verify_snapshot).
_MAX_MEMBER_BYTES = wjs.MAX_RESTORE_MEMBER_BYTES


# Every table with a drama_id foreign key to dramas(id), in insert order,
# except those in _SKIPPED_TABLES. tests/test_auto_backup_service.py
# checks this against the live schema, so a new child table fails a test
# until it is listed in one or the other.
CHILD_TABLES = ("lines", "pages", "characters", "translation_notes", "line_emotions",
                 "consistency_issues", "vocab_lookups", "line_history", "translation_versions",
                 "bug_reports", "wiki_entries", "edit_samples", "voice_suggestion_dismissals",
                 "progress", "personal_notes", "reading_history", "metadata_field_provenance")
_SKIPPED_TABLES = {
    "usage_log": "ON DELETE SET NULL: the spending rows survive a delete, so restoring them "
                 "would count the cost twice",
    "speaker_merge_undos": "short-lived, single-use undo records; a restored one would "
                           "describe lines and rows that no longer match",
    "bulk_jobs": "provider batch jobs: a restored in-flight batch could be polled again and "
                 "write stale results over the restored lines",
    "metadata_research_results": "a short-lived research cache pruned by age, keyed by a "
                                 "research id a restored copy would collide with; the applied "
                                 "values and their sources (metadata_field_provenance) are "
                                 "restored",
}
_LINE_REF_TABLES = ("translation_notes", "line_emotions", "reading_history", "bug_reports")
_PROFILE_TABLES = ("progress", "personal_notes", "reading_history")
_LINE_JSON = {"translation_versions": "lines_json", "line_history": "snapshot_json"}
SERIES_CHILDREN = ("glossary_terms", "series_characters", "translation_memory",
                   "glossary_dismissals")
# Columns naming a file in the drama folder, with the one subfolder the app
# writes that file in (None: the folder itself). Readers join these onto the
# drama folder, so a backup from another library keeps one only when it is a
# name inside the new drama's own folder (see _import_file_ref).
IMPORT_FILE_COLUMNS = {
    "dramas": {"audio_filename": None, "source_video_filename": None,
               "novel_reference_filename": None, "cover_art_filename": None},
    "lines": {"dub_filename": "dub_clips"},
    "characters": {"ref_audio_filename": "voice_refs"},
    "pages": {"filename": "pages", "rendered_filename": "pages"},
}


def columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def has_table(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,)).fetchone() is not None


def _storable_words(value) -> bool:
    """Word timings stay within a transcription's size (a file may be
    hand-made); their content is rechecked on use."""
    return isinstance(value, str) and len(value) <= segment_splitting.MAX_STORED_WORD_BYTES


def _insert(dst, table: str, row: dict, live_cols) -> int:
    cols = [c for c in row if c in live_cols]
    names = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" for _ in cols)
    cur = dst.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                      [row[c] for c in cols])
    return cur.lastrowid


def table_rows(src, table: str, where: str, args) -> list:
    if not has_table(src, table):
        return []
    cur = src.execute(f'SELECT * FROM "{table}" WHERE {where}', args)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _import_file_ref(folder, value, subdir):
    """`value` (normalised to "/") when it is a plain file name, or
    "<subdir>/<plain name>", that stays inside `folder`; None otherwise,
    and always None when `folder` is None (the drama's files weren't
    imported). A name that is a folder there (e.g. "pages") is None: a
    file column naming it would let a media replace move the folder."""
    if folder is None or not isinstance(value, str) or "\x00" in value or ":" in value:
        return None
    parts = value.replace("\\", "/").split("/")
    if len(parts) == 1:
        base, name = folder, parts[0]
    elif len(parts) == 2 and subdir is not None and parts[0] == subdir:
        base, name = os.path.join(folder, subdir), parts[1]
    else:
        return None
    path = delete_service.file_in_folder(base, name)
    if path is None or os.path.isdir(path):
        return None
    return name if len(parts) == 1 else f"{subdir}/{name}"


def _sanitise_file_refs(table: str, row: dict, import_as):
    if import_as is None:
        return
    for col, subdir in IMPORT_FILE_COLUMNS.get(table, {}).items():
        if col in row:
            row[col] = _import_file_ref(import_as.get("media_dir"), row[col], subdir)


def _remap_json_lines(value, line_map, import_as=None):
    """Points the saved lines' ids at the new lines. import_as (see
    _copy_drama): each saved dub_filename is sanitised like lines'
    (restoring the version writes it back into lines), and a value that
    isn't a list of lines becomes an empty list."""
    try:
        items = json.loads(value) if value else None
    except (ValueError, RecursionError):
        return value if import_as is None else "[]"
    if not isinstance(items, list):
        return value if import_as is None else "[]"
    for item in items:
        if isinstance(item, dict) and "id" in item:
            item["id"] = line_map.get(item["id"])
        if import_as is not None and isinstance(item, dict) and "dub_filename" in item:
            item["dub_filename"] = _import_file_ref(import_as.get("media_dir"),
                                                    item["dub_filename"], "dub_clips")
    return json.dumps(items, ensure_ascii=False)


def _live_ids(dst, table: str) -> set:
    return {r[0] for r in dst.execute(f'SELECT id FROM "{table}"')}


def _free_series_name(dst, name, label="restored") -> str:
    base = (str(name) if name else "Series").strip() or "Series"
    today = datetime.date.today().isoformat()
    candidate = f"{base} ({label} {today})"
    n = 2
    while dst.execute("SELECT 1 FROM series WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base} ({label} {today}, {n})"
        n += 1
    return candidate


def _resolve_series(src, dst, series_id, drama_owner, users, counts, import_as=None) -> tuple:
    """(live series id or None, {old series_character id: live id},
    outcome "none" | "linked" | "recreated" | "dropped_private").
    A live series with the snapshot's id that is now someone else's
    private series (the db.assign_drama_series predicate: a private series
    takes only its owner's and the PC owner's dramas) means the drama comes
    back with no series ("dropped_private") and nothing of it is copied --
    checked on the id alone, so a series its owner has since made private
    is never re-shared. Otherwise the drama is linked only when the live
    series has the same id AND name (after a whole-library restore of an
    older library a new series can reuse an id the snapshot used for a
    different one), and a name match alone never links. In every other case
    the series comes back from the snapshot as a new series (renamed
    "... (restored <date>)" if its name is taken) with its glossary,
    characters and memory.
    import_as (see _copy_drama) skips all of that: the series is always
    created new, owned by the importing user, once per source series."""
    if series_id is None:
        return None, {}, "none"
    srows = table_rows(src, "series", "id = ?", (series_id,))
    if not srows:
        return None, {}, "none"
    series = srows[0]
    if import_as is not None:
        if series_id not in import_as["series"]:
            row = {k: v for k, v in series.items() if k != "id"}
            row["owner_user_id"] = import_as["owner_user_id"]
            row["is_private"] = import_as["is_private"]
            row["name"] = _free_series_name(dst, row.get("name"), "imported")
            import_as["series"][series_id] = _insert_series(src, dst, series_id, row, counts)
        live_id, char_map = import_as["series"][series_id]
        return live_id, char_map, "recreated"
    live = dst.execute("SELECT id, owner_user_id, COALESCE(is_private, 0), name FROM series "
                       "WHERE id = ?", (series_id,)).fetchone()
    if live is not None and live[2] and drama_owner is not None and drama_owner != live[1]:
        # Checked on the id alone, whatever the name: never copy or re-share
        # a series that is now someone else's private series.
        return None, {}, "dropped_private"
    if live is not None and live[3] == series.get("name"):
        live_chars = {r[0] for r in dst.execute(
            "SELECT id FROM series_characters WHERE series_id = ?", (series_id,))}
        return series_id, {c: c for c in live_chars}, "linked"
    row = {k: v for k, v in series.items() if k != "id"}
    if row.get("owner_user_id") not in users:
        row["owner_user_id"] = None
    if dst.execute("SELECT 1 FROM series WHERE name = ?", (row.get("name"),)).fetchone():
        row["name"] = _free_series_name(dst, row.get("name"))
    live_id, char_map = _insert_series(src, dst, series_id, row, counts)
    return live_id, char_map, "recreated"


def _insert_series(src, dst, series_id, row, counts) -> tuple:
    """Inserts the series row and its glossary, characters and memory;
    (live series id, {old series_character id: live id})."""
    live_id = _insert(dst, "series", row, columns(dst, "series"))
    counts["series"] = 1
    char_map = {}
    for table in SERIES_CHILDREN:
        live_cols = columns(dst, table)
        n = 0
        for child in table_rows(src, table, "series_id = ?", (series_id,)):
            old = child.pop("id", None)
            child["series_id"] = live_id
            new = _insert(dst, table, child, live_cols)
            if table == "series_characters":
                char_map[old] = new
            n += 1
        counts[table] = n
    return live_id, char_map


def copy_drama(src, dst, old_id: int, new_id, title_suffix, import_as=None) -> tuple:
    """Inserts the drama and its children into dst (inside the caller's
    transaction). new_id None = a fresh id. Returns (live id, counts,
    series outcome -- see _resolve_series).
    import_as (a backup from another library, see backup_import_service) =
    {"owner_user_id", "is_private", "series": {}, "media_dir"}: the owner
    and privacy come from it and never from the file, the series is always
    new, old `notion_page_id` is dropped, per-profile tables (profile ids
    mean something else here) are not copied, and each file
    reference (IMPORT_FILE_COLUMNS) is kept only when it names a file inside
    media_dir (None = no files imported, so all are cleared)."""
    drama = table_rows(src, "dramas", "id = ?", (old_id,))
    if not drama:
        raise NotFoundError("That drama isn't in the snapshot.")
    drama = drama[0]
    counts = {}
    users = {r[0] for r in dst.execute("SELECT id FROM users")}
    profiles = _live_ids(dst, "profiles")
    row = sensitivity_preset.older_row(drama)
    if import_as is not None:
        row["owner_user_id"] = import_as["owner_user_id"]
        row["is_private"] = import_as["is_private"]
        row["notion_page_id"] = None
        _sanitise_file_refs("dramas", row, import_as)
    elif row.get("owner_user_id") not in users:
        row["owner_user_id"] = None
    series_id, char_map, series_outcome = _resolve_series(
        src, dst, drama.get("series_id"), row["owner_user_id"], users, counts, import_as)
    row["series_id"] = series_id
    if series_id is not None:
        row["is_private"] = 0   # a drama in a series follows the series (decision 4)
    if new_id is None:
        row.pop("id", None)
    else:
        row["id"] = new_id
    if title_suffix:
        key = "title_en" if (row.get("title_en") or "").strip() else "title_zh"
        row[key] = f"{(row.get(key) or '').strip()} {title_suffix}".strip()
    row["updated_at"] = datetime.datetime.utcnow().isoformat()
    live_id = _insert(dst, "dramas", row, columns(dst, "dramas"))
    counts["dramas"] = 1

    line_map, page_map = {}, {}
    for table in CHILD_TABLES:
        if not has_table(dst, table):
            continue
        if import_as is not None and table in _PROFILE_TABLES:
            counts[table] = 0
            continue
        live_cols = columns(dst, table)
        n = 0
        for child in table_rows(src, table, "drama_id = ?", (old_id,)):
            old = child.pop("id", None)
            child["drama_id"] = live_id
            _sanitise_file_refs(table, child, import_as)
            if (table in _PROFILE_TABLES and child.get("profile_id") is not None
                    and child["profile_id"] not in profiles):
                continue
            if table in _LINE_REF_TABLES and child.get("line_id") is not None:
                child["line_id"] = line_map.get(child["line_id"])
                if child["line_id"] is None and table == "line_emotions":
                    continue
            if "series_character_id" in child and child["series_character_id"] is not None:
                child["series_character_id"] = char_map.get(child["series_character_id"])
                if child["series_character_id"] is None and table == "voice_suggestion_dismissals":
                    continue
            if table in _LINE_JSON:
                col = _LINE_JSON[table]
                child[col] = _remap_json_lines(child.get(col), line_map, import_as)
            if table == "lines" and not _storable_words(child.get("word_timings")):
                child.pop("word_timings", None)
            new = _insert(dst, table, child, live_cols)
            if table == "lines":
                line_map[old] = new
            elif table == "pages":
                page_map[old] = new
            n += 1
        counts[table] = n
    if page_map and has_table(src, "bubbles"):
        live_cols = columns(dst, "bubbles")
        n = 0
        for old_page, new_page in page_map.items():
            for bubble in table_rows(src, "bubbles", "page_id = ?", (old_page,)):
                bubble.pop("id", None)
                bubble["page_id"] = new_page
                _insert(dst, "bubbles", bubble, live_cols)
                n += 1
        counts["bubbles"] = n
    return live_id, counts, series_outcome


def stage_media(zf: zipfile.ZipFile, old_id: int, staging: str):
    """Extracts dramas/<old_id>/... into a new folder inside `staging` (an
    import's db.new_media_staging folder); returns its path, or None when
    the snapshot has no files for this drama. Member names were already
    checked by validate_backup_file; each target is re-checked to stay
    inside."""
    prefix = f"dramas/{old_id}/"
    members = [i for i in zf.infolist()
               if i.filename.replace("\\", "/").startswith(prefix) and not i.is_dir()]
    if not members:
        return None
    if len(members) > las.RESTORE_MAX_MEMBERS or any(
            i.file_size > _MAX_MEMBER_BYTES for i in members):
        raise InvalidInputError("The drama's files in the snapshot look corrupted or unsafe "
                                "to extract.")
    if not las.has_disk_room(db.DRAMAS_DIR, sum(i.file_size for i in members)):
        raise InvalidInputError("Not enough free disk space to restore this drama's files.")
    folder = os.path.join(staging, f"drama-{int(old_id)}")
    os.makedirs(folder)
    base = os.path.realpath(folder)
    try:
        for info in members:
            rel = info.filename.replace("\\", "/")[len(prefix):]
            dest = os.path.realpath(os.path.join(folder, *rel.split("/")))
            if not dest.startswith(base + os.sep):
                raise InvalidInputError("The backup contains an unsafe file path.")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def claim_folder(drama_id: int, conflict: str):
    """dramas/<drama_id> must not exist for a drama being added: a leftover
    of an import that never committed is cleared, anything else is renamed
    aside (db.claim_new_drama_folder); 409 when that fails."""
    try:
        db.claim_new_drama_folder(drama_id)
    except db.DramaFolderConflict:
        raise ConflictError(conflict) from None


def move_media_in(staging: str, folders: dict, conflict: str):
    """Call inside the transaction, with the new drama rows inserted:
    {new drama id: staged folder}. Refuses before moving anything when a
    new id's folder already exists, records the journal (db's media
    journal), then renames each staged folder to dramas/<id>. The caller
    commits next; on any failure it calls _end_media_staging."""
    for did in folders:
        claim_folder(did, conflict)
    db.write_media_journal(staging, folders)
    for did, path in folders.items():
        if not db.move_staged_folder(staging, did, path):
            raise ConflictError(conflict)
    _fsync_dir(db.DRAMAS_DIR)


def end_media_staging(staging: str) -> bool:
    """Ends an import's staging (see db.finish_media_staging): after a
    commit only the markers, journal and staging folder go; otherwise the
    folders it moved into place go too. False when something was left in
    place (logged by id; the journal stays, so the next start retries)."""
    try:
        return db.finish_media_staging(staging)
    except (OSError, sqlite3.Error):
        log.warning("An import's staging folder could not be removed")
        return False


def restore_drama(drama_id, confirm=False, confirm_text="", actor_id=None,
                  snapshot=None) -> dict:
    """Restores one drama from a copy: the one named `snapshot` (matched
    against the backup folder's listing), else the newest readable one.
    Needs confirm=True and
    confirm_text "RESTORE". Refused while a restore, bulk delete, storage
    cleanup, backup or export holds the library. The drama keeps its id
    when that id (and its folder) are free, else it becomes a new drama
    titled "... (restored <date>)". Returns {drama_id, restored_as_new,
    title, media_restored, snapshot (the copy's name), snapshot_kind,
    series, counts, skipped_tables}."""
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("A drama id is a positive whole number.")
    las.require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a drama")
    if job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with las.maintenance("restoring a drama"), storage.job_workdir() as tmp:
        db.recover_media_imports()
        staging = staged = None
        with _snapshot_lock:
            copy = _pick_copy(snapshot)[0]
            path = copy["path"]
            las.validate_backup_file(path, check_disk=False, check_limits=False)
            try:
                with zipfile.ZipFile(path) as zf:
                    manifest = _read_manifest(zf)
                    if drama_id not in [d.get("id") for d in manifest["dramas"]
                                        if isinstance(d, dict)]:
                        raise NotFoundError("That drama isn't in the snapshot.")
                    snap_db = extract_db(zf, tmp)
                    if manifest["kind"] == "full":
                        staging = db.new_media_staging()
                        staged = stage_media(zf, drama_id, staging)
            except (OSError, zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error):
                if staging is not None:
                    end_media_staging(staging)
                raise InvalidInputError(_BAD_SNAPSHOT) from None
            except BaseException:
                if staging is not None:
                    end_media_staging(staging)
                raise
        try:
            return _restore_from(snap_db, drama_id, staging, staged, manifest, actor_id,
                                 copy["name"])
        finally:
            if staging is not None:
                end_media_staging(staging)


def _restore_from(snap_db, drama_id, staging, staged, manifest, actor_id, copy_name) -> dict:
    folder_free = not os.path.lexists(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    keep_id = db.get_drama(drama_id) is None and folder_free
    suffix = None if keep_id else f"(restored {datetime.date.today().isoformat()})"
    conflict = ("A folder for the restored drama is already in the library's dramas folder "
                "and could not be moved aside; nothing was restored.")
    with contextlib.closing(sqlite3.connect(ro_uri(snap_db), uri=True)) as src, \
            contextlib.closing(db.get_conn()) as dst:
        src.execute("PRAGMA trusted_schema = OFF")
        try:
            dst.execute("BEGIN IMMEDIATE")
            live_id, counts, series_outcome = copy_drama(src, dst, drama_id, drama_id if keep_id else None,
                                          suffix)
            claim_folder(live_id, conflict)   # never inherit a stray dramas/<new id>
            if staged is not None:
                move_media_in(staging, {live_id: staged}, conflict)
            dst.commit()
        except BaseException as exc:
            dst.rollback()
            if staging is not None and not end_media_staging(staging):
                raise ServiceError("The drama was not restored, but some of its files could not "
                                   "be cleaned up and are still in the library's dramas folder; "
                                   "the app tries again at the next start.") from None
            if isinstance(exc, (ServiceError, KeyboardInterrupt, SystemExit)):
                raise
            log.warning("Single-drama restore failed: %s", type(exc).__name__)
            raise ServiceError("The drama could not be restored; nothing was changed.") \
                from None
        title = dst.execute("SELECT COALESCE(NULLIF(title_en, ''), title_zh) FROM dramas "
                            "WHERE id = ?", (live_id,)).fetchone()[0]
    try:
        from services import auth_service
        auth_service.write_audit(actor_id, "library.restore_drama",
                                 f"drama {drama_id} restored from backup copy {copy_name} "
                                 f"as drama {live_id}")
    except Exception:
        log.warning("Could not write the audit entry for a drama restore")
    return {"drama_id": live_id, "restored_as_new": not keep_id, "title": title or "",
            "media_restored": staged is not None, "snapshot": copy_name,
            "snapshot_kind": manifest["kind"],
            "series": series_outcome,
            "counts": counts, "skipped_tables": sorted(_SKIPPED_TABLES)}
