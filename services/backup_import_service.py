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
The row copy is drama_restore_service.copy_drama (the one-drama restore).

Written: dramas, lines, characters and the other per-drama tables, plus the
series, glossary, series characters and translation memory of the dramas'
series, the dramas' usage (spend) rows, and (when the file has them) each
drama's media folder. Not written:
per-profile reading progress and personal notes (profile ids mean something
else here), bulk-job/research-cache tables (see
drama_restore_service._SKIPPED_TABLES), a usage row this library already
has, `notion_page_id` (old backups may carry it; dropped on import), and
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
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
import zipfile

from lib import clock
from lib import ids as lib_ids
import db
import storage
from services import auto_backup_files as abf_
from services import auto_backup_service as abs_
from services import drama_restore_service as drs
from services import library_admin_service as las
from services import library_backup_restore_service as lbr
from services import media_upload_service
from services import ownership_service
from lib.errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

RESTORE_CONFIRM_TEXT = las.RESTORE_CONFIRM_TEXT

MAX_DRAMAS_PER_IMPORT = 200
MAX_LISTED_DRAMAS = 5000
_MAX_DB_BYTES = 1024 ** 3
_MAX_ROWS_PER_IMPORT = 3_000_000
# Byte caps on what an import reads (values are loaded whole, and JSON ones
# parsed): one value, and all the chosen dramas' rows together.
_MAX_VALUE_BYTES = 64 * 1024 ** 2
_MAX_IMPORT_BYTES = 512 * 1024 ** 2
_SQLITE_MAGIC = b"SQLite format 3\x00"
_BAD_FILE = "That file isn't a Baihe backup, or it is damaged."
_FOLDER_EXISTS = ("A folder for an imported title is already in the library's dramas folder "
                  "and could not be moved aside; nothing was imported.")
# How long reads of the uploaded database may run: a crafted file must not
# hold the request (and, for an import, the maintenance lock) indefinitely.
_READ_TIME_LIMIT_S = 30
_IMPORT_TIME_LIMIT_S = 300
# Every table the import reads from the file; each must be an ordinary table.
_READ_TABLES = (("dramas", "lines", "series", "bubbles", "app_settings", "voice_bank", "usage_log")
                + drs.CHILD_TABLES + drs.SERIES_CHILDREN)
# The dramas/lines/characters columns the import treats specially; every
# other column is copied as is. The coverage test in
# tests/test_api_backup_import.py fails when a column is in none of the sets.
FORCED_COLUMNS = {"dramas": {"id", "series_id", "owner_user_id", "created_by_user_id",
                             "is_private", "updated_at"},
                  # mark: an unknown value comes in as none (db.settle_copied_marks).
                  "lines": {"id", "drama_id", "mark"},
                  "characters": {"id", "drama_id", "series_character_id"}}
IGNORED_COLUMNS = {"dramas": {"notion_page_id"}, "lines": set(), "characters": set()}
# File references: copied only after sanitising (see the module docstring).
FILE_COLUMNS = {t: set(cols) for t, cols in drs.IMPORT_FILE_COLUMNS.items()}


MAX_VOICE_BANK_ENTRIES = 1000
_VOICE_BANK_TEXT = {"name": 300, "ref_text": 20_000, "clone_engine": 100, "voice_design": 20_000,
                    "language": 40, "notes": 20_000, "source_drama": 300, "source_speaker": 300}
_VOICE_BANK_DIRNAME = "voice_bank"
# Settings an import may take from the file: the Translation and Preferences
# pages' own fields. Not the System page (GPU, memory, voice detection: this
# PC's hardware), not App updates (how this PC's install updates), not a card
# with its own UI or state the app keeps for itself (no tab), and never a key,
# a path or anything a whole-library restore keeps from this library.
_SETTINGS_TABS = ("translation", "preferences")
_SETTINGS_SKIPPED_SECTIONS = ("App updates",)
KEYS_NOT_IMPORTED = ("API keys are never in a backup: enter them again in Settings > "
                     "Translation and keys.")


def _importable_settings() -> dict:
    """{store key: setting key} of the settings an import may take."""
    from lib import settings_schema
    from services import library_restore_service as lrs
    kept = set(lrs._RESTORE_KEPT_APP_SETTINGS)
    return {st.store_key: st.key for st in settings_schema.SETTINGS
            if st.store == "db" and st.tab in _SETTINGS_TABS and not st.custom
            and not st.secret and st.type != "path" and not st.dev_only
            and st.section not in _SETTINGS_SKIPPED_SECTIONS and st.store_key not in kept}


