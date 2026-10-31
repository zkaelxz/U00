# Database layer

How Baihe stores its data: the SQLite files, what each table is for and who
writes it, the rules that came out of real bugs, and what sits on disk next to
the database. Names are modules and functions, not line numbers; search for
them. For the layering above this (routers, services) see the root `CLAUDE.md`;
for backup and restore operations see `runbook.md`.

## 1. How the `db` package is organised

- **Plain `sqlite3`, no ORM.** The `db` package (`db/__init__.py` and the
  `db/<domain>.py` files it re-exports) holds every statement for
  `library.db`. Services and root domain modules call its functions; they do
  not open their own connections to this file.
- **Paths** (module globals): `LIBRARY_DIR` = `<portable.data_dir()>/library`,
  with `DRAMAS_DIR`, `DB_PATH` (`library.db`), `BENCHMARK_DIR`, `VOICE_BANK_DIR`
  derived from it. `configure_library_dir` repoints all of them (the test
  `isolated_db` fixture uses it).
- **Lazy init.** Importing `db` touches no disk. The first `get_conn()` runs
  `_ensure_ready()`, which creates `dramas/` and calls `init_db()` once per
  process per `LIBRARY_DIR`. `configure_library_dir` clears that flag.
- **`get_conn()`** opens a new connection per call (`row_factory = sqlite3.Row`)
  and sets, on every connection:
  - `PRAGMA foreign_keys = ON` (SQLite defaults it off per connection, so
    `ON DELETE CASCADE` would otherwise silently do nothing);
  - `PRAGMA journal_mode = WAL` (readers and the one writer do not block each
    other);
  - `check_same_thread=False`, so a connection leaked by a dead job thread can
    be swept (`_close_leaked_connections`; it only closes connections whose
    owning thread has exited).
  - Busy timeout: `get_conn()` does not set one, so it is Python's default of
    5 seconds. (`sources/store.py` sets 10 s explicitly.)
- **Callers close their own connection**, normally with
  `with contextlib.closing(get_conn()) as conn:`. A helper called while its
  caller holds a connection gets a second one, never the caller's.
- **Read-then-write transactions start with `BEGIN IMMEDIATE`**
  (`save_lines`, `update_lines_fields_if_many`). Under WAL a deferred `BEGIN`
  that reads and then writes fails at once with "database is locked" if another
  connection committed in between; `IMMEDIATE` waits for the lock instead.
- **Multi-statement table rebuilds** set `conn.isolation_level = None` and
  issue explicit `BEGIN`/`COMMIT` (SQLite rolls DDL back too): see
  `_migrate_line_refs_to_ids` and `_migrate_step26e_profiles`.
- **Snapshots:** `snapshot_database(dest)` copies through SQLite's backup API,
  not a file copy, because recent writes may still be in `library.db-wal`.
- **`reset_library()`** closes this process's connections, removes
  `library.db` and its `-wal`/`-shm`/`-journal`, `dramas/` and `cedict.txt`, and
  re-runs `init_db()`. Callers must have asked for confirmation already.
- **Package files.** `db.py` became the package `db/`; the split is finished
  (every file is under 40 KB). `db/__init__.py` keeps the connection and
  path code (`configure_library_dir`, `get_conn`, `snapshot_database`,
  `_safe_alter`), `init_db` with the newer-database refusal, the `_LINE_REF_*`
  constants, `drama_dir` and `reset_library`, and re-exports the public names of
  every submodule, so callers and `patch("db.<name>")` keep working. A submodule
  reaches everything else as `db.<name>` (never a relative import or a copy of a
  path global); `tests/test_split_guards.py` enforces it. The submodules, by
  domain:
  - schema and upgrades: `db/schema.py` (`CREATE TABLE`/index helpers for the
    library content), `db/schema_system.py` (jobs, sign-in, Benchmark Lab and its
    column migrations), `db/migrations.py` (the `_migrate_*` helpers);
  - titles and lines: `db/dramas.py` (drama rows, list queries,
    `drama_visible_sql`, library stats, global line search, custom tags),
    `db/lines.py` (`save_lines`, the compare-and-set edits, the loaders),
    `db/line_annotations.py` (translation notes, consistency issues, emotion tags;
    `_line_id_for_idx` stays in `db/__init__.py`), `db/line_history.py` (undo
    snapshots), `db/translation_versions.py` (alternate translations),
    `db/speakers.py` (characters, speaker rename and merge with its undo, series
    characters, the voice bank), `db/comic_pages.py` (comic pages and bubbles);
  - series, glossary and style: `db/glossary.py` (series rows, terms, ignore list,
    translation memory), `db/style.py`, `db/presets.py`, `db/wiki.py` (universe
    wiki), `db/vocab.py` (Reader lookups), `db/reading.py` (profiles, progress,
    reading history, personal notes), `db/known_titles.py` (discovery library);
  - runs and settings: `db/bulk_jobs.py`, `db/job_records.py`,
    `db/app_settings.py` (settings, research cache, field provenance),
    `db/usage.py` (usage and cost log), `db/benchmark.py`,
    `db/model_candidates.py` (model re-evaluation), `db/auth.py` (users,
    permissions, sessions, audit log, ownership and sharing fields);
  - files on disk: `db/media_journal.py` (backup import and restore journal, staging
    folders, crash recovery, `claim_new_drama_folder`; `_media_lock` and
    `STALE_MEDIA_STAGING_SECONDS` stay in `db/__init__.py`), `db/library_folder.py`
    (`fsync_dir` and the start-up check that puts back a library an interrupted
    restore left renamed aside).

