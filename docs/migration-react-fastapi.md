# React + FastAPI migration

**What changed in this document (2026-09-29).** It used to be titled
"React + FastAPI migration -- foundation (phase 1)" and described only the
first experimental branch. The app began as Python + Streamlit. It now has a
FastAPI service layer (`services/` + `api/`) and a React frontend
(`frontend/`) that run **alongside** Streamlit and share its library and
database. The migration is moving stage by stage, and it has many more
phases than the foundation. This page is now the overview of all of them.
The original foundation write-up is kept, corrected, in
[Part 3](#part-3-historical-record-the-phase-1-foundation).

**Current status (2026-09-29).** Every ungated backend slice is merged: 26
routers, about 115 routes, 28 service modules. The React frontend has a
router, Library, Diagnostics, Settings, standalone Translate and a Workspace
shell with Source, Translate, Dub and Export stages. The Review stage is
still a placeholder. Streamlit is still the shipped, complete UI and nothing
has been retired. Two backend slices are gated on the user (24 and 34), and
several real-hardware checks are still owed. GitHub Actions minutes are
exhausted, so merges are gated on the local test suite, not CI.

Related documents:
[`migration-handoff.md`](migration-handoff.md) (durable status, slice recipe, queue),
[`migration-review.md`](migration-review.md) (whole-app review, per-slice detail, decisions D1-D6),
[`migration-frontend-plan.md`](migration-frontend-plan.md) (React phase plan),
[`baihe-roadmap-master.md`](baihe-roadmap-master.md) (bug tracker, to-do, deferred steps),
[`../README.md`](../README.md) (how to run).

## Contents

1. [Part 1: Phases and status](#part-1-phases-and-status)
2. [Part 2: What is different from the original Python-only design](#part-2-what-is-different-from-the-original-python-only-design)
3. [Part 3: Historical record: the phase 1 foundation](#part-3-historical-record-the-phase-1-foundation)
4. [Part 4: How to run](#part-4-how-to-run)
5. [Part 5: What is next](#part-5-what-is-next)
6. [Unverified / to confirm](#unverified--to-confirm)

---

## Part 1: Phases and status

Yes, there are more phases: the foundation was phase 1 of 11 numbered
phases (0-10, defined in `migration-review.md` section 5), plus a separate
React frontend phase. Status words: **done**, **in progress**, **not
started**, **gated** (waiting on a user decision or check).

### Backend and migration phases (`migration-review.md` section 5)

| # | Phase | Status | Done so far | Remains | Depends on |
|---|---|---|---|---|---|
| 0 | Decisions D1-D6 | done | All six decided 2026-09-28: D1 API in its own process, D2 keys server-side only, D3 FastAPI core, D4 stop Streamlit-only polish, D5 admin actions PC-only with confirmation, D6 remote access via Tailscale Serve (`migration-review.md` section 6) | Implementing D5/D6 belongs to phase 9 | none |
| 1 | Foundation | done | FastAPI app, React app, Library list/detail, `library_service`, Playwright e2e (see Part 3) | none | none |
| 2 | Low-risk reads | done | Reader read-only endpoint (Slice 4), Diagnostics overview (Slice 5, #205), Library remaining reads (E0, #246) | Destructive Library actions (bulk, backup/restore, storage clean) were deliberately deferred inside E0 | none |
| 3 | Extract job runners | done | `run_*_job` functions moved to `services/workspace_job_service.py` (Slice 2); `migration-review.md` section 5.1 records that all were already extracted | none | none |
| 4 | API host + jobs API | done except SSE | Own-process API (D1); `init_db` ALTER race guard (Slice 6, #207); cross-process job records (Slice 7, #208); `GET /api/jobs`, `GET /api/jobs/{id}` (Slice 8, #209); cross-process cancel (Slice 22, #242); `app_settings` table (Slice 9, #210); process-job completion hook (Slice 49, #235) | SSE / job push (nothing exists; jobs are polled). A generic `POST /api/jobs` was never built: each feature has its own start endpoint | D1 |
| 5 | Settings service + standalone Translate | done except key writes | `GET /api/settings` (Slice 10, #211); non-secret settings writes and persisted `use_gpu` (Slice 23, #236); Translate read endpoints (Slice 11, #212), translate action (Slice 13, #214), history clear (Slice 17, #218); Translate config and estimate (Slice 39, #226) | **Slice 24, API-key writes: gated** on the D5 loopback/admin policy decision | D2 (decided), D5 for Slice 24 |
| 6 | Workspace, stage by stage (API side) | done except Slice 34 | Export: readiness (#213), SRT/VTT (#215), flags (#216), EPUB (#219), ASS (#224), audiobook and burned-in video jobs (Slices 29+30, #250), artifact download (#239). Diarize (#217). Source/Transcribe: config (#220), transcribe (#221), hardsub OCR (#222), upload (#240), upload-and-transcribe (#245), metadata auto-fill (#247), novel attach and chapter OCR (#248), narration (#241). Dub: config/pacing (#223), run (#243). Translate: run (#244), Reflect and bulk (#266). Characters (#227), glossary (#228), line edits (#237), Review reads (#229, #230), Review AI jobs (#249), restructure and version restore (Slice 45, #267), drama create/update/delete (#225, #231) | **Slice 34** (`qwen3_asr` / `qwen3_forced_align` backends): **gated** on a real-model check by the user | phases 3-5 |
| 7 | Scanlate editor | not started | No Scanlate API or React screen exists | React canvas editor; needs its own API first | phase 4 |
| 8 | Sources / Discover / Live | not started | No Sources-tab, Discover or Live APIs exist (`api/routers/source_routes.py` serves the Workspace source stage, not the Sources tab) | Local-only marking for sign-in flows; SSE for Live | phases 4-5 |
| 9 | Admin actions | not started | none | Install/upgrade, reset, restore over the API, if ever | D5 / D6 (decided in principle; see `remote-access-design.md`) |
| 10 | Retire Streamlit screens | not started | Nothing retired | Per-screen criteria, see Part 5 | each screen's gate |

Also merged in this effort: hardening H1 (#232), H2 (#233), H3 (#234) and
Step 97b, the translate fallback chain (#251); Step 95, BGM-preserving dub
(#261); fix #262 (Streamlit dub worker argument order); fix #259 (test
race). Details in `baihe-roadmap-master.md`.

### React frontend phase (`migration-frontend-plan.md`)

| Slice | Scope | Status | Done so far | Remains | Depends on |
|---|---|---|---|---|---|
| F | Foundations: write/multipart helpers, `useJob` polling, `ErrorBanner`, hash router, CSS tokens | done | #254 | none | none |
| A | Library page (stats, search, create, typed-confirm delete) | done | #258, wired in #263 | Destructive bulk/backup/restore actions need a server-side typed confirm first | F |
| B | Diagnostics page and job list with cancel | done | #257, wired in #263 | Cancel-then-refresh has no automated test (B-24) | F |
| C | Settings page (optimistic boolean toggles) | done | #256, wired in #263 | API-key writes stay out until Slice 24 | F |
| I | Standalone Translate page | done | #260, wired in #263 | none | F |
| D | Workspace shell plus Source/Transcribe stage | done | #265 | none listed | F |
| E | Translate stage (config, estimate, run, glossary, characters) | done | #268 | Whether the stage exposes the Reflect/bulk modes (Slice 41) is unverified | D |
| F2 | Review stage (line edit, find/replace, notes, review jobs, records) | in progress | Placeholder only on `baihe-subtitler` (`ReviewStage.tsx`). A local branch `migration-frontend-f2-review` exists in this checkout but is not on origin and not merged | Everything | D; the backend Review slices are merged |
| G | Export stage (readiness, SRT/VTT/ASS, flags, EPUB, audiobook and burned-in video) | done | #269 | none listed | D |
| H | Dub stage (config, pacing, run, narration chunk-and-tag) | done | #270 | Whether a Step 95 BGM option is exposed is unverified | D |

Frontend gaps that need backend work first (from `migration-frontend-plan.md`
and `baihe-roadmap-master.md`): media playback with Range support, an exposed
workspace stage index, serving `frontend/dist` from FastAPI plus a launcher,
SSE/job push. Line-structure operations (add/delete/merge/split/re-segment,
version restore) now have an API (Slice 45) but no React UI.

### Gated on the user

- **Slice 24 (API-key writes):** needs the D5 loopback/admin policy decision
  applied to key writes.
- **Slice 34 (Qwen3 ASR / forced align backends):** needs a real-model check.
- **Real-hardware / real-account checks only the user can run:** real TTS,
  ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR and EPUBs, a
  gated-access Hugging Face token for pyannote diarization, a real-audio
  listening check for Step 95, and mobile/real-device checks before any
  Streamlit screen is retired.

---

## Part 2: What is different from the original Python-only design

- **Layers.** Originally, Streamlit tabs called `db.py` and the engine
  modules directly. Now `services/` (28 modules) holds UI-free logic that
  returns plain dicts and raises errors from `services/service_errors.py`.
  `api/` (26 routers, thin) validates, calls a service and maps to Pydantic
  schemas in `api/schemas.py`. `tabs/` is still the Streamlit UI and, where a
  service exists, calls it directly, never over HTTP. `frontend/` is React
  19 + TypeScript + Vite and talks only to `/api`.
- **The API is its own process** (decision D1): `python -m api`, default
  `127.0.0.1:8600`. It is not a thread inside Streamlit. `page_server.py`
  (the browser-extension bridge) is still separate, still started from
  Streamlit's Settings tab, and still stdlib.
- **Jobs.** `background_jobs.py` is still in-memory per process, but status
  transitions are mirrored to a SQLite job-records table, so either process
  can list jobs, and cancel works across processes (Slice 22). GPU exclusion
  uses the SQLite lock. There is still no resume across restarts and no push
  channel. API job endpoints follow "the job does everything": the job also
  writes its result to the DB. Process jobs use an `on_done` hook to apply
  results.
- **Settings.** Non-secret app settings (GPU limit, notifications, tuning
  knobs, `use_gpu`) live in the DB (`app_settings`) so both processes see
  them, instead of in module globals.
- **Secrets.** API keys stay in `.env`/environment. The API reports only
  configured / not configured booleans and never returns keys, paths or
  URLs. Writing keys through the API is not built (Slice 24).
- **The repo rules apply to every caller.** Field-scoped line writes,
  id-keyed matching and secret redaction hold for the API, the CLI and
  Streamlit alike.

---

## Part 3: Historical record: the phase 1 foundation

> **This part records phase 1 as it was written on 2026-09-27, when it lived
> on the experimental branch `migration/react-fastapi-foundation`. It has
> since been merged and far exceeded.** The original status banner ("never
> merge this branch", "the API only reads", "one read-only feature") is no
> longer true. Where a statement below is now false, a **Correction
> (2026-09-29)** note follows it. Numbers and counts are as measured then.

This is the start of a migration, not a rewrite. The approach is the
strangler-fig one the roadmap already recommended (§3, M8+ analysis): move
logic into UI-independent services one area at a time, let both Streamlit
and FastAPI call those services, and replace Streamlit screens only after
their React equivalents exist and have been checked.

```
            Streamlit (app.py, tabs/)          React (frontend/)
                     │                               │ HTTP, same origin (/api)
                     │ direct Python call            ▼
                     │                        FastAPI (api/)
                     │                               │ direct Python call
                     └──────────────┬────────────────┘
                                    ▼
                     Application services (services/)
                                    ▼
      existing modules: db.py · background_jobs.py · translate_engines.py
                        sources/ · core.py · dub.py · ocr.py · …
```

Streamlit never calls FastAPI over HTTP. Both are UIs over the same Python
code.

---

### 1. What the current codebase looks like (measured 2026-09-27, base `45b5c83`)

| Area | What's there |
|---|---|
| Size | ~48,400 lines of app Python (not counting `tests/`), plus 104 test files. `tabs/` is 11,364 lines. `tabs/workspace_tab.py` alone is 6,418 lines. |
| Entry points | `app.py` (Streamlit: `streamlit run app.py`, port 8501 via `start.bat`/`start.ps1`). `cli.py` (headless batch runner, kept in parity with Workspace). `page_server.py` (the browser extension's stdlib HTTP bridge on `127.0.0.1:8756`, started from inside the Streamlit process). |
| Streamlit layer | `app.py` wires 9 visible tabs (Library, Workspace, Read & Watch, Scanlate, Sources, Discover, Translate, Live, Diagnostics) plus the Settings sidebar. Shared UI pieces live in `ui/`, `common.py` and `ui_theme.py`. **Only these files import Streamlit**: `app.py`, `common.py`, `ui_theme.py`, `tabs/*`, `ui/*`. Every other top-level module and all of `sources/` are already Streamlit-free. |
| Database | `db.py`: plain `sqlite3`, WAL mode, schema created and migrated lazily on first `get_conn()` (`ALTER TABLE … ADD COLUMN` guarded by `PRAGMA table_info`). Thread-aware connection tracking. Library folder fixed at `<app>/library/` (`db.LIBRARY_DIR`); only tests redirect it (`configure_library_dir`). |
| Jobs | `background_jobs.py`: `threading.Thread` and `multiprocessing` workers, state in a **module-level dict in the process that started them**. A soft one-GPU-job-at-a-time queue. Cooperative cancel. No persistence across restarts. |
| Engines | `translate_engines.py`: every LLM/MT engine, id-keyed request/retry helpers, `redact_secrets`. `asr_backend.py`, `diarize.py`, `dub.py`, `ocr.py`, `scanlate.py`, etc. for the media/AI side. |
| Sources | `sources/`: adapter interface, registry, access-method ladder, 13 site adapters, own store (`library/sources.db`). |
| Files/library | Per-drama folders under `library/dramas/<id>/`; `storage.py` for cleanup; `export_package.py` for archives. |
| Settings/config | API keys: environment and `.env`, read by `tabs/settings_tab.py` (UI-coupled) and deliberately never written to the DB. Per-session choices: `st.session_state`. App-wide toggles pushed from Settings into modules (`background_jobs.set_gpu_limit_enabled`, `page_server.set_translation_config`). Env vars: `BAIHE_PORTABLE`, `BAIHE_HF_TOKEN`/`HF_TOKEN`, `BAIHE_MONTHLY_CAP_USD`, `BAIHE_AUDIO_SEP_MODEL_DIR`, `BAIHE_SERVER_ONLY`. `portable.py` redirects model caches. |
| Existing APIs | Only `page_server.py`: `GET /health`, `POST /page`, `POST /pages`. Loopback only, token in the `X-Baihe-Token` header, and **deliberately no CORS**. The extension calls it from its service worker. |
| Tests | pytest, fully mocked (no GPU, network or real models). `isolated_db` fixture. CI (`.github/workflows/tests.yml`) runs a **core-only** install. |

#### Where business logic sits inside Streamlit (the migration seams)

Counted per tab file: widget calls, `db.*` calls, `db.*` writes, job starts, engine calls, and direct file operations.

| Tab | lines | `st.*` | `db.*` | db writes | job starts | engine calls | file ops |
|---|---:|---:|---:|---:|---:|---:|---:|
| workspace_tab | 6418 | 865 | 221 | 129 | 15 | 82 | 32 |
| library_tab | 780 | 120 | 45 | 9 | 2 | 4 | 4 |
| scanlate_tab | 639 | 105 | 18 | 9 | 0 | 4 | 10 |
| reader_tab | 424 | 101 | 17 | 6 | 0 | 7 | 2 |
| diagnostics_tab | 952 | 196 | 12 | 4 | 1 | 5 | 1 |
| translate_tab | 193 | 41 | 3 | 2 | 0 | 17 | 0 |
| settings_tab | 478 | 66 | 6 | 3 | 0 | 7 | 3 |
| discover_tab | 385 | 99 | 7 | 4 | 0 | 3 | 0 |
| sources_tab | 946 | 223 | 2 | 0 | 0 | 4 | 0 |
| live_tab | 149 | 26 | 0 | 0 | 1 | 3 | 0 |

No tab calls `requests` directly; HTTP stays in engines and adapters.

#### Classification

| Area | Class | Why |
|---|---|---|
| `db.py` reads/writes | **Already UI-independent** | Plain functions, thread-safe connections. Reuse directly *behind a service*, never exposed raw as the API contract. |
| `background_jobs.py` | **Already UI-independent**, but **process-bound** | No Streamlit import. State lives only in the process that started the job (see §6). |
| `translate_engines.py`, `core.py`, `dub.py`, `ocr.py`, `scanlate.py`, `diarize.py`, … | **Already UI-independent** | `cli.py` already drives them headlessly. |
| `sources/` | **Already UI-independent** | Adapter interface plus pipeline. `sources_tab.py` is mostly widgets (2 `db.*` calls). |
| `diagnostics.py` | **Already UI-independent** | A dependency-status endpoint would be a thin wrapper. |
| Library list/filter | **Partially UI-independent**, now extracted | SQL filtering was in `db.list_dramas`; quick-filter and custom-tag filtering were inline in the widget code. **Extracted in this phase.** |
| Library bulk actions, backup/restore, presets, voice bank | **Partially** | Mostly single `db.*` calls with confirmation UX around them. Thin services plus Step 43's soft-delete semantics. |
| Settings / API keys | **UI-coupled** | `.env` read/write lives in `tabs/settings_tab.py`, and keys are pushed per session. It needs a server-side settings service before an API can own keys. |
| Workspace pipeline (transcribe → translate → dub → export) | **UI-coupled** | 129 DB writes and 15 job starts inside widget handlers, many of them there *as* fixes for past bugs (stale session state, drama switching). The riskiest area. It goes last, stage by stage, with `cli.py` as the reference for the headless path. |
| Reader/Scanlate | **Partially** | Logic is in `reader.py`/`scanlate.py`; the tabs orchestrate. |

#### Why Library listing was the first target

Read-only. No jobs, keys, GPU or network. It exercises the database, the
file library (`has_audio` etc.) and real filtering semantics. The tab
contained a small piece of real logic (two filters) that a second UI would
otherwise have had to copy. It also matches the roadmap's own named M8
mobile workflows ("library browsing"). The alternatives were weaker:
health/meta alone proves nothing real, diagnostics would be more code, and
settings involve secrets.

---

> **Correction (2026-09-29).** The numbers above were measured at base
> `45b5c83` and are out of date. The existing-API row is also incomplete
> now: besides `page_server.py` there is the FastAPI app (`api/`, port 8600).

### 2. What this phase added

> **Correction (2026-09-29).** This tree lists only the phase 1 files.
> Today `services/` has 28 modules, `api/routers/` has 26 routers (about 115
> routes), and `frontend/` has hash-routed pages (`src/pages/`, a Workspace
> shell with five stages), one API module per area (`src/api/`), a `useJob`
> hook and ten Playwright specs in `frontend/e2e/`. `FILE_ORGANIZATION.md`
> is the current map.

```
services/                      UI-independent application services
  service_errors.py            error vocabulary (InvalidInput/NotFound/Unsupported/DependencyUnavailable)
  library_service.py           list_library_dramas(), get_library_drama(), split_custom_tags()
api/                           FastAPI app (Python package)
  __main__.py                  `python -m api`
  server.py                    create_app(): routers, error handlers, dev-only CORS
  api_config.py                BAIHE_API_* settings
  error_handlers.py            one JSON error shape, redaction, no tracebacks
  schemas.py                   the API contract (Pydantic), API_VERSION
  routers/system_routes.py     /api/health, /api/meta
  routers/library_routes.py    /api/library/dramas, /api/library/dramas/{id}
frontend/                      React 19 + TypeScript + Vite 8 (not a Python package)
  src/api/{client,types}.ts    the only HTTP code in the frontend, typed to the contract
  src/components/…             LibraryList, DramaDetailPanel
  e2e/                         Playwright end-to-end test + seeded-API launcher
tests/test_library_service.py
tests/test_api_foundation.py
```

The one change to existing behaviour-bearing code is in
`tabs/library_tab.py`: its "All dramas" list now calls
`library_service.list_library_dramas(...)` instead of `db.list_dramas(...)`
plus two inline filters. The semantics are the same: the quick filter is
case-insensitive through `db.has_custom_tag`, and custom tags are an exact
match with all of them required.

Naming follows `FILE_ORGANIZATION.md`'s unique-filename convention
(`service_errors.py`, `api_config.py`, `*_routes.py`), so no new file shares
a name with an existing one.

#### API contract (v0.1)

> **Correction (2026-09-29).** This four-endpoint table is only the phase 1
> contract. The API now has about 115 routes over 26 routers, including
> writes (POST, and DELETE for a few notes) and jobs. The current contract
> is `/api/openapi.json`. Two statements here still hold: the error shape
> below, and the CORS middleware allowing only `GET` (`api/server.py`).

Interactive docs: `/api/docs`. Schema: `/api/openapi.json`.

| Method & path | Returns | Errors |
|---|---|---|
| `GET /api/health` | `{"status":"ok"}`. Never touches the DB. | — |
| `GET /api/meta` | `{app, api_version, environment}` | — |
| `GET /api/library/dramas` | `{items: DramaSummary[], count}`, newest first. Query: `search, studio, author, voice_actor, status, source_language, media_type` (exact match, empty = any), `quick_filter` (Favorite / On Hold / Plan to Translate), `tag` (repeatable, all required). | 422 unknown `quick_filter` (with `details.allowed`) or an over-long field |
| `GET /api/library/dramas/{id}` | `DramaDetail` | 404 `not_found`, 422 non-integer or `< 1` |

The contract is not the SQLite row. Stored filenames become booleans
(`has_audio`, `has_novel_reference`, `has_cover_art`). `custom_tags` is a
list. Internal columns (`last_translate_errors`, `personal_notes`, paths)
are omitted. `media_type`/`status` values are passed through, not
enumerated in the schema, so new media types (Steps 87/89, in flight) need
no API change. Adding fields is compatible; renaming or removing one needs
an `API_VERSION` bump. `frontend/src/api/types.ts` mirrors
`api/schemas.py` by hand for now; generate it from the OpenAPI schema once
there are more endpoints.

#### Error strategy

Every error body is `{"error": {"code", "message", "details?"}}`.

| HTTP | `code` | Source |
|---|---|---|
| 422 | `validation_error` | request validation, `InvalidInputError` |
| 404 | `not_found` | `NotFoundError`, unknown route |
| 400 | `unsupported_operation` | `UnsupportedOperationError` (405 for a wrong method uses this code too) |
| 503 | `dependency_unavailable` | `DependencyUnavailableError` (the Diagnostics situation) |
| 500 | `application_error` | any other `ServiceError` |
| 500 | `internal_error` | anything unexpected: generic message, full traceback only in the app log (`applog`) |

Validation errors report where and what, never the rejected input.
Service messages pass through `translate_engines.redact_secrets`. Tests
cover all of this, including a fake API key in a path and a raw exception
carrying a path and a key.

#### Configuration: no second config system

The library and database location stays `db.LIBRARY_DIR`, so the API sees
exactly the library Streamlit and `cli.py` see. Portable mode is honoured:
`api/server.py` and `api/__main__.py` call `portable.activate_portable_mode()`
first, as `app.py`/`cli.py` do. The only new settings are the ones a
network server needs, as `BAIHE_API_*` env vars in the existing `BAIHE_*`
style:

| Variable | Default | Notes |
|---|---|---|
| `BAIHE_API_HOST` | `127.0.0.1` | `0.0.0.0` prints a no-authentication warning (see §7) |
| `BAIHE_API_PORT` | `8600` | not adjacent to 8501 (Streamlit) or 8756 (extension bridge) |
| `BAIHE_API_ENV` | `production` | `development` enables CORS for the origins below, plus uvicorn reload |
| `BAIHE_API_CORS_ORIGINS` | Vite dev/preview on loopback (5173/4173) | development only; `*` is refused |

Bad values fail loudly at startup. No provider keys reach the frontend:
the only frontend setting is the optional `VITE_API_BASE_URL`, which is not
a secret.

#### CORS

The frontend calls the relative path `/api`. Vite's dev and preview
servers proxy `/api` to FastAPI (`BAIHE_API_URL`, default
`http://127.0.0.1:8600`), so **the normal development setup needs no CORS
at all**. `development` mode adds an explicit origin allowlist only for
anyone who points a dev build straight at the API. `production` sends no
CORS headers: the intended deployment is same-origin (FastAPI or a reverse
proxy serving the built `frontend/dist`, which is deferred).

---

### 3. Running it (development)

Requirements: the normal Baihe Python environment (`fastapi` and
`uvicorn` are in `requirements-core.txt`, decision D3), and Node.js 20+
with npm, needed for the frontend only.

```bash
# Terminal 1 -- API (http://127.0.0.1:8600, docs at http://127.0.0.1:8600/api/docs)
BAIHE_API_ENV=development python -m api

# Terminal 2 -- React dev server (http://127.0.0.1:5173)
cd frontend
npm install        # first time only
npm run dev

# Existing app, unchanged, still the real UI (http://localhost:8501)
streamlit run app.py        # or start.bat
```

All three can run at once over the same `library/`. SQLite in WAL mode
handles concurrent readers across processes. (**Correction:** the API no
longer only reads; it writes too. Field-scoped writes and the cross-process
job records keep the two processes safe. See Part 2.)

Checks:

```bash
python run_tests.py                     # whole suite (the API tests skip without fastapi/httpx)
cd frontend && npm run build            # typecheck + production build
cd frontend && npm test                 # Vitest: unit tests (API modules, router, forms)
cd frontend && npm run lint             # oxlint
cd frontend && npm run e2e              # Playwright: real browser -> built React -> real FastAPI on a seeded throwaway library
# (with a preinstalled Chromium: PLAYWRIGHT_CHROMIUM_PATH=/opt/pw-browsers/chromium npm run e2e)
```

A single launcher that starts both is not built yet. The likely shape is a
`start.bat --api` flag that starts `python -m api` next to Streamlit.
`start.bat` was deliberately untouched on this branch because Step 79 was
editing it. (**Correction:** still true today; neither `start.bat` nor
`start.ps1` starts the API or the frontend.)

---

### 4. Proof of the full path

1. The browser loads the built React app from `vite preview`.
2. `LibraryList` calls `api.listDramas({quick_filter: 'Favorite'})`, which
   becomes `GET /api/library/dramas?quick_filter=Favorite`.
3. The Vite proxy forwards it to FastAPI. `library_routes.list_dramas`
   validates the query and calls `library_service.list_library_dramas(...)`.
4. The service runs `db.list_dramas(...)` (SQL), then the quick-filter
   and tag filters: the same function the Streamlit tab now calls.
5. The route maps rows to `DramaSummary` (filenames become flags), and
   FastAPI serializes it.
6. React renders the table. Clicking a row calls `GET
   /api/library/dramas/{id}` → `get_library_drama` → `DramaDetail`.

`frontend/e2e/library.spec.ts` checks exactly this against three seeded
dramas in a real Chromium: list, filter, detail. In a separate check,
Streamlit's own Library tab applied the same Favorite filter through the
same service in a real browser.

Screenshots: `docs/migration-screenshots/react-library.png` (React, seeded
e2e data) and `docs/migration-screenshots/streamlit-library-filtered.png`
(Streamlit Library tab after the service extraction, Favorite filter on).

---

### 5. Streamlit compatibility decision

For this feature Streamlit **also calls the new service** (Streamlit →
`library_service` → `db`), rather than keeping its own copy. That is the
cheapest way to make sure the two UIs can't drift, and it is the pattern
for every later extraction: move logic into `services/`, point the
Streamlit tab at it (verified by the tab's existing tests), then expose it
through a router. Streamlit never calls FastAPI over HTTP.

---

### 6. Background jobs: how the API should work with them (not built yet)

> **Correction (2026-09-29).** This section's recommendation was
> superseded. Decision D1 (2026-09-28) chose to run the API in **its own
> process**, not in-process with Streamlit, and to fix the costs instead:
> a SQLite job-records table so either process sees jobs (Slice 7), an
> `init_db` ALTER race guard (Slice 6, which also resolves the schema-race
> item in section 13), and DB-backed settings (Slice 9). The jobs API is
> `GET /api/jobs`, `GET /api/jobs/{id}` and `POST /api/jobs/{id}/cancel`.
> There is no generic `POST /api/jobs` (each feature has its own start
> endpoint) and no SSE. Everything below is the original analysis; the
> per-process findings (queue order, model caches) still hold.

**The key finding.** `background_jobs._jobs` is a module-level dict. It is
visible across *browser sessions* (roadmap M8-C tier 1 is correct: two
devices on one Streamlit server see the same jobs) but **not across
processes**. A FastAPI server started with `python -m api` is a second
process. It would not see jobs Streamlit started, Streamlit would not see
jobs it started, and each process has its own in-memory GPU *queue*
(`_gpu_queue`). **Correction (2026-09-28):** GPU *exclusion* does already
work across processes. Step 25w added a SQLite-backed lock
(`db.try_acquire_gpu_lock`) that `background_jobs` and `cli.py` both take,
so two processes can't run GPU jobs at the same time. What's per-process
is the queue order and position display. Model caches such as
`core._whisper_model_cache` are also per-process, so each process pays its
own model load, though not at the same time as the other's, since the lock
serializes GPU work.

That is harmless for this phase, which is read-only. It decides the
design for jobs:

1. **Recommended for coexistence (phase 5 start): host the API inside the
   same process as the jobs.** Start uvicorn on a background thread from
   the Streamlit process, the same way `page_server.ensure_server_started`
   already runs the extension bridge. Then `POST /api/jobs` →
   `background_jobs.start_job(...)` and `GET /api/jobs/{id}` →
   `background_jobs.get_status(...)` see the same dict, GPU guard and model
   caches as Streamlit. No queue infrastructure is needed.
2. **Durable jobs** (M8-C tier 2 / Step 41's checkpointing) are what would
   make split processes safe. Job state goes into SQLite and survives
   restarts, and either process can read it. That is the right long-term
   path, but it is Step 41-sized work, not part of this foundation.
3. **Not** Redis/Celery/RQ (M8-C tier 3). Nothing here needs them.

Contract sketch for later:

```
POST /api/jobs {kind, drama_id, params} -> 202 {job_id}
GET  /api/jobs/{id}                     -> {status, progress, message, eta, result?}
POST /api/jobs/{id}/cancel              -> background_jobs.request_cancel
GET  /api/jobs/events (SSE)             -> fed by Step 44's event bus, not a second one
```

`progress`/`message`/`eta` already exist in the job dict
(`update_progress`, `eta_text`). The "phone starts a job, disconnects, and
comes back later" requirement is already met by the in-process registry
while the server stays up. Surviving a server restart is Step 41's
problem.

---

### 7. Authentication and remote access (deferred: roadmap M8-H)

> **Correction (2026-09-29).** Still no authentication in the API. But
> M8-H is no longer undecided: D6 (2026-09-28) chose Tailscale plus
> Tailscale Serve, and D5 says admin actions are PC-only with explicit
> confirmation and Streamlit is never published through Tailscale. See
> [`remote-access-design.md`](remote-access-design.md) and
> `migration-review.md` section 6. Not yet implemented.

No authentication was added, deliberately. The API follows the same
trust model the app has today:

> LAN/trusted-network access currently exists. Remote-access security
> architecture is still a separate decision (roadmap M8-H, deferred).

- Default bind is `127.0.0.1`. LAN exposure is opt-in (`BAIHE_API_HOST=0.0.0.0`)
  and prints a warning. That is not a recommendation for internet exposure.
- Whatever M8-H picks (mesh VPN, reverse proxy with TLS and auth, or a
  gateway) now has to front **two** HTTP services (Streamlit 8501 and the
  API 8600) while they coexist, or one if the API later serves the built
  frontend and absorbs `page_server`. That is worth recording under M8-H
  (proposal B6 below).
- Future write endpoints should classify themselves through the existing
  `action_tiers.py` (Step 57) and Step 43's confirm/soft-delete model,
  rather than inventing an API-specific permission scheme.

---

### 8. Browser extension

Untouched and still decoupled from React. The extension keeps talking to
`page_server.py` (loopback, token header, deliberately no CORS). Nothing
it does depends on `frontend/`. The intended convergence
(`Extension → Baihe API → services`) is a later, explicit move: port
`/page` and `/pages` to a FastAPI router **keeping the token header and
the no-CORS rule** (the dev-only CORS middleware must never apply to those
routes), then retire the stdlib server. That only makes sense after the
API runs in-process (§6 option 1), because the page pipeline's model
caches and `_PIPELINE_LOCK` are per-process too.

---

### 9. Compatibility with roadmap steps that aren't built yet

Checked against the roadmap's not-yet-built and in-flight steps as of
2026-09-27, so this foundation doesn't make them harder:

| Step | Interaction | Handled how |
|---|---|---|
| 43 soft-delete | Library list/detail must hide soft-deleted dramas | One place to change: `library_service`. `get_library_drama` would raise `NotFoundError` for a soft-deleted drama, and both UIs follow. |
| 74 episode ordering/summary | New `episode_number`/summary fields | Additive to `DramaSummary`/`DramaDetail`. No version bump needed. |
| 87 / 89 (in flight) | Language selector; `music` media type | The API doesn't enumerate `media_type`/`source_language`. Nothing to change. |
| 41 durable jobs/checkpoints | Required for split-process jobs | §6 option 2 depends on it. The API job design must not invent its own persistence. |
| 44 event bus / notification center | Live job progress | A future SSE endpoint should consume Step 44's bus, not a parallel one. |
| 57 / 43 confirm-and-review | Future write endpoints | Use `action_tiers.py`. See §7. |
| 79 (start.bat) | Launcher | Not touched here, to avoid a conflict. The API launcher flag comes later. |
| 80 installer design | New components | FastAPI/uvicorn are core (D3). End users must receive a **prebuilt** `frontend/dist`, never need Node. Node is a developer dependency only. |
| 81 dead-code sweep | New packages | Runs against `baihe-subtitler`, which doesn't contain these files. If this branch ever merges, `api/`, `services/`, `frontend/` are reachable via `python -m api`/npm and are not dead code. |
| 13–21 (Streamlit IA redesign, mostly merged) | Streamlit investment | Unaffected. Future React screens should reuse those decisions (stage tabs, project header state model in `ui/project_state.py`) rather than re-derive them. |
| 18c install buttons | New deps | `fastapi`/`uvicorn` are `required` tier (core, D3), so they aren't offered as optional installs. `httpx` is `dev` tier, like `pytest`. |

---

### 10. Migration phases after this one (to be ordered by evidence, not assumed)

> **Correction (2026-09-29).** Superseded by the phase table in Part 1
> (phases 0-10 plus the React slices). Kept as the original six-item sketch.

1. **Foundation (this branch).** API, React, contract, one real feature, Streamlit unchanged.
2. **Service extraction**, one area at a time, each Streamlit tab switched
   to call the new service: Library (bulk actions, stats, series view) →
   Diagnostics (dependency status) → Settings (a server-side settings and
   secrets service) → Jobs (status/list/cancel) → Sources → Workspace
   stages last.
3. **API expansion** over those services, with an in-process API host
   (§6 option 1) before any job endpoint.
4. **React screens** replacing individual Streamlit screens, starting where
   M8-A's mobile testing shows the most pain.
5. **Job UX:** start/monitor/cancel from React, live progress over SSE,
   durable across restarts once Step 41 lands.
6. **Streamlit retirement**, only after each required screen has been
   migrated and validated.

### 11. Deliberately not done yet

> **Correction (2026-09-29).** Now built: write endpoints, job endpoints
> (list, get, cancel), settings writes (non-secret only), and most Workspace
> stages on the API side. Still not done: authentication, serving
> `frontend/dist` from FastAPI, a combined launcher, porting `page_server`,
> OpenAPI-generated TS types, API-key writes, SSE, and any Streamlit
> retirement. Redis/Celery and Docker remain out. The original list follows.

Write endpoints. Job endpoints. Authentication. Serving `frontend/dist`
from FastAPI. A combined launcher. Porting `page_server`. OpenAPI-generated
TS types. Settings/API-key handling in the API. Any Workspace migration.
Redis/Celery or any external queue. Docker. (FastAPI **is** now a core
dependency, per decision D3, 2026-09-28. CI installs it plus `httpx`, and
a separate `frontend` CI job runs build, unit tests, lint and the
Playwright e2e.)

---

### 12. Proposed roadmap changes (for the planning session to apply)

`docs/baihe-roadmap.md` lives on `claude/baihe-subtitle-planning-95qyvq` and
is owned by the planning session. **Nothing below was committed there.**
The A-level edits were applied to a scratch copy only, and
`scripts/roadmap_sync_check.py` passed on both the original and the edited
copy: 173 step ids in all three structures, 173 `### Step` headers before
and after. The edits add status notes and delete nothing.

#### A: definitely stale (the migration makes the text wrong as written)

**A1. Decisions table, "Later backend/UI stack" row.**
Old: `| Later backend/UI stack | **SQLite + FastAPI + React**, deferred to M8+. Streamlit stays until then. |`
New: the same text, then *" (2026-09-27: at the user's explicit request, M8-B foundation work (FastAPI + React alongside Streamlit, one read-only Library feature) started on the unmerged branch `migration/react-fastapi-foundation`; Streamlit remains the only shipped frontend. See that branch's `docs/migration-react-fastapi.md`.)"*
Why: "deferred" is no longer the whole truth. The foundation exists. Streamlit staying is still true, so it is kept.

**A2. §0 "Deliberately not committed to yet", FastAPI bullet.**
Old: `this is the existing **M8+** deferred milestone (§3), unchanged:`
New: `this is the existing **M8+** deferred milestone (§3), unchanged *(except that M8-B foundation work has since started on an unmerged branch at the user's request — see §3's M8+ notes)*:`
Why: "unchanged" is now inaccurate. The rest of the bullet (the extension analysis) still holds.

**A3. §3 deferred table, M8+ row, "Why it's deferred" cell.**
Old: `A large migration with no current pain driving it.`
New: the same text, then *" (M8-B foundation started 2026-09-27 by explicit user decision, not by this row's trigger firing; unmerged branch `migration/react-fastapi-foundation`; the rest of M8+ stays deferred.)"*
Why: it records that work started on a user decision, not the M8-A trigger, and keeps the trigger text as history.

**A4. §3 M8+ follow-up round, the "M8+ decomposed further" bullet.**
Old: `M8-B (React/FastAPI proper, only if M8-A finds a genuine, unfixable limitation)`
New: the same text, then *" (superseded 2026-09-27: the user chose to start M8-B's foundation ahead of M8-A, on an unmerged branch; M8-A is still worth running before any Streamlit screen is actually retired)"*
Why: the sequencing statement is now factually superseded. M8-A's value is preserved, not deleted.

#### B: potentially affected (still valid; flagged for the planning session to decide)

- **B1. M8-C tier 1 ("cross-session job state… already solved").** True
  within one process. The new finding is that a *separately-launched* API
  process can't see Streamlit's jobs or GPU queue (§6). Consider adding a
  qualifier: "…within the one app process; an API process would need to
  run in-process or wait on tier 2."
- **B2. Step 12 item 4 ("Baihe is single-process/local").** Still true on
  `baihe-subtitler`. With a separate API process it would stop being true
  for model caches. It is only relevant once the API runs jobs, and
  in-process hosting keeps it true.
- **B3. M8-G extension.** Resolved analysis still holds. Possible note:
  "page_server stays the extension endpoint; converge onto the FastAPI
  app only after in-process hosting, keeping the token/no-CORS rules."
- **B4. "New backend capabilities should stay UI-agnostic" rule.** Still
  right. If this branch merges, `services/` becomes its concrete home.
  Could be named there then, not now.
- **B5. Step 41 / Step 44 / Step 43 / Step 80.** Each gains an API-facing
  consequence (§9). Nothing in them is wrong. Consider a one-line
  cross-reference in each when it's picked up.
- **B6. M8-H (remote access), per the user's note that this material
  belongs there.** Suggested addition, no decision made: "While
  Streamlit and the FastAPI app coexist, any M8-H option must front
  two local HTTP services (8501 and 8600), or one if the API later
  serves the built React app and absorbs `page_server`. The API binds
  127.0.0.1 by default and has no authentication; LAN exposure is opt-in
  with a warning, matching Step 10e's trust model. The M8-H question
  itself is unchanged."
- **B7. Steps 13–21 (Streamlit IA redesign).** Unaffected as merged work.
  Any not-yet-started Streamlit-only UX investment (Step 19's
  click-through and similar) could be weighed against eventual React
  replacement. That is a prioritization question, not stale text.

#### Considered and deliberately not changed (C)

- Step 10e (LAN URL printing): accurate for Streamlit.
- The CLAUDE.md-mirroring rules (CLI/UI parity etc.): the API is a third
  caller of the same services, and the parity rule extends naturally
  without rewording.
- Steps 87/89/79/74/81 texts: no statement in them is made wrong by this
  branch (§9).
- Outdated line-number references around M8 (e.g. `db.py ~line 872` for
  `delete_drama`, which is now line 1100). The architectural statements are
  still right, so these were left alone per the "don't rewrite a stale
  line number unless it harms review" rule.

---

### 13. Pre-existing things noticed along the way (flagged, not fixed)

> **Correction (2026-09-29).** The schema-migration race below was fixed by
> Slice 6 (#207). The `tabs/library_tab.py` docstring still says
> `tabs/library.py`, so that item still stands.

- **Schema migration race across processes.** `db.init_db`'s
  check-then-`ALTER TABLE` is safe within one process but could raise
  "duplicate column name" if Streamlit and the API start at the same
  moment on an *older* library that still needs migrating. It is only
  reachable once two processes exist, and it's low-probability (only on
  the first start after an upgrade). A `try/except sqlite3.OperationalError`
  around each `ALTER` would close it. Left for a separate decision.
- **`tabs/library_tab.py` docstring** still says `tabs/library.py -- … extracted
  from the former monolithic app.py`. The filename is stale; the file was
  renamed at some point. Cosmetic.


---

## Part 4: How to run

Setup and launch instructions live in [`../README.md`](../README.md); they
are not duplicated here. In short, it covers the Streamlit app, the API
(`python -m api`) and the React dev server. Two facts specific to this
migration: the launcher scripts (`start.bat`, `start.ps1`) do not start the
API or the frontend (verified: neither mentions the API), and the Vite dev
and preview servers proxy `/api` to the API, so the frontend needs no CORS
in normal development.

---

## Part 5: What is next

The full ordered list is in [`baihe-roadmap-master.md`](baihe-roadmap-master.md)
(section 4 to-do queue, section 2 bug tracker, section 5 deferred steps) and
[`migration-handoff.md`](migration-handoff.md). Pointers only:

- **Frontend:** finish slice F2 (Review stage). Then the missing screens
  that have no API yet: Scanlate, Sources, Discover, Live (phases 7-8).
- **Backend gaps the UI will hit:** media playback endpoint with Range
  support; exposing the workspace stage index; SSE/job push; serving a
  prebuilt `frontend/dist` from FastAPI plus a combined launcher story
  (end users must not need Node); server-side typed confirmation for the
  destructive Library actions deferred in E0.
- **Gated:** Slice 24 (API-key writes, needs the D5 policy decision) and
  Slice 34 (needs a real-model check).
- **Deferred / held:** Step 43 soft-delete; Steps 100-105, 40b, 42, 60, 72;
  the fix and cleanup steps 121-132 and the deferred steps 106-119 in the
  roadmap master.
- **Streamlit retirement criteria** (from `migration-frontend-plan.md`,
  gated by `migration-review.md`): for each tab, every action must be
  reachable in React; the embedded invariants in `migration-review.md`
  section 4 need tests (service-level for Class S, React/e2e for Class U);
  the user has run the real TTS, ffmpeg, GPU and paid-key paths; CLI parity
  holds; and a real-device check passes. Proposed order: Diagnostics,
  Library, Settings (after Slice 24), Translate, then Workspace stage by
  stage; Sources, Discover, Live and Scanlate wait for their APIs. Known
  risks: hidden behaviour in `tabs/workspace_tab.py`, and `page_server.py`
  (the extension bridge) is started from Streamlit.

---

## Unverified / to confirm

- Whether the foundation's first merge into `baihe-subtitler` has a PR
  number. The history shows a merge commit, not a numbered PR.
- PR numbers for Slices 2, 3 and 4 (Slice 4's commit has no PR suffix).
- Whether the React Translate stage (#268) exposes Reflect/bulk (Slice 41)
  and whether the Dub stage (#270) exposes the Step 95 BGM option.
- Whether frontend slice F2 is being built: a local branch exists, it is
  not on origin and not merged.
- Whether Slices 24 and 34 have any partial code. Neither appears in the
  merged log, and `migration-handoff.md` lists both as not built.
- The route count (about 115) comes from counting router decorators. The
  count of 28 service modules includes `service_errors.py`.
- Test-suite numbers in `migration-handoff.md` and `baihe-roadmap-master.md`
  are from before the latest merges (#266-#270); a fresh full run is owed.
  `migration-handoff.md` still lists Slices 41 and 45 as not built even
  though both are merged (#266, #267); this document follows the git log.
- Whether the roadmap-side edits proposed in the historical section 12 were
  ever applied on the planning branch.
- `README.md` still describes the API/React work as "migration branch
  only"; another agent is refreshing it.
