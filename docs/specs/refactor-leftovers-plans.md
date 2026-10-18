# Plans for the six refactor issues still open (#1113, #1156, #1158, #1159, #1161, #1162)

Plans only: no code is changed by this file. Each group below needs Kae's go/no-go before a builder starts. Re-checked against `baihe-subtitler` at 639f132.

The file splits these issues once asked for are done (`db/` is a package, `OVERSIZED_MODULE_BYTES = {}`). What is left is repeated code, unused functions and a few small bugs. Dub and voice clone are frozen and no item here touches them.

Legend: **Size** S under 10 KB diff, M 10-25 KB, L over 25 KB. **User-visible** says whether a person using the app can notice. **Opus** marks items the lead reviews with Opus 5.5 before merge.

## Recommended launch order

| Group | Items | Backend/FE | Risk | Size | Timing |
|---|---|---|---|---|---|
| G1 | #1158 rows 2, 3, 4, 6, 11; #1162 rows 3, 4, 6, 1 | backend | low | M (split in two PRs if over 40 KB) | first |
| G2 | #1161 rows 3, 4, 5, 8, 10, 11 | backend | low-med | M | after G1 |
| G3 | #1113 rows 7, 10, then 8 | backend | med (8 is high) | M each | after G1 and G2 |
| G4 | #1113 rows 5, 12, 13, 14 | backend + docs | med | S-M each | any time, independent |
| G5 | #1159 / #1156 frontend (FE-A, FE-B, FE-D, FE-E) | frontend | high churn | one PR each | after the v0.1.0 tag |

Why this order: G1 and G2 are pure backend refactors that existing tests cover. G3 builds shared helpers, so it goes after G1 and G2 to avoid moving the same code twice. G5 touches about 77 api files and many pages, so it goes after the tag.

---

## #1158 transcription and audio (open: 2, 3, 4, 6, 11; rows 7, 10, 12 deferred by the audit)

| Row | What is still real | User-visible | Size | Proof |
|---|---|---|---|---|
| 11 | `asr_backend.py:621` `windows[spans.index(span)]` takes the first equal span, so two equal spans get the first one's window (wrong audio slice) and the lookup is O(n^2). Pair spans and windows with `zip` or an index in the closure. | Only in the rare equal-span case: the second span is transcribed from the wrong audio | S | New test in `tests/test_asr_backend*.py`: two equal spans with different windows each get their own window |
| 3 | `services/transcribe_options_service.py:301` still says "Keep in step with start_transcribe_run's checks". Extract one `_check_run_request` that both call. | No | S-M | Existing transcribe-options and start-run tests; add one test that both entry points reject the same bad input |
| 2 | No `require_audio_pipeline` / `ensure_no_active_jobs` helper; the checks are copied in several services. Extract after row 3 so it lands once. | No | S | Existing tests; one test of the helper |
| 4 | `core.extract_audio_from_video` is a dead re-export (`core.py:299`) kept alive by comments and `tests/test_asr_core_hardening.py:39`. Point those at `audio_extract` and drop the re-export. | No | S | Updated test calls `audio_extract` directly. This rewrites a test's patch target, not a behaviour: say so in the PR |
| 6 | `core.fmt_ts` (core.py:211) and `subtitle_formats._vtt_ts` (:365) format timestamps twice. Merge only if the outputs are byte-identical for all inputs; otherwise leave and say why. | Must not change exports | S | A parametrised test comparing both over edge values (0, rounding at .9995, hours over 99) before merging them |

Opus review: none needed. Deferred rows (7, 10, 12) stay deferred.

## #1162 database and storage (open: 1, 2, 3, 4, 6; 7 and 9 decided deferred)

