# Agent notes (OpenCode and other local-model agents)

OpenCode reads this file, not CLAUDE.md. Read `CLAUDE.md` and
`docs/small-model-checklist.md` before changing anything; they are the rules.

- Your context is small. Do not read many files. Run `python tools/repo_map.py`
  (or `--find "<word>"`, or `python tools/repo_map.py <module>`) on demand.
- Work in small tasks: one task, a handful of files. If it needs more than about
  four files, stop and split it.
- While iterating run only the matching file: `python -m pytest -q tests/test_<area>.py`.
  Per-area commands: `docs/testing-and-ci.md`. Run the full suite once at the end
  (command in CLAUDE.md).
- Never skip or weaken a test.

Rules from CLAUDE.md that bite most:
- Match LLM results back to lines by explicit id, never by list position.
- No secrets in logs, URLs or stored errors: keys go in headers; pass error text
  through `translate_engines.redact_secrets`.
- Every outbound HTTP call has `timeout=`.
- Every API route declares exactly one of `require_permission(...)`,
  `public_route()` or `local_only()`.

## Where to look

Find your area, then open the router and service first. Cells hold names inside
the folder named in the header, comma separated; a trailing `/` is a folder and
`*` a glob. `n/a` means there is none. Root modules sit at the repo top level.

Big files, use `python tools/repo_map.py <name>` (then `--part N`), never open them
whole: `db`, `background_jobs`, `diagnostics`, `cli`, `core`, plus every file listed in
`OVERSIZED_MODULE_BYTES` in `tests/test_static_analysis.py` (e.g. `scanlate`,
`bulk_translate`, `services/transcribe_service`).

