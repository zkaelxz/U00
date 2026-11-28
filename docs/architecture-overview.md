# Architecture overview

One page on how Baihe Studio fits together. Each section links to the long doc. For the module-by-module map, `python tools/repo_map.py` prints it from each module's docstring (`--find <word>` searches, `--part N` pages through a big file).

## Layers, top to bottom

- `frontend/`: the React app. Vite builds it into `frontend/dist`.
- `api/`: FastAPI routers (`api/routers/*_routes.py`), Pydantic models (`api/schemas/`) and auth (`api/auth.py`).
- `services/`: logic with no UI. It raises the errors in `lib/errors.py`.
- Root modules: the domain code (transcription, translation, export and so on).
- `db/`: plain sqlite3, no ORM. Callers use `db.<name>`.
- `lib/`: small shared helpers with no domain knowledge. Every layer may use it; it imports none of them.

A layer calls only the ones below it. Detail: [`api-and-services.md`](api-and-services.md), [`database.md`](database.md).

## Where a request goes

The browser calls `/api/...`. A router checks the caller's permission, hands off to a service, and the service uses domain modules and `db`. Errors travel back as the typed errors from `lib/errors.py`.

## Background jobs

Long work (transcribe, translate, download) runs as a job. `background_jobs.py` is the front door; starting and running live in `jobs/`. The in-memory job table is the authority, with a best-effort copy in the `job_records` table. Detail: [`background-jobs.md`](background-jobs.md).

## Where data is stored

Titles, lines and settings are in `library.db`. Sources keep their own `sources.db`. Media, backups and the Trash sit in the data folder beside them. Detail: [`database.md`](database.md), [`runbook.md`](runbook.md).

## Three rules to know first

1. Match model results back to lines by explicit id, never by list position.
2. API keys go in headers, never in URLs, log lines or stored errors.
3. Every route declares exactly one of `require_permission(...)`, `public_route()`, `local_only()` or `authenticated()`.

The full rules are in `CLAUDE.md`; translation engines are in [`engine-backends.md`](engine-backends.md); tests and CI are in [`testing-and-ci.md`](testing-and-ci.md).

## Where to look

Find your area, then open the router and service first. Cells hold names inside
the folder named in the header, comma separated; a trailing `/` is a folder and
`*` a glob. `n/a` means there is none. Root modules sit at the repo top level.

Big files, use `python tools/repo_map.py <name>` (then `--part N`), never open them
whole: `db`, `diagnostics`, `core`, `scanlate`, `services/transcribe_service`, or any file over about 40 KB.

