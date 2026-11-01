"""
services/library_user_backup_service.py -- the backup of one person's items
("backup of just my stuff"): the snapshot cut down to one owner's dramas and
series, their media, and the job that writes it.

Routes: api/routers/library_admin_routes.py. The zip is restorable into a fresh
install with restore_backup. Every library.db table is classified in
USER_BACKUP_TABLES; an unclassified table is dropped from the copy.

No FastAPI import.
"""

import logging
import os
import re
import zipfile

import background_jobs
from jobs import job_launch
from lib import ids as lib_ids
import db
import storage
from services import auth_service
from services.library_admin_service import (USER_BACKUP_JOB_ID, _artifact_dir, _refuse_duplicate,
                                            refuse_during_maintenance)
from services.library_backup_service import _sanitized_snapshot, _write_artifact
from lib.errors import ConflictError, NotFoundError

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# backup of one owner's items ("backup of just my stuff")
# --------------------------------------------------------------------------

# How the per-owner backup treats every library.db table. Default-deny: a
# table missing here is dropped from the copy at run time, and
# tests/test_user_backup.py fails until it is classified.
#   owned_dramas / owned_series: only the owner's rows (NULL owner = the PC
#       owner), owner column cleared (the target install's PC owner)
#   drama: rows of a kept drama only (a NULL drama_id is nobody's: dropped)
#   page: rows of a kept page only; series: rows of a kept series only
#   series_character: kept drama and kept series character only
#   user: rows of the owner only, user column cleared
#   profile_drama: rows of a kept drama on the default profile only
#   profiles: the default profile only (the one reader data uses)
#   style_scope: the learned style of a kept series only
#   keep: household-wide, nothing personal
#   empty: every row dropped
USER_BACKUP_TABLES = {
    "dramas": ("owned_dramas", "the owner's titles"),
    "series": ("owned_series", "the owner's series"),
    "lines": ("drama", ""), "characters": ("drama", ""), "pages": ("drama", ""),
    "translation_notes": ("drama", ""), "line_emotions": ("drama", ""),
    "consistency_issues": ("drama", ""), "vocab_lookups": ("drama", ""),
    "usage_log": ("drama", ""), "line_history": ("drama", ""),
    "progress": ("profile_drama", "other household profiles' reading stays behind"),
    "personal_notes": ("profile_drama", "other household profiles' notes stay behind"),
    "reading_history": ("profile_drama", "other household profiles' history stays behind"),
    "translation_versions": ("drama", ""), "bug_reports": ("drama", ""),
    "wiki_entries": ("drama", ""), "edit_samples": ("drama", ""),
    "line_provenance": ("drama", ""), "metadata_research_results": ("drama", ""),
    "metadata_field_provenance": ("drama", ""),
    "voice_suggestion_dismissals": ("series_character", ""),
    "bubbles": ("page", ""),
    "glossary_terms": ("series", ""), "series_characters": ("series", ""),
    "translation_memory": ("series", ""), "glossary_dismissals": ("series", ""),
    "translate_history": ("user", "the owner's standalone translations"),
    "profiles": ("profiles", "reader data is per library, on the default profile"),
    "style_profile": ("style_scope", "the global profile is learned from everyone's edits"),
    "known_titles": ("keep", "the household's title catalogue"),
    "presets": ("keep", "household workspace presets"),
    **{t: ("empty", "auth") for t in auth_service.RESTORE_KEPT_TABLES},
    "bulk_jobs": ("empty", "provider batch ids of this PC's API accounts; a restored "
                           "in-flight batch could be polled again"),
    "bulk_job_lines": ("empty", "belongs to bulk_jobs"),
    "voice_bank": ("empty", "household clips; the clip files are not copied either"),
    "job_records": ("empty", "this PC's job history"),
    "speaker_merge_undos": ("empty", "short-lived undo records"),
    "job_checkpoints": ("empty", "this PC's job state"),
    "job_stage_timings": ("empty", "this PC's job history"),
    "gpu_lock": ("empty", "this PC's job state"),
    "result_cache": ("empty", "cached model output from every user's runs"),
    "metadata_research_cache": ("empty", "lookups from every user"),
    "app_settings": ("empty", "this PC's settings"),
    "assistant_backlog": ("empty", "this PC's maintenance notes"),
    "benchmark_cases": ("empty", "this PC's test set, may quote any title"),
    "benchmark_runs": ("empty", "belongs to benchmark_cases"),
    "benchmark_case_edits": ("empty", "belongs to benchmark_cases"),
    "benchmark_sessions": ("empty", "this PC's benchmark runs"),
    "benchmark_results": ("empty", "belongs to benchmark_sessions"),
    "benchmark_picks": ("empty", "belongs to benchmark_sessions"),
    "model_candidates": ("empty", "this PC's model choices"),
    "model_decisions": ("empty", "this PC's model choices"),
}
# Copies an old line-reference migration left behind (_backup_step2_<table>)
# hold rows of every drama; they are dropped from the copy.
_DROPPED_TABLE_PREFIX = "_backup_step2_"


