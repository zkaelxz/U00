# Streamlit retirement plan (proposal)

Status: DECIDED IN PART 2026-09-29 (see section 8). Written by a read-only architecture pass after the user decided to complete the React migration and remove Streamlit.
Section 8 records the decisions taken; anything in sections 3-7 not covered there is still a proposal. Evidence is `file:line` on `baihe-subtitler` at that date; HYP = hypothesis, UNK = unknown.
Streamlit code is about 11,950 lines: tabs 10,876, `ui_theme.py` 468, `ui/` 363, `app.py` 118, `common.py` 113, `.streamlit/config.toml` 15.

## 0. Load-bearing findings

- **Settings must be deleted LAST, not third.** `render_settings_sidebar` (`app.py:51`) pushes `st.session_state.settings_*` keys that other tabs read (`settings_ollama_url` is read in 9 files). Reach React Settings parity early, but delete the sidebar with the final removal (this corrects `docs/migration-frontend-plan.md` order).
- **Only one non-UI module imports a tab:** `services/workspace_job_service.py:684` lazily imports `tabs.workspace_tab` and calls `run_translate_job`, which is defined at `services/workspace_job_service.py:49` (the tab only re-exports it, `workspace_tab.py:19-23`). One-line fix. `library_tab.py:300` also imports `workspace_tab` (goes away with the tab). `cli.py` imports no tab and no `common`.
- **`page_server.py` imports no Streamlit, but only Streamlit starts it** (`settings_tab.py:450` `ensure_server_started()`, `:468` pushes the translation config). The chapter-check scheduler is likewise started only by `sources_tab.py:975`. The API needs a startup hook for both, or the browser extension and tracking notifications die silently. (The "don't run the scheduler in the API" note in the Discover spec S-7 was a two-process concern that disappears once Streamlit is gone.)
- **Streamlit is LAN-exposed with no auth today** (`start.bat:206-211` binds 0.0.0.0 and prints the LAN URL; README:282-303). `python -m api` is loopback. Retiring Streamlit closes that hole but also removes phone and household access until auth (steps 133+) exists.
- **Prebuilt frontend is undecided:** `frontend/.gitignore:11` ignores `dist`; `start-react.bat:52-90` runs `npm ci` and `npm run build`; `docs/migration-react-fastapi.md:549` says end users must not need Node; `start-react.bat` is untested on Windows.
- Handoff "Next" list is partly stale: per-line improve/explain exists (`line_ai_routes.py`), the restructure API exists (`restructure_routes.py`) but React never calls it, dub download exists.
- UNK: what "slice 51" refers to; no doc defines it.

## 1. Coverage matrix

React today: routes library/drama/settings/diagnostics/translate (`frontend/src/router.ts:3-8`); stages source, translate, review, dub, export (`stages.ts:6`). Transcribe and Diarize live inside Source. No Reader/Discover/Sources/Live/Scanlate page, no `<audio>`/`<video>`, no restructure UI.

