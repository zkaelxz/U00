# File organization

A short index: one line per package, with its entry point. Every non-test `.py` file at the top level,
in `services/` and in `api/routers/` is named below, because the hook in `.claude/hooks/file-organization-check.py`
warns when a file's name is missing from this file. A new file there gets its name added to the right group.
Filenames are unique across folders; `test_*.py` lives in `tests/`. The earlier per-file descriptions are kept in
`docs/archive/file-organization-full.md`.

## Packages

| Path | What it is | Entry point |
|---|---|---|
| `api/` | FastAPI app: routers (`api/routers/*_routes.py`), Pydantic models (`api/schemas/`, plus `api/*_schemas.py`), auth (`api/auth.py`) | `python -m api` (`api/__main__.py`, `api/server.py`) |
| `services/` | UI-free application logic shared by `api/` and `cli.py`; raises the errors in `service_errors.py` | called by routers and `cli.py` |
| `engine_backends/` | translation engines by provider, retry/redaction helpers (`shared.py`) | `translate_engines.py` re-exports it |
| `sources/` | site adapters (`sources/adapters/`), fetch ladder, source store | `sources/registry.py`, `sources/front_door.py` |
| `frontend/` | React + Vite + TypeScript app; built output `frontend/dist` is served by the API | `frontend/src/main.tsx` |
| `extension/` | browser-side JavaScript (Chrome extension), not Python | `extension/manifest.json`, bridge in `page_server.py` |
| `installer/` | Windows installer build (Inno Setup, bundled Python/Caddy/WinSW) | `installer/build_installer.py` |
| `deploy/` | Caddy template for household access | `deploy/caddy/Caddyfile.template` |
| `scripts/` | build, probe and migration helpers | per script |
| `tools/` | developer tools, not shipped: `repo_map.py` prints the symbol map for small-context models (output not committed) | `python tools/repo_map.py --help` |
| `tests/` | pytest suite; tests enforce most rules in `CLAUDE.md` | `python -m pytest -q` |
| `docs/` | design notes, status and route table; `docs/archive/` is history | `docs/README.md`, `docs/STATUS.md` |
| `library/` | your data (gitignored, created automatically) | n/a |

## Top-level modules

**App entry & infrastructure**: `cli.py`, `run_tests.py`, `core.py`, `db.py`, `background_jobs.py`, `applog.py`, `diagnostics.py`,
`check_setup.py`, `process_guard.py`, `portable.py`, `storage.py`, `benchmark.py`, `action_tiers.py`

**ASR, transcription & alignment**: `asr_backend.py`, `asr_benchmark.py`, `audio_preprocess.py`, `mixed_language.py`, `vad_segments.py`,
`forced_align.py`, `word_align.py`, `ollama_unload.py`, `raw_transcript.py`, `resegment.py`, `sensevoice_tags.py`, `sensitivity_preset.py`, `diarize.py`,
`voice_id.py`

**Translation & quality (engines live in `engine_backends/`; `translate_engines.py` is its front door)**: `translate_engines.py`, `translation_guide.py`, `translation_memory.py`, `auto_qc.py`, `en_cleanup.py`,
`emotion.py`, `bulk_translate.py`, `live_translate.py`, `live_fetch.py`

**Dubbing, subtitles & video**: `dub.py`, `video_export.py`, `media_inspect.py`, `subtitle_formats.py`, `video_download.py`

**OCR, scanlation & reading**: `ocr.py`, `scanlate.py`, `hardsub_ocr.py`, `segment.py`, `dictionary.py`, `reader.py`

**Story & learning**: `universe_wiki.py`, `story_context.py`, `qa.py`, `line_tools.py`, `debug_view.py`, `adaptive_style.py`,
`vocab_export.py`

**Discovery, sources & I/O**: `page_fetch.py`, `metadata_lookup.py`, `bulk_import.py`, `title_library.py`, `known_sites.py`, `navigator.py`,
`epub_io.py`, `page_server.py`

## services/