| Area | UI `frontend/src/pages/` | Router `api/routers/` | Service `services/` | Root / domain module | db (`db.py` tables) | Tests `tests/` | E2E `frontend/e2e/` | Doc `docs/` |
|---|---|---|---|---|---|---|---|---|
| Library / titles | Library.tsx, LibraryTools.tsx, libraryParity/ | library_routes.py, drama_routes.py | library_service.py, drama_service.py, ownership_service.py | title_library.py | dramas, series | test_library_service.py, test_drama_service.py, test_api_dramas.py, test_title_library.py | library*.spec.ts | database.md |
| Transcription (ASR) | workspace/stages/TranscribeStage.tsx | transcribe_routes.py, asr_options_routes.py, diarization_routes.py | transcribe_service.py, asr_options_service.py, diarization_service.py | asr_backend.py, vad_segments.py, forced_align.py, diarize.py | dramas, lines | test_transcribe_service.py, test_asr_backend.py, test_diarize.py | transcribe-*.spec.ts | engine-backends.md, asr-experiments.md |
| Translation + engines | Translate.tsx, workspace/stages/TranslateStage.tsx | translate_routes.py, translate_run_routes.py, engine_routing_routes.py | translate_service.py, translate_run_service.py, engine_routing_service.py | translate_engines.py, engine_backends/, bulk_translate.py, translation_guide.py | lines, translation_versions, presets, usage_log | test_translate_engines.py, test_translate_run_service.py, test_bulk_translate.py, test_engine_routing.py | translate*.spec.ts | engine-backends.md |
| Review / lines | workspace/stages/ReviewStage.tsx, workspace/stages/review/ | lines_routes.py, review_lines_routes.py, review_extras_routes.py, review_records_routes.py | lines_service.py, review_lines_service.py, line_tools_service.py | line_tools.py, auto_qc.py, resegment.py | lines, line_history, line_provenance | test_lines_service.py, test_review_lines_service.py, test_api_lines.py | review-*.spec.ts | api-and-services.md |
| Characters / glossary | workspace/stages/CharactersPanel.tsx, workspace/stages/GlossaryPanel.tsx | characters_routes.py, glossary_routes.py, series_people_routes.py | characters_service.py, glossary_service.py, series_people_service.py | translation_memory.py | characters, series_characters, glossary_terms | test_characters_service.py, test_glossary_service.py, test_api_glossary.py | characters-*.spec.ts, glossary-*.spec.ts | database.md |
| Live translate | Live.tsx, live/ | live_routes.py | live_service.py | live_translate.py, live_fetch.py | n/a | test_live_service.py, test_live_translate.py, test_live_fetch.py, test_api_live.py | live*.spec.ts | n/a |
| Dubbing | workspace/stages/DubStage.tsx, workspace/stages/VoiceClonePanel.tsx | dub_routes.py, voice_clone_routes.py, narration_routes.py | dub_service.py, voice_clone_service.py, narration_service.py | dub.py, voice_id.py | voice_bank, characters | test_dub.py, test_dub_service.py, test_api_dub.py, test_voice_id.py | dub-*.spec.ts, voice-*.spec.ts | engine-backends.md |
| Scanlate / OCR | Comic.tsx, comic/, SavedManga.tsx | scanlate_routes.py, comic_routes.py, saved_comics_routes.py | scanlate_run_service.py, scanlate_pages_service.py, comic_view_service.py | scanlate.py, ocr.py, hardsub_ocr.py | pages, bubbles | test_scanlate.py, test_ocr.py, test_hardsub_ocr.py, test_api_comic_viewer.py | scanlate*.spec.ts, comic*.spec.ts | n/a |
| Sources / adapters | Sources.tsx, sources/, Discover.tsx | source_routes.py, sources_*_routes.py | source_service.py, sources_*_service.py | sources/ (adapters in sources/adapters/), page_fetch.py | `sources.db` via sources/store.py | test_sources_*.py, test_api_sources_*.py, test_source_service.py | sources*.spec.ts, source-*.spec.ts | adding-source.md, content-sources.md |
| Benchmark lab | Benchmark.tsx, benchmark/ | benchmark_routes.py, model_reeval_routes.py, model_registry_routes.py | benchmark_lab_service.py, model_reeval_service.py, model_registry_service.py | benchmark.py, asr_benchmark.py | benchmark_*, model_candidates, model_decisions | test_benchmark_lab.py, test_benchmark.py, test_model_reeval.py, test_api_benchmark.py | lab-*.spec.ts, bench-sections.spec.ts | database.md |
| Diagnostics / installs | Diagnostics.tsx, diagnostics/ | diagnostics_routes.py, diagnostics_installs_routes.py, diagnostics_gaps_routes.py | diagnostics_service.py, diagnostics_installs_service.py, diagnostics_gaps_service.py | diagnostics.py, check_setup.py | n/a | test_diagnostics_service.py, test_api_diagnostics_installs.py, test_install_presets.py | diagnostics*.spec.ts | runbook.md |
| Jobs | Jobs.tsx, jobs/, workspace/JobPill.tsx | jobs_routes.py, job_stage_routes.py, events_routes.py | jobs_service.py, job_checkpoint_service.py, job_timing_service.py | background_jobs.py | job_records, gpu_lock, job_checkpoints | test_background_jobs.py, test_jobs_service.py, test_api_job_cancel.py | jobs*.spec.ts, job-reattach.spec.ts | background-jobs.md |
| Settings / backups | Settings.tsx, settings/, libraryAdmin/ | settings_routes.py, backup_routes.py, library_admin_routes.py | settings_service.py, auto_backup_service.py, backup_import_service.py | storage.py | app_settings | test_settings_service.py, test_auto_backup_service.py, test_api_backups.py, test_user_backup.py | settings*.spec.ts, backups.spec.ts | database.md, runbook.md |
| Auth / permissions | Login.tsx, Admin.tsx | auth_routes.py, admin_users_routes.py, sharing_routes.py | auth_service.py, oidc_service.py, ownership_service.py | api/auth.py, action_tiers.py | users, user_permissions, auth_sessions, audit_log | test_api_permissions.py, test_auth_service.py, test_auth_login.py, test_api_admin_users.py | signin.spec.ts, admin-audit-users.spec.ts | route-permissions.md, remote-access-decision.md |
| Installer / updates | Settings.tsx, settings/AppUpdatesCard.tsx | update_routes.py | update_service.py | installer/ (service.py is big), portable.py | n/a | test_installer_service.py, test_installer_iss.py, test_update_service.py | app-updates.spec.ts | windows-installer-design.md, RELEASE.md |

Layer rules for the folders above: `docs/api-and-services.md`. Frontend stages:
`frontend/src/pages/workspace/stages/README.md`.