| Row | What is still real | User-visible | Size | Proof |
|---|---|---|---|---|
| 3 | `services/library_service.py:154-164` calls `list_dramas_by_series`, `list_series_characters`, `list_glossary_terms` once per series. Replace with grouped queries. | Yes: the Library list loads faster with many series | S-M | Existing library service tests plus one that counts queries for N series (must not grow with N) |
| 4 | Id-range check copied in `drama_service.py:67`, `delete_service.py:38`, and inline in `reader_service.py`, `metadata_service.py`, `library_admin_service.py` and more. Add `lib/ids.py` `check_id` and use it everywhere. Error text must stay identical. | No | S-M | Existing validation tests unchanged; one test of `check_id` (bool, 0, 2**31, str) |
| 6 | `db/schema.py:447` still creates `bug_reports`. Stop creating it for new databases; do NOT drop it from existing ones. Owner confirmed. | No | S | `tests/test_db.py` upgrade test still passes; new-database test has no such table; existing databases keep the table |
| 1 | 15 test-only db functions (speakers, comic_pages, reading, benchmark). Delete only those with no non-test caller, and the tests that only exercised them (name each in the PR with the reason: behaviour gone). | No | M | Guard and unused-code tests |
| 2 | About 237 `get_conn()` uses and 38 `[dict(r) for r in ...]`. Add `_rows` / `_row` helpers and convert one submodule at a time. Large churn for small gain: recommend only if Kae wants it, else leave open. | No | L | Full db test set |

Opus review: row 6 touches `init_db` (data integrity). Row 2 would need it if chosen.

## #1161 translation (open: 3, 4, 5, 8, 10, 11)

| Row | What is still real | User-visible | Size | Proof |
|---|---|---|---|---|
| 4 | `translation_guide.apply_hard_term_substitutions` (:520) compiles its matcher per line; callers `bulk_apply.py:128`, `bulk_reflect.py:511`, `translation_finish.py:149`. Build once per run (a `build_hard_term_substituter`) and call per line. This is the measured speed-up. | Faster bulk translate on long titles, same output | S-M | Parity test: old function and new substituter give identical text for a glossary with overlapping terms; existing bulk tests |
| 11 | `auto_qc.py:447-448` sorts names and forms for every line. Sort once outside the loop. | No (faster QC) | S | Existing auto_qc tests |
| 3 | Repeated setup in the `apply_*` functions of `bulk_apply.py` and the passes of `bulk_reflect.py`. One `_load_apply_context`. | No | M | Existing bulk apply/reflect tests, unchanged |
| 5 | Five copies of "has no batch API" in `bulk_translate.py` (:131, 238, 259, 277, 302) and two advance functions in `bulk_reflect.py`. One `_require_batch_engine`. Error text stays the same. | No | S-M | Existing tests plus one asserting the exact message |
| 8 | The Gemini `generateContent` URL is built in `engine_backends/gemini.py:180`, `llm_tasks.py:439`, `qa.py:79` (and `services/metadata_research_service.py:193`). One URL builder; keys stay in headers. | No | S | Existing engine tests; a test that the URL has no key in it |
| 10 | `parse_flag_items` / `parse_consistency_issues` do not exist yet. Plan only if rows 3-5 leave a repeated parse; otherwise drop. | No | S | Existing tests |

Opus review: none, unless row 8 is widened to touch key handling.

## #1113 senior review (open: 5 partly, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15; 1 decided; 2, 3, 4 fixed)

| Row | What is still real | User-visible | Size | Proof |
|---|---|---|---|---|
| 7 | 11 `def _redact`, 8 `def _inside`, 15 `def _require_drama` copies. Add `lib/paths.py` `contained()` (lib already has `safe_paths.py`: extend that instead if it fits) and one shared redact; move callers over in two PRs (paths, then redact). | No | M + M | Existing path-traversal and redaction tests; a table test for `contained` (symlink, `..`, case on Windows) |
| 10 | Extend `tests/test_static_analysis.py` `TestLayering`: `db/usage.py:32` and `db/migrations.py:402` import `services`. Fix those two imports first, then add the rule. | No | S | The new rule itself; it fails on a deliberate bad import |
| 8 | Finish the HTTP front door: raw `requests`/`httpx` remain in `live_fetch`, `asmr_vad`, `engine_backends/{local,thinking,shared}`, `notification_service`, `diagnostics_installs_service`, `oidc_service`, `update_service`, `sources/http*.py`. Route them through `lib/http.py`. | Possible: timeouts, redirects and proxy behaviour must not change | L, split by caller group | Existing tests (they patch the module that uses the name, so each move rewrites patch targets: list them); the timeout guard stays green. **Opus** (SSRF, redirects, secrets in errors) |
| 5 | Startup integrity check for `sources.db`; auto-backups never mention it. | Yes if a corrupt file is reported at startup | S-M | New db test with a corrupted `sources.db`. **Opus** (data integrity) |
| 14 | 14 `except` lines in `background_jobs.py` unannotated. Audit each, annotate or narrow. No behaviour change. | No | S | Existing job tests. **Opus** (concurrency) |
| 13 | `page_server.py` has no concurrency cap or logging of 500s. | Yes (limits) | S-M | New test. **Opus** (auth/remote access). Needs Kae's yes |
| 12 | One-page threat model in `docs/`. Docs only. | No | S | None |
| 6 | Hashed install lock for `start.bat` / CI. | Install flow | M | A CI run. Needs Kae's yes; touches release |
| 9, 11, 15 | Services still run raw SQL; 44 `get_app_setting` calls outside tests; no `services/` subpackages. Large, low value: recommend leaving open or closing as won't-do. | No | L | n/a |
| 1 | Decided: do not wire `require_device_token`. Only the revoke control wording is unchecked. | Wording | S | Check the Settings text |