def _user_backup_filter(path: str, owner_id) -> list:
    """Cuts the snapshot at `path` down to one owner's items (owner_id
    None: the PC owner, owner_user_id NULL), following
    USER_BACKUP_TABLES, and compacts it. Returns the kept drama ids.

    Series shared between users: a series goes with its owner, with its
    glossary, characters and translation memory. A drama is kept only
    when its owner is kept, so another user's drama in the owner's series
    stays behind; the owner's drama in someone else's series is kept
    without that series (series_id cleared, and links to that series'
    characters removed)."""
    import sqlite3
    try:
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]
            for t in ("keep_dramas", "keep_series", "keep_pages", "keep_chars"):
                conn.execute(f"CREATE TEMP TABLE {t} (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO temp.keep_dramas SELECT id FROM dramas "
                         "WHERE owner_user_id IS ?", (owner_id,))
            conn.execute("INSERT INTO temp.keep_series SELECT id FROM series "
                         "WHERE owner_user_id IS ?", (owner_id,))
            default_profile = None
            if "profiles" in tables:
                default_profile = conn.execute("SELECT MIN(id) FROM profiles").fetchone()[0]
            rules = {t: USER_BACKUP_TABLES.get(t, ("drop", ""))[0] for t in tables}
            for t in tables:
                _filter_by_own_rule(conn, t, rules[t], owner_id, default_profile)
            _filter_by_kept_rows(conn, rules, default_profile)
            _unlink_other_series(conn)
            _reset_counters(conn)
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                        "AND name LIKE 'sqlite_stat%'").fetchall():
                conn.execute(f'DROP TABLE "{name}"')
            kept = [r[0] for r in conn.execute("SELECT id FROM temp.keep_dramas ORDER BY id")]
            conn.execute("COMMIT")
            conn.execute("VACUUM")
            return kept
        finally:
            conn.close()
    except sqlite3.Error:
        raise OSError("snapshot could not be filtered") from None


_IN_DRAMAS = "drama_id IN (SELECT id FROM temp.keep_dramas)"
_IN_SERIES = "series_id IN (SELECT id FROM temp.keep_series)"
# The column a rule filters on; a table without it has no row to keep.
_RULE_COLUMN = {"drama": "drama_id", "series_character": "drama_id", "page": "page_id",
                "series": "series_id", "user": "user_id", "profile_drama": "profile_id"}


def _cols(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _keep_only(conn, table: str, where: str, args=()):
    # IS NOT 1: a NULL (e.g. a NULL drama_id) counts as not kept.
    conn.execute(f'DELETE FROM "{table}" WHERE ({where}) IS NOT 1', args)


def _filter_by_own_rule(conn, t: str, rule: str, owner_id, default_profile):
    """First pass: the rules that need only the kept dramas and series."""
    if t.startswith(_DROPPED_TABLE_PREFIX) or rule == "drop":
        if not t.startswith(_DROPPED_TABLE_PREFIX):
            log.warning("Per-user backup: unclassified table %s left out", t)
        conn.execute(f'DROP TABLE "{t}"')
        return
    need = _RULE_COLUMN.get(rule)
    if rule == "empty" or (need and need not in _cols(conn, t)) or (
            rule == "profile_drama" and "drama_id" not in _cols(conn, t)):
        conn.execute(f'DELETE FROM "{t}"')
    elif rule == "profile_drama":
        # A NULL profile_id means the default profile.
        _keep_only(conn, t, _IN_DRAMAS + " AND COALESCE(profile_id, ?) IS ?",
                   (default_profile, default_profile))
    elif rule in ("owned_dramas", "owned_series"):
        keep = "keep_dramas" if rule == "owned_dramas" else "keep_series"
        _keep_only(conn, t, f"id IN (SELECT id FROM temp.{keep})")
        conn.execute(f'UPDATE "{t}" SET owner_user_id = NULL')
    elif rule in ("drama", "series"):
        _keep_only(conn, t, _IN_DRAMAS if rule == "drama" else _IN_SERIES)
    elif rule == "user":
        _keep_only(conn, t, "user_id IS ?", (owner_id,))
        conn.execute(f'UPDATE "{t}" SET user_id = NULL')


def _filter_by_kept_rows(conn, rules: dict, default_profile):
    """Second pass: the rules that depend on rows the first pass kept."""
    if "pages" in rules:
        conn.execute("INSERT INTO temp.keep_pages SELECT id FROM pages")
    if "series_characters" in rules:
        conn.execute("INSERT INTO temp.keep_chars SELECT id FROM series_characters")
    for t, rule in rules.items():
        if rule == "page" and "page_id" in _cols(conn, t):
            _keep_only(conn, t, "page_id IN (SELECT id FROM temp.keep_pages)")
        elif rule == "series_character" and "drama_id" in _cols(conn, t):
            _keep_only(conn, t, _IN_DRAMAS + " AND series_character_id IN "
                                "(SELECT id FROM temp.keep_chars)")
        elif rule == "style_scope":
            _keep_only(conn, t, "scope IN (SELECT 'series:' || id FROM temp.keep_series)")
        elif rule == "profiles":
            _keep_only(conn, t, "id IS ?", (default_profile,))


def _unlink_other_series(conn):
    """A kept drama in someone else's series stays without that series and its characters."""
    if "series_id" in _cols(conn, "dramas"):
        conn.execute(f"UPDATE dramas SET series_id = NULL WHERE series_id IS NOT NULL "
                     f"AND NOT {_IN_SERIES}")
    if "series_character_id" in _cols(conn, "characters"):
        conn.execute("UPDATE characters SET series_character_id = NULL "
                     "WHERE series_character_id IS NOT NULL AND series_character_id "
                     "NOT IN (SELECT id FROM temp.keep_chars)")


def _reset_counters(conn):
    # Each AUTOINCREMENT counter is reset to the highest kept id, so
    # it doesn't tell how many rows the whole household has.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
        return
    live = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
    for (name,) in conn.execute("SELECT name FROM sqlite_sequence").fetchall():
        top = (conn.execute(f'SELECT MAX(rowid) FROM "{name}"').fetchone()[0]
               if name in live else None)
        if top is None:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (name,))
        else:
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (top, name))


