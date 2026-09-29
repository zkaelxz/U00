# Streamlit test triage

Written 2026-09-29, for the Streamlit deletion (`docs/streamlit-retirement-plan.md` section 9, guardrail 4: logic tests move to services before their tab test file goes; only widget and render tests are deleted). Companion to `docs/streamlit-feature-inventory.md`. BASE is `bdcc18c489b4ffc246db232b6bb9135a1603c6e0`.

**Scope.** Every test file that imports `tabs`, `app`, `common`, `ui` or `ui_theme`, or uses `streamlit.testing`/`AppTest`. That is 43 files, and every test in each one is listed below. Two more files depend on Streamlit without matching that pattern and are triaged by hand in section 3: `tests/test_static_analysis.py` scans `tabs/`, and `tests/test_streamlit_floor.py` imports `streamlit`. No test file was deleted.

## 1. Summary

| | Tests |
|---|---|
| Tests triaged (43 generated files + 2 hand-triaged) | 1,560 |
| **UI** (AppTest, widget/render, tab-source inspection, Streamlit state helpers): delete with the tab | 590 |
| **LOGIC**, total | 970 |
| • KEEP: no UI dependency (some need the file's top-level UI import removed or split; see each file's header) | 858 |
| • **REPOINTED in this branch** to the service with the same behaviour | 43 |
| • **NEEDS EXTRACTION**: pure logic that exists only in a tab/`ui` module (2 of these are also repointed) | 36 |
| • REWRITE: the service exists, but the assertion target differs (e.g. `st.session_state` vs a return value), so it is not a mechanical repoint | 8 |
| • COVERED: an equivalent service test already exists, so delete with the tab | 10 |
| • UPDATE at deletion: logic tests whose expectations change when `tabs/` goes | 13 |
| • DROP: logic that only exists to serve a Streamlit widget | 4 |

"Repointed" means only the call or import moved; setup was adapted and no assertion was removed or loosened. The repointed tests pass (148 passed, focused run).

### What this branch changed (tests only)

| File | Change |
|---|---|
| `tests/test_auto_qc.py` | The two `_run_auto_qc` tests call `services.export_service.run_auto_qc_flagging(did)`. The service loads the drama's lines itself, so the local `lines` load feeding it was dropped |
| `tests/test_workspace_tab.py` | Top-level import is `services.workflow_service.compute_workspace_stage_index` (aliased to the old name), so the file no longer imports the tab at module level. The two fix-flagged job tests patch `core_module` directly (it was the same object). `test_music_is_a_media_type_option` checks `services.drama_service.MEDIA_TYPE_OPTIONS` |
| `tests/test_local_model_defaults.py` | `run_transcribe_job` from `services.workspace_job_service` |
| `tests/test_library_features.py` | `TestBulkSeriesTranslate` and `TestRestoreFromBackupValidatesBeforeDestroying` import `services.workspace_job_service as lt`. The tab only re-exported both functions, and `lt.time` is the same stdlib module |
| `tests/test_speaker_rerun.py` | The `no_asr` fixture no longer patches `tabs.workspace_tab.transcribe_for_timing`. That patch was dead: the tab never calls the bare name, and the `core`, `cli` and `workspace_job_service` patches stay |
| `tests/test_settings_tab.py` | Three `TestSaveKeyToEnv` file-content tests call `settings_service.set_engine_key`, which writes the same canonical line in place |

### Notes for the deletion PR

- **Files that break at import when the UI modules go**, even though they hold LOGIC tests: `test_app.py` (`streamlit.testing`), `test_app_help.py` (`from common import *`), `test_media_preview.py` and `test_review_workspace.py` (`tabs.workspace_tab`), `test_step20_ux_polish.py` (`tabs.workspace_tab`), `test_settings_tab.py` (`streamlit`, `tabs.settings_tab`), `test_ui_components.py` (`ui`), `test_dark_mode_step68.py` (`ui_theme`), `test_common.py`. Move the KEEP/REPOINTED tests out, or remove the import, before deleting the modules.
- **`app_help.py` is grounded in `tabs/*_tab.py` source.** Once `tabs/` is gone, the App Assistant (Diagnostics Q16) has nothing to ground on and `test_real_app_tabs_dir_produces_nonempty_grounding` fails. Its fate follows the still-open "Diagnostics extras" decision.
- **`diagnostics.py`** lists `streamlit` as required and keeps `EXPECTED_TABS_FILES`. `test_expected_tabs_files_list_is_not_stale` and the file-completeness tests must be updated in the same PR (plan section 2).
- **Extraction targets**, all small and pure: `ui.workflow.stage_statuses_from_index` → `services/workflow_service.py`; `library_tab.cache_hit_share` → `services/library_service.py`; `diagnostics_tab._describe_job` → `services/jobs_service.py`; `reader_tab.caption_tracks` → a reader/export service; `workspace_tab.parse_timestamp`, `_burn_preview_ass`, `_line_audio_clip` and `_adjacent_flagged_idx` → when the backlog item that needs them is built (R02, R03, R22, R08), or lift them from the `pre-streamlit-removal` tag then; `scanlate_tab.add_uploaded_pages` → the Scanlate port (deferred); `workspace_tab._diarization_estimate_caption` → only if React shows the estimate; `ui_theme.resolve_reader_theme` → only if the Reader port wants it.
- Several "UI" AppTest tests assert behaviour whose logic lives **only in the tab**, not in a service: transcription completion (`TestTranscriptionCompletionDoesNotWipeExistingLines`), stale-snapshot guards on merge/restore/re-segment, destructive-action confirmations, and the per-drama widget-key isolation classes. The server-side equivalents have their own service tests (`services/transcribe_service.py`, `restructure_service.py`), but the React features they guard are mostly MISSING (inventory R46-R48, S04, T01). When each React feature is built, its API/service test should restate the invariant named in the Streamlit test class.
- One flaky AppTest seen during the baseline under load: `test_workspace_tab.py::TestLineEditingNotLockedDuringAJob::test_save_edits_stays_enabled_while_translate_is_running` failed once while another pytest process was running, then passed alone (4/4). It is UI and goes with the tab.

## 2. Method

An AST pass (`scratchpad`, not committed) listed every test and followed module-level helpers, fixtures and `self._helper` calls to find what each test touches. Classification:

- It builds an `AppTest` → UI.
- It reads tab source (`inspect.getsource`, `open("tabs/...")`) → UI, unless the text only mentions a tab in a docstring.
- It calls a function from `tabs`/`common`/`ui`/`ui_theme` → LOGIC or UI, decided by hand per function (see section 1 notes).
- Otherwise → LOGIC KEEP.

The hand decisions are listed in the per-test tables.

## 3. Hand-triaged files

### `tests/test_static_analysis.py` (60 tests: 26 LOGIC, 34 UI)

| Tests | Class | Action |
|---|---|---|
| `TestNoUseBeforeDefinition::*` (9), `TestNoUndefinedNames::*` (9), `TestNoBareRepeatedExpanderLabels::test_workspace_tab` (1) | UI | DELETE with tabs (they scan `tabs/`) |
| `TestScanTabFileCatchesLaterReimport::*` (2), `TestScannerItself::*` (6), `TestUndefinedNameCheckerItself::*` (4), `TestDuplicateLabelCheckerItself::*` (3) | UI | DELETE with tabs, together with the tab-only checkers they test (`_names_bound_by_module` reads `common.py`) |
| `TestHttpCallsHaveTimeouts::*` (11), `TestTimeoutCheckerItself::*` (5), `TestSessionVerbTimeouts::*` (2), `TestConstraintsFile::*` (3), `TestRequirementsFilesConsolidation::*` (4), `TestRequirementsPackageNameParserItself::*` (1) | LOGIC | KEEP. Re-check `TestConstraintsFile` when `streamlit` leaves `constraints.txt` |

### `tests/test_streamlit_floor.py` (4 tests)

All 4 (`test_requirements_txt_has_no_floor_of_its_own_to_drift`, `test_floor_is_at_least_what_st_tabs_default_needs`, `test_floor_covers_every_newer_feature_the_app_actually_uses`, `test_every_streamlit_call_exists_in_the_installed_streamlit`): UI, delete with the `streamlit` dependency.

## 4. Per-file, per-test triage (generated)

Columns: line at this branch's pre-edit state (BASE), test, class, action, note.

### `tests/test_adaptive_extraction.py` (36 tests: 32 LOGIC, 4 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 122 | `TestLlmOnlyAsFallback::test_deterministic_success_makes_no_llm_call` | LOGIC | KEEP |  |
| 129 | `TestLlmOnlyAsFallback::test_mocked_success_vs_mocked_empty` | LOGIC | KEEP |  |
| 142 | `TestLlmOnlyAsFallback::test_ambiguous_deterministic_result_triggers_exactly_one_call` | LOGIC | KEEP |  |
| 154 | `TestLlmOnlyAsFallback::test_no_engine_means_no_call_and_an_honest_reason` | LOGIC | KEEP |  |
| 161 | `TestLlmOnlyAsFallback::test_comic_filter_success_makes_no_llm_call` | LOGIC | KEEP |  |
| 179 | `TestNovelExtraction::test_confidence_per_field_boundaries_and_nulls` | LOGIC | KEEP |  |
| 209 | `TestNovelExtraction::test_model_cannot_rewrite_text` | LOGIC | KEEP |  |
| 221 | `TestNovelExtraction::test_unknown_ids_are_ignored_not_matched_by_position` | LOGIC | KEEP |  |
| 263 | `TestComicClassification::test_separates_content_from_chrome_without_downloading` | LOGIC | KEEP |  |
| 291 | `TestComicClassification::test_script_listed_urls_are_fetched_only_when_tags_find_nothing` | LOGIC | KEEP |  |
| 311 | `TestComicClassification::test_import_hands_only_llm_pages_to_the_existing_downloader` | LOGIC | KEEP |  |
| 365 | `TestMediaResources::test_identifies_media_and_subtitles_without_downloading` | LOGIC | KEEP |  |
| 387 | `TestMediaResources::test_urls_the_existing_video_path_handles_are_left_alone` | LOGIC | KEEP |  |
| 394 | `TestMediaResources::test_one_clear_resource_needs_no_llm` | LOGIC | KEEP |  |
| 401 | `TestMediaResources::test_drm_markers_are_named_not_worked_around` | LOGIC | KEEP |  |
| 414 | `TestIndependentValidation::test_implausible_length_is_rejected_despite_model_confidence` | LOGIC | KEEP |  |
| 434 | `TestIndependentValidation::test_high_duplicate_rate_is_rejected` | LOGIC | KEEP |  |
| 452 | `TestIndependentValidation::test_unrelated_next_link_is_rejected` | LOGIC | KEEP |  |
| 472 | `TestIndependentValidation::test_link_resemblance` | LOGIC | KEEP |  |
| 481 | `TestProfileValidationBeforeSave::test_llm_result_generates_a_validated_profile` | LOGIC | KEEP |  |
| 490 | `TestProfileValidationBeforeSave::test_a_candidate_that_fails_validation_is_not_saved` | LOGIC | KEEP |  |
| 506 | `TestProfileValidationBeforeSave::test_save_version_refuses_unvalidated_or_unapproved` | LOGIC | KEEP |  |
| 517 | `TestProfileValidationBeforeSave::test_medium_candidate_waits_for_approval` | LOGIC | KEEP |  |
| 539 | `TestProfileReuseAndVersioning::test_reuse_then_break_then_replace_and_roll_back` | LOGIC | KEEP |  |
| 578 | `TestProfileReuseAndVersioning::test_candidate_needing_approval_leaves_the_old_profile_active` | LOGIC | KEEP |  |
| 594 | `TestProfileReuseAndVersioning::test_comic_profile_reused_without_llm_and_corrections_stick` | LOGIC | KEEP |  |
| 629 | `TestExtractionCache::test_unchanged_page_skips_the_call_changed_page_reruns` | LOGIC | KEEP |  |
| 644 | `TestExtractionCache::test_entry_carries_provenance` | LOGIC | KEEP |  |
| 656 | `TestExtractionCache::test_schema_bump_invalidates_the_cached_entry` | LOGIC | KEEP |  |
| 675 | `TestDiagnostics::test_import_logs_tier_profile_and_reason` | LOGIC | KEEP |  |
| 686 | `TestDiagnostics::test_failure_is_explained_not_generic` | LOGIC | KEEP |  |
| 695 | `TestDiagnostics::test_llm_errors_are_redacted` | LOGIC | KEEP |  |
| 739 | `TestSourcesTabReview::test_novel_correction_is_saved_as_the_site_profile` | UI | DELETE with tab | AppTest |
| 774 | `TestSourcesTabReview::test_comic_image_marked_as_ad_sticks_on_the_next_fetch` | UI | DELETE with tab | AppTest |
| 818 | `TestSourcesTabReview::test_diagnostics_lists_attempts_and_rolls_back_profiles` | UI | DELETE with tab | AppTest |
| 835 | `TestSourcesTabReview::test_ai_fallback_is_off_by_default` | UI | DELETE with tab | AppTest |

### `tests/test_app.py` (2 tests: 1 LOGIC, 1 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 22 | `TestAppIcon::test_app_icon_file_exists` | LOGIC | KEEP (fix file import) | assets/app_icon.ico is also used by make_shortcut.bat; move out of a file that imports streamlit.testing |
| 28 | `TestAppIcon::test_app_runs_with_no_exception` | UI | DELETE with tab | AppTest |

### `tests/test_app_help.py` (12 tests: 12 LOGIC, 0 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 29 | `TestExtractModuleSections::test_extracts_heading_and_caption` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 36 | `TestExtractModuleSections::test_extracts_expander_and_help_kwarg` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 43 | `TestExtractModuleSections::test_syntax_error_returns_empty_list_not_raise` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 46 | `TestExtractModuleSections::test_no_st_calls_returns_empty_list` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 51 | `TestBuildGroundingContext::test_grounding_reflects_a_real_fixture_module_with_no_app_help_change` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 64 | `TestBuildGroundingContext::test_ignores_non_tab_files` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 73 | `TestBuildGroundingContext::test_real_app_tabs_dir_produces_nonempty_grounding` | LOGIC | UPDATE at deletion | fails once tabs/ is gone; app_help.py is grounded in tabs/*_tab.py (fate follows the App Assistant decision) |
| 97 | `TestAskAboutApp::test_grounding_is_passed_into_the_system_prompt` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 108 | `TestAskAboutApp::test_declines_cleanly_for_a_translation_only_engine` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 114 | `TestAskAboutApp::test_system_prompt_instructs_decline_and_excludes_unmatched_terms` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 130 | `TestAskAboutApp::test_chat_history_is_carried_through` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |
| 142 | `TestFormatHelpReport::test_combines_question_answer_and_diagnostics` | LOGIC | KEEP (fix file import) / UPDATE at deletion | app_help unit tests on temp dirs; the file does `from common import *` (breaks when common.py goes); whole module follows the App Assistant decision |

### `tests/test_auto_qc.py` (18 tests: 16 LOGIC, 2 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 35 | `test_missing_detail_is_flagged_with_what_is_missing` | LOGIC | KEEP |  |
| 50 | `test_number_not_in_source_is_flagged_as_likely_hallucination` | LOGIC | KEEP |  |
| 87 | `test_correctly_carried_over_details_are_not_flagged` | LOGIC | KEEP |  |
| 91 | `test_blank_source_or_translation_is_not_checked` | LOGIC | KEEP |  |
| 96 | `test_word_numbers_in_translation_ignored_for_a_non_cjk_source` | LOGIC | KEEP |  |
| 103 | `test_cjk_numeral_parsing` | LOGIC | KEEP |  |
| 112 | `test_name_list_only_uses_name_categories_and_cjk_aliases` | LOGIC | KEEP |  |
| 121 | `test_name_list_builds_multiple_source_and_target_forms_from_glossary_aliases` | LOGIC | KEEP |  |
| 141 | `test_build_banned_terms_uses_term_original_and_aliases_as_source_forms` | LOGIC | KEEP |  |
| 149 | `test_check_line_flags_banned_translation_but_leaves_canonical_untouched` | LOGIC | KEEP |  |
| 164 | `test_run_auto_qc_flags_banned_translation_without_rewriting_the_line` | LOGIC | KEEP |  |
| 183 | `test_run_auto_qc_flags_via_the_normal_flag_fields` | LOGIC | KEEP |  |
| 194 | `test_run_auto_qc_never_replaces_another_checks_flag` | LOGIC | KEEP |  |
| 202 | `test_rerun_clears_its_own_flag_once_fixed` | LOGIC | KEEP |  |
| 210 | `test_run_auto_qc_workspace_helper_saves_flags_and_uses_series_names` | LOGIC | REPOINTED | now calls services.export_service.run_auto_qc_flagging |
| 231 | `test_flagged_line_restores_from_version_history_like_any_other` | LOGIC | REPOINTED | now calls services.export_service.run_auto_qc_flagging |
| 260 | `test_run_auto_qc_button_flags_lines_in_the_review_queue` | UI | DELETE with tab | AppTest |
| 288 | `test_auto_qc_before_export_is_read_only_until_clicked` | UI | DELETE with tab | AppTest |

### `tests/test_benchmark.py` (24 tests: 22 LOGIC, 2 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 23 | `TestScoreTextSimilarity::test_identical_text_scores_one` | LOGIC | KEEP |  |
| 26 | `TestScoreTextSimilarity::test_completely_different_text_scores_low` | LOGIC | KEEP |  |
| 29 | `TestScoreTextSimilarity::test_whitespace_differences_are_ignored` | LOGIC | KEEP |  |
| 32 | `TestScoreTextSimilarity::test_both_empty_scores_one` | LOGIC | KEEP |  |
| 35 | `TestScoreTextSimilarity::test_one_empty_scores_zero` | LOGIC | KEEP |  |
| 38 | `TestScoreTextSimilarity::test_partial_overlap_is_between_zero_and_one` | LOGIC | KEEP |  |
| 44 | `TestRunTranscriptionCase::test_scores_against_a_reference_transcript` | LOGIC | KEEP |  |
| 57 | `TestRunTranscriptionCase::test_no_reference_text_gives_no_score` | LOGIC | KEEP |  |
| 66 | `TestRunTranscriptionCase::test_a_transcription_failure_is_captured_not_raised` | LOGIC | KEEP |  |
| 78 | `TestRunTranscriptionCase::test_passes_through_whisper_size_and_language` | LOGIC | KEEP |  |
| 101 | `TestRunTranslationCase::test_scores_against_a_reference_translation` | LOGIC | KEEP |  |
| 109 | `TestRunTranslationCase::test_a_translation_failure_is_captured_not_raised` | LOGIC | KEEP |  |
| 120 | `TestRunTranslationCase::test_logs_cost_when_the_engine_reports_usage` | LOGIC | KEEP |  |
| 135 | `TestRunTranslationCase::test_no_usage_attribute_costs_nothing` | LOGIC | KEEP |  |
| 143 | `TestRunOcrCase::test_scores_against_reference_ocr_text` | LOGIC | KEEP |  |
| 150 | `TestRunOcrCase::test_an_ocr_failure_is_captured_not_raised` | LOGIC | KEEP |  |
| 161 | `TestRunSuite::test_dispatches_to_the_right_stage_runner` | LOGIC | KEEP |  |
| 170 | `TestRunSuite::test_unknown_stage_raises_a_clear_error` | LOGIC | KEEP |  |
| 174 | `TestRunSuite::test_one_cases_failure_does_not_stop_the_rest` | LOGIC | KEEP |  |
| 202 | `TestCompareConfigs::test_runs_one_case_through_each_config_and_labels_results` | LOGIC | KEEP |  |
| 212 | `TestCompareConfigs::test_one_configs_failure_doesnt_stop_the_other` | LOGIC | KEEP |  |
| 223 | `TestCompareConfigs::test_saves_nothing_to_run_history` | LOGIC | KEEP |  |
| 245 | `TestCompareEnginesInDiagnostics::test_shows_both_engines_outputs_and_leaves_history_alone` | UI | DELETE with tab | AppTest |
| 269 | `TestCompareEnginesInDiagnostics::test_missing_api_key_warns_instead_of_running` | UI | DELETE with tab | AppTest |

### `tests/test_bulk_translate.py` (53 tests: 52 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 103 | `TestSubmit::test_every_batch_goes_in_one_submission_with_valid_custom_ids` | LOGIC | KEEP |  |
| 115 | `TestSubmit::test_lines_are_numbered_by_permanent_id_not_position` | LOGIC | KEEP |  |
| 123 | `TestSubmit::test_saves_each_lines_id_hash_and_english_at_submission` | LOGIC | KEEP |  |
| 134 | `TestSubmit::test_the_stable_system_prompt_is_identical_in_every_request` | LOGIC | KEEP |  |
| 141 | `TestSubmit::test_only_untranslated_lines_are_submitted` | LOGIC | KEEP |  |
| 150 | `TestSubmit::test_a_submission_failure_is_recorded_not_lost` | LOGIC | KEEP |  |
| 168 | `TestApplyResults::test_out_of_order_results_still_land_on_the_right_lines` | LOGIC | KEEP |  |
| 182 | `TestApplyResults::test_a_line_merged_away_meanwhile_has_its_result_dropped` | LOGIC | KEEP |  |
| 215 | `TestApplyResults::test_a_line_whose_source_was_edited_is_flagged_not_applied` | LOGIC | KEEP |  |
| 232 | `TestApplyResults::test_an_english_edit_made_meanwhile_is_kept` | LOGIC | KEEP |  |
| 249 | `TestApplyResults::test_a_response_cannot_assign_to_lines_outside_its_own_request` | LOGIC | KEEP |  |
| 267 | `TestApplyResults::test_a_positional_array_response_is_not_accepted_for_bulk` | LOGIC | KEEP |  |
| 280 | `TestApplyResults::test_errored_and_expired_requests_leave_lines_untranslated` | LOGIC | KEEP |  |
| 296 | `TestApplyResults::test_usage_is_logged_at_the_batch_discount` | LOGIC | KEEP |  |
| 307 | `TestApplyResults::test_enforced_glossary_terms_apply_to_bulk_results` | LOGIC | KEEP |  |
| 321 | `TestApplyResults::test_still_pending_changes_nothing` | LOGIC | KEEP |  |
| 340 | `TestRestartCancelAuth::test_a_restarted_app_picks_up_a_pending_batch_by_its_saved_id` | LOGIC | KEEP |  |
| 363 | `TestRestartCancelAuth::test_resume_without_a_key_reports_it_instead_of_polling` | LOGIC | KEEP |  |
| 371 | `TestRestartCancelAuth::test_cancel_stops_polling_and_later_results_are_ignored` | LOGIC | KEEP |  |
| 392 | `TestRestartCancelAuth::test_a_polling_auth_error_is_recorded_on_the_job` | LOGIC | KEEP |  |
| 408 | `TestRestartCancelAuth::test_the_poller_stops_on_an_auth_error_instead_of_retrying_forever` | LOGIC | KEEP |  |
| 423 | `TestRestartCancelAuth::test_check_now_after_fixing_the_key_clears_the_auth_error` | LOGIC | KEEP |  |
| 454 | `TestGeminiProvider::test_submission_shape_and_key_in_header` | LOGIC | KEEP |  |
| 472 | `TestGeminiProvider::test_results_are_matched_by_metadata_key_not_position` | LOGIC | KEEP |  |
| 494 | `TestGeminiProvider::test_a_response_without_its_key_is_never_attributed` | LOGIC | KEEP |  |
| 507 | `TestGeminiProvider::test_a_401_while_polling_becomes_an_auth_error_on_the_job` | LOGIC | KEEP |  |
| 517 | `TestGeminiProvider::test_a_failed_batch_is_marked_failed` | LOGIC | KEEP |  |
| 527 | `TestGeminiProvider::test_cancel_posts_to_the_cancel_endpoint` | LOGIC | KEEP |  |
| 544 | `TestDeepSeekOffPeak::test_weekday_peak_waits_for_the_window_to_end` | LOGIC | KEEP |  |
| 551 | `TestDeepSeekOffPeak::test_offpeak_now_starts_now` | LOGIC | KEEP |  |
| 573 | `TestDeepSeekOffPeak::test_a_scheduled_job_waits_then_translates_only_what_it_was_scheduled_for` | LOGIC | KEEP |  |
| 597 | `TestDeepSeekOffPeak::test_an_orphaned_running_job_is_resumed_after_a_restart` | LOGIC | KEEP |  |
| 620 | `TestBulkFlag::test_applies_by_line_id_with_drop_flag_and_kept_edit` | LOGIC | KEEP |  |
| 653 | `TestBulkFlag::test_a_line_deleted_meanwhile_is_dropped` | LOGIC | KEEP |  |
| 669 | `TestBulkFlag::test_engines_without_a_batch_api_are_rejected` | LOGIC | KEEP |  |
| 676 | `TestBulkConsistency::test_a_window_with_a_stale_line_is_dropped_a_clean_one_is_kept` | LOGIC | KEEP |  |
| 699 | `TestBulkConsistency::test_no_usable_windows_leaves_existing_issues_untouched` | LOGIC | KEEP |  |
| 713 | `TestBulkEmotion::test_applies_by_line_id_with_drop_flag_and_kept_edit` | LOGIC | KEEP |  |
| 741 | `TestBulkEmotion::test_a_line_deleted_meanwhile_is_dropped` | LOGIC | KEEP |  |
| 755 | `TestBulkTranslationNotes::test_applies_by_line_id_and_drops_a_stale_or_deleted_line` | LOGIC | KEEP |  |
| 778 | `TestBulkTranslationNotes::test_notes_are_additive_not_overwritten` | LOGIC | KEEP |  |
| 812 | `TestBulkReflectPipeline::test_three_sequential_batches_each_keyed_off_the_previous_stage` | LOGIC | KEEP |  |
| 858 | `TestBulkReflectPipeline::test_a_kept_edit_survives_through_every_stage` | LOGIC | KEEP |  |
| 879 | `TestBulkReflectPipeline::test_engines_without_a_batch_api_are_rejected` | LOGIC | KEEP |  |
| 885 | `TestBulkReflectPipeline::test_panel_shows_the_current_stage_not_just_pending` | UI | DELETE with tab | AppTest |
| 933 | `TestFinishTranslationRunGlossaryEnforcement::test_substitution_is_applied_to_the_current_db_text_not_a_stale_job_copy` | LOGIC | KEEP |  |
| 956 | `TestFinishTranslationRunGlossaryEnforcement::test_a_line_the_substitution_does_not_change_is_left_alone` | LOGIC | KEEP |  |
| 1011 | `TestFinishTranslationRunEpisodeSummary::test_generates_and_stores_a_summary_once_the_episode_is_fully_translated` | LOGIC | KEEP |  |
| 1021 | `TestFinishTranslationRunEpisodeSummary::test_no_summary_engine_skips_generation_entirely` | LOGIC | KEEP |  |
| 1031 | `TestFinishTranslationRunEpisodeSummary::test_not_generated_for_a_cancelled_run` | LOGIC | KEEP |  |
| 1042 | `TestFinishTranslationRunEpisodeSummary::test_not_generated_while_lines_remain_untranslated` | LOGIC | KEEP |  |
| 1061 | `TestFinishTranslationRunEpisodeSummary::test_called_once_regardless_of_how_many_lines_the_episode_has` | LOGIC | KEEP |  |
| 1076 | `TestFinishTranslationRunEpisodeSummary::test_a_declining_summary_engine_does_not_overwrite_an_existing_summary` | LOGIC | KEEP |  |

### `tests/test_common.py` (5 tests: 0 LOGIC, 5 UI)