| Streamlit unit (lines) | React replacement | Verdict and what is missing |
|---|---|---|
| Workspace preamble 1189-1602 (picker, create, analysis, autofill, edit, delete, presets, workflow tier) | Library new-drama form; Source panels | PARTIAL: preset apply/delete, workflow tier, and a stage index (`_compute_workspace_stage_index` :1140 has no API) |
| Source 1603-1960 (upload, novel, OCR, EPUB, video URL download) | SourceStage, NovelPanel, MetadataPanel | PARTIAL: video URL import missing (needs Sources S-5); B-09 part 2 |
| Transcribe 1961-2411 | TranscribeStage | PARTIAL: auto-tune (:2219) and LLM glossary-from-novel (`glossary_service.py:18`) missing |
| Diarize 2412-2444 | Source stage diarize run | REPLACED (real HF-token run owed) |
| Translate 2445-4015 (1,571 lines) | TranslateStage, GlossaryPanel, CharactersPanel | PARTIAL: pending-batch list endpoint, moderation-retry UI, duplicated bulk helpers (`workspace_tab.py:347-478` vs `translate_run_service.py:415`, B-20) |
| Review 4016-5502 (1,487 lines) | ReviewStage panels | PARTIAL: no media player/seek (no Range endpoint), no UI for add/delete/merge/split/re-segment/history restore (API done), coverage/pacing views UNK |
| Dub 5503-5661 | DubStage, NarrationPanel | REPLACED (real TTS and BGM listening check owed) |
| Export 5662-6065 | ExportStage | PARTIAL: vertical/shorts (:5971), package (:6032), softsub/SRT-hardsub variants missing |
| Library (550) | Library.tsx | PARTIAL: bulk status/tags/delete/bulk-translate/zip export, storage cleanup, backup/restore (E0 deferred: needs typed confirm and running-job refusal) |
| Settings (485) | Settings.tsx | PARTIAL: profile picker (26e), dark mode, reading prefs, OCR/tesseract, defaults, spend cap edit, offline mode, cookies, extension settings |
| Diagnostics (987) | Diagnostics.tsx | PARTIAL: core-requirement checks, job history detail, bug bundles, model versions, source-access tests, model cache, pyannote check, assistant, support report, log, benchmark, install/upgrade, reset |
| Translate (193) | Translate.tsx | PARTIAL: file upload and download |
| Reader (424) | none (1 route) | NOT STARTED: watch/listen (needs Range), glossary, story tools, wiki, Q&A, notes, vocab/Anki export |
| Sources (990) | none | PARTIAL (API only): registry and config done (56a/56b); search/series (S-3), import (S-4), URL front door (S-5), sign-in (S-6), check-now (S-7) missing; no UI |
| Discover (394) | none | PARTIAL (API only): D-0 and D-1 done; D-2 helpers missing; no UI |
| Live (149) | none | NOT STARTED; KEPT and to be ported (see section 8): API L-1 polling plus a React Live page |
| Scanlate (639) | none | NOT STARTED; DROP or DEFER candidate (specs only) |

## 2. Streamlit-only versus what stays

- **Delete as glue:** `app.py`, `common.py` (also the only `pandas` import), `ui_theme.py`, `ui/*`, all 10 tabs, `.streamlit/config.toml`; dependencies `streamlit`, `pandas` (`requirements-core.txt:6,10`), `streamlit-drawable-canvas` (`requirements-optional.txt:145`), `constraints.txt:30`.
- **Extract to services before deleting the tab:** `_compute_workspace_stage_index` (`workspace_tab.py:1140`; tests at `test_workspace_tab.py:6886+`; the Step 19 invariant); `save_key_to_env`/`_load_env_defaults` (`settings_tab.py:27,87`; equivalents exist in `settings_service.py:236`, check parity); `add_uploaded_pages` (only if Scanlate stays); `caption_tracks`, `cache_hit_share`; the player helpers only if a React player is built.
- **Delete (duplicates of services):** `_start_bulk_*`/`_bulk_engine_factory` (:311-478), `_apply_speaker_turns` (:1016) and `_apply_diarization_job_result` (dup of `diarization_service.apply_diarization_result`), `_page_for_line`/`_search_transcript`/`_adjacent_flagged_idx` (dup of `review_lines_service`), `_auto_qc_*` (dup of `export_service`); `MEDIA_TYPE_OPTIONS` (already copied in `drama_service.py:47-51`).
- **Tests are the biggest hidden risk:** 31 Streamlit-touching test files, about 17,000 lines (`test_workspace_tab.py` 7,204). Several import Class S code from `tabs.workspace_tab` (`test_workspace_tab.py:34`, `test_cli.py:174,385,421`, `test_export_formats.py:244`, `test_line_ids.py:356`, `test_transcription_quality.py:278`, `test_dub.py:266`). Repoint those imports to `services.workspace_job_service` BEFORE deleting, then delete only the AppTest widget tests. HYP: 60-70% of those lines are pure widget tests; needs a per-file triage.
- Also update `diagnostics.py:61` (streamlit listed "required"), `diagnostics.py:52` (expected tabs list), `check_setup.py`.

