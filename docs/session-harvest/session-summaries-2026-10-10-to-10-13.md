# Last-turn summaries of every session from 2026-10-10 16:23Z to 2026-10-13 23:45Z

Only the final summary survives; transcripts are not readable from here. Sessions already listed in
`session-summaries-2026-10-04-to-10-10.md` are left out.

- 10-10T16:23 zX7DjWUW [ARCHIVED] review-W2-3 services: drama, library, ownership, settings, backup, restore, admi
    opened review issue #1124 on zkaelxz/U00 Read-only hostile review of zkaelxz/U00 filed as GitHub issue #1124
- 10-10T16:23 4KVX2TEZ [ARCHIVED] review-W3-12 CLI and headless parity with the app (relaunch 2)
    opened review issue #1123 on zkaelxz/U00 (read-only) Read-only hostile review of the CLI area filed as GitHub issue #1123 on zkaelxz/U00
- 10-10T16:27 C3BpVNmu [ARCHIVED] root-cause: timing-dependent tests in test_update_service (seen flaky on two PRs fix-update-service-test-races
    PR #1143 merged with thread-leak guards; root cause unproven PR #1143 merged into baihe-subtitler adding thread-leak guards to test_update_service.py; the root cause of the two flaky tests is still unproven.
- 10-10T16:33 4XaDU9vf [ARCHIVED] fix: destructive jobs need lines.edit; Reader page declares lines.read with a on fix-permissions-jobs-edit-reader-read **NOTE**
    PR #1135 merged; three permission follow-ups still open PR #1135 merged into baihe-subtitler; greyed-out controls and glossary re-translate permission remain open.
- 10-10T16:35 aQkTDUer [ARCHIVED] fix: "Transcribe this gap" 409 beside nested lines; snapshot-first test never ra fix-gap-insert-nested-lines **NOTE**
    PR #1144 merged into baihe-subtitler; nothing left to do PR #1144 merged into baihe-subtitler with the gap-insert fix; session unsubscribed, nothing left to do
- 10-10T16:37 aSbJkcp2 [ARCHIVED] fix: GPU probe reading stamped after the call; limiter cancel callback outside t fix-gpu-probe-stamp-limiter-cond
    PR #1133 merged into baihe-subtitler; probe and limiter fixed Merged PR #1133 into baihe-subtitler with the GPU probe stamp fix and the limiter lock release, plus tests.
- 10-10T16:37 BDfjV9AL [ARCHIVED] guard: subprocess capture scan catches conditional pipes, **kwargs and installer guard-capture-scan-conditional-pipes
    PR #1145 merged to baihe-subtitler; capture guard extended Merged PR #1145 into baihe-subtitler: capture guard now covers conditional, variable, and **kwargs pipes, asyncio, os.popen, and installer/.
- 10-10T16:42 9FvphAhr [ARCHIVED] fix-records-panel-overlap-360 fix-records-panel-overlap-360
    PR #1146 merged; Records panel buttons now wrap on phones PR #1146 merged: Preview and Restore buttons in the Records panel wrap instead of overlapping at 360 and 390px widths.
- 10-10T16:42 DhE1Zady [ARCHIVED] ci-install-ffmpeg ci-install-ffmpeg
    PR #1142 merged; ffmpeg install added to test job PR #1142 merged into baihe-subtitler; ffmpeg installed in tests.yml test job so real-binary tests run in CI
- 10-10T16:42 LrTzx14C [ARCHIVED] fix-redaction-spaced-filename-tail fix-redaction-spaced-filename-tail
    PR #1137 merged; 3 spaced-filename regression tests added Merged PR #1137 with three regression tests for spaced filenames; PATH_PATTERN unchanged because no leak was found.
- 10-10T16:42 6Nw7KWPL [ARCHIVED] remove-dead-explain-job-fetch-labels remove-dead-explain-job-fetch-labels
    PR #1140 merged; no further work pending PR #1140 (remove-dead-explain-job-fetch-labels) merged into baihe-subtitler
- 10-10T16:42 8NtpZUJ4 [ARCHIVED] migration-resolve-slice-stale-layout migration-resolve-slice-stale-layout
    PR #1136 merged; resolve_slice.py and its tests removed PR #1136 merged into baihe-subtitler, deleting resolve_slice.py and its tests and the merge-slice resolve step
- 10-10T16:50 v8sXs1Dm [ARCHIVED] resolve-1079-import-safety-copy-list hygiene-import-safety-copy-list **NOTE**
    merged baihe-subtitler into PR #1079 branch, pushed, tests green Merged origin/baihe-subtitler into hygiene-import-safety-copy-list and pushed; kept the PR's computed copy list in the test_db.py conflict. test_db 185 passed, guards 187 passed/1 skipped; PR #1079 body rewritten, still draft.
- 10-10T16:50 viZ5e2DH [ARCHIVED] carry-1082-http-review-lows fix-review-http-settings-leftovers **NOTE**
    PR 1082 fixes not applied; branch lacks #1075 code Choose: merge baihe-subtitler into fix-review-http-settings-leftovers, or move the four fixes to a new branch off the current base
- 10-10T16:50 EjfEC128 [ARCHIVED] resolve-1083-install-leftovers-oslock fix-review-install-leftovers
    I'll check it again at about 04:04 UTC, and sooner if a review, a CI result or a base-branch change comes in.
- 10-10T16:50 4WCFqJqm [ARCHIVED] resolve-1085-jobs-leftovers-carry-1078 fix-review-jobs-leftovers
- 10-10T16:57 y8P1TgoX [ARCHIVED] look: unnamed flaky test failures in four session notes docs-session-harvest
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-10T16:58 jxRZHMkp [ARCHIVED] resolve-947-stereo-dsp-library step-stereo-b1-dsp-library **NOTE**
    merged baihe-subtitler into PR #947; guard run 343 passed Merge commit 6b2c1b2 pushed to step-stereo-b1-dsp-library; PR #947 still draft with 2 unfixed issues noted
- 10-10T16:58 ZAxdccF6 [ARCHIVED] db: refuse to open a library written by a newer app version db-refuse-newer-version
    PR #1148 merged; app refuses start on newer library.db PR #1148 merged into baihe-subtitler; app refuses to start when library.db was written by a newer version, with four known gaps listed in the PR body
- 10-10T16:59 6rhrv3Tn [ARCHIVED] fix: gpu_process_job review lows after #1076 fix-gpu-process-job-lows
    PR #1147 merged; unsubscribed from PR activity PR #1147 merged; agent reports nothing further to do
- 10-10T17:18 jNKeKK4j [ARCHIVED] carry-1082-http-review-lows (merge base first) fix-review-http-settings-leftovers **NOTE**
    PR #1082: merged base, four lib/http fixes pushed, still draft PR #1082 merged origin/baihe-subtitler and added fixes L1-L4 at 9eab870; still a draft, CI not checked.
- 10-10T17:21 9s93Bpbj [ARCHIVED] fix-1093-display-url-test-expectations fix-display-url-slugs
    PR #1093 merged into baihe-subtitler; 7 CI checks green PR #1093 (slug paths in display_url, Ollama, CEDICT fixes) merged into baihe-subtitler; check-in cancelled
- 10-10T17:34 dFs4C8pN [ARCHIVED] friendly download names: glossary, dub track, Reader exports friendly-download-names-rest
    PR #1149 merged; glossary/dub/CSV/Anki/wiki exports renamed Friendly download names extended to glossary, dub/narration, vocabulary CSV, Anki deck and universe wiki via merged PR #1149.
- 10-10T17:45 eVzLnTAv [ARCHIVED] storage: cap raw transcripts, compress old files, backup space check, per-kind s storage-retention-and-sizes
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — List Tri… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — List Tri…
- 10-10T17:45 uC3sES6E [ARCHIVED] owner-only full source URL and a Keep original pin per title keep-original-and-full-url
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T…
- 10-10T18:15 PJNnJ5E9 [ARCHIVED] resolve-1139-frontend-test-gaps test-frontend-coverage-gaps **NOTE**
    merged baihe-subtitler into PR #1139; tests green, draft Merged origin/baihe-subtitler into test-frontend-coverage-gaps and pushed; tsc clean, vitest 2230 pass, targeted Playwright 166 pass, guard tests 187 pass. PR #1139 remains a draft.
- 10-10T18:15 DvVMdke8 [ARCHIVED] resolve-1087-extension-canvas-match fix-extension-canvas-match **NOTE**
    PR #1087 merged with baihe-subtitler; tests green, still draft PR #1087 merged with origin/baihe-subtitler and pushed; popup conflict resolved, tests pass, PR still draft
- 10-10T18:16 s5UDiDy3 [ARCHIVED] resolve-1085-after-1076 (gpu_process_job conflict) fix-review-jobs-leftovers **NOTE**
    PR #1085 all 8 checks green, mergeable; still draft, watching
- 10-10T18:18 J7auZjqc [ARCHIVED] investigate: OpenCV flavour for PaddleOCR (contrib vs standard)
    opened issue #1154 recommending contrib-wins (Option A) Issue #1154 (https://github.com/zkaelxz/U00/issues/1154) recommends Option A: keep opencv-contrib-python 4.10, with no repo code changes or pushed branch.
- 10-10T18:21 1nVoDrqN [ARCHIVED] fix-1131-review: accept_risk from the UI, real writable probe, Deno stub fix-install-now-safety **NOTE**
    PR #1131 merged into baihe-subtitler; check-in cancelled PR #1131 merged after CI green on b72078b; follow-up card queued for install preview gap
- 10-10T18:21 FffYEPgw [ARCHIVED] carry review lows: glossary case, launcher pid overflow, small test and doc fixe fix-review-lows-glossary-launcher
    PR #1152 merged to baihe-subtitler; all review findings landed PR #1152 merged into baihe-subtitler, carrying review findings from #1125, #1133, #1092, #1090; nothing further needed
- 10-10T18:21 QioYkyfJ [ARCHIVED] fix-1132-review: default budget must not drop pages silently, Referer on redirec fix-generic-import-bounds **NOTE**
    PR #1132 merged to baihe-subtitler; CI was green PR #1132 merged into baihe-subtitler at 22:40 UTC with CI green; follow-ups left: lib/http.py slow-drip fix and manual check on manhuaku and Bilibili Manga.
- 10-10T18:22 coxSU5zL [ARCHIVED] fix-1134-review: ranobes cap, manhuagui radix, detect offsets, escape paths fix-sources-parser-bounds **NOTE**
    PR #1134 merged into baihe-subtitler; 7/7 CI green PR #1134 (bound source parsers) merged into baihe-subtitler; 23:28 check-in cancelled; kuaikan/manhuagui error-text follow-up queued
- 10-10T18:22 SjcjstJW [ARCHIVED] fix-1126-review: NUL placeholder crash, name boundaries, torch still loaded fix-diagnostics-redaction-gpu-status **NOTE**
    PR #1126 CI green, no conflicts; awaiting lead-session merge PR #1126 is green on 5f68080 with no review comments or conflicts; still a draft, waiting for the lead session to merge
- 10-10T18:34 e3tBQKnh [ARCHIVED] simplify-S1 jobs, process workers, lib helpers
    opened issue #1157: 10 simplification items, ~330 lines Issue #1157 on zkaelxz/U00 lists 10 read-only simplification items (~330 lines), with top wins for shared job-row checks, pid-alive helper, and Jobs poll cost
- 10-10T18:34 uN87oyYm [ARCHIVED] simplify-S2 db, storage, library, backup
    filed #1162: 39 live single-statement write blocks in db.py Issue #1162 filed for the read-only db.py review; it reports 39 live single-statement write blocks, from a line-based scan that replaced a stopped regex run.
- 10-10T18:34 ig7tgNcy [ARCHIVED] simplify-S3 translation, engines, bulk, CLI
    simplification review: 5 ranked items, dead re-exports first Read-only review of the Baihe subtitle area: dead re-exports, 14 duplicated usage-log sites, bulk-translate and hard-term hot spots; gaps listed in the issue.
- 10-10T18:34 XjfbvuiS [ARCHIVED] simplify-S4 transcription, ASR, live, core
    filed U00#1158 simplification review: 12 candidates, ~270 lines Filed zkaelxz/U00#1158 (transcription/audio simplification review): 12 candidates, ~270 removable lines; no repo edits.
- 10-10T18:34 9EvoGBvh [ARCHIVED] simplify-S5 sources, adapters, extension, page import
    filed simplification review #1155; ~330 removable lines Simplification review filed as zkaelxz/U00#1155 (8 items, ~330 removable lines); no repo files changed
- 10-10T18:34 8ro9gPkK [ARCHIVED] simplify-S6 API routers, schemas, auth, permissions
    filed simplification review as U00 issue #1160; 6 items Filed read-only simplification review as zkaelxz/U00 issue #1160: 6 items, about 330 lines (estimate); no repo edits, no tests run.
- 10-10T18:34 5hX2nVpx [ARCHIVED] simplify-S7 frontend pages, components, API client
    filed simplification review #1159; ~550-650 lines removable Read-only simplification review filed as zkaelxz/U00#1159 (~550-650 removable lines); tests not run since node_modules is missing.
- 10-10T18:34 oRsMPQBU [ARCHIVED] simplify-S8 frontend reader, comic, live, benchmark, assistant
    filed issue #1156: ~250 removable lines, ~41s e2e sleeps Filed read-only simplification review as zkaelxz/U00 issue #1156 (~250 removable lines, ~41s fixed e2e sleeps); no repo changes.
- 10-10T18:34 zZ4EDPKb [ARCHIVED] simplify-S9 python tests and CI time
    CI review: ~6 runner-min/run saveable; isolated_db is top win Read-only CI review complete: swapping isolated_db's init_db() for a template copy saves ~3.5 min/run, about 6 runner-min total, and the findings are in the issue.
- 10-10T18:46 9FBfmVWg [ARCHIVED] live: translation on its own worker, one call per chunk, turbo, lag trims, final live-lower-latency
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T…
- 10-10T18:46 H2WbCvzv [ARCHIVED] exploratory test E1: library, import, export, download names
    filed issue #1163: 13 Baihe defects found, no repo changes Filed GitHub issue #1163 listing 13 Baihe defects (2 wrong-data); no repo files changed or pushed.
- 10-10T18:46 QX4a8Gaf [ARCHIVED] exploratory test E2: workspace stages, desktop, phone, keyboard
    exploratory test of Baihe done; filed issue U00#1168 Filed zkaelxz/U00#1168: 15 findings incl. server freeze on regex preview; no repo files changed
- 10-10T18:46 zzwmHs2V [ARCHIVED] exploratory test E3: API and permissions as three account types  **NOTE**
    security report filed as U00#1167: 0 high, 0 med, 3 low Exploratory security test report filed at zkaelxz/U00#1167 (0 HIGH, 0 MEDIUM, 3 LOW, 1 informational); repo left clean.
- 10-10T18:46 SirD2XgE [ARCHIVED] exploratory test E4: settings, diagnostics, jobs, restart behaviour
    filed issue #1166: 7 findings, orphaned ffmpeg worst Exploratory test filed as zkaelxz/U00#1166: seven findings, none data-losing; orphaned ffmpeg after kill is worst. No repo files changed.
- 10-10T18:46 6ePeiErJ [ARCHIVED] exploratory test E5: reader, comic, live, assistant pages
    filed #1179: 19 Baihe defects; reader long-line hang worst Filed GitHub issue #1179 with 19 exploratory-test defects; no repo files changed and servers stopped.
- 10-10T18:49 dBYMNYMd [ARCHIVED] resolve-1111-modernize-extension-js modernize-extension-js
    merged base into PR #1111 (ff92f8f); pushed, tests green Merged origin/baihe-subtitler into modernize-extension-js (ff92f8f) and pushed; the one conflict was in FILE_ORGANIZATION.md; 123 + 190 tests pass.
- 10-10T18:50 hTvvYxjs [ARCHIVED] fix: sharing settings while /me unavailable, Google sign-in in a new tab fix-sharing-and-login-leftovers **NOTE**
    PR #1164 merged; Google sign-in still needs manual check PR #1164 merged into baihe-subtitler. Owner must manually test Google sign-in on PC; Diagnostics nav and 401 draft-loss remain open.
- 10-10T18:51 i2aMvK6q [ARCHIVED] fix-1100-review: leaked claim on DB error, chain lock timeout, bounded dicts fix-page-server-inflight
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T…
- 10-10T18:55 M3zxpDKq [ARCHIVED] fix-1083-review: unparsable version dropped from plan, lock-order and test leaks fix-review-install-leftovers **NOTE**
    base #1183 fixed lost-draft bug; PR #1083 description updated Yes, #1183 fixed the lost-draft bug in the app; PR #1083 is green, mergeable, and still a draft awaiting the lead session's merge.
- 10-10T19:00 zfQj8hL1 [ARCHIVED] fix-find-replace-redos: refuse catastrophic regex in find and replace fix-find-replace-redos
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T…
- 10-10T19:00 NW14oULh [ARCHIVED] test-coverage-review-1121: tests that can fail, exact errors, contention, no net test-coverage-review-1121
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-10T19:00 Risn5y2E [ARCHIVED] fix-scanlate-ocr-lock-decode: one model lock, decode once, chunked translation fix-scanlate-ocr-lock-decode
- 10-10T19:08 Ri5Q246L [ARCHIVED] bulk translate: money and data fixes (#1096 H4, H5, M12 to M14) fix-bulk-translate-review
    PR #1174 green on 16bc7a9; awaiting review; re-check 02:14 UTC
- 10-10T19:08 p993ujmy [ARCHIVED] comic and reader fixes (#1103 H3, M4, M6) fix-comic-reader-review
    PR #1171 merged; H3, M4, M6 from #1103 fixed Fixed H3, M4 and M6 from issue #1103 in merged PR #1171 on baihe-subtitler.
- 10-10T19:08 mbDCo5gX [ARCHIVED] design-audit follow-ups: Library first screen, Make subtitles, orphan Ready, Spo design-audit-followups **NOTE**
    PR #1173 merged to baihe-subtitler; 5 follow-ups landed PR #1173 merged into baihe-subtitler with five follow-ups landed; three found-but-not-fixed items remain open.
- 10-10T19:12 LJig89Gt [ARCHIVED] flag lines whose source text changed after translation ("source changed") flag-source-changed-lines
    PR #1184 green on 242bd17; awaiting review, recheck 02:14 UTC
- 10-10T19:18 Zoq9W73h [ARCHIVED] job queue: a user-managed list of titles and steps, run now or at a set time user-job-queue **NOTE**
    job queue PR #1185 merged into baihe-subtitler; CI green Job queue merged as PR #1185 into baihe-subtitler with CI green; three known issues are listed in the PR body.
- 10-10T19:24 V1mKzGZA [ARCHIVED] test-coverage-review-1121 (trimmed): honest tests only test-coverage-review-1121 **NOTE**
    PR #1175 merged; thread-leak filter follow-up left open PR #1175 merged into baihe-subtitler; item 9 (unhandled-thread filter) deferred because a leaked retranscribe_worker thread fails an unrelated test.
- 10-10T19:24 3XcLQFYG [ARCHIVED] small must-fixes: tsc command, .env bytes, GBK novels, backup vanish, device-tok small-must-fixes-oct10 **NOTE**
    PR #1176 merged by zkaelxz; 4 skipped fixes noted PR #1176 merged; skipped fixes (.env bad-byte, novel encoding, auto-backup, STATUS.md tokens) listed under Found but not fixed
- 10-10T19:33 FEDqyWZL [ARCHIVED] B5 server small fixes: style-learning privacy, atomic export, locale on fix-flag batch-b5-server-small-fixes **NOTE**
    PR #1182 merged: 3 of 9 audit items fixed, 6 skipped PR #1182 merged to baihe-subtitler with 3 of 9 audit items; other 6 deferred to PR body, pending open PRs touching those files.
- 10-10T19:33 1vaGdshA [ARCHIVED] B9 test tidy: stereo importorskip, bare join in update tests, CI timing bounds batch-b9-test-tidy **NOTE**
    PR #1177 merged into baihe-subtitler; task finished PR #1177 merged into baihe-subtitler; wall-clock timing bounds noted as follow-up
- 10-10T19:38 P4PTepGT [ARCHIVED] PaddleOCR with opencv-contrib: pin versions, swap step in the install plan, nump paddleocr-opencv-contrib **NOTE**
    PR #1181 merged with step 1 only; steps 2-4 deferred PR #1181 merged into baihe-subtitler with step 1 check and tests; steps 2-4 wait on PRs #1083, #1131, #1081, #891, #1126
- 10-10T20:12 AmCqC2Db [ARCHIVED] setup.bat: install Git, Python 3.12, ffmpeg and Node with winget on a new Window setup-bat-new-pc
    PR #1186 merged; setup.bat still untested on Windows setup.bat merged via PR #1186 into baihe-subtitler; Windows run and owner PC check still pending
- 10-10T20:43 wFML6PYs [ARCHIVED] Echo test engine: a key-free, model-free translator behind Developer Mode echo-engine-dev-mode
    PR #1188 merged; Echo (testing) engine live behind Dev Mode Echo (testing) engine merged in PR #1188, shown only with Developer Mode on; four known gaps remain open in the PR body.
- 10-10T20:49 6bDRtL8k [ARCHIVED] Transcribe options grid: even rows, no clipped Voice detector text fix-transcribe-options-grid **NOTE**
    PR #1189 merged after CI green; two layout gaps left open Merged PR #1189 (CSS/markup fix for the Transcribe Advanced options .source-grid) into baihe-subtitler; two layout gaps are documented as unfixed in the PR body.
- 10-10T20:54 z81x647b [ARCHIVED] Audit: every control that needs something installed, and what it does when it is
    audit filed: missing-dependency controls fail silently Read-only audit filed as one issue in zkaelxz/U00: Transcribe, Export, Dub, and engine pickers fail or silently skip when deps are missing; batch 1 (Transcribe) proposed first.
- 10-10T21:25 FegSbvBE [ARCHIVED] fix-1085-review: a reused pid must not keep a dead server's job row running fore fix-review-jobs-leftovers
    PR #1195 merged; queued-job delete moved outside lock PR #1195 merged into baihe-subtitler: cancelling a queued job now deletes its DB record after releasing the shared jobs lock
- 10-10T22:40 6BKtABfn [ARCHIVED] batch-setup-winget: setup.bat skips tools already installed batch-setup-winget **NOTE**
    PR #1193 merged; setup.bat skips installed winget tools PR #1193 merged into baihe-subtitler; setup.bat skips winget installs for tools already listed. Windows double-run check not yet run.
- 10-10T22:40 EEycmn9E [ARCHIVED] batch-sticky-topbar: top-right toolbar stays visible while scrolling batch-sticky-topbar
    PR #1196 merged into baihe-subtitler; sticky toolbar in Sticky top-right toolbar shipped via PR #1196, merged into baihe-subtitler; CI green, session unsubscribed.
- 10-10T22:40 sAnM8cKe [ARCHIVED] batch-gpu-cublas: detect missing CUDA runtime for GPU Whisper batch-gpu-cublas **NOTE**
    draft PR #1194 open; waiting on CI and review events
- 10-10T22:41 aoLR7eX4 [ARCHIVED] resolve-1073 (S1): merge base into jobs-db-first-gpu-slots, prove invariants jobs-db-first-gpu-slots **NOTE**
    PR #1073 merged to baihe-subtitler; CI green on f6444cf PR #1073 merged into baihe-subtitler at f6444cf with CI green. Known gap: the GPU owner liveness check runs under background_jobs._lock (MED 4); follow-up queued.
- 10-10T22:50 G6X4Uhkp [ARCHIVED] fix-1174-review: claim needs_resubmit before retrying the next stage fix-bulk-translate-review **NOTE**
    three review fixes tested; PR #1174 comment added Fixed three review items on PR #1174 with failing-first tests (285 and 188 passing); two related issues left unfixed and noted in a PR comment.
- 10-10T23:05 rs3WrDWz [ARCHIVED] fix-1194: re-home the CUDA runtime check onto the current base batch-gpu-cublas
    pushed merge 41d4dd9 to batch-gpu-cublas; focused tests pass Merged origin/baihe-subtitler into batch-gpu-cublas (41d4dd9), pushed, and commented on PR #1194; 610 focused tests pass, full suite not run.
- 10-11T00:03 32TGc3jg [ARCHIVED] fix-source-novel-panel-race: stop the raw-novel panel moving after Remove fix-source-novel-panel-race **NOTE**
    PR #1197 merged; flaky Source-page e2e race fixed PR #1197 merged into baihe-subtitler; flaky remove-audio/video e2e failure dropped from 2/10 to 0/10 runs
- 10-11T00:13 ZgT8Bdsy [ARCHIVED] resolve-1068 (S2): merge base into settings-schema-one-reader, prove tests settings-schema-one-reader **NOTE**
    PR #1068 merged; settings_service.get is sole reader PR #1068 merged into baihe-subtitler; settings_service.get(key) is the only settings reader. Three archived docs still name deleted functions and were left as historical records.
- 10-11T00:13 CYdxRQW7 [ARCHIVED] batch-e-data: import and search correctness (Shift-JIS, titles, search wildcards batch-e-data-import-search
- 10-11T00:14 dfBGQtrm [ARCHIVED] batch-e-reader: Reader long-line hang, wrong page after failed load, CSV injecti batch-e-reader-safety-perf **NOTE**
    PR #1198 merged to baihe-subtitler; R2 still open, needs #1171 PR #1198 merged into baihe-subtitler; R2 (stale text after failed next page) remains open and depends on #1171.
- 10-11T00:19 7q6LCBdw [ARCHIVED] batch-ci-speed: path-based CI job selection, shorter test job batch-ci-speed **NOTE**
    PR #1200 merged into baihe-subtitler; CI 20.5m to 12m Draft PR #1200 (faster CI) merged into baihe-subtitler; second run took about 12 minutes versus 20.5 for the first.
- 10-11T00:29 KH8GtWGh [ARCHIVED] resolve-1126: merge base into fix-diagnostics-redaction-gpu-status fix-diagnostics-redaction-gpu-status
    PR #1126 merged into baihe-subtitler; watch ended PR #1126 merged into baihe-subtitler by zkaelxz; agent has stopped watching it
- 10-11T00:29 tezCyN1T [ARCHIVED] resolve-1184: merge base into flag-source-changed-lines flag-source-changed-lines **NOTE**
    merged base into PR #1184; size fixes; tests green Merged the moved base into PR #1184 with two size-limit fixes; tests pass (vitest 2,257, tsc clean). Two judgement calls are noted in the PR comment.
- 10-11T00:40 YaZax53V [ARCHIVED] split-installer-service: installer/service.py into domain modules split-installer-service-modules
    PR #1202 merged into baihe-subtitler; service.py now 36.7 KB PR #1202 merged into baihe-subtitler by zkaelxz; installer/service.py split to 36.7 KB, CI green
- 10-11T01:43 BZLH51xW [ARCHIVED] resolve-1071 (S3): merge base into settings-schema-generated-form, prove tests settings-schema-generated-form
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Update T… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Update T…
- 10-11T02:31 mqnU7LxK [ARCHIVED] resolve-1115: merge base into hygiene-server-messages-title hygiene-server-messages-title
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Delete T…
- 10-11T02:31 R1TpVKS2 [ARCHIVED] resolve-1070 (S4): merge base into shrink-http-wave4-sessions shrink-http-wave4-sessions
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Send Lat… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Send Lat…
- 10-11T02:31 9L8DyQr1 [ARCHIVED] resolve-1081: merge base into fix-review-lows-pip-tail fix-review-lows-pip-tail **NOTE**
    merged baihe-subtitler into #1081; pip-tail tests pass Merged baihe-subtitler into fix-review-lows-pip-tail and pushed; kept both sides' tests in test_pending_install.py. PR #1081 remains a draft; full suite left to CI.
- 10-11T02:31 Lfm6bRg7 [ARCHIVED] resolve-1188: merge base into echo-engine-dev-mode echo-engine-dev-mode **NOTE**
    merged baihe-subtitler into PR #1188; backend+frontend tests pass Merged baihe-subtitler into echo-engine-dev-mode and fixed the FILE_ORGANIZATION.md conflict and size-guard overage; PR #1188 is still draft, with CI not yet run on the new head.
- 10-11T03:16 UuZACye4 [ARCHIVED] batch-flags-llm: Flag pass crash, language-aware prompts, keep system flags batch-flags-llm-tasks **NOTE**
    PR #1207 merged: three LLM review pass fixes landed PR #1207 merged into baihe-subtitler with the three LLM review pass fixes; three follow-up items remain open and unaddressed.
- 10-11T03:16 CcTmgqSm [ARCHIVED] batch-bench-local-model: let Benchmark Lab test the offline Whisper model folder batch-bench-local-model
    PR #1206 merged; Benchmark Lab local-folder arm in PR #1206 merged into baihe-subtitler; Benchmark Lab can run transcription against an offline local Whisper model folder
- 10-11T03:29 eiZ6PA1v [ARCHIVED] resolve-1099: merge base into fix-services-review-lows fix-services-review-lows
    PR #1099 merged into baihe-subtitler; CI green on final commit Merge conflict on PR #1099 resolved; fixed cancel-across-runs and download-label test gaps, then merged into baihe-subtitler.
- 10-11T03:29 4NW7dNM8 [ARCHIVED] resolve-1080: merge base into fix-review-lows-oct10 fix-review-lows-oct10 **NOTE**
    resolved PR #1080 merge conflicts, pushed; guard tests green Merged baihe-subtitler into fix-review-lows-oct10, resolved 2 conflicts, pushed; PR #1080 still draft; guard tests pass, CI runs full suite.
- 10-11T03:29 X55Yni2g [ARCHIVED] resolve-1175: merge base into test-coverage-review-1121 test-coverage-review-1121 **NOTE**
    merged baihe-subtitler into PR #1175 branch; 551 tests pass Merged baihe-subtitler into test-coverage-review-1121 and resolved one conflict in test_sources_bilibili.py; touched tests and guards pass (551 passed, 1 skipped). PR #1175 remains draft.
- 10-11T03:59 AzmKy1yc [ARCHIVED] batch-ja-model-tool: safe fetch/convert/check script for Japanese Whisper fine-t batch-ja-model-tool
    PR #1211 ready for review; CI green; lead merges PR #1211 (batch-ja-model-tool) ready for review, CI green on 2439d91; merge is the lead session's step
- 10-11T06:28 ogGtPtvu [ARCHIVED] batch-ci-shard: shard the test job, cache installs, slowest-tests report, merge- batch-ci-shard **NOTE**
    PR #1214 merged; 4-shard test matrix cut test job PR #1214 merged: test job now 4 shards (slowest ~5m16s); merge queue still needs owner to enable
- 10-11T06:34 2TYdLs9T [ARCHIVED] batch-fnf-privacy-log: sign-in codes out of the access log, private style edits, batch-fnf-privacy-log **NOTE**
    PR #1212 merged by lead; four privacy/log fixes landed PR #1212 merged into baihe-subtitler: sign-in codes kept out of logs, style learning scoped to visible titles, atomic exports; two follow-ups listed in PR body.
- 10-11T06:34 bZ8WmbcS [ARCHIVED] batch-fnf-money: no second paid bulk run on Resume, write drafts of failed batch batch-fnf-money **NOTE**
    PR #1213 merged into baihe-subtitler; session unsubscribed PR #1213 merged into baihe-subtitler after green CI; Reflect no-results/batch-failed drafts still unwritten and listed as open in PR body
- 10-11T06:34 TuztUztQ [ARCHIVED] batch-fnf-cheap: wall-clock tests, encoding guard, dead code, small leftovers batch-fnf-cheap **NOTE**
    PR #1215 merged into baihe-subtitler; 3 items left open PR #1215 merged into baihe-subtitler; automatic-backup, chat-engine echo, and reader timing items left unfixed
- 10-11T07:40 CsKds48D [ARCHIVED] batch-precommit: pre-commit hooks (ruff errors-only, large-file warning, whitesp batch-precommit
    PR #1216 merged; pre-commit config landed, no further work PR #1216 merged adding .pre-commit-config.yaml; ~20 .py files still lack a final newline, needing a separate cleanup PR if wanted
- 10-11T08:46 dymvVF4g [ARCHIVED] resolve-891: merge base into qwen3-asr-native qwen3-asr-native
    merged baihe-subtitler into #891; CI pending on 1355e9f
- 10-11T09:39 dcnUMMxJ [ARCHIVED] resolve-883: merge base into translate-by-sentence translate-by-sentence **NOTE**
    pushed a545aca fixing 2 specs; local specs green, CI unchecked Pushed a545aca to translate-by-sentence: 'Advanced' changed to 'More options' in two Playwright specs, which pass locally. PR #883 is still draft, CI not checked, and the PR body is stale.
- 10-11T12:39 9tjn8XXy [ARCHIVED] resolve-943: merge base into step-db-0-guards-and-rename step-db-0-guards-and-rename
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Subscrib… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Subscrib…
- 10-11T12:39 Swengz2J [ARCHIVED] batch-fnf-money2: Gemini batch expiry drafts, re-apply guard, atomic Resume, for batch-fnf-money2
    PR #1217: Opus review fixes pushed, CI green at 07ff524 PR #1217 on batch-fnf-money2 (head 07ff524): three review fixes and new tests pushed, CI green; ready for lead merge
- 10-11T12:39 cuEhNoiJ [ARCHIVED] batch-followups: retention fsync, Trash label, export space check, label restore batch-followups
- 10-11T15:06 cCv3Ym7t [ARCHIVED] batch-db-1: first db/ split step (leaf domain move, guards ready for relative im batch-db-1
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Unsubscr… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Unsubscr…
- 10-11T15:08 BNq7sKMA [ARCHIVED] batch-diag-tabs: Diagnostics page split into tabs (shared Tabs component) batch-diag-tabs
    PR #1228 merged to baihe-subtitler; unsubscribed Diagnostics tabs work merged into baihe-subtitler via PR #1228
- 10-11T15:08 2svbCjFR [ARCHIVED] batch-ja-picker: whisper-ja-1.5B as an opt-in Japanese model (pinned, safe file  batch-ja-picker
    PR #1223 ready for review, CI green; watching for changes
- 10-11T15:08 U65a4Aa5 [ARCHIVED] batch-review-flags: Fix flagged by reason, spend cap on live jobs and AI re-segm batch-review-flags **NOTE**
    PR #1225 merged; fix-flagged batching and spend cap landed PR #1225 merged into baihe-subtitler: batched fix-flagged, monthly spend cap, preset cleanup; prompt still needs a paid Opus pass.
- 10-11T15:09 zer27bRZ [ARCHIVED] batch-subtitle-rules: per-language reading speed, Netflix duration/gap checks be batch-subtitle-rules
- 10-11T15:09 7Swp9WvD [ARCHIVED] batch-uninstall: uninstall needs the typed word DELETE for the library, lists le batch-uninstall
    PR #1220 merged to baihe-subtitler; Windows check pending PR #1220 (batch-uninstall) merged into baihe-subtitler; owner-PC Windows check still outstanding
- 10-11T15:09 DkqUpD3a [ARCHIVED] batch-updater-prerelease: opt-in pre-release updates (beta channel), off by defa batch-updater-prerelease
- 10-11T15:32 dsnipJT2 [ARCHIVED] W-SOURCE-PROBE-CLOUD batch-source-probe-cloud **NOTE**
    PR #1227 draft CI green; watching for review comments
- 10-11T15:32 U5NfyraX [ARCHIVED] W-SELF-TEST batch-self-test
- 10-11T15:42 2nsQJVqN [ARCHIVED] W-MODEL-LANG-GATING batch-model-language-gating
    PR #1229 marked ready; CI green on d7db656, unmerged PR #1229 ready for review on d7db656, all 13 checks green; lead session to merge
- 10-11T16:16 7Sup4Ne8 [ARCHIVED] W-V100-GPU-SUPPORT batch-v100-gpu
    Running a cu126 install on the actual card is still a manual check for you.
- 10-11T16:40 kcZQN2X7 [ARCHIVED] DB-2: auth storage to db/auth.py batch-db-2
    PR #1231 DB-2 merged into baihe-subtitler; CI green DB-2 auth storage moved to db/auth.py; PR #1231 merged into baihe-subtitler with CI green.
- 10-11T16:40 qjhMso7m [ARCHIVED] W-TEST-RACE-LIVE-CUE-FEED batch-test-race-live-cue
- 10-11T16:55 D2uUAXwh [ARCHIVED] W-TRANSLATE-BATCH-DEFAULT batch-translate-batch-default
    PR #1237 merged; engine-aware batch default in baihe-subtitler PR #1237 merged, so baihe-subtitler now has the engine-aware batch default. Manual check still pending: a 1,000-line Sonnet episode with batch size blank.
- 10-11T16:56 FuHcGeXr [ARCHIVED] W-WINDOWS-CI-SCRIPT-CHECKS batch-windows-ci-scripts
- 10-11T16:56 MaET4wN3 [ARCHIVED] W-FNF-SWEEP-1 small leftovers batch-fnf-sweep-1
    PR #1233 ready for review, CI green; watching for review events
- 10-11T16:59 2ip4ivgw [ARCHIVED] W-TRANSLATE-CONTEXT-BENCH batch-translate-context-bench
- 10-11T17:03 A1zuu6jd [ARCHIVED] W-RESEARCH-E whisper path cleanup batch-research-e **NOTE**
    PR #1236 merged into baihe-subtitler despite do-not-merge PR #1236 (batch-research-e) merged into baihe-subtitler; CI green on latest head before merge Review PR #1236, which was merged into baihe-subtitler although you asked for a draft with no merge. Revert the merge if you didn't intend it.
- 10-11T17:10 WgUb5nBV [ARCHIVED] W-RESEARCH-SMALL independent leftovers batch-research-small
    PR #1238 merged by repo owner; checks green on 3881e6e PR #1238 (batch-research-small) merged by repo owner after all checks passed on head 3881e6e
- 10-11T17:33 j3gmEacs [ARCHIVED] W-LANGPACKS housekeeping (orphan + Settings list) batch-langpacks-housekeeping **NOTE**
    PR #1239 merged into baihe-subtitler; 2 follow-ups open PR #1239 merged into baihe-subtitler; follow-ups left open: move title language-pack DELETE into db/, add Playwright spec for Language packs card
- 10-11T17:33 tFxVruad [ARCHIVED] W-LIVE-GLOSSARY title glossary and packs in Live batch-live-glossary
    PR #1240 CI green, merged by zkaelxz; unsubscribed Live glossary change merged into baihe-subtitler via PR #1240; manual PC check still open for user
- 10-11T17:39 KpZTf9GH [ARCHIVED] W-GLOSSARY-VARIANTS find name variants batch-glossary-variants
    PR #1244 merged: glossary sound-alike variants; PC check open Glossary sound-alike spelling feature merged via PR #1244 into baihe-subtitler; manual PC check and phase-2 items remain open.
- 10-11T17:41 wqPYi5Nn [ARCHIVED] W-TEST-LEAK hold_job threads batch-test-leak-hold-job **NOTE**
    PR #1241 merged after CI went green; test-leak fix landed Draft PR #1241 (test-only _hold_job wait fix for test_api_versions_retry_blocked) was marked ready and merged by zkaelxz; CI green on all shards.
- 10-11T17:52 A9CGqMsh [ARCHIVED] DB-3: runtime-state storage out of db/__init__.py batch-db-3
- 10-11T17:52 i51PczsN [ARCHIVED] W-REVIEW-FOLLOWUPS fix flagged and re-segment batch-review-followups **NOTE**
    PR #1242 merged; all five #1225 follow-ups on baihe-subtitler PR #1242 merged into baihe-subtitler, though the ask was a draft PR with no merge; "Found but not fixed" items remain open
- 10-11T17:52 WkNCXZgN [ARCHIVED] W-RESEARCH-A2 dismissals and export fps batch-research-a2
    PR #1243 merged despite do-not-merge; both items in PR #1243 merged into baihe-subtitler: dismissed reading-speed flags persist, SRT export uses title frame rate; manual PC check still pending.
- 10-11T18:46 vPRi3LSv [ARCHIVED] W-VTUBER-PACKS ja/zh VTuber packs + rank before cap batch-vtuber-packs **NOTE**
    PR #1247 merged into baihe-subtitler; four VTuber packs in Merged PR #1247 with four VTuber packs and the rank-before-cap change; entries needing a native-reader check and two follow-ups are listed in the PR body.
- 10-11T18:52 dAb4frhT [ARCHIVED] W-KOTOBA kotoba-whisper v2.0-faster for Japanese batch-kotoba
    PR #1248 merged into baihe-subtitler; kotoba-whisper v2.0 landed PR #1248 merged into baihe-subtitler; manual PC check on 3080 Ti/V100 still open
- 10-11T19:10 sUs7ddkJ [ARCHIVED] W-FNF-SWEEP-2 leftover bugs from merged sessions batch-fnf-sweep-2 **NOTE**
    PR #1249 draft; CI green, no review comments Draft PR #1249 for W-FNF-SWEEP-2 is open with CI green on the required jobs; the lead session can merge it.
- 10-11T19:16 X7nXS3py [ARCHIVED] W-RESEARCH-C translation merge/shift guard + Reflect cached block batch-research-c **NOTE**
    PR #1251 merged into baihe-subtitler; manual PC check left W-RESEARCH-C merged as PR #1251; manual 1,000+ line Sonnet run still needed to tune guard thresholds
- 10-11T19:16 8CUp3zeW [ARCHIVED] DB-4: characters, speakers and voice bank into db/speakers.py batch-db-4
    PR #1250 merged; speakers code moved to db/speakers.py DB-4 merged as PR #1250: characters/speakers code moved to db/speakers.py, with the AGENTS.md size-limit rule updated.
- 10-11T19:42 5aeAARSL [ARCHIVED] W-VDB-NAMES personal VTuber names list from vdb (fetch on demand) batch-vdb-names
    PR #1252 ready for review; CI green, no open threads PR #1252 marked ready for review by zkaelxz; CI green, no open review threads or conflicts
- 10-11T20:01 RBjLnUtm [ARCHIVED] W-ASR-BENCH owner-run ASR recipe comparison tool batch-asr-bench **NOTE**
    PR #1255 merged; tools.asr_bench landed, GPU run untested PR #1255 merged with tools.asr_bench; real GPU run still needs the owner's manual check
- 10-11T20:01 Des6yxjg [ARCHIVED] DB-5: comic pages and bubbles out of db/__init__.py batch-db-5
    DB-5 PR #1254 merged; comic pages now in comic_pages.py PR #1254 (DB-5) merged into baihe-subtitler; comic pages and bubbles moved to db/comic_pages.py; 21:01 UTC check-in cancelled
- 10-11T20:09 kaffeCGB [ARCHIVED] W-TIMING-SETTINGS phase 1: voice detection, line splitting, sentence grouping batch-timing-settings
    PR #1259 merged; timing settings phase 1 landed PR #1259 (W-TIMING-SETTINGS phase 1) merged; open items not built are listed in the PR body
- 10-11T20:38 ZgHTNMxN [ARCHIVED] W-GUARD-DEFAULT engine-aware translation reply guard batch-guard-default
    PR #1261 merged to baihe-subtitler; CI green on final head Engine-aware reply guard and translate_reply_guard setting (Auto/On/Off) merged via #1261; real-model manual PC check still pending for the user
- 10-11T20:38 hLVfDxq6 [ARCHIVED] W-SEC-AUDIT-FIXES audit findings M1 and L1-L3 batch-sec-audit-fixes **NOTE**
    PR #1262 merged; 2 policy calls pending on you PR #1262 merged into baihe-subtitler with the security audit fixes; check-in cancelled Decide whether Caddy should allow household members to reach bulk translate, and whether lines.edit should imply lines.read
- 10-11T20:40 TvYZmnzd [ARCHIVED] W-TEST-GAPS UI tests for language packs card and Live title picker batch-test-gaps **NOTE**
    PR #1260 merged into baihe-subtitler with UI tests Merged PR #1260 adds Language packs, Live title picker, and glossary empty-state UI tests to baihe-subtitler.
- 10-11T21:33 YEnWTdcU [ARCHIVED] W-ANIME-WHISPER ja-only anime_whisper transcription backend step-anime-whisper-backend **NOTE**
    PR #1266 merged; real-model alignment bench still unrun PR #1266 merged into baihe-subtitler; PC bench on a Japanese clip still needs someone to run it
- 10-11T21:35 qJvrA5z4 [ARCHIVED] W-FLAKY-TIMING rewrite two wall-clock tests to count events fix-flaky-timing-tests
    PR #1265 merged into baihe-subtitler; flaky tests fixed PR #1265 merged into baihe-subtitler; the two flaky tests are fixed on the base branch
- 10-11T21:50 fHVGxEBa [ARCHIVED] W-SETTINGS-COLLAPSE Settings in six tabs with collapsible cards batch-settings-collapse
    PR #1272 merged to baihe-subtitler; Settings rework landed PR #1272 (Settings rework) merged into baihe-subtitler. Open: screenshots, phone tab wrap, manual PC check.
- 10-11T22:00 wzuSJVM2 [ARCHIVED] B1 DATA-LOSS sources.db backup, compare-and-set on long writers, remove inert gr batch-b1-data-loss **NOTE**
    PR #1267 merged to baihe-subtitler; check-in cancelled PR #1267 merged into baihe-subtitler; reader-page grant kept on purpose; PC check and follow-ups listed in PR description
- 10-11T22:01 jDwGQVCn [ARCHIVED] W-EXPORT-BY-KIND Export tab leads with the title's kind, collapsible batch-export-by-kind **NOTE**
    PR #1268 merged to baihe-subtitler; screenshots not attached PR #1268 merged into baihe-subtitler; screenshots still in scratchpad, manual PC check left to user
- 10-11T22:14 9FJBdBXW [ARCHIVED] W-LIBRARY-TOOLS-REORG Library tools in tabs, Data folder simplified batch-library-tools-reorg
    PR #1271 merged into baihe-subtitler; CI green PR #1271 merged into baihe-subtitler after green CI; screenshots still need attaching by hand
- 10-11T22:14 jQwiuoeY [ARCHIVED] Glossary phase 2a spelling proposals verified against the transcript batch-glossary-phase2a **NOTE**
    PR #1270 merged to baihe-subtitler; Phase 2a complete PR #1270 merged into baihe-subtitler (Phase 2a). Manual PC check left for user; Phase 2b waits on another PR.
- 10-11T22:14 CiCAqkj2 [ARCHIVED] Hardening batch A slow-drip deadline, cancelled job under All, yt-dlp docstring batch-hardening-a
    PR #1269 merged into baihe-subtitler; check-in cancelled PR #1269 merged into baihe-subtitler; manual PC check of the Cancelled filter remains for the user
- 10-11T22:24 bpx5wM45 [ARCHIVED] K1 local-coder kit: lean tests, doc fixes, check-my-change script batch-local-coder-kit
    PR #1273 merged into baihe-subtitler; CI green, no review comments PR #1273 merged into baihe-subtitler after CI passed on f148aaf
- 10-11T22:24 MkrVMDhX [ARCHIVED] K2 Ollama call hardening: truncation, output limits, stop on repeated failure batch-k2-ollama-hardening
    PR #1274 Ollama hardening merged into baihe-subtitler PR #1274 (Ollama hardening) merged into baihe-subtitler; manual Ollama check listed in PR description
- 10-11T23:08 D2ac7xBX [ARCHIVED] B1b edit-collision gaps in bulk apply and CLI, flush before restore swap batch-b1b-data-safety **NOTE**
    PR #1275 merged; bulk-submit copy follow-up not queued PR #1275 merged; nothing running. Follow-up to guard _copy_english_lines against overwriting edits is listed as found-not-fixed in the PR description.
- 10-11T23:29 ejyjLpK6 [ARCHIVED] Live: reap the folder of a session cancelled while queued batch-live-reap-cancelled
    PR #1276 merged; queued-cancel folder leak fixed PR #1276 merged into baihe-subtitler; sessions cancelled while queued now have their folders reaped on the next poll
- 10-11T23:32 dadgPiay [ARCHIVED] W-LIVE-FOLLOW follow YouTube channels for live streams (notify, optional auto-st batch-live-follow **NOTE**
    PR #1278 YouTube live follows merged; CI green PR #1278 (follow YouTube channels for live streams) merged into baihe-subtitler at 39ab7e2; four manual PC checks remain for the user.
- 10-11T23:44 vXkkN3gb [ARCHIVED] Docs sweep: make FILE_ORGANIZATION.md, STATUS.md and other docs true again docs-sweep-oct11 **NOTE**
    PR #1277 docs refresh merged into baihe-subtitler, CI green PR #1277 (docs-only Baihe refresh) merged into baihe-subtitler with CI green; three follow-ups left open.
- 10-12T00:20 qLmFHmND [ARCHIVED] T0 bundle: money and line-text holes found by the gap sweep batch-t0-reading-speed-removal
    PR #1280 merged; unused zh/ja/ko reading-speed limits removed PR #1280 merged into baihe-subtitler, removing the unused zh/ja/ko reading-speed limits; nothing further pending.
- 10-12T00:20 DDz1awit [ARCHIVED] P-ACCESS household members cannot trigger model downloads batch-access-model-downloads
- 10-12T00:51 2amT5QXK [ARCHIVED] All-titles name check report batch-glossary-name-report
    PR #1282 merged to baihe-subtitler; name report landed PR #1282 (Check names across all titles) merged into baihe-subtitler with review fixes; ja/zh paths still need real-title testing on PC.
- 10-12T00:58 eiHubsjR [ARCHIVED] Manage sources page: tabs and simpler settings batch-sources-ui **NOTE**
    PR #1283 merged to baihe-subtitler; session unsubscribed Sources management UI merged via PR #1283; open items listed under 'Found but not fixed' in the PR body
- 10-12T01:01 KXvsrx3t [ARCHIVED] Money lane A: cloud stop, Live cap, standalone chunk cap batch-money-a **NOTE**
    PR #1284 merged into baihe-subtitler; nothing left to do PR #1284 merged into baihe-subtitler; session unsubscribed from its activity
- 10-12T01:09 cd8kqN3p [ARCHIVED] Media stage sub-tabs: less scrolling batch-media-tabs
- 10-12T01:18 ryUB2nLg [ARCHIVED] Fix anime-whisper download refused, failed jobs shown as Done batch-fix-ct2-allowlist **NOTE**
    PR #1285 merged into baihe-subtitler; CI green on head Merged PR #1285 into baihe-subtitler: anime-whisper allowlist, failed downloads fail the job, zero-line warnings, speaker-detection notices.
- 10-12T01:26 v9FqWZkp [ARCHIVED] Translate stage tidy and batch-size default fix batch-translate-tidy **NOTE**
    PR #1286 merged into baihe-subtitler; no follow-up open PR #1286 merged into baihe-subtitler; session unsubscribed, no follow-up work open
- 10-12T01:30 QkpzwQkQ [ARCHIVED] Review stage sub-tabs and waveform gaps batch-review-tabs **NOTE**
    PR #1293 draft; CI run 38234501007 running, watching e2e
- 10-12T01:31 sjNc33jV [ARCHIVED] Jobs page fixes and People and access rename batch-jobs-admin-ui **NOTE**
    PR #1287 merged to baihe-subtitler; 3 follow-ups in body PR #1287 merged into baihe-subtitler; open follow-ups (bell for other failed outcomes, real-library CI hit, disk-usage flake) listed in PR body
- 10-12T01:31 rGxQ9qiM [ARCHIVED] Assistant tabs and Make subtitles page batch-assistant-make-ui
    PR #1289 merged; Assistant tabs and Make subtitles in PR #1289 merged into baihe-subtitler with the Assistant tabs and Make subtitles page; no further work on it.
- 10-12T01:33 T1zvXwsZ [ARCHIVED] Restore safety B2: accounts, previous-library folder, sidecars batch-restore-b2 **NOTE**
    PR #1291 merged to baihe-subtitler; check-in cancelled PR #1291 merged into baihe-subtitler; three open items left for user decision: account restore, title ownership, second-process restore.
- 10-12T01:54 5PBCJguQ [ARCHIVED] GPU Whisper runtime: find CUDA 12 cuBLAS/cuDNN automatically batch-gpu-whisper-runtime
    PR #1288 merged to baihe-subtitler; Windows GPU fix in Windows GPU Whisper fix merged via PR #1288; not run on real Windows GPU, so owner check is still needed.
- 10-12T02:12 ycGC5h1J [ARCHIVED] Glossary tabs and glossary results that survive leaving the page batch-glossary-tabs-resume
- 10-12T04:37 FQDmj4pF [ARCHIVED] Restore owner matching by username batch-restore-owners
- 10-12T04:39 snqVYzsU [ARCHIVED] Money lane B: failure streaks and usage logging batch-money-b
- 10-12T05:24 Svihi8t5 [ARCHIVED] Local-model readiness: task card, check_change --run, OpenCode setup batch-local-model-ready
    PR #1299 merged into baihe-subtitler; unsubscribed PR #1299 merged into baihe-subtitler with the model card, V100 Ollama/OpenCode notes, and --run tests; open items are listed in the PR body.
- 10-12T07:41 e7PSRkGf [ARCHIVED] Translate reliability: glossary enforce, retry failed lines, summary button batch-translate-reliability
    waiting on CI
- 10-12T08:36 xVyHPakj [ARCHIVED] Soft-sub video export: ASS in MKV option batch-ass-in-mkv **NOTE**
    PR #1304 CI green on ba7eaa7; draft, watching for reviews
- 10-12T08:36 astCt2fe [ARCHIVED] Benchmark Lab: Traditional vs Simplified count batch-lab-traditional-count **NOTE**
    PR #1303 merged; Lab shows Chinese Trad/Simp counts PR #1303 merged into baihe-subtitler; Lab now shows Traditional vs Simplified line and character counts. Two doc issues remain open for a follow-up decision.
- 10-12T08:42 xWjspRHJ [ARCHIVED] PageShell: one page width, header and spacing batch-pageshell
- 10-12T09:01 UggppRZV [ARCHIVED] Jobs truth: cancelled/interrupted/failed read honestly batch-jobs-truth
    pushed merge 33eec97; CI pending, re-checking at 10:26 UTC
- 10-12T09:01 S635PKhp [ARCHIVED] Whisper: large-v3 default, hide small sizes, truthful run line batch-whisper-trio
    I'll pick the PR back up when CI results or reviews arrive.
- 10-12T09:13 rfgT1k4F [ARCHIVED] URL downloads: queue, 3 at once staggered, long-video and range options batch-url-download-queue
    pushed 7fdba85 test fix; waiting on CI for test-shard
- 10-12T09:13 u9z7MLMZ [ARCHIVED] Test-first leaves no temp files; guard and flaky-test fixes batch-testfirst-and-guards
    pushed review fixes 26a2a29; waiting on CI for PR #1306
- 10-12T09:18 X5WLdxnH [ARCHIVED] ASS and VTT keep overlapping cues; ASS text hardening batch-ass-overlap
    pushed 277ba32 to #1308; waiting on CI for new commit
- 10-12T09:42 X1vTdYiJ [ARCHIVED] Translate stage tabs (Translate | Glossary | Characters | Reference novel) batch-translate-tabs **NOTE**
    PR #1318 merged; Translate stage tabs landed PR #1318 merged into baihe-subtitler; Translate stage tabs landed. Listed follow-ups remain open (event-stream/jobs.spec failures, flaky live tests, duplicate openSection, e2e not in tsc -b).
- 10-12T09:42 yL6UC3ZZ [ARCHIVED] Single-title restore keeps owners by email, never by id batch-restore-owners-2 **NOTE**
    PR #1311 merged; check-in cancelled, follow-ups noted in PR PR #1311 merged into baihe-subtitler; title restore now routes by email match or PC owner; two non-blocking follow-ups recorded in the PR
- 10-12T09:49 FKu8uo1S [ARCHIVED] Novel flow + hide settings that don't apply to the media type batch-novel-media-type
- 10-12T10:20 RqNbU5tc [ARCHIVED] P-UI 1: fold default, Library tools empty tabs, Backup order batch-pui-folds **NOTE**
    batch-pui-folds PR CI green; left for lead to merge batch-pui-folds draft PR into baihe-subtitler: CI green, PageShell used, awaiting lead merge
- 10-12T10:20 qiVnn9JN [ARCHIVED] Benchmark Lab tabs batch-bench-tabs **NOTE**
    PR #1314 merged into baihe-subtitler; nothing left to do PR #1314 (Benchmark tabs) merged into baihe-subtitler; open follow-ups: CER/Simplified docs mismatch and two jobs.spec.ts failures
- 10-12T10:20 qXCkiCvV [ARCHIVED] Sources page tabs batch-sources-tabs **NOTE**
    PR #1315 merged; Sources page now has three tabs Sources page split into Find, New chapters and Manage sources tabs; PR #1315 merged into baihe-subtitler. Two known issues left open.
- 10-12T10:21 soEk6Coy [ARCHIVED] Orphan child processes: die with the server, sweep on start batch-orphan-procs
    #1313 CI green, no threads; awaiting lead merge; check 15:25Z
- 10-12T10:24 i9ciVsg6 [ARCHIVED] Review: engine + model picker for line AI actions batch-line-ai-engine **NOTE**
    PR #1317 merged into baihe-subtitler; 3 items left unfixed PR #1317 merged into baihe-subtitler; open items: paid flag for all engines, per-call monthly cap, model-format-only check
- 10-12T10:24 m9f5uP4A [ARCHIVED] Review: 'Source text looks off' flag batch-source-suspect-flag
    PR #1316 ready for review, CI green; watching for reviews
- 10-12T13:03 CPvZmYhD [ARCHIVED] Show the real error reason; Retry on failed loads batch-error-text **NOTE**
    PR #1321 merged into baihe-subtitler; follow-ups in body PR #1321 merged into baihe-subtitler; open follow-ups listed under 'Found but not fixed'
- 10-12T13:03 mYWxp8KN [ARCHIVED] Line breaks that respect words (BudouX / jieba) batch-linebreak **NOTE**
    PR #1320 draft, CI green; staying subscribed for reviews
- 10-12T13:47 TcPygJxH [ARCHIVED] Missing package -> readable 503, not a 500 batch-engine-importerror **NOTE**
    draft PR #1322 pushed; CI green on a29b6a0 Draft PR #1322 pushed as a29b6a0 with missing-package handling and tests; CI green; four Found-but items listed in the PR body.
- 10-12T15:40 uAa8bVQ4 [ARCHIVED] Live: glossary by series + Echo hint batch-live-glossary-series **NOTE**
    PR #1324 merged into baihe-subtitler; oxlint warnings open Merged PR #1324 (live glossary series picker) into baihe-subtitler; unrelated oxlint warnings in LibraryTools.tsx and LinesPanel.tsx remain open.
- 10-12T15:40 k3BHezTy [ARCHIVED] Scanlate/OCR: check every backend's dependency, stop swallowing OCR errors batch-scanlate-deps
- 10-12T16:07 1eDQBcfL [ARCHIVED] Benchmark Lab: CER ignores Simplified/Traditional batch-cer-script
    PR #1325 merged; Chinese CER now ignores script Chinese CER in Benchmark Lab now ignores Simplified vs Traditional script; PR #1325 merged into baihe-subtitler.
- 10-12T16:10 9rtzcmRS [ARCHIVED] Series page: titles in a series, rename, delete, copy glossary terms batch-series-manage
- 10-12T16:19 3uh4spc9 [ARCHIVED] UI theme and layout exploration (temporary screenshots) step-ui-9-review-spacing **NOTE**
    PR #1393 merged, CI green; stray remote branch left PR #1393 (phone Review spacing) merged with CI green; stray step-ui-8-review-phone branch remains on remote for manual deletion
- 10-12T16:39 Ft4rpRvR [ARCHIVED] Glossary: Packs tab; Re-translate on the Translate tab too batch-glossary-packs-retranslate
    PR #1327 merged into baihe-subtitler; packs tab shipped PR #1327 merged into baihe-subtitler with the Packs tab and shared Re-translate action; both Packs cards start folded.
- 10-12T16:39 yEoChLBv [ARCHIVED] Uninstall cleanup: leftovers removed and listed batch-uninstall-cleanup
- 10-12T20:39 QroTKqWJ [ARCHIVED] PaddleOCR steps 2-4: pins and plain-opencv swap plan batch-paddle-steps
- 10-12T20:39 KPQB1FNv [ARCHIVED] Diagnostics: GPU memory readout shows all programs batch-gpu-memory **NOTE**
    PR #1331 merged into baihe-subtitler; nothing left to do PR #1331 merged into baihe-subtitler: nvidia-smi GPU memory fix, label, polling, review fixes
- 10-12T21:52 C7W2WvPC [ARCHIVED] Release signing: Ed25519 signature check for app updates batch-release-signing
    PR #1332 CI green, ready for review; recheck ~03:00 UTC
- 10-12T22:16 dCjYg4mi [ARCHIVED] Household jobs: cancel only your own, queue position for everyone batch-job-ownership-queue
- 10-12T22:19 iwWH32Lu [ARCHIVED] Install preview for Engine/Preflight buttons + Turn on GPU banner batch-install-plan-callers **NOTE**
    PR #1334 merged into baihe-subtitler; 3 follow-ups open PR #1334 merged into baihe-subtitler. Open: Upgrade skips preview, two installDependency callers, Windows/GPU untested.
- 10-12T22:19 1HoGrdM6 [ARCHIVED] Stream/release date, series sort, Library group by series batch-release-date **NOTE**
    PR #1335 merged into baihe-subtitler; release-date work done PR #1335 merged into baihe-subtitler: Details field, Library and series sorting, review fixes; Fill-from-link left for a later PR
- 10-12T22:25 defeuQ15 [ARCHIVED] Pre-443 hardening: per-user job caps, saved comics owner, Caddy template batch-pre443-hardening
- 10-12T22:51 GaY2BiUm [ARCHIVED] Install readiness: disabled-with-reason controls, Recommended installs, GPU PyTo batch-b11-readiness
    PR #1341 merged into baihe-subtitler; readiness checks landed PR #1341 merged into baihe-subtitler: readiness route, disabled Whisper controls, install card
- 10-12T23:49 gZYusevn [ARCHIVED] Split bulk_translate.py (86 KB) by domain batch-split-bulk-translate **NOTE**
    bulk_translate.py split merged as PR #1343 bulk_translate.py split merged into baihe-subtitler via PR #1343; stale OVERSIZED_MODULE_BYTES entry left for a follow-up
- 10-12T23:49 wEZn6Hg1 [ARCHIVED] Split cli.py (65 KB) by command group batch-split-cli
- 10-12T23:49 U43WrGHw [ARCHIVED] Split diagnostics.py (56 KB) by domain batch-split-diagnostics
    PR #1342 merged into baihe-subtitler; diagnostics split PR #1342 (batch-split-diagnostics) merged into baihe-subtitler; diagnostics.py split out to dependency_updates
- 10-13T00:23 S5PrmDTN [ARCHIVED] Docs cleanup: remove stale documents and files batch-docs-cleanup
- 10-13T00:31 GpZxERTQ [ARCHIVED] Remove the CLI (cli*.py, tests, docs, parity rule) batch-cli-remove **NOTE**
    PR #1345 merged; CLI removal landed in baihe-subtitler PR #1345 merged into baihe-subtitler, removing the CLI; follow-ups left open
- 10-13T02:30 jsvXxM19 [ARCHIVED] Difficult audio: opt-in recipe for screaming, whispering, many speakers batch-difficult-audio
    PR #1351 merged into baihe-subtitler; session unsubscribed PR #1351 (Difficult audio) merged into baihe-subtitler; open items listed in PR body
- 10-13T02:30 AaBMdzHj [ARCHIVED] Split services/auto_backup_service.py (59 KB) batch-split-auto-backup
    PR #1348 merged into baihe-subtitler; task complete auto_backup_service split; PR #1348 merged into baihe-subtitler by lead session
- 10-13T02:30 oqkqgmrD [ARCHIVED] Split services/maintenance_assistant_service.py (61 KB) batch-split-maintenance-assistant
    PR #1347 merged into baihe-subtitler; split complete PR #1347 merged into baihe-subtitler; maintenance_assistant_service split into modules
- 10-13T02:30 JX1GLuQM [ARCHIVED] Split services/disk_usage_service.py (52 KB) batch-split-disk-usage **NOTE**
    PR #1350 merged; disk_usage service split complete Split services/disk_usage_service.py; draft PR #1350 merged into baihe-subtitler. Two follow-ups remain: the stale OVERSIZED_MODULE_BYTES entry and a diskUsageModel.ts comment.
- 10-13T05:39 2cjAomcp [ARCHIVED] Split core.py (54 KB) batch-split-core
- 10-13T05:39 8f15s2oM [ARCHIVED] Split services/transcribe_service.py (66 KB) batch-split-transcribe **NOTE**
    PR #1352 merged; transcribe split done, 3 follow-ups open transcribe_service.py split merged into baihe-subtitler via PR #1352; stale comment and oversized-file entries remain as follow-ups
- 10-13T07:39 HxJC9p1p [ARCHIVED] Split scanlate.py (50 KB) batch-split-scanlate **NOTE**
    PR #1355 merged; scanlate.py split into domain modules PR #1355 merged on batch-split-scanlate: scanlate.py split into domain modules as a pure move; 765 tests passed, 11 skipped; CI green.
- 10-13T07:39 1KXPcSoZ [ARCHIVED] Split services/restructure_service.py (50 KB) batch-split-restructure **NOTE**
    PR #1354 merged; restructure_service split landed restructure_service.py split into service modules on baihe-subtitler via PR #1354; two stale-cleanup items left open
- 10-13T07:39 NpdFQ4Vn [ARCHIVED] Split services/workspace_job_service.py (48 KB) batch-split-workspace-job **NOTE**
    PR #1357 merged into baihe-subtitler; follow-ups noted in body Split workspace_job_service.py as a pure move; PR #1357 merged into baihe-subtitler. Four found-but-unfixed items are in the PR body.
- 10-13T07:39 kKTBYLuZ [ARCHIVED] Split sources/ai_extract.py (51 KB) batch-split-ai-extract **NOTE**
    PR #1356 merged; ai_extract.py split complete ai_extract.py split into four modules via merged PR #1356; two stale size-allowlist follow-ups remain open.
- 10-13T07:39 JdsmqR3i [ARCHIVED] Split sources/http.py (48 KB) batch-split-sources-http
    PR #1358 merged; sources/http.py split into modules PR #1358 merged into baihe-subtitler; sources/http.py split into modules under 40 KB as a pure move
- 10-13T08:44 ovjmDYcg [ARCHIVED] Split services/translate_run_service.py (44 KB) batch-split-translate-run **NOTE**
    PR #1359 merged; translate_run_service split complete translate_run_service.py split into cohesive modules as a pure move; PR #1359 merged. Follow-ups in PR body: bulk-translate section split, stale OVERSIZED_MODULE_BYTES entry.
- 10-13T08:44 kfyQTDnq [ARCHIVED] Split services/diagnostics_gaps_service.py (44 KB) batch-split-diagnostics-gaps **NOTE**
    PR #1361 merged; diagnostics_gaps_service.py split complete PR #1361 merged: diagnostics_gaps_service.py split into modules under 40 KB; stale OVERSIZED_MODULE_BYTES entry left for a follow-up ratchet PR
- 10-13T08:44 rf2z7xou [ARCHIVED] Split page_fetch.py (42 KB) batch-split-page-fetch **NOTE**
    page_fetch.py split merged via PR #1360; nothing left page_fetch.py split into cohesive modules merged in PR #1360; stale OVERSIZED_MODULE_BYTES entry left for the ratchet PR
- 10-13T08:44 UKFLXTbK [ARCHIVED] Split services/library_admin_service.py (55 KB) batch-split-library-admin **NOTE**
    PR #1362 merged; library_admin_service split complete Split services/library_admin_service.py into modules via PR #1362, now merged. Three leftovers remain open and are listed in the PR body.
- 10-13T08:44 WPUxM4zh [ARCHIVED] Split background_jobs.py (89 KB) batch-split-background-jobs
- 10-13T09:45 85mZsTZy [ARCHIVED] DB split: next domain out of db/__init__.py (serial series) step-db19-migrations
    pending check
- 10-13T13:41 AuNDZ9qV [ARCHIVED] Small UI: shell accessibility, tap targets, 360px brand batch-a11y-shell
- 10-13T13:41 4xSc9FFF [ARCHIVED] Upgrade route: server-side plan, write probe, restore on failure batch-upgrade-plan
- 10-13T14:13 UPoXkXsY [ARCHIVED] Real-app smoke test: real server, tiny CPU Whisper, fake engine
- 10-13T14:18 28sTNoee [ARCHIVED] Fill from link: probe without download, New title from link, check old titles batch-link-probe
- 10-13T14:19 moWfA8ck [ARCHIVED] Keep results and forms when leaving a page (useStoredRun) batch-stored-run **NOTE**
    PR #1378 merged; three follow-up items left open PR #1378 merged at 2d6e8d1 with all checks green; items 11 and re-transcribe e2e test remain open for the lead
- 10-13T14:19 DLtr2Jcb [ARCHIVED] Jobs polish: failed shows Failed, cancel hints, real stages and step log batch-jobs-polish **NOTE**
    PR #1376 draft; CI queued, subscribed for result
- 10-13T14:57 GSWPmjdB [ARCHIVED] UI polish 2: segmented controls, tab bars, card padding, table cut-offs batch-ui-polish-2 **NOTE**
    PR #1387 merged by zkaelxz; CI green on e54a2a4 PR #1387 merged into baihe-subtitler with CI green on e54a2a4; Review empty-state alignment and screenshot attachment left open.
- 10-13T16:25 QxkE4eK7 [ARCHIVED] Design exploration: make Baihe Studio look less AI-generated
    frost plan ready: all 11 kept, lead-session kickoff prompt given Frost plan covers all eleven changes, sequenced after #1374, #1377, #1378, #1382 and UI polish 2, starting at S1; two open questions have defaults.
- 10-13T17:56 gz7EUr1j [ARCHIVED] Fix race: slow-drip send deadline raises timeout, not connection reset fix-notify-deadline-race
    PR #1390 merged into baihe-subtitler; deadline race fixed PR #1390 merged into baihe-subtitler: _pinned_post raises the send-deadline TimeoutError after a watchdog-fired reset; CI green.
- 10-13T17:59 bUKSYive [ARCHIVED] Transcribe advisor: asr_advice notes, setup select, stage times batch-transcribe-advisor
    PR #1391 merged into baihe-subtitler; CI green on b08de89 PR #1391 merged into baihe-subtitler with CI green; known gaps listed in the PR
- 10-13T18:09 xPtBuGZT [ARCHIVED] Data-loss fixes B1: compare-and-set writers, sources.db in backups batch-b1-dataloss **NOTE**
    PR #1392 merged into baihe-subtitler; batch B1 complete Batch B1 merged via PR #1392; open items (#1088 #5/#6, N2/N3, #1116 #12, source_suspect_service) left for follow-up
- 10-13T18:27 kuZCBevk [ARCHIVED] Translation engines B2: max_tokens truncation keeps complete pairs, engine lows batch-b2-engines
    - Should bulk jobs get a new status for resubmitting or claiming? - Should bulk jobs get a new status for resubmitting or claiming?
- 10-13T18:39 XY1hD5t6 [ARCHIVED] Root-cause race: live pipeline stop leaves first chunk skipped fix-live-stop-race
    PR #1395 merged; Stop now drains already-queued chunks PR #1395 merged into baihe-subtitler; Stop translates already-queued chunks. Unfixed: no cap after Stop; fix-queue entry not found under docs/.
- 10-13T19:31 kVvktuNT [ARCHIVED] Scanlate B7: Redo no longer blanks pages, scanlate/extension lows batch-b7-scanlate
    PR #1398 merged; Scanlate Redo fixes from #1119 shipped PR #1398 merged to baihe-subtitler: Scanlate Redo no longer replaces good pages with incomplete results; H1, M2, M6, L7, L8, L11 shipped
- 10-13T19:31 zjfSgKUB [ARCHIVED] Frost S1: panel radius 14px and flat primary button batch-frost-s1
    PR #1397 Frost S1 merged; S2–S7 untouched Frost tuning S1 merged as PR #1397 into baihe-subtitler; S2–S7 not started
- 10-13T20:07 pUk7UTou [ARCHIVED] Frost S2: quieter wash and fewer boxes batch-frost-s2
    PR #1400 (Frost S2) merged into baihe-subtitler PR #1400 Frost S2 merged into baihe-subtitler; S3–S7 and Jobs dot not started
- 10-13T20:35 hMzFBbRF [ARCHIVED] B3 ASR/core fixes: hard-sub crop, audio slicing, subtitle import lows batch-b3-asr-core **NOTE**
    PR #1401 merged with CI green; follow-ups listed in PR body PR #1401 merged on green CI; four unfixed follow-ups (#1094-18, -9, -24, -3) noted in PR body for owner
- 10-13T20:35 L3jCGhAg [ARCHIVED] B4 install/diagnostics/jobs small fixes batch-b4-install-small **NOTE**
    PR #1402 merged; all checks green on fe3ea4a PR #1402 merged on head fe3ea4a45a8076bb2907358442807e46b9ba71e4 after all checks passed; N2 remains open for the owner
- 10-13T20:39 ZEsrKMrZ [ARCHIVED] Frost S3: flat covers and mono numbers batch-frost-s3
    PR #1404 Frost S3 flat covers merged into baihe-subtitler Frost S3 (flat hue covers, mono numbers) merged as PR #1404 into baihe-subtitler; S4-S7 untouched
- 10-13T21:28 H1oc17AR [ARCHIVED] B11-d Scanlate/OCR readiness: honest OCR errors, backend checks batch-b11d-scanlate-ocr **NOTE**
    PR #1405 merged into baihe-subtitler; nothing left to do PR #1405 merged into baihe-subtitler; session auto-unsubscribed
- 10-13T21:28 GhfuzhVE [ARCHIVED] B11-b/c readiness: missing packages give readable errors, not 500s batch-b11bc-engines-export
- 10-13T21:28 UhaGSTyk [ARCHIVED] B1b data-integrity leftovers (Opus): #1088 #5/#6, #1116 #12, re-segment N2/N3 batch-b1b-integrity **NOTE**
    PR #1406 merged to baihe-subtitler; 3 questions left for owner PR #1406 merged into baihe-subtitler; three open owner questions listed in the PR body
- 10-13T21:39 44JexAVW [ARCHIVED] B5 server/service fixes: atomic export, fix-flagged locale/style, #1116 lows batch-b5-services **NOTE**
    PR #1407 merged; 4 items left open for owner decision PR #1407 merged to baihe-subtitler; open items M9, dub-vs-export, EPUB path, and audio_filename listed in PR body
- 10-13T22:00 gcAMKv4d [ARCHIVED] Community files: AGPL-3.0 license, SECURITY, CONTRIBUTING, CODE_OF_CONDUCT docs-community-files
    PR #1409 merged: COPYRIGHT, SECURITY, CONTRIBUTING, CoC added Merged PR #1409 adding COPYRIGHT.md, SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md; no LICENSE file, dependency licences unaudited.
- 10-13T22:41 LAwRKFJ4 [RUNNING] #1378 follow-ups: research cache writes, session-expiry clearing, run proposals batch-stored-run-followups
- 10-13T22:43 znp6N2Aw [ARCHIVED] Ratchet cleanup: stale size entries, unused imports left by the DB split batch-ratchet-cleanup **NOTE**
    PR #1411 merged into baihe-subtitler; batch cleanup finished PR #1411 merged into baihe-subtitler; open leftovers: stale comments in db/migrations.py, db/__init__.py headers, unused imports
- 10-13T22:43 eMMHfMQh [ARCHIVED] Docs second pass: STATUS refresh, deferred docs, stale references batch-docs-second-pass
- 10-13T23:01 cB7cEQgw [ARCHIVED] CLI-removal leftovers: dead video_export wrappers, GPU slot CLI params, stale CL batch-cli-leftovers
- 10-13T23:01 Nn88YCGi [IDLE] B15: one shared plain-file check for stored filenames (audio_filename etc.) batch-b15-plainfile
    PR #1415 CI green; subscribed for review activity
