"""
services/backup_import_service.py -- import chosen dramas from a backup file
into the current library without replacing it.

The file is a backup zip (an automatic snapshot copy, which has a
manifest.json, or a manual "Back up library" zip, which has none) or the bare
library.db of a database-only backup. Nothing is trusted from it: the
dramas are listed from its database, never from a manifest; every imported
drama is a NEW drama (never overwriting one); the owner and privacy come
from the acting user (ownership_service.new_item_defaults), never from the
file; a series comes back as a new series ("... (imported <date>)"), shared
by the imported dramas that shared it, never merged into an existing one.
The row copy is auto_backup_service._copy_drama (the one-drama restore).

Written: dramas, lines, characters and the other per-drama tables, plus the
series, glossary, series characters and translation memory of the dramas'
series, and (when the file has them) each drama's media folder. Not written:
per-profile reading progress and personal notes (profile ids mean something
else here), usage/bulk-job/research-cache tables (see
auto_backup_service._SKIPPED_TABLES), the drama's Notion page link, and
anything outside the chosen dramas. Responses hold ids, titles and counts,
never paths.

File references (FILE_COLUMNS: audio, video, novel reference, cover, dub
clip, voice reference and page image names) are sanitised, because readers
join them onto the drama folder: one is kept only when it is a plain name
(or "<the app's subfolder>/<plain name>", e.g. pages/) inside the new
drama's own folder AND that drama's files were imported; otherwise it is
cleared, so a database-only import has no audio, cover or pages to show.
The file's database is only read from ordinary tables (a view or virtual
table under a name the import reads rejects the file), and every read of it
stops at a deadline.
"""

import contextlib
import datetime
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile

import db
from services import auto_backup_service as abs_
from services import library_admin_service as las
from services import media_upload_service
from services import ownership_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

RESTORE_CONFIRM_TEXT = las.RESTORE_CONFIRM_TEXT

MAX_DRAMAS_PER_IMPORT = 200
MAX_LISTED_DRAMAS = 5000
_MAX_DB_BYTES = 1024 ** 3
_MAX_ROWS_PER_IMPORT = 3_000_000
_SQLITE_MAGIC = b"SQLite format 3\x00"
_BAD_FILE = "That file isn't a Baihe backup, or it is damaged."
_TOO_LARGE = "The uploaded file is too large."
# How long reads of the uploaded database may run: a crafted file must not
# hold the request (and, for an import, the maintenance lock) indefinitely.
_READ_TIME_LIMIT_S = 30
_IMPORT_TIME_LIMIT_S = 300
# Every table the import reads from the file; each must be an ordinary table.
_READ_TABLES = (("dramas", "lines", "series", "bubbles") + abs_._CHILD_TABLES
                + abs_._SERIES_CHILDREN)
# The dramas/lines/characters columns the import treats specially; every
# other column is copied as is. The coverage test in
# tests/test_api_backup_import.py fails when a column is in none of the sets.
FORCED_COLUMNS = {"dramas": {"id", "series_id", "owner_user_id", "is_private", "updated_at"},
                  "lines": {"id", "drama_id"},
                  "characters": {"id", "drama_id", "series_character_id"}}
IGNORED_COLUMNS = {"dramas": {"notion_page_id"}, "lines": set(), "characters": set()}
# File references: copied only after sanitising (see the module docstring).
FILE_COLUMNS = {t: set(cols) for t, cols in abs_.IMPORT_FILE_COLUMNS.items()}


def _save_upload(stream, dest: str):
    """Streams the upload to `dest`, refused past BAIHE_MAX_UPLOAD_MB."""
    limit = media_upload_service.max_upload_bytes()
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise InvalidInputError(_TOO_LARGE)
            out.write(chunk)
    if total == 0:
        raise InvalidInputError("Choose a backup file.")


