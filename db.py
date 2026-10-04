"""
db.py -- local SQLite-backed library for the subtitler app.

Everything lives in ./library/library.db plus per-drama folders under
./library/dramas/<id>/ for audio and novel reference files. This is
all local -- nothing leaves your machine except the actual translation
API calls.
"""

import contextlib
import logging
import os
import re
import shutil
import sqlite3
import datetime
import json
import stat
import threading
import time
import uuid
import weakref
from typing import List

import portable

# Next to the code for a source checkout; the per-user data folder for an
# installed copy (portable.data_dir(), Step 80b).
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


def configure_library_dir(path: str):
    """Redirects the library to a different directory -- used by the
    test suite to point at a temp directory instead of the real
    library, so tests never touch your actual data. Not something
    you'd normally call yourself."""
    global LIBRARY_DIR, DRAMAS_DIR, DB_PATH, BENCHMARK_DIR, VOICE_BANK_DIR, _db_ready
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
# that call straight into db.py concurrently with the request threads, and
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
    Sets _db_ready before doing the work, since init_db() itself calls
    get_conn(), which would otherwise recurse back into this function."""
    global _db_ready
    if _db_ready:
        return
    _db_ready = True
    os.makedirs(DRAMAS_DIR, exist_ok=True)
    init_db()


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


def init_db():
    with contextlib.closing(get_conn()) as conn:
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
        _migrate_off_removed_test_engine(conn)
        conn.commit()
    _init_benchmark_lab_schema()
    _migrate_gpu_lock_slots()
    _migrate_line_refs_to_ids()
    _migrate_step26e_profiles()


def _create_library_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dramas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title_zh TEXT,
            title_en TEXT,
            author TEXT,
            studio TEXT,
            director TEXT,
            voice_actors TEXT,      -- comma-separated
            summary TEXT,
            status TEXT DEFAULT 'not started',   -- not started / aligned / translated / dubbed / exported
            content_mode TEXT DEFAULT 'audio_drama',  -- 'audio_drama', 'streamer_vod', or 'novel_narration'
            narration_language TEXT DEFAULT 'translation',  -- novel_narration only: 'translation' or
                                                       -- 'original' (Step 26c)
            source_language TEXT DEFAULT 'zh',        -- 'zh', 'ja', or 'ko'
            media_type TEXT DEFAULT 'audio_drama',    -- 'audio_drama', 'video_drama', 'novel',
                                                       -- 'manhwa', 'manga', 'manhua', 'asmr', 'other'
            series_id INTEGER,        -- shares a glossary across multiple dramas of the same series
            episode_number INTEGER,   -- Step 74: explicit ordering within series_id, for "previous
                                       -- episode" lookups. NULL/unset by default -- an existing
                                       -- series with no numbers set keeps its old created_at order.
            episode_summary TEXT,     -- Step 74: a short auto-generated running summary of this
                                       -- episode (key events, unresolved threads, character state),
                                       -- fed forward as fixed context into the immediately following
                                       -- episode's translation prompt. Editable, never auto-applied
                                       -- beyond that -- same "suggestion" pattern as glossary/TM.
            audio_filename TEXT,
            novel_reference_filename TEXT,
            translation_engine TEXT DEFAULT 'claude',
            author_romanized TEXT,
            studio_romanized TEXT,
            voice_actors_romanized TEXT,
            director_romanized TEXT,
            cover_art_filename TEXT,
            genre TEXT,
            publication_status TEXT,   -- ongoing / completed / hiatus / unknown
            chapter_count INTEGER,
            custom_tags TEXT,          -- comma-separated, user-defined
            last_translate_errors TEXT, -- JSON: failed line indices from the most recent
                                         -- translation run, persisted (not just shown once)
                                         -- so a missed warning isn't lost forever
            personal_notes TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            idx INTEGER,
            start REAL,
            end REAL,
            zh TEXT,
            en TEXT,
            speaker TEXT,            -- raw diarization label OR LLM-tagged character name
            dub_filename TEXT,       -- generated TTS clip for this line, if dubbed
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            speaker_label TEXT,      -- raw diarization label OR tagged character name this maps to
            character_name TEXT,
            voice_actor TEXT,
            tts_voice TEXT,          -- fallback free TTS voice for this character (an edge-tts name)
            offline_voice TEXT,      -- offline/Piper fallback voice (a Piper voice name); NULL = default
            ref_audio_filename TEXT, -- reference clip for voice cloning (relative to drama dir)
            ref_text TEXT,           -- transcript of what's said in the reference clip
            elevenlabs_voice_id TEXT,-- hosted clone (engine removed in Step 11d); kept as a record, unused
            clone_engine TEXT,       -- local voice engine (dub.CLONE_ENGINES key); NULL = F5-TTS
            voice_design TEXT,       -- described voice (OmniVoice voice design) for a character with no clip
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, speaker_label)
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            idx INTEGER,
            filename TEXT,           -- original page image, relative to drama dir
            rendered_filename TEXT,  -- typeset output, once rendered
            width INTEGER,
            height INTEGER,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS bubbles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            idx INTEGER,
            x INTEGER, y INTEGER, w INTEGER, h INTEGER,
            source_text TEXT,
            translated_text TEXT,
            font_size INTEGER DEFAULT 18,
            skip INTEGER DEFAULT 0,   -- 1 = don't render this bubble (e.g. false positive)
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS known_titles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title_original TEXT,
            title_en TEXT,
            author TEXT,
            tags TEXT,               -- comma-separated
            summary_en TEXT,
            summary_original TEXT,
            source_name TEXT,        -- e.g. 'baihehub', 'manual'
            source_url TEXT,
            language TEXT,           -- 'zh', 'ja', 'ko'
            media_type TEXT,         -- 'novel', 'audio_drama', 'manhwa', 'manga', 'manhua', 'game'
            created_at TEXT
        );
    """)


def _create_series_tables(conn):
    conn.executescript("""

        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            created_at TEXT
        );

        -- Named characters that persist across every drama in a series --
        -- distinct from the per-drama `characters` row, whose speaker_label
        -- comes from that ONE drama's own diarization run and isn't stable
        -- across dramas (SPEAKER_00 in one recording isn't necessarily the
        -- same person as SPEAKER_00 in another). This is what makes a
        -- "streamer archive" series useful: once "Su Shan" exists here, every
        -- later stream from the same streamer can pick her from a list
        -- instead of retyping and re-spelling her name each time.
        CREATE TABLE IF NOT EXISTS series_characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            character_name TEXT NOT NULL,
            aliases TEXT,             -- pipe-separated nicknames/alternate spellings
            notes TEXT,               -- speaking style, relationships, anything worth remembering
            created_at TEXT,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, character_name)
        );

        CREATE TABLE IF NOT EXISTS glossary_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            term_original TEXT,
            term_translation TEXT,
            notes TEXT,
            category TEXT,            -- person_name, clan_sect, honorific, etc.
            policy TEXT,              -- keep_pinyin, hybrid, translate_meaning, etc.
            enforce_exact INTEGER DEFAULT 0,  -- 1 = hard find-replace, no drift allowed
            aliases TEXT,              -- pipe-separated alt spellings/transliterations of
                                       -- term_original itself (Step 30) -- same convention
                                       -- as series_characters.aliases, but for the source
                                       -- term, not a character
            banned_translations TEXT,  -- pipe-separated known-bad renderings (Step 30) --
                                       -- Auto QC flags a line using one of these for review;
                                       -- it never rewrites. Independent of the older
                                       -- enforce_exact/notes hard-substitution mechanism
                                       -- (see translation_guide.apply_hard_term_substitutions)
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, term_original)
        );

        -- Step 24: source->translation pairs a translator has approved by hand
        -- (edited and saved, or accepted), reused as suggestions on later
        -- near-identical source lines in the same series -- never auto-applied.
        CREATE TABLE IF NOT EXISTS translation_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            source_text TEXT NOT NULL,
            translation TEXT NOT NULL,
            use_count INTEGER DEFAULT 1,
            updated_at TEXT,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, source_text)
        );

        -- Step 8: a voice-match suggestion ("SPEAKER_01 sounds like <name>")
        -- the user explicitly rejected for this exact (drama, speaker,
        -- candidate) triple -- never shown again for that combination, but a
        -- different candidate for the same speaker (or the same candidate
        -- for a different speaker) can still be suggested.
        CREATE TABLE IF NOT EXISTS voice_suggestion_dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            speaker_label TEXT NOT NULL,
            series_character_id INTEGER NOT NULL,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            FOREIGN KEY (series_character_id) REFERENCES series_characters(id) ON DELETE CASCADE,
            UNIQUE(drama_id, speaker_label, series_character_id)
        );
    """)


def _create_line_annotation_tables(conn):
    conn.executescript("""

        CREATE TABLE IF NOT EXISTS translation_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            line_id INTEGER,          -- lines.id: the permanent link
            line_idx INTEGER,         -- position when saved; fallback only, see list_translation_notes
            term TEXT,
            note_type TEXT,           -- idiom, wordplay, name_meaning, allusion, cultural, honorific
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, line_id, term)
        );

        CREATE TABLE IF NOT EXISTS line_emotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            line_id INTEGER,          -- lines.id: the permanent link
            line_idx INTEGER,         -- position when saved; fallback only
            emotion TEXT,
            intensity REAL,
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, line_id)
        );

        CREATE TABLE IF NOT EXISTS consistency_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            term TEXT,
            variants TEXT,            -- JSON-encoded list of strings
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS vocab_lookups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER,
            word TEXT,
            reading TEXT,
            definitions TEXT,        -- JSON-encoded list of strings
            language TEXT,
            first_seen_line_idx INTEGER,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, word)
        );

        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER,
            engine TEXT,
            model TEXT,
            operation TEXT,           -- 'translate', 'consistency_check', 'qa', etc.
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE SET NULL
        );
    """)


def _create_bulk_job_tables(conn):
    conn.executescript("""

        -- Step 9 bulk mode: one row per submitted provider batch (or, for
        -- DeepSeek, per job scheduled into its next off-peak window), kept on
        -- disk so a restarted app can pick a pending batch back up by its id.
        CREATE TABLE IF NOT EXISTS bulk_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            engine TEXT NOT NULL,
            model TEXT,
            provider_batch_id TEXT,   -- Anthropic msgbatch_..., Gemini batches/...; NULL for DeepSeek
            status TEXT NOT NULL,     -- submitted | scheduled | applied | cancelled | failed | auth_error
            scheduled_for TEXT,       -- DeepSeek: UTC start of the off-peak window it waits for
            translate_args TEXT,      -- JSON: what a scheduled run needs to build its prompt
            last_error TEXT,
            result_summary TEXT,      -- JSON counts once results are applied
            submitted_at TEXT,
            updated_at TEXT,
            -- Step 9d: which per-line LLM pass this job runs. 'translate' is
            -- Step 9's original (and still the default, for old rows and
            -- every existing call site that doesn't pass one).
            kind TEXT NOT NULL DEFAULT 'translate',
            -- Step 9d: Reflect's own three-pass pipeline ('faithful' ->
            -- 'reflect' -> 'expressive'), NULL for every other kind -- see
            -- bulk_translate.py's own module docstring for how one stage's
            -- applied results submit the next.
            stage TEXT,
            -- Step 9d: shared across all three of one Reflect run's stage
            -- rows (a plain string id, not a FK -- there's no single "parent"
            -- row, just three siblings), so the Bulk jobs panel can group and
            -- show them as one pipeline instead of three unrelated entries.
            pipeline_id TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        -- Every line a bulk job covers, with what it looked like when it was
        -- submitted -- results are applied by line id, and only to a line whose
        -- source text still hashes the same and whose English hasn't been
        -- changed since.
        CREATE TABLE IF NOT EXISTS bulk_job_lines (
            bulk_job_id INTEGER NOT NULL,
            line_id INTEGER NOT NULL,
            request_key TEXT,         -- the provider request (custom_id / metadata key) it was sent in
            zh_hash TEXT NOT NULL,
            en_at_submit TEXT,
            -- Step 9d: a Reflect faithfulness/reflection stage's own raw
            -- per-line output (a draft translation or a critique) -- not
            -- applied to the line itself, just held here so the NEXT stage's
            -- prompt can be built from it once this stage's batch returns.
            -- Unused (NULL) for every other kind/stage.
            result_text TEXT,
            -- Step 9d: JSON snapshot of whatever field(s) a non-translate
            -- kind is about to write, as they stood at submission -- e.g.
            -- {"flag": ..., "flag_note": ...} for a flag job. Lets its own
            -- apply step tell "the user already changed this since
            -- submission" (keep their edit, same as en_at_submit already
            -- does for translate) apart from "still exactly what it was".
            -- NULL for kinds with no such conflict (translate uses
            -- en_at_submit instead; notes are additive, never overwritten).
            state_at_submit TEXT,
            PRIMARY KEY (bulk_job_id, line_id),
            FOREIGN KEY (bulk_job_id) REFERENCES bulk_jobs(id) ON DELETE CASCADE
        );
    """)


def _create_reading_tables(conn):
    conn.executescript("""

        CREATE TABLE IF NOT EXISTS line_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            label TEXT,               -- e.g. 'before force re-translate', 'before merge'
            snapshot_json TEXT,       -- JSON-encoded list of line dicts
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT,
            created_at TEXT
        );

        -- Step 26e: household profiles (Jellyfin-style) -- one shared library,
        -- but each profile's own reading position, history and notes. No
        -- password field: this app has no accounts/auth of its own, so a
        -- profile is "which household member is this" (picked from a list),
        -- not a login -- whatever gets you to the app at all (running it
        -- locally, or Tailscale/a reverse proxy for remote access) already
        -- established that you're a trusted person before you ever see this.
        CREATE TABLE IF NOT EXISTS progress (
            drama_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            last_line_idx INTEGER DEFAULT 0,
            audio_position_seconds REAL DEFAULT 0,
            last_page INTEGER DEFAULT 1,
            percent_complete REAL DEFAULT 0,
            last_accessed_at TEXT,
            PRIMARY KEY (drama_id, profile_id),
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );

        -- Replaces dramas.personal_notes (left in place, unread/unwritten from
        -- here on -- see _migrate_step26e_profiles) now that notes are private
        -- per profile rather than one shared field everyone on the household
        -- server would otherwise see and overwrite.
        CREATE TABLE IF NOT EXISTS personal_notes (
            profile_id INTEGER NOT NULL,
            drama_id INTEGER NOT NULL,
            notes TEXT,
            updated_at TEXT,
            PRIMARY KEY (profile_id, drama_id),
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            profile_id INTEGER,
            line_id INTEGER,
            line_idx INTEGER,
            percent_complete REAL,
            accessed_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS translation_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            label TEXT,
            engine TEXT,
            model TEXT,
            is_active INTEGER DEFAULT 0,
            lines_json TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
    """)


def _create_review_tables(conn):
    conn.executescript("""

        CREATE TABLE IF NOT EXISTS bug_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            line_id INTEGER,          -- lines.id this bundle was saved for, if any
            label TEXT,
            input_json TEXT,          -- frozen input: source text, context, glossary, settings
            engine TEXT,
            model TEXT,
            produced_output TEXT,     -- the (bad/flagged) output at save time
            flag TEXT,
            flag_note TEXT,
            replay_output TEXT,       -- filled in after replay_bug_bundle() runs
            replayed INTEGER DEFAULT 0,
            reproduced INTEGER,       -- NULL until replayed; 1/0 after
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS wiki_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            entry_type TEXT,          -- character / place / sect / artifact / concept / event
            name TEXT,
            aliases TEXT,             -- comma-separated: titles, nicknames, name changes
            description TEXT,
            attributes_json TEXT,     -- flexible: cultivation level, rank, affiliations, equipment
            first_seen_line_idx INTEGER,
            known_through_line_idx INTEGER,  -- spoiler boundary: built only from lines up to here
            updated_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, entry_type, name)
        );

        CREATE TABLE IF NOT EXISTS style_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT,               -- 'global' or 'series:<id>'
            profile_json TEXT,        -- learned preferences
            sample_count INTEGER DEFAULT 0,
            updated_at TEXT,
            UNIQUE(scope)
        );

        CREATE TABLE IF NOT EXISTS edit_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER,
            zh TEXT,
            ai_version TEXT,
            user_version TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
    """)


def _create_library_tool_tables(conn):
    conn.executescript("""

        CREATE TABLE IF NOT EXISTS benchmark_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            stage TEXT,              -- 'transcription', 'translation', 'ocr'
            content_type TEXT,       -- 'audio_drama', 'streamer_vod', 'novel', 'manhua' -- descriptive only
            source_language TEXT DEFAULT 'zh',
            input_filename TEXT,     -- relative to the shared benchmark_cases/ dir; NULL for translation cases
            source_text TEXT,        -- translation cases only: the text to translate
            reference_text TEXT,     -- optional known-correct transcript/translation/OCR text
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER,
            run_label TEXT,          -- freeform, e.g. "before VAD threshold change"
            output_text TEXT,
            score REAL,              -- 0.0-1.0, NULL if the case has no reference_text
            duration_seconds REAL,
            cost_usd REAL DEFAULT 0.0,
            error TEXT,
            created_at TEXT,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE
        );

        -- Step 9c: a named, reusable snapshot of a Workspace configuration --
        -- captured at the library level (no drama_id/series_id), so it works
        -- across unrelated series/projects, not just the drama it was saved
        -- from. Applying one is a one-time fill-in, never a live link: no
        -- other table references presets.id, so deleting or renaming a
        -- preset never touches any drama it was previously applied to.
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            translation_engine TEXT,
            engine_model TEXT,        -- e.g. a Claude/Gemini/Ollama model id -- which
                                       -- dict it belongs to is decided by translation_engine
            style_preset TEXT,        -- a tguide.STYLE_PRESETS key
            locale TEXT,               -- 'en-US' / 'en-GB' / 'en-AU'
            default_female_pronouns INTEGER DEFAULT 0,
            include_genre_notes INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );

        -- Step 26b: history for the standalone translate tool -- not tied to
        -- any drama/project (no drama_id), same "library-level" shape as
        -- presets above, since a standalone translation doesn't belong to
        -- any one drama.
        CREATE TABLE IF NOT EXISTS translate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_language TEXT NOT NULL,
            target_language TEXT NOT NULL,
            engine TEXT NOT NULL,
            source_text TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            created_at TEXT
        );

        -- Step 26: reusable voice clips sampled from any drama's clone
        -- reference, for reuse as a character's clone reference in a
        -- *different* project -- library-level, not tied to any one
        -- drama/series, so the source drama can be deleted afterward with no
        -- effect on this entry. clip_filename is a COPY under
        -- db.VOICE_BANK_DIR, never a path back into the source drama's own
        -- folder. source_drama/source_speaker are provenance text only, not a
        -- live foreign key.
        CREATE TABLE IF NOT EXISTS voice_bank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            clip_filename TEXT NOT NULL,
            ref_text TEXT,
            clone_engine TEXT,
            voice_design TEXT,
            language TEXT,
            notes TEXT,
            source_drama TEXT,
            source_speaker TEXT,
            created_at TEXT
        );
    """)


def _create_job_tables(conn):
    conn.executescript("""

        -- Step 25w: cross-process GPU guard. background_jobs.py's own guard
        -- (Step 5c) is plain in-process module state, invisible to a separate OS
        -- process -- this table is the shared coordination point so cli.py's
        -- GPU-touching commands and the live UI can't hold more GPU slots than
        -- the "GPU jobs at once" setting allows. One row per holder; id is the
        -- slot, and the CHECK is the hard cap. See try_acquire_gpu_lock below.
        CREATE TABLE IF NOT EXISTS gpu_lock (
            id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 4),
            holder TEXT NOT NULL,
            description TEXT,
            acquired_at REAL NOT NULL,
            heartbeat_at REAL NOT NULL
        );

        -- Cross-process mirror of background_jobs.py's in-memory _jobs dict, which
        -- another process (`python -m api`, cli.py) cannot see. Records only,
        -- written at status transitions (queued, started, finished), never on
        -- every progress tick. No resume: a job whose owning process dies is not
        -- restarted; its row is closed as cancelled by the owner_pid / heartbeat
        -- sweep (close_orphaned_job_record, close_stale_job_record).
        CREATE TABLE IF NOT EXISTS job_records (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            progress REAL,
            message TEXT,
            error TEXT,
            description TEXT,
            gpu_touching INTEGER DEFAULT 0,
            started_at REAL,
            finished_at REAL,
            updated_at REAL NOT NULL
        );

        -- A general-purpose, cross-process app-settings store -- not
        -- sources/store.py's settings table, which is deliberately scoped to
        -- the source-adapter system's own domain (its own docstring: "a
        -- separate domain from the drama/line data"). This table is for
        -- app-wide runtime toggles (e.g. background_jobs.py's GPU-limit and
        -- notify-on-completion) that a module global would hide from a
        -- separate process and lose on every restart. Same
        -- JSON-encoded-value/upsert shape as sources/store.py's own
        -- settings table, for consistency, not shared storage.
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- Step 42: the maintenance assistant's small project backlog
        -- ("track this as a bug"). Written only by the user, through
        -- services/maintenance_assistant_service.py.
        CREATE TABLE IF NOT EXISTS assistant_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Step 41 (services/job_checkpoint_service.py): per-unit progress
        -- of a long job, so a re-run after a crash or cancel skips the
        -- units already done. `scope` already folds in the input, model
        -- and settings, so a changed run never reuses stale units.
        CREATE TABLE IF NOT EXISTS job_checkpoints (
            scope TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (scope, unit_id)
        );

        -- Step 41 item 1: opt-in result cache keyed on
        -- (kind, input_hash, model, settings_hash).
        CREATE TABLE IF NOT EXISTS result_cache (
            cache_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            settings_hash TEXT NOT NULL,
            value_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_result_cache_kind ON result_cache(kind, created_at);

        -- Step 41 item 5 (services/job_timing_service.py): one row per
        -- stage of a real job (duration and the estimated spend logged
        -- while it ran). Job ids repeat across runs; `run_started_at`
        -- tells runs apart.
        CREATE TABLE IF NOT EXISTS job_stage_timings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            run_started_at REAL NOT NULL,
            stage TEXT NOT NULL,
            started_at REAL NOT NULL,
            ended_at REAL NOT NULL,
            duration_s REAL NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_job_stage_timings_job ON job_stage_timings(job_id, run_started_at);

        -- Step 41 item 4 (services/line_provenance_service.py): what
        -- produced each line's current translation.
        CREATE TABLE IF NOT EXISTS line_provenance (
            drama_id INTEGER NOT NULL,
            line_id INTEGER NOT NULL,
            engine TEXT,
            model TEXT,
            prompt_version TEXT,
            glossary_hash TEXT,
            settings_hash TEXT,
            input_hash TEXT,
            output_hash TEXT,
            software_version TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY (drama_id, line_id)
        );
    """)


def _create_metadata_research_tables(conn):
    conn.executescript("""

        -- Step 37: grounded metadata research. The cache is keyed by the
        -- looked-up entity (not the drama), so a repeat lookup never
        -- re-spends the daily free-search budget; the provenance table
        -- holds one row per researched field value the user accepted
        -- ("applied") or kept beside the existing value ("alternate").
        CREATE TABLE IF NOT EXISTS metadata_research_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- One row per lookup shown to a user (Step 37): apply reads the
        -- snapshot by this id and drama, never the shared entity cache.
        CREATE TABLE IF NOT EXISTS metadata_research_results (
            research_id TEXT PRIMARY KEY,
            drama_id INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS metadata_field_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT,
            source_url TEXT,
            sources_json TEXT,
            retrieved_at TEXT,
            confidence REAL,
            last_verified TEXT,
            status TEXT NOT NULL,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
    """)


