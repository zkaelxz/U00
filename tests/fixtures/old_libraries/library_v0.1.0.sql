BEGIN TRANSACTION;
CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
CREATE TABLE assistant_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            detail_redacted TEXT DEFAULT ''
        );
CREATE TABLE auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            last_seen_at REAL NOT NULL,
            user_agent_short TEXT DEFAULT '',
            ip_prefix TEXT DEFAULT '',
            csrf_hash TEXT NOT NULL
        , device_label TEXT DEFAULT '', signed_in_at REAL);
CREATE TABLE benchmark_case_edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            field TEXT NOT NULL,     -- 'reference_text' or 'source_text'
            old_text TEXT,
            new_text TEXT,
            version INTEGER NOT NULL,
            edited_at TEXT,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE
        );
CREATE TABLE benchmark_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            stage TEXT,              -- 'transcription', 'translation', 'ocr'
            content_type TEXT,       -- 'audio_drama', 'streamer_vod', 'novel', 'manhua' -- descriptive only
            source_language TEXT DEFAULT 'zh',
            input_filename TEXT,     -- relative to the shared benchmark_cases/ dir; NULL for translation cases
            source_text TEXT,        -- translation cases only: the text to translate
            reference_text TEXT,     -- optional known-correct transcript/translation/OCR text
            created_at TEXT
        , tier TEXT DEFAULT 'application', set_name TEXT DEFAULT '', origin_drama_id INTEGER, origin_line_id INTEGER, title TEXT, tags TEXT, scene_order INTEGER, clip_key TEXT, excluded INTEGER DEFAULT 0, ref_version INTEGER DEFAULT 1, ref_start REAL, ref_end REAL, hardsub TEXT);
CREATE TABLE benchmark_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            session_a INTEGER NOT NULL,
            session_b INTEGER NOT NULL,
            winner TEXT NOT NULL,    -- 'a', 'b' or 'tie'
            created_at TEXT,
            UNIQUE (case_id, session_a, session_b),
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE,
            FOREIGN KEY (session_a) REFERENCES benchmark_sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (session_b) REFERENCES benchmark_sessions(id) ON DELETE CASCADE
        );
CREATE TABLE benchmark_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            case_id INTEGER,
            case_label TEXT,
            output_text TEXT,
            score REAL,
            metric TEXT,             -- 'similarity', 'cer', 'wer'
            timing TEXT,             -- JSON of asr_timing_service.case_timing; NULL without reference times
            scorer TEXT,             -- 'jiwer', 'builtin'; NULL on rows from before it was recorded
            passed INTEGER,
            duration_seconds REAL,
            cost_usd REAL DEFAULT 0.0,
            error TEXT,
            created_at TEXT, ref_version INTEGER, judge_scores TEXT, recognised_text TEXT, read_score REAL,
            FOREIGN KEY (session_id) REFERENCES benchmark_sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE SET NULL
        );
CREATE TABLE benchmark_runs (
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
CREATE TABLE benchmark_sessions (
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
        , judge_meta TEXT);
CREATE TABLE bubbles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            idx INTEGER,
            x INTEGER, y INTEGER, w INTEGER, h INTEGER,
            source_text TEXT,
            translated_text TEXT,
            font_size INTEGER DEFAULT 18,
            skip INTEGER DEFAULT 0, font_category TEXT DEFAULT 'regular', kind TEXT DEFAULT 'bubble', kind_confidence REAL, confidence REAL, language TEXT, orientation TEXT, panel_id INTEGER, include_sfx INTEGER DEFAULT 0, text_color TEXT,   -- 1 = don't render this bubble (e.g. false positive)
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
        );