| Area | UI `frontend/src/pages/` | Router `api/routers/` | Service `services/` | Root / domain module | db (`db/` tables) | Tests `tests/` | E2E `frontend/e2e/` | Doc `docs/` |
|---|---|---|---|---|---|---|---|---|
| Library / titles | Library.tsx, LibraryTools.tsx, libraryParity/ | library_routes.py, drama_routes.py | library_service.py, drama_service.py, ownership_service.py | title_library.py | dramas, series | test_library_service.py, test_drama_service.py, test_api_dramas.py, test_title_library.py | library*.spec.ts | database.md |
| Transcription (ASR) | workspace/stages/TranscribeStage.tsx | transcribe_routes.py, asr_options_routes.py, diarization_routes.py | transcribe_service.py, transcribe_options_service.py, transcribe_speed_service.py, retranscribe_line_service.py, transcribe_pipeline.py, asr_options_service.py, diarization_service.py | asr_backend.py, whisper_models.py, vad_segments.py, forced_align.py, diarize.py, segment_splitting.py | dramas, lines | test_transcribe_service.py, test_transcribe_backends.py, test_transcribe_hardsub.py, test_asr_backend.py, test_diarize.py, test_segment.py | transcribe-*.spec.ts | engine-backends.md, asr-experiments.md |
| Translation + engines | Translate.tsx, workspace/stages/TranslateStage.tsx | translate_routes.py, translate_run_routes.py, engine_routing_routes.py | translate_service.py, translate_run_service.py, translate_preset_service.py, engine_routing_service.py | engine_backends/, bulk/bulk_translate.py, bulk/bulk_apply.py, bulk/bulk_reflect.py, bulk/bulk_poll.py, translation_finish.py, translation_guide.py | lines, translation_versions, presets, usage_log | test_translate_engines.py, test_engine_providers.py, test_engine_llm_tasks.py, test_engine_costs.py, test_translate_run_service.py, test_bulk_translate.py, test_bulk_tasks.py, test_bulk_paid_results.py, test_engine_routing.py | translate*.spec.ts | engine-backends.md |
| Review / lines | workspace/stages/ReviewStage.tsx, workspace/stages/review/ | lines_routes.py, review_lines_routes.py, review_extras_routes.py, review_records_routes.py | lines_service.py, review_lines_service.py, line_tools_service.py | line_tools.py, auto_qc.py, resegment.py | lines, line_history, line_provenance | test_lines_service.py, test_review_lines_service.py, test_api_lines.py | review-*.spec.ts | api-and-services.md |
| Characters / glossary | workspace/stages/CharactersPanel.tsx, workspace/stages/GlossaryPanel.tsx | characters_routes.py, glossary_routes.py, series_people_routes.py | characters_service.py, glossary_service.py, series_people_service.py | translation_memory.py | characters, series_characters, glossary_terms | test_characters_service.py, test_glossary_service.py, test_api_glossary.py | characters-*.spec.ts, glossary-*.spec.ts | database.md |
| Live translate | Live.tsx, live/ | live_routes.py | live_service.py | live/live_translate.py, live/live_fetch.py | n/a | test_live_service.py, test_live_translate.py, test_live_fetch.py, test_api_live.py | live*.spec.ts | n/a |
| Dubbing | workspace/stages/VoiceClonePanel.tsx | listen_routes.py, voice_clone_routes.py, narration_routes.py | listen_service.py, voice_clone_service.py, narration_service.py | voice/dub.py, voice/dub_narration.py, voice/voice_id.py | voice_bank, characters | test_dub.py, test_listen_service.py, test_api_listen.py, test_voice_id.py | voice-*.spec.ts | engine-backends.md |
| Scanlate / OCR | Comic.tsx, comic/, SavedManga.tsx | scanlate_routes.py, comic_routes.py, saved_comics_routes.py | scanlate_run_service.py, scanlate_pages_service.py, comic_view_service.py | scanlate/scanlate.py, scanlate/scanlate_detect.py, scanlate/scanlate_inpaint.py, ocr.py, hardsub_ocr.py | pages, bubbles | test_scanlate.py, test_ocr.py, test_hardsub_ocr.py, test_api_comic_viewer.py | scanlate*.spec.ts, comic*.spec.ts | n/a |
| Sources / adapters | Sources.tsx, sources/, Discover.tsx | source_routes.py, sources_*_routes.py | source_service.py, sources_*_service.py | sources/ (adapters in sources/adapters/), page_fetch.py | `sources.db` via sources/store.py | test_sources_*.py, test_api_sources_*.py, test_source_service.py | sources*.spec.ts, source-*.spec.ts | adding-source.md, content-sources.md |
| Benchmark lab | Benchmark.tsx, benchmark/ | benchmark_routes.py, model_reeval_routes.py, model_registry_routes.py | benchmark_lab_service.py, model_reeval_service.py, model_registry_service.py | benchmark.py, asr_benchmark.py | benchmark_*, model_candidates, model_decisions | test_benchmark_lab.py, test_benchmark.py, test_model_reeval.py, test_api_benchmark.py | lab-*.spec.ts, bench-sections.spec.ts | database.md |
| Diagnostics / installs | Diagnostics.tsx, diagnostics/ | diagnostics_routes.py, diagnostics_installs_routes.py, diagnostics_gaps_routes.py | diagnostics_service.py, diagnostics_installs_service.py, diagnostics_gaps_service.py, diagnostics_admin_service.py, diagnostics_packages_service.py, diagnostics_gpu_torch_service.py | diagnostics.py, diagnostics_report.py, diagnostics_torch.py, upgrade_check.py, install_plan.py, pending_install.py, check_setup.py | n/a | test_diagnostics_service.py, test_api_diagnostics_installs.py, test_install_presets.py, test_install_plan.py, test_upgrade_check.py | diagnostics*.spec.ts | runbook.md |
| Jobs | Jobs.tsx, jobs/, workspace/JobPill.tsx | jobs_routes.py, job_stage_routes.py, events_routes.py | jobs_service.py, job_checkpoint_service.py, job_timing.py, gpu_process_job.py, ytdlp_child.py | background_jobs.py, jobs/, job_process_result.py, lib/cancellable_lock.py | job_records, gpu_lock, job_checkpoints | test_background_jobs.py, test_process_jobs.py, test_job_limits.py, test_jobs_service.py, test_api_job_cancel.py, test_gpu_process_job.py, test_job_process_result.py | jobs*.spec.ts, job-reattach.spec.ts | background-jobs.md |
| Settings / backups | Settings.tsx, settings/, libraryAdmin/ | settings_routes.py, backup_routes.py, library_admin_routes.py | settings_service.py, auto_backup_service.py, auto_backup_settings.py, auto_backup_copies.py, auto_backup_files.py, drama_restore_service.py, backup_import_service.py | storage.py, lib/settings_schema.py | app_settings | test_settings_service.py, test_auto_backup_*.py, test_api_backups.py, test_user_backup.py | settings*.spec.ts, backups.spec.ts | database.md, runbook.md |
| Auth / permissions | Login.tsx, Admin.tsx, settings/ExtensionDevicesCard.tsx | auth_routes.py, admin_users_routes.py, sharing_routes.py, device_token_routes.py | auth_service.py, oidc_service.py, ownership_service.py, device_token_service.py | api/auth.py, action_tiers.py, device_tokens.py | users, user_permissions, auth_sessions, audit_log, extension_device_tokens | test_api_permissions.py, test_auth_service.py, test_auth_login.py, test_api_admin_users.py, test_device_tokens.py | signin.spec.ts, admin-audit-users.spec.ts, extension-devices*.spec.ts | route-permissions.md, remote-access-decision.md |
| Installer / updates | Settings.tsx, settings/AppUpdatesCard.tsx | update_routes.py | update_service.py | installer/ (service.py is big), portable.py | n/a | test_installer_service.py, test_installer_remote.py, test_installer_iss.py, test_update_service.py | app-updates.spec.ts | windows-installer-design.md, RELEASE.md |
| Export | workspace/stages/ExportStage.tsx | export_routes.py | export_service.py, media_export_service.py | subtitle_formats.py, video_export.py, vocab_export.py, glossary_io.py | n/a | test_export_service.py, test_export_formats.py, test_api_export_ass.py, test_api_media_export.py, test_video_export.py, test_vocab_export.py | export-*.spec.ts | user-guide.md |
| Language packs | settings/LanguagePacksCard.tsx | language_pack_routes.py | language_pack_service.py | language_packs.py, language_pack_data/ (JSON glossaries) | app_settings | test_language_packs.py, test_api_language_packs.py | language-packs*.spec.ts, settings-language-packs*.spec.ts | user-guide.md |
| Reader | Reader.tsx, reader/, SavedMangaReader.tsx | reader_routes.py | reader_service.py | reader.py | n/a | test_reader_service.py, test_api_reader_m4.py, test_reader_no_segmenter.py | reader.spec.ts | n/a |
| Assistant | Assistant.tsx, assistant/ | assistant_routes.py | maintenance_assistant_service.py, assistant_settings_service.py, assistant_tools_service.py, assistant_repo_access.py, assistant_diagnosis_service.py, assistant_backlog_service.py, assistant_roles_service.py | n/a | app_settings | test_maintenance_assistant.py, test_assistant_roles.py | assistant*.spec.ts | n/a |
| Jellyfin | settings/JellyfinSection.tsx | jellyfin_routes.py | jellyfin_service.py | n/a | app_settings (`jellyfin` key; API key in .env) | test_jellyfin.py | jellyfin.spec.ts | n/a |

