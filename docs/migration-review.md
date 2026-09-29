# Full migration review: moving Baihe from Streamlit to React + FastAPI

Companion to [`migration-react-fastapi.md`](migration-react-fastapi.md), which
covers the foundation built on this branch (FastAPI, React, one read-only
Library feature). **This document reviews the whole app**: every tab and
Workspace stage, what it would take to move each one, what has to be
preserved, in what order, and which decisions are yours to make first.
Nothing here is implemented beyond the foundation. It's the map for the
steps that would follow.

Base commit reviewed: `45b5c83` (`baihe-subtitler`, 2026-09-27).

## How this was measured

Estimates of "how coupled is X" drift fast, so everything below comes
from direct measurement of the current code, not skimming:

- **AST inventory** of every top-level function in `tabs/`, `ui/`,
  `app.py`, `common.py`. For each: length, Streamlit calls, `db.*` reads
  vs writes, other app modules called, `st.session_state` keys, and file
  operations. 134 functions.
- **Line-range counts per Workspace stage** (the `st.tabs` blocks in
  `render_workspace_tab`): widgets, DB reads/writes, job starts, engine
  calls, `st.rerun()`s, uploads/downloads, "Step NN" fix comments.
- **Every "Step NN" comment in `workspace_tab.py`** (107 of them),
  read and classified. They mark bug fixes that currently live inside UI
  code.
- **Cross-file session-state keys**, polling, media/embed, upload/download,
  subprocess and server-side-browser usage.

---

## 1. Headline findings

1. **The business logic is mostly already UI-free. The orchestration
   isn't.** Only `app.py`, `common.py`, `ui_theme.py`, `tabs/*` and
   `ui/*` import Streamlit. The engines, `db.py`, `background_jobs.py`,
   `sources/`, `bulk_translate.py` (including the Workspace/CLI-shared
   `finish_translation_run`) are all callable headlessly, and `cli.py`
   already does so for `align`, `diarize`, `translate`, `dub`,
   `export-video`, `narrate-prep` and `run`. What's coupled is the glue:
   *which* functions get called, in what order, with what guards, and
   what gets written back. That glue is spread through widget handlers.

2. **Workspace is one 4,827-line function.** `render_workspace_tab`
   (lines 1591–6418) holds 889 Streamlit calls, 105 DB writes and 63
   background-job calls, across 8 stages. Translate (≈1,570 lines, 47 DB
   writes, 25 reruns) and Review (≈1,490 lines, 30 writes, 41 reruns) are
   ~60% of it. No other tab is close: the next largest render function is
   Diagnostics at 716 lines.

3. **Many job runners are already extractable as-is.** They are
   top-level, Streamlit-free functions that just happen to live in tab
   files: `run_translate_job`, `run_transcribe_job`, `run_hardsub_ocr_job`,
   `run_emotion_job`, `run_sensevoice_job`, `run_flag_job`,
   `run_consistency_job`, `run_translation_notes_job`,
   `run_fix_flagged_lines_job`, `_start_bulk_translation/_generic/_reflect`
   (`workspace_tab.py`), `run_bulk_series_translate_job` and
   `restore_library_backup` (`library_tab.py`), `add_uploaded_pages`
   (`scanlate_tab.py`), `save_key_to_env`/`_load_env_defaults`
   (`settings_tab.py`). Moving them into `services/` is mechanical, and
   it's the cheapest, highest-value extraction available.

4. **The hard part is state, not widgets.** Workspace edits a
   *working copy* of a drama's lines in `st.session_state.lines`, shared
   across tabs, saved explicitly. Around it sit 305 dynamic, per-line
   widget keys and 76 distinct literal session keys. At least a dozen
   past bugs (4j, 9h, 9i, 25b, 25i, 25j, 25p, 25r, 23c) were this state
   going stale or leaking between dramas. React replaces the mechanism,
   but the *rules* those fixes encode have to be carried over deliberately
   (§4).

5. **Job state is per-process** (details in the foundation doc §6). Any
   job-starting endpoint needs the API hosted inside the same process as
   the job registry, or durable jobs (Step 41). This one decision gates
   every write-heavy area.

6. **Some features are "server-local" by nature.** Sources' sign-in opens
   a *visible* browser window on the machine running Baihe. Diagnostics
   installs pip packages and can reset the whole library. These can move
   to React, but they can't be offered to a remote device the same way,
   and they need an explicit safety tier over HTTP.

---

## 2. Cross-cutting concerns (apply to every area)

| Concern | How it works today | What a React/FastAPI version needs |
|---|---|---|
| **Current drama** | `st.session_state.active_drama_id`, per browser session, read by 5 files | URL state (`/dramas/:id/...`). Every API call carries the drama id explicitly. No server-side "current drama". |
| **Working copy of lines** | `st.session_state.lines` (a list of `core.Line`), lazily loaded, edited in place, saved with `db.save_lines`; 305 per-line widget keys | The server is the source of truth, with per-line PATCH endpoints (`fields=`-scoped, the same rule background jobs already follow) and optimistic concurrency (a line/drama revision or the Step 6f snapshot check) instead of a client-held full list that's later bulk-saved. |
| **Household profile** | `common.get_active_profile_id()` from session state | A client-held profile id sent per request (header, e.g. `X-Baihe-Profile`). Not an auth identity (Step 78 / M8-H). |
| **API keys & per-session settings** | Typed into the sidebar and kept in `st.session_state` only (never the DB). `.env` read on every render. `SETTINGS_KEYS` pushed into modules (`background_jobs.set_gpu_limit_enabled`, `page_server.set_translation_config`) | **A decision is needed (D2).** Keys can't round-trip through the browser on every call. Options: server-side only (`.env` via the existing `save_key_to_env`), or an in-memory server-side session store. Non-secret preferences (reader font, theme, defaults) can live client-side or in a small settings table. |
| **Background jobs** | `background_jobs` dict in the Streamlit process; UI polls on rerun; Diagnostics uses `@st.fragment(run_every="2s")` | Host the API in-process (D1). `POST /api/jobs`, `GET /api/jobs/{id}`, cancel, then SSE fed by Step 44's event bus. The "phone disconnects, job continues" requirement is already met while the process lives. |
| **Media playback** | `st.audio`/`st.video` with file paths and bytes; Reader builds data-URI audio (`reader.build_page_audio_data_uri`) | A `GET /api/dramas/{id}/media/{kind}` endpoint with HTTP Range support (a `FileResponse` from Starlette does this). Never expose raw filesystem paths. |
| **Uploads** | 15 `st.file_uploader`s (9 in Workspace); `server.maxUploadSize = 2048` MB | Multipart/streamed upload endpoints with size limits; for 2 GB video, chunked or resumable upload. Keep the Step 25i/25p rule: **write only on explicit submit, scoped to one drama.** |
| **Downloads/exports** | 22 `st.download_button`s (subtitles, video, zips, apkg, backups) | Build-then-download endpoints. Long builds (full episode video) become jobs whose result is a file id. |
| **Embedded HTML** | Reader: `reader.build_reader_html` renders a self-contained HTML string via `st.iframe`. Discover embeds external sites | Reader HTML can be served as-is (`text/html` endpoint shown in a sandboxed iframe) as an early win, then replaced by native components later. External embeds stay iframes. |
| **Theming / dark mode** | CSS overlay (`ui_theme.py`) over a fixed Streamlit theme (the known canvas/data-editor gap from Step 68 / secondary-review Issue 12) | Solved natively by React (CSS variables). This is one of the few places React *removes* a known limitation rather than just matching it. |
| **Error display** | `st.error` + `app._safe_render` per-tab containment | The API error shape (done) plus error boundaries per screen. |
| **CLI parity rule** | `cli.py` mirrors Workspace | Becomes three callers of one service. Parity is enforced by construction instead of by comments. |
| **Browser extension** | `page_server.py`, loopback, token, no CORS | Unchanged until the API is in-process, then ported with its security rules intact (foundation doc §8). |

---

## 3. Area-by-area review

Sizes are relative effort for *service extraction + API + React screen*,
based on the measurements. Treat them as ranking, not a schedule. **S** is
days, **M** about a week or two, **L** several weeks, **XL** a multi-step
program of its own.

### 3.1 Library — *first area; list/detail done*

- **Does:** dashboard stats and spend; recently active; cost by drama;
  series view (Step 22); global line search; All-dramas list with filters
  and bulk actions (status change, delete, bulk translate, tags, open in
  Workspace); storage cleanup; reading history; backup (DB-only / full
  zip) and restore; presets; voice bank.
- **Coupling:** `render_library_tab` has 526 lines, 132 widgets, 26 DB
  reads and 11 writes. `run_bulk_series_translate_job` (151 lines) and
  `restore_library_backup` are already UI-free.
- **Reusable now:** `db.get_library_stats`, `get_usage_summary`,
  `get_usage_by_drama`, `list_series`, `list_dramas_by_series`,
  `search_lines_globally`, `list_reading_history`, `list_presets`,
  `list_voice_bank_entries`, `storage.*`, `snapshot_database`.