CREATE TABLE bulk_job_lines (
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
CREATE TABLE bulk_jobs (
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
CREATE TABLE characters (
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
            voice_design TEXT, series_character_id INTEGER, pronouns TEXT,       -- described voice (OmniVoice voice design) for a character with no clip
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, speaker_label)
        );
CREATE TABLE consistency_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            term TEXT,
            variants TEXT,            -- JSON-encoded list of strings
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE dramas (
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
        , source_video_filename TEXT, chinese_script TEXT DEFAULT 'simplified', source_url TEXT, transcript_mode TEXT, whisper_size TEXT, alignment_method TEXT, asr_backend_choice TEXT, min_silence_ms INTEGER DEFAULT 300, vad_threshold REAL DEFAULT 0.5, beam_size INTEGER DEFAULT 5, hallucination_silence_sec REAL DEFAULT 0, min_pause_sec REAL DEFAULT 0.35, sensitivity_preset TEXT, separate_vocals_first INTEGER DEFAULT 0, separation_backend TEXT DEFAULT 'auto', realign_long_segments INTEGER DEFAULT 0, whisper_fast_mode INTEGER DEFAULT 0, use_groq INTEGER DEFAULT 0, hardsub_ocr_backend TEXT, hardsub_interval_sec REAL DEFAULT 1.0, project_instructions TEXT, notion_page_id TEXT, reading_speed_mode TEXT DEFAULT 'normal', default_female_pronouns INTEGER, include_genre_notes INTEGER, whisper_repeat_guard INTEGER DEFAULT 0, split_by_sentences INTEGER DEFAULT 0, vocabulary_hint INTEGER, translate_thinking INTEGER, source_url_full TEXT, keep_original INTEGER DEFAULT 0, translate_by_sentence INTEGER, release_date TEXT, difficult_audio INTEGER DEFAULT 0, translate_start_json TEXT, transcribe_start_json TEXT, ass_styles TEXT, owner_user_id INTEGER, is_private INTEGER DEFAULT 0);
INSERT INTO "dramas" VALUES(1,'旧剧','Old title',NULL,NULL,NULL,NULL,NULL,'not started','audio_drama','translation','zh','audio_drama',1,NULL,NULL,NULL,NULL,'claude',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,'2026-11-18T00:10:50.384808','2026-11-18T00:10:50.384808',NULL,'simplified',NULL,NULL,NULL,NULL,NULL,300,0.5,5,0.0,0.35,NULL,0,'auto',0,0,0,NULL,1.0,NULL,NULL,'normal',NULL,NULL,0,0,NULL,NULL,NULL,0,NULL,NULL,0,NULL,NULL,NULL,NULL,0);
CREATE TABLE edit_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER,
            zh TEXT,
            ai_version TEXT,
            user_version TEXT,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE extension_device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            last_used_at REAL,
            last_used_ip_prefix TEXT DEFAULT '',
            revoked_at REAL,
            expires_at REAL
        );
CREATE TABLE glossary_dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            term_original TEXT NOT NULL,
            created_at TEXT,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, term_original)
        );
CREATE TABLE glossary_terms (
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
INSERT INTO "glossary_terms" VALUES(1,1,'旧剧','Old Drama','title',NULL,NULL,0,NULL,NULL);
CREATE TABLE gpu_lock (
            id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 4),
            holder TEXT NOT NULL,
            description TEXT,
            acquired_at REAL NOT NULL,
            heartbeat_at REAL NOT NULL
        , owner_pid INTEGER, owner_instance TEXT, job_id TEXT);
CREATE TABLE job_checkpoints (
            scope TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (scope, unit_id)
        );
CREATE TABLE job_records (
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
        , cancel_requested INTEGER DEFAULT 0, result_json TEXT, owner_pid INTEGER, kind TEXT, owner_instance TEXT, cancel_requested_at REAL, detail_state TEXT, sync_error TEXT, owner_user_id INTEGER);
CREATE TABLE job_stage_timings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            run_started_at REAL NOT NULL,
            stage TEXT NOT NULL,
            started_at REAL NOT NULL,
            ended_at REAL NOT NULL,
            duration_s REAL NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0
        );
