"""
db -- local SQLite-backed library for the subtitler app.

Everything lives in ./library/library.db plus per-drama folders under
./library/dramas/<id>/ for audio and novel reference files. This is
all local -- nothing leaves your machine except the actual translation
API calls.
"""

import contextlib
import os
import re
import sqlite3
import json
import threading
import weakref

import portable

# Next to the code for a source checkout; the per-user data folder for an
# installed copy (portable.data_dir()).
LIBRARY_DIR = os.path.join(portable.data_dir(), "library")
DRAMAS_DIR = os.path.join(LIBRARY_DIR, "dramas")
DB_PATH = os.path.join(LIBRARY_DIR, "library.db")
BENCHMARK_DIR = os.path.join(LIBRARY_DIR, "benchmark_cases")
VOICE_BANK_DIR = os.path.join(LIBRARY_DIR, "voice_bank")

# Whether the library dir/schema have been initialized for the *current*
# LIBRARY_DIR. Left False here so importing this module alone never touches
# disk -- initialization happens lazily, on first real call to get_conn()
# (see _ensure_ready() below), not at import time. This is what keeps
# `import db` safe to do before the test suite's `isolated_db` fixture has
# had a chance to redirect the path via configure_library_dir().
_db_ready = False

# Held while the first use migrates the schema, so another thread waits for
# it instead of getting a connection to a half-migrated database. Re-entrant:
# init_db() calls get_conn() on the same thread.
_init_lock = threading.RLock()
_db_initializing = False


def configure_library_dir(path: str):
    """Redirects the library to a different directory -- used by the
    test suite to point at a temp directory instead of the real
    library, so tests never touch your actual data. Not something
    you'd normally call yourself."""
    global LIBRARY_DIR, DRAMAS_DIR, DB_PATH, BENCHMARK_DIR, VOICE_BANK_DIR, _db_ready
    # Under the init lock: a first use still migrating the old path would
    # otherwise mark the new, unmigrated path ready when it finishes.
    with _init_lock:
        LIBRARY_DIR = path
        DRAMAS_DIR = os.path.join(LIBRARY_DIR, "dramas")
        DB_PATH = os.path.join(LIBRARY_DIR, "library.db")
        # Every library path is redirected, BENCHMARK_DIR included, or a test
        # of the benchmark case upload path would write into the real library.
        BENCHMARK_DIR = os.path.join(LIBRARY_DIR, "benchmark_cases")
        VOICE_BANK_DIR = os.path.join(LIBRARY_DIR, "voice_bank")
        os.makedirs(DRAMAS_DIR, exist_ok=True)
        # The new path hasn't been initialized yet -- clear readiness so the
        # next get_conn() (or an explicit init_db() call) sets it up there
        # rather than assuming the old path's readiness still applies.
        _db_ready = False


# Connections opened but not yet closed: thread ident -> list of weak
# references, one per get_conn() call that thread still has open. A list,
# because a thread may legitimately hold more than one at a time: a helper
# that calls get_conn() while its caller still holds a connection (possibly
# mid-transaction) must get a second connection, not close the caller's,
# which would silently roll back the caller's uncommitted writes.
#
# Weak references, so the registry never keeps a connection alive by
# itself: a connection its caller dropped without close() is closed (and
# any open transaction rolled back) by garbage collection -- the cycle
# collector, since sqlite3's statement cache refers back to its connection.
#
# The sweep in _close_leaked_connections only ever closes connections whose
# owning thread has died -- a dead thread can never touch them again, so
# there's no race. background_jobs.py runs real threading.Thread workers
# that call straight into db concurrently with the request threads, and
# a live thread's still-open connection is never closed by anyone else
# (an earlier version that did reliably broke the other thread's very next
# statement with "Cannot operate on a closed database").
#
# Only the owning thread changes its own list; other threads only pop the
# entry of a thread that has died.
_open_connections = {}