## 3. Prune list (each is a user decision; safest first)

1. Streamlit machinery: `ui_theme` CSS, the drawable-canvas brush (it "fails to load at all" on the pinned Streamlit, `requirements-optional.txt:139-145`), `_safe_render`, `synced_api_key_input`. Loses nothing.
2. Diagnostics "danger zone / reset library" (`diagnostics_tab.py:928`): replace with a documented manual step or CLI.
3. ~~**Live tab** (149 lines)~~ REVERSED 2026-09-29: kept and ported (see section 8).
4. **Scanlate** brush and, HYP, the whole tab: ML detector and LaMa "not run end-to-end", 639 lines plus 9 serial API slices plus a canvas editor. Alternative: keep `scanlate.py` and the extension pipeline (`page_server.py:268-363`) and drop only the UI.
5. **`MEDIA_TYPE_OPTIONS` (11 values):** real branching is on `content_mode`, not `media_type`. `streamer_vod` is real (syncs `content_mode`); `video_drama` and `asmr` have small uses; the manga family is a label plus prompt phrase; `music` is label-only (Step 89; absent from the prompt label map, `translate_engines.py:294-297`); `other` is the fallback; `anime` has no branch found. Safe cut: `music` and `other` as UI options while still accepting them on read.
6. Vertical/shorts export and "package" export (no API, Slice 30 out of scope).
7. Diagnostics extras: accuracy benchmark, bug bundles, App Assistant (usage UNK).
8. Sources leftovers: missevan import (no caller of `get_audio_url`), bilibili_manga (untested), manhuaku (hung 2026-09-27), mangaz full book unproven, the Review-Extraction session UI. Do NOT prune the verified adapters: manhuagui, 52shuku, xbanxia, toonkor, guazimanhua, miaoqumh, baozimh, kuaikan, zerosum.
9. Discover D-2 extras: bulk import (no dedup), navigator help, baihehub search. Keep the catalog.
10. Household profile picker (26e): Google users (steps 133-134) may supersede it.

## 4. Retirement sequence

**Proposed freeze (not yet in force):** no new features, polish or tests in `tabs/`, `ui/`, `ui_theme.py`, `common.py`, `app.py`. Allowed: data-loss or crash fixes that block the migration, deletions, extraction to services. New logic is service-first. Do not chip away at `workspace_tab.py` stage by stage: delete it whole after parity. Single writer per shared file (`api/schemas.py` append-only, `api/server.py`, `background_jobs.py`, `db.py`, `frontend/src/App.tsx`, `router.ts`, `stageRegistry.ts`, `stages.ts`).

Dates assume 2.5 sessions per day plus about 30% slack (all HYP):

