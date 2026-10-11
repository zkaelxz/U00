# Audit findings review, 2026-10-10

This is a plain-language account of every audit run on Baihe on 2026-10-10. It says what each audit looked at, what it found, what was done about it, what is still open, and what the auditors were unsure of.

It reflects the state of GitHub and the lead session's tracking notes at about 18:45 UTC on 2026-10-10. PRs merge quickly, so some "open" items may have merged since.

**Sources read:**
- The 18 "Hostile review" issues, the senior review (#1113) and their comments. No issue has any comments yet.
- The bodies of PRs #1089, #1111, #1117, #1120 and #1122, plus the main fix PRs.
- The lead's notes: `hostile-review-2026-10-10.md`, `fix-queue-from-reviews.md`, `full-review-campaign.md`, `review-plan.md` and `owner-test-checklist.md`.

**Words used below:**
- **HIGH**: will hurt a real user or corrupt data.
- **MEDIUM**: wrong under a plausible condition.
- **LOW**: a smell, a slowdown or a wrong comment.
- **Merged**: the fix is in the app.
- **Open PR**: a fix is written and waiting for CI or review.
- **Wait-listed**: a fix is needed, but another open PR owns the file, so the fix waits for that PR to merge.
- **Not started**: nobody owns it yet.

---

## 1. One-page summary

### What was audited

| Audit | What it covered | Where the findings are |
|---|---|---|
| Hostile review of the day's own changes | 24 merges from that day (466 files), plus four wider follow-up passes (A to D) | Lead's notes only, not an issue |
| Whole-codebase hostile review | 18 areas. One Opus session per area, each reading every line with five "lenses" | Issues #1086 to #1124 |
| Senior review | Architecture, security and maintainability | #1113 |
| Design audit of the day's UI changes | Visual hierarchy, density and edge cases | PR #1120 (merged) |
| UX review fixes | Findings from follow-up pass A of the day's review | PR #1089 (merged) |
| JavaScript modernisation | Frontend duplicate code and the extension | PRs #1117 (merged) and #1111 (open) |
| Docs-versus-code fixes | The fixable half of #1105 | PR #1122 (merged) |
| Opus reviews of the fix PRs | Before merge, and after merge for three older PRs | Lead's notes (section 3 below) |

### How many findings, by severity

How I counted:
- I counted every rated finding in each issue body.
- For the 12 issues the lead triaged, my counts match the fix queue exactly: 188 HIGH+MEDIUM and 97 LOW.
- For the day's own diff review, I counted each numbered item in `hostile-review-2026-10-10.md`, including its follow-up passes A to D.
- The senior review (#1113), the design audit (#1120) and the Opus reviews of fix PRs don't use the same scale. They are left out of these totals and described in their own sections.

| Source | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|
| 12 hostile-review issues triaged into the fix queue (#1086 to #1106) | 55 | 133 | 97 | 285 |
| 6 hostile-review issues filed later and **not yet triaged** (#1116, #1118, #1119, #1121, #1123, #1124) | 13 | 48 | 55 | 116 |
| Hostile review of the day's own changes (lead's notes, passes A to D) | 8 | 28 | 32 | 68 |
| **All** | **76** | **209** | **184** | **469** |

**One disagreement between sources.** The docs-drift issue #1105 says "11 HIGH, 17 MEDIUM, 12 LOW" in its summary. Its own list, though, has 11 HIGH, 21 MEDIUM and 8 LOW. The fix queue used the list, and so do I.

### Where the HIGH and MEDIUM findings stand

The fix queue tracked HIGH and MEDIUM findings together, so this table does too. LOW findings were mostly not routed one by one (see below).

| Status | 12 triaged issues | 6 later issues | Day's diff review | All |
|---|---|---|---|---|
| Fixed and merged | 45 | 0 | 17 | **62** |
| In an open PR | 36 | 0 | 18 | **54** |
| Wait-listed behind another PR | 93 | 0 | 0 | **93** |
| Nobody owns it | 13 (backlog) | 61 (not triaged) | 0 | **74** |
| Duplicate, or kept by decision | 1 | 0 | 1 | 2 |
| **Total** | 188 | 61 | 36 | **285** |

Notes on this table:
- **"Merged" sometimes means only part of a finding.** For example, #1086 H1 and #1096 H1 are only half fixed. The other half is wait-listed.
- **Open PRs.** These are the 33 brief items in open PRs (B1, B2, B3, B4, F3, T1), plus #1086 M5 in #1099 and #1106 M4 and M7 in #1135. Opus judged four of those open PRs "not safe yet" (section 3).
- **About 9 wait-listed items could start now.** The PR they waited for has merged, and the tracking notes show no session launched for them:
  - #1106 H1, the regex freeze, which waited for #937;
  - #1096 H4, H5, M12, M13 and M14, which waited for #1110;
  - #1103 H3, M4 and M6, which waited for #1089.
- **The 61 HIGH and MEDIUM findings from the six later issues have no owner.** The fix queue was built at 16:22, before those issues landed, and the notes show no triage of them since. At least two of them repeat triaged findings: #1116 H2 is #1088 M12, and #1123 H2 is #1086 M1.
- **LOW findings.** The 97 LOWs from the triaged issues are listed in an appendix to the fix queue, with advice on which are worth a sweep. A few were folded into the briefs. Nearly every LOW from the day's diff review sits in a merged or open PR. The 55 LOWs in the later issues are untriaged.

### The five most serious things found

1. **Restoring a backup can silently delete or misattribute data** (#1088 H1 to H4, all reproduced; #1124 M2, M3).
   - Restoring a database-only backup deletes the separate `sources.db`, which holds tracked series and source settings.
   - Restore resets the database's ID counters. A new household user can then be given a deleted user's private titles.
   - A crafted backup can name any file as a voice clip. "Apply" then copies `.env` into a title folder, and "Delete" removes any file Baihe can write (#1088 M9).
   - A restored backup also brings its own automatic-backup folder and schedule. From then on, the whole library is copied to a folder the file's author chose (#1124 M3).
   - **Status:** the #1088 items are wait-listed behind #1068 and #1073. The #1124 items are not started.

2. **Long jobs and the command-line tool (CLI) overwrite what people type, and can pay twice** (#1116 H1, H2; #1088 M11, M12; #1123 H1).
   - "Translate all lines" writes the model's English over English the user typed during the run. The safe compare-and-set write that would prevent this already exists in the code but isn't used.
   - "Fix flagged lines" does the same to the Chinese text, the English and the flags.
   - The CLI and the app can't see each other's translation runs. Both pay for the same lines, and the last writer wins.
   - **Status:** #1088 M12 is wait-listed. The rest is not started.

3. **One request or one hostile web page can freeze or crash the app.**
   - A household member with read-only access to lines can send one find-and-replace regular expression that locks up the whole server (#1106 H1, reproduced). This is "ReDoS": a regex built to take exponential time.
   - Pages from comic and novel sites can pin a worker thread for hours, or exhaust memory, through slow regexes and unbounded decoders (#1097 H3 to H7, reproduced).
   - A server that sends one byte at a time holds a thread forever (#1097 H2, reproduced).
   - The browser-extension bridge decodes any image the extension sends, whatever type it claims and however many pixels it has (#1119 H1). A 12 MB file can become about 3 GB in memory.
   - **Status:**
     - The regex fix is unblocked but not started.
     - The parser and slow-read fixes are in open PRs #1134 and #1132. Opus found new problems in both.
     - The bridge image check is not started.

4. **Baihe can be steered to private addresses, or trusts local connections too widely.**
   - yt-dlp, the video downloader, follows links with no address check. A hostile page can make Baihe fetch from the PC itself (including its PC-only pages), the home network or a cloud metadata address, and save the reply as "media" a remote user can play (#1097 H1). This class of attack is called SSRF.
   - With sign-in off, anything that relays a TCP connection to 127.0.0.1:8600 gets full owner rights. That includes an ssh or VS Code tunnel, a port proxy, or a second Windows account on a shared PC (#1118 M1).
   - Per-device extension tokens can be made and revoked in the UI, but no route checks them. Revoking a device cuts off nothing (#1113).
   - **Status:** the yt-dlp fix is wait-listed behind #1080 and #1082. The other two are not started.

5. **"Install now" and "Upgrade" can leave Python half-replaced, so Baihe won't start** (#1098 H1 to H4).
   - Both skip the safety plan. On Windows, they can replace a package the server has loaded halfway through, with no restore.
   - Under the Windows service account, no install can ever succeed.
   - The Diagnostics page loads PyTorch in the server, which locks the files the GPU PyTorch install has to replace.
   - **Status:**
     - The client-side fallback is fixed (#1091, merged).
     - The server-side "Install now" fix is in #1131 (open). Opus judged it not safe: the UI never sends the risk acceptance it now needs.
     - The torch fix in #1126 (open) doesn't yet meet its goal.
     - "Upgrade" waits for #891.

**Runner-up: money.**
- The pre-run cost estimate is low by more than ten times. It is the only gate before a bulk batch that can't be stopped (#1096 H3).
- An interrupted bulk apply bills and submits the next paid stage twice (#1096 H4).
- An off-peak DeepSeek run can't be cancelled (#1096 H5).
- All three are wait-listed or unblocked but not started.

---

## 2. Audit by audit

Each area lists the most important findings with a status: **fixed (PR)**, **in open PR (PR)**, **wait-listed (behind PR)** or **not started**.

### 2.1 Hostile review of the day's own changes (lead's notes)

**What it examined:**
- Every new module from the day's 24 merges, read in full.
- Four wider passes afterwards:
  - A: frontend and extension
  - B: sources, engines and lib
  - C: services, API and jobs
  - D: root modules

**Most important findings:**
- **The job store waited up to 5 to 10 seconds while holding the global jobs lock** (1.1). A locked `library.db` froze every job status read. **Fixed (#1078).**
- **`lib.http` treated a redirect as success** (2.1). It also had case-sensitive headers, and it dropped the body when following a 307. **Fixed (#1075).**
- **A crash during a queued pip install blocked every start for up to an hour** (5.1, D-H1). After a crash, nothing restored the old packages (5.2).
  - Fixed for the pending installer (#1077) and the launcher (#1092).
  - Follow-ups are **in open PR #1083**.
- **The extension mapped every canvas to page 1's image** (A1). Pages 2 to N then downloaded page 1 and were dropped as duplicates. **In open PR (#1087).**
- **Diagnostics installs** (A2, A3):
  - Any failed install preview fell back to installing anyway.
  - The "I understand" tick carried over to the next package.
  - **Fixed (#1091).**
- **The page bridge could bill the same page twice,** or throw away a stored page on a late failure (D-M1). **In open PR (#1100).**

**Status of the 36 HIGH and MEDIUM:** 17 merged, 18 in open PRs, 1 kept by decision (6.1, already documented).

**Unsure:**
- The frontend pass couldn't run vitest or tsc (no `node_modules` in its checkout).
- The extension fixes need a real browser (#1087 lists 7 owner checks).

### 2.2 Background jobs and process workers (#1086)

**What it examined:** `background_jobs.py`, the job store, process workers, force stop, shutdown and Live Whisper.

**Most important findings:**
- **H1. An old run of a job can write its result into a new run with the same id.**
  - Two tabs starting the same source search can mark a running search done with the other's result.
  - A Jobs-page delete racing a new start can kill the fresh transcription and leave no record.
  - Search half **fixed (#1125)**. Delete half **wait-listed (#1085, #1073)**.
- **H2. The GPU slot decision does database calls and a live nvidia-smi call (up to 5 s) while holding the global jobs lock.** One CLI write can stall every job read. **Wait-listed (#1073).** `background_jobs.py` is frozen until then.
- **M1. The GPU slot holder name doesn't say which server holds it.** Two servers on one library (or the CLI, #1123 H2) both think they own the slot, and both load Whisper. **Partly in open PR #1073**, the rest wait-listed.
- **M3. The heartbeat thread dies on its first exception.** After that, a long job's GPU slot goes stale and another process takes the card. **Wait-listed (#1073).**
- **M5. A cancel flag from the previous run carries into the next one.** The new run is cancelled within 2 seconds. **In open PR (#1099).**

**Unsure:**
- Nothing was run. Every scenario was traced by hand.
- Workers' own bodies were only spot-checked.
- Windows process handling is only exercised in mocked form on Linux.

### 2.3 Database, storage, migrations and restore (#1088)

**What it examined:**
- All of `db.py`, plus restore staging.
- Two sub-reviews: backup and restore, and `sources/store.py` with the storage code.

**Most important findings (H1 to H4 reproduced with throwaway scripts):**
- **H1. `sources.db` is zipped while live, and its write-ahead log (WAL) is thrown away on restore.** Recent changes vanish. **Wait-listed (#1068, #1073).**
- **H2. Restoring a backup without `sources.db` deletes the live one.** That is the default auto-backup. **Wait-listed.**
- **H3. A restored `sources.db` lacks its later columns.** "Save as CBZ" errors until restart. **Wait-listed.**
- **H4. Restore resets ID counters.** A new user can inherit a deleted user's private titles. **Wait-listed.**
- **H5. Schema upgrades that add several columns at once are not atomic.** A locked database can leave six columns missing forever. **Wait-listed (#1073, #1099).**
- **M9. File names in a restored backup are trusted as paths.** A crafted backup can copy `.env` out, or delete a file. **Wait-listed (security).**

**Unsure:**
- Windows file locking was reasoned about, not run.
- File columns other than `voice_bank.clip_filename` were not each traced.

### 2.4 Core, segment splitting and speech recognition (#1094)

**What it examined:** `core.py`, `segment_splitting.py`, `whisper_models.py`, `asr_backend.py`, subtitle formats, hardsub OCR, OCR and the dictionary.

**Most important findings:**
- **H1. Each audio slice makes ffmpeg decode from the start of the file.** On a 6-hour file the work grows with the square of the length, and late slices can time out. **Wait-listed (#1099).**
- **H2. Qwen3-ASR loads the whole file into memory** (about 1.4 GB for 6 hours). **Wait-listed (#891).**
- **H3. Hardsub OCR writes every sampled frame as a full-size PNG.** That is about 40 to 60 GB for a 6-hour 1080p video. **Wait-listed (#1099).**
- **H4. A blank line inside English text makes bilingual SRT export lose the Chinese line on re-import** (verified). **Wait-listed (#1099).**
- **H5. ffmpeg errors put full file paths into job error messages.** **Wait-listed (#1099).**
- **H6. Qwen3-ASR stops at 256 tokens.** Long lines lose their tail text silently. **Wait-listed (#891).**
- Also: a model shows as "cached" when only a similar model is (M14), and the Hugging Face token is exported to every child process (M16). **In open PR #1126.**

**Unsure:**
- No real models, GPU, Groq, PaddleOCR or long ffmpeg runs were used.
- The time and size numbers for H1 to H3 are estimates.
- Other callers (the CLI, re-transcribe and forced align) were not reviewed.

### 2.5 Translation engines and bulk translate (#1096)

**What it examined:** all of `bulk_translate.py` and `engine_backends/`, glossary I/O, standalone translate, and the translate services.

**Most important findings:**
- **H1. Enforced glossary terms corrupt English, and get worse on every run.** "Berlin" became "BerLin Wan Wan". It runs over every line of the title.
  - **Fixed (#1125).** Opus then found a case-handling bug in the fix, which is **in open PR #1152**.
  - "Only lines this run wrote" is **unblocked (#1110 merged), not started**.
- **H2. A reply cut off by the output limit blanks the whole batch,** after two billed calls. **Wait-listed (#1093, #883).**
- **H3. The cost estimate is low by more than ten times.** It leaves out the instructions re-sent with every batch, and it is the only gate before an unstoppable bulk batch. **Wait-listed (#1068).**
- **H4. An interrupted bulk apply logs usage twice and submits the next paid stage twice.** **Unblocked, not started.**
- **H5. An off-peak DeepSeek run can't be cancelled.** It also skips the finishing steps: glossary, flags, version and parity with the CLI. **Unblocked, not started.**
- **M11. Prompt caching was set on a part that changes every batch.** Each batch paid the cache-write price and never got a cache read. **Fixed (#1125).** Real cache reads still need an owner check.

**Unsure:**
- No provider calls were made. DeepSeek and Gemini reply shapes were taken from code comments.

### 2.6 Sources, fetch ladder and URL guard (#1097)

**What it examined:** the sources fetch ladder, the adapters, `lib/http`, `capped_body` and `url_guard`. Five HIGH findings were confirmed by running code.

**Most important findings:**
- **H1. yt-dlp downloads have no address guard.** A hostile page can make Baihe fetch private or local addresses and save the reply as media (SSRF). **Wait-listed (#1080, #1082).**
- **H2. The "total deadline" is only checked between 64 KB chunks.** A server sending one byte every half second held a thread for 20 s. One that never stops holds it forever.
  - The shared helper half is **in open PR #1132**.
  - The `lib/http` half is **wait-listed (#1082)**.
- **H3 to H6. Regexes and decoders that a hostile page can blow up.**
  - Tag stripping took 11 s at 160 KB.
  - A manhuagui regex took 7 s at 3 KB.
  - LZString turned 18 KB into 40 MB.
  - `Array(n)` allocated whatever size the site named.
  - **In open PR #1134.** Opus judged it not safe yet (section 3).
- **H7. Text extraction is quadratic** (14.6 s on 88 KB), and it holds a lock that blocks every other pasted preview. **In open PR (#1132).**
- **M15. A signed chapter URL is sent as the Referer to third-party image hosts.** **In open PR (#1132).** Opus found it still leaks through redirects.
- **M8. A system proxy turns off address pinning on the sources path.** **Wait-listed (#1070).**

**Unsure:**
- No real site, proxy, browser or yt-dlp extractor was used.
- `page_fetch.py` and the egress proxy need their own review.

### 2.7 Diagnostics, installs, upgrade and installer (#1098)

**What it examined:** diagnostics modules, the install and upgrade services, the pending install, and the installer where it touches installs.

**Most important findings:**
- **H1. "Install now" and "Upgrade" skip the plan:** no in-use check, no OpenCV clash check, no downgrade confirmation.
  - The client fallback is **fixed (#1091)**.
  - The server-side "Install now" check is **in open PR #1131**, which Opus judged not safe.
  - "Upgrade" is **wait-listed (#891)**.
- **H2. An interrupted queued install can't be restored.** The "before" list lived only in memory.
  - The "before" list is now written to disk: **fixed (#1077)**.
  - Restoring from it on the next start is **wait-listed (#1081, #1083)**.
- **H3. Under the boot service, installs can never succeed** (read-only site-packages).
  - The "Install now" half is **in open PR #1131**. Opus says its check doesn't work on Windows.
  - The queued half is **wait-listed (#1083)**.
- **H4. The Diagnostics page imports PyTorch and opens the GPU in the server,** which locks the files GPU setup must replace. **In open PR (#1126).** Opus says the goal is not met yet.
- **H5. The "safe to share" support report keeps signed-URL tokens.** **In open PR (#1126).** Opus found the new redaction can crash the bug-report route.

**Unsure:**
- Nothing was run on Windows, and no installer was built.
- Console-close and service-account behaviour was reasoned from how Windows and pip behave.

### 2.8 Frontend: library, settings, diagnostics, jobs and sign-in (#1101)

**What it examined:** those pages, the session and jobs hooks, and permission checks. tsc and vitest pass. Playwright was not run.

**Most important findings:**
- **H1. "New title" is the Library's only primary button, and every household member gets "Not allowed."** after filling it in. **Fixed (#1129).**
- **H2. Bulk status, list and rename are offered to members who can't use them.** **Fixed (#1129).**
- **H3. An older jobs list can overwrite a newer "job finished" event.** Diagnostics then keeps every install button disabled. **Fixed (#1130).**
- **M6. A failed "who am I" call shows admin menus and hides Sign out.** **Fixed (#1130).** A follow-up remains: `sharing.ts` still says "can see all" in that state.
- **M7. A 401 anywhere threw away unsaved drafts.** **Fixed (#1130).**
- **M4 and M5. Settings saves overwrite each other.** A failed settings load shows the member view to an admin. **Wait-listed (#1071).**

**Unsure:**
- The races follow from the code. They were not reproduced in a browser.

### 2.9 Frontend: workspace stages (#1102)

**What it examined:** every workspace stage. Five sub-reviewers each took a slice. The lead reviewer re-checked the three HIGH findings.

**Most important findings:**
- **H1. A stale glossary instructions box can overwrite a series' instructions with empty text.** **In open PR (#1141).**
- **H2. A double click on "Attach" stores the novel twice.** **In open PR (#1141).**
- **H3. Leaving Review with an unsaved edit loses it if the background save fails.** The code comment claimed this never happens. **In open PR (#1141).** That PR's seven new end-to-end tests all failed on the old code.
- **M4. Run-status polling gives up but keeps showing "running".** **Fixed (#1127).**
- **M5. A double click starts Translate or Transcribe twice.** **Wait-listed (#883, #891).**
- **M8. The cost estimate still says "free" after switching to a paid engine.** **Wait-listed (#883).**

**Unsure:**
- Only the HIGH findings were re-checked by the lead reviewer.
- 360px layouts were judged from CSS.

### 2.10 Frontend: reader, comic, live, benchmark and assistant (#1103)

**What it examined:** those pages. No HTML-injection path was found.

**Most important findings:**
- **H1. A Benchmark cost estimate stays valid after the number of cases changes.** A run can cost about 100 times the estimate. **Wait-listed (#910).**
- **H2. An assistant question couldn't be cancelled.** It locked the chat for up to 90 minutes. **Fixed (#1128).** Cancel only stops the browser waiting; the server may still finish the request.
- **H3. Vertical comic reading keeps every page decoded.** A long chapter can crash a phone tab. **Unblocked (#1089 merged), not started.**
- **M4. A Reader page that fails to load shows the previous page's text** under the new page number. **Unblocked, not started.**
- **M7. Live polled forever on a 403 or 500 with no message.** **Fixed (#1128).**
- **M11. Peeking at another chapter overwrote "Continue".** **Fixed (#1128).**

**Unsure:**
- The memory claim in H3 was not measured.
- Benchmark and Assistant came from a sub-reviewer.

### 2.11 Frontend test coverage (#1104)

**What it examined:** vitest and Playwright coverage per page, and CI timings.

**Most important findings:**
- **H1. Vitest never runs `.test.tsx` files, so 13 tests never run.** **In open PR (#1139).**
- **H2. The fallback-engine test is skipped on every run.** **In open PR (#1139).**
- **H3. No test runs a workspace page as a household member with limited permissions.** **In open PR (#1139).**
- **H4. Hand-written test stubs can drift from the real API shapes.** **Not started** (backlog).
- **M5. Tests change the shared test library,** and pass only because files run in alphabetical order. **In open PR (#1139).**
- **M9 and M10. Shards are unbalanced (10 minutes against 7), and a skipped test never fails CI.** **Not started** (backlog).

**Unsure:**
- Flaky-test claims come from reading the code and one green CI run, not from a reproduced failure.

### 2.12 Docs versus code (#1105) and the fix PR (#1122)

**What it examined:** CLAUDE.md, AGENTS.md, FILE_ORGANIZATION.md, STATUS.md and every doc under `docs/`.

**Most important findings:**
- **H1 to H3. STATUS.md, background-jobs.md and CLAUDE.md say the new job store (#1058) is unmerged,** or that the in-memory dict decides. Both are wrong. **Wait-listed:** those files are owned by open PRs.
- **H4 and H5. content-sources.md says a site's terms block imports.** That check has done nothing since 2026-09-27. **Fixed (#1122).**
- **H7 to H10. Four wrong menu paths** for Remote access, Users and Settings tabs. **Fixed (#1122).**
- **H11. The docs tell users to run `cli.py timing-check`, which doesn't exist.** **Wait-listed (#891).**

**What #1122 fixed:** 17 HIGH and MEDIUM findings plus 4 LOW, docs only.

**What #1122 left:**
- It deleted brief B6 (Jellyfin headroom) from the backlog. Restoring it is an owner call.
- Frontend code comments still say "Settings > Remote access".
- Every doc owned by an open PR is untouched.

**Also found by the modernisation PR #1117:** the `npx tsc --noEmit` command in CLAUDE.md checks nothing at the frontend root. `npx tsc -b` is the command that works.

### 2.13 Remaining routers and schemas (#1106)

**What it examined:** every router outside the core set, plus all schemas.

**Most important findings:**
- **H1. A household member with only `lines.read` can freeze the whole server with one find-and-replace regex** (ReDoS, reproduced). The guard misses nested groups. **Unblocked (#937 merged), not started.** The lead's notes called this the highest-priority wait.
- **M2. `POST /api/translate` ignores the monthly spend cap and records no usage.** **Wait-listed (#1068).**
- **M3. Learning a series style reads other members' private edit samples.** **Wait-listed (#1068).**
- **M4. A member with `jobs.start` alone can overwrite or wipe lines.** **In open PR (#1135),** after the owner's decision at 17:05.
- **M5, M6 and M9.** All **fixed (#1125)**:
  - The Discover catalogue leaked full source URLs to every member.
  - Two AI routes had no concurrency limit.
  - One account could use up the shared bug-report cap.
- **M10. "Reset library" erases accounts, device tokens and the audit log without a trace.** **Wait-listed.**

**Unsure:** M7 (Reader page under `library.read`), L17 and L23 touch an earlier owner decision. The reviewer flagged them for re-confirmation, not as bugs. The owner then chose `lines.read` for the Reader page (in #1135).

### 2.14 Transcribe, translate, review and export services (#1116), not triaged

**What it examined:** the line-proposal flows, full-title translate, fix-flagged and the export services.

**Most important findings:**
- **H1. "Translate all lines" overwrites English typed during the run.** The protection exists in the code but isn't used. **Not started.**
- **H2. "Fix flagged lines" overwrites edits made during the run.** **Wait-listed** as #1088 M12.
- **M3. Exports aren't atomic on Windows.** A re-export copies over the live file, so a download mid-copy is truncated. **Not started.**
- **M4. The dub job and the exports that read its track don't exclude each other.** A dub mid-export makes a corrupt video. **Not started.**
- **M8. A stored "novel reference" file name can point outside the title folder.** That file is then read and sent to a cloud AI. **Not started.**
- **M10. yt-dlp downloads have no size cap,** though the docstring promises one. **Not started.**

**Unsure:** nothing was run. The Windows move behaviour comes from Python's documentation.

### 2.15 Auth, server and core routers (#1118), not triaged

**What it examined:** `api/auth.py`, the server wiring, the error handlers and the core routers. No HIGH. OIDC sign-in holds up.

**Most important findings:**
- **M1. With sign-in off, any local TCP relay gets full owner rights.** Examples: a port proxy, an ssh or VS Code tunnel, or another Windows account. **Not started.**
- **M2. Library search returns line text to members without `lines.read`.** An unescaped `%` matches every line. **Not started.**
- **L2. The single-port setup writes sign-in codes into the access log.** **Not started.**
- **L6. The shared error handler removes keys but not file paths.** **Not started.**
- **L7. Any member who can see a shared title can cancel the owner's jobs on it.** **Not started.**

**Unsure:**
- M1 and L3 follow from the code. No live tunnel was used.
- Whether Baihe's own fetchers can be pointed at its own port was not traced.

### 2.16 Scanlate, comic, novel, page server and extension (#1119), not triaged

**What it examined:** the scanlate pipeline, comic and novel attach, the page bridge and the extension worker.

**Most important findings:**
- **H1. The bridge trusts the image type the extension claims and has no pixel cap.** A 12 MB image can decode to about 3 GB. The TIFF-as-PNG case was checked by running it. **Not started.**
- **H2. "Redo" replaces translated text boxes with blank ones when OCR finds no text.** **Not started.**
- **M1. The extension's background script hands the access token to any script that asks.** **Not started.**
- **M2. Bridge calls have no timeout.** Chrome may stop the worker mid-batch. **Not started.**
- **M8 and M9. Raw-novel writes have no lock and no recovery after a failure.** One full disk can lose every chapter split. **Not started.**

**Unsure:**
- Chrome's worker time limit comes from Chrome's documentation.
- M5 and M7 were reasoned, not reproduced.
- Half of the area came from a sub-reviewer.

### 2.17 Python test coverage (#1121), not triaged

**What it examined:** whether the Python tests can fail. The reviewer broke the code on purpose in a scratch copy and ran the tests.

**Most important findings:**
- **H1. With the main jobs lock replaced by a no-op, no jobs test failed.** **Not started.**
- **H2. An error in a job thread is only a warning, so it never fails a test.** **Not started.**
- **H3. The cross-process GPU slot is only tested single-threaded.** Weakening its transaction left all 35 tests passing. **Not started.**
- **H4. CI skips the only tests of id-keyed matching for Scanlate,** because OpenCV isn't installed there. **Not started.**
- **H5. Removing secret redaction from four bulk error paths left 76 tests passing.** **Not started.**
- **H6. Two permission tests accept any exception,** so a crash passes as a refusal. **Not started.**

**Unsure:**
- The full suite was not run.
- "Skipped in CI" was read from the workflow file, not a CI log.

### 2.18 CLI and headless parity (#1123), not triaged

**What it examined:** `cli.py` and related scripts against the services they mirror, with a parity table.

**Most important findings:**
- **H1. The CLI and the app can't see each other's translation runs.** Both pay, and the last writer wins. **Not started.**
- **H2. CLI-started jobs reuse the app's GPU slot name, so two Whisper runs load at once.** This is the same root cause as #1086 M1. **Partly in open PR #1073.**
- **H3. `narrate-prep` on Ollama takes no GPU slot.** **Not started.**
- **M4. An unknown `--id` crashes five commands** (reproduced). **Not started.**
- **M8. Batch commands exit 0 even when titles fail.** **Not started.**
- **M11. CJK titles crash the CLI when output goes to a file on Windows** (reproduced under a Windows code page). **Not started.**

**Unsure:** there is no Windows console, GPU or real engine in the sandbox.

### 2.19 Titles, library, settings, backup and restore services (#1124), not triaged

**What it examined:** the drama, library, ownership and settings services, plus the backup services (through a sub-review). No HIGH.

**Most important findings:**
- **M1. A job can start between a delete's job check and the delete itself.** It spends paid tokens on a deleted title. **Not started.**
- **M2. Restoring a title from another PC's copy keeps that PC's owner, private flag and absolute file paths.** **Not started.**
- **M3. A full restore takes the automatic-backup folder and schedule from the uploaded file.** **Not started.**
- **M4. One non-UTF-8 byte in `.env` makes every API key read as unset,** and every key save fails with a server error (reproduced). **Not started.**

**Unsure:** Windows-only behaviour was reasoned, not run.

### 2.20 Senior review: architecture, security and maintainability (#1113)

**What it examined:** the whole codebase, measured with grep and size counts. Static only.

**What it found:**
- **The overall shape is sound.** Every route declares one permission marker, the loopback gate blocks DNS rebinding, and the household listener has its own host gate.
- **Per-device extension tokens are half-built.** No route checks them, so revoking a device cuts off nothing.
- **The layer rule is only partly true.**
  - 17 lower modules import `services`.
  - 13 services run raw SQL.
  - `db.py` (297 KB) is the real debt.
- **Job state is still written to sqlite while holding the global lock,** with sqlite's default 5 s wait.
- **The size ratchet has about 180 KB of slack** after the October splits.
- **Operations gaps:**
  - 126 places swallow exceptions silently.
  - Auto-backups leave out `sources.db`.
  - Nothing runs a database integrity check.
- **CI and source installs use floor versions, while the installer is hash-locked.** CI therefore tests a different dependency set from the one that ships.

**Its 15 ranked improvements and their status:**

| # | Improvement | Status |
|---|---|---|
| 1 | Wire device tokens into the extension bridge | Not started |
| 2 | Tighten the size ratchet | Not started |
| 3 | Short busy wait for job writes under the lock | Fixed (#1078) |
| 4 | Fix the stale docs | Partly fixed (#1122). STATUS.md still waits. |
| 8 | Move the remaining fetchers onto `lib/http` | Wave 4 in open PR (#1070) |
| 9 | Start splitting `db.py` into a package | #943, open and parked |
| 11 | One settings reader | Open PRs #1068 and #1071 |
| 5, 6, 7, 10, 12, 13, 14, 15 | Integrity check, hash-locked installs, shared path/ownership/redaction helpers, layering guard, threat model, bridge GPU cap, swallow-site audit, grouping `services/` | Not started |

**Unsure:**
- The bridge-versus-GPU contention and the impact of the 126 swallow sites are hypotheses from grep.
- Open PRs were not read.

### 2.21 Design audit (#1120, merged) and UX review fixes (#1089, merged)

**What the design audit examined:**
- About 25 changed components, judged for hierarchy, density and edge cases.
- Every touched page loaded at 360px with 130-character titles. No page scrolled sideways.

**Fixed: 12 flaws (2 High, 6 Medium, 4 Low).** The most visible:
- A failed "Make subtitles" run now shows its reason at the top of the form, not a screen below.
- The phone header's Read button no longer stretches into a full-width bar that outranks each stage's own primary button.
- The Library loading cards now match the size of loaded cards (101px against 170 to 216px before), so the grid no longer jumps.
- Empty states that offered no way forward got one action each: Import from Sources, New title, Clear search and Go to Library.

**Deferred to #1089, now fixed:**
- Read and "Open in Scanlate" became secondary buttons.
- Source links got a 44px touch target on phones.

**Found but not fixed:**
- The Library list moves down about 290px after load.
- There are two primary buttons on the Library's first screen. **Owner decision (17:00):** collapse "Make subtitles" once the library has titles.
- An orphan "Ready" line.
- "Spoiler-free" is a checkbox, not a toggle.
- The CJK clip width uses a character-range rule, not real font metrics.

These became a "design-audit follow-ups" brief. Both PRs it waited for (#1120 and #1129) have merged, but the notes show no launch yet.

**#1089 also fixed:**
- A stale source link that carried over between titles.
- Wording damage from the "drama to title" rename.

**#1089 was unsure about:**
- Its new stale-link test was never run against the unfixed code.
- No phone test covers the Reader header or the novel chapter rows.

### 2.22 JavaScript modernisation (#1117 merged, #1111 open)

**#1117 (frontend):**
- Merged three copies of the modal-dialog open/close effect and two copies of the Tab trap into one hook.
- Merged two copies of the "Aa" reading-settings control into one component.
- Net change: 33 lines removed.

It checked the brief's three supposed duplicates and found they **were not duplication**:
- **Clipboard.** Every caller already used one helper. The one exception is a deliberate Safari workaround.
- **The two `SelectionBar`s.** They share a name but are different components. Sharing one would need more props than either has.
- **Dialogs.** There is no four-way family. Three `<dialog>`s share behaviour (now merged), and two "dialogs" were popovers.

Candidates it saw but left:
- The same close-on-navigation effect in the nav drawer and command palette.
- Focus-restore logic.
- A shared form-update helper in the Comic and Reader settings.
- A `useCopy` hook for 8 copy-feedback states.

**#1111 (extension, open):**
- Modernised `options.js` and added `extension/shared.js` with four helpers, plus 11 tests.
- Lines went up by 29 for now. The saving comes when `popup.js` and `content.js` move over, after the other extension PRs merge.
- `background.js` has nothing to share, because its reply shapes are produced, not consumed.
- One CI failure in an unrelated workspace test was judged not caused by the PR.

---

## 3. Opus reviews of the fix PRs

The lead had Opus review each security- or data-sensitive fix PR before merge, and three earlier merged PRs after merge. Several reviews found problems in the fixes themselves. Source: the timeline in `review-plan.md`.

| PR | Verdict | Worst problem found | Outcome |
|---|---|---|---|
| #1075 `lib.http` | Safe, 4 LOW | After a redirect chain A to B to A, credentials could come back on the last hop. One code comment was wrong. | Merged. The LOWs are carried by open #1082. |
| #1077 pending-install lock | Safe to merge; 1 MEDIUM, 3 LOW | Two starts after a crash could both take over the stale lock. A reused process id could look alive. | Merged. Follow-up in open #1083 (a real OS file lock). |
| #1078 job store | Runtime sound | Two of its new tests could not fail: one passes vacuously, one never reaches the disk error. Three small state bugs. | Merged. Carried by open #1085. A #1078 test later failed on #1068's CI and is #1085's to fix. |
| #1076 GPU process job | No HIGH or MEDIUM, 4 LOW | The watchdog flush can cut a large message mid-write. The callback guard swallows a cancel. Two tests pass on the old code. | Merged. Follow-up in open #1147. |
| #1072 extension novel capture | No blocking, 1 LOW | A surrogate-pair split in long headings | Merged, with a LOW fix |
| #1070 HTTP front door, wave 4 | 1 MEDIUM, 4 LOW | An oversized-body error had the wrong type, so the fetch ladder re-downloaded. | Fix pushed. Still open and needs a re-review. |
| #1068 settings, one reader | Needs a fix (wording from the tracker) | The PR removed tests that were the only tests of behaviour still in the app (H1 to H4). | Must restore them. Open, with CI red on the #1078 test. |
| #1133, #1092, #1090 | Safe | LOWs only | Merged. LOWs in open #1152. |
| #1125 caps, privacy and glossary | Safe on security; 1 MEDIUM | A case-handling bug in the new glossary substitution | Merged. Fix in open #1152. |
| **#1131** Install now safety | **Not safe** | The UI never sends the risk acceptance the server now requires (H1). The writable-folder check is useless on Windows. The Deno stub is a dead end. Seven LOWs. | A fix session is working on the same branch. Open. |
| **#1132** generic import bounds | Safe on security; 2 MEDIUM | The default image budget silently drops GIF and text/plain pages. The Referer still leaks through redirects. Four LOWs. | Fix session. Open. |
| **#1134** source parser bounds | **Not safe** | The new ranobes cap silently drops chapter 1 onwards on lists over 200 pages. A Turkish-İ offset bug and a 512 KB pre-strip cap in `detect.py`. A quadratic helper left in manhuagui. The caps are untested. | Fix session. Open. |
| **#1126** diagnostics redaction and GPU | **Not safe yet** | A NUL placeholder makes redaction crash, which turns a bug report into a server error. Whole-word matching still leaks `bob@host`, `BOB-PC` and `bob_notes`. The Diagnostics page still imports PyTorch, so the goal is not met. Five LOWs. | Fix session. Open. |
| #1135 permissions | Not reviewed yet | (pending) | Open |
| #1073 one GPU path | Held | An invariant checklist was prepared for after its merge resolve. | Open, held for the owner's PC test (gate 1). |

**After-merge reviews of three earlier PRs:**

| PR | Findings | Outcome |
|---|---|---|
| #1020 | 2 LOW | Open #1144 |
| #1024 | 1 MEDIUM: the GPU probe stamped its time before calling nvidia-smi, so a timeout left a stale reading and a 5 to 10 s stall. 5 LOW. | MEDIUM fixed in #1133. Some LOWs wait-listed. |
| #1025 | 2 LOW | Open #1145. One LOW wait-listed. |

**What this shows:** of the four security-sensitive fix PRs (#1126, #1131, #1132, #1134), three needed another round and none has merged. The pre-merge reviews are earning their cost.

---

## 4. Patterns across audits

These defects recur across many areas. Each pattern points at a structural fix, not more one-off patches.

| Pattern | Examples | What it suggests |
|---|---|---|
| **Silent truncation or dropped data** | SRT blank line drops the Chinese line (#1094 H4). Qwen stops at 256 tokens (#1094 H6). A cut-off AI reply blanks the batch (#1096 H2). Missing batch results counted as success (#1096 M14). Strip slicing stops at 201 pieces (#1119 L8). Two fixes added new silent drops: the ranobes cap (#1134) and the image budget (#1132). | Every cap and limit should fail loudly or flag the line, never drop quietly. Add a rule to the brief template: "when a cap is hit, the user sees it". |
| **Locks held across slow calls** | Job store and GPU slot decision under the jobs lock (#1086 H2, day's review 1.1, #1113). Whole-page image decodes under the pipeline lock (#1119 M4). File copies under the snapshot lock (#1088 L23). `.env` rewrites on the event loop (#1106 L22). | Decide under the lock, do the slow work outside it. A short sqlite busy timeout everywhere a lock is held. |
| **Check-then-act races** | Search double start (#1086 H1). Delete versus job start (#1124 M1, #1088 M13). Upload versus job start (#1106 M8). Novel append (#1119 M9). Stale-lock takeover (#1077 review). Stale responses in the frontend (#1101 H3 and M4, #1103 M9). | One "hold" helper that `start_job` checks under its lock. Request counters or abort signals in every frontend loader. |
| **Long jobs overwrite user edits** | Full translate (#1116 H1). Fix flagged (#1116 H2, #1088 M12). Re-segment (#1088 M11). CLI diarize (#1123 M10). Find-and-replace apply (#1106 L16). | Make the compare-and-set save (`only_if_unchanged`) the default for job writes, with a full overwrite as the explicit exception. |
| **Secrets, URLs or paths leaking into text** | Support report tokens (#1098 H5). ffmpeg paths in job errors (#1094 H5). Source error records (#1097 M16). Referer (#1097 M15). Catalogue URLs (#1106 M5). OCR notes (#1119 M6). HF_TOKEN exported to children (#1094 M16, #1119 L6). Sign-in codes in the access log (#1118 L2). The error handler (#1118 L6). The fix #1126 itself had redaction gaps. | One redaction function (secrets plus URLs plus paths) used by the shared error handler and every stored error, instead of per-service copies. The senior review counts 9 copies of `_redact`. |
| **Hostile input without bounds** | Regex blowups (#1097 H3, H4, M11; #1106 H1). Decoders (#1097 H5, H6, M12). Image pixel caps (#1119 H1). Multipart uploads spooled before any cap (#1106 L14, #1119 L13, #1118 L9). | Shared bounded helpers: a regex runner with a timeout, a capped decoder, one image check used by every image entry point. |
| **Stored file names trusted as paths** | Voice bank clips (#1088 M9). Novel reference (#1116 M8). Audio file from another PC's copy (#1124 M2). The `_inside` helper copied 5 times with subtle differences (#1113). | One `lib/paths.contained()` helper, used everywhere a stored name becomes a path. |
| **Two processes that can't see each other** | GPU slot name with no instance (#1086 M1, #1123 H2). The CLI can't see app jobs (#1123 H1). Reused process ids (#1086 L4, #1077 review). | Put the instance id in every holder and job row. Make the CLI write job records like the app does. Much of this is #1073 and Phase 1. |
| **Tests that cannot fail** | `.tsx` tests never run (#1104 H1). Always-skipped tests (#1104 H2, M7). Sleeps used to prove nothing happened (#1104 M6). Locks removed with no failing test (#1121 H1, H3, M14, M15). `pytest.raises(Exception)` (#1121 H6). A fake-clock deadline test that passes while the bug exists (#1097). Tests pinning the bad behaviour (#1094 M7, #1098 H2). Vacuous tests in a fix (#1078 review). | Make thread exceptions fail pytest. Add `Barrier`-based contention tests for each lock. Require each new test to be run once against the unfixed code. #1141 and #1087 did this; #1089 did not. |
| **Docs and comments that claim what the code doesn't do** | 40 drifts in #1105. STATUS.md says device tokens work (#1113). Docstrings promising protections the code lacks (#1102 H3, #1116 M7 and M10, #1119 L1, #1088 H1). | Keep docs that state facts short and generated where possible (as the route table now is). One docs-sync pass after each merge wave. Treat a false "never" in a comment as a bug. |
| **Frontend shows what the server refuses** | Members offered admin-only actions (#1101 H1, H2, M8, M11; #1104 H3). | One permission hook every page uses, and a test that runs pages as a limited member. #1139, open, adds that test. |

---

## 5. Honest limits

### What the audits could not check

- **No real GPU, models or long media.** Memory, time and disk figures for 6-hour files (#1094 H1 to H3, #1119 H1 and M4) are estimates. Qwen truncation and GPU fallback were not observed.
- **No real sites, proxies, browsers or yt-dlp extractors.** Every sources finding was shown with local servers or timing probes (#1097). Whether yt-dlp actually reaches private addresses through a real extractor is reasoned, not shown.
- **No real API keys or provider calls.** Prompt caching savings (#1125), DeepSeek and Gemini reply shapes (#1096) and cost figures are unverified.
- **No Windows.** File locks on loaded `.pyd` files, closing the install console, the service account, `shutil.move` over an existing file, CJK profile paths and the console code page were all reasoned from documentation (#1098, #1116, #1123).
- **No exploratory run in the live app with auth on or with real jobs.** The only live check was a smoke test of the GET routes at 15:24: 172 routes, no server errors. It didn't cover POST flows, model jobs or sign-in.
- **Most audits were static.** Some did reproduce findings:
  - #1088 H1 to H4
  - #1097, five HIGH findings
  - #1106 H1
  - #1119 H1
  - #1121, with deliberate code breakage
  - #1123 M4 and M11
  - #1124 M4
- **Several audits handed parts to sub-reviewers and re-checked only the HIGH findings:** #1102, #1103, #1119 and #1124.
- **The full test suite was not run by any auditor.** CI runs it on each PR.

### Owner-only checks that remain

The owner's checklist is in `owner-test-checklist.md`. Its last entries are for #1072 to #1078 and #1084.

**Gate 1** (the headline PC test, from `review-plan.md`):
- `start.bat` boots on a fresh library.
- A transcription runs, cancels, and survives a server restart.
- A Diagnostics install queues for restart.
- The "Make subtitles" flow works.
- The extension captures one chapter.

PR #1073 (one GPU path) is held for this.

**Checklist items for fixes from these audits:**
- **#1077:** kill pip mid-install, then restart. Baihe should start at once and show the package names.
- **#1078:** with a backup tool holding `library.db`, the Jobs page should still answer within a second.
- **#1076:** Compare transcription on a short clip, then Cancel. The GPU should be freed.
- **#1075:** Ollama behind a reverse proxy still works.
- **#1084:** a long comic page captured through the extension opens.
- **#1058:** the four job-store steps (kill and restart, clean stop, read-only database, force stop).

**Owner checks named in PR bodies but not yet in the checklist:**
- **#1087:** seven real-browser checks of the canvas matching and the Allow-site flow.
- **#1125:** confirm real Anthropic cache reads on a long-novel run.
- **#1126:**
  - Account names are now redacted as whole words, so "runner" becomes `[USER]`. Confirm this is acceptable.
  - Confirm no GPU probe runs during a torch install.
- **#1129:** 360px screenshots.
- **#1130:** a real Google sign-in in a new tab.
- **#1131:**
  - Delete the old Deno stub once.
  - Confirm a failed preview on Windows gives a 409.
- **#1134:** a live-site check of the capped parsers.
- **#1099:** a zh export after an en one downloads as "(zh)".
- **#1141:** a Review edit survives an API restart.
- **#1120:** the Read button is compact on a phone.
- **#937:** run one real stream through the streamer review flags.

**The checklist needs updating.** Merged fixes #1089, #1090, #1091, #1092, #1120, #1122, #1125, #1127 to #1130, #1133 and #1137 have no checklist entry yet.

### Open owner decisions from the audits

- **Restore brief B6:** whether to bring back the Jellyfin-headroom brief that #1122 deleted.
- **#1125 departures and limits:**
  - Spaced glossary variants are still applied.
  - The cache breakpoint is kept on the novel block.
  - A per-user report cap of 10.
- **#1135:** whether the glossary-affected re-translate should also need `lines.edit`.
- **#1137:** whether to keep its pinning tests or close it.
- **OpenCV and PaddleOCR:** which OpenCV package wins. An investigation is running.

---

## 6. What I recommend next, in priority order

1. **Fix the find-and-replace regex freeze now** (#1106 H1).
   - Its blocker (#937) has merged.
   - Any household member with read access can freeze the server.
   - The fix is small: run user regexes with a timeout, or refuse quantified groups.
   - Use Opus (security).
2. **Finish the four "not safe" security PRs before anything else in their files:** #1126, #1131, #1132 and #1134.
   - Their fix sessions are running.
   - Merge only when CI is green on the exact head.
   - Re-review only if the fix adds new surface, per the lead's protocol.
3. **Triage the six untriaged issues into the fix queue:** #1116, #1118, #1119, #1121, #1123 and #1124 (13 HIGH, 48 MEDIUM). Suggested first briefs:
   - **(a) Stop jobs overwriting edits:** #1116 H1, H2 and #1088 M12. Make the compare-and-set save the default.
   - **(b) Bridge image checks and Redo data loss:** #1119 H1, H2, plus the token handout (M1).
   - **(c) Make tests able to fail:** #1121 H1 to H6. Start with thread exceptions failing pytest, and install OpenCV in the CI test job.
   - **(d) CLI and app coordination:** #1123 H1 and H3, M4, M8 and M11.
   - **(e) Restore and `.env` safety:** #1124 M2 to M4.
   - **(f) The loopback trust question:** #1118 M1. A per-launch secret on the admin listener. Owner decision and Opus.
4. **Get the owner's PC test (gate 1) done, so #1073 and #1068 can merge.** Those two PRs block most of the 93 wait-listed findings, including every restore data-loss finding (#1088 H1 to H5) and the jobs-lock findings (#1086 H2, M1 to M4).
5. **Launch the briefs that are already unblocked:**
   - Bulk translate: #1096 H4, H5, M12 to M14, now that #1110 merged. Money and data; Opus.
   - Comic and Reader: #1103 H3, M4 and M6, now that #1089 merged.
   - The design-audit follow-ups (#1120 and #1129 merged), including the owner's "collapse Make subtitles" decision.
6. **Structural helpers that retire whole classes of defect** (section 4):
   - one redaction function for every error that is stored or shown;
   - one `contained()` path helper;
   - bounded regex and decoder helpers;
   - compare-and-set by default for job writes;
   - instance ids in GPU and job holders.
   
   Each is small to medium, and each makes a pattern impossible rather than relying on review to catch it.
7. **Close the senior review's cheap wins:**
   - Wire or remove the device tokens (#1113 item 1).
   - Tighten the size ratchet (item 2).
   - Back up `sources.db` with the auto-backup, and run a quick integrity check at startup (item 5).
   - Write a one-page threat model (item 12).
8. **One docs pass after the next merge wave:** STATUS.md, background-jobs.md, CLAUDE.md and AGENTS.md (#1105 H1 to H3, M12, M13), plus the `npx tsc -b` command and the stale "Settings > Remote access" comments. Then update `owner-test-checklist.md` for the 13 merged fixes it is missing.

---

### Also run on 2026-10-10, not covered in detail here

These landed after the reviews above. I did not read them for this report.

- **The simplification pass.** Read-only quality reviews, not bug hunts. Seven of nine issues had landed by 18:42: #1155 to #1161.
- **The live-translation speed and accuracy research:** draft PR #1153 and `reports/Live translation speed and accuracy.md`.
- **The harvests of "found but not fixed" notes:**
  - from open PRs: 88 rows;
  - from merged PRs: 120 rows;
  - the session-notes triage: 60 done, 19 covered, 18 owner decisions, 16 still open, 18 too vague (dropped by the owner) and 3 unresolved.

  Their actionable rows were folded into the fix queue's wait lists.
