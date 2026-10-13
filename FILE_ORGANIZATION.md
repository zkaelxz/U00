# File organization

A short index: one line per package, then every non-test `.py` file in the directories below with its group. A file's
name must appear exactly once, in the section of its own folder (`tests/test_file_organization.py` checks that each listed
name is a real file there and that no module is missing); the hook in `.claude/hooks/file-organization-check.py` warns
earlier. A new file gets one bullet, in the right group. `test_*.py` lives in `tests/`. The earlier long per-file
descriptions are kept in `docs/archive/file-organization-full.md`.

## Packages

| Path | What it is | Entry point |
|---|---|---|
| `api/` | FastAPI app: routers (`api/routers/*_routes.py`), Pydantic models (`api/schemas/`, plus `api/*_schemas.py`), auth (`api/auth.py`) | `python -m api` (`api/__main__.py`, `api/server.py`) |
| `lib/` | shared helpers with no domain knowledge (`errors.py`, `url_guard.py`, `capped_body.py`, `release_signature.py`, `http.py`, `proc.py`, `proc_kill.py`, `link_new.py`, `cancellable_lock.py`, `settings_schema.py`, `gzip_text.py`, `ct2_repo.py`, `model_offline.py`); imports nothing of the app's own code | imported by every layer |
| `services/` | UI-free application logic used by `api/`; raises the errors in `lib/errors.py` | called by routers |
| `jobs/` | the job machinery behind `background_jobs.py`: `jobs/job_table.py` holds the in-memory job table and its lock; `jobs/job_launch.py` starts jobs (`start_job`, `start_process_job`) and holds the exclusive/maintenance/stopping refusals; `jobs/job_runner.py` runs them and handles how they end; `jobs/gpu_queue.py` is the in-process GPU wait line; `jobs/job_settings.py` the job settings; `jobs/process_worker.py` what a process-job worker calls in its child; `jobs/job_store.py` writes and closes every `job_records` row (transition writes with retry, heartbeat, dead-owner sweep, exit flush); `jobs/gpu_slots.py` is the one path to the cross-process `gpu_lock` slots (acquire, release, heartbeat, reap, status); `jobs/user_caps.py` caps one user's running and queued jobs | `job_table._mirror_locked`, `jobs_service.sweep_stale_job_records`; `gpu_slots` from `background_jobs`, `live_whisper.py`, `api/server.py` startup |
| `engine_backends/` | translation engines by provider, retry/redaction helpers (`shared.py`); `echo.py` is the key-free Echo (testing) engine, Developer Mode only | `translate_engines.py` re-exports it |
| `sources/` | site adapters (`sources/adapters/`), fetch ladder, source store | `sources/registry.py`, `sources/front_door.py` |
| `frontend/` | React + Vite + TypeScript app; built output `frontend/dist` is served by the API | `frontend/src/main.tsx` |
| `extension/` | browser-side JavaScript (Chrome extension), not Python; `site_access.js` decides which origins the extension may ask the person to grant (no private or LAN hosts); `shared.js` holds the status-line and error-wording helpers the popup and options pages share | `extension/manifest.json`, bridge in `page_server.py` |
| `installer/` | Windows installer build (Inno Setup, bundled Python/Caddy/WinSW); `installer/service.py` runs the Windows service (`Services`; its siblings are `service_layout.py` folder safety, `service_config.py` config, XML and Caddyfile text, `service_control.py` sc/icacls/netsh commands, `service_checks.py` health checks and `service_cli.py` the command line) and `installer/setup_lock.py` holds the named mutexes that keep Setup and the service commands apart | `installer/build_installer.py` |
| `deploy/` | Caddy template for household access | `deploy/caddy/Caddyfile.template` |
| `scripts/` | build, probe and migration helpers | per script |
| `tools/` | developer tools, not shipped: `repo_map.py` prints the symbol map for small-context models (output not committed); `route_table.py` generates the table in `docs/route-permissions.md`; `check_change.py` maps your changed files to the tests and guards to run, as a lean pytest command under 2 KB (`docs/small-model-checklist.md`); `ci_changes.py` decides which CI jobs a change needs (`docs/testing-and-ci.md`); `windows_script_checks.py` runs setup.bat, start.bat and uninstall.bat on the Windows CI runner (`docs/testing-and-ci.md`); `self_test.py` runs the owner's hardware checks in one go and writes a redacted report (`docs/runbook.md` 2c); `asr_bench.py` runs one window of a title's audio through several transcription recipes (dry run unless `--yes`, takes the GPU slot, saves nothing to the title) and writes a comparison report, with its recipe grammar and backend registry in `asr_bench_recipes.py` (`docs/asr-experiments.md`); `translate_context_bench.py` re-translates one window of a title under several batch-size/context configs with Claude (dry run unless `--yes`, spend-capped, saves nothing to the title) and writes a comparison report; `ja_whisper_models.py` fetches or converts and checks Japanese Whisper fine-tune folders for the Benchmark Lab (`docs/asr-experiments.md`) | `python tools/repo_map.py --help`, `python tools/check_change.py`, `python tools/route_table.py --write`, `python tools/ja_whisper_models.py --help`, `python -m tools.self_test`, `python -m tools.translate_context_bench TITLE_ID`, `python -m tools.asr_bench TITLE_ID --config whisper` |
| `docs/` | design notes, status and route table; `docs/archive/` is history | `docs/README.md`, `docs/STATUS.md`, `docs/user-guide.md` |
| `library/` | your data (gitignored, created automatically) | n/a |

## Top-level modules

**App entry & infrastructure**

