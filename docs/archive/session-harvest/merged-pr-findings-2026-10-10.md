# Merged PRs 2026-10-10 (zkaelxz/U00): not-done / follow-up harvest

61 PRs merged on 2026-10-10 (59 in the first 60 closed-by-updated, plus #1023 and #1020 on page 2; #903 is closed unmerged and excluded). Comments read via get_comments for all 61; only the ones noted below had any. "Covered by" = an open PR/issue the text names; none of the texts names an open PR or issue number, so entries only cite backlog/doc references when given.

| PR | Title | Item | Kind | Covered by |
|---|---|---|---|---|
| 1078 | job_store busy wait | Review finding 3 premise differs from code: no sweep removes a job from `_jobs` while keeping its row; fix only closes the gap between `_clear_job_locked` and `_delete_job_record` | uncertain | none named |
| 1078 | job_store busy wait | After a non-lock write failure, row `sync_error` column is written on the first failing retry tick, not at once; nothing in the app reads it | uncertain | none named |
| 1078 | job_store busy wait | Manual: hold a write txn on library.db (DB Browser), check Jobs keeps updating and shows "Job state could not be saved" until released | needs-PC-test | none |
| 1072 | Extension: save novel page text | Page check is a heuristic (no page-sized images, main block >=300 chars); novel page with big inline illustration hides the button unless text selected | uncertain | none named |
| 1072 | Extension: save novel page text | Popup now injects content script on open to classify the page (was on button press) | uncertain | none named |
| 1072 | Extension: save novel page text | Ownership check runs as local owner so it only confirms the title exists (bridge trust model has no per-user identity) | uncertain | none named |
| 1072 | Extension: save novel page text | `page_server.py` imports from `services/` inside `do_POST`, against layer order in CLAUDE.md; not fixed | pre-existing | none named |
| 1072 | Extension: save novel page text | `novel_files_service.SOURCE_IMPORT_PREFIX` ("source_import_") != `pipeline.import_job_id` ("sourceimport_"), so `_source_import_running` may never match; not fixed | pre-existing | none named |
| 1072 | Extension: save novel page text | Manual 6-step check on a real web-novel site (save, save again = "Already saved", Saved raw chapters panel, selection save, comic page hides section) | needs-PC-test | none |
| 1072 | (comment) | Lone-surrogate heading fix pushed; PR "stays a draft" at that time, since merged | (info) | n/a |
| 1077 | Fix stale pending-install lock | Windows ctypes path not run (Linux container) | needs-PC-test | none |
| 1075 | Fix lib.http ok/header/307-308 | On 307/308 hop `params` dropped but other kwargs kept; slightly beyond "keep kwargs body", easy to revert | needs-owner-decision | none |
| 1075 | Fix lib.http ok/header/307-308 | `tests/http_fakes.py` `StreamedBody.ok` still `status < 400` (mimics requests); left | uncertain | none |
| 1063 | Split cli: translate | `python cli.py` loads cli.py twice (as `cli` via cli_translate imports); harmless | uncertain | none |
| 1063 | (comments) | Merged base into branch; conflict only in cli.py import block; tests passed | (info) | n/a |
| 935 | Hide novel sections in Source stage | Before/after screenshots (streamer VOD, audio drama at 390 px) not committed; "attach them here" | follow-up | none |
| 935 | (comment, 2026-10-08) | CI red on `test_static_analysis ... test_no_text_capture_without_explicit_decoding` flagging `services/loaded_models_service.py:125` (`subprocess.run text=True` without encoding/errors); fails on base, also seen on #934/#939; proposed `encoding="utf-8", errors="replace"` in a separate PR, not done in this PR | unrelated-failure-seen | none named (may be fixed since; verify) |
| 1064 | Docs: sync after Oct 10 merges | `OVERSIZED_MODULE_BYTES` in `tests/test_static_analysis.py` has stale numbers (core.py 59 KB vs entry 84 KB); only the ratchet PR should lower them | follow-up | "the ratchet PR" (unnumbered) |
| 1064 | Docs: sync after Oct 10 merges | Did not audit optional imports against `diagnostics.OPTIONAL_DEPENDENCIES` | follow-up | none |
| 1064 | Docs: sync after Oct 10 merges | `lib/settings_schema.py` is declarations + reader only; parts 2 and 3 not merged | follow-up | Settings schema 2/3 (unnumbered) |
| 1058 | Jobs: library.db is authority | `write_transition` = `save_job_record` + one UPDATE in one retry (not literally one statement) | uncertain | none |
| 1058 | Jobs: library.db is authority | Retries run under `_lock`; worst case now ~3 x sqlite 5 s busy timeout instead of 5 s | uncertain | none |
| 1058 | Jobs: library.db is authority | Force stop offered from a row owned by another process, but `force_stop` works only in the owning process, so click gets 409; "PR B/C should decide" | needs-owner-decision | "PR B/C" (unnumbered) |
| 1058 | Jobs: library.db is authority | `kind = 'cli'` not written (cli.py doesn't use `start_job`); cli.py still uses `db.request_job_record_cancel` without `cancel_requested_at`; left because cli.py is frozen at size limit | follow-up | none |
| 1058 | Jobs: library.db is authority | `owner_process_alive` and `_db_cancel_requested` not moved out of `background_jobs` (tests monkeypatch there) | follow-up | none |
| 1058 | Jobs: library.db is authority | `_ensure_heartbeat` starts its thread while `_lock` held | pre-existing | none |
| 1058 | Jobs: library.db is authority | Manual: start long transcription, close Baihe window, restart; job shows cancelled "Interrupted: Baihe restarted", next GPU job doesn't wait | needs-PC-test | none |
| 1062 | Settings schema (1/3) | Bare bool keys read with `bool(...)` so hand-edited `"false"` reads true; `coerce` treats non-bool as invalid -> default; nothing calls `get()` yet; "PR 2 decides which side wins" | needs-owner-decision | Settings schema 2/3 (unnumbered) |
| 1062 | Settings schema (1/3) | `services/settings_service.py` is 34,341 B (was 33,182) | uncertain | none |
| 1062 | Settings schema (1/3) | Keys deliberately not declared (auto_backup.state/identity, jellyfin, run-time built keys, sources.db settings, BAIHE_MONTHLY_CAP_USD) | uncertain | none |
| 1061 | Split scanlate: detect | `page_server.py:64` docstring still says scanlate's `_bubble_ml_model`; left for comments-only PR | follow-up | none |
| 1061 | Split scanlate: detect | `scanlate.py` still over ceiling (66 KB); inpainting/rendering/OCR next split candidates | follow-up | (inpaint done in #1065) |
| 1065 | Split scanlate: inpaint | `scanlate.py` module docstring still mentions `inpaint_region()`; left | follow-up | none |
| 1059 | Split transcribe_service | `docs/specs/gpu-worker-plan.md` still cites old transcribe_service line numbers | follow-up | none |
| 1057 | HTTP front door wave 3 | `services/notification_service.py` (`_pinned_post`) not migrated: wave 4 | follow-up | wave 4 (unnumbered) |
| 1057 | HTTP front door wave 3 | `sources/http.py`, `live_fetch.py`, `engine_backends/local.py:174`, `scripts/source_probe.py` real Sessions not migrated: wave 4 | follow-up | wave 4 |
| 1057 | HTTP front door wave 3 | Total deadline for engine calls now lib.http default (3 x timeout incl. headers), previously only body read bounded; error messages now fixed text | uncertain | none |
| 1055 | URL media: killable yt-dlp | Wall-clock cap now also covers extraction/post-processing; child sends only yt-dlp's chained message (not DownloadError's generic advice) | uncertain | none |
| 1055 | URL media: killable yt-dlp | `video_download.DownloadAborted`/`_find_aborted` no longer raised on this path; left in place, candidate for removal | follow-up | none |
| 1055 | URL media: killable yt-dlp | Not run: real yt-dlp / Windows run (taskkill path of `kill_tree`); full suite left to CI | needs-PC-test | none |
| 1054 | HTTP front door wave 2 | `services/update_service.py:216` (releases JSON, hash, 600 MB installer stream, `auth=_no_credentials`, tuple timeout) not migrated: lib.http lacks stream-to-file | follow-up | wave 4 / lib.http change |
| 1054 | HTTP front door wave 2 | `diagnostics_installs_service.py:255` (Deno zip) and `asmr_vad.py:193` streamed downloads need a capped stream-to-file option in lib.http | follow-up | none |
| 1054 | HTTP front door wave 2 | "Wave 3" list (Session users, LLM engines, oidc_service, three streamed downloads); no guard test retired until no raw requests remain outside lib/http.py and engine_backends/ | follow-up | wave 3 (#1057 merged) / wave 4 |
| 1046 | HTTP front door wave 1 | `web_search_service`, `jellyfin_service`, `loaded_models_service` left: LAN/loopback `trust_env=False`; migrating needs a lib/http change | follow-up | none |
| 1046 | HTTP front door wave 1 | `update_service`, `diagnostics_installs_service` (streamed), `notification_service` (pinned adapter), `oidc_service` left | follow-up | none |
| 1043 | Add lib/http.py | CEDICT now via `requests` not `urllib`; a server sending `Content-Encoding: gzip` on the `.gz` would be decoded twice; untestable here, "worth one real run" | needs-PC-test | none |
| 1043 | Add lib/http.py | Not done (next wave): `sources/http.py` (`_requests_transport`, `FetchLimits`), `page_fetch.smart_fetch`/`make_request_guard` still raw requests; `safe_fetch` and `metadata_service.check_public_url` stay | follow-up | waves 1-3 (#1046/#1054/#1057 merged) |
| 1043 | Add lib/http.py | Groq/BaiheHub/Gemini transport errors now fixed text "The page could not be fetched."; `[REDACTED]` assertion dropped | uncertain | none |
| 998 | Extension: tainted canvases (twmanga) | Owner re-test: reload extension, open twmanga chapter, Translate this page, Allow this site, approve prompt | needs-PC-test | none |
| 1004 | Diagnostics installs preview/queue | No Restart/Stop button (shutdown route needs launcher token); UI explains close/reopen | follow-up | none |
| 1004 | Diagnostics installs preview/queue | OpenCV clash refused not resolved (paddleocr pulls opencv-contrib vs opencv-python); "choosing which flavour wins is a follow-up decision" | needs-owner-decision | none |
| 1004 | Diagnostics installs preview/queue | Direct `POST /dependencies/{package}/install` still in-process and skips the checks; upgrades still in-process | follow-up | none |
| 1004 | Diagnostics installs preview/queue | Loaded-file check Windows-only; queued installs skip qwen-asr `sox` fallback; NVIDIA PyTorch refused; no `--only-binary` | uncertain | none |
| 1004 | Diagnostics installs preview/queue | `diagnostics.py` grew 258 B for #903 registry entries; cap in test_static_analysis raised by that | uncertain | #903 (closed, superseded by this PR) |
| 1004 | Diagnostics installs preview/queue | `frontend/vite.config.ts` only runs `*.test.ts`, so `RealModelCheck.test.tsx` never runs | pre-existing | none |
| 1004 | Diagnostics installs preview/queue | Not verified without Windows: locked-file failure, taskkill tree kill, launcher's new console window. Manual: install PaddleOCR for Scanlate, queue, close/reopen Baihe, check Packages outcome, `import cv2, numpy`, Cancel before restart | needs-PC-test | none |
| 1052 | Jobs: cancel checks/deadlines | Sign-in timeout 10 min and OCR page timeout 120 s are guesses | uncertain | none |
| 1052 | Jobs: cancel checks/deadlines | Signin job error uses status 408 / generic `ServiceError.code` | uncertain | none |
| 1052 | Jobs: cancel checks/deadlines | "Tesseract timeout" detection matches "timeout" in pytesseract's RuntimeError text | uncertain | none |
| 1047 | Page bridge: translate outside lock | `sources/limiter` deliberately unchanged (has its own cancellable wait) | uncertain | none |
| 1049 | Frontend: SelectionBar/copy/dialog | Left alone: two SelectionBars (different shells), no common "Copied" hook, Dialog.tsx modal shell not needed | uncertain | none |
| 1001 | Link imported titles to source page | Novel panels on comic titles not changed: panel also holds Chapter images (OCR); hiding by media_type would hide it; safe change = split panel sections | follow-up | #935 (merged same day; handles audio/streamer only, comics unchanged) |
| 1048 | Split diagnostics: torch | `check_cuda` stays in `diagnostics`; reverse import done lazily | uncertain | none |
| 1044 | GPU process-job helper | Not in this PR: `resplit_`, `retime_`, `sensevoice_`, hardsub transcribe path, `real_model_check`, Benchmark Lab runs (docs/local-agent-backlog.md row 20) | follow-up | backlog row 20 |
| 1044 | (comment) | CI: 2 retime tests were its own fault (fixed); `test_background_jobs ... test_the_result_file_is_removed_once_the_job_is_done` intermittent race, not this PR -> fixed by #1051 | unrelated-failure-seen | #1051 (merged) |
| 1045 | UX naming | Backend messages reaching UI (job messages, API errors) still say "drama"; no tests cover them | follow-up | none |
| 1045 | UX naming | `report/reportBundle.ts` still labels `Workspace/Source` in problem-report bundle | follow-up | none |
| 1045 | UX naming | Manual: Library -> create title -> workspace stage strip reads Media/Translate/Review/Dub/Export, left menu Quick translate | needs-PC-test | none |
| 1045 | (comment) | CI: `test_background_jobs` result-file race (1 of 3 local runs); `wide-layout` library-grid e2e known intermittent on base -> fixed by #1051 / #1050 | unrelated-failure-seen | #1051, #1050 (merged) |
| 1005 | Small fixes: download names etc. | Files exported before the change keep old ID-based names until re-exported | uncertain | none |
| 1042 | Settings: three tabs | Before/after screenshots not attached | follow-up | none |
| 1042 | Settings: three tabs | "Notifications" and "Spending" toggle cards keep old titles, so share a title with another tab's card | follow-up | #1069 (merged; renamed to "Notify me"/"Batch resume") |
| 1041 | Make subtitles one-screen | Screenshots not attached (in session scratchpad); `tsc --noEmit` doesn't check project references (`tsc -b` does) | follow-up | none |
| 1041 | Make subtitles one-screen | Full Playwright run: 4 failures (live switch size x2, phone-spacing dense buttons x2) passed on re-run alone, attributed to load flakes | unrelated-failure-seen | none |
| 1032 | Job panel Last run card | Screenshots not attachable; sent to owner to drag onto PR | follow-up | none |
| 1032 | Job panel Last run card | Card matches stage's exact job ids, not `kind`; stalled-before-load shows "No progress for a while" without count | uncertain | none |
| 1032 | Job panel Last run card | Manual: translation Last run card + Retry; transcription idle 10 min shows amber no-progress line | needs-PC-test | none |
| 1032 | (comments) | CI `test_retranscribe_many ... test_whole_run_gives_up_on_one_timeout` failed on base; fixed by merging #1028 | unrelated-failure-seen | #1028 (merged) |
| 1039 | Process-job hardening | Cancelled-after-save transcribe skips the speed record that follows the chain start | uncertain | none |
| 1039 | Process-job hardening | Item-1 test can't reproduce a half-written pipe message (file route avoids it by construction) | uncertain | none |
| 1039 | Process-job hardening | Spawn children of dub/diarize/resegment/autotune read config from defaults, as transcribe workers do | uncertain | none |
| 1040 | UX basic view / More options | Before/after screenshots not attached | follow-up | none |
| 1035 | One job surface | `JobPanel` Cancel converted here because #1032 wasn't in base; stuck-state warning "appears once" point should be rechecked after #1032 landed | follow-up | #1032 (merged) |
| 1035 | (comment) | CI red from `dictionary.py:59` `from services import capped_body` after #1031 | unrelated-failure-seen | #1038 (merged) |
| 1037 | Shrink: lib/proc.py | Not migrated (B8): `lncrawl_service._run_process`, `installer/postinstall._run`, ffmpeg callers, `background_jobs.run_cancellable` | follow-up | "B8" backlog item |
| 1003 | Fix false Playwright row | `test_update_service::test_start_download_reports_downloading_even_if_the_thread_wins_the_race` thread race failed once in full run, passes in isolation | unrelated-failure-seen | none |
| 1003 | Fix false Playwright row | Cached-import issue: just-installed package not seen without restart (`importlib.invalidate_caches()` added); noted as a separate real issue | uncertain | none |
| 1036 | Preflight card | Two optional props (`whisperInstalled`, `onUseEngine`) beyond spec; no install output shown (paths); only `whisper_installed` reloaded after install | uncertain | none |
| 1036 | Preflight card | Before/after screenshots section present with no attachment recorded | follow-up | none |
| 1028 | Reap worker before done | `_kill_worker_group` SIGKILLs the worker pid as a group id even after reap; pid reuse could hit another process (remote on Linux) | pre-existing | none |
| 1028 | Reap worker before done | `reap_worker` looks up `kill_tree` on `background_jobs` at call time for test monkeypatching; orphan-test ESRCH fix is test-helper hardening | uncertain | none |
| 1031 | Move helpers to lib/ | `safe_fetch` not moved (needs `check_public_url`/`pinned_get` to leave `metadata_service`; a logic move) | follow-up | none |
| 1031 | Move helpers to lib/ | Sed PR could retire `services/service_errors.py` shim (~310-line churn) once lead agrees | needs-owner-decision | none |
| 1031 | Move helpers to lib/ | Docstrings in `services/disk_usage*_service.py` and `docs/specs/discover-sources-live-api-spec.md` still say `services/service_errors` | follow-up | none |
| 1033 | Per-drama draft store | Draft kept after run starts (not cleared); subtitle import options not drafted; Export format/language now per title; Re-split/Merge short lines have no Reset; drafts per browser only | uncertain | none |
| 1033 | Per-drama draft store | Manual: change fields on Transcribe/Translate/Export, switch tabs/reload, Reset to defaults, check another browser profile | needs-PC-test | none |
| 1024 | Force stop for hung thread jobs | Owner try-on-PC list: force stop on a Cancel-ignoring step, restart message, GPU-queue message, header Jobs responsiveness | needs-PC-test | none |
| 1024 | Force stop for hung thread jobs | "Needs the Opus review (job framework + a new route)" | follow-up | none |
| 1030 | docs: refresh STATUS | `docs/local-agent-backlog.md` brief B2 still says `services/live_fetch.py`; file is root `live_fetch.py` | follow-up | none |
| 1030 | docs: refresh STATUS | STATUS "what works" compiled from git log/PR bodies, not from running the app | uncertain | none |
| 1026 | Bound every AI call | Still unbounded: `services/workspace_job_service.py:594`, `blocked_retry_service.py:142`, `compare_transcription_service.py:467`, `benchmark_lab_service.py:613`, `navigator.py:49`, `title_library.py:35`, `benchmark.py:121`, `live_cue_translation.py:54`, `qa.py:60-109` | follow-up | docs/local-agent-backlog.md row 19 |
| 1026 | Bound every AI call | Job-less scopes don't use abortable Ollama path (rely on Ollama timeout); request-thread callers deadline-only; job-less cap per engine (5th concurrent call refused) | uncertain | none |
| 1026 | Bound every AI call | Deadline can cut a legitimately slow DeepSeek batch (>630 s) | uncertain | none |
| 1026 | Bound every AI call | Owner test on PC: DeepSeek (thinking+Reflect) and Ollama Cancel mid-batch; healthy Reflect batch; bad-network timeout error | needs-PC-test | none |
| 1026 | (comments) | CI red: `test_live_stalls.py` 2 tests, failing on base; fixed by #1023 | unrelated-failure-seen | #1023 (merged) |
| 1025 | Diagnostics installs as jobs | Not converted: `browser_install_service._run`, `lncrawl_service._run_process`, `background_jobs.run_cancellable` (allow-listed pending), ffmpeg callers | follow-up | none |
| 1025 | Diagnostics installs as jobs | Package Upgrade still synchronous | follow-up | none |
| 1025 | Diagnostics installs as jobs | UI doesn't re-attach to running install after reload | follow-up | none |
| 1025 | Diagnostics installs as jobs | "Opus security/concurrency review pending; full-suite result to be added" | follow-up | none |
| 1025 | Diagnostics installs as jobs | To test on PC: Install from Diagnostics > Packages, Cancel, check Jobs cancelled, library usable; run to completion | needs-PC-test | none |
| 1025 | (comment) | CI red: same two `test_live_stalls.py` tests as base | unrelated-failure-seen | #1023 (merged) |
| 997 | Capture whole chapter | Reported "47 pages / 8 received" popup text doesn't match this code's popup; server log lines/exact popup text needed | uncertain | none |
| 997 | Capture whole chapter | No Playwright spec covers the extension; `extension/verify_end_to_end.py` manual script not run (needs full Chromium + real bridge port) | needs-PC-test | none |
| 1002 | Release GPU slots at startup | Owner re-test: start long GPU job, kill Baihe in Task Manager, restart, start another GPU job | needs-PC-test | none |
| 1018 | Review "Check timing" | Thresholds untested on real audio; music/noise can pass as speech (missed flags) | uncertain | none |
| 1018 | Review "Check timing" | Overlap flagged as `timing_drift` beside existing `timing_overlap`; snap only shortens; silence or >3 s move gets no suggestion | uncertain | none |
| 1018 | Review "Check timing" | Manual on title #25 with Qwen-only backend: Timing check job, Flagged lines, Snap to speech, undo, dismiss | needs-PC-test | none |
| 1023 | Live: Whisper time limit | Not done: no conftest cleanup fixture or back-to-back warm-start test; a failing test in the file can leave a stuck thread up to 20 s that fails later tests | follow-up | none |
| 1020 | Review: transcribe gap + bulk | Owner check on title #25 (4 steps: bulk re-transcribe, apply selected, mid-run edit, waveform gap button incl. >30 s gap) | needs-PC-test | none |
| 1020 | Review: transcribe gap + bulk | "Needs Opus security review (new routes + process job)" | follow-up | none |
| 1020 | Review: transcribe gap + bulk | `review-stage` "delete is two-step" Playwright failed once under load, passed on rerun | unrelated-failure-seen | none |
| 1020 | Review: transcribe gap + bulk | "Add and transcribe" leaves new line unflagged (`add_line` has no flag; `restructure_service.py` frozen) | uncertain | none |

## Merged PRs with no not-done / follow-up section at all
(No such section, and no comments with deferred items.)
- #1074 Docs: lead-session troubleshooting guide
- #1069 UX punch list (Make subtitles cancel/reset, Preflight placement, Settings titles)
- #1067 Split core: whisper_models.py
- #1066 Split diagnostics: diagnostics_report.py
- #1060 Route table generated from decorators ("Stale rows found: None")
- #1056 Split diagnostics: upgrade_check.py
- #1053 Split core: segment_splitting.py
- #1051 Process jobs: done job already removed its result file
- #1050 e2e: Retry and wide-layout races (notes only that races did not reproduce locally)
- #1038 dictionary: import capped_body from lib
- #1034 Canary test: judge uv by executable name
- #1029 CI quick guards job first
- #1027 Sessions run area tests plus quick guards

Borderline (have a "Notes"/"Behaviour" section but nothing deferred): #1063, #1048, #1066/#1067 (notes only describe design).