def _open_db(path: str, time_limit: float = None):
    """Read-only connection whose statements are interrupted (an
    sqlite3.OperationalError) once `time_limit` seconds have passed."""
    conn = sqlite3.connect(abs_._ro_uri(path), uri=True)
    conn.execute("PRAGMA trusted_schema = OFF")
    deadline = time.monotonic() + (_READ_TIME_LIMIT_S if time_limit is None else time_limit)
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    return conn


def _check_tables(conn):
    """Every table the import reads is an ordinary table under its own
    (lower-case) name, and dramas and lines exist."""
    marks = ",".join("?" for _ in _READ_TABLES)
    found = set()
    for name, kind, sql in conn.execute(
            f"SELECT name, type, sql FROM sqlite_master WHERE lower(name) IN ({marks})",
            _READ_TABLES).fetchall():
        virtual = str(sql or "").lstrip().upper().startswith("CREATE VIRTUAL")
        if kind != "table" or virtual or name not in _READ_TABLES:
            raise InvalidInputError(_BAD_FILE)
        found.add(name)
    if not {"dramas", "lines"} <= found:
        raise InvalidInputError(_BAD_FILE)


def _check_db(path: str):
    try:
        with contextlib.closing(_open_db(path)) as conn:
            ok = conn.execute("PRAGMA quick_check").fetchone()
            if not ok or ok[0] != "ok":
                raise InvalidInputError(_BAD_FILE)
            _check_tables(conn)
            conn.execute("SELECT id, title_en, title_zh, media_type FROM dramas LIMIT 1"
                         ).fetchall()
            conn.execute("SELECT drama_id FROM lines LIMIT 1").fetchall()
    except sqlite3.Error:
        raise InvalidInputError(_BAD_FILE) from None


class _Backup:
    """An uploaded backup opened for reading: `db_path` is the extracted
    (or uploaded) database, `zip_path` the zip (None for a bare database)."""

    def __init__(self, tmp: str, stream):
        upload = os.path.join(tmp, "upload")
        _save_upload(stream, upload)
        with open(upload, "rb") as fh:
            head = fh.read(len(_SQLITE_MAGIC))
        self.zip_path = None
        if head == _SQLITE_MAGIC:
            if os.path.getsize(upload) > _MAX_DB_BYTES:
                raise InvalidInputError(_BAD_FILE)
            self.db_path = upload
        else:
            las.validate_backup_file(upload, check_disk=False, check_limits=True)
            try:
                with zipfile.ZipFile(upload) as zf:
                    self.db_path = abs_._extract_db(zf, tmp, _MAX_DB_BYTES)
            except (OSError, zipfile.BadZipFile, KeyError):
                raise InvalidInputError(_BAD_FILE) from None
            self.zip_path = upload
        _check_db(self.db_path)

    def media_ids(self) -> set:
        """Old drama ids that have files under dramas/<id>/."""
        if self.zip_path is None:
            return set()
        found = set()
        with zipfile.ZipFile(self.zip_path) as zf:
            for name in zf.namelist():
                parts = name.replace("\\", "/").split("/")
                if len(parts) > 2 and parts[0] == "dramas" and parts[2] and _plain_id(parts[1]):
                    found.add(int(parts[1]))
        return found


def _plain_id(text: str) -> bool:
    """ASCII digits in canonical form ("7", never "07" or a superscript)."""
    return text.isascii() and text.isdigit() and str(int(text)) == text


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _drama_summaries(backup: _Backup) -> list:
    media = backup.media_ids()
    try:
        with contextlib.closing(_open_db(backup.db_path)) as conn:
            rows = conn.execute("SELECT id, title_en, title_zh, media_type FROM dramas "
                                "ORDER BY id LIMIT ?", (MAX_LISTED_DRAMAS,)).fetchall()
            counts = dict(conn.execute(
                "SELECT drama_id, COUNT(*) FROM lines WHERE drama_id IN "
                "(SELECT id FROM dramas ORDER BY id LIMIT ?) GROUP BY drama_id",
                (MAX_LISTED_DRAMAS,)).fetchall())
    except sqlite3.Error:
        raise InvalidInputError(_BAD_FILE) from None
    out = []
    for did, en, zh, media_type in rows:
        if isinstance(did, bool) or not isinstance(did, int) or did < 1:
            continue
        n = counts.get(did, 0)
        out.append({"id": did,
                    "title": _text(en, 300) or _text(zh, 300) or f"Drama {did}",
                    "media_type": _text(media_type, 40),
                    "line_count": n if isinstance(n, int) else 0,
                    "has_media": did in media})
    return out


