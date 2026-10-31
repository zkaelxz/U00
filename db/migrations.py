"""Migrations that bring an existing library.db up to the current schema: the column
adds (ALTER TABLE ... ADD COLUMN through db._safe_alter), the one-off data fixes, and the
table rebuilds SQLite can't do in place (GPU lock slots, line refs to line ids, reading
profiles). init_db in db/__init__.py runs them in a fixed order; each is idempotent."""

from lib import clock

import db


def _migrate_job_records_columns(conn):
    # Cross-process cancel request flag on the job mirror.
    jr_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_records)").fetchall()}
    if "cancel_requested" not in jr_cols:
        db._safe_alter(conn, "ALTER TABLE job_records ADD COLUMN cancel_requested INTEGER DEFAULT 0")
    # A job's redacted, allowlisted result (services/jobs_service.project_result),
    # JSON-encoded, so the API can tell a "done" job that failed from one that worked.
    if "result_json" not in jr_cols:
        db._safe_alter(conn, "ALTER TABLE job_records ADD COLUMN result_json TEXT")
    # The OS pid of the process running the job, so a row whose owner
    # has exited can be closed at once instead of after the heartbeat
    # cutoff (services/jobs_service.sweep_stale_job_records).
    if "owner_pid" not in jr_cols:
        db._safe_alter(conn, "ALTER TABLE job_records ADD COLUMN owner_pid INTEGER")
    # Written by jobs/job_store.py.
    for col, kind in (("kind", "TEXT"), ("owner_instance", "TEXT"), ("cancel_requested_at", "REAL"),
                      ("detail_state", "TEXT"), ("sync_error", "TEXT")):
        if col not in jr_cols:
            db._safe_alter(conn, f"ALTER TABLE job_records ADD COLUMN {col} {kind}")
    # The holder's process and instance (jobs/gpu_slots.py decides liveness from them).
    gl_cols = {r[1] for r in conn.execute("PRAGMA table_info(gpu_lock)").fetchall()}
    for col, kind in (("owner_pid", "INTEGER"), ("owner_instance", "TEXT"), ("job_id", "TEXT")):
        if col not in gl_cols:
            db._safe_alter(conn, f"ALTER TABLE gpu_lock ADD COLUMN {col} {kind}")