| M | Content (sessions) | Window | Exit criterion |
|---|---|---|---|
| M0 (3) | Fix `workspace_job_service.py:684`; repoint 8 test imports; stage-index service and endpoint; API startup starts page_server and chapter scheduler; launcher runs API plus dist; decide dist delivery | 09-29 to 10-01 | `python -m api` alone runs the extension bridge; no non-UI import of `tabs`; suite green |
| M1 (8) | Translate file upload/download; Diagnostics gaps (2); Library E0 with server typed-confirm (3); Settings parity (2) | 10-01 to 10-05 | translate, diagnostics and library tabs deleted, tests moved; Settings sidebar still present |
| M2 (10) | Range endpoint plus React player; line-structure UI; auto-tune; glossary-from-novel; pending-batch list; export decisions; coverage/pacing panels | 10-03 to 10-10 | every Workspace row REPLACED or in the approved prune list; Class S tests re-homed |
| M3 (12) | D-2; S-3..S-7; React Discover and Sources | 10-02 to 10-14 | sources and discover tabs deleted; video URL import works from React or is pruned |
| M4 (5) | Reader service split plus React page (needs the Range endpoint) | 10-09 to 10-14 | reader tab deleted |
| M5 (about 12, serial) | Scanlate S0-S8 plus canvas | 10-06 to 10-25, if approved | scanlate tab deleted; page_server bubble writes frozen |
| M6 (4) | Live: L-1 polling API (per-session temp dir, `max_minutes`, `use_gpu`, stop with generation bump) plus React Live page; L-2 SSE optional | parallel with M2-M4 | live tab deleted after the user's real-stream check |
| M7 (5) | Delete workspace tab, remaining tabs, app, common, ui_theme, ui, .streamlit; remove deps; rewrite launchers and `windows-bootstrap.yml`; triage tests; docs | +2 days after M2-M6 | `grep -r streamlit` finds only history docs; suite passes; a fresh-venv launch works |

Critical path (HYP): without Scanlate, M0, the Range endpoint, the Review player and line-structure UI, in parallel with the Sources chain S-3, S-4, S-5 (S-5 is on the path because Workspace video URL download has no other API), then your real-device pass, then M7. With Scanlate the serial S0-S8 chain is the path. **Auth step 133 is not on the retirement path** (only LAN/phone/remote use needs 133-140); S-3..S-6 may ship loopback-only.

Earliest realistic removal (HYP): about 2026-10-23 (plus or minus 4 days, about 45 sessions) with Scanlate deferred and Live ported (Live adds about 4 sessions, in parallel with M2-M4); about 2026-11-10 (plus or minus 5, about 57 sessions) with Scanlate ported. Recommended hard date: 2026-10-30, with the prune list as the scope lever.