class _TrackedConnection(sqlite3.Connection):
    """Deregisters itself on close, so a normal call leaves nothing behind.
    sqlite3.Connection forbids assigning to .close (and plain instances
    can't be weakly referenced), so subclassing via connect(factory=...) is
    the supported way to hook it. A connection is closed by the thread
    that opened it (see _open_connections above), so only that thread's
    own list is looked at here."""

    def close(self):
        ident = threading.get_ident()
        refs = _open_connections.get(ident)
        if refs is not None:
            refs[:] = [r for r in refs if r() is not None and r() is not self]
            if not refs:
                del _open_connections[ident]
        super().close()


def _close_leaked_connections():
    # Snapshot the registered threads first: a thread that registers after this
    # point is not in the list being swept, so it can't look dead because it
    # started after the live-thread set was taken.
    registered = list(_open_connections)
    alive_idents = {t.ident for t in threading.enumerate()}
    for ident in registered:
        if ident in alive_idents:
            continue  # possibly still in ordinary use by a live thread -- not a leak
        for ref in _open_connections.pop(ident, ()):
            leaked = ref()
            if leaked is None:
                continue
            try:
                sqlite3.Connection.close(leaked)
            except Exception:
                # Already popped from _open_connections above, so a failed
                # close here would otherwise vanish silently -- untracked and
                # never actually closed, holding its WAL handle open for the
                # rest of the process's life with no future get_conn() call
                # ever retrying it. Log so that's visible instead of invisible.
                import applog  # local import: applog imports db, so this can't be top-level
                applog.get_logger().warning(
                    "Failed to close a leaked db connection; it may stay open "
                    "for the rest of this process's life.", exc_info=True)


def _ensure_ready():
    """Creates the library dir and schema for the current LIBRARY_DIR, the
    first time any real database operation runs -- not at import time.
    _db_ready is set only once init_db() has finished; until then other
    threads wait on _init_lock, and init_db()'s own get_conn() calls on this
    thread return early through _db_initializing."""
    global _db_ready, _db_initializing
    if _db_ready:
        return
    with _init_lock:
        if _db_ready or _db_initializing:
            return
        _db_initializing = True
        try:
            recover_interrupted_restore(LIBRARY_DIR)
            os.makedirs(DRAMAS_DIR, exist_ok=True)
            # Left unset on failure, so a refused (newer) database is refused
            # again by the next caller instead of being used unmigrated.
            init_db()
            _db_ready = True
        finally:
            _db_initializing = False