## 2. Schema creation and migrations

`init_db()` (in `db/__init__.py`) runs, in a fixed order (later steps read tables
and columns earlier ones create):

1. `_create_*_tables` helpers: `CREATE TABLE IF NOT EXISTS` for every table
   (`library`, `series`, `line_annotation`, `bulk_job`, `reading`, `review`,
   `library_tool`, `job`, `metadata_research`), then `_create_core_indexes`.
   They live in `db/schema.py`, except `_create_job_tables` in
   `db/schema_system.py`.
2. `_migrate_job_records_columns` (the job mirror's added columns), between
   `_create_core_indexes` and `_create_auth_tables`. It and every other
   `_migrate_*` helper below live in `db/migrations.py`.
3. `_create_auth_tables` (users, permissions, sessions, audit log), in
   `db/schema_system.py`.
4. `_migrate_*_columns` helpers: for a database created before a column existed,
   read `PRAGMA table_info(<table>)` and, if the column is missing, run
   `ALTER TABLE ... ADD COLUMN` through `_safe_alter`.
5. `_migrate_off_removed_test_engine` and similar small data fixes.
6. Outside the shared connection: `_init_benchmark_lab_schema` (in
   `db/schema_system.py`; it also adds the Benchmark Lab columns
   `benchmark_cases.tier`, `set_name`, `origin_drama_id`, `origin_line_id` and
   `benchmark_results.scorer`), `_migrate_gpu_lock_slots`,
   `_migrate_line_refs_to_ids`, `_migrate_step26e_profiles`. The line-ref
   rebuild reads `_LINE_REF_TABLE_DDL`, `_LINE_REF_TABLE_COLUMNS` and
   `_LINE_ID_FOR_IDX_SQL` from `db/__init__.py` (as `db.<name>`, so a test can
   patch them there).

Things to know:

- **`_safe_alter`** swallows only `duplicate column name`. `init_db` has no
  cross-process lock, so the API server and a second process can both reach
  the same missing-column check; the second `ALTER` then fails even though the
  migration succeeded. Any other error is re-raised.
- **Every migration is idempotent.** A fresh database and an upgraded one must
  end with the same schema, and running `init_db()` again must change nothing.
- **Not every change is an `ADD COLUMN`.** SQLite cannot alter a primary key, so
  `_migrate_step26e_profiles` (rebuilds `progress`), `_migrate_gpu_lock_slots`
  and `_migrate_line_refs_to_ids` (rebuilds `translation_notes`, `line_emotions`)
  copy to a new table, drop and rename. The line-ref migration first copies the
  old tables to `_backup_step2_<table>`, which are kept and never overwritten
  (`library_user_backup_service` leaves them out of per-user backups).
- **`migrate_database_file(path)`** runs the same `init_db()` against another
  file, in the calling thread only (a thread-local path override in
  `get_conn`). A library restore uses it on a staged copy, with
  `PRAGMA trusted_schema = OFF` on that connection.
- **The guard test.** `tests/test_db.py` keeps `_INIT_DB_MIGRATED_COLUMNS`:
  every column `init_db` adds with `ALTER TABLE ... ADD COLUMN`, per table.
  - `test_old_database_upgrades_to_the_fresh_schema` builds a fresh database,
    drops every listed column (`_make_old_shape`), runs `init_db()` and requires
    the schema (`_schema_shape`) to equal the fresh one.
  - `test_every_added_column_is_listed` scans every module of the `db` package (`_alter_columns_in_db_py`:
    written-out `ALTER TABLE t ADD COLUMN c` plus constant `("col", "TYPE ...")`
    / `("table", "col", "TYPE ...")` tuples) and fails if a column is not in the
    dict. `_ALTERS_WITH_OWN_MIGRATION` exempts the two `reading_history`
    columns added inside the data migrations.
  - `test_running_init_db_again_changes_nothing` and the data-migration test
    cover idempotence and the row fixes.

## 3. Table map (`library.db`)

"Written by" names the module that normally owns the writes; a function in
a function in the `db` package does the SQL. Tables with an `ON DELETE CASCADE` foreign key to `dramas`
or `series` go when the parent goes.

| Table | Purpose and key columns | Written by | How rows are written |
|---|---|---|---|
| `dramas` | One title: titles, language, `media_type`, `status`, per-drama engine and ASR settings, media filenames, `series_id`, `episode_number`, `release_date` (ISO `YYYY-MM-DD`, NULL = unknown), `owner_user_id`, `is_private`, `updated_at` | `services/drama_service.py` (metadata); `transcribe_service`, `dub_service`, `export_service` (status and settings) | `create_drama` / `update_drama` (kwarg keys become SQL: whitelist in the service) |
| `lines` | Subtitle lines: stable `id`, `drama_id`, `idx`, `start`, `end`, `zh` (source text, any language), `en`, `speaker`, `speaker_manual`, `flag`, `flag_note`, `sfx`, `lang`, `dub_filename` | `lines_service`, `diarization_service`, `export_service`, `translate_run_service`, jobs | `save_lines` (full sync or field-scoped), `update_line_fields_if` (compare-and-set) |
| `characters` | Per-drama speaker: `speaker_label` (unique per drama), `character_name`, voices, clone reference (`ref_audio_filename`, `ref_text`), `series_character_id` | `characters_service` | `upsert_character`, `rename_speaker_atomic`, `merge_speakers_atomic` |
| `series` | Groups titles; `name` (unique), `instructions`, `owner_user_id`, `is_private` | `services/ownership_service.py` (`get_or_create_series_for`) | `create_series`, `get_or_create_series` |
| `series_characters` | Characters shared across a series: `aliases`, `gender`, `voice_fingerprint(_samples)` | `characters_service`, `series_people_service` | `upsert_series_character`, `accept_voice_link`, fingerprint blend |
| `glossary_terms` | Per-series glossary: `term_original`, `term_translation`, `policy`, `enforce_exact`, `aliases`, `banned_translations` | `glossary_service` | `upsert_glossary_term`, `insert_glossary_term_if_absent`, `update_glossary_term` |
| `translation_memory` | Per-series source -> translation pairs with `use_count` | `lines_service` | `record_translation_memory` |
| `glossary_dismissals` | Per-series proposals the user ignored (`term_original`); no extraction lists them again until restored | `glossary_service` | `add_glossary_dismissals`, `remove_glossary_dismissals` |
| `voice_suggestion_dismissals` | Suggested voice links a user rejected (drama, label, series character) | `characters_service` | `dismiss_voice_suggestion` |
| `speaker_merge_undos` | Single-use snapshot (`snapshot`, `stale`, `expires_at`) to undo a speaker merge | `characters_service` | written inside `merge_speakers_atomic`; consumed by `undo_merge_speakers_atomic`; `drop_speaker_merge_undos` expires old ones |
| `pages`, `bubbles` | Comic pages and their text bubbles; `pages.rev` is bumped on bubble edits | `scanlate_*_service`, `scanlate.py` | `create_page`, `update_bubble_fields` (compare-and-set), `replace_bubbles_if_unchanged`, `save_bubbles` |
| `known_titles` | The discover catalogue | `discover_catalog_service` | `create_known_title` |
| `translation_notes`, `line_emotions` | Per-line notes/emotion, keyed by `line_id` (not `idx`) | `lines_service`, `workspace_job_service`, `bulk_apply.py` | `save_translation_notes`, `save_emotions` |
| `consistency_issues`, `vocab_lookups`, `wiki_entries`, `edit_samples` | Review and reading records per drama | `review_records_service`, `reader_service`, `lines_service` | `save_consistency_issues`, `save_vocab_lookup`, `upsert_wiki_entry`, `record_edit_sample` |
| `bug_reports` (old libraries only) | No longer created or written; libraries made before keep the table, and restore and single-title copy recreate it (`db.ensure_legacy_bug_reports`) when the source has it (bug reports are files now, see `bug_report_service`) | none | none |
| `line_history` | Whole-drama line snapshots taken before risky bulk edits; `snapshot_json`, newest 10 kept per drama | `lines_service`, `line_tools_service`, `translation_version_service`, `transcribe_service` | `save_line_history_snapshot` (insert and prune in one transaction) |
| `translation_versions` | Named full translations (`lines_json`, `engine`, `model`, `is_active`) so a re-translation does not destroy the previous one | `translation_version_service`, `bulk_apply.py`, `translation_finish.py` | `save_translation_version`, `set_active_translation_version` |
| `profiles`, `progress`, `personal_notes`, `reading_history` | Reader profiles (a default one always exists) and each profile's place, notes and history | `reader_service`, `comic_view_service` | `save_progress`, `save_personal_notes`; `progress` is keyed `(drama_id, profile_id)` |
| `style_profile` | Learned style per scope (`global` or a series), with `history_json` (last 5 kept) | `review_extras_service` | `save_style_profile`, `replace_style_profile`, `restore_style_profile` |
| `presets` | Saved translation settings | `translate_run_service`, `glossary_service` | `save_preset`, `insert_preset` |
| `translate_history` | Standalone translations, `user_id` per owner | `translate_service`, `page_server.py` | `save_translate_history` |
| `usage_log` | Token and cost log; `drama_id` is `ON DELETE SET NULL`, so spend survives deleting a title | engine callers via `log_usage` | `log_usage`; `get_month_spend` reads it |
| `bulk_jobs`, `bulk_job_lines` | Provider batch-API translations in flight | `bulk_translate.py`, `bulk_apply.py`, `bulk_reflect.py`, `bulk_poll.py` | `create_bulk_job`, `update_bulk_job`, `set_bulk_job_line_result_texts` |
| `voice_bank` | Reusable voice clips; the file lives in `library/voice_bank/` | `voice_clone_service` | `save_voice_bank_entry` copies the clip first, then inserts |
| `job_records` | Mirror of `background_jobs` job status for other processes | `jobs/job_store.py` | its own writes (upsert); see section 5 |
| `gpu_lock` | Cross-process GPU slots with owner pid/instance, heartbeat and stale cutoff | `jobs/gpu_slots.py` only | `acquire`, `heartbeat`, `release`, `reap` |
| `job_checkpoints`, `result_cache`, `job_stage_timings`, `line_provenance` | Resume points, cached model output, timings, per-line origin (engine, model, hashes). No foreign key to `dramas` | `job_checkpoint_service`, `job_timing_service`, `line_provenance_service` | `delete_drama` removes `line_provenance` and checkpoints explicitly |
| `app_settings` | JSON key/value store for runtime toggles; also backup settings and identity | `settings_service`, `auto_backup_service`, others | `get_app_setting`, `set_app_setting` |
| `assistant_backlog` | Maintenance assistant notes | `maintenance_assistant_service` | service-level |
| `metadata_research_cache`, `metadata_research_results`, `metadata_field_provenance` | Metadata lookups (results age out after `RESEARCH_RESULT_TTL_DAYS`) and where each applied value came from | `metadata_research_service` | `put_research_cache`, `put_research_result`, `add_field_provenance` |
| `benchmark_cases`, `benchmark_runs`, `benchmark_sessions`, `benchmark_results`, `model_candidates`, `model_decisions` | Benchmark lab and model re-evaluation | `benchmark_lab_service`, `model_reeval_service` | `create_benchmark_*`, `save_benchmark_result`, `record_model_decision` |
| `users`, `user_permissions`, `auth_sessions`, `audit_log` | Accounts, per-user permissions, server-side sessions, audit trail | `services/auth_service.py` | `auth_*` functions; see section 4 |
| `extension_device_tokens` | Browser-extension device tokens (SHA-256 only, revoked/expiry times, last use as an IP prefix) | `services/device_token_service.py` | `device_tokens.py` (created from `init_db`); a restore keeps the live rows |

`app_settings` keys are declared in `lib/settings_schema.py`: each row names the key the value is saved under (`pref.<name>`, `bulk.auto_resume`, `assistant.<name>` or a bare key, so old saved values still read), its type, default and limits. `settings_service.get(key)` reads a row and returns the default for a missing or invalid value. Keys built at run time (`engine_test.<engine>`, `capability.<name>`, per-title language packs and shorten passes) and the backup state keys have no row; `tests/test_settings_schema.py` lists each with its reason. Secrets stay in `.env`.

### `sources.db` (separate file)

`<library>/sources.db` belongs to the source-adapter system and has its own
module, `sources/store.py`: settings, health and capabilities, tracked series,
known and imported chapters, retry state, poll validators, notifications,
`cache_index`, `extraction_cache`, domain proposals. It is kept separate so
nothing there can interfere with `db.save_lines()` or a library backup.

- Connections come from `sources.store.connect()`: 10 s busy timeout
  (`BUSY_TIMEOUT_SECONDS`), WAL set once at initialisation, `synchronous =
  NORMAL`, and `SourcesDatabaseBusy` (fixed, safe message) in place of a raw
  lock error.
- Schema is the `SCHEMA` string plus `_ADDED_COLUMNS`, applied by
  `_add_missing_columns`. This list is **not** covered by `_INIT_DB_MIGRATED_COLUMNS`.
- Every backup zip holds a backup-API snapshot of it (`store.snapshot_to`). A
  library restore rebuilds it with `store.migrate_file`, keeping the current
  settings table, or carries a snapshot of the live one when the zip has none;
  either copy must pass `quick_check` before the swap
  (`workspace_job_service._build_staged_databases`).

## 4. Rules learned from bugs

**Line writes**

- *Field-scoped saves for background jobs.* A job writes only the fields it
  owns: `db.save_lines(drama_id, lines, fields=("en",))`. Field-scoped saves
  update existing rows only; they never insert or delete, so a stale job copy
  cannot resurrect a line the user merged away or delete one the user just
  added. A full sync (`fields=None`) makes the list the drama's lines: update by
  id, insert new, delete rows missing from the list (their notes and emotions go
  with them, except that a line's `merged_ids` are re-pointed first by
  `_repoint_line_refs`).
- *Only changed fields are written.* A Line from `core.line_from_row` carries
  `orig`; a field equal to `orig` is skipped so another writer's newer value
  survives. After a save `orig` is refreshed. Build Lines with
  `core.line_from_row`, or flags and speakers get wiped.
- *Compare-and-set (`expected`).* `update_line_fields_if` and
  `update_lines_fields_if_many` (one `BEGIN IMMEDIATE`, one commit) write only if
  the `expected` columns still hold what the caller saw; they return False or
  the missed ids and never insert or delete. `save_lines(...,
  only_if_unchanged=True, guard_fields=...)` does the same inside a field-scoped
  save and returns the ids it left alone. NULL and `""` compare equal, times
  within 1e-6. `update_bubble_fields(..., expected=...)` and
  `replace_bubbles_if_unchanged` apply it to bubbles.
- *Lines are referenced by `lines.id`, never position.* Notes, emotions and
  reading history moved from `line_idx` to `line_id` because a merge or split
  renumbers `idx`. Match LLM results to lines by explicit id
  (`translate_engines.request_translations_with_retry`, `parse_id_keyed_json`).

**Dramas and ownership**

- *Whitelist kwarg keys.* `create_drama` and `update_drama` put their keyword
  names straight into SQL. Services must pass only whitelisted keys
  (`drama_service` does and raises `InvalidInputError` without echoing values);
  db-layer helpers that build SQL from keys carry their own tuple
  (`_USER_WRITABLE`, `_PAGE_UPDATE_FIELDS`, `BUBBLE_EDIT_FIELDS`,
  `_BENCHMARK_SESSION_UPDATABLE`). Both also refuse, with `ValueError`, any key
  that is not a real `dramas` column (and `id`/`created_at` on update) as defence in depth.
- *Ownership is a service concern.* `services/ownership_service.py` checks drama
  and series visibility from the request principal. A denied read is a 404, not a
  403. The `db` package only stores `owner_user_id` / `is_private` and enforces
  the one structural rule (in `db/auth.py`): giving a drama a series clears its
  own `is_private` (only whole series, or series-less dramas, can be private).
- *Compound writes are one transaction.* `rename_speaker_atomic`,
  `merge_speakers_atomic` and `undo_merge_speakers_atomic` update several tables
  together or not at all.

**Secrets and errors**

- *Redact at write time.* Error text passes through
  `translate_engines.redact_secrets` (or `redact_for_storage`, which also strips
  URL query strings) before it is stored, shown or logged: `jobs/job_store.py`
  (`_storage_text`, because `job_records` lands in backups), `auth_service`
  (`_scrub_detail` for `audit_log.detail_redacted`), `translate_run_service`
  (`last_translate_errors`), `sources/store.py`. API keys go in headers, never
  URLs. API responses carry no secrets, filesystem paths or fetched URLs.
- *Auth stores hashes only.* `auth_sessions.id_hash` and `csrf_hash` are SHA-256
  hashes; raw session ids and CSRF tokens are never stored.

**Files next to the database**

- *Atomic writes.* `core.atomic_write(path, data)` writes a temp file in the same
  folder and `os.replace`s it, so a crash never leaves a truncated file. Backups
  are written to a hidden partial file, verified, flushed and linked into place
  (`auto_backup_files._place_copy`, `lib.link_new.link_new`); the Trash uses one same-volume
  rename (`disk_usage_trash_store._rename`). New drama media goes through a staging
  folder with a journal (`db.new_media_staging`, `move_staged_folder`,
  `recover_media_imports`) so a crash between the database insert and the folder
  move does not leave an orphan or adopt a stranger's folder:
  `claim_new_drama_folder` renames an unexpected `dramas/<id>` aside to
  `<id>.orphan-<hex>` instead of reusing it.
- *Insert, then file.* `save_voice_bank_entry` copies the clip, then inserts the
  row; `delete_voice_bank_entry` deletes the row, then the file. `delete_drama`
  deletes rows first, then `dramas/<id>` (`shutil.rmtree`).

## 5. Job records: the best-effort mirror

`job_table._jobs` (in memory) is the authority for jobs the process owns.
`jobs/job_store.py` (`write_transition`) copies every status change into
`job_records` so another process (a restarted server) can see the
last known state.

- A failed write never breaks the job; `jobs/job_store.py` shows it on the job
  (`sync_error`) and retries it on the heartbeat.
- A row is the *last written* state. A `running` row whose `updated_at` is old is
  suspect: the live process heartbeats its rows (`touch_job_records`), and
  stale ones are closed by `jobs/job_store.py` (`close_stale` /
  `close_if_owner_gone`, by `owner_instance` or `owner_pid`), driven by
  `jobs_service.sweep_stale_job_records`.
- `cancel_requested` lets another process ask a job to stop;
  `result_json` holds the redacted, allow-listed result.
- On restore, running and queued rows are marked cancelled and `gpu_lock` is
  cleared (a backup can only hold stale ones).

## 6. Backups and restores

| What | Where | Database part | Media |
|---|---|---|---|
| Manual full backup | `library_backup_service.start_backup`, `write_backup_zip` | `_sanitized_snapshot`: backup-API copy with every `auth_sessions` and `extension_device_tokens` row deleted (`secure_delete`), journal mode `DELETE`; accounts (`users`, `user_permissions`) stay | yes |
| Database-only backup | `start_database_backup` | same snapshot | no |
| Automatic backup | `auto_backup_service` (settings in `app_settings`) | same writer | `include_media`, off by default |
| My-items backup | `write_user_backup_zip`, per `USER_BACKUP_TABLES` | filtered copy: owner's rows only, auth and machine-local tables emptied | owner's drama media |
| Single-drama restore / import | `drama_restore_service.restore_drama`, `backup_import_service.import_dramas` | `copy_drama` copies rows with new ids | staged, then moved in |
| Full restore | `library_backup_restore_service.restore_backup` -> `library_restore_service.restore_library_backup` | staged, rebuilt from the app's own schema, current auth tables kept | staged, rename-aside, rename-in |

What they skip or keep:

- **Never in a backup:** `backups/`, `profiles/` (saved site sign-ins),
  `source_profiles/`, the extension token, `tmp/`
  (`library_restore_service.restore_kept_names`), `source_cache/`, live `-wal` /
  `-shm` files, symlinks and `.env`. A full restore keeps the current copies of
  these instead of taking them from the upload.
- **A full restore keeps** the live `users`, `user_permissions`, `audit_log`, the
  sources settings table and the `app_settings` automatic-backup identity,
  settings and state (`_RESTORE_KEPT_APP_SETTINGS`); every session is revoked and an audit row
  written. It refuses while any job runs (exclusive hold), validates the zip
  first, and rolls back if the swap fails. The uploaded database is only ever
  attached read-only; its rows are copied into a database built fresh from the
  current schema.
- **Accounts on a library without any** (a rebuilt PC): when the live
  `users` table is empty (a live database that can't be read counts as having
  accounts), the restore takes `users` and `user_permissions`
  from the backup (`auth_service.RESTORE_ACCOUNT_TABLES`), so titles' owners
  resolve again; sessions and device tokens never come from a backup. When
  the live accounts are kept, the backup's `followed_channels` (sources.db)
  pass to the PC owner (`owner_user_id` NULL, `pc_owner` 0, automatic checks
  off, mode `notify`); every account id the restored rows name (titles,
  series, job records, translate history, speaker-merge undos, and in a
  restored `sources.db` tracked-series links and extension-only marks) becomes
  the live account with the same email, or NULL (the PC owner) when there is
  none, its Google sign-in differs, or the backup's account row has no integer
  id or text email (`library_restore_sql.owner_map`; private
  flags unchanged, the count of titles moved is in the result as
  `titles_reassigned` and in the audit entry). New account ids still start
  above every id the backup's rows named (`library_restore_sql.settle_accounts`).
- **A single-title restore** from an automatic copy matches the same way: the
  title's owner, and a recreated series' owner, become the live account with
  the copy's account's email (`library_restore_sql.owner_map_between`), or
  NULL (the PC owner) when there is none or the copy has no `users` table;
  never the copy's id alone. Private flags are unchanged; the result
  (`owners_reassigned`, `copy_has_accounts`) and the audit entry give the count.
- **The previous library** (`<library>.pre_restore_<time>`) is deleted once
  everything was carried across. It stays when something couldn't be: the
  media of titles added since a database-only backup, and any folder the zip
  lacks other than media and the folders the app rebuilds (named in the
  restore result as `folders_left_behind`). Disk usage lists it, an unfinished
  `.restore_staging_*` folder and the copy kept before a Reset library
  (`<library>.pre_reset_<time>`, the last one only) as one entry, "Previous
  library kept after restore", deleted after typing `DELETE`
  (`services/previous_library_service.py`). The delete is refused while the live
  `library.db` is missing, fails `quick_check`, or holds no titles while a
  `.pre_restore_*` copy does (a start during a restore can create a fresh, empty
  library while the real one is set aside); the previous library may then be the
  only good copy. A `.restore_staging_*` folder is taken only once nothing has
  written to it for 6 hours, since a restore in another process stages without
  the restore lock.
- **`<library>.restore-lock`** (beside the library folder) is an OS lock a
  restore holds across its renames and the start-up recovery takes before
  putting a set-aside library back. The holder removes the file when it lets
  go and no other process has it, so it stays only while a restore or recovery
  may be running, or after a crash (the next holder removes it). A process
  that locked a file the last holder had just removed doesn't count that lock
  (`db/library_folder.py`).
- **Reset library** keeps the database (a snapshot) and `dramas/` in
  `<library>.pre_reset_<time>` first and refuses with nothing changed when that
  copy can't be made. If the reset itself then fails while the live database
  still holds every title, the media is moved back and that copy dropped; if
  the database was already deleted or emptied, the copy (database and media)
  stays whole. Older copies are removed only after a reset succeeded.
- **Backup snapshots are vacuumed** after sessions, device tokens and the
  VTuber-name prompt text are removed: the backup API copies the live file's
  free pages, which can still hold deleted rows where SQLite was built without
  secure delete.
- **Single-drama restore and import skip** `usage_log`, `speaker_merge_undos`,
  `bulk_jobs` and `metadata_research_results` (`_SKIPPED_TABLES`: restoring them
  would double-count spend, replay stale undos or batches, or collide on ids).
  Drama, series, user and file-column references are remapped or cleared; file
  names are kept only when they point inside the new drama's folder
  (`IMPORT_FILE_COLUMNS`, `_import_file_ref`).
- **Auto-backup retention:** one copy per day for the last 2 days plus the first
  copy of each of the last 2 ISO weeks (at most 4); only copies this library
  manages are rotated (`KEEP_DAILY`, `KEEP_WEEKLY`).

## 7. On disk beside the database

Everything below is under the data folder (`portable.data_dir()`; for a source
checkout, the repo root).

```
<data>/
  library/
    library.db (+ -wal, -shm)    main database
    sources.db (+ -wal, -shm)    source-adapter database
    dramas/<id>/                 per-title files (below)
    voice_bank/                  clips for voice_bank rows
    benchmark_cases/             uploaded benchmark case files
    backups/                     manual and auto/ backups, exports/
    tmp/                         job work folders, partial files (swept at startup)
    source_cache/, source_review_tmp/, updates/, logs/, piper_voices/ (old, unused)
    profiles/, source_profiles/  saved site sign-ins and approved profiles
    cedict.txt                   downloaded dictionary
  model_cache/                   downloaded models (installed or portable copy)
  baihe_trash/<id>/              Trash: manifest.json + payload (disk-usage "Clear")
  saved_comics/                  CBZ files saved from Sources
  .env                           keys (never in the database or a backup)
```

Inside `dramas/<id>/`: source audio/video and `audio.wav`, `transcript.txt`,
`novel_reference.txt`, cover art, the mixed `dub_track.wav`, `dub_clips/`
(per-line TTS), `voice_refs/` (clone samples), `pages/` (comic scans and
typeset output). `drama_dir(id)` creates the folder on demand.

**Regenerable vs irreplaceable** (`services/disk_usage_measure.py`, with the
categories in `storage.CLEANABLE_CATEGORIES`):

| Regenerable (rebuilt by the app or re-downloaded) | Irreplaceable (cannot be recreated inside Baihe) |
|---|---|
| `model_cache/` | `library.db`, `sources.db` (protected: never cleared or moved) |
| `library/tmp/`, `source_cache/`, `source_review_tmp/`, `updates/`, `logs/`, `piper_voices/` | a title's own files: source media, reference novel, `voice_refs/`, mixed dub track, pages |
| per title: `dub_clips/`, OCR temp (`ocr_*`, `_bubble_crop*`), rendered `typeset_*` pages, `*.tmp` files | `voice_bank/`, `benchmark_cases/`, `source_profiles/`, `saved_comics/`, `backups/` (except `backups/exports/`) |
| `backups/exports/` (make again from the Library) | `profiles/` (site sign-ins) is protected outright |

Disk-usage "Clear" never deletes: it moves an item into `baihe_trash/` with a
manifest, refuses the protected items above and anything while a job, restore or
maintenance run holds the library, and needs the typed word `DELETE` to purge.
Irreplaceable items also need `confirm_irreplaceable`.

## 8. Adding a column or a table

Add a column:

1. Add it to the `CREATE TABLE` in the right `_create_*_tables` helper (fresh
   installs).
2. Add the `if col not in cols: _safe_alter(conn, "ALTER TABLE t ADD COLUMN c
   <type> [DEFAULT ...]")` to the matching `_migrate_*_columns` helper (existing
   installs). Give it a default that keeps old rows meaning what they meant.
3. Add the column name to `_INIT_DB_MIGRATED_COLUMNS["t"]` in
   `tests/test_db.py`. `test_every_added_column_is_listed` fails if you forget.
4. If the value is written through a whitelist (`drama_service`, `_LINE_COLUMNS`
   from `core.LINE_FIELDS`, a `*_FIELDS` tuple), add it there deliberately;
   a line column also belongs in `core.Line`, `core.line_from_row` and
   `line_value`.
5. Check backup and import paths: `IMPORT_FILE_COLUMNS` if it names a file,
   `FORCED_COLUMNS` / `IGNORED_COLUMNS` in `backup_import_service` if a foreign
   backup must not set it.
6. Run `python -m pytest -q tests/test_db.py`.

Add a table:

1. `CREATE TABLE IF NOT EXISTS` in a `_create_*_tables` helper, with
   `FOREIGN KEY ... ON DELETE CASCADE` to `dramas` or `series` when it belongs to
   one. No foreign key means `delete_drama` must clean it explicitly (as for
   `line_provenance`, `job_checkpoints`).
2. Put its access functions in the `db` package; services call those.
3. Decide its backup behaviour: add it to `USER_BACKUP_TABLES` in
   `library_user_backup_service` (the my-items backup treats every table explicitly),
   and to `CHILD_TABLES`, `_SKIPPED_TABLES` or the line-reference lists in
   `auto_backup_service` if single-drama restore must handle it.
4. Redact anything error-like with `redact_secrets` before storing it.
5. A new table in `sources.db` goes in `sources/store.py` (`SCHEMA`,
   `_ADDED_COLUMNS` for columns); it is outside `_INIT_DB_MIGRATED_COLUMNS`.
6. If the change adds a top-level module, give it a one-line docstring (`python tools/repo_map.py` lists it).