- `action_tiers.py`
- `applog.py`
- `background_jobs.py`
- `job_force_stop.py` (Force stop for a thread job that ignores Cancel: closes its record, abandons the worker, refuses a rerun while it lives)
- `gpu_probe.py` (nvidia-smi readings taken before the job table's lock, used once by the GPU-slot decision)
- `job_process_kill.py` (stopping a job's worker process; `background_jobs` and `jobs/job_runner.py` import the `kill_tree` wrapper here, which adds the app-log warning on top of `lib/proc_kill.py`)
- `job_process_result.py` (the channel a process-job worker sends its result through: a large result travels as a file so a child killed mid-write cannot hang the watcher)
- `benchmark.py`
- `check_setup.py`
- `core.py` (the `Line` model, line rows and saved-version restore, SRT text; moved names still resolve through it)
- `db/` (package: `__init__.py` holds what has not moved out yet and re-exports the rest, so callers keep `db.<name>`)
  - `db/app_settings.py` (cross-process app settings, the metadata research cache and results, field provenance)
  - `db/auth.py` (auth storage: users, permissions, sessions, audit log, and drama/series ownership and sharing fields)
  - `db/benchmark.py` (accuracy benchmark cases and runs, Benchmark Lab sessions and results)
  - `db/bulk_jobs.py` (bulk batch-API / off-peak translation jobs: job rows, submitted lines, results by line id)
  - `db/comic_pages.py` (comic pages and their text regions for scanlation and typesetting: page rows, id-preserving bubble edits, the legacy full replace)
  - `db/dramas.py` (library titles: create, update, delete and list a drama, the visibility filter drama queries share, library-wide stats and line search, custom tags)
  - `db/glossary.py` (series and their shared glossary terms and ignore list, the per-series translation memory)
  - `db/line_annotations.py` (per-line annotations a translation run produces: translation notes, consistency-check results, emotion tags)
  - `db/lines.py` (a drama's lines: the id-keyed save, full sync or field-scoped, the compare-and-set edits, the per-line tables a sync carries along, the loaders)
  - `db/library_folder.py` (the library folder on disk: `fsync_dir`, and the start-up check that puts back a library an interrupted restore left renamed aside)
  - `db/media_journal.py` (the media journal for backup imports and restores: staging folders, the journal of drama folders moved in, crash recovery, claiming a new drama's folder)
  - `db/job_records.py` (the job_records mirror of background job status)
  - `db/reading.py` (profiles and what each has read: resume points, reading history, personal notes per drama)
  - `db/speakers.py` (characters and speakers: speaker rename and merge with its undo, series characters and voice links, the voice bank)
  - `db/style.py` (adaptive style: the translator's edit samples, the learned style profile and its restorable history)
  - `db/usage.py` (usage and cost log, monthly spend and its reset, re-costing, the standalone translate tool's history)
- `dependency_updates.py` (installed versions against PyPI: update classification, constraints.txt and requirement minimums, known upgrade and install limitations)
- `diagnostics.py`
- `diagnostics_report.py` (which model engines are installed or reachable, and the plain-text support report with its redaction)
- `diagnostics_torch.py` (is the GPU usable and is the torch family installed right: GPU readout, nvidia-smi probes, torch setup helpers)
- `upgrade_check.py` (try a package upgrade in a throwaway venv and run the tests there before touching the real environment)
- `expected_files.py`
- `install_plan.py` (what a Diagnostics install would change, and whether to run it now or at restart)
- `install_registry.py` (package keys an install may name, and their pip argv)
- `pending_install.py` (installs queued for the next start, and the apply step `python -m pending_install` that start.bat and the launcher run first; standard library only)
- `pending_install_child.py` (the apply step's short-lived helper: derive the pip command, redact output)
- `portable.py`
- `process_guard.py`
- `qwen3_native.py`
- `run_tests.py`
- `storage.py`

**ASR, transcription & alignment**

- `anime_whisper.py` (the opt-in Japanese-only anime-whisper backend: window decoding, forced alignment, words placed in speech spans; personal non-commercial use)
- `asmr_vad.py`
- `asr_backend.py`
- `asr_benchmark.py`
- `audio_preprocess.py`
- `audio_extract.py` (ffmpeg: pull the audio track out of a video, cut a short slice out of an audio file)
- `diarize.py`
- `forced_align.py`
- `line_alignment.py` (a user transcript plus Whisper timing become timed lines: merging short lines, character-time reconstruction, coverage checks)
- `gpu_wait_message.py` (the waiting text when another program is using the GPU)
- `long_line_split.py` (last-resort even split for lines no punctuation or pause can cut, and the plain-words reasons a re-split found nothing)
- `memory_headroom.py` (the Settings keep-free VRAM/RAM check the model loaders call)
- `mixed_language.py`
- `novel_text.py` (the raw source novel: chunking it into narration lines, paragraph ends, and use as transcription context)
- `ollama_unload.py`
- `raw_transcript.py`
- `resegment.py`
- `segment_splitting.py` (cuts long Whisper segments into subtitle-sized lines on sentence, clause and word-timing boundaries; the stored word times a line keeps)
- `sensevoice_tags.py`
- `sensitivity_preset.py`
- `vad_segments.py`
- `voice_id.py`
- `cuda_runtime_path.py` (puts the folders holding cuBLAS 12 and cuDNN 9 on the Windows DLL search path so CTranslate2 can use the GPU; inspects folders only)
- `whisper_hallucination.py` (drops the stock-phrase and repeated-phrase segments Whisper invents on silence)
- `whisper_models.py` (loads or releases the Whisper model on the right device and explains why it fell back to the CPU; the model list, GPU status and download-error diagnosis)
- `whisper_transcribe.py` (speech-to-text for timing: local faster-whisper, the opt-in Groq cloud path, initial prompt and sensitivity settings)
- `word_align.py`

**Translation & quality (engines live in `engine_backends/`; `translate_engines.py` is its front door)**

- `auto_qc.py`
- `bulk_apply.py` (applies finished bulk results by line id, per kind)
- `bulk_poll.py` (polling, scheduled off-peak run, resume, cancel)
- `bulk_providers.py`
- `bulk_reflect.py` (the staged Reflect pipeline as bulk jobs)
- `bulk_translate.py` (building and submitting bulk requests)
- `emotion.py`
- `en_cleanup.py`
- `glossary_io.py` (term categories/policies and glossary file import/export)
- `glossary_phonetic.py` (sound-alike keys and edit distance for names in kana, kanji, hanzi, Hangul and Latin; offline, all thresholds in one block)
- `language_packs.py` (built-in language pack glossaries, read from the JSON files in `language_pack_data/`)
- `live_audio.py` (Live chunk WAV windows: overlap tail, and the cap on audio per Whisper call)
- `live_agreement.py` (streaming recognition: commits stable words from overlapping Whisper hypotheses into cues)
- `live_cue_feed.py` (a Live cue shown as transcript first, translation filled in later, by id)
- `live_cue_translation.py` (Live's translation context, one request per chunk with a per-cue retry, and the error note)
- `live_fetch.py`
- `live_pipeline.py` (what runs beside Live's capture loop: the translation worker and its bounded queue, the shared cue list and status line, Stop-then-finish, the lag figures)
- `live_tokens.py` (token comparison shared by the chunk overlap dedup and the agreement rule)
- `live_translate.py`
- `sentence_groups.py` (translate-by-sentence: grouping timed fragments and splitting the English back)
- `live_whisper.py` (Live's one-at-a-time Whisper worker: per-chunk time limit, prompt Stop, warm start, backlog and skip notes, per-chunk timing log)
- `source_change.py` (the `source_changed` review flag a translated line gets when its source text changes, the new English that clears it, and the compare-and-set save of flags a run cleared)
- `translate_engines.py`
- `translation_finish.py` (untranslated count, aligned -> translated, the shared end of a translation run)
- `translation_guide.py`
- `translation_memory.py`

**Dubbing, subtitles & video**

- `dub.py`
- `dub_narration.py` (novel narration and M4B export; imports from `dub`, never the reverse)
- `loudness.py` (stereo-track loudness measurement and normalising, numpy only; `docs/specs/stereo-track.md`)
- `media_inspect.py`
- `review_thresholds.py`
- `subtitle_formats.py`
- `subtitle_parse.py` (SRT/VTT/ASS/LRC import parsers)
- `stereo_cues.py` (stereo-track placement cues from the script, numpy only; `docs/specs/stereo-track.md`)
- `stereo_render.py` (stereo-track renderers: pan, delay and level per line, numpy only; `docs/specs/stereo-track.md`)
- `subtitle_sidecar.py` (sidecar file-name ranking, language from characters)
- `source_suspect.py` (pure: the Review "Source text looks off" rules, the `source_suspect` flag)
- `timing_drift.py` (pure: line times vs detected speech spans, the `timing_drift` review flag)
- `video_download.py`
- `video_export.py`

**OCR, scanlation & reading**

- `comic_chapters.py` (chapter labels and hidden pages per comic page, kept in the drama folder's `chapters.json`)
- `dictionary.py`
- `hardsub_ocr.py`
- `ocr.py`
- `reader.py`
- `scanlate.py`
- `scanlate_detect.py`
- `scanlate_inpaint.py`
- `scanlate_regions.py` (text-region classification, script/orientation guesses, SFX exclusion)
- `scanlate_render.py` (font choice, text fitting, the inpaint-and-draw render pass, font-style sampling)
- `scanlate_translate.py` (id-keyed translation of a page's regions)
- `segment.py`

**Story & learning**

- `adaptive_style.py`
- `debug_view.py`
- `line_tools.py`
- `qa.py`
- `story_context.py`
- `universe_wiki.py`
- `vocab_export.py`

**Discovery, sources & I/O**

- `browser_support.py` (JavaScript-page browser/package messages)
- `bulk_import.py`
- `device_tokens.py` (the browser extension's per-device token table; rules in `services/device_token_service.py`)
- `epub_io.py`
- `known_sites.py`
- `metadata_lookup.py`
- `navigator.py`
- `page_fetch.py` (static and browser-rendered page fetches, shell detection, `smart_fetch`)
- `page_fetch_browser.py` (which browser program to launch, the Playwright check and the shutdown flag)
- `page_fetch_guard.py` (the pinning proxy, request guard and fail-closed `_goto` that keep fetch browsers off private addresses)
- `page_fetch_profile.py` (persistent sign-in profiles: signed-in reads and the visible sign-in window)
- `page_scroll.py` (scroll-through-then-settle step shared by the rendered and signed-in fetches)
- `page_capture_checks.py` (blank-page probe, re-capture lookup and fixed failure messages for the extension bridge)
- `page_server.py`
- `page_turns.py` (which extension-bridge request translates a page and in what order: in-flight page claims, the per-title context chain, discard only before release)
- `title_library.py`

## lib/

Shared helpers with no domain knowledge; nothing here imports `services`, `api`, `db` or a domain module (`tests/test_static_analysis.py` enforces it).

- `cancellable_lock.py` (`hold`: take a lock while polling a cancel check, so a job waiting on the pipeline lock can be cancelled)
- `capped_body.py` (byte- and time-capped read of a streamed HTTP body)
- `http.py` (the one outbound GET/POST: guard on every redirect hop, pinned connection, byte cap, total deadline, fixed-text errors)
- `proc.py` (the one runner for external commands whose output is read: own process group, tree kill on timeout or cancel, bounded output drain)
- `proc_kill.py` (`kill_tree`: kills a child and everything it started)
- `gzip_text.py` (`pack`/`unpack`: gzip for stored text that readers must take in both plain and compressed form, with a capped inflate)
- `ct2_repo.py` (which files of a Hugging Face CTranslate2 Whisper repo may be downloaded; refuses a listing holding code-bearing files; shared by `whisper_models` and `tools/ja_whisper_models.py`)
- `ort_telemetry.py` (`disable`: switches off ONNX Runtime's usage reporting in this process before a session is made; called by the Whisper loader and the ASMR voice detector)
- `file_lock.py` (`hold`: an exclusive cross-process lock on a file, for a model download two processes must not run at once)
- `errors.py` (the `ServiceError` vocabulary services raise; `api/error_handlers.py` maps it to HTTP codes)
- `url_guard.py` (`resolve_public`: the public-address check before any server-side fetch)
- `release_signature.py` (the Ed25519 release signature format: the signed message, key and `.sig` texts, sign and verify; used by `services/update_service.py` and `tools/release_signing.py`)
- `link_new.py` (`link_new`: gives a finished file its final name without replacing an existing one)
- `model_offline.py` (offline model loads for a run started away from the PC: the per-run mark jobs carry into their thread or child process, and the Hugging Face / ModelScope cache folders)
- `settings_schema.py` (the declared settings: one row per `app_settings` or `.env` key with its saved name, type, default and limits; `coerce` turns a stored value into a typed one; read through `settings_service.get`)

## services/

- `artifact_service.py`
- `asr_options_service.py`
- `assistant_backlog_service.py` (the maintenance assistant's backlog items, written only by the user)
- `assistant_diagnosis_service.py` (one assistant diagnosis run: prompts, input checks, engine build, the tool loop)
- `assistant_github_service.py`
- `assistant_pytest_guard.py`
- `assistant_repo_access.py` (what the assistant may see: redaction, repo path rules, the read-only git runner)
- `assistant_roles_service.py`
- `assistant_settings_service.py` (the assistant's settings: Developer Mode, engines, cloud consent, escalation ladder)
- `assistant_tools_service.py` (the assistant's fixed table of read-only tools and the runner)
- `auth_service.py`
- `auto_backup_service.py` (automatic backup orchestration: save settings, the backup job, the due-check, delete copies, list dramas in a copy)
- `auto_backup_settings.py` (the automatic backup's settings, run state and when the next run is due)
- `auto_backup_copies.py` (the copies in the backup folder: library identity, ownership, rotation, default restore pick, `snapshot_info`)
- `auto_backup_files.py` (snapshot files: manifest, verification, placing a new copy, reading `library.db` out, moving copies to a new folder)
- `autotune_service.py` (the "Auto-tune" speech-splitting sensitivity process job: start, status, apply a measured candidate)
- `backup_import_service.py`
- `backup_space_check.py` (the automatic backup's free-space check before it writes a copy)
- `benchmark_case_service.py`
- `benchmark_judge_service.py`
- `benchmark_lab_service.py`
- `benchmark_scoring_service.py` (the Benchmark Lab's scorers: chrF/similarity, CER and WER, pass thresholds)
- `benchmark_script_service.py` (the Benchmark Lab's Traditional vs Simplified line and character counts for Chinese transcripts)
- `benchmark_set_builder_service.py`
- `blocked_retry_service.py`
- `browser_install_service.py`
- `bug_report_service.py`
- `characters_service.py`
- `comic_chapters_service.py`
- `comic_view_service.py`
- `compare_transcription_service.py`
- `cover_art_service.py`
- `delete_service.py`
- `device_token_service.py`
- `diagnostics_admin_service.py` (admin refusals, the install guard, library reset, model-cache delete)
- `diagnostics_gaps_service.py` (read-only Diagnostics reports)
- `diagnostics_gpu_torch_service.py` (GPU PyTorch status and setup)
- `diagnostics_packages_service.py` (pip install/upgrade, update check, install presets)
- `cuda_runtime_service.py` (state of the Diagnostics "CUDA runtime for GPU Whisper" row)
- `diagnostics_installs_service.py`
- `diagnostics_service.py`
- `setup_readiness_service.py` (booleans for what is installed and configured, so a control can say why it is off)
- `pending_install_service.py` (preview, queue, cancel and report installs that wait for restart)
- `previous_library_service.py` (the library copies kept beside the library folder after a restore or before a Reset library: size for Disk usage, delete, the reset safety copy)
- `diarization_service.py`
- `difficult_audio_service.py` (the per-title Difficult audio option for a transcription run: which of its parts each recognizer honours, and the temporary levelled copy of the audio)
- `discover_catalog_service.py`
- `discover_lookup_service.py`
- `disk_usage_clips_service.py`
- `disk_usage_guard.py` (path confinement and the protection rules for clear and move)
- `disk_usage_hold.py` (why clear/move are refused now, and the exclusive library hold around a change)
- `disk_usage_links.py`
- `disk_usage_measure.py` (the bounded walk and the public record of one item)
- `disk_usage_service.py` (scan, clear and move)
- `disk_usage_title_sizes.py` (each title's size split into originals, raw transcripts, line-history snapshots and regenerable files)
- `disk_usage_trash_service.py` (Trash list, Restore, purge and empty)
- `disk_usage_trash_store.py` (the Trash folder layout, manifests and the rename into it)
- `temp_cleanup_service.py`
- `drama_restore_service.py` (copy one drama out of a backup file: single-drama restore and the row copy the import reuses; imports from `auto_backup_service`, `auto_backup_copies` and `auto_backup_files`, never the reverse)
- `drama_service.py`
- `dub_service.py`
- `egress_proxy.py`
- `engine_routing_service.py`
- `event_stream_service.py`
- `export_service.py`
- `extension_novel_service.py` (the extension bridge's "Save text into a title": a page's text appended to a novel title as a new raw chapter)
- `extension_service.py`
- `fixflag_transcribe.py`
- `fixflag_translate.py` (the re-translation half of "fix flagged lines": reason-aware, id-keyed batches)
- `bulk_job_view.py` (public view of a bulk_jobs row)
- `bulk_submit_service.py` (the callable that submits a bulk translation, bulk Reflect or off-peak schedule from inside the job)
- `glossary_common.py` (helpers shared by glossary_service and glossary_extract_service)
- `glossary_extract_service.py` (glossary extraction from the attached novel: job, proposals, cache)
- `glossary_retranslate_service.py`
- `glossary_name_report_service.py` (read-only "name check across all titles": runs the per-title name scan over every title the caller may see as one cancellable job, merges the clusters that are one name, keeps the owner's right/wrong/skip marks, and exports the unresolved groups as text)
- `glossary_spelling_service.py` (one bounded model call that proposes how a known name may be spelled in the transcript, kept only when it occurs in the lines; and a read-only check of lines whose English renders a name differently)
- `glossary_service.py`
- `glossary_variants_service.py` (read-only scans: other spellings of a glossary term in the source lines, and names that recur under several spellings)
- `jellyfin_service.py`
- `keep_original_service.py` (the per-title "Keep original files" pin: which files are originals, storage cleanup, Trash and export)
- `job_checkpoint_service.py`
- `job_stage_service.py`
- `job_timing_service.py`
- `jobs_service.py`
- `language_pack_service.py`
- `vdb_names_service.py` (the optional personal VTuber name list, downloaded on demand; personal use only, never exported)
- `library_admin_service.py` (shared admin guards and bulk actions: typed confirm, maintenance hold, artifact lookup, bulk status/tags/delete/translate, storage cleanup)
- `library_backup_service.py` (the export-all zip, full backup and database-only backup jobs, and the sanitized database snapshot)
- `library_user_backup_service.py` (the backup of one person's items)
- `library_backup_restore_service.py` (validating and applying an uploaded library backup)
- `library_restore_sql.py` (SQL helpers for the library restore)
- `library_restore_service.py` (restoring a whole-library backup zip: validation, staged databases, the folder swap)
- `library_service.py`
- `library_translated_guard.py` (what counts as translated for bulk status and export)
- `line_ai_service.py`
- `line_provenance_service.py`
- `line_tools_service.py`
- `lines_service.py`
- `live_follow_poller.py` (the followed-channel live checks: the manual-check queue, the optional hourly scheduler, one yt-dlp metadata read at a time, backoff, the extra check before a scheduled start, notify once per stream, the PC owner's automatic start)
- `live_follow_service.py` (followed YouTube channels on the Live page: channel input, ownership, limits, saved Live options, Start Live from a follow)
- `live_service.py`
- `live_spend_service.py` (cap check, usage log and failure stop for a Live session on a paid engine)
- `paid_job_service.py` (money limits for a background job's AI calls, and the refusal of unattended paid work with no monthly cap)
- `episode_summary_service.py` (why a run left the episode summary empty, and writing one on demand with a chosen engine)
- `failed_lines_service.py` (retry exactly the last run's failed lines, and forget the ones a run fixed)
- `glossary_flagged_service.py` (one-click redo of the lines flagged for a missing glossary name, with the stronger engine)
- `lncrawl_service.py`
- `loaded_models_service.py`
- `maintenance_assistant_service.py` (one assistant turn: ask, escalation, independent review, developer report, changelog)
- `media_export_service.py`
- `media_peaks_service.py`
- `media_playback_service.py`
- `media_upload_service.py`
- `metadata_research_service.py`
- `metadata_service.py`
- `model_readiness_service.py` (whether a run's models are on disk; refuses a run started away from the PC that would download one)
- `model_reeval_service.py`
- `model_registry_service.py`
- `narration_service.py`
- `notification_service.py`
- `novel_attach_service.py`
- `novel_chapters_service.py`
- `novel_files_service.py`
- `oidc_service.py`
- `ownership_service.py`
- `page_import_limits.py`
- `queue_service.py` (the Jobs page queue: items, run controls, the one-at-a-time runner thread)
- `qwen3_requirements_service.py`
- `reader_service.py`
- `real_model_check_service.py`
- `remote_health_service.py`
- `resegmentation_service.py` (Review re-segmentation: the rules preview, the LLM preview job and the apply job; writes through restructure_service's guard)
- `resplit_service.py` (Review "Split long lines": the rule-based re-split and its optional forced-aligner job; writes through restructure_service's guard)
- `restructure_service.py` (add, delete, merge and split lines, version-history restore, and the shared structural-write guard)
- `retranscribe_line_service.py` (Review's "Re-transcribe" for one line: the job, its in-memory proposal and the compare-and-set apply)
- `retranscribe_many_service.py` (Review's "Re-transcribe selected": start, read and apply of the many-line re-transcription; shares the one-line job's id)
- `retranscribe_worker.py` (the spawned workers and result hooks of the one-line and many-line re-transcriptions; the one-line start, read and apply are in retranscribe_line_service)
- `gpu_process_job.py` (the shared GPU process-job shape: `run_in_child` in the parent, `run_worker` with the deadline watchdog in the child)
- `compare_hear_worker.py` (the spawned worker that cuts and hears the chosen lines for compare transcription, so Cancel can kill it)
- `retime_service.py`
- `review_extras_service.py`
- `review_jobs_service.py`
- `review_lines_service.py`
- `review_records_service.py`
- `run_settings_service.py` (allow-listed settings a job run used, shown in Jobs Details)
- `safe_fetch.py`
- `saved_comics_service.py`
- `scanlate_pages_service.py`
- `scanlate_render_service.py`
- `scanlate_run_service.py`
- `series_people_service.py`
- `series_defaults_service.py` (a series' default title settings: stored, inherited by new and moved titles, applied to every title after a preview)
- `series_service.py` (the Series page: titles and counts, rename, delete keeping titles, copy glossary terms between series)
- `service_errors.py` (re-exports `lib/errors.py` for the existing call sites)
- `settings_schema_service.py` (the declared settings as the metadata the Settings page builds forms from)
- `settings_service.py`
- `shutdown_service.py`
- `source_change_service.py` (Review's lines whose source changed after translation: the list, "Keep English" and "Retranslate these")
- `source_domains_service.py`
- `source_link_service.py` (a title's cleaned and full source link; the full one only for the PC-only route)
- `source_service.py`
- `sources_extension_service.py`
- `sources_extraction_service.py`
- `sources_import_service.py`
- `sources_registry_service.py`
- `sources_save_service.py`
- `sources_search_service.py`
- `sources_signin_service.py`
- `sources_tools_service.py`
- `sources_tracking_service.py`
- `sources_url_service.py`
- `speech_coverage_service.py`
- `spend_history_service.py`
- `stronger_engine_service.py`
- `subtitle_import_service.py`
- `source_suspect_service.py` (Review "Check source text": flag/flag_note only, list with the merge target)
- `timing_check_service.py` (Review "Check timing" job, snap to speech, dismissed ids)
- `timing_settings_service.py` (the Settings > Timing and splitting values as runs use them; Developer Mode rows read as defaults while it is off)
- `resplit_config.py` (what a Review re-split cuts: the sensitivity presets, duration cap and fresh-transcription limits; moved out of restructure_service; used by resplit_service)
- `reading_speed_dismissal_service.py` (remembers dismissed reading-speed flags in a sidecar, keyed by line id and English text hash)
- `transcribe_gap_service.py` (Review's untranscribed gaps: detection from the saved lines, and adding the blank flagged lines that "Transcribe this gap" then re-transcribes)
- `transcribe_pipeline.py` (the in-process transcription pipeline and its spawn-target worker: separation, model load, VAD, decode, Qwen pass, realign, split; writes no database rows)
- `transcribe_options_service.py` (what a transcribe run may be asked to do: tuning defaults, the Whisper sizes a title may store, and validation of saved config fields and run choices)
- `transcribe_service.py`
- `transcribe_speed_service.py` (remembered speed of finished transcription and speaker-detection runs per model and device, behind the time estimate)
- `translate_preset_service.py` (apply a workflow tier or preset to a title; save the Translate form as a preset)
- `translate_run_service.py`
- `translate_service.py`
- `translate_thinking_service.py`
- `translation_version_service.py`
- `update_service.py`
- `url_media_service.py`
- `usage_recost_service.py`
- `vocabulary_hint_service.py`
- `voice_bank_audio_service.py`
- `voice_clone_service.py`
- `vram_service.py`
- `web_search_service.py`
- `workflow_service.py`
- `workspace_audio_job_service.py` (Whisper transcription, hard-sub OCR and SenseVoice tag jobs)
- `workspace_job_service.py` (translate and bulk series translate jobs, and the style context they share)
- `workspace_review_job_service.py` (emotion, flag, consistency, notes and fix-flagged LLM review jobs)
- `ytdlp_child.py` (one yt-dlp download in a killable child process, for `url_media_service`)

## api/

Everything directly in `api/`; routers are below, Pydantic models in `api/schemas/` (one module per domain) and the `api/*_schemas.py` modules beside the core files.

- `__main__.py` (`python -m api`: start the Baihe API server with the settings from `BAIHE_API_*`)
- `admin_users_schemas.py` (response models for user administration and the audit log view)
- `api_config.py` (where the FastAPI server's own settings come from)
- `asr_options_schemas.py` (request/response models for the experimental transcription settings)
- `assistant_schemas.py` (request/response models for the maintenance assistant routes)
- `auth.py` (the deny-by-default permission layer)
- `background.py` (the background pieces of the app, started once when the API process starts)
- `backup_schemas.py` (request/response models for the automatic backup routes)
- `benchmark_schemas.py` (request/response models for the Benchmark Lab routes)
- `comic_schemas.py` (request/response models for the comic viewer routes)
- `diagnostics_install_schemas.py` (Pydantic models for api/routers/diagnostics_installs_routes.py)
- `disk_usage_schemas.py` (request/response models for the Disk usage routes)
- `engine_routing_schemas.py` (request/response models for the "Which engine does what" routes)
- `error_handlers.py` (one JSON error shape for every failure)
- `jellyfin_schemas.py` (request/response models for the optional Jellyfin connector)
- `job_stage_schemas.py` (models for the per-stage job timing route)
- `llm_slots.py` (the shared concurrency cap for SYNCHRONOUS long work in a request handler)
- `metadata_research_schemas.py` (request/response models for grounded metadata research)
- `model_reeval_schemas.py` (request/response models for scheduled model re-evaluation and promotion)
- `model_registry_schemas.py` (request/response models for the model deprecation / migration assistant)
- `notification_schemas.py` (request/response models for the notification routes added after the first version)
- `saved_comics_schemas.py` (models for api/routers/saved_comics_routes.py)
- `scanlate_schemas.py` (request/response models for the automatic Scanlate routes)
- `server.py` (the FastAPI application: Baihe's HTTP API)
- `sharing_schemas.py` (request/response models for who can see each drama and series)
- `sources_extraction_schemas.py` (request/response models for the pasted-URL extraction extras)
- `sources_import_schemas.py` (response models for the import state route)
- `sources_tools_schemas.py` (request/response models for the Sources tools routes and the Discover pasted listing route)
- `static_frontend.py` (serves the built React app from the same FastAPI process and origin as `/api`)
- `stronger_engine_schemas.py` (request/response models for the "Try with a stronger engine" routes)
- `web_search_schemas.py` (request/response models for the optional web-search fallback)

### api/schemas/

Models for the domains that have been moved into the package; `from api.schemas import X` works for all.

- `benchmark.py` (Benchmark Lab shapes added with the reviewed-title set builder and the optional LLM judge)
- `browser.py` (Schemas for Diagnostics > "Install browser support")
- `characters.py` (Characters, glossary and series-person shapes)
- `common.py` (Shared building blocks: the error envelope and the API version, plus any model used by more than one domain module)
- `disk_usage.py` (the per-title size split in the Disk usage scan)
- `language_packs.py` (/api/language-packs: the built-in language packs, a title's choice of packs, and the per-language default)
- `library.py` (Library shapes: dramas, series, bulk actions, storage, media and export)
- `loaded_models.py` (the "Models in memory" panel: names, sizes and states)
- `novel_chapters.py` (the chapters saved in a drama's raw novel)
- `pending_install.py` (Pydantic models for api/routers/pending_install_routes.py)
- `queue.py` (/api/queue: the user-managed job queue on the Jobs page)
- `reader.py` (Reader and novel shapes: reading views, notes, vocabulary, wiki and novel files)
- `setup.py` (GET /api/setup/readiness: what is installed, as booleans)
- `real_model_check.py` (Pydantic models for api/routers/real_model_check_routes.py)
- `retranscribe_lines.py` (Review's many-line re-transcription and the untranscribed-gap helpers)
- `review.py` (Review shapes: line views and edits, records and versions, restructure and resegment, and the Review AI extras)
- `settings.py` (The declared-settings schema the Settings page builds its forms from)
- `series.py` (/api/series: the Series page, rename, delete and copy-terms shapes; ids, names and counts only)
- `sources.py` (Sources, Discover and tracked-series shapes)
- `spend_history.py` (GET /api/settings/spend-history: paid-API spend by month and what caused it)
- `subtitle_import.py` (Subtitle-file import: what a file contains, what importing it would do, and what it did)
- `system.py` (System-level shapes: health, settings, diagnostics and setup, jobs, updates, notifications, extension, ports and bug reports)
- `source_suspect.py` (the Review "Check source text" shapes)
- `timing_check.py` (the Review "Check timing" shapes)
- `transcribe.py` (Transcription shapes: transcribe runs and config, diarization, autotune, re-transcribe and live sessions)
- `translate.py` (Translation shapes: the standalone translator, translate runs, workflow tiers, presets, bulk jobs, retrying failed lines, the glossary redo and the episode summary)
- `vdb_names.py` (/api/vdb-names: the optional personal VTuber name list; booleans and counts only)
- `voice.py` (Voice shapes: dubbing, narration and voice-clone setup)

### api/routers/

- `admin_users_routes.py`
- `artifact_routes.py`
- `asr_options_routes.py`
- `assistant_github_routes.py`
- `assistant_routes.py`
- `auth_routes.py`
- `backup_routes.py`
- `benchmark_routes.py`
- `blocked_retry_routes.py`
- `bug_report_routes.py`
- `characters_routes.py`
- `comic_routes.py`
- `delete_routes.py`
- `device_token_routes.py`
- `diagnostics_browser_routes.py`
- `diagnostics_gaps_routes.py`
- `diagnostics_installs_routes.py`
- `diagnostics_routes.py`
- `setup_routes.py` (GET /api/setup/readiness, booleans only, `library.read`)
- `pending_install_routes.py`
- `diarization_routes.py`
- `source_suspect_routes.py`
- `timing_check_routes.py`
- `discover_lookup_routes.py`
- `discover_routes.py`
- `disk_usage_routes.py`
- `drama_routes.py`
- `dub_routes.py`
- `engine_routing_routes.py`
- `events_routes.py`
- `export_routes.py`
- `extension_routes.py`
- `glossary_routes.py`
- `jellyfin_routes.py`
- `language_pack_routes.py`
- `vdb_names_routes.py`
- `job_stage_routes.py`
- `jobs_routes.py`
- `library_admin_routes.py`
- `library_routes.py`
- `line_ai_routes.py`
- `lines_routes.py`
- `live_follow_routes.py`
- `live_routes.py`
- `loaded_models_routes.py`
- `media_routes.py`
- `metadata_research_routes.py`
- `metadata_routes.py`
- `model_reeval_routes.py`
- `model_registry_routes.py`
- `narration_routes.py`
- `notification_center_routes.py`
- `notification_routes.py`
- `novel_files_routes.py`
- `novel_routes.py`
- `queue_routes.py`
- `reader_routes.py`
- `real_model_check_routes.py`
- `restructure_routes.py`
- `review_extras_routes.py`
- `review_jobs_routes.py`
- `review_lines_routes.py`
- `review_records_routes.py`
- `saved_comics_routes.py`
- `scanlate_routes.py`
- `series_people_routes.py`
- `series_routes.py`
- `settings_routes.py`
- `sharing_routes.py`
- `source_domains_routes.py`
- `source_routes.py`
- `sources_catalog_routes.py`
- `sources_extraction_routes.py`
- `sources_import_routes.py`
- `sources_local_routes.py`
- `sources_search_routes.py`
- `sources_tools_routes.py`
- `spend_history_routes.py`
- `stronger_engine_routes.py`
- `subtitle_import_routes.py`
- `system_routes.py`
- `transcribe_routes.py`
- `translate_routes.py`
- `translate_run_routes.py`
- `translation_version_routes.py`
- `update_routes.py`
- `usage_recost_routes.py`
- `voice_bank_audio_routes.py`
- `voice_clone_routes.py`
- `web_search_routes.py`
- `workflow_routes.py`

## engine_backends/

Translation engines by provider; `translate_engines.py` is the front door that re-exports them.

- `claude.py` (Claude engine)
- `echo.py` (the key-free Echo engine so a new PC can exercise the whole app; Developer Mode only)
- `engine_registry.py` (Engine registry: ENGINES, capability tags, notes, model overrides and get_engine)
- `fallback.py` (Translate fallback chain)
- `flag_reasons.py` (Flag keys a line can carry, and the text shown for each)
- `glossary_check.py` (checks a translated batch against the series glossary: one re-ask on the same engine, then a Review flag)
- `gemini.py` (Gemini engine, its rate-limit status text and free-tier limits)
- `llm_tasks.py` (Single-prompt LLM calls for features other than a translation batch)
- `local.py` (Local engine: Ollama)
- `openai_compat.py` (DeepSeek and OpenAI engines)
- `pricing.py` (Model lists, per-million-token prices and cost estimation)
- `prompts.py` (Prompt builders shared by the LLM engines)
- `reflect_pass.py` (Reflect mode: three passes over one batch)
- `reflect_prompts.py` (Prompts for Reflect mode's three passes)
- `run_limits.py` (Limits that end a translate run early: failed batches in a row, rate-limit wait budget)
- `paid_calls.py` (the one usage-row writer for a paid call, and the failure-streak, cap and wait-budget limits for a job's single-prompt AI calls)
- `shared.py` (Helpers every engine uses)
- `standalone.py` (Standalone text translation)
- `thinking.py` (Switching a model's reasoning off or on in the request)
- `translate_pipeline.py` (The per-run translate loop)
- `translation_guard.py` (Post-checks on a translated batch for the ways an id-keyed reply can be complete and still wrong)

## jobs/

The job machinery behind `background_jobs.py` (progress, results, status, cancel).

- `gpu_queue.py` (this process's wait line for GPU-touching jobs: may a GPU job start now, place in line, freeing a slot)
- `gpu_slots.py` (the one way any process takes, keeps and frees a GPU slot)
- `job_launch.py` (starting a job, thread or process, and the exclusive/maintenance/stopping holds that refuse a start)
- `job_runner.py` (runs a started job and handles how it ends: outcome, GPU slot, promoting the next queued job, dead workers)
- `job_settings.py` (the Settings-page knobs for background jobs and the completion notice)
- `job_store.py` (writes and closes the job_records row of every job)
- `job_table.py` (the in-memory job table, its lock, change listeners and the job_records mirror heartbeat)
- `process_worker.py` (what a process-job worker calls inside its child: progress, its own process group, exiting when orphaned)
- `user_caps.py` (how many running and queued jobs one signed-in user may have at once)

## sources/

Site adapters, the fetch ladder and the source store (`sources.db`).

- `adaptive.py` (the order things are tried in when a pasted URL has no dedicated adapter)
- `ai_extract.py` (content extraction with an LLM as the *fallback*: the confidence scoring, page model and LLM call every result goes through)
- `ai_extract_novel.py` (novel chapters: block/link picks, the deterministic tier and the novel validator)
- `ai_extract_comic.py` (comic chapters: image candidates, roles, duplicate removal and the comic validator)
- `ai_extract_media.py` (video/audio/subtitle resources on an unrecognized page)
- `auth_browser.py` (signed-in browser sessions)
- `base.py` (the adapter interface)
- `cache.py` (the configurable raw-content cache)
- `chapter_check.py` (scheduled checks for new chapters on tracked series, the same simple shape as Mihon's library update)
- `chapter_manifest.py` (which chapters sit where in a drama's `raw_novel_context.txt`)
- `chapter_order.py` (sorting chapter lists the way a reader expects)
- `charset_sniff.py` (decoding a page that declares no charset)
- `detect.py` (reading a response and naming what happened)
- `domains.py` (a domain list for a site that moves between domains)
- `extension_marker.py` (the person's "this source works only through the browser extension" marker, one row per source in sources.db)
- `front_door.py` (one "paste any URL" box)
- `generic_import.py` (one-off "paste a URL" imports for sites with no registered adapter)
- `health.py` (per-source health, last success/failure and the backoff window that keeps a failing source from being hit again too soon)
- `http.py` (the one paced HTTP client every adapter and the generic importer go through)
- `http_transport.py` (the real network transport under it: SSRF-guarded hand-followed redirects, capped streamed body reads, the request deadline, and the `Response` and refusal types)
- `ladder.py` (the access-method ladder)
- `limiter.py` (the per-source concurrency limit)
- `live_follow_store.py` (the `followed_channels` rows in `sources.db` behind `services/live_follow_service.py`)
- `lzstring.py` (LZString's Base64 variant, which some sites use to compress data their own page JS unpacks)
- `mock.py` (an offline demo source)
- `models.py` (the shared vocabulary of the source-adapter system)
- `novel_follow.py` (following next-chapter links from a pasted novel URL)
- `pacing.py` (per-source pace levels and the automatic slowdown)
- `pipeline.py` (handing fetched content to the rest of the app)
- `preflight.py` ("will this site work?", answered before anyone commits to an import)
- `profiles.py` (per-domain extraction profiles)
- `registry.py` (which adapters exist, which are switched on, and the top-level multi-source search)
- `site_terms.py` (terms findings for sites that have no adapter but whose terms have been read directly)
- `store.py` (persistence for the source-adapter system)

### sources/adapters/

One module per site; each is registered in `sources/registry.py` (how to add one: `docs/adding-source.md`).

- `52shuku.py` (52shuku.net)
- `baozimh.py` (包子漫画 baozimh.org / godamh.com)
- `bilibili.py` (Bilibili as a real VideoSource adapter)
- `bilibili_manga.py` (哔哩哔哩漫画 / Bilibili Manga, an authenticated comic source)
- `fanjiao.py` (饭角 Fanjiao, zh GL audio dramas)
- `guazimanhua.py` (瓜子漫画 guazimanhua.com)
- `kuaikan.py` (快看漫画 Kuaikan Manhua)
- `lightnovel_fun.py` (轻之国度, zh light novels)
- `mangak.py` (MangaK)
- `manhuagui.py` (漫画柜 / ManHuaGui)
- `manhuaku.py` (漫画库 manhuaku.net)
- `miaoqumh.py` (妙趣漫画 miaoqumh.org)
- `missevan.py` (猫耳FM / MissEvan: Baihe's first audio-drama adapter)
- `piaotian.py` (飘天文学, zh web novels on static GBK pages)
- `ranobes.py` (ranobes.net)
- `syosetu.py` (小説家になろう, ja web novels)
- `toonkor.py` (툰코 ToonKor)
- `twmanga.py` (包子漫畫 on its regional mirrors www.twmanga.com and www.twbzmg.com)
- `xbanxia.py` (xbanxia.cc)
- `zerosumonline.py` (Zero-Sum Online)

## scripts/

Build, probe and migration helpers, run by hand or by CI.

- `build_release.py` (packages the built React app as a release zip)
- `check_constraints.py` (do requirements, constraints.txt and the installer lock agree?)
- `dependency_canary.py` (does upgrading ONE package break the app?)
- `smoke_pack.py` (does the transcription pipeline still behave after an upgrade?)
- `source_probe.py` (which source hosts still answer?)
- `source_status.py` (builds docs/known-working-sources.md's status board from the adapter registry plus docs/source-status.json)

### scripts/migration/

- `keepboth.py` (one-off migration helper for the keep-both-copies case)

## tools/

Developer tools, not shipped.

- `asr_bench.py` (ASR recipe bench: `python -m tools.asr_bench TITLE_ID --config RECIPE [--config RECIPE ...] [--yes]`)
- `asr_bench_recipes.py` (Recipes for tools.asr_bench)
- `check_change.py` (prints the checks that matter for the files you changed, in under 2 KB)
- `ci_changes.py` (Decides which CI jobs a change needs; used by the `changes` job in .github/workflows/tests.yml)
- `ja_whisper_models.py` (Prepare Japanese Whisper fine-tunes for the Benchmark Lab's offline-model-folder arm)
- `release_signing.py` (the owner's release key by hand: `generate`, `sign`, `verify`; docs/runbook.md §1a)
- `repo_map.py` (Print a symbol map of the repo, sized for a model with a small context window)
- `route_table.py` (Print the route -> permission table for docs/route-permissions.md, derived from the decorators)
- `self_test.py` (One-run hardware self-test)
- `translate_context_bench.py` (Context/batch-size bench: `python -m tools.translate_context_bench TITLE_ID [--yes]`)
- `windows_script_checks.py` (Runs setup.bat, start.bat and uninstall.bat on a Windows runner, in temp folders, with their prompts answered through stdin)

## installer/

Windows installer and boot service, not imported by the app.

- `build_installer.py` (assembles the Windows installer's payload and, where Inno Setup is available, compiles BaiheStudio-Setup-<version>.exe)
- `launcher.py` (what the installed app's Start-menu shortcut runs)
- `postinstall.py` (the installer's one post-install step)
- `release_public_key.txt` (the release public key the installed app checks in-app updates against; ships in the payload; no key line = SHA-256 only)
- `service.py` (runs the installed Baihe Studio as a Windows service)
- `service_checks.py` (Asking what is listening: the loopback health checks, waiting for a condition, and whether this process is an administrator)
- `service_cli.py` (The service script's command line)
- `service_config.py` (What the service runs with and the text generated from it)
- `service_control.py` (Running Windows' own tools for the service)
- `service_layout.py` (Where Baihe Studio's service files live and which folders the service may be given rights on)
- `setup_lock.py` (the named mutexes that keep Setup and the owner's service commands from running at the same time)
- `smoke_child.py` (CI only: stands in for a long-running child of the installed server, to prove the service stops its children)

## Rules of thumb

- UI code goes in `frontend/`; logic lives at the top level, in `services/` or in `sources/` so it stays testable without a UI.
- Logic the API and the CLI both need goes in `services/`.
- Nothing writes outside `library/` except exports you explicitly download.
- Optional dependencies are imported inside functions, never at module top level.
- Adding a new module in any folder listed here (top level, `services/`, `api/routers/`, `sources/`, `tools/` and so on)? Add it as one bullet to the right group above, in the same PR (a top-level module also gets one sorted line in `expected_files.py`).
- `sources/chapter_manifest.py`: `raw_novel_chapters.json` beside the raw novel (title, site, time and byte range per imported chapter); `services/novel_chapters_service.py` reads it.
- `sources/novel_follow.py`: following next-chapter links from a pasted novel URL (`follow_novel`); builds on `sources/adaptive.py`, which does not import it.
- `sources/extension_marker.py`: the person's "works only through the browser extension" marker per source (`source_extension_only` table in `sources.db`); imports and scheduled checks consult it, `services/sources_extension_service.py` holds the early stop.