def get_conn():
    _ensure_ready()
    _close_leaked_connections()
    # check_same_thread=False: needed for the sweep above -- closing a
    # connection whose owning thread has died, which sqlite3 forbids by
    # default even though it's safe (a dead thread can never race with us).
    path = getattr(_path_override, "path", None) or DB_PATH
    conn = sqlite3.connect(path, factory=_TrackedConnection, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # SQLite leaves foreign keys off by default, per connection, so every
    # connection has to turn them on or ON DELETE CASCADE silently no-ops.
    conn.execute("PRAGMA foreign_keys = ON")
    if path != DB_PATH:
        # migrate_database_file on a staged restore copy: no schema-defined
        # function calls (the restore allows only plain tables/indexes).
        conn.execute("PRAGMA trusted_schema = OFF")
    # WAL: readers (job threads, the API, cli.py) and the one writer don't
    # block each other; it is also why snapshot_database uses the backup API.
    # Under WAL, every read-then-write transaction in this module starts with
    # BEGIN IMMEDIATE: a deferred BEGIN takes the write lock only at its first
    # write, and if another connection committed in between, that upgrade
    # fails at once with "database is locked" instead of waiting.
    conn.execute("PRAGMA journal_mode = WAL")
    refs = _open_connections.setdefault(threading.get_ident(), [])
    refs[:] = [r for r in refs if r() is not None]
    refs.append(weakref.ref(conn))
    return conn


# Per-thread redirect of get_conn(), used only by migrate_database_file.
_path_override = threading.local()


def migrate_database_file(path: str):
    """Runs init_db()'s schema creation and migrations against another
    database file (a staged library restore), in this thread only; every
    other thread keeps using DB_PATH."""
    _ensure_ready()
    _path_override.path = path
    try:
        init_db()
    finally:
        _path_override.path = None


def snapshot_database(dest_path: str):
    """Writes a consistent point-in-time copy of the database to dest_path
    using SQLite's own backup API, rather than copying library.db as a
    plain file. The database runs in WAL mode (see get_conn above), so a
    recent write can still be sitting in library.db-wal rather than in
    library.db itself -- a plain file copy of just library.db can miss it
    or land mid-write. Connection.backup() reads a transactionally
    consistent snapshot regardless of what's in the WAL file."""
    conn = get_conn()
    try:
        dest_conn = sqlite3.connect(dest_path)
        try:
            conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        conn.close()


def _safe_alter(conn, sql: str):
    """Runs one `ALTER TABLE ... ADD COLUMN` from init_db()'s own
    check-then-ALTER lightweight-migration block, swallowing exactly the
    race it's there to guard against: `_ensure_ready()` calls `init_db()`
    lazily, per process, with no cross-process lock -- the API server
    and a separately-running cli.py process can both reach the same
    "column not in existing_cols yet" check at once on a fresh/upgraded database, and
    whichever ALTER runs second then hits sqlite3.OperationalError:
    duplicate column name, even though the migration itself succeeded.
    Anything else raises -- a column genuinely failing to add for a real
    reason (a locked file, a malformed DB) must not be hidden."""
    try:
        conn.execute(sql)
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e):
            raise


APP_VERSION_SETTING = "app_version_last_opened"


_VERSION_RE = re.compile(r"^v?(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?")


def _version_tuple(text):
    """Numeric (major, minor, patch), or None for anything that isn't a
    version (a dev build, a damaged value), which is never "newer"."""
    m = _VERSION_RE.match(text.strip()) if isinstance(text, str) else None
    return tuple(int(g or 0) for g in m.groups()) if m else None


def _running_app_version():
    """None for a source checkout, which has no version and so is neither
    checked nor recorded."""
    return portable.installed_app_version()


def _refuse_newer_database(conn, running):
    """An older build would silently misread columns a newer one added, so
    it must not open the database at all."""
    from lib.errors import NewerDatabaseError
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                           (APP_VERSION_SETTING,)).fetchone()
    except sqlite3.OperationalError:
        return   # a brand-new database has no app_settings yet
    if row is None:
        return
    try:
        stored = json.loads(row[0])
    except ValueError:
        return
    stored_v, running_v = _version_tuple(stored), _version_tuple(running)
    if stored_v and running_v and stored_v > running_v:
        raise NewerDatabaseError(
            f"This library was last opened by Baihe {stored}, but this is Baihe {running}. "
            "Upgrade Baihe, or restore a backup from the backups folder "
            "(the backups folder inside your Baihe data folder).")


