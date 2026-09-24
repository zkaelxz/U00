"""
db.py -- local SQLite-backed library for the subtitler app.

Everything lives in ./library/library.db plus per-drama folders under
./library/dramas/<id>/ for audio and novel reference files. This is
all local -- nothing leaves your machine except the actual translation
API calls.
"""

import os
import sqlite3
import datetime
import json
from dataclasses import dataclass, field
from typing import Optional, List

LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library")
DRAMAS_DIR = os.path.join(LIBRARY_DIR, "dramas")
DB_PATH = os.path.join(LIBRARY_DIR, "library.db")

os.makedirs(DRAMAS_DIR, exist_ok=True)


def configure_library_dir(path: str):
    """Redirects the library to a different directory -- used by the
    test suite to point at a temp directory instead of the real
    library, so tests never touch your actual data. Not something
    you'd normally call yourself."""
    global LIBRARY_DIR, DRAMAS_DIR, DB_PATH
    LIBRARY_DIR = path
    DRAMAS_DIR = os.path.join(LIBRARY_DIR, "dramas")
    DB_PATH = os.path.join(LIBRARY_DIR, "library.db")
    os.makedirs(DRAMAS_DIR, exist_ok=True)


# Connections opened but not yet closed. Under normal flow a function
# opens one and closes it before returning, so this stays empty between
# calls. If a statement raises between get_conn() and conn.close() --
# a foreign-key violation, a bad parameter type -- the close is skipped
# and the connection leaks. Under WAL that leaked reader/writer blocks
# every subsequent write with "database is locked", so a single failed
# call would poison the whole session until restart.
#
# Verified safe: no db function calls another db function while holding
# a connection, so anything still open when a new one is requested is
# by definition a leak from a previous failure.
_open_connections = []


class _TrackedConnection(sqlite3.Connection):
    """Deregisters itself on close, so a normal call leaves nothing behind
    for the next get_conn() to clean up. sqlite3.Connection forbids
    assigning to .close, so subclassing via connect(factory=...) is the
    supported way to hook it."""

    def close(self):
        try:
            _open_connections.remove(self)
        except ValueError:
            pass
        super().close()


def _close_leaked_connections():
    while _open_connections:
        leaked = _open_connections.pop()
        try:
            sqlite3.Connection.close(leaked)
        except Exception:
            pass


