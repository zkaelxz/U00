# Unfixed items harvested from open PRs, zkaelxz/U00, 2026-10-10 (38 open PRs)

| PR | Title | Item | Kind | Waits on |
|---|---|---|---|---|
| 1122 | Docs drift fix | Deleted brief B6 (Jellyfin headroom) and its mention in the "Opus for B2,B4,B6,B7" line; "Restore it if you want it kept" | needs-owner-decision | |
| 1122 | Docs drift fix | `docs/source-status.json` and Pace notes in `known-working-sources.md` not touched | follow-up | |
| 1122 | Docs drift fix | Stale `Settings > Remote access` comments in `frontend/src` (code out of scope) | follow-up | |
| 1120 | Design audit | `pages/workspace/WorkspaceShell.tsx:96` two primaries in one header; fix (secondary ButtonLink, e2e `workspace-shell.spec.ts:47` + mobile spec expect `btn-secondary`) given but not applied | deferred-to-other-PR | PR that owns WorkspaceShell (unnamed) |
| 1120 | Design audit | `index.css` `.source-link` global 44px touch target (only set in `novelChapters.css`) | deferred-to-other-PR | PR that owns index.css (unnamed) |
| 1120 | Design audit | `pages/Library.tsx`: list moves down ~290px after load (Continue shelf/stats arrive); "pre-existing", separate loading-shift fix | follow-up | |
| 1120 | Design audit | Library first screen: Make subtitles primary beside New title primary; "product call" whether to collapse Make subtitles | needs-owner-decision | |
| 1120 | Design audit | `MakeSubtitles.tsx:247` orphan "Ready" line | follow-up | |
| 1120 | Design audit | ReaderPrefs "Spoiler-free" is a checkbox, not `Toggle` (rule 19), pre-existing | follow-up | |
| 1120 | Design audit | Clip width uses character-range check, not font metrics; narrow Latin names may clip early | uncertain | |
| 1120 | Design audit | `npm run lint` warnings pre-existing incl. `MakeSubtitles.tsx:90` exhaustive-deps | unrelated-failure-seen | |
| 1120 | Design audit | Manual: phone Source stage Read button compact; Library slow-load no jump | needs-PC-test | |
| 1117 | Frontend dedupe | `NavDrawer`/`CommandPalette` duplicate close-on-route-change effect (~4 lines) | follow-up | |
| 1117 | Frontend dedupe | CommandPalette focus-restore vs Aa/Sheet return-focus (~10 lines, needs care) | follow-up | |
| 1117 | Frontend dedupe | `ComicControls.ViewForm` vs `ReaderPrefs.PrefsForm` same set/select helpers (~10 lines) | follow-up | |
| 1117 | Frontend dedupe | Copy-feedback state repeated in 8 components (~25 lines, a `useCopy` hook); scan "not exhaustive" | follow-up | |
| 1117 | Frontend dedupe | Excluded files untouched (PackagesSection, PendingInstall, WorkspaceShell, Reader, Comic, ReviewChecks, index.css); `SupportReportPanel.tsx` untouched; `SelectionBar` x2 left | deferred-to-other-PR | other PRs editing those files |
| 1115 | title not drama | `test_update_service.py::test_install_starts_a_private_copy...` failed in full xdist run, passes in isolation, "looks flaky" | unrelated-failure-seen | |
| 1112 | Split auto_backup | `# restore one drama` banner left above `list_snapshot_dramas` unreworded on purpose | uncertain | |
| 1111 | Extension options/shared.js | Deferred: move popup.js blocks (lines 121,123,293-294,362-363,411-412,215,448, `say()`) to `shared.js` helpers `pluralize/isFailure/failureMessage/showStatus` | deferred-to-other-PR | "after the other branches merge" (unnamed extension branches) |
| 1111 | Extension options/shared.js | content.js 1179,1217,297,706 to shared helpers (only if shared.js injected with content.js) | deferred-to-other-PR | same |
| 1111 | Extension options/shared.js | Guard run "187 passed, 1 skipped (pre-existing: db split rule)" | unrelated-failure-seen | |
| 1110 | Split bulk_translate | No baseline test count from before change | uncertain | |
| 1110 | Split bulk_translate | `bulk_translate.py` still 79 KB; next: Reflect pipeline or `apply_*` split, needs shared-helpers module first | follow-up | |
| 1109 | Split autotune | `services/transcribe_service.py` still 58 KB; next: speed-recording block, config get/update, `_apply_transcription` | follow-up | |
| 1109 | Split autotune | `OVERSIZED_MODULE_BYTES` may need ratchet later | follow-up | |
| 1108 | Split disk_usage | `disk_usage_service.py` still above 40 KB; later split for move/clear planning | follow-up | |
| 1108 | Split disk_usage | 5 skips in guard run not checked | uncertain | |
| 1107 | Split Setup lock | `stage_service` copies `setup_lock.py` and adds `..\lib` to helper `._pth`; "Not exercised on Windows", run Windows smoke test before release | needs-PC-test | |
| 1100 | page_server in-flight | 30 s wait bound is a judgement call; /pages batch of 12 could wait up to 30 s per page | uncertain | |
| 1100 | page_server in-flight | Reused page with no bubbles re-read doesn't wait on claim | uncertain | |
| 1100 | page_server in-flight | `threading.Lock` not FIFO; 3+ queued pages may translate out of capture order | uncertain | |
| 1100 | page_server in-flight | Manual: paid engine, click Translate this page twice, expect one call | needs-PC-test | |
| 1099 | Seven services findings | Items 3 and 5: old labels lack size/mtime so download without language suffix until re-exported (deliberate) | uncertain | |
| 1099 | Seven services findings | Force-stopped job with failed mirror write gets `detail_state="unknown"` | uncertain | |
| 1099 | Seven services findings | Item 7 only reports in transcribe pipeline; `workspace_job_service.run_hardsub_ocr_job` doesn't pass `info` | follow-up | |
| 1099 | Seven services findings | Manual: export zh burned-in video after en, filename ends "(zh)" | needs-PC-test | |
| 1092 | Launcher stale lock | `installer/service.py` ~493 feeds the same wait via `pending_install`; benefits once #1077 lands | deferred-to-other-PR | #1077 |
| 1092 | Launcher stale lock | Windows `ctypes` branch not exercised by Linux tests | needs-PC-test | |
| 1091 | Pending-install UI | "tick for A, open B" can't be reached via UI; only unit-tested via `planPanelKey`; vitest is node-only, component behaviour covered by Playwright | uncertain | |
| 1089 | UX review fixes | New stale-link spec not run against code without the fix, so unconfirmed it fails there | uncertain | |
| 1089 | UX review fixes | No phone spec covers Reader header or novel chapter rows (share the CSS rule) | follow-up | |
| 1087 | Extension tainted canvases | 6 manual checks: one img+canvas per page; twmanga Allow retry; popup closing on permission prompt; Deny keeps Allow; mixed page partial; tile-scrambled unverified note | needs-PC-test | |
| 1085 | Job review leftovers | Live owner genuinely hung stays "running" until process ends or Cancel/Force stop (trade-off) | uncertain | |
| 1085 | Job review leftovers | Recycled pid for unrelated live process keeps orphan row open; `owner_instance` can't distinguish | uncertain | |
| 1085 | Job review leftovers | Stopping message may show briefly before child timeout (parent clock starts earlier) | uncertain | |
| 1085 | Job review leftovers | Manual: pause server >15 min under debugger, check Jobs | needs-PC-test | |
| 1082 | Review leftovers | `docs/STATUS.md` follow-up: `lib.http.get/post` should reuse shared session from #1070 once merged | deferred-to-other-PR | #1070 |
| 1082 | Review leftovers | Did not touch `Response.ok`/headers/307-308, ytdlp second downloaded event/spec cleanup, `settings_service.get` | follow-up | |
| 1081 | pip tail cap | Brief's described `dist_in_use` behaviour (dependent pure-Python dist counts) does not match code; documented actual behaviour | uncertain | |
| 1080 | Four review lows | `extension/background.js` fetchImage change: no `extension/package.json`, no extension tests run | uncertain | |
| 1079 | TestImportTimeSafety | `from lib import x` would copy `lib/__init__.py` but not `lib/x.py`; db.py doesn't do it today | follow-up | |
| 1073 | GPU slots | `_gpu_slot_available_locked` / `_release_gpu_slot` still run gpu_lock SQL under `background_jobs._lock`; moving needs own design | follow-up | |
| 1073 | GPU slots | Legacy `ui:` rows no longer released early at first start after upgrade; expire by heartbeat | uncertain | |
| 1073 | GPU slots | Manual: CLI `translate --engine ollama`, kill mid-run, start UI GPU job, should start at once | needs-PC-test | |
| 1071 | Settings schema 3/3 | Still hand-written: every other card, `Settings.tsx`, `settingsIndex.ts`, Month counter, Cap line, Server addresses | follow-up | |
| 1071 | Settings schema 3/3 | `settings_schema_service.py` reads private `settings_service._SCHEMA_CHOICES`; "PR 2 should keep it" | deferred-to-other-PR | #1068 (PR 2) |
| 1071 | Settings schema 3/3 | Differences from brief: number rows `type="text"`, no mock in `settingsNav.ts`, `monthly_cap_usd` row included | uncertain | |
| 1070 | HTTP front door wave 4 | `live_fetch.py` left (unbounded stream, no max_bytes, no URL guard on redirects) | follow-up | |
| 1070 | HTTP front door wave 4 | `services/notification_service.py` still raw `requests.Session`; `oidc_service.py` untouched (auth) | follow-up | |
| 1070 | HTTP front door wave 4 | Unsure: pinning guards caller URL then adapter checks prepared URL host | uncertain | |
| 1070 | HTTP front door wave 4 (comment) | Full suite not run; no `tests/test_engine_backends*.py` exists | uncertain | |
| 1068 | Settings schema 2/3 | "Not done as specified (please decide)": one-reader guard can't be limited to the two root modules; 13 modules still use `db.get_app_setting` (`asr_options_service`, `update_service`, `ownership_service`, `notification_service`, `web_search_service`, `engine_backends/pricing.py`, ...), listed in `OTHER_READERS` | needs-owner-decision | "PR 3 or separate" |
| 947 | Stereo B1 DSP | "Only listening can validate quality"; full-suite result "reported in the session" (chat only) | needs-PC-test | |
| 943 | DB-0 | `repo_map.py db --part N` now says "db/ has parts 1-1"; old shorthand gone until DB-1+ split | follow-up | DB-1+ |
| 943 | DB-0 | `_DB_SHARED_STATE` is hand-listed; new shared global must be added | follow-up | |
| 943 | DB-0 | Bare-name rule flags local vars sharing re-exported names (strictness chosen) | uncertain | |
| 943 | DB-0 | Pre-existing: many comments/docs still say `db.py` (CLAUDE.md, docs/, docstrings) | follow-up | |
| 937 | Review flags streamer | Streamer numbers are judgement calls, not measured on real streams | uncertain | |
| 937 | Review flags streamer | Could not check report's unattributed ~755 flags or real-data results | needs-PC-test | |
| 937 | Review flags streamer (comment) | `phone-spacing.mobile.spec.ts:99` (390 and 360 px) fails when run after review specs: `RecordsPanel.tsx` Line history "Preview"/"Restore..." buttons overlap; left out of diff; suggests follow-up; base not checked | follow-up | |
| 937 | Review flags streamer (comment) | CI: `test_update_service::test_start_download_thread_reports_verified` (expected `downloading`, saw `verified`): thread-timing race, passed 3/3 locally, not changed, "will re-check" | unrelated-failure-seen | |
| 937 | Review flags streamer (comment) | CI: `test_static_analysis::TestSubprocessTextDecoding` (`services/loaded_models_service.py:125`) red on base, fixed by #930 | unrelated-failure-seen | #930 (merged in) |
| 910 | Benchmark Lab | No new columns (db.py and `benchmark_lab_service.py` at size limit); judge results in `app_settings` `benchmark_judge.<run id>`; proper columns need db.py split first | deferred-to-other-PR | db.py split |
| 910 | Benchmark Lab | Judge sees reference as one acceptable rendering, may bias to similarity | uncertain | |
| 910 | Benchmark Lab | Judge engine cost not under per-run `max_cost_usd`, only monthly cap | uncertain | |
| 910 | Benchmark Lab | Ollama judge not treated as GPU-touching for scheduling | uncertain | |
| 910 | Benchmark Lab | `edit_samples` marking misses pre-existing edits, find-and-replace, version restores | uncertain | |
| 910 | Benchmark Lab (comment) | Needs real LLM key on owner PC: blind judge `run_pass` vs real provider (JSON parse, cost cap, usage log); real judge model vs tested engines; build-from-reviewed-title end to end; real run with judge vs GPU slots after merge | needs-PC-test | |
| 910 | Benchmark Lab | Manual `#/benchmark` walk-through (build set, two engines, different judge, warning gate) | needs-PC-test | |
| 891 | Qwen3-ASR native | Still unverified: no real Qwen3 weights, audio, GPU, Windows; real-audio parity run is owner's; Scanlate detector and PaddleOCR-VL-For-Manga not run on transformers 5.x; non-ASCII Windows data folder | needs-PC-test | |
| 891 | Qwen3-ASR native | Owner manual check list (pip uninstall qwen-asr, parity, name hint, Scanlate/Paddle page, non-ASCII folder) plus 9-step real-GPU check in comment | needs-PC-test | |
| 891 | Qwen3-ASR native (comment) | Full suite and Playwright e2e not run, "CI is the gate" | uncertain | |
| 883 | Translate by sentence | Inert in Reflect mode, NLLB, and provider bulk batch (Claude/Gemini); "Say if you want Reflect or bulk covered" | needs-owner-decision | |
| 883 | Translate by sentence | `TRANSLATE_PROMPT_VERSION` not bumped; line provenance doesn't record the mode | uncertain | |
| 883 | Translate by sentence | Not in style presets, not per-run field; per title only | needs-owner-decision | |
| 883 | Translate by sentence | Size guard: recorded sizes raised for `cli.py`, `db.py`, `translate_run_service.py` | uncertain | |
| 883 | Translate by sentence (comment) | Full local suite: 11460 passed, 4 failed. `test_core_requirements` (defusedxml) "known failure, unrelated", not fixed; `test_resplit_lines::test_job_started_during_alignment_blocks_the_commit` passed alone, "load-related", not touched; suite not re-run in full after fix | unrelated-failure-seen | |

## Counts per kind (rows above)
- uncertain: 29
- follow-up: 25
- needs-PC-test: 14
- deferred-to-other-PR: 9
- unrelated-failure-seen: 6
- needs-owner-decision: 5
- total: 88

## PRs with no 'not done' section at all (notes only in chat)
- 1114 (docs post-mortem 9): body has no sections
- 1095 (test deletion rule): no sections
- 947 (Stereo B1 DSP): no sections; says full-suite result 'reported in the session'
- 1090 (engine review LOWs): no uncertainty/follow-up section (only a note that an old test was replaced)
- 1084 (extension base64): no such section
- 1076 (GPU process job drain): no such section
- 1080 (four review lows): no such section (only a no-extension-tests remark)
- 1093 (display_url slugs): no such section (mentions one fixture failure, since fixed)
- 1082 (review leftovers): no heading, but lists items it did not touch
- 1115, 1091, 1083, 1122 have only trivial or 'None' content; 1122 'Manual check: None'