def _schema_differs(backup: _Backup) -> bool:
    """True when the file's dramas/lines/characters tables have columns
    this version doesn't know (a newer app made it; they are ignored)."""
    try:
        with contextlib.closing(_open_db(backup.db_path)) as conn, \
                contextlib.closing(db.get_conn()) as dst:
            for table in ("dramas", "lines", "characters"):
                if not abs_._has_table(conn, table):
                    continue
                if set(abs_._columns(conn, table)) - set(abs_._columns(dst, table)):
                    return True
    except sqlite3.Error:
        raise InvalidInputError(_BAD_FILE) from None
    return False


def list_backup_dramas(stream) -> dict:
    """The dramas inside an uploaded backup file: {kind ("zip" | "database"),
    media_available, schema_differs, dramas: [{id, title, media_type,
    line_count, has_media}]}. Touches nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        backup = _Backup(tmp, stream)
        dramas = _drama_summaries(backup)
        return {"kind": "zip" if backup.zip_path else "database",
                "media_available": any(d["has_media"] for d in dramas),
                "schema_differs": _schema_differs(backup), "dramas": dramas}


def _check_ids(drama_ids) -> list:
    if not isinstance(drama_ids, (list, tuple)) or not drama_ids:
        raise InvalidInputError("Pick at least one drama.")
    if len(drama_ids) > MAX_DRAMAS_PER_IMPORT:
        raise InvalidInputError(f"At most {MAX_DRAMAS_PER_IMPORT} dramas per import.")
    out = []
    for did in drama_ids:
        if isinstance(did, bool) or not isinstance(did, int) or not 1 <= did <= 2 ** 31 - 1:
            raise InvalidInputError("A drama id is a positive whole number.")
        if did not in out:
            out.append(did)
    return out


def _check_row_limits(src, ids: list):
    """Refuses an import whose rows (the dramas' children, their pages'
    bubbles and their series' glossary, characters and memory) exceed
    _MAX_ROWS_PER_IMPORT; each of those tables is read whole per drama."""
    total = 0
    marks = ",".join("?" for _ in ids)
    wheres = [(table, f"drama_id IN ({marks})") for table in abs_._CHILD_TABLES]
    if abs_._has_table(src, "pages"):
        wheres.append(("bubbles", f"page_id IN (SELECT id FROM pages WHERE drama_id IN ({marks}))"))
    wheres += [(table, f"series_id IN (SELECT series_id FROM dramas WHERE id IN ({marks}))")
               for table in abs_._SERIES_CHILDREN]
    for table, where in wheres:
        if not abs_._has_table(src, table):
            continue
        total += src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', ids).fetchone()[0]
        if total > _MAX_ROWS_PER_IMPORT:
            raise InvalidInputError("Those dramas hold too many rows to import at once; "
                                    "import fewer of them.")


def _live_titles() -> set:
    return {(d.get("title_en") or d.get("title_zh") or "").strip().casefold()
            for d in db.list_dramas()}


def import_dramas(stream, drama_ids, confirm=False, confirm_text="", principal=None) -> dict:
    """Imports the chosen dramas (ids inside the file, as listed by
    list_backup_dramas) as new dramas. Needs confirm=True and confirm_text
    "RESTORE"; refused while a backup, export, restore or cleanup holds the
    library. All chosen dramas are imported or none. A drama whose title
    matches one already here is titled "... (restored <date>)".
    Returns {imported: [{source_id, drama_id, title, media_imported}],
    series_created, media_imported, counts}."""
    ids = _check_ids(drama_ids)
    las._require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Importing dramas")
    if abs_._job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with las._maintenance("importing dramas"), tempfile.TemporaryDirectory() as tmp:
        backup = _Backup(tmp, stream)
        stagings = {}
        try:
            try:
                with contextlib.closing(_open_db(backup.db_path)) as src:
                    marks = ",".join("?" for _ in ids)
                    present = {r[0] for r in src.execute(
                        f"SELECT id FROM dramas WHERE id IN ({marks})", ids)}
                    if any(i not in present for i in ids):
                        raise NotFoundError("A chosen drama isn't in that file.")
                    _check_row_limits(src, ids)
            except sqlite3.Error:
                raise InvalidInputError(_BAD_FILE) from None
            if backup.zip_path is not None:
                media = backup.media_ids()
                try:
                    with zipfile.ZipFile(backup.zip_path) as zf:
                        for did in ids:
                            if did in media:
                                stagings[did] = abs_._stage_media(zf, did)
                except (OSError, zipfile.BadZipFile):
                    raise InvalidInputError(_BAD_FILE) from None
            return _import_from(backup.db_path, ids, stagings, principal)
        finally:
            for path in stagings.values():
                if path is not None and os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)


def _import_from(snap_db, ids, stagings, principal) -> dict:
    defaults = ownership_service.new_item_defaults(principal)
    imported, moved, totals = [], [], {}
    today = datetime.date.today().isoformat()
    titles = _live_titles()   # before dst opens: db helpers close the connection they use
    with contextlib.closing(_open_db(snap_db, _IMPORT_TIME_LIMIT_S)) as src, \
            contextlib.closing(db.get_conn()) as dst:
        users = {r[0] for r in dst.execute("SELECT id FROM users")}
        owner = defaults["owner_user_id"]
        import_as = {"owner_user_id": owner if owner in users else None,
                     "is_private": defaults["is_private"], "series": {}, "media_dir": None}
        try:
            dst.execute("BEGIN IMMEDIATE")
            for old_id in ids:
                row = abs_._rows(src, "dramas", "id = ?", (old_id,))[0]
                title = (_text(row.get("title_en"), 300) or _text(row.get("title_zh"), 300))
                suffix = f"(restored {today})" if title.casefold() in titles else None
                staging = stagings.get(old_id)
                import_as["media_dir"] = staging
                live_id, counts, _ = abs_._copy_drama(src, dst, old_id, None, suffix, import_as)
                shown = dst.execute("SELECT COALESCE(NULLIF(title_en, ''), title_zh) FROM dramas "
                                    "WHERE id = ?", (live_id,)).fetchone()[0] or ""
                titles.add(str(shown).strip().casefold())
                if staging is not None:
                    final = os.path.join(db.DRAMAS_DIR, str(live_id))
                    if os.path.lexists(final):
                        raise ConflictError("A folder for an imported drama already exists; "
                                            "nothing was imported.")
                    os.rename(staging, final)
                    moved.append((final, staging))
                for key, n in counts.items():
                    totals[key] = totals.get(key, 0) + n
                imported.append({"source_id": old_id, "drama_id": live_id, "title": str(shown),
                                 "media_imported": staging is not None})
            dst.commit()
        except BaseException as exc:
            dst.rollback()
            for final, staging in reversed(moved):
                with contextlib.suppress(OSError):
                    os.rename(final, staging)
            if isinstance(exc, (ServiceError, KeyboardInterrupt, SystemExit)):
                raise
            log.warning("Drama import failed: %s", type(exc).__name__)
            raise ServiceError("The dramas could not be imported; nothing was changed.") \
                from None
    try:
        from services import auth_service
        auth_service.write_audit(
            ownership_service._user_id(principal), "library.import_dramas",
            "dramas " + ",".join(str(i["source_id"]) for i in imported) + " imported as "
            + ",".join(str(i["drama_id"]) for i in imported))
    except Exception:
        log.warning("Could not write the audit entry for a drama import")
    return {"imported": imported, "series_created": len(import_as["series"]),
            "media_imported": sum(1 for i in imported if i["media_imported"]),
            "counts": totals}