Real-device checks, one owed-to-you card per milestone (5-8 one-line actions each; deletion PRs are prepared but merged after "pass"). Proposed default-pass rule (needs approval): if a path was equally unverified in Streamlit, silence after 3 days permits deletion (F-12 shows the Streamlit dub was silently broken since #161).
- M1: restore a real backup zip on a copy; storage clean on a scratch drama; a real pip install through Diagnostics (if kept).
- M2: real ffmpeg/libass burn; real TTS dub plus BGM listening (B-23); Whisper on GPU; a paid-key translate with cost cap; pyannote with an HF token; real seek with Range on a large video.
- M3: import a known-good site (manhuagui, 52shuku); PC-local sign-in. M4: play real video and audio in Reader; import an .apkg into Anki. M5: one real page through OCR, ML detector and LaMa. M6: one real stream.

## 5. What replaces start-up

- One command: `start.bat` merges start.bat's Python/venv/deps/check_setup logic (:80-155) with start-react.bat's serve-and-open logic, runs `python -m api` on 8600 and opens Edge app mode as before.
- Node must stop being a runtime requirement: a prebuilt dist via a release zip attached by a local build (Actions minutes are exhausted, so CI cannot build it), or a committed dist; otherwise keep the Node prompt (`start-react.bat:52-64`).
- Required: Python 3.9+ and the core requirements (fewer: no streamlit, no pandas). Only for media features: ffmpeg with libass (`check_setup.py` already warns). Optional, detected and explained in React Diagnostics (`/api/diagnostics` already has the data): Tesseract, HF token (gated pyannote only), yt-dlp and a JS runtime (URL import only), GPU/CUDA, Node (developer only).
- Rewrite or remove: `start.ps1:118,141`; the `--server-only`/`--ci` LAN URL print (`start.bat:205-284`; unsafe once no auth; re-add after auth); `.github/workflows/windows-bootstrap.yml:59-61`; `tests/test_launcher_python_detection.py` as needed.
- README (1,739 lines, 29 Streamlit/8501/start.bat mentions): remove the dual-app "Project status" text (:43-63), the LAN access section (:282-303, now false), the "Node only for experimental React" line (:199), port 8501, per-tab descriptions and Diagnostics-tab install-button text (:235-237).

## 6. Doc consolidation

Line counts: `CLAUDE.md` 351, `.claude/CLAUDE.md` 26, engineering-standards 67, testing-and-ci 154, migration-handoff 66, migration-frontend-plan 51, migration-review 1,211, migration-react-fastapi 757, roadmap-master 154, README 1,739, plus a roughly 5,000-line roadmap on the planning branch.

Target set: (1) `CLAUDE.md` of about 120 lines absorbing engineering-standards, testing-and-ci and `.claude/CLAUDE.md`, keeping "Rules learned from real bugs" and test/CI notes, dropping the step-number branch/mode rules, the model table, the roadmap-fetch preamble, "Current work", the spawned-by-planning text, the screenshot section and the efficiency prose once the planning branch stops being the source of truth; (2) `docs/status.md`: one live page merging migration-handoff, roadmap-master and this plan; (3) `docs/architecture.md`: the FILE_ORGANIZATION tree plus a stable-ID invariant register; (4) `README.md` of about 300 lines (install, run, troubleshoot), moving the feature reference (README:395-1536) into `docs/user-guide.md`; (5) keep as reference: content-sources, known-working-sources, adding-source, browser-extension, remote-access-decision, react-ui-guidelines, windows-installer-design (re-based to the API+React launcher). Archive or delete after M7: migration-react-fastapi, migration-review (keep the section 4 register re-keyed as stable IDs), remote-access-design, handoff-browser-extension, windows-installer-research-notes, ux-click-through-audit, specs/* once built. Freeze the roadmap and extract its still-live parts into `docs/status.md`.

Rule: code comments must not cite roadmap steps that live on another branch. About 609 non-test lines match `Step [0-9]` (lower bound): top-level about 329 (db.py 59, translate_engines.py 55, diagnostics.py 33, scanlate.py 26, dub.py 24, cli.py 20), tabs 154 (workspace_tab 103), sources/ 85, services/ 38, api/ 3; plus 506 lines in 95 test files. About 150 disappear with `tabs/`. Cleanup: delete tabs and their tests first; a one-off script strips trailing parenthetical tags such as "(Step 25d item 1)" and "Step N:" prefixes without touching the reason text; list mid-sentence "Step N's ..." references for manual rewrite to a stable invariant ID; add a static test that fails on new `Step \d+` in non-test `.py`; leave test names and docstrings last.

## 7. Risks and decisions needed

Risks (ranked): (1) hidden behaviour in the 4,875-line `render_workspace_tab` (889 widget calls) is lost: mitigate with the section 1 gap list and a Class S test per row; (2) Class S tests lost when 31 tab-test files are deleted unread; (3) household and phone access disappears with no auth built; (4) extension bridge and chapter scheduler orphaned; (5) scope blow-out in Scanlate and Sources; (6) real-device unknowns gate deletion; (7) prebuilt dist undecided and no CI gating; (8) no profile concept in API or React (26e); (9) parallel edits on shared files; (10) docs and process drag.

Decisions needed (ranked): 1. Scanlate: port, defer past the removal date, or drop the UI (keep the library and extension pipeline)? 2. Live: drop? 3. LAN/phone gap: accept no household access from removal until auth 133+140, or an interim measure (recommendation: never expose an unauthenticated API)? 4. Prebuilt frontend delivery: release zip, committed dist, or require Node. 5. The end date (proposed 2026-10-30) and who may cut scope to hit it. 6. Approval of the prune list and the default-pass rule for real-device checks. 7. Extension bridge: API-started `page_server` as an interim (recommended) versus breaking the extension until step 135. 8. Profiles (26e): drop, or replace with Google users later. 9. The API process owning the chapter-check scheduler. 10. Remaining Discover/Sources/Live spec questions Q4-Q12. 11. Copy the planning-branch roadmap into `docs/history/` or leave it on the branch.

## 8. Decisions taken (user, 2026-09-29)

- **Goal:** complete the React migration and remove Streamlit. **End date: 2026-10-30** (hard date; the prune list is the scope lever; earliest realistic finish is about 2026-10-23 without Scanlate).
- **Scanlate:** deferred past removal. The Streamlit Scanlate tab is deleted with the rest; keep `scanlate.py` and the browser-extension pipeline (`page_server.py:268-363`). The React canvas editor is built later from `docs/specs/scanlate-api-spec.md`.
- **Live capture: KEPT (reversed the same day).** The user first chose to drop it, then said they misunderstood what it was and want it to work. It is ported before removal (milestone M6 is no longer optional): API slice L-1 (polling) from `docs/specs/discover-sources-live-api-spec.md` section 4, plus a React Live page, before `tabs/live_tab.py` is deleted. Any public URL that yt-dlp can resolve is allowed (earlier decision). The capture pipeline itself (`live_translate.py`) was verified end to end only against a local looping source; it has never run against a real broadcast, so a real-stream check by the user gates deletion of the Streamlit tab. Known issues to fix in the port: the tab never passes `use_gpu` (Whisper runs on CPU while holding the GPU slot, `live_tab.py:96-103` vs `live_translate.py:475-478`); a fixed shared temp dir means stale `chunk_*.wav` files can be processed as new (use a per-session directory); no spend or duration cap (add `max_minutes`); the resolved stream URL can expire after a few hours (restart to re-resolve); cookies never travel over the API. Permission: `media.import_url`.
- **Frontend delivery:** a prebuilt release zip built locally (users need no Node). Actions minutes are exhausted, so CI cannot build it.
- **Freeze (in force from 2026-09-29):** no new features, polish or tests in `tabs/`, `ui/`, `ui_theme.py`, `common.py`, `app.py`. Allowed: crash or data-loss fixes that block the migration (state the justification in the PR), deletions, and extraction to services. New logic is service-first.
- **Still open (need the user):** approval of the prune list in section 3 (items 1-2 and the Live and Scanlate rows are decided above; the media types, export extras, Diagnostics extras, Sources leftovers and Discover D-2 extras are not), the default-pass rule for real-device checks, the LAN/phone gap (recommendation: do not expose an unauthenticated API), the extension-bridge interim (recommended: API-started `page_server`), profiles (26e), who owns the chapter scheduler, and whether the planning-branch roadmap is copied into `docs/history/`.

## 9. Revised approach: delete early, rebuild in React (user, 2026-09-29)

The user approved deleting the Streamlit UI as soon as the new app can be launched, instead of waiting for React parity and real-device checks. Nobody uses Streamlit; the React app is the only UI going forward, and gaps are fixed there. This supersedes the per-milestone "delete after parity and after the user's checks" gating in section 4.

Guardrails:
1. **Launcher first (M0-b).** One command starts the API and serves the prebuilt frontend; the API starts the extension bridge (`page_server`) and the chapter-check scheduler, which today only Streamlit starts (`settings_tab.py:450`, `sources_tab.py:975`). No tab is deleted before this works.
2. **Archive.** Before the first deletion PR, tag the last commit that has Streamlit as `pre-streamlit-removal` and push a `legacy/streamlit` branch at that commit. Anyone can check it out to compare behaviour or lift code back.
3. **Feature inventory.** `docs/streamlit-feature-inventory.md` lists, per tab and per Workspace stage, what the UI did, its source lines at the tag, the tests that covered it, and what React lacks. The missing items become the React backlog, ranked by use.
4. **Keep logic tests.** Tests that check real logic are moved to run against services before their tab test file is deleted; only widget/render tests are deleted.
5. **Real-device checks** become a React checklist in the handoff ("owed to the user") rather than a deletion gate.

Sequence: step 133 and the three pending branches (B-04/B-05, slice 51, slice 52) merge; M0-b (startup hooks, single launcher, release zip); inventory, tag and test triage; one series of deletion PRs (all tabs, `app.py`, `common.py`, `ui_theme.py`, `ui/`, `.streamlit/`, the `streamlit`/`pandas`/canvas dependencies, launcher and doc references); then the React backlog. Expected removal: about 2026-10-05 to 10-08. The 2026-10-30 date now applies to closing the highest-ranked React gaps rather than to deletion.

Status (docs sync 2026-09-29, base 4b2d5e5): step 133 (#339), B-04/B-05 (#350) and slices 51+52 (#352) are merged; the M0-b startup hook is merged (`api/background.py`: chapter-check scheduler + extension bridge, #372); feature inventory and test triage are merged (#343); the tag, `legacy/streamlit` branch and deletion PRs are not done. APIs now exist for Reader (#370), Sources search (#372), Discover D-2 (#372), media playback (#352) and Live (#372); their React pages are not merged yet. Update (docs sync after #402, base f48ec58): React Reader (#388) and Sources (#401) pages are merged, plus Library admin (#385) and Diagnostics admin/extension (#402); Discover and Live still have no React page.

Until React catches up, these are unavailable: video URL import, Reader, the Sources and Discover screens, media playback, vertical/shorts and package export, Live (planned port) and Scanlate (held until after removal).

### Deletion PR checklist (prepared in branch `streamlit-removal-prep`, 2026-09-29)

Guardrail 4 is done for the `tabs` imports: every logic test that lived in a file importing `tabs` now runs from a file that does not (tests split into `*_streamlit.py` files, or logic moved out of a tab-named file). After that branch, `grep -rn "from tabs\|import tabs" tests/` hits only the files in the first list below.

**Test files to delete with the tabs** (whole files; every test in them is widget, AppTest, tab-source or a Streamlit-only helper):

- Tab-named and UI-only: `test_workspace_tab.py`, `test_settings_tab.py`, `test_reader_tab.py`, `test_discover_tab.py`, `test_live_tab.py`, `test_scanlate_tab.py`, `test_sources_tab.py`, `test_translate_tab.py`, `test_review_workspace.py`, `test_step20_ux_polish.py`, `test_workspace_raw_novel.py`, `test_diagnostics_regrouping.py`, `test_diagnostics_source_access.py`, `test_page_server_settings.py`, `test_gui_polish.py`, `test_dark_mode_step68.py`.
- Split out in `streamlit-removal-prep`: `test_adaptive_extraction_streamlit.py`, `test_auto_qc_streamlit.py`, `test_benchmark_streamlit.py`, `test_bulk_translate_streamlit.py`, `test_db_streamlit.py`, `test_diagnostics_and_export_streamlit.py`, `test_dub_streamlit.py`, `test_emotion_manhua_ui_streamlit.py`, `test_export_formats_streamlit.py`, `test_install_buttons_streamlit.py`, `test_library_features_streamlit.py`, `test_media_preview_streamlit.py`, `test_project_instructions_streamlit.py`, `test_raw_transcript_streamlit.py`, `test_sources_auth_browser_streamlit.py`, `test_sources_preflight_streamlit.py`, `test_speaker_rerun_streamlit.py`, `test_transcription_quality_streamlit.py`, `test_translation_memory_streamlit.py`, `test_workspace_job_service_streamlit.py`.
- Other Streamlit-only files (import `streamlit`, `common`, `ui` or `ui_theme`, not `tabs`): `test_app.py` (the icon check moved to `test_app_icon.py`), `test_common.py`, `test_ui_components.py`, `test_dark_mode_consistency.py`, `test_streamlit_floor.py`, and `test_app_help.py` with `app_help.py` (section 10).

**Tests to edit, not delete:**

- [ ] `tests/test_static_analysis.py`: delete the `tabs/`-scanning classes and their checker self-tests (triage section 3); keep the timeout, constraints and requirements classes. Re-check `TestConstraintsFile` once `streamlit` leaves `constraints.txt`.
- [ ] `tests/test_diagnostics_and_export.py::test_expected_tabs_files_list_is_not_stale` and the file-completeness tests: update or drop with `EXPECTED_TABS_FILES`.
- [ ] `tests/test_api_foundation.py:304` asserts `"streamlit" in body["dependencies"]`: use another required package.
- [ ] `tests/test_diagnostics_gaps_service.py:222` uses `streamlit` as the example of a required-tier package: use another one.

**Dependencies and launchers** (line numbers at base `7da3ba3`):

- [ ] `requirements-core.txt:6-9` (`streamlit>=1.56` and its comment) and `:10` (`pandas>=2.0`): remove.
- [ ] `requirements-optional.txt:139-145`: remove `streamlit-drawable-canvas` and its comment block.
- [ ] `constraints.txt:30` (`streamlit<2`): remove.
- [ ] `start.bat:162` and `start.ps1:148`: drop `streamlit, pandas` from the "already installed?" import check; keep the rest of the list and the urllib3 `>= (2, 6)` assert (`test_static_analysis.py::test_urllib3_has_the_2_6_floor` checks it).
- [ ] `diagnostics.py:61-62`: remove the `streamlit` and `pandas` entries (both marked "required") from `OPTIONAL_DEPENDENCIES`; `:111-114`: remove `streamlit_drawable_canvas`.
- [ ] `diagnostics.py:30,37,40`: remove `app.py`, `common.py`, `ui_theme.py` and `app_help.py` from `EXPECTED_TOP_LEVEL_FILES`; `:51-56`: remove `EXPECTED_TABS_FILES` and its callers.
- [ ] `check_setup.py:3-5`: the docstring still says it runs "before Streamlit starts" and refers to `app.py`; reword (no code change needed).
- [ ] `.github/workflows/windows-bootstrap.yml`: no Streamlit step is left; its `paths:` filters list `requirements-core.txt` and `constraints.txt`, so the dependency PR triggers it (Actions minutes are exhausted, so run `start.bat --ci` locally instead).
- [ ] Files to delete: `app.py`, `common.py`, `ui_theme.py`, `ui/`, `tabs/`, `.streamlit/config.toml`, `app_help.py`.
- [ ] `FILE_ORGANIZATION.md`, `README.md` and `docs/migration-handoff.md`: remove the deleted modules and the Streamlit launch text.

## 10. Prune decisions (user, 2026-09-29)

- **Vertical/shorts export and "package" export: dropped.** Not ported to React; they go with `tabs/workspace_tab.py` (~l.5971, ~l.6032). The feature inventory marks them DROPPED, not MISSING.
- **Media types: `music` and `other` removed from the new-drama picker** (`frontend/src/pages/libraryForm.ts`). The API still accepts both (`services/drama_service.py` `MEDIA_TYPE_OPTIONS`) so existing dramas keep loading and saving, and Discover imports that map a `game` title to `other` keep working.
- **Household profile picker (Step 26e): dropped.** Google sign-in (steps 133-134) replaces it for telling household members apart. The picker is not ported; per-profile data already in the database is left in place and not migrated. Reader and notes features in React are per library, not per profile, until per-user data is designed on top of the Google users.
- **App Assistant (`app_help.py`): dropped for now** (user, 2026-09-29). It answers from `tabs/*_tab.py`, so it stops working when `tabs/` is deleted; delete it and its tests (`tests/test_app_help.py`) in the Streamlit deletion PRs. Reconsider after the admin portal (the admin/users UI on the D5 listener) is built.
- Still open from section 3: Diagnostics extras (benchmark, bug bundles), the Sources leftovers, Discover D-2 extras, the default-pass rule, the LAN/phone gap, the extension-bridge interim, the chapter scheduler owner, the roadmap history copy.