def _save_upload(stream, dest: str):
    """Streams the upload to `dest`, refused past the upload limit."""
    limit = media_upload_service.max_upload_bytes()
    media_upload_service._check_disk_room(os.path.dirname(dest) or ".", stream)
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise InvalidInputError(media_upload_service.too_large_message(limit))
            out.write(chunk)
    if total == 0:
        raise InvalidInputError("Choose a backup file.")


def _open_db(path: str, time_limit: float | None = None):
    """Read-only connection whose statements are interrupted (an
    sqlite3.OperationalError) once `time_limit` seconds have passed."""
    conn = sqlite3.connect(abf_.ro_uri(path), uri=True)
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
            lbr.validate_backup_file(upload, check_disk=False, check_limits=True)
            try:
                with zipfile.ZipFile(upload) as zf:
                    # No SQL runs on it until _check_db's guarded connection.
                    self.db_path = abf_.extract_db(zf, tmp, _MAX_DB_BYTES, check=False)
            except (OSError, zipfile.BadZipFile, KeyError):
                raise InvalidInputError(_BAD_FILE) from None
            self.zip_path = upload
        _check_db(self.db_path)

    def voice_bank_clips(self) -> dict:
        """{clip file name: ZipInfo} of the files under voice_bank/ (plain
        names only, the only kind the bank reads)."""
        if self.zip_path is None:
            return {}
        out = {}
        with zipfile.ZipFile(self.zip_path) as zf:
            for info in zf.infolist():
                parts = info.filename.replace("\\", "/").split("/")
                if (len(parts) == 2 and parts[0] == _VOICE_BANK_DIRNAME and parts[1]
                        and parts[1] not in (".", "..") and not info.is_dir()):
                    out[parts[1]] = info
        return out

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
    """True when a table the import reads has columns this version doesn't
    know (a newer app made the file; they are ignored)."""
    try:
        with contextlib.closing(_open_db(backup.db_path)) as conn, \
                contextlib.closing(db.get_conn()) as dst:
            for table in _READ_TABLES:
                # A table this install lacks (one a fresh install no longer makes) is created
                # by the copy, so nothing in it is left out.
                if not drs.has_table(conn, table) or not drs.has_table(dst, table):
                    continue
                if set(drs.columns(conn, table)) - set(drs.columns(dst, table)):
                    return True
    except sqlite3.Error:
        raise InvalidInputError(_BAD_FILE) from None
    return False


def _voice_bank_rows(backup: _Backup) -> list:
    """The file's voice bank rows whose clip is in the file, at most
    MAX_VOICE_BANK_ENTRIES (a database-only backup has no clips, so none)."""
    clips = backup.voice_bank_clips()
    if not clips:
        return []
    try:
        with contextlib.closing(_open_db(backup.db_path)) as conn:
            if not drs.has_table(conn, "voice_bank"):
                return []
            rows = drs.table_rows(conn, "voice_bank", "1 ORDER BY id LIMIT ?",
                                  (MAX_VOICE_BANK_ENTRIES + 1,))
    except sqlite3.Error:
        raise InvalidInputError(_BAD_FILE) from None
    if len(rows) > MAX_VOICE_BANK_ENTRIES:
        raise InvalidInputError(f"The file's voice bank has more than {MAX_VOICE_BANK_ENTRIES} "
                                "entries, more than an import takes.")
    out = []
    for row in rows:
        info = clips.get(row.get("clip_filename")) if isinstance(row.get("clip_filename"),
                                                                 str) else None
        if info is None or info.file_size > drs._MAX_MEMBER_BYTES:
            continue
        entry = {col: (row.get(col) if isinstance(row.get(col), str) else None)
                 for col in _VOICE_BANK_TEXT}
        # Read as a number, not text, and kept: a private voice must not turn public on import.
        entry["is_private"] = row.get("is_private") in (1, True)
        if not (entry["name"] or "").strip() or any(
                v is not None and len(v) > _VOICE_BANK_TEXT[c] for c, v in entry.items() if c in _VOICE_BANK_TEXT):
            continue
        out.append((entry, info))
    return out