`artifact_service.py`, `asr_options_service.py`, `assistant_github_service.py`, `assistant_pytest_guard.py`,
`assistant_roles_service.py`, `auth_service.py`, `auto_backup_service.py`, `backup_import_service.py`,
`benchmark_lab_service.py`, `blocked_retry_service.py`, `bug_report_service.py`, `capped_body.py`,
`characters_service.py`, `comic_view_service.py`, `compare_transcription_service.py`, `cover_art_service.py`,
`delete_service.py`, `diagnostics_gaps_service.py`, `diagnostics_installs_service.py`,
`diagnostics_service.py`, `diarization_service.py`, `discover_catalog_service.py`,
`discover_lookup_service.py`, `disk_usage_service.py`, `drama_service.py`, `dub_service.py`,
`egress_proxy.py`, `engine_routing_service.py`, `event_stream_service.py`, `export_service.py`,
`extension_service.py`, `fixflag_transcribe.py`, `glossary_retranslate_service.py`, `glossary_service.py`, `jellyfin_service.py`,
`job_checkpoint_service.py`, `job_stage_service.py`, `job_timing_service.py`, `jobs_service.py`, `library_admin_service.py`,
`library_service.py`, `line_ai_service.py`, `line_provenance_service.py`, `line_tools_service.py`,
`lines_service.py`, `live_service.py`, `lncrawl_service.py`, `maintenance_assistant_service.py`,
`media_export_service.py`, `media_peaks_service.py`, `media_playback_service.py`, `media_upload_service.py`,
`metadata_research_service.py`, `metadata_service.py`, `model_reeval_service.py`, `model_registry_service.py`,
`narration_service.py`, `notification_service.py`, `notion_service.py`, `novel_attach_service.py`,
`novel_files_service.py`, `oidc_service.py`, `ownership_service.py`, `page_import_limits.py`,
`reader_service.py`, `remote_health_service.py`, `restructure_service.py`, `retime_service.py`,
`review_extras_service.py`, `review_jobs_service.py`, `review_lines_service.py`, `review_records_service.py`,
`safe_fetch.py`, `saved_comics_service.py`, `scanlate_pages_service.py`, `scanlate_render_service.py`,
`scanlate_run_service.py`, `series_people_service.py`, `service_errors.py`, `settings_service.py`,
`shutdown_service.py`, `source_domains_service.py`, `source_service.py`, `sources_extraction_service.py`,
`sources_import_service.py`, `sources_registry_service.py`, `sources_save_service.py`,
`sources_search_service.py`, `sources_signin_service.py`, `sources_tools_service.py`,
`sources_tracking_service.py`, `sources_url_service.py`, `speech_coverage_service.py`,
`stronger_engine_service.py`,
`transcribe_service.py`, `translate_run_service.py`, `translate_service.py`, `translation_version_service.py`,
`update_service.py`, `url_guard.py`, `url_media_service.py`, `usage_recost_service.py`,
`voice_bank_audio_service.py`, `voice_clone_service.py`, `vram_service.py`, `web_search_service.py`,
`workflow_service.py`, `workspace_job_service.py`

## api/

Core: `api_config.py`, `auth.py`, `background.py`, `error_handlers.py`, `llm_slots.py`, `server.py`,
`static_frontend.py`

`api/schemas/`: `characters.py`, `common.py`, `library.py`, `reader.py`, `review.py`, `sources.py`, `system.py`,
`transcribe.py`, `translate.py`, `voice.py`; other schema modules sit beside it as `api/*_schemas.py`.

### api/routers/

`admin_users_routes.py`, `artifact_routes.py`, `asr_options_routes.py`, `assistant_github_routes.py`,
`assistant_routes.py`, `auth_routes.py`, `backup_routes.py`, `benchmark_routes.py`, `blocked_retry_routes.py`,
`bug_report_routes.py`, `characters_routes.py`, `comic_routes.py`, `delete_routes.py`,
`diagnostics_gaps_routes.py`, `diagnostics_installs_routes.py`, `diagnostics_routes.py`,
`diarization_routes.py`, `discover_lookup_routes.py`, `discover_routes.py`, `disk_usage_routes.py`,
`drama_routes.py`, `dub_routes.py`, `engine_routing_routes.py`, `events_routes.py`, `export_routes.py`,
`extension_routes.py`, `glossary_routes.py`, `jellyfin_routes.py`, `job_stage_routes.py`, `jobs_routes.py`,
`library_admin_routes.py`, `library_routes.py`, `line_ai_routes.py`, `lines_routes.py`, `live_routes.py`,
`media_routes.py`, `metadata_research_routes.py`, `metadata_routes.py`, `model_reeval_routes.py`,
`model_registry_routes.py`, `narration_routes.py`, `notification_center_routes.py`, `notification_routes.py`,
`notion_routes.py`, `novel_files_routes.py`, `novel_routes.py`, `reader_routes.py`, `restructure_routes.py`,
`review_extras_routes.py`, `review_jobs_routes.py`, `review_lines_routes.py`, `review_records_routes.py`,
`saved_comics_routes.py`, `scanlate_routes.py`, `series_people_routes.py`, `settings_routes.py`,
`sharing_routes.py`, `source_domains_routes.py`, `source_routes.py`, `sources_catalog_routes.py`,
`sources_extraction_routes.py`, `sources_import_routes.py`, `sources_local_routes.py`,
`sources_search_routes.py`, `sources_tools_routes.py`, `stronger_engine_routes.py`, `system_routes.py`,
`transcribe_routes.py`, `translate_routes.py`, `translate_run_routes.py`, `translation_version_routes.py`,
`update_routes.py`, `usage_recost_routes.py`, `voice_bank_audio_routes.py`, `voice_clone_routes.py`,
`web_search_routes.py`, `workflow_routes.py`

## Rules of thumb

- UI code goes in `frontend/`; logic lives at the top level, in `services/` or in `sources/` so it stays testable without a UI.
- Logic the API and the CLI both need goes in `services/`.
- Nothing writes outside `library/` except exports you explicitly download.
- Optional dependencies are imported inside functions, never at module top level.
- Adding a new top-level module, `services/*.py` or `api/routers/*.py` file? Add its name above in the same PR.
