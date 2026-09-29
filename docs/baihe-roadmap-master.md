# Baihe roadmap MASTER -- bug tracker, fixed bugs, to-do, deferred/review-later steps

Last updated 2026-09-29. This is the **master index**; it does not replace the full roadmap (`docs/baihe-roadmap.md`, on the planning
branch `claude/baihe-subtitle-planning-95qyvq`, ~5,000 lines, source of truth for Steps 1-105 and the §4 status table). Anything new that
appeared after that document's last edit lives here, with **proposed** step ids 106+ (the planning session confirms or renumbers them and
folds them into roadmap §2/§4). Migration detail: `docs/migration-handoff.md`, `docs/migration-review.md`, `docs/migration-frontend-plan.md`.
Default model for every step below is Sonnet unless a row says otherwise (roadmap §4 model table decides; Opus rows need the user's confirmation first).

## 1. Status snapshot
- Backend migration (services + FastAPI): every ungated slice is merged (PRs #220-#267, including Slices 41 and 45 built on Opus), plus Step 95 (BGM-preserving dub, #261) and Step 97b (fallback chain, #251). Full suite on the merged batch-1 state: 3805 passed; batch 2: 4028 passed + 1 error; final base: 4111 passed + the same 1 error (fixed by #259, see B-01/F-11; re-verification run pending).
- Frontend (React): foundations (#254), Library (#258), Diagnostics (#257), Settings (#256), standalone Translate (#260) merged and wired into the router (#263); Workspace shell + Source (#265), Translate stage (#268), Export (#269), Dub (#270) and Review (#271) also merged, so every planned stage exists (`docs/migration-frontend-plan.md`). Since then: serving from `python -m api` (#287, 405 fix #294), GPU/CPU reporting (#283), Slice 24 key writes (#289, off by default), Slice 34 with fakes (#290), and the concise-UI pass on every page/stage (#293, #295-#302). Tests: vitest 138 / 23 files, Playwright 40 specs, pytest 4342 collected (2026-09-29; collection count, full pass not re-run).
- CI on GitHub is red on every PR since #212 only because Actions minutes are exhausted; merges are gated on the local suite.

## 2. Bug tracker -- OPEN
Severity is a judgement (H/M/L). "Latent" = wrong only if a condition changes.

| ID | Sev | Where | Problem | Proposed fix | Step |
|---|---|---|---|---|---|
| B-01 | ~~L~~ | tests/test_api_foundation.py | ~~Recurring `database is locked` in `test_run_already_running_is_409` setup~~ **FIXED (#259)**: it was not a load flake but an order-dependent race (the previous test started a real job thread and returned without waiting) | Test now waits for the job to finish | done (F-11) |
| B-02 | ~~M~~ | flag job (Slice 44 / tab) | ~~The `flag` job can overwrite a manual flag change the user made while it runs (existing tab behaviour)~~ **FIXED (#276)** | Scope write to lines whose flag is unchanged since start | done (F-13) |
| B-03 | ~~M~~ | chapter OCR (Slice 38) | ~~Settings `tesseract_cmd` is not passed to OCR; a Windows install off PATH fails~~ **FIXED (#276)** | Pass the setting through like Slice 21's hardsub path | done (F-14) |
| B-06 | ~~M~~ | fallback chain (Step 97b) | ~~Switches engine on the first qualifying error with no backoff, so one rate-limit response moves the whole rest of the run~~ **FIXED (#277)** | Retry the primary with backoff N times, then switch | done (F-15) |
| B-07 | ~~M~~ | metadata autofill (Slice 37) | ~~Per-engine LLM call timeouts were not verified (project rule: every request has `timeout=`)~~ **FIXED (#278)** | Audit engines used by `supports_reference`; extend `tests/test_static_analysis.py` to `services/` HTTP calls | done (F-16) |
| B-08 | ~~L~~ | metadata autofill (Slice 37) | ~~SSRF check has a small DNS-rebinding gap between resolve and connect~~ **FIXED (connection pinned to validated IP)** | Pin the resolved IP for the connection | done |
| B-09 | L | media upload (Slice 31/32) | Accepts uploads for any drama type (Streamlit offers it only in the audio flow); video audio extraction (ffmpeg) runs synchronously in the request **FIXED: part 1 (type check) in #286; part 2 (extraction moved into job `extract_audio_<id>`, 409 while it runs, cancel kills ffmpeg) on branch `fix-b09-async-extraction`.** | Type check; move extraction into a job | 126 |
| B-11 | L | export API vs Streamlit | API exports read saved DB lines; unsaved Streamlit session edits differ | Document; resolved when the tab retires | 132 |
| B-12 | ~~L~~ | Slice 43 line edit | ~~`expected` compare and write are two steps, not one atomic statement~~ **FIXED (#291)** | Single conditional UPDATE in `db` | done (F-17) |
| B-13 | ~~L~~ | Slice 49 | ~~API diarization merged with `overwrite_manual=False` only~~ **FIXED** | Fixed: optional `overwrite_manual` query param, needs `confirm=true` (else 422); default unchanged | done (step 132 still covers other parity items) |
| B-14 | ~~M-L~~ | drama delete (Slice 36) | ~~If the folder cannot be fully removed the DB row is already gone (500, no paths)~~ **FIXED (#286)** | Delete folder first (rename-then-delete) or record a tombstone; interacts with Step 43 soft-delete | done (F-18) |
| B-15 | ~~L~~ | video_export | ~~`_escape_filter_path` does not escape `'`; safe only because paths come from `tempfile`~~ **FIXED (#291)** | Escape or stop using the helper | done (F-19) |
| B-16 | ~~L~~ | Settings | ~~`gemini_free_tier` is stored but nothing consumes it~~ **FIXED (#286)** | Wire into `translate_engines.engine_picker_label` | done (F-20) |
| B-17 | L | api CORS | `allow_methods=["GET"]` blocks cross-origin POST; the Vite proxy is the only supported dev path | Widen only if a cross-origin deployment is chosen | 131 |
| B-18 | ~~L~~ | frontend `useJob` | ~~Stops polling on any ApiError, including transient network/5xx~~ **FIXED (#279)** | Retry with backoff | done (F-21) |
| B-19 | ~~L~~ | code quality | ~~Private helpers used across modules (`drama_service.job_running_for_drama`, `translate_service.resolve_api_key`, `media_inspect.run_ffprobe`)~~ **DONE (refactor-b19-public-helpers)** | Renamed to public names, all callers and tests updated | done |
| B-20 | L | code quality | The guideline-building block is duplicated across the workspace tab, CLI and `translate_run_service` | Shared builder (own step; touches CLI + tab) | 129 |
| B-21 | L | docs drift | Stale statements: `drama_service` and Slice 35 docs say auto-fill is out of scope; `export_service` docstrings say audiobook/video are out of scope; transcribe docstrings and `FILE_ORGANIZATION.md` say `chunk_and_tag` is out of scope; Slice 40 docs say 422 where code returns 400; stub line `**Next candidates:** the` near line 876 of `docs/migration-review.md`; `TranscribeConfig` booleans overlap `MediaStatus` | One doc-only sweep | done (docs-b21-drift-sweep; `TranscribeConfig`/`MediaStatus` overlap left as a note only) |
| B-22 | proc | merge tooling | `keepboth.py` is unsafe on `api/schemas.py` when a branch edits a class in place (it split a class body once); `resolve_slice.py` used to duplicate an already-listed router | Documented in handoff; script guard added; prefer base + appended block for schemas | done (#250) |
| B-23 | L | Step 95 | `dub_background.wav` cache compares mtime only; it does not refresh if the separation backend changes (delete the file to force a redo); the -6 dB background gain is a fixed guess; real Demucs/mixing never run | Cache key on backend + gain; user listening check | 126 |
| B-24 | L | frontend e2e | Diagnostics cancel-then-refresh and the Library 409 refusal have no automated test (no seeded running job); specs share one seeded DB, so Playwright runs with 1 worker | Seed a `job_records` row and a running-job drama | 131 |
| B-25 | H | Sources / video URL import (Steps 23d, front door) | ~~Adapter URL patterns are unanchored `re.search` (`sources/adapters/bilibili.py:47-51`, `front_door._VIDEO_URL`), so `http://169.254.169.254/?b23.tv/` matches; `normalize_url` then does `"b23.tv" in url` and sends `HEAD` with redirects (`bilibili.py:168-185`) and yt-dlp gets the same URL. Blind SSRF from the pasted-URL box. Found by the Discover/Sources API spec (`docs/specs/discover-sources-live-api-spec.md` section 3); not yet reproduced by a test~~ **FIXED** (`fix-b25-adapter-url-anchor`: `sources.base.host_url_search` matches the parsed http(s) host for every adapter, `front_door.is_video_url` and `bilibili.normalize_url`; tests in `tests/test_sources_url_anchor.py`). Redirect targets of a b23.tv HEAD are still not validated | Match the URL host against the adapter's known hosts; validate scheme and public host before any fetch; add a regression test | 125 |
| B-26 | ~~M~~ | redaction | ~~`translate_engines.redact_secrets` (and `diagnostics.redact_for_support`) has no pattern for Hugging Face `hf_...` tokens; `services/diagnostics_gaps_service.py` had a local stopgap~~ **FIXED (branch fix-b26-hf-token-redaction)**: shared helper now redacts `hf_`, Groq `gsk_`, DeepL (`:fx` / `DeepL-Auth-Key`) keys; local copy dropped; `tests/test_secret_redaction.py` | Add the `hf_` pattern to the shared helper and a regression test; drop the local copy | done |
| B-27 | M | translate run (line-scoped retry) | A line-scoped run on a different engine (`translate_run_service.start_translate_run(..., engine_name="ollama", line_ids=[...])`, the API's "retry a content_blocked line elsewhere") overwrites the drama's saved engine: `bulk_translate.finish_translation_run` always writes `translation_engine=engine_choice` (`bulk_translate.py:1539`). The Streamlit retry kept the drama's engine (`TestContentBlockedRetryWithDifferentEngine`). Pinned by the `xfail(strict=True)` `TestRetryOnDifferentEngineInvariants` in `tests/test_service_invariants.py` | Only record `translation_engine` for a whole-drama run (no `target_ids`), or let the caller opt out | open |

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
| F-13 | B-02, #276 | flag job keeps a flag the user changed while it ran (compare-and-set against the job-start value) |
| F-14 | B-03, #276 | `tesseract_cmd` is now passed through chapter OCR |
| F-15 | B-06, #277 | fallback chain retries the primary with capped backoff on transient errors before switching |
| F-16 | B-07, #278 | static timeout check extended to `services/`, `api/`, `sources/http.py`, `dictionary.py` (also `urlopen`, `session.request`); audit found no missing timeouts |
| F-17 | B-12, #291 | line-edit compare-and-set is one conditional UPDATE (`db.update_line_fields_if`) |
| F-18 | B-14, #286 | drama folder is renamed to a tombstone before the DB row is deleted, restored if the DB delete fails; a failed final removal is a logged warning, not a 500 |
| F-19 | B-15, #291 | single quotes are escaped in the ffmpeg subtitles filter path |
| F-20 | B-16, #286 | engine list labels now reflect the `gemini_free_tier` setting |
| F-21 | B-18, #279 | `useJob` retries network errors and 5xx with capped backoff, surfaces after 5 consecutive failures; 4xx still stops |
| F-22 | B-10, this PR | `cmd_narrate_prep`, the Workspace tab and `narration_service` paired speaker labels by list position; now `translate_engines.tag_speakers_by_id` returns `{chunk idx: label}` and callers look up by idx (missing -> Narrator, unknown ids ignored) |
| F-23 | B-04, this PR | cancel always sets the job_records flag first; a queued/running record whose owner has not heartbeated for 15 min (`jobs_service.STALE_JOB_SECONDS`; owners bump `updated_at` every `background_jobs.HEARTBEAT_INTERVAL` = 60 s) and that is not live in this process is then closed as `cancelled` by one conditional UPDATE (`db.close_stale_job_record`), so a live owner's heartbeat or `done` always wins |
| F-24 | B-05, this PR | audiobook and burned-video ffmpeg runs go through `background_jobs.run_cancellable` (own process group; the whole tree is killed on cancel, bounded drain; job ends `cancelled` via `JobCancelled`); flag/consistency/notes jobs check cancel before every LLM batch and skip their save. The one batch already in flight still finishes and is billed |

## 4. To-do queue (in order)
**Waiting on the user (cannot proceed):**
- Slice 24 (API-key writes): **user requirement (2026-09-29):** API keys live only on the main Baihe PC; jobs started from other authorized devices use those server-side keys; keys are never sent to a phone, tablet or laptop. The API already resolves keys server-side and never accepts or returns one (checked: `services/translate_service.resolve_api_key`; no `api_key` field in `api/schemas.py` or the routers). So no remote key-write endpoint is needed for this. Still open: how keys are *entered* on the PC (Streamlit now, or a PC-only admin listener later, D5). Slice 24 **built, OFF by default** (`BAIHE_API_ALLOW_KEY_WRITES=1` to enable): write-only `POST /api/settings/keys/{engine}` (body `{value, confirm:true}`) and `POST /api/settings/keys/{engine}/clear`; response is only `{engine, configured}`. Guards (all must hold, else a generic 403): flag on; TCP peer is loopback; Host header is 127.0.0.1/localhost/[::1]; none of X-Forwarded-For/Host/Proto, Forwarded, X-Real-IP, Tailscale-User-Login, Cf-Connecting-Ip, Cf-Ray, Via present; Origin, if present, is loopback; then `confirm=true`, a known secret engine, and a clean value (no whitespace/quotes/control chars, max 512). **Honest limit: this is a safeguard, not authentication** -- a header/peer check can be defeated by a proxy that strips headers or a local process; real admin isolation is the separate admin listener (D5), still to be built.
- Slice 34 (qwen3_asr / qwen3_forced_align): built with mocks (branch `migration-slice34-qwen3`); the real-model check is still owed by the user.
- Real-run checks only the user can do: real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR and EPUBs, a gated-access HF token for pyannote diarization, mobile/real-device checks for Streamlit retirement.

**Ready / in flight:**
1. Step 95 BGM-preserving dub: **done (#261)**; needs the user's real-audio listening check.
2. Frontend: every planned slice is done (#254-#271) and every page has the concise treatment (#295-#302). Remaining: real-browser checks and the Streamlit retirement criteria (`docs/migration-frontend-plan.md`).
3. Backend gaps the UI will hit (also: pending-batch list + cancel endpoints for Translate "Resume pending batches" are done (Slice 51); metadata auto-fill/media-analysis and OCR/EPUB UIs; per-line improve/why-this; dub download): media playback endpoint with Range support (done: Slice 52, API only); expose the workspace stage index; serve `frontend/dist` from FastAPI plus a launcher story; SSE/job push (needed for Live); E0 destructive library actions (bulk, backup/restore, storage clean) once a server-side typed-confirm + running-job refusal exists; the fix/cleanup steps 121-132 below.
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
| 133 | Remote access: users table, allowlist, server-side sessions, permission dependency (deny by default, one permission per route) plus the static test; `python -m api grant-admin` | D6 replaced 2026-09-29 |
| 134 | Remote access: Google OIDC login and callback (Authlib; `sub`, `email_verified`, PKCE, state, nonce), CSRF, login rate limits, audit log | needs 133 |
| 135 | Remote access: per-device extension tokens, then move the extension bridge from `page_server.py` into the API (never route 8756) | needs 133 |
| 136 | Remote access: URL-import guards (http/https, private/loopback/link-local incl. after redirects, size/count/concurrency caps); B-25 redirect-target gap belongs here | needs Slice 54 |
| 137 | Remote access: per-user job limits and per-user media bandwidth caps; verify Range/seek with a multi-GB file and job progress through Caddy | needs 133, slice 52 |
| 138 | Remote access: PWA manifest + minimal service worker (app shell only); check session cookies in installed PWAs on real devices | user checks |
| 139 | Remote access: hardening (no public `/api/docs`, production mode, Streamlit never exposed) and operations (services on boot, uptime alert, backups, dynamic DNS, cert renewal monitoring, per-device session list and revocation) | needs 133-134 |
| 140 | Remote access: Caddy config, LAN test with a real certificate, then open the router port last | needs 133-139 |
| 141 | Spec (migration-architect) for a standalone PC shell and a connection toggle. The PC shell is a desktop window (pywebview or Tauri) with an icon, tray and installer, running the local API. The toggle is an app-shell setting: "This PC" (local, no login) or "Connect to my PC" (server URL plus Google login through Caddy). The phone is a PWA client (a Capacitor wrapper can come later) with only "Connect". Offline or standalone phone use is deferred. User approved writing the spec 2026-09-29. | needs 140 |

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