CREATE TABLE known_titles (
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
CREATE TABLE line_emotions (
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
CREATE TABLE line_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            label TEXT,               -- e.g. 'before force re-translate', 'before merge'
            snapshot_json TEXT,       -- JSON-encoded list of line dicts
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE line_provenance (
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
CREATE TABLE lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            idx INTEGER,
            start REAL,
            end REAL,
            zh TEXT,
            en TEXT,
            speaker TEXT,            -- raw diarization label OR LLM-tagged character name
            dub_filename TEXT, flag TEXT, flag_note TEXT, speaker_manual INTEGER DEFAULT 0, sfx INTEGER DEFAULT 0, reviewed INTEGER DEFAULT 0, lang TEXT, word_timings TEXT, ass_event TEXT,       -- generated TTS clip for this line, if dubbed
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
INSERT INTO "lines" VALUES(1,1,0,0.0,1.5,'你好','Hello','A',NULL,NULL,'',0,0,1,NULL,NULL,NULL);
INSERT INTO "lines" VALUES(2,1,1,1.6,3.0,'再见','Goodbye',NULL,NULL,'check','tone',0,0,0,NULL,NULL,NULL);
CREATE TABLE member_api_keys (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            last4 TEXT NOT NULL,
            added_at REAL NOT NULL,
            PRIMARY KEY (user_id, provider)
        );
CREATE TABLE metadata_field_provenance (
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
CREATE TABLE metadata_research_cache (
            cache_key TEXT PRIMARY KEY,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
CREATE TABLE metadata_research_results (
            research_id TEXT PRIMARY KEY,
            drama_id INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE model_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capability TEXT NOT NULL,
            engine TEXT NOT NULL,
            model TEXT,
            note TEXT DEFAULT '',
            status TEXT DEFAULT 'candidate',   -- candidate / promoted / rejected
            created_at TEXT,
            UNIQUE(capability, engine, model)
        );
CREATE TABLE model_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            decision TEXT NOT NULL,            -- promoted / rejected
            reason TEXT DEFAULT '',
            scores TEXT,                       -- JSON snapshot of the runs compared
            decided_at TEXT,
            FOREIGN KEY (candidate_id) REFERENCES model_candidates(id) ON DELETE CASCADE
        );
CREATE TABLE pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            idx INTEGER,
            filename TEXT,           -- original page image, relative to drama dir
            rendered_filename TEXT,  -- typeset output, once rendered
            width INTEGER,
            height INTEGER, rev INTEGER DEFAULT 0, context_summary TEXT, run_notes TEXT, edit_history TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE personal_notes (
            profile_id INTEGER NOT NULL,
            drama_id INTEGER NOT NULL,
            notes TEXT,
            updated_at TEXT,
            PRIMARY KEY (profile_id, drama_id),
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE presets (
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
            updated_at TEXT,
            options_json TEXT,
            is_default INTEGER DEFAULT 0
        );
CREATE TABLE profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT,
            created_at TEXT
        );
INSERT INTO "profiles" VALUES(1,'Me',NULL,'2026-11-18T00:10:38.242883');
CREATE TABLE progress (
            drama_id INTEGER NOT NULL,
            profile_id INTEGER NOT NULL,
            last_line_idx INTEGER DEFAULT 0,
            audio_position_seconds REAL DEFAULT 0,
            last_page INTEGER DEFAULT 1,
            percent_complete REAL DEFAULT 0,
            last_accessed_at TEXT,
            view_prefs TEXT,
            PRIMARY KEY (drama_id, profile_id),
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
CREATE TABLE reading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            profile_id INTEGER,
            line_id INTEGER,
            line_idx INTEGER,
            percent_complete REAL,
            accessed_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE
        );
CREATE TABLE result_cache (
            cache_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            settings_hash TEXT NOT NULL,
            value_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
CREATE TABLE series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            created_at TEXT
        , instructions TEXT, default_settings TEXT, owner_user_id INTEGER, is_private INTEGER DEFAULT 0);
INSERT INTO "series" VALUES(1,'Old series','2026-11-18T00:10:50.381911',NULL,NULL,NULL,0);
CREATE TABLE series_characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            character_name TEXT NOT NULL,
            aliases TEXT,             -- pipe-separated nicknames/alternate spellings
            notes TEXT,               -- speaking style, relationships, anything worth remembering
            created_at TEXT, gender TEXT, voice_fingerprint TEXT, voice_fingerprint_samples INTEGER DEFAULT 0,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, character_name)
        );
CREATE TABLE speaker_merge_undos (
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
CREATE TABLE style_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT,               -- 'global' or 'series:<id>'
            profile_json TEXT,        -- learned preferences
            sample_count INTEGER DEFAULT 0,
            updated_at TEXT, history_json TEXT,
            UNIQUE(scope)
        );
CREATE TABLE translate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_language TEXT NOT NULL,
            target_language TEXT NOT NULL,
            engine TEXT NOT NULL,
            source_text TEXT NOT NULL,
            translated_text TEXT NOT NULL,
            created_at TEXT
        , user_id INTEGER);
CREATE TABLE translation_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            source_text TEXT NOT NULL,
            translation TEXT NOT NULL,
            use_count INTEGER DEFAULT 1,
            updated_at TEXT,
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, source_text)
        );
CREATE TABLE translation_notes (
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
CREATE TABLE translation_versions (
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
CREATE TABLE usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER,
            engine TEXT,
            model TEXT,
            operation TEXT,           -- 'translate', 'consistency_check', 'qa', etc.
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            created_at TEXT, cache_read_tokens INTEGER DEFAULT 0, estimated_cost_usd_before_recost REAL,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE SET NULL
        );
INSERT INTO "usage_log" VALUES(1,1,'claude','claude-x','translate',10,5,0.01,'2026-11-18T00:10:50.390183',0,NULL);
CREATE TABLE user_permissions (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            permission TEXT NOT NULL,
            PRIMARY KEY (user_id, permission)
        );
CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_sub TEXT UNIQUE,
            email TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        , share_by_default INTEGER DEFAULT 0);
