# Baihe roadmap MASTER -- bug tracker, fixed bugs, to-do, deferred/review-later steps

Last updated 2026-09-29. This is the **master index**; it does not replace the full roadmap (`docs/baihe-roadmap.md`, on the planning
branch `claude/baihe-subtitle-planning-95qyvq`, ~5,000 lines, source of truth for Steps 1-105 and the §4 status table). Anything new that
appeared after that document's last edit lives here, with **proposed** step ids 106+ (the planning session confirms or renumbers them and
folds them into roadmap §2/§4). Migration detail: `docs/migration-handoff.md`, `docs/migration-review.md`, `docs/migration-frontend-plan.md`.
Default model for every step below is Sonnet unless a row says otherwise (roadmap §4 model table decides; Opus rows need the user's confirmation first).

## 1. Status snapshot
- Backend migration (services + FastAPI): every ungated slice is merged (PRs #220-#252), plus Step 95 (BGM-preserving dub, #261) and Step 97b (fallback chain, #251). Full suite on the merged batch-1 state: 3805 passed; batch 2: 4028 passed + 1 error; final base: 4111 passed + the same 1 error (fixed by #259, see B-01/F-11; re-verification run pending).
- Frontend (React): foundations (#254), Library (#258), Diagnostics (#257), Settings (#256), standalone Translate (#260) merged and wired into the router (#263); slice D (Workspace shell + Source stage) building; E (Translate stage), F2 (Review), G (Export), H (Dub) queued behind D's stage registry (`docs/migration-frontend-plan.md`).
- CI on GitHub is red on every PR since #212 only because Actions minutes are exhausted; merges are gated on the local suite.

## 2. Bug tracker -- OPEN
Severity is a judgement (H/M/L). "Latent" = wrong only if a condition changes.

| ID | Sev | Where | Problem | Proposed fix | Step |
|---|---|---|---|---|---|
| B-01 | ~~L~~ | tests/test_api_foundation.py | ~~Recurring `database is locked` in `test_run_already_running_is_409` setup~~ **FIXED (#259)**: it was not a load flake but an order-dependent race (the previous test started a real job thread and returned without waiting) | Test now waits for the job to finish | done (F-11) |
| B-02 | M | flag job (Slice 44 / tab) | The `flag` job can overwrite a manual flag change the user made while it runs (existing tab behaviour) | Scope write to lines whose flag is unchanged since start | 122 |
| B-03 | M | chapter OCR (Slice 38) | Settings `tesseract_cmd` is not passed to OCR; a Windows install off PATH fails | Pass the setting through like Slice 21's hardsub path | 122 |
| B-04 | M | job cancel (Slice 22) | Cancelling a job whose owner process died sets a flag nobody reads; record stays "running" (existing no-resume limit) | Stale-job sweep using `db.list_job_records` staleness cutoff | 123 |
| B-05 | L-M | thread jobs (audiobook, burned video, review jobs) | A thread job that never polls `is_cancel_requested` cannot be stopped; ffmpeg is not killed mid-run | Run ffmpeg via `Popen` with cooperative kill, or process jobs | 123 |
| B-06 | M | fallback chain (Step 97b) | Switches engine on the first qualifying error with no backoff, so one rate-limit response moves the whole rest of the run | Retry the primary with backoff N times, then switch | 124 |
| B-07 | M | metadata autofill (Slice 37) | Per-engine LLM call timeouts were not verified (project rule: every request has `timeout=`) | Audit engines used by `supports_reference`; extend `tests/test_static_analysis.py` to `services/` HTTP calls | 125 |
| B-08 | L | metadata autofill (Slice 37) | SSRF check has a small DNS-rebinding gap between resolve and connect (documented) | Pin the resolved IP for the connection | 125 |
| B-09 | L | media upload (Slice 31/32) | Accepts uploads for any drama type (Streamlit offers it only in the audio flow); video audio extraction (ffmpeg) runs synchronously in the request | Type check; move extraction into a job | 126 |
| B-10 | L (latent) | `cli.cmd_narrate_prep` | Zips speakers back to lines by position (violates the "match by id" rule); safe today because the list is built locally | Convert to the id-keyed pattern | 126 |
| B-11 | L | export API vs Streamlit | API exports read saved DB lines; unsaved Streamlit session edits differ | Document; resolved when the tab retires | 132 |
| B-12 | L | Slice 43 line edit | `expected` compare and write are two steps, not one atomic statement | Single conditional UPDATE in `db` | 126 |
| B-13 | L | Slice 49 | API diarization always merges with `overwrite_manual=False` (no confirm step); manual speakers never overwritten | Optional explicit `overwrite_manual` field later | 132 |
| B-14 | M-L | drama delete (Slice 36) | If the folder cannot be fully removed the DB row is already gone (500, no paths) | Delete folder first (rename-then-delete) or record a tombstone; interacts with Step 43 soft-delete | 127 |
| B-15 | L | video_export | `_escape_filter_path` does not escape `'`; safe only because paths come from `tempfile` | Escape or stop using the helper | 126 |
| B-16 | L | Settings | `gemini_free_tier` is stored but nothing consumes it | Wire into `translate_engines.engine_picker_label` | 128 |
| B-17 | L | api CORS | `allow_methods=["GET"]` blocks cross-origin POST; the Vite proxy is the only supported dev path | Widen only if a cross-origin deployment is chosen | 131 |
| B-18 | L | frontend `useJob` | Stops polling on any ApiError, including transient network/5xx | Retry with backoff | 131 |
| B-19 | L | code quality | Private helpers used across modules (`drama_service._job_running_for_drama`, `translate_service._resolve_api_key`, `media_inspect._run_ffprobe`) | Give them public names | 129 |
| B-20 | L | code quality | The guideline-building block is duplicated across the workspace tab, CLI and `translate_run_service` | Shared builder (own step; touches CLI + tab) | 129 |
| B-21 | L | docs drift | Stale statements: `drama_service` and Slice 35 docs say auto-fill is out of scope; `export_service` docstrings say audiobook/video are out of scope; transcribe docstrings and `FILE_ORGANIZATION.md` say `chunk_and_tag` is out of scope; Slice 40 docs say 422 where code returns 400; stub line `**Next candidates:** the` near line 876 of `docs/migration-review.md`; `TranscribeConfig` booleans overlap `MediaStatus` | One doc-only sweep | 130 |
| B-22 | proc | merge tooling | `keepboth.py` is unsafe on `api/schemas.py` when a branch edits a class in place (it split a class body once); `resolve_slice.py` used to duplicate an already-listed router | Documented in handoff; script guard added; prefer base + appended block for schemas | done (#250) |
| B-23 | L | Step 95 | `dub_background.wav` cache compares mtime only; it does not refresh if the separation backend changes (delete the file to force a redo); the -6 dB background gain is a fixed guess; real Demucs/mixing never run | Cache key on backend + gain; user listening check | 126 |
| B-24 | L | frontend e2e | Diagnostics cancel-then-refresh and the Library 409 refusal have no automated test (no seeded running job); specs share one seeded DB, so Playwright runs with 1 worker | Seed a `job_records` row and a running-job drama | 131 |

## 3. Bugs FIXED (this migration effort; earlier fixes are roadmap Steps 1b, 4i, 4j, 5b, 6d, 6f, 9h, 9i, 25-25z, 28-35, 71, 77)
| ID | Fixed in | What was wrong |
|---|---|---|
| F-01 | Slice 49 (#235) | Diarization, dub and re-segment results started through the API were never applied (a process job's result lived only in its own process). Fixed with the `on_done` hook and `apply_diarization_result` |
| F-02 | Slices 20/21 (#221, #222), before merge | Ignored persisted language/script; diarization ran on `vocals.wav`; no audio-pipeline check; Groq key checked too late; hardsub diarize got a `None` audio path; a duplicate start was silently ignored (now 409) |
| F-03 | H1 (#232) | Error messages echoed client input; unbounded ids (now capped at 2**31-1); unbounded drama fields and non-http `source_url`; voice-bank labels allowed `/`, `..`, control chars; missing clip not reported; `clone_engine` unchecked against the drama's language; series created before the drama row (non-atomic) |
| F-04 | H2 (#233) | Glossary create-by-term wiped omitted fields; ASS output allowed line injection through font, title and speaker names; `speaker_colors` unbounded; ASS `wrap_chars_*` had no upper bound |
| F-05 | H3 (#234) | GET handlers created drama folders; line dict leaked the dub filename path; null-unsafe pacing/notes/version compare/labels; TM suggestion scan unbounded; ReDoS-prone find/replace patterns |
| F-06 | Slice 22 (#242) | Line-job ids are reused (`prefix + drama_id`), so a stale cancel flag could kill the next run; the flag is now cleared on any terminal save |
| F-07 | Slice 33 (#241) | `narration_` jobs were invisible to the drama-delete and upload running-job guards |
| F-08 | Slice 28 (#239), caught in build | A symlinked kind folder under `exports/` was being served by the artifact download |
| F-09 | Slice 38/29-30 | `ocrchapter_`, `audiobook_`, `burned_video_` jobs added to the per-drama guard from the start |
| F-10 | Frontend F (#254) | `ErrorBanner` never prints a server message containing a path, key/token pattern, or over 200 characters |
| F-11 | #259 | `test_run_starts_a_real_job_visible_in_jobs_api` returned while its real job thread was still writing; the thread's last DB write raced the next test's `init_db` and failed its fixture setup ("database is locked"), in two consecutive full-suite runs. Test now waits for the job |
| F-12 | #262 | **Streamlit dub tab was broken since Step 26c (#161):** the tab passed `narrate_original`/`source_language` positionally to `dub.build_track_subprocess_worker`, but `background_jobs` appends the result queue LAST while the worker declares `result_queue` before those parameters, so the queue landed in the wrong slot and the job ended without a result. The tests' fake worker copied the wrong signature, hiding it. Fixed with keyword binding (`functools.partial`), fake worker corrected, regression tests added (found by the Step 95 builder) |

## 4. To-do queue (in order)
**Waiting on the user (cannot proceed):**
- Slice 41 (translate bulk/batch + Reflect; roadmap Steps 9/9d) and Slice 45 (restructure + version restore; Step 6c): **need the user's OK to run on Opus**.
- Slice 24 (API-key writes): needs the D5 loopback/admin policy decision.
- Slice 34 (qwen3_asr / qwen3_forced_align): built with mocks (branch `migration-slice34-qwen3`); the real-model check is still owed by the user.
- Real-run checks only the user can do: real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR and EPUBs, a gated-access HF token for pyannote diarization, mobile/real-device checks for Streamlit retirement.

**Ready / in flight:**
1. Step 95 BGM-preserving dub: **done (#261)**; needs the user's real-audio listening check.
2. Frontend: A, B, C, I are done and wired; D (Workspace shell + Source stage) is building; then E (Translate stage), F2 (Review), G (Export), H (Dub) in parallel, each replacing one line of D's stage registry (`docs/migration-frontend-plan.md`).
3. Backend gaps the UI will hit: media playback endpoint with Range support; expose the workspace stage index; serve `frontend/dist` from FastAPI plus a launcher story; SSE/job push (needed for Live); E0 destructive library actions (bulk, backup/restore, storage clean) once a server-side typed-confirm + running-job refusal exists; the fix/cleanup steps 121-132 below.
4. Streamlit retirement, per `docs/migration-frontend-plan.md` (order: Diagnostics, Library, Settings, Translate, Workspace stage by stage).

**Held roadmap steps (unchanged, not reopened here):** 43 soft-delete (plug into `drama_service._hard_delete_drama`), 100-105, 40b, 42, 60, 72.

## 5. Deferred / review-later -> steps (proposed ids; includes the GitHub repos the user supplied)
Sources: roadmap §8a (18 repos matched on "baihe"/"yuri", 2026-09-28: 13 false leads, 5 with real overlap) and §8 (13 scraper repos, 2026-09-27, ideas "deliberately NOT dispatched"). Rule kept: adopt **ideas** only, no code copied verbatim; repo licences were MIT/Apache-2.0.
The 13 false leads (pure keyword collisions, nothing to adopt): baihepailei, baihehui300, GLify, yurison, Yuri1st, yurievve, Yuri-bot, crawler-baihe, baihe-data, baihe, baihelp, cigarette1412/baihe, and `YuriAudio2Notion-Frontend` (a placeholder page). Closed as already built: pinyin alongside hanzi (`Baihe-Zapp-App` -> `segment.py`/`reader.py`).

| Step | From | What | Exit condition | Notes |
|---|---|---|---|---|
| 106 | DynastyReader | **Conditional HTTP (ETag / Last-Modified, 304) in `sources/http.py`**, first user `chapter_check.py`'s scheduled new-chapter polling | A re-poll of an unchanged series page sends `If-None-Match`, a 304 skips the fetch and parse; cache stores validators; tests with a fake server; Step 98 proxy + ToS checks unaffected | Confirmed gap (no ETag/Last-Modified anywhere in `sources/http.py`/`cache.py`); README-claim only for DynastyReader itself |
| 107 | yuri_scraper | **Partial-import retry state**: `run_import_job` currently `break`s the whole job on `ChallengeDetected`/`TermsProhibited`/`SourceError`; surface which chapters already succeeded to the chapter picker (`tabs/sources_tab.py`, later React) and auto-exclude them on retry; write a `.failed.json`-style manifest of what is missing | A retry after a mid-import failure fetches only missing chapters and the UI shows the succeeded ones | Confirmed gap; `RawCache` already limits the re-fetch cost |
| 108 | yuri_scraper | **Adapter capability interfaces**: keep the minimal adapter contract and let adapters opt into extra capabilities (`CredentialsAuthenticator`, `ScopedSearcher`, `BookMetaProvider`, `ScrapePolicyProvider`) instead of one monolithic interface | Design note first, then `sources/base.py` refactor with no behaviour change and all adapter tests green | Design-only first; Baihe's tiered ladder is already broader |
| 109 | yuri_scraper | **Named concurrency profiles** (Slow/Normal/Fast token bucket) with per-source override, compared against Step 98's configurable concurrency | Either a written "already covered" note or a small profile setting with a per-source cap | Compare-then-close is a valid outcome |
| 110 | yuri_scraper | **Credential handling audit**: confirm no adapter persists account/password/cookies (in-memory only unless the user opted in) | Audit note + a static/behavioural test | Security check, small |
| 111 | DynastyReader | **Media/page cache disk ceiling with LRU eviction** (downloaded pages, audio, video) | Configurable ceiling; eviction tested; or a note that Step 9b's model-disk management already covers it | Not a confirmed gap: check first |
| 112 | YuriAudio2Notion | **Notion export companion to the Anki export** (field-scoped partial updates of Notion pages) | Export a reviewed transcript/metadata to Notion via user-supplied token; token never logged | Speculative, user did not ask for it: build only on request |
| 113 | YuriAudio2Notion | **Fanjiao (泛娱有声) audio-drama source adapter** for metadata/episodes | Adapter under `sources/` passing the ToS-check flow, registered like the MaoerFM work (Step 94) | Speculative; needs a ToS review first |
| 114 | searxng-mcp | **General-web-search fallback in Discover** when a title is on none of the ~15 adapters (self-hosted SearXNG) | Optional fallback behind a setting, clearly labelled | From §8 "not dispatched" |
| 115 | lightnovel_epub, NovelScraper | **New novel adapters**: lightnovel.us, wenku8.net; NovelFull/NovelBin/Novgo | One adapter per site through the existing ladder + ToS check | New-adapter scope; pick per pain point |
| 116 | plex-anime-metadata-provider | **Plex/Jellyfin metadata-provider adjacency** for "Fetch & add to library" | Design note (relates to Step 39 Jellyfin) | Design-only |
| 117 | toonkor-translate | Its one distinct idea (see roadmap §8) beyond what Scanlate already does | Compare and close, or spin out | Low |
| 118 | apify/crawlee | One remembered idea only; do not adopt the library | Note kept, no build | Wrong-shaped dependency |
| 119 | Yuri1st | Opt-in, local-only, advisory-not-authoritative pattern for any AI classification we add | Written into the AI-feature guidelines | Note only |

**Migration follow-ups turned into steps:**
| Step | What | Bugs |
|---|---|---|
| 120 | Frontend phase (slices F, A-I, then Streamlit retirement) tracked in `docs/migration-frontend-plan.md` | |
| 121 | Test isolation for the job-thread flake | B-01 |
| 122 | Small correctness fixes: manual-flag overwrite in the flag job, `tesseract_cmd` for chapter OCR | B-02, B-03 |
| 123 | Job robustness: stale-job sweep for dead owner processes; cancellable ffmpeg jobs | B-04, B-05 |
| 124 | Fallback chain: primary backoff before switching; CLI/UI parity for the chain | B-06 |
| 125 | Network hardening: per-engine timeout audit (extend the static check to `services/`), pin resolved IP in the SSRF guard | B-07, B-08 |
| 126 | API robustness: upload type check + async video extraction, id-keyed `cmd_narrate_prep`, atomic line-edit compare-and-set, quote-safe filter path | B-09, B-10, B-12, B-15 |
| 127 | Drama delete ordering / tombstone (with Step 43) | B-14 |
| 128 | Wire `gemini_free_tier` into the engine picker | B-16 |
| 129 | Refactor: public names for cross-module private helpers; shared guideline builder (tab, CLI, service) | B-19, B-20 |
| 130 | Doc-only drift sweep | B-21 |
| 131 | Frontend/API hygiene: CORS decision, `useJob` retry/backoff | B-17, B-18 |
| 132 | Streamlit-parity items to resolve at tab retirement | B-11, B-13 |

## 5b. Needs to be added to the roadmap (3 items)
Real findings with nowhere to live yet: flagged in review, not written up as a numbered step in `docs/baihe-roadmap.md`, or waiting on their own verification before they can be.
I could not see the old planner artifact's original three entries, so this list is reconstructed from roadmap §8a; the planning session should reconcile it against that artifact.
1. **Conditional HTTP (ETag / Last-Modified) for `sources/http.py` and `chapter_check.py`** -- confirmed gap, proposed Step 106.
2. **Partial-import retry state** (show already-succeeded chapters to the picker; failed-chapter manifest) -- confirmed gap, proposed Step 107.
3. **Media/page cache disk ceiling with LRU eviction** -- waiting on its own verification (not a confirmed gap; check Step 9b model-disk management first), proposed Step 111.

A separate third adversarial QC pass (`dub.py`, `background_jobs.py`, `sources/`, `cli.py`, `bulk_translate.py`, every translate engine) has since completed clean: its one apparent finding turned out to already be fixed by Step 25o, and nothing else it checked turned up a new issue.

## 5c. Deferred milestones (long-term, not current scope)
Recorded direction, revisited only if a real need shows up -- not steps waiting in a queue. (Carried over from the old planner chat's artifact.)

| Milestone | Why it is deferred | Revisit when |
|---|---|---|
| R4 -- standalone VAD, independent ASR | Whisper already works and nothing shows Qwen3-ASR is better on this content | Whisper transcripts turn out clearly poor, or long jobs keep failing partway |
| R7 -- full-pipeline benchmark | A developer tool whose only real use is deciding R4 | R4 is being reconsidered |
| Live capture (Bilibili / TikTok Live) | Each platform's live/HLS quirks are real, separate work; the app's actual focus is VOD audio dramas | Live capture itself is wanted, not just downloading a finished VOD |
| R1-full -- general artifact/versioning | R1-lite already covers not losing the original | Several stages need a real version history |
| R2 windowing -- diarize long audio in windows | Only matters for streams several hours long | Such streams are actually being processed |
| R3-full -- single-slot model manager | Only matters once the GPU actually runs out of memory | GPU out-of-memory errors appear |
| M8+ -- FastAPI + React, job queue | Was deferred: a large migration with no current pain. **Status update: now underway at the user's direction** (backend services/API merged, React foundations merged; see section 1 and `docs/migration-frontend-plan.md`). The separate "job queue" half is still deferred | Streamlit becomes the real bottleneck (job queue) |
| Docker / browser extension | User explicitly put both on the back burner | The rest of the roadmap's functionality is further along |

## 6. Working rules
See root `CLAUDE.md`, `docs/engineering-standards.md` and `docs/testing-and-ci.md`; not repeated here.