Top-level UI/Streamlit imports: `streamlit`, `common (_pull_canonical_settings_value)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 38 | `TestPullCanonicalSettingsValue::test_fills_a_blank_widget_from_the_canonical_value` | UI | DELETE with tab | common._pull_canonical_settings_value is Streamlit widget-state sync |
| 43 | `TestPullCanonicalSettingsValue::test_returns_the_settings_state_key` | UI | DELETE with tab | common._pull_canonical_settings_value is Streamlit widget-state sync |
| 47 | `TestPullCanonicalSettingsValue::test_does_not_overwrite_a_widget_that_already_has_its_own_value` | UI | DELETE with tab | common._pull_canonical_settings_value is Streamlit widget-state sync |
| 53 | `TestPullCanonicalSettingsValue::test_a_blank_canonical_value_leaves_the_widget_untouched` | UI | DELETE with tab | common._pull_canonical_settings_value is Streamlit widget-state sync |
| 57 | `TestPullCanonicalSettingsValue::test_works_with_a_dynamic_engine_scoped_key` | UI | DELETE with tab | common._pull_canonical_settings_value is Streamlit widget-state sync |

### `tests/test_dark_mode_consistency.py` (4 tests: 0 LOGIC, 4 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 41 | `test_expander_header_bar_gets_a_dark_background` | UI | DELETE with tab |  |
| 51 | `test_popover_trigger_button_gets_dark_styling` | UI | DELETE with tab |  |
| 59 | `test_disabled_text_inputs_override_webkit_text_fill_color` | UI | DELETE with tab |  |
| 70 | `test_standalone_translate_source_box_is_a_plain_disabled_text_area` | UI | DELETE with tab |  |

### `tests/test_dark_mode_step68.py` (18 tests: 1 LOGIC, 17 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`, `ui_theme`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 49 | `TestReactAriaWidgetsAreCovered::test_selectbox_and_multiselect_field` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 55 | `TestReactAriaWidgetsAreCovered::test_the_older_baseweb_select_rule_is_kept_too` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 59 | `TestReactAriaWidgetsAreCovered::test_open_option_list` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 66 | `TestReactAriaWidgetsAreCovered::test_text_number_and_chat_input_surfaces` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 72 | `TestReactAriaWidgetsAreCovered::test_number_input_error_state_keeps_its_red_tint` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 80 | `TestPanelsRenderedOutsideTheApp::test_popover_content_panel` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 85 | `TestPanelsRenderedOutsideTheApp::test_help_and_error_tooltips` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 94 | `TestOtherGapsFromTheAudit::test_st_text_is_readable` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 99 | `TestOtherGapsFromTheAudit::test_file_uploader_rule_is_not_tag_qualified` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 104 | `TestOtherGapsFromTheAudit::test_non_st_button_secondary_buttons` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 110 | `TestOtherGapsFromTheAudit::test_top_header_strip` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 124 | `TestReaderFollowsAppDarkMode::test_resolve_reader_theme` | LOGIC | NEEDS EXTRACTION | ui_theme.resolve_reader_theme -> React Reader theme (TS) when the Reader is ported; else drop |
| 127 | `TestReaderFollowsAppDarkMode::test_match_app_is_the_default_setting` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 137 | `TestReaderFollowsAppDarkMode::test_reader_table_is_rendered_dark_when_the_app_is` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 171 | `TestPopoverScrollAndHeight::test_wheel_passthrough_is_installed_in_both_themes` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 192 | `TestPopoverScrollAndHeight::test_older_streamlit_without_the_js_flag_does_not_crash` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 201 | `TestPopoverScrollAndHeight::test_popovers_with_sections_get_a_constant_height` | UI | DELETE with tab | ui_theme/dark-mode CSS |
| 215 | `test_discover_embed_iframe_is_documented_as_deliberately_unthemed` | UI | DELETE with tab | ui_theme/dark-mode CSS |

### `tests/test_db.py` (165 tests: 164 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 20 | `TestDramaCRUD::test_create_and_get` | LOGIC | KEEP |  |
| 26 | `TestDramaCRUD::test_narration_language_defaults_to_translation_and_persists_original` | LOGIC | KEEP |  |
| 35 | `TestDramaCRUD::test_source_url_persists` | LOGIC | KEEP |  |
| 45 | `TestDramaCRUD::test_create_sets_created_and_updated_at` | LOGIC | KEEP |  |
| 51 | `TestDramaCRUD::test_update_changes_updated_at` | LOGIC | KEEP |  |
| 62 | `TestDramaCRUD::test_delete_removes_drama` | LOGIC | KEEP |  |
| 67 | `TestDramaCRUD::test_get_nonexistent_returns_none` | LOGIC | KEEP |  |
| 70 | `TestDramaCRUD::test_list_dramas_filters_by_language` | LOGIC | KEEP |  |
| 77 | `TestDramaCRUD::test_list_dramas_filters_by_media_type` | LOGIC | KEEP |  |
| 84 | `TestDramaCRUD::test_search_matches_title_and_summary` | LOGIC | KEEP |  |
| 91 | `TestCharacters::test_upsert_creates_new_character` | LOGIC | KEEP |  |
| 98 | `TestCharacters::test_upsert_preserves_existing_fields_when_not_overridden` | LOGIC | KEEP |  |
| 107 | `TestCharacters::test_elevenlabs_voice_id_persists` | LOGIC | KEEP |  |
| 113 | `TestCharacters::test_voice_engine_and_description_persist_without_clobbering` | LOGIC | KEEP |  |
| 123 | `TestCharacters::test_existing_characters_get_no_engine_so_they_keep_f5tts` | LOGIC | KEEP |  |
| 128 | `TestCharacters::test_series_character_id_persists` | LOGIC | KEEP |  |
| 140 | `TestLinesSaveLoad::test_save_and_load_roundtrip` | LOGIC | KEEP |  |
| 150 | `TestLinesSaveLoad::test_save_lines_replaces_previous_set` | LOGIC | KEEP |  |
| 158 | `TestLinesSaveLoad::test_dub_filename_persists` | LOGIC | KEEP |  |
| 166 | `TestLinesSaveLoad::test_flag_and_flag_note_persist` | LOGIC | KEEP |  |
| 175 | `TestLinesSaveLoad::test_unflagged_line_has_no_flag` | LOGIC | KEEP |  |
| 181 | `TestLinesSaveLoad::test_resaving_clears_a_previously_set_flag` | LOGIC | KEEP |  |
| 197 | `TestLoadLineIds::test_returns_the_current_id_set` | LOGIC | KEEP |  |
| 204 | `TestLoadLineIds::test_empty_for_a_drama_with_no_lines` | LOGIC | KEEP |  |
| 208 | `TestLoadLineIds::test_reflects_a_change_made_by_another_save` | LOGIC | KEEP |  |
| 222 | `TestSeriesAndGlossary::test_get_or_create_is_idempotent` | LOGIC | KEEP |  |
| 227 | `TestSeriesAndGlossary::test_different_names_get_different_ids` | LOGIC | KEEP |  |
| 232 | `TestSeriesAndGlossary::test_glossary_term_upsert_updates_translation` | LOGIC | KEEP |  |
| 240 | `TestSeriesAndGlossary::test_glossary_term_aliases_and_banned_translations_round_trip` | LOGIC | KEEP |  |
| 252 | `TestSeriesAndGlossary::test_glossary_term_upsert_without_aliases_keeps_existing_value` | LOGIC | KEEP |  |
| 264 | `TestSeriesAndGlossary::test_update_glossary_term_sets_aliases_and_banned_translations` | LOGIC | KEEP |  |
| 274 | `TestSeriesAndGlossary::test_two_dramas_share_a_series_glossary` | LOGIC | KEEP |  |
| 285 | `TestSeriesAndGlossary::test_delete_glossary_term` | LOGIC | KEEP |  |
| 292 | `TestSeriesAndGlossary::test_update_glossary_term_can_rename_the_original_text` | LOGIC | KEEP |  |
| 312 | `TestSeriesAndGlossary::test_update_glossary_term_does_not_create_a_duplicate_row` | LOGIC | KEEP |  |
| 335 | `TestPresets::test_save_preset_creates_and_captures_every_field` | LOGIC | KEEP |  |
| 349 | `TestPresets::test_defaults_are_applied_when_not_given` | LOGIC | KEEP |  |
| 355 | `TestPresets::test_list_presets_is_sorted_case_insensitively_by_name` | LOGIC | KEEP |  |
| 361 | `TestPresets::test_saving_under_an_existing_name_overwrites_its_fields` | LOGIC | KEEP |  |
| 370 | `TestPresets::test_delete_preset_does_not_change_a_drama_it_was_applied_to` | LOGIC | KEEP |  |
| 386 | `TestPresets::test_delete_preset_only_removes_that_one_preset` | LOGIC | KEEP |  |
| 392 | `TestPresets::test_rename_preset_changes_only_the_name` | LOGIC | KEEP |  |
| 407 | `TestPresets::test_rename_preset_does_not_affect_other_presets` | LOGIC | KEEP |  |
| 420 | `TestSeriesCharacters::test_upsert_creates_new` | LOGIC | KEEP |  |
| 430 | `TestSeriesCharacters::test_upsert_same_name_updates_not_duplicates` | LOGIC | KEEP |  |
| 438 | `TestSeriesCharacters::test_same_name_in_different_series_are_independent` | LOGIC | KEEP |  |
| 446 | `TestSeriesCharacters::test_gender_can_be_set_and_read_back` | LOGIC | KEEP |  |
| 452 | `TestSeriesCharacters::test_gender_defaults_to_unset` | LOGIC | KEEP |  |
| 458 | `TestSeriesCharacters::test_updating_notes_without_passing_gender_does_not_clear_it` | LOGIC | KEEP |  |
| 469 | `TestSeriesCharacters::test_gender_can_be_explicitly_cleared` | LOGIC | KEEP |  |
| 476 | `TestSeriesCharacters::test_per_drama_pronouns_set_kept_and_cleared` | LOGIC | KEEP |  |
| 486 | `TestSeriesCharacters::test_linked_series_pronouns_come_back_with_the_drama_character` | LOGIC | KEEP |  |
| 496 | `TestSeriesCharacters::test_rename_updates_in_place` | LOGIC | KEEP |  |
| 505 | `TestSeriesCharacters::test_rename_propagates_to_linked_drama_characters` | LOGIC | KEEP |  |
| 526 | `TestSeriesCharacters::test_unlinked_character_name_is_unaffected_by_rename` | LOGIC | KEEP |  |
| 537 | `TestSeriesCharacters::test_delete_unlinks_but_keeps_the_drama_characters_own_name` | LOGIC | KEEP |  |
| 552 | `TestSeriesCharacters::test_list_is_empty_for_a_series_with_no_characters_yet` | LOGIC | KEEP |  |
| 561 | `TestVoiceFingerprint::test_defaults_to_unset` | LOGIC | KEEP |  |
| 568 | `TestVoiceFingerprint::test_first_sample_is_stored_as_is` | LOGIC | KEEP |  |
| 578 | `TestVoiceFingerprint::test_second_sample_is_averaged_with_the_first` | LOGIC | KEEP |  |
| 589 | `TestVoiceFingerprint::test_dimension_mismatch_restarts_from_the_new_sample` | LOGIC | KEEP |  |
| 600 | `TestVoiceFingerprint::test_unknown_series_character_id_is_a_no_op` | LOGIC | KEEP |  |
| 605 | `TestVoiceSuggestionDismissals::test_dismissed_pair_is_listed` | LOGIC | KEEP |  |
| 613 | `TestVoiceSuggestionDismissals::test_a_different_candidate_for_the_same_speaker_is_not_dismissed` | LOGIC | KEEP |  |
| 624 | `TestVoiceSuggestionDismissals::test_dismissing_the_same_pair_twice_does_not_raise` | LOGIC | KEEP |  |
| 633 | `TestVoiceSuggestionDismissals::test_no_dismissals_yet_is_an_empty_set` | LOGIC | KEEP |  |
| 639 | `TestVocabLookups::test_save_and_list` | LOGIC | KEEP |  |
| 646 | `TestVocabLookups::test_duplicate_word_does_not_overwrite` | LOGIC | KEEP |  |
| 654 | `TestVocabLookups::test_rich_export_flag_defaults_off_and_is_filterable` | LOGIC | KEEP |  |
| 665 | `TestVocabLookups::test_rich_export_flag_can_be_unset` | LOGIC | KEEP |  |
| 674 | `TestUsageTracking::test_log_and_summarize` | LOGIC | KEEP |  |
| 682 | `TestUsageTracking::test_library_wide_total_sums_all_dramas` | LOGIC | KEEP |  |
| 690 | `TestUsageTracking::test_no_usage_returns_zeros_not_none` | LOGIC | KEEP |  |
| 701 | `TestCacheReadsAndMonthSpend::test_cache_read_tokens_are_summed` | LOGIC | KEEP |  |
| 710 | `TestCacheReadsAndMonthSpend::test_month_spend_counts_only_the_current_month` | LOGIC | KEEP |  |
| 724 | `TestCacheReadsAndMonthSpend::test_the_cache_column_is_added_to_an_older_database` | LOGIC | KEEP |  |
| 738 | `TestUsageByDrama::test_includes_translation_engine_and_call_count` | LOGIC | KEEP |  |
| 747 | `TestUsageByDrama::test_a_drama_with_no_usage_has_zero_call_count` | LOGIC | KEEP |  |
| 759 | `TestConsistencyIssuesPersist::test_save_and_load` | LOGIC | KEEP |  |
| 769 | `TestConsistencyIssuesPersist::test_a_later_run_replaces_the_previous_one` | LOGIC | KEEP |  |
| 776 | `TestConsistencyIssuesPersist::test_no_issues_saved_yet_returns_empty_list` | LOGIC | KEEP |  |
| 788 | `TestEmotionsPersist::test_save_and_load` | LOGIC | KEEP |  |
| 800 | `TestEmotionsPersist::test_re_running_updates_existing_lines_rather_than_duplicating` | LOGIC | KEEP |  |
| 809 | `TestEmotionsPersist::test_no_emotions_saved_yet_returns_empty_dict` | LOGIC | KEEP |  |
| 815 | `TestLibraryStatsAndSearch::test_get_library_stats_counts_dramas` | LOGIC | KEEP |  |
| 822 | `TestLibraryStatsAndSearch::test_recently_active_orders_by_updated_at` | LOGIC | KEEP |  |
| 829 | `TestLibraryStatsAndSearch::test_global_search_finds_across_dramas` | LOGIC | KEEP |  |
| 836 | `TestLibraryStatsAndSearch::test_global_search_no_match_returns_empty` | LOGIC | KEEP |  |
| 843 | `TestKnownTitles::test_create_and_list` | LOGIC | KEEP |  |
| 849 | `TestKnownTitles::test_filter_by_source_name` | LOGIC | KEEP |  |
| 856 | `TestKnownTitles::test_delete_known_title` | LOGIC | KEEP |  |
| 864 | `TestPagesAndBubbles::test_create_page_and_list` | LOGIC | KEEP |  |
| 871 | `TestPagesAndBubbles::test_save_and_load_bubbles` | LOGIC | KEEP |  |
| 882 | `TestPagesAndBubbles::test_update_page_rendered_filename` | LOGIC | KEEP |  |
| 889 | `TestPagesAndBubbles::test_list_bubbles_for_drama_spans_every_page` | LOGIC | KEEP |  |
| 904 | `TestPagesAndBubbles::test_list_bubbles_for_drama_ignores_other_dramas` | LOGIC | KEEP |  |
| 915 | `TestPagesAndBubbles::test_update_bubble_text_only_changes_translated_text` | LOGIC | KEEP |  |
| 935 | `TestPagesAndBubbles::test_update_bubble_text_does_not_touch_other_bubbles` | LOGIC | KEEP |  |
| 952 | `TestPagesAndBubbles::test_font_category_round_trips` | LOGIC | KEEP |  |
| 962 | `TestPagesAndBubbles::test_font_category_defaults_to_regular_when_omitted` | LOGIC | KEEP |  |
| 974 | `TestBenchmarkCasesAndRuns::test_configure_library_dir_redirects_benchmark_dir_too` | LOGIC | KEEP |  |
| 983 | `TestBenchmarkCasesAndRuns::test_create_and_list_a_case` | LOGIC | KEEP |  |
| 992 | `TestBenchmarkCasesAndRuns::test_list_cases_filters_by_stage` | LOGIC | KEEP |  |
| 999 | `TestBenchmarkCasesAndRuns::test_delete_a_case` | LOGIC | KEEP |  |
| 1004 | `TestBenchmarkCasesAndRuns::test_save_and_list_runs_for_a_case` | LOGIC | KEEP |  |
| 1014 | `TestBenchmarkCasesAndRuns::test_deleting_a_case_cascades_to_its_runs` | LOGIC | KEEP |  |
| 1021 | `TestBenchmarkCasesAndRuns::test_latest_run_per_case_returns_only_the_most_recent` | LOGIC | KEEP |  |
| 1030 | `TestBenchmarkCasesAndRuns::test_latest_run_per_case_filters_by_stage` | LOGIC | KEEP |  |
| 1040 | `TestBenchmarkCasesAndRuns::test_cost_defaults_to_zero_when_omitted` | LOGIC | KEEP |  |
| 1059 | `TestConnectionLeakRecovery::test_healthy_calls_leave_no_open_connections` | LOGIC | KEEP |  |
| 1066 | `TestConnectionLeakRecovery::test_write_succeeds_after_a_failed_write` | LOGIC | KEEP |  |
| 1075 | `TestConnectionLeakRecovery::test_recovers_from_repeated_failures` | LOGIC | KEEP |  |
| 1085 | `TestConnectionLeakRecovery::test_a_failed_write_no_longer_leaks_a_connection` | LOGIC | KEEP |  |
| 1103 | `TestFullLibraryReset::test_wipes_dramas_series_and_files` | LOGIC | KEEP |  |
| 1117 | `TestFullLibraryReset::test_schema_is_immediately_usable_after_reset` | LOGIC | KEEP |  |
| 1122 | `TestFullLibraryReset::test_dramas_directory_exists_but_empty_after_reset` | LOGIC | KEEP |  |
| 1128 | `TestFullLibraryReset::test_reset_on_an_already_empty_library_does_not_crash` | LOGIC | KEEP |  |
| 1132 | `TestFullLibraryReset::test_wipes_the_cached_cedict_dictionary_too` | LOGIC | KEEP |  |
| 1141 | `TestFullLibraryReset::test_reset_without_a_cedict_file_present_does_not_crash` | LOGIC | KEEP |  |
| 1155 | `TestSnapshotDatabase::test_snapshot_is_written_to_disk` | LOGIC | KEEP |  |
| 1164 | `TestSnapshotDatabase::test_snapshot_includes_a_write_made_just_before_it` | LOGIC | KEEP |  |
| 1177 | `TestSnapshotDatabase::test_snapshot_is_independent_of_the_live_database` | LOGIC | KEEP |  |
| 1199 | `TestTranslateHistory::test_save_and_list_roundtrips_every_field` | LOGIC | KEEP |  |
| 1211 | `TestTranslateHistory::test_list_is_most_recent_first` | LOGIC | KEEP |  |
| 1217 | `TestTranslateHistory::test_list_respects_limit` | LOGIC | KEEP |  |
| 1222 | `TestTranslateHistory::test_clear_removes_every_row` | LOGIC | KEEP |  |
| 1243 | `TestVoiceBank::test_save_copies_the_clip_not_a_reference` | LOGIC | KEEP |  |
| 1268 | `TestVoiceBank::test_list_is_sorted_by_name` | LOGIC | KEEP |  |
| 1273 | `TestVoiceBank::test_apply_copies_clip_into_the_new_drama_and_sets_clone_fields` | LOGIC | KEEP |  |
| 1294 | `TestVoiceBank::test_apply_writes_a_separate_copy_not_shared_with_the_bank_or_other_dramas` | LOGIC | KEEP |  |
| 1314 | `TestVoiceBank::test_deleting_the_source_drama_leaves_the_bank_entrys_clip_intact` | LOGIC | KEEP |  |
| 1330 | `TestVoiceBank::test_rename_changes_only_the_name` | LOGIC | KEEP |  |
| 1339 | `TestVoiceBank::test_delete_removes_the_entry_and_its_clip_file` | LOGIC | KEEP |  |
| 1349 | `TestVoiceBank::test_delete_only_removes_that_one_entry` | LOGIC | KEEP |  |
| 1355 | `TestVoiceBank::test_apply_raises_a_clear_error_for_an_unknown_entry_id` | LOGIC | KEEP |  |
| 1369 | `TestGpuLock::test_free_lock_is_acquired` | LOGIC | KEEP |  |
| 1375 | `TestGpuLock::test_held_lock_refuses_a_different_holder` | LOGIC | KEEP |  |
| 1383 | `TestGpuLock::test_same_holder_can_reacquire_its_own_lock` | LOGIC | KEEP |  |
| 1387 | `TestGpuLock::test_release_frees_it_for_someone_else` | LOGIC | KEEP |  |
| 1393 | `TestGpuLock::test_releasing_the_wrong_holder_is_a_no_op` | LOGIC | KEEP |  |
| 1402 | `TestGpuLock::test_stale_lock_is_taken_over` | LOGIC | KEEP |  |
| 1415 | `TestGpuLock::test_heartbeat_keeps_a_long_running_holder_from_going_stale` | LOGIC | KEEP |  |
| 1426 | `TestGpuLock::test_status_is_free_when_nothing_has_ever_held_it` | LOGIC | KEEP |  |
| 1448 | `TestImportTimeSafety::test_bare_import_does_not_touch_any_library_dir` | LOGIC | KEEP |  |
| 1463 | `TestImportTimeSafety::test_first_real_call_still_initializes_the_schema` | LOGIC | KEEP |  |
| 1483 | `TestImportTimeSafety::test_isolated_db_fixture_still_initializes_a_working_schema` | LOGIC | KEEP |  |
| 1489 | `TestImportTimeSafety::test_configure_library_dir_reinitializes_at_the_new_path` | LOGIC | KEEP |  |
| 1509 | `TestLeakedConnectionCleanup::test_leaked_connection_from_a_dead_background_thread_is_genuinely_closed` | LOGIC | KEEP |  |
| 1551 | `TestLeakedConnectionCleanup::test_a_still_running_threads_own_connection_is_never_closed_by_another_thread` | UI | DELETE with tab | AppTest |
| 1591 | `TestLeakedConnectionCleanup::test_leaked_connection_close_failure_is_logged_not_silently_discarded` | LOGIC | KEEP |  |
| 1685 | `TestStep26eProfilesMigration::test_upgrading_a_pre_profiles_install_preserves_its_data` | LOGIC | KEEP |  |
| 1708 | `TestStep26eProfilesMigration::test_running_init_db_twice_does_not_duplicate_the_default_profile` | LOGIC | KEEP |  |
| 1731 | `TestSafeAlterGuardsInitDbAgainstACrossProcessRace::test_duplicate_column_is_swallowed` | LOGIC | KEEP |  |
| 1743 | `TestSafeAlterGuardsInitDbAgainstACrossProcessRace::test_other_operational_errors_still_raise` | LOGIC | KEEP |  |
| 1750 | `TestSafeAlterGuardsInitDbAgainstACrossProcessRace::test_init_db_still_adds_a_genuinely_missing_column` | LOGIC | KEEP |  |
| 1766 | `TestJobRecords::test_save_and_get_a_job_record` | LOGIC | KEEP |  |
| 1777 | `TestJobRecords::test_saving_again_updates_in_place_not_a_second_row` | LOGIC | KEEP |  |
| 1785 | `TestJobRecords::test_get_missing_record_is_none` | LOGIC | KEEP |  |
| 1788 | `TestJobRecords::test_delete_job_record` | LOGIC | KEEP |  |
| 1793 | `TestJobRecords::test_clear_all_job_records` | LOGIC | KEEP |  |
| 1799 | `TestJobRecords::test_list_job_records_orders_newest_started_first` | LOGIC | KEEP |  |
| 1811 | `TestAppSettings::test_missing_key_returns_the_given_default` | LOGIC | KEEP |  |
| 1815 | `TestAppSettings::test_set_then_get_round_trips` | LOGIC | KEEP |  |
| 1819 | `TestAppSettings::test_setting_again_updates_in_place_not_a_second_row` | LOGIC | KEEP |  |
| 1828 | `TestAppSettings::test_value_types_round_trip_via_json` | LOGIC | KEEP |  |