def _create_core_indexes(conn):
    conn.executescript("""

        CREATE INDEX IF NOT EXISTS idx_lines_drama ON lines(drama_id);
        CREATE INDEX IF NOT EXISTS idx_characters_drama ON characters(drama_id);
        CREATE INDEX IF NOT EXISTS idx_pages_drama ON pages(drama_id);
        CREATE INDEX IF NOT EXISTS idx_bubbles_page ON bubbles(page_id);
        CREATE INDEX IF NOT EXISTS idx_known_titles_lang ON known_titles(language);
        CREATE INDEX IF NOT EXISTS idx_field_provenance_drama ON metadata_field_provenance(drama_id);
        CREATE INDEX IF NOT EXISTS idx_glossary_series ON glossary_terms(series_id);
        CREATE INDEX IF NOT EXISTS idx_tm_series ON translation_memory(series_id);
        CREATE INDEX IF NOT EXISTS idx_series_characters_series ON series_characters(series_id);
        CREATE INDEX IF NOT EXISTS idx_vocab_drama ON vocab_lookups(drama_id);
        CREATE INDEX IF NOT EXISTS idx_usage_drama ON usage_log(drama_id);
        CREATE INDEX IF NOT EXISTS idx_history_drama ON line_history(drama_id);
        CREATE INDEX IF NOT EXISTS idx_reading_history_drama ON reading_history(drama_id);
        CREATE INDEX IF NOT EXISTS idx_versions_drama ON translation_versions(drama_id);
        CREATE INDEX IF NOT EXISTS idx_wiki_drama ON wiki_entries(drama_id);
        CREATE INDEX IF NOT EXISTS idx_edits_drama ON edit_samples(drama_id);
        CREATE INDEX IF NOT EXISTS idx_benchmark_runs_case ON benchmark_runs(case_id);
        CREATE INDEX IF NOT EXISTS idx_translate_history_created ON translate_history(created_at);
        CREATE INDEX IF NOT EXISTS idx_voice_bank_name ON voice_bank(name);
    """)


def _migrate_job_records_columns(conn):
    # Migration Slice 22: cross-process cancel request flag on the job mirror.
    jr_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_records)").fetchall()}
    if "cancel_requested" not in jr_cols:
        _safe_alter(conn, "ALTER TABLE job_records ADD COLUMN cancel_requested INTEGER DEFAULT 0")
    # A job's redacted, allowlisted result (services/jobs_service.project_result),
    # JSON-encoded, so the API can tell a "done" job that failed from one that worked.
    if "result_json" not in jr_cols:
        _safe_alter(conn, "ALTER TABLE job_records ADD COLUMN result_json TEXT")
    # The OS pid of the process running the job, so a row whose owner
    # has exited can be closed at once instead of after the heartbeat
    # cutoff (services/jobs_service.sweep_stale_job_records).
    if "owner_pid" not in jr_cols:
        _safe_alter(conn, "ALTER TABLE job_records ADD COLUMN owner_pid INTEGER")


def _create_auth_tables(conn):
    # Step 133: API users, permissions, server-side sessions, audit log.
    # Additive only; nothing above is touched. Session ids / CSRF tokens
    # are stored as SHA-256 hashes only (see services/auth_service.py).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_sub TEXT UNIQUE,
            email TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE TABLE IF NOT EXISTS user_permissions (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission TEXT NOT NULL,
            PRIMARY KEY (user_id, permission)
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            user_agent_short TEXT DEFAULT '',
            ip_prefix TEXT DEFAULT '',
            csrf_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail_redacted TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, id);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, id);
    """)


def _migrate_line_columns(conn):
    # Lightweight migrations for DBs created before these columns existed
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()}
    if "speaker" not in existing_cols:
        _safe_alter(conn, "ALTER TABLE lines ADD COLUMN speaker TEXT")
    if "dub_filename" not in existing_cols:
        _safe_alter(conn, "ALTER TABLE lines ADD COLUMN dub_filename TEXT")
    if "flag" not in existing_cols:
        # A key from translate_engines.FLAG_REASONS, set by flag_uncertain_lines()
        # -- the review queue for a long file, so a person doesn't have to
        # scan every line to find the handful worth a second look.
        _safe_alter(conn, "ALTER TABLE lines ADD COLUMN flag TEXT")
    if "flag_note" not in existing_cols:
        _safe_alter(conn, "ALTER TABLE lines ADD COLUMN flag_note TEXT")
    if "speaker_manual" not in existing_cols:
        # 1 once a line's speaker was set by hand; re-running speaker
        # detection won't overwrite it without confirmation (Step 4).
        _safe_alter(conn, "ALTER TABLE lines ADD COLUMN speaker_manual INTEGER DEFAULT 0")
    if "sfx" not in existing_cols:
        # 1 for a non-verbal/SFX cue line ("[door slams]") -- exported
        # bracketed and styled apart from dialogue (Step 12c).
        _safe_alter(conn, "ALTER TABLE lines ADD COLUMN sfx INTEGER DEFAULT 0")


def _migrate_drama_columns(conn):
    drama_cols = {r[1] for r in conn.execute("PRAGMA table_info(dramas)").fetchall()}
    if "translation_engine" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN translation_engine TEXT DEFAULT 'claude'")
    if "content_mode" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN content_mode TEXT DEFAULT 'audio_drama'")
    if "narration_language" not in drama_cols:
        # Step 26c: novel narration only -- 'translation' (default, existing
        # behavior) speaks ln.en; 'original' speaks ln.zh (the app's generic
        # source-text field, holding ja/ko source text too when that's the
        # drama's actual source_language).
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN narration_language TEXT DEFAULT 'translation'")
    if "source_video_filename" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN source_video_filename TEXT")
    if "source_language" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN source_language TEXT DEFAULT 'zh'")
    if "chinese_script" not in drama_cols:
        # Only meaningful when source_language == "zh": Whisper transcription
        # and LLM translation don't care (they read/produce either script
        # fine), but OCR (Tesseract's chi_sim vs chi_tra language pack) and
        # jieba segmentation (built for Simplified, degrades on Traditional)
        # both need to know which one they're looking at.
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN chinese_script TEXT DEFAULT 'simplified'")
    if "media_type" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN media_type TEXT DEFAULT 'audio_drama'")
    if "series_id" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN series_id INTEGER")
    if "episode_number" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN episode_number INTEGER")
    if "episode_summary" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN episode_summary TEXT")
    if "updated_at" not in drama_cols:
        _safe_alter(conn, "ALTER TABLE dramas ADD COLUMN updated_at TEXT")
    for col, coltype in [("last_translate_errors", "TEXT"),
                          ("author_romanized", "TEXT"), ("studio_romanized", "TEXT"),
                          ("voice_actors_romanized", "TEXT"), ("director_romanized", "TEXT"),
                          ("cover_art_filename", "TEXT"), ("genre", "TEXT"),
                          ("publication_status", "TEXT"), ("chapter_count", "INTEGER"),
                          ("custom_tags", "TEXT"), ("personal_notes", "TEXT"),
                          # The original URL a stream/VOD was downloaded from --
                          # title_en/title_zh already double as translated/
                          # untranslated stream name, this was the missing piece
                          # (no dedicated "where did this come from" field existed).
                          ("source_url", "TEXT"),
                          # Recognition/alignment pipeline choices, per drama, so
                          # "I don't have a transcript" (and the model/backend
                          # picks) survive an app restart.
                          ("transcript_mode", "TEXT"), ("whisper_size", "TEXT"),
                          ("alignment_method", "TEXT"), ("asr_backend_choice", "TEXT"),
                          # The remaining Whisper-tuning knobs, persisted per drama
                          # so a stateless API client can read a real per-drama
                          # default.
                          ("min_silence_ms", "INTEGER DEFAULT 300"),
                          ("vad_threshold", "REAL DEFAULT 0.5"),
                          ("beam_size", "INTEGER DEFAULT 5"),
                          ("separate_vocals_first", "INTEGER DEFAULT 0"),
                          ("separation_backend", "TEXT DEFAULT 'auto'"),
                          ("realign_long_segments", "INTEGER DEFAULT 0"),
                          ("whisper_fast_mode", "INTEGER DEFAULT 0"),
                          ("use_groq", "INTEGER DEFAULT 0"),
                          # Migration Slice 21: hardsub_ocr's own two tuning knobs --
                          # same "previously session-state only" gap as Slice 20's.
                          ("hardsub_ocr_backend", "TEXT"),
                          ("hardsub_interval_sec", "REAL DEFAULT 1.0"),
                          # Step 12e: freeform, multi-line instructions that DO reach
                          # the translation prompt (translate_engines.build_llm_instructions)
                          # -- unlike personal_notes above, which is private and never
                          # sent anywhere. The series-level counterpart is
                          # series.instructions, inherited by every drama in the series.
                          ("project_instructions", "TEXT"),
                          # Roadmap 112: the Notion page this drama was last exported
                          # to (services/notion_service.py), so a re-export updates
                          # that page in place. Only the id, never a token or URL.
                          ("notion_page_id", "TEXT")]:
        if col not in drama_cols:
            _safe_alter(conn, f"ALTER TABLE dramas ADD COLUMN {col} {coltype}")


def _migrate_series_and_character_columns(conn):
    series_cols = {r[1] for r in conn.execute("PRAGMA table_info(series)").fetchall()}
    if "instructions" not in series_cols:
        _safe_alter(conn, "ALTER TABLE series ADD COLUMN instructions TEXT")
    char_cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
    if "ref_audio_filename" not in char_cols:
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN ref_audio_filename TEXT")
    if "ref_text" not in char_cols:
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN ref_text TEXT")
    if "elevenlabs_voice_id" not in char_cols:
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN elevenlabs_voice_id TEXT")
    if "clone_engine" not in char_cols:
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN clone_engine TEXT")
    if "voice_design" not in char_cols:
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN voice_design TEXT")
    if "offline_voice" not in char_cols:
        # Step 25c: Piper can't load an edge-tts voice name, so the offline
        # engine gets its own per-character voice instead of reading tts_voice.
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN offline_voice TEXT")
    if "series_character_id" not in char_cols:
        # Links this drama's speaker to a persistent series_characters row,
        # so renaming/updating the series-level character (once) reflects
        # everywhere it's been assigned, instead of needing a per-drama edit.
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN series_character_id INTEGER")
    if "pronouns" not in char_cols:
        # Per-drama pronoun text ("she/her", "they/them", "xe/xem", ...) --
        # lets a drama with no series set pronouns at all, and overrides
        # the linked series character's value when both are set.
        _safe_alter(conn, "ALTER TABLE characters ADD COLUMN pronouns TEXT")
    gloss_cols = {r[1] for r in conn.execute("PRAGMA table_info(glossary_terms)").fetchall()}
    if "category" not in gloss_cols:
        _safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN category TEXT")
    if "policy" not in gloss_cols:
        _safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN policy TEXT")
    if "enforce_exact" not in gloss_cols:
        _safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN enforce_exact INTEGER DEFAULT 0")
    if "aliases" not in gloss_cols:
        _safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN aliases TEXT")
    if "banned_translations" not in gloss_cols:
        _safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN banned_translations TEXT")
    sc_cols = {r[1] for r in conn.execute("PRAGMA table_info(series_characters)").fetchall()}
    if "gender" not in sc_cols:
        # Feeds translation as a fixed pronoun hint for this character
        # (e.g. "Su Shan: she/her") -- Mandarin's spoken 他/她/它 are
        # homophones, so Whisper's transcribed character for a pronoun is
        # not a reliable gender signal on its own, and misgendering a
        # named character is a much more visible error than an ambiguous
        # unnamed one. NULL/"" means unset -- no hint is added for that
        # character, distinct from "unspecified" as a deliberate choice.
        _safe_alter(conn, "ALTER TABLE series_characters ADD COLUMN gender TEXT")
    usage_cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
    if "cache_read_tokens" not in usage_cols:
        # Step 9: the part of input_tokens served from a provider prompt
        # cache, so the dashboard can show how often caching actually hits.
        _safe_alter(conn, "ALTER TABLE usage_log ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
    if "voice_fingerprint" not in sc_cols:
        # Step 8: a running-average pyannote voice embedding (JSON list of
        # floats), built up from every drama where a speaker was confirmed
        # (by Accept, never automatically) as this character -- see
        # update_series_character_voice_fingerprint(). Compared by cosine
        # similarity against a NEW drama's own per-speaker embeddings to
        # suggest "this speaker sounds like <name>". NULL until at least
        # one confirmed sample exists.
        _safe_alter(conn, "ALTER TABLE series_characters ADD COLUMN voice_fingerprint TEXT")
        _safe_alter(conn, "ALTER TABLE series_characters ADD COLUMN voice_fingerprint_samples INTEGER DEFAULT 0")


def _migrate_scanlate_columns(conn):
    bubble_cols = {r[1] for r in conn.execute("PRAGMA table_info(bubbles)").fetchall()}
    if "font_category" not in bubble_cols:
        # One of scanlate.FONT_CATEGORIES ("regular"/"bold"/"handwritten"),
        # auto-filled from sample_text_style()'s classical-CV stroke-weight/
        # irregularity analysis at detection time, editable per bubble
        # before render -- see scanlate.py's own docstring for why this
        # isn't a trained font-classifier model.
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN font_category TEXT DEFAULT 'regular'")
    if "kind" not in bubble_cols:
        # Step 12d: each bubble row is a structured text region (see
        # scanlate.TextRegion) -- region type from classify_text_regions(),
        # the detector's own confidence (NULL for the OpenCV heuristic,
        # which has none), language, text orientation, and panel. Rows
        # predating this are all speech bubbles, hence kind's default.
        # include_sfx is the per-region override that puts an SFX region
        # back into the automated inpaint-and-replace pass.
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN kind TEXT DEFAULT 'bubble'")
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN kind_confidence REAL")
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN confidence REAL")
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN language TEXT")
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN orientation TEXT")
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN panel_id INTEGER")
        _safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN include_sfx INTEGER DEFAULT 0")
    page_cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)").fetchall()}
    if "rev" not in page_cols:
        # Scanlate S0: rev is bumped by every id-preserving region write
        # (insert/update/delete/reorder/replace_bubbles_if_unchanged; not
        # the legacy save_bubbles); context_summary is the rolling
        # translation context after this page; run_notes is a redacted
        # JSON list of {level, message} from the last automatic run.
        _safe_alter(conn, "ALTER TABLE pages ADD COLUMN rev INTEGER DEFAULT 0")
    if "context_summary" not in page_cols:
        _safe_alter(conn, "ALTER TABLE pages ADD COLUMN context_summary TEXT")
    if "run_notes" not in page_cols:
        _safe_alter(conn, "ALTER TABLE pages ADD COLUMN run_notes TEXT")


def _migrate_bulk_job_columns(conn):
    bulk_job_cols ={r[1] for r in conn.execute("PRAGMA table_info(bulk_jobs)").fetchall()}
    if "kind" not in bulk_job_cols:
        # Step 9d: see the `bulk_jobs` table's own comment above -- every
        # bulk job predating this column was a translation job.
        _safe_alter(conn, "ALTER TABLE bulk_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'translate'")
        _safe_alter(conn, "ALTER TABLE bulk_jobs ADD COLUMN stage TEXT")
        _safe_alter(conn, "ALTER TABLE bulk_jobs ADD COLUMN pipeline_id TEXT")
    bulk_job_line_cols = {r[1] for r in conn.execute("PRAGMA table_info(bulk_job_lines)").fetchall()}
    if "result_text" not in bulk_job_line_cols:
        _safe_alter(conn, "ALTER TABLE bulk_job_lines ADD COLUMN result_text TEXT")
        _safe_alter(conn, "ALTER TABLE bulk_job_lines ADD COLUMN state_at_submit TEXT")


def _migrate_vocab_and_style_columns(conn):
    vocab_cols = {r[1] for r in conn.execute("PRAGMA table_info(vocab_lookups)").fetchall()}
    if "export_rich" not in vocab_cols:
        # Step 20b: flags a lookup as queued for the richer sentence+audio
        # Anki card type, set from the Reader right where the word was
        # looked up, rather than only via a bulk end-of-session export.
        _safe_alter(conn, "ALTER TABLE vocab_lookups ADD COLUMN export_rich INTEGER DEFAULT 0")
    style_cols = {r[1] for r in conn.execute("PRAGMA table_info(style_profile)").fetchall()}
    if "history_json" not in style_cols:
        # Earlier learned-style profiles (newest first, at most
        # STYLE_HISTORY_KEEP), so a learn or reset can be undone.
        _safe_alter(conn, "ALTER TABLE style_profile ADD COLUMN history_json TEXT")


def _migrate_ownership_columns(conn):
    # Auth slice B1: ownership and sharing (services/ownership_service.py).
    # Existing rows keep owner_user_id NULL / is_private 0, meaning "the PC
    # owner / admins, shared" -- no admin id is guessed.
    for table, col, coltype in (
        ("dramas", "owner_user_id", "INTEGER"),
        ("dramas", "is_private", "INTEGER DEFAULT 0"),
        ("series", "owner_user_id", "INTEGER"),
        ("series", "is_private", "INTEGER DEFAULT 0"),
        ("users", "share_by_default", "INTEGER DEFAULT 0"),
        ("translate_history", "user_id", "INTEGER"),
        ("job_records", "owner_user_id", "INTEGER"),
    ):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            _safe_alter(conn, f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    # New items became private by default (user decision 2026-09-30).
    # A users.share_by_default added before that defaulted to 1, which
    # nobody chose (nothing could set it): switch every account off once.
    # The marker keeps later choices across restarts.
    user_cols = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    marker = "migrations.share_by_default_off"
    if str(user_cols.get("share_by_default")) == "1" and conn.execute(
            "SELECT 1 FROM app_settings WHERE key = ?", (marker,)).fetchone() is None:
        conn.execute("UPDATE users SET share_by_default = 0")
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, 'true')", (marker,))


def _migrate_auth_session_columns(conn):
    session_cols = {r[1] for r in conn.execute("PRAGMA table_info(auth_sessions)").fetchall()}
    if "device_label" not in session_cols:
        # A coarse "Chrome on Android" label (auth_service.device_label),
        # the only device detail the signed-in-devices list shows.
        _safe_alter(conn, "ALTER TABLE auth_sessions ADD COLUMN device_label TEXT DEFAULT ''")
    # Sessions from before the label kept the first 60 characters of the
    # user agent: label them from it, then drop it, so no raw user agent
    # stays stored. Rows already blanked don't match, so this runs once.
    old_agents = conn.execute(
        "SELECT id, user_agent_short, device_label FROM auth_sessions "
        "WHERE user_agent_short IS NOT NULL AND user_agent_short != ''").fetchall()
    if old_agents:
        from services.auth_service import device_label
        conn.executemany(
            "UPDATE auth_sessions SET device_label = ?, user_agent_short = '' WHERE id = ?",
            [(r[2] or device_label(r[1]), r[0]) for r in old_agents])


def _migrate_off_removed_test_engine(conn):
    # The fake "test_offline" engine is gone: a drama or preset still on it
    # goes back to the default engine instead of failing to start a run.
    conn.execute("UPDATE dramas SET translation_engine = 'claude' WHERE translation_engine = 'test_offline'")
    conn.execute("UPDATE presets SET translation_engine = 'claude' WHERE translation_engine = 'test_offline'")


# ---------------------------------------------------------------------------
# Step 40b: model re-evaluation candidates and decisions
# ---------------------------------------------------------------------------

def create_model_candidate(capability: str, engine: str, model: str, note: str = "") -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO model_candidates (capability, engine, model, note, status, created_at) "
            "VALUES (?, ?, ?, ?, 'candidate', ?)",
            (capability, engine, model, note, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def get_model_candidate(candidate_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM model_candidates WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def find_model_candidate(capability: str, engine: str, model: str):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM model_candidates WHERE capability = ? AND engine = ? AND model IS ?",
            (capability, engine, model)).fetchone()
    return dict(row) if row else None


def list_model_candidates(capability: str):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM model_candidates WHERE capability = ? ORDER BY id",
                            (capability,)).fetchall()
    return [dict(r) for r in rows]


def set_model_candidate_status(candidate_id: int, status: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE model_candidates SET status = ? WHERE id = ?", (status, candidate_id))
        conn.commit()


def record_model_decision(candidate_id: int, decision: str, reason: str, scores_json: str) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO model_decisions (candidate_id, decision, reason, scores, decided_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (candidate_id, decision, reason, scores_json, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def latest_model_decision(candidate_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM model_decisions WHERE candidate_id = ? "
                           "ORDER BY id DESC LIMIT 1", (candidate_id,)).fetchone()
    return dict(row) if row else None


def list_model_decisions(capability: str):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT d.* FROM model_decisions d JOIN model_candidates c ON d.candidate_id = c.id "
            "WHERE c.capability = ? ORDER BY d.id DESC", (capability,)).fetchall()
    return [dict(r) for r in rows]


def _init_benchmark_lab_schema():
    """Step 38 (Benchmark Lab): golden-set tiers on benchmark_cases, plus a
    per-run record (benchmark_sessions: engine/model/prompt version/context,
    aggregate score, latency, cost, VRAM) and its per-case results. Additive
    only; the older benchmark_runs history is left as it was."""
    with contextlib.closing(get_conn()) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS benchmark_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT DEFAULT '',
            stage TEXT,              -- 'translation', 'transcription', 'ocr'
            engine TEXT,             -- translate engine, 'whisper', or an OCR backend
            model TEXT,
            prompt_version TEXT DEFAULT '',
            context_settings TEXT,   -- JSON
            case_filter TEXT,        -- JSON: which cases were selected
            arena_group TEXT,        -- shared by runs started together (Model Arena)
            status TEXT,             -- running / done / stopped_cap / cancelled / failed
            case_count INTEGER DEFAULT 0,
            scored_count INTEGER DEFAULT 0,
            passed_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            aggregate_score REAL,
            avg_latency_seconds REAL,
            total_cost_usd REAL DEFAULT 0.0,
            peak_vram_mb REAL,
            note TEXT,
            created_at TEXT,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS benchmark_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            case_id INTEGER,
            case_label TEXT,
            output_text TEXT,
            score REAL,
            metric TEXT,             -- 'similarity', 'cer', 'wer'
            scorer TEXT,             -- 'jiwer', 'builtin'; NULL on rows from before it was recorded
            passed INTEGER,
            duration_seconds REAL,
            cost_usd REAL DEFAULT 0.0,
            error TEXT,
            created_at TEXT,
            FOREIGN KEY (session_id) REFERENCES benchmark_sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_benchmark_results_session ON benchmark_results(session_id);
        -- Step 40b: candidate models for scheduled re-evaluation, and every
        -- promote/reject decision (kept so a rejected candidate isn't re-proposed
        -- as new).
        CREATE TABLE IF NOT EXISTS model_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capability TEXT NOT NULL,
            engine TEXT NOT NULL,
            model TEXT,
            note TEXT DEFAULT '',
            status TEXT DEFAULT 'candidate',   -- candidate / promoted / rejected
            created_at TEXT,
            UNIQUE(capability, engine, model)
        );
        CREATE TABLE IF NOT EXISTS model_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            decision TEXT NOT NULL,            -- promoted / rejected
            reason TEXT DEFAULT '',
            scores TEXT,                       -- JSON snapshot of the runs compared
            decided_at TEXT,
            FOREIGN KEY (candidate_id) REFERENCES model_candidates(id) ON DELETE CASCADE
        );
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(benchmark_cases)").fetchall()}
        for col, coltype in (("tier", "TEXT DEFAULT 'application'"), ("set_name", "TEXT DEFAULT ''"),
                             ("origin_drama_id", "INTEGER"), ("origin_line_id", "INTEGER")):
            if col not in cols:
                _safe_alter(conn, f"ALTER TABLE benchmark_cases ADD COLUMN {col} {coltype}")
        result_cols = {r[1] for r in conn.execute("PRAGMA table_info(benchmark_results)").fetchall()}
        if "scorer" not in result_cols:
            _safe_alter(conn, "ALTER TABLE benchmark_results ADD COLUMN scorer TEXT")
        conn.commit()


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


def _migrate_line_refs_to_ids():
    """One-time (Step 2): translation_notes, line_emotions and
    reading_history pointed at a line by its position (line_idx), which a
    merge or split renumbers -- so a note silently ended up on a
    different line. They now point at lines.id.

    Safe to interrupt: the affected tables are first copied as-is into
    _backup_step2_<table> (kept, never overwritten on a re-run), then the
    rebuild runs as ONE transaction -- SQLite rolls DDL back too, so a
    crash partway leaves the old tables untouched and the next start just
    runs it again. Once done, the line_id columns exist and this is a no-op."""
    conn = get_conn()
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    try:
        def cols(t):
            return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        rebuild = [t for t in _LINE_REF_TABLE_DDL if "line_id" not in cols(t)]
        history = "line_id" not in cols("reading_history")
        if not rebuild and not history:
            return
        conn.execute("BEGIN")
        for t in rebuild + (["reading_history"] if history else []):
            conn.execute(f"CREATE TABLE IF NOT EXISTS _backup_step2_{t} AS SELECT * FROM {t}")
        conn.execute("COMMIT")

        conn.execute("BEGIN")
        for t in rebuild:
            new = f"{t}_step2_new"
            keep = _LINE_REF_TABLE_COLUMNS[t]
            conn.execute(f"DROP TABLE IF EXISTS {new}")
            conn.execute(_LINE_REF_TABLE_DDL[t].format(name=new))
            conn.execute(
                f"INSERT OR IGNORE INTO {new} (id, line_id, {', '.join(keep)}) "
                f"SELECT t.id, {_LINE_ID_FOR_IDX_SQL}, {', '.join('t.' + c for c in keep)} FROM {t} t")
            conn.execute(f"DROP TABLE {t}")
            conn.execute(f"ALTER TABLE {new} RENAME TO {t}")
        if history:
            conn.execute("ALTER TABLE reading_history ADD COLUMN line_id INTEGER")
            conn.execute("UPDATE reading_history SET line_id = " + _LINE_ID_FOR_IDX_SQL.replace("t.", "reading_history."))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _line_id_for_idx(conn, drama_id, line_idx):
    if line_idx is None:
        return None
    row = conn.execute("SELECT id FROM lines WHERE drama_id = ? AND idx = ? ORDER BY id LIMIT 1",
                       (drama_id, int(line_idx))).fetchone()
    return row["id"] if row else None


def _migrate_step26e_profiles():
    """One-time (Step 26e): creates the default profile every pre-profiles
    install's existing progress/reading_history/personal_notes data gets
    attached to, and rebuilds `progress` for its new (drama_id, profile_id)
    primary key -- SQLite can't ALTER a PRIMARY KEY in place, so this is a
    backup-then-rebuild, same pattern as _migrate_line_refs_to_ids above.

    Keyed off `profiles` being empty: a fresh install (via the
    CREATE TABLE IF NOT EXISTS block above) already gets the new `progress`
    schema directly and starts with zero profiles too, so this still runs
    for it -- it just has no old rows to migrate, and creates the same
    default profile a real upgrade would. Once at least one profile exists
    this is a no-op forever after (delete_profile refuses to remove the
    last one, so that stays true)."""
    conn = get_conn()
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    try:
        if conn.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
            return
        conn.execute("BEGIN")
        now = datetime.datetime.utcnow().isoformat()
        cur = conn.execute("INSERT INTO profiles (name, color, created_at) VALUES (?, ?, ?)",
                           ("Me", None, now))
        default_id = cur.lastrowid

        progress_cols = {r[1] for r in conn.execute("PRAGMA table_info(progress)").fetchall()}
        if "profile_id" not in progress_cols:
            conn.execute("""
                CREATE TABLE progress_step26e_new (
                    drama_id INTEGER NOT NULL,
                    profile_id INTEGER NOT NULL,
                    last_line_idx INTEGER DEFAULT 0,
                    audio_position_seconds REAL DEFAULT 0,
                    last_page INTEGER DEFAULT 1,
                    percent_complete REAL DEFAULT 0,
                    last_accessed_at TEXT,
                    PRIMARY KEY (drama_id, profile_id),
                    FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                INSERT INTO progress_step26e_new
                    (drama_id, profile_id, last_line_idx, audio_position_seconds,
                     last_page, percent_complete, last_accessed_at)
                SELECT drama_id, ?, last_line_idx, audio_position_seconds,
                       last_page, percent_complete, last_accessed_at
                FROM progress
            """, (default_id,))
            conn.execute("DROP TABLE progress")
            conn.execute("ALTER TABLE progress_step26e_new RENAME TO progress")

        history_cols = {r[1] for r in conn.execute("PRAGMA table_info(reading_history)").fetchall()}
        if "profile_id" not in history_cols:
            conn.execute("ALTER TABLE reading_history ADD COLUMN profile_id INTEGER")
        conn.execute("UPDATE reading_history SET profile_id = ? WHERE profile_id IS NULL",
                     (default_id,))

        drama_cols = {r[1] for r in conn.execute("PRAGMA table_info(dramas)").fetchall()}
        if "personal_notes" in drama_cols:
            conn.execute("""
                INSERT INTO personal_notes (profile_id, drama_id, notes, updated_at)
                SELECT ?, id, personal_notes, ?
                FROM dramas WHERE personal_notes IS NOT NULL AND personal_notes != ''
            """, (default_id, now))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def drama_dir(drama_id: int) -> str:
    d = os.path.join(DRAMAS_DIR, str(drama_id))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Media journal: drama folders moved in by a backup import or restore
# ---------------------------------------------------------------------------
# An import stages drama folders in DRAMAS_DIR/.import-<token>/ and holds an
# OS lock on <staging>/lock while it runs, so recovery in another process
# leaves it alone. Inside its database transaction (new rows inserted, not
# committed) it writes a marker file with a fresh random token into each
# staged folder, then <staging>/journal.json -- each new drama id with that
# token and the folder's identity (st_dev, st_ino) -- and only then renames
# the folders into place and commits. The commit is the commit point: when
# the journal's drama rows don't exist the transaction never committed, and
# a dramas/<id> folder is removed only when the journal lists that id and
# the folder provably is the one it recorded (its marker token matches, or
# its identity does; FAT, exFAT and some network shares change or omit the
# inode on a rename, the marker moves with the folder). Anything else --
# a folder that can't be proven, a damaged journal, a library database that
# can't be read -- is left alone and reported.
MEDIA_STAGING_PREFIX = ".import-"
_MEDIA_STAGING_RE = re.compile(r"\.import-[0-9a-f]{32}", re.ASCII)
_MEDIA_JOURNAL = "journal.json"
_MEDIA_LOCK_FILE = "lock"
MEDIA_MARKER = ".baihe-import-marker"
_TOKEN_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
_MAX_JOURNAL_BYTES = 1024 * 1024
_MAX_JOURNAL_IDS = 10000
_MAX_DRAMA_ID = 2 ** 63 - 1
# A staging folder with no journal moved nothing yet; one this young may
# belong to an import in another process that hasn't taken its lock yet.
STALE_MEDIA_STAGING_SECONDS = 24 * 3600
_DAMAGED_JOURNAL = object()
_media_lock = threading.RLock()
# Imports running in this process: staging name -> {"lock": [fd or None],
# "moved": {drama id: staged folder it was renamed from}}.
_active_media_stagings = {}

log = logging.getLogger(__name__)


class DramaFolderConflict(RuntimeError):
    """dramas/<id> already exists for a drama id being created and could not
    be renamed aside."""


def _is_link(st) -> bool:
    """A symlink, or on Windows any reparse point (a junction too)."""
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _real_dir(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not _is_link(st)


def _folder_identity(path: str):
    """[st_dev, st_ino] of a real directory (not a link), else None; also
    None when the file system reports no inode number (it can't prove which
    folder is which)."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or _is_link(st) or not st.st_ino:
        return None
    return [int(st.st_dev), int(st.st_ino)]


def _marker_matches(folder: str, token) -> bool:
    if not (isinstance(token, str) and _TOKEN_RE.fullmatch(token)):
        return False
    path = os.path.join(folder, MEDIA_MARKER)
    try:
        st = os.lstat(path)
        if _is_link(st) or not stat.S_ISREG(st.st_mode) or st.st_size > 64:
            return False
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as fh:
            return fh.read(65).strip() == token.encode("ascii")
    except OSError:
        return False


def _is_journalled_folder(final: str, entry: dict) -> bool:
    """True only when dramas/<id> is provably the folder the journal entry
    recorded: a real directory whose marker token or identity matches."""
    if not _real_dir(final):
        return False
    marker = entry.get("marker")
    if marker:
        # A marker was written into the staged folder, so it is the proof.
        # Inode numbers are reused: a folder someone else put in its place
        # can carry the same identity, so identity alone must not count.
        return _marker_matches(final, marker)
    ident = entry.get("ident")
    return ident is not None and _folder_identity(final) == ident


def fsync_dir(path: str):
    """Makes a rename into `path` durable; raises OSError. POSIX only:
    Windows can't open a directory and commits the rename with the file."""
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_file(path: str, create: bool):
    """Opens `path` and takes an exclusive OS lock on it without waiting;
    the fd, or None when another process (or another open of it) holds the
    lock or it can't be opened."""
    try:
        fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_lock(holder: list):
    """Unlocks and closes holder[0] (once). Windows can't delete an open
    file, so this runs before the staging folder is removed."""
    fd, holder[0] = holder[0], None
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def new_media_staging() -> str:
    """Creates an empty staging folder DRAMAS_DIR/.import-<token>/ for one
    import, locked and registered as running (recovery leaves it alone)
    until finish_media_staging; returns its path."""
    os.makedirs(DRAMAS_DIR, exist_ok=True)
    name = MEDIA_STAGING_PREFIX + uuid.uuid4().hex
    path = os.path.join(DRAMAS_DIR, name)
    with _media_lock:
        os.mkdir(path)
        # A file system without locks still imports; recovery elsewhere
        # then can't lock the folder either and leaves it alone.
        _active_media_stagings[name] = {
            "lock": [_lock_file(os.path.join(path, _MEDIA_LOCK_FILE), True)], "moved": {}}
    return path


def write_media_journal(staging: str, folders: dict):
    """Marks each staged folder and records {new drama id: its marker token
    and identity} in the staging folder's journal, flushed to disk; call
    inside the import's transaction, before any folder is moved into place."""
    ids = {}
    for did, path in folders.items():
        token = uuid.uuid4().hex
        with open(os.path.join(path, MEDIA_MARKER), "w", encoding="ascii") as fh:
            fh.write(token)
            fh.flush()
            os.fsync(fh.fileno())
        ids[str(int(did))] = {"marker": token, "ident": _folder_identity(path)}
    # Markers and journal are fsynced, and the journal lands by os.replace
    # (then a directory fsync), before any folder moves: after a crash,
    # recovery trusts the journal as proof of which folders it may remove,
    # so it must be durable and never seen half-written.
    tmp = os.path.join(staging, _MEDIA_JOURNAL + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"format": 2, "staging": os.path.basename(staging), "ids": ids}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, os.path.join(staging, _MEDIA_JOURNAL))
    fsync_dir(staging)