def _settings_rows(backup: _Backup) -> dict:
    """{setting key: stored value} of the importable settings the file holds."""
    wanted = _importable_settings()
    try:
        with contextlib.closing(_open_db(backup.db_path)) as conn:
            if not drs.has_table(conn, "app_settings"):
                return {}
            marks = ",".join("?" for _ in wanted)
            rows = conn.execute(f"SELECT key, value FROM app_settings WHERE key IN ({marks})",
                                list(wanted)).fetchall()
    except sqlite3.Error:
        raise InvalidInputError(_BAD_FILE) from None
    out = {}
    for key, value in rows:
        if not isinstance(value, str) or len(value) > _MAX_VALUE_BYTES:
            continue
        try:
            out[wanted[key]] = json.loads(value)
        except (ValueError, RecursionError):
            continue
    return out


def list_backup_dramas(stream) -> dict:
    """The dramas inside an uploaded backup file: {kind ("zip" | "database"),
    media_available, schema_differs, voice_bank_entries, settings_available,
    dramas: [{id, title, media_type, line_count, has_media}]}. Touches nothing."""
    with storage.job_workdir("backup_import") as tmp:
        backup = _Backup(tmp, stream)
        dramas = _drama_summaries(backup)
        return {"kind": "zip" if backup.zip_path else "database",
                "media_available": any(d["has_media"] for d in dramas),
                "schema_differs": _schema_differs(backup),
                "voice_bank_entries": len(_voice_bank_rows(backup)),
                "settings_available": bool(_settings_rows(backup)), "dramas": dramas}


def _check_ids(drama_ids) -> list:
    if not isinstance(drama_ids, (list, tuple)) or not drama_ids:
        raise InvalidInputError("Pick at least one title.")
    if len(drama_ids) > MAX_DRAMAS_PER_IMPORT:
        raise InvalidInputError(f"At most {MAX_DRAMAS_PER_IMPORT} titles per import.")
    out = []
    for did in drama_ids:
        lib_ids.check_id(did, "A title id is a positive whole number.")
        if did not in out:
            out.append(did)
    return out


def _check_row_limits(src, ids: list):
    """Refuses an import whose rows (the dramas' children, their pages'
    bubbles and their series' glossary, characters and memory) exceed
    _MAX_ROWS_PER_IMPORT, or whose values (those rows plus the dramas and
    series rows) exceed _MAX_VALUE_BYTES each or _MAX_IMPORT_BYTES in all;
    each of those tables is read whole per drama."""
    total = 0
    marks = ",".join("?" for _ in ids)
    in_series = f"IN (SELECT series_id FROM dramas WHERE id IN ({marks}))"
    wheres = [(table, f"drama_id IN ({marks})") for table in drs.CHILD_TABLES]
    if drs.has_table(src, "pages"):
        wheres.append(("bubbles", f"page_id IN (SELECT id FROM pages WHERE drama_id IN ({marks}))"))
    wheres += [(table, f"series_id {in_series}") for table in drs.SERIES_CHILDREN]
    wheres.append(("usage_log", f"drama_id IN ({marks})"))
    for table, where in wheres:
        if not drs.has_table(src, table):
            continue
        total += src.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', ids).fetchone()[0]
        if total > _MAX_ROWS_PER_IMPORT:
            raise InvalidInputError("Those titles hold too many rows to import at once; "
                                    "import fewer of them.")
    size = 0
    for table, where in [("dramas", f"id IN ({marks})"), ("series", f"id {in_series}")] + wheres:
        if not drs.has_table(src, table):
            continue
        cols = ['"' + c.replace('"', '""') + '"' for c in drs.columns(src, table)]
        if not cols:
            continue
        sizes = ", ".join(f"MAX(length(CAST({c} AS BLOB))), SUM(length(CAST({c} AS BLOB)))"
                          for c in cols)
        row = src.execute(f'SELECT {sizes} FROM "{table}" WHERE {where}', ids).fetchone()
        biggest = max((v or 0) for v in row[0::2])
        size += sum((v or 0) for v in row[1::2])
        if biggest > _MAX_VALUE_BYTES or size > _MAX_IMPORT_BYTES:
            raise InvalidInputError("Those titles hold too much data to import at once "
                                    "(or one value is too large); import fewer of them.")


def _live_titles() -> set:
    return {(d.get("title_en") or d.get("title_zh") or "").strip().casefold()
            for d in db.list_dramas()}