def _drama_media_files(drama_id: int):
    """(full path, zip name) for every regular file in the drama's own
    folder. The folder must be a real directory directly under dramas/;
    symlinks (files or folders) and any `.env` are skipped."""
    base = db.DRAMAS_DIR
    folder = os.path.join(base, str(drama_id))
    if (os.path.islink(folder) or not os.path.isdir(folder)
            or os.path.dirname(os.path.realpath(folder)) != os.path.realpath(base)):
        return
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fname in files:
            full = os.path.join(root, fname)
            if fname == ".env" or os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full, os.path.relpath(full, db.LIBRARY_DIR).replace(os.sep, "/")


def write_user_backup_zip(dest: str, owner_id=None, cancelled=None):
    """A backup zip of one owner's items (owner_id None: the PC owner):
    the snapshot cut down by _user_backup_filter, plus only the kept
    dramas' own folders under dramas/. Nothing else from the library
    folder (voice bank, benchmark files, sources.db, other dramas'
    folders) is included. The live library is only read. `cancelled`, if
    given, is polled between files; when it returns True the job stops
    with background_jobs.JobCancelled (the caller removes the partial file)."""
    def check():
        if cancelled is not None and cancelled():
            raise background_jobs.JobCancelled()

    with storage.job_workdir("snapshot") as snapdir:
        snap = os.path.join(snapdir, "library.db")
        _sanitized_snapshot(snap)
        kept = _user_backup_filter(snap, owner_id)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for did in kept:
                for full, arcname in _drama_media_files(did):
                    check()
                    zf.write(full, arcname)
            zf.write(snap, "library.db")
    check()


_USER_BACKUP_NAME = re.compile(r"baihe_my_items_backup_\d{8}_\d{6}\.zip")


def _remove_older_user_backups(keep: str):
    """Only the newest per-person backup is kept: each one holds a person's
    private items. Only regular files with the generated name are removed."""
    folder = _artifact_dir("user_backup", create=False)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if (name == keep or not _USER_BACKUP_NAME.fullmatch(name) or os.path.islink(path)
                or not os.path.isfile(path)):
            continue
        try:
            os.remove(path)
        except OSError:
            log.warning("An older per-person backup could not be removed")


def _user_backup_job(job_id, owner_id):
    name, size = _write_artifact(
        "user_backup", ".zip", "The backup could not be written.",
        lambda dest: write_user_backup_zip(
            dest, owner_id, cancelled=lambda: background_jobs.is_cancel_requested(job_id)))
    _remove_older_user_backups(name)
    background_jobs.set_result(job_id, {"name": name, "size": size})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def start_user_backup(user_id=None) -> dict:
    """Job: backup zip of one owner's dramas and series, restorable into a
    fresh install with restore_backup. user_id None: the items owned at
    the PC (no owner). Unknown user: NotFoundError. Fetch the file with
    admin_artifact_path("user_backup")."""
    if user_id is not None:
        lib_ids.check_id(user_id, "A user id is a positive whole number.")
        if db.auth_get_user(user_id) is None:
            raise NotFoundError("No such user.")
    refuse_during_maintenance("backup")
    _refuse_duplicate(USER_BACKUP_JOB_ID, "backup")
    if not job_launch.start_job(USER_BACKUP_JOB_ID, _user_backup_job, USER_BACKUP_JOB_ID,
                                     user_id, description="Backup of one person's items",
                                     line_fields=()):
        raise ConflictError("A backup is already running.")
    return {"job_id": USER_BACKUP_JOB_ID}