def _migrate_gpu_lock_slots():
    """gpu_lock used to allow a single row (CHECK (id = 1)); it now has one
    row per holder. A CHECK can't be changed with ALTER TABLE, so the table
    is rebuilt once, in one transaction, keeping any held row."""
    conn = db.get_conn()
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
                heartbeat_at REAL NOT NULL,
                owner_pid INTEGER, owner_instance TEXT, job_id TEXT
            )""")
        conn.execute("INSERT INTO gpu_lock_new (id, holder, description, acquired_at, heartbeat_at) "
                     "SELECT id, holder, description, acquired_at, heartbeat_at FROM gpu_lock")
        conn.execute("DROP TABLE gpu_lock")
        conn.execute("ALTER TABLE gpu_lock_new RENAME TO gpu_lock")
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _migrate_line_columns(conn):
    # Lightweight migrations for DBs created before these columns existed
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()}
    if "speaker" not in existing_cols:
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN speaker TEXT")
    if "dub_filename" not in existing_cols:
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN dub_filename TEXT")
    if "flag" not in existing_cols:
        # A key from translate_engines.FLAG_REASONS, set by flag_uncertain_lines()
        # -- the review queue for a long file, so a person doesn't have to
        # scan every line to find the handful worth a second look.
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN flag TEXT")
    if "flag_note" not in existing_cols:
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN flag_note TEXT")
    if "speaker_manual" not in existing_cols:
        # 1 once a line's speaker was set by hand; re-running speaker
        # detection won't overwrite it without confirmation.
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN speaker_manual INTEGER DEFAULT 0")
    if "sfx" not in existing_cols:
        # 1 for a non-verbal/SFX cue line ("[door slams]") -- exported
        # bracketed and styled apart from dialogue.
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN sfx INTEGER DEFAULT 0")
    if "lang" not in existing_cols:
        # The line's spoken language (core.Line.lang); NULL is the title's
        # source_language, so existing lines keep their meaning.
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN lang TEXT")
    if "word_timings" not in existing_cols:
        # segment_splitting.encode_line_words' payload: the line's Whisper word times, kept
        # so a later re-split cuts at real pauses. Never selected by load_lines
        # unless asked for, so line lists don't read it.
        db._safe_alter(conn, "ALTER TABLE lines ADD COLUMN word_timings TEXT")


def _migrate_drama_columns(conn):
    drama_cols = {r[1] for r in conn.execute("PRAGMA table_info(dramas)").fetchall()}
    if "whisper_repeat_guard" not in drama_cols and "hallucination_silence_sec" in drama_cols:
        # Once: the old 2.0 s guard default is now off. Before the ALTER that
        # marks it done: run first, the UPDATE opens the transaction the ALTER
        # then joins, so a crash in between can't leave the column added and
        # the titles still on 2.0.
        conn.execute("UPDATE dramas SET hallucination_silence_sec = 0 WHERE hallucination_silence_sec = 2")
    for col, coltype in [("translation_engine", "TEXT DEFAULT 'claude'"),
                          ("content_mode", "TEXT DEFAULT 'audio_drama'"),
                          # Novel narration only -- 'translation' (default, existing
                          # behavior) speaks ln.en; 'original' speaks ln.zh (the app's generic
                          # source-text field, holding ja/ko source text too when that's the
                          # drama's actual source_language).
                          ("narration_language", "TEXT DEFAULT 'translation'"),
                          ("source_video_filename", "TEXT"), ("source_language", "TEXT DEFAULT 'zh'"),
                          # Only meaningful when source_language == "zh": Whisper transcription
                          # and LLM translation don't care (they read/produce either script
                          # fine), but OCR (Tesseract's chi_sim vs chi_tra language pack) and
                          # jieba segmentation (built for Simplified, degrades on Traditional)
                          # both need to know which one they're looking at.
                          ("chinese_script", "TEXT DEFAULT 'simplified'"),
                          ("media_type", "TEXT DEFAULT 'audio_drama'"), ("series_id", "INTEGER"),
                          ("episode_number", "INTEGER"), ("episode_summary", "TEXT"),
                          ("updated_at", "TEXT"), ("last_translate_errors", "TEXT"),
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
                          ("hallucination_silence_sec", "REAL DEFAULT 0"),
                          ("min_pause_sec", "REAL DEFAULT 0.35"),
                          ("sensitivity_preset", "TEXT"),
                          ("separate_vocals_first", "INTEGER DEFAULT 0"),
                          ("separation_backend", "TEXT DEFAULT 'auto'"),
                          ("realign_long_segments", "INTEGER DEFAULT 0"),
                          ("whisper_fast_mode", "INTEGER DEFAULT 0"),
                          ("use_groq", "INTEGER DEFAULT 0"),
                          # hardsub_ocr's own two tuning knobs --
                          # same "previously session-state only" gap as the ones above.
                          ("hardsub_ocr_backend", "TEXT"),
                          ("hardsub_interval_sec", "REAL DEFAULT 1.0"),
                          # Freeform, multi-line instructions that DO reach
                          # the translation prompt (translate_engines.build_llm_instructions)
                          # -- unlike personal_notes above, which is private and never
                          # sent anywhere. The series-level counterpart is
                          # series.instructions, inherited by every drama in the series.
                          ("project_instructions", "TEXT"),
                          # Legacy: nothing writes it now. Kept so older databases
                          # and backups load; dropping it needs a table rebuild.
                          ("notion_page_id", "TEXT"),
                          # Per-title reading-speed flag strictness
                          # (subtitle_formats.READING_SPEED_MODES).
                          ("reading_speed_mode", "TEXT DEFAULT 'normal'"),
                          # The Translate stage's "genre guidance" and "default to
                          # she/her" toggles. NULL = never chosen for this title, so
                          # the API defaults apply (genre on, she/her off).
                          ("default_female_pronouns", "INTEGER"),
                          ("include_genre_notes", "INTEGER"),
                          ("whisper_repeat_guard", "INTEGER DEFAULT 0"),
                          ("split_by_sentences", "INTEGER DEFAULT 0"),
                          # NULL/0 = off: the "Vocabulary: ..." hint for Qwen3-ASR.
                          ("vocabulary_hint", "INTEGER"),
                          # "Think harder" for translation; NULL = never chosen (off).
                          ("translate_thinking", "INTEGER"),
                          ("source_url_full", "TEXT"), ("keep_original", "INTEGER DEFAULT 0"),
                          # NULL = off: translate whole sentences, then split the English
                          # across the short timed lines (sentence_groups.py).
                          ("translate_by_sentence", "INTEGER"),
                          ("release_date", "TEXT"), ("difficult_audio", "INTEGER DEFAULT 0"),
                          # JSON: the Translate form's starting options for this title
                          # (what a profile applied; services/profile_service.py).
                          ("translate_start_json", "TEXT"),
                          # JSON: the Transcribe form's run choices for this title (speaker
                          # detection, speaker counts, extra names, prompt override).
                          ("transcribe_start_json", "TEXT")]:
        if col not in drama_cols:
            db._safe_alter(conn, f"ALTER TABLE dramas ADD COLUMN {col} {coltype}")
    # Here, not with the other indexes: an old library gets these columns
    # only just above. Every library list looks up each title's previous
    # episode by series, which without it scans all titles once per title.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dramas_series ON dramas(series_id, episode_number)")


def _migrate_series_and_character_columns(conn):
    series_cols = {r[1] for r in conn.execute("PRAGMA table_info(series)").fetchall()}
    for col in ("instructions", "default_settings"):   # the latter: series_defaults_service
        if col not in series_cols:
            db._safe_alter(conn, f"ALTER TABLE series ADD COLUMN {col} TEXT")
    preset_cols = {r[1] for r in conn.execute("PRAGMA table_info(presets)").fetchall()}
    for col, coltype in (("options_json", "TEXT"), ("is_default", "INTEGER DEFAULT 0")):
        if col not in preset_cols:
            db._safe_alter(conn, f"ALTER TABLE presets ADD COLUMN {col} {coltype}")
    char_cols = {r[1] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
    if "ref_audio_filename" not in char_cols:
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN ref_audio_filename TEXT")
    if "ref_text" not in char_cols:
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN ref_text TEXT")
    if "elevenlabs_voice_id" not in char_cols:
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN elevenlabs_voice_id TEXT")
    if "clone_engine" not in char_cols:
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN clone_engine TEXT")
    if "voice_design" not in char_cols:
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN voice_design TEXT")
    if "offline_voice" not in char_cols:
        # Kept for databases that already have the column; nothing reads it now.
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN offline_voice TEXT")
    if "series_character_id" not in char_cols:
        # Links this drama's speaker to a persistent series_characters row,
        # so renaming/updating the series-level character (once) reflects
        # everywhere it's been assigned, instead of needing a per-drama edit.
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN series_character_id INTEGER")
    if "pronouns" not in char_cols:
        # Per-drama pronoun text ("she/her", "they/them", "xe/xem", ...) --
        # lets a drama with no series set pronouns at all, and overrides
        # the linked series character's value when both are set.
        db._safe_alter(conn, "ALTER TABLE characters ADD COLUMN pronouns TEXT")
    gloss_cols = {r[1] for r in conn.execute("PRAGMA table_info(glossary_terms)").fetchall()}
    if "category" not in gloss_cols:
        db._safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN category TEXT")
    if "policy" not in gloss_cols:
        db._safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN policy TEXT")
    if "enforce_exact" not in gloss_cols:
        db._safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN enforce_exact INTEGER DEFAULT 0")
    if "aliases" not in gloss_cols:
        db._safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN aliases TEXT")
    if "banned_translations" not in gloss_cols:
        db._safe_alter(conn, "ALTER TABLE glossary_terms ADD COLUMN banned_translations TEXT")
    sc_cols = {r[1] for r in conn.execute("PRAGMA table_info(series_characters)").fetchall()}
    if "gender" not in sc_cols:
        # Feeds translation as a fixed pronoun hint for this character
        # (e.g. "Su Shan: she/her") -- Mandarin's spoken 他/她/它 are
        # homophones, so Whisper's transcribed character for a pronoun is
        # not a reliable gender signal on its own, and misgendering a
        # named character is a much more visible error than an ambiguous
        # unnamed one. NULL/"" means unset -- no hint is added for that
        # character, distinct from "unspecified" as a deliberate choice.
        db._safe_alter(conn, "ALTER TABLE series_characters ADD COLUMN gender TEXT")
    usage_cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
    if "cache_read_tokens" not in usage_cols:
        # The part of input_tokens served from a provider prompt
        # cache, so the dashboard can show how often caching actually hits.
        db._safe_alter(conn, "ALTER TABLE usage_log ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
    if "estimated_cost_usd_before_recost" not in usage_cols:
        # The stored estimate a row had before the opt-in re-cost
        # (services/usage_recost_service) replaced it; NULL means never
        # re-costed. Undo copies it back.
        db._safe_alter(conn, "ALTER TABLE usage_log ADD COLUMN estimated_cost_usd_before_recost REAL")
    if "voice_fingerprint" not in sc_cols:
        # A running-average pyannote voice embedding (JSON list of
        # floats), built up from every drama where a speaker was confirmed
        # (by Accept, never automatically) as this character -- see
        # update_series_character_voice_fingerprint(). Compared by cosine
        # similarity against a NEW drama's own per-speaker embeddings to
        # suggest "this speaker sounds like <name>". NULL until at least
        # one confirmed sample exists.
        db._safe_alter(conn, "ALTER TABLE series_characters ADD COLUMN voice_fingerprint TEXT")
    # Each column of a group is checked on its own: an upgrade interrupted
    # after the group's first ALTER must still add the rest on the next start.
    if "voice_fingerprint_samples" not in sc_cols:
        db._safe_alter(conn, "ALTER TABLE series_characters ADD COLUMN voice_fingerprint_samples INTEGER DEFAULT 0")


def _migrate_scanlate_columns(conn):
    bubble_cols = {r[1] for r in conn.execute("PRAGMA table_info(bubbles)").fetchall()}
    if "font_category" not in bubble_cols:
        # One of scanlate.FONT_CATEGORIES ("regular"/"bold"/"handwritten"),
        # auto-filled from sample_text_style()'s classical-CV stroke-weight/
        # irregularity analysis at detection time, editable per bubble
        # before render -- see scanlate.py's own docstring for why this
        # isn't a trained font-classifier model.
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN font_category TEXT DEFAULT 'regular'")
    if "kind" not in bubble_cols:
        # Each bubble row is a structured text region (see
        # scanlate.TextRegion) -- region type from classify_text_regions(),
        # the detector's own confidence (NULL for the OpenCV heuristic,
        # which has none), language, text orientation, and panel. Rows
        # predating this are all speech bubbles, hence kind's default.
        # include_sfx is the per-region override that puts an SFX region
        # back into the automated inpaint-and-replace pass.
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN kind TEXT DEFAULT 'bubble'")
    if "kind_confidence" not in bubble_cols:
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN kind_confidence REAL")
    if "confidence" not in bubble_cols:
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN confidence REAL")
    if "language" not in bubble_cols:
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN language TEXT")
    if "orientation" not in bubble_cols:
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN orientation TEXT")
    if "panel_id" not in bubble_cols:
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN panel_id INTEGER")
    if "include_sfx" not in bubble_cols:
        db._safe_alter(conn, "ALTER TABLE bubbles ADD COLUMN include_sfx INTEGER DEFAULT 0")
    page_cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)").fetchall()}
    if "rev" not in page_cols:
        # rev is bumped by every id-preserving region write
        # (insert/update/delete/reorder/replace_bubbles_if_unchanged; not
        # save_bubbles, which reissues every id); context_summary is the rolling
        # translation context after this page; run_notes is a redacted
        # JSON list of {level, message} from the last automatic run.
        db._safe_alter(conn, "ALTER TABLE pages ADD COLUMN rev INTEGER DEFAULT 0")
    if "context_summary" not in page_cols:
        db._safe_alter(conn, "ALTER TABLE pages ADD COLUMN context_summary TEXT")
    if "run_notes" not in page_cols:
        db._safe_alter(conn, "ALTER TABLE pages ADD COLUMN run_notes TEXT")


def _migrate_bulk_job_columns(conn):
    bulk_job_cols ={r[1] for r in conn.execute("PRAGMA table_info(bulk_jobs)").fetchall()}
    if "kind" not in bulk_job_cols:
        # Every bulk job predating this column was a translation job.
        db._safe_alter(conn, "ALTER TABLE bulk_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'translate'")
    if "stage" not in bulk_job_cols:
        db._safe_alter(conn, "ALTER TABLE bulk_jobs ADD COLUMN stage TEXT")
    if "pipeline_id" not in bulk_job_cols:
        db._safe_alter(conn, "ALTER TABLE bulk_jobs ADD COLUMN pipeline_id TEXT")
    bulk_job_line_cols = {r[1] for r in conn.execute("PRAGMA table_info(bulk_job_lines)").fetchall()}
    if "result_text" not in bulk_job_line_cols:
        db._safe_alter(conn, "ALTER TABLE bulk_job_lines ADD COLUMN result_text TEXT")
    if "state_at_submit" not in bulk_job_line_cols:
        db._safe_alter(conn, "ALTER TABLE bulk_job_lines ADD COLUMN state_at_submit TEXT")


def _migrate_vocab_and_style_columns(conn):
    vocab_cols = {r[1] for r in conn.execute("PRAGMA table_info(vocab_lookups)").fetchall()}
    if "export_rich" not in vocab_cols:
        # Flags a lookup as queued for the richer sentence+audio
        # Anki card type, set from the Reader right where the word was
        # looked up, rather than only via a bulk end-of-session export.
        db._safe_alter(conn, "ALTER TABLE vocab_lookups ADD COLUMN export_rich INTEGER DEFAULT 0")
    style_cols = {r[1] for r in conn.execute("PRAGMA table_info(style_profile)").fetchall()}
    if "history_json" not in style_cols:
        # Earlier learned-style profiles (newest first, at most
        # STYLE_HISTORY_KEEP), so a learn or reset can be undone.
        db._safe_alter(conn, "ALTER TABLE style_profile ADD COLUMN history_json TEXT")


def _migrate_ownership_columns(conn):
    # Ownership and sharing (services/ownership_service.py).
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
            db._safe_alter(conn, f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
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


def _grant_lines_read_for_reader_page(conn):
    # The Reader page moved from library.read to lines.read (owner decision
    # 2026-10-10): grant it once to everyone who could read the page before,
    # so nobody loses the Reader on upgrade. The marker keeps a later revoke.
    # A database with no accounts (a fresh install, a restored user backup)
    # has nothing to grant and gets no marker: new accounts hold lines.read.
    marker = "migrations.reader_page_lines_read"
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None and conn.execute(
            "SELECT 1 FROM app_settings WHERE key = ?", (marker,)).fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO user_permissions (user_id, permission) "
                     "SELECT user_id, 'lines.read' FROM user_permissions "
                     "WHERE permission = 'library.read'")
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, 'true')", (marker,))


def _migrate_auth_session_columns(conn):
    session_cols = {r[1] for r in conn.execute("PRAGMA table_info(auth_sessions)").fetchall()}
    if "device_label" not in session_cols:
        # A coarse "Chrome on Android" label (lib.device_label),
        # the only device detail the signed-in-devices list shows.
        db._safe_alter(conn, "ALTER TABLE auth_sessions ADD COLUMN device_label TEXT DEFAULT ''")
    # Sessions from before the label kept the first 60 characters of the
    # user agent: label them from it, then drop it, so no raw user agent
    # stays stored. Rows already blanked don't match, so this runs once.
    old_agents = conn.execute(
        "SELECT id, user_agent_short, device_label FROM auth_sessions "
        "WHERE user_agent_short IS NOT NULL AND user_agent_short != ''").fetchall()
    if old_agents:
        from lib.device_label import device_label
        conn.executemany(
            "UPDATE auth_sessions SET device_label = ?, user_agent_short = '' WHERE id = ?",
            [(r[2] or device_label(r[1]), r[0]) for r in old_agents])


def _migrate_off_removed_test_engine(conn):
    # The fake "test_offline" engine is gone: a drama or preset still on it
    # goes back to the default engine instead of failing to start a run.
    conn.execute("UPDATE dramas SET translation_engine = 'claude' WHERE translation_engine = 'test_offline'")
    conn.execute("UPDATE presets SET translation_engine = 'claude' WHERE translation_engine = 'test_offline'")


def _migrate_line_refs_to_ids():
    """One-time: translation_notes, line_emotions and
    reading_history pointed at a line by its position (line_idx), which a
    merge or split renumbers -- so a note silently ended up on a
    different line. They now point at lines.id.

    Safe to interrupt: the affected tables are first copied as-is into
    _backup_step2_<table> (kept, never overwritten on a re-run), then the
    rebuild runs as ONE transaction -- SQLite rolls DDL back too, so a
    crash partway leaves the old tables untouched and the next start just
    runs it again. Once done, the line_id columns exist and this is a no-op."""
    conn = db.get_conn()
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    try:
        def cols(t):
            return {r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        def pending():
            return ([t for t in db._LINE_REF_TABLE_DDL if "line_id" not in cols(t)],
                    "line_id" not in cols("reading_history"))
        rebuild, history = pending()
        if not rebuild and not history:
            return
        conn.execute("BEGIN")
        for t in rebuild + (["reading_history"] if history else []):
            conn.execute(f"CREATE TABLE IF NOT EXISTS _backup_step2_{t} AS SELECT * FROM {t}")
        conn.execute("COMMIT")

        # IMMEDIATE, then look again: another process starting at the same
        # time may have rebuilt the tables while this one waited for the lock.
        conn.execute("BEGIN IMMEDIATE")
        rebuild, history = pending()
        for t in rebuild:
            new = f"{t}_step2_new"
            keep = db._LINE_REF_TABLE_COLUMNS[t]
            conn.execute(f"DROP TABLE IF EXISTS {new}")
            conn.execute(db._LINE_REF_TABLE_DDL[t].format(name=new))
            conn.execute(
                f"INSERT OR IGNORE INTO {new} (id, line_id, {', '.join(keep)}) "
                f"SELECT t.id, {db._LINE_ID_FOR_IDX_SQL}, {', '.join('t.' + c for c in keep)} FROM {t} t")
            conn.execute(f"DROP TABLE {t}")
            conn.execute(f"ALTER TABLE {new} RENAME TO {t}")
        if history:
            conn.execute("ALTER TABLE reading_history ADD COLUMN line_id INTEGER")
            conn.execute("UPDATE reading_history SET line_id = " + db._LINE_ID_FOR_IDX_SQL.replace("t.", "reading_history."))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _migrate_step26e_profiles():
    """One-time: creates the default profile every pre-profiles
    install's existing progress/reading_history/personal_notes data gets
    attached to, and rebuilds `progress` for its new (drama_id, profile_id)
    primary key -- SQLite can't ALTER a PRIMARY KEY in place, so this is a
    backup-then-rebuild, same pattern as _migrate_line_refs_to_ids above.

    Keyed off `profiles` being empty: a fresh install (via the
    CREATE TABLE IF NOT EXISTS block in db/schema.py) already gets the new `progress`
    schema directly and starts with zero profiles too, so this still runs
    for it -- it just has no old rows to migrate, and creates the same
    default profile a real upgrade would. Once at least one profile exists
    this is a no-op forever after (delete_profile refuses to remove the
    last one, so that stays true)."""
    conn = db.get_conn()
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    try:
        if conn.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
            return
        # IMMEDIATE, then look again: another process starting at the same
        # time may have made the default profile while this one waited.
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
            conn.execute("ROLLBACK")
            return
        now = clock.utcnow().isoformat()
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