def _joinable_series(src, ids, principal, owner_user_id) -> dict:
    """{series name: live series id} for each series of the chosen dramas
    whose name a live series has and that the importer may put a title in
    (ownership_service.check_series_assignment); any other comes in new."""
    marks = ",".join("?" for _ in ids)
    names = {r[0] for r in src.execute(
        f"SELECT s.name FROM series s JOIN dramas d ON d.series_id = s.id "
        f"WHERE d.id IN ({marks})", ids) if isinstance(r[0], str) and r[0]}
    out = {}
    for name in names:
        live_id = db.get_series_id_by_name(name)
        if live_id is None:
            continue
        try:
            out[name] = ownership_service.check_series_assignment(principal, live_id,
                                                                  owner_user_id)
        except ServiceError:
            continue
    return out


def import_dramas(stream, drama_ids, confirm=False, confirm_text="", principal=None,
                  join_series=False, voice_bank=False, settings=False) -> dict:
    """Imports the chosen dramas (ids inside the file, as listed by
    list_backup_dramas) as new dramas. Needs confirm=True and confirm_text
    "RESTORE"; refused while a backup, export, restore or cleanup holds the
    library. All chosen dramas are imported or none. A drama whose title
    matches one already here is titled "... (restored <date>)".
    join_series: a series whose name a series here has (and that the
    importer may use) is merged into that one instead of coming back as
    "... (imported <date>)" (drama_restore_service._merge_into_series).
    voice_bank: the file's voice bank entries whose clip it holds are added
    (one already here with the same name and clip is skipped). settings: the
    file's _importable_settings replace this library's, each one checked as
    a Settings write is (one that fails is left as it is); applied after the
    titles are in, so a settings failure never undoes the import.
    Returns {imported: [{source_id, drama_id, title, media_imported}],
    series_created, series_joined, media_imported, voice_bank_imported,
    settings_imported, notes, counts}."""
    ids = _check_ids(drama_ids)
    las.require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Importing titles")
    if abs_.job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with las.maintenance("importing titles"), storage.job_workdir("backup_import") as tmp:
        db.recover_media_imports()
        backup = _Backup(tmp, stream)
        stagings, staging = {}, None
        try:
            try:
                with contextlib.closing(_open_db(backup.db_path)) as src:
                    marks = ",".join("?" for _ in ids)
                    present = {r[0] for r in src.execute(
                        f"SELECT id FROM dramas WHERE id IN ({marks})", ids)}
                    if any(i not in present for i in ids):
                        raise NotFoundError("A chosen title isn't in that file.")
                    _check_row_limits(src, ids)
                    join = (_joinable_series(src, ids, principal, _import_owner(principal))
                            if join_series else {})
            except sqlite3.Error:
                raise InvalidInputError(_BAD_FILE) from None
            bank = _voice_bank_rows(backup) if voice_bank else []
            if bank and not las.has_disk_room(db.VOICE_BANK_DIR if os.path.isdir(
                    db.VOICE_BANK_DIR) else db.LIBRARY_DIR, sum(i.file_size for _, i in bank)):
                raise InvalidInputError("Not enough free disk space for the voice bank's clips.")
            wanted_settings = _settings_rows(backup) if settings else None
            if backup.zip_path is not None:
                media = backup.media_ids() & set(ids)
                try:
                    if media:
                        staging = db.new_media_staging()
                    with zipfile.ZipFile(backup.zip_path) as zf:
                        for did in ids:
                            if did in media and staging is not None:
                                stagings[did] = drs.stage_media(zf, did, staging)
                except (OSError, zipfile.BadZipFile):
                    raise InvalidInputError(_BAD_FILE) from None
            result = _import_from(backup.db_path, ids, stagings, staging, principal, join,
                                  (backup.zip_path, bank))
        finally:
            if staging is not None:
                drs.end_media_staging(staging)
    result["settings_imported"] = (0 if wanted_settings is None
                                   else _apply_settings(wanted_settings))
    result["notes"] = [KEYS_NOT_IMPORTED] if wanted_settings is not None else []
    return result


def _apply_settings(values: dict) -> int:
    """Writes each value through settings_service.set_settings, the Settings
    page's own checks; one it refuses is left as this library has it."""
    from services import settings_service
    applied = 0
    for key, value in values.items():
        try:
            settings_service.set_settings({key: value})
            applied += 1
        except (ServiceError, TypeError, ValueError):
            log.info("Import: setting %s from the file was not applied", key)
    return applied