Layer rules for the folders above: `docs/api-and-services.md`. Frontend stages:
`frontend/src/pages/workspace/stages/README.md`.

## Fragile seams
- db: `configure_library_dir` rebinds `LIBRARY_DIR`, `DB_PATH` and friends, so read them as `db.X`
  when used and never copy them. A read-then-write transaction starts with `BEGIN IMMEDIATE`. See `docs/database.md`.
- Process-job workers (`diarize.py`, `resegment.py`, `voice/dub.py`, `voice/dub_narration.py`, `services/transcribe_pipeline.py`, `services/transcribe_service.py`, `services/retranscribe_worker.py`, `services/compare_hear_worker.py`) stay
  top-level and picklable: Windows starts them in a fresh interpreter. Every process job starts with spawn,
  its own process group (`start_own_process_group()` first in the worker) and `kill_whole_tree=True`, so a bad
  worker fails on Linux CI too. See `docs/background-jobs.md`.
- `background_jobs`: the `_jobs` dict under `_lock` (both in `jobs/job_table.py`) is the authority. Never start a thread or process while holding `_lock`.
- Warm transcription workers (`jobs/warm_worker.py`): a GPU transcription runs in a kept spawned worker when one with the same settings is idle, at most one worker per GPU slot and fewer when the card cannot carry the separator each one keeps (`card_budget`). Every item on its long-lived queue carries the job's number, each job runs in a fresh context copy, and the worker exits after any failure, fallback or cancel. An idle worker is retired before any GPU job that cannot use it is admitted, so a new GPU start path must call `warm_worker.retire_idle` first.
- Tests patch the module that uses a name, not a front door that re-exports it.
- Keep the removed-name sets, because old saved settings still name removed engines:
  `REMOVED_ENGINES` (`engine_backends/engine_registry.py`), `REMOVED_VOICE_ENGINES` (`voice/dub.py`),
  `REMOVED_ASR_BACKENDS` (`services/asr_options_service.py`), `REMOVED_SOURCES` (`sources/registry.py`).