def move_staged_folder(staging: str, drama_id: int, staged: str) -> bool:
    """Renames a staged folder to dramas/<drama_id> (after
    write_media_journal) and remembers the move, so a failure before the
    commit can rename it straight back. False, moving nothing, when that
    path already exists."""
    final = os.path.join(DRAMAS_DIR, str(int(drama_id)))
    with _media_lock:
        if os.path.lexists(final):
            return False
        os.rename(staged, final)
        entry = _active_media_stagings.get(os.path.basename(staging))
        if entry is not None:
            entry["moved"][int(drama_id)] = staged
    return True


def _journal_id(key) -> int:
    if not (isinstance(key, str) and 0 < len(key) <= 19 and key.isascii() and key.isdigit()
            and str(int(key)) == key and 0 < int(key) <= _MAX_DRAMA_ID):
        raise ValueError
    return int(key)


def _journal_entry(value) -> dict:
    if not (isinstance(value, dict) and isinstance(value.get("marker"), str)
            and _TOKEN_RE.fullmatch(value["marker"])):
        raise ValueError
    ident = value.get("ident")
    if ident is not None and not (isinstance(ident, list) and len(ident) == 2 and all(
            isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 64 for v in ident)):
        raise ValueError
    return {"marker": value["marker"], "ident": ident}


def _read_media_journal(staging: str):
    """{drama id: {"marker", "ident"}}; None when there is no journal (the
    import stopped before moving anything); _DAMAGED_JOURNAL when it can't
    be read or doesn't belong to this staging folder. Never raises."""
    path = os.path.join(staging, _MEDIA_JOURNAL)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except Exception:
        return _DAMAGED_JOURNAL
    try:
        if _is_link(st) or not stat.S_ISREG(st.st_mode) or st.st_size > _MAX_JOURNAL_BYTES:
            return _DAMAGED_JOURNAL
        with open(path, "rb") as fh:
            data = json.loads(fh.read(_MAX_JOURNAL_BYTES + 1).decode("utf-8"))
        if not (isinstance(data, dict) and data.get("format") == 2
                and data.get("staging") == os.path.basename(staging)
                and isinstance(data.get("ids"), dict) and len(data["ids"]) <= _MAX_JOURNAL_IDS):
            return _DAMAGED_JOURNAL
        return {_journal_id(k): _journal_entry(v) for k, v in data["ids"].items()}
    except Exception:   # OSError, ValueError, RecursionError, OverflowError, ...
        return _DAMAGED_JOURNAL


def _drama_rows_exist(ids):
    """True when any of these drama rows exists, False when none does, None
    when the library database or its dramas table can't be read (then
    nothing can be proven either way)."""
    ids = list(ids)
    if not ids:
        return False
    if not os.path.isfile(DB_PATH):
        return None
    try:
        # A separate connection: callers hold a get_conn() connection
        # mid-transaction, so this sees only committed rows.
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" for _ in chunk)
                if conn.execute(f"SELECT 1 FROM dramas WHERE id IN ({marks}) LIMIT 1",
                                chunk).fetchone() is not None:
                    return True
    except Exception:
        return None
    return False


def _settle_media_staging(name: str, holder: list, moved: dict = None):
    """Resolves one staging folder whose lock `holder` has (released here
    before the folder is removed). Returns (ended, ids left in place): ended
    is False when the staging folder and its journal were kept -- a damaged
    journal, an unreadable library database, or dramas/<id> folders that
    could not be proven to be this import's or could not be removed (the
    next start tries again)."""
    staging = os.path.join(DRAMAS_DIR, name)
    journal = _read_media_journal(staging)
    if journal is _DAMAGED_JOURNAL:
        log.warning("An import journal could not be read; its folders were left alone")
        return False, []
    committed = _drama_rows_exist(journal or {})
    if committed is None:
        log.warning("The library database could not be read; an interrupted import's "
                    "folders were left alone")
        return False, []
    if committed:
        # Every folder is in place: only the markers, the journal and the
        # staging folder go (anything else still in it is left alone).
        for did, entry in journal.items():
            final = os.path.join(DRAMAS_DIR, str(did))
            if _real_dir(final) and _marker_matches(final, entry["marker"]):
                try:
                    os.remove(os.path.join(final, MEDIA_MARKER))
                except OSError:
                    log.warning("Could not remove the import marker of drama %d", did)
        os.remove(os.path.join(staging, _MEDIA_JOURNAL))
        _release_lock(holder)
        for leftover in (_MEDIA_JOURNAL + ".tmp", _MEDIA_LOCK_FILE):
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(staging, leftover))
        try:
            os.rmdir(staging)
        except OSError:
            log.warning("A finished import's staging folder was not empty; it was left alone")
        return True, []
    moved = moved or {}
    left = []
    for did, entry in sorted((journal or {}).items()):
        final = os.path.join(DRAMAS_DIR, str(did))
        if not os.path.lexists(final):
            continue
        if not _is_journalled_folder(final, entry):
            left.append(did)    # can't prove it's the folder this import moved there
            continue
        staged = moved.get(did)
        if staged is not None and not os.path.lexists(staged):
            try:
                os.rename(final, staged)    # straight back; removed with the staging folder
                continue
            except OSError:
                pass
        try:
            shutil.rmtree(final)
        except FileNotFoundError:
            pass
        except OSError:
            left.append(did)
    if left:
        log.warning("The folders of uncommitted imported dramas %s were left in place",
                    ",".join(str(i) for i in left))
        return False, left
    _release_lock(holder)
    shutil.rmtree(staging)
    return True, []


def finish_media_staging(staging: str) -> bool:
    """Ends this process's import staging: after a commit only the markers,
    the journal and the staging folder go; otherwise the folders it moved
    into place are renamed back and removed with the staging folder. False
    when something was left in place (logged by id; the journal stays for
    the next start). Raises OSError when the staging folder itself can't be
    removed. Calling it again is harmless."""
    name = os.path.basename(staging)
    with _media_lock:
        entry = _active_media_stagings.pop(name, None)
        if entry is None:
            return not os.path.lexists(staging)     # already ended
        try:
            if not os.path.isdir(staging):
                return True
            return _settle_media_staging(name, entry["lock"], entry["moved"])[0]
        finally:
            _release_lock(entry["lock"])


def _leftover_stagings() -> list:
    try:
        names = os.listdir(DRAMAS_DIR)
    except OSError:
        return []
    return sorted(n for n in names if _MEDIA_STAGING_RE.fullmatch(n)
                  and n not in _active_media_stagings
                  and _real_dir(os.path.join(DRAMAS_DIR, n)))


def _recover_staging(name: str, now: float, max_age: float):
    """(ended, ids left in place) for a staging folder no import in this
    process owns; (False, []) when its import may still be running in
    another process (its lock is held, or it has no lock or journal yet and
    is younger than max_age)."""
    staging = os.path.join(DRAMAS_DIR, name)
    holder = [_lock_file(os.path.join(staging, _MEDIA_LOCK_FILE), False)]
    try:
        if holder[0] is None:
            if os.path.lexists(os.path.join(staging, _MEDIA_LOCK_FILE)):
                return False, []    # held by a running import
        if holder[0] is None or _read_media_journal(staging) is None:
            if now - os.lstat(staging).st_mtime < max_age:
                return False, []
        return _settle_media_staging(name, holder)
    finally:
        _release_lock(holder)


def recover_media_imports(max_age: float = STALE_MEDIA_STAGING_SECONDS, now: float = None) -> dict:
    """Crash recovery for imports that are not running (run at startup and
    before an import or restore; a staging folder whose lock another process
    holds is skipped): a journal whose drama rows exist is deleted; one whose
    rows don't removes the dramas/<id> folders it provably recorded, then
    its staging folder; a staging folder with no journal (the import stopped
    before moving anything) is removed once older than max_age. Nothing a
    journal doesn't list is touched. Returns {"settled": n, "failed_ids":
    [ids left in place]}. Never raises."""
    now = time.time() if now is None else now
    settled, failed = 0, []
    with _media_lock:
        for name in _leftover_stagings():
            try:
                ended, left = _recover_staging(name, now, max_age)
            except Exception:
                log.warning("An interrupted import's leftovers could not be removed")
                continue
            failed += left
            settled += bool(ended)
    return {"settled": settled, "failed_ids": failed}


def claim_new_drama_folder(drama_id: int):
    """Call with a new drama's row inserted but not committed (so no other
    import is mid-transaction). A dramas/<id> folder is never inherited: one
    that an uncommitted import's journal provably recorded is removed with
    that import's other leftovers; anything else there is renamed aside to
    <id>.orphan-<hex> (kept, never deleted). Raises DramaFolderConflict when
    it can't be renamed aside."""
    final = os.path.join(DRAMAS_DIR, str(drama_id))
    if not os.path.lexists(final):
        return
    with _media_lock:
        for name in _leftover_stagings():
            journal = _read_media_journal(os.path.join(DRAMAS_DIR, name))
            if isinstance(journal, dict) and drama_id in journal:
                try:
                    _recover_staging(name, time.time(), STALE_MEDIA_STAGING_SECONDS)
                except Exception:
                    log.warning("An interrupted import's leftovers could not be removed")
                break
        if not os.path.lexists(final):
            return
        try:
            os.rename(final, f"{final}.orphan-{uuid.uuid4().hex[:8]}")
        except OSError:
            raise DramaFolderConflict(
                f"A folder for new drama {int(drama_id)} is already in the library's dramas "
                "folder and could not be moved aside; move it out and try again.") from None
        log.warning("A folder for new drama %d was already in the dramas folder; it was "
                    "renamed aside and kept", int(drama_id))


# ---------------------------------------------------------------------------
# Dramas CRUD
# ---------------------------------------------------------------------------

def create_drama(**fields) -> int:
    if fields.get("series_id") is not None:
        # Auth slice B1 (user decision 4): only a whole series, or a drama
        # with no series, can be private.
        fields["is_private"] = 0
    with contextlib.closing(get_conn()) as conn:
        fields.setdefault("status", "not started")
        now = datetime.datetime.utcnow().isoformat()
        fields["created_at"] = now
        fields["updated_at"] = now
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO dramas ({cols}) VALUES ({placeholders})",
                            list(fields.values()))
        new_id = cur.lastrowid
        claim_new_drama_folder(new_id)   # raising here rolls the insert back
        conn.commit()
    return new_id


def update_drama(drama_id: int, **fields):
    if not fields:
        return
    if fields.get("series_id") is not None:
        fields["is_private"] = 0    # auth slice B1, as in create_drama
    fields["updated_at"] = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(get_conn()) as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE dramas SET {set_clause} WHERE id = ?",
                     list(fields.values()) + [drama_id])
        conn.commit()


