"""System schema: the tables behind background jobs (GPU lock, job records, checkpoints,
result cache, app settings), sign-in and sessions, and the Benchmark Lab with model
re-evaluation. init_db in db/__init__.py runs them in a fixed order."""

import contextlib

import db


def _create_job_tables(conn):
    conn.executescript("""

        -- Cross-process GPU guard. background_jobs.py's own guard
        -- is plain in-process module state, invisible to a separate OS
        -- process -- this table is the shared coordination point so cli.py's
        -- GPU-touching commands and the live UI can't hold more GPU slots than
        -- the "GPU jobs at once" setting allows. One row per holder; id is the
        -- slot, and the CHECK is the hard cap. See jobs/gpu_slots.py.
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
        -- sweep (jobs/job_store.py).
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

        -- The maintenance assistant's small project backlog
        -- ("track this as a bug"). Written only by the user, through
        -- services/maintenance_assistant_service.py.
        CREATE TABLE IF NOT EXISTS assistant_backlog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- (services/job_checkpoint_service.py): per-unit progress
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

        -- Opt-in result cache keyed on
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

        -- (services/job_timing_service.py): one row per
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

        -- (services/line_provenance_service.py): what
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


def _create_auth_tables(conn):
    # API users, permissions, server-side sessions, audit log.
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


def _init_benchmark_lab_schema():
    """Benchmark Lab: golden-set tiers on benchmark_cases, plus a
    per-run record (benchmark_sessions: engine/model/prompt version/context,
    aggregate score, latency, cost, VRAM) and its per-case results. Additive
    only; the older benchmark_runs history is left as it was."""
    with contextlib.closing(db.get_conn()) as conn:
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
        -- Every change to a case's reference text, so a score from before a
        -- fix can be told apart from one after it (version = the case's
        -- ref_version once the change was applied).
        CREATE TABLE IF NOT EXISTS benchmark_case_edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            field TEXT NOT NULL,     -- 'reference_text' or 'source_text'
            old_text TEXT,
            new_text TEXT,
            version INTEGER NOT NULL,
            edited_at TEXT,
            FOREIGN KEY (case_id) REFERENCES benchmark_cases(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_benchmark_case_edits_case ON benchmark_case_edits(case_id);
        -- Candidate models for scheduled re-evaluation, and every
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
                             ("origin_drama_id", "INTEGER"), ("origin_line_id", "INTEGER"),
                             ("title", "TEXT"), ("tags", "TEXT"), ("scene_order", "INTEGER"),
                             ("clip_key", "TEXT"), ("excluded", "INTEGER DEFAULT 0"),
                             ("ref_version", "INTEGER DEFAULT 1")):
            if col not in cols:
                db._safe_alter(conn, f"ALTER TABLE benchmark_cases ADD COLUMN {col} {coltype}")
        result_cols = {r[1] for r in conn.execute("PRAGMA table_info(benchmark_results)").fetchall()}
        if "scorer" not in result_cols:
            db._safe_alter(conn, "ALTER TABLE benchmark_results ADD COLUMN scorer TEXT")
        if "ref_version" not in result_cols:
            db._safe_alter(conn, "ALTER TABLE benchmark_results ADD COLUMN ref_version INTEGER")
        conn.commit()