def _file_sha256(path: str):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _import_voice_bank(dst, zip_path, bank, written: list) -> int:
    """Adds the bank entries inside the caller's transaction, each clip under
    a new name in the voice bank folder (never over a file there); `written`
    collects those files so a rolled-back import removes them."""
    if not bank:
        return 0
    import uuid
    os.makedirs(db.VOICE_BANK_DIR, exist_ok=True)
    # By clip alone, not name and clip: an entry renamed since the last import must not return as a copy.
    live = set()
    for (clip,) in dst.execute("SELECT clip_filename FROM voice_bank"):
        path = db.voice_bank_clip_path(clip)
        if path is not None:
            live.add(_file_sha256(path))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    added = 0
    with zipfile.ZipFile(zip_path) as zf:
        for entry, info in bank:
            ext = os.path.splitext(info.filename)[1][:16]
            if not ext.isascii() or not all(c.isalnum() or c == "." for c in ext):
                ext = ""
            clip = f"{uuid.uuid4().hex}{ext}"
            dest = os.path.join(db.VOICE_BANK_DIR, clip)
            written.append(dest)
            digest = hashlib.sha256()
            with zf.open(info) as src, open(dest, "xb") as out:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() in live:
                os.remove(dest)
                written.remove(dest)
                continue
            live.add(digest.hexdigest())
            dst.execute("INSERT INTO voice_bank (name, clip_filename, ref_text, clone_engine, "
                        "voice_design, language, notes, source_drama, source_speaker, created_at, "
                        "is_private) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (entry["name"], clip, entry["ref_text"], entry["clone_engine"],
                         entry["voice_design"], entry["language"], entry["notes"],
                         entry["source_drama"], entry["source_speaker"], now,
                         1 if entry["is_private"] else 0))
            added += 1
    return added


# Without the spending rows, an imported title's cost (often all of it, when
# the titles move from another PC) is missing from the Library's spend total.
_USAGE_TEXT = {"engine": 100, "model": 200, "operation": 100}
_USAGE_NUMBERS = ("input_tokens", "output_tokens", "cache_read_tokens")
_MAX_SQLITE_INT = 2 ** 63 - 1
# A row stamped after now would count toward every month's spend until its
# date (the monthly cap has no upper bound, and Reset can't clear it).
_USAGE_CLOCK_SKEW = datetime.timedelta(minutes=10)


def _usage_key(row) -> tuple:
    return (row["created_at"], row["engine"], row["model"], row["operation"],
            row["input_tokens"], row["output_tokens"])


def _clean_usage_row(row: dict):
    """The row as this library stores it, or None when it can't be a usage
    row (no usable or a future time stamp, or a cost that isn't a finite
    non-negative number). Text stays as written (only cut to length) so the
    row still matches its original when deduplicating."""
    try:
        created = datetime.datetime.fromisoformat(str(row.get("created_at") or ""))
        costs = [float(row.get("estimated_cost_usd") or 0)]
        before = row.get("estimated_cost_usd_before_recost")
        if before is not None:
            costs.append(float(before))
        numbers = {k: min(_MAX_SQLITE_INT, max(0, int(row.get(k) or 0))) for k in _USAGE_NUMBERS}
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(c) and c >= 0 for c in costs):
        return None
    if created.tzinfo is not None:
        created = created.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if created > clock.utcnow() + _USAGE_CLOCK_SKEW:
        return None
    text = {k: row[k][:n] if isinstance(row.get(k), str) else None for k, n in _USAGE_TEXT.items()}
    return {**text, **numbers, "estimated_cost_usd": costs[0],
            "estimated_cost_usd_before_recost": costs[1] if len(costs) > 1 else None,
            "created_at": created.isoformat()}


def _import_usage(src, dst, old_id: int, live_id: int) -> int:
    """Copies the drama's usage rows onto its new id. The file's account ids
    mean nothing here, so the rows are the PC owner's (user_id NULL). A row
    this library already has (importing a backup of this same library, whose
    rows outlive a deleted title) is skipped so its cost isn't counted twice;
    rows of the file are never matched with each other, since two real calls
    can share a time stamp."""
    rows = [r for r in map(_clean_usage_row, drs.table_rows(src, "usage_log", "drama_id = ?",
                                                             (old_id,))) if r]
    if not rows:
        return 0
    stamps = [r["created_at"] for r in rows]
    have = {tuple(r) for r in dst.execute(
        "SELECT created_at, engine, model, operation, input_tokens, output_tokens "
        "FROM usage_log WHERE created_at BETWEEN ? AND ?", (min(stamps), max(stamps)))}
    fresh = [r for r in rows if _usage_key(r) not in have]
    imported_at = clock.utcnow().isoformat()
    dst.executemany(
        "INSERT INTO usage_log (drama_id, engine, model, operation, input_tokens, output_tokens, "
        "estimated_cost_usd, estimated_cost_usd_before_recost, created_at, cache_read_tokens, "
        "user_id, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
        [(live_id, r["engine"], r["model"], r["operation"], r["input_tokens"],
          r["output_tokens"], r["estimated_cost_usd"], r["estimated_cost_usd_before_recost"],
          r["created_at"], r["cache_read_tokens"], imported_at) for r in fresh])
    return len(fresh)


