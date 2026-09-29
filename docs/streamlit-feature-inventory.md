# Streamlit feature inventory

Written 2026-09-29 to prepare the Streamlit deletion (`docs/streamlit-retirement-plan.md`, sections 9 and 10). It lists what every Streamlit tab and every Workspace stage did, so nothing is lost silently when the files go.

- **BASE:** `bdcc18c489b4ffc246db232b6bb9135a1603c6e0` (`origin/baihe-subtitler`). Every `path:line` below is at BASE; the archive tag `pre-streamlit-removal` points at BASE or later, so `git show pre-streamlit-removal:tabs/workspace_tab.py` gives the same text unless a later commit touched it.
- **React column:** the React file that does the same job, or one of:
  - **MISSING**: not in React yet. It goes on the backlog at the end.
  - **PARTIAL**: React has part of it. The missing part is named.
  - **DROPPED**: removed on purpose by a user decision (plan section 10). It is not ported.
  - **DEFERRED**: Scanlate. It is held until after the removal (plan section 8). It is not on this backlog.
  - **N/A**: Streamlit mechanics with no user-visible behaviour to port (rerun buttons, widget-cache clearing).
- **API column:** the FastAPI endpoint that already exists, or "no API". Here, "no API" means no endpoint exists, even if a service or module function does.
- **Tests column:** the test file and class that covered the feature at BASE. `wt` means `tests/test_workspace_tab.py`. See `docs/streamlit-test-triage.md` for what happens to each test.
- React paths are relative to `frontend/src/`. `stages/` means `pages/workspace/stages/`.

## 0. Corrections to the plan's coverage matrix (section 1)

I checked the plan's matrix against the code at BASE. These points differ from it:

1. `tabs/workspace_tab.py` is **6,017 lines**, not 6,065. The real stage ranges are: preamble 1141-1553, Source 1555-1911, Transcript 1913-2362 (the tab label is "Transcript", not "Transcribe"), Diarize 2364-2395, Translate 2397-3966, Review 3968-5454, Dub 5455-5613 and Export 5614-6016. Two things sit inside the Translate block even though they belong to other stages. The "Transcribe & Align" button and its whole completion handler are at 2927-3460, in `with tab_translate:`. Character naming and voice cloning are at 3637-3966.
2. `_compute_workspace_stage_index` is **already extracted**. It now lives in `services/workflow_service.py:10`, and the tab imports it back (`workspace_tab.py:19`). What is still missing is an API endpoint and React use of it (P14/P15).
3. The plan lists 8 test imports of Class S code. That list is stale. `test_cli.py`, `test_line_ids.py`, `test_export_formats.py`, `test_transcription_quality.py` and `test_dub.py` no longer import tab logic; they only render the tab through AppTest or read its source. The real remaining imports are in the triage doc.
4. Diarize is **PARTIAL**, not REPLACED. The API supports `overwrite_manual` plus `confirm`, but React never offers the "keep my corrections / overwrite them" choice (D07).
5. Review: Streamlit **never had** add-line, delete-line or split-line UI. Those exist only in the API (`restructure_routes.py`). Streamlit had auto-merge of short lines, re-segmentation and history restore; React has none of the three.
6. Transcribe: auto-tune and glossary-from-novel are missing, as the plan says. Also missing are the content-mode and transcript-mode pickers (API exists), the novel-reference translation upload, the raw-novel priming upload and URL import. None of these have React UI.
7. Export: the plan's "softsub/SRT-hardsub missing" is only partly right. The ASS burn covers SRT-style hardsub (E16). Still missing: softsub mux, the dub-audio video export, a custom base filename, "Mark as exported", and most of the ASS style controls (the API accepts them; React only sends font, colours, per-speaker colours and notes-on-own-line).
8. Settings: the biggest gap is **API key entry**. `POST /api/settings/keys/{engine}` and `/clear` exist, but React never calls them. React shows only "configured yes/no", so a React-only user has to edit `.env` by hand before any paid engine works.
9. Library: `POST /api/library/presets/{id}/rename` and `/voice-bank/{id}/rename` exist, but React does not call them.
10. Translate page: `DELETE /api/translate/history` exists, but React has no Clear button.
11. Diagnostics: Python, ffmpeg and JS-runtime checks now show in React's Setup section (`GET /api/diagnostics/setup-checks`, branch `react-diagnostics-admin`).
12. Other API endpoints that exist with no React caller: `GET /api/source/.../config` and `POST` of it, `DELETE /api/glossary/.../terms/{id}`, `POST /api/dramas/{id}/metadata`, all of `restructure_routes`, `GET /api/review/.../consistency|emotions|tendencies|versions/compare|notes/markdown`, `GET .../lines/{id}/provenance|original-text`, `GET .../coverage|pacing-flags`, `POST /api/characters/.../voice-bank/apply`, `GET /api/characters/.../clone-engines`, `GET /api/characters/series/{id}/characters`, all of `sources_catalog_routes`, `discover_routes` and `reader_routes`.

## 1. Session-state keys shared across tabs

These are the Streamlit globals other tabs read. Each one needs a server-side or React-side home, or a conscious drop.