The issue's text about splitting `db.py` is stale; strike it when the issue is narrowed.

## #1159 React frontend part 1 (0 of 8, post-tag)

Current counts: 77 api files declare `type Fetch = typeof fetch` (`client.ts` does not export it); `useLoad` is used in 7 files; about 140 hand-written `let live = true` / `cancelled` flags; `useDramaJobRunning.ts:23` calls `listJobs()` directly; `formatDuration` is defined in `reviewLogic.ts:16` and `workspace/metadataForm.ts:62` (and a different one in `diagnosticsFormat.ts:59`).

| Batch | Items | Risk | Size | Proof |
|---|---|---|---|---|
| FE-A | Export `Fetch` from `client.ts`, replace the 77 local declarations. Mechanical. | Low | M | `npx tsc -b`, vitest |
| FE-B | `downloadBlob` (rows 5 and #1156 row 3; fix the synchronous revoke in `downloadText.ts:8`, which can cancel the download in some browsers), `usePopoverDismiss` in `JobsMenu`, `NotificationBell`, `JobPill`, `useDramaJobRunning` on `useJobs()`, `AdminBlocks` poll, the second `formatDuration`. | Med, user-visible only for the revoke bug | M, split downloads / popovers / jobs | vitest for each hook; a download test that the URL stays valid after click; e2e for popover Escape |
| FE-C | `useCancellableEffect` over about 60 load effects | High churn | L, one folder per PR | vitest per page; e2e 1-4 |
| Small | Dead CSS (`.form-grid`, `.folder-form`, `.subcard-head`, `.ass-preview-left`, `.review-ai-actions`), test-only `urlMediaJobId` | Low | S | Visual diff of the affected pages |

Post-tag. Opus: none. Any change to a page needs Kae's screen OK per the project rule.

## #1156 React frontend part 2 (open: 1, 2, 3, 5, 6b; 4 and 7 deferred)

| Batch | What | Risk | Size | Proof |
|---|---|---|---|---|
| FE-B (shared with #1159) | Blob-download snippet (3). | See above | | |
| FE-D | `mockAppShell` for e2e plus the `const json` helper, repeated in 36 e2e files; 15 `waitForTimeout` remain. | Med (touches many specs; a spec loosened by accident is the risk) | L, in chunks of 6-8 specs | Each spec passes before and after, unchanged assertions. No test is deleted or loosened |
| (1) | `Comic.tsx:287` / `SavedMangaReader.tsx:142` still duplicate the jump effect and observer; extract `usePageJump` | Med, user-visible if wrong | M | Existing comic specs and vitest; reader e2e on desktop and phone |
| FE-E | `useJob` and `useSourcesJob` share one poll loop (5) | High: polling, cancel and reattach | M | `useJob.test.ts`, `useReattachJob.test.ts`, e2e jobs. **Opus** (concurrency), and do it last |
| (6b) | `needsConsent` (`pages/assistant/assistantFormat.ts:44`) is test-only: delete it with its test only if the test pins nothing else | Low | S | State the removed test in the PR |

Post-tag. FE-D and FE-E last.

---

## What Kae decides

1. Start G1? (recommended: yes, low risk, one visible gain: faster Library list)
2. Start G2 after it? (recommended: yes)
3. G3 (#1113 helpers and HTTP front door): yes, or only 7 and 10? (recommended: 7 and 10 now, 8 later with Opus)
4. G4 items 13 and 6 need an explicit yes because they change access limits and release.
5. G5 waits for the tag unless Kae says otherwise.