def set_drama_notion_page_id(drama_id: int, page_id):
    """Roadmap 112: records (or clears, with None) the Notion page a drama
    was exported to. Left out of update_drama on purpose: an export is not
    an edit, so updated_at stays as it was."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE dramas SET notion_page_id = ? WHERE id = ?", (page_id, drama_id))
        conn.commit()


def delete_drama(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM dramas WHERE id = ?", (drama_id,))
        # Step 41 tables keyed by drama (no foreign key): a checkpoint scope
        # is "<kind>:<drama_id>:<digest>".
        conn.execute("DELETE FROM line_provenance WHERE drama_id = ?", (drama_id,))
        conn.execute("DELETE FROM job_checkpoints WHERE scope LIKE ?", (f"%:{int(drama_id)}:%",))
        conn.commit()
    d = os.path.join(DRAMAS_DIR, str(drama_id))
    if os.path.isdir(d):
        shutil.rmtree(d)


# Every drama row also carries its series' instructions (Step 12e) as
# series_instructions, so any drama_meta handed to the translation prompt
# already has both levels -- Workspace, CLI and bulk translate all build
# drama_meta from get_drama()/list_dramas(), and nothing else has to thread
# the series lookup through. A subquery rather than a JOIN keeps every
# existing unqualified column name in list_dramas' filters unambiguous.
#
# Step 74: same trick for previous_episode_summary -- the immediately
# preceding episode's stored running summary (the sibling row in the same
# series whose episode_number is the largest one strictly less than this
# row's own). Only resolves when BOTH this row and a sibling have
# episode_number set -- a drama with no ordering has no reliable
# "previous" to feed forward, so it gets NULL/"" here rather than guessing.
_DRAMA_SELECT = ("SELECT dramas.*, (SELECT series.instructions FROM series "
                 "WHERE series.id = dramas.series_id) AS series_instructions, "
                 "(SELECT d2.episode_summary FROM dramas d2 "
                 "WHERE d2.series_id = dramas.series_id AND d2.episode_number IS NOT NULL "
                 "AND dramas.episode_number IS NOT NULL AND d2.episode_number < dramas.episode_number "
                 "ORDER BY d2.episode_number DESC LIMIT 1) AS previous_episode_summary "
                 "FROM dramas")


def get_drama(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(f"{_DRAMA_SELECT} WHERE id = ?", (drama_id,)).fetchone()
    return dict(row) if row else None


def drama_visible_sql(alias: str, visible_to: int):
    """(sql, params): a WHERE fragment true when the drama row `alias` is
    visible to user `visible_to` (auth slice B1/B2). Same rule as
    ownership_service._visible: the series owner sees every drama in their
    series; a private series hides its dramas from everyone else (drama
    ownership doesn't override it); otherwise the drama's owner, or anyone
    when it isn't private. `alias` is a trusted literal, never user input."""
    return ((f"(EXISTS (SELECT 1 FROM series s WHERE s.id = {alias}.series_id"
             " AND s.owner_user_id = ?)"
             f" OR (NOT EXISTS (SELECT 1 FROM series s WHERE s.id = {alias}.series_id"
             " AND COALESCE(s.is_private, 0) = 1)"
             f" AND ({alias}.owner_user_id = ? OR COALESCE({alias}.is_private, 0) = 0)))"),
            [visible_to, visible_to])


def list_dramas(search: str = "", studio: str = "", author: str = "",
                 voice_actor: str = "", status: str = "", source_language: str = "",
                 media_type: str = "", visible_to: int = None):
    """`visible_to` (auth slice B1): a non-admin user id; when set, only
    dramas that user may see are returned (any drama in a series they own;
    otherwise, outside a private series, their own or non-private ones).
    None = unfiltered. The rule itself lives in
    services/ownership_service.py; admins/local owner pass None."""
    with contextlib.closing(get_conn()) as conn:
        query = f"{_DRAMA_SELECT} WHERE 1=1"
        params = []
        if visible_to is not None:
            clause, extra = drama_visible_sql("dramas", visible_to)
            query += " AND " + clause
            params.extend(extra)
        if search:
            query += " AND (title_zh LIKE ? OR title_en LIKE ? OR summary LIKE ?)"
            like = f"%{search}%"
            params += [like, like, like]
        if studio:
            query += " AND studio = ?"
            params.append(studio)
        if author:
            query += " AND author = ?"
            params.append(author)
        if voice_actor:
            query += " AND voice_actors LIKE ?"
            params.append(f"%{voice_actor}%")
        if status:
            query += " AND status = ?"
            params.append(status)
        if source_language:
            query += " AND source_language = ?"
            params.append(source_language)
        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def list_dramas_by_series(series_id: int):
    """Every drama in a series, across every media_type -- Step 22's
    series-level view: a manga project and a video project already share
    one glossary/character list under the hood (series_characters and
    glossary_terms are keyed by series_id, not drama_id/media_type); this
    is the query that surfaces that sharing as one grouped list instead
    of unrelated Library rows. Uses _DRAMA_SELECT like every other
    drama-list function, so a row from here carries series_instructions
    too if it's ever used to build a drama_meta, same guarantee
    list_dramas()/get_drama() already give.

    Order: newest first (created_at DESC), same as list_dramas() -- UNLESS
    Step 74's episode_number is actually in use somewhere in this series,
    in which case it's the real ordering signal and takes over (ascending,
    reading order), with any not-yet-numbered sibling sorted after the
    numbered ones rather than crashing or getting silently interleaved.
    An existing series with no episode numbers set at all keeps its old
    created_at order exactly -- this never reorders a series that hasn't
    opted in."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            f"{_DRAMA_SELECT} WHERE series_id = ? ORDER BY created_at DESC", (series_id,)).fetchall()
    dramas = [dict(r) for r in rows]
    if any(d.get("episode_number") is not None for d in dramas):
        dramas.sort(key=lambda d: (d.get("episode_number") is None, d.get("episode_number")))
    return dramas


# ---------------------------------------------------------------------------
# Lines CRUD
# ---------------------------------------------------------------------------

from core import LINE_FIELDS as _LINE_COLUMNS  # noqa: E402 -- core has no db dependency


def line_value(ln, f):
    v = getattr(ln, f, None)
    if f in ("speaker_manual", "sfx"):
        return int(bool(v))
    return "" if (f == "flag_note" and v is None) else v


def update_line_fields_if(drama_id: int, line_id: int, values: dict, expected: dict) -> bool:
    """Compare-and-set for one line: ONE conditional UPDATE that writes
    `values` (column -> new value) only if every `expected` column still
    holds the value the caller saw (start/end within 1e-6, sfx as a bool,
    text/speaker/flag/flag_note with NULL equal to ""). Returns True if the row changed,
    False if it no longer matches (or no longer exists) -- nothing is
    written then. Never inserts or deletes."""
    sql, params = _line_cas_sql(drama_id, line_id, values, expected)
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_lines_fields_if_many(drama_id: int, items) -> list:
    """Batch compare-and-set: `items` is [(line_id, values, expected), ...],
    each the same conditional UPDATE as update_line_fields_if, all inside
    ONE `BEGIN IMMEDIATE` transaction with one commit. Returns the line ids
    whose expected values no longer matched (or that no longer exist);
    those are skipped, the rest are written. On any error the whole batch
    is rolled back and nothing is written."""
    stmts = [(lid, *_line_cas_sql(drama_id, lid, values, expected))
             for lid, values, expected in items]
    if not stmts:
        return []
    missed = []
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        for lid, sql, params in stmts:
            if conn.execute(sql, params).rowcount == 0:
                missed.append(lid)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return missed


def _line_cas_sql(drama_id: int, line_id: int, values: dict, expected: dict):
    """(sql, params) for one line's conditional UPDATE; validates columns."""
    sets, args = [], []
    for col, val in values.items():
        if col not in _LINE_COLUMNS:
            raise ValueError(f"Unknown line column: {col}")
        sets.append(f"{col} = ?")
        args.append(val)
    conds, cargs = ["id = ?", "drama_id = ?"], [line_id, drama_id]
    for col, val in (expected or {}).items():
        if col in ("start", "end"):
            conds.append(f"ABS({col} - ?) < 1e-6")
            cargs.append(float(val))
        elif col == "sfx":
            conds.append("COALESCE(sfx, 0) = ?")
            cargs.append(int(bool(val)))
        elif col in ("zh", "en", "speaker", "flag", "flag_note"):
            conds.append(f"COALESCE({col}, '') = ?")
            cargs.append(val or "")
        else:
            raise ValueError(f"Unknown expected column: {col}")
    return f"UPDATE lines SET {', '.join(sets)} WHERE {' AND '.join(conds)}", args + cargs


def save_lines(drama_id: int, lines, fields=None, only_if_unchanged=False, guard_fields=()):
    """Saves a drama's lines by their permanent id (Line.id).

    Full sync (fields=None) -- the list IS the drama's lines now:
      - a line whose id exists for this drama is updated in place;
      - a line with no id (or an id that isn't this drama's) is inserted,
        and its new id is written back onto the object;
      - an existing row whose id isn't in the list is deleted (its notes
        and emotions with it), except that a line's `merged_ids` are
        re-pointed onto that line first, so a note on a merged-away line
        stays with the line it was merged into.

    Field-scoped (fields=("en",) etc.) -- for background jobs: only those
    columns are updated, only on rows that still exist. Never inserts or
    deletes, so a job can't resurrect a line the user merged away or
    delete one the user just added.

    Either way, a line loaded through core.line_from_row carries `orig`,
    and a field whose value still equals `orig` isn't written -- it wasn't
    changed by this caller, so whatever is in the database now (another
    writer's newer value) is kept. After saving, `orig` is updated.

    only_if_unchanged (field-scoped only): a field is written only while
    the database still holds this caller's `orig` value, so an edit saved
    by someone else since the lines were loaded is kept, not overwritten.
    A line without `orig` is then not written at all.

    guard_fields (with only_if_unchanged): columns not written but whose
    database value must still equal this Line's own value -- the text the
    written fields were computed from (a flag from `en` and its timing).

    Returns the ids only_if_unchanged left unwritten (an edit was kept);
    empty otherwise.

    One transaction: on any error nothing is written."""
    if only_if_unchanged and fields is None:
        raise ValueError("only_if_unchanged needs field-scoped saving")
    if guard_fields and not only_if_unchanged:
        raise ValueError("guard_fields needs only_if_unchanged")
    guard_cols = tuple(f for f in _LINE_COLUMNS if f in guard_fields)
    cols = _LINE_COLUMNS if fields is None else tuple(f for f in _LINE_COLUMNS if f in fields)
    conn = get_conn()
    try:
        # IMMEDIATE (B-29): a deferred BEGIN reads then upgrades to a write,
        # and in WAL mode a commit from another connection in between fails
        # that upgrade at once with "database is locked" (no busy wait).
        conn.execute("BEGIN IMMEDIATE")
        existing = {r["id"] for r in conn.execute(
            "SELECT id FROM lines WHERE drama_id = ?", (drama_id,)).fetchall()}
        kept, unwritten = set(), set()
        for ln in lines:
            lid = getattr(ln, "id", None)
            orig = getattr(ln, "orig", None)
            if lid in existing and lid not in kept:
                changed = [f for f in cols
                           if orig is None or line_value(ln, f) != orig.get(f)]
                if changed and only_if_unchanged:
                    if orig is None:
                        unwritten.add(lid)
                    else:
                        # Compare-and-set: NULL and "" are the same empty text
                        # to a Line, so a text field compares through COALESCE.
                        expected = [(f, orig.get(f)) for f in changed]
                        # A guard with no value is empty text, as NULL is to a Line.
                        expected += [(f, "" if line_value(ln, f) is None else line_value(ln, f))
                                     for f in guard_cols]
                        guards = [f"COALESCE({f}, '') = ?" if isinstance(v, str)
                                  else f"{f} IS ?" for f, v in expected]
                        cur = conn.execute(
                            f"UPDATE lines SET {', '.join(f + ' = ?' for f in changed)} "
                            f"WHERE id = ? AND drama_id = ? AND {' AND '.join(guards)}",
                            [line_value(ln, f) for f in changed] + [lid, drama_id]
                            + [v for _f, v in expected])
                        if cur.rowcount == 0:
                            unwritten.add(lid)
                elif changed:
                    conn.execute(
                        f"UPDATE lines SET {', '.join(f + ' = ?' for f in changed)} "
                        f"WHERE id = ? AND drama_id = ?",
                        [line_value(ln, f) for f in changed] + [lid, drama_id])
                kept.add(lid)
            elif fields is None:
                cur = conn.execute(
                    f"INSERT INTO lines (drama_id, {', '.join(_LINE_COLUMNS)}) "
                    f"VALUES (?, {', '.join('?' for _ in _LINE_COLUMNS)})",
                    [drama_id] + [line_value(ln, f) for f in _LINE_COLUMNS])
                ln.id = cur.lastrowid
                kept.add(ln.id)
            else:
                # A field-scoped save never inserts: a job's stale copy of a
                # line the user deleted or merged away must not resurrect it.
                continue
        if fields is None:
            for ln in lines:
                for mid in getattr(ln, "merged_ids", None) or []:
                    if mid in existing and mid not in kept:
                        _repoint_line_refs(conn, drama_id, mid, ln.id)
            removed = existing - kept
            for lid in removed:
                _delete_line_refs(conn, lid)
            conn.executemany("DELETE FROM lines WHERE id = ?", [(lid,) for lid in removed])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    for ln in lines:
        if getattr(ln, "id", None) is None or not hasattr(ln, "orig"):
            continue
        # What this caller last wrote/saw is now the baseline, so its next
        # save only writes what changes after this point.
        ln.orig = {**(ln.orig or {}), **{f: line_value(ln, f) for f in cols}}
        if fields is None:
            ln.merged_ids = []
    return unwritten


def _repoint_line_refs(conn, drama_id, from_id, to_id):
    """Moves notes/emotions/reading history from a merged-away line onto
    the line it was merged into. Where the target already has an emotion
    (one per line) or a note on the same term, the target's own wins."""
    conn.execute("UPDATE OR IGNORE translation_notes SET line_id = ? WHERE line_id = ? AND drama_id = ?",
                 (to_id, from_id, drama_id))
    conn.execute("UPDATE OR IGNORE line_emotions SET line_id = ? WHERE line_id = ? AND drama_id = ?",
                 (to_id, from_id, drama_id))
    conn.execute("UPDATE reading_history SET line_id = ? WHERE line_id = ? AND drama_id = ?",
                 (to_id, from_id, drama_id))


def _delete_line_refs(conn, line_id):
    conn.execute("DELETE FROM translation_notes WHERE line_id = ?", (line_id,))
    conn.execute("DELETE FROM line_emotions WHERE line_id = ?", (line_id,))
    conn.execute("UPDATE reading_history SET line_id = NULL WHERE line_id = ?", (line_id,))


def load_lines(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, idx, start, end, zh, en, speaker, dub_filename, flag, flag_note, speaker_manual, "
            "sfx FROM lines WHERE drama_id = ? ORDER BY idx, id",
            (drama_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def load_line_objects(drama_id: int):
    """db.load_lines as core.Line objects (id and `orig` set) -- the shared
    loader every caller that edits and re-saves lines should use."""
    from core import lines_from_rows
    return lines_from_rows(load_lines(drama_id))


def load_line_ids(drama_id: int) -> set:
    """Just the current set of line ids for this drama -- a cheap
    staleness check for a caller about to commit a full line-list
    replacement it computed from a snapshot taken earlier (see
    resegment Apply's own safety check, Step 6f): if the database's
    real current id set doesn't match what the snapshot was built
    from, something else changed the drama's lines in between, and
    committing the snapshot anyway would silently orphan/duplicate rows."""
    with contextlib.closing(get_conn()) as conn:
        ids = {r["id"] for r in conn.execute(
            "SELECT id FROM lines WHERE drama_id = ?", (drama_id,)).fetchall()}
    return ids


def line_ids_exist(drama_id: int, line_ids) -> int:
    """How many of line_ids are still lines of this drama."""
    ids = [i for i in line_ids if i is not None]
    if not ids:
        return 0
    with contextlib.closing(get_conn()) as conn:
        n = conn.execute(
            f"SELECT COUNT(*) FROM lines WHERE drama_id = ? AND id IN ({', '.join('?' for _ in ids)})",
            [drama_id] + ids).fetchone()[0]
    return n


# ---------------------------------------------------------------------------
# Characters CRUD (speaker-label -> character name / voice actor / TTS voice)
# ---------------------------------------------------------------------------

def upsert_character(drama_id: int, speaker_label: str, character_name: str = None,
                      voice_actor: str = None, tts_voice: str = None,
                      ref_audio_filename: str = None, ref_text: str = None,
                      elevenlabs_voice_id: str = None, series_character_id: int = None,
                      pronouns: str = None, clone_engine: str = None, voice_design: str = None,
                      offline_voice: str = None):
    """pronouns/voice_design: None leaves an existing value untouched; ""
    clears it."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO characters (drama_id, speaker_label, character_name, voice_actor, tts_voice,
                                     ref_audio_filename, ref_text, elevenlabs_voice_id, series_character_id,
                                     pronouns, clone_engine, voice_design, offline_voice)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, speaker_label) DO UPDATE SET
                clone_engine = COALESCE(excluded.clone_engine, characters.clone_engine),
                voice_design = COALESCE(excluded.voice_design, characters.voice_design),
                pronouns = COALESCE(excluded.pronouns, characters.pronouns),
                character_name = COALESCE(excluded.character_name, characters.character_name),
                voice_actor = COALESCE(excluded.voice_actor, characters.voice_actor),
                tts_voice = COALESCE(excluded.tts_voice, characters.tts_voice),
                offline_voice = COALESCE(excluded.offline_voice, characters.offline_voice),
                ref_audio_filename = COALESCE(excluded.ref_audio_filename, characters.ref_audio_filename),
                ref_text = COALESCE(excluded.ref_text, characters.ref_text),
                elevenlabs_voice_id = COALESCE(excluded.elevenlabs_voice_id, characters.elevenlabs_voice_id),
                series_character_id = COALESCE(excluded.series_character_id, characters.series_character_id)
        """, (drama_id, speaker_label, character_name, voice_actor, tts_voice, ref_audio_filename, ref_text,
              elevenlabs_voice_id, series_character_id, pronouns, clone_engine, voice_design,
              offline_voice))
        conn.commit()


def list_characters(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM characters WHERE drama_id = ? ORDER BY speaker_label", (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def clear_character_series_link(drama_id: int, speaker_label: str):
    """Unlinks one speaker from its series character (upsert_character's
    COALESCE can't write NULL). The character keeps its own name."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE characters SET series_character_id = NULL "
                     "WHERE drama_id = ? AND speaker_label = ?", (drama_id, speaker_label))
        conn.commit()


def rename_speaker_atomic(drama_id: int, old_label: str, new_label: str, character_name,
                          line_updates: list):
    """Renames a speaker in ONE transaction: each of line_updates ({id,
    expect_speaker, expect_manual, speaker, manual}) is written only while the
    row still holds the expected speaker and manual flag, then the characters
    row (voice, pronouns, series link) and dismissed voice matches move from
    old_label to new_label with character_name set (None clears it; a speaker
    with no row gets one). Returns the number of lines written, or "changed"
    (a line no longer matched) / "name_taken" (new_label already has a row);
    in those cases, and on any error, nothing is written."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        written = 0
        for u in line_updates:
            cur = conn.execute(
                "UPDATE lines SET speaker = ?, speaker_manual = ? WHERE id = ? AND drama_id = ? "
                "AND COALESCE(speaker, '') = ? AND COALESCE(speaker_manual, 0) = ?",
                (u["speaker"], int(bool(u["manual"])), u["id"], drama_id,
                 u["expect_speaker"], int(bool(u["expect_manual"]))))
            if cur.rowcount != 1:
                conn.rollback()
                return "changed"
            written += 1
        # A line outside the update still on either label would end up merged
        # with the renamed speaker or stranded on the old label.
        ids = {u["id"] for u in line_updates}
        if any(r["id"] not in ids for r in conn.execute(
                "SELECT id FROM lines WHERE drama_id = ? AND COALESCE(speaker, '') IN (?, ?)",
                (drama_id, old_label, new_label)).fetchall()):
            conn.rollback()
            return "changed"
        if conn.execute("SELECT 1 FROM characters WHERE drama_id = ? AND speaker_label = ?",
                        (drama_id, new_label)).fetchone():
            conn.rollback()
            return "name_taken"
        conn.execute("INSERT OR IGNORE INTO characters (drama_id, speaker_label) VALUES (?, ?)",
                     (drama_id, old_label))
        conn.execute("UPDATE characters SET speaker_label = ?, character_name = ? "
                     "WHERE drama_id = ? AND speaker_label = ?",
                     (new_label, character_name, drama_id, old_label))
        conn.execute("UPDATE OR IGNORE voice_suggestion_dismissals SET speaker_label = ? "
                     "WHERE drama_id = ? AND speaker_label = ?", (new_label, drama_id, old_label))
        conn.commit()
        return written
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Series-level characters -- persist across every drama in a series (a
# streamer's whole archive, or a book series), independent of any one
# drama's own diarization labels. See the series_characters table comment
# in init_db() for why this has to be a separate concept from `characters`.
# ---------------------------------------------------------------------------

def upsert_series_character(series_id: int, character_name: str, aliases: str = "",
                             notes: str = "", gender: str = None):
    """Creates or updates a named character for a series. Matching is on
    (series_id, character_name) -- renaming isn't done through this
    function (it would create a new row); use rename_series_character.

    gender: the character's pronoun text ("she/her", "they/them", a custom
    value like "xe/xem"), or "" / None. Legacy rows may still hold
    "female"/"male" -- translation_guide.normalize_pronouns maps those.
    Named `gender` only because the column predates free-text pronouns.
    Feeds translation as a fixed
    pronoun hint for this character -- Mandarin's spoken 他/她/它 are
    homophones, so the character Whisper happens to transcribe for a
    pronoun isn't a reliable gender signal, and misgendering a NAMED
    character reads as a much more obvious error than an ambiguous
    unnamed one. None leaves an existing gender value untouched (so
    re-running "remember this character" from a different tab doesn't
    silently clear a gender set earlier); pass "" explicitly to clear it.
    """
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO series_characters (series_id, character_name, aliases, notes, gender, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, character_name) DO UPDATE SET
                aliases = excluded.aliases,
                notes = excluded.notes,
                gender = COALESCE(?, series_characters.gender)
        """, (series_id, character_name, aliases, notes, gender,
              datetime.datetime.utcnow().isoformat(), gender))
        conn.commit()


def rename_series_character(series_character_id: int, new_name: str):
    """Renaming updates the one series_characters row -- every drama's
    `characters` row linked to it via series_character_id picks up the
    new name automatically next time it's displayed (see
    list_characters_with_series_names), rather than needing a per-drama
    edit for a name correction that should apply everywhere."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE series_characters SET character_name = ? WHERE id = ?",
                     (new_name, series_character_id))
        conn.commit()


def insert_series_character(series_id: int, character_name: str, aliases: str = "",
                            notes: str = "", gender: str = "") -> int:
    """Plain INSERT (unlike upsert_series_character, which would overwrite
    an existing same-named row's aliases/notes). Raises
    sqlite3.IntegrityError when the series already has that name."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO series_characters (series_id, character_name, aliases, notes, gender, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (series_id, character_name, aliases, notes, gender,
             datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def update_series_character(series_character_id: int, *, character_name: str = None,
                            aliases: str = None, notes: str = None, gender: str = None):
    """Field-scoped update of one series character by id, in one statement:
    None leaves a column alone, "" clears it. Fixed column list (no
    caller-supplied keys reach the SQL). Raises sqlite3.IntegrityError
    (nothing written) when the new name is already taken in the series."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "UPDATE series_characters SET character_name = COALESCE(?, character_name), "
            "aliases = COALESCE(?, aliases), notes = COALESCE(?, notes), "
            "gender = COALESCE(?, gender) WHERE id = ?",
            (character_name, aliases, notes, gender, series_character_id))
        conn.commit()


def list_series_characters(series_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM series_characters WHERE series_id = ? ORDER BY character_name",
            (series_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_series_character(series_character_id: int):
    """Only removes the series-level record. Any drama's `characters` row
    still pointing at it keeps its own character_name (already copied in
    at assignment time) -- it just stops being linked for future rename
    propagation, rather than losing the name it already had."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE characters SET series_character_id = NULL WHERE series_character_id = ?",
                     (series_character_id,))
        conn.execute("DELETE FROM series_characters WHERE id = ?", (series_character_id,))
        conn.commit()


def _blend_voice_fingerprint(conn, series_character_id: int, new_embedding: list):
    row = conn.execute(
        "SELECT voice_fingerprint, voice_fingerprint_samples FROM series_characters WHERE id = ?",
        (series_character_id,)).fetchone()
    if row is None:
        return
    existing = json.loads(row["voice_fingerprint"]) if row["voice_fingerprint"] else None
    n = row["voice_fingerprint_samples"] or 0
    if existing and len(existing) == len(new_embedding):
        blended = [(e * n + v) / (n + 1) for e, v in zip(existing, new_embedding)]
    else:
        # No prior fingerprint, or a dimension mismatch (a different
        # embedding model produced it) -- start over from this sample
        # rather than averaging incompatible vectors.
        blended, n = list(new_embedding), 0
    conn.execute(
        "UPDATE series_characters SET voice_fingerprint = ?, voice_fingerprint_samples = ? WHERE id = ?",
        (json.dumps(blended), n + 1, series_character_id))


def update_series_character_voice_fingerprint(series_character_id: int, new_embedding: list):
    """Step 8: blends a newly-confirmed voice embedding into this
    character's running-average fingerprint (simple incremental mean,
    weighted by how many samples went into the average so far), so later
    dramas compare against an average across every drama where this
    character's voice was confirmed, not just the first one. Only ever
    called from an explicit user action -- accepting a voice suggestion,
    or "Remember as a known series character" for a speaker not yet
    linked (services/characters_service.py) -- never automatically."""
    with contextlib.closing(get_conn()) as conn:
        _blend_voice_fingerprint(conn, series_character_id, new_embedding)
        conn.commit()


def accept_voice_link(drama_id: int, speaker_label: str, series_character_id: int,
                      character_name: str, embedding: list = None) -> bool:
    """An accepted voice suggestion, in one transaction: names the speaker
    and links it to the series character ONLY while the speaker is still
    unnamed and unlinked, then blends `embedding` into that character's
    fingerprint. False (nothing written) when the speaker was named or
    linked meanwhile -- so two concurrent accepts can't both link it or
    blend the same embedding twice."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO characters (drama_id, speaker_label) VALUES (?, ?)",
                     (drama_id, speaker_label))
        cur = conn.execute(
            "UPDATE characters SET character_name = ?, series_character_id = ? "
            "WHERE drama_id = ? AND speaker_label = ? AND series_character_id IS NULL "
            "AND COALESCE(TRIM(character_name), '') = ''",
            (character_name, series_character_id, drama_id, speaker_label))
        if cur.rowcount == 0:
            conn.rollback()
            return False
        if embedding:
            _blend_voice_fingerprint(conn, series_character_id, embedding)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remember_speaker_as_series_character(drama_id: int, speaker_label: str, series_id: int,
                                         name: str, gender: str = None,
                                         embedding: list = None):
    """"Remember as a known series character", in one transaction: only
    while the speaker is still unlinked and its saved name (trimmed) is
    still `name`, adds `name` to the series if it isn't there (insert-only:
    an existing character's aliases, notes and pronouns are never
    touched), links the speaker to it and blends `embedding` into its
    fingerprint. Returns (series_character_id, created), or None (nothing
    written) when the speaker was linked or renamed meanwhile."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT 1 FROM characters WHERE drama_id = ? AND speaker_label = ? "
            "AND series_character_id IS NULL AND TRIM(character_name) = ?",
            (drama_id, speaker_label, name)).fetchone()
        if row is None:
            conn.rollback()
            return None
        created = conn.execute("""
            INSERT INTO series_characters (series_id, character_name, aliases, notes, gender, created_at)
            VALUES (?, ?, '', '', ?, ?)
            ON CONFLICT(series_id, character_name) DO NOTHING
        """, (series_id, name, gender, datetime.datetime.utcnow().isoformat())).rowcount == 1
        sc_id = conn.execute(
            "SELECT id FROM series_characters WHERE series_id = ? AND character_name = ?",
            (series_id, name)).fetchone()["id"]
        conn.execute("UPDATE characters SET series_character_id = ? "
                     "WHERE drama_id = ? AND speaker_label = ? AND series_character_id IS NULL",
                     (sc_id, drama_id, speaker_label))
        if embedding:
            _blend_voice_fingerprint(conn, sc_id, embedding)
        conn.commit()
        return sc_id, created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dismiss_voice_suggestion(drama_id: int, speaker_label: str, series_character_id: int):
    """Records that this exact (drama, speaker, candidate) voice-match
    suggestion was rejected, so it's never shown again for that
    combination. A different candidate for the same speaker isn't
    affected."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO voice_suggestion_dismissals
                (drama_id, speaker_label, series_character_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (drama_id, speaker_label, series_character_id, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_dismissed_voice_suggestions(drama_id: int) -> set:
    """{(speaker_label, series_character_id), ...} already rejected for
    this drama -- checked before generating suggestions so a dismissed
    one doesn't silently reappear on the next render."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT speaker_label, series_character_id FROM voice_suggestion_dismissals WHERE drama_id = ?",
            (drama_id,)).fetchall()
    return {(r["speaker_label"], r["series_character_id"]) for r in rows}


def list_characters_with_series_names(drama_id: int):
    """Like list_characters, but a character linked to a series_characters
    row shows that row's current character_name (so a series-level rename
    reflects here immediately) instead of the possibly-stale name copied
    into `characters` at the time it was first assigned."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("""
            SELECT c.*, sc.character_name AS series_character_name,
                   sc.gender AS series_pronouns
            FROM characters c
            LEFT JOIN series_characters sc ON sc.id = c.series_character_id
            WHERE c.drama_id = ?
            ORDER BY c.speaker_label
        """, (drama_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("series_character_name"):
            d["character_name"] = d["series_character_name"]
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Pages & bubbles CRUD (scanlation/typesetting)
# ---------------------------------------------------------------------------

def create_page(drama_id: int, idx: int, filename: str, width: int, height: int) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO pages (drama_id, idx, filename, width, height) VALUES (?, ?, ?, ?, ?)",
            (drama_id, idx, filename, width, height))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


_PAGE_UPDATE_FIELDS = ("idx", "filename", "rendered_filename", "width", "height",
                       "context_summary", "run_notes")


def update_page(page_id: int, **fields):
    """Field-scoped page update. Keys are interpolated into the SQL, so only
    _PAGE_UPDATE_FIELDS are accepted (ValueError otherwise); rev is bumped
    only by the region writes below, never set directly."""
    if not fields:
        return
    bad = [k for k in fields if k not in _PAGE_UPDATE_FIELDS]
    if bad:
        raise ValueError(f"update_page: unknown field(s) {bad}")
    with contextlib.closing(get_conn()) as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE pages SET {set_clause} WHERE id = ?", list(fields.values()) + [page_id])
        conn.commit()


def list_pages(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM pages WHERE drama_id = ? ORDER BY idx", (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def get_page(page_id: int, drama_id: int = None):
    """One page row, or None. With drama_id, None unless the page belongs to
    that drama (ids from a request must be scoped to their drama)."""
    with contextlib.closing(get_conn()) as conn:
        if drama_id is None:
            row = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM pages WHERE id = ? AND drama_id = ?",
                               (page_id, drama_id)).fetchone()
    return dict(row) if row else None


def next_page_idx(drama_id: int) -> int:
    """MAX(idx)+1 for a drama's pages (0 for none). Unlike len(list_pages),
    never reuses an idx after a gap. Callers adding pages hold their own
    per-drama lock around this and the inserts."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT MAX(idx) FROM pages WHERE drama_id = ?", (drama_id,)).fetchone()
    return 0 if row is None or row[0] is None else int(row[0]) + 1


# Scanlate S0: id-preserving region writes. Unlike save_bubbles (a full
# delete-and-reinsert that gives every bubble a new id, still used by the
# extension bridge in page_server.py), each of
# these keeps every other bubble's id and bumps pages.rev once.
BUBBLE_EDIT_FIELDS = ("x", "y", "w", "h", "source_text", "translated_text", "font_size",
                      "skip", "font_category", "kind", "kind_confidence", "confidence",
                      "language", "orientation", "panel_id", "include_sfx")
_BUBBLE_TEXT_FIELDS = ("source_text", "translated_text", "font_category", "kind",
                       "language", "orientation")
_BUBBLE_BOOL_FIELDS = ("skip", "include_sfx")


def _bubble_row_values(b: dict) -> tuple:
    return (b["x"], b["y"], b["w"], b["h"], b.get("source_text", "") or "",
            b.get("translated_text", "") or "", b.get("font_size", 18),
            int(bool(b.get("skip", False))), b.get("font_category") or "regular",
            b.get("kind") or "bubble", b.get("kind_confidence"), b.get("confidence"),
            b.get("language"), b.get("orientation"), b.get("panel_id"),
            int(bool(b.get("include_sfx"))))


_BUBBLE_INSERT_SQL = (
    "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, "
    "font_size, skip, font_category, kind, kind_confidence, confidence, language, "
    "orientation, panel_id, include_sfx) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def _bump_page_rev(conn, page_id: int):
    conn.execute("UPDATE pages SET rev = COALESCE(rev, 0) + 1 WHERE id = ?", (page_id,))


def _bubble_value(field, value):
    if field in _BUBBLE_BOOL_FIELDS:
        return int(bool(value))
    return value


def update_bubble_fields(bubble_id: int, fields: dict, expected: dict = None,
                         page_id: int = None) -> bool:
    """Compare-and-set for one bubble: ONE conditional UPDATE that writes
    `fields` only if every `expected` column still holds the value the
    caller saw (text columns: NULL equals ""; skip/include_sfx as bools).
    With page_id, the bubble must also be on that page. Returns True if the
    row changed (and bumps its page's rev), False if it no longer matches
    or no longer exists -- nothing is written then. Unknown columns raise
    ValueError (they are interpolated into the SQL)."""
    expected = expected or {}
    bad = [k for k in list(fields) + list(expected) if k not in BUBBLE_EDIT_FIELDS]
    if bad or not fields:
        raise ValueError(f"update_bubble_fields: bad field(s) {bad or 'none given'}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = [_bubble_value(k, v) for k, v in fields.items()]
    where = ["id = ?"]
    params.append(bubble_id)
    if page_id is not None:
        where.append("page_id = ?")
        params.append(page_id)
    for k, v in expected.items():
        if k in _BUBBLE_TEXT_FIELDS:
            where.append(f"COALESCE({k}, '') = ?")
            params.append("" if v is None else v)
        elif v is None:
            where.append(f"{k} IS NULL")
        else:
            where.append(f"{k} = ?")
            params.append(_bubble_value(k, v))
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(f"UPDATE bubbles SET {sets} WHERE {' AND '.join(where)}", params)
        changed = cur.rowcount > 0
        if changed:
            pid = conn.execute("SELECT page_id FROM bubbles WHERE id = ?", (bubble_id,)).fetchone()[0]
            _bump_page_rev(conn, pid)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_bubble(page_id: int, bubble: dict, position: int = None) -> int:
    """Adds one bubble to a page without touching the others' ids. position
    None appends in reading order; otherwise it is inserted at that list
    position (clamped) and later bubbles' idx shift by one. Returns the new id."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ? ORDER BY idx, id", (page_id,)).fetchall()]
        pos = len(ids) if position is None else max(0, min(int(position), len(ids)))
        cur = conn.execute(_BUBBLE_INSERT_SQL, (page_id, pos) + _bubble_row_values(bubble))
        new_id = cur.lastrowid
        ids.insert(pos, new_id)
        conn.executemany("UPDATE bubbles SET idx = ? WHERE id = ?",
                         [(i, bid) for i, bid in enumerate(ids)])
        _bump_page_rev(conn, page_id)
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_bubble(page_id: int, bubble_id: int) -> bool:
    """Deletes one bubble of that page; the others keep their ids (idx is
    renumbered to stay contiguous). False if it isn't on that page."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM bubbles WHERE id = ? AND page_id = ?", (bubble_id, page_id))
        if cur.rowcount == 0:
            conn.rollback()
            return False
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ? ORDER BY idx, id", (page_id,)).fetchall()]
        conn.executemany("UPDATE bubbles SET idx = ? WHERE id = ?",
                         [(i, bid) for i, bid in enumerate(ids)])
        _bump_page_rev(conn, page_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reorder_bubbles(page_id: int, ordered_ids) -> bool:
    """Sets reading order to `ordered_ids`, which must be exactly the page's
    current bubble ids (no more, no fewer, no repeats); False, and nothing
    written, otherwise."""
    ordered_ids = [int(i) for i in ordered_ids]
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        current = {r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ?", (page_id,)).fetchall()}
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != current:
            conn.rollback()
            return False
        conn.executemany("UPDATE bubbles SET idx = ? WHERE id = ?",
                         [(i, bid) for i, bid in enumerate(ordered_ids)])
        _bump_page_rev(conn, page_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_bubbles_if_unchanged(page_id: int, expected_ids, bubbles, expected_rev: int = None):
    """Conditional full replace for a background job: in ONE transaction,
    replaces the page's bubbles with `bubbles` (reading order = list order)
    only if the page's current bubble-id set is exactly `expected_ids` and,
    when given, its rev is still `expected_rev` (an id-preserving edit bumps
    rev; a legacy save_bubbles changes the ids) -- so a page the user (or
    another writer) changed since the job read it is left alone. Returns
    the new ids in order, or None if nothing was written."""
    expected = {int(i) for i in expected_ids}
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT COALESCE(rev, 0) FROM pages WHERE id = ?", (page_id,)).fetchone()
        if row is None or (expected_rev is not None and int(row[0]) != int(expected_rev)):
            conn.rollback()
            return None
        current = {r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ?", (page_id,)).fetchall()}
        if current != expected:
            conn.rollback()
            return None
        conn.execute("DELETE FROM bubbles WHERE page_id = ?", (page_id,))
        new_ids = [conn.execute(_BUBBLE_INSERT_SQL, (page_id, i) + _bubble_row_values(b)).lastrowid
                   for i, b in enumerate(bubbles)]
        _bump_page_rev(conn, page_id)
        conn.commit()
        return new_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_bubbles(page_id: int, bubbles):
    """bubbles: list of dicts with x,y,w,h,source_text,translated_text,font_size,skip,
    font_category (one of scanlate.FONT_CATEGORIES -- "regular" if unset), plus the
    Step 12d region fields kind ("bubble" if unset), kind_confidence, confidence,
    language, orientation, panel_id, include_sfx. List order is reading order (idx).
    Replaces all bubbles for this page -- so a caller rebuilding the list must carry
    every one of these fields through, or they're wiped."""
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM bubbles WHERE page_id = ?", (page_id,))
        conn.executemany(
            "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, "
            "font_size, skip, font_category, kind, kind_confidence, confidence, language, "
            "orientation, panel_id, include_sfx) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(page_id, i, b["x"], b["y"], b["w"], b["h"], b.get("source_text", ""),
              b.get("translated_text", ""), b.get("font_size", 18), int(b.get("skip", False)),
              b.get("font_category") or "regular", b.get("kind") or "bubble",
              b.get("kind_confidence"), b.get("confidence"), b.get("language"),
              b.get("orientation"), b.get("panel_id"), int(bool(b.get("include_sfx"))))
             for i, b in enumerate(bubbles)]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_bubbles(page_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM bubbles WHERE page_id = ? ORDER BY idx", (page_id,)).fetchall()
    return [dict(r) for r in rows]


def list_bubbles_for_drama(drama_id: int):
    """Every bubble across every saved page of a drama, each carrying its
    page's idx as page_idx -- used by Scanlate's bulk find-and-replace
    (Step 11 item 9), which needs to preview/apply across a whole
    drama's saved pages at once, not just whichever page is currently
    open in the tab."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT bubbles.*, pages.idx AS page_idx FROM bubbles "
            "JOIN pages ON pages.id = bubbles.page_id "
            "WHERE pages.drama_id = ? ORDER BY pages.idx, bubbles.idx", (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def update_bubble_text(bubble_id: int, translated_text: str):
    """Updates just one bubble's translated_text, nothing else -- unlike
    save_bubbles() (which deletes and re-inserts every bubble on a
    page), this is the safe, minimal-field write bulk find-and-replace
    (Step 11 item 9) needs: touching only the field the operation is
    actually about, so x/y/w/h/source_text/skip/font_category on every
    other bubble -- and every OTHER bubble on the same page -- are never
    at risk of being silently clobbered."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE bubbles SET translated_text = ? WHERE id = ?",
                     (translated_text, bubble_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Known titles (searchable discovery library, separate from your working
# drama catalog -- import a known_title into `dramas` when you're ready
# to actually work on it)
# ---------------------------------------------------------------------------

def create_known_title(**fields) -> int:
    with contextlib.closing(get_conn()) as conn:
        fields.setdefault("source_name", "manual")
        fields["created_at"] = datetime.datetime.utcnow().isoformat()
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO known_titles ({cols}) VALUES ({placeholders})", list(fields.values()))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def list_known_titles(search: str = "", language: str = "", media_type: str = "", source_name: str = ""):
    with contextlib.closing(get_conn()) as conn:
        query = "SELECT * FROM known_titles WHERE 1=1"
        params = []
        if search:
            query += " AND (title_original LIKE ? OR title_en LIKE ? OR tags LIKE ? OR author LIKE ?)"
            like = f"%{search}%"
            params += [like, like, like, like]
        if language:
            query += " AND language = ?"
            params.append(language)
        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)
        if source_name:
            query += " AND source_name = ?"
            params.append(source_name)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def delete_known_title(title_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM known_titles WHERE id = ?", (title_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Series & shared glossaries (terms consistent across multiple dramas of
# the same series, e.g. book 1/2/3 of the same title by the same author)
# ---------------------------------------------------------------------------

def get_or_create_series(name: str, owner_user_id: int = None, is_private: bool = False) -> int:
    """`owner_user_id`/`is_private` (auth slice B1) only stamp a newly
    created row; an existing series is returned unchanged. Not
    visibility-aware: non-admin callers use
    ownership_service.get_or_create_series_for, which refuses a name taken
    by a series they can't see."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
        if row:
            conn.close()
            return row["id"]
        cur = conn.execute("INSERT INTO series (name, created_at, owner_user_id, is_private) "
                           "VALUES (?, ?, ?, ?)",
                            (name, datetime.datetime.utcnow().isoformat(), owner_user_id,
                             int(bool(is_private))))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def create_series(name: str, owner_user_id: int = None, is_private: bool = False) -> int:
    """Auth slice B1: insert-only. Raises sqlite3.IntegrityError when the
    name is taken (series.name is UNIQUE) -- never returns an existing id."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute("INSERT INTO series (name, created_at, owner_user_id, is_private) "
                           "VALUES (?, ?, ?, ?)",
                           (name, datetime.datetime.utcnow().isoformat(), owner_user_id,
                            int(bool(is_private))))
        conn.commit()
        return cur.lastrowid


def get_series_id_by_name(name: str):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def list_series(visible_to: int = None):
    """`visible_to` (auth slice B1): a non-admin user id; when set, only
    that user's own or non-private series. None = unfiltered."""
    with contextlib.closing(get_conn()) as conn:
        if visible_to is None:
            rows = conn.execute("SELECT * FROM series ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM series WHERE owner_user_id = ? "
                                "OR COALESCE(is_private, 0) = 0 ORDER BY name",
                                (visible_to,)).fetchall()
    return [dict(r) for r in rows]


def update_series_instructions(series_id: int, instructions: str):
    """Step 12e: the series-level project instructions every drama in the
    series inherits (see _DRAMA_SELECT's series_instructions)."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE series SET instructions = ? WHERE id = ?", (instructions, series_id))
        conn.commit()


def upsert_glossary_term(series_id: int, term_original: str, term_translation: str, notes: str = "",
                          category: str = None, policy: str = None, enforce_exact: bool = False,
                          aliases: str = None, banned_translations: str = None):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO glossary_terms (series_id, term_original, term_translation, notes,
                                         category, policy, enforce_exact, aliases, banned_translations)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, term_original) DO UPDATE SET
                term_translation = excluded.term_translation,
                notes = excluded.notes,
                category = COALESCE(excluded.category, glossary_terms.category),
                policy = COALESCE(excluded.policy, glossary_terms.policy),
                enforce_exact = excluded.enforce_exact,
                aliases = COALESCE(excluded.aliases, glossary_terms.aliases),
                banned_translations = COALESCE(excluded.banned_translations,
                                                glossary_terms.banned_translations)
        """, (series_id, term_original, term_translation, notes, category, policy, int(enforce_exact),
              aliases, banned_translations))
        conn.commit()



def insert_glossary_term_if_absent(series_id: int, term_original: str, term_translation: str,
                                   notes: str = "", category: str = None, policy: str = None,
                                   enforce_exact: bool = False) -> bool:
    """Adds a term only if the series has none with this original text (a
    term added meanwhile is left alone). Returns True when it was added."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO glossary_terms (series_id, term_original, term_translation, notes,
                                         category, policy, enforce_exact)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, term_original) DO NOTHING
        """, (series_id, term_original, term_translation, notes, category, policy,
              int(enforce_exact)))
        conn.commit()
        return cur.rowcount == 1

# ---------------------------------------------------------------------------
# Translation notes (idioms, wordplay, meaningful names, allusions)
# ---------------------------------------------------------------------------

def save_translation_notes(drama_id: int, notes, id_by_idx: dict = None):
    """notes: list of {line_idx, term, note_type, note} (or with line_id).
    Stored against the line's permanent id. id_by_idx: {line_idx: line_id}
    as of when the notes were generated -- a background job passes its
    own copy's mapping, since the user may have merged lines since. Without
    it, line_idx is resolved against the drama's lines as they are now.
    Existing notes for the same (drama, line, term) are updated rather
    than duplicated."""
    with contextlib.closing(get_conn()) as conn:
        now = datetime.datetime.utcnow().isoformat()
        for n in notes:
            line_idx = n.get("line_idx")
            line_id = n.get("line_id")
            if line_id is None:
                line_id = (id_by_idx.get(line_idx) if id_by_idx is not None
                           else _line_id_for_idx(conn, drama_id, line_idx))
            if line_id is None:
                # The line this note was about no longer exists (same case
                # save_emotions skips). Inserting it with line_id = NULL
                # would accumulate orphaned duplicates, since SQLite treats
                # every NULL as distinct for the (drama_id, line_id, term)
                # uniqueness this ON CONFLICT relies on, so it would never
                # match an earlier NULL row to update instead of insert.
                continue
            conn.execute("""
                INSERT INTO translation_notes (drama_id, line_id, line_idx, term, note_type, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drama_id, line_id, term) DO UPDATE SET
                    note_type = excluded.note_type,
                    note = excluded.note
            """, (drama_id, line_id, line_idx, n.get("term", ""), n.get("note_type", "cultural"),
                  n.get("note", ""), now))
        conn.commit()


def list_translation_notes(drama_id: int):
    """line_idx is the note's line's CURRENT position (it follows the line
    through merges/splits); the stored position is only a fallback for a
    note whose line couldn't be matched when it was migrated."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("""
            SELECT n.id, n.drama_id, n.line_id, COALESCE(l.idx, n.line_idx) AS line_idx,
                   n.term, n.note_type, n.note, n.created_at
            FROM translation_notes n LEFT JOIN lines l ON l.id = n.line_id
            WHERE n.drama_id = ? ORDER BY 4, n.id
        """, (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_translation_note(note_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM translation_notes WHERE id = ?", (note_id,))
        conn.commit()


def save_consistency_issues(drama_id: int, issues):
    """Replaces the drama's consistency-check results wholesale -- each
    run is a fresh full check of the current translation, not something
    to accumulate across runs the way translation notes do. Persisted so
    an LLM call that already cost real money survives a page refresh
    instead of only living in session state."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM consistency_issues WHERE drama_id = ?", (drama_id,))
        now = datetime.datetime.utcnow().isoformat()
        for issue in issues:
            conn.execute("""
                INSERT INTO consistency_issues (drama_id, term, variants, note, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (drama_id, issue.get("term", ""),
                  json.dumps(issue.get("variants", []), ensure_ascii=False),
                  issue.get("note", ""), now))
        conn.commit()


def load_consistency_issues(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM consistency_issues WHERE drama_id = ?", (drama_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["variants"] = json.loads(d["variants"]) if d["variants"] else []
        except (json.JSONDecodeError, TypeError):
            d["variants"] = []
        out.append(d)
    return out


def save_emotions(drama_id: int, emotion_map: dict, id_by_idx: dict = None):
    """emotion_map: {line_idx: {"emotion", "intensity", "note"}}, the shape
    emotion.detect_emotions() returns. Persisted so a whole-drama emotion
    detection run (a real LLM batch job, same cost scale as translation)
    survives a page refresh and an app restart, rather than needing a
    re-run (and re-paying for) the whole thing.

    Stored against each line's permanent id; id_by_idx works as in
    save_translation_notes."""
    with contextlib.closing(get_conn()) as conn:
        now = datetime.datetime.utcnow().isoformat()
        for line_idx, tag in emotion_map.items():
            line_idx = int(line_idx)
            line_id = (id_by_idx.get(line_idx) if id_by_idx is not None
                       else _line_id_for_idx(conn, drama_id, line_idx))
            if line_id is None:
                continue  # the line it was about no longer exists
            conn.execute("""
                INSERT INTO line_emotions (drama_id, line_id, line_idx, emotion, intensity, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drama_id, line_id) DO UPDATE SET
                    emotion = excluded.emotion,
                    intensity = excluded.intensity,
                    note = excluded.note
            """, (drama_id, line_id, line_idx, tag.get("emotion", "neutral"),
                  tag.get("intensity", 0.5), tag.get("note", ""), now))
        conn.commit()


def load_emotions(drama_id: int) -> dict:
    """Returns the same {line_idx: {"emotion", "intensity", "note"}} shape
    save_emotions() takes, keyed by each line's CURRENT position, so it
    drops straight back into emotion.emotion_summary()/
    build_emotion_guidance() unchanged."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("""
            SELECT COALESCE(l.idx, e.line_idx) AS line_idx, e.emotion, e.intensity, e.note
            FROM line_emotions e LEFT JOIN lines l ON l.id = e.line_id
            WHERE e.drama_id = ?
        """, (drama_id,)).fetchall()
    return {r["line_idx"]: {"emotion": r["emotion"], "intensity": r["intensity"],
                             "note": r["note"] or ""} for r in rows if r["line_idx"] is not None}


# ---------------------------------------------------------------------------
# Progress tracking & reading history
# ---------------------------------------------------------------------------

def _default_profile_id(conn) -> int:
    """The profile progress/history/notes falls back to when no profile_id
    is given -- the first profile ever created (lowest id), which is the
    one _migrate_step26e_profiles creates on every install (a fresh one or
    an upgrade alike). Lets every caller/test that predates profiles keep
    working completely unchanged, and is what a single-profile household
    transparently keeps using forever if it never adds a second one."""
    row = conn.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def create_profile(name: str, color: str = None) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute("INSERT INTO profiles (name, color, created_at) VALUES (?, ?, ?)",
                           (name, color, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def list_profiles():
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get_profile(profile_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return dict(row) if row else None


def rename_profile(profile_id: int, new_name: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE profiles SET name = ? WHERE id = ?", (new_name, profile_id))
        conn.commit()


def delete_profile(profile_id: int):
    """Refuses to remove the last remaining profile -- the app always
    needs at least one to attach progress/history/notes to (and for
    _migrate_step26e_profiles's own "profiles is empty" check to stay a
    true one-time marker). progress and personal_notes cascade via their
    own FOREIGN KEY ... ON DELETE CASCADE; reading_history predates
    profiles and only gained the column via ALTER TABLE (which can't add
    a FK), so its rows for this profile are cleaned up explicitly here
    instead of being left orphaned."""
    with contextlib.closing(get_conn()) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
        if count <= 1:
            raise ValueError("Can't delete the last remaining profile.")
        conn.execute("DELETE FROM reading_history WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()


def get_personal_notes(drama_id: int, profile_id: int = None) -> str:
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        row = conn.execute("SELECT notes FROM personal_notes WHERE profile_id = ? AND drama_id = ?",
                           (profile_id, drama_id)).fetchone()
    return (row["notes"] or "") if row else ""


def save_personal_notes(drama_id: int, notes: str, profile_id: int = None):
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        conn.execute("""
            INSERT INTO personal_notes (profile_id, drama_id, notes, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id, drama_id) DO UPDATE SET
                notes = excluded.notes, updated_at = excluded.updated_at
        """, (profile_id, drama_id, notes, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def save_progress(drama_id: int, profile_id: int = None, last_line_idx: int = None,
                   audio_position_seconds: float = None, last_page: int = None,
                   percent_complete: float = None, record_history: bool = True):
    """Upserts the resume point for a drama, for one profile. Only the
    fields you pass are updated, so saving an audio position doesn't
    clobber the reading page. profile_id defaults to the household's
    first/only profile (_default_profile_id) -- every pre-profiles caller
    keeps working unchanged against that one profile."""
    now = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        # Raw values (possibly NULL) go in deliberately: coalescing them here would
        # make excluded.<col> a real 0 and defeat the COALESCE in the conflict clause,
        # so a partial update would silently zero out the fields it didn't touch.
        conn.execute("""
            INSERT INTO progress (drama_id, profile_id, last_line_idx, audio_position_seconds,
                                   last_page, percent_complete, last_accessed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, profile_id) DO UPDATE SET
                last_line_idx = COALESCE(excluded.last_line_idx, progress.last_line_idx),
                audio_position_seconds = COALESCE(excluded.audio_position_seconds, progress.audio_position_seconds),
                last_page = COALESCE(excluded.last_page, progress.last_page),
                percent_complete = COALESCE(excluded.percent_complete, progress.percent_complete),
                last_accessed_at = excluded.last_accessed_at
        """, (drama_id, profile_id, last_line_idx, audio_position_seconds, last_page,
             percent_complete, now))
        if record_history and (last_line_idx is not None or percent_complete is not None):
            conn.execute(
                "INSERT INTO reading_history (drama_id, profile_id, line_id, line_idx, "
                "percent_complete, accessed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (drama_id, profile_id, _line_id_for_idx(conn, drama_id, last_line_idx), last_line_idx,
                 percent_complete, now))
        conn.commit()


def get_progress(drama_id: int, profile_id: int = None):
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        row = conn.execute("SELECT * FROM progress WHERE drama_id = ? AND profile_id = ?",
                           (drama_id, profile_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    # Normalize NULLs (from partial first writes) to sensible defaults here,
    # rather than at write time where it would break partial updates.
    d["last_line_idx"] = d["last_line_idx"] or 0
    d["audio_position_seconds"] = d["audio_position_seconds"] or 0.0
    d["last_page"] = d["last_page"] or 1
    d["percent_complete"] = d["percent_complete"] or 0.0
    return d


def list_continue_reading(limit: int = 8, profile_id: int = None, visible_to: int = None):
    """Dramas with partial progress, most recently touched first -- the
    'Continue' shelf. Excludes anything finished (>=99%) or untouched.
    `limit` stays the first positional parameter (profile_id was added
    later) so `list_continue_reading(8)` keeps meaning "limit=8".
    `visible_to` (auth B2): only dramas that user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        rows = conn.execute("""
            SELECT d.*, p.last_line_idx, p.audio_position_seconds, p.last_page,
                   p.percent_complete, p.last_accessed_at
            FROM progress p JOIN dramas d ON d.id = p.drama_id
            WHERE p.profile_id = ? AND p.percent_complete > 0 AND p.percent_complete < 99""" + visible + """
            ORDER BY p.last_accessed_at DESC LIMIT ?
        """, [profile_id] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]


def list_reading_history(drama_id: int = None, limit: int = 50, profile_id: int = None,
                         visible_to: int = None):
    """`visible_to` (auth B2, all-dramas listing only): only dramas that
    user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        if drama_id:
            rows = conn.execute(
                "SELECT h.id, h.drama_id, h.line_id, COALESCE(l.idx, h.line_idx) AS line_idx, "
                "h.percent_complete, h.accessed_at, d.title_en, d.title_zh FROM reading_history h "
                "JOIN dramas d ON d.id = h.drama_id LEFT JOIN lines l ON l.id = h.line_id "
                "WHERE h.drama_id = ? AND h.profile_id = ? "
                "ORDER BY h.accessed_at DESC LIMIT ?", (drama_id, profile_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT h.id, h.drama_id, h.line_id, COALESCE(l.idx, h.line_idx) AS line_idx, "
                "h.percent_complete, h.accessed_at, d.title_en, d.title_zh FROM reading_history h "
                "JOIN dramas d ON d.id = h.drama_id LEFT JOIN lines l ON l.id = h.line_id "
                "WHERE h.profile_id = ?" + visible + " "
                "ORDER BY h.accessed_at DESC LIMIT ?",
                [profile_id] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]


def clear_reading_history(drama_id: int = None, profile_id: int = None):
    with contextlib.closing(get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        if drama_id:
            conn.execute("DELETE FROM reading_history WHERE drama_id = ? AND profile_id = ?",
                         (drama_id, profile_id))
        else:
            conn.execute("DELETE FROM reading_history WHERE profile_id = ?", (profile_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Custom tags
# ---------------------------------------------------------------------------

# Step 24: personal organizational tags, kept in custom_tags alongside any
# user-defined ones -- deliberately separate from dramas.status, which
# tracks pipeline progress, not how the person is organizing their list.
ORGANIZATIONAL_TAGS = ("Favorite", "On Hold", "Plan to Translate")


def has_custom_tag(drama: dict, tag: str) -> bool:
    """Case-insensitive, so a hand-typed "favorite" counts as Favorite."""
    return tag.lower() in (t.strip().lower() for t in (drama.get("custom_tags") or "").split(","))


def set_custom_tag(drama_id: int, tag: str, present: bool):
    """Adds or removes one tag from a drama's custom_tags, leaving every
    other tag (and every other column) untouched."""
    drama = get_drama(drama_id)
    if not drama:
        return
    tags = [t.strip() for t in (drama.get("custom_tags") or "").split(",")
            if t.strip() and t.strip().lower() != tag.lower()]
    if present:
        tags.append(tag)
    update_drama(drama_id, custom_tags=", ".join(tags))


# ---------------------------------------------------------------------------
# Translation versions -- keep alternate translations side by side
# ---------------------------------------------------------------------------

def save_translation_version(drama_id: int, lines, label: str, engine: str = "",
                              model: str = "", make_active: bool = False):
    """Stores a complete translation as a named version, so re-translating
    with a different model never destroys the previous attempt."""
    payload = [{"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start, "end": ln.end,
                "zh": ln.zh, "en": ln.en,
                "speaker": getattr(ln, "speaker", None),
                "speaker_manual": bool(getattr(ln, "speaker_manual", False)),
                "flag": getattr(ln, "flag", None), "flag_note": getattr(ln, "flag_note", "") or "",
                "sfx": bool(getattr(ln, "sfx", False))} for ln in lines]
    with contextlib.closing(get_conn()) as conn:
        if make_active:
            conn.execute("UPDATE translation_versions SET is_active = 0 WHERE drama_id = ?", (drama_id,))
        cur = conn.execute("""
            INSERT INTO translation_versions (drama_id, label, engine, model, is_active,
                                               lines_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, label, engine, model, int(make_active),
              json.dumps(payload, ensure_ascii=False), datetime.datetime.utcnow().isoformat()))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def list_translation_versions(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, drama_id, label, engine, model, is_active, created_at "
            "FROM translation_versions WHERE drama_id = ? ORDER BY created_at DESC",
            (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def get_translation_version(version_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM translation_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["lines"] = json.loads(d["lines_json"]) if d["lines_json"] else []
    return d


def set_active_translation_version(drama_id: int, version_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE translation_versions SET is_active = 0 WHERE drama_id = ?", (drama_id,))
        conn.execute("UPDATE translation_versions SET is_active = 1 WHERE id = ?", (version_id,))
        conn.commit()


def delete_translation_version(version_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM translation_versions WHERE id = ?", (version_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Universe wiki -- accumulating encyclopedia, spoiler-bounded
# ---------------------------------------------------------------------------

def upsert_wiki_entry(drama_id: int, entry_type: str, name: str, description: str = None,
                       aliases: str = None, attributes: dict = None,
                       first_seen_line_idx: int = None, known_through_line_idx: int = None):
    """Adds or updates an encyclopedia entry. known_through_line_idx records
    how far into the story this entry was built from -- the spoiler boundary,
    so an entry is never shown to someone who hasn't read that far."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO wiki_entries (drama_id, entry_type, name, aliases, description,
                                       attributes_json, first_seen_line_idx,
                                       known_through_line_idx, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, entry_type, name) DO UPDATE SET
                aliases = COALESCE(excluded.aliases, wiki_entries.aliases),
                description = COALESCE(excluded.description, wiki_entries.description),
                attributes_json = COALESCE(excluded.attributes_json, wiki_entries.attributes_json),
                first_seen_line_idx = COALESCE(wiki_entries.first_seen_line_idx, excluded.first_seen_line_idx),
                known_through_line_idx = MAX(COALESCE(excluded.known_through_line_idx, 0),
                                              COALESCE(wiki_entries.known_through_line_idx, 0)),
                updated_at = excluded.updated_at
        """, (drama_id, entry_type, name, aliases, description,
              json.dumps(attributes, ensure_ascii=False) if attributes else None,
              first_seen_line_idx, known_through_line_idx,
              datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_wiki_entries(drama_id: int, entry_type: str = None, spoiler_limit_line_idx: int = None):
    """spoiler_limit_line_idx: when set, only returns entries first introduced
    at or before that line -- so the wiki never reveals something you haven't
    reached yet."""
    with contextlib.closing(get_conn()) as conn:
        q = "SELECT * FROM wiki_entries WHERE drama_id = ?"
        params = [drama_id]
        if entry_type:
            q += " AND entry_type = ?"
            params.append(entry_type)
        if spoiler_limit_line_idx is not None:
            q += " AND (first_seen_line_idx IS NULL OR first_seen_line_idx <= ?)"
            params.append(spoiler_limit_line_idx)
        q += " ORDER BY entry_type, name"
        rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["attributes"] = json.loads(d["attributes_json"]) if d["attributes_json"] else {}
        except (json.JSONDecodeError, TypeError):
            d["attributes"] = {}
        out.append(d)
    return out


def clear_wiki(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM wiki_entries WHERE drama_id = ?", (drama_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Adaptive style -- learn translation preferences from your edits
# ---------------------------------------------------------------------------

def record_edit_sample(drama_id: int, zh: str, ai_version: str, user_version: str):
    """Records a line you rewrote, so the system can learn what you
    consistently change. Only stores genuine edits, not untouched lines."""
    if not user_version or ai_version.strip() == user_version.strip():
        return
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO edit_samples (drama_id, zh, ai_version, user_version, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (drama_id, zh, ai_version, user_version, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_edit_samples(drama_id: int = None, limit: int = 100):
    with contextlib.closing(get_conn()) as conn:
        if drama_id:
            rows = conn.execute(
                "SELECT * FROM edit_samples WHERE drama_id = ? ORDER BY created_at DESC LIMIT ?",
                (drama_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM edit_samples ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_style_profile(scope: str, profile: dict, sample_count: int = 0):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO style_profile (scope, profile_json, sample_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                profile_json = excluded.profile_json,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
        """, (scope, json.dumps(profile, ensure_ascii=False), sample_count,
              datetime.datetime.utcnow().isoformat()))
        conn.commit()


STYLE_HISTORY_KEEP = 5


def _style_history(raw) -> list:
    try:
        hist = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return [h for h in hist if isinstance(h, dict)] if isinstance(hist, list) else []


def replace_style_profile(scope: str, profile: dict, sample_count: int = 0):
    """save_style_profile for a learn or reset: in one transaction, the
    profile being replaced (when it had preferences) is kept first in the
    scope's history (at most STYLE_HISTORY_KEEP), so restore_style_profile
    can bring it back."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT profile_json, sample_count, updated_at, history_json "
                           "FROM style_profile WHERE scope = ?", (scope,)).fetchone()
        hist = _style_history(row["history_json"]) if row else []
        if row:
            try:
                old = json.loads(row["profile_json"]) if row["profile_json"] else {}
            except (json.JSONDecodeError, TypeError):
                old = {}
            if isinstance(old, dict) and old.get("preferences"):
                hist.insert(0, {"profile": old, "sample_count": row["sample_count"] or 0,
                                "updated_at": row["updated_at"]})
        conn.execute("""
            INSERT INTO style_profile (scope, profile_json, sample_count, updated_at, history_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                profile_json = excluded.profile_json,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at,
                history_json = excluded.history_json
        """, (scope, json.dumps(profile, ensure_ascii=False), sample_count,
              datetime.datetime.utcnow().isoformat(),
              json.dumps(hist[:STYLE_HISTORY_KEEP], ensure_ascii=False)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_style_profile(scope: str, index: int = 0):
    """Makes history entry `index` (0 = the most recent earlier profile)
    current again, in one transaction; the profile it replaces (when it had
    preferences) takes its place first in the history, so nothing is lost.
    Returns False when there is no such entry."""
    conn = get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT profile_json, sample_count, updated_at, history_json "
                           "FROM style_profile WHERE scope = ?", (scope,)).fetchone()
        hist = _style_history(row["history_json"]) if row else []
        if not 0 <= index < len(hist):
            conn.rollback()
            return False
        chosen = hist.pop(index)
        try:
            cur = json.loads(row["profile_json"]) if row["profile_json"] else {}
        except (json.JSONDecodeError, TypeError):
            cur = {}
        if isinstance(cur, dict) and cur.get("preferences"):
            hist.insert(0, {"profile": cur, "sample_count": row["sample_count"] or 0,
                            "updated_at": row["updated_at"]})
        conn.execute("UPDATE style_profile SET profile_json = ?, sample_count = ?, "
                     "updated_at = ?, history_json = ? WHERE scope = ?",
                     (json.dumps(chosen.get("profile") or {}, ensure_ascii=False),
                      int(chosen.get("sample_count") or 0),
                      datetime.datetime.utcnow().isoformat(),
                      json.dumps(hist[:STYLE_HISTORY_KEEP], ensure_ascii=False), scope))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_style_profile(scope: str = "global"):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM style_profile WHERE scope = ?", (scope,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else {}
    except (json.JSONDecodeError, TypeError):
        d["profile"] = {}
    d["history"] = _style_history(d.get("history_json"))
    return d


def list_glossary_terms(series_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM glossary_terms WHERE series_id = ? ORDER BY term_original",
                             (series_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_glossary_term(term_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM glossary_terms WHERE id = ?", (term_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Translation memory (Step 24) -- see translation_memory.py for matching
# ---------------------------------------------------------------------------

def record_translation_memory(series_id: int, source_text: str, translation: str):
    """Stores a translation the translator approved by hand. The same pair
    again counts as another use; a different translation for the same
    source replaces the old one and restarts its count."""
    source_text, translation = (source_text or "").strip(), (translation or "").strip()
    if not series_id or not source_text or not translation:
        return
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO translation_memory (series_id, source_text, translation, use_count, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(series_id, source_text) DO UPDATE SET
                use_count = CASE WHEN translation_memory.translation = excluded.translation
                                 THEN translation_memory.use_count + 1 ELSE 1 END,
                translation = excluded.translation,
                updated_at = excluded.updated_at
        """, (series_id, source_text, translation, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_translation_memory(series_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM translation_memory WHERE series_id = ? ORDER BY id",
                            (series_id,)).fetchall()
    return [dict(r) for r in rows]


def bump_translation_memory_use(entry_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE translation_memory SET use_count = use_count + 1, updated_at = ? WHERE id = ?",
                     (datetime.datetime.utcnow().isoformat(), entry_id))
        conn.commit()


def update_translation_memory_after_replace(series_id: int, old_translation: str,
                                            new_translation: str) -> int:
    """Find-and-replace just changed a line's translation from old to new:
    any stored entry still holding the old text is corrected to match (or
    dropped, if the replacement emptied it), so memory doesn't keep
    suggesting the mistake that was just fixed. Returns rows touched."""
    old, new = (old_translation or "").strip(), (new_translation or "").strip()
    if not series_id or not old or old == new:
        return 0
    with contextlib.closing(get_conn()) as conn:
        if new:
            cur = conn.execute(
                "UPDATE translation_memory SET translation = ?, updated_at = ? "
                "WHERE series_id = ? AND translation = ?",
                (new, datetime.datetime.utcnow().isoformat(), series_id, old))
        else:
            cur = conn.execute("DELETE FROM translation_memory WHERE series_id = ? AND translation = ?",
                               (series_id, old))
        conn.commit()
    return cur.rowcount


def update_glossary_term(term_id: int, term_original: str, term_translation: str,
                          notes: str = "", category: str = None, policy: str = None,
                          enforce_exact: bool = False, aliases: str = None,
                          banned_translations: str = None):
    """Updates an existing glossary term by its own id -- unlike
    upsert_glossary_term (keyed on term_original, for the extract-and-add
    flow), this lets a term's original text itself be corrected without
    orphaning the old row as a stale duplicate entry."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            UPDATE glossary_terms
            SET term_original = ?, term_translation = ?, notes = ?, category = ?,
                policy = ?, enforce_exact = ?, aliases = ?, banned_translations = ?
            WHERE id = ?
        """, (term_original, term_translation, notes, category, policy, int(enforce_exact),
              aliases, banned_translations, term_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Step 9c: Workspace-configuration presets -- library-level, reusable
# across unrelated series/projects. See the `presets` table's own comment
# in init_db for why deleting/renaming one never touches a drama it was
# previously applied to: nothing else references presets.id at all.
# ---------------------------------------------------------------------------

def save_preset(name: str, translation_engine: str = None, engine_model: str = None,
                 style_preset: str = None, locale: str = None,
                 default_female_pronouns: bool = False, include_genre_notes: bool = True) -> int:
    """Creates a new preset, or overwrites the existing one with this exact
    name -- "Save as preset" under a name that's already taken replaces
    its captured fields rather than failing on the name's UNIQUE
    constraint, the same "save as" behavior as most apps. Overwriting
    never touches any drama the old version was previously applied to,
    since applying one only ever copies its fields onto a drama at that
    moment (see the `presets` table comment) -- there's no live link to
    break."""
    now = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO presets (name, translation_engine, engine_model, style_preset, locale,
                                  default_female_pronouns, include_genre_notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                translation_engine = excluded.translation_engine,
                engine_model = excluded.engine_model,
                style_preset = excluded.style_preset,
                locale = excluded.locale,
                default_female_pronouns = excluded.default_female_pronouns,
                include_genre_notes = excluded.include_genre_notes,
                updated_at = excluded.updated_at
        """, (name, translation_engine, engine_model, style_preset, locale,
              int(bool(default_female_pronouns)), int(bool(include_genre_notes)), now, now))
        conn.commit()
        # cur.lastrowid isn't reliable on the UPDATE branch of an upsert --
        # look the row up by its own UNIQUE name instead of trusting it.
        preset_id = conn.execute("SELECT id FROM presets WHERE name = ?", (name,)).fetchone()["id"]
    return preset_id


def insert_preset(name: str, translation_engine: str = None, engine_model: str = None,
                  style_preset: str = None, locale: str = None,
                  default_female_pronouns: bool = False, include_genre_notes: bool = True) -> int:
    """Insert-only twin of save_preset: a taken name raises
    sqlite3.IntegrityError (UNIQUE(name)) instead of replacing the row, so
    a caller can refuse an overwrite without a check-then-write race."""
    now = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO presets (name, translation_engine, engine_model, style_preset, locale,
                                  default_female_pronouns, include_genre_notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, translation_engine, engine_model, style_preset, locale,
              int(bool(default_female_pronouns)), int(bool(include_genre_notes)), now, now))
        conn.commit()
        return cur.lastrowid


def set_preset_engine_model(preset_id: int, engine_model: str, expected_model: str = None) -> bool:
    """Step 40's guided switch: changes only a preset's model, and only if it
    still holds expected_model (when given). False when nothing changed."""
    with contextlib.closing(get_conn()) as conn:
        if expected_model is None:
            cur = conn.execute("UPDATE presets SET engine_model = ?, updated_at = ? WHERE id = ?",
                               (engine_model, datetime.datetime.utcnow().isoformat(), preset_id))
        else:
            cur = conn.execute(
                "UPDATE presets SET engine_model = ?, updated_at = ? WHERE id = ? AND engine_model = ?",
                (engine_model, datetime.datetime.utcnow().isoformat(), preset_id, expected_model))
        conn.commit()
    return cur.rowcount > 0


def list_presets():
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM presets ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def rename_preset(preset_id: int, new_name: str):
    """Updates only the name -- every captured field is left exactly as
    saved."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
                     (new_name, datetime.datetime.utcnow().isoformat(), preset_id))
        conn.commit()


def delete_preset(preset_id: int):
    """Removes only the presets row. No drama row (or any other table)
    references a preset's id, so a drama this preset was previously
    applied to is completely unaffected -- see the `presets` table's own
    comment in init_db."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Line history / undo -- snapshots taken before risky bulk operations
# (force re-translate, merge) so you can roll back to a previous version
# instead of losing translation work with no way back.
# ---------------------------------------------------------------------------

def save_line_history_snapshot(drama_id: int, lines, label: str, keep_last: int = 10):
    """Saves a full snapshot of the current lines before a risky bulk
    operation. Keeps only the most recent `keep_last` snapshots per
    drama to avoid unbounded growth -- older ones are pruned."""
    snapshot = [
        {"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start, "end": ln.end,
         "zh": ln.zh, "en": ln.en,
         "speaker": getattr(ln, "speaker", None), "dub_filename": getattr(ln, "dub_filename", None),
         "speaker_manual": bool(getattr(ln, "speaker_manual", False)),
         "flag": getattr(ln, "flag", None), "flag_note": getattr(ln, "flag_note", "") or "",
         "sfx": bool(getattr(ln, "sfx", False))}
        for ln in lines
    ]
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO line_history (drama_id, label, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
            (drama_id, label, json.dumps(snapshot, ensure_ascii=False),
             datetime.datetime.utcnow().isoformat())
        )
        # Prune old snapshots. Inside the same transaction so a failure here
        # can't leave the prune applied without the new snapshot written.
        ids = conn.execute(
            "SELECT id FROM line_history WHERE drama_id = ? ORDER BY created_at DESC", (drama_id,)
        ).fetchall()
        if len(ids) > keep_last:
            old_ids = [r["id"] for r in ids[keep_last:]]
            conn.executemany("DELETE FROM line_history WHERE id = ?", [(i,) for i in old_ids])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_line_history(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, drama_id, label, created_at FROM line_history WHERE drama_id = ? ORDER BY created_at DESC",
            (drama_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_line_history_snapshot(history_id: int):
    """Returns the list of line dicts from a saved snapshot, ready to
    be turned back into Line objects and saved via save_lines()."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT snapshot_json FROM line_history WHERE id = ?", (history_id,)).fetchone()
    if not row:
        return None
    return json.loads(row["snapshot_json"])


# ---------------------------------------------------------------------------
# Vocab lookups (persisted Reader click-to-define history, for export)
# ---------------------------------------------------------------------------

def save_vocab_lookup(drama_id: int, word: str, reading: str, definitions, language: str,
                       first_seen_line_idx: int = None):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO vocab_lookups (drama_id, word, reading, definitions, language,
                                        first_seen_line_idx, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, word) DO NOTHING
        """, (drama_id, word, reading, json.dumps(definitions, ensure_ascii=False), language,
              first_seen_line_idx, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_vocab_lookups(drama_id: int = None, rich_only: bool = False):
    """rich_only=True returns only lookups queued (via set_vocab_export_rich)
    for the richer sentence+audio Anki card type -- see vocab_export.
    export_vocab_apkg_sentence."""
    with contextlib.closing(get_conn()) as conn:
        conditions, params = [], []
        if drama_id:
            conditions.append("drama_id = ?")
            params.append(drama_id)
        if rich_only:
            conditions.append("export_rich = 1")
        query = "SELECT * FROM vocab_lookups"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["definitions"] = json.loads(d["definitions"]) if d["definitions"] else []
        except (json.JSONDecodeError, TypeError):
            d["definitions"] = []
        out.append(d)
    return out


def set_vocab_export_rich(drama_id: int, word: str, flag: bool = True):
    """Queues (or un-queues) a single already-looked-up word for the
    richer sentence+audio Anki card type -- called from the Reader's
    definitions right where the word was looked up, per Step 20b."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE vocab_lookups SET export_rich = ? WHERE drama_id = ? AND word = ?",
                     (1 if flag else 0, drama_id, word))
        conn.commit()


# ---------------------------------------------------------------------------
# Usage/cost logging
# ---------------------------------------------------------------------------

def log_usage(drama_id: int, engine: str, model: str, operation: str,
              input_tokens: int = 0, output_tokens: int = 0, estimated_cost_usd: float = 0.0,
              cache_read_tokens: int = 0):
    """input_tokens is the whole prompt; cache_read_tokens is the part of
    it a provider served from its prompt cache."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO usage_log (drama_id, engine, model, operation, input_tokens,
                                    output_tokens, estimated_cost_usd, created_at, cache_read_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, engine, model, operation, input_tokens, output_tokens,
              estimated_cost_usd, datetime.datetime.utcnow().isoformat(), cache_read_tokens or 0))
        conn.commit()
    try:
        # Step 41 item 5: count the spend toward the running job's stage
        # (a no-op outside a background job's own thread).
        from services import job_timing_service
        job_timing_service.add_cost(estimated_cost_usd)
    except Exception:
        pass


def get_month_spend(now: datetime.datetime = None) -> float:
    """Estimated spend logged so far in the current calendar month (UTC),
    across the whole library -- what the monthly cap is checked against."""
    now = now or datetime.datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS spent FROM usage_log WHERE created_at >= ?",
            (month_start,)).fetchone()
    return float(row["spent"])


# Step 25w: how long a held gpu_lock row is trusted before it's treated as
# abandoned (its holder process crashed or was killed without releasing
# it) and given to whoever asks next. Comfortably longer than
# background_jobs.py's own progress-poll cadence, so a live job's regular
# heartbeat_gpu_lock() calls always land well inside this window.
GPU_LOCK_STALE_SECONDS = 600
# Matches the gpu_lock table's CHECK: no caller can hold more slots.
GPU_LOCK_MAX_SLOTS = 4


def _migrate_gpu_lock_slots():
    """gpu_lock used to allow a single row (CHECK (id = 1)); it now has one
    row per holder. A CHECK can't be changed with ALTER TABLE, so the table
    is rebuilt once, in one transaction, keeping any held row."""
    conn = get_conn()
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    def single_row():
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'gpu_lock'").fetchone()
        return bool(row) and "CHECK(id=1)" in "".join((row[0] or "").split())

    try:
        if not single_row():
            return
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        if not single_row():  # another process migrated it while this one waited
            conn.execute("ROLLBACK")
            return
        conn.execute("DROP TABLE IF EXISTS gpu_lock_new")
        conn.execute("""
            CREATE TABLE gpu_lock_new (
                id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 4),
                holder TEXT NOT NULL,
                description TEXT,
                acquired_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL
            )""")
        conn.execute("INSERT INTO gpu_lock_new SELECT id, holder, description, acquired_at, heartbeat_at "
                     "FROM gpu_lock")
        conn.execute("DROP TABLE gpu_lock")
        conn.execute("ALTER TABLE gpu_lock_new RENAME TO gpu_lock")
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def try_acquire_gpu_lock(holder: str, description: str = None, max_holders: int = 1,
                         settle_seconds: float = 0) -> bool:
    """Cross-process GPU guard. background_jobs.py's own guard (Step 5c)
    is plain in-process module state -- invisible to a separate OS
    process, so a `cli.py` run and the live UI could each start their own
    GPU-touching job with neither ever seeing the other. The gpu_lock
    table in the shared library.db is the coordination point instead
    (BEGIN IMMEDIATE makes the read-then-write below atomic across
    processes).

    Returns True if `holder` already holds a slot (refreshed), or fewer
    than `max_holders` (at most GPU_LOCK_MAX_SLOTS) other live holders
    exist -- a holder whose last heartbeat is older than
    GPU_LOCK_STALE_SECONDS is abandoned and doesn't count -- and is now
    holding a slot. With `settle_seconds`, joining other holders is also
    refused until the newest of them has held its slot that long, so a
    free-VRAM reading taken before this call already includes that
    holder's model; checked here so two processes reading the same free
    VRAM can't both join on it. Returns False otherwise."""
    now = time.time()
    max_holders = max(1, min(int(max_holders), GPU_LOCK_MAX_SLOTS))
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT id, holder, acquired_at, heartbeat_at FROM gpu_lock").fetchall()
        mine = next((r for r in rows if r["holder"] == holder), None)
        if mine is not None:
            slot = mine["id"]
        else:
            live = [r for r in rows if (now - r["heartbeat_at"]) < GPU_LOCK_STALE_SECONDS]
            if len(live) >= max_holders or (
                    live and settle_seconds
                    and now - max(r["acquired_at"] for r in live) < settle_seconds):
                # Refused, nothing to write: end the IMMEDIATE transaction
                # here so the write lock other processes wait on is released
                # explicitly rather than left to close().
                conn.execute("ROLLBACK")
                return False
            taken = {r["id"] for r in live}
            slot = next(i for i in range(1, GPU_LOCK_MAX_SLOTS + 1) if i not in taken)
        conn.execute("""
            INSERT INTO gpu_lock (id, holder, description, acquired_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET holder = excluded.holder,
                description = excluded.description, acquired_at = excluded.acquired_at,
                heartbeat_at = excluded.heartbeat_at
        """, (slot, holder, description, now, now))
        conn.commit()
        return True
    finally:
        conn.close()


def heartbeat_gpu_lock(holder: str):
    """Refreshes a held lock's heartbeat so a still-running job doesn't
    look abandoned to another process partway through a long run."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE gpu_lock SET heartbeat_at = ? WHERE holder = ?",
                     (time.time(), holder))
        conn.commit()


def release_gpu_lock(holder: str):
    """No-ops if `holder` holds no slot -- e.g. it already went stale and
    was taken over by someone else, so releasing it now would release the
    new holder's slot instead of this one's."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM gpu_lock WHERE holder = ?", (holder,))
        conn.commit()


def gpu_lock_holder_count(exclude_holder: str = None) -> int:
    """How many live (not stale) holders the GPU lock has, not counting
    `exclude_holder`."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM gpu_lock WHERE heartbeat_at > ? AND holder IS NOT ?",
            (time.time() - GPU_LOCK_STALE_SECONDS, exclude_holder)).fetchone()
    return int(row[0])


def gpu_lock_status():
    """(holder, description) of a live holder of the cross-process GPU
    lock (the lowest slot), or (None, None) if none holds it or every
    holder went stale."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT holder, description FROM gpu_lock WHERE heartbeat_at > ? "
                           "ORDER BY id LIMIT 1",
                           (time.time() - GPU_LOCK_STALE_SECONDS,)).fetchone()
    if not row:
        return None, None
    return row["holder"], row["description"]


def get_app_setting(key: str, default=None):
    """A general-purpose, cross-process app setting (Migration Slice 9,
    D1 fix 2) -- distinct from sources/store.py's own settings table,
    which is scoped to the source-adapter system only."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value"])


def set_app_setting(key: str, value):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, json.dumps(value)))
        conn.commit()


def get_research_cache(cache_key: str):
    """Step 37: a cached grounded-research result (the decoded dict), or None."""
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT result_json FROM metadata_research_cache WHERE cache_key = ?",
                           (cache_key,)).fetchone()
    return json.loads(row["result_json"]) if row else None


def put_research_cache(cache_key: str, result: dict):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO metadata_research_cache (cache_key, result_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET result_json = excluded.result_json,
                                                 created_at = excluded.created_at
        """, (cache_key, json.dumps(result), datetime.datetime.utcnow().isoformat()))
        conn.commit()


RESEARCH_RESULT_TTL_DAYS = 7


def put_research_result(research_id: str, drama_id: int, result: dict):
    """Step 37: the snapshot a research_id applies; rows older than
    RESEARCH_RESULT_TTL_DAYS are pruned on each insert."""
    now = datetime.datetime.utcnow()
    cutoff = (now - datetime.timedelta(days=RESEARCH_RESULT_TTL_DAYS)).isoformat()
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM metadata_research_results WHERE created_at < ?", (cutoff,))
        conn.execute("INSERT INTO metadata_research_results (research_id, drama_id, result_json, "
                     "created_at) VALUES (?, ?, ?, ?)",
                     (research_id, drama_id, json.dumps(result), now.isoformat()))
        conn.commit()


def get_research_result(research_id: str, drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT result_json FROM metadata_research_results "
                           "WHERE research_id = ? AND drama_id = ?",
                           (research_id, drama_id)).fetchone()
    return json.loads(row["result_json"]) if row else None


def add_field_provenance(drama_id: int, field: str, value: str, status: str, *,
                         sources=None, retrieved_at: str = None, confidence: float = None,
                         last_verified: str = None):
    """Step 37: one researched field value with its own evidence. `sources`
    is a list of {"title", "url"}; the first is also stored as source /
    source_url."""
    sources = list(sources or [])
    first = sources[0] if sources else {}
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO metadata_field_provenance (drama_id, field, value, source, source_url,
                sources_json, retrieved_at, confidence, last_verified, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, field, value, first.get("title"), first.get("url"), json.dumps(sources),
              retrieved_at, confidence, last_verified, status))
        conn.commit()


def list_field_provenance(drama_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("""
            SELECT id, field, value, source, source_url, sources_json, retrieved_at, confidence,
                   last_verified, status
            FROM metadata_field_provenance WHERE drama_id = ? ORDER BY id
        """, (drama_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d.pop("sources_json") or "[]")
        out.append(d)
    return out


def save_job_record(job_id: str, status: str, progress: float = None, message: str = None,
                    error: str = None, description: str = None, gpu_touching: bool = False,
                    started_at: float = None, finished_at: float = None,
                    result_json: str = None, owner_user_id: int = None, owner_pid: int = None):
    """Mirrors one background_jobs.py job's status-transition fields into
    the cross-process job_records table (Migration Slice 7) -- records
    only, no resume: this is the *last written* state, not necessarily
    the *current* state, if the process that wrote it has since died
    without writing a terminal status. A caller reading this table for
    cross-process visibility should treat a long-unchanged `updated_at`
    on a "running"/"queued" row as suspect, not trust `status` blindly."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("""
            INSERT INTO job_records (job_id, status, progress, message, error, description,
                gpu_touching, started_at, finished_at, updated_at, result_json, owner_user_id,
                owner_pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status, progress = excluded.progress,
                message = excluded.message, error = excluded.error,
                description = excluded.description, gpu_touching = excluded.gpu_touching,
                started_at = excluded.started_at, finished_at = excluded.finished_at,
                updated_at = excluded.updated_at, result_json = excluded.result_json,
                owner_pid = excluded.owner_pid,
                owner_user_id = CASE
                    WHEN excluded.status IN ('queued', 'running')
                         AND job_records.status NOT IN ('queued', 'running')
                    THEN excluded.owner_user_id
                    -- A new run (auth B2): a stale "running" row left by a
                    -- crashed process must not keep its old owner.
                    WHEN excluded.status IN ('queued', 'running')
                         AND excluded.started_at IS NOT job_records.started_at
                    THEN excluded.owner_user_id
                    ELSE COALESCE(job_records.owner_user_id, excluded.owner_user_id) END,
                cancel_requested = CASE WHEN excluded.status IN ('queued', 'running')
                    THEN job_records.cancel_requested ELSE 0 END
        """, (job_id, status, progress, message, error, description, int(bool(gpu_touching)),
              started_at, finished_at, time.time(), result_json, owner_user_id, owner_pid))
        conn.commit()


def request_job_record_cancel(job_id: str) -> bool:
    """Migration Slice 22: flags a job_records row as cancel-requested so
    the process actually running the job (which may not be this one) can
    notice it. Only touches a still queued/running row; returns whether
    it did."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "UPDATE job_records SET cancel_requested = 1 "
            "WHERE job_id = ? AND status IN ('queued', 'running')", (job_id,))
        conn.commit()
        return cur.rowcount > 0


def touch_job_records(job_ids) -> None:
    """B-04: the owning process's heartbeat (background_jobs) -- bumps
    updated_at on its still queued/running rows so a job that is alive
    but not changing status never looks abandoned. updated_at is only ever
    written by the owner (request_job_record_cancel leaves it alone)."""
    job_ids = list(job_ids)
    if not job_ids:
        return
    now = time.time()
    with contextlib.closing(get_conn()) as conn:
        conn.executemany(
            "UPDATE job_records SET updated_at = ? "
            "WHERE job_id = ? AND status IN ('queued', 'running')",
            [(now, j) for j in job_ids])
        conn.commit()


def close_stale_job_record(job_id: str, cutoff: float, error: str = None) -> bool:
    """B-04: marks a queued/running row cancelled only if its owner has not
    written or heartbeated since `cutoff` -- a single conditional UPDATE,
    so a row the owner just finished ("done") or just touched is never
    overwritten. `error`, if given, says why. Returns whether it closed
    the row."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "UPDATE job_records SET status = 'cancelled', finished_at = ?, cancel_requested = 0, "
            "error = COALESCE(?, error) "
            "WHERE job_id = ? AND status IN ('queued', 'running') "
            "AND COALESCE(updated_at, 0) < ?", (time.time(), error, job_id, cutoff))
        conn.commit()
        return cur.rowcount > 0


def close_orphaned_job_record(job_id: str, owner_pid: int, error: str = None) -> bool:
    """Marks a queued/running row cancelled if it still belongs to the
    (exited) process `owner_pid`: one conditional UPDATE, so a new run of
    the same job id by a live process is never closed. Returns whether it
    closed the row."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "UPDATE job_records SET status = 'cancelled', finished_at = ?, cancel_requested = 0, "
            "error = COALESCE(?, error) "
            "WHERE job_id = ? AND status IN ('queued', 'running') AND owner_pid = ?",
            (time.time(), error, job_id, owner_pid))
        conn.commit()
        return cur.rowcount > 0


def is_job_record_cancel_requested(job_id: str) -> bool:
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT cancel_requested FROM job_records WHERE job_id = ?",
                           (job_id,)).fetchone()
    return bool(row and row[0])


def list_job_records() -> list:
    """Every job_records row, newest-started first -- the cross-process
    job list a `GET /api/jobs` endpoint (Migration Slice 8) would read.
    Rows accumulate forever unless cleared (delete_job_record/
    clear_all_job_records) -- no automatic pruning in this slice."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM job_records ORDER BY started_at DESC NULLS LAST").fetchall()
        return [dict(r) for r in rows]


def list_job_record_fingerprints() -> dict:
    """{job_id: (status, progress, message, error, finished_at,
    cancel_requested, result length)} for every job_records row: the cheap
    change check behind the SSE stream's sweep (services/
    event_stream_service.py). updated_at is left out on purpose: the
    heartbeat bumps it without any visible change."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT job_id, status, progress, message, error, finished_at, cancel_requested, "
            "LENGTH(result_json) FROM job_records").fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def get_job_record(job_id: str):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM job_records WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def delete_job_record(job_id: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM job_records WHERE job_id = ?", (job_id,))
        conn.commit()


def clear_all_job_records():
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM job_records")
        conn.commit()


def get_usage_summary(drama_id: int = None, visible_to: int = None):
    """Returns {"input_tokens", "output_tokens", "estimated_cost_usd", "call_count"} --
    totals for one drama, or the whole library if drama_id is None.
    `visible_to` (auth B2, library totals only): only calls logged against
    a drama that user may see (calls with no drama are left out)."""
    with contextlib.closing(get_conn()) as conn:
        if not drama_id and visible_to is not None:
            clause, params = drama_visible_sql("d", visible_to)
            row = conn.execute(f"""
                SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                       COUNT(*) as call_count
                FROM usage_log u JOIN dramas d ON d.id = u.drama_id WHERE {clause}
            """, params).fetchone()
        elif drama_id:
            row = conn.execute("""
                SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                       COUNT(*) as call_count
                FROM usage_log WHERE drama_id = ?
            """, (drama_id,)).fetchone()
        else:
            row = conn.execute("""
                SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                       COUNT(*) as call_count
                FROM usage_log
            """).fetchone()
    return dict(row)


def get_usage_by_drama(visible_to: int = None):
    """Per-drama cost breakdown, joined with drama titles, for the dashboard.
    Includes translation_engine so the dashboard can show "$0.00 (free)"
    for a free engine instead of a bare, ambiguous-looking $0.00.
    `visible_to` (auth B2): only dramas that user may see."""
    where, params = "", []
    if visible_to is not None:
        clause, params = drama_visible_sql("d", visible_to)
        where = "WHERE " + clause
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(f"""
            SELECT d.id, d.title_en, d.title_zh, d.translation_engine,
                   COALESCE(SUM(u.input_tokens),0) as input_tokens,
                   COALESCE(SUM(u.output_tokens),0) as output_tokens,
                   COALESCE(SUM(u.cache_read_tokens),0) as cache_read_tokens,
                   COALESCE(SUM(u.estimated_cost_usd),0) as estimated_cost_usd,
                   COUNT(u.id) as call_count
            FROM dramas d LEFT JOIN usage_log u ON u.drama_id = d.id
            {where}
            GROUP BY d.id ORDER BY estimated_cost_usd DESC
        """, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Step 26b: standalone translate tool history
# ---------------------------------------------------------------------------

def save_translate_history(source_language: str, target_language: str, engine: str,
                           source_text: str, translated_text: str, user_id: int = None) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO translate_history (source_language, target_language, engine,
                                            source_text, translated_text, created_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source_language, target_language, engine, source_text, translated_text,
              datetime.datetime.utcnow().isoformat(), user_id))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def list_translate_history(limit: int = 50, visible_to: int = None) -> List[dict]:
    """Most recent first. `visible_to` (auth B2): only that user's own rows
    (rows with no user -- the PC, the extension -- are for admins and the
    local owner, who pass None)."""
    with contextlib.closing(get_conn()) as conn:
        if visible_to is None:
            rows = conn.execute(
                "SELECT * FROM translate_history ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM translate_history WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?", (visible_to, limit)).fetchall()
    return [dict(r) for r in rows]


def clear_translate_history():
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM translate_history")
        conn.commit()


# ---------------------------------------------------------------------------
# Step 26: voice bank -- reuse a cloned voice across projects
# ---------------------------------------------------------------------------

def save_voice_bank_entry(name: str, clip_source_path: str, ref_text: str = "",
                           clone_engine: str = None, voice_design: str = "",
                           language: str = None, notes: str = "",
                           source_drama: str = None, source_speaker: str = None) -> int:
    """Copies clip_source_path into the shared library's voice bank folder
    (never a reference into the source drama's own folder) and records a
    new voice_bank row -- so deleting the source drama afterward leaves
    this entry's own clip intact. Returns the new entry's id."""
    import shutil
    import uuid
    os.makedirs(VOICE_BANK_DIR, exist_ok=True)
    ext = os.path.splitext(clip_source_path)[1]
    clip_filename = f"{uuid.uuid4().hex}{ext}"
    shutil.copyfile(clip_source_path, os.path.join(VOICE_BANK_DIR, clip_filename))
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO voice_bank (name, clip_filename, ref_text, clone_engine, voice_design,
                                     language, notes, source_drama, source_speaker, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, clip_filename, ref_text, clone_engine, voice_design, language, notes,
              source_drama, source_speaker, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def list_voice_bank_entries() -> List[dict]:
    """Alphabetical by name -- this is a picklist, not an activity log."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM voice_bank ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_voice_bank_entry(entry_id: int) -> dict:
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM voice_bank WHERE id = ?", (entry_id,)).fetchone()
    return dict(row) if row else None


def apply_voice_bank_entry(entry_id: int, drama_dir: str, drama_id: int, speaker_label: str) -> str:
    """Copies a bank entry's clip into drama_dir (a new file, not shared
    with the bank's own copy or any other drama's) and sets the matching
    character clone fields. Returns the clip's filename relative to
    drama_dir, as stored in characters.ref_audio_filename."""
    import shutil
    entry = get_voice_bank_entry(entry_id)
    if not entry:
        raise ValueError(f"No voice bank entry with id {entry_id}")
    ext = os.path.splitext(entry["clip_filename"])[1]
    dest_filename = f"voicebank_{entry_id}_{speaker_label}{ext}"
    os.makedirs(drama_dir, exist_ok=True)
    shutil.copyfile(os.path.join(VOICE_BANK_DIR, entry["clip_filename"]),
                     os.path.join(drama_dir, dest_filename))
    upsert_character(drama_id, speaker_label, ref_audio_filename=dest_filename,
                     ref_text=entry["ref_text"] or "", clone_engine=entry["clone_engine"],
                     voice_design=entry["voice_design"] or "")
    return dest_filename


def rename_voice_bank_entry(entry_id: int, new_name: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE voice_bank SET name = ? WHERE id = ?", (new_name, entry_id))
        conn.commit()


def delete_voice_bank_entry(entry_id: int):
    entry = get_voice_bank_entry(entry_id)
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM voice_bank WHERE id = ?", (entry_id,))
        conn.commit()
    if entry:
        clip_path = os.path.join(VOICE_BANK_DIR, entry["clip_filename"])
        if os.path.exists(clip_path):
            os.remove(clip_path)


# ---------------------------------------------------------------------------
# Bulk (batch-API / off-peak) translation jobs -- Step 9
# ---------------------------------------------------------------------------

BULK_PENDING_STATUSES = ("submitted", "scheduled", "auth_error")


def create_bulk_job(drama_id: int, engine: str, model: str, status: str, lines,
                    provider_batch_id: str = None, scheduled_for: str = None,
                    translate_args: dict = None, kind: str = "translate",
                    stage: str = None, pipeline_id: str = None) -> int:
    """lines: [(line_id, request_key, zh_hash, en_at_submit), ...], or,
    for a non-translate kind that needs something to compare against once
    results come back (see the `bulk_job_lines` table's own comment),
    [(line_id, request_key, zh_hash, en_at_submit, state_at_submit), ...]
    -- a plain 4-tuple still works, with state_at_submit left NULL.

    kind/stage/pipeline_id: see the `bulk_jobs` table's own comment
    (Step 9d). Every existing call site left these at their defaults, so
    an old job is still exactly what it always was: a translate job."""
    now = datetime.datetime.utcnow().isoformat()
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute("""
            INSERT INTO bulk_jobs (drama_id, engine, model, provider_batch_id, status,
                                   scheduled_for, translate_args, submitted_at, updated_at,
                                   kind, stage, pipeline_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, engine, model, provider_batch_id, status, scheduled_for,
              json.dumps(translate_args, ensure_ascii=False) if translate_args is not None else None,
              now, now, kind, stage, pipeline_id))
        job_id = cur.lastrowid
        rows = []
        for row in lines:
            if len(row) == 4:
                lid, key, h, en = row
                state = None
            else:
                lid, key, h, en, state = row
            rows.append((job_id, lid, key, h, en, state))
        conn.executemany("""
            INSERT INTO bulk_job_lines (bulk_job_id, line_id, request_key, zh_hash, en_at_submit,
                                        state_at_submit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return job_id


def _bulk_job_row(r) -> dict:
    d = dict(r)
    d["translate_args"] = json.loads(d["translate_args"]) if d.get("translate_args") else None
    d["result_summary"] = json.loads(d["result_summary"]) if d.get("result_summary") else None
    return d


def get_bulk_job(bulk_job_id: int):
    with contextlib.closing(get_conn()) as conn:
        r = conn.execute("SELECT * FROM bulk_jobs WHERE id = ?", (bulk_job_id,)).fetchone()
    return _bulk_job_row(r) if r else None


def list_bulk_jobs(drama_id: int = None, statuses=None, pipeline_id: str = None) -> list:
    """Newest first. statuses: optional iterable to filter on.
    pipeline_id: Step 9d -- every stage row of one Reflect run shares one,
    so the Bulk jobs panel can pull all three (whichever exist so far) to
    show them as a single pipeline."""
    sql, params = "SELECT * FROM bulk_jobs WHERE 1=1", []
    if drama_id is not None:
        sql += " AND drama_id = ?"
        params.append(drama_id)
    if statuses:
        statuses = list(statuses)
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    if pipeline_id is not None:
        sql += " AND pipeline_id = ?"
        params.append(pipeline_id)
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    return [_bulk_job_row(r) for r in rows]


def update_bulk_job(bulk_job_id: int, **fields):
    allowed = {"status", "provider_batch_id", "scheduled_for", "last_error", "result_summary"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "result_summary" in fields and fields["result_summary"] is not None:
        fields["result_summary"] = json.dumps(fields["result_summary"])
    fields["updated_at"] = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(get_conn()) as conn:
        conn.execute(f"UPDATE bulk_jobs SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                     list(fields.values()) + [bulk_job_id])
        conn.commit()


def count_bulk_job_lines(bulk_job_id: int) -> int:
    with contextlib.closing(get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) FROM bulk_job_lines WHERE bulk_job_id = ?",
                            (bulk_job_id,)).fetchone()[0]


def list_bulk_job_lines(bulk_job_id: int) -> list:
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM bulk_job_lines WHERE bulk_job_id = ? ORDER BY line_id",
                            (bulk_job_id,)).fetchall()
    return [dict(r) for r in rows]


def set_bulk_job_line_result_texts(bulk_job_id: int, result_by_line_id: dict):
    """Step 9d: records a Reflect stage's own raw per-line output (a
    faithfulness draft or a reflection critique) against this job's own
    line rows -- read back by the NEXT stage's prompt builder, never
    applied to a line directly (see the `bulk_job_lines` table's own
    comment). A line id not in result_by_line_id is left NULL, meaning
    that line either dropped out at this stage (source changed/deleted)
    or the model simply returned nothing for it."""
    if not result_by_line_id:
        return
    with contextlib.closing(get_conn()) as conn:
        conn.executemany(
            "UPDATE bulk_job_lines SET result_text = ? WHERE bulk_job_id = ? AND line_id = ?",
            [(text, bulk_job_id, lid) for lid, text in result_by_line_id.items()])
        conn.commit()


# ---------------------------------------------------------------------------
# Library-wide dashboard stats & global search
# ---------------------------------------------------------------------------

def get_library_stats(visible_to: int = None):
    """`visible_to` (auth B2): count only the dramas (and their lines) that
    user may see; None = the whole library."""
    where, params = "", []
    if visible_to is not None:
        clause, params = drama_visible_sql("d", visible_to)
        where = " WHERE " + clause
    lines_from = ("FROM lines JOIN dramas d ON d.id = lines.drama_id" + where) if where \
        else "FROM lines"
    lines_where = " AND " if where else " WHERE "
    with contextlib.closing(get_conn()) as conn:
        total_dramas = conn.execute(f"SELECT COUNT(*) FROM dramas d{where}", params).fetchone()[0]
        by_status = conn.execute(f"SELECT status, COUNT(*) as n FROM dramas d{where} GROUP BY status",
                                 params).fetchall()
        by_media_type = conn.execute(f"SELECT media_type, COUNT(*) as n FROM dramas d{where} "
                                     "GROUP BY media_type", params).fetchall()
        total_lines = conn.execute(f"SELECT COUNT(*) {lines_from}", params).fetchone()[0]
        translated_lines = conn.execute(
            f"SELECT COUNT(*) {lines_from}{lines_where}lines.en IS NOT NULL AND lines.en != ''",
            params).fetchone()[0]
    return {
        "total_dramas": total_dramas,
        "by_status": {r["status"]: r["n"] for r in by_status},
        "by_media_type": {r["media_type"]: r["n"] for r in by_media_type},
        "total_lines": total_lines,
        "translated_lines": translated_lines,
    }


def list_dramas_recently_active(n: int = 10, visible_to: int = None):
    where, params = "", []
    if visible_to is not None:
        clause, params = drama_visible_sql("dramas", visible_to)
        where = " WHERE " + clause
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(f"SELECT * FROM dramas{where} ORDER BY updated_at DESC LIMIT ?",
                            params + [n]).fetchall()
    return [dict(r) for r in rows]


def search_lines_globally(query: str, limit: int = 100, visible_to: int = None):
    """Searches zh/en text across every drama's lines, returns results
    with the parent drama's title attached, for the Library tab's
    global search -- not scoped to one drama like the Reader tab is.
    `visible_to` (auth B2): only dramas that user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(get_conn()) as conn:
        like = f"%{query}%"
        rows = conn.execute(f"""
            SELECT l.drama_id, l.idx, l.zh, l.en, d.title_en, d.title_zh
            FROM lines l JOIN dramas d ON d.id = l.drama_id
            WHERE (l.zh LIKE ? OR l.en LIKE ?){visible}
            LIMIT ?
        """, [like, like] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Accuracy benchmark cases & runs -- see benchmark.py for what actually
# runs a case; this is just the persistence for it, the same "lives in
# the same backed-up sqlite file as everything else" reasoning as the
# rest of this app.
# ---------------------------------------------------------------------------

def create_benchmark_case(label: str, stage: str, content_type: str, source_language: str = "zh",
                           input_filename: str = None, source_text: str = None,
                           reference_text: str = None) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO benchmark_cases (label, stage, content_type, source_language, "
            "input_filename, source_text, reference_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (label, stage, content_type, source_language, input_filename, source_text, reference_text,
             datetime.datetime.utcnow().isoformat()))
        conn.commit()
        case_id = cur.lastrowid
    return case_id


def list_benchmark_cases(stage: str = None):
    """All cases, or only those for one stage ('transcription'/'translation'/'ocr')."""
    with contextlib.closing(get_conn()) as conn:
        if stage:
            rows = conn.execute(
                "SELECT * FROM benchmark_cases WHERE stage = ? ORDER BY id", (stage,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM benchmark_cases ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def delete_benchmark_case(case_id: int):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM benchmark_cases WHERE id = ?", (case_id,))
        conn.commit()


def save_benchmark_run(case_id: int, result: dict, run_label: str = ""):
    """result: one entry from benchmark.run_suite()'s own return list --
    {"output_text", "score", "duration_seconds", "error", "cost_usd"}
    (cost_usd only present for translation-stage results)."""
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO benchmark_runs (case_id, run_label, output_text, score, duration_seconds, "
            "cost_usd, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, run_label, result.get("output_text", ""), result.get("score"),
             result.get("duration_seconds"), result.get("cost_usd", 0.0), result.get("error"),
             datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_benchmark_runs(case_id: int):
    """A case's own run history, oldest first -- the last two entries are
    what a "did this get better or worse" comparison reads."""
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_runs WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    return [dict(r) for r in rows]


def latest_benchmark_run_per_case(stage: str = None):
    """The most recent run for every case (optionally scoped to one
    stage) -- what a fresh "run everything" comparison is checked
    against. Returns {case_id: run_dict}."""
    with contextlib.closing(get_conn()) as conn:
        if stage:
            rows = conn.execute(
                "SELECT r.* FROM benchmark_runs r JOIN benchmark_cases c ON r.case_id = c.id "
                "WHERE c.stage = ? ORDER BY r.id", (stage,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM benchmark_runs ORDER BY id").fetchall()
    latest = {}
    for r in rows:
        latest[r["case_id"]] = dict(r)  # later rows overwrite earlier ones -- id order
    return latest


# ---------------------------------------------------------------------------
# Step 38: Benchmark Lab -- golden-set cases and persistent per-run records.
# services/benchmark_lab_service.py holds the logic; this is persistence.
# ---------------------------------------------------------------------------

def create_benchmark_lab_case(label: str, stage: str, source_language: str, source_text: str,
                              reference_text: str, tier: str, set_name: str,
                              content_type: str = "novel", origin_drama_id: int = None,
                              origin_line_id: int = None) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO benchmark_cases (label, stage, content_type, source_language, source_text, "
            "reference_text, tier, set_name, origin_drama_id, origin_line_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (label, stage, content_type, source_language, source_text, reference_text, tier,
             set_name, origin_drama_id, origin_line_id, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def update_benchmark_case_texts(case_id: int, source_text: str, reference_text: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE benchmark_cases SET source_text = ?, reference_text = ? WHERE id = ?",
                     (source_text, reference_text, case_id))
        conn.commit()


def get_benchmark_case(case_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM benchmark_cases WHERE id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def find_benchmark_case(set_name: str, source_text: str, origin_line_id: int = None):
    """An existing case in `set_name` with this source text (or, for a
    regression case, from this line) -- so an import or "add as regression
    test" run twice doesn't store the same example twice."""
    with contextlib.closing(get_conn()) as conn:
        if origin_line_id is not None:
            row = conn.execute(
                "SELECT * FROM benchmark_cases WHERE set_name = ? AND origin_line_id = ?",
                (set_name, origin_line_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM benchmark_cases WHERE set_name = ? AND source_text = ?",
                (set_name, source_text)).fetchone()
    return dict(row) if row else None


def create_benchmark_session(fields: dict) -> int:
    cols = ("label", "stage", "engine", "model", "prompt_version", "context_settings",
            "case_filter", "arena_group", "status", "case_count", "note")
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            f"INSERT INTO benchmark_sessions ({', '.join(cols)}, created_at) "
            f"VALUES ({', '.join('?' for _ in cols)}, ?)",
            tuple(fields.get(c) for c in cols) + (datetime.datetime.utcnow().isoformat(),))
        conn.commit()
        return cur.lastrowid


_BENCHMARK_SESSION_UPDATABLE = {"status", "scored_count", "passed_count", "error_count",
                                "aggregate_score", "avg_latency_seconds", "total_cost_usd",
                                "peak_vram_mb", "note", "finished_at", "case_count"}


def update_benchmark_session(session_id: int, **fields):
    bad = set(fields) - _BENCHMARK_SESSION_UPDATABLE
    if bad:
        raise ValueError(f"not updatable: {sorted(bad)}")
    if not fields:
        return
    with contextlib.closing(get_conn()) as conn:
        conn.execute(f"UPDATE benchmark_sessions SET {', '.join(f'{k} = ?' for k in fields)} "
                     "WHERE id = ?", tuple(fields.values()) + (session_id,))
        conn.commit()


def delete_benchmark_sessions(session_ids):
    with contextlib.closing(get_conn()) as conn:
        conn.executemany("DELETE FROM benchmark_sessions WHERE id = ?", [(i,) for i in session_ids])
        conn.commit()


def get_benchmark_session(session_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM benchmark_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def list_benchmark_sessions(limit: int = 50, stage: str = None):
    with contextlib.closing(get_conn()) as conn:
        if stage:
            rows = conn.execute("SELECT * FROM benchmark_sessions WHERE stage = ? "
                                "ORDER BY id DESC LIMIT ?", (stage, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM benchmark_sessions ORDER BY id DESC LIMIT ?",
                                (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_benchmark_result(session_id: int, case: dict, result: dict):
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO benchmark_results (session_id, case_id, case_label, output_text, score, "
            "metric, scorer, passed, duration_seconds, cost_usd, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, case.get("id"), case.get("label"), result.get("output_text", ""),
             result.get("score"), result.get("metric"), result.get("scorer"),
             None if result.get("passed") is None else int(bool(result["passed"])),
             result.get("duration_seconds"), result.get("cost_usd", 0.0), result.get("error"),
             datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_benchmark_results(session_id: int):
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute("SELECT * FROM benchmark_results WHERE session_id = ? ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(r) for r in rows]


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


# ---------------------------------------------------------------------------
# Step 133: auth storage (users, permissions, sessions, audit log).
# Plain data access only; policy lives in services/auth_service.py. Every
# writable column is whitelisted here because the UPDATE is built from keys.
# ---------------------------------------------------------------------------

_USER_WRITABLE = ("google_sub", "email", "display_name", "is_admin", "is_active",
                  "share_by_default")


def auth_create_user(email: str, display_name: str = "", is_admin: bool = False) -> int:
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            # share_by_default is explicit: a database from before new items
            # became private by default has the column with DEFAULT 1.
            "INSERT INTO users (email, display_name, is_admin, is_active, share_by_default, "
            "created_at) VALUES (?, ?, ?, 1, 0, ?)",
            (email, display_name or "", int(bool(is_admin)),
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        conn.commit()
        return cur.lastrowid


def auth_update_user(user_id: int, **fields) -> bool:
    bad = set(fields) - set(_USER_WRITABLE)
    if bad:
        raise ValueError(f"not a writable user field: {sorted(bad)}")
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)   # keys are whitelisted above
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
        conn.commit()
        return cur.rowcount > 0


def get_item_ownership(kind: str, item_id: int):
    """Auth slice B1: the ownership fields of one drama or series, or None
    if it doesn't exist. For a drama, `series_is_private` and
    `series_owner_user_id` are included."""
    with contextlib.closing(get_conn()) as conn:
        if kind == "drama":
            row = conn.execute(
                "SELECT d.id, d.owner_user_id, COALESCE(d.is_private, 0) AS is_private, d.series_id, "
                "COALESCE(s.is_private, 0) AS series_is_private, "
                "s.owner_user_id AS series_owner_user_id "
                "FROM dramas d LEFT JOIN series s ON s.id = d.series_id WHERE d.id = ?",
                (item_id,)).fetchone()
        elif kind == "series":
            row = conn.execute("SELECT id, owner_user_id, COALESCE(is_private, 0) AS is_private "
                               "FROM series WHERE id = ?", (item_id,)).fetchone()
        else:
            raise ValueError(f"unknown ownership kind: {kind!r}")
    return dict(row) if row else None


# A drama "owned by someone else" than series `s`: NULL-owned dramas belong
# to the PC owner, who sees everything anyway, so they never count.
_OTHERS_DRAMA_SQL = ("d.owner_user_id IS NOT NULL AND d.owner_user_id IS NOT s.owner_user_id")


def assign_drama_series(drama_id: int, series_id: int) -> bool:
    """Auth slice B1: moves a drama into a series in one conditional write,
    so it can't race the series being made private. Refused (False) when
    the series is private and the drama belongs to someone other than the
    series owner. Also clears the drama's own private flag (user decision
    4). False also for an unknown drama."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET series_id = ?, is_private = 0, updated_at = ? WHERE id = ? "
            "AND NOT EXISTS (SELECT 1 FROM series s, dramas d WHERE s.id = ? AND d.id = ? "
            f"AND COALESCE(s.is_private, 0) = 1 AND {_OTHERS_DRAMA_SQL})",
            (series_id, datetime.datetime.utcnow().isoformat(), drama_id, series_id, drama_id))
        conn.commit()
        return cur.rowcount > 0


def unassign_drama_series(drama_id: int) -> bool:
    """Parity P11: takes a drama out of its series in one write. A drama
    leaving a private series becomes private itself (assigning cleared its
    own flag, so otherwise it would turn visible to everyone). False for an
    unknown drama."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET is_private = CASE WHEN EXISTS (SELECT 1 FROM series s "
            "WHERE s.id = dramas.series_id AND COALESCE(s.is_private, 0) = 1) THEN 1 "
            "ELSE COALESCE(is_private, 0) END, series_id = NULL, updated_at = ? WHERE id = ?",
            (datetime.datetime.utcnow().isoformat(), drama_id))
        conn.commit()
        return cur.rowcount > 0


def set_item_private(kind: str, item_id: int, private: bool) -> bool:
    """Auth slice B1: field-scoped write of is_private only. Making private
    is one conditional write, so it can't race a drama being moved: a
    series is refused while it holds another user's drama, a drama while
    it is in a series. False when refused or the item doesn't exist."""
    if kind == "series":
        guard = ("NOT EXISTS (SELECT 1 FROM dramas d, series s WHERE s.id = series.id "
                 f"AND d.series_id = s.id AND {_OTHERS_DRAMA_SQL})")
        table = "series"
    elif kind == "drama":
        guard, table = "series_id IS NULL", "dramas"
    else:
        raise ValueError(f"unknown ownership kind: {kind!r}")
    sql = f"UPDATE {table} SET is_private = ? WHERE id = ?"
    if private:
        sql += f" AND {guard}"
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(sql, (int(bool(private)), item_id))
        conn.commit()
        return cur.rowcount > 0


def series_has_unowned_drama(series_id: int) -> bool:
    """Whether a series holds a drama with no owner (made at the PC)."""
    with contextlib.closing(get_conn()) as conn:
        return conn.execute("SELECT 1 FROM dramas WHERE series_id = ? AND owner_user_id IS NULL "
                            "LIMIT 1", (series_id,)).fetchone() is not None


def list_item_sharing(limit: int, offset: int):
    """Every series and drama with its sharing fields, for the admin
    Sharing screen: (total, rows). A series comes just before its dramas;
    groups are sorted by series name or solo drama title. Owner names come
    from users.display_name only (never the email)."""
    items = (
        "SELECT 'series' AS kind, 0 AS kind_order, s.id, s.name AS title, s.owner_user_id, "
        "COALESCE(s.is_private, 0) AS is_private, NULL AS series_id, NULL AS series_name, "
        "NULL AS series_is_private, s.name AS sort_key, s.id AS group_id FROM series s "
        "UNION ALL "
        "SELECT 'drama', 1, d.id, COALESCE(NULLIF(d.title_en, ''), NULLIF(d.title_zh, ''), ''), "
        "d.owner_user_id, COALESCE(d.is_private, 0), d.series_id, s.name, "
        "CASE WHEN s.id IS NULL THEN NULL ELSE COALESCE(s.is_private, 0) END, "
        "COALESCE(s.name, NULLIF(d.title_en, ''), d.title_zh, ''), d.series_id "
        "FROM dramas d LEFT JOIN series s ON s.id = d.series_id")
    with contextlib.closing(get_conn()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM ({items})").fetchone()[0]
        rows = conn.execute(
            f"SELECT i.*, u.id AS owner_found, u.display_name AS owner_display_name "
            f"FROM ({items}) i LEFT JOIN users u ON u.id = i.owner_user_id "
            "ORDER BY i.sort_key COLLATE NOCASE, i.sort_key, i.group_id IS NULL, i.group_id, "
            "i.kind_order, i.title COLLATE NOCASE, i.id LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
    return total, [dict(r) for r in rows]


def auth_get_user(user_id: int):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def auth_get_user_by_email(email: str):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def auth_get_user_by_sub(google_sub: str):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        return dict(row) if row else None


def auth_list_users():
    with contextlib.closing(get_conn()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]


def auth_get_permissions(user_id: int):
    with contextlib.closing(get_conn()) as conn:
        return sorted(r[0] for r in conn.execute(
            "SELECT permission FROM user_permissions WHERE user_id = ?", (user_id,)).fetchall())


def auth_grant_permission(user_id: int, permission: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("INSERT OR IGNORE INTO user_permissions (user_id, permission) VALUES (?, ?)",
                     (user_id, permission))
        conn.commit()


def auth_revoke_permission(user_id: int, permission: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM user_permissions WHERE user_id = ? AND permission = ?",
                     (user_id, permission))
        conn.commit()


def auth_insert_session(id_hash: str, user_id: int, created_at: float, expires_at: float,
                        device_label: str, ip_prefix: str, csrf_hash: str) -> int:
    """The raw user agent is not stored: only the coarse `device_label`."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, last_seen_at, "
            "user_agent_short, device_label, ip_prefix, csrf_hash) "
            "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)",
            (id_hash, user_id, created_at, expires_at, created_at, device_label,
             ip_prefix, csrf_hash))
        conn.commit()
        return cur.lastrowid


def auth_rotate_session(session_id: int, user_id: int, id_hash: str, csrf_hash: str,
                        now: float, ip_prefix: str):
    """Replaces the user's session `session_id` with a new row under new
    hashes, in one transaction. The new row keeps the old one's sign-in time,
    expiry and device label, so rotating never extends a session. Returns the
    new id, or None if that session is already gone."""
    with contextlib.closing(get_conn()) as conn:
        with conn:
            old = conn.execute(
                "SELECT created_at, expires_at, device_label FROM auth_sessions "
                "WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
            if old is None:
                return None
            conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))
            cur = conn.execute(
                "INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, "
                "last_seen_at, user_agent_short, device_label, ip_prefix, csrf_hash) "
                "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)",
                (id_hash, user_id, old["created_at"], old["expires_at"], now,
                 old["device_label"] or "", ip_prefix, csrf_hash))
            return cur.lastrowid


def auth_get_session_by_hash(id_hash: str):
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM auth_sessions WHERE id_hash = ?", (id_hash,)).fetchone()
        return dict(row) if row else None


def auth_touch_session(session_id: int, now: float):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (now, session_id))
        conn.commit()


def auth_delete_session(session_id: int, user_id: int = None) -> bool:
    """`user_id`, when given, scopes the delete to that user's own session."""
    sql, args = "DELETE FROM auth_sessions WHERE id = ?", [session_id]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount > 0


def auth_delete_user_sessions(user_id: int, except_id: int = None) -> int:
    """Every session of the user, or every one but `except_id` (the caller's own)."""
    sql, args = "DELETE FROM auth_sessions WHERE user_id = ?", [user_id]
    if except_id is not None:
        sql += " AND id != ?"
        args.append(except_id)
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount


def auth_delete_stale_sessions(now: float, idle_cutoff: float) -> int:
    """Deletes every session a lookup would refuse anyway: past its absolute
    expiry, idle since `idle_cutoff` or earlier, or held by a deactivated or
    missing user. Returns how many rows went."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ? OR last_seen_at <= ? "
            "OR user_id NOT IN (SELECT id FROM users WHERE is_active = 1)",
            (now, idle_cutoff))
        conn.commit()
        return cur.rowcount


def auth_list_sessions(user_id: int):
    """Never selects id_hash or csrf_hash."""
    with contextlib.closing(get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, user_id, created_at, expires_at, last_seen_at, user_agent_short, "
            "device_label, ip_prefix FROM auth_sessions WHERE user_id = ? ORDER BY id",
            (user_id,)).fetchall()]


def auth_insert_audit(user_id, action: str, detail_redacted: str):
    with contextlib.closing(get_conn()) as conn:
        conn.execute("INSERT INTO audit_log (ts, user_id, action, detail_redacted) "
                     "VALUES (?, ?, ?, ?)",
                     (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), user_id, action,
                      detail_redacted))
        conn.commit()


def auth_list_audit(limit: int = 100, before_id: int = None, action: str = None,
                    user_id: int = None):
    """Newest first. `before_id` pages back (rows with a smaller id);
    `action` and `user_id` are exact-match filters."""
    where, args = [], []
    if before_id is not None:
        where.append("id < ?")
        args.append(int(before_id))
    if action is not None:
        where.append("action = ?")
        args.append(action)
    if user_id is not None:
        where.append("user_id = ?")
        args.append(int(user_id))
    sql = "SELECT id, ts, user_id, action, detail_redacted FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with contextlib.closing(get_conn()) as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?",
                                              (*args, int(limit))).fetchall()]


def auth_list_audit_actions(limit: int = 200):
    with contextlib.closing(get_conn()) as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action LIMIT ?",
            (int(limit),)).fetchall()]


def auth_deactivate_user_keeping_an_admin(user_id: int) -> bool:
    """Sets is_active=0 unless that would leave no active admin. One
    statement, so two admins deactivating each other at once can't both
    succeed. False when refused or the user doesn't exist."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            # Truthiness as auth_service reads the flags: NULL is off, any
            # non-zero value is on.
            "UPDATE users SET is_active = 0 WHERE id = ? AND (COALESCE(is_admin, 0) = 0 "
            "OR COALESCE(is_active, 0) = 0 OR EXISTS (SELECT 1 FROM users o WHERE o.id != ? "
            "AND COALESCE(o.is_admin, 0) != 0 AND COALESCE(o.is_active, 0) != 0))",
            (user_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def auth_revoke_admin_keeping_an_admin(user_id: int) -> bool:
    """Sets is_admin=0 on an admin unless that would leave no active admin.
    One statement, so two admins demoting each other at once can't both
    succeed. False when refused, not an admin, or the user doesn't exist."""
    with contextlib.closing(get_conn()) as conn:
        cur = conn.execute(
            # Flag truthiness as in auth_deactivate_user_keeping_an_admin.
            "UPDATE users SET is_admin = 0 WHERE id = ? AND COALESCE(is_admin, 0) != 0 "
            "AND (COALESCE(is_active, 0) = 0 OR EXISTS (SELECT 1 FROM users o WHERE o.id != ? "
            "AND COALESCE(o.is_admin, 0) != 0 AND COALESCE(o.is_active, 0) != 0))",
            (user_id, user_id))
        conn.commit()
        return cur.rowcount > 0
