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
| **8** | ✅ Built, read-only half only. `GET /api/jobs`, `GET /api/jobs/{id}` through the Slice 7 table. **`POST /api/jobs/{id}/cancel` deliberately not built**: `request_cancel()` only sets a flag in the calling process's own in-memory `_jobs` dict, a no-op from a different process; making it cross-process means the *owning* process has to notice the request, which `is_cancel_requested()`'s existing single choke-point (every cooperative job-loop call site already funnels through it) could do with a throttled DB fallback check -- but that's a real latency/IO-cost design decision (how often to poll, whether to cache a hit), not a mechanical follow-on to the read side. Left as its own, not-yet-scoped follow-up rather than rushed in here. | Slice 7 | Small |
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
key server-side (`_resolve_api_key`, per-engine -- `test_offline` gets the
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

**Slice 49 -- process-job completion hook (API-started diarization applies its own result).** A process job's `result` lived only in the starting process's memory and only Streamlit's render loop persisted it, so an API-started diarization (Slice 16 endpoint, or the chain-start inside the Slice 20/21 transcribe job) ended "done" with its speaker turns never saved. `background_jobs.start_process_job` now takes optional `on_done(job_id, result)`, called in the watcher thread after a successful result and before the job is marked "done"; a raising hook ends the job "error" (redacted message, logged), it is not called on error/cancel, and queued GPU starts carry it through the queue. `diarization_service.apply_diarization_result` ports the DB half of Streamlit's `_apply_diarization_job_result`/`_apply_speaker_turns` (save turns, `merge_speakers`, character upserts, field-scoped `save_lines(fields=("speaker","speaker_manual"))`); both `start_diarization_run` and the transcribe chain-start pass it. One deliberate difference: Streamlit skips the merge when manual lines would change and asks the user; with no user to ask, the API merges with `overwrite_manual=False` (manual corrections still never undone). Double-apply with the tab is harmless (same turns file rewritten, merge idempotent). Hard cancel is unchanged.

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
`novel_narration_source.txt`, tags speakers with `tag_speakers_llm` (id-keyed; a
wrong-length result falls back to all Narrator), upserts characters, takes a "before chunk
& tag speakers" history snapshot, then replaces the drama's lines (the same full replace
Streamlit/CLI do; the lines are brand new) and sets status `aligned`. Errors: unknown drama
404, no novel text or non-LLM engine 422, no key 503 (Streamlit's silent all-Narrator
fallback is deliberately not offered), duplicate run 409; failed-job errors are redacted by
`background_jobs`. Not verified against a real LLM (fake engine only).

**Next candidates:** the

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
series rename/unassign, presets CRUD, metadata auto-fill, personal notes.

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
refusal 422. Deliberate differences: the summary engine is always local Ollama
(no per-session Settings pick); Ollama's num_ctx override is not applied.
Out of scope: bulk/Reflect (Slice 41), the fallback chain (Step 97b). Paid-key
runs were not verified (tests use the offline engine and fakes only).

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
confirm and `drama_service._job_running_for_drama` refusal), and Continue reading (per-profile).

**Next candidates:** the `chunk_and_tag` novel-narration path (needs its
own scoping -- fully synchronous today, no natural job boundary), the
experimental `qwen3_asr`/`qwen3_forced_align` backends, or continue with
ASS export/audiobook/burned-in-video export (Export's remaining scope) or
Dub (§3.2's build order).

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
- **D6. Remote access (M8-H). [Decided 2026-09-28: Tailscale + Tailscale
  Serve (option A).]** Private to the household's tailnet, no open ports,
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
