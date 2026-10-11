# Right-sized triage of the 2026-10-10 audit findings

This document re-judges every rated finding from the 2026-10-10 audits against how Baihe is really used.
It is written for the owner.
It answers three questions: which findings really need fixing, how likely each one is, and which ones are over-cautious or wrong.

Code was checked against `origin/baihe-subtitler` at `61439a42` (the newest commit on 2026-10-10 at about 19:10 UTC).
PRs that merged or opened after that may have changed some statuses.

## The yardstick used

- One main server: the owner's Windows PC.
- Up to 3 more PCs on the same home network.
- Up to 3 remote or phone users, each with an account the owner created.
- Everyone is someone the owner knows. Nobody signs in anonymously.
- The money at risk is the owner's own AI keys. The data at risk is a personal subtitle library.
- The realistic dangers are: accidents by trusted people, crashes, power cuts, flaky networks, Windows quirks, lost edits, surprise AI bills, and one household member seeing another's private titles or a secret.
- An internet attacker can only reach Baihe through the remote-access feature. So sign-in and the "this PC only" boundaries still matter.
- Attacks that need a hostile household member with precise timing, a second hostile Windows account, or a hand-made booby-trapped file from a stranger are judged unlikely.

## Words used

- **MUST-FIX**: loses or corrupts data, hangs or crashes in normal use, wastes real money, shows a secret or someone's private data to the wrong person, or makes a main feature unusable.
- **SHOULD-FIX**: likely to annoy someone within months, or cheap and clearly right.
- **CHEAP-WIN**: small and low risk. Do it when someone is editing that file anyway.
- **DEFER**: real, but unlikely at this scale. Write it down. Do not schedule it.
- **SKIP**: over-cautious, speculative or wrong. The failure cannot happen, is already prevented, needs an attacker or timing this household does not have, or the fix adds more complexity than the risk is worth.
- **CAS (compare-and-set)**: a save that only writes a line if it still holds what the job read at the start. It stops a long job from overwriting an edit made while the job ran.
- **PR**: pull request, a proposed code change.
- **CI**: the automatic test run on GitHub.
- **LLM**: the AI model that translates (Claude, Gemini, DeepSeek, Ollama).
- **SSRF**: tricking Baihe into fetching an address on the home network for someone else.
- **ReDoS**: a search pattern written so that checking it takes practically forever.
- **"Fixed #N"** in a reason means PR #N already merged. **"In #N"** means a PR is open.

---

## (a) One-page summary

### Counts per class (all 469 rated findings)