### `tests/test_diagnostics_and_export.py` (122 tests: 112 LOGIC, 10 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 25 | `TestDiagnostics::test_python_version_check_returns_version_string` | LOGIC | KEEP |  |
| 30 | `TestDiagnostics::test_ffmpeg_check_returns_expected_shape` | LOGIC | KEEP |  |
| 35 | `TestDiagnostics::test_js_runtime_check_returns_expected_shape` | LOGIC | KEEP |  |
| 40 | `TestDiagnostics::test_js_runtime_finds_deno_on_path` | LOGIC | KEEP |  |
| 46 | `TestDiagnostics::test_js_runtime_falls_through_to_a_later_candidate` | LOGIC | KEEP |  |
| 55 | `TestDiagnostics::test_js_runtime_reports_missing_when_none_found` | LOGIC | KEEP |  |
| 59 | `TestDiagnostics::test_check_dependency_true_for_stdlib_backed_package` | LOGIC | KEEP |  |
| 63 | `TestDiagnostics::test_check_dependency_false_for_nonexistent` | LOGIC | KEEP |  |
| 66 | `TestDiagnostics::test_check_all_dependencies_covers_every_listed_package` | LOGIC | KEEP |  |
| 74 | `TestDiagnostics::test_step_6_optional_dependencies_are_registered` | LOGIC | KEEP |  |
| 83 | `TestDiagnostics::test_media_only_deps_are_not_tagged_required` | LOGIC | KEEP |  |
| 94 | `TestDiagnostics::test_file_completeness_detects_all_present_in_real_project` | LOGIC | KEEP |  |
| 99 | `TestDiagnostics::test_expected_top_level_files_list_is_not_stale` | LOGIC | KEEP |  |
| 112 | `TestDiagnostics::test_expected_tabs_files_list_is_not_stale` | LOGIC | UPDATE at deletion | diagnostics.EXPECTED_TABS_FILES (and check_file_completeness's tabs check) must be emptied/removed with tabs/ |
| 118 | `TestDiagnostics::test_file_completeness_reports_missing_in_empty_dir` | LOGIC | KEEP |  |
| 124 | `TestDiagnostics::test_library_writable_true_for_temp_dir` | LOGIC | KEEP |  |
| 127 | `TestDiagnostics::test_run_full_diagnostics_returns_all_sections` | LOGIC | KEEP |  |
| 136 | `TestLineHistory::test_snapshot_and_restore_roundtrip` | LOGIC | KEEP |  |
| 150 | `TestLineHistory::test_snapshot_preserves_speaker_and_dub_filename` | LOGIC | KEEP |  |
| 160 | `TestLineHistory::test_pruning_keeps_only_most_recent` | LOGIC | KEEP |  |
| 168 | `TestLineHistory::test_history_ordered_newest_first` | LOGIC | KEEP |  |
| 176 | `TestLineHistory::test_nonexistent_snapshot_returns_none` | LOGIC | KEEP |  |
| 179 | `TestLineHistory::test_history_deleted_with_drama` | LOGIC | KEEP |  |
| 188 | `TestExportPackage::test_full_package_contains_all_components` | LOGIC | KEEP |  |
| 212 | `TestExportPackage::test_overlapping_cues_are_clamped_before_export` | LOGIC | KEEP |  |
| 229 | `TestExportPackage::test_metadata_includes_drama_and_characters` | LOGIC | KEEP |  |
| 240 | `TestExportPackage::test_incomplete_drama_still_produces_valid_zip` | LOGIC | KEEP |  |
| 249 | `TestExportPackage::test_nonexistent_drama_raises_value_error` | LOGIC | KEEP |  |
| 255 | `TestExportPackage::test_manifest_matches_zip_contents` | LOGIC | KEEP |  |
| 289 | `TestBenchmarkRunnerEnginePassesOllamaUrlAndFreeTier::test_ollama_base_url_is_passed_through` | UI | DELETE with tab | AppTest |
| 315 | `TestDescribeJob::test_live_capture_has_a_fixed_label` | LOGIC | NEEDS EXTRACTION | tabs.diagnostics_tab._describe_job -> services/jobs_service if React wants readable job labels; else drop |
| 319 | `TestDescribeJob::test_known_prefix_includes_the_drama_title` | LOGIC | NEEDS EXTRACTION | tabs.diagnostics_tab._describe_job -> services/jobs_service if React wants readable job labels; else drop |
| 326 | `TestDescribeJob::test_deleted_drama_says_so_instead_of_crashing` | LOGIC | NEEDS EXTRACTION | tabs.diagnostics_tab._describe_job -> services/jobs_service if React wants readable job labels; else drop |
| 331 | `TestDescribeJob::test_unrecognized_job_id_falls_back_to_the_raw_string` | LOGIC | NEEDS EXTRACTION | tabs.diagnostics_tab._describe_job -> services/jobs_service if React wants readable job labels; else drop |
| 352 | `TestRunningJobsPanelAutoRefresh::test_panel_is_an_auto_refreshing_fragment` | UI | DELETE with tab | tab source inspection |
| 366 | `TestRunningJobsPanelAutoRefresh::test_shows_nothing_running_caption_when_idle` | UI | DELETE with tab | tab source inspection |
| 373 | `TestRunningJobsPanelAutoRefresh::test_shows_a_real_running_job_with_progress_and_cancel` | UI | DELETE with tab | tab source inspection |
| 394 | `TestRunningJobsPanelAutoRefresh::test_cancel_button_requests_cancellation` | UI | DELETE with tab | tab source inspection |
| 454 | `TestHfCacheScanAndDelete::test_lists_entries_with_real_sizes` | LOGIC | KEEP |  |
| 475 | `TestHfCacheScanAndDelete::test_lists_multiple_repos_largest_first` | LOGIC | KEEP |  |
| 484 | `TestHfCacheScanAndDelete::test_deleting_a_revision_actually_frees_the_space` | LOGIC | KEEP |  |
| 495 | `TestHfCacheScanAndDelete::test_empty_or_missing_cache_dir_is_not_an_error` | LOGIC | KEEP |  |
| 499 | `TestHfCacheScanAndDelete::test_delete_of_an_unknown_revision_fails_cleanly` | LOGIC | KEEP |  |
| 503 | `TestHfCacheScanAndDelete::test_huggingface_hub_not_installed_returns_empty_not_raised` | LOGIC | KEEP |  |
| 530 | `TestPiperVoiceScanAndDelete::test_lists_voices_with_real_sizes_largest_first` | LOGIC | KEEP |  |
| 538 | `TestPiperVoiceScanAndDelete::test_empty_when_the_directory_does_not_exist_yet` | LOGIC | KEEP |  |
| 542 | `TestPiperVoiceScanAndDelete::test_only_onnx_files_are_counted_as_voices` | LOGIC | KEEP |  |
| 549 | `TestPiperVoiceScanAndDelete::test_delete_removes_both_the_model_and_its_config` | LOGIC | KEEP |  |
| 555 | `TestPiperVoiceScanAndDelete::test_delete_of_an_unknown_voice_fails_cleanly` | LOGIC | KEEP |  |
| 575 | `TestPiperVoicesPanelUI::test_shows_a_downloaded_piper_voice` | UI | DELETE with tab | AppTest |
| 585 | `TestPiperVoicesPanelUI::test_nothing_shown_when_no_piper_voices_downloaded` | UI | DELETE with tab | AppTest |
| 612 | `TestBugBundleDeleteNeedsConfirmation::test_delete_bundle_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 620 | `TestBugBundleDeleteNeedsConfirmation::test_delete_bundle_removes_it_once_confirmed` | UI | DELETE with tab | AppTest |
| 631 | `TestModelEngineVersions::test_lists_every_registered_backend_with_a_version_or_repo_id` | LOGIC | KEEP |  |
| 640 | `TestModelEngineVersions::test_repo_entry_shows_the_actual_diarization_model_ids` | LOGIC | KEEP |  |
| 647 | `TestModelEngineVersions::test_an_installed_package_shows_a_real_version_string` | LOGIC | KEEP |  |
| 656 | `TestModelEngineVersions::test_step_11b_voice_engines_have_rows` | LOGIC | KEEP |  |
| 663 | `TestModelEngineVersions::test_step_11b_pip_engines_are_registered_dependencies` | LOGIC | KEEP |  |
| 670 | `TestModelEngineVersions::test_ollama_tag_appended_only_when_given` | LOGIC | KEEP |  |
| 676 | `TestModelEngineVersions::test_makes_no_network_call` | LOGIC | KEEP |  |
| 683 | `TestModelEngineVersions::test_installed_is_a_real_boolean_not_a_string_match` | LOGIC | KEEP |  |
| 696 | `TestModelEngineVersions::test_ollama_row_is_installed` | LOGIC | KEEP |  |
| 706 | `TestGpuStatus::test_unavailable_when_torch_not_installed` | LOGIC | KEEP |  |
| 712 | `TestGpuStatus::test_unavailable_no_error_when_no_gpu_present` | LOGIC | KEEP |  |
| 724 | `TestGpuStatus::test_names_the_real_mismatch_when_gpu_present_but_torch_is_cpu_only` | LOGIC | KEEP |  |
| 736 | `TestGpuStatus::test_available_reports_real_name_and_vram` | LOGIC | KEEP |  |
| 754 | `TestGpuStatus::test_broken_torch_import_does_not_crash` | LOGIC | KEEP |  |
| 767 | `TestStreamDependencyInstall::test_torch_with_gpu_present_uses_the_gpu_aware_reinstall` | LOGIC | KEEP |  |
| 784 | `TestStreamDependencyInstall::test_torch_with_no_gpu_uses_a_plain_install` | LOGIC | KEEP |  |
| 796 | `TestStreamDependencyInstall::test_other_dependencies_always_use_a_plain_install` | LOGIC | KEEP |  |
| 821 | `TestPyannoteGatedAccessCheck::test_accessible_and_gated_models_both_reported` | LOGIC | KEEP |  |
| 832 | `TestPyannoteGatedAccessCheck::test_hf_token_is_passed_through` | LOGIC | KEEP |  |
| 837 | `TestPyannoteGatedAccessCheck::test_huggingface_hub_not_installed_returns_empty` | LOGIC | KEEP |  |
| 849 | `TestCheckEngineReachable::test_a_working_engine_reports_ok` | LOGIC | KEEP |  |
| 853 | `TestCheckEngineReachable::test_unknown_engine_name_reports_failure_not_a_crash` | LOGIC | KEEP |  |
| 859 | `TestCheckEngineReachable::test_a_translate_call_that_raises_is_reported_as_failure` | LOGIC | KEEP |  |
| 869 | `TestCheckEngineReachable::test_an_empty_translation_is_reported_as_failure_not_silently_ok` | LOGIC | KEEP |  |
| 876 | `TestCheckEngineReachable::test_doctor_report_checks_a_batch_of_engines` | LOGIC | KEEP |  |
| 893 | `TestDependencyVersionCheck::test_get_installed_version_returns_none_for_unknown_distribution` | LOGIC | KEEP |  |
| 896 | `TestDependencyVersionCheck::test_get_installed_version_returns_a_real_version_for_something_installed` | LOGIC | KEEP |  |
| 901 | `TestDependencyVersionCheck::test_get_latest_pypi_version_parses_a_successful_response` | LOGIC | KEEP |  |
| 915 | `TestDependencyVersionCheck::test_get_latest_pypi_version_returns_none_on_404` | LOGIC | KEEP |  |
| 921 | `TestDependencyVersionCheck::test_get_latest_pypi_version_returns_none_on_network_error` | LOGIC | KEEP |  |
| 927 | `TestDependencyVersionCheck::test_check_dependency_versions_flags_an_outdated_package` | LOGIC | KEEP |  |
| 936 | `TestDependencyVersionCheck::test_check_dependency_versions_flags_an_up_to_date_package` | LOGIC | KEEP |  |
| 944 | `TestDependencyVersionCheck::test_check_dependency_versions_compares_numerically_not_lexically` | LOGIC | KEEP |  |
| 952 | `TestDependencyVersionCheck::test_check_dependency_versions_skips_packages_that_arent_installed` | LOGIC | KEEP |  |
| 959 | `TestDependencyVersionCheck::test_check_dependency_versions_outdated_is_none_when_latest_cant_be_determined` | LOGIC | KEEP |  |
| 965 | `TestDependencyVersionCheck::test_no_network_call_unless_the_check_is_explicitly_run` | LOGIC | KEEP |  |
| 976 | `TestUpgradePipArgs::test_adds_the_upgrade_flag_and_package_name` | LOGIC | KEEP |  |
| 980 | `TestUpgradePipArgs::test_adds_constraints_when_the_file_exists` | LOGIC | KEEP |  |
| 986 | `TestUpgradePipArgs::test_no_constraints_flag_when_the_file_is_missing` | LOGIC | KEEP |  |
| 996 | `TestRedundantTtsInstallWarning::test_none_for_a_package_outside_the_group` | LOGIC | KEEP |  |
| 999 | `TestRedundantTtsInstallWarning::test_none_when_nothing_else_in_the_group_is_installed` | LOGIC | KEEP |  |
| 1003 | `TestRedundantTtsInstallWarning::test_warns_when_a_group_sibling_is_already_installed` | LOGIC | KEEP |  |
| 1010 | `TestRedundantTtsInstallWarning::test_never_warns_against_itself` | LOGIC | KEEP |  |
| 1015 | `TestRedundantTtsInstallWarning::test_names_every_sibling_already_installed_not_just_one` | LOGIC | KEEP |  |
| 1020 | `TestRedundantTtsInstallWarning::test_underscore_and_hyphen_spellings_are_treated_the_same` | LOGIC | KEEP |  |
| 1035 | `TestUpgradeBlockedReason::test_none_for_an_ordinary_package_with_no_known_limitation` | LOGIC | KEEP |  |
| 1039 | `TestUpgradeBlockedReason::test_known_python_314_limitation_only_applies_on_python_314` | LOGIC | KEEP |  |
| 1047 | `TestUpgradeBlockedReason::test_known_limitation_does_not_apply_on_a_different_python_version` | LOGIC | KEEP |  |
| 1052 | `TestUpgradeBlockedReason::test_constraints_cap_explains_why_when_latest_exceeds_it` | LOGIC | KEEP |  |
| 1060 | `TestUpgradeBlockedReason::test_no_reason_when_latest_is_still_within_the_cap` | LOGIC | KEEP |  |
| 1065 | `TestUpgradeBlockedReason::test_no_reason_when_constraints_file_is_missing` | LOGIC | KEEP |  |
| 1077 | `TestKnownInstallLimitationReason::test_none_for_an_ordinary_package` | LOGIC | KEEP |  |
| 1080 | `TestKnownInstallLimitationReason::test_known_limitation_on_the_affected_python_version` | LOGIC | KEEP |  |
| 1086 | `TestKnownInstallLimitationReason::test_underscore_and_hyphen_names_are_equivalent` | LOGIC | KEEP |  |
| 1091 | `TestKnownInstallLimitationReason::test_does_not_apply_on_a_different_python_version` | LOGIC | KEEP |  |
| 1097 | `TestRedactForSupport::test_strips_api_keys` | LOGIC | KEEP |  |
| 1101 | `TestRedactForSupport::test_strips_the_os_username` | LOGIC | KEEP |  |
| 1106 | `TestRedactForSupport::test_collapses_posix_paths_to_the_last_segment` | LOGIC | KEEP |  |
| 1111 | `TestRedactForSupport::test_collapses_windows_paths_to_the_last_segment` | LOGIC | KEEP |  |
| 1116 | `TestRedactForSupport::test_empty_text_is_safe` | LOGIC | KEEP |  |
| 1136 | `TestFormatDiagnosticsReport::test_report_mentions_missing_dependency_and_key_state` | LOGIC | KEEP |  |
| 1141 | `TestFormatDiagnosticsReport::test_includes_hf_cache_and_model_versions_when_given` | LOGIC | KEEP |  |
| 1150 | `TestFormatDiagnosticsReport::test_omits_cache_and_versions_sections_when_not_given` | LOGIC | KEEP |  |
| 1155 | `TestFormatDiagnosticsReport::test_report_content_survives_redaction_intact_apart_from_secrets` | LOGIC | KEEP |  |
| 1179 | `TestDiagnosticsTabSupportReport::test_report_appears_with_no_path_or_username` | UI | DELETE with tab | AppTest |
| 1202 | `TestCheckCuda::test_torch_not_installed` | LOGIC | KEEP |  |
| 1206 | `TestCheckCuda::test_torch_installed_cuda_available` | LOGIC | KEEP |  |
| 1212 | `TestCheckCuda::test_torch_installed_no_cuda` | LOGIC | KEEP |  |
| 1218 | `TestCheckCuda::test_torch_installed_but_broken_import_does_not_crash` | LOGIC | KEEP |  |

### `tests/test_diagnostics_regrouping.py` (19 tests: 0 LOGIC, 19 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 38 | `TestSectionsAreAllCollapsible::test_every_top_level_section_is_an_expander` | UI | DELETE with tab | AppTest |
| 44 | `TestSectionsAreAllCollapsible::test_routine_sections_open_by_default` | UI | DELETE with tab | AppTest |
| 50 | `TestSectionsAreAllCollapsible::test_everything_else_collapsed_by_default` | UI | DELETE with tab | AppTest |
| 56 | `TestSectionsAreAllCollapsible::test_danger_zone_reset_button_not_expanded_by_default` | UI | DELETE with tab | AppTest |
| 67 | `TestCheckMySetupWiredDirectlyUnderItsHeader::test_run_diagnostics_button_lives_inside_check_my_setup` | UI | DELETE with tab | AppTest |
| 72 | `TestCheckMySetupWiredDirectlyUnderItsHeader::test_results_render_inside_the_same_expander_once_run` | UI | DELETE with tab | AppTest |
| 82 | `TestModelEngineVersionsTable::test_table_shows_a_clickable_name_link_and_an_install_icon` | UI | DELETE with tab | AppTest |
| 92 | `TestModelEngineVersionsTable::test_not_installed_row_shows_the_muted_icon_not_installed_text` | UI | DELETE with tab | AppTest |
| 112 | `TestModelEngineVersionsInstallAndHelp::test_install_button_appears_for_a_not_installed_package_row` | UI | DELETE with tab | AppTest |
| 124 | `TestModelEngineVersionsInstallAndHelp::test_no_install_button_for_a_repo_or_service_row` | UI | DELETE with tab | AppTest |
| 134 | `TestModelEngineVersionsInstallAndHelp::test_no_install_button_when_package_name_is_missing` | UI | DELETE with tab | AppTest |
| 147 | `TestModelEngineVersionsInstallAndHelp::test_clicking_install_calls_stream_dependency_install_with_the_registry_package_name` | UI | DELETE with tab | AppTest |
| 166 | `TestModelEngineVersionsInstallAndHelp::test_help_popover_present_with_distinct_plain_english_text_per_row` | UI | DELETE with tab | AppTest |
| 190 | `TestGpuStatusDisplay::test_shows_a_clean_message_with_no_exception_when_gpu_unavailable` | UI | DELETE with tab | AppTest |
| 198 | `TestGpuStatusDisplay::test_shows_real_name_and_vram_when_available` | UI | DELETE with tab | AppTest |
| 211 | `TestLogKeywordFilter::test_filter_narrows_the_shown_log_lines` | UI | DELETE with tab | AppTest |
| 220 | `TestLogKeywordFilter::test_blank_filter_shows_every_line` | UI | DELETE with tab | AppTest |
| 228 | `TestLogKeywordFilter::test_no_match_says_so_rather_than_showing_nothing` | UI | DELETE with tab | AppTest |
| 238 | `TestTypeScaleScope::test_diagnostics_tab_opts_into_the_type_scale` | UI | DELETE with tab | tab source inspection |

### `tests/test_diagnostics_source_access.py` (3 tests: 0 LOGIC, 3 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 27 | `TestDiagnosticsSourceAccess::test_section_lists_a_source_with_no_prior_challenge` | UI | DELETE with tab | AppTest |
| 35 | `TestDiagnosticsSourceAccess::test_test_now_records_real_evidence_on_success` | UI | DELETE with tab | AppTest |
| 54 | `TestDiagnosticsSourceAccess::test_test_now_is_disabled_for_a_tos_prohibited_source` | UI | DELETE with tab | AppTest |

### `tests/test_discover_tab.py` (20 tests: 0 LOGIC, 20 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 74 | `TestBaihehubSearch::test_ollama_with_no_key_still_translates_query` | UI | DELETE with tab | AppTest |
| 83 | `TestBaihehubSearch::test_non_local_engine_without_key_falls_back_to_raw_query` | UI | DELETE with tab | AppTest |
| 92 | `TestBaihehubSearch::test_engine_with_key_translates_query` | UI | DELETE with tab | AppTest |
| 142 | `TestFindATitleTranslationCaching::test_translates_once_for_a_new_query` | UI | DELETE with tab | AppTest |
| 148 | `TestFindATitleTranslationCaching::test_unrelated_rerun_does_not_retranslate_the_same_query` | UI | DELETE with tab | AppTest |
| 161 | `TestFindATitleTranslationCaching::test_changing_the_query_translates_again` | UI | DELETE with tab | AppTest |
| 171 | `TestFindATitleTranslationCaching::test_uses_the_chosen_engine_not_a_hardcoded_claude_key` | UI | DELETE with tab | AppTest |
| 278 | `TestSiteNavigationHelperButton::test_runs_end_to_end` | UI | DELETE with tab | AppTest |
| 289 | `TestSiteNavigationHelperButton::test_passes_gemini_free_tier` | UI | DELETE with tab | AppTest |
| 294 | `TestSiteNavigationHelperButton::test_passes_ollama_url` | UI | DELETE with tab | AppTest |
| 300 | `TestSiteNavigationHelperButton::test_button_enabled_with_ollama_and_no_api_key` | UI | DELETE with tab | AppTest |
| 345 | `TestDiscoverConsolidation::test_only_one_engine_and_key_picker_on_the_page` | UI | DELETE with tab | AppTest |
| 352 | `TestDiscoverConsolidation::test_empty_library_shows_exactly_one_notice` | UI | DELETE with tab | AppTest |
| 359 | `TestDiscoverConsolidation::test_import_to_library_is_idempotent` | UI | DELETE with tab | AppTest |
| 384 | `TestBulkImportUrlPatternGenerator::test_generates_urls_into_the_bulk_urls_box` | UI | DELETE with tab | AppTest |
| 410 | `TestKeyGatingIsEngineAware::test_fetch_and_add_button_enabled_for_test_offline_with_no_key` | UI | DELETE with tab | AppTest |
| 426 | `TestKeyGatingIsEngineAware::test_fetch_and_add_button_disabled_for_real_engine_with_no_key` | UI | DELETE with tab | AppTest |
| 438 | `TestKeyGatingIsEngineAware::test_fetch_and_add_button_still_works_for_test_offline_with_key_present` | UI | DELETE with tab | AppTest |
| 460 | `TestKeyGatingIsEngineAware::test_bulk_extract_button_enabled_for_test_offline_with_no_key` | UI | DELETE with tab | AppTest |
| 474 | `TestKeyGatingIsEngineAware::test_bulk_extract_button_disabled_for_real_engine_with_no_key` | UI | DELETE with tab | AppTest |

### `tests/test_dub.py` (87 tests: 85 LOGIC, 2 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 141 | `TestEdgeTTSBlockedErrorHandling::test_a_403_error_is_wrapped_with_a_clear_message` | LOGIC | KEEP |  |
| 147 | `TestEdgeTTSBlockedErrorHandling::test_a_non_403_error_passes_through_unwrapped` | LOGIC | KEEP |  |
| 152 | `TestEdgeTTSBlockedErrorHandling::test_success_is_unaffected` | LOGIC | KEEP |  |
| 165 | `TestSynthesizeEdgeTTSWithPiperFallback::test_falls_back_to_piper_when_installed` | LOGIC | KEEP |  |
| 180 | `TestSynthesizeEdgeTTSWithPiperFallback::test_uses_the_speakers_configured_offline_voice_if_set` | LOGIC | KEEP |  |
| 197 | `TestSynthesizeEdgeTTSWithPiperFallback::test_reraises_the_blocked_error_when_piper_is_not_installed` | LOGIC | KEEP |  |
| 206 | `TestSynthesizeEdgeTTSWithPiperFallback::test_a_non_blocked_error_propagates_without_trying_piper` | LOGIC | KEEP |  |
| 213 | `TestSynthesizeEdgeTTSWithPiperFallback::test_reports_when_piper_actually_rendered_it` | LOGIC | KEEP |  |
| 227 | `TestSynthesizeEdgeTTSWithPiperFallback::test_reports_false_when_edge_tts_itself_succeeded` | LOGIC | KEEP |  |
| 235 | `TestAssignVoicesToCharacters::test_round_robins_through_the_pool` | LOGIC | KEEP |  |
| 239 | `TestAssignVoicesToCharacters::test_sorted_for_deterministic_assignment` | LOGIC | KEEP |  |
| 243 | `TestAssignVoicesToCharacters::test_defaults_to_the_default_voice_pool` | LOGIC | KEEP |  |
| 252 | `TestFillMissingVoices::test_unvoiced_speakers_get_distinct_voices` | LOGIC | KEEP |  |
| 256 | `TestFillMissingVoices::test_picked_voices_are_kept_and_not_reused_while_the_pool_allows` | LOGIC | KEEP |  |
| 260 | `TestFillMissingVoices::test_falls_back_to_the_whole_pool_once_it_is_used_up` | LOGIC | KEEP |  |
| 264 | `TestFillMissingVoices::test_workspace_dub_button_uses_it_for_both_engines` | UI | DELETE with tab | tab source inspection (dub_service carries the same call) |
| 282 | `TestBuildDubTrackClonePriority::test_uses_f5tts_cloning_when_a_clone_ref_is_set` | LOGIC | KEEP |  |
| 299 | `TestBuildDubTrackClonePriority::test_falls_back_to_plain_tts_with_no_clone_ref` | LOGIC | KEEP |  |
| 311 | `TestBuildDubTrackClonePriority::test_a_failed_line_does_not_lose_other_lines_audio` | LOGIC | KEEP |  |
| 332 | `TestBuildDubTrackClonePriority::test_lines_with_no_translation_are_skipped_silently` | LOGIC | KEEP |  |
| 344 | `TestBuildDubTrackClonePriority::test_already_generated_clips_are_reused_not_resynthesized` | LOGIC | KEEP |  |
| 362 | `TestBuildNarrationTrack::test_concatenates_clips_with_a_gap_and_sets_line_timing` | LOGIC | KEEP |  |
| 381 | `TestBuildNarrationTrack::test_a_blank_line_advances_the_cursor_by_nothing_and_records_a_point_in_time` | LOGIC | KEEP |  |
| 387 | `TestBuildNarrationTrack::test_a_failed_line_still_advances_the_timeline_with_a_silent_gap` | LOGIC | KEEP |  |
| 403 | `TestNarrateOriginalLanguage::test_original_mode_speaks_source_text_not_translation` | LOGIC | KEEP |  |
| 408 | `TestNarrateOriginalLanguage::test_translation_mode_still_speaks_the_translation_by_default` | LOGIC | KEEP |  |
| 413 | `TestNarrateOriginalLanguage::test_original_mode_generates_audio_with_no_translation_at_all` | LOGIC | KEEP | only a docstring mentions the tab |
| 423 | `TestNarrateOriginalLanguage::test_a_line_with_no_source_text_is_still_treated_as_blank_in_original_mode` | LOGIC | KEEP |  |
| 429 | `TestNarrateOriginalLanguage::test_switching_narration_language_changes_the_clip_cache_signature_even_with_identical_text` | LOGIC | KEEP |  |
| 447 | `TestNarrateOriginalLanguage::test_original_mode_splits_unit_timing_by_the_source_text_length` | LOGIC | KEEP |  |
| 464 | `TestNarrateOriginalLanguage::test_original_mode_reaches_gpt_sovits_with_the_drama_source_language` | LOGIC | KEEP |  |
| 476 | `TestNarrateOriginalLanguage::test_translation_mode_still_reaches_gpt_sovits_with_english` | LOGIC | KEEP |  |
| 487 | `TestNarrateOriginalLanguage::test_original_mode_narration_lines_still_export_bilingual_subtitles` | LOGIC | KEEP |  |
| 497 | `TestNarrationChaptersOriginalLanguage::test_original_mode_uses_source_text_for_titles_and_voiced_filter` | LOGIC | KEEP |  |
| 503 | `TestNarrationChaptersOriginalLanguage::test_translation_mode_is_unaffected` | LOGIC | KEEP |  |
| 513 | `TestGPTSoVITSTextLang::test_defaults_to_english` | LOGIC | KEEP |  |
| 519 | `TestGPTSoVITSTextLang::test_an_explicit_text_lang_is_mapped_to_gpt_sovits_own_codes` | LOGIC | KEEP |  |
| 526 | `TestGPTSoVITSTextLang::test_an_unrecognized_text_lang_falls_back_to_english` | LOGIC | KEEP |  |
| 533 | `TestGPTSoVITSTextLang::test_synthesize_cloned_threads_text_lang_through_without_touching_ref_language` | LOGIC | KEEP |  |
| 542 | `TestGPTSoVITSTextLang::test_synthesize_cloned_defaults_text_lang_to_english` | LOGIC | KEEP |  |
| 557 | `TestCloneEngineOriginalLanguages::test_omnivoice_supports_all_three` | LOGIC | KEEP |  |
| 561 | `TestCloneEngineOriginalLanguages::test_tada_supports_zh_and_ja_but_not_ko` | LOGIC | KEEP |  |
| 566 | `TestCloneEngineOriginalLanguages::test_chatterbox_is_not_confirmed_for_any_of_them` | LOGIC | KEEP |  |
| 570 | `TestCloneEngineOriginalLanguages::test_engines_outside_the_table_are_unrestricted` | LOGIC | KEEP |  |
| 578 | `TestExtractReferenceClips::test_picks_the_clip_closest_to_six_seconds_per_speaker` | LOGIC | KEEP |  |
| 590 | `TestExtractReferenceClips::test_ignores_clips_outside_the_duration_bounds` | LOGIC | KEEP |  |
| 599 | `TestExtractReferenceClips::test_each_speaker_gets_their_own_best_clip` | LOGIC | KEEP |  |
| 609 | `TestExtractReferenceClips::test_reports_a_specific_reason_for_a_speaker_with_no_eligible_segment` | LOGIC | KEEP |  |
| 620 | `TestExtractReferenceClips::test_reports_too_long_when_the_closest_segment_exceeds_the_max` | LOGIC | KEEP |  |
| 642 | `TestBuildTrackSubprocessWorker::test_matches_a_direct_dub_track_call_on_success` | LOGIC | KEEP |  |
| 662 | `TestBuildTrackSubprocessWorker::test_is_narration_true_routes_to_build_narration_track` | LOGIC | KEEP |  |
| 679 | `TestBuildTrackSubprocessWorker::test_reports_an_exception_instead_of_raising` | LOGIC | KEEP |  |
| 748 | `TestStretchForWindow::test_within_the_tolerance_of_one_is_left_alone` | LOGIC | KEEP |  |
| 752 | `TestStretchForWindow::test_a_modest_overflow_is_sped_up_to_fit` | LOGIC | KEEP |  |
| 755 | `TestStretchForWindow::test_past_the_cap_it_gets_the_cap_and_overflows` | LOGIC | KEEP |  |
| 758 | `TestStretchForWindow::test_a_short_clip_is_slowed_no_further_than_the_floor` | LOGIC | KEEP |  |
| 762 | `TestStretchForWindow::test_the_clamp_is_configurable` | LOGIC | KEEP |  |
| 766 | `TestStretchForWindow::test_a_zero_length_window_never_divides_by_zero` | LOGIC | KEEP |  |
| 769 | `TestStretchForWindow::test_each_state_has_its_indicator` | LOGIC | KEEP |  |
| 775 | `TestTimeStretch::test_runs_a_pitch_preserving_atempo_and_leaves_no_partial_file` | LOGIC | KEEP |  |
| 788 | `TestTimeStretch::test_a_failed_stretch_leaves_nothing_behind_for_the_next_run` | LOGIC | KEEP |  |
| 802 | `TestDubTrackTimeStretch::test_a_modest_overflow_is_sped_up_by_a_clamped_factor` | LOGIC | KEEP |  |
| 810 | `TestDubTrackTimeStretch::test_a_clip_that_already_fits_is_left_untouched` | LOGIC | KEEP |  |
| 817 | `TestDubTrackTimeStretch::test_past_the_cap_it_is_capped_and_overflows_not_crushed` | LOGIC | KEEP |  |
| 825 | `TestDubTrackTimeStretch::test_the_clamp_passed_in_is_the_one_used` | LOGIC | KEEP |  |
| 831 | `TestDubTrackTimeStretch::test_all_three_states_map_to_their_indicator` | LOGIC | KEEP |  |
| 838 | `TestDubTrackTimeStretch::test_without_ffmpeg_the_line_still_plays_unstretched` | LOGIC | KEEP |  |
| 848 | `TestDubTrackTimeStretch::test_the_pacing_rewrite_runs_first_and_the_stretch_only_closes_what_is_left` | LOGIC | KEEP |  |
| 870 | `TestDubTrackTimeStretch::test_a_pacing_record_goes_stale_once_the_line_points_elsewhere` | LOGIC | KEEP |  |
| 888 | `TestClipCacheFollowsTheText::test_editing_a_dubbed_line_re_voices_it` | LOGIC | KEEP |  |
| 899 | `TestClipCacheFollowsTheText::test_changing_a_characters_voice_re_voices_their_lines` | LOGIC | KEEP |  |
| 905 | `TestClipCacheFollowsTheText::test_a_cancelled_run_still_resumes_from_its_finished_clips` | LOGIC | KEEP |  |
| 925 | `TestClipCacheFollowsTheText::test_a_kill_after_partial_bytes_still_leaves_no_corrupted_cache_file` | LOGIC | KEEP |  |
| 955 | `TestClipCacheFollowsTheText::test_an_edited_narration_unit_is_re_voiced_too` | LOGIC | KEEP |  |
| 971 | `TestPiperFallbackClipCaching::test_dub_track_retries_edge_tts_once_it_recovers` | LOGIC | KEEP |  |
| 994 | `TestPiperFallbackClipCaching::test_narration_track_retries_edge_tts_once_it_recovers` | LOGIC | KEEP |  |
| 1021 | `TestHostedCloningRemoved::test_no_references_remain` | LOGIC | KEEP |  |
| 1040 | `TestHostedCloningRemoved::test_a_character_cloned_with_it_dubs_without_crashing` | LOGIC | KEEP |  |
| 1109 | `TestSynthesizeLineOffline::test_downloads_the_model_then_writes_a_real_wav` | LOGIC | KEEP |  |
| 1123 | `TestSynthesizeLineOffline::test_an_already_downloaded_model_is_not_downloaded_again` | LOGIC | KEEP |  |
| 1136 | `TestSynthesizeLineOffline::test_an_edge_tts_voice_name_is_refused_with_a_clear_message` | LOGIC | KEEP |  |
| 1149 | `TestOfflineEngineUsesOfflineVoices::test_offline_dub_calls_piper_with_a_resolvable_model_not_the_edge_voice` | LOGIC | KEEP |  |
| 1167 | `TestOfflineEngineUsesOfflineVoices::test_offline_narration_uses_the_offline_voice_too` | LOGIC | KEEP |  |
| 1179 | `TestOfflineEngineUsesOfflineVoices::test_edge_fallback_never_hands_piper_the_edge_voice` | LOGIC | KEEP |  |
| 1194 | `test_offline_voice_is_saved_separately_from_the_edge_voice` | LOGIC | KEEP |  |
| 1211 | `TestDubWorkerArgumentBinding::test_queue_lands_in_result_queue_when_extras_are_bound_by_keyword` | LOGIC | KEEP |  |
| 1224 | `TestDubWorkerArgumentBinding::test_workspace_tab_binds_them_by_keyword` | UI | DELETE with tab | tab source inspection (keyword binding now also in dub_service; re-check there) |

### `tests/test_emotion_manhua_ui.py` (39 tests: 36 LOGIC, 3 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 21 | `TestEmotionGuidance::test_neutral_lines_excluded` | LOGIC | KEEP |  |
| 25 | `TestEmotionGuidance::test_low_intensity_excluded` | LOGIC | KEEP |  |
| 29 | `TestEmotionGuidance::test_charged_line_included_with_register_guidance` | LOGIC | KEEP |  |
| 35 | `TestEmotionGuidance::test_sarcasm_guidance_warns_against_softening` | LOGIC | KEEP |  |
| 40 | `TestEmotionGuidance::test_empty_map_and_empty_indices` | LOGIC | KEEP |  |
| 44 | `TestEmotionGuidance::test_note_is_included` | LOGIC | KEEP |  |
| 51 | `TestEmotionSummary::test_counts_and_high_risk` | LOGIC | KEEP |  |
| 59 | `TestEmotionSummary::test_empty` | LOGIC | KEEP |  |
| 62 | `TestEmotionSummary::test_pure_mt_returns_no_tags` | LOGIC | KEEP |  |
| 95 | `TestDetectEmotionsProgress::test_progress_cb_called_once_per_batch` | LOGIC | KEEP |  |
| 102 | `TestDetectEmotionsProgress::test_progress_cb_is_optional` | LOGIC | KEEP |  |
| 107 | `TestDetectEmotionsProgress::test_progress_still_advances_when_a_batch_fails_to_parse` | LOGIC | KEEP |  |
| 121 | `TestEmotionTagDescriptions::test_all_tags_have_descriptions` | LOGIC | KEEP |  |
| 135 | `TestManhuaPanels::test_detects_panels` | LOGIC | KEEP |  |
| 142 | `TestManhuaPanels::test_missing_file_raises` | LOGIC | KEEP |  |
| 159 | `TestWebtoonSlicing::test_tall_strip_is_sliced` | LOGIC | KEEP |  |
| 166 | `TestWebtoonSlicing::test_slices_cover_entire_strip` | LOGIC | KEEP |  |
| 175 | `TestWebtoonSlicing::test_all_slices_have_positive_height` | LOGIC | KEEP |  |
| 183 | `TestWebtoonSlicing::test_short_image_not_sliced` | LOGIC | KEEP |  |
| 220 | `TestWebtoonUpload::test_tall_strip_is_sliced_into_pages` | LOGIC | NEEDS EXTRACTION | tabs.scanlate_tab.add_uploaded_pages -> scanlate service (Scanlate deferred: extract before deletion or lift from the tag later) |
| 231 | `TestWebtoonUpload::test_strip_is_kept_whole_when_slicing_is_off` | LOGIC | NEEDS EXTRACTION | tabs.scanlate_tab.add_uploaded_pages -> scanlate service (Scanlate deferred: extract before deletion or lift from the tag later) |
| 236 | `TestWebtoonUpload::test_ordinary_page_is_never_sliced` | LOGIC | NEEDS EXTRACTION | tabs.scanlate_tab.add_uploaded_pages -> scanlate service (Scanlate deferred: extract before deletion or lift from the tag later) |
| 243 | `TestTextRegionClassification::test_returns_valid_kind` | LOGIC | KEEP |  |
| 257 | `TestTextRegionClassification::test_missing_file_defaults_to_bubble` | LOGIC | KEEP |  |
| 261 | `TestTextRegionClassification::test_all_kinds_documented` | LOGIC | KEEP |  |
| 265 | `TestTextRegionClassification::test_font_style_sampling_returns_suggestion` | LOGIC | KEEP |  |
| 275 | `TestTextRegionClassification::test_font_sampling_missing_file_fallback` | LOGIC | KEEP |  |
| 301 | `TestReaderFollowAlong::test_rows_carry_timing_data` | LOGIC | KEEP |  |
| 307 | `TestReaderFollowAlong::test_no_follow_bar_without_audio` | LOGIC | KEEP |  |
| 313 | `TestReaderFollowAlong::test_follow_bar_and_listener_present_with_audio` | LOGIC | KEEP |  |
| 322 | `TestReaderFollowAlong::test_offset_is_zero_for_the_first_page` | LOGIC | KEEP |  |
| 329 | `TestReaderFollowAlong::test_offset_matches_a_mid_drama_page` | LOGIC | KEEP |  |
| 338 | `TestReaderFollowAlong::test_active_line_styling_in_every_theme` | LOGIC | KEEP |  |
| 354 | `TestDarkModeCoverage::test_covers_every_widget_type_the_app_actually_uses` | UI | DELETE with tab | ui_theme CSS |
| 366 | `TestDarkModeCoverage::test_previously_covered_selectors_still_present` | UI | DELETE with tab | ui_theme CSS |
| 376 | `TestDarkModeCoverage::test_dark_palette_has_all_expected_keys` | UI | DELETE with tab | ui_theme CSS |
| 450 | `TestHfTokenInScanlateMlDetector::test_explicit_token_argument_reaches_the_download` | LOGIC | KEEP |  |
| 461 | `TestHfTokenInScanlateMlDetector::test_falls_back_to_an_already_set_environment_token` | LOGIC | KEEP |  |
| 473 | `TestHfTokenInScanlateMlDetector::test_no_token_available_does_not_crash` | LOGIC | KEEP |  |

### `tests/test_export_formats.py` (34 tests: 33 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 25 | `TestSrtUnchanged::test_default_srt_export_is_byte_identical` | LOGIC | KEEP |  |
| 37 | `TestVtt::test_header_and_timestamps` | LOGIC | KEEP |  |
| 43 | `TestVtt::test_bilingual_and_arrow_safety` | LOGIC | KEEP |  |
| 51 | `TestAss::test_style_and_one_colour_per_speaker` | LOGIC | KEEP |  |
| 66 | `TestAss::test_text_is_escaped` | LOGIC | KEEP |  |
| 72 | `TestAss::test_presets_differ_and_clean_is_the_old_hardsub_look` | LOGIC | KEEP |  |
| 77 | `TestAss::test_speaker_colours_are_distinct_by_default` | LOGIC | KEEP |  |
| 87 | `TestAssShadow::test_shadow_value_reaches_the_style_line` | LOGIC | KEEP |  |
| 93 | `TestAssShadow::test_missing_shadow_key_falls_back_to_1` | LOGIC | KEEP |  |
| 99 | `TestAssShadow::test_both_presets_default_to_shadow_1` | LOGIC | KEEP |  |
| 115 | `TestAssNotesAsSeparateLine::test_off_by_default_keeps_the_inline_suffix` | LOGIC | KEEP |  |
| 122 | `TestAssNotesAsSeparateLine::test_on_produces_a_second_smaller_dialogue_line` | LOGIC | KEEP |  |
| 134 | `TestAssNotesAsSeparateLine::test_a_line_with_no_note_gets_no_extra_dialogue_line` | LOGIC | KEEP |  |
| 141 | `TestAssNotesAsSeparateLine::test_srt_and_vtt_have_no_such_toggle_and_always_stay_inline` | LOGIC | KEEP |  |
| 149 | `TestLongLines::test_splits_at_a_clause_boundary` | LOGIC | KEEP |  |
| 154 | `TestLongLines::test_never_splits_mid_word` | LOGIC | KEEP |  |
| 160 | `TestLongLines::test_cjk_splits_at_punctuation` | LOGIC | KEEP |  |
| 165 | `TestLongLines::test_tighter_limit_for_cjk` | LOGIC | KEEP |  |
| 168 | `TestLongLines::test_wrapping_applies_to_every_format` | LOGIC | KEEP |  |
| 177 | `TestOverlapClamp::test_overlap_is_trimmed_in_the_export_copy_only` | LOGIC | KEEP |  |
| 186 | `TestOverlapClamp::test_export_panel_warns_but_does_not_silently_flag_on_render` | UI | DELETE with tab | AppTest |
| 222 | `TestReadingSpeed::test_too_dense_is_flagged_normal_is_not` | LOGIC | KEEP |  |
| 229 | `TestReadingSpeed::test_limits_follow_the_script` | LOGIC | KEEP |  |
| 236 | `TestReadingSpeed::test_an_existing_flag_is_never_replaced` | LOGIC | KEEP |  |
| 241 | `TestReadingSpeed::test_translate_job_puts_dense_lines_in_the_review_queue` | LOGIC | KEEP |  |
| 272 | `TestHardsub::test_burning_ass_skips_force_style` | LOGIC | KEEP |  |
| 279 | `TestHardsub::test_srt_burn_uses_the_chosen_style` | LOGIC | KEEP |  |
| 289 | `TestHardsub::test_default_srt_burn_keeps_the_old_look` | LOGIC | KEEP |  |
| 297 | `TestPreview::test_preview_reflects_the_style_and_escapes_text` | LOGIC | KEEP |  |
| 304 | `TestPreview::test_font_and_colour_can_not_inject_css` | LOGIC | KEEP |  |
| 322 | `TestLinesForClip::test_only_overlapping_lines_are_kept` | LOGIC | KEEP |  |
| 326 | `TestLinesForClip::test_kept_lines_are_timeshifted_to_start_at_zero` | LOGIC | KEEP |  |
| 331 | `TestLinesForClip::test_a_partially_overlapping_line_is_clamped_to_the_window` | LOGIC | KEEP |  |
| 336 | `TestLinesForClip::test_idx_is_renumbered_and_original_lines_untouched` | LOGIC | KEEP |  |

### `tests/test_gui_polish.py` (5 tests: 0 LOGIC, 5 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 28 | `test_style_controls_are_a_fragment` | UI | DELETE with tab |  |
| 43 | `test_current_style_is_published_to_session_state` | UI | DELETE with tab |  |
| 51 | `test_a_control_change_updates_the_same_dict_in_place` | UI | DELETE with tab |  |
| 63 | `test_library_scanlate_live_and_discover_opt_into_the_type_scale` | UI | DELETE with tab |  |
| 77 | `test_type_scale_marker_reaches_the_page` | UI | DELETE with tab |  |

### `tests/test_install_buttons.py` (67 tests: 34 LOGIC, 33 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 31 | `TestStreamPipInstall::test_streams_lines_then_a_final_done_item` | LOGIC | KEEP |  |
| 40 | `TestStreamPipInstall::test_targets_the_running_interpreter_not_a_bare_pip` | LOGIC | KEEP |  |
| 52 | `TestStreamPipInstall::test_explicit_interpreter_overrides_sys_executable` | LOGIC | KEEP |  |
| 63 | `TestStreamPipInstall::test_failure_surfaces_the_real_error_text_not_a_generic_message` | LOGIC | KEEP |  |
| 73 | `TestStreamPipUninstall::test_passes_dash_y_and_targets_the_running_interpreter` | LOGIC | KEEP |  |
| 88 | `TestParseRequirementsFile::test_skips_comments_and_blank_lines` | LOGIC | KEEP |  |
| 93 | `TestParseRequirementsFile::test_strips_trailing_inline_comments` | LOGIC | KEEP |  |
| 98 | `TestParseRequirementsFile::test_a_fully_commented_out_line_is_never_returned` | LOGIC | KEEP |  |
| 105 | `TestParseRequirementsFile::test_missing_file_returns_empty_list` | LOGIC | KEEP |  |
| 110 | `TestStreamBulkInstall::test_installs_every_package_and_reports_all_results` | LOGIC | KEEP |  |
| 126 | `TestStreamBulkInstall::test_tags_every_event_with_its_own_package` | LOGIC | KEEP |  |
| 136 | `TestStreamBulkInstall::test_empty_file_yields_only_the_bulk_done_summary` | LOGIC | KEEP |  |
| 143 | `TestStreamDenoInstall::test_already_on_path_does_nothing` | LOGIC | KEEP |  |
| 148 | `TestStreamDenoInstall::test_prefers_winget_on_windows_when_available` | LOGIC | KEEP |  |
| 163 | `TestStreamDenoInstall::test_falls_back_to_the_powershell_script_on_windows_without_winget` | LOGIC | KEEP |  |
| 176 | `TestStreamDenoInstall::test_uses_the_shell_script_on_linux_and_mac` | LOGIC | KEEP |  |
| 190 | `TestStreamDenoInstall::test_success_but_not_yet_on_path_reports_needs_restart` | LOGIC | KEEP |  |
| 201 | `TestStreamDenoInstall::test_failed_install_reports_not_ok` | LOGIC | KEEP |  |
| 214 | `TestGpuTorchMismatch::test_false_when_nvidia_smi_not_on_path` | LOGIC | KEEP |  |
| 218 | `TestGpuTorchMismatch::test_true_when_gpu_present_but_torch_is_cpu_only` | LOGIC | KEEP |  |
| 225 | `TestGpuTorchMismatch::test_false_when_cuda_is_available` | LOGIC | KEEP |  |
| 231 | `TestGpuTorchMismatch::test_false_when_torch_isnt_installed_at_all` | LOGIC | KEEP |  |
| 254 | `TestExternalGpuLoad::test_none_when_nvidia_smi_not_on_path` | LOGIC | KEEP |  |
| 259 | `TestExternalGpuLoad::test_parses_a_real_nvidia_smi_response` | LOGIC | KEEP |  |
| 267 | `TestExternalGpuLoad::test_none_on_a_failed_or_malformed_query` | LOGIC | KEEP |  |
| 276 | `TestExternalGpuLoad::test_busy_when_utilization_crosses_the_threshold` | LOGIC | KEEP |  |
| 282 | `TestExternalGpuLoad::test_busy_when_free_vram_is_low_even_at_low_utilization` | LOGIC | KEEP |  |
| 288 | `TestExternalGpuLoad::test_not_busy_when_idle_and_plenty_free` | LOGIC | KEEP |  |
| 296 | `TestGpuTorchCudaIndex::test_python_3_14_uses_cu128` | LOGIC | KEEP |  |
| 301 | `TestGpuTorchCudaIndex::test_unlisted_version_falls_back_to_the_default` | LOGIC | KEEP |  |
| 308 | `TestStreamGpuTorchReinstall::test_uninstalls_then_installs_with_index_and_constraints` | LOGIC | KEEP |  |
| 332 | `TestStreamGpuTorchReinstall::test_missing_constraints_file_is_skipped_cleanly` | LOGIC | KEEP |  |
| 344 | `TestStreamGpuTorchReinstall::test_only_the_last_yielded_item_carries_done` | LOGIC | KEEP |  |
| 355 | `TestStreamGpuTorchReinstall::test_a_failed_uninstall_step_still_surfaces_its_own_lines` | LOGIC | KEEP |  |
| 407 | `TestDiagnosticsTabInstallButtonGating::test_exactly_the_missing_feature_and_engine_deps_get_a_button` | UI | DELETE with tab | AppTest |
| 439 | `TestDiagnosticsTabUpgradeButtonGating::test_upgrade_button_appears_for_an_outdated_feature_tier_dependency` | UI | DELETE with tab | AppTest |
| 446 | `TestDiagnosticsTabUpgradeButtonGating::test_no_upgrade_button_when_up_to_date` | UI | DELETE with tab | AppTest |
| 451 | `TestDiagnosticsTabUpgradeButtonGating::test_no_upgrade_button_for_required_tier_even_if_flagged_outdated` | UI | DELETE with tab | AppTest |
| 456 | `TestDiagnosticsTabUpgradeButtonGating::test_no_upgrade_button_before_the_check_has_ever_run` | UI | DELETE with tab | AppTest |
| 460 | `TestDiagnosticsTabUpgradeButtonGating::test_clicking_upgrade_calls_pip_install_with_the_upgrade_flag_and_streams_output` | UI | DELETE with tab | AppTest |
| 499 | `TestDiagnosticsTabCheckForUpdatesButton::test_button_present_and_makes_no_network_call_until_clicked` | UI | DELETE with tab | AppTest |
| 509 | `TestDiagnosticsTabCheckForUpdatesButton::test_clicking_it_populates_version_results` | UI | DELETE with tab | AppTest |
| 535 | `TestDiagnosticsTabGpuTorchButtonVisibility::test_button_appears_when_a_real_mismatch_is_detected` | UI | DELETE with tab | AppTest |
| 540 | `TestDiagnosticsTabGpuTorchButtonVisibility::test_button_absent_with_no_mismatch` | UI | DELETE with tab | AppTest |
| 574 | `TestModelPanelRedundantTtsConfirm::test_first_click_shows_a_warning_instead_of_installing` | UI | DELETE with tab | AppTest |
| 588 | `TestModelPanelRedundantTtsConfirm::test_install_anyway_actually_installs` | UI | DELETE with tab | AppTest |
| 602 | `TestModelPanelRedundantTtsConfirm::test_cancel_clears_the_confirmation_without_installing` | UI | DELETE with tab | AppTest |
| 616 | `TestModelPanelRedundantTtsConfirm::test_no_confirmation_needed_when_nothing_redundant_is_installed` | UI | DELETE with tab | AppTest |
| 664 | `TestDependenciesUpgradeExplainsWhenBlocked::test_known_python_314_limitation_shows_a_reason_not_a_button` | UI | DELETE with tab | AppTest |
| 675 | `TestDependenciesUpgradeExplainsWhenBlocked::test_upgrade_button_appears_normally_on_a_different_python_version` | UI | DELETE with tab | AppTest |
| 707 | `TestNotInstalledExplainsAKnownLimitationUpFront::test_known_python_314_limitation_shown_in_the_caption` | UI | DELETE with tab | AppTest |
| 712 | `TestNotInstalledExplainsAKnownLimitationUpFront::test_install_button_still_offered_despite_the_known_limitation` | UI | DELETE with tab | AppTest |
| 716 | `TestNotInstalledExplainsAKnownLimitationUpFront::test_no_known_limitation_mentioned_on_a_different_python_version` | UI | DELETE with tab | AppTest |
| 726 | `TestNotInstalledExplainsAKnownLimitationUpFront::test_a_failed_install_adds_the_known_reason_after_the_real_traceback` | UI | DELETE with tab | AppTest |
| 780 | `TestBulkTierInstallUI::test_all_three_tier_buttons_exist` | UI | DELETE with tab | AppTest |
| 787 | `TestBulkTierInstallUI::test_clicking_shows_a_per_package_result_summary` | UI | DELETE with tab | AppTest |
| 804 | `TestBulkTierInstallUI::test_a_later_rerun_keeps_showing_the_last_results` | UI | DELETE with tab | AppTest |
| 838 | `TestDenoInstallUI::test_install_button_appears_only_when_no_runtime_found` | UI | DELETE with tab | AppTest |
| 842 | `TestDenoInstallUI::test_success_and_already_on_path_offers_no_restart_notice` | UI | DELETE with tab | AppTest |
| 851 | `TestDenoInstallUI::test_success_but_not_on_path_shows_a_restart_notice` | UI | DELETE with tab | AppTest |
| 860 | `TestDenoInstallUI::test_failed_install_shows_the_failure_not_a_restart_notice` | UI | DELETE with tab | AppTest |
| 895 | `TestUpgradeCheckUI::test_outdated_row_reads_untested_and_offers_the_check` | UI | DELETE with tab | AppTest |
| 900 | `TestUpgradeCheckUI::test_a_broken_result_is_shown_with_the_failing_tests` | UI | DELETE with tab | AppTest |
| 920 | `TestUpgradeCheckUI::test_a_safe_result_is_shown_as_such` | UI | DELETE with tab | AppTest |
| 929 | `TestUpgradeCheckUI::test_an_incomplete_check_is_neither_safe_nor_broken` | UI | DELETE with tab | AppTest |
| 939 | `TestUpgradeCheckUI::test_a_result_for_an_older_target_version_still_reads_untested` | UI | DELETE with tab | AppTest |
| 945 | `TestUpgradeCheckUI::test_a_conflict_result_is_a_warning_with_pips_own_report` | UI | DELETE with tab | AppTest |

### `tests/test_library_features.py` (125 tests: 89 LOGIC, 36 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 26 | `TestTimeEstimates::test_reading_time_counts_words` | LOGIC | KEEP |  |
| 30 | `TestTimeEstimates::test_reading_time_scales_with_wpm` | LOGIC | KEEP |  |
| 36 | `TestTimeEstimates::test_listening_time_uses_last_timestamp` | LOGIC | KEEP |  |
| 40 | `TestTimeEstimates::test_empty_lines_no_crash` | LOGIC | KEEP |  |
| 44 | `TestTimeEstimates::test_percent_complete` | LOGIC | KEEP |  |
| 49 | `TestTimeEstimates::test_duration_formatting` | LOGIC | KEEP |  |
| 57 | `TestRelationshipMap::test_mermaid_includes_nodes_and_edges` | LOGIC | KEEP |  |
| 63 | `TestRelationshipMap::test_empty_map_returns_empty_string` | LOGIC | KEEP |  |
| 66 | `TestRelationshipMap::test_relationship_missing_endpoint_skipped` | LOGIC | KEEP |  |
| 72 | `TestRelationshipMap::test_pure_mt_engine_returns_empty_map` | LOGIC | KEEP |  |
| 79 | `TestProgressTracking::test_save_and_get` | LOGIC | KEEP |  |
| 85 | `TestProgressTracking::test_partial_update_preserves_other_fields` | LOGIC | KEEP |  |
| 94 | `TestProgressTracking::test_no_progress_returns_none` | LOGIC | KEEP |  |
| 98 | `TestProgressTracking::test_continue_shelf_excludes_finished` | LOGIC | KEEP |  |
| 106 | `TestProgressTracking::test_continue_shelf_excludes_untouched` | LOGIC | KEEP |  |
| 110 | `TestProgressTracking::test_history_records_entries` | LOGIC | KEEP |  |
| 116 | `TestProgressTracking::test_clear_history` | LOGIC | KEEP |  |
| 130 | `TestProfiles::test_init_db_creates_a_default_profile` | LOGIC | KEEP |  |
| 135 | `TestProfiles::test_create_list_get` | LOGIC | KEEP |  |
| 141 | `TestProfiles::test_get_unknown_profile_returns_none` | LOGIC | KEEP |  |
| 144 | `TestProfiles::test_rename` | LOGIC | KEEP |  |
| 149 | `TestProfiles::test_delete_a_second_profile` | LOGIC | KEEP |  |
| 155 | `TestProfiles::test_cannot_delete_the_last_remaining_profile` | LOGIC | KEEP |  |
| 161 | `TestProfiles::test_deleting_a_profile_drops_its_reading_history` | LOGIC | KEEP |  |
| 171 | `TestProfiles::test_progress_is_isolated_per_profile` | LOGIC | KEEP |  |
| 186 | `TestProfiles::test_continue_shelf_is_isolated_per_profile` | LOGIC | KEEP |  |
| 195 | `TestProfiles::test_reading_history_is_isolated_per_profile` | LOGIC | KEEP |  |
| 206 | `TestProfiles::test_clear_reading_history_only_clears_that_profile` | LOGIC | KEEP |  |
| 217 | `TestProfiles::test_personal_notes_are_isolated_per_profile` | LOGIC | KEEP |  |
| 228 | `TestProfiles::test_personal_notes_default_to_empty_string_not_none` | LOGIC | KEEP |  |
| 232 | `TestProfiles::test_calls_without_profile_id_all_resolve_to_the_same_default` | LOGIC | KEEP |  |
| 245 | `TestTranslationVersions::test_save_and_list` | LOGIC | KEEP |  |
| 252 | `TestTranslationVersions::test_only_one_active_at_a_time` | LOGIC | KEEP |  |
| 260 | `TestTranslationVersions::test_content_preserved_across_versions` | LOGIC | KEEP |  |
| 268 | `TestTranslationVersions::test_switching_active_version` | LOGIC | KEEP |  |
| 277 | `TestTranslationVersions::test_delete_version` | LOGIC | KEEP |  |
| 286 | `TestCustomTagsAndMetadata::test_distinct_tags_split_and_deduped` | LOGIC | KEEP |  |
| 291 | `TestCustomTagsAndMetadata::test_no_tags_returns_empty` | LOGIC | KEEP |  |
| 295 | `TestCustomTagsAndMetadata::test_metadata_fields_persist` | LOGIC | KEEP |  |
| 318 | `TestStorage::test_scan_categorizes_sizes` | LOGIC | KEEP |  |
| 327 | `TestStorage::test_cleanup_never_removes_source` | LOGIC | KEEP |  |
| 336 | `TestStorage::test_cleanup_reports_freed_bytes` | LOGIC | KEEP |  |
| 344 | `TestStorage::test_nonexistent_dir_no_crash` | LOGIC | KEEP |  |
| 348 | `TestStorage::test_format_bytes` | LOGIC | KEEP |  |
| 352 | `TestStorage::test_archival_preset_keeps_regenerables` | LOGIC | KEEP |  |
| 356 | `TestStorage::test_minimal_preset_cleans_everything_regenerable` | LOGIC | KEEP |  |
| 361 | `TestStorage::test_all_cleanable_categories_are_regenerable` | LOGIC | KEEP |  |
| 388 | `TestResumeHandoff::test_resume_overrides_a_stale_selection` | LOGIC | KEEP |  |
| 394 | `TestResumeHandoff::test_resume_sets_a_one_time_banner` | LOGIC | KEEP |  |
| 401 | `TestResumeHandoff::test_manual_selection_survives_when_no_resume_pending` | LOGIC | KEEP |  |
| 407 | `TestResumeHandoff::test_pending_flag_is_consumed_not_sticky` | LOGIC | KEEP |  |
| 413 | `TestResumeHandoff::test_unknown_drama_id_leaves_selection_alone` | LOGIC | KEEP |  |
| 419 | `TestResumeHandoff::test_resume_page_carries_the_saved_position` | LOGIC | KEEP |  |
| 454 | `TestCostDashboardShowsFreeEngineUsage::test_free_engine_usage_shows_zero_free_not_omitted` | UI | DELETE with tab | AppTest |
| 461 | `TestCostDashboardShowsFreeEngineUsage::test_paid_engine_usage_shows_a_real_dollar_amount` | UI | DELETE with tab | AppTest |
| 468 | `TestCostDashboardShowsFreeEngineUsage::test_a_drama_with_no_usage_at_all_is_not_in_the_table` | UI | DELETE with tab | AppTest |
| 490 | `TestAnimeInLibraryTypeFilter::test_anime_is_a_type_filter_option` | UI | DELETE with tab | AppTest |
| 495 | `TestAnimeInLibraryTypeFilter::test_filtering_by_anime_shows_only_anime_dramas` | UI | DELETE with tab | AppTest |
| 507 | `TestCacheHitShare::test_share_of_input_tokens` | LOGIC | NEEDS EXTRACTION | tabs.library_tab.cache_hit_share -> services/library_service (3-line pure function) |
| 511 | `TestCacheHitShare::test_no_usage_is_zero_not_a_division_error` | LOGIC | NEEDS EXTRACTION | tabs.library_tab.cache_hit_share -> services/library_service (3-line pure function) |
| 541 | `TestBulkSeriesTranslate::test_translates_every_eligible_drama_with_its_own_saved_engine` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 551 | `TestBulkSeriesTranslate::test_a_drama_already_running_is_skipped_not_queued` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 562 | `TestBulkSeriesTranslate::test_a_drama_needing_a_key_with_none_supplied_is_skipped` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 568 | `TestBulkSeriesTranslate::test_a_drama_with_no_lines_is_skipped` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 574 | `TestBulkSeriesTranslate::test_each_drama_uses_its_own_series_glossary` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 599 | `TestBulkSeriesTranslate::test_cancelling_stops_the_current_drama_and_skips_the_rest` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 629 | `TestBulkSeriesTranslate::test_monthly_cap_already_reached_skips_the_drama_without_translating` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 639 | `TestBulkSeriesTranslate::test_monthly_cap_not_reached_still_translates` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 644 | `TestBulkSeriesTranslate::test_a_novel_narration_drama_uses_the_novel_style_preset_not_audio_drama` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 668 | `TestBulkSeriesTranslate::test_uses_the_settings_configured_model_not_the_engines_bare_default` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 686 | `TestBulkSeriesTranslate::test_an_ollama_drama_is_gpu_touching` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 695 | `TestBulkSeriesTranslate::test_combined_progress_message_names_the_current_drama` | LOGIC | REPOINTED | services.workspace_job_service.run_bulk_series_translate_job (the tab only re-exported it) |
| 731 | `TestBulkSeriesTranslateStatusUI::test_combined_status_line_shows_while_running` | UI | DELETE with tab | AppTest |
| 744 | `TestBulkSeriesTranslateStatusUI::test_cancel_button_requests_cancellation` | UI | DELETE with tab | AppTest |
| 757 | `TestBulkSeriesTranslateStatusUI::test_done_summary_shown_and_job_cleared` | UI | DELETE with tab | AppTest |
| 792 | `TestBulkSeriesTranslateRefreshesOpenWorkspaceDrama::test_refreshes_lines_when_the_open_drama_was_just_translated` | UI | DELETE with tab | AppTest |
| 817 | `TestBulkSeriesTranslateRefreshesOpenWorkspaceDrama::test_does_not_touch_lines_for_an_unrelated_open_drama` | UI | DELETE with tab | AppTest |
| 855 | `TestManagePresetsUI::test_no_presets_shows_the_empty_state` | UI | DELETE with tab | AppTest |
| 859 | `TestManagePresetsUI::test_a_saved_preset_shows_its_captured_fields_at_a_glance` | UI | DELETE with tab | AppTest |
| 872 | `TestManagePresetsUI::test_rename_updates_the_name_only` | UI | DELETE with tab | AppTest |
| 885 | `TestManagePresetsUI::test_rename_to_the_same_name_is_disabled` | UI | DELETE with tab | AppTest |
| 891 | `TestManagePresetsUI::test_delete_removes_only_that_preset` | UI | DELETE with tab | AppTest |
| 901 | `TestManagePresetsUI::test_deleting_a_preset_does_not_change_a_drama_it_was_applied_to` | UI | DELETE with tab | AppTest |
| 913 | `TestManagePresetsUI::test_delete_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 949 | `TestVoiceBankDeleteConfirm::test_delete_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 957 | `TestVoiceBankDeleteConfirm::test_delete_removes_only_that_entry_once_confirmed` | UI | DELETE with tab | AppTest |
| 972 | `TestListDramasBySeries::test_returns_every_media_type_in_the_series` | LOGIC | KEEP |  |
| 982 | `TestListDramasBySeries::test_a_different_series_id_is_not_included` | LOGIC | KEEP |  |
| 988 | `TestListDramasBySeries::test_empty_for_a_series_with_no_dramas` | LOGIC | KEEP |  |
| 992 | `TestListDramasBySeries::test_falls_back_to_created_at_when_no_episode_numbers_set` | LOGIC | KEEP |  |
| 1002 | `TestListDramasBySeries::test_orders_by_episode_number_once_any_drama_in_series_has_one` | LOGIC | KEEP |  |
| 1013 | `TestListDramasBySeries::test_unnumbered_sibling_sorts_after_numbered_ones_without_crashing` | LOGIC | KEEP |  |
| 1031 | `TestPreviousEpisodeSummary::test_reaches_the_next_episode_by_episode_number` | LOGIC | KEEP |  |
| 1038 | `TestPreviousEpisodeSummary::test_only_the_immediately_preceding_episode_reaches_forward` | LOGIC | KEEP |  |
| 1049 | `TestPreviousEpisodeSummary::test_empty_with_no_episode_number_set` | LOGIC | KEEP |  |
| 1056 | `TestPreviousEpisodeSummary::test_empty_for_the_first_episode` | LOGIC | KEEP |  |
| 1061 | `TestPreviousEpisodeSummary::test_editing_the_stored_summary_changes_what_is_fed_forward` | LOGIC | KEEP |  |
| 1091 | `TestLibrarySeriesView::test_a_single_drama_series_is_not_shown` | UI | DELETE with tab | AppTest |
| 1098 | `TestLibrarySeriesView::test_a_multi_drama_series_lists_every_drama_across_media_types` | UI | DELETE with tab | AppTest |
| 1111 | `TestLibrarySeriesView::test_counts_pluralize_when_more_than_one` | UI | DELETE with tab | AppTest |
| 1119 | `TestLibrarySeriesView::test_shows_shared_character_and_glossary_counts` | UI | DELETE with tab | AppTest |
| 1130 | `TestLibrarySeriesView::test_open_button_switches_the_active_drama` | UI | DELETE with tab | AppTest |
| 1167 | `TestLibraryEntryPointsClearStaleWidgetState::test_resume_button_clears_stale_line_widget_state` | UI | DELETE with tab | AppTest |
| 1179 | `TestLibraryEntryPointsClearStaleWidgetState::test_series_open_button_clears_stale_line_widget_state` | UI | DELETE with tab | AppTest |
| 1208 | `TestLibrarySectionsAreCollapsible::test_every_top_level_section_is_an_expander` | UI | DELETE with tab | AppTest |
| 1215 | `TestLibrarySectionsAreCollapsible::test_dashboard_and_search_and_filter_default_open` | UI | DELETE with tab | AppTest |
| 1222 | `TestLibrarySectionsAreCollapsible::test_series_and_the_rest_default_collapsed` | UI | DELETE with tab | AppTest |
| 1248 | `TestSeriesPickerSharedBetweenMetadataAndGlossary::test_metadata_expander_has_a_series_picker` | UI | DELETE with tab | AppTest |
| 1254 | `TestSeriesPickerSharedBetweenMetadataAndGlossary::test_assigning_a_series_from_metadata_reflects_in_the_glossary_picker` | UI | DELETE with tab | AppTest |
| 1266 | `TestSeriesPickerSharedBetweenMetadataAndGlossary::test_assigning_from_glossary_reflects_in_the_metadata_picker` | UI | DELETE with tab | AppTest |
| 1297 | `TestSeriesSharingIndicators::test_a_series_linked_character_shows_the_shared_indicator` | UI | DELETE with tab | AppTest |
| 1309 | `TestSeriesSharingIndicators::test_a_character_with_no_series_link_shows_no_indicator` | UI | DELETE with tab | AppTest |
| 1333 | `TestRestoreFromBackupValidatesBeforeDestroying::test_not_a_zip_at_all_leaves_existing_library_intact` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1348 | `TestRestoreFromBackupValidatesBeforeDestroying::test_valid_zip_missing_library_db_leaves_existing_library_intact` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1364 | `TestRestoreFromBackupValidatesBeforeDestroying::test_valid_backup_zip_still_restores_correctly` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1382 | `TestRestoreFromBackupValidatesBeforeDestroying::test_restoring_into_a_library_dir_that_does_not_exist_yet_works` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1391 | `TestRestoreFromBackupValidatesBeforeDestroying::test_backup_over_total_size_limit_is_rejected_before_extraction` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1413 | `TestRestoreFromBackupValidatesBeforeDestroying::test_backup_with_oversized_single_member_is_rejected` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1425 | `TestRestoreFromBackupValidatesBeforeDestroying::test_backup_over_member_count_limit_is_rejected` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1440 | `TestRestoreFromBackupValidatesBeforeDestroying::test_backup_within_limits_still_restores_normally` | LOGIC | REPOINTED | services.workspace_job_service.restore_library_backup |
| 1460 | `TestBulkExportClampsOverlappingCues::test_overlapping_cues_are_clamped_before_srt_generation` | UI | DELETE with tab | AppTest |
| 1501 | `TestOrganizationalTags::test_set_and_clear_leaves_other_tags_and_status_alone` | LOGIC | KEEP |  |
| 1512 | `TestOrganizationalTags::test_setting_twice_or_hand_typed_lowercase_doesnt_duplicate` | LOGIC | KEEP |  |
| 1519 | `TestOrganizationalTags::test_has_custom_tag_needs_the_whole_tag` | LOGIC | KEEP |  |
| 1542 | `TestOrganizationalTags::test_quick_filter_shows_only_that_list_with_real_status` | UI | DELETE with tab | AppTest |

### `tests/test_live_tab.py` (7 tests: 0 LOGIC, 7 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 32 | `TestCookieSettingsReachTheStartButton::test_cookies_from_settings_reach_run_live_job` | UI | DELETE with tab | AppTest |
| 48 | `TestCookieSettingsReachTheStartButton::test_no_cookies_configured_passes_none` | UI | DELETE with tab | AppTest |
| 61 | `TestCookieSettingsReachTheStartButton::test_cookies_file_takes_priority_when_both_set` | UI | DELETE with tab | AppTest |
| 100 | `TestOverlapSettingReachesTheStartButton::test_default_overlap_is_passed` | UI | DELETE with tab | AppTest |
| 105 | `TestOverlapSettingReachesTheStartButton::test_chosen_overlap_is_passed` | UI | DELETE with tab | AppTest |
| 129 | `TestQueuedJobCancelButton::test_cancel_clears_a_still_queued_job` | UI | DELETE with tab | AppTest |
| 141 | `TestQueuedJobCancelButton::test_cancel_falls_back_to_a_real_stop_when_promoted_in_the_gap` | UI | DELETE with tab | AppTest |

### `tests/test_local_model_defaults.py` (8 tests: 8 LOGIC, 0 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 16 | `TestOllamaDefaults::test_default_model_is_qwen3_8b` | LOGIC | KEEP |  |
| 21 | `TestOllamaDefaults::test_14b_is_opt_in_and_says_it_may_not_fit` | LOGIC | KEEP |  |
| 25 | `TestOllamaDefaults::test_translate_requests_have_a_timeout_and_use_the_default_model` | LOGIC | KEEP |  |
| 61 | `TestReleaseGpuModels::test_clears_every_cache_and_empties_cuda` | LOGIC | KEEP |  |
| 67 | `TestReleaseGpuModels::test_does_not_import_torch_just_to_clear_it` | LOGIC | KEEP |  |
| 72 | `TestReleaseGpuModels::test_a_broken_cuda_doesnt_fail_the_stage` | LOGIC | KEEP |  |
| 81 | `test_caches_are_empty_after_the_transcription_stage_completes` | LOGIC | REPOINTED | now calls services.workspace_job_service.run_transcribe_job |
| 106 | `test_cli_diarize_releases_models_even_when_detection_fails` | LOGIC | KEEP |  |

### `tests/test_media_preview.py` (34 tests: 23 LOGIC, 11 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`, `tabs.workspace_tab (parse_timestamp, _burn_preview_ass, _unsaved_line_count, _player_state_key)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 34 | `TestParseTimestamp::test_accepts_mm_ss_and_raw_seconds` | LOGIC | NEEDS EXTRACTION | parse_timestamp -> React player (TS) or a service (backlog R02) |
| 39 | `TestParseTimestamp::test_rejects_anything_else` | LOGIC | NEEDS EXTRACTION | parse_timestamp -> React player (TS) or a service (backlog R02) |
| 76 | `TestPlayerTimes::test_rounds_outward_to_whole_seconds` | UI | DROP (Streamlit-only helper) -> delete | _player_times works around Streamlit's whole-second player |
| 85 | `TestReviewPlayer::test_starts_at_zero_without_autoplay` | UI | DELETE with tab | AppTest |
| 93 | `TestReviewPlayer::test_typed_timestamp_seeks_the_player` | UI | DELETE with tab | AppTest |
| 104 | `TestReviewPlayer::test_typed_timestamp_selects_the_line_it_lands_in` | UI | DELETE with tab | AppTest |
| 113 | `TestReviewPlayer::test_bad_timestamp_warns_and_leaves_the_player_alone` | UI | DELETE with tab | AppTest |
| 122 | `TestReviewPlayer::test_play_current_segment_stops_at_the_line_end` | UI | DELETE with tab | AppTest |
| 138 | `TestReviewPlayer::test_play_current_segment_uses_the_lines_edited_timing` | UI | DELETE with tab | AppTest |
| 148 | `TestReviewPlayer::test_repeating_the_same_seek_gets_a_fresh_player` | UI | DELETE with tab | AppTest |
| 165 | `TestReviewPlayer::test_video_uses_st_video_and_offers_burned_preview` | UI | DELETE with tab | AppTest |
| 173 | `TestReviewPlayer::test_audio_only_has_no_burned_preview` | UI | DELETE with tab | AppTest |
| 183 | `TestRowClickSeeksInWorkspace::test_row_click_seeks_player` | UI | DELETE with tab | AppTest |
| 216 | `TestBurnPreview::test_clip_is_padded_around_the_line_and_retimed` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass -> export/media service when the burn preview is built (backlog R03) |
| 224 | `TestBurnPreview::test_captions_use_the_current_style_settings` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass -> export/media service when the burn preview is built (backlog R03) |
| 231 | `TestBurnPreview::test_falls_back_to_clean_when_export_section_not_rendered` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass -> export/media service when the burn preview is built (backlog R03) |
| 236 | `TestBurnPreview::test_clip_start_never_goes_negative` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass -> export/media service when the burn preview is built (backlog R03) |
| 240 | `TestBurnPreview::test_render_preview_clip_trims_and_burns_the_ass_file` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass -> export/media service when the burn preview is built (backlog R03) |
| 250 | `TestBurnPreview::test_ass_tempfile_is_cleaned_up_even_on_failure` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass -> export/media service when the burn preview is built (backlog R03) |
| 269 | `TestSfxExport::test_sfx_cue_text` | LOGIC | KEEP (fix file import) |  |
| 275 | `TestSfxExport::test_srt_brackets_and_italicises_sfx_only` | LOGIC | KEEP (fix file import) |  |
| 280 | `TestSfxExport::test_bilingual_srt_brackets_both_languages` | LOGIC | KEEP (fix file import) |  |
| 284 | `TestSfxExport::test_vtt_brackets_and_italicises_sfx` | LOGIC | KEEP (fix file import) |  |
| 288 | `TestSfxExport::test_ass_uses_a_distinct_italic_sfx_style` | LOGIC | KEEP (fix file import) |  |
| 303 | `TestSfxExport::test_wrapped_sfx_is_bracketed_once` | LOGIC | KEEP (fix file import) |  |
| 308 | `TestSfxExport::test_burned_preview_carries_the_sfx_style` | LOGIC | NEEDS EXTRACTION | _burn_preview_ass, as above |
| 314 | `TestSfxPersistence::test_round_trips_through_the_database` | LOGIC | NEEDS EXTRACTION | keep the db sfx round-trip in a db test; the _unsaved_line_count asserts are Streamlit page state |
| 327 | `TestSfxPersistence::test_restoring_a_snapshot_keeps_the_mark` | LOGIC | KEEP (fix file import) |  |
| 335 | `TestSfxPersistence::test_resegment_split_keeps_the_mark` | LOGIC | KEEP (fix file import) |  |
| 358 | `TestSfxAndNotesPositions::test_sfx_and_notes_get_their_own_positions` | LOGIC | KEEP (fix file import) |  |
| 364 | `TestSfxAndNotesPositions::test_unset_positions_keep_the_dialogues_own` | LOGIC | KEEP (fix file import) |  |
| 369 | `TestSfxAndNotesPositions::test_notes_position_needs_the_separate_line_toggle` | LOGIC | KEEP (fix file import) |  |
| 377 | `TestSfxAndNotesPositions::test_sfx_line_still_gets_its_separate_note` | LOGIC | KEEP (fix file import) |  |
| 385 | `TestSfxAndNotesPositions::test_srt_and_vtt_keep_notes_inline` | LOGIC | KEEP (fix file import) |  |

### `tests/test_page_server_settings.py` (9 tests: 0 LOGIC, 9 UI)

Top-level UI/Streamlit imports: `streamlit.testing.v1 (AppTest)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 64 | `TestItIsActuallyReachableInTheApp::test_the_settings_sidebar_calls_the_expander` | UI | DELETE with tab | settings_tab source/AppTest |
| 83 | `TestTheOptInSwitch::test_off_by_default_and_nothing_is_started` | UI | DELETE with tab | AppTest |
| 89 | `TestTheOptInSwitch::test_turning_it_on_starts_the_endpoint_and_shows_the_token` | UI | DELETE with tab | AppTest |
| 97 | `TestTheOptInSwitch::test_the_persisted_flag_survives_a_restart` | UI | DELETE with tab | AppTest |
| 107 | `TestTheConfigBridge::test_the_chosen_engines_own_key_is_handed_to_the_endpoint` | UI | DELETE with tab | AppTest |
| 115 | `TestTheConfigBridge::test_another_engines_key_is_not_handed_over` | UI | DELETE with tab | AppTest |
| 122 | `TestTheConfigBridge::test_ocr_settings_cross_the_bridge_too` | UI | DELETE with tab | AppTest |
| 131 | `TestTheConfigBridge::test_a_missing_key_leaves_the_endpoint_without_an_engine` | UI | DELETE with tab | AppTest |
| 141 | `TestSecretsStayOutOfTheStore::test_no_api_key_and_no_token_reaches_the_settings_database` | UI | DELETE with tab | AppTest |

### `tests/test_project_instructions.py` (12 tests: 10 LOGIC, 2 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 16 | `TestInstructionsInPrompt::test_drama_instructions_appear_alongside_the_global_style_note` | LOGIC | KEEP |  |
| 24 | `TestInstructionsInPrompt::test_series_instructions_come_first_then_the_dramas_own` | LOGIC | KEEP |  |
| 29 | `TestInstructionsInPrompt::test_multi_line_instructions_are_kept_whole` | LOGIC | KEEP |  |
| 33 | `TestInstructionsInPrompt::test_no_instructions_means_no_block` | LOGIC | KEEP |  |
| 38 | `TestInstructionsInPrompt::test_instructions_stay_before_the_output_format_rule` | LOGIC | KEEP |  |
| 44 | `TestInstructionsPersistAndInherit::test_drama_instructions_round_trip_and_reach_the_prompt` | LOGIC | KEEP |  |
| 51 | `TestInstructionsPersistAndInherit::test_second_drama_in_the_series_inherits_series_instructions` | LOGIC | KEEP |  |
| 72 | `TestInstructionsPersistAndInherit::test_list_dramas_filters_still_work` | LOGIC | KEEP |  |
| 78 | `TestInstructionsPersistAndInherit::test_init_db_is_idempotent_with_the_new_columns` | LOGIC | KEEP |  |
| 90 | `TestWorkflowTiers::test_tier_definitions` | LOGIC | KEEP |  |
| 100 | `TestWorkflowTiers::test_applying_a_tier_in_workspace_sets_engine_reflect_and_auto_qc` | UI | DELETE with tab | AppTest |
| 132 | `TestInstructionsUi::test_typing_instructions_in_workspace_saves_them` | UI | DELETE with tab | AppTest |

### `tests/test_raw_transcript.py` (9 tests: 8 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 36 | `TestNeverOverwritten::test_first_run_writes_raw_transcript_json_with_its_details` | LOGIC | KEEP |  |
| 44 | `TestNeverOverwritten::test_a_second_run_gets_its_own_file` | LOGIC | KEEP |  |
| 54 | `TestNeverOverwritten::test_edits_merges_and_retranscribes_leave_it_byte_identical` | LOGIC | KEEP |  |
| 70 | `TestOriginalTextForLine::test_matches_by_time` | LOGIC | KEEP |  |
| 77 | `TestOriginalTextForLine::test_a_merged_line_shows_both_original_parts` | LOGIC | KEEP |  |
| 83 | `TestOriginalTextForLine::test_falls_back_to_the_line_id_when_timing_moved` | LOGIC | KEEP |  |
| 89 | `TestOriginalTextForLine::test_no_transcript_means_no_original` | LOGIC | KEEP |  |
| 112 | `TestRestoreOneLineInReview::test_restore_button_puts_back_only_that_lines_original_text` | UI | DELETE with tab | AppTest |
| 132 | `test_cli_align_writes_the_raw_transcript_too` | LOGIC | KEEP |  |

### `tests/test_reader_tab.py` (12 tests: 3 LOGIC, 9 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 52 | `TestCaptionTracks::test_both_sides_filled_gives_three_tracks` | LOGIC | NEEDS EXTRACTION | tabs.reader_tab.caption_tracks -> a service (Reader/player port, backlog RD02/R02) |
| 61 | `TestCaptionTracks::test_untranslated_drama_gets_source_only` | LOGIC | NEEDS EXTRACTION | tabs.reader_tab.caption_tracks -> a service (Reader/player port, backlog RD02/R02) |
| 67 | `TestCaptionTracks::test_no_text_at_all_gives_no_tracks` | LOGIC | NEEDS EXTRACTION | tabs.reader_tab.caption_tracks -> a service (Reader/player port, backlog RD02/R02) |
| 85 | `TestCaptionTracks::test_video_player_gets_the_tracks` | UI | DELETE with tab | AppTest |
| 92 | `TestCaptionTracks::test_video_player_skips_an_empty_language` | UI | DELETE with tab | AppTest |
| 98 | `TestCaptionTracks::test_audio_only_drama_has_no_subtitles` | UI | DELETE with tab | AppTest |
| 105 | `TestCaptionTracks::test_audio_only_drama_shows_an_unsynced_caption_fallback` | UI | DELETE with tab | AppTest |
| 122 | `TestCaptionTracks::test_audio_only_drama_with_no_translation_shows_nothing_extra` | UI | DELETE with tab | AppTest |
| 147 | `TestStoryPanelAndCollapsibleSections::test_primary_reader_renders_before_any_secondary_content` | UI | DELETE with tab |  |
| 158 | `TestStoryPanelAndCollapsibleSections::test_story_tools_universe_wiki_and_qa_are_reachable` | UI | DELETE with tab |  |
| 171 | `TestStoryPanelAndCollapsibleSections::test_my_notes_and_vocab_export_are_individually_collapsible` | UI | DELETE with tab |  |
| 183 | `TestStoryPanelAndCollapsibleSections::test_no_bare_or_duplicate_leftover_line_tools_section` | UI | DELETE with tab |  |

### `tests/test_review_workspace.py` (20 tests: 2 LOGIC, 18 UI)

Top-level UI/Streamlit imports: `tabs.workspace_tab (_line_audio_clip, _unsaved_line_count)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 32 | `TestLineAudioClip::test_cuts_the_given_range_and_cleans_up` | LOGIC | NEEDS EXTRACTION | _line_audio_clip -> a per-line clip endpoint (backlog R22) |
| 40 | `TestLineAudioClip::test_temp_slice_is_removed_even_when_reading_fails` | LOGIC | NEEDS EXTRACTION | _line_audio_clip -> a per-line clip endpoint (backlog R22) |
| 58 | `TestUnsavedLineCount::test_nothing_unsaved_right_after_loading` | UI | DROP (Streamlit-only helper) -> delete | _unsaved_line_count is Streamlit page state; React saves per line |
| 62 | `TestUnsavedLineCount::test_timing_is_compared_at_the_boxes_two_decimals` | UI | DROP (Streamlit-only helper) -> delete | _unsaved_line_count is Streamlit page state; React saves per line |
| 69 | `TestUnsavedLineCount::test_each_editable_field_counts_as_unsaved` | UI | DROP (Streamlit-only helper) -> delete | _unsaved_line_count is Streamlit page state; React saves per line |
| 74 | `TestUnsavedLineCount::test_saved_immediately_after_save_lines` | UI | DROP (Streamlit-only helper) -> delete | _unsaved_line_count is Streamlit page state; React saves per line |
| 82 | `TestUnsavedLineCount::test_new_or_removed_lines_count_as_unsaved` | UI | DROP (Streamlit-only helper) -> delete | _unsaved_line_count is Streamlit page state; React saves per line |
| 124 | `TestReviewAndEditUI::test_no_audio_is_cut_until_play_is_clicked` | UI | DELETE with tab | AppTest |
| 140 | `TestReviewAndEditUI::test_no_play_button_without_audio` | UI | DELETE with tab | AppTest |
| 145 | `TestReviewAndEditUI::test_status_shows_saved_then_unsaved_then_saved` | UI | DELETE with tab | AppTest |
| 159 | `TestReviewAndEditUI::test_unsaved_survives_the_page_widgets_going_away` | UI | DELETE with tab | AppTest |
| 181 | `TestReviewAndEditUI::test_save_failure_is_shown` | UI | DELETE with tab | AppTest |
| 227 | `TestLineFindAndReplace::test_preview_shows_every_match_without_saving_anything` | UI | DELETE with tab | AppTest |
| 238 | `TestLineFindAndReplace::test_apply_changes_only_matched_lines_en_field` | UI | DELETE with tab | AppTest |
| 251 | `TestLineFindAndReplace::test_regex_mode` | UI | DELETE with tab | AppTest |
| 265 | `TestLineFindAndReplace::test_no_find_text_warns_instead_of_previewing` | UI | DELETE with tab | AppTest |
| 271 | `TestLineFindAndReplace::test_invalid_regex_shows_an_error` | UI | DELETE with tab | AppTest |
| 279 | `TestLineFindAndReplace::test_apply_refuses_a_line_hand_edited_since_preview` | UI | DELETE with tab | AppTest |
| 299 | `TestLineFindAndReplace::test_apply_survives_a_merge_shifting_idx_between_preview_and_apply` | UI | DELETE with tab | AppTest |
| 326 | `TestLineFindAndReplace::test_apply_clears_the_line_widget_state` | UI | DELETE with tab | AppTest |

### `tests/test_scanlate_tab.py` (15 tests: 0 LOGIC, 15 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 49 | `TestAddBubbleManually::test_expander_is_visible_with_zero_detected_bubbles` | UI | DELETE with tab | AppTest |
| 57 | `TestAddBubbleManually::test_clicking_add_bubble_persists_it` | UI | DELETE with tab | AppTest |
| 75 | `TestAddBubbleManually::test_adding_a_second_bubble_keeps_the_first` | UI | DELETE with tab | AppTest |
| 105 | `TestOcrBackendDefaultFromSettings::test_picker_defaults_to_the_settings_value` | UI | DELETE with tab | AppTest |
| 122 | `TestOcrBackendDefaultFromSettings::test_picker_still_overridable_per_page` | UI | DELETE with tab | AppTest |
| 131 | `TestOcrBackendDefaultFromSettings::test_prefer_paddle_vl_manga_checkbox_defaults_from_settings` | UI | DELETE with tab | AppTest |
| 173 | `TestBulkFindAndReplace::test_apply_clears_the_bubble_text_widget` | UI | DELETE with tab | AppTest |
| 186 | `TestBulkFindAndReplace::test_save_bubble_edits_after_apply_does_not_revert_it` | UI | DELETE with tab | AppTest |
| 199 | `TestBulkFindAndReplace::test_matches_are_scoped_by_drama` | UI | DELETE with tab | AppTest |
| 272 | `TestFontUploadDoesNotLeakAcrossDramas::test_switching_dramas_never_overwrites_the_new_dramas_font` | UI | DELETE with tab | AppTest |
| 291 | `TestFontUploadDoesNotLeakAcrossDramas::test_uploaded_font_is_only_written_after_an_explicit_save` | UI | DELETE with tab | AppTest |
| 349 | `TestPerPageContextDoesNotLeakAcrossPages::test_reprocessing_an_earlier_page_uses_its_own_predecessor_not_the_latest_touched` | UI | DELETE with tab | AppTest |
| 390 | `TestAutoResolvesToCvWarning::test_warning_shown_when_auto_resolves_to_cv` | UI | DELETE with tab | AppTest |
| 398 | `TestAutoResolvesToCvWarning::test_warning_absent_when_auto_resolves_to_ml` | UI | DELETE with tab | AppTest |
| 406 | `TestAutoResolvesToCvWarning::test_warning_absent_when_backend_explicitly_set_to_ml` | UI | DELETE with tab | AppTest |

### `tests/test_settings_tab.py` (32 tests: 15 LOGIC, 17 UI)

Top-level UI/Streamlit imports: `streamlit`, `tabs.settings_tab (_load_env_defaults, save_key_to_env)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 51 | `TestLoadsFromEnvFile::test_loads_a_recognized_key` | LOGIC | COVERED (equivalent service test exists) -> delete | test_settings_service.test_resolve_key_reads_env_file |
| 56 | `TestLoadsFromEnvFile::test_strips_quotes_and_whitespace` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite against settings_service.resolve_key (asserts st.session_state today; quotes/comments/malformed/Gemini-name cases are not covered by test_settings_service) |
| 61 | `TestLoadsFromEnvFile::test_ignores_comments_and_blank_lines` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite against settings_service.resolve_key (asserts st.session_state today; quotes/comments/malformed/Gemini-name cases are not covered by test_settings_service) |
| 68 | `TestLoadsFromEnvFile::test_first_matching_name_wins` | LOGIC | COVERED (equivalent service test exists) -> delete | test_settings_service.test_resolve_key_priority_order_prefers_first_env_name |
| 76 | `TestLoadsFromEnvFile::test_missing_file_does_not_crash` | LOGIC | COVERED (equivalent service test exists) -> delete | test_settings_service.test_resolve_key_missing_file_returns_none |
| 80 | `TestLoadsFromEnvFile::test_malformed_file_does_not_crash` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite against settings_service.resolve_key (asserts st.session_state today; quotes/comments/malformed/Gemini-name cases are not covered by test_settings_service) |
| 85 | `TestLoadsFromEnvFile::test_a_utf8_bom_on_the_first_line_does_not_break_that_variable` | LOGIC | COVERED (equivalent service test exists) -> delete | test_settings_service.test_resolve_key_handles_bom |
| 104 | `TestLoadsFromEnvFile::test_loads_gemini_key` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite against settings_service.resolve_key (asserts st.session_state today; quotes/comments/malformed/Gemini-name cases are not covered by test_settings_service) |
| 109 | `TestLoadsFromEnvFile::test_gemini_does_not_fall_back_to_google_api_key` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite against settings_service.resolve_key (asserts st.session_state today; quotes/comments/malformed/Gemini-name cases are not covered by test_settings_service) |
| 120 | `TestAlreadySetValuesWin::test_manually_typed_value_is_not_overwritten` | UI | DELETE with tab | session_state precedence |
| 126 | `TestAlreadySetValuesWin::test_value_loaded_from_env_on_an_earlier_call_still_wins` | UI | DELETE with tab | session_state precedence |
| 155 | `TestGeminiFreeTierCheckbox::test_checkbox_defaults_to_off` | UI | DELETE with tab | AppTest |
| 160 | `TestGeminiFreeTierCheckbox::test_ticking_it_sets_session_state` | UI | DELETE with tab | AppTest |
| 165 | `TestGeminiFreeTierCheckbox::test_stays_on_across_a_rerun` | UI | DELETE with tab | AppTest |
| 195 | `TestLimitOneGpuJobToggle::test_defaults_to_on` | UI | DELETE with tab | AppTest |
| 201 | `TestLimitOneGpuJobToggle::test_turning_it_off_syncs_to_background_jobs` | UI | DELETE with tab | AppTest |
| 234 | `TestNotifyOnJobDoneToggle::test_defaults_to_off` | UI | DELETE with tab | AppTest |
| 240 | `TestNotifyOnJobDoneToggle::test_turning_it_on_syncs_to_background_jobs` | UI | DELETE with tab | AppTest |
| 278 | `TestCookieBasedLoginSettings::test_defaults_to_none` | UI | DELETE with tab | AppTest |
| 284 | `TestCookieBasedLoginSettings::test_picking_a_browser_sets_session_state` | UI | DELETE with tab | AppTest |
| 289 | `TestCookieBasedLoginSettings::test_setting_a_cookies_file_path` | UI | DELETE with tab | AppTest |
| 294 | `TestCookieBasedLoginSettings::test_offered_browsers_match_video_download_module` | UI | DELETE with tab | AppTest |
| 306 | `TestSaveKeyToEnv::test_creates_the_file_when_it_does_not_exist_yet` | LOGIC | REWRITE (service exists, assertion target differs) | asserts save_key_to_env's return value (the var name); set_engine_key returns {engine, configured} |
| 314 | `TestSaveKeyToEnv::test_appends_a_new_line_when_the_file_exists_but_lacks_that_key` | LOGIC | REPOINTED | now settings_service.set_engine_key |
| 321 | `TestSaveKeyToEnv::test_updates_an_existing_line_in_place_rather_than_appending_a_duplicate` | LOGIC | REPOINTED | now settings_service.set_engine_key |
| 331 | `TestSaveKeyToEnv::test_preserves_comments_and_other_lines` | LOGIC | REPOINTED | now settings_service.set_engine_key |
| 341 | `TestSaveKeyToEnv::test_round_trips_through_load_env_defaults` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite as set_engine_key -> resolve_key (asserts st.session_state today) |
| 366 | `TestApiKeySaveToEnvButton::test_clicking_save_calls_save_key_to_env_with_the_typed_value` | UI | DELETE with tab | AppTest |
| 403 | `TestOcrDefaultBackendSetting::test_defaults_to_auto` | UI | DELETE with tab | AppTest |
| 408 | `TestOcrDefaultBackendSetting::test_picking_a_backend_sets_session_state` | UI | DELETE with tab | AppTest |
| 413 | `TestOcrDefaultBackendSetting::test_prefer_paddle_vl_manga_checkbox_present_and_off_by_default` | UI | DELETE with tab | AppTest |
| 427 | `TestRepeatedCallsPickUpLateEdits::test_a_key_added_to_env_after_the_first_call_is_picked_up_on_the_second` | LOGIC | REWRITE (service exists, assertion target differs) | rewrite against settings_service.resolve_key |

### `tests/test_sources_auth_browser.py` (33 tests: 32 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 161 | `TestPersistentProfiles::test_profile_dir_is_computed_from_the_library_at_call_time` | LOGIC | KEEP |  |
| 171 | `TestPersistentProfiles::test_signed_in_profile_survives_two_separate_fetch_calls` | LOGIC | KEEP |  |
| 190 | `TestPersistentProfiles::test_sign_in_survives_a_restart` | LOGIC | KEEP |  |
| 199 | `TestPersistentProfiles::test_login_window_waits_for_the_person_with_no_timeout` | LOGIC | KEEP |  |
| 206 | `TestPersistentProfiles::test_closing_the_window_without_signing_in_is_reported_plainly` | LOGIC | KEEP |  |
| 214 | `TestPersistentProfiles::test_without_a_sign_in_the_ordinary_tiers_run` | LOGIC | KEEP |  |
| 223 | `TestPersistentProfiles::test_forget_deletes_the_saved_profile` | LOGIC | KEEP |  |
| 230 | `TestPersistentProfiles::test_a_busy_profile_is_refused_not_opened_twice` | LOGIC | KEEP |  |
| 237 | `TestPersistentProfiles::test_backups_leave_saved_sign_ins_out` | UI | DELETE with tab (re-home when a backup API exists) | reads library_tab source for the sign-in exclusion |
| 242 | `TestPersistentProfiles::test_base_adapter_login_is_the_manual_flow` | LOGIC | KEEP |  |
| 292 | `TestAutomationPermission::test_record_reports_restricted_regardless_of_a_signed_in_session` | LOGIC | KEEP |  |
| 315 | `TestAutomationPermission::test_a_signed_in_success_alone_doesnt_claim_sign_in_is_required` | LOGIC | KEEP |  |
| 324 | `TestAutomationPermission::test_pipeline_refuses_before_anything_runs` | LOGIC | KEEP |  |
| 350 | `TestAutomationPermission::test_real_vetted_restricted_sites_are_refused_signed_in_or_not` | LOGIC | KEEP |  |
| 364 | `TestAutomationPermission::test_kakaopage_stays_unknown_not_cleared` | LOGIC | KEEP |  |
| 372 | `TestAutomationPermission::test_an_ai_ml_use_restriction_refuses_too` | LOGIC | KEEP |  |
| 379 | `TestAutomationPermission::test_fields_default_to_unknown_and_stay_separate` | LOGIC | KEEP |  |
| 390 | `TestAutomationPermission::test_records_stored_before_23k_migrate` | LOGIC | KEEP |  |
| 400 | `TestAutomationPermission::test_tos_prohibited_and_automation_permission_agree` | LOGIC | KEEP |  |
| 418 | `TestProtectedResources::test_protected_resource_is_reported_specifically` | LOGIC | KEEP |  |
| 436 | `TestProtectedResources::test_sign_in_check_names_protection_too` | LOGIC | KEEP |  |
| 441 | `TestProtectedResources::test_missing_purchase_is_named_not_worked_around` | LOGIC | KEEP |  |
| 470 | `TestFullResourceSet::test_signed_in_page_feeds_every_existing_extractor` | LOGIC | KEEP |  |
| 539 | `TestPromptNeverCarriesSession::test_the_only_llm_call_in_sources_goes_through_prompt_safe` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 557 | `TestPromptNeverCarriesSession::test_prompt_building_code_never_touches_session_state` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 561 | `TestPromptNeverCarriesSession::test_prompt_constants_carry_no_session_state` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 566 | `TestPromptNeverCarriesSession::test_llm_modules_never_import_the_session_layer` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 572 | `TestPromptNeverCarriesSession::test_session_layer_never_imports_the_llm` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 581 | `TestPromptNeverCarriesSession::test_the_app_never_reads_session_data_at_all` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 590 | `TestPromptNeverCarriesSession::test_behaviour_signed_in_import_sends_no_session_or_token` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 630 | `TestPromptNeverCarriesSession::test_prompt_safe_keeps_ordinary_parameters` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 637 | `TestPromptNeverCarriesSession::test_prompt_safe_redacts_a_path_embedded_signed_token` | LOGIC | KEEP | static checks on sources/*, no tab dependency |
| 647 | `TestPromptNeverCarriesSession::test_prompt_safe_keeps_an_ordinary_chapter_or_page_slug` | LOGIC | KEEP | static checks on sources/*, no tab dependency |

### `tests/test_sources_preflight.py` (16 tests: 14 LOGIC, 2 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 47 | `TestAReadableNovelPage::test_it_says_the_site_would_work` | LOGIC | KEEP |  |
| 54 | `TestAReadableNovelPage::test_it_reports_how_much_text_and_how_confident` | LOGIC | KEEP |  |
| 59 | `TestAReadableNovelPage::test_it_reports_whether_a_series_can_be_followed` | LOGIC | KEEP |  |
| 68 | `TestAReadableNovelPage::test_it_says_so_when_chapters_cannot_be_followed` | LOGIC | KEEP |  |
| 73 | `TestAReadableNovelPage::test_it_reads_as_a_report_a_person_can_follow` | LOGIC | KEEP |  |
| 80 | `TestEveryOrdinaryNoIsAnAnswerNotAnException::test_prohibited_terms_are_reported_not_raised` | LOGIC | KEEP |  |
| 94 | `TestEveryOrdinaryNoIsAnAnswerNotAnException::test_an_unreachable_page_is_reported` | LOGIC | KEEP |  |
| 101 | `TestEveryOrdinaryNoIsAnAnswerNotAnException::test_a_page_with_no_real_text_is_refused_clearly` | LOGIC | KEEP |  |
| 110 | `TestEveryOrdinaryNoIsAnAnswerNotAnException::test_an_empty_url_is_an_answer_too` | LOGIC | KEEP |  |
| 117 | `TestTheTranslatedPageWarning::test_an_already_translated_page_warns_before_importing` | LOGIC | KEEP |  |
| 133 | `TestTheTranslatedPageWarning::test_an_ordinary_page_carries_no_warning` | LOGIC | KEEP |  |
| 139 | `TestComicAndOtherPages::test_a_comic_page_is_judged_on_its_images` | LOGIC | KEEP |  |
| 150 | `TestComicAndOtherPages::test_a_registered_adapter_answers_by_existing` | LOGIC | KEEP |  |
| 162 | `TestItOnlyFetchesOnce::test_the_page_is_fetched_a_single_time` | LOGIC | KEEP |  |
| 173 | `TestItIsActuallyReachableInTheApp::test_the_front_door_offers_the_check` | UI | DELETE with tab | sources_tab source check |
| 188 | `TestItIsActuallyReachableInTheApp::test_the_button_key_is_not_the_state_key` | UI | DELETE with tab | sources_tab source check |

### `tests/test_sources_tab.py` (11 tests: 0 LOGIC, 11 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 31 | `TestSourcesTab::test_renders_with_no_dramas_and_no_sources_used` | UI | DELETE with tab | AppTest |
| 38 | `TestSourcesTab::test_demo_series_browser_lists_sorted_chapters` | UI | DELETE with tab | AppTest |
| 47 | `TestSourcesTab::test_chapter_rows_are_checkboxes_not_a_data_editor` | UI | DELETE with tab | AppTest |
| 64 | `TestSourcesTab::test_demo_challenge_shows_the_handoff` | UI | DELETE with tab | AppTest |
| 73 | `TestSourcesTab::test_saving_settings_persists_them` | UI | DELETE with tab | AppTest |
| 86 | `TestSourcesTab::test_series_browser_fetches_the_chapter_list_once_across_reruns` | UI | DELETE with tab | AppTest |
| 104 | `TestSourcesTab::test_adult_toggle_shown_only_for_sources_that_support_it` | UI | DELETE with tab | AppTest |
| 115 | `TestSourcesTab::test_test_now_buttons_disabled_for_a_tos_prohibited_source` | UI | DELETE with tab | AppTest |
| 148 | `TestSourcesTab::test_importing_video_into_a_drama_with_existing_audio_needs_confirmation` | UI | DELETE with tab | AppTest |
| 161 | `TestSourcesTab::test_importing_video_into_a_fresh_drama_needs_no_confirmation` | UI | DELETE with tab | AppTest |
| 172 | `TestSourcesTab::test_checking_the_confirmation_allows_importing_over_existing_audio` | UI | DELETE with tab | AppTest |

### `tests/test_speaker_rerun.py` (19 tests: 10 LOGIC, 9 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 34 | `TestMergeSpeakers::test_relabels_without_touching_text_or_timing` | LOGIC | KEEP |  |
| 41 | `TestMergeSpeakers::test_a_hand_corrected_speaker_survives_by_default` | LOGIC | KEEP |  |
| 50 | `TestMergeSpeakers::test_overwrites_it_only_when_explicitly_told_to` | LOGIC | KEEP |  |
| 58 | `TestTurnsAreStored::test_round_trip` | LOGIC | KEEP |  |
| 62 | `TestTurnsAreStored::test_nothing_stored_yet` | LOGIC | KEEP |  |
| 85 | `TestModelChoice::test_community_1_is_tried_first` | LOGIC | KEEP |  |
| 91 | `TestModelChoice::test_falls_back_to_3_1` | LOGIC | KEEP |  |
| 95 | `TestModelChoice::test_raises_if_neither_loads` | LOGIC | KEEP |  |
| 193 | `TestCliDiarize::test_changing_expected_speakers_relabels_without_asr` | LOGIC | REPOINTED | no_asr fixture no longer patches tabs.workspace_tab (dead patch: the tab never calls the bare name) |
| 203 | `TestCliDiarize::test_a_manual_correction_survives_unless_overwrite_manual` | LOGIC | REPOINTED | no_asr fixture no longer patches tabs.workspace_tab (dead patch: the tab never calls the bare name) |
| 242 | `TestRerunButton::test_rerun_relabels_from_stored_audio_without_asr` | UI | DELETE with tab | AppTest |
| 252 | `TestRerunButton::test_asks_before_touching_a_hand_correction_and_keeps_it_by_default` | UI | DELETE with tab | AppTest |
| 270 | `TestRerunButton::test_overwrites_only_after_explicit_confirmation` | UI | DELETE with tab | AppTest |
| 283 | `TestRerunButton::test_editing_a_speaker_in_review_marks_it_as_a_manual_correction` | UI | DELETE with tab | AppTest |
| 318 | `TestExpectedSpeakersDefaultsToLastRun::test_defaults_to_0_when_no_run_has_ever_happened` | UI | DELETE with tab | AppTest |
| 323 | `TestExpectedSpeakersDefaultsToLastRun::test_defaults_to_the_count_used_for_the_last_real_run` | UI | DELETE with tab | AppTest |
| 329 | `TestExpectedSpeakersDefaultsToLastRun::test_a_prior_auto_detect_run_still_defaults_to_0` | UI | DELETE with tab | AppTest |
| 335 | `TestExpectedSpeakersDefaultsToLastRun::test_manually_changing_it_survives_a_rerun_rather_than_snapping_back` | UI | DELETE with tab | AppTest |
| 375 | `TestExpectedSpeakersCapturedAtJobStart::test_widget_change_while_the_job_is_still_running_does_not_corrupt_its_result` | UI | DELETE with tab | AppTest |

### `tests/test_step20_ux_polish.py` (18 tests: 10 LOGIC, 8 UI)

Top-level UI/Streamlit imports: `tabs.workspace_tab (_page_for_line, _search_transcript, _adjacent_flagged_idx)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 27 | `TestPageForLine::test_first_page` | UI | DROP (Streamlit-only helper) -> delete | Streamlit page maths; server paging is review_lines_service.list_review_lines |
| 32 | `TestPageForLine::test_later_page` | UI | DROP (Streamlit-only helper) -> delete | Streamlit page maths; server paging is review_lines_service.list_review_lines |
| 37 | `TestPageForLine::test_uses_position_in_the_list_not_the_idx_value` | UI | DROP (Streamlit-only helper) -> delete | Streamlit page maths; server paging is review_lines_service.list_review_lines |
| 48 | `TestPageForLine::test_missing_idx_defaults_to_first_page` | UI | DROP (Streamlit-only helper) -> delete | Streamlit page maths; server paging is review_lines_service.list_review_lines |
| 60 | `TestAdjacentFlaggedIdx::test_forward_finds_the_next_flagged_line` | LOGIC | NEEDS EXTRACTION | _adjacent_flagged_idx -> review_lines_service if cross-page flagged navigation is built (backlog R08) |
| 65 | `TestAdjacentFlaggedIdx::test_backward_finds_the_previous_flagged_line` | LOGIC | NEEDS EXTRACTION | _adjacent_flagged_idx -> review_lines_service if cross-page flagged navigation is built (backlog R08) |
| 70 | `TestAdjacentFlaggedIdx::test_none_when_nothing_further_in_that_direction` | LOGIC | NEEDS EXTRACTION | _adjacent_flagged_idx -> review_lines_service if cross-page flagged navigation is built (backlog R08) |
| 75 | `TestAdjacentFlaggedIdx::test_none_when_nothing_flagged_at_all` | LOGIC | NEEDS EXTRACTION | _adjacent_flagged_idx -> review_lines_service if cross-page flagged navigation is built (backlog R08) |
| 82 | `TestSearchTranscript::test_finds_a_term_in_the_translated_text` | LOGIC | COVERED (equivalent service test exists) -> delete | test_review_lines_service (search_lines: case, zh/en, order, blank term) |
| 88 | `TestSearchTranscript::test_finds_a_term_in_the_source_text` | LOGIC | COVERED (equivalent service test exists) -> delete | test_review_lines_service (search_lines: case, zh/en, order, blank term) |
| 94 | `TestSearchTranscript::test_case_insensitive` | LOGIC | COVERED (equivalent service test exists) -> delete | test_review_lines_service (search_lines: case, zh/en, order, blank term) |
| 100 | `TestSearchTranscript::test_empty_term_matches_nothing` | LOGIC | COVERED (equivalent service test exists) -> delete | test_review_lines_service (search_lines: case, zh/en, order, blank term) |
| 105 | `TestSearchTranscript::test_no_match_returns_empty` | LOGIC | COVERED (equivalent service test exists) -> delete | test_review_lines_service (search_lines: case, zh/en, order, blank term) |
| 109 | `TestSearchTranscript::test_matches_in_line_order` | LOGIC | COVERED (equivalent service test exists) -> delete | test_review_lines_service (search_lines: case, zh/en, order, blank term) |
| 144 | `TestTranscriptSearchJumpsToCorrectPage::test_search_jumps_to_the_matching_lines_page` | UI | DELETE with tab | AppTest |
| 156 | `TestTranscriptSearchJumpsToCorrectPage::test_no_match_shows_no_jump_button` | UI | DELETE with tab | AppTest |
| 189 | `TestAutoQCToastVsWarning::test_nothing_to_flag_shows_a_toast` | UI | DELETE with tab | AppTest |
| 197 | `TestAutoQCToastVsWarning::test_a_real_mismatch_stays_a_warning_not_a_toast` | UI | DELETE with tab | AppTest |

### `tests/test_transcription_quality.py` (17 tests: 16 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 47 | `TestWhisperSettings::test_anti_loop_kwargs_are_sent` | LOGIC | KEEP |  |
| 55 | `TestWhisperSettings::test_the_hallucination_filter_is_still_the_backstop` | LOGIC | KEEP |  |
| 63 | `TestWhisperSettings::test_fast_mode_uses_the_batched_pipeline_with_the_same_settings` | LOGIC | KEEP |  |
| 69 | `TestWhisperSettings::test_large_v3_turbo_is_offered_but_flagged_for_japanese_and_korean` | LOGIC | KEEP |  |
| 84 | `TestForcedAlignerReliability::test_chunks_default_to_about_a_minute` | LOGIC | KEEP |  |
| 93 | `TestForcedAlignerReliability::test_detects_zero_duration_and_backwards_units` | LOGIC | KEEP |  |
| 100 | `TestForcedAlignerReliability::test_a_broken_line_falls_back_to_diff_alignment_and_is_flagged` | LOGIC | KEEP |  |
| 165 | `TestVocalSeparationBackends::test_auto_prefers_mel_band_roformer` | LOGIC | KEEP |  |
| 181 | `TestVocalSeparationBackends::test_auto_falls_back_to_demucs_when_audio_separator_is_missing` | LOGIC | KEEP |  |
| 190 | `TestVocalSeparationBackends::test_auto_falls_back_to_demucs_when_roformer_fails` | LOGIC | KEEP |  |
| 201 | `TestVocalSeparationBackends::test_a_named_backend_uses_only_that_one` | LOGIC | KEEP |  |
| 209 | `TestVocalSeparationBackends::test_both_failing_reports_both` | LOGIC | KEEP |  |
| 244 | `TestSenseVoiceTags::test_parses_emotion_and_events_from_raw_tokens` | LOGIC | KEEP |  |
| 252 | `TestSenseVoiceTags::test_results_are_matched_to_lines_by_id_not_position` | LOGIC | KEEP |  |
| 264 | `TestSenseVoiceTags::test_missing_funasr_is_a_clear_error` | LOGIC | KEEP |  |
| 271 | `TestSenseVoiceTags::test_surfaced_alongside_text_emotions_never_merged` | LOGIC | KEEP |  |
| 305 | `TestSenseVoiceTags::test_workspace_shows_both_columns` | UI | DELETE with tab | AppTest |

### `tests/test_translate_tab.py` (8 tests: 0 LOGIC, 8 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 27 | `TestTranslateTab::test_renders_without_error` | UI | DELETE with tab | AppTest |
| 31 | `TestTranslateTab::test_translating_with_test_offline_saves_history` | UI | DELETE with tab | AppTest |
| 43 | `TestTranslateTab::test_english_to_chinese_direction_saves_the_right_languages` | UI | DELETE with tab | AppTest |
| 54 | `TestTranslateTab::test_libretranslate_english_to_cjk_shows_a_refusal_and_saves_nothing` | UI | DELETE with tab | AppTest |
| 65 | `TestTranslateTab::test_clear_history_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 73 | `TestTranslateTab::test_clear_history_button_empties_it_once_confirmed` | UI | DELETE with tab | AppTest |
| 80 | `TestTranslateTab::test_engine_setup_failure_never_shows_a_raw_key_in_the_error` | UI | DELETE with tab | AppTest |
| 104 | `TestTranslateTab::test_translation_failure_never_shows_a_raw_key_in_the_error` | UI | DELETE with tab | AppTest |

### `tests/test_translation_memory.py` (24 tests: 17 LOGIC, 7 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 25 | `TestFindMatch::test_exact_match` | LOGIC | KEEP |  |
| 30 | `TestFindMatch::test_whitespace_differences_still_count_as_exact` | LOGIC | KEEP |  |
| 34 | `TestFindMatch::test_near_identical_line_matches` | LOGIC | KEEP |  |
| 39 | `TestFindMatch::test_genuinely_different_line_gets_nothing` | LOGIC | KEEP |  |
| 42 | `TestFindMatch::test_exact_match_beats_a_closer_looking_fuzzy_one` | LOGIC | KEEP |  |
| 47 | `TestFindMatch::test_best_fuzzy_match_wins` | LOGIC | KEEP |  |
| 52 | `TestFindMatch::test_empty_source_or_memory` | LOGIC | KEEP |  |
| 58 | `TestSuggestForLines::test_repeated_line_suggested_new_line_not` | LOGIC | KEEP |  |
| 65 | `TestSuggestForLines::test_line_already_using_the_remembered_translation_needs_no_suggestion` | LOGIC | KEEP |  |
| 70 | `TestSuggestForLines::test_suggesting_never_touches_the_line` | LOGIC | KEEP |  |
| 78 | `TestTranslationMemoryStorage::test_record_then_same_pair_counts_another_use` | LOGIC | KEEP |  |
| 85 | `TestTranslationMemoryStorage::test_new_translation_for_same_source_replaces_and_restarts_count` | LOGIC | KEEP |  |
| 93 | `TestTranslationMemoryStorage::test_blank_or_seriesless_input_is_ignored` | LOGIC | KEEP |  |
| 100 | `TestTranslationMemoryStorage::test_memory_is_scoped_per_series` | LOGIC | KEEP |  |
| 106 | `TestTranslationMemoryStorage::test_bump_use` | LOGIC | KEEP |  |
| 113 | `TestTranslationMemoryStorage::test_replace_updates_matching_rows_only_in_that_series` | LOGIC | KEEP |  |
| 124 | `TestTranslationMemoryStorage::test_replace_that_empties_the_translation_drops_the_row` | LOGIC | KEEP |  |
| 161 | `TestTranslationMemoryInReview::test_suggestion_shown_for_near_identical_line_but_not_applied` | UI | DELETE with tab | AppTest |
| 172 | `TestTranslationMemoryInReview::test_accept_applies_to_that_line_only_and_counts_a_use` | UI | DELETE with tab | AppTest |
| 185 | `TestTranslationMemoryInReview::test_dismiss_hides_it_without_changing_anything` | UI | DELETE with tab | AppTest |
| 193 | `TestTranslationMemoryInReview::test_no_series_means_no_memory` | UI | DELETE with tab | AppTest |
| 200 | `TestTranslationMemoryInReview::test_hand_edit_saved_is_remembered` | UI | DELETE with tab | AppTest |
| 209 | `TestTranslationMemoryInReview::test_untouched_lines_are_not_remembered_on_save` | UI | DELETE with tab | AppTest |
| 215 | `TestTranslationMemoryInReview::test_find_and_replace_corrects_the_remembered_translation` | UI | DELETE with tab | AppTest |

### `tests/test_ui_components.py` (32 tests: 1 LOGIC, 31 UI)

Top-level UI/Streamlit imports: `streamlit`, `streamlit.testing.v1 (AppTest)`, `ui (project_state, workflow)`

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 34 | `TestProjectState::test_get_project_creates_defaults` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 42 | `TestProjectState::test_get_project_returns_the_same_dict_object` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 49 | `TestProjectState::test_round_trips_drama_id_and_current_stage` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 56 | `TestProjectState::test_stage_status_selection_and_active_job_round_trip` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 67 | `TestProjectState::test_errors_and_warnings_accumulate_and_clear` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 77 | `TestProjectState::test_switching_drama_clears_stage_and_selection_but_not_drama_id` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 89 | `TestProjectState::test_resetting_the_same_drama_id_keeps_its_state` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 95 | `TestProjectState::test_reset_project_gives_back_fresh_defaults` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 102 | `TestProjectState::test_new_state_dicts_are_never_shared` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 111 | `TestProjectState::test_does_not_touch_narrowly_scoped_keys` | UI | DELETE with tab | ui.project_state is a Streamlit session-state store |
| 149 | `TestWorkflowStepper::test_stage_statuses_from_index` | LOGIC | NEEDS EXTRACTION | ui.workflow.stage_statuses_from_index -> services.workflow_service |
| 158 | `TestWorkflowStepper::test_render_stepper_rejects_mismatched_lengths` | UI | DELETE with tab | stepper rendering |
| 170 | `TestWorkflowStepper::test_render_stepper_shows_a_check_for_done_stages` | UI | DELETE with tab | stepper rendering |
| 177 | `TestWorkflowStepper::test_render_stepper_shows_a_dot_for_the_current_stage` | UI | DELETE with tab | stepper rendering |
| 184 | `TestWorkflowStepper::test_render_stepper_shows_a_circle_for_not_started_stages` | UI | DELETE with tab | stepper rendering |
| 190 | `TestWorkflowStepper::test_render_stepper_from_index_matches_render_stepper` | UI | DELETE with tab | stepper rendering |
| 205 | `TestProjectHeader::test_shows_the_drama_name_and_stepper` | UI | DELETE with tab | AppTest |
| 215 | `TestProjectHeader::test_falls_back_to_title_zh_then_drama_id` | UI | DELETE with tab | AppTest |
| 223 | `TestProjectHeader::test_shows_a_placeholder_when_no_drama_is_open` | UI | DELETE with tab | AppTest |
| 231 | `TestProjectHeader::test_omits_the_stepper_when_no_stages_given` | UI | DELETE with tab | AppTest |
| 239 | `TestProjectHeader::test_settings_shortcut_click_is_reported_back_to_the_caller` | UI | DELETE with tab | AppTest |
| 264 | `TestStatusBlock::test_no_job_renders_nothing` | UI | DELETE with tab | AppTest |
| 273 | `TestStatusBlock::test_queued_job_shows_an_info_message` | UI | DELETE with tab | AppTest |
| 282 | `TestStatusBlock::test_running_job_shows_a_progress_bar_with_its_message` | UI | DELETE with tab | AppTest |
| 293 | `TestStatusBlock::test_running_job_falls_back_to_the_running_label` | UI | DELETE with tab | AppTest |
| 302 | `TestStatusBlock::test_done_job_shows_success_only_when_a_message_is_given` | UI | DELETE with tab | AppTest |
| 310 | `TestStatusBlock::test_done_job_with_no_message_stays_silent` | UI | DELETE with tab | AppTest |
| 318 | `TestStatusBlock::test_error_job_shows_a_plain_reason_and_hides_the_traceback_by_default` | UI | DELETE with tab | AppTest |
| 338 | `TestStatusBlock::test_explicit_reason_overrides_the_default_de_technicalization` | UI | DELETE with tab | AppTest |
| 347 | `TestStatusBlock::test_retry_button_shown_and_wired_only_when_on_retry_is_given` | UI | DELETE with tab | AppTest |
| 365 | `TestStatusBlock::test_no_retry_button_when_retry_is_not_safe` | UI | DELETE with tab | AppTest |
| 373 | `TestStatusBlock::test_render_failure_card_directly` | UI | DELETE with tab | AppTest |

### `tests/test_workflow_service.py` (12 tests: 12 LOGIC, 0 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 24 | `TestWorkspaceStageIndex::test_brand_new_drama_with_no_source_content_is_still_on_source` | LOGIC | KEEP |  |
| 31 | `TestWorkspaceStageIndex::test_source_uploaded_but_not_yet_transcribed_is_on_transcript` | LOGIC | KEEP |  |
| 36 | `TestWorkspaceStageIndex::test_novel_narration_with_no_saved_novel_text_is_still_on_source` | LOGIC | KEEP |  |
| 41 | `TestWorkspaceStageIndex::test_novel_narration_with_saved_novel_text_is_on_transcript` | LOGIC | KEEP |  |
| 47 | `TestWorkspaceStageIndex::test_lines_with_no_speaker_yet_is_on_diarize` | LOGIC | KEEP |  |
| 52 | `TestWorkspaceStageIndex::test_novel_narration_has_no_diarize_stage` | LOGIC | KEEP |  |
| 60 | `TestWorkspaceStageIndex::test_partway_translated_shows_transcribe_diarize_done_translate_current` | LOGIC | NEEDS EXTRACTION | imports ui.workflow.stage_statuses_from_index; move it into services.workflow_service |
| 77 | `TestWorkspaceStageIndex::test_fully_translated_not_yet_dubbed_or_exported_is_on_review` | LOGIC | KEEP |  |
| 82 | `TestWorkspaceStageIndex::test_dub_track_on_disk_moves_to_export` | LOGIC | KEEP |  |
| 88 | `TestWorkspaceStageIndex::test_exported_status_is_fully_done` | LOGIC | NEEDS EXTRACTION | same |
| 97 | `TestWorkspaceStageIndex::test_exported_with_no_persisted_speaker_still_shows_export_not_diarize` | LOGIC | KEEP |  |
| 110 | `TestWorkspaceStageIndex::test_fully_translated_with_no_persisted_speaker_shows_review_not_diarize` | LOGIC | KEEP |  |

### `tests/test_workspace_job_service.py` (4 tests: 3 LOGIC, 1 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 15 | `test_never_imports_streamlit` | LOGIC | KEEP |  |
| 27 | `test_every_moved_workspace_function_is_exported` | LOGIC | KEEP |  |
| 35 | `test_every_moved_library_function_is_exported` | LOGIC | KEEP |  |
| 40 | `test_tabs_re_export_the_same_function_objects_not_copies` | UI | DELETE with tab | asserts the tab re-exports |

### `tests/test_workspace_raw_novel.py` (2 tests: 0 LOGIC, 2 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 45 | `TestRawNovelContextRemove::test_remove_actually_deletes_the_file` | UI | DELETE with tab | AppTest |
| 62 | `TestRawNovelContextRemove::test_remove_sticks_even_with_a_file_still_selected_in_the_uploader` | UI | DELETE with tab | AppTest |

### `tests/test_workspace_tab.py` (284 tests: 71 LOGIC, 213 UI)

Top-level UI/Streamlit imports: none

| Line | Test | Class | Action | Note |
|---|---|---|---|---|
| 66 | `test_success_stores_segments_and_no_gpu_fallback` | LOGIC | KEEP |  |
| 84 | `test_gpu_fallback_message_is_captured` | LOGIC | KEEP |  |
| 103 | `test_progress_cb_is_wired_to_update_progress` | LOGIC | KEEP |  |
| 123 | `test_vocal_separation_runs_before_transcription_and_feeds_its_output` | LOGIC | KEEP |  |
| 157 | `test_vocal_separation_off_by_default_transcribes_the_original_audio` | LOGIC | KEEP |  |
| 175 | `test_groq_transcription_is_used_instead_of_local_whisper_when_enabled` | LOGIC | KEEP |  |
| 209 | `test_groq_failure_is_recorded_not_raised` | LOGIC | KEEP |  |
| 232 | `test_use_groq_off_by_default_still_uses_local_whisper` | LOGIC | KEEP |  |
| 251 | `test_vocal_separation_failure_is_recorded_not_raised` | LOGIC | KEEP |  |
| 278 | `test_vocal_separation_cancel_reports_cancelled_and_never_starts_transcription` | LOGIC | KEEP |  |
| 307 | `test_cancel_right_after_vocal_separation_finishes_stops_before_transcription_starts` | LOGIC | KEEP |  |
| 343 | `test_cancel_before_transcription_starts_with_separation_off_also_stops_it` | LOGIC | KEEP |  |
| 365 | `test_vocal_separation_threads_progress_and_cancel_callbacks_through` | LOGIC | KEEP |  |
| 399 | `test_cancel_after_transcription_skips_realign_but_keeps_the_transcript` | LOGIC | KEEP |  |
| 437 | `test_realign_long_segments_runs_after_transcription` | LOGIC | KEEP |  |
| 470 | `test_realign_off_by_default_leaves_segments_unchanged` | LOGIC | KEEP |  |
| 486 | `test_realign_missing_dependency_keeps_the_transcript_and_reports_the_issue` | LOGIC | KEEP |  |
| 512 | `test_model_download_failure_is_recorded_not_raised` | LOGIC | KEEP |  |
| 531 | `test_empty_segments_is_recorded_not_raised` | LOGIC | KEEP |  |
| 546 | `test_unexpected_exception_still_propagates` | LOGIC | KEEP |  |
| 566 | `test_hardsub_ocr_success_stores_segments` | LOGIC | KEEP |  |
| 586 | `test_hardsub_ocr_progress_cb_is_wired` | LOGIC | KEEP |  |
| 612 | `test_hardsub_ocr_empty_cues_is_recorded_not_raised` | LOGIC | KEEP |  |
| 631 | `test_hardsub_ocr_unexpected_exception_still_propagates` | LOGIC | KEEP |  |
| 669 | `test_flag_job_persists_flags_to_db` | LOGIC | KEEP |  |
| 691 | `test_flag_job_progress_cb_is_wired` | LOGIC | KEEP |  |
| 706 | `test_flag_job_with_nothing_flagged` | LOGIC | KEEP |  |
| 734 | `test_flag_job_logs_usage_to_the_cost_dashboard` | LOGIC | KEEP |  |
| 750 | `test_emotion_job_persists_to_db_and_logs_usage` | LOGIC | KEEP |  |
| 791 | `test_consistency_job_persists_and_logs_usage` | LOGIC | KEEP |  |
| 834 | `test_translation_notes_job_persists_and_logs_usage` | LOGIC | KEEP |  |
| 897 | `test_fix_flagged_job_retranscribes_and_retranslates_with_audio` | LOGIC | REPOINTED | patches core_module (was the same object via tabs.workspace_tab.core_module) |
| 933 | `test_fix_flagged_job_skips_retranscription_with_no_audio` | LOGIC | KEEP |  |
| 955 | `test_fix_flagged_job_leaves_flag_set_when_translation_fails` | LOGIC | KEEP |  |
| 980 | `test_fix_flagged_job_ignores_unflagged_lines` | LOGIC | KEEP |  |
| 1017 | `test_fix_flagged_job_stops_at_the_cost_cap_and_keeps_finished_lines` | LOGIC | KEEP |  |
| 1051 | `test_fix_flagged_job_with_no_cap_processes_every_flagged_line` | LOGIC | KEEP |  |
| 1074 | `test_fix_flagged_job_keeps_earlier_fixes_when_a_later_line_crashes` | LOGIC | REPOINTED | patches core_module (was the same object via tabs.workspace_tab.core_module) |
| 1130 | `test_translate_job_resolves_named_characters_for_the_engine` | LOGIC | KEEP |  |
| 1156 | `test_translate_job_omits_speakers_with_no_name_set` | LOGIC | KEEP |  |
| 1178 | `test_translate_job_sends_a_standalone_dramas_per_drama_pronouns` | LOGIC | KEEP |  |
| 1220 | `TestFreeEngineVersionLabelling::test_test_offline_version_is_labelled` | LOGIC | KEEP |  |
| 1226 | `TestFreeEngineVersionLabelling::test_ollama_version_is_labelled` | LOGIC | KEEP |  |
| 1232 | `TestFreeEngineVersionLabelling::test_claude_version_is_not_labelled` | LOGIC | KEEP |  |
| 1238 | `TestFreeEngineVersionLabelling::test_paid_gemini_version_is_not_labelled` | LOGIC | KEEP |  |
| 1245 | `TestFreeEngineVersionLabelling::test_free_tier_gemini_version_is_labelled` | LOGIC | KEEP |  |
| 1294 | `TestLineEditingNotLockedDuringAJob::test_save_edits_stays_enabled_while_translate_is_running` | UI | DELETE with tab | AppTest |
| 1305 | `TestLineEditingNotLockedDuringAJob::test_apply_merge_stays_enabled_while_a_flag_job_is_running` | UI | DELETE with tab | AppTest |
| 1317 | `TestLineEditingNotLockedDuringAJob::test_generate_dub_track_stays_enabled_while_translate_is_running` | UI | DELETE with tab | AppTest |
| 1327 | `TestLineEditingNotLockedDuringAJob::test_chunk_and_tag_speakers_stays_enabled_while_translate_is_running` | UI | DELETE with tab | AppTest |
| 1379 | `TestChunkAndTagSpeakersHistorySnapshot::test_a_history_snapshot_exists_before_chunk_and_tag_replaces_lines` | UI | DELETE with tab | AppTest |
| 1389 | `TestChunkAndTagSpeakersHistorySnapshot::test_existing_translation_is_recoverable_via_version_history_afterward` | UI | DELETE with tab | AppTest |
| 1455 | `TestOllamaReachabilityGatesTranslateButton::test_translate_is_disabled_and_warns_when_ollama_is_unreachable` | UI | DELETE with tab | AppTest |
| 1462 | `TestOllamaReachabilityGatesTranslateButton::test_translate_is_enabled_when_ollama_is_reachable` | UI | DELETE with tab | AppTest |
| 1503 | `TestTranslateButtonUsesConfiguredOllamaUrl::test_translate_uses_the_configured_ollama_url_not_the_hardcoded_default` | UI | DELETE with tab | AppTest |
| 1562 | `TestContextAheadAndBatchSizeSliders::test_audio_drama_starts_at_todays_defaults` | UI | DELETE with tab | AppTest |
| 1569 | `TestContextAheadAndBatchSizeSliders::test_novel_narration_pre_selects_higher_starting_values` | UI | DELETE with tab | AppTest |
| 1576 | `TestContextAheadAndBatchSizeSliders::test_look_ahead_and_batch_size_reach_the_translate_job` | UI | DELETE with tab | AppTest |
| 1586 | `TestContextAheadAndBatchSizeSliders::test_moving_the_sliders_changes_what_reaches_the_job` | UI | DELETE with tab | AppTest |
| 1598 | `TestContextAheadAndBatchSizeSliders::test_run_translate_job_forwards_both_to_translate_lines_with_engine` | LOGIC | KEEP |  |
| 1662 | `TestGlossaryReviewBeforeTranslating::test_checkbox_hidden_without_a_series` | UI | DELETE with tab | AppTest |
| 1667 | `TestGlossaryReviewBeforeTranslating::test_unchecked_by_default_job_starts_immediately` | UI | DELETE with tab | AppTest |
| 1678 | `TestGlossaryReviewBeforeTranslating::test_checked_shows_review_instead_of_starting_the_job` | UI | DELETE with tab | AppTest |
| 1695 | `TestGlossaryReviewBeforeTranslating::test_confirming_adds_the_term_and_starts_the_job` | UI | DELETE with tab | AppTest |
| 1716 | `TestGlossaryReviewBeforeTranslating::test_cancel_starts_nothing_and_adds_no_terms` | UI | DELETE with tab | AppTest |
| 1762 | `TestDownloadButtonUsesCookieSettings::test_cookies_reach_the_download_call` | UI | DELETE with tab | AppTest |
| 1781 | `TestDownloadButtonUsesCookieSettings::test_no_cookies_configured_passes_none` | UI | DELETE with tab | AppTest |
| 1829 | `TestDownloadAudioOnlyDefault::test_streamer_vod_defaults_to_keeping_the_video` | UI | DELETE with tab | AppTest |
| 1835 | `TestDownloadAudioOnlyDefault::test_audio_drama_still_defaults_to_audio_only` | UI | DELETE with tab | AppTest |
| 1841 | `TestDownloadAudioOnlyDefault::test_streamer_vod_download_defaults_to_audio_only_false` | UI | DELETE with tab | AppTest |
| 1893 | `TestReflectModeUI::test_checkbox_hidden_for_a_translation_only_engine` | UI | DELETE with tab | AppTest |
| 1901 | `TestReflectModeUI::test_checkbox_shown_and_off_by_default_for_an_llm_engine` | UI | DELETE with tab | AppTest |
| 1906 | `TestReflectModeUI::test_checking_it_shows_a_cost_estimate` | UI | DELETE with tab | AppTest |
| 1912 | `TestReflectModeUI::test_reflect_mode_reaches_the_background_job` | UI | DELETE with tab | AppTest |
| 1926 | `TestReflectModeUI::test_unchecked_reflect_defaults_to_false_on_the_job` | UI | DELETE with tab | AppTest |
| 1974 | `TestBulkGlossaryAndPronounActions::test_bulk_delete_removes_only_selected_glossary_terms` | UI | DELETE with tab | AppTest |
| 1988 | `TestBulkGlossaryAndPronounActions::test_no_bulk_actions_shown_with_no_glossary_terms` | UI | DELETE with tab | AppTest |
| 1993 | `TestBulkGlossaryAndPronounActions::test_bulk_set_pronouns_applies_to_only_selected_people` | UI | DELETE with tab | AppTest |
| 2010 | `TestBulkGlossaryAndPronounActions::test_no_bulk_pronoun_control_shown_with_no_people` | UI | DELETE with tab | AppTest |
| 2015 | `TestBulkGlossaryAndPronounActions::test_glossary_editor_saves_aliases_and_banned_translations` | UI | DELETE with tab | AppTest |
| 2070 | `TestVoiceMatchSuggestions::test_a_matching_voice_shows_a_suggestion` | UI | DELETE with tab | AppTest |
| 2076 | `TestVoiceMatchSuggestions::test_no_suggestion_below_threshold` | UI | DELETE with tab | AppTest |
| 2084 | `TestVoiceMatchSuggestions::test_accept_sets_the_character_name_and_updates_the_fingerprint` | UI | DELETE with tab | AppTest |
| 2100 | `TestVoiceMatchSuggestions::test_reject_dismisses_without_changing_anything` | UI | DELETE with tab | AppTest |
| 2116 | `TestVoiceMatchSuggestions::test_rejecting_never_re_shows_that_pair` | UI | DELETE with tab | AppTest |
| 2162 | `TestCharacterNamingGaps::test_a_speaker_with_lines_shows_a_real_sample` | UI | DELETE with tab | AppTest |
| 2168 | `TestCharacterNamingGaps::test_a_speaker_with_no_lines_says_so_instead_of_showing_nothing` | UI | DELETE with tab | AppTest |
| 2174 | `TestCharacterNamingGaps::test_a_skipped_speaker_shows_the_specific_reason_not_a_bare_caption` | UI | DELETE with tab | AppTest |
| 2192 | `TestCharacterNamingGaps::test_a_failed_ref_text_match_shows_a_specific_reason_not_a_blank_box` | UI | DELETE with tab | AppTest |
| 2214 | `TestCharacterNamingGaps::test_a_successful_ref_text_match_is_saved_normally` | UI | DELETE with tab | AppTest |
| 2252 | `TestSpendingCapJob::test_job_stops_at_the_cap_and_keeps_its_finished_lines` | LOGIC | KEEP |  |
| 2274 | `TestSpendingCapJob::test_no_cap_reports_no_stop` | LOGIC | KEEP |  |
| 2315 | `TestSpendingCapUI::test_cap_input_and_estimate_shown_for_a_paid_engine` | UI | DELETE with tab | AppTest |
| 2321 | `TestSpendingCapUI::test_no_cap_input_for_a_free_engine` | UI | DELETE with tab | AppTest |
| 2335 | `TestSpendingCapUI::test_cap_input_now_shown_for_google` | UI | DELETE with tab | AppTest |
| 2346 | `TestSpendingCapUI::test_cap_input_now_shown_for_deepl` | UI | DELETE with tab | AppTest |
| 2356 | `TestSpendingCapUI::test_the_cap_reaches_the_background_job` | UI | DELETE with tab | AppTest |
| 2367 | `TestSpendingCapUI::test_monthly_cap_already_used_up_disables_translate` | UI | DELETE with tab | AppTest |
| 2374 | `TestSpendingCapUI::test_monthly_remainder_becomes_the_jobs_cap` | UI | DELETE with tab | AppTest |
| 2421 | `TestFixFlaggedLinesCapUI::test_monthly_cap_already_used_up_disables_the_fix_button_too` | UI | DELETE with tab | AppTest |
| 2428 | `TestFixFlaggedLinesCapUI::test_the_jobs_cap_reaches_the_fix_flagged_background_job` | UI | DELETE with tab | AppTest |
| 2439 | `TestFixFlaggedLinesCapUI::test_no_cap_set_passes_no_cap_to_the_fix_flagged_job` | UI | DELETE with tab | AppTest |
| 2487 | `TestContentBlockedRetryWithDifferentEngine::test_flag_display_shows_the_real_engine_and_reason` | UI | DELETE with tab | AppTest |
| 2492 | `TestContentBlockedRetryWithDifferentEngine::test_retry_picker_defaults_to_ollama` | UI | DELETE with tab | AppTest |
| 2497 | `TestContentBlockedRetryWithDifferentEngine::test_retry_with_ollama_translates_the_line_and_clears_the_flag` | UI | DELETE with tab | AppTest |
| 2515 | `TestContentBlockedRetryWithDifferentEngine::test_retry_does_not_change_the_drama_s_own_translation_engine` | UI | DELETE with tab | AppTest |
| 2529 | `TestContentBlockedRetryWithDifferentEngine::test_a_second_provider_also_blocking_the_retry_keeps_the_line_flagged` | UI | DELETE with tab | AppTest |
| 2633 | `TestBulkModeUI::test_bulk_checkbox_shown_for_claude_not_for_a_free_engine` | UI | DELETE with tab | AppTest |
| 2639 | `TestBulkModeUI::test_bulk_translate_submits_a_batch_instead_of_a_live_job` | UI | DELETE with tab | AppTest |
| 2656 | `TestBulkModeUI::test_bulk_and_reflect_together_submits_the_faithfulness_stage` | UI | DELETE with tab | AppTest |
| 2677 | `TestBulkModeUI::test_bulk_reflect_is_unavailable_for_deepseek` | UI | DELETE with tab | AppTest |
| 2684 | `TestBulkModeUI::test_panel_lists_a_pending_batch` | UI | DELETE with tab | AppTest |
| 2693 | `TestBulkModeUI::test_panel_restarts_polling_for_a_pending_batch` | UI | DELETE with tab | AppTest |
| 2705 | `TestBulkModeUI::test_cancel_stops_polling` | UI | DELETE with tab | AppTest |
| 2721 | `TestBulkModeUI::test_a_polling_auth_error_shows_on_the_panel` | UI | DELETE with tab | AppTest |
| 2752 | `TestGemini31FlashLiteInDropdown::test_offered_with_the_default_unchanged_and_reaches_the_job` | UI | DELETE with tab | AppTest |
| 2803 | `TestGeminiFreeTierProGating::test_selecting_pro_shows_a_clear_message_and_blocks_translate` | UI | DELETE with tab | AppTest |
| 2819 | `TestGeminiFreeTierProGating::test_flash_lite_is_unaffected` | UI | DELETE with tab | AppTest |
| 2852 | `TestJobEtaDisplay::test_no_eta_at_zero_percent` | UI | DELETE with tab | AppTest |
| 2864 | `TestJobEtaDisplay::test_eta_shown_once_progress_is_non_trivial` | UI | DELETE with tab | AppTest |
| 2876 | `TestJobEtaDisplay::test_no_eta_once_done_shows_success_not_a_bar` | UI | DELETE with tab | AppTest |
| 2931 | `TestMergePreviewDoesNotMutateLiveLines::test_preview_merge_leaves_the_live_lines_untouched` | UI | DELETE with tab | AppTest |
| 2994 | `TestRetranscribeUseThisRefreshesTheZhBox::test_accepting_a_retranscription_is_not_reverted_on_the_next_rerun` | UI | DELETE with tab | AppTest |
| 3058 | `TestRenumberingClearsStaleLineWidgets::test_apply_merge` | UI | DELETE with tab | AppTest |
| 3065 | `TestRenumberingClearsStaleLineWidgets::test_restore` | UI | DELETE with tab | AppTest |
| 3084 | `TestImproveTranslationUseThisRefreshesTheEnBox::test_accepted_improvement_survives_a_rerun` | UI | DELETE with tab | AppTest |
| 3150 | `TestPerLineExplainToolsMovedFromReader::test_why_this_result_for_one_line_is_not_shown_under_another` | UI | DELETE with tab | AppTest |
| 3169 | `TestPerLineExplainToolsMovedFromReader::test_why_this_result_does_not_survive_a_switch_to_another_drama` | UI | DELETE with tab | AppTest |
| 3196 | `TestPerLineExplainToolsMovedFromReader::test_why_this_shows_an_explanation` | UI | DELETE with tab | AppTest |
| 3204 | `TestPerLineExplainToolsMovedFromReader::test_alternatives_lists_each_option` | UI | DELETE with tab | AppTest |
| 3213 | `TestPerLineExplainToolsMovedFromReader::test_grammar_shows_a_breakdown_table` | UI | DELETE with tab | AppTest |
| 3222 | `TestPerLineExplainToolsMovedFromReader::test_pronounce_plays_audio` | UI | DELETE with tab | AppTest |
| 3264 | `TestTranslateJobRefreshesStaleEnBoxes::test_translated_text_shows_immediately_no_hard_refresh_needed` | UI | DELETE with tab | AppTest |
| 3286 | `TestTranslateJobRefreshesStaleEnBoxes::test_bulk_jobs_panel_completion_also_refreshes_the_en_box` | UI | DELETE with tab | AppTest |
| 3309 | `TestTranslateJobRefreshesStaleEnBoxes::test_fix_flagged_lines_completion_also_refreshes_the_zh_and_en_boxes` | UI | DELETE with tab | AppTest |
| 3385 | `TestDramaSwitchResetsLoadedLines::test_switching_shows_the_new_dramas_own_lines_not_the_old_ones` | UI | DELETE with tab | AppTest |
| 3396 | `TestDramaSwitchResetsLoadedLines::test_save_edits_immediately_after_switching_does_not_corrupt_the_new_drama` | UI | DELETE with tab | AppTest |
| 3467 | `TestDramaSwitchKeepsCharactersSeparate::test_switching_back_and_forth_never_leaks_character_data` | UI | DELETE with tab | AppTest |
| 3486 | `TestDramaSwitchKeepsCharactersSeparate::test_editing_a_field_right_after_switching_writes_only_the_new_dramas_data` | UI | DELETE with tab | AppTest |
| 3534 | `TestManualRefreshButton::test_refresh_button_exists_next_to_the_drama_picker` | UI | DELETE with tab | AppTest |
| 3539 | `TestManualRefreshButton::test_refresh_reloads_a_change_made_outside_the_page` | UI | DELETE with tab | AppTest |
| 3558 | `TestManualRefreshButton::test_refresh_is_disabled_for_new_drama` | UI | DELETE with tab | AppTest |
| 3611 | `TestRawNovelToggleGatedByContentMode::test_toggle_hidden_for_streamer_vod` | UI | DELETE with tab | AppTest |
| 3616 | `TestRawNovelToggleGatedByContentMode::test_toggle_hidden_for_novel_narration` | UI | DELETE with tab | AppTest |
| 3621 | `TestRawNovelToggleGatedByContentMode::test_toggle_off_by_default_for_audio_drama_with_no_existing_raw_novel` | UI | DELETE with tab | AppTest |
| 3629 | `TestRawNovelToggleGatedByContentMode::test_toggle_on_when_raw_novel_already_saved` | UI | DELETE with tab | AppTest |
| 3672 | `TestDestructiveActionsNeedConfirmation::test_delete_drama_button_disabled_until_checkbox_and_typed_confirm` | UI | DELETE with tab | AppTest |
| 3689 | `TestDestructiveActionsNeedConfirmation::test_delete_drama_button_deletes_once_confirmed` | UI | DELETE with tab | AppTest |
| 3707 | `TestDestructiveActionsNeedConfirmation::test_remove_audio_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 3713 | `TestDestructiveActionsNeedConfirmation::test_remove_audio_button_removes_file_once_confirmed` | UI | DELETE with tab | AppTest |
| 3732 | `TestDestructiveActionsNeedConfirmation::test_remove_raw_novel_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 3738 | `TestDestructiveActionsNeedConfirmation::test_remove_raw_novel_button_removes_file_once_confirmed` | UI | DELETE with tab | AppTest |
| 3778 | `TestFourMoreDestructiveActionsNeedConfirmation::test_delete_version_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 3783 | `TestFourMoreDestructiveActionsNeedConfirmation::test_delete_version_removes_it_once_confirmed` | UI | DELETE with tab | AppTest |
| 3803 | `TestFourMoreDestructiveActionsNeedConfirmation::test_delete_glossary_term_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 3808 | `TestFourMoreDestructiveActionsNeedConfirmation::test_delete_glossary_term_removes_it_once_confirmed` | UI | DELETE with tab | AppTest |
| 3817 | `TestFourMoreDestructiveActionsNeedConfirmation::test_bulk_delete_glossary_terms_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 3824 | `TestFourMoreDestructiveActionsNeedConfirmation::test_bulk_delete_glossary_terms_removes_them_once_confirmed` | UI | DELETE with tab | AppTest |
| 3834 | `TestFourMoreDestructiveActionsNeedConfirmation::test_remove_series_character_button_disabled_until_confirmed` | UI | DELETE with tab | AppTest |
| 3839 | `TestFourMoreDestructiveActionsNeedConfirmation::test_remove_series_character_removes_it_once_confirmed` | UI | DELETE with tab | AppTest |
| 3857 | `TestDiarizationEstimateCaption::test_caption_scales_with_audio_length` | LOGIC | NEEDS EXTRACTION | _diarization_estimate_caption: move to services/diarization_service if React shows the estimate (backlog D04); else drop |
| 3862 | `TestDiarizationEstimateCaption::test_caption_has_a_generic_fallback_for_unknown_length` | LOGIC | NEEDS EXTRACTION | _diarization_estimate_caption: move to services/diarization_service if React shows the estimate (backlog D04); else drop |
| 3899 | `TestDiarizationEstimateCaption::test_estimate_caption_shown_before_rerunning_speaker_detection` | UI | DELETE with tab | AppTest |
| 3992 | `TestSpeakerDetectionRealMidRunStop::test_clicking_the_button_starts_a_real_background_job_not_a_blocking_call` | UI | DELETE with tab | AppTest |
| 4022 | `TestSpeakerDetectionRealMidRunStop::test_cancel_button_appears_while_running_and_actually_requests_a_stop` | UI | DELETE with tab | AppTest |
| 4057 | `TestSpeakerDetectionRealMidRunStop::test_done_job_result_is_applied_the_same_way_a_synchronous_run_used_to` | UI | DELETE with tab | AppTest |
| 4164 | `TestDubGenerationRealMidRunStop::test_clicking_the_button_starts_a_real_background_job_not_a_blocking_call` | UI | DELETE with tab | AppTest |
| 4187 | `TestDubGenerationRealMidRunStop::test_cancel_button_appears_while_running_and_actually_requests_a_stop` | UI | DELETE with tab | AppTest |
| 4212 | `TestDubGenerationRealMidRunStop::test_done_job_result_is_applied_via_field_scoped_save` | UI | DELETE with tab | AppTest |
| 4317 | `TestResegmentationRealMidRunStop::test_cancel_button_appears_while_running_and_actually_requests_a_stop` | UI | DELETE with tab | AppTest |
| 4341 | `TestResegmentationRealMidRunStop::test_done_job_result_is_applied_as_a_preview_not_saved_directly` | UI | DELETE with tab | AppTest |
| 4410 | `TestVerticalShortsExport::test_estimate_and_range_slider_appear` | UI | DELETE with tab | AppTest |
| 4419 | `TestVerticalShortsExport::test_long_selection_shows_a_soft_prompt_not_a_block` | UI | DELETE with tab | AppTest |
| 4426 | `TestVerticalShortsExport::test_probe_failure_warns_instead_of_crashing` | UI | DELETE with tab | AppTest |
| 4444 | `TestVerticalShortsExport::test_generate_renders_a_timeshifted_clip_and_offers_a_download` | UI | DELETE with tab | AppTest |
| 4538 | `TestSection10StandaloneSubtitleDownload::test_new_buttons_field_tracks_the_selected_language_not_hardcoded` | UI | DELETE with tab | AppTest |
| 4570 | `TestSection10StandaloneSubtitleDownload::test_matches_section_9s_download_for_the_same_default_format_and_language` | UI | DELETE with tab | AppTest |
| 4585 | `TestSection10StandaloneSubtitleDownload::test_file_name_pairs_with_the_video_files_own_naming` | UI | DELETE with tab | AppTest |
| 4619 | `TestWhisperSizeDefaultsToLargeV3::test_new_drama_defaults_to_large_v3` | UI | DELETE with tab | AppTest |
| 4626 | `TestWhisperSizeDefaultsToLargeV3::test_a_drama_with_an_explicit_size_keeps_it` | UI | DELETE with tab | AppTest |
| 4661 | `TestTranscriptionCancelButton::test_cancel_button_appears_while_running_and_actually_requests_a_stop` | UI | DELETE with tab | AppTest |
| 4678 | `TestTranscriptionCancelButton::test_cancelled_outcome_shows_a_clear_message_not_a_generic_error` | UI | DELETE with tab | AppTest |
| 4763 | `TestAutotuneRealMidRunStop::test_clicking_auto_tune_starts_a_real_background_job_not_a_blocking_call` | UI | DELETE with tab | AppTest |
| 4788 | `TestAutotuneRealMidRunStop::test_cancel_button_appears_while_running_and_actually_requests_a_stop` | UI | DELETE with tab | AppTest |
| 4816 | `TestAutotuneRealMidRunStop::test_each_candidate_produces_a_distinct_coverage_result_and_chains_to_the_next` | UI | DELETE with tab | AppTest |
| 4865 | `TestAutotuneRealMidRunStop::test_page_refresh_while_a_result_is_ready_does_not_crash` | UI | DELETE with tab | AppTest |
| 4893 | `TestAutotuneRealMidRunStop::test_nothing_is_applied_until_the_user_explicitly_picks_one` | UI | DELETE with tab | AppTest |
| 4979 | `TestTranscribeQueuesBehindAnotherGpuJob::test_transcription_queues_while_another_gpu_job_is_running` | UI | DELETE with tab | AppTest |
| 5046 | `TestQueuedJobPanelVisibleAndCancellable::test_queued_transcription_stays_visible_with_a_cancel_button_on_a_later_rerun` | UI | DELETE with tab | AppTest |
| 5092 | `TestRomanizeCreditsEnginePassesOllamaUrlAndFreeTier::test_ollama_base_url_is_passed_through` | UI | DELETE with tab | AppTest |
| 5109 | `TestRomanizeCreditsEnginePassesOllamaUrlAndFreeTier::test_gemini_free_tier_flag_is_passed_through` | UI | DELETE with tab | AppTest |
| 5151 | `TestDeleteDramaBlockedByRunningJob::test_delete_stays_disabled_while_a_job_is_running_even_when_confirmed` | UI | DELETE with tab | AppTest |
| 5172 | `TestTranscribeJobInputsCapture::test_round_trips_the_captured_fields` | LOGIC | DROP (Streamlit-only helper) -> delete | _write/_read_transcribe_job_inputs only serve the tab's render-loop completion; services/transcribe_service captures inputs at job start |
| 5181 | `TestTranscribeJobInputsCapture::test_missing_file_returns_an_empty_dict` | LOGIC | DROP (Streamlit-only helper) -> delete | _write/_read_transcribe_job_inputs only serve the tab's render-loop completion; services/transcribe_service captures inputs at job start |
| 5185 | `TestTranscribeJobInputsCapture::test_corrupt_file_returns_an_empty_dict_rather_than_raising` | LOGIC | DROP (Streamlit-only helper) -> delete | _write/_read_transcribe_job_inputs only serve the tab's render-loop completion; services/transcribe_service captures inputs at job start |
| 5191 | `TestTranscribeJobInputsCapture::test_a_later_click_overwrites_the_earlier_capture` | LOGIC | DROP (Streamlit-only helper) -> delete | _write/_read_transcribe_job_inputs only serve the tab's render-loop completion; services/transcribe_service captures inputs at job start |
| 5269 | `TestTranscriptionCompletionDoesNotWipeExistingLines::test_a_job_finishing_in_a_new_session_does_not_wipe_the_dramas_lines` | UI | DELETE with tab | AppTest |
| 5296 | `TestTranscriptionCompletionDoesNotWipeExistingLines::test_the_captured_transcript_text_is_what_gets_used_not_an_empty_one` | UI | DELETE with tab | AppTest |
| 5314 | `TestTranscriptionCompletionDoesNotWipeExistingLines::test_editing_the_transcript_while_the_job_runs_in_the_same_session_is_ignored` | UI | DELETE with tab | AppTest |
| 5341 | `TestTranscriptionCompletionDoesNotWipeExistingLines::test_zero_lines_against_an_existing_non_empty_drama_is_refused_not_saved` | UI | DELETE with tab | AppTest |
| 5357 | `TestTranscriptionCompletionDoesNotWipeExistingLines::test_a_history_snapshot_exists_before_a_legitimate_completion_replaces_lines` | UI | DELETE with tab | AppTest |
| 5410 | `TestDiarizationAutoStartsAfterAlign::test_diarize_checkbox_starts_a_background_job_once_alignment_finishes` | UI | DELETE with tab | AppTest |
| 5487 | `TestResegmentGuardrail::test_warns_and_waits_for_confirmation_then_clears_only_the_split_line` | UI | DELETE with tab | AppTest |
| 5518 | `TestResegmentGuardrail::test_untranslated_drama_needs_no_confirmation` | UI | DELETE with tab | AppTest |
| 5560 | `TestSpeechSplittingSensitivityDefaultAndPersistence::test_a_fresh_drama_defaults_to_300ms_not_2000ms` | UI | DELETE with tab | AppTest |
| 5565 | `TestSpeechSplittingSensitivityDefaultAndPersistence::test_manually_changing_it_survives_a_rerun_rather_than_snapping_back` | UI | DELETE with tab | AppTest |
| 5614 | `TestResegmentationStaleSnapshotSafety::test_preview_reflects_a_database_change_made_after_the_page_loaded` | UI | DELETE with tab | AppTest |
| 5640 | `TestResegmentationStaleSnapshotSafety::test_apply_refuses_when_the_database_changed_since_preview_ran` | UI | DELETE with tab | AppTest |
| 5666 | `TestResegmentationStaleSnapshotSafety::test_apply_succeeds_normally_when_nothing_changed_in_between` | UI | DELETE with tab | AppTest |
| 5722 | `TestMergeAndRestoreStaleIdSetSafety::test_apply_merge_refuses_when_the_database_changed_since_preview_ran` | UI | DELETE with tab | AppTest |
| 5739 | `TestMergeAndRestoreStaleIdSetSafety::test_apply_merge_succeeds_normally_when_nothing_changed_in_between` | UI | DELETE with tab | AppTest |
| 5749 | `TestMergeAndRestoreStaleIdSetSafety::test_restore_refuses_when_the_database_changed_since_lines_were_loaded` | UI | DELETE with tab | AppTest |
| 5768 | `TestMergeAndRestoreStaleIdSetSafety::test_restore_succeeds_normally_when_nothing_changed_in_between` | UI | DELETE with tab | AppTest |
| 5800 | `TestRestoreAndActivateKeepSpeakerCorrections::test_speaker_correction_survives_a_history_restore` | LOGIC | KEEP |  |
| 5815 | `TestRestoreAndActivateKeepSpeakerCorrections::test_speaker_correction_survives_activating_a_translation_version` | LOGIC | KEEP |  |
| 5834 | `TestRestoreAndActivateKeepSpeakerCorrections::test_activate_refuses_when_the_database_changed_since_lines_were_loaded` | LOGIC | KEEP |  |
| 5885 | `TestTranslationOnlyEngineGatesLlmOnlyButtons::test_check_consistency_is_disabled_for_a_translation_only_engine` | UI | DELETE with tab | AppTest |
| 5891 | `TestTranslationOnlyEngineGatesLlmOnlyButtons::test_find_lines_to_flag_is_disabled_for_a_translation_only_engine` | UI | DELETE with tab | AppTest |
| 5896 | `TestTranslationOnlyEngineGatesLlmOnlyButtons::test_generate_translation_notes_is_disabled_for_a_translation_only_engine` | UI | DELETE with tab | AppTest |
| 5901 | `TestTranslationOnlyEngineGatesLlmOnlyButtons::test_check_consistency_is_enabled_for_an_llm_capable_engine` | UI | DELETE with tab | AppTest |
| 5930 | `TestTestModeExportWarning::test_export_warns_when_test_mode_lines_are_present` | UI | DELETE with tab | AppTest |
| 5938 | `TestTestModeExportWarning::test_export_does_not_warn_for_a_real_engine` | UI | DELETE with tab | AppTest |
| 5946 | `TestTestModeExportWarning::test_export_does_not_warn_when_test_mode_engine_has_no_translated_lines_yet` | UI | DELETE with tab | AppTest |
| 5982 | `TestPerDramaPronounPicker::test_standalone_drama_can_set_they_them` | UI | DELETE with tab | AppTest |
| 5992 | `TestPerDramaPronounPicker::test_series_value_is_shown_as_the_default_without_being_copied` | UI | DELETE with tab | AppTest |
| 6040 | `TestPresetsInWorkspaceUI::test_save_as_preset_captures_current_settings` | UI | DELETE with tab | AppTest |
| 6060 | `TestPresetsInWorkspaceUI::test_save_as_preset_is_not_nested_in_its_own_expander` | UI | DELETE with tab | AppTest |
| 6071 | `TestPresetsInWorkspaceUI::test_saving_with_a_blank_name_is_disabled` | UI | DELETE with tab | AppTest |
| 6078 | `TestPresetsInWorkspaceUI::test_no_presets_saved_shows_no_apply_control` | UI | DELETE with tab | AppTest |
| 6083 | `TestPresetsInWorkspaceUI::test_apply_button_is_disabled_when_no_preset_is_selected` | UI | DELETE with tab | AppTest |
| 6090 | `TestPresetsInWorkspaceUI::test_applying_a_preset_sets_every_captured_field` | UI | DELETE with tab | AppTest |
| 6106 | `TestPresetsInWorkspaceUI::test_applied_fields_are_not_frozen_against_later_manual_changes` | UI | DELETE with tab | AppTest |
| 6143 | `TestApplyPresetOnNewDrama::test_no_presets_saved_still_allows_creating_a_drama` | UI | DELETE with tab | AppTest |
| 6152 | `TestApplyPresetOnNewDrama::test_creating_a_drama_with_a_preset_applies_its_engine_and_fields` | UI | DELETE with tab | AppTest |
| 6195 | `TestNewDramaLanguageSelector::test_no_language_preselected` | UI | DELETE with tab | AppTest |
| 6200 | `TestNewDramaLanguageSelector::test_create_drama_button_disabled_until_a_language_is_picked` | UI | DELETE with tab | AppTest |
| 6211 | `TestNewDramaLanguageSelector::test_creating_a_drama_saves_the_picked_language_not_a_silent_zh_default` | UI | DELETE with tab | AppTest |
| 6225 | `TestMusicMediaType::test_music_is_a_media_type_option` | LOGIC | REPOINTED | now checks services.drama_service.MEDIA_TYPE_OPTIONS |
| 6229 | `TestMusicMediaType::test_music_displays_with_ordinary_title_case` | UI | DROP (Streamlit-only helper) -> delete | _format_media_type is a Streamlit display label; React has its own labels |
| 6252 | `TestAnimeContentTypeAndSeriesAtCreation::test_anime_is_a_content_type_option` | UI | DELETE with tab | AppTest |
| 6257 | `TestAnimeContentTypeAndSeriesAtCreation::test_creating_a_drama_with_anime_media_type_saves_it` | UI | DELETE with tab | AppTest |
| 6268 | `TestAnimeContentTypeAndSeriesAtCreation::test_assigning_an_existing_series_at_creation` | UI | DELETE with tab | AppTest |
| 6281 | `TestAnimeContentTypeAndSeriesAtCreation::test_creating_a_new_series_at_creation` | UI | DELETE with tab | AppTest |
| 6296 | `TestAnimeContentTypeAndSeriesAtCreation::test_leaving_series_unset_does_not_assign_one` | UI | DELETE with tab | AppTest |
| 6306 | `TestAnimeContentTypeAndSeriesAtCreation::test_an_anime_movie_shares_a_series_with_the_shows_episodes_but_stays_its_own_drama` | UI | DELETE with tab | AppTest |
| 6349 | `TestNarrationVoiceSetup::test_engine_and_voice_description_persist` | UI | DELETE with tab | AppTest |
| 6363 | `TestNarrationVoiceSetup::test_dub_button_passes_voices_emotions_and_takes_the_gpu` | UI | DELETE with tab | AppTest |
| 6382 | `TestNarrationVoiceSetup::test_offline_voice_is_its_own_setting_and_reaches_dub_generation` | UI | DELETE with tab | AppTest |
| 6407 | `TestNarrationVoiceSetup::test_m4b_export_needs_the_narration_then_exports_it` | UI | DELETE with tab | AppTest |
| 6464 | `TestDubTimingAndRemovedCloneUI::test_stretch_limits_reach_dub_generation` | UI | DELETE with tab | AppTest |
| 6478 | `TestDubTimingAndRemovedCloneUI::test_pacing_indicator_per_line` | UI | DELETE with tab | AppTest |
| 6495 | `TestDubTimingAndRemovedCloneUI::test_removed_hosted_clone_is_explained_and_not_offered` | UI | DELETE with tab | AppTest |
| 6565 | `TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate::test_switching_dramas_never_overwrites_the_new_dramas_epub` | UI | DELETE with tab | AppTest |
| 6584 | `TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate::test_uploaded_epub_is_only_written_after_an_explicit_save` | UI | DELETE with tab | AppTest |
| 6602 | `TestDramaSwitchDoesNotCarryUploadsOrForceRetranslate::test_force_retranslate_does_not_carry_over_to_another_drama` | UI | DELETE with tab | AppTest |
| 6688 | `TestReferenceNovelUploadDoesNotLeakAcrossDramas::test_transcribing_drama_b_does_not_pull_in_drama_as_upload` | UI | DELETE with tab | AppTest |
| 6712 | `TestReferenceNovelUploadDoesNotLeakAcrossDramas::test_pasted_reference_does_not_leak_either` | UI | DELETE with tab | AppTest |
| 6794 | `TestOriginalNovelForGlossaryDoesNotLeakAndNeedsExplicitSave::test_switching_dramas_never_writes_the_new_dramas_raw_novel` | UI | DELETE with tab | AppTest |
| 6811 | `TestOriginalNovelForGlossaryDoesNotLeakAndNeedsExplicitSave::test_uploaded_raw_novel_is_only_written_after_an_explicit_save` | UI | DELETE with tab | AppTest |
| 6854 | `TestSaveEditsDoesNotRoundUntouchedTimestamps::test_untouched_timing_keeps_its_original_precision_on_save` | UI | DELETE with tab | AppTest |
| 6862 | `TestSaveEditsDoesNotRoundUntouchedTimestamps::test_an_actually_edited_timing_is_saved_and_the_other_keeps_its_precision` | UI | DELETE with tab | AppTest |
| 6882 | `TestWorkspaceStageIndex::test_brand_new_drama_with_no_source_content_is_still_on_source` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6889 | `TestWorkspaceStageIndex::test_source_uploaded_but_not_yet_transcribed_is_on_transcript` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6894 | `TestWorkspaceStageIndex::test_novel_narration_with_no_saved_novel_text_is_still_on_source` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6899 | `TestWorkspaceStageIndex::test_novel_narration_with_saved_novel_text_is_on_transcript` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6905 | `TestWorkspaceStageIndex::test_lines_with_no_speaker_yet_is_on_diarize` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6910 | `TestWorkspaceStageIndex::test_novel_narration_has_no_diarize_stage` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6918 | `TestWorkspaceStageIndex::test_partway_translated_shows_transcribe_diarize_done_translate_current` | LOGIC | REPOINTED + NEEDS EXTRACTION | stage index now from services.workflow_service; still imports ui.workflow.stage_statuses_from_index (move that 8-line pure function into services.workflow_service) |
| 6935 | `TestWorkspaceStageIndex::test_fully_translated_not_yet_dubbed_or_exported_is_on_review` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6940 | `TestWorkspaceStageIndex::test_dub_track_on_disk_moves_to_export` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6946 | `TestWorkspaceStageIndex::test_exported_status_is_fully_done` | LOGIC | REPOINTED + NEEDS EXTRACTION | same as above |
| 6955 | `TestWorkspaceStageIndex::test_exported_with_no_persisted_speaker_still_shows_export_not_diarize` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 6968 | `TestWorkspaceStageIndex::test_fully_translated_with_no_persisted_speaker_shows_review_not_diarize` | LOGIC | REPOINTED | file-level import now from services.workflow_service.compute_workspace_stage_index |
| 7006 | `TestStageTabsReplaceTheExpanderScroll::test_seven_stage_tabs_replace_the_old_numbered_expanders` | UI | DELETE with tab | AppTest |
| 7016 | `TestStageTabsReplaceTheExpanderScroll::test_project_header_shows_the_drama_name` | UI | DELETE with tab | AppTest |
| 7021 | `TestStageTabsReplaceTheExpanderScroll::test_stepper_reflects_progress_for_a_partly_translated_drama` | UI | DELETE with tab | AppTest |
| 7086 | `TestStageTabsOpenOnTheCurrentStage::test_a_new_drama_defaults_to_source` | UI | DELETE with tab | AppTest |
| 7091 | `TestStageTabsOpenOnTheCurrentStage::test_an_aligned_drama_with_no_speakers_defaults_to_diarize` | UI | DELETE with tab | AppTest |
| 7097 | `TestStageTabsOpenOnTheCurrentStage::test_a_partly_translated_drama_defaults_to_translate` | UI | DELETE with tab | AppTest |
| 7106 | `TestStageTabsOpenOnTheCurrentStage::test_a_fully_translated_drama_defaults_to_review` | UI | DELETE with tab | AppTest |
| 7113 | `TestStageTabsOpenOnTheCurrentStage::test_the_default_key_is_scoped_per_drama` | UI | DELETE with tab | AppTest |
| 7172 | `TestReviewTabGroupedSubsections::test_three_popovers_exist_in_source` | UI | DELETE with tab | AppTest |
| 7181 | `TestReviewTabGroupedSubsections::test_all_twelve_subfeatures_still_reachable` | UI | DELETE with tab | AppTest |
| 7190 | `TestReviewTabGroupedSubsections::test_find_replace_review_queue_and_history_stay_standalone` | UI | DELETE with tab | tab source inspection |