def _import_owner(principal):
    """The owner an imported title gets: the importer, or the PC owner (None)
    when that account isn't in this library."""
    owner = ownership_service.new_item_defaults(principal)["owner_user_id"]
    with contextlib.closing(db.get_conn()) as conn:
        known = conn.execute("SELECT 1 FROM users WHERE id = ?", (owner,)).fetchone()
    return owner if known else None


def _import_from(snap_db, ids, stagings, staging, principal, join=None, bank=(None, ())) -> dict:
    """Inserts the dramas (and `bank`, (zip path, _voice_bank_rows)) in one
    transaction, then moves their staged folders (in `staging`) into
    dramas/<new id>, then commits: the commit is the commit point (see db's
    media journal)."""
    defaults = ownership_service.new_item_defaults(principal)
    imported, folders, totals, clips = [], {}, {}, []
    today = datetime.date.today().isoformat()
    titles = _live_titles()   # before dst opens: db helpers close the connection they use
    with contextlib.closing(_open_db(snap_db, _IMPORT_TIME_LIMIT_S)) as src, \
            contextlib.closing(db.get_conn()) as dst:
        import_as = {"owner_user_id": _import_owner(principal),
                     "is_private": defaults["is_private"], "series": {}, "media_dir": None,
                     "join": join or {}}
        try:
            dst.execute("BEGIN IMMEDIATE")
            drs.refuse_newer_copy(src)
            for old_id in ids:
                row = drs.table_rows(src, "dramas", "id = ?", (old_id,))[0]
                title = (_text(row.get("title_en"), 300) or _text(row.get("title_zh"), 300))
                suffix = f"(restored {today})" if title.casefold() in titles else None
                staged = stagings.get(old_id)
                import_as["media_dir"] = staged
                live_id, counts, _ = drs.copy_drama(src, dst, old_id, None, suffix, import_as)
                # Never inherit a stray dramas/<new id>, files imported or not.
                drs.claim_folder(live_id, _FOLDER_EXISTS)
                shown = dst.execute("SELECT COALESCE(NULLIF(title_en, ''), title_zh) FROM dramas "
                                    "WHERE id = ?", (live_id,)).fetchone()[0] or ""
                titles.add(str(shown).strip().casefold())
                if staged is not None:
                    folders[live_id] = staged
                counts = {**counts, "usage_log": _import_usage(src, dst, old_id, live_id)}
                for key, n in counts.items():
                    totals[key] = totals.get(key, 0) + n
                imported.append({"source_id": old_id, "drama_id": live_id, "title": str(shown),
                                 "media_imported": staged is not None})
            bank_added = _import_voice_bank(dst, bank[0], bank[1], clips)
            if folders:
                drs.move_media_in(staging, folders, _FOLDER_EXISTS)
            dst.commit()
        except BaseException as exc:
            dst.rollback()
            for path in clips:
                with contextlib.suppress(OSError):
                    os.remove(path)
            if staging is not None and not drs.end_media_staging(staging):
                raise ServiceError("The titles were not imported, but some of their files could "
                                   "not be cleaned up and are still in the library's titles "
                                   "folder; the app tries again at the next start.") from None
            if isinstance(exc, (ServiceError, KeyboardInterrupt, SystemExit)):
                raise
            log.warning("Drama import failed: %s", type(exc).__name__)
            raise ServiceError("The titles could not be imported; nothing was changed.") \
                from None
    try:
        from services import auth_service
        auth_service.write_audit(
            ownership_service.user_id(principal), "library.import_dramas",
            "dramas " + ",".join(str(i["source_id"]) for i in imported) + " imported as "
            + ",".join(str(i["drama_id"]) for i in imported))
    except Exception:
        log.warning("Could not write the audit entry for a drama import")
    joined = len(import_as.get("joined", ()))
    return {"imported": imported, "series_created": len(import_as["series"]) - joined,
            "series_joined": joined, "voice_bank_imported": bank_added,
            "media_imported": sum(1 for i in imported if i["media_imported"]),
            "counts": totals}