def get_conn():
    _close_leaked_connections()
    conn = sqlite3.connect(DB_PATH, factory=_TrackedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _open_connections.append(conn)
    return conn


def init_db():
    conn = get_conn()
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
        source_language TEXT DEFAULT 'zh',        -- 'zh', 'ja', or 'ko'
        media_type TEXT DEFAULT 'audio_drama',    -- 'audio_drama', 'video_drama', 'novel',
                                                   -- 'manhwa', 'manga', 'manhua', 'asmr', 'other'
        series_id INTEGER,        -- shares a glossary across multiple dramas of the same series
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
        tts_voice TEXT,          -- fallback free TTS voice for this character
        ref_audio_filename TEXT, -- reference clip for voice cloning (relative to drama dir)
        ref_text TEXT,           -- transcript of what's said in the reference clip
        elevenlabs_voice_id TEXT,-- cloned voice ID from ElevenLabs, if used instead of F5-TTS
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
        FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
        UNIQUE(series_id, term_original)
    );

    CREATE TABLE IF NOT EXISTS translation_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drama_id INTEGER NOT NULL,
        line_idx INTEGER,
        term TEXT,
        note_type TEXT,           -- idiom, wordplay, name_meaning, allusion, cultural, honorific
        note TEXT,
        created_at TEXT,
        FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
        UNIQUE(drama_id, line_idx, term)
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

    CREATE TABLE IF NOT EXISTS line_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drama_id INTEGER NOT NULL,
        label TEXT,               -- e.g. 'before force re-translate', 'before merge'
        snapshot_json TEXT,       -- JSON-encoded list of line dicts
        created_at TEXT,
        FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS progress (
        drama_id INTEGER PRIMARY KEY,
        last_line_idx INTEGER DEFAULT 0,
        audio_position_seconds REAL DEFAULT 0,
        last_page INTEGER DEFAULT 1,
        percent_complete REAL DEFAULT 0,
        last_accessed_at TEXT,
        FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS reading_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        drama_id INTEGER NOT NULL,
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

    CREATE INDEX IF NOT EXISTS idx_lines_drama ON lines(drama_id);
    CREATE INDEX IF NOT EXISTS idx_characters_drama ON characters(drama_id);
    CREATE INDEX IF NOT EXISTS idx_pages_drama ON pages(drama_id);
    CREATE INDEX IF NOT EXISTS idx_bubbles_page ON bubbles(page_id);
    CREATE INDEX IF NOT EXISTS idx_known_titles_lang ON known_titles(language);
    CREATE INDEX IF NOT EXISTS idx_glossary_series ON glossary_terms(series_id);
    CREATE INDEX IF NOT EXISTS idx_series_characters_series ON series_characters(series_id);
    CREATE INDEX IF NOT EXISTS idx_vocab_drama ON vocab_lookups(drama_id);
    CREATE INDEX IF NOT EXISTS idx_usage_drama ON usage_log(drama_id);
    CREATE INDEX IF NOT EXISTS idx_history_drama ON line_history(drama_id);
    CREATE INDEX IF NOT EXISTS idx_reading_history_drama ON reading_history(drama_id);
    CREATE INDEX IF NOT EXISTS idx_versions_drama ON translation_versions(drama_id);
    CREATE INDEX IF NOT EXISTS idx_wiki_drama ON wiki_entries(drama_id);
    CREATE INDEX IF NOT EXISTS idx_edits_drama ON edit_samples(drama_id);
    """)
    # Lightweight migrations for DBs created before these columns existed
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()}
    if "speaker" not in existing_cols:
        conn.execute("ALTER TABLE lines ADD COLUMN speaker TEXT")
    if "dub_filename" not in existing_cols:
        conn.execute("ALTER TABLE lines ADD COLUMN dub_filename TEXT")
    if "flag" not in existing_cols:
        # A key from translate_engines.FLAG_REASONS, set by flag_uncertain_lines()
        # -- the review queue for a long file, so a person doesn't have to
        # scan every line to find the handful worth a second look.
        conn.execute("ALTER TABLE lines ADD COLUMN flag TEXT")
    if "flag_note" not in existing_cols:
        conn.execute("ALTER TABLE lines ADD COLUMN flag_note TEXT")
    drama_cols = {r[1] for r in conn.execute("PRAGMA table_info(dramas)").fetchall()}
    if "translation_engine" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN translation_engine TEXT DEFAULT 'claude'")
    if "content_mode" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN content_mode TEXT DEFAULT 'audio_drama'")
    if "source_video_filename" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN source_video_filename TEXT")
    if "source_language" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN source_language TEXT DEFAULT 'zh'")
    if "media_type" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN media_type TEXT DEFAULT 'audio_drama'")
    if "series_id" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN series_id INTEGER")
    if "updated_at" not in drama_cols:
        conn.execute("ALTER TABLE dramas ADD COLUMN updated_at TEXT")
    for col, coltype in [("last_translate_errors", "TEXT"),
                          ("author_romanized", "TEXT"), ("studio_romanized", "TEXT"),
                          ("voice_actors_romanized", "TEXT"), ("director_romanized", "TEXT"),
                          ("cover_art_filename", "TEXT"), ("genre", "TEXT"),
                          ("publication_status", "TEXT"), ("chapter_count", "INTEGER"),
                          ("custom_tags", "TEXT"), ("personal_notes", "TEXT"),
                          # Recognition/alignment pipeline choices -- previously only
                          # lived in Streamlit session_state, which resets on every
                          # app restart, so "I don't have a transcript" (and the
                          # model/backend picks) had to be re-selected every time.
                          ("transcript_mode", "TEXT"), ("whisper_size", "TEXT"),
                          ("alignment_method", "TEXT"), ("asr_backend_choice", "TEXT")]:
        if col not in drama_cols:
            conn.execute(f"ALTER TABLE dramas ADD COLUMN {col} {coltype}")
    char_cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
    if "ref_audio_filename" not in char_cols:
        conn.execute("ALTER TABLE characters ADD COLUMN ref_audio_filename TEXT")
    if "ref_text" not in char_cols:
        conn.execute("ALTER TABLE characters ADD COLUMN ref_text TEXT")
    if "elevenlabs_voice_id" not in char_cols:
        conn.execute("ALTER TABLE characters ADD COLUMN elevenlabs_voice_id TEXT")
    if "series_character_id" not in char_cols:
        # Links this drama's speaker to a persistent series_characters row,
        # so renaming/updating the series-level character (once) reflects
        # everywhere it's been assigned, instead of needing a per-drama edit.
        conn.execute("ALTER TABLE characters ADD COLUMN series_character_id INTEGER")
    gloss_cols = {r[1] for r in conn.execute("PRAGMA table_info(glossary_terms)").fetchall()}
    if "category" not in gloss_cols:
        conn.execute("ALTER TABLE glossary_terms ADD COLUMN category TEXT")
    if "policy" not in gloss_cols:
        conn.execute("ALTER TABLE glossary_terms ADD COLUMN policy TEXT")
    if "enforce_exact" not in gloss_cols:
        conn.execute("ALTER TABLE glossary_terms ADD COLUMN enforce_exact INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def drama_dir(drama_id: int) -> str:
    d = os.path.join(DRAMAS_DIR, str(drama_id))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Dramas CRUD
# ---------------------------------------------------------------------------

def create_drama(**fields) -> int:
    conn = get_conn()
    fields.setdefault("status", "not started")
    now = datetime.datetime.utcnow().isoformat()
    fields["created_at"] = now
    fields["updated_at"] = now
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO dramas ({cols}) VALUES ({placeholders})",
                        list(fields.values()))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_drama(drama_id: int, **fields):
    if not fields:
        return
    fields["updated_at"] = datetime.datetime.utcnow().isoformat()
    conn = get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE dramas SET {set_clause} WHERE id = ?",
                 list(fields.values()) + [drama_id])
    conn.commit()
    conn.close()


def delete_drama(drama_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM dramas WHERE id = ?", (drama_id,))
    conn.commit()
    conn.close()
    import shutil
    d = os.path.join(DRAMAS_DIR, str(drama_id))
    if os.path.isdir(d):
        shutil.rmtree(d)


def get_drama(drama_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM dramas WHERE id = ?", (drama_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_dramas(search: str = "", studio: str = "", author: str = "",
                 voice_actor: str = "", status: str = "", source_language: str = "",
                 media_type: str = ""):
    conn = get_conn()
    query = "SELECT * FROM dramas WHERE 1=1"
    params = []
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
    conn.close()
    return [dict(r) for r in rows]


def distinct_values(column: str) -> List[str]:
    conn = get_conn()
    rows = conn.execute(f"SELECT DISTINCT {column} FROM dramas WHERE {column} IS NOT NULL AND {column} != ''").fetchall()
    conn.close()
    return sorted({r[0] for r in rows})


def distinct_voice_actors() -> List[str]:
    """voice_actors is comma-separated per row -- split and dedupe."""
    conn = get_conn()
    rows = conn.execute("SELECT voice_actors FROM dramas WHERE voice_actors IS NOT NULL AND voice_actors != ''").fetchall()
    conn.close()
    names = set()
    for r in rows:
        for name in r[0].split(","):
            name = name.strip()
            if name:
                names.add(name)
    return sorted(names)


# ---------------------------------------------------------------------------
# Lines CRUD
# ---------------------------------------------------------------------------

def save_lines(drama_id: int, lines):
    """Replaces all lines for a drama with the given list of Line-like objects.

    Wrapped in an explicit transaction: this deletes every existing line
    before re-inserting, so a failure partway through would otherwise
    destroy translation work that cost real money to produce. On any
    error the delete is rolled back and the previous lines survive
    intact. Do not remove the rollback -- sqlite's implicit transaction
    happens to cover this today, but that's a side effect of connection
    lifecycle, not a guarantee."""
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM lines WHERE drama_id = ?", (drama_id,))
        conn.executemany(
            "INSERT INTO lines (drama_id, idx, start, end, zh, en, speaker, dub_filename, flag, flag_note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(drama_id, ln.idx, ln.start, ln.end, ln.zh, ln.en,
              getattr(ln, "speaker", None), getattr(ln, "dub_filename", None),
              getattr(ln, "flag", None), getattr(ln, "flag_note", "")) for ln in lines]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    conn.close()


def load_lines(drama_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT idx, start, end, zh, en, speaker, dub_filename, flag, flag_note "
        "FROM lines WHERE drama_id = ? ORDER BY idx",
        (drama_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Characters CRUD (speaker-label -> character name / voice actor / TTS voice)
# ---------------------------------------------------------------------------

def upsert_character(drama_id: int, speaker_label: str, character_name: str = None,
                      voice_actor: str = None, tts_voice: str = None,
                      ref_audio_filename: str = None, ref_text: str = None,
                      elevenlabs_voice_id: str = None, series_character_id: int = None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO characters (drama_id, speaker_label, character_name, voice_actor, tts_voice,
                                 ref_audio_filename, ref_text, elevenlabs_voice_id, series_character_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(drama_id, speaker_label) DO UPDATE SET
            character_name = COALESCE(excluded.character_name, characters.character_name),
            voice_actor = COALESCE(excluded.voice_actor, characters.voice_actor),
            tts_voice = COALESCE(excluded.tts_voice, characters.tts_voice),
            ref_audio_filename = COALESCE(excluded.ref_audio_filename, characters.ref_audio_filename),
            ref_text = COALESCE(excluded.ref_text, characters.ref_text),
            elevenlabs_voice_id = COALESCE(excluded.elevenlabs_voice_id, characters.elevenlabs_voice_id),
            series_character_id = COALESCE(excluded.series_character_id, characters.series_character_id)
    """, (drama_id, speaker_label, character_name, voice_actor, tts_voice, ref_audio_filename, ref_text,
          elevenlabs_voice_id, series_character_id))
    conn.commit()
    conn.close()


