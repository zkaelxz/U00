"""Library schema: the CREATE TABLE and CREATE INDEX statements for the library's
content (dramas, lines, series, annotations, reading, review, tools, bulk jobs,
metadata research). init_db in db/__init__.py runs them in a fixed order."""


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
                                                       -- 'original'
            source_language TEXT DEFAULT 'zh',        -- 'zh', 'ja', or 'ko'
            media_type TEXT DEFAULT 'audio_drama',    -- 'audio_drama', 'video_drama', 'novel',
                                                       -- 'manhwa', 'manga', 'manhua', 'asmr', 'other'
            series_id INTEGER,        -- shares a glossary across multiple dramas of the same series
            episode_number INTEGER,   -- Explicit ordering within series_id, for "previous
                                       -- episode" lookups. NULL/unset by default -- an existing
                                       -- series with no numbers set keeps its old created_at order.
            episode_summary TEXT,     -- A short auto-generated running summary of this
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
            tts_voice TEXT,          -- legacy: an Edge TTS voice name; no longer read
            offline_voice TEXT,      -- legacy: a Piper voice name; no longer read
            ref_audio_filename TEXT, -- reference clip for voice cloning (relative to drama dir)
            ref_text TEXT,           -- transcript of what's said in the reference clip
            elevenlabs_voice_id TEXT,-- hosted clone (engine removed); kept as a record, unused
            clone_engine TEXT,       -- local voice engine (dub.CLONE_ENGINES key); NULL = the engine picked for the run; a removed key (f5tts) is refused
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
                                       -- term_original itself -- same convention
                                       -- as series_characters.aliases, but for the source
                                       -- term, not a character
            banned_translations TEXT,  -- pipe-separated known-bad renderings --
                                       -- Auto QC flags a line using one of these for review;
                                       -- it never rewrites. Independent of the older
                                       -- enforce_exact/notes hard-substitution mechanism
                                       -- (see translation_guide.apply_hard_term_substitutions)
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, term_original)
        );

        -- Source->translation pairs a translator has approved by hand
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

        -- A voice-match suggestion ("SPEAKER_01 sounds like <name>")
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

        -- A glossary proposal the user rejected for this series: never
        -- proposed again (by either extraction), until restored.
        CREATE TABLE IF NOT EXISTS glossary_dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            term_original TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, term_original)
        );

        -- One undo for a speaker merge, kept here so the browser never holds
        -- the Characters rows (reference-clip names, transcripts). Single-use
        -- and short-lived; scoped to the drama and the user who merged.
        CREATE TABLE IF NOT EXISTS speaker_merge_undos (
            id TEXT PRIMARY KEY,
            drama_id INTEGER NOT NULL,
            user_id INTEGER,
            source_label TEXT NOT NULL,
            target_label TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            stale INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
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

        -- Bulk mode: one row per submitted provider batch (or, for
        -- DeepSeek, per job scheduled into its next off-peak window), kept on
        -- disk so a restarted app can pick a pending batch back up by its id.
        CREATE TABLE IF NOT EXISTS bulk_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            engine TEXT NOT NULL,
            model TEXT,
            provider_batch_id TEXT,   -- Anthropic msgbatch_..., Gemini batches/...; NULL for DeepSeek
            status TEXT NOT NULL,     -- submitted | scheduled | running | applying | needs_resubmit | applied | cancelled | failed | auth_error
            scheduled_for TEXT,       -- DeepSeek: UTC start of the off-peak window it waits for
            translate_args TEXT,      -- JSON: what a scheduled run needs to build its prompt
            last_error TEXT,
            result_summary TEXT,      -- JSON counts once results are applied
            submitted_at TEXT,
            updated_at TEXT,
            -- Which per-line LLM pass this job runs. 'translate' is
            -- the original (and still the default, for old rows and
            -- every existing call site that doesn't pass one).
            kind TEXT NOT NULL DEFAULT 'translate',
            -- Reflect's own three-pass pipeline ('faithful' ->
            -- 'reflect' -> 'expressive'), NULL for every other kind -- see
            -- bulk_translate.py's own module docstring for how one stage's
            -- applied results submit the next.
            stage TEXT,
            -- Shared across all three of one Reflect run's stage
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
            -- A Reflect faithfulness/reflection stage's own raw
            -- per-line output (a draft translation or a critique) -- not
            -- applied to the line itself, just held here so the NEXT stage's
            -- prompt can be built from it once this stage's batch returns.
            -- Unused (NULL) for every other kind/stage.
            result_text TEXT,
            -- JSON snapshot of whatever field(s) a non-translate
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

        -- Household profiles (Jellyfin-style) -- one shared library,
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

        -- A named, reusable snapshot of a Workspace configuration --
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

        -- History for the standalone translate tool -- not tied to
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

        -- Reusable voice clips sampled from any drama's clone
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


def _create_metadata_research_tables(conn):
    conn.executescript("""

        -- Grounded metadata research. The cache is keyed by the
        -- looked-up entity (not the drama), so a repeat lookup never
        -- re-spends the daily free-search budget; the provenance table
        -- holds one row per researched field value the user accepted
        -- ("applied") or kept beside the existing value ("alternate").
        CREATE TABLE IF NOT EXISTS metadata_research_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- One row per lookup shown to a user: apply reads the
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