CREATE TABLE vocab_lookups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER,
            word TEXT,
            reading TEXT,
            definitions TEXT,        -- JSON-encoded list of strings
            language TEXT,
            first_seen_line_idx INTEGER,
            first_seen_line_id INTEGER,
            created_at TEXT, export_rich INTEGER DEFAULT 0,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, word)
        );
CREATE TABLE voice_bank (
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
CREATE TABLE voice_suggestion_dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            speaker_label TEXT NOT NULL,
            series_character_id INTEGER NOT NULL,
            created_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            FOREIGN KEY (series_character_id) REFERENCES series_characters(id) ON DELETE CASCADE,
            UNIQUE(drama_id, speaker_label, series_character_id)
        );
CREATE TABLE wiki_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            entry_type TEXT,          -- character / place / sect / artifact / concept / event
            name TEXT,
            aliases TEXT,             -- comma-separated: titles, nicknames, name changes
            description TEXT,
            attributes_json TEXT,     -- flexible: cultivation level, rank, affiliations, equipment
            first_seen_line_idx INTEGER,
            first_seen_line_id INTEGER,
            known_through_line_idx INTEGER,  -- spoiler boundary: built only from lines up to here
            known_through_line_id INTEGER,   -- the boundary line by id, which a split or delete can't move
            updated_at TEXT,
            FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE,
            UNIQUE(drama_id, entry_type, name)
        );
CREATE INDEX idx_result_cache_kind ON result_cache(kind, created_at);
CREATE INDEX idx_job_stage_timings_job ON job_stage_timings(job_id, run_started_at);
CREATE INDEX idx_lines_drama ON lines(drama_id);
CREATE INDEX idx_characters_drama ON characters(drama_id);
CREATE INDEX idx_pages_drama ON pages(drama_id);
CREATE INDEX idx_bubbles_page ON bubbles(page_id);
CREATE INDEX idx_known_titles_lang ON known_titles(language);
CREATE INDEX idx_field_provenance_drama ON metadata_field_provenance(drama_id);
CREATE INDEX idx_glossary_series ON glossary_terms(series_id);
CREATE INDEX idx_tm_series ON translation_memory(series_id);
CREATE INDEX idx_series_characters_series ON series_characters(series_id);
CREATE INDEX idx_vocab_drama ON vocab_lookups(drama_id);
CREATE INDEX idx_usage_drama ON usage_log(drama_id);
CREATE INDEX idx_history_drama ON line_history(drama_id);
CREATE INDEX idx_reading_history_drama ON reading_history(drama_id);
CREATE INDEX idx_versions_drama ON translation_versions(drama_id);
CREATE INDEX idx_wiki_drama ON wiki_entries(drama_id);
CREATE INDEX idx_edits_drama ON edit_samples(drama_id);
CREATE INDEX idx_benchmark_runs_case ON benchmark_runs(case_id);
CREATE INDEX idx_translate_history_created ON translate_history(created_at);
CREATE INDEX idx_voice_bank_name ON voice_bank(name);
CREATE UNIQUE INDEX idx_users_email ON users(email);
CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX idx_audit_action ON audit_log(action, id);
CREATE INDEX idx_audit_user ON audit_log(user_id, id);
CREATE INDEX idx_dramas_series ON dramas(series_id, episode_number);
CREATE INDEX idx_extension_device_tokens_user
            ON extension_device_tokens(user_id);
CREATE INDEX idx_benchmark_results_session ON benchmark_results(session_id);
CREATE INDEX idx_benchmark_case_edits_case ON benchmark_case_edits(case_id);
CREATE INDEX idx_translation_notes_line ON translation_notes(line_id);
CREATE INDEX idx_line_emotions_line ON line_emotions(line_id);
CREATE INDEX idx_reading_history_line ON reading_history(line_id);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('profiles',1);
INSERT INTO "sqlite_sequence" VALUES('series',1);
INSERT INTO "sqlite_sequence" VALUES('dramas',1);
INSERT INTO "sqlite_sequence" VALUES('lines',2);
INSERT INTO "sqlite_sequence" VALUES('usage_log',1);
INSERT INTO "sqlite_sequence" VALUES('glossary_terms',1);
COMMIT;