## Splitting files
64 KB is a soft limit that only warns, not a target. A new file must own one domain or workflow, cut what a model reads for a
typical task, and be easier to edit than the whole; no forwarding-only modules, no grab-bags of helpers.
Done means:
1. Moved, not rewritten: behaviour, messages and signatures unchanged.
2. Callers import from the new module. Old names stay importable only through the front doors
   `db`, `engine_backends`, `api.schemas`, or where the brief says.
3. A test patch on a moved name points at the module that now uses it; no assertion removed or loosened; same pass count.
4. Inside the `db` package call other db functions as `db.<name>`.
5. The new module starts with a one-line docstring (`python tools/repo_map.py` reads it); a new top-level `.py` is also added to the
   expected-files list (`expected_files.EXPECTED_TOP_LEVEL_FILES`: one sorted line per name; check the current location first).
6. Run the area's tests, then `tests/test_static_analysis.py`, `tests/test_split_guards*` (if present) and
   `tests/test_diagnostics_and_export.py`, and report counts; CI runs the full suite.

## Recipes

**Add a Settings row**
1. `lib/settings_schema.py`: one `_pref(...)` row (name, type, default, label, help, tab, section).
2. `api/schemas/system.py`: the field on `SettingsPreferences` and on `SettingsUpdateRequest`.
3. `frontend/src/types/settings.ts`: the same field.
4. `services/settings_service.py` (a choices lambda or helper, only if the row needs one) and the code that reads it via
   `settings_service.get(...)`.
5. `tests/test_settings_parity.py::test_preference_defaults` lists every default; add the row there. Editing only
   `lib/settings_schema.py` fails that test.
6. A schema or UI test for the row (`tests/test_settings_schema.py`, `frontend/src/pages/settings/*.test.ts`) and a line in the user guide (`docs/user-guide*.md`).
7. Run: `tests/test_settings_schema.py tests/test_settings_parity.py tests/test_settings_service.py tests/test_api_settings_schema.py`.

**Add an API route**
1. Router in `api/routers/<area>_routes.py`, thin: validate, call `services/<area>_service.py`, return a model from `api/schemas/`.
2. Declare exactly one of `require_permission(...)`, `public_route()`, `local_only()` or `authenticated()` on it.
3. `python tools/route_table.py --write` (never without `--write`: it prints 36 KB), then `git diff docs/route-permissions.md`.
4. A new router file starts with a one-line docstring.
5. Run `tests/test_api_permissions.py` and the area's API test. The undeclared-route failure there is a bare IndexError:
   it means a route has no declaration.

**Add a language-pack entry**
1. Add `{"source": ..., "en": ..., "category": ..., "note": ...}` to `entries` in `language_pack_data/<id>.json`
   (look at a neighbour; `note` is optional). Bump `version` only if the file says to.
2. Run `tests/test_language_packs.py tests/test_api_language_packs.py`. No code change is needed for an entry.

**Add a library.db column**
1. `db/__init__.py` `init_db`: `ALTER TABLE ... ADD COLUMN` (a change that cannot be an ADD COLUMN is a table rebuild: stop and ask).
2. Add `(table, column)` to `_INIT_DB_MIGRATED_COLUMNS` in `tests/test_db.py`; a guard test fails if you forget.
3. Read and write it with `db.<name>` helpers only. Run `tests/test_db.py`.

**Fix a flaky wall-clock test**
1. Do not lengthen a sleep or loosen a bound. Find what the test waits for.
2. Replace the sleep with `threading.Event`s: the code under test sets "started", the test releases it, and the test asserts
   on state ("the read returned while the probe was still blocked"), with `wait(10)` only as a hang guard.
3. When two events can land between two polls, assert `>=` the count, not `==`. Example: `tests/test_job_force_stop.py::TestGpuProbeOutsideTheLock`.

**Add an optional dependency**
1. Entry in `diagnostics.OPTIONAL_DEPENDENCIES` (key = the real pip name; the Install button runs `pip install <key>`).
2. Import it lazily and guard with a clear "not installed" error; tests use `pytest.importorskip`.
3. Add it to `requirements-optional.txt` like its neighbours. Run `tests/test_diagnostics_service.py`.

**Rename a UI label across specs and docs**
1. `git grep -n -F "<old text>" -- frontend/src frontend/e2e docs` (add `-m 3` per file if long).
2. Change the component, then every `getByText`/`getByRole` in `frontend/e2e/*.spec.ts` and the `*.test.tsx` files, then the docs.
3. `cd frontend && npx tsc -b && npm run lint && npx vitest run <changed tests>`; run the one Playwright spec for that page (`docs/testing-and-ci.md`).