def init_db():
    running = _running_app_version()
    with contextlib.closing(get_conn()) as conn:
        if running:
            _refuse_newer_database(conn, running)
        # Order matters: later steps read tables and columns that earlier
        # ones create (the ownership step needs users and app_settings).
        _create_library_tables(conn)
        _create_series_tables(conn)
        _create_line_annotation_tables(conn)
        _create_bulk_job_tables(conn)
        _create_reading_tables(conn)
        _create_review_tables(conn)
        _create_library_tool_tables(conn)
        _create_job_tables(conn)
        _create_metadata_research_tables(conn)
        _create_core_indexes(conn)
        _migrate_job_records_columns(conn)
        _create_auth_tables(conn)
        _migrate_line_columns(conn)
        _migrate_drama_columns(conn)
        _migrate_series_and_character_columns(conn)
        _migrate_scanlate_columns(conn)
        _migrate_bulk_job_columns(conn)
        _migrate_vocab_and_style_columns(conn)
        _migrate_ownership_columns(conn)
        _migrate_auth_session_columns(conn)
        _grant_lines_read_for_reader_page(conn)
        import device_tokens   # owns its table; imports db, so not at the top
        device_tokens.create_tables(conn)
        import member_keys     # owns its table; imports db, so not at the top
        member_keys.create_tables(conn)
        _migrate_off_removed_test_engine(conn)
        if running:
            conn.execute("""
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (APP_VERSION_SETTING, json.dumps(running)))
        conn.commit()
    _init_benchmark_lab_schema()
    _migrate_gpu_lock_slots()
    _migrate_line_refs_to_ids()
    _migrate_step26e_profiles()


from db.schema import (  # noqa: E402,F401
    _create_bulk_job_tables, _create_core_indexes, _create_library_tables,
    _create_library_tool_tables, _create_line_annotation_tables,
    _create_metadata_research_tables, _create_reading_tables, _create_review_tables,
    ensure_legacy_bug_reports,
    _create_series_tables)

from db.schema_system import (  # noqa: E402,F401
    _create_auth_tables, _create_job_tables, _init_benchmark_lab_schema)


from db.migrations import (  # noqa: E402,F401
    _grant_lines_read_for_reader_page, _migrate_auth_session_columns, _migrate_bulk_job_columns,
    _migrate_drama_columns, _migrate_gpu_lock_slots, _migrate_job_records_columns,
    _migrate_line_columns, _migrate_line_refs_to_ids, _migrate_off_removed_test_engine,
    _migrate_ownership_columns, _migrate_scanlate_columns,
    _migrate_series_and_character_columns, _migrate_step26e_profiles,
    _migrate_vocab_and_style_columns)


# ---------------------------------------------------------------------------
# Model re-evaluation candidates and decisions
# ---------------------------------------------------------------------------

from db.model_candidates import (  # noqa: E402,F401
    create_model_candidate, find_model_candidate, get_model_candidate, latest_model_decision,
    list_model_candidates, list_model_decisions, record_model_decision,
    set_model_candidate_status)


# ---------------------------------------------------------------------------
# Line references by position (line_idx -> lines.id). The line-id rebuild in
# db/migrations.py reads these as db.<name> so a test can patch them here;
# db/reading.py and db/line_annotations.py resolve through _line_id_for_idx.
# ---------------------------------------------------------------------------

_LINE_REF_TABLE_DDL = {
    "translation_notes": """
        CREATE TABLE {name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            line_id INTEGER,
            line_idx INTEGER,
            term TEXT,
            note_type TEXT,
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, line_id, term)
        )""",
    "line_emotions": """
        CREATE TABLE {name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            line_id INTEGER,
            line_idx INTEGER,
            emotion TEXT,
            intensity REAL,
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, line_id)
        )""",
}
_LINE_REF_TABLE_COLUMNS = {
    "translation_notes": ("drama_id", "line_idx", "term", "note_type", "note", "created_at"),
    "line_emotions": ("drama_id", "line_idx", "emotion", "intensity", "note", "created_at"),
}
_LINE_ID_FOR_IDX_SQL = ("(SELECT l.id FROM lines l WHERE l.drama_id = t.drama_id "
                        "AND l.idx = t.line_idx ORDER BY l.id LIMIT 1)")


def _line_id_for_idx(conn, drama_id, line_idx):
    if line_idx is None:
        return None
    row = conn.execute("SELECT id FROM lines WHERE drama_id = ? AND idx = ? ORDER BY id LIMIT 1",
                       (drama_id, int(line_idx))).fetchone()
    return row["id"] if row else None


def drama_dir(drama_id: int) -> str:
    d = os.path.join(DRAMAS_DIR, str(drama_id))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Media journal: drama folders moved in by a backup import or restore
# ---------------------------------------------------------------------------
# A staging folder with no journal moved nothing yet; one this young may
# belong to an import in another process that hasn't taken its lock yet.
STALE_MEDIA_STAGING_SECONDS = 24 * 3600
# One lock per process for every import and recovery; db/media_journal.py
# reads it as db._media_lock so no submodule holds its own copy.
_media_lock = threading.RLock()


from db.media_journal import (  # noqa: E402,F401
    MEDIA_STAGING_PREFIX, MEDIA_MARKER, DramaFolderConflict, _lock_file, _release_lock,
    new_media_staging, write_media_journal, move_staged_folder, finish_media_staging,
    recover_media_imports, claim_new_drama_folder)


from db.library_folder import (  # noqa: E402,F401
    PRE_RESTORE_MARKER, fsync_dir, hold_restore_lock, library_startup_notes,
    recover_interrupted_restore, release_restore_lock)


# ---------------------------------------------------------------------------
# Dramas CRUD
# ---------------------------------------------------------------------------

from db.dramas import (  # noqa: E402,F401
    create_drama, delete_drama, drama_has_translated_lines, drama_visible_sql, get_drama,
    get_library_stats, list_dramas, list_dramas_by_series, list_dramas_by_series_ids,
    list_dramas_recently_active,
    search_lines_globally, set_status_if, update_drama, ORGANIZATIONAL_TAGS, MERGED_TAG,
    has_custom_tag, set_custom_tag)


# ---------------------------------------------------------------------------
# Lines CRUD
# ---------------------------------------------------------------------------

from db.lines import (  # noqa: E402,F401
    line_value, update_line_fields_if, update_lines_fields_if_many, save_lines,
    line_ids_with_refs, load_lines, load_line_objects, load_line_ids, line_ids_exist)


from db.speakers import (  # noqa: F401
    upsert_character,
    list_characters,
    clear_character_series_link,
    rename_speaker_atomic,
    MERGE_ROW_FIELDS,
    MERGE_UNDO_TTL_SECONDS,
    merge_speakers_atomic,
    undo_merge_speakers_atomic,
    drop_speaker_merge_undos,
    live_speaker_merge_undo_clips,
    retire_speaker_merge_undos,
    upsert_series_character,
    count_series_characters_by_series,
    rename_series_character,
    insert_series_character,
    update_series_character,
    list_series_characters,
    delete_series_character,
    update_series_character_voice_fingerprint,
    accept_voice_link,
    remember_speaker_as_series_character,
    dismiss_voice_suggestion,
    list_dismissed_voice_suggestions,
    list_characters_with_series_names,
    save_voice_bank_entry,
    list_voice_bank_entries,
    get_voice_bank_entry,
    apply_voice_bank_entry,
    rename_voice_bank_entry,
    delete_voice_bank_entry,
)


from db.comic_pages import (  # noqa: F401
    create_page,
    update_page,
    list_pages,
    get_page,
    move_pages,
    BUBBLE_EDIT_FIELDS,
    update_bubble_fields,
    replace_bubbles_if_unchanged,
    save_bubbles,
    load_bubbles,
    list_bubbles_for_drama,
)


# ---------------------------------------------------------------------------
# Known titles (searchable discovery library, separate from your working
# drama catalog -- import a known_title into `dramas` when you're ready
# to actually work on it)
# ---------------------------------------------------------------------------

from db.known_titles import (  # noqa: E402,F401
    create_known_title, delete_known_title, list_known_titles)


# ---------------------------------------------------------------------------
# Series & shared glossaries (terms consistent across multiple dramas of
# the same series, e.g. book 1/2/3 of the same title by the same author)
# ---------------------------------------------------------------------------

from db.glossary import (  # noqa: E402,F401
    bump_translation_memory_use, create_series, get_or_create_series, get_series_id_by_name,
    insert_glossary_term_if_absent, list_series, list_translation_memory,
    record_translation_memory, update_glossary_term, update_translation_memory_after_replace,
    upsert_glossary_term, count_glossary_terms_by_series, list_glossary_terms, list_glossary_dismissals,
    list_dismissed_glossary_terms, add_glossary_dismissals, remove_glossary_dismissals,
    delete_glossary_term)


# ---------------------------------------------------------------------------
# Translation notes (idioms, wordplay, meaningful names, allusions)
# ---------------------------------------------------------------------------

from db.line_annotations import (  # noqa: E402,F401
    delete_translation_note, line_emotion_ids, list_translation_notes, load_consistency_issues,
    load_emotions,
    save_consistency_issues, save_emotions, save_translation_notes)


# ---------------------------------------------------------------------------
# Progress tracking & reading history
# ---------------------------------------------------------------------------

from db.reading import (  # noqa: E402,F401
    clear_reading_history, create_profile, delete_profile, get_personal_notes, get_profile,
    get_progress, list_continue_reading, list_profiles, list_reading_history,
    save_personal_notes, save_progress)


# ---------------------------------------------------------------------------
# Translation versions -- keep alternate translations side by side
# ---------------------------------------------------------------------------

from db.translation_versions import (  # noqa: E402,F401
    delete_translation_version, get_translation_version, list_translation_versions,
    save_translation_version, set_active_translation_version)


# ---------------------------------------------------------------------------
# Universe wiki -- accumulating encyclopedia, spoiler-bounded
# ---------------------------------------------------------------------------

from db.wiki import (  # noqa: E402,F401
    clear_wiki, list_wiki_entries, upsert_wiki_entry)


# ---------------------------------------------------------------------------
# Adaptive style -- learn translation preferences from your edits
# ---------------------------------------------------------------------------

from db.style import (  # noqa: E402,F401
    STYLE_HISTORY_KEEP, get_style_profile, list_edit_samples, record_edit_sample,
    replace_style_profile, restore_style_profile, save_style_profile)


# ---------------------------------------------------------------------------
# Workspace-configuration presets -- library-level, reusable
# across unrelated series/projects. See the `presets` table's own comment
# in db/schema.py for why deleting/renaming one never touches a drama it was
# previously applied to: nothing else references presets.id at all.
# ---------------------------------------------------------------------------

from db.presets import (  # noqa: E402,F401
    delete_preset, insert_preset, list_presets, rename_preset, save_preset,
    set_default_preset, set_preset_engine_model, update_preset)


# ---------------------------------------------------------------------------
# Line history / undo -- snapshots taken before risky bulk operations
# (force re-translate, merge) so you can roll back to a previous version
# instead of losing translation work with no way back.
# ---------------------------------------------------------------------------

from db.line_history import (  # noqa: E402,F401
    MAX_SNAPSHOT_WORD_BYTES, PLAIN_SNAPSHOTS, get_line_history_snapshot, list_line_history,
    save_line_history_snapshot)


# ---------------------------------------------------------------------------
# Vocab lookups (persisted Reader click-to-define history, for export)
# ---------------------------------------------------------------------------

from db.vocab import (  # noqa: E402,F401
    list_vocab_lookups, save_vocab_lookup, set_vocab_export_rich)


from db.usage import (  # noqa: F401
    log_usage,
    on_spend,
    MONTHLY_SPEND_RESET_KEY,
    get_month_spend_reset_at,
    get_month_spend,
    usage_recost_candidates,
    apply_usage_recost,
    undo_usage_recost,
    get_usage_summary,
    get_usage_by_drama,
    save_translate_history,
    list_translate_history,
    clear_translate_history,
)
from db.app_settings import (  # noqa: F401
    get_app_setting,
    set_app_setting,
    delete_app_setting,
    get_research_cache,
    put_research_cache,
    RESEARCH_RESULT_TTL_DAYS,
    put_research_result,
    get_research_result,
    add_field_provenance,
    list_field_provenance,
)
from db.job_records import (  # noqa: F401
    save_job_record,
    touch_job_records,
    list_job_records,
    list_job_record_fingerprints,
    count_active_job_records,
    get_job_record,
    delete_job_record,
    delete_finished_job_record,
    clear_all_job_records,
)


# ---------------------------------------------------------------------------
# Bulk (batch-API / off-peak) translation jobs
# ---------------------------------------------------------------------------

from db.bulk_jobs import (  # noqa: E402,F401
    BULK_PENDING_STATUSES, count_bulk_job_lines, create_bulk_job, get_bulk_job,
    list_bulk_job_lines, list_bulk_jobs, set_bulk_job_line_result_texts, set_bulk_job_status_if,
    update_bulk_job)


from db.benchmark import (  # noqa: F401
    create_benchmark_case,
    list_benchmark_cases,
    delete_benchmark_case,
    save_benchmark_run,
    list_benchmark_runs,
    create_benchmark_lab_case,
    update_benchmark_case_texts,
    list_benchmark_case_edits,
    set_benchmark_case_excluded,
    create_benchmark_clip_cases,
    list_benchmark_cases_by_clip,
    count_benchmark_cases_with_file,
    get_benchmark_case,
    find_benchmark_case,
    create_benchmark_session,
    update_benchmark_session,
    delete_benchmark_sessions,
    get_benchmark_session,
    list_benchmark_sessions,
    save_benchmark_result,
    list_benchmark_results,
    set_benchmark_judge,
    save_benchmark_pick,
    list_benchmark_picks,
)


# ---------------------------------------------------------------------------
# Full reset -- wipes everything, for starting over during testing
# ---------------------------------------------------------------------------

def reset_library():
    """
    Deletes the database, every drama's files, and the cached CC-CEDICT
    dictionary, then reinitializes an empty schema. Irreversible --
    callers must get explicit confirmation before calling this; nothing
    here asks again.

    Closes any connections this process still holds open first (WAL mode
    leaves -wal/-shm files that must go too, or a stale one can confuse
    the next connection).

    cedict.txt re-downloads automatically the next time it's needed (see
    dictionary.py's _ensure_cedict()) -- it's a generic reference file,
    not drama data, but "reset everything" should mean everything
    downloaded, not just the per-drama folders.
    """
    import shutil

    _close_leaked_connections()
    # The calling thread's own leftovers too: an open handle blocks removing
    # the files on Windows.
    for ref in _open_connections.pop(threading.get_ident(), ()):
        leftover = ref()
        if leftover is not None:
            try:
                sqlite3.Connection.close(leftover)
            except Exception:
                pass
    for suffix in ("", "-wal", "-shm", "-journal"):
        # A job thread closing its last connection can checkpoint and delete
        # the -wal/-shm files between a check and the remove, so just try.
        try:
            os.remove(DB_PATH + suffix)
        except FileNotFoundError:
            pass
    if os.path.isdir(DRAMAS_DIR):
        shutil.rmtree(DRAMAS_DIR)
    cedict_path = os.path.join(LIBRARY_DIR, "cedict.txt")
    if os.path.exists(cedict_path):
        os.remove(cedict_path)
    os.makedirs(DRAMAS_DIR, exist_ok=True)
    init_db()


from db.auth import (  # noqa: F401
    auth_create_user,
    auth_update_user,
    get_item_ownership,
    assign_drama_series,
    unassign_drama_series,
    set_item_private,
    series_has_unowned_drama,
    rename_series, series_content_counts, delete_series, copy_series_terms,
    update_series_instructions, set_series_defaults, apply_series_settings,
    list_item_sharing,
    auth_get_user,
    auth_get_user_by_email,
    auth_get_user_by_sub,
    auth_list_users,
    auth_get_permissions,
    auth_grant_permission,
    auth_revoke_permission,
    auth_insert_session,
    auth_rotate_session,
    auth_get_session_by_hash,
    auth_touch_session,
    auth_delete_session,
    auth_delete_user_sessions,
    auth_delete_stale_sessions,
    auth_list_sessions,
    auth_insert_audit,
    auth_list_audit,
    auth_list_audit_actions,
    auth_deactivate_user_keeping_an_admin,
    auth_revoke_admin_keeping_an_admin,
)
