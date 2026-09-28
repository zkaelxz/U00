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
| 4 | In-process API host + jobs API | Uvicorn thread inside the Streamlit process; `POST/GET /jobs`, cancel; SSE after Step 44 | D1 |
| 5 | Settings service + standalone Translate | Keys/settings model; the Translate tab in React | D2 |
| 6 | Workspace, stage by stage | Export → Diarize → Transcript → Dub → Pick/create → Source → Translate → Review | phases 3–5; §4 tests per stage |
| 7 | Scanlate editor | React canvas editor | phase 4 |
| 8 | Sources / Discover / Live | Local-only marking for sign-in; SSE for Live | phases 4–5 |
| 9 | Admin actions | Install/upgrade, reset, restore via the API, if ever | D5 / M8-H |
| 10 | Retire Streamlit screens | Per screen, only after M8-A-style real-device checks | each screen's gate |

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
- **D5. Admin actions over HTTP: [proposal pending confirmation.]**
  `admin.system` permission, a confirmation step, and only from devices
  the admin marks as trusted (default: the Baihe PC). Until built, these
  stay in Streamlit, which is owner-only over Tailscale. See
  [`remote-access-design.md`](remote-access-design.md) §6.
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