| Key(s) | Written by | Read by | React / API equivalent |
|---|---|---|---|
| `active_drama_id`, `lines` | Workspace picker (`workspace_tab.py:1152-1175,1356,1367`), Library Resume/Open (`library_tab.py:63-67,139-141`) | Workspace, Library bulk-translate refresh (`library_tab.py:299-302`), Diagnostics reset (`diagnostics_tab.py:983`) | Route `#/drama/:id` (`router.ts`); lines are fetched per request |
| `settings_{claude,deepseek,gemini,deepl,google,groq,hf_token}` (keys), `settings_{ollama_url,libretranslate_url,gpt_sovits_url}` | `settings_tab.py:69-76` (from `.env`), `:297-305`, and `common.synced_api_key_input` everywhere | Workspace, Translate, Reader, Discover, Sources, Diagnostics, Live, Library bulk translate, page_server bridge | Server reads `.env` (`settings_service.resolve_key`); React **MISSING** key entry (API exists) |
| `use_gpu`, `gemini_free_tier`, `settings_limit_one_gpu_job`, `settings_notify_on_job_done` | `settings_tab.py:307,337-371` | Every job start | `pages/Settings.tsx` toggles; `GET/POST /api/settings` |
| `settings_default_engine`, `settings_default_locale`, `settings_default_style_note`, `settings_episode_summary_engine` | `settings_tab.py:264-290` | Workspace translate defaults (`:2781,2869-2872`), Translate/Live/Discover engine default, `_episode_summary_engine` (`:325`), benchmark | MISSING (no API) |
| `settings_{claude,gemini,ollama}_model` | Workspace model pickers (`:2791-2824`), presets/tiers (`:84-129`) | Library bulk translate (`library_tab.py:235-237`), Translate tab, Diagnostics model versions (`diagnostics_tab.py:518`) | Per-run `model` in `TranslateRunStart`; no stored default. A preset's model comes back as `preset_defaults.engine_model` and prefills the Translate form's model for the drama's saved engine (branch `fix-translate-preset-parity`) |
| `settings_ocr_backend`, `settings_ocr_prefer_paddle_vl_manga`, `settings_tesseract_cmd`, `settings_whisper_model_path`, `settings_ollama_num_ctx_override`, `settings_monthly_cap_usd`, `settings_cookies_browser`, `settings_cookies_file` | `settings_tab.py:237-413` | OCR, transcribe, translate cap, URL download, Live | MISSING (Tesseract path is typed per OCR run in `stages/NovelPanel.tsx`) |
| `app_dark_mode`, `spoiler_free_mode`, `reader_{font_size,line_height,max_width,theme,font}` | `settings_tab.py:200-235` | `ui_theme`, Reader | MISSING (Reader not ported) |
| `active_profile_id` | `settings_tab.py:138-190` | Reader progress/notes, Library continue reading/history, Workspace personal notes | **DROPPED** (plan section 10: profile picker) |
| `nav_notice`, `reader_jump_page`, `reader_resume_pending` | Library Resume (`library_tab.py:61-75`) | Library banner, Reader (`reader_tab.py:46-58,137`) | MISSING (Reader) |
| `apply_style_profile` | Review adaptive style (`workspace_tab.py:5037`) | Translate run (`:3531`) | MISSING (no API) |
| `sub_style_current_{id}` | Export style fragment (`:254`) | Review burned preview (`:965`) | MISSING |
| `diarize_job_expected_speakers_{id}`, `speaker_segments_{id}` | Diarize/transcribe completion (`:1096,3446,1060`) | Diarize apply, Characters auto-extract (`:3644`) | Server-side in `diarization_service.make_apply_on_done` |
| `page_server` config bridge | `settings_tab.py:418-485` (`page_server.ensure_server_started`, `set_translation_config`) | Browser extension endpoint | API: started by the API startup hook (`api/background.py`, #372); control routes `/api/extension/*` (#372) |
| `chapter_check.ensure_scheduler_started()` | `sources_tab.py:975` | Tracked-series notifications | API: started by the API startup hook (`api/background.py`, #372) |

## 2. Workspace (`tabs/workspace_tab.py`)

### 2.1 Preamble: drama picker, create, metadata (1141-1553)

| ID | Feature (plain words) | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| P01 | Pick a drama from a dropdown. Switching resets the loaded lines and the per-line widget cache so one drama's edits can't be saved into another | 1147-1175 | `db.list_dramas`, `_clear_line_widget_state` | `pages/Library.tsx` + route `#/drama/:id` (`router.ts`) | `GET /api/library/dramas` | wt TestDramaSwitchResetsLoadedLines, TestDramaSwitchKeepsCharactersSeparate, TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate |
| P02 | "Refresh" button: reload lines from the database | 1183-1189 | `db.load_line_objects` | N/A | — | wt TestManualRefreshButton |
| P03 | New drama: auto-fill metadata from a public listing URL, with a manual paste fallback | 1193-1228 | `metadata_lookup.lookup_metadata`, `lookup_metadata_from_text` | PARTIAL `stages/MetadataPanel.tsx` AutofillPanel (only after the drama exists) | `POST /api/metadata/dramas/{id}/autofill`, `/autofill/apply` | — |
| P04 | "Known official platforms" popover in the auto-fill box | 1198-1200 | `known_sites.KNOWN_SITES` | `stages/MetadataPanel.tsx` "Known official platforms" fold in Auto-fill (links open in a new tab) | `GET /api/discover/platforms` | — |
| P05 | New drama: analyze a media file first (duration, resolution, tracks, suggested pipeline, "use this content type") | 1230-1280 | `media_inspect.probe_media` | PARTIAL `stages/MetadataPanel.tsx` AnalyzePanel (after upload, not before creating) | `POST /api/metadata/dramas/{id}/analyze-media` | — |
| P06 | New drama form: titles, author, studio, director, cast, summary, content type, required source language | 1282-1311 | — | PARTIAL `pages/Library.tsx` (titles, language, media type only). Credits and summary MISSING. `music`/`other` hidden: **DROPPED** from the picker | `POST /api/dramas` (all fields) | wt TestNewDramaLanguageSelector, TestAnimeContentTypeAndSeriesAtCreation |
| P07 | New drama: pick an existing series or create one | 1313-1325, 1348-1353 | `db.get_or_create_series` | MISSING | `POST /api/dramas` (`series_id`/`new_series_name`) | wt TestAnimeContentTypeAndSeriesAtCreation |
| P08 | New drama: apply a saved preset | 1327-1347 | `apply_preset_to_session` (90) | MISSING | `POST /api/dramas` (`preset_id`) | wt TestApplyPresetOnNewDrama |
| P09 | Create drama button | 1339-1358 | `db.create_drama` | `pages/Library.tsx` | `POST /api/dramas` | wt TestNewDramaLanguageSelector |
| P10 | Edit metadata: titles, source URL, credits, summary, content type, genre, publication status, chapter count, custom tags, episode number, running episode summary | 1376-1431, 1486-1503 | `db.update_drama` | MISSING (no form; only auto-fill apply writes some fields) | `POST /api/dramas/{id}/metadata` | test_library_features TestCustomTagsAndMetadata, TestPreviousEpisodeSummary |
| P11 | Series picker in Edit metadata (assign, or "+ New series" then create and assign) | `_series_picker` 36-77, 1403 | `db.get_or_create_series`, `db.update_drama` | MISSING | `POST /api/dramas/{id}/metadata` (`series_id` only; no create-series endpoint) | test_library_features TestSeriesPickerSharedBetweenMetadataAndGlossary |
| P12 | Personal notes (private per profile) | 1432-1441, 1501 | `db.get/save_personal_notes` | MISSING (per-profile scoping DROPPED, section 10) | no API | test_library_features TestProfiles |
| P13 | Romanize credits (LLM), shown as bilingual credits | 1442-1479 | `tguide.romanize_metadata`, `format_bilingual_credit` | `stages/CreditsCoverPanel.tsx` (bilingual credits, Romanize credits with the drama's engine) | `POST /api/metadata/dramas/{id}/romanize-credits` (`admin.library`; a paid engine also needs `engines.paid`; LLM slot) | wt TestRomanizeCreditsEnginePassesOllamaUrlAndFreeTier |
| P14 | Cover art upload and preview | 1481-1491 | file write + `db.update_drama` | `stages/CreditsCoverPanel.tsx` (preview, PC-only upload/replace); cover also on the Library Continue strip | `POST /api/dramas/{id}/cover` (`local_only`; PNG/JPEG/WebP, 10 MB, 25 MP, re-encoded without metadata), `GET /api/dramas/{id}/cover` (`library.read`) | — |
| P15 | Delete drama: checkbox plus typed DELETE, blocked while a job runs | 1504-1526 | `background_jobs.any_job_running_for_drama`, `db.delete_drama` | `components/DramaDetailPanel.tsx` | `DELETE /api/dramas/{id}?confirm=true&confirm_text=DELETE` | wt TestDestructiveActionsNeedConfirmation, TestDeleteDramaBlockedByRunningJob |
| P16 | Project header and stage stepper showing real progress | 1528-1542 | `workflow_service.compute_workspace_stage_index`, `ui.project_header` | DONE `pages/workspace/WorkspaceShell.tsx` (stepper marks done/next/optional/blocked from the progress API, plus line/untranslated/flagged counts) | `GET /api/workflow/dramas/{id}/progress` | wt TestWorkspaceStageIndex, test_ui_components TestProjectHeader |
| P17 | Stage tabs open on the drama's current stage | 1544-1553 | same | DONE (`#/drama/{id}` with no stage opens the stage the progress API reports; Library links omit the stage; Source if progress fails) | `GET /api/workflow/dramas/{id}/progress` | wt TestStageTabsOpenOnTheCurrentStage, TestStageTabsReplaceTheExpanderScroll |

### 2.2 Source tab (1555-1911)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| S01 | Source language (saved to the drama) | 1557-1564 | `db.update_drama` | `stages/TranscribeStage.tsx` (sent with the run) | `POST /api/transcribe/dramas/{id}/run` (`source_language`); `POST /api/source/dramas/{id}/config` | — |
| S02 | Chinese script: simplified or traditional | 1566-1581 | `db.update_drama` | `stages/TranscribeStage.tsx` | same | — |
| S03 | Content mode: audio drama, streamer VOD or novel narration. Streamer VOD also sets media type | 1583-1605 | `db.update_drama` | MISSING | `POST /api/source/dramas/{id}/config` (`content_mode`) | wt TestRawNovelToggleGatedByContentMode |
| S04 | "I have the original novel" toggle: upload, save or remove the raw novel used to prime Whisper | 1619-1660 | `core.load_novel_text_for_context` → `raw_novel_context.txt` | `stages/NovelFilePanel.tsx` kind=raw in TranscribeStage (status, PC-only upload or paste, replace, remove; refreshes the auto prompt; replaces NovelPanel's old remove line), branch `slice-novel-uploads`. No content-mode toggle: offered for every mode | `GET`/`POST /api/novel/dramas/{id}/raw-novel` (.txt/.md/.epub; status `library.read`, upload `local_only()`), `POST .../raw-novel/text` (paste); `POST .../raw-novel/remove` (#374) | test_workspace_raw_novel TestRawNovelContextRemove; wt TestRawNovelToggleGatedByContentMode; test_api_novel_files |
| S05 | Current audio/video caption, and remove it (with confirm) | 1667-1690 | `os.remove`, `db.update_drama` | `stages/SourceStage.tsx` status + remove, PC-only (#387) | `GET /api/media/dramas/{id}/status`; remove: `POST /api/media/dramas/{id}/remove` (#374) | wt TestFourMoreDestructiveActionsNeedConfirmation |
| S06 | Upload an audio or video file (video is converted to `audio.wav` on run) | 1703-1707, 3107-3122 | `extract_audio_from_video`, `db.update_drama` | `stages/SourceStage.tsx` | `POST /api/media/dramas/{id}/upload`, `/upload-and-transcribe` | — |
| S07 | Download from a URL with yt-dlp: audio-only default per content mode, cookies from Settings, title auto-fill | 1708-1769 | `video_download.download`, `core.extract_audio_from_video` | MISSING | `POST /api/media/dramas/{id}/download-url` (local_only; no cookies over the API) | wt TestDownloadButtonUsesCookieSettings, TestDownloadAudioOnlyDefault |
| S08 | Transcript source: have transcript, Whisper, or burned-in captions (OCR, video only) | 1771-1792 | `db.update_drama(transcript_mode)` | MISSING picker (React follows the stored mode) | `POST /api/source/dramas/{id}/config` (`transcript_mode`) | — |
| S09 | Paste the transcript | 1794-1799 | — (written to `transcript.txt` at 3134) | `stages/TranscribeStage.tsx` "Transcript text" | `POST /api/transcribe/.../run` (`transcript_text`) | wt TestReferenceNovelUploadDoesNotLeakAcrossDramas |
| S10 | Hardsub OCR engine and frame interval | 1800-1823 | — | `stages/TranscribeStage.tsx` (Advanced) | `POST /api/transcribe/dramas/{id}/config` | — |
| S11 | Novel narration: OCR chapter page images | 1835-1861 | `ocr.extract_text_from_images` | `stages/NovelPanel.tsx` | `POST /api/novel/dramas/{id}/ocr-chapter` | — |
| S12 | Novel narration: pre-fill the text from chapters imported in Sources (`raw_novel_context.txt`) | 1863-1875 | file read | `stages/NovelPanel.tsx` "Use chapters imported in Sources" (shown when the raw novel exists; Replace/Append) | `POST /api/novel/dramas/{id}/attach-from-sources` (`local_only`) | — |
| S13 | Novel narration: import an EPUB with a chapter range | 1877-1909 | `epub_io.get_epub_chapter_count`, `import_epub_text` | `stages/NovelPanel.tsx` (From/To chapter, blank = first/last; the result names the range and chapter count) | `POST /api/novel/dramas/{id}/attach-epub` | wt TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate |
| S14 | Novel narration text box | 1911 | — (saved on run, 3189-3191) | `stages/NovelPanel.tsx` | `POST /api/novel/dramas/{id}/attach-text` | — |

### 2.3 Transcript tab (1913-2362) and the transcribe run (2927-3460)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| T01 | Upload or paste an existing English novel translation as translation reference | 1913-1928, saved 3124-3132, used 3514-3519 | file write, `db.update_drama(novel_reference_filename)` | `stages/NovelFilePanel.tsx` kind=reference in TranslateStage (status, PC-only upload or paste, replace, remove), branch `slice-novel-uploads` | `GET`/`POST /api/novel/dramas/{id}/reference` (.txt/.md; status `library.read`, upload `local_only()`), `POST .../reference/text` (paste, `local_only()`), `POST .../reference/remove` (`local_only()`, confirm) | wt TestReferenceNovelUploadDoesNotLeakAcrossDramas; test_api_novel_files |
| T02 | Build a glossary from the novel (LLM), optionally paired with the original; review the proposals, then add them to the series glossary | 1931-2039 | `tguide.extract_glossary_from_novel`, `db.upsert_glossary_term` | `stages/NovelGlossary.tsx` in `GlossaryPanel.tsx` (#387) | `POST /api/glossary/dramas/{id}/from-novel`, `GET .../from-novel`, `POST .../from-novel/apply` (#365) | wt TestOriginalNovelForGlossaryDoesNotLeakAndNeedsExplicitSave |
| T03 | Import a glossary file (CSV/TSV/JSON) | 2041-2059 | `tguide.parse_glossary_file` | MISSING | no API | — |
| T04 | Export the glossary as CSV | 2061-2066 | `tguide.glossary_to_csv` | MISSING | no API | — |
| T05 | Speech model picker, language warning and "not downloaded yet" note | 2068-2084, 2952-2956 | `core.whisper_model_warning`, `is_whisper_model_cached` | `stages/TranscribeStage.tsx` + `sourceForm.whisperModelWarning` | `GET/POST /api/transcribe/dramas/{id}/config` | wt TestWhisperSizeDefaultsToLargeV3 |
| T06 | Fast (batched) mode | 2085-2090 | — | `stages/TranscribeStage.tsx` | same | — |
| T07 | Groq cloud transcription toggle and key | 2091-2102 | `synced_api_key_input` | PARTIAL: toggle in TranscribeStage; key entry MISSING (see G06) | config `use_groq`; key via `POST /api/settings/keys/groq` | — |
| T08 | Initial prompt built automatically from glossary names and a raw-novel excerpt, plus extra typed names | 2104-2135 | `core.build_initial_prompt`, `extract_novel_excerpt_for_prompt`, `combine_initial_prompt` (now via `transcribe_service.build_auto_initial_prompt`, shared with `cli.cmd_align`) | `stages/TranscribeStage.tsx`: read-only preview of the auto prompt ("from glossary and novel"), an "Extra names to expect" input joined after the glossary names as in Streamlit (used by Transcribe and Auto-tune), and a folded "Advanced: replace the automatic prompt" override | config `auto_initial_prompt`; run and autotune `extra_names`, `initial_prompt` (non-empty = full override) | test_transcription_quality TestWhisperSettings |
| T09 | Beam size, silence split and VAD sensitivity sliders | 2137-2164 | — | `stages/TranscribeStage.tsx` Advanced | config | wt TestSpeechSplittingSensitivityDefaultAndPersistence |
| T10 | Auto-tune the silence split: one background re-transcription per candidate, cancel, results table, "Use X ms", discard | 2166-2282 | `core.autotune_subprocess_worker`, `diagnose_line_coverage` | `stages/AutoTune.tsx` in Transcribe > Advanced (#387) | `POST /api/transcribe/dramas/{id}/autotune`, `GET .../autotune`, `POST .../autotune/apply` (#365) | wt TestAutotuneRealMidRunStop |
| T11 | Remove background music first, plus model choice | 2284-2300 | `audio_preprocess.SEPARATION_BACKENDS` | `stages/TranscribeStage.tsx` | config | test_transcription_quality TestVocalSeparationBackends |
| T12 | Split long merged lines by word alignment | 2302-2314 | — | `stages/TranscribeStage.tsx` | config | — |
| T13 | Timing method (character diff or Qwen3 forced align) | 2320-2341 | `db.update_drama` | `stages/TranscribeStage.tsx` | config | test_transcription_quality TestForcedAlignerReliability |
| T14 | Transcription model (Whisper or Qwen3-ASR) | 2343-2362 | `db.update_drama` | `stages/TranscribeStage.tsx` | config | — |
| T15 | Run button ("Transcribe & Align" / "Transcribe with Whisper" / "Read Captions from Video") and a "still needed" message | 2927-2958, 3104-3187 | `background_jobs.start_job(run_transcribe_job / run_hardsub_ocr_job)` | `stages/TranscribeStage.tsx` | `POST /api/transcribe/.../run`, `POST /api/media/.../upload-and-transcribe` | wt TestTranscribeQueuesBehindAnotherGpuJob |
| T16 | Job progress, refresh, cancel and queued panel | 3239-3256, `_render_queued_job_panel` 666 | `background_jobs.request_cancel`, `cancel_queued` | `stages/JobPanel.tsx`, `hooks/useJob.ts` | `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` | wt TestTranscriptionCancelButton, TestQueuedJobPanelVisibleAndCancellable, TestJobEtaDisplay |
| T17 | Completion handling: failure reasons, GPU-fallback and word-align warnings, Qwen3 ASR/forced align, refuse to wipe existing lines, history snapshot, raw transcript file, status "aligned", auto-start diarization | 3257-3450 | `align_transcript_to_timing`, `raw_transcript.write_raw_transcript`, `db.save_line_history_snapshot` | Server side: `services/transcribe_service.py:369` (on-done apply). Now served (branch `api-job-results`): `GET /api/jobs/{id}` returns the redacted `result` plus `outcome`/`outcome_message` (failed, cancelled, kept_existing, ...), and `stages/JobPanel.tsx` shows the outcome in words | `GET /api/jobs/{id}` | wt TestTranscriptionCompletionDoesNotWipeExistingLines, TestDiarizationAutoStartsAfterAlign, TestTranscribeJobInputsCapture |
| T18 | Novel narration: "Chunk & Tag Speakers" (LLM speaker tags, with a snapshot first) | 2950-2951, 3189-3234 | `chunk_novel_text`, `translate_engines.tag_speakers_by_id` | `stages/NarrationPanel.tsx` | `GET/POST /api/narration/dramas/{id}/config|run` | wt TestChunkAndTagSpeakersHistorySnapshot |

### 2.4 Diarize tab (2364-2395; helpers 1017-1139)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| D01 | Hugging Face token field | 2371-2372 | `synced_api_key_input` | MISSING (see G06) | `POST /api/settings/keys/hf_token` | — |
| D02 | "Run speaker diarization during alignment" | 2373-2375 | — | `stages/TranscribeStage.tsx` (`run_diarize`) | run `run_diarize` | wt TestDiarizationAutoStartsAfterAlign |
| D03 | Expected speakers, defaulting to the last run | 2376-2386 | `diarize.load_last_speaker_count` | `stages/TranscribeStage.tsx` "Expected speakers" (default from last run UNK) | run/diarize `expected_speakers`; `GET /api/diarization/dramas/{id}/config` | test_speaker_rerun TestExpectedSpeakersDefaultsToLastRun, TestExpectedSpeakersCapturedAtJobStart |
| D04 | Re-run speaker detection only (no ASR), with a time estimate | 1070-1097, 683-701 | `diarize.diarize_subprocess_worker` | `stages/TranscribeStage.tsx` (estimate caption MISSING) | `POST /api/diarization/dramas/{id}/run` | test_speaker_rerun TestRerunButton; wt TestDiarizationEstimateCaption |
| D05 | Diarize job running, cancel, cancelled or error | 1099-1121 | `background_jobs.request_cancel` | `stages/JobPanel.tsx` | `/api/jobs` | wt TestSpeakerDetectionRealMidRunStop |
| D06 | Hand-corrected speakers conflict: "keep my N corrections" or "overwrite them" | 1034-1068, 1125-1138 | `diarize.manual_lines_that_would_change`, `merge_speakers` | PARTIAL: React always keeps corrections; the overwrite choice is MISSING | `POST /api/diarization/.../run?overwrite_manual=true&confirm=true` | test_speaker_rerun TestRerunButton, TestMergeSpeakers |

### 2.5 Translate tab (2397-3635) and character setup (3637-3966)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| X01 | Notice about the last run's failed batches, with Dismiss | 2399-2414 | `db.update_drama(last_translate_errors=None)` | DONE `stages/TranslateStage.tsx` FailedBatchesNotice (batch count, 1-based line ranges, distinct reasons; Dismiss notice) | `GET /api/translate-run/dramas/{id}/config`; `POST /api/translate-run/dramas/{id}/errors/dismiss` (lines.edit, `translate_run_service.dismiss_translate_errors`) | test_api_translate_errors_dismiss |
| X02 | Starting tier (Draft/Standard/Release sets engine, model, Reflect and Auto QC) | 2416-2428, `apply_workflow_tier` 117 | `translate_engines.WORKFLOW_TIERS`; `translate_run_service.apply_workflow_tier` | `stages/TranslateStage.tsx` "Starting tier" + "Apply tier" (fills engine, model, Reflect and refreshes the form's Default engine; Auto QC has no React flag, so the status line only says the tier recommends it) | `POST /api/translate-run/dramas/{id}/workflow-tier` (`lines.edit`) | test_project_instructions TestWorkflowTiers; test_api_translate_presets_tiers |
| X03 | Apply a saved preset | 2430-2447 | `apply_preset_to_session` | MISSING | no API (presets list only) | wt TestPresetsInWorkspaceUI |
| X04 | Translation style and its guidance text | 2455-2467 | `tguide.STYLE_PRESETS` | `stages/TranslateStage.tsx` Style (guidance text MISSING) | run `style_preset` | — |
| X05 | Include baihe/GL genre guidance toggle | 2476-2480 | used in `tguide.build_style_guidelines` 3539 | `stages/TranslateStage.tsx` Advanced checkbox, prefilled from the preset (branch `fix-translate-preset-parity`) | run `include_genre_notes` (omitted: on); CLI `--no-genre-notes` | test_translate_run_start (prompt toggles); translateForm.test |
| X06 | Default ambiguous pronouns to she/her | 2481-2490 | same | `stages/TranslateStage.tsx` Advanced checkbox, prefilled from the preset (branch `fix-translate-preset-parity`) | run `default_female_pronouns` (omitted: off); CLI `--female-pronouns` | wt TestPerDramaPronounPicker; test_translate_run_start (prompt toggles); translateForm.test |
| X07 | Project instructions, saved on change | 2496-2506 | `db.update_drama(project_instructions)` | `stages/GlossaryPanel.tsx` | `POST /api/glossary/dramas/{id}/instructions/project` | test_project_instructions TestInstructionsPersistAndInherit, TestInstructionsUi |
| X08 | Series instructions | 2518-2527 | `db.update_series_instructions` | `stages/GlossaryPanel.tsx` | `.../instructions/series` | same |
| X09 | Series picker inside the glossary box | 2513 | `_series_picker` | MISSING (see P11) | metadata `series_id` | test_library_features TestSeriesPickerSharedBetweenMetadataAndGlossary |
| X10 | Auto-extract terms from the source lines (LLM), review, then add | 2529-2576 | `tguide.extract_terms_llm` | MISSING | no API | — |
| X11 | Glossary list with inline edit of every field (term, translation, category, policy, notes, enforce exactly, aliases, banned translations) | 2578-2646 | `db.update_glossary_term` | `stages/GlossaryPanel.tsx` | `GET/POST /api/glossary/dramas/{id}/terms` | test_db TestSeriesAndGlossary |
| X12 | Delete one term (with confirm) | 2590-2596 | `db.delete_glossary_term` | MISSING | `DELETE /api/glossary/dramas/{id}/terms/{term_id}` | wt TestFourMoreDestructiveActionsNeedConfirmation |
| X13 | Bulk delete selected terms | 2650-2668 | same | `stages/GlossaryPanel.tsx` (#346) | single delete only | wt TestBulkGlossaryAndPronounActions |
| X14 | Add term form | 2670-2697 | `db.upsert_glossary_term` | `stages/GlossaryPanel.tsx` | `POST .../terms` | — |
| X15 | Series people and pronouns: list, rename, remove (confirm), notes, pronouns (preset or custom) | 2699-2750, `_pronoun_picker` 149 | `db.*_series_character` | PARTIAL `stages/SeriesCast.tsx`: list + PC-only remove (#387); rename, notes and pronoun edits MISSING | read-only `GET /api/characters/series/{id}/characters` | wt TestBulkGlossaryAndPronounActions; test_db TestSeriesCharacters |
| X16 | Bulk set pronouns for selected series people | 2752-2768 | `db.upsert_series_character` | MISSING | no API | wt TestBulkGlossaryAndPronounActions |
| X17 | Add a known series character | 2770-2777 | same | MISSING | no API | — |
| X18 | Engine and model pickers (Claude/Gemini/Ollama/NLLB) | 2780-2833 | `translate_engines.ENGINES`, `*_MODELS` | `stages/TranslateStage.tsx` | `GET /api/translate-run/.../config`, run `engine`/`model` | wt TestFreeEngineVersionLabelling, TestGemini31FlashLiteInDropdown |
| X19 | Gemini free tier blocks Pro | 2810-2813, 2834-2836 | `GEMINI_FREE_TIER_UNAVAILABLE_MODELS` | Server side (`translate_run_service.py`, review jobs, line AI): an omitted `gemini_free_tier` now falls back to the saved Settings value (`settings_service.resolve_gemini_free_tier`), so Pro is blocked for a free-tier user without the client sending the flag | run | wt TestGeminiFreeTierProGating |
| X20 | Per-engine API key field (test_offline needs none) | 2838-2854 | `synced_api_key_input` | MISSING (see G06) | `POST /api/settings/keys/{engine}` | — |
| X21 | Style notes and English variant | 2868-2877 | — | `stages/TranslateStage.tsx` Style note, Locale | run `style_note`, `locale` | — |
| X22 | Save current settings as a preset | 2879-2892 | `db.save_preset`; `translate_run_service.save_translate_preset` | `stages/TranslateStage.tsx` Advanced "Save as preset…" (name prompt; a taken name asks "Replace it"). A preset saved with "Engine default" stores null for the model (Streamlit always stored a concrete model) | `POST /api/translate-run/presets` (`admin.library`; insert-only, 409 on a taken name unless `overwrite`; `overwrite` is PC-only, 403 remotely with auth on) | wt TestPresetsInWorkspaceUI; test_api_translate_presets_tiers |
| X23 | Context before/after and batch size sliders (higher defaults for novels) | 2893-2925 | — | `stages/TranslateStage.tsx` Advanced | run fields | wt TestContextAheadAndBatchSizeSliders |
| X24 | Ollama unreachable warning (button disabled) | 2965-2970 | `translate_engines.check_ollama_reachable` | UNK in React | UNK | wt TestOllamaReachabilityGatesTranslateButton, TestTranslateButtonUsesConfiguredOllamaUrl |
| X25 | Monthly spending cap refusal | 2972-2984 | `resolve_cost_cap` | Server side (`translate_run_service.py:168`); React shows the API error | run | wt TestSpendingCapUI, TestSpendingCapJob |
| X26 | "Translate all lines" background job | 2986-2989, 3510-3594 | `run_translate_job` | `stages/TranslateStage.tsx` | `POST /api/translate-run/dramas/{id}/run` | wt module-level run_translate_job tests |
| X27 | Force re-translate (snapshot first) | 2990-2999, 3524-3526 | `db.save_line_history_snapshot` | `translateForm.ts` force + confirm | run `force_retranslate` | wt TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate |
| X28 | Review the glossary before translating (LLM proposals, then start or cancel) | 3001-3011, 3466-3508 | `tguide.extract_glossary_from_novel` | MISSING | no API | wt TestGlossaryReviewBeforeTranslating |
| X29 | Reflect mode (3 passes) with its cost estimate | 3013-3020, 3063-3082 | `estimate_reflect_mode_cost` | `stages/TranslateStage.tsx` Reflect + Estimate | run `reflect`; `GET .../estimate` | wt TestReflectModeUI |
| X30 | "Auto QC before export" toggle | 3022-3028 | read at 5699 | Always on in React (`ExportFlags.tsx` shows the count) | `GET /api/export/.../readiness` | test_auto_qc |
| X31 | Bulk mode (batch APIs / DeepSeek off-peak, bulk Reflect) | 3030-3061, 348-478 | `bulk_translate.*` | `stages/TranslateStage.tsx` Bulk | run `bulk` | wt TestBulkModeUI; test_bulk_translate |
| X32 | Cost estimate captions | 3063-3089 | `estimate_translation_cost` | `stages/TranslateStage.tsx` "Estimate cost" | `GET .../estimate` | — |
| X33 | Per-job cost cap | 3091-3102 | — | `stages/TranslateStage.tsx` Cost cap | run `job_cost_cap_usd` | wt TestSpendingCapUI |
| X34 | Translate job progress and done messages (cap reached, failed lines) | 3596-3633 | — | `stages/JobPanel.tsx`; now served: `result` (`errors`, `cap_reached`, `lines_replaced`) and `outcome` partial/failed shown in words (branch `api-job-results`) | `/api/jobs` | wt TestTranslateJobRefreshesStaleEnBoxes |
| X35 | Bulk jobs panel: list, "Check now", cancel, auto-resume after restart | 493-577 | `bulk_translate.resume_pending`, `check_once`, `cancel_bulk_job` | `stages/TranslateStage.tsx` bulk batches panel: list, resume, cancel (#379) | `POST .../bulk/resume`; list `GET .../bulk` and `POST .../bulk/{id}/cancel` (Slice 51, #352) | test_bulk_translate TestRestartCancelAuth |
| C01 | Auto-extract voice reference clips from the diarized audio | 3650-3681 | `dub.extract_reference_clips` (scoring reused) | `stages/VoiceClonePanel.tsx` "Find clips in the audio": per speaker, preview candidates, pick one (branch `slice-voice-clone-setup`) | `POST /api/characters/dramas/{id}/reference-clips/extract` (job `voiceref_<id>`), `GET .../candidates`, `GET .../candidates/{cid}/audio`, `POST .../candidates/{cid}/choose` | test_api_voice_clone TestExtract; wt TestCharacterNamingGaps |
| C02 | Recurring-voice suggestions ("sounds like X"): accept or reject | 3685-3720 | `voice_id.suggest_speaker_matches`, `db.dismiss_voice_suggestion` | MISSING | no API | wt TestVoiceMatchSuggestions |
| C03 | Pick a known series character for a speaker | 3729-3746 | `db.upsert_character(series_character_id)`, `db.clear_character_series_link` | `stages/VoiceClonePanel.tsx` "Series character" (link or unlink) | `POST /api/characters/dramas/{id}/series-link` | test_api_voice_clone TestBankLinkActor; wt TestCharacterNamingGaps |
| C04 | Sample lines per speaker and "shared with series" caption | 3747-3768 | — | MISSING (line count only) | `GET /api/characters/dramas/{id}` | wt TestCharacterNamingGaps |
| C05 | Character name, voice actor and edge-tts voice | 3770-3788 | `db.upsert_character` | `stages/CharactersPanel.tsx` (name + voice); voice actor in `stages/VoiceClonePanel.tsx` | `POST /api/characters/dramas/{id}/character` (`voice_actor` added) | test_api_voice_clone TestBankLinkActor |
| C06 | Offline/Piper voice | 3789-3799 | same | MISSING | same (`offline_voice`) | wt TestNarrationVoiceSetup |
| C07 | Per-drama pronouns with the series default and a custom option | 3800-3810 | same | PARTIAL `stages/CharactersPanel.tsx` (presets only) | same (`pronouns`) | wt TestPerDramaPronounPicker |
| C08 | "Remember as a known series character" | 3811-3824 | `db.upsert_series_character` | MISSING | no API | — |
| C09 | Clone reference status, skip reasons, upload a clip | 3826-3867 | file write (`services/voice_clone_service.py`) | `stages/VoiceClonePanel.tsx`: status, skip reason, upload/replace/remove (PC-only) | `POST /api/characters/dramas/{id}/reference-clip` (multipart), `.../reference-clip/remove` (both `local_only()`); skip reason in `GET .../reference-clips/candidates` | test_api_voice_clone TestUpload; wt TestCharacterNamingGaps, TestDubTimingAndRemovedCloneUI |
| C10 | Reference clip transcript text | 3849-3869 | same | MISSING | same (`ref_text`) | — |
| C11 | Clone engine per character (limited to engines that speak the source language in original-language narration) | 3871-3923 | `dub.CLONE_ENGINES`, `clone_engine_supports_language` | MISSING | same + `GET /api/characters/dramas/{id}/clone-engines` | wt TestNarrationVoiceSetup |
| C12 | Describe a voice (OmniVoice voice design) | 3902-3910 | same | MISSING | same (`voice_design`) | — |
| C13 | Save a character's voice to the voice bank | 3924-3948 | `db.save_voice_bank_entry` | `stages/VoiceClonePanel.tsx` "Save to voice bank" | `POST /api/characters/dramas/{id}/voice-bank/save` (`admin.library`) | test_api_voice_clone TestBankLinkActor; test_db TestVoiceBank |
| C14 | Apply a voice from the bank | 3949-3965 | `db.apply_voice_bank_entry` | MISSING | `POST /api/characters/dramas/{id}/voice-bank/apply` | — |

### 2.6 Review tab (3968-5454)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| R01 | Pick up a finished flag job before counting flags | 3982-3984 | `db.load_line_objects` | N/A | — | test_step20_ux_polish TestAutoQCToastVsWarning |
| R02 | Media player with seek, "Jump to time", "Play current segment" (Alt+Space), selected line | 823-988, 3988-3990 | `st.video`/`st.audio`, `parse_timestamp` | DONE `stages/review/Player.tsx` (#377, `react-review-player`): open by default (Hide/Show player), seek bar with time/duration, "Jump to time" (mm:ss, h:mm:ss or seconds), "Go to line #N" for the selected line, Alt+Space; subtitles on the video (English, Original, Both or Off, re-read after every save; audio shows the current cue as text); phones keep the video and tools below the sticky toolbar. Burned-subtitle preview not ported | `GET`/`HEAD /api/media/dramas/{id}/audio` and `.../video` with Range (Slice 52, #352); subtitles from `GET /api/reader/dramas/{id}/captions/{track}` (lines.read) | test_media_preview TestReviewPlayer, TestRowClickSeeksInWorkspace, TestParseTimestamp, TestPlayerTimes |
| R03 | Burned-subtitle preview around the selected line (video) | 958-987, 877-894 | `video_export.render_preview_clip` | MISSING | no API | test_media_preview TestBurnPreview |
| R04 | Find and replace over translations: preview, apply by line id, skip stale lines, update translation memory | 3992-4066 | `scanlate.bulk_find_replace_preview`, `db.update_translation_memory_after_replace` | `stages/review/FindReplacePanel.tsx` | `POST /api/review/.../find-replace/preview`, `POST /api/lines/.../find-replace/apply` | test_review_workspace TestLineFindAndReplace |
| R05 | Search the transcript and jump to the line's page | 4068-4085 | `_search_transcript` | PARTIAL `stages/review/LinesPanel.tsx` search (jump to page MISSING) | `GET /api/review/dramas/{id}/search` | test_step20_ux_polish TestSearchTranscript, TestTranscriptSearchJumpsToCorrectPage |
| R06 | Filters: flagged only, untranslated only (with counts) | 4087-4104 | — | `stages/review/LinesPanel.tsx` Show | `GET /api/review/dramas/{id}/lines?only=` | test_review_workspace TestReviewAndEditUI |
| R07 | Lines per page and page number | 4106-4112 | — | `stages/review/LinesPanel.tsx` (fixed 40) | same | — |
| R08 | Previous/next flagged line across pages (Alt+Up/Down) | 4114-4127 | `_adjacent_flagged_idx` | PARTIAL (React: within the current page) | — | test_step20_ux_polish TestAdjacentFlaggedIdx |
| R09 | Flag banner and Dismiss | 4136-4143 | `db.save_lines` | `stages/review/LineRow.tsx` | `POST /api/lines/.../lines/{id}/dismiss-flag` | — |
| R10 | Content-blocked line: retry with another engine | 4144-4194 | `engine.translate_batch` | `stages/review/LineRow.tsx` (Edit details, engine picker) | `POST /api/lines/dramas/{id}/lines/{line_id}/retry-blocked` (`blocked_retry_service`) | wt TestContentBlockedRetryWithDifferentEngine |
| R11 | Translation memory suggestion per line: accept or dismiss | 4195-4212 | `translation_memory.suggest_for_lines`, `db.bump_translation_memory_use` | PARTIAL `stages/review/RecordsPanel.tsx` accept (list, not per line); dismiss MISSING | `GET /api/review/.../tm-suggestions`, `POST /api/lines/.../accept-tm` | test_translation_memory TestTranslationMemoryInReview |
| R12 | Edit a line's start, end, speaker, source and translation | 4213-4225, 4428-4458 | — | `stages/review/LineRow.tsx` | `POST /api/lines/dramas/{id}/lines/{line_id}` | wt TestSaveEditsDoesNotRoundUntouchedTimestamps, TestLineEditingNotLockedDuringAJob |
| R13 | Row "▶" seeks the player to that line | 4227-4232 | `_seek_to_line` | DONE `stages/review/LineRow.tsx` "▶ Play" (#377) seeks the player and plays the line; the player's "Go to line #N" seeks without playing (`react-review-player`) | — | test_media_preview TestRowClickSeeksInWorkspace |
| R14 | Mark a line as a non-verbal/SFX cue | 4234-4238 | `Line.sfx` | `stages/review/LineRow.tsx` "Sound cue" checkbox (#377) | same endpoint (`sfx`) | test_media_preview TestSfxPersistence, TestSfxExport |
| R15 | Improve translation, then "Use this" | 4241-4266 | `line_tools.improve_line`, `db.record_edit_sample` | `stages/review/LineAi.tsx` | `POST /api/line-ai/.../improve` | wt TestImproveTranslationUseThisRefreshesTheEnBox |
| R16 | "Why this?" explanation | 4272-4279 | `line_tools.explain_translation` | `stages/review/LineAi.tsx` | `POST /api/line-ai/.../explain` | wt TestPerLineExplainToolsMovedFromReader |
| R17 | Alternative translations | 4280-4287 | `line_tools.alternative_translations` | MISSING | no API | same |
| R18 | Grammar breakdown | 4288-4295 | `line_tools.grammar_breakdown` | MISSING | no API | same |
| R19 | Pronounce (edge-tts clip) | 4296-4302 | `line_tools.pronunciation_audio` | MISSING | no API | same |
| R20 | "What happened?": speaker, engine, flag, glossary matches, notes, consistency, neighbours | 4303-4336 | `debug_view.explain_line` | `stages/review/LineOrigin.tsx` (#392) | `GET /api/review/.../lines/{id}/provenance` | — |
| R21 | Save a bug-reproduction bundle | 4337-4346 | `debug_view.save_bug_bundle` | MISSING (a Diagnostics extra, undecided) | no API | test_diagnostics_and_export TestBugBundleDeleteNeedsConfirmation |
| R22 | Play just this line's audio | 4355-4375, `_line_audio_clip` 789 | `core.extract_audio_slice` | DONE `stages/review/LineRow.tsx` "▶ Play" (#377): plays the line's span of the drama audio/video and stops at its end (Loop line repeats it); no separate clip file | no API (uses the Range stream) | test_review_workspace TestLineAudioClip |
| R23 | Re-transcribe one line, then "Use this" | 4376-4411 | `core.transcribe_for_timing` (via `transcribe_service.start_retranscribe_line` / `get_retranscribe_result` / `apply_retranscribe_line`) | `stages/review/RetranscribeLine.tsx` in the line details: "Heard" next to the current text, "Use this" / "Discard" | `POST /api/transcribe/dramas/{id}/lines/{line_id}/retranscribe` (`jobs.start`), `GET` same path (`lines.read`), `.../retranscribe/apply` (`lines.edit`) | wt TestRetranscribeUseThisRefreshesTheZhBox, test_api_retranscribe_line |
| R24 | Compare with the original transcript, and restore it | 4412-4427 | `raw_transcript.original_text_for_line` | PARTIAL `stages/review/LineOrigin.tsx` shows the original (#392); restore MISSING | `GET /api/review/.../lines/{id}/original-text` + line patch | test_raw_transcript TestRestoreOneLineInReview |
| R25 | Save edits (Ctrl+S), record edit samples and translation memory, "Unsaved changes (N)" indicator | 4460-4491, `_unsaved_line_count` 990 | `db.save_lines`, `record_edit_sample`, `record_translation_memory` | `stages/review/LineRow.tsx` per-line save (server records samples/TM, `lines_service.py:164-166`); page-level unsaved count N/A | `POST /api/lines/...` | test_review_workspace TestUnsavedLineCount, TestReviewAndEditUI |
| R26 | Line coverage check (long lines, silent gaps, blank source/translation) | 4494-4526 | `core.diagnose_line_coverage` | `stages/review/ReviewChecks.tsx` (#392) | `GET /api/review/dramas/{id}/coverage` | — |
| R27 | Dubbing pacing check with jump-to-line | 4528-4550 | `translate_engines.smart_segment_lines` | `stages/review/ReviewChecks.tsx` with go-to-line (#392) | `GET /api/review/dramas/{id}/pacing-flags` | — |
| R28 | Auto-shorten overlong lines (LLM) | 4551-4569 | `translate_engines.rewrite_for_pacing_llm` | MISSING | no API | — |
| R29 | Consistency check job, with a bulk option, and the found issues listed | 4571-4650 | `run_consistency_job`, `db.load_consistency_issues` | PARTIAL `stages/review/ReviewJobsPanel.tsx` starts it; results list `ReviewFindings.tsx` (#392); bulk MISSING | `POST /api/review-jobs/.../consistency`; `GET /api/review/.../consistency` | test_bulk_translate TestBulkConsistency |
| R30 | Review queue: flag lines for a second look (with bulk option) | 4652-4715 | `run_flag_job` | `stages/review/ReviewJobsPanel.tsx` (bulk MISSING) | `POST /api/review-jobs/.../flag` | test_bulk_translate TestBulkFlag |
| R31 | Run Auto QC (numbers, dates, names, units) | 4717-4745 | `_run_auto_qc` 779 → `auto_qc.run_auto_qc` | `stages/ExportFlags.tsx` (in Export, not Review) | `POST /api/export/.../flag-auto-qc` | test_auto_qc; test_step20_ux_polish TestAutoQCToastVsWarning |
| R32 | Fix flagged lines: re-transcribe and re-translate, with the cap | 4747-4845 | `run_fix_flagged_lines_job` | `stages/review/ReviewJobsPanel.tsx` (free tier now from the saved setting when omitted; the per-job cap defaults to none, same as a translate run, and the monthly cap still applies); fixed/total/errors/cap now served as job `result` + `outcome` (branch `api-job-results`) | `POST /api/review-jobs/.../fix-flagged` | wt TestFixFlaggedLinesCapUI; wt fix_flagged job tests |
| R33 | Emotion detection job (audio cues, bulk option) | 4848-4918 | `run_emotion_job` | PARTIAL `stages/review/ReviewJobsPanel.tsx` (audio-cues toggle in Check options, default on with audio; bulk MISSING) | `POST /api/review-jobs/.../emotion` (`use_audio_cues`) | test_emotion_manhua_ui TestDetectEmotionsProgress |
| R34 | Emotion summary (tagged, high-risk, per emotion) | 4920-4929 | `emotion.emotion_summary` | `stages/review/ReviewFindings.tsx` (#392) | `GET /api/review/dramas/{id}/emotions` | test_emotion_manhua_ui TestEmotionSummary |
| R35 | SenseVoice tagging from the audio, with a side-by-side table | 4931-4985 | `run_sensevoice_job`, `sensevoice_tags.side_by_side` | MISSING | no API | test_transcription_quality TestSenseVoiceTags |
| R36 | Adaptive style: edit tendencies metrics | 4987-5002 | `adaptive_style.summarize_edit_tendencies` | `stages/review/ReviewChecks.tsx` (#392) | `GET /api/review/dramas/{id}/tendencies` | — |
| R37 | Learn my style (LLM), show the profile, apply toggle, reset | 5004-5042 | `adaptive_style.analyze_edit_patterns`, `db.save_style_profile` | MISSING | no API | — |
| R38 | Translation versions list | 5044-5058 | `db.list_translation_versions` | `stages/review/RecordsPanel.tsx` | `GET /api/review/dramas/{id}/versions` | test_library_features TestTranslationVersions |
| R39 | Activate a version (with stale-id guard) | 5059-5076, `_restore_saved_lines` 629 | `core.restore_saved_lines`, `db.set_active_translation_version` | `stages/review/RecordsPanel.tsx` ("Use this version"; restructured lines refused, not restored whole) | `POST /api/review/dramas/{id}/versions/{version_id}/activate` (`translation_version_service`) | wt TestRestoreAndActivateKeepSpeakerCorrections |
| R40 | Delete a version (with confirm) | 5077-5086 | `db.delete_translation_version` | `stages/review/RecordsPanel.tsx`, PC-only (#387) | `POST /api/review/dramas/{id}/versions/{version_id}/delete` (#374) | wt TestFourMoreDestructiveActionsNeedConfirmation |
| R41 | Compare two versions | 5088-5106 | `db.get_translation_version` | `stages/review/ReviewChecks.tsx` (#392) | `GET /api/review/dramas/{id}/versions/compare` | — |
| R42 | Translation notes job (with bulk option) | 5108-5173 | `run_translation_notes_job` | `stages/review/ReviewJobsPanel.tsx` (bulk MISSING) | `POST /api/review-jobs/.../notes` | test_bulk_translate TestBulkTranslationNotes |
| R43 | Notes list with jump-to-line and delete | 5175-5188 | `db.delete_translation_note` | PARTIAL `stages/review/RecordsPanel.tsx` (list + delete; jump MISSING) | `GET /api/review/.../notes`, `DELETE /api/lines/.../notes/{id}` | — |
| R44 | Download notes as Markdown | 5190-5193 | `tguide.format_notes_as_markdown` | `stages/review/ReviewChecks.tsx` link (#392) | `GET /api/review/.../notes/markdown` | — |
| R45 | Add your own note (term, type, line number) | 5195-5208 | `db.save_translation_notes` | `stages/review/LineRow.tsx` "Add note" | `POST /api/lines/dramas/{id}/notes` | — |
| R46 | Merge short adjacent lines: preview, then apply with a stale-id guard and snapshot | 5211-5257 | `core.merge_adjacent_short_lines` | MISSING | PARTIAL: `POST /api/restructure/.../merge` merges explicit ids; no auto short-line preview | wt TestMergePreviewDoesNotMutateLiveLines, TestMergeAndRestoreStaleIdSetSafety |
| R47 | Re-segment long lines by meaning: rules or LLM preview, background Ollama job with cancel, confirm clearing translations, apply with guard | 5259-5429 | `resegment.resegment_lines`, `resegment_subprocess_worker` | PARTIAL `stages/review/StructureSection.tsx` (#377): preview, then typed-confirm run; LLM/cancel options not verified | `GET /api/restructure/.../resegment/preview`, `POST .../resegment` | wt TestResegmentGuardrail, TestResegmentationStaleSnapshotSafety, TestResegmentationRealMidRunStop |
| R48 | Version history (snapshots) list and Restore | 5431-5451 | `db.list_line_history`, `_restore_saved_lines` | `stages/review/RecordsPanel.tsx` list + typed-confirm Restore (#377) | `GET /api/review/.../history`, `POST /api/restructure/.../history/{id}/restore` | wt TestMergeAndRestoreStaleIdSetSafety; test_diagnostics_and_export TestLineHistory |
| R49 | Bulk (half-price) option for consistency, flag, emotion and notes jobs | 4585, 4666, 4866, 5124 | `_start_bulk_generic` 410 | MISSING | no API field on review jobs | test_bulk_translate TestBulkFlag/Consistency/Emotion/TranslationNotes |
| R50 | Engine and model used for Review AI jobs (shared with the Translate pickers) | used throughout | `get_engine` | DONE `stages/review/ReviewJobsPanel.tsx` (Check options: engine and model for consistency, emotion, notes, flag; fix-flagged keeps its own; unset fields fall back to the server default; paid engines still gated by engines.paid) | `ReviewJobStart.engine/model` | wt TestTranslationOnlyEngineGatesLlmOnlyButtons |

### 2.7 Dub tab (5455-5613)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| U01 | Narrate in translation or original language (novel narration), with a warning about untranslated lines | 5463-5488 | `db.update_drama(narration_language)` | `stages/DubStage.tsx` Narration language (warning UNK) | `POST /api/dub/.../run` (`narration_language`) | test_dub TestNarrateOriginalLanguage |
| U02 | Fallback TTS engine (edge-tts or offline Piper) | 5489-5496 | — | `stages/DubStage.tsx` Voice engine | run `tts_engine` | test_dub |
| U03 | Max speed-up / slow-down to fit timing | 5497-5513 | — | `stages/DubStage.tsx` | run fields | wt TestDubTimingAndRemovedCloneUI |
| U04 | Generate dub or narration track (background process job, voice maps, clone map) | 5514-5559 | `dub.build_track_subprocess_worker`, `fill_missing_voices`, `clone_map_from_characters` | `stages/DubStage.tsx` | `POST /api/dub/dramas/{id}/run` | test_dub TestFillMissingVoices, TestDubWorkerArgumentBinding |
| U05 | Dub job queued, running, cancel, cancelled or error | 5561-5585 | `background_jobs.request_cancel` | `stages/JobPanel.tsx` | `/api/jobs` | wt TestDubGenerationRealMidRunStop |
| U06 | On done: save clip names (and narration timings), status "dubbed", per-line errors, download the track | 5586-5607 | `db.save_lines(fields=...)` | `stages/DubStage.tsx` (server applies; download link) | `GET /api/dub/dramas/{id}/track` | test_dub |
| U07 | Dub pacing panel (fit, sped up, overflow) with a "needs attention only" filter | 724-751, 5609-5610 | `dub.load_pacing`, `pacing_for_line` | `stages/DubStage.tsx` "Pacing of the last run" | `GET /api/dub/dramas/{id}/pacing` | wt TestDubTimingAndRemovedCloneUI |

### 2.8 Export tab (5614-6016)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| E01 | Test-mode output warning | 5626-5630, 5842-5845, 5991-5994 | — | `stages/ExportStage.tsx` (readiness) | `GET /api/export/dramas/{id}/readiness` | wt TestTestModeExportWarning |
| E02 | Untranslated / no source text warnings | 5635-5646 | — | `stages/ExportStage.tsx` (readiness counts) | same | — |
| E03 | Include translation notes inline | 5648-5659 | `tguide.group_notes_by_line` | `stages/ExportSubtitles.tsx` | `GET .../subtitle?include_notes=` | test_export_formats |
| E04 | Custom base filename | 5661-5666 | `_sanitize_filename` 132 | MISSING (React names files `drama_{id}_{field}.{fmt}`) | no API (filename is client-side) | — |
| E05 | Overlap warning and "Flag overlapping lines" | 5677-5694 | `subtitle_formats.clamp_overlaps`, `overlap_note` | `stages/ExportFlags.tsx` | `POST /api/export/.../flag-overlaps` | test_export_formats TestOverlapClamp |
| E06 | Auto QC warning and "Flag these" | 5696-5716 | `auto_qc.find_issues`, `_run_auto_qc` | `stages/ExportFlags.tsx` | `POST .../flag-auto-qc` | test_auto_qc |
| E07 | Reading-speed (dense lines) warning and flag | 5718-5727 | `subtitle_formats.dense_lines`, `flag_dense_lines` | `stages/ExportFlags.tsx` | `POST .../flag-dense-lines` | test_export_formats TestReadingSpeed |
| E08 | Format: SRT, VTT or ASS | 5729-5733 | — | `stages/ExportSubtitles.tsx`, `stages/ExportAss.tsx` | `GET .../subtitle`, `POST .../ass` | test_export_formats |
| E09 | ASS: notes on their own line, and notes position | 5734-5750 | — | PARTIAL `stages/ExportAss.tsx` "Notes on own line"; notes position MISSING | `POST .../ass` (`notes_as_separate_line`, style `notes_alignment`) | test_export_formats TestAssNotesAsSeparateLine; test_media_preview TestSfxAndNotesPositions |
| E10 | Split long lines (max characters per line, English and source) | 5751-5763 | `subtitle_formats.wrap_lines` | `stages/ExportSubtitles.tsx` Advanced | `wrap_chars_en/source` | test_export_formats TestLongLines |
| E11 | Subtitle style: starting look, font (or custom), size, outline width, shadow, bold, italic, text and outline colour, position, SFX position, colour per speaker, live preview | 183-256, 5765-5774 | `subtitle_formats.ASS_PRESETS`, `style_preview_html` | PARTIAL `stages/ExportAss.tsx` (preset, font, colours, per-speaker colours). Size, outline width, shadow, bold, italic, position, SFX position and live preview MISSING | `POST .../ass` (all `AssStyleOverrides`), `GET /api/export/ass-style-options` | test_export_formats TestAss, TestAssShadow, TestPreview; test_gui_polish |
| E12 | Download English, Chinese or bilingual subtitles | 5793-5801 | `lines_to_srt/vtt/ass`, `lines_to_bilingual_srt` | `stages/ExportSubtitles.tsx` (one field at a time) | `GET .../subtitle?field=` | test_export_formats |
| E13 | Generate EPUB (novel narration) | 5803-5815 | `epub_io.export_epub` | `stages/ExportMedia.tsx` | `GET /api/export/dramas/{id}/epub` | — |
| E14 | Generate audiobook .m4b | 5817-5831 | `dub.export_narration_m4b` | `stages/ExportMedia.tsx` | `POST .../audiobook` + `/api/artifacts` | — |
| E15 | Burn subtitles into the episode (ASS, per-speaker colours) | 5840-5900 | `video_export.burn_ass` | `stages/ExportMedia.tsx` | `POST .../burned-video` + `/api/artifacts` | test_export_formats TestHardsub |
| E16 | Burn in from SRT with style settings (non-ASS path) | 5883-5890 | `video_export.burn_subtitles` | Covered by E15 (the ASS burn carries the same style) | — | test_export_formats TestHardsub |
| E17 | Soft subtitle track muxed into the video | 5892-5894 | `video_export.mux_soft_subtitles` | MISSING | no API | — |
| E18 | Choose which subtitles go on the video (English, bilingual, Chinese) and download the matching file | 5854-5872 | — | `stages/ExportMedia.tsx` (`field`) | `AssExportRequest.field` | wt TestSection10StandaloneSubtitleDownload |
| E19 | Export the video with the dub audio (replace, or mix the original quietly underneath) | 5902-5921 | `video_export.replace_audio_with_dub` | MISSING | no API | — |
| E20 | Vertical/shorts export (9:16 crop, clip range) | 5923-5981 | `video_export.render_vertical_clip` | **DROPPED** (section 10) | no API | wt TestVerticalShortsExport |
| E21 | Export this drama as a package (.zip) | 5984-6010 | `export_package.build_drama_export_package` | **DROPPED** (section 10) | no API | test_diagnostics_and_export TestExportPackage |
| E22 | Mark as exported | 6012-6014 | `db.update_drama(status="exported")` | MISSING | no API (status is not updatable) | — |

## 3. Library (`tabs/library_tab.py`, 550 lines)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| L01 | Dashboard: totals, lines translated, API calls, spend, cache-hit share, by status, by type | 26-42, `cache_hit_share` 13 | `db.get_library_stats`, `get_usage_summary` | PARTIAL `pages/Library.tsx` Stats (cache-hit share UNK) | `GET /api/library/stats` | test_library_features TestCacheHitShare |
| L02 | Continue reading (cover, progress, Resume jumps to the Reader page) | 48-75 | `db.list_continue_reading` | `pages/Library.tsx` "Continue reading" strip (title, progress, page, Resume opens `#/read/<id>`, which resumes where the reader left off, #388); profile scoping DROPPED | `GET /api/library/continue` | test_library_features TestResumeHandoff, TestProgressTracking |
| L03 | Recently active dramas | 77-82 | `db.list_dramas_recently_active` | `pages/Library.tsx` "Recently active" | `GET /api/library/recent` | — |
| L04 | Cost breakdown by drama (free engines shown as $0) | 84-102 | `db.get_usage_by_drama` | `pages/Library.tsx` "Cost by drama" | `GET /api/library/costs` | test_library_features TestCostDashboardShowsFreeEngineUsage |
| L05 | Series view: each series with 2+ dramas, types, shared characters and terms, Open | 105-145 | `db.list_series`, `list_dramas_by_series` | PARTIAL `pages/Library.tsx` "Series" (Open-to-Workspace UNK) | `GET /api/library/series` | test_library_features TestLibrarySeriesView, TestSeriesSharingIndicators |
| L06 | Search text across every drama's lines | 147-154 | `db.search_lines_globally` | `pages/Library.tsx` "Search all lines" | `GET /api/library/search` | test_db TestLibraryStatsAndSearch |
| L07 | All dramas filters: title/summary, studio, author, voice actor, status, language, type, quick filter (Favorite, On Hold, Plan to Translate), custom tags | 156-176 | `library_service.list_library_dramas` | `components/LibraryList.tsx` (search, status, quick filter; "More filters": studio, author, voice actor, language, type, custom tags) | `GET /api/library/dramas` (all filters) + `GET /api/library/filter-options` | test_library_features TestAnimeInLibraryTypeFilter, TestOrganizationalTags |
| L08 | Table with bilingual credits and row selection | 178-203 | `tguide.format_bilingual_credit` | `components/LibraryList.tsx` + row selection feeding `pages/libraryAdmin/SelectionBar.tsx` (#385) | same | — |
| L09 | Bulk: set status of selected | 205-214 | `db.update_drama` | `pages/libraryAdmin/SelectionBar.tsx` (#385) | `POST /api/library/admin/bulk/status` (#376) | — |
| L10 | Bulk: delete selected (confirm) | 215-220 | `db.delete_drama` | `pages/libraryAdmin/SelectionBar.tsx`, typed DELETE, PC-only (#385) | single `DELETE /api/dramas/{id}`; bulk `POST /api/library/admin/bulk/delete` (#376) | — |
| L11 | Bulk translate selected "aligned" dramas (one job, cancel, refresh, summary) | 222-305 | `run_bulk_series_translate_job` | `pages/libraryAdmin/SelectionBar.tsx` (#385) | `POST /api/library/admin/bulk/translate` (#376) | test_library_features TestBulkSeriesTranslate, TestBulkSeriesTranslateStatusUI, TestBulkSeriesTranslateRefreshesOpenWorkspaceDrama |
| L12 | Add/remove selected to a quick list | 253-263 | `db.set_custom_tag` | `pages/libraryAdmin/SelectionBar.tsx` (#385) | `POST /api/library/admin/bulk/tags` (#376) | test_library_features TestOrganizationalTags |
| L13 | Bulk export translated dramas as .zip (SRTs + dub track) | 307-333 | `lines_to_srt`, `clamp_overlaps` | `pages/libraryAdmin/SelectionBar.tsx` / `AdminSection.tsx` + download link, PC-only (#385) | `POST /api/library/admin/export` + `GET /api/library/admin/artifacts/{kind}` (#376) | test_library_features TestBulkExportClampsOverlappingCues |
| L14 | Storage: quality preset, scan, reclaimable by category and by drama, clean (confirm) | 340-376 | `storage.scan_library_storage`, `clean_drama_storage` | `pages/libraryAdmin/AdminSection.tsx` scan + typed CLEAN, PC-only (#385) | `GET /api/library/admin/storage`, `POST .../storage/clean` (#376) | test_library_features TestStorage |
| L15 | Reading history and Clear | 378-390 | `db.list_reading_history`, `clear_reading_history` | `pages/Library.tsx` "Reading history" + two-step Clear, PC-only (progress kept) | `GET /api/library/history`, `POST /api/library/history/clear` (`local_only`) | test_library_features TestProgressTracking |
| L16 | Backup: database-only snapshot plus download; full .zip backup without saved sign-ins | 392-449 | `db.snapshot_database`, `zipfile` | `pages/libraryAdmin/AdminSection.tsx` backup + download, PC-only (#385) | `POST /api/library/admin/backup` + `GET /api/library/admin/artifacts/{kind}` (#376) | test_db TestSnapshotDatabase; test_sources_auth_browser TestPersistentProfiles |
| L17 | Restore from a backup .zip (confirm) | 451-461 | `workspace_job_service.restore_library_backup` | `pages/libraryAdmin/AdminSection.tsx` typed RESTORE, PC-only (#385) | `POST /api/library/admin/restore` (#376) | test_library_features TestRestoreFromBackupValidatesBeforeDestroying |
| L18 | Presets: list, rename, delete (confirm) | 463-503 | `db.list_presets`, `rename_preset`, `delete_preset` | PARTIAL `pages/Library.tsx` "Presets" (list + PC-only delete, #385); rename MISSING | `GET /api/library/presets`, `POST .../rename`; delete: `POST /api/library/presets/{id}/delete` (#374) | test_library_features TestManagePresetsUI |
| L19 | Voice bank: list with audio preview, rename, delete (confirm) | 505-549 | `db.list_voice_bank_entries`, `rename_`, `delete_voice_bank_entry` | PARTIAL `pages/Library.tsx` "Voice bank" (list + PC-only delete, #385); rename and audio preview MISSING | `GET /api/library/voice-bank`, `POST .../rename`; delete: `POST /api/library/voice-bank/{id}/delete` (#374); audio: no API | test_library_features TestVoiceBankDeleteConfirm |

## 4. Settings (`app.py:50-53` → `tabs/settings_tab.py`, 485 lines)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| G01 | Load keys from `.env`/environment on every render (BOM-safe) | 27-76 | `ENV_NAMES` | Server `settings_service.resolve_key` | `GET /api/settings` (`engine_keys`) | test_settings_tab TestLoadsFromEnvFile, TestAlreadySetValuesWin, TestRepeatedCallsPickUpLateEdits; test_settings_service |
| G02 | Household profile picker: add, rename, delete (confirm) | 138-190 | `db.*_profile` | **DROPPED** (section 10) | no API | test_library_features TestProfiles; test_db TestStep26eProfilesMigration |
| G03 | Dark mode | 200-203 | `ui_theme.inject_css` | `src/theme.ts` + `pages/settings/PreferencesSections.tsx` (Appearance: light/dark/system, per browser; the Reader's Auto theme follows it) | no API (client-side) | test_dark_mode_step68, test_dark_mode_consistency |
| G04 | Reading experience: spoiler-free, text size, spacing, width, theme, font | 212-235 | — | MISSING (Reader) | no API | test_dark_mode_step68 TestReaderFollowsAppDarkMode |
| G05 | OCR default backend, prefer PaddleOCR-VL for Japanese, Tesseract path | 237-262 | `ocr.OCR_BACKEND_OPTIONS` | `pages/settings/PreferencesSections.tsx` (OCR); the saved Tesseract path applies to transcription (hardsub OCR) and chapter OCR when none is sent. The default backend and PaddleOCR-VL preference are saved and exposed as `settings_service.resolve_ocr_backend`; no API OCR path picks a backend by default yet (chapter OCR keeps its own picker, as Streamlit; Scanlate has no API) | `POST /api/settings` (`local_only`) | test_settings_tab TestOcrDefaultBackendSetting |
| G06 | API keys and endpoints (Claude, DeepSeek, Gemini, DeepL, Google, Ollama URL, LibreTranslate URL, GPT-SoVITS URL, Groq, HF token), "Save to .env" | 124-135, 292-305, `save_key_to_env` 87 | `synced_api_key_input`, `save_key_to_env` | MISSING (React only shows "configured"); endpoint URLs (Ollama, LibreTranslate, GPT-SoVITS): `pages/settings/PreferencesSections.tsx` (Server addresses) | `POST /api/settings/keys/{engine}`, `/clear` (keys only; no URL endpoints); `POST /api/settings/endpoints/{name}` and `/clear` (URLs, key-write gate) | test_settings_tab TestSaveKeyToEnv, TestApiKeySaveToEnvButton; test_settings_writes |
| G07 | "My Gemini key is free-tier" | 306-322 | — | `pages/Settings.tsx`; the saved flag is now applied server-side to every run that omits it (translate run and estimate, review jobs, line AI, `/api/translate`) | `POST /api/settings` | test_settings_tab TestGeminiFreeTierCheckbox |
| G08 | Defaults for new dramas: engine, English variant, style note, episode-summary engine | 264-290 | — | `pages/settings/PreferencesSections.tsx` (Defaults for new dramas); new dramas are stamped with the default engine, a drama with none uses it, the Translate form starts from the saved locale and style note, the episode-summary engine is used by single and bulk runs and the CLI | `POST /api/settings`; `GET /api/translate-run/...` `default_locale`/`default_style_note` | — |
| G09 | Offline Whisper model folder | 327-334 | — | `pages/settings/PreferencesSections.tsx` (Offline and performance); used by the transcribe job and `cli.py` | `POST /api/settings` | test_local_model_defaults |
| G10 | Use GPU | 336-342 | — | `pages/Settings.tsx` | `POST /api/settings` | — |
| G11 | Limit to one GPU job | 349-359 | `background_jobs.set_gpu_limit_enabled` | `pages/Settings.tsx` | same | test_settings_tab TestLimitOneGpuJobToggle |
| G12 | Desktop notification when a job finishes | 361-371 | `background_jobs.set_notify_on_completion` | `pages/Settings.tsx` | same | test_settings_tab TestNotifyOnJobDoneToggle |
| G13 | Ollama context window override | 373-381 | — | `pages/settings/PreferencesSections.tsx` (Offline and performance); passed to single and bulk translate runs and the CLI | `POST /api/settings` | — |
| G14 | Monthly spending cap | 383-395 | — | `pages/settings/PreferencesSections.tsx` (Spending); saved value wins over `BAIHE_MONTHLY_CAP_USD` (blank = .env); used by translate runs, library bulk translate and the CLI | `POST /api/settings` | wt TestSpendingCapUI |
| G15 | yt-dlp cookies from a browser or a cookies.txt | 397-413 | `video_download.COOKIE_BROWSERS` | `pages/settings/PreferencesSections.tsx` (Downloads); used by the PC-only URL download and by Live capture started at the PC (never a remote start). Path only; file contents never read by the API | `POST /api/settings` | test_settings_tab TestCookieBasedLoginSettings; test_live_tab |
| G16 | Browser extension: run the local endpoint, engine for extension pages, token to paste | 418-485 | `page_server.ensure_server_started`, `set_translation_config`, `load_or_create_token` | DONE `pages/settings/ExtensionSection.tsx` (on/off, status, two-step Show token kept in component state and cleared after 120 s, engine and model for extension pages saved as an app setting and hooked into page_server at API startup; PC only) | `GET /api/extension/status`, `POST /api/extension/enabled`, `POST /api/extension/token` (local_only, #372); `GET /api/extension/engine` (admin.settings), `POST /api/extension/engine` (local_only) | test_page_server_settings |

## 5. Diagnostics (`tabs/diagnostics_tab.py`, 987 lines)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| Q01 | Run diagnostics: Python, ffmpeg, library writable, JS runtime | 247-294 | `diagnostics.run_full_diagnostics` | `pages/diagnostics/SetupSection.tsx` (core checks always at the top: Python, ffmpeg with libass, JS runtime; then the Setup fold: GPU, app files, library folder; "Problem: …" rows open the section; Check again) | `GET /api/diagnostics/setup-checks` + `GET /api/diagnostics` (GPU) | test_diagnostics_regrouping TestCheckMySetupWiredDirectlyUnderItsHeader |
| Q02 | Install Deno | 275-291 | `diagnostics.stream_deno_install` | MISSING | no API | test_install_buttons TestDenoInstallUI |
| Q03 | Project files check | 296-311 | `check_file_completeness` | `pages/diagnostics/SetupSection.tsx` (App files row) | `GET /api/diagnostics/setup-checks` | test_diagnostics_and_export TestDiagnostics |
| Q04 | Dependencies by tier, installed or missing | 313-373 | same | `pages/diagnostics/PackagesSection.tsx` | same | test_diagnostics_and_export |
| Q05 | Check for dependency updates (PyPI) | 322-328 | `check_dependency_versions` | MISSING | no API | test_install_buttons TestDiagnosticsTabCheckForUpdatesButton |
| Q06 | Install, upgrade or "Test first" per package (with confirms and known-limitation notes) | 375-429 | `stream_dependency_install`, `upgrade_pip_args`, `check_upgrade_candidate` | PARTIAL `pages/diagnostics/PackagesSection.tsx`: two-step Install…/Upgrade… per installable package (PC only; synchronous, no progress or cancel; Output tail shown). "Test first", the update check and known-limitation notes MISSING (no API); Upgrade… is offered for every installed installable package (no version/latest in the API) | `POST /api/diagnostics/dependencies/{package}/install` and `.../upgrade` (local_only, #372) | test_install_buttons (several UI classes) |
| Q07 | Bulk install a whole requirements tier | 436-451 | `_run_bulk_install_stream` | MISSING | no API | test_install_buttons TestBulkTierInstallUI |
| Q08 | Running jobs, auto-refreshing, with Cancel; nudges the GPU queue | 203-235, 453-454 | `background_jobs.list_running_jobs`, `recheck_gpu_queue` | `pages/Diagnostics.tsx` Jobs (cards on phones; shown open while a job runs or failed; GPU-queue nudge UNK) | `GET /api/jobs`, `POST .../cancel` | test_diagnostics_and_export TestRunningJobsPanelAutoRefresh, TestDescribeJob |
| Q09 | Finished job history with duration and error | 456-476 | `debug_view.explain_job` | `pages/diagnostics/JobHistorySection.tsx` (label · status · duration · GPU; the error for a failed job, no stale progress text for a finished one) | `GET /api/diagnostics/job-history` | — |
| Q10 | Saved bug bundles: replay, delete (confirm) | 478-511 | `debug_view.replay_bug_bundle`, `db.delete_bug_report` | PARTIAL `pages/diagnostics/BugBundlesSection.tsx` (list with saved/replayed output, two-step PC-only delete); replay MISSING | `GET /api/diagnostics/bug-bundles`; delete: `POST /api/diagnostics/bug-bundles/{id}/delete` (#374); replay: no API | test_diagnostics_and_export TestBugBundleDeleteNeedsConfirmation |
| Q11 | Model and engine versions, with install buttons and help | 513-539 | `get_model_engine_versions` | `pages/diagnostics/SetupSection.tsx` (versions) + `PackagesSection.tsx` "Model engines not installed" (Install… by package) | `GET /api/diagnostics`, `POST .../dependencies/{package}/install` | test_diagnostics_regrouping TestModelEngineVersionsTable, TestModelEngineVersionsInstallAndHelp |
| Q12 | GPU status, plus "Install GPU PyTorch" when the build is CPU-only | 541-565 | `get_gpu_status`, `gpu_torch_mismatch`, `stream_gpu_torch_reinstall` | PARTIAL (status shown; reinstall MISSING) | `GET /api/diagnostics` | test_diagnostics_regrouping TestGpuStatusDisplay; test_install_buttons TestDiagnosticsTabGpuTorchButtonVisibility |
| Q13 | Source access "Test Now" per adapter | 567-600 | `sources.ladder.test_tier` | MISSING | no API | test_diagnostics_source_access |
| Q14 | Downloaded model cache and Piper voices, with delete | 603-639 | `scan_hf_cache`, `delete_hf_cache_revision`, `scan_piper_voices` | `pages/diagnostics/ModelCacheSection.tsx` (list; two-step delete per model/voice, PC-only, waits for running jobs) | `GET /api/diagnostics/model-cache` (#372); `POST /api/diagnostics/model-cache/hf/{revision}/delete`, `.../piper/{voice}/delete` (`local_only`) | test_diagnostics_and_export TestHfCacheScanAndDelete, TestPiperVoicesPanelUI |
| Q15 | pyannote gated-model access check | 641-661 | `check_pyannote_gated_access` | `pages/diagnostics/PyannoteSection.tsx` (Speaker detection; "Check access online" on request, Accept terms link) | `GET /api/diagnostics/pyannote` (#372) | test_diagnostics_and_export TestPyannoteGatedAccessCheck |
| Q16 | App Assistant chat and developer report | 663-702 | `app_help.ask_about_app` (grounded in `tabs/*_tab.py` source) | MISSING (a Diagnostics extra, undecided; `app_help.py` stops working once `tabs/` is gone) | no API | test_app_help |
| Q17 | Copyable redacted support report | 704-714 | `format_diagnostics_report`, `redact_for_support` | `pages/diagnostics/SupportReportSection.tsx` (Build report, Copy with a select-to-copy fallback) | `GET /api/diagnostics/support-report` (#372) | test_diagnostics_and_export TestDiagnosticsTabSupportReport |
| Q18 | Log tail with keyword filter | 716-729 | `applog.tail`, `filter_lines` | `pages/diagnostics/LogSection.tsx` (50/100/200 lines, debounced keyword filter, Copy) | `GET /api/diagnostics/log` | test_diagnostics_regrouping TestLogKeywordFilter |
| Q19 | Accuracy benchmark: register cases, run all, compare engines, regression warnings, run history, remove | 731-926 | `benchmark.run_suite`, `compare_configs`, `db.*benchmark*` | MISSING (a Diagnostics extra, undecided) | no API | test_benchmark |
| Q20 | Reset the entire library (checkbox, typed RESET, stops jobs first) | 928-987 | `db.reset_library`, `background_jobs.clear_all_jobs` | `pages/diagnostics/DangerZone.tsx` (PC only, typed RESET, refuses while jobs run instead of stopping them; self-contained so it can be dropped if reset becomes CLI-only, plan section 3 item 2) | `POST /api/diagnostics/reset-library` (local_only, confirm + RESET, #372) | test_db TestFullLibraryReset |

## 6. Translate (`tabs/translate_tab.py`, 193 lines)

| ID | Feature | Source | Calls | React | API | Tests |
|---|---|---|---|---|---|---|
| N01 | Direction and language | 24-38 | — | `pages/Translate.tsx` | `POST /api/translate` | test_translate_tab |
| N02 | Engine and model pickers | 43-77 | `translate_engines.ENGINES` | `pages/Translate.tsx` | `GET /api/translate/engines` | test_translate_tab |
| N03 | API key field | 79-92 | `synced_api_key_input` | MISSING (see G06) | `POST /api/settings/keys/{engine}` | — |
| N04 | Direction support warning | 94-99 | `standalone_direction_support` | UNK (server raises) | `POST /api/translate` | test_standalone_translate |
| N05 | Upload a .txt/.md/.epub to translate | 103-112 | `core.load_novel_text_for_context` | MISSING | no API | — |
| N06 | Translate and Clear | 114-150 | `translate_engines.standalone_translate`, `db.save_translate_history` | `pages/Translate.tsx` (free tier from the saved setting when omitted; Ollama always uses the configured URL, a client `base_url` is no longer accepted) | `POST /api/translate` | test_translate_tab |
| N07 | Result side by side and "Download .txt" | 152-168 | — | PARTIAL (result shown; download MISSING) | — | — |
| N08 | History list and Clear (confirm) | 170-193 | `db.list_translate_history`, `clear_translate_history` | PARTIAL (list; clear MISSING) | `GET /api/translate/history`, `DELETE /api/translate/history` | test_db TestTranslateHistory |

## 7. Read & Watch (`tabs/reader_tab.py`, 424 lines)

React: `pages/Reader.tsx` + `pages/reader/` at `#/read/<id>` (#388), calling every Reader route except `GET .../captions/{track}/readout`. API: `GET /api/reader/dramas/{id}/page` plus the route batch 2B Reader routes (#370); the per-row API column below predates #370 and still says "no API" for rows #370 covers (RD03, RD06, RD10-RD14). Not verified row by row in React.

| ID | Feature | Source | Calls | API | Tests |
|---|---|---|---|---|---|
| RD01 | Drama picker, honouring a Resume from Library | 35-61 | `db.list_dramas` | — | test_library_features TestResumeHandoff |
| RD02 | Watch/listen: original video with CC tracks, audio with an unsynced caption list, AI dub and narration players | 66-120, `caption_tracks` 10 | `subtitle_formats.lines_to_vtt` | no API (Range endpoint needed) | test_reader_tab TestCaptionTracks |
| RD03 | Length, progress and line metrics; progress saved per page | 122-145 | `story_context.estimate_*`, `db.save_progress` | no API | test_library_features TestTimeEstimates, TestProgressTracking |
| RD04 | Paged reader with pinyin/furigana, click-to-define, theme and font prefs | 134-140, 218-229 | `reader.build_reader_html`, `segment.segment_and_annotate`, `dictionary.build_word_definitions` | `GET /api/reader/dramas/{id}/page` | test_reader_service |
| RD05 | Load definitions (saved to vocab), API key | 147-176 | `db.save_vocab_lookup` | partly in the page endpoint | test_db TestVocabLookups |
| RD06 | Queue words for a rich Anki card | 180-201 | `db.set_vocab_export_rich` | no API | — |
| RD07 | Click-to-seek audio for the page | 152-154, 203-209 | `reader.build_page_audio_data_uri` | no API | test_emotion_manhua_ui TestReaderFollowAlong |
| RD08 | Spoiler-free scoping | 211-216 | — | no API | — |
| RD09 | Series glossary view | 231-242 | `db.list_glossary_terms` | `GET /api/glossary/dramas/{id}/terms` | — |
| RD10 | Story tools: who is this character, explain an idiom, recap so far, relationship map (Mermaid) | 245-296 | `story_context.*` | no API | test_library_features TestRelationshipMap; test_reader_tab TestStoryPanelAndCollapsibleSections |
| RD11 | Universe wiki: update from what's read, clear, filter, download Markdown | 298-346 | `universe_wiki.*`, `db.*wiki*` | no API | — |
| RD12 | Ask about this drama (Q&A chat) | 347-369 | `qa.ask_about_drama` | no API | — |
| RD13 | My notes (private per profile) | 372-384 | `db.save_personal_notes` | no API (profile scoping DROPPED) | — |
| RD14 | Vocabulary export: CSV, Anki .apkg, rich sentence+audio .apkg | 387-424 | `vocab_export.*` | no API | — |

## 8. Sources (`tabs/sources_tab.py`, 990 lines)

The API covers the registry and settings (56a/56b), search/series jobs (S-3, #372), import (S-4/S-5, #411), and, on branch `react-sources-remaining`, check now and the tracked-series drama link (S-7), sign-in (S-6), per-tier tests and the proxy (PC only). React: `pages/Sources.tsx` + `pages/sources/` (`#/sources`, #401, #421, `react-sources-remaining`), marked "React:" in the API column (re-verified 2026-09-29). Still without React or API: the preflight (SO02), pasting page source after a verification page (SO03), comic import from a link (SO06, open question Q4), identify media (SO08), the AI fallback (SO09), Review extraction (SO10, prune candidate) and the pasted-URL diagnostics list (SO16).

| ID | Feature | Source | Calls | API | Tests |
|---|---|---|---|---|---|
| SO01 | Paste any URL: preview (type, platform, chapter, language, count) | 64-80, 129-140 | `front_door.preview` | `POST /api/sources/url/preview` + job result (S-5). React: REPLACED (UrlBox, #421) | test_sources_tab |
| SO02 | "Will this site work?" preflight | 82-99 | `preflight.preflight` | no API | test_sources_preflight |
| SO03 | Verification hand-off: open in browser, retry, cancel, continue from pasted page source | 42-61, 101-128 | `front_door.classify_html` | Handoff in the preview/import errors (409 `handoff`, `open_url`). React: PARTIAL (open in your browser, try again; no paste-page-source) | test_sources_tab |
| SO04 | Open the series browser from a URL | 143-146 | — | From the preview result (source, series id). React: REPLACED (Open series, the pasted chapter ticked; #421) | — |
| SO05 | Import video into a drama (audio only, overwrite confirm, cookies) | 147-178 | `front_door.import_video` | `POST /api/media/dramas/{id}/download-url` (local_only; generic yt-dlp even for Bilibili; no cookies). React: REPLACED for the PC (UrlBox video, #421; no cookies) | test_sources_tab |
| SO06 | Import comic pages into Scanlate (with skipped-image list) | 179-206 | `adaptive.import_comic`, `pipeline.add_page_images` | no API (S-4) | test_adaptive_extraction TestSourcesTabReview |
| SO07 | Import novel text (append, download .txt) | 207-235 | `adaptive.import_novel`, `pipeline.save_novel_text` | `POST /api/sources/url/import` (append; no LLM fallback, no review step: needs-review writes nothing). React: REPLACED (UrlBox novel import, #421; no .txt download) | same |
| SO08 | Identify media on an unknown page and pick a resource | 545-571 | `adaptive.identify_media` | no API | — |
| SO09 | AI-assisted fallback engine (optional) | 334-359 | `translate_engines.get_engine` | no API | test_adaptive_extraction TestLlmOnlyAsFallback |
| SO10 | Review extraction: confidence, pick containers, title, next/prev links, re-run, save profile, approve, import; comic roles and order | 362-543 | `ai_extract.*`, `profiles.*` | no API (plan section 3 item 8 prune candidate) | test_adaptive_extraction |
| SO11 | Sign in through a real browser window, or forget the sign-in | 270-320 | `auth_browser.manual_login`, `forget` | `POST /api/sources/{name}/signin/open` (job, window on the PC), `POST .../signin/forget` (confirm); both local_only, result PC only (S-6). React: REPLACED (Source settings > Details > Sign in) | test_sources_auth_browser, test_api_sources_local |
| SO12 | Search every enabled source, clear results, open a result | 574-603 | `registry.multi_search` | `POST /api/sources/search` + job result (S-3). React: REPLACED (search, Search in, cancel, clear, open; no cover images) | test_sources_tab |
| SO13 | Series browser: chapter list, reload, tick chapters, import into a drama, track for new chapters | 606-684 | `pipeline.start_import`, `store.track_series` | `POST /api/sources/{name}/series` + job result; `POST /api/sources/{name}/import` (chapter ids, per-drama job); `POST /api/sources/tracked` (track needs the loaded series result; untrack). React: REPLACED (info, chapter list, reload, ticks, import, track, stop tracking; #421) | test_sources_tab |
| SO14 | Source access status: live job metrics, cancel, results per chapter, dismiss | 687-734 | `background_jobs.list_running_jobs` | `/api/jobs`. React: PARTIAL (per-chapter outcomes and cancel for an import in the series panel, #421; no all-jobs panel here) | test_sources_tab |
| SO15 | New chapters: check now, open, dismiss; tracked series with "stop tracking" | 737-765 | `chapter_check.start_check_now`, `store.dismiss_notification`, `untrack_series` | `GET /api/sources/notifications`, `POST .../dismiss`, `GET/POST /api/sources/tracked`, `POST /api/sources/check-now` (job `sources_chapter_check`, S-7), `POST /api/sources/tracked/drama` (auto-import target). React: REPLACED (check now with result line, open, dismiss, stop tracking, auto-import drama) | test_sources_tab, test_api_sources_local |
| SO16 | Pasted-URL diagnostics and site profile rollback | 768-796 | `adaptive.recent_extractions`, `profiles.rollback` | `GET /api/sources/profiles`, `POST .../rollback`. React: PARTIAL (profile versions + Make active; no pasted-URL diagnostics) | test_adaptive_extraction TestDiagnostics |
| SO17 | Per-source enable, adult toggle, health, "try again now", test tiers, sign in, attempt log | 799-893 | `registry.set_enabled`, `store.set_adult_enabled`, `health.reset`, `ladder.test_tier` | `POST /api/sources/{name}/enabled|adult|health/reset`, `GET .../attempts`, `POST /api/sources/{name}/tier-test` (job, local_only, result PC only). React: REPLACED (on, adult, health text, try again now, details, attempts, Test now per tier, sign in) | test_sources_tab, test_api_sources_local |
| SO18 | Source settings: pacing, concurrency, retries, session breaks, cache mode, check interval, auto-import, demo source, diagnostics mode, proxy; clear cache | 896-967 | `store.set_setting`, `cache.RawCache` | `GET/POST /api/sources/settings`, `POST /api/sources/cache/clear`, `POST /api/sources/settings/proxy` (local_only, write-only). React: REPLACED (save sends changed keys only; the proxy is write-only, shown as set/none; PC only: LAN editing waits for the network-zones slice) | test_sources_tab, test_api_sources_local |
| SO19 | Starts the chapter-check scheduler | 975 | `chapter_check.ensure_scheduler_started` | Done in the API startup hook (`api/background.py`, #372) | — |

## 9. Discover (`tabs/discover_tab.py`, 394 lines)

React: the Discover page `#/discover` (branch `react-discover-page`) covers DI01-DI10 over the catalog (D-0/D-1) and D-2 helper routes (#372); DI08 opens a new tab instead of an in-app frame, and DI07 has no pasted-text fallback (no API).

| ID | Feature | Source | Calls | API | Tests |
|---|---|---|---|---|---|
| DI01 | Shared engine and API key for every AI action on the page | 36-56 | `translate_engines.get_engine` | no API (engine name per request; keys resolved on the PC). React: DONE (one picker, configured engines the routes accept) | test_discover_tab TestKeyGatingIsEngineAware |
| DI02 | Load starter titles | 60-70 | `title_library.seed_known_titles` | `POST /api/discover/titles/seed`. React: DONE (shown when the catalogue is empty) | test_discover_tab |
| DI03 | Search the saved catalog, import a title into the Library, remove it | 72-113 | `db.list_known_titles`, `db.create_drama`, `delete_known_title` | `GET /api/discover/titles`, `POST .../import-to-library`, `POST .../delete`. React: DONE (search, language/format filters, details, add to Library with the 409 "already in your Library" link, PC-only remove) | test_discover_tab TestDiscoverConsolidation |
| DI04 | Find a title on official platforms (query translated to Chinese, cached; search links) | 115-158 | `title_library.translate_query_to_zh`, `known_sites.build_search_links` | `GET /api/discover/search-links`, `POST /api/discover/translate-query` (#372). React: DONE (translation on Find, cached per query and engine; the JJWXC tag link is not ported) | test_discover_tab TestFindATitleTranslationCaching |
| DI05 | Site navigation helper (translate a page and give steps) | 160-221 | `navigator` | `POST /api/discover/navigation-help` + `GET .../result` (#372). React: DONE (steps as plain text, translated labels; known-platforms list under Find) | test_discover_tab TestSiteNavigationHelperButton |
| DI06 | Search baihehub.com | 223-242 | — | `POST /api/discover/baihehub-search` (#372). React: DONE (browser fallback link) | test_discover_tab TestBaihehubSearch |
| DI07 | Bulk import from listing pages: URL pattern generator, extract, manual paste, review, add | 244-334 | `title_library.*` | `POST /api/discover/bulk-extract` + `GET .../result`, `POST /api/discover/bulk-commit` (#372). React: PARTIAL (pattern generator, extract job, review, add with dedup; no pasted-text extraction: no API) | test_discover_tab TestBulkImportUrlPatternGenerator |
| DI08 | Browse a site in-app (iframe) | 336-355 | `st.iframe` | no API. React: CHANGED (opens the site in a new tab; no in-app frame or embeddability check) | test_dark_mode_step68 |
| DI09 | Import a title from a URL into the catalog | 357-376 | `title_library.import_title_from_url`, `db.create_known_title` | `POST /api/discover/import-suggestion` (#372) + `POST /api/discover/titles`. React: DONE (the suggestion fills the add form; nothing is saved until Add) | test_discover_tab |
| DI10 | Add a title manually | 378-394 | `db.create_known_title` | `POST /api/discover/titles`. React: DONE | test_discover_tab |

## 10. Live (`tabs/live_tab.py`, 149 lines)

Kept and ported (plan section 8, M6). React page `#/live` (`frontend/src/pages/Live.tsx`, branch `react-live-page`) over the L-1 API (polling, #372). Deleting `tabs/live_tab.py` still waits on the user's real-stream check (plan section 8).

| ID | Feature | Source | Calls | Tests | React |
|---|---|---|---|---|---|
| LV01 | Stream URL, language, Whisper size, chunk length and overlap | 41-66 | — | test_live_tab TestOverlapSettingReachesTheStartButton | DONE: link, Language; Whisper, Chunk (s), Overlap (s) under Advanced (remembered per browser). e2e live.spec.ts |
| LV02 | Engine and API key | 68-85 | `translate_engines.get_engine` | — | DONE: Engine picker lists engines with a key; keys are resolved server-side (no key field; add keys in Settings) |
| LV03 | Start the live capture job (cookies from Settings) | 87-107 | `live_translate.run_live_job` | test_live_tab TestCookieSettingsReachTheStartButton | DONE: Start (POST /api/live/sessions, `media.import_url`), plus Stop after (min) and Use GPU. Cookies do not travel over the API (decision); a signed-in-only stream won't resolve |
| LV04 | Queued: cancel, falling back to a real stop | 108-116 | `background_jobs.cancel_queued`, `live_translate.bump_generation` | test_live_tab TestQueuedJobCancelButton | DONE: Cancel while queued (the stop route does cancel_queued or a real stop) |
| LV05 | Stop (discards chunks still in flight) | 117-127 | same | — | DONE: Stop (POST .../stop, `jobs.cancel`) |
| LV06 | Feed of the latest 50 cues, refresh, error details | 129-149 | — | — | DONE: newest 50 lines, polled every 2 s (no Refresh button); error text is the service's cleaned message (no traceback over the API). Reopening the page shows the running or latest session |

Port fixes from plan section 8 are in `services/live_service.py` (`use_gpu`, a per-session temp dir, `max_minutes`); URL expiry still needs a restart to re-resolve.

## 11. Scanlate (`tabs/scanlate_tab.py`, 639 lines): DEFERRED

These are held until after the removal (plan section 8). `scanlate.py` and `page_server.py:268-363` stay. They are listed so the later React canvas port knows what existed. Spec: `docs/specs/scanlate-api-spec.md`.

| ID | Feature | Source | Calls | Tests |
|---|---|---|---|---|
| SC01 | Drama picker; upload pages or PDFs, optionally slicing webtoon strips | 17-47, `add_uploaded_pages` 598 | `scanlate.pdf_to_page_images`, `slice_webtoon_to_files`, `db.create_page` | test_emotion_manhua_ui TestWebtoonUpload |
| SC02 | Page picker, engine, OCR backend (default from Settings), ML detector and inpainting choice | 49-110 | `scanlate.bubble_ml_weights_cached` | test_scanlate_tab TestOcrBackendDefaultFromSettings, TestAutoResolvesToCvWarning |
| SC03 | Detect bubbles, clean and translate one page | 146-170 | `scanlate.detect_and_ocr_page`, `translate_page_bubbles` | test_scanlate_tab TestPerPageContextDoesNotLeakAcrossPages |
| SC04 | Batch: detect, OCR and translate every page (skip existing) | 172-224 | `scanlate.batch_process_pages` | — |
| SC05 | Review and adjust bubbles (source, translation, SFX), translate from source | 226-318 | `db.save_bubbles` | — |
| SC06 | Custom fonts per category | 320-345 | — | test_scanlate_tab TestFontUploadDoesNotLeakAcrossDramas |
| SC07 | Manual erase/heal brush (canvas) | 347-419 | `scanlate.inpaint_mask_region` | — |
| SC08 | Save bubble edits, render the typeset page and download, export font styles as JSON | 421-465 | `scanlate.process_page`, `export_font_style_report` | — |
| SC09 | Add a bubble manually (with OCR of a region) | 467-499 | `scanlate.ocr_box_region` | test_scanlate_tab TestAddBubbleManually |
| SC10 | Bulk render all pages as ZIP or PDF | 501-546 | `scanlate.bulk_render_pages`, `pages_to_pdf` | — |
| SC11 | Bulk find and replace over bubble text | 548-595 | `scanlate.bulk_find_replace_preview`, `db.update_bubble_text` | test_scanlate_tab TestBulkFindAndReplace |

## 12. App shell (`app.py`, 118 lines)

| ID | Feature | Source | React | Notes |
|---|---|---|---|---|
| A01 | Page title, icon, the "Baihe Studio" heading | 44-57 | `App.tsx` | tested by test_app TestAppIcon |
| A02 | Per-tab crash containment with a traceback | 59-82 | N/A (React error boundaries and `components/ErrorBanner.tsx`) | tested by test_app (AppTest smoke) |
| A03 | Nine top-level tabs | 90-118 | `router.ts` (library, drama, settings, diagnostics, translate; sources on branch `react-sources-page`) | Reader, Scanlate, Discover and Live have no route |

## 13. Counts

Note (docs sync after #402, base f48ec58): these counts and the section 14 backlog are as of BASE and were not recomputed; rows updated since (Review #377/#387/#392/#398/#399, Library #385, Workspace #387, Reader #388, Sources #401, Diagnostics/Settings #402) are changed in the tables above.

These are counts of feature rows, computed from the tables above. Each row is counted once, by the first word of its React column. "Replaced" includes the rows where the behaviour moved server-side (T17, X19, X25).

| Area | Rows | Replaced | Partial | Missing | Dropped | Deferred | N/A | UNK |
|---|---|---|---|---|---|---|---|---|
| Workspace preamble (P) | 17 | 3 | 4 | 9 | 0 | 0 | 1 | 0 |
| Source (S) | 14 | 7 | 2 | 5 | 0 | 0 | 0 | 0 |
| Transcript (T) | 18 | 11 | 2 | 5 | 0 | 0 | 0 | 0 |
| Diarize (D) | 6 | 4 | 1 | 1 | 0 | 0 | 0 | 0 |
| Translate (X) | 35 | 18 | 1 | 15 | 0 | 0 | 0 | 1 |
| Characters (C) | 14 | 0 | 2 | 12 | 0 | 0 | 0 | 0 |
| Review (R) | 50 | 14 | 7 | 28 | 0 | 0 | 1 | 0 |
| Dub (U) | 7 | 7 | 0 | 0 | 0 | 0 | 0 | 0 |
| Export (E) | 22 | 14 | 2 | 4 | 2 | 0 | 0 | 0 |
| **Workspace total** | **183** | **78** | **21** | **79** | **2** | **0** | **2** | **1** |
| Library (L) | 19 | 3 | 7 | 9 | 0 | 0 | 0 | 0 |
| Settings (G) | 16 | 5 | 0 | 10 | 1 | 0 | 0 | 0 |
| Diagnostics (Q) | 20 | 3 | 5 | 12 | 0 | 0 | 0 | 0 |
| Translate page (N) | 8 | 3 | 2 | 2 | 0 | 0 | 0 | 1 |
| Reader (RD) | 14 | 0 | 0 | 14 | 0 | 0 | 0 | 0 |
| Sources (SO) | 19 | 1 | 5 | 13 | 0 | 0 | 0 | 0 |
| Discover (DI) | 10 | 0 | 0 | 10 | 0 | 0 | 0 | 0 |
| Live (LV) | 6 | 0 | 0 | 6 | 0 | 0 | 0 | 0 |
| Scanlate (SC) | 11 | 0 | 0 | 0 | 0 | 11 | 0 | 0 |
| App shell (A) | 3 | 2 | 0 | 0 | 0 | 0 | 1 | 0 |
| **All** | **309** | **94** | **35** | **161** | **3** | **11** | **3** | **2** |

Dropped items that sit inside other rows are not counted separately: `music`/`other` hidden from the picker (P06), and per-profile scoping of personal notes, continue reading and reader notes (P12, L02, RD13).

## 14. React backlog (MISSING and PARTIAL gaps), ranked by use

Size: **S** is under half a session, **M** is about one session, **L** is several sessions. "API" says whether the endpoint already exists (**yes**), exists in part (**part**), or has to be built (**no**). The rank is my judgement of how often the feature is used in the main pipeline (create → source → transcribe → translate → review → export), then how bad losing it is.

### Top 15 overall

| # | Item (IDs) | Screen | Size | API |
|---|---|---|---|---|
| 1 | API key and endpoint entry in Settings (G06, X20, T07, D01, N03). Without it, paid engines only work after editing `.env` by hand. Endpoint URLs (Ollama, LibreTranslate, GPT-SoVITS) have no API | Settings | S (keys) / M (URLs) | part |
| 2 | Edit-metadata form, including the series picker, custom tags and episode number/summary (P10, P11, X09) | Workspace preamble | S | yes |
| 3 | Content-mode and transcript-mode pickers (S03, S08) | Source | S | yes |
| 4 | ~~Review media player: Range endpoint, seek, jump to time, play segment, row ▶, per-line clip (R02, R13, R22)~~ Done (`react-review-player`; burned-subtitle preview not ported) | Review | L | yes |
| 5 | Line-structure UI: re-segment preview and apply, history restore, merge (R47, R48, R46) | Review | M | yes (merge: part) |
| 6 | Character voice setup: clone engine, voice design, ref text, offline voice, voice-bank apply, custom pronouns (C06, C07, C10-C12, C14); ref clip upload and auto-extract (C09, C01) | Translate/Characters | M + M | yes / no |
| 7 | New-drama form: credits, summary, series, preset (P06-P08) | Library | S | yes |
| 8 | Glossary: delete and bulk delete (X12, X13); series people and pronouns (X15-X17); import/export CSV (T03, T04) | Translate | S / M / S | yes / no / no |
| 9 | Review read-only panels: coverage, pacing, consistency results, emotion summary, "What happened?", notes Markdown, version compare, tendencies (R26, R27, R29, R34, R20, R44, R41, R36) | Review | S each | yes |
| 10 | Stage index: open on the current stage and show progress in the stepper (P16, P17) | Workspace shell | S | no (service exists) |
| 11 | Novel reference translation upload (T01) and raw-novel priming upload plus auto initial prompt (S04, T08, S12) | Source/Transcript | M | no |
| 12 | URL / yt-dlp import (S07; the same backend as Sources SO05) | Source | L | no (S-5) |
| 13 | Bulk-jobs panel: list, check now, cancel (X35) and the last-run failure notice (X01) | Translate | M | part |
| 14 | Presets and tiers: save, apply, delete, workflow tier (X02, X03, X22, L18). Genre/she-her toggles (X05, X06) done on branch `fix-translate-preset-parity` | Translate/Library | M | no |
| 15 | Export completion: all ASS style controls plus a live preview, base filename, "Mark as exported", notes position (E11, E04, E22, E09) | Export | M | yes (mark exported: no) |

### By screen

**Settings**
1. Keys and endpoints (G06). S/M, part.
2. Spending cap, defaults for new dramas, Ollama num_ctx, offline Whisper folder (G14, G08, G13, G09). S, no.
3. OCR defaults and Tesseract path (G05). S, no.
4. Cookies for yt-dlp (G15). S, no.
5. Browser extension settings and token (G16), after M0-b. S, no.
6. Dark mode (G03). S, no.

**Library**
1. New-drama fields (P06-P08). S, yes.
2. More filters: studio, author, voice actor, language, type, tags (L07). S, yes.
3. Preset and voice-bank rename (L18, L19). S, yes. Delete needs an API.
4. Bulk status, delete and quick-list tags (L09, L10, L12). M, no.
5. Bulk translate selected (L11). M, no.
6. Backup and restore (L16, L17), E0 with typed confirm. L, no.
7. Storage scan and clean (L14). M, no.
8. Bulk .zip export (L13). S, no.
9. Reading history clear (L15). S, no.
10. Continue reading (L02), with the Reader. M, no.

**Workspace: preamble and Source**
1. Metadata form (P10, P11). S, yes.
2. Content and transcript mode pickers (S03, S08). S, yes.
3. Stage index (P16, P17). S, no.
4. Remove current audio (S05). S, no.
5. Cover art (P14). S, no.
6. Romanize credits (P13). S, no.
7. EPUB chapter range (S13). S, part.
8. Known-platforms list in auto-fill (P04). S, yes.

**Workspace: Transcript and Diarize**
1. Novel reference and raw-novel uploads, auto prompt (T01, S04, T08, S12). M, no.
2. Diarize overwrite-corrections choice (D06). S, yes.
3. Glossary from novel (T02). M, no.
4. Auto-tune (T10). M, no.
5. Diarization estimate caption (D04). S, no.

**Workspace: Translate and Characters**
1. Characters voice fields (C06, C07, C10-C12, C14). M, yes.
2. Glossary delete, bulk delete, series people (X12, X13, X15-X17). M, part.
3. Bulk-jobs panel and failure notice (X35, X01). M, part.
4. Presets and tiers (X02, X03, X22). M, no. Genre/she-her toggles (X05, X06) done on branch `fix-translate-preset-parity`.
5. Reference clip upload, auto-extract, voice suggestions, known series character, remember, save to voice bank (C09, C01, C02, C03, C08, C13). M, no.
6. Extract terms and review-glossary-before-translate (X10, X28). M, no.
7. Style guidance text (X04). S, no.

**Workspace: Review**
1. ~~Media player and per-line audio (R02, R13, R22).~~ Done (`react-review-player`).
2. Re-segment, merge, history restore (R47, R46, R48). M, yes.
3. Read-only panels (R26, R27, R29, R34, R36, R20, R41, R44). S each, yes.
4. SFX toggle (R14). S, yes.
5. Compare/restore original text (R24). S, yes.
6. Version activate and delete (R39, R40). S, no.
7. Engine choice and bulk option for AI jobs (R50, R49), audio-cues toggle (R33). S, part.
8. Cross-page flagged navigation and search jump (R08, R05), note jump (R43). S, part.
9. Per-line tools: alternatives, grammar, pronounce, re-transcribe, retry a blocked line (R17-R19, R23, R10). M, no.
10. TM dismiss and inline suggestion (R11). S, part.
11. Auto-shorten (R28), learn style (R37), SenseVoice (R35). M, no.
12. Burned-subtitle preview (R03). M, no.

**Workspace: Export**
1. ASS style controls plus preview, notes position (E11, E09). S/M, yes.
2. Base filename (E04). S, client-side only.
3. Mark as exported (E22). S, no.
4. Dub-audio video export (E19). M, no.
5. Softsub mux (E17). S, no.

**Diagnostics**
1. Core checks: Python, ffmpeg, JS runtime (Q01). S, no.
2. Log filter (Q18). S, part.
3. Support report (Q17). S, no.
4. pyannote access check (Q15). S, no.
5. Model cache management (Q14). M, no.
6. Install, upgrade, GPU torch, Deno (Q02, Q05-Q07, Q11, Q12). L, no, and admin actions over the API need a safety design.
7. Undecided extras: benchmark, bug bundles, App Assistant (Q19, Q10, Q16, R21).

**Translate page**
1. Clear history (N08). S, yes.
2. Download result (N07). S, client-side.
3. Upload file (N05). S, no.

**Reader** (L; needs the Range endpoint): RD01-RD14.
**Sources** (L; S-3..S-7): SO01-SO19, including the scheduler move in M0-b.
**Discover** (M/L; D-2): DI01, DI04-DI09. React page built (`react-discover-page`); left: DI07 pasted-text extraction (needs an API), DI08 in-app frame (replaced by a new tab).
**Live** (L; L-1): LV01-LV06 built (React `#/live`, branch `react-live-page`); tab deletion waits on the real-stream check.