| Source | MUST-FIX | SHOULD-FIX | CHEAP-WIN | DEFER | SKIP | Total |
|---|---|---|---|---|---|---|
| 12 triaged issues (#1086 to #1106) | 12 | 45 | 116 | 82 | 30 | 285 |
| 6 later issues (#1116 to #1124) | 3 | 7 | 43 | 42 | 21 | 116 |
| Lead's review of the day's own changes | 4 | 10 | 28 | 11 | 15 | 68 |
| **All** | **19** | **62** | **187** | **135** | **66** | **469** |

Read the table this way:
- Only **19 of 469 (4%)** are MUST-FIX. Six of them are already fixed and merged. Five more are in open PRs or running sessions. Eight are not started.
- **81 (17%)** are MUST-FIX or SHOULD-FIX. That is the real workload, and 32 of those are already fixed and merged.
- **187 (40%)** are cheap wins. Pick them up only when someone is already in the file.
- **201 (43%)** are DEFER or SKIP. They are mostly correct observations about situations this household will not hit.

### How often the auditors were simply wrong

The auditors rarely invented problems. They mostly over-weighted real ones.

- **Wrong, or a misreading of the code: 7 findings (1.5%).**
  - Day review 6.1, 6.2 and 7.1 are rated MEDIUM, yet each one's own text concludes there is no defect ("Keep", "no leak", "not a bug").
  - #1119 M1 says the browser extension hands its token "to any sender". The extension has no `externally_connectable` entry, so web pages cannot message it. Only the extension's own scripts, or a browser that is already hacked, can.
  - #1098 M6 says a hard kill leaves a broken `deno.exe`. The code already creates the file exclusively and deletes it on any error. Only a power cut mid-copy can leave a stub.
  - #1097 H3 says a slow regex "runs on every HTML response" and a 10 MB page "would take hours". It is only slow when a page contains thousands of unclosed `<script` tags. Normal pages, and pages cut off mid-script, are fast.
  - #1106 M7 (Reader page under `library.read`) contradicts a recorded owner decision; the fix queue already marked it disputed.
- **Real, but with the size overstated: at least 1 more.** #1096 H3 says the cost estimate is "low by more than ten times". That is true of token counts. Since #1125 turned on working prompt caching, the money error is closer to 2 to 4 times. (Separately, #1105's own summary counts disagree with its list; the audit review document already noticed.)
- **Real code, but no reachable failure today: 7 more findings (about 1.5%).** These are "defence in depth" items where the reviewer said so: #1088 L19, #1118 L4, #1118 L6, #1124 L5, #1121 M13, #1106 M4 and #1118 M2. The last two need the owner to take away a permission every member has by default.
- **Real, but implausible at this scale: about 188 more (40%).** These need a hostile web page, a crafted backup file, a second Baihe server on one library, a laptop going to sleep mid-job, or two clicks landing in the same millisecond.

### The 10 things that really need doing, regardless of the audit's severity

| # | What | Finding ids | How likely here | Status |
|---|---|---|---|---|
| 1 | Long jobs overwrite what people type. "Translate all lines" and "Fix flagged lines" save over edits made while they run. The safe save (CAS) already exists and just isn't used. | #1116 H1, H2; #1088 M12 | Monthly. Anyone who reviews while a long translate or fix runs. | Not started; waits for #1068 and #1073. |
| 2 | "Install now", "Upgrade" and "Install GPU PyTorch" can half-replace Python on Windows, so Baihe will not start. Diagnostics itself loads PyTorch, which locks the files the GPU install must replace. | #1098 H1, H4, M4 | Once, the first time the owner sets up GPU PyTorch, and again on any risky upgrade. | In #1131 and #1126; both judged not safe yet. Finish them, but lean (see section c). |
| 3 | Closing the "installing" console window (or a power cut) mid-install leaves Python broken, with no automatic restore. | #1098 H2 | Once a year. Closing that window is a natural thing to do. | Half fixed (#1077). The restore-on-next-start half is in #1083. |
| 4 | Restoring a backup loses `sources.db` (tracked series, proxy and source settings). Auto-backups never include it, and restore deletes the live copy when the backup lacks it. | #1088 H1, H2, H3; #1113 item 5 | Certain, every time someone restores an auto-backup. Restores happen about once a year, usually after something already went wrong. | Waits for #1068 and #1073. |
| 5 | Phone comic reading keeps every page decoded. A long vertical chapter can crash the phone's browser tab mid-chapter. | #1103 H3 | Weekly for anyone reading long webtoons on a phone. | Session `fix-comic-reader-review` launched. |
| 6 | Hard-subtitle OCR writes every sampled frame as a full-size PNG. That is about 5 to 10 GB of temporary files for one 45-minute 1080p episode, not only for 6-hour videos. | #1094 H3 | Every hard-subtitle run on a full episode. | Waits for #1099. Fix is a crop or scale in the ffmpeg command. |
| 7 | A cut-off AI reply blanks a whole batch after paying twice. Claude is capped at 4,000 output tokens per batch. | #1096 H2 | Monthly on novels with long paragraph lines (30 lines per batch). | Waits. Cheapest fix: raise the cap and keep the complete pairs. |
| 8 | Cost estimates are too low before paid runs (translate, and Benchmark after importing a bigger case set). | #1096 H3; #1103 H1; #1102 M8 | Every bulk run is under-estimated. Benchmark: when a set changes after estimating. | Waits for #1068, #910, #883. |
| 9 | Stored source links lose their whole path when any part is 32 or more characters long, which is common for English novel slugs. The link is saved permanently as just the site name. | Day review D-M3 | Weekly for anyone importing from sites with long readable URLs. | Under-rated as MEDIUM. Check whether #1093 covers it. |
| 10 | Scanlate "Redo" replaces translated (and hand-corrected) text boxes with blank ones when OCR finds no text, for example when the OCR package is missing. | #1119 H2 | A few times a year for Scanlate users; certain whenever OCR is broken. | Waits for #1068. One condition to change. |

Close runners-up: one non-UTF-8 byte in `.env` silently hides every API key (#1124 M4, one-line fix). Re-transcribing many lines at once can time out and lose every heard line (day review C2, rated LOW). CLAUDE.md tells agents to run `npx tsc --noEmit`, which checks nothing at the frontend root (found by #1117). The find-and-replace regex freeze (#1106 H1) is cheap and already in flight, so finish it.

### The 10 biggest items not to implement

| # | Item | Finding ids | Why not, in plain words |
|---|---|---|---|
| 1 | A per-launch secret for the admin listener when sign-in is off | #1118 M1 | It only matters if someone forwards the PC's port 8600 or logs in as a second Windows user on the owner's PC. Changing how the local window signs in risks locking the owner out a week before the freeze. Do the cheap part instead: refuse a wider set of proxy headers (#1118 L3), and write "never forward port 8600; use Remote access" in the docs. |
| 2 | Routing yt-dlp video downloads through the guard proxy | #1097 H1, M9, M10 | It needs a web page built to attack Baihe specifically, opened by a household member the owner trusts. Putting a proxy in front of every download can break downloads. |
| 3 | Hardening every site parser against booby-trapped pages | #1097 H3, H5, H6, H7, M11, M12, M13, M14 | These defend against comic and novel sites attacking the app. The first fix round (#1134) already broke a real feature: the new ranobes cap silently drops chapter 1 onwards on lists over 200 pages. Keep only caps that can never drop real content. |
| 4 | Two-phase GPU slot decisions, instance ids everywhere, and clock-jump handling | #1086 H2, M1, M2, M4; #1123 H2 | They matter for two Baihe servers on one library, or a laptop sleeping mid-job. There is one server on a desktop PC. #1073 already carries what is needed. |
| 5 | Thread-contention test suites with barriers for every lock | #1121 H1, H3, M14, M15 | They test races that need two schedulers or two processes at once. They add CI time and flaky tests before a freeze. Keep the cheap honest-test fixes instead (#1121 H2, H4, H6, M7). |
| 6 | Defences against crafted or foreign backup files | #1088 M9; #1124 M2; #1116 M8 | They need a backup file made by an attacker, or a library copied from another PC's Baihe. There is one library. If one shared "is this path inside the folder?" helper gets written anyway, use it here, but don't make it a project. |
| 7 | Permission fixes for unusual permission combinations | #1106 M4, M7; #1118 M2 | They only matter if the owner gives someone `jobs.start` without `lines.edit`, or `library.read` without `lines.read`. Every member gets all four by default. #1135 already does part of this; do not extend it. |
| 8 | Wiring per-device tokens into the extension bridge | #1113 item 1 | The bridge only listens on the PC itself, so remote users can't reach it anyway. Instead, remove or label the "revoke device" control so it doesn't promise something it doesn't do. |
| 9 | CI plumbing: OpenAPI contract tests, shard rebalancing, skip allow-lists | #1104 H4, M8, M9, M10 | Useful one day. Not user-facing, and not worth the churn before Oct 24. |
| 10 | Maintenance-assistant and support-report redaction hardening | #1098 M8, M9, M10, M11, H5 | The assistant is owner-only, and the files it can read are already public on GitHub. The support report goes to the owner. The redaction rework in #1126 already crashed the bug-report route once. Strip URL query strings and stop. |

---

## (b) Every finding

Columns: **ID**, **Finding** (one line), **Class**, **Likelihood** (at this scale), **Impact** (if it happens), **Reason**.
Where a row lists several ids, each one gets the same class.

### Lead's review of the day's own changes (68: 8 HIGH, 28 MEDIUM, 32 LOW)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| 1.1 (H) | Job store waits up to 5-10 s for a locked `library.db` while holding the jobs lock | SHOULD-FIX | Weekly while an auto-backup or backup tool holds the DB | Every job status freezes for seconds | Fixed #1078. Right size. |
| 1.2 (M) | `_last_write` grows by one entry per job ever run | SKIP | Cannot matter: thousands of tiny entries | A few KB of memory | Carried in #1085 anyway; not worth its own work. |
| 1.3 (M) | `owner_gone` closes its own job's row while a write is pending | DEFER | Needs a failed DB write plus the finished-job sweep | A job briefly shown "interrupted" | Rare; #1085 owns it. |
| 1.4 (M) | `close_stale` closes rows of a paused second process | SKIP | Needs a second Baihe process suspended over 15 min | A status flicker | One server. |
| 1.5 (L) | `atexit` flush registered at import in every importer | CHEAP-WIN | Test runs only | A stray warning | Move it when touching the file. |
| 2.1 (H) | `lib.http` treated a redirect as success | SHOULD-FIX | Whenever Ollama or an API sits behind a redirecting proxy | Wrong "not running" message | Fixed #1075. |
| 2.2 (M) | 307/308 redirects resent a POST as a bodiless GET | CHEAP-WIN | Rare | A confusing 4xx | Fixed #1075. |
| 2.3 (M) | Response headers were case-sensitive | SHOULD-FIX | Any server sending `Content-Type` in another case | Silent misreads | Fixed #1075. Real bug. |
| 2.4 (M) | Pinned fetches ignore a system proxy | DEFER | Only on a PC that must use a proxy to reach the internet | All fetches fail with a vague message | A docs note is enough. |
| 2.5 (L) | New session per redirect hop, no keep-alive | DEFER | Every crawl | Slower chapter crawls | Speed only. |
| 3.1 (M) | A slow result feeder after the GPU child exits reads as "worker lost" | SHOULD-FIX | Loaded Windows PC with the spawn start method | A finished transcription reported as lost | Plausible on Windows; small grace-period fix. #1076 merged; check #1147. |
| 3.2 (M) | `give_up` can block forever when the parent is gone | DEFER | Parent must already be dead | A lingering child process | Rare. |
| 3.3 (M) | `on_item` callback errors are unguarded and unredacted | CHEAP-WIN | A DB write failing in a callback | Job fails with raw error text | Small wrap. #1076 follow-up. |
| 3.4 (L) | 30 s parent grace on top of the job budget | SKIP | Every timed-out job | 30 s longer GPU hold | Harmless. |
| 3.5 (L) | `cancellable_lock.hold` allows no cancel check | DEFER | Only if a future caller forgets | A non-cancellable wait | Code-hygiene. |
| 4.1 (M) | yt-dlp warning could echo the event nonce | SKIP | Reviewer: low probability | A forged progress event | Needs a hostile page plus a rare echo. |
| 4.2 (M) | A second `downloaded_*` event overwrites the first path | SKIP | Download options produce one file | Wrong file picked | Speculative. |
| 4.3 (L) | yt-dlp spec file not removed on the kill path | CHEAP-WIN | Each cancelled download | A small temp file left | One `finally`. |
| 5.1 (H) | A crash during pip blocks every start for up to an hour | MUST-FIX | Once a year (closed console, power cut) | Baihe won't start for an hour | Fixed #1077 and #1092. |
| 5.2 (H) | A watchdog stop during restore reports no package list | SHOULD-FIX | Rare | Owner can't tell what broke | Fixed #1077. |
| 5.3 (M) | The pip command comes from the child's last JSON line | SKIP | Needs our own code to print stray JSON | Wrong pip command | Own code; validated in #1083. |
| 5.4 (M) | A long silent pip run is shown as "interrupted" | DEFER | pip silent for over an hour | Misleading status | Rare. |
| 5.5 (L) | `queue()` does not check the exclusive hold | SKIP | Harmless today (reviewer) | None | Inconsistency only. |
| 6.1 (M) | Extension private-host check | SKIP | Cannot happen | None | The reviewer's own text concludes "Keep": not a defect. Counted as wrong. |
| 6.2 (M) | `response.url` fallback is dead code | SKIP | Cannot happen | None | Reviewer says "no leak". Counted as wrong. |
| 6.3 (L) | Base64 built by string concatenation | CHEAP-WIN | Each large capture | Slow on phones | Fixed #1084. |
| 6.4 (L) | `/page` vs `/pages` branch reads like a bug | SKIP | Intended | None | Reviewer says intended. |
| 7.1 (M) | `dist_in_use` is conservative, so most upgrades need a restart | SKIP | Every upgrade | One extra restart | Reviewer: "a UX cost, not a bug". Counted as wrong. |
| 7.2 (L) | Unparsable versions always look like a downgrade | DEFER | Rare packages | Extra confirm | #1083 review found a related MEDIUM; let #1083 handle it. |
| 8.1 (M) | Three different caps for the same pip output tail | SKIP | Always | Inconsistent tail length | Not a failure. |
| 8.2 (L) | An unreaped child gives the hint "are you online?" | CHEAP-WIN | Rare | Wrong hint | One message. |
| 9.1 (M) | Settings reader silently falls back to defaults on any DB error | CHEAP-WIN | Rare (locked or damaged DB) | App quietly ignores the owner's settings, including the spend cap | Add one log line. |
| 9.2 (L) | An empty "choice" setting can't be told from unset | SKIP | Rare | None visible | Edge case. |
| A1 (H) | Extension maps every canvas to page 1's image | MUST-FIX | Every capture on canvas-based comic readers | Pages 2 to N dropped: the extension is unusable there | In #1087; needs the owner's browser check. |
| A2 (H) | Any failed install preview fell back to "install now" | MUST-FIX | Any preview timeout or 409 | Installs replace files in use; Python breaks | Fixed #1091. |
| A3 (H) | The "I understand" tick carried over to the next package | SHOULD-FIX | Installing two packages in a row | Risk accepted without consent | Fixed #1091. |
| A4 (M) | Tainted canvases re-downloaded on every capture step | DEFER | Each step on such sites | Slower captures | Speed only. |
| A5 (M) | Shape check comment over-claims scrambled tile sheets | SKIP | Comment only | None | Wording. |
| A6 (M) | Two primary buttons in the workspace header | CHEAP-WIN | Always | Visual clutter | Fixed #1120. |
| A7 (M) | Install queue and cancel errors swallowed | CHEAP-WIN | Rare | Silent failure | Small. |
| A8 (M) | About 150 server messages still say "drama" | CHEAP-WIN | Always | Wording mismatch | #1115 sweep. |
| A9 (M) | Extension "Allow site" flow can lose the retry | SHOULD-FIX | First use on each new site | Capture fails confusingly | In #1087; needs a real browser. |
| A10 (M) | Source links lack a 44px touch target on phones | CHEAP-WIN | Phone users | Hard to tap | Fixed #1089. |
| A11 (L) | Stale source link carried across titles | CHEAP-WIN | Navigating between titles | Wrong link shown | Fixed #1089. |
| A12 (L) | No Allow button after a partial read | CHEAP-WIN | Some sites | Dead end | Small. |
| A13 (L) | Wording damage from the "title" rename | CHEAP-WIN | Always | Odd text | Fixed #1089. |
| A14 (L) | Double submit on the pending-install panel | CHEAP-WIN | Double clicks | Duplicate request | Small. |
| A15 (L) | A redirected image gives no reason | CHEAP-WIN | Some sites | Vague error | Small. |
| B1, D-L1 (L) | Every fetch error, including redirects, counts as transient: about 6 requests before a fallback engine | CHEAP-WIN | Rare | Slower fallback | Small. B1 and D-L1 are the same item. |
| B2 (L) | A too-large provider reply is retried, billing twice | CHEAP-WIN | Rare | One extra paid call | Add to the no-retry list. |
| B3 (L) | Ollama model check accepts 3xx | CHEAP-WIN | Ollama behind a proxy | Wrong status | Small. |
| B4 (L) | Ollama checks don't follow redirects, chat does | CHEAP-WIN | Ollama behind a 308 proxy | "Unreachable" shown while translate works | Small. |
| B5 (L) | `display_url` misses percent-encoded tokens | DEFER | Rare URLs | A token kept in a stored link | Low value at this scale. |
| B6 (L) | Error response text unredacted | CHEAP-WIN | Provider errors | A key fragment could be logged | Use the existing redaction. |
| B7 (L) | Unused import; docs say "plain requests" | CHEAP-WIN | n/a | None | Cleanup. |
| C1 (L) | Upload follower checks the deadline before the finished status | CHEAP-WIN | A job finishing right at the deadline | Success reported as timeout | Reorder two checks. |
| C2 (L) | Re-transcribe many lines uses a one-window timeout | SHOULD-FIX | Whenever someone re-transcribes many short lines at once | Every heard line lost to a timeout | Under-rated as LOW. The code still passes one total at `retranscribe_many_service.py:100`. Confirm on the PC. |
| C3 (L) | A zh re-export can download labelled "(en)" | CHEAP-WIN | Exporting both languages | Wrong file name | In #1099. |
| C4 (L) | Groq failures shown as "page could not be fetched" | CHEAP-WIN | Groq outages | Misleading message | Small. |
| C5 (L) | Stale cancel flag carried into a new run | DEFER | Same as #1086 M5 | New run cancelled | Duplicate; in #1099. |
| D-H1 (H) | Launcher judged a stale install lock by age only | MUST-FIX | Once a year | Start blocked for an hour | Fixed #1092. |
| D-M1 (M) | Page bridge can bill the same page twice or drop a stored page | SHOULD-FIX | Re-capturing a page | Double LLM charge | In #1100. |
| D-M2 (M) | "Ollama not running" reads as "not set up" in the assistant | CHEAP-WIN | Ollama stopped | Misleading message | Small. |
| D-M3 (M) | `display_url` drops the whole path for any 32+ character segment; stored permanently | SHOULD-FIX | Weekly for sites with long readable slugs (English novel URLs) | Saved source link becomes just the site name; title guessing returns nothing | Under-rated. Verified: `_TOKEN_SEGMENT` matches any 32+ char slug. Check #1093. |
| D-L2 (L) | Dictionary download failure says "page could not be fetched" | CHEAP-WIN | First dictionary use offline | Misleading message | Small. |
| D-L3 (L) | `abandoned` detail state written, never read | SKIP | n/a | None | Harmless. |
| D-L4 (L) | Tesseract timeout drops a frame silently | DEFER | Rare | One subtitle missed | Low impact. |

### #1086 Background jobs and process workers (17: 2 H, 5 M, 10 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | An old run writes into a new run with the same id (search double start; Jobs delete racing a new start) | CHEAP-WIN | Search double-submit: monthly. Delete race: needs millisecond timing in two tabs | Wrong search status; in the delete race, a lost transcription | Search half fixed cheaply (#1125, removed one call). Skip the delete half: it needs two tabs to act in the same instant. |
| H2 | GPU slot decision does DB calls and nvidia-smi under the jobs lock | DEFER | Only while the CLI or a backup holds the DB | Job status stalls for seconds | Annoying, not harmful. Phase 1 work; don't rush it before the freeze. |
| M1 | GPU holder name `ui:<job_id>` names no server | SKIP | Needs two Baihe servers on one library | Two Whisper runs on one card | One server. The CLI case is #1123 H2, mostly covered by #1073. |
| M2 | A failed GPU-slot release is swallowed | DEFER | DB locked past 5 s at the moment of release | Queued GPU jobs wait up to 10 min | Rare; self-heals in 10 min. |
| M3 | Heartbeat thread dies on its first exception | CHEAP-WIN | Rare | GPU slot goes stale on long jobs | One try/except, after #1073 unfreezes the file. |
| M4 | Wall-clock staleness after sleep lets a second GPU user in | SKIP | Needs a laptop sleeping with the CLI waiting | Two jobs on the GPU | The server is a desktop. |
| M5 | A stale cancel flag carries into the next run | DEFER | Needs a failed final write and a restart within seconds | New run cancelled | Rare; already in #1099. |
| L1 | A child killed mid-write of a large progress item hangs the watcher | DEFER | Rare | Job stuck "running" | Needs a kill at an exact moment. |
| L2 | `run_in_child` drops stage reports, so model loads look "stalled" after 5 min | CHEAP-WIN | First load of a big model | False "may be stalled" warning | Small. |
| L3 | `_fail_start` skips the group kill | SKIP | Only when the OS can't start a thread | An ffmpeg left running | Practically never. |
| L4 | Windows pid reuse keeps a dead server's rows "alive" | DEFER | Rare | GPU jobs wait up to 15 min | Self-heals. |
| L5 | Progress writes a DB heartbeat on every call | DEFER | Only while the CLI writes | Slower jobs | Speed only. |
| L6 | A cancel during finishing leaves "Cancelling…" on a done job | CHEAP-WIN | Rare | Odd message | Small. |
| L7 | Cancel-check failure records never reset | CHEAP-WIN | Rare | A failure goes unlogged | Small. |
| L8 | Every `GET /api/jobs` runs a full sweep | DEFER | Every poll | Extra DB work | Fine for 7 users. |
| L9 | A clean stop deletes queued jobs' rows | DEFER | Each stop with queued jobs | No trace of dropped work | Rare to have a queue. |
| L10 | Docs disagree about which store is the authority | SHOULD-FIX | Every agent session reads these | Agents make wrong changes | Same as #1105 H1 to H3; one docs pass. |

### #1088 Database, storage and restore (24: 5 H, 9 M, 10 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| 1 (H) | `sources.db` zipped while live; its write-ahead log (WAL, recent changes) dropped on restore | MUST-FIX | Every manual full backup copies it live; restore about once a year | Recent tracked-series changes lost, or the whole restore refused | Reproduced. Fix with the SQLite backup API that `library.db` already uses. |
| 2 (H) | Restoring a backup without `sources.db` deletes the live one | MUST-FIX | Certain on any auto-backup restore (auto-backups never include it) | All tracked series and source settings gone | Reproduced and verified (`workspace_job_service.py` returns early). Carry the live file across. |
| 3 (H) | Restored `sources.db` lacks later columns | MUST-FIX | Every restore that includes it | "Save as CBZ" errors until restart | Reproduced. Run the column migration on the staged file. |
| 4 (H) | Restore resets id counters; a new user can inherit a deleted user's private titles | CHEAP-WIN | Needs: delete a user, restore an older backup, then add a new user | Private titles shown to the wrong person | Real, but the sequence is very unlikely here. Five lines in the restore brief. |
| 5 (H) | Grouped `ADD COLUMN` migrations are not atomic | CHEAP-WIN | Only during an upgrade while something locks the DB (antivirus, CLI) | A feature fails with "no such column" for good | The owner's library has already passed these migrations. Fix the pattern for future ones. |
| 6 (M) | `_ensure_ready` marks the DB ready before migrations finish | CHEAP-WIN | A failed first start | Half-built schema, never retried | Set the flag after success. |
| 7 (M) | Crash between the restore renames leaves no library | DEFER | Needs a kill in a split-second window | Library stranded in a renamed folder | Recoverable by hand; rare. |
| 8 (M) | "Sign out other devices" can be raced into two sessions | SKIP | Two simultaneous sign-out-everywhere calls | One cookie survives | Needs hostile timing. |
| 9 (M) | Voice-clip file names from a restored backup trusted as paths | DEFER | Needs a crafted backup file | `.env` copied out, or a file deleted | Needs an attacker's file. Use a shared path helper if one is written. |
| 10 (M) | Notes and emotions can attach to a deleted line | DEFER | Merging lines while emotion detection runs | A note shows on the wrong line | Low impact. |
| 11 (M) | Non-LLM re-segmentation can delete text added while it runs | DEFER | Short window (fast job) | One edit lost | Fold into the CAS work if cheap. |
| 12 (M) | "Fix flagged lines" overwrites user edits | MUST-FIX | Monthly: people review flagged lines while it runs | Hand corrections lost | Same as #1116 H2. |
| 13 (M) | Maintenance doesn't stop job starts | DEFER | A job starting in the second a cleanup runs | A running dub loses its clips | Rare timing. |
| 14 (M) | A full backup aborts if any file vanishes; single delete allowed during backup | SHOULD-FIX | Monthly: jobs create and delete temp files while long media backups run | The backup fails; retried later | Under-rated: silent failed backups. Skip vanished files. |
| 15 (L) | `reset_library` only closes some DB connections | DEFER | Reset is a rare deliberate act | Partial reset on Windows | Rare. |
| 16 (L) | Six read-then-write helpers can lose a concurrent update | SKIP | Two writes to the same key in milliseconds | One tag or sample lost | Hostile timing. |
| 17 (L) | Default profile can be created twice | SKIP | Two processes on first start | Duplicate "Me" profile | Rare and harmless. |
| 18 (L) | Snapshot pruning by clock time | CHEAP-WIN | Clock stepping back | A snapshot pruned | Order by id. |
| 19 (L) | Full sync has no short-list guard | SKIP | Reviewer: all callers are fine | None today | Defence in depth only. |
| 20 (L) | Leftovers from killed backups are never swept | CHEAP-WIN | After a killed backup | Disk slowly fills | Add two prefixes to the sweep. |
| 21 (L) | Disk-full during restore reported as a bad backup | CHEAP-WIN | Restore on a near-full disk | Wrong advice | Better message. |
| 22 (L) | `sources.db` grows forever and stores full URLs | DEFER | Grows slowly | Larger backups | Not urgent at this scale. |
| 23 (L) | Locks held across file copies | DEFER | During restores and folder moves | Backup settings page hangs | Rare actions. |
| 24 (L) | Storage cleanup over-reports a symlinked folder | SKIP | Owner-made symlinks only | Wrong "freed" number | Rare setup. |

### #1094 Core, segment splitting and speech recognition (25: 6 H, 13 M, 6 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| 1 (H) | Audio slices seek on the output side, so work grows with the square of file length | CHEAP-WIN | Qwen3-ASR runs on long files | Slow, or a timeout late in the file | One-line move of `-ss`; also speeds normal files. |
| 2 (H) | Qwen3-ASR decodes the whole file into memory | DEFER | Only for multi-hour files (about 230 MB per hour) | Out of memory on 6-hour files | Audio dramas are short. #891 rewrites this. |
| 3 (H) | Hard-sub OCR writes every frame as a full-size PNG | SHOULD-FIX | Every hard-sub run on a full episode | 5-10 GB per 45-minute 1080p episode; disk can fill | Under-rated for normal lengths. Verified `fps=1` PNGs. Crop or scale in the ffmpeg filter. |
| 4 (H) | A blank line inside English loses the Chinese line on SRT re-import | CHEAP-WIN | Rare: English rarely contains blank lines | One line's Chinese lost on re-import | Verified; copy the VTT collapse. |
| 5 (H) | ffmpeg errors put full file paths into job errors | CHEAP-WIN | Each ffmpeg failure | Household members see folder names | Harmless here; tidy when touching. |
| 6 (H) | Qwen3-ASR stops at 256 tokens | CHEAP-WIN | Long segments in sentence-split mode | Tail of a long line silently lost | Raise the cap. |
| 7 (M) | An empty Qwen result drops a line Whisper heard | SHOULD-FIX | Quiet spans, each Qwen run | A line silently disappears | Keep Whisper's text. Re-check after #891. |
| 8 (M) | GPU fallback covers model load only | DEFER | Out of GPU memory mid-run | Run fails at 80% | Rare with the headroom checks. |
| 9 (M) | Hallucination filter misses punctuation variants | CHEAP-WIN | Whisper loops on silence are common | Repeated "谢谢" lines kept | Compare on letters only. |
| 10 (M) | Merging short lines joins Korean or English with no space; merges sound-effect cues | SHOULD-FIX | Every Korean title that uses merge | Words glued together | Korean is a supported language. Cheap. |
| 11 (M) | Novel chunking ignores `.!?` | CHEAP-WIN | Korean or English novels | Oversized chunks | Small. |
| 12 (M) | Uploaded novel context decoded as UTF-8 with errors ignored | SHOULD-FIX | Common: Chinese web-novel .txt files are often GBK | Context silently becomes garbage | Under-rated. Try `utf-8-sig`, `gb18030`, then fail loudly. |
| 13 (M) | Transcript alignment is super-linear | DEFER | Only multi-hour transcripts | Minutes of CPU | About 15 s for a 1-hour file. |
| 14 (M) | Model "cached" check matches by substring | CHEAP-WIN | Owners with turbo and non-turbo models | Missing download warning | In #1126. |
| 15 (M) | Model downloads not pinned; no disk check | DEFER | Upstream model changes | Output changes; disk fills | Pinning is a product decision. |
| 16 (M) | `HF_TOKEN` put into the environment for the whole process | CHEAP-WIN | Token changed in Settings | Old token used until restart | In #1126. |
| 17 (M) | Broken GPU model stays cached after an inference error | DEFER | After a GPU error | Repeated failures until restart | Rare. |
| 18 (M) | Audio extraction can't be cancelled | SHOULD-FIX | Monthly: people cancel after picking the wrong video | ffmpeg keeps burning CPU for hours | `run_cancellable` already exists. |
| 19 (M) | Dictionary has no Traditional Chinese index | CHEAP-WIN | Traditional-script titles | Word lookup finds nothing | Small. |
| 20 (L) | NaN times from Groq crash exports | DEFER | Groq would have to send NaN | Export fails | Unlikely. |
| 21 (L) | Groq response shape not validated | DEFER | Groq API change | Raw error | Unlikely. |
| 22 (L) | `clamp_overlaps` zeroes a line after manual retiming out of order | CHEAP-WIN | Manual retimes | A line vanishes from export | Small. |
| 23 (L) | ASS escaping misses a bare `\r` | CHEAP-WIN | Rare text | Broken line in players | Small. |
| 24 (L) | Hard-sub timestamps off by up to half an interval; a corrupt frame crashes | CHEAP-WIN | Each run | Cues slightly early; rare crash | Skip unreadable frames. |
| 25 (L) | PaddleOCR cache created without a lock | DEFER | Two OCR jobs at once | Double GPU memory | Same as #1119 M3. |

### #1096 Translation engines and bulk translate (21: 5 H, 12 M, 4 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | Enforced glossary terms corrupt English and compound on every run | MUST-FIX | Every run with variant notes | Every line of the title damaged | Fixed #1125; case bug fix in #1152. Still do "only lines this run wrote". |
| H2 | A reply cut off by the output limit blanks the whole batch after two paid calls | MUST-FIX | Monthly on novels with long lines | 30 lines blank; paid twice | Top-10 item 7. Raise `max_tokens`, keep complete pairs. |
| H3 | Cost estimate leaves out the instructions re-sent with every batch | SHOULD-FIX | Every bulk run | Bill 2-4x the estimate | Real but overstated: "over 10x" is tokens, and caching now cuts the money gap. Cheap formula fix. |
| H4 | An interrupted bulk apply logs usage twice and submits the next stage twice | DEFER | Needs a DB lock or a kill at an exact moment | One duplicate (half-price) batch | Rare. If the launched session fixes it, keep it to one CAS claim. |
| H5 | An off-peak DeepSeek run can't be cancelled and skips glossary, flags and version | SHOULD-FIX | Every off-peak DeepSeek run, if that feature is used | Glossary and flags never applied; spend can't be stopped | In session `fix-bulk-translate-review`. |
| M6 | A failed attempt's spend isn't counted toward the cap | DEFER | 429 storms | Cap slightly undercounts | Small sums. |
| M7 | Reflect retries re-bill passes that succeeded | DEFER | 429 storms during Reflect | Some double billing | Rare. |
| M8 | Backoff sleeps after the last try, ignores `Retry-After` | CHEAP-WIN | Each rate-limit failure | 32 s wasted | Small. |
| M9 | JSON extraction takes the first value even inside prose | SHOULD-FIX | Common with chatty local models | Batch blank after a retry | Cheap: pick the first dict whose keys match. |
| M10 | Gemini's unknown finish reasons crash | CHEAP-WIN | Gemini safety or recitation stops | Raw `KeyError` stored | Small. |
| M11 | Prompt caching set on a block that changes every batch | SHOULD-FIX | Every Claude run with a long novel reference | Paid cache-write price with no reads | Fixed #1125. |
| M12 | A failed next-stage submit strands a bulk Reflect pipeline | DEFER | A network blip at submit time | Paid drafts never written | Rare. |
| M13 | Bulk Reflect sends a different prompt from live Reflect | SHOULD-FIX | Every bulk Reflect run | Missing novel reference and pronoun handling | Quality every run. In session `fix-bulk-translate-review`. |
| M14 | Missing request keys counted as success | CHEAP-WIN | Provider response-shape change | Lines blank with no warning | Small check after the loop. |
| M15 | A 5xx outage never switches to the fallback engine | CHEAP-WIN | Provider outages | Batch fails instead of switching | Small. |
| M16 | Quick translate is uncapped and unlogged | CHEAP-WIN | Owner or opted-in member pasting large text | Spend missing from history and cap | Same as #1106 M2. |
| M17 | 他/她 rewritten in Japanese lines of a mixed title | DEFER | Mixed zh/ja titles | A Japanese word mangled | Rare content. |
| 18 (L) | One bad flag item crashes the flag job | CHEAP-WIN | Model returns a bad id | Flag job fails | Wrap the conversion. |
| 19 (L) | Gemini free-tier pacing counted per engine object | DEFER | Free-tier users | Google's 429 instead of our limit | Google enforces it anyway. |
| 20 (L) | A newline in source text can look like another numbered line | DEFER | Scraped text with embedded numbers | Model confusion | Parser keeps only expected ids. |
| 21 (L) | DeepSeek reply checks weaker than OpenAI's | CHEAP-WIN | Empty choices or a filter stop | Raw `IndexError` | Small. |

### #1097 Sources, fetch ladder and URL guard (25: 7 H, 9 M, 9 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | yt-dlp downloads have no address guard (SSRF) | DEFER | Needs a page built to attack Baihe, pasted by a trusted member | PC-only pages fetched as "media" | Not-to-do item 2. |
| H2 | The total deadline is only checked between 64 KB chunks | DEFER | Only a server that drips bytes on purpose | One thread held | Real networks stall completely, which the socket timeout catches. In #1132. |
| H3 | Tag-stripping regex is quadratic on unclosed `<script` | SKIP | Needs thousands of unclosed script tags | A thread pinned | Overstated: normal and truncated pages are fast. Counted as wrong. |
| H4 | manhuagui packed-script regex backtracks catastrophically | SHOULD-FIX | When manhuagui changes its page layout | Chapter fetch hangs | A layout change is an accident, not an attack. In #1134. |
| H5 | LZString decoder has no output cap | DEFER | Needs a malicious payload | Out of memory | Not-to-do item 3. |
| H6 | kuaikan `Array(n)` allocates the site's number | DEFER | Needs a malicious page | Out of memory | Cap is trivial if #1134 already has it. |
| H7 | Text-extraction heuristic is quadratic on nested markup | DEFER | Very deep markup | Pasted preview takes seconds | In #1132. |
| M8 | A system proxy turns off address pinning | SKIP | Needs a system proxy plus DNS rebinding | Proxy reaches the LAN | Attacker-only. |
| M9 | ffmpeg inside yt-dlp has no protocol whitelist | SKIP | Needs a malicious manifest | LAN fetch | Attacker-only. |
| M10 | bilibili adapter's yt-dlp outside the guard | SKIP | Needs a malicious redirect | LAN fetch | Attacker-only. |
| M11 | Manifest URL regex has an unbounded lazy run | DEFER | Pathological scripts | Seconds to minutes | In #1132. |
| M12 | `_read_varint` has no length limit | DEFER | Malformed page | Slow parse | Rare. In #1134. |
| M13 | ranobes `pages_count` uncapped | DEFER | Malformed page | Endless paced fetches | #1134's cap broke real lists over 200 pages. Make the cap far above real sizes, or drop it. |
| M14 | No per-page image memory budget | DEFER | 300 huge images on one page | Memory spike | #1132's budget silently drops GIF and text pages. Fix that before merging. |
| M15 | Signed chapter URL sent as Referer to other image hosts | SKIP | Pasted signed links | A short-lived token seen by the site's CDN | Low value. |
| M16 | Raw exception text, IPs and URLs in source health records | SKIP | Every fetch error | Members see public site hosts | Harmless here. |
| 17 (L) | Two non-public IPv6 forms pass the guard | SKIP | Needs crafted DNS | LAN fetch | Attacker-only. |
| 18 (L) | Session cookie jar lives for the whole process | DEFER | Always | Site cookies re-sent | Mostly harmless. |
| 19 (L) | Cross-origin redirects strip only two credential headers | SKIP | Adapter-added token headers | Token follows a redirect | No adapter does this today. |
| 20 (L) | yt-dlp writes back to the user's cookies.txt | DEFER | Every download with cookies | Site cookies added to the file | Normal yt-dlp behaviour. |
| 21 (L) | URL-media downloads have no size cap; post-processing not space-checked | CHEAP-WIN | Very long videos | Disk fills to the 2 GB floor | Same as #1116 M10. |
| 22 (L) | Research source links not cleaned | CHEAP-WIN | Each research run | Query strings shown | Use `safe_url`. |
| 23 (L) | "Analyze media" runs ffprobe with no input whitelist | SKIP | Needs a crafted upload | LAN fetch | Attacker-only. |
| 24 (L) | Source tables grow without bound | DEFER | Slowly | Bigger DB | Same as #1088 L22. |
| 25 (L) | Smaller parser loop bounds (lightnovel_fun, kuaikan) | DEFER | Malformed pages | Slow parse | Rare. |

### #1098 Diagnostics, installs, upgrade and installer (23: 5 H, 11 M, 7 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | "Install now" and "Upgrade" skip the safety plan | MUST-FIX | A few times a year | Python half-replaced; Baihe won't start | Client half fixed #1091. Server half in #1131 (UI must send the risk acceptance). Upgrade waits for #891. |
| H2 | An interrupted queued install can't be restored | MUST-FIX | Once a year (closed console, power cut) | Baihe won't start; numpy or torch missing | Half fixed #1077. Restore half in #1083. |
| H3 | Under the Windows boot service, installs can't write site-packages | DEFER | Only if the owner runs Baihe as a service | Every install fails with a permission error | Fails cleanly, no damage. Opus found the `os.access` check useless on Windows: drop it, show a plain message. Ask the owner if the service is used. |
| H4 | Diagnostics imports PyTorch in the server, locking files GPU setup must replace | MUST-FIX | Once: the first GPU PyTorch setup, which Diagnostics itself recommends | Broken torch install | In #1126; goal not met yet. Keep this part. |
| H5 | "Safe to share" support report keeps signed-URL tokens | CHEAP-WIN | When the owner shares a report | A short-lived CDN token shared | Strip URL query strings. Nothing more elaborate. |
| M1 | The plan confirmed is not re-checked at restart | DEFER | Queue on Friday, a dependency changes by Monday | Unconfirmed downgrade | Rare. |
| M2 | Watchdog budget includes the lock wait | DEFER | Two starts queued behind each other | pip killed mid-run | Rare; in #1083. |
| M3 | Disk-full during apply loses the outcome | DEFER | pip fills the disk | Wrong message | Rare. |
| M4 | Immediate installs have no restore; Cancel can half-replace a package | SHOULD-FIX | Cancelling a running install | Package missing | In #1131. |
| M5 | Cached install plan can be 10 minutes stale | CHEAP-WIN | Preview, then an OCR job, then queue | In-use file replaced | Pass `fresh=True`. |
| M6 | A cut-off Deno download blocks later Deno installs | DEFER | Only a power cut mid-copy | Deno must be deleted by hand | Overstated: code already creates the file exclusively and cleans up on error. Counted as wrong. |
| M7 | "Test first" can't work on an installed copy | CHEAP-WIN | Every click on an installed copy | Always "incomplete" | Hide the button. In #1131. |
| M8 | Profile folder names that differ from the user name survive redaction | SKIP | Renamed accounts | Owner's folder name in a report | Household knows the owner's name. |
| M9 | Maintenance assistant `git_diff` with a folder skips the deny-list | SKIP | Needs prompt injection in the owner's own assistant | Tracked files sent to a cloud model | Those files are already public on GitHub. |
| M10 | When git fails, "tracked files only" fails open | DEFER | Source checkout with git "dubious ownership" | Untracked local notes readable by the assistant | Owner-only feature; rare setup. |
| M11 | Disk scan follows symlinks to network shares | SKIP | Owner-made symlink to a network share | Scan stalls | Owner's own setup. |
| L1 | Launcher's apply call is not a tree kill | DEFER | Watchdog itself hangs | pip runs alongside the server | Rare. |
| L2 | Installer upgrade never runs `pip check` | CHEAP-WIN | After app upgrades | A feature breaks at first use | Log-only check. |
| L3 | App update checked only against a same-release hash; installer unsigned | SKIP | Needs a compromised GitHub release | Code execution | Design decision; signing costs money and time. |
| L4 | Short user names corrupt reports; `getuser()` raising gives a 500 | CHEAP-WIN | Service accounts | Diagnostics 500 | Wrap one call. |
| L5 | A new package reads as "not installed" until restart | CHEAP-WIN | After every install | Confusing status | One line. |
| L6 | Assistant git calls bypass `lib.proc` | DEFER | git-lfs hangs | Assistant 409 until restart | Rare. |
| L7 | Restoring a cleared title folder blocked by an empty recreated folder | CHEAP-WIN | Clear to Trash, then open the title | Restore refused | Small. |

### #1101 Frontend: library, settings, diagnostics, jobs, sign-in (18: 3 H, 10 M, 5 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | "New title" offered to members, who always get "Not allowed" | SHOULD-FIX | Weekly for members | Frustration after filling a form | Fixed #1129. |
| H2 | Bulk status, lists and rename offered to members | SHOULD-FIX | Members using Library tools | Bare 403 | Fixed #1129. |
| H3 | An older jobs list overwrites a newer "finished" event | SHOULD-FIX | Weekly | Diagnostics buttons stay disabled | Fixed #1130. |
| M4 | Settings saves overwrite each other | DEFER | Rapid clicks on the same setting | Screen shows a stale value | Server keeps the right value; reload fixes. |
| M5 | A failed settings load shows an admin the member view | CHEAP-WIN | API slow at load | Confusing page | Waits for #1071. |
| M6 | A failed "who am I" shows admin menus and hides Sign out | SHOULD-FIX | Phones on weak signal | Wrong menus | Fixed #1130. Follow-up in `sharing.ts`. |
| M7 | A 401 threw away unsaved drafts | SHOULD-FIX | Session expiry | Typed text lost | Fixed #1130. |
| M8 | Diagnostics page has no permission gate | CHEAP-WIN | A member after a crash | Several 403s | Fixed #1129. |
| M9 | Diagnostics error banner outlives a good recheck | CHEAP-WIN | After a timeout | Stale red banner | Fixed #1129. |
| M10 | Preflight key form closes before its warning | CHEAP-WIN | Key overridden by the environment | No explanation | Fixed #1130. |
| M11 | Quick translate dead ends for members | CHEAP-WIN | Members without paid engines | 403 | Fixed #1129. |
| M12 | Library fetches every title with no paging | DEFER | Libraries of thousands of titles | Slow phone list | A personal library is hundreds of titles. |
| M13 | Load errors swallowed (Continue, series, presets) | CHEAP-WIN | Server errors | Shelf silently empty | Fixed #1129. |
| 14 (L) | Every key-write 403 says "turn on key writes in start.bat" | CHEAP-WIN | Stale CSRF token | Wrong advice | Small. |
| 15 (L) | Browser offers to save API keys as passwords | CHEAP-WIN | Each key save | Key stored in the owner's own password manager | One attribute; low risk either way. |
| 16 (L) | Spending card has two primary buttons | CHEAP-WIN | Always | Clutter | Small. |
| 17 (L) | "Can't reach Baihe" badge checked once | CHEAP-WIN | API restarts | Stale badge | Small. |
| 18 (L) | Quick translate shares one error state | CHEAP-WIN | Mixed failures | Misleading banner | Small. |

### #1102 Frontend: workspace stages (25: 3 H, 18 M, 4 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | A stale instructions editor wipes a series' instructions | MUST-FIX | Assigning a title to a series, then saving | Series instructions erased | Fixed #1141. |
| H2 | Double "Attach" stores the novel twice | SHOULD-FIX | Slow uploads invite a second click | Duplicate novel text | Fixed #1141. |
| H3 | Leaving Review loses an edit if its background save fails | MUST-FIX | Flaky phone networks | Typed edit lost | Fixed #1141. |
| M4 | Run-status polling gives up but still says "running" | SHOULD-FIX | Flaky networks | Fake "Testing…" until reload | Fixed #1127. |
| M5 | Double click starts Translate or Transcribe twice | CHEAP-WIN | Double clicks | Two alerts; a second upload | Server refuses the duplicate. Waits #883 and #891. |
| M6 | Export, Dub and Narration double submit; export results race | CHEAP-WIN | Double clicks | Wrong format shown | Export half fixed #1127. Dub is frozen. |
| M7 | Source upload shows no progress state | SHOULD-FIX | Every large upload | Second upload of 2 GB | Fixed #1127. |
| M8 | Cost estimate still says "free" after switching to a paid engine | SHOULD-FIX | Switching engines after estimating | Unexpected bill | Money truthfulness. Waits #883. |
| M9 | A failed cancel of a paid extraction is swallowed | CHEAP-WIN | Cancel fails | Paid scan keeps running unseen | Fixed #1127. |
| M10 | Renaming a speaker wipes other rows' unsaved edits | SHOULD-FIX | Editing several characters | Edits lost | Fixed #1141. |
| M11 | Rename undo dropped on a temporary error | CHEAP-WIN | Network blip | Rename can't be undone | Fixed #1141. |
| M12 | Fast language-pack toggles lose a change | CHEAP-WIN | Quick toggling | Pack silently off | Fixed #1127. |
| M13 | Subtitle import dead end after a failed preview | CHEAP-WIN | Wrong encoding guess | Must reload | Fixed #1127. |
| M14 | "Use this", Restore and TM Accept ignore an open edit | SHOULD-FIX | Editing while accepting a suggestion | Reload discards typing | Fixed #1141. |
| M15 | One line save triggers 15-20 requests | DEFER | Every save | Slower on huge titles | Fine at this scale. |
| M16 | Re-transcribe and Compare results lost after leaving the stage | CHEAP-WIN | Leaving mid-run | Paid or GPU result not shown | Reuse `useReattachJob`. |
| M17 | lightnovel-crawler output shown unredacted | CHEAP-WIN | Crawler errors | Owner's paths shown | Harmless here. |
| M18 | Narration settings stale after a run | SKIP | Dub area | Stale message | Dub is frozen. |
| M19 | "Show more chapters" can duplicate rows | CHEAP-WIN | Double click | Duplicate rows | Small. |
| M20 | Jellyfin "ASS (styled)" ignores the style form | CHEAP-WIN | Every styled send, if used | Default style sent | Small. |
| M21 | Job pill drops focus; result not announced to screen readers | DEFER | Screen-reader users | Missed status | No known screen-reader users. |
| 22 (L) | A deliberate cancel flashes red "Failed" | CHEAP-WIN | Each cancel | Alarming colour | Small. |
| 23 (L) | Raw ids and "drama" reach users | CHEAP-WIN | Always | Odd wording | #1115 sweep. |
| 24 (L) | More than one primary button per view | CHEAP-WIN | Always | Clutter | Small. |
| 25 (L) | Errors swallowed; one 404 on every Transcribe visit | CHEAP-WIN | Every visit | Log noise | Small. |

### #1103 Frontend: reader, comic, live, benchmark, assistant (25: 3 H, 9 M, 13 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | Benchmark estimate stays valid after the case count changes | SHOULD-FIX | Importing a larger set after estimating | A run costing ~100x the shown estimate | Money. Waits #910. |
| H2 | Assistant question can't be cancelled | SHOULD-FIX | Stuck local model | Chat locked up to 90 min | Fixed #1128. |
| H3 | Vertical comic reading keeps every page decoded | MUST-FIX | Weekly for phone webtoon readers | Phone tab crashes mid-chapter | Top-10 item 5. In session `fix-comic-reader-review`. |
| M4 | A failed Reader page shows the previous page's text | SHOULD-FIX | Flaky phone networks | Wrong text under the new page number | In the same session. |
| M5 | Pages without sizes shift the layout and corrupt Continue | SHOULD-FIX | Saved webtoons | Resume lands on the wrong page | Fixed #1128. |
| M6 | Closing the Translate panel drops the Scanlate job's completion | CHEAP-WIN | Closing the panel to keep reading | New pages appear only after reload | In the same session. |
| M7 | Live polls forever on 403 or 500 | CHEAP-WIN | Revoked access | Silent endless polling | Fixed #1128. |
| M8 | A lost Benchmark job connection disappears silently | DEFER | Phone network loss in the lab | Progress hidden | Owner tool; waits #910. |
| M9 | An older Benchmark runs response overwrites a newer one | DEFER | Fast filter clicks | Wrong list | Waits #910. |
| M10 | Re-evaluation reload errors hidden | CHEAP-WIN | Failed reload | Stale card | Fixed #1128. |
| M11 | Peeking at another chapter overwrites "Continue" | SHOULD-FIX | Weekly | Reading place lost | Fixed #1128. |
| M12 | "Redo page N" replaces edited boxes with no confirm | SHOULD-FIX | One mis-tap | Hand edits lost | Fixed #1128. |
| 13 (L) | Progress lost if the reader leaves within a second | CHEAP-WIN | Often | Resume one page early | Small. |
| 14 (L) | Position within a Reader page not kept | DEFER | Large pages on phones | Starts at the top | Feature work. |
| 15 (L) | Comic "Go to" uses a different numbering from the pager | CHEAP-WIN | Hidden pages on | Lands on the wrong page | Small. |
| 16 (L) | Comic errors never cleared; failed title load never ends | CHEAP-WIN | After a blip | Stale banner; "Loading…" forever | Small. |
| 17 (L) | A failed text-box request is never retried | CHEAP-WIN | Network blip | Text missing until reload | Small. |
| 18, 19, 20 (L) | Failures swallowed on Live sessions, New chapters and Discover engines | CHEAP-WIN | Server errors | Misleading empty states | Show the error. |
| 21 (L) | A slow reload can undo a schedule save | CHEAP-WIN | Rare | Form shows old values | Folded into #1128. |
| 22 (L) | Two progress bars during a re-evaluation | CHEAP-WIN | Each re-evaluation | Clutter | Small. |
| 23 (L) | 18px checkboxes on tablets | DEFER | Tablet users | Hard to tap | Few tablets. |
| 24 (L) | Opening a golden-set file can fail silently or freeze a phone | DEFER | Huge files on a phone | Frozen tab | Owner tool, used on the PC. |
| 25 (L) | Changelog shows the old result under a new error | CHEAP-WIN | Rare | Confusing | Small. |

### #1104 Frontend test coverage (17: 4 H, 7 M, 6 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | Vitest never runs `.test.tsx` files (13 tests) | SHOULD-FIX | Always | Regressions go unseen | One-line config. In #1139. |
| H2 | Fallback-engine test always skipped | CHEAP-WIN | Always | A rule untested | In #1139. |
| H3 | No spec runs a workspace page as a limited member | SHOULD-FIX | Always | Member 403s go unseen (the main remote-user annoyance) | In #1139. |
| H4 | Hand-written test stubs can drift from API shapes | DEFER | Over time | Tests pass on stale shapes | Not-to-do item 9. |
| M5 | Specs mutate the shared test library and depend on order | CHEAP-WIN | When files are renamed | Flaky CI | In #1139. |
| M6 | "Nothing happened" checks rely on short sleeps | CHEAP-WIN | Loaded CI | Tests that can't fail | In #1139. |
| M7 | Discover catalogue tests always skipped | CHEAP-WIN | Always | Hidden feature untested | In #1139. |
| M8 | About 35 s of fixed sleeps | DEFER | Every run | Slower CI | Not user-facing. |
| M9 | Shards unbalanced (10 vs 7 minutes) | DEFER | Every run | Slower CI | Not user-facing. |
| M10 | A skipped test never fails CI | DEFER | Always | Silent coverage loss | Not user-facing. |
| M11 | No phone specs for Admin, Reader, Dub | DEFER | Always | Layout regressions unseen | Owner's phone check covers it. |
| 12, 13, 14, 15, 16 (L) | Node test environment, no coverage report, screenshot tests that skip, ambiguous `first()`, narrow tab-order tests | DEFER | n/a | Weaker tests | Test hygiene; after the freeze. |
| 17 (L) | Testing doc names a spec that doesn't exist | CHEAP-WIN | Agents read it | Confusion | Fixed #1122. |

### #1105 Docs versus code (40: 11 H, 21 M, 8 L)

Docs drift misleads agents and users, but it never corrupts data. The audit rated 11 of these HIGH; at this scale none is a MUST-FIX.

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1, H2, H3 | STATUS.md, background-jobs.md and CLAUDE.md say the job store is unmerged or the dict decides | SHOULD-FIX | Every agent session | Agents change the wrong layer | One docs pass after the next merge wave. |
| H4, H5 | content-sources says site terms block imports; manual check expects a refusal | SHOULD-FIX | Owner reading the doc | Wrong expectations | Fixed #1122. |
| H6 | engine-backends says every provider uses streaming reads | SHOULD-FIX | Agents adding an engine | New engine built the old way | Docs pass. |
| H7, H8, H9, H10 | Four wrong menu paths for users | SHOULD-FIX | Owner and members following the guide | Can't find a setting | Fixed #1122. |
| H11 | Docs tell users to run a CLI command that doesn't exist | CHEAP-WIN | Rare | Error message | Waits #891. |
| M12 | Fourth route kind `authenticated()` missing from the rules | CHEAP-WIN | Agents adding routes | Confusion | Docs pass. |
| M13 | Timeout rule doesn't name `lib/http.py` | CHEAP-WIN | Agents adding HTTP calls | Raw `requests` added | Docs pass. |
| M14, M15, M16, M17, M18 | content-sources site table and pacing details stale | CHEAP-WIN | Owner reading it | Minor confusion | Fixed #1122. |
| M19 | Extension CORS wording | CHEAP-WIN | Rare | Minor confusion | Waits #1087. |
| M20, M21, M22 | background-jobs.md names and error codes stale | CHEAP-WIN | Agents | Minor confusion | Docs pass. |
| M23 | Backoff and no-retry list incomplete | CHEAP-WIN | Agents | Minor confusion | Docs pass. |
| M24 | "First four" guard files (it is five) | CHEAP-WIN | Agents | Minor | Fixed #1122. |
| M25, M26 | api-and-services and FILE_ORGANIZATION lists incomplete | CHEAP-WIN | Agents | Minor | Docs pass. |
| M27, M28 | Audit-log location and "context before" default | CHEAP-WIN | Owner | Minor | Fixed #1122. |
| M29 | STATUS says LocalAgreement streaming works | CHEAP-WIN | Owner planning | Wrong expectation | Backlog row fixed #1122; STATUS waits. |
| M30, M31 | Backlog rows already done or with wrong paths | CHEAP-WIN | Agents | Wasted work | Fixed #1122. |
| M32 | STATUS "checked through #1062" | CHEAP-WIN | Owner | Minor | Docs pass. |
| 33, 34, 35, 36, 37, 38, 39 (L) | Stale names, columns, labels and token estimates across docs | CHEAP-WIN | Agents and owner | Minor | Docs pass; 37 and 39 fixed #1122. |
| 40 (L) | `OPTIONAL_DEPENDENCIES` entries wrong for starlette, pydantic, openai, requests | CHEAP-WIN | Diagnostics page | Wrong labels | Small. |

### #1106 Remaining routers and schemas (25: 1 H, 9 M, 15 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | Find-and-replace regex guard misses nested groups (ReDoS) | SHOULD-FIX | Needs a nested-group pattern; by accident rarely, on purpose only by a hostile member | Whole server frozen until restart | Real and cheap (in session U1), but over-rated as the top priority. |
| M2 | `POST /api/translate` ignores the spend cap and logs no usage | CHEAP-WIN | Owner or an opted-in member pasting large text | Spend missing from history | Log usage and check the cap. |
| M3 | Style learning reads every member's private edit samples | SHOULD-FIX | When anyone uses "learn series style" | Another member's private text in a summary | Private data to the wrong person. Verified: `list_edit_samples()` has no filter. One filter. |
| M4 | `jobs.start` alone can overwrite lines | SKIP | Needs a member with `jobs.start` but not `lines.edit`; defaults include both | Lines replaced | Prevented by defaults. #1135 should not grow further. |
| M5 | Discover catalogue returns full source URLs to members | CHEAP-WIN | Pasted signed links | Token seen by household | Fixed #1125. |
| M6 | Autofill and import-suggestion run without the AI slot limit | CHEAP-WIN | Parallel requests | Thread pool tied up | Fixed #1125. |
| M7 | Reader page serves line text under `library.read` | SKIP | Needs a member without `lines.read` | Transcript readable | Contradicts a recorded owner decision. Counted as wrong. |
| M8 | A job can start while an upload is still copying | DEFER | Owner replaces audio while a member starts a transcribe | Wrong audio transcribed | Rare timing. |
| M9 | One account can use up the global bug-report cap | SKIP | A known member filing 100 reports | Owner can't file reports | Fixed #1125, but out of proportion. |
| M10 | "Reset library" erases accounts and the audit log silently | CHEAP-WIN | Owner resets | Members must be re-invited | Say so in the confirm text. |
| 11 (L) | Admin routes return the raw drama row | SKIP | PC-only routes | Owner sees own data | PC only. |
| 12 (L) | Autofill echoes the pasted URL | SKIP | Owner pastes | Owner sees own URL | Harmless. |
| 13 (L) | Engine failure messages carry LAN hosts | SKIP | Ollama errors | Household sees a LAN address | Harmless here. |
| 14 (L) | Seven upload routes spool to disk before the size cap | DEFER | Huge uploads | Temporary double disk use | Known users. |
| 15 (L) | Concurrent EPUB downloads share one path | DEFER | Same second, two tabs | Wrong language file | Same as #1116 M5. |
| 16 (L) | Find-and-replace apply has no compare-and-set | DEFER | An edit in the same instant | One edit lost | Short synchronous window. |
| 17 (L) | Reader personal notes are one shared row per title | DEFER | Members writing notes | Owner's notes replaced | Owner decision first. |
| 18 (L) | Package upgrade runs pip for up to 15 min in the request | DEFER | Each upgrade | Client timeout while pip continues | Waits #891. |
| 19 (L) | Update install can launch Setup twice | DEFER | Double click on update | Two installers | Rare. |
| 20 (L) | Pending-install queue loses a package on concurrent calls | SKIP | Two queue calls at once | One package dropped | Hostile timing. |
| 21 (L) | Household streams can take every live-update slot | SKIP | Needs 6+ remote streams | PC falls back to polling | At most 3 remote users. |
| 22 (L) | Some async routes rewrite `.env` on the event loop | DEFER | Key saves | Brief stall | Rare. |
| 23 (L) | Source notifications ignore the caller | DEFER | Shared households | Sees others' series notices | Owner decision. |
| 24 (L) | Novel status polls re-read up to 32 MB | DEFER | Each poll | CPU and disk churn | Fine at this scale. |
| 25 (L) | Saved-comic pages read fully into memory | DEFER | Large pages | Memory spikes | Fine at this scale. |

### #1116 Transcribe, translate, review and export services (16: 2 H, 8 M, 6 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | "Translate all lines" overwrites English typed during the run | MUST-FIX | Monthly | Hand-typed lines replaced by model text | Verified: plain `save_lines(fields=("en",))` unless `own_lines_only`. The safe save exists. Top-10 item 1. |
| H2 | "Fix flagged lines" overwrites edits made while it runs | MUST-FIX | Monthly | Corrections and cleared flags overwritten | Verified at `workspace_job_service.py:629`. Top-10 item 1. |
| M3 | Exports are not atomic on Windows (`shutil.move` copies over the old file) | SHOULD-FIX | Re-exports on Windows | Truncated download; old export destroyed on disk-full | `storage.move_into_place` already exists. |
| M4 | Dub and the exports that read its track don't exclude each other | DEFER | Clicking "Generate dub" during an export | Corrupt video | Dub is frozen; rare. |
| M5 | Two EPUB exports at once can return the wrong language | DEFER | Two tabs in the same second | Wrong book | Rare. |
| M6 | Three "use new source text" paths treat old English three ways | SHOULD-FIX | Weekly during review | Old English exported beside new Chinese | Owner picks one rule (clear, or keep and flag). |
| M7 | Compare-apply docstring claims a check the code lacks | CHEAP-WIN | With M6 | Same as M6 | Do with M6. |
| M8 | Stored novel-reference file name can point outside the title folder | DEFER | Needs a crafted or foreign restored row | A file sent to a cloud AI | Not-to-do item 6. |
| M9 | Fix-flagged and compare ignore the title's locale and style preset | SHOULD-FIX | Titles with a non-default locale | Mixed spelling in one episode | CLI-app parity rule; reuse the last run settings. |
| M10 | yt-dlp download has no byte cap, though the docstring says it does | CHEAP-WIN | Very long videos | Disk fills to the 2 GB floor | Owner decision: cap it or fix the docstring. |
| 11 (L) | Density flags computed from stale English | CHEAP-WIN | With H1 | Wrong flags | Do with H1. |
| 12 (L) | Synchronous flag actions unguarded | SKIP | Same-instant edit | One flag lost | Short window. |
| 13 (L) | Failure after the line save leaves the job in error | DEFER | Rare | Misleading error | Rare. |
| 14 (L) | All-whitespace transcription marks an empty title "aligned" | CHEAP-WIN | Silent audio | Misleading "done" | Small. |
| 15 (L) | URL-download temp folder not where the docstring says | DEFER | Library over two drives | Save fails | Rare setup. |
| 16 (L) | Force re-translate snapshots before the job starts | CHEAP-WIN | Repeated clicks | Undo points pushed out | Small. |

### #1118 Auth, server and core routers (11: 0 H, 2 M, 9 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| M1 | With sign-in off, any local TCP relay or second Windows account is the owner | DEFER | Needs a port forward or another Windows user on the owner's PC | Full owner rights | Not-to-do item 1. Do L3 and a docs line instead. |
| M2 | Library search returns line text under `library.read` | SKIP | Needs a member without `lines.read`; defaults include it | Transcript readable | Prevented by defaults. |
| L1 | Settings returns the Ollama URL to a remote admin (single-port setup) | SKIP | Single-port setup only | Admin sees a LAN address | Harmless. |
| L2 | Single-port setup logs sign-in codes in the access log | CHEAP-WIN | Each sign-in in that setup | A one-time code in a local log | One argument. |
| L3 | Proxy-header deny-list is incomplete | CHEAP-WIN | Some tunnels | Tunnel treated as local | Match header prefixes. The cheap part of M1. |
| L4 | Bulk status and tags skip the ownership check | SKIP | Unreachable today | None | Reviewer: defence in depth. |
| L5 | Key-write refusal has code `application_error` | CHEAP-WIN | Each refusal | Client can't tell it apart | Raise the right error. |
| L6 | Global error handler removes keys but not paths | CHEAP-WIN | Reviewer found no message carrying a path | None confirmed | Point it at the existing secrets-plus-paths function. |
| L7 | Any member who can see a shared title can cancel the owner's jobs | DEFER | A member cancelling by mistake | A paid run stopped | Documented decision; known people. |
| L8 | `GET /api/jobs` writes on every poll; list unbounded | DEFER | Every poll | Extra DB work | Same as #1086 L8. |
| L9 | PC-only routes have no body size bound | SKIP | Owner's own uploads | Memory spike | PC only. |

### #1119 Scanlate, comic, novel, page server, extension (25: 2 H, 10 M, 13 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | Bridge trusts the claimed image type and has no pixel cap | CHEAP-WIN | Needs a decompression-bomb image on a comic site | Out-of-memory crash | Real webtoon strips are far below the cap. Reuse the existing `check_image` in one call. |
| H2 | Scanlate "Redo" replaces translated boxes with blank ones when OCR finds no text | MUST-FIX | Whenever the OCR backend is missing or a page is sound effects only | Translations and hand edits wiped | Top-10 item 10. |
| M1 | Extension service worker gives the token to any sender | SKIP | Needs a hacked browser renderer | Bridge token exposed (local PC only) | Verified: no `externally_connectable`, so web pages can't message it. Counted as wrong. |
| M2 | Bridge calls have no timeout | CHEAP-WIN | First capture with a model download | "Stopped unexpectedly" while pages still save | AbortController. |
| M3 | OCR model caches used without the pipeline lock | DEFER | Bridge and novel OCR at the same moment | Double GPU memory | Rare overlap. In session S1. |
| M4 | Whole-page decodes per bubble, all under the lock | DEFER | Every run | Slower Scanlate | Speed only; typical pages are small. |
| M5 | A many-region page goes to the AI in one call | DEFER | Pages with 100+ bubbles | Page translation fails | Typical pages have 5-30 bubbles. S1's chunking is more than needed. |
| M6 | `clean_note` keeps URLs and query tokens | CHEAP-WIN | Model download errors | Short-lived token shown | Same fix as #1098 H5. |
| M7 | Third-party-page filter judged on URLs the extension makes up | SHOULD-FIX | Canvas-based readers mixed with image pages | Real pages silently skipped | Not reproduced; check in the owner's browser test. |
| M8 | `save_novel_text` has no lock or truncate-on-failure | DEFER | Disk fills mid-import | Chapter splits lost | Rare. |
| M9 | Raw-novel appends can be lost | DEFER | Two tabs appending at once | One paste lost | The double-click half fixed #1141. |
| M10 | Render uses a stale snapshot and keeps an old typeset image | DEFER | Bridge saves during a render | Outdated page exported | Rare overlap. |
| L1 | Page server logs raw request lines | SKIP | Malformed requests | Path in the local log | Local log. |
| L2 | Token file creation can race | DEFER | Two first requests at once | Extension 401 once | Rare. |
| L3 | `drama_id` not validated | SKIP | Our own extension sends it | Wrong error text | Own client. |
| L4 | Bridge doesn't apply photo orientation (EXIF) | CHEAP-WIN | Rotated JPEGs | Overlay misplaced | Same call as H1. |
| L5 | Rolling context unbounded and keyed too coarsely | DEFER | Capturing chapters out of order | Slightly worse context | Quality only. |
| L6 | `HF_TOKEN` written into the environment | CHEAP-WIN | Each model load | Old token kept | In session S1. |
| L7 | Page after a hidden page gets the hidden page's context | CHEAP-WIN | Hidden pages | Slightly worse context | Small. |
| L8 | Strip slicing silently stops at 201 slices | CHEAP-WIN | Very long thin strips | Tail of a strip untranslated | Raise a clear error. |
| L9 | A failed rollback can leave the next page hidden | DEFER | Rare | A page skipped | Rare. |
| L10 | Deleting a title mid-write recreates its folder | DEFER | Delete during a capture | Orphan folder | Rare. |
| L11 | Dead raw-novel guard never matches | CHEAP-WIN | n/a | None | Delete it. |
| L12 | Novel chapter titles not scrubbed | SKIP | Site headings | Odd characters | Harmless. |
| L13 | Novel multipart uploads uncapped at the route | SKIP | PC-only routes | Temp disk use | Owner only. |

### #1121 Python test coverage (29: 6 H, 13 M, 10 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | No contention test for the main jobs lock | DEFER | n/a (test gap) | A future lock bug unseen | Not-to-do item 5. |
| H2 | Errors in job threads are only warnings in pytest | SHOULD-FIX | Always | Real job crashes pass CI | One `pytest.ini` line, after #1073. |
| H3 | Cross-process GPU slot only tested single-threaded | DEFER | n/a | A future race unseen | One server. |
| H4 | CI skips the only Scanlate id-matching tests (no OpenCV) | CHEAP-WIN | Always | A "learned from real bugs" rule untested | Move one `importorskip`. In session S1. |
| H5 | Bulk error redaction untested | CHEAP-WIN | n/a | A key could reach a stored error unseen | In session T1. |
| H6 | Two permission tests accept any exception | CHEAP-WIN | n/a | A crash passes as a refusal | In session T1. |
| M7 | `fields=` scoping of saves barely tested | CHEAP-WIN | n/a | The "jobs write only their fields" rule unprotected | In session T1. |
| M8 | CLI/app glossary parity untested | CHEAP-WIN | n/a | Parity can break unseen | Waits for the CLI brief. |
| M9 | Optional-dependency registration scan covers two files | DEFER | n/a | Unregistered imports | Low value. |
| M10 | Three permission tests swallow setup errors | CHEAP-WIN | n/a | Tests prove nothing | Small. |
| M11 | `patch_post` test helper drops method and params | DEFER | n/a | Wrong verbs unseen | Low value. |
| M12 | Timeout guard accepts `timeout=None` | SKIP | n/a | None in practice | Nobody writes that. |
| M13 | An undeclared route is blocked only by a test | SKIP | The test runs on every PR | None | Already prevented. |
| M14 | Chapter-check claim has no contention test | SKIP | Two schedulers | Duplicate cycle | One server. |
| M15 | AI slot caps have no contention test | SKIP | n/a | Cap off by one under a race | Low value. |
| M16 | Two SSRF tests pass if the code crashes early | DEFER | n/a | Weaker tests | Attacker-path tests. |
| M17 | Real-browser SSRF tests always skip in CI | SKIP | n/a | Weaker tests | Attacker-path tests. |
| M18 | Seven tests need real DNS | CHEAP-WIN | When DNS hiccups | Flaky CI | CLAUDE.md says tests use no network. |
| M19 | Some job-test waits never check success | DEFER | n/a | Vacuous passes | Test hygiene. |
| 20, 21, 22 (L) | Timeout and ffmpeg guard tests can't retire yet; allow-list scope | DEFER | n/a | None | Not bugs. |
| 23 (L) | Two timing bounds too tight for CI | CHEAP-WIN | Loaded CI | Flaky failures | Remove before the freeze. |
| 24 (L) | Slow test setup | DEFER | Every run | Slower CI | Not user-facing. |
| 25, 26, 27 (L) | Global state leaks, an assert that can't fail, catch-all `pytest.raises` | CHEAP-WIN | n/a | Weaker tests | In session T1. |
| 28 (L) | Dead position-matching translate function kept alive by tests | CHEAP-WIN | n/a | Risk of re-use | Delete. In session S1. |
| 29 (L) | Emotion tag parser lacks a shuffled-id test | DEFER | n/a | Weaker tests | Low value. |

### #1123 CLI and headless parity (23: 3 H, 11 M, 9 L)

Whether these matter depends on whether the owner runs `cli.py` at all. The classes below assume occasional overnight CLI use.

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| H1 | CLI and app can't see each other's translation runs | DEFER | Needs CLI and app translating the same title at once | Double spend; last writer wins | Unlikely for one owner. |
| H2 | CLI-started jobs reuse the app's GPU slot name | DEFER | CLI and app transcribing the same title | Two Whisper runs on one card | Mostly covered by #1073. |
| H3 | `narrate-prep` on Ollama takes no GPU slot | DEFER | Overnight narrate-prep beside an app GPU job | Out of GPU memory | Rare overlap. |
| M4 | Unknown `--id` crashes five commands | CHEAP-WIN | Typos | Traceback | Small. |
| M5 | CLI GPU slot goes stale in long single steps | DEFER | CLI step over 10 min with app GPU work queued | Two GPU users | Rare overlap. |
| M6 | `translate` uses an environment key when none is saved | DEFER | Key only in the environment | Owner's own key billed | It is the owner's key. |
| M7 | `doctor` tests a different key | CHEAP-WIN | Each `doctor` run | Wrong verdict | Small. |
| M8 | Batch commands exit 0 when titles fail | CHEAP-WIN | Overnight scripts | Failures unnoticed | Small. |
| M9 | `run` translates after a failed align | CHEAP-WIN | Missing audio | Money spent on stale lines | Small. |
| M10 | `diarize` merges into lines read before the run | DEFER | Editing speakers in the app during a CLI diarize | Speaker edits overwritten | Rare overlap. |
| M11 | CJK titles crash the CLI when output is redirected on Windows | SHOULD-FIX | Every redirected run with a CJK title | Overnight batch dies | Reproduced. One line. Only if the CLI is used. |
| M12 | A locked DB during the GPU wait kills the command | CHEAP-WIN | CLI waiting while the app writes | Traceback | Catch and retry. |
| M13 | `dub --keep-background` separates vocals outside the GPU slot | SKIP | Dub is frozen | GPU contention | Frozen feature. |
| M14 | `smoke_pack` uses the GPU without a slot; private positional call | DEFER | Dev script | GPU contention | Developer tool. |
| 15, 16, 17, 18, 20, 23 (L) | Ollama GPU lock for cloud models, softsub language tag and folder, exit codes and messages, missing `choices` | CHEAP-WIN | CLI use | Minor wrong behaviour | Small each. |
| 19 (L) | `align` writes vocals next to the audio | DEFER | Ctrl-C mid-align | Partial file | Rare. |
| 21 (L) | Release build script runs npm without a timeout | SKIP | Developer only | Hung build | Developer tool. |
| 22 (L) | Docs show `--api-key $KEY` on the command line | CHEAP-WIN | CLI users | Key in shell history | Docs line. |

### #1124 Titles, library, settings, backup and restore services (12: 0 H, 4 M, 8 L)

| ID | Finding | Class | Likelihood | Impact | Reason |
|---|---|---|---|---|---|
| M1 | A job can start between a delete's job check and the delete | SKIP | Needs a start in the same instant | Job fails on a deleted title | Hostile timing; the job fails safely. |
| M2 | Restoring from another library's copy keeps its owner and file names | DEFER | Needs another PC's Baihe library in the backup folder | Wrong owner on a restored title | One library. |
| M3 | A full restore takes the auto-backup folder and schedule from the file | CHEAP-WIN | Restoring an older or another PC's backup | Backups go to an old folder | One line in the restore brief. |
| M4 | One non-UTF-8 byte in `.env` hides every key; key saves fail with a 500 | SHOULD-FIX | Hand-editing `.env` in an ANSI editor | All AI engines "not configured", silently | Reproduced. One-line decode change. |
| L5 | Bulk status and tags never see the user | SKIP | Unreachable today | None | Defence in depth (same as #1118 L4). |
| L6 | Backup copy records keyed by file name only | DEFER | Backup drive unplugged once | Old copies pile up | Rare. |
| L7 | Moving the backup folder saves the setting outside the rollback | DEFER | DB locked at that moment | Copies un-managed | Rare. |
| L8 | A cancelled scheduled backup restarts every hour | CHEAP-WIN | Cancelling a long media backup | It restarts hourly | Annoying in normal use; small fix. |
| L9 | Only one per-person backup kept, unlabelled | DEFER | Backing up two people in a row | Wrong file downloaded | PC-only, owner action. |
| L10 | `set_settings` writes keys one by one | SKIP | DB locked mid-batch | Partial settings save | Rare and visible. |
| L11 | `create_drama` undo can leave a new series | SKIP | A rare failure path | An empty series | Harmless. |
| L12 | Orphan title folders never swept | DEFER | After rare failures | Disk leak | Low impact. |

---

## (c) The structural recommendations

| Recommendation | Verdict | Reasoning |
|---|---|---|
| **One redaction function** for every stored or shown error | **Only the cheap part** | Point the shared error handler (#1118 L6) and the job-error writer at the one function that already strips keys and paths. Add "drop URL query strings" to it. Do not hunt down all 9 `_redact` copies before the freeze. The #1126 redaction rework shows the risk: it crashed the bug-report route. |
| **One `contained()` path helper** | **Only the cheap part** | Write it once and use it in the three places that turn a stored file name into a path (voice clips, novel reference, audio file). The threats it stops need crafted backups, so do not refactor the five `_inside` copies now. |
| **Bounded regex and decoder helpers** | **Skip at this scale** | They defend against hostile web pages and booby-trapped data. The one user-facing regex (find-and-replace, #1106 H1) gets its own cheap check in session U1. The parser caps in #1134 already broke ranobes, which shows the cost. |
| **Compare-and-set by default for job writes** | **Worth doing, targeted** | This is the most valuable recommendation, because lost edits are the most likely real harm. Do not flip the default of `save_lines` for every caller a week before the freeze. Switch the long writers that need it: full translate, bulk-series translate, fix flagged, and re-segment (#1116 H1, H2; #1088 M11, M12). The CAS helpers already exist. |
| **Instance ids in GPU and job holders** | **Skip beyond #1073** | There is one server. #1073 already adds owner instance columns. Nothing further is needed for this household. |
| **A one-page threat model** | **Only the cheap part** | Write half a page that states this document's yardstick: who is trusted, what remote access exposes, what is out of scope. Its real value is telling future auditors what not to chase. Skip the full asset-actor-boundary model. |
| **Per-launch secret on the off-mode admin listener** | **Skip** | See not-to-do item 1. Broaden the proxy-header check (#1118 L3) and add a docs warning instead. |
| **Back up `sources.db`** (and a quick integrity check) | **Worth doing** | Restore currently loses tracked series for certain (#1088 H1 to H3). Copy it with the SQLite backup API in auto-backup, carry the live copy when a backup lacks it, and run `PRAGMA quick_check` on the backup copy. All small. |
| **Device tokens** (wire them in) | **Only the cheap part** | The bridge only listens on the PC itself, so per-device revocation adds little here. Remove or relabel the "revoke" control and fix STATUS.md, so nothing promises protection it doesn't give. |

### Fixes already merged or in flight that were out of proportion

Stop investing in these. Merge what is green and small. Do not start follow-up rounds.

- **#1135 (permission combinations, #1106 M4 and M7):** guards against permission sets no member has by default. Its harvested follow-ups (UI gating for each stage) should not be launched.
- **#1134 (source parser bounds):** hostile-page defence that introduced a real regression (ranobes chapter lists over 200 pages lose chapter 1 onwards). Keep only caps that cannot drop real content. Keep the manhuagui regex fix, because a site layout change can trigger it by accident.
- **#1132 (generic import bounds):** the drip-feed deadline and image budget defend against hostile servers. The new budget silently drops GIF and text pages. Remove or loosen the budget; keep the quadratic-markup fix only if it's already passing.
- **#1126 (diagnostics redaction and GPU):** the GPU half is a real Windows fix and should finish. The redaction half grew whole-word user-name matching ("runner" becomes `[USER]`) and a placeholder that crashed bug reports. Cut it back to stripping URL query strings.
- **#1131 (install-now safety):** keep the server-side plan check. Drop the "is site-packages writable" check, which Opus found useless on Windows, unless the owner actually runs the boot service.
- **#1125's per-user bug-report cap (#1106 M9):** already merged and small, but it defends against a known member filing 100 reports. Don't extend it.
- **#1083's OS file-lock redesign:** the stale-lock problem was already fixed by #1077 and #1092. The part of #1083 that matters is restoring packages on the next start (#1098 H2). If the lock redesign keeps needing review rounds, split it off and land only the restore.
- **Session S1's chunked page translation and decode-once work (#1119 M4, M5):** speed work for 100-megapixel, 200-bubble pages. Keep its cheap parts (OCR lock, `HF_TOKEN`, cv2 test skip, dead-code removal).
- **Session T1's contention tests (#1121 M14, M15):** barrier tests for races that need two schedulers. Keep the exact-error and redaction tests.
- **Phase 1 PR C (persisting the GPU queue) and the two-phase slot decision (#1086 H2):** multi-process plumbing for a single-server household. Leave them until after the freeze, or for good.

---

## (d) What the audit review document got wrong or overstated

- **It ranked by attack severity, not by likelihood.** Its "five most serious things" include SSRF through yt-dlp, a TCP relay on the PC, and decompression bombs. All three need an attacker this household does not have. Lost edits from long jobs, the most likely real harm, came second.
- **It made the regex freeze (#1106 H1) the first priority.** The freeze is real and cheap to fix. But it needs a deliberately nested pattern from a trusted member. Restore losing `sources.db` and jobs overwriting edits are far more likely.
- **It repeated the "over ten times" cost claim (#1096 H3).** That is a token count. Since prompt caching was fixed in #1125, the money error is roughly 2 to 4 times.
- **It treated every reviewer HIGH as a real HIGH.** Eleven docs-drift findings were HIGH, and one was a CLI command missing from a doc. Three day-review MEDIUMs (6.1, 6.2, 7.1) say themselves that there is no defect.
- **It missed some under-rated items** that will bite in normal use:
  - stored source links losing their path (day review D-M3);
  - re-transcribing many lines timing out (day review C2, rated LOW);
  - hard-sub OCR filling the disk on ordinary 45-minute episodes, not only 6-hour ones (#1094 H3);
  - GBK novel text silently turning into garbage (#1094 M12);
  - backups failing when a file vanishes mid-backup (#1088 14);
  - a cancelled scheduled backup restarting every hour (#1124 L8);
  - CLAUDE.md's `npx tsc --noEmit` command checking nothing, so agents' frontend type-checks have been empty.
- **It praised the pre-merge reviews without counting what they cost.** Of the four security-sensitive fix PRs (#1126, #1131, #1132, #1134), three needed another round. Two of those rounds were needed because the hardening itself broke something real. That is a sign the hardening was out of proportion, not only that review works.
- **It called the 61 untriaged findings "no owner".** The triage at 19:05 since gave each one a brief or a wait-list. By this document's yardstick only 10 of those 61 are MUST-FIX or SHOULD-FIX.
- **Its counts were accurate.** The 469 total, the severity split and the per-issue counts all match the issues. The one inconsistency it flagged (#1105's summary) was the issue's own.

---

## (e) Recommended work order to the Oct 24 code freeze

Assumptions: gate 1 (the owner's PC test) happens around Oct 13. #1073 and #1068 are held for it. Most data-loss fixes live in files those two PRs own.

### Now to gate 1 (Oct 10 to 13): finish, don't start

1. Land the four in-flight security PRs **trimmed** as section (c) says: #1131 (plan check only), #1126 (GPU half plus query-string stripping), #1132 and #1134 (no content-dropping caps). No further review rounds unless a fix adds new surface.
2. Finish session U1 (the regex freeze). It is cheap and nearly done.
3. Let #1139 (frontend tests able to fail) and the T1 and S1 sessions land only their cheap parts.
4. Fix the CLAUDE.md and AGENTS.md type-check command to `npx tsc -b`. Without it, frontend checks in every later session are empty.
5. Launch nothing new from the DEFER and SKIP lists.

### Gate 1 (around Oct 13)

6. The owner's PC test, plus three quick checks this triage adds:
   - Install GPU PyTorch from Diagnostics (it tests #1098 H4 and #1131).
   - Restore one auto-backup into a spare folder (it shows #1088 H2 in action).
   - Capture one canvas-based comic chapter with the extension (#1087, A1).
7. Merge #1073, then #1068.

### Oct 14 to 18: the data-loss wave (Opus, one brief each)

8. Jobs stop overwriting edits: #1116 H1, H2, L11; #1088 M12, M11.
9. Restore and backup safety: #1088 H1, H2, H3, 4, 14; #1124 M3, M4, L8; add `sources.db` to auto-backup with `quick_check`.
10. Install safety follow-through: #1098 H2 restore on next start (#1083), M5.
11. Scanlate Redo (#1119 H2) and the comic reader memory fix (#1103 H3, M4), already in session.
12. Translation money and blanks: #1096 H2 (raise `max_tokens`, keep complete pairs), H3 (estimate), M9; #1102 M8; #1103 H1 after #910.

### Oct 19 to 21: SHOULD-FIX sweep (Sonnet, small briefs)

13. Hard-sub disk use (#1094 H3), audio extraction cancel (#1094 M18), empty Qwen result (#1094 M7), Korean merge (#1094 M10), GBK novel decode (#1094 M12).
14. Stored link paths (D-M3), re-transcribe-many timeout (C2), Windows export move (#1116 M3), the English-after-new-source rule once the owner picks it (#1116 M6, M7), style-learning privacy filter (#1106 M3).
15. One docs pass: #1105 H1 to H3, H6, M12, M13, plus the device-token wording and the half-page trust note.
16. Pick up CHEAP-WINs only inside files these briefs already touch.

### Oct 22 to 23: gate 2 and regression fixes only

17. The owner re-runs the gate 1 checklist plus one restore, one long translate while editing, and one phone comic chapter.
18. Fix only what that test breaks. Anything else waits.

### Oct 24: code freeze

19. Everything still DEFER or SKIP stays written down here, unscheduled.