- **Services:** `library_service` (done: list, detail) plus stats,
  series, search, history; `library_admin_service` for bulk
  status/tags/delete (delete goes through Step 43's soft-delete when
  that lands); `backup_service` (wraps `snapshot_database` and
  `restore_library_backup`, keeping Step 25k's "validate the zip before
  destroying anything"); `presets_service`; `voice_bank_service`.
- **API:** `GET /library/stats`, `/library/series[/{id}]`,
  `/library/search?q=`, `/library/history`, `PATCH /library/dramas`
  (bulk), `POST /library/backup` (job → file), `POST /library/restore`
  (upload, then a confirm step), presets and voice-bank CRUD.
- **Invariants to carry:** 25k restore validation; 25z/71
  confirm-before-delete; Step 25d item 8 (don't delete a drama while a
  job runs for it); 26e per-profile history.
- **Size:** M. **Risk:** low (mostly reads), except restore and delete.

### 3.2 Workspace — the pipeline. **XL, do it stage by stage**

| Stage (line range) | Does | Widgets | DB R/W | Jobs | Reruns | Size | Notes |
|---|---|---:|---:|---:|---:|---|---|
| Pick / create drama (1591–1964) | Drama picker, create form (media type, series), metadata auto-fill, media analysis, edit metadata, delete | 69 | 9 / 10 | 0 | 7 | M | Steps 4j, 9i, 22/22b, 25z, 26e, 87 (in flight) |
| Source (1965–2322) | Upload audio/video/novel/EPUB, chapter OCR, raw novel context | 64 | 0 / 8 | 0 | 5 | M | Upload-heavy; Steps 25i, 25p, 25r, 45 |
| Transcript (2323–2773) | Transcribe (Whisper/Qwen, preprocessing, word align), paste transcript, accuracy tuning / auto-tune | 71 | 3 / 6 | 1 | 8 | M | `run_transcribe_job` already UI-free; Step 25 (capture inputs) |
| Diarize (2774–2806) | Speaker detection re-run, apply result | 4 | 1 / 0 | 0 | 0 | S | Delegates to `_render_speaker_rerun`/`_apply_*` (subprocess job, Step 4d/4f) |
| Translate (2807–4373) | Engine/model/style/glossary/instructions, cost estimate and cap, normal/bulk/reflect translate, narration prep, characters and voice setup (section 6) | 210 | 22 / 47 | 4 | 25 | XL | 24 step comments. The densest block: Steps 9, 12e, 23c, 25, 25b, 25m, 26/26c, 32 |
| Review (4374–5861) | Line editor, find/replace, search, player seek, checks (coverage, pacing, consistency), review queue, AI refinement (emotion, adaptive style, versions, notes), restructure (merge/re-segment), version history | 284 | 26 / 30 | 7 | 41 | XL | Editing model rework (§2); Steps 6f, 9h, 24, 31, 55 |
| Dub (5862–6014) | TTS engine, pacing, generate dub, mix | 19 | 3 / 3 | 1 | 3 | M | Steps 4e, 11c, 26c |
| Export (6015–6418) | Subtitle formats and styles, burn/softsub video, vertical export, package | 78 | 2 / 3 | 0 | 3 | M | 7 downloads; video build should become a job; Steps 6d, 12b |

- **Reusable now:** every `run_*_job`; `bulk_translate.*` (including
  `finish_translation_run`); `core.*`, `subtitle_formats.*`,
  `video_export.*`, `dub.*`, `export_package.*`, `resegment.*`,
  `auto_qc.*`; the `cli.py` command bodies as the reference headless
  sequence for align → diarize → translate → dub → export.
- **Services (proposed):**
  - `drama_service`: create, update, delete, series assignment.
  - `source_service`: attach media/novel/EPUB, OCR chapters.
  - `transcription_service`: start/apply transcribe, auto-tune.
  - `diarization_service`.
  - `translation_service`: settings resolution, cost estimate/cap, start
    normal/bulk/reflect, characters/pronouns/voices.
  - `lines_service`: get, patch a line, find/replace, merge,
    re-segment, restore version, with the snapshot and staleness guards
    in §4.
  - `review_service`: flag, consistency, notes, emotion, fix-flagged,
    auto QC.
  - `dub_service`.
  - `export_service`: subtitle files now, video as a job.
- **Order inside Workspace:** Export (read-mostly, downloads) → Diarize
  → Transcript → Dub → Pick/create → Source (uploads) → Translate →
  Review last (it needs the new editing model).
- **Risk:** highest in the app. This is where the embedded invariants
  live (§4), and silently dropping one reintroduces a known data-loss
  bug.

### 3.3 Read & Watch (Reader) — **M; good early candidate**

- **Does:** interactive reader (HTML with segmentation, pinyin/furigana,
  definitions); watch/listen with captions; series glossary; Story
  tools (recap, relationships, character lookup); universe wiki; ask
  about this drama (Q&A); notes; vocab and rich Anki export.
- **Coupling:** 396 lines, 116 widgets, 12 DB reads, 6 writes. Logic
  already lives in `reader.py`, `story_context.py`, `universe_wiki.py`,
  `qa.py`, `line_tools.py`, `vocab_export.py`.
- **Early win:** serve `reader.build_reader_html(...)` output from an
  endpoint and show it in a sandboxed iframe, for immediate parity. Native
  components can replace it later.
- **Needs:** media Range endpoint (§2); profile id for progress and
  notes (26e); an engine for the AI tools (depends on D2, keys).
- **Mobile value:** high. Reading and listening on a phone is exactly
  the M8 "library browsing / review" workflow.

### 3.4 Scanlate — **L**

- **Does:** page upload/PDF import, bubble detection + OCR + translate
  (single and batch), bubble editing, manual bubbles, fonts, erase/heal
  brush, render typeset pages, bulk render/export, bulk find/replace.
- **Coupling:** 588-line render, 117 widgets, 9 reads, 8 writes, 7 file
  ops. Logic is in `scanlate.py`/`ocr.py`; `add_uploaded_pages` is
  UI-free.
- **Hard parts:** the image/bubble editor is genuinely interactive (drawn
  regions, the brush). This is where React *gains* the most. The
  Streamlit brush depends on `streamlit_drawable_canvas`, which is
  flagged incompatible with the current Streamlit version. A React
  canvas (e.g. Konva or plain `<canvas>`) removes that dependency
  problem.
- **Invariants:** Step 25n (a failed page translation must not blank
  every bubble); 25o (stale find/replace matches); per-drama upload
  scoping (25p). OCR jobs are GPU-touching, so the GPU guard applies (D1).

### 3.5 Sources — **L, with a server-local exception**

- **Does:** paste-any-URL front door (preflight, adaptive ladder, AI
  fallback extraction, review/correct extraction, save site profile),
  site search, series browser (chapters, import), access status/health,
  new-chapter notifications, sign-in, per-source diagnostics, settings
  and cache.
- **Coupling:** spread over 21 small functions (the best-factored tab),
  223 widgets but only 2 DB calls. Nearly everything goes through
  `sources.*` (`front_door`, `pipeline`, `registry`, `ladder`,
  `adaptive`, `store`, `profiles`).
- **Server-local:** `auth_browser.manual_login` opens a **visible
  browser window on the Baihe machine**. That works from a React page
  only when the user is sitting at that PC. It should be marked as
  local-only in the API (refused, or clearly labelled, for remote
  clients). It's a capability boundary, not a bug.
- **Invariants:** ToS gates (Steps 25q, 28, `site_terms.py`,
  `EXPLICITLY_RESTRICTED`) must stay enforced **in the service**, never
  only in the UI. An API makes it trivial to call an adapter directly, so
  this matters more than it does today. Step 25s (don't overwrite an
  existing drama's audio on "Import video" without confirmation).

### 3.6 Discover — **M**

- Known-titles catalog, search, official platforms, site navigation
  helper (`navigator.py`), baihehub search, bulk import from listing
  pages, in-app site browse (iframe), manual add. 359-line render, 104
  widgets. Logic is in `title_library`, `bulk_import`, `navigator`,
  `known_sites`, `metadata_lookup`.
- **Watch:** Steps 84/25u (engine key requirement: use
  `translate_engines.FREE_ENGINES`, not a hardcoded check). Step 84 is in
  flight on `baihe-subtitler` right now, so re-read this after it merges.

### 3.7 Translate (standalone) — **S; good early candidate**

- Paste/upload text, pick an engine, translate, history. 177 lines, 51
  widgets, mostly `translate_engines` calls. Self-contained. It only
  needs engine and key handling (D2) and a small job or a synchronous
  call with a timeout.

### 3.8 Live — **M, later**

- Start/stop near-live stream translation (`live_translate.py`) as a
  background job. React is a good fit (a live-updating view via SSE) but
  it depends on in-process jobs (D1) and Step 44's event bus. Step 9f's
  queued-job cancellation behaviour must hold.

### 3.9 Diagnostics — **M, with admin-action care**

- **Does:** check my setup; dependency panel with Install/Upgrade (pip
  streaming); running jobs (fragment polling); job history; bug-repro
  bundles and replay; model/engine versions; model cache; pyannote
  gated-access check; App Assistant; support report; log tail; accuracy
  benchmark; danger zone (reset library).
- **Coupling:** 716-line render, 191 widgets; logic mostly in
  `diagnostics.py`, `benchmark.py`, `debug_view.py`, `applog.py`,
  `app_help.py`.
- **Read-only parts are an easy early win.** Dependency status, versions,
  jobs and log map directly onto existing functions and meet the
  roadmap's "job monitoring" mobile workflow.
- **Admin actions need a policy before they get an endpoint.**
  `diagnostics.stream_pip_install`/`stream_bulk_install` run `pip` in a
  subprocess, and the danger zone deletes the library. Over HTTP, with no
  authentication, that is remote code execution plus remote data
  destruction for anyone who can reach the port. Recommendation: keep
  these Streamlit-only (or loopback-only in the API, refused for any
  non-local client) until M8-H decides remote access, and classify them
  as 🔴 in `action_tiers.py`.

### 3.10 Settings (sidebar) — **M, but decision-first**

- Profile picker (26e); dark mode; reading experience; OCR backend;
  defaults for new dramas; API keys and endpoints (with "Save to .env");
  offline/restricted networks; performance (GPU limit, notifications);
  spending cap; downloads (cookies); browser-extension settings.
- 22 session keys set here and read across tabs. It's the hub of the
  cross-tab state (`settings_ollama_url` is used in 9 files,
  `gemini_free_tier` in 8).
- **Needs D2 decided first** (where keys and per-session engine settings
  live once there's more than one frontend). Then a `settings_service`
  (typed read/write of non-secret settings, key presence as
  "configured"/"not configured" without ever returning the value, and
  `.env` writes through the existing `save_key_to_env`).

---

## 4. Register of embedded invariants (what must not be lost)

Every row is a past, real bug whose fix currently lives in UI code.
**Class U**: a Streamlit-mechanism artifact; in React it becomes a
*frontend rule* (and an e2e test). **Class S**: real business logic;
it must move into the service layer, with a unit test, *before* the
Streamlit version is retired.

| Step | Where today | Rule | Class | Post-migration home |
|---|---|---|---|---|
| 4j, 25j | drama picker | Switching drama must never show or carry the previous drama's lines | U | Route-scoped data; remount per drama id |
| 9h, 9i | line editors | Positional widget cache went stale after a job rewrote lines | U | Refetch lines after a job completes (job-done event) |
| 25b, 23c | character widgets | Widget keys must include the drama id and the current value | U | State keyed by `(drama_id, speaker)` |
| 25i, 25p, 25r | uploaders | Uploads are per-drama and written only on an explicit click | U+S | Explicit submit, plus a service that requires `drama_id` |
| 19 | stage tabs | Open on the drama's real current stage | U | `ui/workflow.py`'s stage index moves to a service (`_compute_workspace_stage_index`) |
| 4f, 25 | diarize / transcribe | Capture the job's *starting* inputs; later UI edits must not be applied to its result | S | Job params snapshotted at `POST /jobs` |
| 25, 25m, 4j-adjacent | full line replace | Take a history snapshot before replacing every line | S | Inside `lines_service` / `transcription_service` |
| 6f | merge / re-segment apply | Refuse to apply a preview if the DB changed since the preview | S | Optimistic concurrency on apply |
| 25z, 71, 25d-8 | deletes | Confirm; block while a job runs for that drama | S | Service check, plus a confirm step in the API (two-phase or `confirm=true`), plus Step 43 soft-delete |
| 25c | translate | One shared finish path for UI and CLI | S | Already `bulk_translate.finish_translation_run`; keep it the only path |
| 9, 25w | translate | Monthly spend cap stops jobs | S | Inside `translation_service` / job |
| 25d-3, 55 | fix-flagged, checks | A failed batch must be surfaced, never silently mean "no issues" | S | Job result schema carries per-batch errors |
| 24 | line edit | A hand edit is recorded in translation memory; a corrected line isn't suggested back | S | `lines_service.patch_line` |
| 31 | review | Content-moderation refusal offers a retry on another engine | S | Error code (`moderation_refused`) plus a retry endpoint |
| 6d, 12b | export | Flags are written only on an explicit click, never on render | S | Separate `POST` actions; GETs stay read-only |
| 26e | notes / history | Per-profile, not shared | S | Profile id on every request (§2) |
| 12e | instructions | Persisted per drama and sent with every translation | S | Already in DB; the service reads it |
| 26c | dub | Don't offer a clone engine that can't speak the source language | S | Engine capability list from the API |
| 25k | restore | Validate the backup zip before destroying anything | S | Already in `restore_library_backup`; move it to `backup_service` |
| 25q, 28 | sources | ToS verdicts enforced on every fetch path | S | `sources` layer, never the UI |

**Rule for every future extraction:** a Streamlit screen is retired only
after each Class S row it touches has a service-level test, and each
Class U row has a React/e2e test.

---

## 5. Recommended sequence

Ordered by value ÷ risk. It builds each prerequisite before the thing
that needs it.

| # | Phase | Contents | Gate to start |
|---|---|---|---|
| 0 | Decisions | D1–D6 below | — |
| 1 | Foundation | ✅ done on this branch | — |
| 2 | Low-risk reads | Library (stats, series, search, history); Diagnostics read-only (deps, versions, running jobs, log); Reader via the served HTML | none |
| 3 | Extract job runners | Move every `run_*_job` / bulk starter out of tab files into `services/`, Streamlit calling them unchanged | none. Pure refactor, safest large win |
| 4 | API host + jobs API | Stale cell, corrected 2026-09-28: D1 decided **its own process**, not an in-process thread -- and `api/__main__.py` already runs `python -m api` as its own uvicorn process today, so that half of this row is already built. What's left: `POST/GET /jobs`, cancel; SSE after Step 44. See the slice breakdown right below this table -- D1's own 4 fixes are real prerequisites, not optional polish, and are being built as their own narrow slices rather than one big Phase 4 PR. | D1 |
| 5 | Settings service + standalone Translate | Keys/settings model; the Translate tab in React | D2 |
| 6 | Workspace, stage by stage | Export → Diarize → Transcript → Dub → Pick/create → Source → Translate → Review | phases 3–5; §4 tests per stage |
| 7 | Scanlate editor | React canvas editor | phase 4 |
| 8 | Sources / Discover / Live | Local-only marking for sign-in; SSE for Live | phases 4–5 |
| 9 | Admin actions | Install/upgrade, reset, restore via the API, if ever | D5 / M8-H |
| 10 | Retire Streamlit screens | Per screen, only after M8-A-style real-device checks | each screen's gate |

### 5.1 Phase 4 broken into slices (added 2026-09-28)

Phase 4 as one PR would bundle a schema change, a settings-storage
migration, and a new HTTP surface -- too much for one reviewed step, and
not how every slice before this one was actually built (Slices 2/4/5 were
each one small, narrowly-scoped thing). D1's own four fixes are the real
prerequisites; splitting them out by risk and dependency:

| Slice | Goal | Depends on | Size |
|---|---|---|---|
| **6** | ✅ Built. D1 fix 4: guard every `db.init_db()` `ALTER TABLE` against a concurrent-process race (`sqlite3.OperationalError: duplicate column name`) | none | Small -- one helper, 50 mechanical call-site edits |
| **7** | ✅ Built. D1 fix 1: a small SQLite job-record table (`db.job_records`, records only, no resume) so a job started in one process is visible from another -- `background_jobs.py` mirrors every status transition (queued/running/done/error/cancelled), never per-progress-tick; a mirror-write failure is swallowed (logged), never breaks the job it describes | Slice 6 (touches the same `init_db` migration path) | Small-medium |
| **8** | ✅ Built, read-only half only. `GET /api/jobs`, `GET /api/jobs/{id}` through the Slice 7 table. **`POST /api/jobs/{id}/cancel` deliberately not built**: `request_cancel()` only sets a flag in the calling process's own in-memory `_jobs` dict, a no-op from a different process; making it cross-process means the *owning* process has to notice the request, which `is_cancel_requested()`'s existing single choke-point (every cooperative job-loop call site already funnels through it) could do with a throttled DB fallback check -- but that's a real latency/IO-cost design decision (how often to poll, whether to cache a hit), not a mechanical follow-on to the read side. Left as its own, not-yet-scoped follow-up rather than rushed in here. (Cancel was later built in Slice 22.) **Job results now served (branch `api-job-results`):** `JobRecord` gains `result` (allowlisted, `redact_for_support`-ed, size-capped projection of the job's `set_result` dict, mirrored to `job_records.result_json` so it survives a restart) and a normalised `outcome` (`ok`/`failed`/`cancelled`/`partial`/`kept_existing`) + `outcome_message`, so a "done" job that failed or was cancelled no longer reads as success. Transcribe warnings (`gpu_fallback`, `word_align_error`, `forced_align_error`) map to `partial`; a cancelled bulk run maps to `cancelled`. `drama_id` / `?drama_id=` still not served. | Slice 7 | Small |
| **9** | ✅ Built, narrower than first scoped. D1 fix 2 turned out to be much smaller than "every module-global settings read across the app" once actually audited: a repo-wide grep for bare module-level settings globals found API keys already resolve through `.env`/environment (D2, already cross-process-safe by construction) and source-adapter settings already persist through `sources/store.py`'s own table -- the *only* real instances of "a setting living only as a Python module global, reset every restart, invisible cross-process" were D1's own two named examples, `background_jobs.py`'s `_gpu_limit_enabled`/`_notify_on_completion`. New `db.app_settings` table (JSON-encoded value, upsert, same shape as `sources/store.py`'s own settings table but deliberately kept separate -- general app settings, not the source-adapter system's own domain) + `get_app_setting`/`set_app_setting`. `background_jobs.py`'s two toggles now read/write through it via new `get_gpu_limit_enabled()`/`get_notify_on_completion()` getters, reading fresh each check (only checked at job start/finish, never a hot per-tick loop) rather than cached; a read failure fails open (GPU limit stays on) or closed (no notification) rather than ever breaking the job it's checked from. Everything else in `tabs/settings_tab.py` (OCR backend, default engine, etc.) stays Streamlit-session-only, deliberately out of scope -- those were never module globals to begin with, just ephemeral UI state, a different (and much larger) problem than D1 fix 2 actually named. | D2 (server-side-only keys, already decided) | Turned out small once scoped -- see note |

D1 fix 3 (model caches load once per process) needs no code change --
already acceptable per D1's own text. Slices 6, 7, 8's read half, and 9
are all built; a cross-process cancel mechanism (8's other half) is the
one item left in this table with no code yet.

**Slice 10 — ✅ Built (2026-09-28).** Phase 5's settings half: read-only
`GET /api/settings` (engine key presence as booleans only, never a value
-- D2; plus Slice 9's two `app_settings` toggles). New
`services/settings_service.py` (`ENV_NAMES`, `resolve_key`, `key_status`,
`get_settings_overview`) is the single canonical copy of the env-var
mapping `tabs/settings_tab.py` previously kept as its own `_ENV_NAMES`
dict -- the tab now imports it rather than maintaining a second copy that
could drift. Scoped via a `migration-architect` pass first; deliberately
excludes a write endpoint (writing a secret to disk over HTTP is a
separate, higher-risk slice of its own, same reasoning as Slice 8 splitting
cancel out) and excludes non-key Settings state that was never a module
global to begin with (default engine, default locale, OCR backend, etc. --
still Streamlit-session-only, unchanged). Unblocks Translate-standalone
(needs server-side key resolution under D2) and, later, any Workspace
stage that calls an engine.

**Doc-freshness note from the Slice 10 scoping pass:** §3.2's Workspace
stage line numbers are stale (the file is 359 lines shorter than when that
table was written, from unrelated earlier edits), but every stage's own
size/order is unchanged -- offsets moved by a constant ~-361 lines, not a
restructure. Also, Phase 3 (job-runner extraction) turned out to already
be complete: every `run_*_job` name §3.2/§5 called "still in
`tabs/workspace_tab.py`" is already in `services/workspace_job_service.py`
(including `run_bulk_series_translate_job`, which the doc had misattributed
to `library_tab.py`). Neither is fixed here (scoping passes don't edit this
doc); flagging for whoever next touches §3.2's numbers.

### 5.2 Parallel-slice guardrails (added 2026-09-28)

Slices 2 and 5-10 were each built sequentially, one session at a time --
correct for slices that touch shared files (`db.py`, `background_jobs.py`)
or depend on each other. But not every pair of remaining slices does: two
slices are safe to build **concurrently, in separate sessions**, only when
**all** of the following hold, checked explicitly before starting, not
assumed:

1. **File-disjoint.** Each slice's new/edited files (per its own migration
   map) don't overlap, *except* for a small fixed set of append-only shared
   files: `api/server.py` (router registration), `api/schemas.py` (new
   Pydantic models), `FILE_ORGANIZATION.md`, and this doc. Two slices each
   adding their own new lines to those is fine; two slices editing the
   *same* line, or one slice's logic living inside a file the other also
   needs to change, is not -- that pair goes back to sequential.
2. **No shared-file edit lands mid-flight.** The lead applies the shared-
   file edits (routers list in `api/server.py`, new schema classes) itself,
   once, after each worker's own files are done and tested -- never while
   both workers are still active, and never let a worker touch those files
   directly.
3. **Dependency-clean.** Neither slice's migration map lists the other as
   a prerequisite, and neither reads a table/function the other is still
   in the middle of adding.
4. **Independently reviewable.** Each still gets its own PR, its own
   `code-reviewer` pass, its own entry in this doc and the roadmap's
   session notes -- parallel build time never means merged review or a
   combined PR (same rule as batched-but-sequential steps in the root
   `CLAUDE.md`).

**Slices 11 and 12 — ✅ Built in parallel (2026-09-28), the first real use of
the guardrails above.** Two `implementer` agents ran concurrently in the
same checkout, each owning only its own two new files (never `api/schemas.py`,
`api/server.py`, or the other agent's files); the lead added both routers,
both schema blocks, and both `api/server.py` registrations itself afterward,
once, after both agents reported done -- exactly the sequencing guardrail
#2 above describes. No file conflicts, no coordination needed mid-flight.
Merged as two separate PRs per guardrail #4, even though built together.

**Slice 11** (§3.7, Translate-standalone's read-only half):
`services/translate_service.py` -- `list_engines()` (name/label/free/
models/`key_configured`, built on Slice 10's `settings_service.key_status`;
`test_offline`/`nllb`/`ollama`/`libretranslate` always report
`key_configured: True` since none needs a real hosted-API key -- see the
service's own docstring for the reasoning) and `list_history()`. New
`GET /api/translate/engines`, `GET /api/translate/history`. Actually
translating (a real network call) and clearing history (a write) stay out
of scope, deferred to a later slice.

**Slice 12** (§3.2, Phase 6's first Workspace stage, Export):
`services/export_service.py` -- `get_export_readiness(drama_id)` reusing
`reader_service`'s own drama/lines-loading pattern, reporting line/
translation counts plus the same overlap (`subtitle_formats.clamp_overlaps`)/
Auto QC (`auto_qc.find_issues`)/dense-line (`subtitle_formats.dense_lines`)
issue counts the Streamlit Export tab already computes read-only. New
`GET /api/export/dramas/{id}/readiness`. Flagging a line, generating a
subtitle file, and inlining translation notes are all writes/file-output
and stay Streamlit-only for now.

**Slice 13 — Translate-standalone's write half (2026-09-28).**
`services/translate_service.py` gains `translate()`: resolves the engine's
key server-side (`resolve_api_key`, per-engine -- `test_offline` gets the
literal `"offline"`, `nllb` gets `None`, `ollama`/`libretranslate` fall
back to the literal `"local"` when unconfigured, everything else goes
through `settings_service.resolve_key`), builds the engine via
`translate_engines.get_engine`, calls `translate_engines.standalone_translate`,
and saves the result to history the same way `tabs/translate_tab.py`'s own
"Translate" button does. New `POST /api/translate` (`TranslateRequest` ->
`TranslateResponse`, never accepts or returns a key value -- D2). Raises
`InvalidInputError` (422) for an unknown engine, `UnsupportedOperationError`
(400) for a refused engine/direction pair, `DependencyUnavailableError`
(503) for a missing key; any other engine failure (a real network error)
falls through to the API's own generic 500, never leaking its raw text.
Clearing history is still out of scope -- a separate write action.

**Slice 14 — Export's subtitle-text generation (2026-09-28).** On closer
read, the Export stage's "write half" splits into two genuinely different
risk classes: flagging (a real `db.save_lines` write) and subtitle-file
generation (a pure function -- lines in, text out, no state mutated at
all). This slice does the second, safer half only. `services/
export_service.py` gains `generate_subtitle_text(drama_id, fmt, field,
include_notes=False, wrap_chars_en=None, wrap_chars_source=None)`: trims
overlapping cues the same way the Streamlit tab does
(`subtitle_formats.clamp_overlaps`) before generating SRT
(`core.lines_to_srt`/`lines_to_bilingual_srt`) or VTT
(`subtitle_formats.lines_to_vtt`) text, optionally wrapping long lines
(`subtitle_formats.wrap_lines`) and inlining saved translation notes
(`db.list_translation_notes` + `translation_guide.group_notes_by_line`).
New `GET /api/export/dramas/{id}/subtitle` returns the text as a
plain-text download (`Content-Disposition: attachment`, `fmt`/`field`
validated both at the route (a regex `Query`) and the service layer, for
a caller that skips the route). Still explicitly out of scope: ASS export
(needs the interactive per-drama style state
`_subtitle_style_fragment` builds in Streamlit -- no API contract for
that yet), EPUB/audiobook/burned-in-video export (each its own
subprocess/library dependency), and the readiness page's flagging
actions (a write, deferred to its own slice).

**Slice 15 — Export's flagging actions (2026-09-28).** The real database
write Slice 14 deliberately deferred. `services/export_service.py` gains
three functions, each writing ONLY `flag`/`flag_note`
(`db.save_lines(..., fields=("flag", "flag_note"))`) -- a field-scoped
write that can't clobber a concurrent edit to a line's text/timing/
speaker, same discipline as every other background-job write in this
app:

- `flag_overlapping_lines(drama_id)` -- flags every currently-
  overlapping, not-yet-flagged line (`subtitle_formats.OVERLAP_FLAG`),
  same as the tab's "Flag overlapping lines for review" button.
- `flag_dense_lines(drama_id)` -- flags every line too dense to read
  (`subtitle_formats.flag_dense_lines`), same as the tab's dense-line
  button.
- `run_auto_qc_flagging(drama_id)` -- runs Auto QC's factual-detail
  check (`auto_qc.run_auto_qc`) with the drama's own series glossary/
  character names, same as the tab's own `_run_auto_qc` helper (reused
  logic, not duplicated).

New `POST /api/export/dramas/{id}/flag-overlaps`, `.../flag-dense-lines`,
`.../flag-auto-qc`. Each is a no-op-safe action -- a 0 count is a normal
result, not an error.

Export's own read/write actions are now complete: ASS export (needs the
interactive per-drama style state `_subtitle_style_fragment` builds in
Streamlit -- no API contract for it yet) and EPUB/audiobook/burned-in-
video export remain deliberately out of scope, each its own future
slice.

**Slices 16 and 17 — built in parallel (2026-09-28), the second real use
of the §5.2 guardrails** (the first was Slices 11+12). Two `implementer`
agents ran concurrently, each owning only its own files -- Slice 16
created two brand-new files, Slice 17 extended two files Slice 16 never
touched -- so this pair was file-disjoint by construction, not just by
scoping care. The lead added both routers/schemas/server registrations
and fixed one real defect found during integration (Slice 16's own
entry has the detail). Merged as two separate PRs per guardrail #4.

**Slice 16** (§3.2, Phase 6's second Workspace stage, Diarize):
`services/diarization_service.py` -- `get_diarization_config(drama_id)`
(read-only: `hf_token_configured` as a boolean only, never the token
itself -- D2; `expected_speakers` from `diarize.load_last_speaker_count`;
`audio_available`) and `start_diarization_run(drama_id, expected_speakers)`,
which starts the same real, GPU-touching background job
`_render_speaker_rerun` does (`background_jobs.start_process_job` with
`diarize.diarize_subprocess_worker`). New `GET /api/diarization/dramas/
{id}/config`, `POST /api/diarization/dramas/{id}/run`. Deliberately does
not duplicate job-status polling -- the existing `GET /api/jobs/{job_id}`
(Slice 8) already covers any job id, including this one. **Integration
fix**: the agent's own first draft raised `NotFoundError` for "drama
exists but has no audio yet" -- corrected to `UnsupportedOperationError`
(400) during integration, since the drama itself isn't missing, just not
in a state that supports this action (matches
`services/service_errors.py`'s own documented distinction). The "run
diarization during alignment" checkbox and the turns-to-lines merge stay
out of scope -- a future Transcript/Align-stage slice's concern.

**Slice 17** (Translate-standalone's one remaining deferred piece):
`services/translate_service.py` gains `clear_history(confirm: bool =
False)`, raising `InvalidInputError` unless `confirm=True` -- an
explicit-opt-in gate translating `tabs/translate_tab.py`'s own
checkbox-then-button UI pattern into API terms, rather than a bare
delete. New `DELETE /api/translate/history?confirm=true`.

**Slice 18 — Export's EPUB generation (2026-09-28).** `services/
export_service.py` gains `generate_epub(drama_id, field="en")`, reusing
`epub_io.export_epub` verbatim -- the same action as the Export tab's own
"Generate EPUB" button, gated the same way (`drama["content_mode"] ==
"novel_narration"` -- `UnsupportedOperationError` otherwise). Writes the
.epub to the drama's own directory (`translated.epub`, same path the
Streamlit tab already uses, so a resolved `[[IMG:...]]` placeholder's
`epub_images` cache stays put) then reads it back as bytes to return --
read-only from the caller's point of view even though it does touch disk
internally. New `GET /api/export/dramas/{id}/epub` (binary download,
`application/epub+zip`). Missing the optional `ebooklib` dependency maps
to `DependencyUnavailableError` (503), same tier as any other
optional-package gap. Audiobook and burned-in-video export remain out of
scope -- both need a real `ffmpeg` subprocess, a different risk class
than a pure-Python library call.

**Transcript stage scoping pass (2026-09-28) -- found a real ordering
conflict, not yet resolved.** A `migration-architect` pass to scope
Transcript per §3.2's build order (Export → Diarize → **Transcript** →
Dub → Pick/create → Source → Translate → Review) found that the
"Transcript" *tab*'s own body (`tabs/workspace_tab.py:1960-2410`) holds
only settings (novel-reference upload, the "Build a glossary from this
novel" expander -- a real LLM call, Whisper/alignment/ASR-backend
pickers). **The actual transcription trigger, job start, and result-
apply logic live inside the `tab_translate` block instead**
(`workspace_tab.py:2940-3507`, confirmed against `stage_labels` and the
`st.tabs()` unpacking -- not a line-number drift, a genuine cross-tab
split), and nearly every input that action consumes (`audio_file`,
`transcript_mode`, `content_mode`, etc.) is set up in the *Source* tab,
not Transcript -- a stage this build order hasn't reached yet. So
"Transcript" can't be cleanly extracted as one self-contained slice the
way Diarize/Export were, without either (a) pulling a slice of Source
forward as a prerequisite, or (b) narrowing this round to Transcript's
config + the novel-glossary builder only (both genuinely buildable now,
independent of the ordering question) and deferring the actual
run/apply action to a later slice once Source exists. **This is a
build-order decision, not a slice-scoping one -- needs the user's/
planning session's call before building anything under the "Transcript"
name.** Full detail (proposed services, API contracts, five other
unresolved design questions -- job-result "apply" as a stateless HTTP
step, autotune's self-chaining behavior, sync-vs-background-job for
glossary extraction, a missing "job already running" error class, and
`min_silence_ms`'s session-only persistence) is in this scoping pass's
own report, not yet copied into this doc; ask the session that ran it if
picking this back up.

**Decision (2026-09-28):** the user chose to pull a slice of Source
forward as a prerequisite, rather than narrowing Transcript or promoting
Dub. A second `migration-architect` pass scoped exactly which slice.

**Slice 19 — Source-stage config, the Transcript prerequisite
(2026-09-28).** `services/source_service.py` gains `get_source_config`
(read-only: `source_language`, `chinese_script`, `content_mode`,
`has_audio_pipeline`, `audio_available`, `has_video_source`,
`transcript_mode`, `transcript_mode_options`, `has_raw_novel_context`)
and `update_source_config` (a field-scoped partial update -- only fields
actually passed are validated/written, mirroring `db.update_drama`'s own
shape). Replicates `tab_source`'s own `content_mode == "streamer_vod"` ->
`media_type` sync exactly, including its one-directional-only behavior
(switching away from `streamer_vod` never reverts `media_type` -- an
existing behavior, not a gap this slice introduces). New
`GET /api/source/dramas/{id}/config`, `POST /api/source/dramas/{id}/config`
(POST for consistency with every other mutation endpoint in this
migration -- no PATCH precedent exists yet).

**Deliberately out of scope, by design**: audio/video upload and
yt-dlp download (a materially different risk tier -- multipart upload,
an `ffmpeg` subprocess -- than any config write so far) and
`transcript_text`/`novel_narration_text` (neither is persisted ahead of
the transcribe/chunk action today either). Both are folded into a future
"transcribe-and-align" action slice instead, which preserves the current
one-click upload-then-transcribe behavior rather than inventing a new
two-step flow. `transcript_mode == "hardsub_ocr"` on a drama with no
video source raises `UnsupportedOperationError` (400) -- a new
validation the Streamlit UI never needed, since the radio option simply
wasn't rendered; the API has to enforce explicitly what the UI enforced
by omission.

**Slice 20 — Transcript-stage action, job-does-everything
(2026-09-28).** A third `migration-architect` pass scoped the
transcribe-and-align action (its real trigger, `run_prep`, lives in
`tab_translate`, not `tab_transcript`) and found that today's "apply the
finished job's result to lines" step runs as a side effect of Streamlit's
own render loop, densely interleaved with UI calls -- no clean way to
expose that over a stateless API, and diarization's Slice 16 had already
punted on an easier version of the same problem. **User decisions
(2026-09-28):** (1) the background job itself does the whole pipeline
(ASR, alignment, DB write, optional diarization chain-start) and reports
one "done" outcome, rather than a separate "apply" call; (2) persist the
remaining Whisper-tuning knobs now; (3) fix Slice 16's silent-duplicate-
start gap in this same PR.

New `services/transcribe_service.py`: `get_transcribe_config` /
`update_transcribe_config` (new per-drama columns `min_silence_ms`,
`vad_threshold`, `beam_size`, `separate_vocals_first`, `separation_
backend`, `realign_long_segments`, `whisper_fast_mode`, `use_groq` --
`transcript_mode`, `whisper_size`, `alignment_method`, `asr_backend_
choice` were **already** persisted; the scoping pass had wrongly said
none were, corrected here by re-reading `db.py`/`workspace_tab.py`
directly) and `start_transcribe_run`, whose job body
(`_run_transcribe_and_apply_job`) reuses `transcribe_for_timing` and the
same empty-result-never-wipes-existing-lines safety rule as Step 25 item
2. New `ConflictError` (409 `conflict`) in `service_errors.py` -- none of
the four existing classes fit "valid request, but a job is already
running" -- and **fixes a real pre-existing bug in Slice 16's**
`start_diarization_run`, which never checked `start_process_job`'s
return value and silently reported success on a duplicate start.

**Deliberately out of scope, by design:** `hardsub_ocr` transcript_mode
(raises `UnsupportedOperationError`), the synchronous `chunk_and_tag`
novel-narration path (needs its own benchmark pass before deciding a
synchronous API call fits), the experimental `qwen3_asr` /
`qwen3_forced_align` backends, audio upload (unchanged from Slice 19),
auto-tune (a separate Source-tab feature), and `use_gpu` (a bare
`st.session_state` toggle with no server-side source of truth --
hardcoded `False` in the job body, documented in its docstring), and
Streamlit's automatic `initial_prompt` derivation from the series glossary
and raw-novel excerpt (the API takes an optional client-supplied
`initial_prompt` instead). An independent code review of this slice found
and fixed: persisted `source_language`/`chinese_script` being ignored
(now default to the drama's own stored values, validated), diarization
chained on the vocals-only file instead of the original audio, no
`has_audio_pipeline` check (novel_narration dramas are now rejected), and
`use_groq` with no key failing late instead of at start.

**Slice 21 — Transcript-stage action, hardsub_ocr (2026-09-28).**
Extends `transcribe_service.py`'s job-does-everything action to
`transcript_mode == "hardsub_ocr"` (reading captions burned into video),
via `hardsub_ocr.extract_hardsub_subtitles` -- the OCR cues already carry
real per-cue timing straight from the video, so unlike Whisper's own text
there's no separate alignment step, matching `run_hardsub_ocr_job`'s own
reasoning. `start_transcribe_run` now branches on `transcript_mode`:
hardsub_ocr requires a video source (not audio) and skips the ASR-only
steps (vocal separation, Groq, word-realign) entirely. New per-drama
`hardsub_ocr_backend` / `hardsub_interval_sec` columns (same "previously
session-state only" gap Slice 20 closed for the Whisper knobs);
`tesseract_cmd` stays a per-request, client-supplied value rather than a
persisted setting, since Streamlit's own equivalent is a global Settings
value with no `settings_service`-backed home yet.

**Slice 25 — Dub config and pacing reads (2026-09-28).**
New `services/dub_service.py` and `/api/dub/dramas/{id}/config` and
`.../pacing` (read-only). Config reports the TTS engine options, each
speaker's resolved edge/offline voice and engine (the same resolution the
Generate button uses), whether generating needs the GPU, how many lines are
speakable, and whether a finished track exists; pacing reports each line's
fit/stretched/overflow status from the last run. D2 discipline: no
filesystem path, GPT-SoVITS URL or secret is returned -- only booleans such
as `gpt_sovits_configured` and `has_clone_ref`. Out of scope: the Generate
job (Slice 26), voice/character CRUD, per-line preview, and track download.
Speakers and lines are read from the database, not the browser's unsaved
session lines. No new db column.

**Slice 27 — Export ASS subtitle text (2026-09-28).** `export_service.
generate_ass_text` / `get_ass_style_options`, exposed as `POST /api/export/
dramas/{id}/ass` (text download) and `GET /api/export/ass-style-options`.
Style is per-request (preset plus optional overrides, only client-set
fields override) with no new column. Lines come from the DB, not the
Streamlit tab's unsaved session copy, so unsaved edits will differ.
Explicit colour/range validation replaces the tab's silent white/alignment
fallbacks. Out of scope: burned-in video, audiobook, package zip,
mark-as-exported, Anki (Reader tab).
*Hardening H2:* `_ass_field`/the `Title:` header now replace control characters (newlines
would inject extra `Style:`/`Dialogue:`/`[Events]` lines) and `style.font` with control
characters is a 422; `speaker_colors` is capped (200 entries, 100-char labels; kept rather
than intersected with the drama's speakers); ASS `wrap_chars_*` is `le=200` like SRT/VTT.
Explicit JSON `null` for a style field stays a 422 (omit the key instead).

**Slice 46 -- Glossary, instructions and catalogues.** `services/glossary_service.py`
plus `/api/glossary/*`: series glossary term list/upsert/delete, project and series
instructions, and the read-only option catalogues (style presets, term categories/policies,
workflow tiers). Terms are series-owned, so every write/delete verifies the term belongs to
the drama's own series first (`db.update_glossary_term`/`db.delete_glossary_term` take a bare
id with no series check); a series-less drama reads as empty and refuses term writes and
series instructions (400). Delete needs `confirm=true`, mirroring the tab's Step 71 confirm
checkbox. Text, list and instruction lengths are capped. Out of scope: LLM term extraction (a
paid call, later slice), presets CRUD, characters.
*Hardening H2:* a POST without `id` for an existing `term_original` now starts from that term's
stored values (like update-by-id) so omitted notes/aliases/banned/enforce_exact survive;
explicit `""`/`[]` still clears, and a brand-new term still gets fresh defaults.

**Slice 47 — Review read-only line views (2026-09-28).**
`services/review_lines_service.py` + `api/routers/review_lines_routes.py`
(`/api/review/dramas/{id}/...`): paged/filtered line list, transcript search,
find-and-replace preview, coverage check, pacing check, per-line provenance
and original-transcript text. Everything reads the database by permanent line
id, not the browser's unsaved session list, so coverage/pacing results can
differ from what the Streamlit tab shows until edits are saved. The
find-replace preview is a POST only because of its body and writes nothing;
with `use_regex` the pattern runs server-side with no execution timeout (a
ReDoS risk), so only find/replace length (500) is capped -- the API stays
local/trusted-network only. `re.error` (bad pattern or bad group reference in
the replacement) maps to 422, not 500. Caps: page_size 200, search limit 200,
search/find/replace text 500 characters. Out of scope: all writes, player/
media, translation-memory suggestions, LLM tools, bulk modes, and
history/versions/notes reads (Slice 48).

**Slice 48 — Review read-only records (2026-09-28).**
`services/review_records_service.py` + `api/routers/review_records_routes.py`
(`/api/review/dramas/{id}/...`): history list/snapshot, versions list/compare,
notes list/Markdown, stored consistency issues, emotion summary, edit
tendencies, TM suggestions. The service checks drama ownership itself,
because `db.get_line_history_snapshot` and `db.get_translation_version` take
only an id; another drama's record gives the same 404 as a missing one.
Snapshot lines omit `flag`/`flag_note`/`sfx` because
`save_line_history_snapshot` never stored them, and `dub_filename` is a bare
filename. Notes carry both `line_id` and the current `line_idx`; TM
suggestions are `line_id`/`line_idx`/`zh`/`en`/`suggestion`/`similarity`/
`exact`/`entry_id` (empty when the drama has no series; optional repeated
`line_id` filter); notes Markdown is served inline as `text/markdown`.
Out of scope: all writes (restore/activate/delete/add/dismiss), LLM analysis,
job starters, bulk modes.

**Slice 23 (non-secret Settings writes + persisted `use_gpu`):** `POST /api/settings`
takes optional booleans (`gpu_limit_enabled`, `notify_on_completion`, `use_gpu`,
`gemini_free_tier`; `extra="forbid"`, applied via `exclude_unset`) and returns the updated
overview, which now also reports `use_gpu`/`gemini_free_tier` (both default False, stored in
`db.app_settings`). `settings_service.set_settings` validates the whole batch against a typed
allow-list before writing anything; unknown key or non-bool -> `InvalidInputError` (422, the
value is never echoed). Keys, URLs and paths are never accepted (D2; key writes remain a
separate gated slice). `start_transcribe_run` now reads `settings_service.get_use_gpu()` and
passes it as a new trailing job parameter `use_gpu=False`, replacing the hardcoded
`use_gpu=False` in `transcribe_for_timing`; diarization keeps its own path.

**Slice 49 -- process-job completion hook (API-started diarization applies its own result).** A process job's `result` lived only in the starting process's memory and only Streamlit's render loop persisted it, so an API-started diarization (Slice 16 endpoint, or the chain-start inside the Slice 20/21 transcribe job) ended "done" with its speaker turns never saved. `background_jobs.start_process_job` now takes optional `on_done(job_id, result)`, called in the watcher thread after a successful result and before the job is marked "done"; a raising hook ends the job "error" (redacted message, logged), it is not called on error/cancel, and queued GPU starts carry it through the queue. `diarization_service.apply_diarization_result` ports the DB half of Streamlit's `_apply_diarization_job_result`/`_apply_speaker_turns` (save turns, `merge_speakers`, character upserts, field-scoped `save_lines(fields=("speaker","speaker_manual"))`); both `start_diarization_run` and the transcribe chain-start pass it. One deliberate difference: Streamlit skips the merge when manual lines would change and asks the user; with no user to ask, the API merges with `overwrite_manual=False` by default (manual corrections never undone). B-13 follow-up: `POST /api/diarization/dramas/{id}/run` takes optional query params `overwrite_manual` (default false) and `confirm`; `overwrite_manual=true` without `confirm=true` is 422 (nothing started), and with both the result replaces hand-corrected speakers, mirroring Streamlit's explicit "Apply and overwrite" button. Writes stay field-scoped (`speaker`, `speaker_manual`). Double-apply with the tab is harmless (same turns file rewritten, merge idempotent). Hard cancel is unchanged.

**Hardening H3 (Slices 25/39/47/48 read services).** GETs no longer create
the drama folder (`db.drama_dir` makes it; these services build the path
without creating it). Review lines return `dub_filename` as a bare filename,
like records. Malformed data degrades instead of 500: non-dict pacing records
are skipped, pacing `clip_ms`/`window_ms` accept floats, a corrupt raw
transcript reads as "none", NULL `line_idx` notes render as line 1, version
lines without `idx` are skipped, and legacy NULL `label`/`created_at` validate.
`tm-suggestions` takes at most 200 `line_id`s (each >= 1) and scans at most the
first 2000 lines. Regex find/replace preview rejects nested-quantifier
patterns such as `(a+)+` and matches only the first 2000 characters of each
line: a mitigation, not a guarantee (Python's `re` has no timeout).

**Slice 43 — Review per-line edit writes (2026-09-28).**
`services/lines_service.py` + `api/routers/lines_routes.py` (`/api/lines/...`,
schemas `Lines*`): partial line edit (`POST .../lines/{line_id}`), dismiss
flag, find-and-replace apply, TM-suggestion accept, note add/delete
(`DELETE .../notes/{note_id}`, no confirm, as in the tab). Every write is
addressed by permanent `Line.id` and goes through `db.save_lines` with an
explicit `fields=` tuple -- never `fields=None`, whose full sync from a stale
list resurrects/deletes rows (a test spies on every call to enforce this).
Concurrency is compare-and-set on client-supplied old values (`expected`
maps field -> value seen; mismatch = 409, nothing written; no new column);
the compare and write are two steps, not one atomic statement. "Editing `en`
clears the flag" and "changing the speaker sets `speaker_manual`" are now
explicit writes (`flag`/`flag_note`, `speaker_manual` added to `fields`);
edit samples and translation memory are recorded as Save edits does.
Find-and-replace apply re-checks each line's current `en` against the
previewed `old_text` and reports `stale` (a line that no longer exists also
counts as stale, unlike the tab, which skips it silently).
`db.delete_translation_note` and the TM entry lookups take only an id, so the
service checks drama/series ownership itself (another drama's note or another
series' TM entry is the same 404 as a missing one). Text caps: 2000 chars for
line/note text, 500 for terms. Out of scope: merge/split/delete lines,
restore original text, LLM tools, bulk modes.

**Slice 26 -- Dub run job.** `POST /api/dub/dramas/{id}/run` (body `DubRunRequest`: `tts_engine`, optional `max_speedup`/`max_slowdown`/`narration_language`) starts the Dub tab's Generate as process job `dub_<id>` via `dub_service.start_dub_run` and returns `{job_id}` to poll at `/api/jobs/{job_id}`. It builds the same voice/offline-voice/clone/emotion inputs and GPU decision as the tab and `cli dub` (TTS uses no glossary/locale). The result is applied by the Slice 49 `on_done` hook (`apply_dub_result`): field-scoped `save_lines(dub_filename [+ start/end for narration])` plus status "dubbed", copying the produced fields by permanent line id onto the CURRENT database lines, so flag/flag_note/speaker and edits made during the run are never overwritten. Errors: unknown drama 404; no speakable text or bad engine/pacing/narration language 422 (the app's InvalidInput status); ffmpeg or the engine package missing 503; duplicate start 409. No paths or secrets in any response. Track download stays out of scope. Real TTS was not verified (tests fake the worker).

**Slice 22 (cross-process job cancel).** `POST /api/jobs/{job_id}/cancel`
flags the job (`job_records.cancel_requested`, new column) and, if this
process owns it, sets the in-memory flag too. The owning process notices via
`background_jobs.is_cancel_requested` (and the process-job watcher), which
checks the DB at most once per 2s per job, so tight loops never hit SQLite
each iteration. Cancellation is asynchronous. Unknown id is 404; an already
finished job is 409 (the flag is cleared on any terminal status, so a reused
job id never inherits a stale request). Only ids/status are returned.

**Migration Slice 28 (artifact download).** `services/artifact_service.py` defines where a job
writes a downloadable output: `<drama folder>/exports/<kind>/<filename>` (`output_path` validates
the bare filename). `GET /api/artifacts/dramas/{id}/{kind}` streams the newest regular file there
with a sanitized `Content-Disposition`; `.../info` returns name/size/kind only (never a path).
Kinds are whitelisted (subtitle, epub, audio, video, archive); clients never send a path;
symlinks and anything resolving outside the kind folder are ignored; errors are fixed text
(404 when missing, 422 for an unknown kind). No job writes artifacts yet -- wiring is later.

**Slice 31 — Media upload (2026-09-28).**
`services/media_upload_service.py` + `api/routers/media_routes.py`:
`POST /api/media/dramas/{id}/upload` (multipart, `file` field; needs
`python-multipart`, added to `requirements-core.txt`). Same result as the
Source tab's upload: stored as `source<ext>` in the drama folder (audio:
`audio_filename` set; video: audio extracted to `audio.wav`, both
`audio_filename` and `source_video_filename` set). The client filename is
never stored or returned (only a whitelisted extension is read: mp3 wav m4a
flac ogg mp4 mkv mov webm); the body streams to a temp file in the drama
folder, is capped by `BAIHE_MAX_UPLOAD_MB` (default 2048; over-limit is a 422
with fixed text and the partial file is deleted) and is atomically renamed.
409 if a job is running for the drama, 404 for an unknown drama. Response is
`name`, `size`, `kind` only. Uploading replaces the previous `source<ext>`
of the same extension. Video audio extraction runs synchronously in the
request (ffmpeg). Out of scope: yt-dlp URL download, ref-audio/cover uploads.

**Slice 33 -- Novel narration chunk & tag.** `GET /api/narration/dramas/{id}/config`
(booleans/enums only: novel text attached, per-engine `key_configured`, existing line
count, job running) and `POST /api/narration/dramas/{id}/run` (`{engine?, model?}`,
default engine `claude`; claude/deepseek/gemini/ollama) -> `{"job_id": "narration_<id>"}`.
The job does everything (Slice 20 pattern): chunks the drama's attached
`novel_narration_source.txt`, tags speakers with `tag_speakers_by_id` (id-keyed; a
wrong-length result falls back to all Narrator), upserts characters, takes a "before chunk
& tag speakers" history snapshot, then replaces the drama's lines (the same full replace
Streamlit/CLI do; the lines are brand new) and sets status `aligned`. Errors: unknown drama
404, no novel text or non-LLM engine 422, no key 503 (Streamlit's silent all-Narrator
fallback is deliberately not offered), duplicate run 409; failed-job errors are redacted by
`background_jobs`. Not verified against a real LLM (fake engine only).

**Slice 37 -- Metadata auto-fill and media analysis.** `POST
/api/metadata/dramas/{id}/analyze-media` (ffprobe of the stored video/audio:
`duration_seconds`, `has_video`, `has_audio`, `audio_track_count`,
`sample_rate`; no paths), `POST .../autofill` (`{url | page_text, engine?}`;
returns a `suggestion` of title/author/studio/director/voice_actors/summary
(+ `source_url` when a URL was used) and writes nothing) and `POST
.../autofill/apply` (whitelisted fields only, via
`drama_service.update_drama_metadata`). URLs must be http(s) and every
resolved address (and redirect hop, followed manually, max 3) must be global,
else 422; no key, unreachable page, LLM failure or missing ffprobe is a 503
with fixed text (exceptions are never echoed); unknown drama 404. Keys come
from server settings, never the request. The connection is pinned to the validated IP
(Host/SNI/cert keep the hostname), closing the DNS-rebinding gap; the LLM call's timeout is the engine's own. No
JS-rendered fetch (Streamlit's fallback) -- paste text instead. Not verified
against a real LLM or site (tests mock everything).

**Slice 38 -- Novel attach + chapter OCR (2026-09-28).** `services/novel_attach_service.py` +
`api/routers/novel_routes.py`: `POST /api/novel/dramas/{id}/attach-text` (JSON, 2M-char cap),
`POST .../attach-epub` (multipart; stdlib zip/HTML only, entry-count and uncompressed-size caps, rejects
traversal/absolute names/symlinks, no entity resolution, only plain text stored, the .epub is not kept),
`POST .../ocr-chapter` (multipart PNG/JPG images staged under generated names, background job
`ocrchapter_{id}`, backend per source language, `mode` append|replace) and `GET .../status` (booleans and
counts only). Text goes to `novel_narration_source.txt`, which Slice 33 reads. 404 unknown drama, 409 job
running or duplicate OCR, 422 bad input, 503 OCR backend not installed. Deliberate differences: the tab's
EPUB chapter-range picker and image extraction are not offered; no stored-image OCR (none exist); the
Settings tesseract path is not applied. Tests use a fake OCR; no real OCR was run.

**Slice 44 -- Review checks + AI jobs (2026-09-28).** `POST
/api/review-jobs/dramas/{id}/{consistency|emotion|notes|flag|fix-flagged}`
each start a background job (ids `consistency_`/`emotion_`/`notes_`/`flag_`/
`fixflag_{id}`, the tab's own, so either side sees a running one) that does
everything, DB write included, by reusing the tab's runners
(`run_consistency_job`, `run_emotion_job`, `run_translation_notes_job`,
`run_flag_job`, `run_fix_flagged_lines_job`). Writes are field-scoped by
permanent line id: `("flag","flag_note")` for flag, the consistency-issues /
emotions / notes tables, and `("zh","en","flag","flag_note")` for fix-flagged
(as the tab does); `db.save_lines` skips a field the user changed meanwhile.
Body: optional `engine`/`model`/`gemini_free_tier` (default: the drama's
engine); emotion adds `use_audio_cues` (default: drama has audio); fix-flagged
adds `job_cost_cap_usd` (same cap/monthly-refusal machinery as translate) and
uses the drama's stored Whisper size, source language and the persisted GPU
toggle. Keys are resolved server-side only. Errors: unknown drama 404;
duplicate start 409; no key 503 with fixed text; bad engine/body 422; no
lines, nothing translated (flag), nothing flagged (fix-flagged), a
translation-only engine (all but fix-flagged) or a cap refusal 400. Job
errors are redacted by the job runner. Out of scope: the Claude/Gemini bulk
(batch) variants, Auto QC, pacing auto-shorten. Notes need an engine with
`supports_reference`; otherwise the job finishes with 0 notes (as the tab).
Real LLM/Whisper runs were not verified (tests stub the helpers).

**Slice 29 -- Audiobook export job.** `POST /api/export/dramas/{id}/audiobook` (no body) starts thread job `audiobook_<id>` (`services/media_export_service.start_audiobook_export`) and returns `{job_id}`. The job encodes the drama's `narration_track.wav` to an AAC `.m4b` with chapter markers via `dub.export_narration_m4b` (fixed ffmpeg argument list, no shell, no client paths), builds it in a temp folder, and moves it to `artifact_service.output_path(id, "audio", "audiobook_<id>.m4b")`, so a failed run never leaves a partial file; download with `GET /api/artifacts/dramas/{id}/audio`. Errors: unknown drama 404; no lines or no narration audio yet 422 (fixed text); ffmpeg missing 503; duplicate start 409. Job failures carry fixed text with no paths. `audiobook_` and `burned_video_` are now in `background_jobs.DRAMA_JOB_PREFIXES`. Only `.m4b` exists in the app today (no mp3). Real ffmpeg was not verified (tests patch `subprocess.run`).

**Slice 30 -- Burned-in video export job.** `POST /api/export/dramas/{id}/burned-video` (optional body: the Slice 27 `AssExportRequest` -- field, preset, style overrides, speaker colours, notes, wrapping) starts thread job `burned_video_<id>` and returns `{job_id}`. The ASS text is generated and validated at start with `export_service.generate_ass_text` (Slice 27 helpers: ranges, colours, control characters rejected), then the job writes it to a temp folder as the fixed name `subs.ass` and runs `ffmpeg -y -i <stored source video> -vf subtitles=subs.ass -c:a copy out.<ext>` with that folder as the working directory, so the filter string holds nothing client-supplied and needs no path escaping. The result is moved to `artifact_service.output_path(id, "video", "burned_video_<id>.<ext>")` (source extension if mp4/mkv/mov/webm, else mp4); download via `GET /api/artifacts/dramas/{id}/video`. Errors: unknown drama 404; no lines, no stored source video (name re-checked as a bare filename) or bad style 422 (fixed text); ffmpeg missing 503; duplicate 409; job failures carry no paths. Softsub, the SRT hardsub with `force_style`, dub-audio replacement and the vertical clip stay out of scope. Real ffmpeg (including libass) was not verified.

**Slice 50 -- per-line Improve translation and Why this? (2026-09-29).** `services/line_ai_service.py` + `/api/line-ai/dramas/{id}/lines/{line_id}/improve|explain` (POST, synchronous single-line LLM calls like the tab's spinner calls, not jobs). Body: optional `engine`, `model`, `gemini_free_tier`; `improve` also takes `issue` (max 500 chars); unknown fields, including any key, are 422. `improve` returns `{line_id, current_en, suggestion, changed, engine, model}` and never writes: the UI applies it through the Slice 43 field-scoped compare-and-set line patch (the tab's "Use this" also records an edit sample and a translation-memory entry; those are not done here). `explain` returns `{line_id, explanation, engine, model}`. The line is found by permanent id and its stored zh/en is what the model sees. Keys are resolved server-side (`translate_service.resolve_api_key`); missing key 503, unknown drama/line 404, unknown engine or over-long `issue` 422, translation-only/non-LLM engine, or a line with no source or translation 400, engine failure 500 with `redact_secrets` applied. Reuses `line_tools.improve_line`/`explain_translation` unchanged. Deviation from the tab: improve also gets the drama's style guidelines (glossary, learned style, character gender hints), explain gets the glossary; the English-variant locale is not applied because the helpers have no locale parameter. UI pending.

**Slice 45 -- restructure lines + version-history restore.** `services/restructure_service.py` + `/api/restructure/dramas/{id}/...`: `lines/add`, `lines/{line_id}/delete` (needs `confirm=true`, the tab's checkbox bar for deletes), `merge` (2-50 adjacent ids, in order; joined like `core.merge_adjacent_short_lines`), `lines/{line_id}/split` (character offset `at_char` + `expected_zh`, optional `at_time` strictly inside the line else proportional via `resegment.split_times`, optional `en_at_char`; the first piece keeps the id, flag, notes and emotion, the second is new with no flag), `GET resegment/preview` (read-only, rules only), `POST resegment` (job `resegment_<id>`, already in `DRAMA_JOB_PREFIXES`; re-segments AND saves; an Ollama LLM pass runs as a process job applied via the Slice 49 `on_done` hook, other engines/rules in a thread; duplicate start 409), `GET history` (Slice 48's `list_line_history`) and `history/{hid}/restore` (ownership 404; `core.restore_saved_lines`, so Step 25l's id-first matching applies). Every write takes `expected_line_ids` (the drama's ids in order) and returns 409 with nothing written on any difference, is refused 409 while any job runs on the drama (`drama_service.job_running_for_drama`; stricter than the tab, which does not check running jobs), loads lines fresh, takes a history snapshot of them FIRST ("before merge"/"before split"/"before add line"/"before delete line"/"before re-segment"/"before restore"), then does one `db.save_lines(fields=None)` over that fresh list, so ids carry flag/flag_note/speaker/notes/emotions: merged-away lines' notes/emotions are re-pointed via `merged_ids`, deleted/split lines' refs are deleted with their rows, and no orphan or duplicate rows remain (tests cover merge -> restore, delete, split, re-segment with a concurrent writer). Re-segmentation's `confirm` is required when any line LONG ENOUGH to be split (a superset of what a run changes) carries a translation, flag or note; the tab asks only for the lines actually split (deviation: it can't know an LLM run's result up front). Atomicity gap: snapshot, id re-check and `save_lines` are three transactions (db.py has no combined API); guarded by a per-drama in-process lock across load -> check -> snapshot -> save (also taken by the re-segment job's apply step) and an id-set re-read immediately before the save. A full sync from another process (Streamlit) between that re-read and the save is not prevented; a failure after the snapshot leaves only an extra snapshot. Pre-existing gap: history snapshots don't store flag/flag_note/sfx, so a restored line whose id no longer exists comes back unflagged. Add/delete/split have no tab equivalent yet.

**Slice 56a -- Sources registry and status, read-only (S-1, 2026-09-29).** New `services/sources_registry_service.py` and `api/routers/sources_catalog_routes.py` (prefix `/api/sources`; a different file name on purpose, `source_routes.py` is the Workspace Source stage). Sync GETs: `/api/sources` (per adapter: name, display name, content types, languages, `supports` flags via `adapter.supports()`, `import_supported` = has `get_pages` or `get_chapter_text`, so false for missevan and bilibili today, `auth_supported`, `supports_adult_toggle`, `enabled`, `adult_enabled`, health light green/yellow/red, `has_saved_signin` bool only), `/api/sources/{name}` (capability record from `ladder.apply_terms(load_capabilities(...))`: tiers, status, the separate 23k fields, `terms` as read-only information with `terms_enforced:false` (enforcement is off by user decision, nothing is ever labelled permitted), health times and `retry_after`), `/{name}/attempts` (from `store.recent_attempts`), `/settings`, `/profiles`, `/tracked`, `/notifications`. Fixed-path routes are declared before `/{name}`. Unknown source (or the demo source while hidden) is 404. Safety: attempt/tracked URLs are reduced to scheme+host+path (signed tokens live in queries); every free-text field goes through `redact_secrets` plus URL-query and filesystem-path stripping (library dir, Windows, UNC, POSIX, `~`); `/settings` returns numbers/enums/bools and `proxy_configured` only, never the proxy URL; profiles return version summaries, never rules. First-use write: `store.connect()` creates `sources.db` and its schema on the first call, so these GETs can create that file (tests use the isolated library). Tests: `tests/test_api_sources_registry.py` (every adapter serializes, proxy bool only and no proxy/secret/path/query in any response using a seeded fixture, 404s, scrub units). Not covered: the offline demo adapter is only listed when `demo_source_enabled`.

**Slice 56b -- Sources config writes (S-2, 2026-09-29).** All POST on the same router, each verifying the source, domain or id exists first (unknown source 404). `/{name}/enabled {enabled}`; `/{name}/adult {enabled}` (unknown source 404, a known source without `supports_adult_toggle` 400 `unsupported_operation`); `/settings` (partial update, `extra=forbid` model plus a service whitelist of the tab's own keys and ranges: pace 0-60/0-120, concurrent 1-4, retries 0-6, break requests 0-200, break delay 0-600/0-900, interval 0-168, cache mode from `cache.MODES`, three bools). `http_proxy_url` and `page_server_enabled` are not settable through the API (a proxy URL set remotely is an exfiltration/SSRF pivot; the page server opens a port), any unknown key is 422. Pacing floor: `pace_min_delay` below the built-in default (3 s) is 422, unlike the tab which allows 0; a max is raised to its min like the tab; `reset_pacing_state()` runs after saving, as the tab does. `/{name}/health/reset`; `/cache/clear {confirm:true}` (422 without it); `/profiles/{domain}/{kind}/rollback {version}` (`ProfileRejected` is not a `ServiceError`: unknown domain, kind or version all map to `NotFoundError` 404); `/notifications/{id}/dismiss`; `/tracked {source, series_id, tracked, title?, url?, drama_id?}` (untrack only: tracking a NEW series is refused with 400 because the API records no chapters as known, so the first check would announce and possibly auto-import the whole back catalogue; it returns with the Sources search slice). Doubt: tracking fetches nothing, so no chapters are marked known and the first chapter check announces the whole back catalogue (and would auto-import it if `auto_queue_new_chapters` is on); the tab records the fetched chapter list at track time. The pacing floor covers only `pace_min_delay`; a remote client can still set `max_concurrent` up to 4 or turn session breaks off (0), as the tab can. Tests: `tests/test_api_sources_config.py`.

**Step 133 -- auth storage, permission dependency and static test (2026-09-29).** Additive `db.py` tables `users`, `user_permissions`, `auth_sessions` (SHA-256 of session id and CSRF token only), `audit_log`; `services/auth_service.py` (allowlist, deny-by-default catalogue, sessions with idle 14 d / absolute 30 d expiry, audit through `redact_secrets`, in-memory sliding-window rate limiter for step 134's login routes); `api/auth.py` (`require_permission`, `public_route`, `local_only`, `require_engines_allowed`, cookie helpers, `EarlyAuthGate` middleware that refuses anonymous/CSRF-less `/api` requests before the body is read); `python -m api grant-admin <email>` / `list-users`. `BAIHE_API_AUTH=off` (default) keeps today's behaviour and still refuses a non-loopback bind; `on` enforces sessions, permissions and CSRF and drops `/api/docs` and `/api/openapi.json`. Every route declares exactly one permission; the table and judgement calls are in [`remote-access-decision.md`](remote-access-decision.md) ("Step 133"). Also fixed: `sources_catalog_routes` was included after the frontend catch-all, so its GETs were shadowed whenever `frontend/dist` exists. Tests: `tests/test_api_permissions.py` (static route test plus both modes and the adversarial cases), `tests/test_auth_service.py`. Security-review fixes (same day): off mode now refuses every request that isn't direct loopback (`LoopbackOnlyGate`; a same-PC Caddy would otherwise have exposed full owner access), the early gate refuses remote requests to `local_only()` routes before the body is read, `resegment` with `use_llm` and Groq transcription need `engines.paid`, `create_app` enforces the bind check, a demoted admin keeps no opt-in rows, the rate limiter's memory is bounded, and `tests/conftest.py` makes an unconfigured TestClient a loopback client. Open: no login route yet (step 134; requirements listed in the decision doc), so nothing can obtain a session with auth on except a test; jobs are not per-user.

**PC-only delete routes (handoff queue item 2, 2026-09-29, #374).** New `services/delete_service.py` + `api/routers/delete_routes.py` (own router, prefix `/api`, so the owning routers stay unchanged). Seven POSTs, all `local_only()` and all needing body `{"confirm": true}` (StrictBool, extra fields forbidden; 422 otherwise). Each mirrors its Streamlit button, every one of which is a plain Confirm checkbox with no typed word: `/api/media/dramas/{id}/remove` deletes the files named by `audio_filename` and `source_video_filename` (only a plain name inside the drama folder is followed; anything else is left on disk) and clears both fields, lines untouched; `/api/novel/dramas/{id}/raw-novel/remove` deletes `raw_novel_context.txt`; `/api/review/dramas/{id}/versions/{vid}/delete` (a version of another drama is 404; the active version may be deleted, as in the tab, and `was_active` says so); `/api/characters/series/{sid}/characters/{cid}/delete` (`db.delete_series_character`: drama characters keep their copied name, only the link goes; another series' character is 404); `/api/diagnostics/bug-bundles/{id}/delete`; `/api/library/presets/{id}/delete`; `/api/library/voice-bank/{id}/delete` (the bank's clip file goes too). Order as in `drama_service.delete_drama`: 404 (unknown/foreign id, or nothing to remove for media/raw novel) before 422 (confirm), then 409 when `drama_service.job_running_for_drama` for the three drama-scoped deletes (media, raw novel, version). Results are ids and booleans only. Tests: `tests/test_api_deletes.py`.

**Comic viewer backend (2026-09-29).** New `services/comic_view_service.py`, `api/routers/comic_routes.py` and `api/comic_schemas.py` (models kept out of `api/schemas.py` so the slice could be built beside `slice-url-import-backend`). Prefix `/api/scanlate/dramas/{id}`, the read subset of Scanlate S1: `GET pages` (`library.read`; `{drama_id, media_type, reading_mode_default ("paged" for manga, else "vertical"), page_count, pages:[{id, ordinal, width, height, has_rendered, has_regions, image_version}], chapters: []}`, width/height from the DB, `image_version` = newest file mtime in ms, no filenames); `GET`/`HEAD pages/{pid}/image?variant=original|rendered` (`media.stream`; bad variant 422); `GET pages/{pid}/regions` (`lines.read`; skipped regions and SFX without `include_sfx` dropped, keyed by reading-order `idx`, not bubble id, since ids change on every save); `GET`/`POST progress` (`library.read` / `lines.edit`; body `{page}`; writes only `last_page` and `percent_complete = page/page_count*100`, leaving `last_line_idx` and the audio position alone; past the last page or no pages 422). Image safety: the page is found by id inside that drama's `list_pages`; the stored name must be relative, png/jpg/jpeg, and its normalised join must equal its `realpath` (so no `..` escape and no symlink at any level below the drama folder), inside the folder by `commonpath`, a regular file by `lstat`, 1 byte to 50 MB, and start with a PNG or JPEG signature, which sets the content type (never the extension, never PIL). Every refusal, including an unknown drama or another drama's page, is one generic 404. Headers: `Cache-Control: private, no-cache`, Starlette's ETag/Last-Modified/Range, `nosniff`, `Content-Disposition: inline; filename="page_<n>.<png|jpg>"`; the router adds the `If-None-Match` 304 that `FileResponse` lacks. Doubts: the file is checked then reopened by `FileResponse`, so a swap in between (local access only) is not caught; progress shares `last_page` with the line Reader for a drama that has both lines and pages; the SFX rule is copied from `scanlate.region_excluded_from_auto` to avoid importing scanlate's image stack. Tests: `tests/test_api_comic_viewer.py`. UI pending.

**Next candidates:** the remaining slices are tracked as an ordered queue (Slices 22 onward, with
dependencies and which are gated on a user decision) in the Migration Roadmap Tracker's "Migration
slices" tab rather than repeated here, so this paragraph doesn't go stale every slice. Decisions
taken 2026-09-28 that shape that queue: process-based jobs (speaker detection, dub, Ollama
re-segment) get an `on_done` completion hook in `background_jobs.py` so an API-started job applies
its own result; destructive actions follow today's UI confirmation bar (drama delete needs
`confirm=true` plus a typed `confirm_text="DELETE"` and is refused while a job runs; other
destructive actions rely on the pre-write snapshot or `confirm=true` where the tab has a checkbox);
uploads use streamed multipart (new `python-multipart` dependency) with long exports as jobs
writing a fixed file in the drama folder served by a download endpoint; `use_gpu` is persisted in
`db.app_settings` (default off) and honoured by every GPU-capable API job.

**Slice 35 -- Drama create and update.** `POST /api/dramas` (201) and
`POST /api/dramas/{id}/metadata` (partial update; only fields present in the
body are applied). POST for writes, per the convention set by Slice 19 (no
PATCH). Updatable fields are a whitelist, enforced twice: the request schema
forbids unknown keys (422), and `services/drama_service.py` re-checks, because
`db.create_drama`/`db.update_drama` interpolate kwarg keys straight into SQL --
`status`, `content_mode`, `source_language`, `*_filename`, `translation_engine`
and `personal_notes` (per-profile) are never client-writable here.
`source_language` is required on create (Step 87 enforced explicitly). Preset
semantics: only the preset's `translation_engine` is persisted on the drama;
`style_preset`, `locale`, `default_female_pronouns` and `include_genre_notes`
are session-only in Streamlit, so create returns them as `preset_defaults`
for the client to hold. Out of scope: delete (destructive; gated on a
confirmation-semantics decision), cover upload (needs python-multipart),
series rename/unassign, presets CRUD, personal notes. (Metadata auto-fill was
later added as Slice 37, `/api/metadata`.)

**Slice 39 — Translate stage config and cost estimate (2026-09-28).**
Read-only half of the per-drama Translate stage: `GET
/api/translate-run/dramas/{id}/config` (engines, style presets, locales,
workflow tiers, context/batch defaults that differ for novel_narration,
line/untranslated counts, monthly cap and spend, per-engine cap
applicability, bulk-capable engines) and `GET .../estimate` (pre-run cost
estimate and cap gating for a chosen engine/model/reflect/bulk). Every
knob is a request-time parameter with the widget's own default as
fallback -- no new drama columns. The estimate is advisory, not a
guarantee. Booleans/numbers only, never a key or the novel text. Out of
scope: the start-translate job (Slice 40), bulk/Reflect runs (Slice 41),
glossary review, style-preset CRUD and characters.

**Slice 55 -- Discover known-titles catalog (D-1, 2026-09-29).** `services/discover_catalog_service.py` + `/api/discover/...`; no network, no LLM. `GET /titles?q&language&media_type` (returns `{titles, total}`), `POST /titles` (manual add; whitelisted fields with length caps, unknown fields 422, `title_original` required, `language` zh/ja/ko, valid `media_type`, `source_url` http(s) only), `POST /titles/{id}/delete` (body `confirm=true` else 422; unknown id 404, id verified before the write), `POST /titles/seed` (idempotent, wraps `title_library.seed_known_titles`), `POST /titles/{id}/import-to-library` (creates the drama through `drama_service.create_drama`, so media type and lengths are validated, unlike the tab's raw `db.create_drama`; an unknown catalog media type becomes `other` as in the tab). The duplicate check moved server-side with the tab's rule (same `title_en`, or same `title_zh` as the original) and returns 409 `conflict` with `details.drama_id` via the existing `ConflictError(details=...)` mechanism. `GET /platforms?language&content_type` and `GET /search-links?q&format` are pure `known_sites` wrappers. No path or key in any response. Tests: `tests/test_discover_catalog_service.py`, `tests/test_api_discover.py`. Note: the tab's LLM query translation, baihehub search and URL import are D-2. UI pending.

**Slice 54 -- Safe public-page fetch and 403/429 errors (D-0, 2026-09-29).** `services/safe_fetch.py` is a shared building block (no router, no new dependency) for the Discover/Sources slices that must read a user-supplied URL. `fetch_public_text(url, max_bytes)` wraps `metadata_service._check_public_url` and `_pinned_get` by import (neither `metadata_service.py` nor `tests/test_api_metadata.py` is touched): every hop (max 3 redirects) is re-validated and pinned to its own validated IP, private/loopback/link-local/reserved/v4-mapped addresses give 422 before any connection, at most `max_bytes` (cap 2 MB) are read from the stream, and visible text is extracted with bs4 like `metadata_service`. It never renders a browser: a page with under 200 visible characters (JS shell, empty) returns `FetchResult(needs_manual=True)` with "Paste the page text instead." Errors are fixed strings only (no URL, exception text, path or key); returned text goes through `redact_secrets`. `ForbiddenError` (403 `forbidden`) and `RateLimitedError` (429 `rate_limited`) are added to `service_errors.py` and `_STATUS_BY_ERROR`. `tests/test_static_analysis.py`'s timeout checker gained a `session_verbs` mode (used for `services/`): `session.get/post` and `<name>.get/post` on a `requests.Session()` are checked for `timeout=`; `_pinned_get` passes. Tests: `tests/test_safe_fetch.py` (mocked DNS and transport, no network) plus new handler-mapping rows in `tests/test_api_foundation.py`. Not covered: `trust_env=False` means a proxy-only network cannot use it.

**Slice 53 -- Dub track download (2026-09-29).** `GET
/api/dub/dramas/{id}/track` streams the finished `dub_track.wav` (or
`narration_track.wav` for narration dramas) as an attachment. The drama is
verified, the path is never returned, the filename is server-built
(`drama_<id>_<track>`), symlinks/escapes and a missing file give 404. The React
Dub stage shows a plain download link once a track exists. No Range support yet.

**Slice 42 — Characters and voice config (2026-09-28).**
`services/characters_service.py` + `/api/characters/*`: list a drama's
speakers with character/voice settings, a validated partial update
(POST, speaker label in the JSON body since labels can hold spaces,
unicode or slashes), series-character listing, the clone-engine picklist
and the voice bank (list + apply). Everything is scoped per
(drama_id, speaker_label), so one drama's write never touches another's
same-named speaker. The Step 26c rule (never a clone engine that can't
speak the drama's source language) is enforced server-side (422), not
just by the picker. Update semantics are None = leave alone, "" = clear,
because `db.upsert_character` uses COALESCE; the router forwards only
fields the client set (`exclude_unset`), and an explicit JSON null counts
as not passed. Voice-bank apply copies the clip into the drama's folder
server-side but never returns a path or filename (only `has_ref_audio`).
Out of scope: reference-audio upload/auto-extract (multipart), series-
character writes, Dub generation.

*Hardening H1 (Slices 35/42).* Review fixes: error messages never echo client
text (speaker label, clone engine, unknown field names); ids and counts are
capped at 2**31-1 in services and schemas (422, not a sqlite OverflowError);
drama text fields have length caps, titles are stripped and `source_url` must
be empty or http(s) (blank titles stay allowed, as in the tab); voice-bank apply
rejects labels that can't be a filename part, returns NotFound for a missing
clip, and enforces the Step 26c language rule on the entry's engine (stricter
than the tab, by design); a new series is created only after the drama row
exists, so a failed create leaves no stray series.

**Slice 36 -- Drama delete.** `DELETE /api/dramas/{id}?confirm=true&confirm_text=DELETE`
-> `{"deleted": true, "drama_id": n}`. User-approved rule: needs `confirm=true`
AND an exact-match typed `confirm_text` (Streamlit's checkbox + type-DELETE
pair, translated to API terms like Slice 17's `clear_history`), and is refused
409 while a job runs for the drama. Check order: unknown id 404 first (always),
then 422 for a missing/wrong confirmation (message never echoes the text), then
409. The running-job check covers in-process jobs
(`background_jobs.any_job_running_for_drama`) AND cross-process
`db.job_records` rows for this drama's job ids that are running/queued and
updated within 6 hours -- older rows are a crashed process's leftovers and must
not block forever. Deletion goes through one private function
(`_hard_delete_drama`) so roadmap Step 43's soft-delete can replace it. Known
hazard, unchanged: `db.delete_drama` removes the DB row first, then the folder
(including non-regenerable `voice_refs/`); a failed rmtree leaves an orphan
folder, surfaced as a clear 500 `application_error` (no paths).

**Slice 40 -- Translate run, normal (2026-09-28).** `POST
/api/translate-run/dramas/{id}/run` starts a normal (single-pass, non-bulk,
non-reflect) translation as job `translate_{id}` (the same id the tab uses, so
either side sees a running job). The job does everything, DB write included:
it reuses `run_translate_job` (field-scoped `save_lines(fields=("en",))` by
permanent line id, notes by id, then `finish_translation_run`); only
empty-`en` lines are translated unless `force_retranslate` (so hand edits
survive, as in the tab, with the same "before force re-translate" snapshot),
and optional `line_ids` narrows the run to those lines (rest = context only),
via a new optional `target_ids` pass-through on `run_translate_job`. Lines,
glossary, style guidelines (learned profile, emotions, gender hints), character
names and locale are built server-side from the DB exactly as the tab and
`cli.py translate` do. Errors: unknown drama 404; duplicate start 409; no
key 503 with fixed text (keys resolved server-side, never accepted or echoed);
bad engine/locale/preset/line_ids 422; nothing to translate or monthly cap
refusal 400 (the app's UnsupportedOperation status). Deliberate differences: the summary engine is always local Ollama
(no per-session Settings pick); Ollama's num_ctx override is not applied.
Out of scope: bulk/Reflect (Slice 41), the fallback chain (Step 97b). Paid-key
runs were not verified (tests use the offline engine and fakes only).

**Step 97b -- Translate fallback chain (2026-09-28).** `POST
/api/translate-run/dramas/{id}/run` gains optional `fallback_chain: [{engine,
model?}, ...]` (max 3; no keys/URLs -- keys resolved server-side per engine).
The chain is wrapped in `translate_engines.FallbackEngine`: on an auth failure
(401/403), rate limit, timeout or connection error (`is_fallback_error`; never
a content-moderation refusal, never a generic exception) the run switches to
the next engine for the rest of the run and records it; the job result gains
`fallbacks: [{from, to, reason, detail(redacted)}]` and the start response
`fallback_engines`. The chain must stay in one class (all instruction-following
or all `TRANSLATION_ONLY_ENGINES`) with no repeats, else 422 -- so glossary/style
adherence is never silently dropped. Costs: every finished batch is logged
against the engine that ran it; each engine has its own cap and spend
(`cap_exhausted` stops the run cleanly), not shared with the one it fell back
from. Deliberate: the switch is immediate on the first qualifying error (no
backoff wait on the primary first); the CLI/Streamlit button do not expose a
chain yet (API/service level only, per the held step's scope). Out of scope:
bulk/Reflect chains.

**Slice 32 (upload-then-transcribe wiring).** `POST /api/media/dramas/{id}/upload-and-transcribe`
takes a multipart `file` plus the `TranscribeRunRequest` options as form fields
(validated through that same model before anything is stored), stores the file
via `media_upload_service.upload_media`, then calls the existing
`start_transcribe_run`, returning `{upload, job_id}`. If the run cannot start
after a successful upload (no key, already running, wrong mode, ...), the
service error is returned and the uploaded file is deliberately kept; the client
can retry via `POST /api/transcribe/dramas/{id}/run`. The persisted `use_gpu`
(Slice 23) is read inside `start_transcribe_run` and is tested through this
route. New `GET /api/media/dramas/{id}/status` returns `has_audio`,
`has_source_video`, `upload_max_mb` (BAIHE_MAX_UPLOAD_MB) only, no paths; it is a
separate endpoint because the transcribe config's response shape is pinned by
exact-match tests.

**E0 -- Library remainder (2026-09-28).** Read endpoints under `/api/library`: `GET /stats`
(counts + usage totals), `/recent`, `/costs` (dramas with logged calls, free runs included),
`/series` (series with 2+ dramas plus character/glossary counts), `/search?q=` (1-200 chars,
limit 1-100), `/history` (default profile only -- no profile selector yet), `/presets`,
`/voice-bank` (no clip filename/path; `clip_available` flag). Non-destructive writes:
`POST /presets/{id}/rename` and `POST /voice-bank/{id}/rename` (name 1-100 chars, ids capped at
2**31-1, duplicate preset name 409, unknown id 404). Deferred, not built: preset and voice-bank
delete (the tab's checkbox confirm has no server equivalent yet), clear reading history, bulk
status/tags/delete and bulk translate, storage scan/clean, backup/restore (need the typed
confirm and `drama_service.job_running_for_drama` refusal), and Continue reading (per-profile).

**Step 95 -- BGM-preserving dub (service + API + CLI, 2026-09-28).** `DubRunRequest` gained `keep_background: bool = False` (edited in place; `DubConfig` gained a booleans-only `can_keep_background`, also in place). With it set on a video dub, `start_dub_run` binds the drama's stored audio and `separation_backend` into the worker (`functools.partial`, so the trailing result queue lands on the worker's `result_queue` parameter); after `build_dub_track` the worker calls `dub.mix_original_background`, which reuses `audio_preprocess.separate_vocals` (new `extract_background` subtracts the vocals stem from the original), caches `dub_background.wav` in the drama folder, and overlays it at -6 dB under the track. Narration or a drama with no audio is 422; missing soundfile/numpy/separation backend is 503 with fixed text. A separation failure keeps the plain dub and reports `background_mixed: false` with a fixed `background_error`. `cli dub --keep-background` does the same. Pre-existing bug found: the Streamlit Dub tab (and, before this step, Slice 26's service) passes `narrate_original, source_lang` positionally after which `background_jobs` appends `result_queue`, but the worker declares `result_queue` before them, so the queue lands in the wrong parameter; the service now binds them by keyword, the tab still has the old call. Real separation and mixing were not verified (tests fake them).

**Slice 41 -- Translate Reflect + bulk (2026-09-28).** `POST
/api/translate-run/dramas/{id}/run` gains `reflect` and `bulk` (both default
false; `TranslateRunStart`/`TranslateRunStarted` edited in place). `reflect`
alone passes `reflect=True` to the same `run_translate_job` the tab and `cli.py
translate --reflect` use: Step 7's three passes (the code has three, not two),
id-keyed at every pass, critiques saved as notes by line, field-scoped `en`
writes, same force-retranslate snapshot. `bulk` starts job
`bulk_translate_{id}`, which submits exactly what the tab's
`_start_bulk_translation`/`_start_bulk_reflect` submit (Claude/Gemini batch
API; DeepSeek off-peak schedule; with `reflect`, the bulk Reflect pipeline),
then polls inside the job with `bulk_translate.run_bulk_poller` and follows each
Reflect stage bulk_translate submits in turn, until applied, failed (job
error, redacted text) or cancelled. Results are applied by line id by
bulk_translate's existing apply step (dropped if deleted, flagged if the source
changed, hand edits kept). Cancelling the job (`POST /api/jobs/{id}/cancel`)
cancels the pending bulk job at the provider when it can. Resumability reuses
what exists: `POST /api/translate-run/dramas/{id}/bulk/resume` calls
`bulk_translate.resume_pending` (the Bulk jobs panel's call) with server-side
keys; nothing new is persisted. Errors: unknown drama 404; a running job or a
pending bulk job for the drama 409; no key 503; fallback chain with reflect or
bulk, or `line_ids` with bulk, 422; Reflect on a translation-only engine, bulk
on a non-bulk engine or Gemini free tier, bulk Reflect on DeepSeek, nothing to
translate, a monthly-cap refusal, or a Claude/Gemini batch estimate above the
cap 400 (a batch can't stop part-way; DeepSeek off-peak stops at the cap when it
runs). The fallback chain is refused for both modes: Reflect goes through
`call_llm_json`, which `FallbackEngine` does not wrap, and a batch is bound to
one provider. Deliberate differences: bulk Reflect now gets the same cap
refusal as bulk translation (the tab checks none); the API job, not a separate
`bulkpoll_` job, polls, so a Streamlit tab calling `resume_pending` at the same
time could start a second poller (`check_once` is locked, so results apply once).
Paid-key and real batch runs were not verified (tests use a fake provider and a
fake Reflect helper).

**Slice 24 -- write-only engine key endpoints (off by default).** `POST /api/settings/keys/{engine}` and `.../clear` in `api/routers/settings_routes.py`, service functions `set_engine_key`/`clear_engine_key` in `services/settings_service.py`. Engines: claude, deepseek, gemini, deepl, google, groq, hf_token (URLs and the cap are not secrets and are not accepted). Defence in depth, because a reverse proxy on the same PC (Tailscale Serve, Cloudflare Tunnel, Vite proxy) makes remote requests arrive from 127.0.0.1: (1) `BAIHE_API_ALLOW_KEY_WRITES=1` is required, else 403; (2) TCP peer must be loopback, Host must be 127.0.0.1/localhost/[::1], and none of X-Forwarded-For/-Host/-Proto, Forwarded, X-Real-IP, Tailscale-User-Login, Cf-Connecting-Ip, Cf-Ray, Via may be present; (3) Origin, if present, must be a loopback origin (blunts CSRF from a page in the user's own browser) and the body needs `confirm=true`; every refusal at these levels is the same generic 403 "Not allowed from this connection."; (4) write-only: the response is `{engine, configured}`, never the key, its length or a part of it; validation errors never echo the value (the API's 422 handler already drops input) and nothing is logged; (5) unknown engine 422; empty, over 512 chars, or any whitespace/control/quote character (env-injection) 422; (6) the key is written to the same `.env` under the canonical name (first of `ENV_NAMES`) as the Streamlit "Save to .env" button, via a new atomic writer (temp file in the same folder + `os.replace`, other lines kept, permission bits kept, new file 0600) because `tabs/settings_tab.save_key_to_env` imports Streamlit and isn't reusable from services; `resolve_key` reads `.env` per call so the key is live immediately; (7) `/clear` removes every accepted env name for that engine from `.env` (a key also set as a real environment variable stays configured; the response says so); (8) presence stays the existing `engine_keys` booleans. **Honest limit:** this is a safeguard, not authentication -- a proxy that strips headers, or any local process, defeats it; real admin isolation is the separate admin listener (D5), still to be built. Keep the flag off unless the API is bound to loopback and not published through a tunnel. Tests: `tests/test_api_key_writes.py` (no network). Not verified: behaviour behind a real Tailscale/Cloudflare proxy or real uvicorn peer addresses (TestClient supplies the peer), Windows file permissions, and the Streamlit tab is unchanged (its non-atomic writer remains).

**Slice 51 -- Pending bulk batch list + cancel (2026-09-29).** `GET /api/translate-run/dramas/{id}/bulk` lists the drama's `bulk_jobs` rows newest first (`TranslateBulkList`): `bulk_job_id`, `engine`, `model`, `kind`, `stage`, `pipeline_id` (Reflect stages group by it), `status`, `pending`, `cancellable`, `line_count`, `scheduled_for`, `result_summary`, redacted `last_error`, `submitted_at`, `updated_at`. It is read-only and never contacts a provider: states are those last recorded, and re-attaching pollers stays the Slice 41 `POST .../bulk/resume`. The provider batch id, `translate_args` (prompt inputs) and keys are not returned. The tab's Cancel is exposed as `POST .../bulk/{bulk_job_id}/cancel` via `bulk_translate.cancel_bulk_job` unchanged: it stops polling, marks the job cancelled, and asks the provider to cancel only when a server-side key exists (failure is redacted and the local cancel still stands; the message says which happened). Job of another drama or unknown 404; a status outside submitting/submitted/scheduled/auth_error 409. Gap: a mid-run `running` off-peak job is listed but not cancellable, as in the tab. Service code in `services/translate_run_service.py`; no new module.

**Slice 52 -- media playback with Range (2026-09-29).** `services/media_playback_service.py` + `GET`/`HEAD /api/media/dramas/{id}/audio` and `/video` in `api/routers/media_routes.py`. Serves the drama's `audio_filename` / `source_video_filename` through Starlette's `FileResponse` (chunked streaming, never buffered; `Accept-Ranges: bytes`; single range -> 206 with `Content-Range`, suffix `bytes=-N` and open-ended `bytes=N-` supported, unsatisfiable -> 416 with `Content-Range: bytes */size`; a multi-range request gets Starlette's multipart/byteranges response; HEAD returns headers only). Content-Type comes from a fixed extension map (whitelisted audio/video extensions only; not `mimetypes`, whose answer varies by host), plus `X-Content-Type-Options: nosniff` and `Content-Disposition: inline; filename="drama_<id>_<kind>.<ext>"` (a generic name, never the stored one). Every route needs `media.stream` (off by default for household users); Range/If-Range needs `starlette>=0.39` (pinned in `requirements-core.txt`). The stored filename is resolved with `realpath` and must stay inside that drama's own folder and be a regular file, so `..`, absolute names and symlinks that escape are refused (a name on another Windows drive, where `commonpath` raises, is a 404 too). Unknown drama, no file, or any refusal is the same generic 404; no path, stored file name or secret appears in any body. The video route serves the original upload (`source.<ext>`), not the extracted `audio.wav`. UI pending.

**API startup hook (2026-09-29).** `create_app` now has a FastAPI lifespan that calls `api/background.py`'s `start_background_services()` when `ApiSettings.background_services` is on (`BAIHE_API_BACKGROUND`, default `1` through `load_settings`; the dataclass default is off, and `tests/conftest.py` sets `0`, so no test starts anything). `app.py` itself starts nothing; the tabs do, so the hook mirrors them: the chapter-check scheduler (`sources.chapter_check.ensure_scheduler_started`, which Sources starts on every render; it only kicks off a cycle when `check_interval_hours` > 0 and a series is tracked) always, and the browser-extension endpoint (`page_server.ensure_server_started`, loopback port 8756) only when the Sources setting `page_server_enabled` is on, as the Settings sidebar does. Both are once-per-process daemon threads, so the hook is idempotent and nothing is stopped at shutdown; a failure is logged (redacted) and the API still starts. Known gaps: the sidebar also pushes the extension's engine and key from Streamlit session state (`page_server.set_translation_config`), which is not persisted, so under the API alone the endpoint returns OCR text marked untranslated until a config route exists; with Streamlit and the API both running, both run a scheduler (the `last_check_cycle` setting spaces cycles, but two could start in the same poll window) and the second page server fails to bind (logged). Tests: `tests/test_api_background.py` (fakes; no thread or port).

**API batch 1 -- workflow progress (2026-09-29; all API batch 1 paragraphs below merged in #372).** `GET /api/workflow/dramas/{id}/progress` (`api/routers/workflow_routes.py`, `library.read`) returns `WorkflowProgress`: `stage_index` from `compute_workspace_stage_index` (the 7-tab scale), `stage` (the five-stage key: 0-2 source, 3 translate, 4 review, 6 export; 5/dub is never current), whole-drama `line_count`, `untranslated_count` and `flagged_count` (the Review stage's definitions), booleans `has_audio`, `has_dub_track`, `exported`, and `stages`: one `{key, state}` per React stage, state `done`/`current`/`pending`/`optional`/`blocked`. Rules: Source is current while the index is 0-2, else done; with no lines every later stage is `blocked`; Translate is done once the index is past 3 with no untranslated line; Review done at index 6; Dub is `done` when `dub_track.wav` exists, otherwise `optional`; Export is `current` at index 6 and `done` when the drama is marked exported. New service function `workflow_service.get_drama_progress` (reads only; it builds the drama path without `db.drama_dir`, so it never creates the folder). Unknown drama 404; ids outside 1..2**31-1 are 422. No path or file name is returned. "Blocked by a running job" is not in the state: the spec's job pill reads `/api/jobs`. Tests: `tests/test_api_workflow.py`.

**API batch 1 -- Live capture routes (spec L-1, 2026-09-29).** `api/routers/live_routes.py` over the existing `services/live_service.py`: `POST /api/live/sessions` (`LiveSessionStart`: url, source_language, whisper_size, segment_seconds, overlap_seconds, engine, model, max_minutes, use_gpu; `extra=forbid`, so no cookies or key can be passed) returns `{session_id}`; `GET /api/live/sessions` lists this process's sessions; `GET /api/live/sessions/{id}?after=N` returns status, redacted/path-stripped message, progress, `cues[N:]` and `next_index`; `POST /api/live/sessions/{id}/stop` bumps the generation and cancels (idempotent). Permissions: start is `media.import_url` (any public URL, per the remote-access decision) and the handler calls `require_engines_allowed` with the named engine, so a paid engine, or no engine (the default is Claude), also needs `engines.paid`; status and list are `library.read`; stop is `jobs.cancel`. Note: the service docstring says "start/get are gated like media.import_url"; per the lead's brief, reads are `library.read` (a household member can watch a session someone with `media.import_url` started). Session ids must match `live_[0-9a-f]{32}` (else 422); unknown or restarted-away sessions 404; bad/private URL or option 422; unresolvable host or missing key 503. L-2 (SSE) is not built. Sessions are not per-user (jobs are not yet per-user, step 137). Tests: `tests/test_api_live.py` (the capture job is faked; no yt-dlp, DNS, Whisper or engine).

**API batch 1 -- Discover network helpers (spec D-2, 2026-09-29).** `api/routers/discover_lookup_routes.py` (same `/api/discover` prefix as Slice 55's catalogue routes, no path overlap) over the existing `services/discover_lookup_service.py`: `POST /translate-query {q, engine}` (sync; a query that already has Chinese is returned without an engine call), `POST /baihehub-search {q}` (fixed host, no LLM), `POST /import-suggestion {url, engine}` (static fetch through `safe_fetch`, LLM extraction, writes nothing), `POST /bulk-extract {urls (1-10), source_label, engine}` and `POST /navigation-help {url, goal, target_language, engine}` (fixed-id jobs; a second start while one runs is 429), `GET /bulk-extract/result` and `GET /navigation-help/result` (`{job_id, status, progress, message, result}`; 404 if the job never ran in this process), and `POST /bulk-commit {entries (1-500), source_label}` (known_titles only, deduped by title and detail-page URL). Permissions (judgement calls, see the route table): baihehub search and the two result reads are `library.read`; translate-query is `library.read` plus `require_engines_allowed` on the named engine (none = the default Claude, paid); the three routes that make this PC fetch a caller-chosen public URL are `media.import_url` plus `require_engines_allowed` (the same permission as Live's "any public URL"; `sources.import` is kept for Sources adapters); bulk-commit writes the catalogue, so it is `admin.library` like the catalogue's create/seed. Request models are `extra=forbid`, so no key or cookie can be sent; keys are resolved server-side and every returned text is redacted. Unknown or non-reference engine, bad URL/scheme or private host, oversize input 422; no key or fetch failure 503; site terms refusal 400. The spec's `paginate_urls` fix is already in `bulk_import.py` (str.replace); no pagination route was added (the service has none). Tests: `tests/test_api_discover_lookup.py` (fake engine, fetch, LLM helpers and baihehub).

**API batch 1 -- Sources search routes (spec S-3, 2026-09-29).** `api/routers/sources_search_routes.py` (same `/api/sources` prefix as Slice 56's catalogue routes; the shapes differ, so neither shadows the other, and a test checks `GET /api/sources/{name}` and `/tracked` still answer) over the merged `services/sources_search_service.py` (#336): `POST /api/sources/search {query, sources?}` starts the fixed-id job `sources_search`; `POST /api/sources/{name}/series {series_id}` starts `sources_series_<name>` (series info plus chapters in `chapter_order.sort_chapters_grouped` order); `GET /api/sources/jobs/{job_id}/result` reads the in-process result (404 if not resident: restart, other process, or not a Sources job id). Cancel is the existing `POST /api/jobs/{job_id}/cancel`. All three are `library.read`: search and chapter listing write nothing, which is the spec's "read and search allowed" interim default; importing (S-4) is not built and will be `sources.import`. Inputs are names, text and ids only (`extra=forbid`; a URL-looking query or id is 422). Error mapping from the service: `SourceUnavailable` 503 with `details.retry_after`, `ChallengeDetected` 409 with `details {reason, handoff:true, open_url}` (scheme+host+path only), unsupported/terms/hidden 400, a second start while one runs 409, unknown source 404, switched-off source 400; results carry URLs reduced to scheme+host+path and scrubbed text. Fixed while wiring it: the service accepted a `series_id` such as `//169.254.169.254/x`, and `sources/adapters/52shuku.py` `urljoin`s the id onto its base URL, so the id could change the host the PC fetched from; `start_series` now refuses a leading slash or backslash, backslashes, `@`, `:`, `..`, whitespace and control characters (422). These routes reach real network fetches through the adapters and `sources/http.py`, whose redirect hops are not re-validated yet (B-25, fixed on `fix-b25-redirect-hops`); the adapters only follow their own hosts' redirects, but until that lands a redirect from a source site is not checked against private addresses. Jobs are process-wide, not per-user (step 137): any household member with `library.read` sees the current search's result. Tests: `tests/test_api_sources_search.py` (fake adapters on a scripted transport; no network) and new cases in `tests/test_sources_search_service.py`.

**API batch 1 -- Diagnostics gaps routes (M1, 2026-09-29).** `api/routers/diagnostics_gaps_routes.py` (same `/api/diagnostics` prefix as Slice 5's overview) over `services/diagnostics_gaps_service.py`, covering the inventory's Diagnostics rows Q01, Q06, Q09, Q14 (list), Q15, Q17, Q18 and Q20. Reads, all `admin.diagnostics`: `GET /setup-checks` (Python, ffmpeg, JS runtime, CUDA, project files, library writable; found/version/name only), `GET /model-cache` (HF cache revisions and Piper voices by name and size), `GET /pyannote?check_access=false` (booleans; `check_access=true` asks Hugging Face with the server-side token and returns only `accessible` per model), `GET /job-history` (finished jobs in this process, redacted, with duration), `GET /log?n=50&keyword=` (n 0-200, keyword up to 100 chars; lines are redacted before the keyword filter runs, so a keyword can't probe for redacted text) and `GET /support-report` (`{report}`, redacted text). Writes, all `local_only()` with `confirm=true` (StrictBool) and refused with 409 while any job runs or is queued, in this process or (fresh `job_records` rows) another one (`library_admin_service._any_job_running`), and also while `background_jobs.exclusive_active()` (a restore or reset) or a maintenance operation (bulk delete, storage cleanup; `background_jobs._maintenance_count`, which has no public reader) is in progress; the reset itself takes `background_jobs.acquire_exclusive("Library reset")` for its duration, as restore does, so no job can start mid-reset, and releases it even if the reset fails: `POST /dependencies/{package}/install` and `/upgrade` (the name must match `[A-Za-z0-9][A-Za-z0-9._-]*` and be in the service's `installable_packages()` whitelist, else 404; synchronous, returns `{package, ok, output_tail}` with the last 40 redacted pip lines; after the security reviews each pip command runs in its own process group through `_stream_tree`, which kills the whole process tree (`background_jobs._kill_tree`) on timeout: `PIP_TIMEOUT_SECONDS` (900 s), or `GPU_TORCH_TIMEOUT_SECONDS` (3600 s) for the CUDA torch path; the whole pip run holds `background_jobs.acquire_exclusive("Dependency install")`, so no job, restore, reset, cleanup or second install can start in this API process mid-upgrade (jobs in another process, e.g. Streamlit, are checked before and again under the hold, but the hold can't stop one starting there afterwards), and it is 409 if the hold can't be taken; every wait is bounded: output is read on a helper thread, and after the kill (or after pip exits) reading stops within `KILL_DRAIN_SECONDS` (5 s) even if a surviving grandchild keeps the pipe open, so the hold is always released) and `POST /reset-library` (`confirm_text` must be exactly `RESET`, as the Streamlit button required; unlike Streamlit it refuses rather than cancelling running jobs; it also deletes every user account, session and the audit log along with the library database, which is left for the user to decide, L2). Service change: `AdminActionRefused` is now a `ServiceError`, with subclasses for unconfirmed (422), unknown package (404) and jobs running (409), so the API maps them without parsing messages; existing callers that catch `AdminActionRefused` still work. `reset_library` gained an optional `confirm_text`. Not exposed: model-cache delete, the Deno install, the separate "Install GPU PyTorch" button (but `install` of `torch` on a machine with an NVIDIA GPU installs the same CUDA build from PyTorch's index, so GPU torch is reachable through the whitelist; unlike Streamlit's button it does not uninstall first: `pip install --force-reinstall --no-deps torch torchaudio` from the CUDA index downloads both wheels (~2.5 GB) before replacing anything, so a timeout or kill during the download leaves the old torch installed, then a plain install from the same index adds any missing dependencies such as the nvidia-* wheels on Linux), bulk tier install, dependency update check (PyPI), bug bundles (delete is on `api-delete-routes`), benchmark, App Assistant, source-access tests; the overview's model-version list already covers Q11's read. Doubts: install/upgrade run pip inside the request (minutes; fine on loopback, but it is not a job and cannot be cancelled); the retirement plan (section 3 item 2) suggests replacing the reset with a CLI or documented step, so the reset route may be dropped if the user prefers that. Tests: `tests/test_api_diagnostics_gaps.py` (every outside call faked; no pip, network or deletion).

**API batch 1 -- browser-extension bridge control (2026-09-29, user decision).** For the PC-only React control that replaces the Settings sidebar's "Browser extension" expander (`tabs/settings_tab.py:430-484`) once Streamlit is gone. New `services/extension_service.py` (the service that owns the bridge's switch: `sources` store `page_server_enabled` and `page_server.load_or_create_token`) and `api/routers/extension_routes.py`, all three routes `local_only()`: `GET /api/extension/status` returns `{enabled, running}` only (never the port or the token); `POST /api/extension/enabled {enabled}` (StrictBool, `extra=forbid`) persists the setting and, when turning it on, starts `page_server` in this process only if the API's background services are on (`BAIHE_API_BACKGROUND`, the same switch the startup hook uses; so `create_app()` in tests never opens the port), returning `{enabled, running, restart_needed}`; `POST /api/extension/token {confirm: true}` returns `{token}` with `Cache-Control: no-store` and `Pragma: no-cache` (a POST so it is never cached or prefetched; nothing logs the body). The startup hook already starts `page_server` only when `page_server_enabled` is set (Streamlit's behaviour, `tabs/settings_tab.py:439-450`); a test here pins that again. Port 8756 stays loopback-bound (`page_server._serve` binds `127.0.0.1`). Gaps: turning the bridge off cannot stop a running endpoint, because `page_server` has no stop function (Streamlit never stopped it either), so the response says `restart_needed: true` and it stops at the next API restart; adding `page_server.stop_server()` and a token rotation (`POST /api/extension/token/rotate`) needs an edit to `page_server.py`, which was not made on this branch. The extension's engine choice is still not persisted (see the startup-hook note above), so the bridge returns OCR text marked untranslated under the API alone. Tests: `tests/test_api_extension.py` (page_server and the scheduler faked; remote, proxied and cross-origin requests refused even for an admin; the token appears only in the token response).

**API batch 1 -- Live security-review fixes (F1, F2; 2026-09-29).** F1: `live_service.start_session` allowed more than `MAX_SESSIONS` when none could be evicted, and every start made a new session, so repeated starts could hold the GPU and an engine many times over. It now allows one session at a time (the Streamlit tab allowed exactly one): a start while a session is reserved, queued or running, or while the Streamlit tab's own `live_capture` job runs in this process, is 409 `conflict`; the check and the reservation share one lock hold, so two concurrent starts cannot both pass. F2: only the URL the user typed was checked; the stream URL yt-dlp resolved went to `ffmpeg -i` unchecked. `live_translate.run_live_job` gained optional `stream_url_check` and `protocol_whitelist` arguments (default `None`, so the Streamlit tab is unchanged), and `start_segment_capture` an optional `protocol_whitelist` (placed before `-i`, so it limits only the input). The API passes `live_service.check_stream_url` and the whitelist `http,https,tcp,tls,crypto`. After B-25 merged, both the typed URL and the resolved stream URL go through `services.url_guard.resolve_public` directly (http/https only, no userinfo, every resolved address public; the stream URL is checked in full with no length cap; refusal text never echoes the URL), the same policy `sources/http.py` applies to every redirect hop. L3 (security review): a session is reserved with a `starting` flag until `start_job` has registered its job, and `_reap` skips it, so a concurrent request's reap can no longer delete a just-reserved session's directory. S-3 takes no URL (names and ids only); its adapter fetches go through `sources/http.py`, which uses the same `url_guard` check on every hop since B-25. Remaining risk, accepted for now: yt-dlp's own requests while resolving (its redirects and extractor fetches) are not IP-checked, ffmpeg does its own DNS (a TOCTOU between the check and ffmpeg's lookup), and HLS playlists can name segment hosts that are never checked; the whitelist only stops non-network protocols (`file`, `concat`, `data`, `pipe`, ...). Tests: `tests/test_api_live.py`, `tests/test_live_service.py`, `tests/test_live_translate.py`.

**API batch 1 -- D-2 URL echo fix (F5, 2026-09-29).** `discover_lookup_service` returned the caller's URLs verbatim in the import suggestion's `source_url`, each bulk-extract page row's `url` and each extracted entry's `source_url`. Per spec section 5 (a pasted URL can carry a signed token) they are now reduced to scheme + host + path (`_display_url`, the same rule as `sources_registry_service.safe_url`, kept local so the module does not import the Sources adapters). Stripping is display-only: the bulk-extract job keeps each entry's full URL server-side (a private `_source_urls` map in the job result, dropped from every response), each entry carries an `entry_id` that is unique per run (a random run prefix plus a counter, so an id from an earlier extraction never matches a later one), and `POST /bulk-commit` stores and dedupes on the server-held full URL for any entry that has an `entry_id`, ignoring the client's `source_url` for it (unknown or replaced id: 422). An entry without `entry_id` is a manual entry and its `source_url` is stored as sent. So books identified by `?id=` stay distinct. The import suggestion's `source_url` is display-only too: the client already holds the URL it sent and should use that when it creates the title. Tests: `tests/test_api_discover_lookup.py::test_user_urls_come_back_without_query_or_fragment` and `::test_urls_differing_only_by_query_stay_distinct`.

**API batch 1 -- two schedulers made safe (F3) and the GPU-queue nudge (2026-09-29).** The startup hook starts the chapter-check scheduler in the API process (retirement plan section 9: the API owns it), so while `app.py` still runs, two processes can run cycles. F3: `sources/chapter_check.run_check_cycle` now claims the cycle first with `sources/store.claim_check_cycle` (a `BEGIN IMMEDIATE` compare-and-set on the `check_cycle_started_at` row of the Sources `settings` table: refused while another claim is younger than `CYCLE_LEASE_SECONDS`, 2 hours, so a process that died mid-cycle blocks checks for at most that long) and releases it at the end only if the claim is still its own; a cycle that can't claim returns `{"skipped": true}` and fetches nothing. Scheduled cycles (`start_check_now(scheduled=True)`, used by the scheduler loop) are also skipped when another process finished one within the interval since this one was found due; a manual "check now" is only refused while a cycle runs. `store.record_new_chapters` is now idempotent: each notification is inserted in the same write transaction as its `known_chapters` row, and only when that `INSERT OR IGNORE` inserted a row, so a chapter two checks find at once is notified once, and `check_series` returns (and auto-import queues) only what its own call recorded. It now returns the recorded chapters instead of a count (callers ignored the count). No unique index was added: an existing `sources.db` may already hold duplicate notifications (from past double checks, or a chapter re-seen after untracking and re-tracking), and a failing `CREATE UNIQUE INDEX` inside `store.connect()`'s schema script would break every Sources call. GPU-queue nudge (parity audit): `background_jobs.recheck_gpu_queue` was only called by the Streamlit Diagnostics tab's auto-refresh, so a job queued behind GPU load Baihe didn't start could stay queued once the tab is gone. `api/background.py` now runs `start_gpu_queue_poller` (every `GPU_QUEUE_POLL_SECONDS`, 20 s, a no-op when nothing is queued) from the lifespan, only when background services are on, and `stop_gpu_queue_poller` at shutdown. Tests: `tests/test_chapter_check_two_processes.py`, `tests/test_api_background.py`.

**API batch 1 -- local_only simple-POST guard (security review L1, 2026-09-29).** `local_only()` accepted any request from a direct loopback connection, and the Origin check ignores the port, so a page served on another loopback port could send a no-cors "simple" POST (`text/plain` or `application/x-www-form-urlencoded`, no preflight) to a PC-only route. The key-write routes read the body with `request.json()` whatever the content type, so a `text/plain` JSON body worked there. `api/auth.py` now requires, in both auth modes, that a POST/PUT/PATCH to a `local_only()` route be `application/json` or carry `X-Baihe-Local: 1` (JSON and the custom header both force a preflight, which CORS refuses); otherwise it answers the generic 403. `multipart/form-data` is not enough on its own, because it is a CORS-simple type too: `postMultipart` in `frontend/src/api/client.ts` sends `X-Baihe-Local: 1` with every upload (vitest 205 passed, `npm run build` ok). Routes affected (every `local_only()` POST): `POST /api/settings`, `/api/settings/keys/{engine}`, `/api/settings/keys/{engine}/clear`, `/api/media/dramas/{id}/upload`, `/upload-and-transcribe`, `/api/novel/dramas/{id}/attach-text`, `/attach-epub`, `/ocr-chapter`, `/api/discover/titles/{id}/delete`, `/api/sources/settings`, `/api/diagnostics/dependencies/{package}/install`, `/upgrade`, `/api/diagnostics/reset-library`, `/api/extension/enabled`, `/api/extension/token`. Unaffected: `DELETE /api/translate/history`, `DELETE /api/dramas/{id}` (DELETE is never a simple method) and `GET /api/extension/status`. The React client sends JSON for its other POSTs. Test changes: the multipart test clients in `test_api_media_upload.py`, `test_api_media_upload_transcribe.py` and `test_api_novel_attach.py` send the header as the React helper does, and `test_api_media_upload.py::test_missing_file_field_422` posts multipart without the file field, since a body-less POST is refused earlier. L5: `api.auth.LocalOnlyCrossSiteGate`, a pure-ASGI middleware installed in both modes, applies the same rule to `local_only()` paths (from `local_only_matchers`) before the body is read, so a refused multipart upload is never spooled to disk; the route dependency still repeats the check. Tests: `tests/test_api_permissions.py::TestLocalOnlyNeedsPreflightedPost`.

**Off-mode cross-site rule on every route (audit H1/M1, 2026-09-29).** The L1 rule only covered `local_only()` routes, so with auth off a page on another loopback port (Streamlit on 8501, a dev server) could send a no-preflight POST to permission routes, e.g. `POST /api/glossary/dramas/{id}/from-novel` (paid LLM run) or `POST /api/jobs/{id}/cancel`. JSON-body routes were reachable too: FastAPI versions allowed by `requirements-core.txt` (`>=0.115`) parse a body with no Content-Type as JSON, and a no-cors `fetch` with a Blob or ArrayBuffer body sends none, so e.g. `POST /api/translate-run/dramas/{id}/run` could start a paid run. (The locally installed FastAPI 0.141.1 answered 422 to such a body in a probe, but that is version-dependent.) The fix closes this whatever the version. `LocalOnlyCrossSiteGate` takes `all_api=True` when auth is off and applies `_cross_site_safe` to every `/api` POST/PUT/PATCH before the body is read. Auth on is unchanged (the CSRF header already forces a preflight). `frontend/src/api/client.ts` now sends `X-Baihe-Local: 1` from `postJson` and `deleteJson` as well as `postMultipart`; the browser extension talks to `page_server`, not the API, and `cli.py` does not call the API, so neither changed. Test changes: off-mode test clients that post bodyless requests send the header, one e2e request (`e2e/diagnostics.spec.ts`) sends it, and `libraryAdmin.test.ts` now expects it. M1: `.streamlit/config.toml` sets `server.address = "127.0.0.1"` so Streamlit no longer serves the LAN. Streamlit only reads that file when started from the repo root; started from anywhere else, pass `--server.address 127.0.0.1`. Tests: `tests/test_api_offmode_cross_site.py` (including a walk of every POST/PUT/PATCH route from `iter_route_declarations`).

**Route batch 2B -- Reader API (M4, 2026-09-29, #370).** 21 routes added to `api/routers/reader_routes.py` (prefix `/api/reader/dramas/{id}`) over the merged `services/reader_service.py` (#358), using its permission contract (user decision, 2026-09-29). `library.read`: `GET overview`, `notes`, `media` (availability only: `original` video/audio/null, `dub`, `narration`, `caption_tracks`, `captions_overlay`), `vocab?rich_only=`, `wiki?up_to_line_idx=&entry_type=`. `lines.read`: `GET captions/{Source|English|Bilingual}` (`text/vtt; charset=utf-8`, inline; a track the drama has no text for is 404), `captions/{track}/readout`, and the downloads `vocab/export.csv`, `vocab/export.apkg?rich=` (503 without genanki) and `wiki/export.md`, all with generic ASCII names (`drama_<id>_vocab.csv`, `drama_<id>_vocab[_sentence].apkg`, `drama_<id>_universe_wiki.md`), never the title. `lines.edit`: `POST progress` `{page, chapter_size 10-200}`, `notes` `{notes <=100000}`, `vocab/rich` `{words 1-500, queued}` (a word not in this drama's vocab is 404), `wiki/clear` `{confirm: true}` (anything else 422, entries kept), and `lookup` `{page, chapter_size, use_llm=false, engine?, model?}`; with `use_llm` the lookup also needs `jobs.start` (checked in the handler, since a route declares one permission) and `require_engines_allowed`. LLM tools (`jobs.start` + `require_engines_allowed(request, body.engine)`, an omitted engine meaning Claude and counting as paid): `POST story/who`, `story/explain`, `story/recap`, `story/relationships`, `wiki/update`, `ask` (`{question <=2000, chat_history <=40 turns of {role: user|assistant, content <=20000}, engine?, model? <=100}`; the service also caps the history at 100000 characters). **LLM routes are synchronous**, like the service: the request waits for the engine; there is no job id. Engine failures come back redacted (keys and paths stripped by the service), a missing key is 503, a translation-only engine 400. Not exposed: `media_file_path` (React plays media through `/api/media` and `/api/dub`) and `get_series_glossary` (the glossary routes cover it). No path, file name or key appears in any body or header. The route table in `docs/remote-access-decision.md` is regenerated after merge, not edited here; note that the lookup route's row (`lines.edit`) does not show its in-handler `jobs.start` + engine gate for `use_llm`. *Security review fixes (M1/M2):* because each synchronous LLM request holds a server worker thread (Starlette's pool is 40, and a free Ollama call may take up to 300 s), the six LLM routes, the lookup with `use_llm` and the rich `.apkg` export share a non-blocking in-flight cap in the router: at most 2 server-wide and 1 per caller (user id, or "local" with auth off); over either cap is an immediate 429 `rate_limited`, and the slot is always released. The service bounds each request: a recap sends only the last 400 lines before the page and at most 60,000 characters of their English (`truncated: true` in the response when it cut); a wiki update reads at most 10 chunks of 150 text lines per call and returns `{updated, remaining, next_line_idx}`, so the client calls again with `from_line_idx=next_line_idx` until `remaining` is 0; a rich deck has at most 300 cards (`X-Cards-Capped: 300` when cut), each audio clip is cut with a 15 s ffmpeg timeout (`core.extract_audio_slice` gained an optional `timeout`) and clips stop after a 120 s budget (later cards are text-only). The rich deck embeds audio clips only for a caller holding `media.stream`, since media bytes need it; for anyone else the deck is built text-only and the response carries `X-Audio-Omitted: true`. Tests: `tests/test_api_reader_m4.py` (fakes only). UI pending.

**Route batch 2C -- auto-tune and glossary from novel (2026-09-29, #365).** Routes over the #361 services, in `api/routers/transcribe_routes.py` and `api/routers/glossary_routes.py`. Auto-tune: `POST /api/transcribe/dramas/{id}/autotune` (`jobs.start`; body `{candidates?: 1-6 ints 300..3000, initial_prompt <= 1000}`; returns `{job_id, candidates}`) has no engine gate because it is local ASR (`transcribe_service.PAID_ENGINE_FUNCTIONS == ()`); `GET .../autotune` (`library.read`) returns `{job_id, status, progress, message, results?, best_candidate_ms?}`; `POST .../autotune/apply` (`lines.edit`, body `{candidate_ms}`) writes only `min_silence_ms` and returns the `TranscribeConfig`. Glossary from novel: `POST /api/glossary/dramas/{id}/from-novel` (`jobs.start`) calls `require_engines_allowed` with the drama's own engine (`glossary_service.novel_glossary_engine`, default claude), so a household user without `engines.paid` can only run it on a free engine (ollama). The gate resolves the drama's stored engine server-side because the request can't name one, and passes the checked name to `start_novel_glossary_run(engine_name=...)`, which refuses with 409 if the stored engine changed in between (e.g. a bulk resume), so the run and its key are always for the engine the gate checked; returns `{job_id, engine, paired}`; `GET .../from-novel` (`library.read`) returns the status plus `proposals?`; `POST .../from-novel/apply` (`lines.edit`, body `{terms 1-1000, overwrite_existing, confirm}`) matches terms by text and needs `confirm=true` with `overwrite_existing` (else 422). Both jobs' results live only in `background_jobs` memory (`JobRecord` doesn't carry them), so two getters were added, `transcribe_service.get_autotune_status` and `glossary_service.get_novel_glossary_status`: they return an explicit field allowlist of the result, a redacted message (a failed job's redacted error), and 404 when no job is resident (for example after a restart). Errors: unknown drama 404; no audio, no series, no novel, no finished run 400; duplicate run 409; no key 503; bad body, and applying an unmeasured candidate (the service's `InvalidInputError`), 422. The route table rows in `docs/remote-access-decision.md` are left to the lead. Tests: `tests/test_api_autotune_novel_glossary.py` (fakes only; no model, LLM or network). UI pending.

**Route batch 2A -- library admin (2026-09-29, #376).** `api/routers/library_admin_routes.py` (prefix `/api/library/admin`) over `services/library_admin_service.py` (#356, hardened in #369). `admin.library`: `POST /bulk/status` (`{drama_ids 1..500, status}` with status a Literal of the service's `STATUSES`), `POST /bulk/tags` (`{drama_ids, tag, present}`, tag a Literal of `db.ORGANIZATIONAL_TAGS`), `GET /artifacts/{kind}/info` (kind backup/export/database; `{kind, name, size}`, never a path) and `GET /storage?preset=` (dry run). `jobs.start`: `POST /bulk/translate` (`{drama_ids, default_locale}`): the route calls `bulk_translate_engines`, runs `require_engines_allowed` on every engine the job would use (so a household user without `engines.paid` gets 403 for a claude drama and passes for ollama), and always passes `by_drama` as `expected_engines`, so a drama whose engine changes later is skipped rather than run on an engine that wasn't checked. `local_only()`: `POST /bulk/delete` (confirm=true + `DELETE`), `POST /export` (`{drama_ids?}`), `POST /backup` (`{database_only}`; true uses `start_database_backup`), `GET /artifacts/{kind}` (download), `POST /restore` and `POST /storage/clean` (confirm=true + `CLEAN`). Bulk calls return one result per requested id (`not_found`, `job_running`, `delete_failed`, `not_translated`); per-id messages pass through `redact_secrets`. Confirm fields are `StrictBool`, so the string `"true"` is a 422. The download opens the file (`O_NOFOLLOW` where available) and streams from the open handle with `Content-Disposition: attachment` (sanitized ASCII name), exact `Content-Length`, `nosniff`, media type zip or `application/octet-stream` for the database; no artifact, or a file gone between lookup and open, is the same generic 404. Restore is multipart (`file`, `confirm` which must be the string `true`, `confirm_text` `RESTORE`); the confirm is checked before the upload is read, the upload is refused over `BAIHE_MAX_UPLOAD_MB` (422; `UploadFile.size` first, then one read of cap+1 bytes), and `restore_backup` gets the bytes plus `actor_id` from the principal. Export, backup and database backup refuse with 409 while a bulk delete or storage cleanup runs (`background_jobs.maintenance_active()`), and the bulk translate job skips a drama as `skipped_running` if its `translate_<id>` job could not be started (someone else's run), instead of adopting it. Status codes: 409 while a job, restore or maintenance holds the library; 422 for a bad zip, a bad confirm or bad input. Known limits: Starlette spools the whole multipart body to a temp file before the handler runs, so "before the body is read" means before it is read into memory or validated (remote clients are refused before parsing by `EarlyAuthGate`); the service takes bytes, so a restore holds the whole upload in memory (up to the upload cap); on a `local_only` route the principal is the local owner, so the audit `actor_id` is always None today. Route table rows are left to the lead. Tests: `tests/test_api_library_admin.py` (real export/backup jobs on a temp library, fake translate starter; no network). UI pending.

**Re-transcribe one line (parity audit B1, inventory R23; 2026-09-29).** Preview then apply, as Streamlit ("Re-transcribe" shows the text, "Use this" writes it). Three routes in `api/routers/transcribe_routes.py`: `POST /api/transcribe/dramas/{id}/lines/{line_id}/retranscribe` (`jobs.start`; optional body `{initial_prompt?, extra_names?}`, each at most 1000 characters, extra fields refused) starts thread job `retranscribe_<id>` (GPU-queued, `gpu_touching=True`) and returns `{job_id, drama_id, line_id}`; poll `GET /api/jobs/{job_id}` for status. The job cuts the line's window with `core.extract_audio_slice` (`timeout=120`; a timeout ends the job with `failed_reason: audio_slice`) and runs `core.transcribe_for_timing` with the drama's full-transcribe settings (whisper_size, source_language, beam_size, min_silence_ms, vad_threshold, whisper_fast_mode, the server's use_gpu) and the `_resolve_initial_prompt` prompt from #393. It writes nothing. Its in-process result holds `line_id`, `proposed_zh` (at most 2000 characters), `base_zh` (the line's zh when it started) and the start/end then; only `line_id` (and `gpu_fallback`) is in `jobs_service.RESULT_ALLOWED_KEYS`, so line text never lands in `job_records` or `GET /api/jobs` (security review MEDIUM). `GET .../lines/{line_id}/retranscribe` (`lines.read`, like auto-tune's "results are only readable here") returns `{job_id, line_id, status, proposed_zh, base_zh}` raw for the finished result of that drama and line, else 404. `POST .../retranscribe/apply` (`lines.edit`; body `{job_id, expected_zh, expected_proposed}`) writes `proposed_zh` to that line's zh only when both expected values equal the raw ones held for the run (so what was shown is what is written, and the apply is tied to that run) and the line's zh/start/end are unchanged since the job started, as one compare-and-set (`db.update_line_fields_if`); otherwise 409, nothing written, the user's edit wins. Before writing it saves a line-history snapshot ("before re-transcribing line N"). Other errors: unknown drama or foreign line 404; no audio pipeline, no audio or a zero-length window 400; bad body, ids or a job_id that isn't this drama's `retranscribe_` 422; 409 on start while `retranscribe_`, `transcribe_`, `fixflag_`, `resegment_` or `narration_` runs or is queued for the drama. Local Whisper only, even when the drama's full transcribe uses Groq (as Streamlit's button), so there is no engine gate. `retranscribe_` joins `background_jobs.LINE_WRITING_JOB_PREFIXES` (jobs that write to, or propose for, a drama's lines). Also fixed here (security review): `update_transcribe_config` accepts `whisper_size` only from the known model names (`core.WHISPER_MODELS` plus the download-size table: tiny, base, small, medium, large, large-v1/v2/v3, large-v3-turbo), 422 otherwise, so a `lines.edit` user can't make faster-whisper download an arbitrary Hugging Face repo. React: `RetranscribeLine.tsx`, after "Where this line came from" in the line editor's details when the drama has audio: progress and Cancel for its own run (a job record naming another `line_id` is ignored), then "Now"/"Heard" (raw, from the GET) with "Use this" and "Discard"; `LineOrigin` passes the applied text to its `onChanged`, which `LineRow` wires to `applyLine` (the row shows the new text; the editor closes unless it holds unsaved changes). **Known limit:** proposals live in the API process's memory only, so after a restart the GET and apply return 404 and the user re-transcribes. Each apply adds a line-history snapshot, and history keeps the newest 10 per drama. Tests: `tests/test_api_retranscribe_line.py`, `retranscribeLogic.test.ts`, `e2e/retranscribe-line.spec.ts` (job, GET and apply mocked). Real Whisper on a real slice was not run.

**Review parity R39 and R10 -- use a saved version, retry a blocked line (2026-09-29, branch `slice-versions-retry-blocked`).** Two Streamlit-only Review features from the parity audit (B1). R39: `services/translation_version_service.py` + `POST /api/review/dramas/{id}/versions/{vid}/activate` (`api/routers/translation_version_routes.py`, `lines.edit`, body `{confirm: true}` strict, 422 otherwise). It takes a "before switching version" line-history snapshot, writes only `en` by permanent line id (`db.save_lines(..., fields=("en",))`) and marks the version active; 404 for another drama's version, 409 while any job runs on the drama (`drama_service.job_running_for_drama`). Deliberate difference from the tab: a version saved over a different set of lines (merged, split, re-segmented since, or pre-Step-2 ids) is refused with 409 instead of being restored whole by a full sync. R10: `services/blocked_retry_service.py` + `POST /api/lines/dramas/{id}/lines/{lid}/retry-blocked` (`api/routers/blocked_retry_routes.py`, `jobs.start` plus `require_engines_allowed` on the chosen engine; default `ollama` as in the tab). Synchronous like the tab, inside a slot of the shared cap `api/llm_slots.py` (moved out of the Reader router; 2 server-wide, 1 per caller, 429 when busy): one `translate_batch([zh], {source_language, line_ids: [id]})`, the answer taken only when exactly one comes back for that id. Only the engine name is accepted: the model is the engine's default and Gemini free tier the saved setting (a caller-supplied `model` could reach `transformers.pipeline(model=...)` for nllb). The paid-engine check runs as a dependency before body validation. Refused with 409 while a translate, bulk-translate, flag or fix-flagged job runs for the drama, before any engine is built. Success writes `en`, `flag`, `flag_note` in one compare-and-set UPDATE (`db.update_line_fields_if`, expected `zh`/`en`/`flag`; `flag` and `flag_note` are now accepted as expected columns), so an edit or dismissal during the call is a 409 with nothing written; a second block stores only `"<engine>: <reason>"` (redacted) in `flag_note` and returns `blocked: true` with the bare reason. 409 when the line isn't flagged `content_blocked`; 503 without a key; an engine failure is the fixed 500 "The engine call failed." with the redacted detail in the app log only. React: a two-step "Use this version…" `ConfirmButton` per non-active row in Records -> Translation versions (`ConfirmButton` gained optional `verb`, default "delete", used in the confirm label and the announcement, and `tone`, default "danger"; existing callers unchanged), and a retry panel with an engine picker in the line's Edit details area when the flag is `content_blocked`, disabled while the details draft is unsaved or a job runs; the returned line is applied in place (new `applyLine` row action in `LinesPanel`). Tests: `tests/test_api_versions_retry_blocked.py`, `frontend/src/api/reviewVersionsRetry.test.ts`, `frontend/e2e/review-versions-retry.spec.ts`.

**Parity X02/X22 -- apply a workflow tier, save as preset (2026-09-29).** From the B1 parity audit. `services/translate_run_service.py` gains `apply_workflow_tier(drama_id, tier)`, a port of `tabs/workspace_tab.py` `apply_workflow_tier`: the tier's engine goes onto the drama row (`db.update_drama(translation_engine=...)`, a literal kwarg), and the model, Reflect and Auto QC come back for the form (Streamlit put them in session_state). It starts nothing. It also gains `save_translate_preset(...)`, the tab's "Save as preset": `db.save_preset` with engine, model, style, locale and the she/her and genre toggles, validated against `translate_engines.ENGINES`, the engine's model list, `translation_guide.STYLE_PRESETS` and `LOCALES`. One change from Streamlit: saving under a taken name replaced that preset silently; the service now inserts first (`db.insert_preset`, new, insert-only) and maps the UNIQUE(name) failure to Conflict (409) unless `overwrite=true`, so there is no check-then-write race; React asks "Replace it" first. Replacing is effectively a delete, so `overwrite=true` from a non-loopback client with auth on is 403 (`is_local_request`, as `system_routes` does). Routes in `api/routers/translate_run_routes.py`: `POST /api/translate-run/dramas/{id}/workflow-tier` (`lines.edit`, like other per-drama stage config; body `{tier}` a Literal of `WORKFLOW_TIERS`) and `POST /api/translate-run/presets` (`admin.library`, matching preset rename: a library catalogue write. A new name deletes nothing, so the route itself is not `local_only`; only the overwrite is PC-only, and delete stays PC-only). Neither calls an engine, so no paid-engine gate. React: a "Starting tier" picker plus "Apply tier" at the top of the Translate form (`applyTierToForm` sets engine, model and Reflect, keeps bulk only if it still fits, touches nothing else; `withSavedEngine` updates the loaded config's default engine so "Default", validation and a saved preset use the tier's engine) and a "Save as preset…" control with a name prompt at the end of Advanced (`buildPresetBody`; a blank model is saved as null, the engine default). Tests: `tests/test_api_translate_presets_tiers.py`, vitest in `translateForm.test.ts` and `api/translateStage.test.ts`, Playwright `e2e/translate-presets-tiers.spec.ts` (write routes mocked).

**Report a problem (user request, 2026-09-29, branch `feat-report-problem`).** A header button in the React app opens a dialog (what happened, what you expected, optional PNG/JPEG screenshot up to 5 MB, "Include recent server log" on by default). `frontend/src/report/capture.ts` keeps ring buffers from page load: 30 console errors/warnings (strings and Error name/message only; objects become a type tag), 30 window errors and unhandled rejections, 30 failed API calls recorded by `api/client.ts` (method, path without query, status, error code; never bodies or headers) and the current route plus the last 10 route changes. The report adds `/api/meta` versions, the entry asset name as build id, user agent, viewport and PC/LAN/remote mode. `services/bug_report_service.py` stores it as files under `<library>/bug_reports/<UTC stamp>_<n>/` (`report.json`, `report.md`, screenshot with EXIF/XMP/PNG text chunks stripped), adding the git commit, a setup-check summary and (when asked) the last 40 redacted log lines. Every stored string passes `redact_secrets` and `redact_for_support` plus cookie/CSRF/long-token and user-home-folder masking; routes keep their shape unless they look like filesystem paths. The existing "bug bundles" (`db.bug_reports`, per-line replay inputs) are a different store and are not reused. Routes: `POST /api/diagnostics/bug-reports` (`library.read`; multipart, Content-Length checked before parsing; the server section is returned only to `admin.diagnostics`), `GET /api/diagnostics/bug-reports` and `GET .../{id}` (`admin.diagnostics`), `POST .../{id}/delete` (`local_only()`, `confirm=true`). After saving, the dialog offers Copy report and Open GitHub issue (`.github/ISSUE_TEMPLATE/bug.yml`; the link pre-fills the form fields by id and is cut to 6000 characters, with a note to paste the full text); if saving fails, both still work with the client data. Diagnostics gains a "Bug reports" section (Copy, PC-only Delete). Tests: `tests/test_api_bug_reports.py`, `src/report/*.test.ts`, `e2e/report-problem.spec.ts` and `e2e/report-problem.mobile.spec.ts`. Security-review fixes (same branch): the screenshot part is PC-only (a remote request with one gets 403; the dialog hides the field off the PC); a request with `Transfer-Encoding` is refused and the body stream is counted, ending in 413 past the cap whatever `Content-Length` says; the GitHub link uses only the server-scrubbed `issue_markdown`/`what_happened`/`expected`/`title` (never the server section; on the save-failed path the client masks keys, tokens, cookies, URL userinfo, user folders and paths); the server scrub also masks URL userinfo, credential query parameters of any length and Cookie/Set-Cookie values; the report cap is checked before git/setup/log work (git commit and setup summary cached), 5 reports per principal per 10 minutes (429); report ids come from a high-water mark and are never reused, and delete needs the folder stamp from the list; deeply nested JSON is a 422; starlette floor raised to 0.40 (`form(max_part_size=)`).

**Next candidates:** superseded. Every candidate listed here has since been built
(`chunk_and_tag` narration in `services/narration_service.py`, the qwen3 backends in
`services/transcribe_service.py` with fakes (#290), ASS/audiobook/burned-in video export
and Dub). The live queue is in `docs/migration-handoff.md`.

---

## 6. Decisions

Status as of 2026-09-28 (user answers in brackets).

- **D1. Where the API runs. [Decided 2026-09-28: its own process, with
  all four fixes below to be implemented.]** An earlier version of this section said a separate
  process "needs Step 41's durable jobs first" and wouldn't share the GPU
  guard. That was overstated. Step 25w's SQLite GPU lock
  (`db.try_acquire_gpu_lock`) already coordinates GPU jobs across
  processes (it's how `cli.py` and the UI avoid colliding). The real costs
  of a separate process are narrower:
  1. job *lists* aren't shared (each UI only sees jobs it started);
  2. settings pushed into module globals (GPU-limit toggle, notify
     toggle, `page_server` config) don't cross over;
  3. model caches load once per process;
  4. `db.init_db`'s check-then-ALTER migration could race on a
     simultaneous first start after an upgrade.

  Fix (1) with a small SQLite job table (records only, no resume), which
  is much smaller than Step 41's checkpointing. Fix (2) with settings read
  from the DB or `.env`, which D2 already implies. (3) is acceptable. Fix
  (4) with a `try/except` around each `ALTER`. The upside of a separate
  process: the API is independent of Streamlit's lifecycle, it's the same
  shape as the end state (retiring Streamlit is just not starting it),
  it's a standard uvicorn deployment, and there's no thread-embedding.
- **D2. API keys: [decided: server-side only.]** `.env` / environment.
  The API reports "configured / not configured", never the value.
- **D3. FastAPI core: [decided: fully integrate.]** Done on this branch.
  `fastapi`/`uvicorn` are in `requirements-core.txt` and `required` tier
  in Diagnostics. CI installs them plus `httpx`, and a new `frontend` job
  runs build, unit tests, lint and the Playwright e2e.
- **D4. Streamlit UX investment. [User: React + FastAPI as soon as
  possible while the app stays usable; asked for a judgment.]**
  Judgment: stop new Streamlit-only *polish* on any screen React will
  replace, and keep all bug fixes. Bug fixes land in logic React reuses,
  and they keep the app usable meanwhile. Concretely: Step 19's
  click-through pass and the Issue 12 data-editor dark-mode work aren't
  worth doing in Streamlit (React fixes dark mode natively). Anything a
  step builds as a plain function or service carries straight over, so
  new features should be built service-first, with a thin Streamlit
  surface. Migrating a service-first feature later costs only its UI.
  Migrating one written inside a widget handler costs an extraction plus
  the risk of dropping its guards (§4).
- **D5. Admin actions over HTTP: [Decided 2026-09-28: admin permission,
  explicit confirmation, initially only from the Baihe PC.]** Enforced by
  putting admin endpoints on a separate listener that Tailscale Serve
  never publishes. The identity headers identify users, not devices.
  During the transition, **Streamlit is never published through
  Tailscale**; it stays PC-only until it's removed. Remote access is
  React-only. See [`remote-access-design.md`](remote-access-design.md) §6.
- **D6. Remote access (M8-H). [REPLACED 2026-09-29: Caddy on the PC with a real Baihe login (Google OIDC + allowlist); see [`remote-access-decision.md`](remote-access-decision.md). The 2026-09-28 Tailscale decision below is kept for history. D5 is unchanged.]** [Was decided 2026-09-28: Tailscale + Tailscale
  Serve (option A).] Private to the household's tailnet, no open ports,
  no domain, HTTPS and identity from Tailscale. Baihe adds
  deny-by-default permissions keyed on `Tailscale-User-Login`. Option E
  (built-in logins, publicly exposed) was chosen first and then reversed
  on effort. Full design:
  [`remote-access-design.md`](remote-access-design.md).

---

## 7. Size of the whole thing

Summing the areas above (S≈days, M≈1–2 weeks, L≈several weeks, XL a
program), a full replacement of Streamlit is **several months of
sequential work**. That's consistent with the roadmap's earlier estimate,
and Workspace is about half of it. The phases are designed so the app
stays fully usable in Streamlit the whole time, and each phase is useful
on its own even if the migration later stops. Phase 3 (job runner
extraction) also pays off for Streamlit and `cli.py` whether or not React
ever replaces anything.