def list_characters(drama_id: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM characters WHERE drama_id = ? ORDER BY speaker_label", (drama_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Series-level characters -- persist across every drama in a series (a
# streamer's whole archive, or a book series), independent of any one
# drama's own diarization labels. See the series_characters table comment
# in init_db() for why this has to be a separate concept from `characters`.
# ---------------------------------------------------------------------------

def upsert_series_character(series_id: int, character_name: str, aliases: str = "",
                             notes: str = ""):
    """Creates or updates a named character for a series. Matching is on
    (series_id, character_name) -- renaming isn't done through this
    function (it would create a new row); use rename_series_character."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO series_characters (series_id, character_name, aliases, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(series_id, character_name) DO UPDATE SET
            aliases = excluded.aliases,
            notes = excluded.notes
    """, (series_id, character_name, aliases, notes, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def rename_series_character(series_character_id: int, new_name: str):
    """Renaming updates the one series_characters row -- every drama's
    `characters` row linked to it via series_character_id picks up the
    new name automatically next time it's displayed (see
    list_characters_with_series_names), rather than needing a per-drama
    edit for a name correction that should apply everywhere."""
    conn = get_conn()
    conn.execute("UPDATE series_characters SET character_name = ? WHERE id = ?",
                 (new_name, series_character_id))
    conn.commit()
    conn.close()


def list_series_characters(series_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM series_characters WHERE series_id = ? ORDER BY character_name",
        (series_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_series_character(series_character_id: int):
    """Only removes the series-level record. Any drama's `characters` row
    still pointing at it keeps its own character_name (already copied in
    at assignment time) -- it just stops being linked for future rename
    propagation, rather than losing the name it already had."""
    conn = get_conn()
    conn.execute("UPDATE characters SET series_character_id = NULL WHERE series_character_id = ?",
                 (series_character_id,))
    conn.execute("DELETE FROM series_characters WHERE id = ?", (series_character_id,))
    conn.commit()
    conn.close()


def list_characters_with_series_names(drama_id: int):
    """Like list_characters, but a character linked to a series_characters
    row shows that row's current character_name (so a series-level rename
    reflects here immediately) instead of the possibly-stale name copied
    into `characters` at the time it was first assigned."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT c.*, sc.character_name AS series_character_name
        FROM characters c
        LEFT JOIN series_characters sc ON sc.id = c.series_character_id
        WHERE c.drama_id = ?
        ORDER BY c.speaker_label
    """, (drama_id,)).fetchall()
    conn.close()
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
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO pages (drama_id, idx, filename, width, height) VALUES (?, ?, ?, ?, ?)",
        (drama_id, idx, filename, width, height))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_page(page_id: int, **fields):
    if not fields:
        return
    conn = get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE pages SET {set_clause} WHERE id = ?", list(fields.values()) + [page_id])
    conn.commit()
    conn.close()


def list_pages(drama_id: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM pages WHERE drama_id = ? ORDER BY idx", (drama_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_page(page_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def save_bubbles(page_id: int, bubbles):
    """bubbles: list of dicts with x,y,w,h,source_text,translated_text,font_size,skip.
    Replaces all bubbles for this page."""
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM bubbles WHERE page_id = ?", (page_id,))
        conn.executemany(
            "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, font_size, skip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(page_id, i, b["x"], b["y"], b["w"], b["h"], b.get("source_text", ""),
              b.get("translated_text", ""), b.get("font_size", 18), int(b.get("skip", False)))
             for i, b in enumerate(bubbles)]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_bubbles(page_id: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM bubbles WHERE page_id = ? ORDER BY idx", (page_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Known titles (searchable discovery library, separate from your working
# drama catalog -- import a known_title into `dramas` when you're ready
# to actually work on it)
# ---------------------------------------------------------------------------

def create_known_title(**fields) -> int:
    conn = get_conn()
    fields.setdefault("source_name", "manual")
    fields["created_at"] = datetime.datetime.utcnow().isoformat()
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO known_titles ({cols}) VALUES ({placeholders})", list(fields.values()))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def list_known_titles(search: str = "", language: str = "", media_type: str = "", source_name: str = ""):
    conn = get_conn()
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
    conn.close()
    return [dict(r) for r in rows]


def get_known_title(title_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM known_titles WHERE id = ?", (title_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_known_title(title_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM known_titles WHERE id = ?", (title_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Series & shared glossaries (terms consistent across multiple dramas of
# the same series, e.g. book 1/2/3 of the same title by the same author)
# ---------------------------------------------------------------------------

def get_or_create_series(name: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
    if row:
        conn.close()
        return row["id"]
    cur = conn.execute("INSERT INTO series (name, created_at) VALUES (?, ?)",
                        (name, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def list_series():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM series ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_glossary_term(series_id: int, term_original: str, term_translation: str, notes: str = "",
                          category: str = None, policy: str = None, enforce_exact: bool = False):
    conn = get_conn()
    conn.execute("""
        INSERT INTO glossary_terms (series_id, term_original, term_translation, notes,
                                     category, policy, enforce_exact)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(series_id, term_original) DO UPDATE SET
            term_translation = excluded.term_translation,
            notes = excluded.notes,
            category = COALESCE(excluded.category, glossary_terms.category),
            policy = COALESCE(excluded.policy, glossary_terms.policy),
            enforce_exact = excluded.enforce_exact
    """, (series_id, term_original, term_translation, notes, category, policy, int(enforce_exact)))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Translation notes (idioms, wordplay, meaningful names, allusions)
# ---------------------------------------------------------------------------

def save_translation_notes(drama_id: int, notes):
    """notes: list of {line_idx, term, note_type, note}. Existing notes
    for the same (drama, line, term) are updated rather than duplicated."""
    conn = get_conn()
    now = datetime.datetime.utcnow().isoformat()
    for n in notes:
        conn.execute("""
            INSERT INTO translation_notes (drama_id, line_idx, term, note_type, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, line_idx, term) DO UPDATE SET
                note_type = excluded.note_type,
                note = excluded.note
        """, (drama_id, n.get("line_idx"), n.get("term", ""), n.get("note_type", "cultural"),
              n.get("note", ""), now))
    conn.commit()
    conn.close()


def list_translation_notes(drama_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM translation_notes WHERE drama_id = ? ORDER BY line_idx", (drama_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_translation_note(note_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM translation_notes WHERE id = ?", (note_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Progress tracking & reading history
# ---------------------------------------------------------------------------

def save_progress(drama_id: int, last_line_idx: int = None, audio_position_seconds: float = None,
                   last_page: int = None, percent_complete: float = None,
                   record_history: bool = True):
    """Upserts the resume point for a drama. Only the fields you pass are
    updated, so saving an audio position doesn't clobber the reading page."""
    now = datetime.datetime.utcnow().isoformat()
    conn = get_conn()
    # Raw values (possibly NULL) go in deliberately: coalescing them here would
    # make excluded.<col> a real 0 and defeat the COALESCE in the conflict clause,
    # so a partial update would silently zero out the fields it didn't touch.
    conn.execute("""
        INSERT INTO progress (drama_id, last_line_idx, audio_position_seconds, last_page,
                               percent_complete, last_accessed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(drama_id) DO UPDATE SET
            last_line_idx = COALESCE(excluded.last_line_idx, progress.last_line_idx),
            audio_position_seconds = COALESCE(excluded.audio_position_seconds, progress.audio_position_seconds),
            last_page = COALESCE(excluded.last_page, progress.last_page),
            percent_complete = COALESCE(excluded.percent_complete, progress.percent_complete),
            last_accessed_at = excluded.last_accessed_at
    """, (drama_id, last_line_idx, audio_position_seconds, last_page, percent_complete, now))
    if record_history and (last_line_idx is not None or percent_complete is not None):
        conn.execute(
            "INSERT INTO reading_history (drama_id, line_idx, percent_complete, accessed_at) "
            "VALUES (?, ?, ?, ?)",
            (drama_id, last_line_idx, percent_complete, now))
    conn.commit()
    conn.close()


def get_progress(drama_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM progress WHERE drama_id = ?", (drama_id,)).fetchone()
    conn.close()
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


def list_continue_reading(limit: int = 8):
    """Dramas with partial progress, most recently touched first -- the
    'Continue' shelf. Excludes anything finished (>=99%) or untouched."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT d.*, p.last_line_idx, p.audio_position_seconds, p.last_page,
               p.percent_complete, p.last_accessed_at
        FROM progress p JOIN dramas d ON d.id = p.drama_id
        WHERE p.percent_complete > 0 AND p.percent_complete < 99
        ORDER BY p.last_accessed_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_reading_history(drama_id: int = None, limit: int = 50):
    conn = get_conn()
    if drama_id:
        rows = conn.execute(
            "SELECT h.*, d.title_en, d.title_zh FROM reading_history h "
            "JOIN dramas d ON d.id = h.drama_id WHERE h.drama_id = ? "
            "ORDER BY h.accessed_at DESC LIMIT ?", (drama_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT h.*, d.title_en, d.title_zh FROM reading_history h "
            "JOIN dramas d ON d.id = h.drama_id ORDER BY h.accessed_at DESC LIMIT ?",
            (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_reading_history(drama_id: int = None):
    conn = get_conn()
    if drama_id:
        conn.execute("DELETE FROM reading_history WHERE drama_id = ?", (drama_id,))
    else:
        conn.execute("DELETE FROM reading_history")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Custom tags
# ---------------------------------------------------------------------------

def distinct_custom_tags():
    """custom_tags is comma-separated per drama -- split and dedupe."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT custom_tags FROM dramas WHERE custom_tags IS NOT NULL AND custom_tags != ''").fetchall()
    conn.close()
    tags = set()
    for r in rows:
        for t in r[0].split(","):
            if t.strip():
                tags.add(t.strip())
    return sorted(tags)


# ---------------------------------------------------------------------------
# Translation versions -- keep alternate translations side by side
# ---------------------------------------------------------------------------

def save_translation_version(drama_id: int, lines, label: str, engine: str = "",
                              model: str = "", make_active: bool = False):
    """Stores a complete translation as a named version, so re-translating
    with a different model never destroys the previous attempt."""
    payload = [{"idx": ln.idx, "start": ln.start, "end": ln.end, "zh": ln.zh, "en": ln.en,
                "speaker": getattr(ln, "speaker", None)} for ln in lines]
    conn = get_conn()
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
    conn.close()
    return new_id


def list_translation_versions(drama_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, drama_id, label, engine, model, is_active, created_at "
        "FROM translation_versions WHERE drama_id = ? ORDER BY created_at DESC",
        (drama_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_translation_version(version_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM translation_versions WHERE id = ?", (version_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["lines"] = json.loads(d["lines_json"]) if d["lines_json"] else []
    return d


def set_active_translation_version(drama_id: int, version_id: int):
    conn = get_conn()
    conn.execute("UPDATE translation_versions SET is_active = 0 WHERE drama_id = ?", (drama_id,))
    conn.execute("UPDATE translation_versions SET is_active = 1 WHERE id = ?", (version_id,))
    conn.commit()
    conn.close()


def delete_translation_version(version_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM translation_versions WHERE id = ?", (version_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Universe wiki -- accumulating encyclopedia, spoiler-bounded
# ---------------------------------------------------------------------------

def upsert_wiki_entry(drama_id: int, entry_type: str, name: str, description: str = None,
                       aliases: str = None, attributes: dict = None,
                       first_seen_line_idx: int = None, known_through_line_idx: int = None):
    """Adds or updates an encyclopedia entry. known_through_line_idx records
    how far into the story this entry was built from -- the spoiler boundary,
    so an entry is never shown to someone who hasn't read that far."""
    conn = get_conn()
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
    conn.close()


def list_wiki_entries(drama_id: int, entry_type: str = None, spoiler_limit_line_idx: int = None):
    """spoiler_limit_line_idx: when set, only returns entries first introduced
    at or before that line -- so the wiki never reveals something you haven't
    reached yet."""
    conn = get_conn()
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
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["attributes"] = json.loads(d["attributes_json"]) if d["attributes_json"] else {}
        except (json.JSONDecodeError, TypeError):
            d["attributes"] = {}
        out.append(d)
    return out


def delete_wiki_entry(entry_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM wiki_entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()


def clear_wiki(drama_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM wiki_entries WHERE drama_id = ?", (drama_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Adaptive style -- learn translation preferences from your edits
# ---------------------------------------------------------------------------

def record_edit_sample(drama_id: int, zh: str, ai_version: str, user_version: str):
    """Records a line you rewrote, so the system can learn what you
    consistently change. Only stores genuine edits, not untouched lines."""
    if not user_version or ai_version.strip() == user_version.strip():
        return
    conn = get_conn()
    conn.execute(
        "INSERT INTO edit_samples (drama_id, zh, ai_version, user_version, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (drama_id, zh, ai_version, user_version, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def list_edit_samples(drama_id: int = None, limit: int = 100):
    conn = get_conn()
    if drama_id:
        rows = conn.execute(
            "SELECT * FROM edit_samples WHERE drama_id = ? ORDER BY created_at DESC LIMIT ?",
            (drama_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM edit_samples ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_style_profile(scope: str, profile: dict, sample_count: int = 0):
    conn = get_conn()
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
    conn.close()


def get_style_profile(scope: str = "global"):
    conn = get_conn()
    row = conn.execute("SELECT * FROM style_profile WHERE scope = ?", (scope,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else {}
    except (json.JSONDecodeError, TypeError):
        d["profile"] = {}
    return d


def clear_edit_samples(drama_id: int = None):
    conn = get_conn()
    if drama_id:
        conn.execute("DELETE FROM edit_samples WHERE drama_id = ?", (drama_id,))
    else:
        conn.execute("DELETE FROM edit_samples")
    conn.commit()
    conn.close()


def list_glossary_terms(series_id: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM glossary_terms WHERE series_id = ? ORDER BY term_original",
                         (series_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_glossary_term(term_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM glossary_terms WHERE id = ?", (term_id,))
    conn.commit()
    conn.close()


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
        {"idx": ln.idx, "start": ln.start, "end": ln.end, "zh": ln.zh, "en": ln.en,
         "speaker": getattr(ln, "speaker", None), "dub_filename": getattr(ln, "dub_filename", None)}
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
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, drama_id, label, created_at FROM line_history WHERE drama_id = ? ORDER BY created_at DESC",
        (drama_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_line_history_snapshot(history_id: int):
    """Returns the list of line dicts from a saved snapshot, ready to
    be turned back into Line objects and saved via save_lines()."""
    conn = get_conn()
    row = conn.execute("SELECT snapshot_json FROM line_history WHERE id = ?", (history_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["snapshot_json"])


# ---------------------------------------------------------------------------
# Vocab lookups (persisted Reader click-to-define history, for export)
# ---------------------------------------------------------------------------

def save_vocab_lookup(drama_id: int, word: str, reading: str, definitions, language: str,
                       first_seen_line_idx: int = None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO vocab_lookups (drama_id, word, reading, definitions, language,
                                    first_seen_line_idx, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(drama_id, word) DO NOTHING
    """, (drama_id, word, reading, json.dumps(definitions, ensure_ascii=False), language,
          first_seen_line_idx, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def list_vocab_lookups(drama_id: int = None):
    conn = get_conn()
    if drama_id:
        rows = conn.execute("SELECT * FROM vocab_lookups WHERE drama_id = ? ORDER BY created_at",
                             (drama_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vocab_lookups ORDER BY created_at").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["definitions"] = json.loads(d["definitions"]) if d["definitions"] else []
        except (json.JSONDecodeError, TypeError):
            d["definitions"] = []
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Usage/cost logging
# ---------------------------------------------------------------------------

def log_usage(drama_id: int, engine: str, model: str, operation: str,
              input_tokens: int = 0, output_tokens: int = 0, estimated_cost_usd: float = 0.0):
    conn = get_conn()
    conn.execute("""
        INSERT INTO usage_log (drama_id, engine, model, operation, input_tokens,
                                output_tokens, estimated_cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (drama_id, engine, model, operation, input_tokens, output_tokens,
          estimated_cost_usd, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_usage_summary(drama_id: int = None):
    """Returns {"input_tokens", "output_tokens", "estimated_cost_usd", "call_count"} --
    totals for one drama, or the whole library if drama_id is None."""
    conn = get_conn()
    if drama_id:
        row = conn.execute("""
            SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                   COALESCE(SUM(output_tokens),0) as output_tokens,
                   COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                   COUNT(*) as call_count
            FROM usage_log WHERE drama_id = ?
        """, (drama_id,)).fetchone()
    else:
        row = conn.execute("""
            SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                   COALESCE(SUM(output_tokens),0) as output_tokens,
                   COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                   COUNT(*) as call_count
            FROM usage_log
        """).fetchone()
    conn.close()
    return dict(row)


def get_usage_by_drama():
    """Per-drama cost breakdown, joined with drama titles, for the dashboard."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT d.id, d.title_en, d.title_zh,
               COALESCE(SUM(u.input_tokens),0) as input_tokens,
               COALESCE(SUM(u.output_tokens),0) as output_tokens,
               COALESCE(SUM(u.estimated_cost_usd),0) as estimated_cost_usd
        FROM dramas d LEFT JOIN usage_log u ON u.drama_id = d.id
        GROUP BY d.id ORDER BY estimated_cost_usd DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Library-wide dashboard stats & global search
# ---------------------------------------------------------------------------

def get_library_stats():
    conn = get_conn()
    total_dramas = conn.execute("SELECT COUNT(*) FROM dramas").fetchone()[0]
    by_status = conn.execute("SELECT status, COUNT(*) as n FROM dramas GROUP BY status").fetchall()
    by_media_type = conn.execute("SELECT media_type, COUNT(*) as n FROM dramas GROUP BY media_type").fetchall()
    total_lines = conn.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
    translated_lines = conn.execute("SELECT COUNT(*) FROM lines WHERE en IS NOT NULL AND en != ''").fetchone()[0]
    conn.close()
    return {
        "total_dramas": total_dramas,
        "by_status": {r["status"]: r["n"] for r in by_status},
        "by_media_type": {r["media_type"]: r["n"] for r in by_media_type},
        "total_lines": total_lines,
        "translated_lines": translated_lines,
    }


def list_dramas_recently_active(n: int = 10):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM dramas ORDER BY updated_at DESC LIMIT ?", (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_lines_globally(query: str, limit: int = 100):
    """Searches zh/en text across every drama's lines, returns results
    with the parent drama's title attached, for the Library tab's
    global search -- not scoped to one drama like the Reader tab is."""
    conn = get_conn()
    like = f"%{query}%"
    rows = conn.execute("""
        SELECT l.drama_id, l.idx, l.zh, l.en, d.title_en, d.title_zh
        FROM lines l JOIN dramas d ON d.id = l.drama_id
        WHERE l.zh LIKE ? OR l.en LIKE ?
        LIMIT ?
    """, (like, like, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


init_db()


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
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = DB_PATH + suffix
        if os.path.exists(p):
            os.remove(p)
    if os.path.isdir(DRAMAS_DIR):
        shutil.rmtree(DRAMAS_DIR)
    cedict_path = os.path.join(LIBRARY_DIR, "cedict.txt")
    if os.path.exists(cedict_path):
        os.remove(cedict_path)
    os.makedirs(DRAMAS_DIR, exist_ok=True)
    init_db()
