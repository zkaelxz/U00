# File organization

Almost every filename in this project is unique, even across folders —
if you're downloading files individually, a file's name tells you
unambiguously where it belongs: anything ending `_tab.py` goes in
`tabs/`, anything starting `test_` goes in `tests/`, everything else
sits at the top level or in one of the subsystem packages below. The
one expected exception is `__init__.py` (a marker in every package --
empty, or a docstring mapping the package's modules; `sources/adapters/`'s
also holds the list of built-in adapters to load).
The other exception to "Python" is `extension/`, which is
browser-side JavaScript loaded by Chrome rather than anything Python
imports, and `frontend/` (experimental migration branch only), a
TypeScript/React app built with npm that talks to `api/` over HTTP.

```
baihe-subtitler/
│
├── app.py                     ← START HERE:  streamlit run app.py
├── __init__.py                   (empty)
├── common.py                     shared imports every tab pulls in
├── cli.py                        headless batch runner
│                                 (experimental: `python -m api` starts the HTTP API, see api/ below)
├── run_tests.py                  test runner wrapper
│
├── requirements-core.txt         minimum to launch + translate text
├── requirements-media.txt        audio/video: align, dub, burn subtitles
├── requirements-optional.txt     per-feature extras
├── requirements.txt              everything, in one shot -- just the three files above combined
├── constraints.txt               upper bounds for packages that have broken this app before
├── start.bat                     one-click Windows launcher
├── start.ps1                     PowerShell version of the launcher (start.bat is primary)
├── start-react.bat               one-command Windows launcher for the React app: builds frontend/dist once,
│                                 runs `python -m api`, opens http://127.0.0.1:8600/ (untested on Windows)
├── make_lock.bat                 snapshots installed package versions to constraints.lock.txt
├── make_shortcut.bat             creates a desktop shortcut to start.bat
├── uninstall.bat                 this app has no registry/Program Files footprint to clean up
├── uninstall_path_cleanup.ps1    optional uninstall.bat add-on: remove ffmpeg/Tesseract PATH entries
├── pytest.ini                    test config
├── .gitignore                    excludes library/ and .env
├── .env.example                  copy to .env for persistent API keys
├── README.md
├── FILE_ORGANIZATION.md          this file
├── CLAUDE.md                     rules for AI sessions working in this repo
│
├── .streamlit/
│   └── config.toml               visual theme
│
├── .github/
│   ├── pull_request_template.md
│   └── workflows/                tests.yml (core-only suite), windows-bootstrap.yml (launcher check)
│
├── .claude/                      session-start hook, settings + project subagents (agents/) for AI coding sessions
│
├── assets/
│   └── app_icon.ico              used by make_shortcut.bat / packaging
│
├── docs/                       (see role tags below: what each doc is for and who keeps it current)
│   ├── README.md                 short navigational index + the roadmap fetch pointer; this
│   │                             tree listing is the detailed per-file map, README.md is the
│   │                             front door — keep both in sync if either changes
│   ├── adding-source.md          how to write a new sources/adapters/*.py adapter, incl. the
│   │                             pre-coding site checklist [reference — read before adding a
│   │                             source; tells the author to update content-sources.md]
│   ├── browser-extension.md      the Translate-the-page-you're-reading feature (Step 34/34b/96):
│   │                             what it does, what was verified against a real site [source of
│   │                             truth for this built, merged feature]
│   ├── content-sources.md        every source the Sources tab can reach, what was actually
│   │                             checked and how [maintained status tracking — updated per
│   │                             adapter, per adding-source.md's own instruction]
│   ├── handoff-browser-extension.md   the browser extension's original pre-build reasoning
│   │                             [superseded handoff — its own banner says the feature is now
│   │                             built as Step 34/34b and points to browser-extension.md;
│   │                             kept only as historical record, not a live plan]
│   ├── known-working-sources.md  quick "can I point the app at this site" status board, a
│   │                             short companion to content-sources.md's full technical detail
│   │                             [maintained status tracking — update a row's status on
│   │                             re-verification rather than trusting an old date]
│   ├── migration-react-fastapi.md   the React + FastAPI migration: phase tables, differences from
│   │                             the original Python-only design, historical foundation write-up
│   │                             [source of truth for migration phases; merged into `baihe-subtitler`]
│   ├── migration-review.md       whole-app migration review: per-tab/stage plan, invariants,
│   │                             sequence, decisions [planning/reference, companion to
│   │                             migration-react-fastapi.md]
│   ├── migration-handoff.md      durable migration status, recipe and queue [live status page]
│   ├── migration-frontend-plan.md   React frontend slice plan [planning/reference]
│   ├── baihe-roadmap-master.md   master index: status snapshot, bug tracker, to-do queue,
│   │                             deferred steps [snapshot; the roadmap on the planning branch
│   │                             stays the source of truth]
│   ├── engineering-standards.md  shared principles: precedence, scope, review policy,
│   │                             verification, git/safety [authoritative; role files link here]
│   ├── testing-and-ci.md         test commands, gotchas, current merge gate, CI-minutes notes
│   ├── remote-access-design.md   M8-H: Tailscale Serve access + Baihe permissions [design
│   │                             proposal, nothing built yet; written for the planning session
│   │                             to fold the decision into the roadmap]
│   ├── migration-screenshots/    before/after screenshots referenced by migration-review.md
│   ├── technical-notes.md        engineering changelog: real bugs found during development, how
│   │                             they were diagnosed and fixed [audit record, append-only;
│   │                             deliberately kept separate from README.md so that stays
│   │                             focused on using the app]
│   ├── windows-installer-design.md   Step 80 Windows installer/uninstaller architecture
│   │                             [design proposal, nothing built yet; written for an
│   │                             implementing session or the user to read before Step 80's
│   │                             build work starts]
│   ├── windows-installer-research-notes.md   follow-up research stress-testing that design's
│   │                             recommendation against prior art [research/reference,
│   │                             discussion only — no roadmap step id, doesn't change the
│   │                             merged design's recommendation]
│   └── ux-click-through-audit.md Step 19's live click-through UX audit of every workflow,
│                                 against the roadmap's own 12-workflow/8-question spec [audit
│                                 record]
│
│   Note: the numbered build-order roadmap (`docs/baihe-roadmap.md`) and its own status table
│   don't live in this repo — they're tracked on the separate planning branch
│   `claude/baihe-subtitle-planning-95qyvq` until the roadmap's own final step copies the file
│   in (see root `CLAUDE.md`, and `docs/README.md`'s own fetch command). A branch,
│   `claude/baihe-subtitler-tracker-gzuzhg`, once carried a copy of `docs/baihe-roadmap.md` and a
│   `docs/README.md` committed straight into this repo — deliberately not merged: the roadmap
│   copy was a stale snapshot (missing several already-merged steps) and duplicating the file
│   here at all is exactly the two-copies-drift the "doesn't live in this repo yet" convention
│   above exists to prevent. `docs/README.md` was instead rewritten from scratch against
│   verified-current state, pointing at the roadmap by fetch command rather than by a copied-in
│   file. If a `docs/README.md` or `docs/baihe-roadmap.md` copy turns up again on another
│   branch, re-verify it the same way before trusting or merging any of it.
│
├── tabs/                      ← UI ONLY. One file per tab, 10 tabs total.
│   ├── __init__.py               (empty, marks the package)
│   ├── library_tab.py            dashboard, filters, backup, storage, quick-filter tags
│   ├── workspace_tab.py           the main pipeline: align → translate → dub → export
│   ├── reader_tab.py             reading, wiki, story tools, line tools
│   ├── scanlate_tab.py           manhua/webtoon typesetting
│   ├── discover_tab.py           title discovery, bulk import, site navigation help
│   ├── sources_tab.py            paste any URL, run it through the sources/ adapter pipeline
│   ├── translate_tab.py          standalone translate tool: paste or upload text, translate it
│   ├── live_tab.py               near-live translation of an ongoing stream
│   ├── settings_tab.py           sidebar: API keys, appearance, defaults
│   └── diagnostics_tab.py        "check my setup"
│
├── sources/                   ← the site-adapter system (roadmap Step 23 and its sub-steps).
│   ├── __init__.py               (package docstring: a map of the modules below)
│   ├── base.py                    the adapter interface every site implements
│   ├── models.py                  shared vocabulary (result/chapter/etc. types) for the system
│   ├── registry.py                which adapters exist and which are switched on
│   ├── pipeline.py                hands fetched content to the rest of the app
│   ├── detect.py                  names what happened when a fetch didn't go as expected
│   ├── ladder.py                  the access-method ladder (try the cheap method, then the next)
│   ├── adaptive.py                the order methods are tried in for a pasted URL, learned over time
│   ├── ai_extract.py              LLM-based extraction, used only as a fallback
│   ├── auth_browser.py            signed-in browser sessions for sites that need a login
│   ├── cache.py                   the configurable raw-content cache
│   ├── chapter_check.py           scheduled checks for new chapters on tracked titles
│   ├── chapter_order.py           sorts chapter lists the way a reader would expect
│   ├── front_door.py              the single "paste any URL" entry point
│   ├── generic_import.py          one-off imports for sites with no dedicated adapter
│   ├── health.py                  per-source health status (🟢/🟡/🔴, last success/failure)
│   ├── http.py                    the one paced HTTP client every adapter shares
│   ├── lzstring.py                LZString decoding (some sites compress embedded JSON with it)
│   ├── mock.py                    offline demo source ("Show the demo source" in the Sources tab)
│   ├── preflight.py               "will this site work?", answered before committing to import
│   ├── profiles.py                per-domain extraction profiles
│   ├── site_terms.py              terms-of-service findings for sites with no adapter
│   ├── store.py                   persistence for the source-adapter system
│   └── adapters/                  one file per supported site (14 sites)
│       ├── __init__.py            BUILTIN: which adapter modules get loaded
│       ├── 52shuku.py, baozimh.py, bilibili.py, bilibili_manga.py, guazimanhua.py,
│       └── kuaikan.py, mangaz.py, manhuagui.py, manhuaku.py, miaoqumh.py, missevan.py,
│           ranobes.py, toonkor.py, xbanxia.py, zerosumonline.py
│
├── ui/                         ← small shared UI building blocks used across tabs (Step 13).
│   ├── __init__.py               (package docstring: a map of the modules below)
│   ├── project_header.py         the compact, always-visible project header
│   ├── project_state.py          the unified project-state model
│   ├── status.py                 the shared background-job status block
│   └── workflow.py               the pipeline-stage stepper
│
├── services/                   ← UI-INDEPENDENT application services (React/FastAPI migration).
│   ├── __init__.py               (empty, marks the package)   Called by Streamlit tabs AND api/ alike;
│   ├── service_errors.py         error types every service raises   never imports streamlit/fastapi.
│   ├── library_service.py        Library list/filter + one drama's details
│   ├── workspace_job_service.py  Workspace/Library's background-job runner functions (Migration
│   │                             Slice 2 -- moved out of tabs/workspace_tab.py and tabs/library_tab.py
│   │                             unchanged; those tabs import them back and call them as before)
│   ├── reader_service.py         Migration Slice 4 -- one page of a drama's Reader HTML, definitions
│   │                             from cache only, never a live/paid lookup or a DB write
│   ├── diagnostics_service.py    Migration Slice 5 -- read-only Diagnostics overview (deps, GPU,
│   │                             versions, running jobs, log tail); no admin action, no network call
│   ├── jobs_service.py           Migration Slice 8 -- read-only, cross-process job list (reads
│   │                             db.job_records, Slice 7's mirror); no cancel (needs its own design)
│   ├── settings_service.py       Migration Slice 10 -- ENV_NAMES + resolve_key/key_status/
│   │                             get_settings_overview + Slice 24 set/clear_engine_key (atomic .env writer); server-side key resolution shared with
│   │                             tabs/settings_tab.py; never returns a key value over an API (D2)
│   ├── translate_service.py      Migration Slices 11+13+17 -- list_engines/list_history
│   │                             (read-only), translate() (Slice 13, server-side key resolution
│   │                             per engine, D2), clear_history() (Slice 17, confirm-gated delete)
│   ├── export_service.py         Migration Slices 12+14+15+18+27 -- get_export_readiness (read-only
│   │                             counts), generate_subtitle_text (Slice 14: SRT/VTT, pure/no disk
│   │                             write), flag_overlapping_lines/flag_dense_lines/
│   │                             run_auto_qc_flagging (Slice 15: field-scoped db.save_lines
│   │                             writes, flag/flag_note only), generate_epub (Slice 18:
│   │                             novel-narration dramas only, needs optional `ebooklib`),
│   │                             generate_ass_text/get_ass_style_options (Slice 27: ASS text,
│   │                             per-request style); audiobook/burned-in-video export stay out of scope
│   ├── diarization_service.py    Migration Slice 16 -- get_diarization_config (read-only:
│   │                             hf_token_configured bool, expected_speakers, audio_available)
│   │                             plus start_diarization_run (a real GPU-touching background job);
│   │                             status polling reuses the existing jobs API, not duplicated here
│   ├── source_service.py         Migration Slice 19 -- get_source_config/update_source_config
│   │                             (language/script/content mode/transcript mode; read-only audio/
│   │                             video/transcript-source presence); audio upload and transcript/
│   │                             novel text stay out of scope, folded into a future
│   │                             transcribe-and-align action slice instead
│   ├── transcribe_service.py     Migration Slice 20 -- get_transcribe_config/update_transcribe_config
│   │                             (Whisper tuning knobs, newly persisted per drama) plus
│   │                             start_transcribe_run: a background job that does the WHOLE
│   │                             pipeline (ASR, alignment, DB write, optional diarization chain-
│   │                             start), unlike Streamlit's render-loop apply step. Slice 21 adds
│   │                             hardsub_ocr transcript_mode (burned-in video captions, via
│   │                             hardsub_ocr.extract_hardsub_subtitles -- no separate alignment
│   │                             step, same as Whisper's own text). chunk_and_tag and qwen3
│   │                             backends still stay out of scope
│   ├── dub_service.py            Migration Slice 25 -- get_dub_config/get_dub_pacing (read-only:
│   │                             engines, per-speaker voices, pacing of the last run; no paths)
│   ├── drama_service.py          Migration Slice 35 -- create_drama (optional series/preset) and
│   │                             update_drama_metadata (whitelisted partial update); Slice 36
│   │                             delete_drama (typed-confirm, refused while a job runs);
│   │                             cover upload and metadata auto-fill stay out of scope
│   ├── translate_run_service.py  Migration Slice 39 -- READ-ONLY per-drama Translate stage:
│   │                             get_translate_config + estimate_translate_cost (advisory cost
│   │                             estimate / cap gating); start-translate job is a later slice
│   ├── characters_service.py     Migration Slice 42 -- per-drama speakers' character/voice config:
│   │                             list/update (None = leave alone, "" = clear), series-character
│   │                             list, clone-engine picklist (Step 26c language rule), voice bank
│   │                             list/apply; no paths returned; ref-audio upload stays out of scope
│   ├── glossary_service.py       Migration Slice 46 -- series glossary terms (ownership-checked
│   │                             CRUD, confirm-gated delete), project/series instructions, and
│   │                             read-only option catalogues; LLM term extraction stays out
│   ├── review_lines_service.py   Migration Slice 47 -- Review stage's READ-ONLY line views: paged/
│   │                             filtered list, search, find-replace preview, coverage, pacing,
│   │                             provenance, original text (by permanent line id; no writes)
│   ├── review_records_service.py Migration Slice 48 -- READ-ONLY Review records: line history,
│   │                             translation versions (list/compare), notes (list/Markdown),
│   │                             stored consistency issues, emotion summary, edit tendencies,
│   │                             TM suggestions; enforces drama ownership itself; no writes/LLM
│   ├── lines_service.py          Migration Slice 43 -- Review per-line WRITES by permanent line id
│   │                             (field-scoped save_lines only): patch with compare-and-set, dismiss
│   │                             flag, find-replace apply, TM accept, note add/delete; ownership-checked
│   ├── artifact_service.py       Migration Slice 28 -- job-output file convention
│   │                             (<drama>/exports/<kind>/<file>), output_path, get_artifact
│   │                             (whitelisted kind, no symlinks, stays inside drama folder)
│   ├── media_upload_service.py   Migration Slice 31 -- audio/video upload into the drama folder
│   │                             (safe stored name, extension whitelist, size cap, temp+atomic rename)
│   ├── narration_service.py      Migration Slice 33 -- get_narration_config/start_narration_run:
│   │                             novel chunk_and_tag as a job-does-everything background job
│   ├── metadata_service.py       Migration Slice 37 -- ffprobe media analysis + metadata auto-fill
│   │                             suggestion/apply (public-host-only URL fetch, whitelisted fields)
│   ├── novel_attach_service.py   Migration Slice 38 -- attach novel text/safe-EPUB text, chapter OCR job
│   ├── review_jobs_service.py    Migration Slice 44 -- Review AI jobs (consistency, emotion,
│   │                             notes, flag, fix-flagged): background jobs that write themselves,
│   │                             field-scoped by line id; reuse workspace_job_service runners
│   ├── line_ai_service.py        Migration Slice 49 -- per-line Improve translation / Why this?
│   │                             (synchronous, read-only suggestions; id-addressed)
│   ├── media_export_service.py   Migration Slices 29+30 -- audiobook (.m4b) and burned-in video
│   │                             export as thread jobs; ffmpeg via fixed arg lists, output via artifact_service
│   └── restructure_service.py    Migration Slice 45 -- add/delete/merge/split lines, re-segmentation
│                                 preview + apply job, version-history restore (snapshot first,
│                                 expected_line_ids 409, running-job refusal, refs follow line ids)
│
├── api/                        ← HTTP API (FastAPI), EXPERIMENTAL. Runs alongside Streamlit, same library/.
│   ├── __init__.py               (empty, marks the package)
│   ├── __main__.py               `python -m api` -- starts uvicorn with BAIHE_API_* settings
│   ├── server.py                 create_app(): routers, error handlers, dev-only CORS
│   ├── api_config.py             BAIHE_API_HOST/PORT/ENV/CORS_ORIGINS/ALLOW_KEY_WRITES/SERVE_FRONTEND
│   ├── static_frontend.py        serves the built React app (frontend/dist) at / on the same origin as /api;
│   │                             no-op (API only) if dist is missing; traversal-safe; tests/test_api_static_frontend.py
│   ├── error_handlers.py         one JSON error shape; no tracebacks/secrets to clients
│   ├── schemas.py                the API contract (Pydantic models, API_VERSION)
│   └── routers/
│       ├── __init__.py
│       ├── system_routes.py      /api/health, /api/meta
│       ├── library_routes.py     /api/library/dramas[/{id}]
│       ├── reader_routes.py      /api/reader/dramas/{id}/page (Migration Slice 4)
│       ├── diagnostics_routes.py /api/diagnostics (Migration Slice 5, read-only)
│       ├── jobs_routes.py        /api/jobs[/{id}] (Migration Slice 8, read-only, no cancel)
│       ├── settings_routes.py    /api/settings (Slices 10, 23, 24: GET overview, POST non-secret bool toggles, write-only key set/clear, off by default)
│       ├── translate_routes.py   /api/translate/engines, /api/translate/history (Migration Slice 11)
│       │                         + POST /api/translate (Migration Slice 13)
│       │                         + DELETE .../history?confirm=true (Migration Slice 17)
│       ├── export_routes.py      /api/export/dramas/{id}/readiness (Migration Slice 12)
│       │                         + .../subtitle (Migration Slice 14, SRT/VTT)
│       │                         + POST .../flag-overlaps, .../flag-dense-lines, .../flag-auto-qc
│       │                         (Migration Slice 15)
│       │                         + .../epub (Migration Slice 18, novel-narration only)
│       │                         + POST .../audiobook, .../burned-video (Migration Slices 29+30)
│       │                         + POST .../ass and GET /ass-style-options (Migration Slice 27)
│       ├── diarization_routes.py /api/diarization/dramas/{id}/config, POST .../run
│       │                         (Migration Slice 16)
│       ├── source_routes.py      /api/source/dramas/{id}/config (GET + POST, Migration Slice 19)
│       ├── transcribe_routes.py  /api/transcribe/dramas/{id}/config (GET + POST), POST .../run
│       │                         (Migration Slice 20)
│       ├── dub_routes.py         /api/dub/dramas/{id}/config, .../pacing (Migration Slice 25, read-only)
│       ├── drama_routes.py       POST /api/dramas (create), POST /api/dramas/{id}/metadata
│       │                         (Migration Slice 35), DELETE /api/dramas/{id} (Slice 36)
│       ├── translate_run_routes.py /api/translate-run/dramas/{id}/config, .../estimate
│       │                         (Migration Slice 39, read-only)
│       ├── characters_routes.py  /api/characters/dramas/{id}[/clone-engines], POST .../character,
│       │                         POST .../voice-bank/apply, /series/{id}/characters, /voice-bank
│       │                         (Migration Slice 42)
│       ├── glossary_routes.py    /api/glossary/dramas/{id}/terms (GET/POST, DELETE .../{term_id}
│       │                         ?confirm=true), .../instructions[/project|/series], /catalogues
│       │                         (Migration Slice 46)
│       ├── review_lines_routes.py /api/review/dramas/{id}/lines, .../search, POST .../find-replace/
│       │                         preview (writes nothing), .../coverage, .../pacing-flags,
│       │                         .../lines/{line_id}/provenance, .../original-text (Migration Slice 47)
│       ├── review_records_routes.py /api/review/dramas/{id}/history[/{hid}], /versions,
│       │                         /versions/compare, /notes, /notes/markdown, /consistency,
│       │                         /emotions, /tendencies, /tm-suggestions (Migration Slice 48,
│       │                         read-only)
│       ├── lines_routes.py       /api/lines/dramas/{id}/lines/{line_id} (POST partial edit, 409 on stale
│       │                         `expected`), .../dismiss-flag, .../accept-tm, find-replace/apply,
│       │                         notes (POST, DELETE .../{note_id}) (Migration Slice 43)
│       ├── artifact_routes.py    GET /api/artifacts/dramas/{id}/{kind}[/info] (Migration Slice 28)
│       ├── media_routes.py       POST /api/media/dramas/{id}/upload (multipart; returns name/size/kind
│       │                         only) (Migration Slice 31)
│       ├── narration_routes.py   /api/narration/dramas/{id}/config, POST .../run (Migration Slice 33)
│       ├── metadata_routes.py    POST /api/metadata/dramas/{id}/analyze-media, .../autofill, .../autofill/apply
│       │                         (Migration Slice 37)
│       ├── novel_routes.py       /api/novel/dramas/{id}/attach-text|attach-epub|ocr-chapter, GET status (Slice 38)
│       ├── review_jobs_routes.py /api/review-jobs/dramas/{id}/consistency|emotion|notes|flag|
│       │                         fix-flagged (POST, start job; Migration Slice 44)
│       ├── line_ai_routes.py     /api/line-ai/dramas/{id}/lines/{lid}/improve|explain (POST; Slice 49)
│       └── restructure_routes.py /api/restructure/dramas/{id}/lines/add|lines/{lid}/delete|merge|
│                                 lines/{lid}/split|resegment(/preview)|history(/{hid}/restore) (Slice 45)
│
├── frontend/                   ← REACT APP (Vite + TypeScript), EXPERIMENTAL. Not a Python package.
│   ├── package.json, vite.config.ts, tsconfig*.json, index.html
│   ├── src/api/                   client.ts (all HTTP) + types.ts (mirrors api/schemas.py)
│   ├── src/components/            LibraryList, DramaDetailPanel
│   ├── e2e/                       Playwright end-to-end test + seeded-API launcher
│   └── playwright.config.ts
│
├── extension/                  ← BROWSER SIDE. Loaded unpacked, not a Python package.
│   ├── manifest.json              MV3; loopback host permission only
│   ├── background.js              service worker: holds the token, calls the app
│   ├── content.js                 injected on a click: reads pages, draws overlays
│   ├── popup.html / popup.js      pick a drama, send, toggle
│   ├── options.html / options.js  paste the token
│   └── verify_end_to_end.py       standalone script that checks the extension ↔ app handshake
│
├── tests/                      ← 100+ test files, 3,000+ test functions. Run: python run_tests.py
│   ├── __init__.py
│   ├── conftest.py                fixtures (isolated temp database, etc.)
│   ├── sources_helpers.py         shared fakes for adapter tests (clock, scripted HTTP, PNGs)
│   ├── manhuagui_fixtures.py      offline stand-ins for manhuagui pages
│   └── test_*.py                  one or more files per module above, named to match
│
└── library/                    ← YOUR DATA. Created automatically. Gitignored.
    ├── library.db                everything: dramas, lines, glossaries, progress
    ├── cedict.txt                Chinese dictionary (downloaded once)
    └── dramas/<id>/              per-drama files
        ├── source.mp3|mp4        original media
        ├── audio.wav             extracted audio (video sources)
        ├── transcript.txt
        ├── novel_reference.txt
        ├── cover.jpg
        ├── dub_track.wav
        ├── dub_clips/            per-line TTS (regenerable — safe to clean)
        ├── voice_refs/           voice-cloning samples (NOT regenerable)
        └── pages/                manhua pages + typeset output
```

## Top-level modules, by subsystem

**App entry & shared infrastructure**
| File | Does |
|---|---|
| `app.py` | Streamlit entry point |
| `common.py` | shared imports every tab pulls in |
| `cli.py` | headless batch runner (kept in parity with the Workspace tab) |
| `run_tests.py` | test runner wrapper |
| `core.py` | timing, alignment, SRT formatting, line merging |
| `db.py` | all database access (plain `sqlite3`, no ORM) |
| `background_jobs.py` | in-memory background-job tracker (thread + dict) |
| `applog.py` | a single rotating log file for the whole app |
| `diagnostics.py` | environment self-check: which optional dependencies/models are available |
| `check_setup.py` | `start.bat`/`start.ps1`'s "print anything missing in plain words" check |
| `portable.py` | lets the whole app folder be copied/moved and still work |
| `ui_theme.py` | design system (CSS, layout primitives) |
| `app_help.py` | "App Assistant": ask "where is X" or "is this a bug" |
| `storage.py` | disk usage, cache cleanup |
| `benchmark.py` | the Benchmark Lab: regression tracking against your own reference cases, across every content type (audio drama, streamer VOD, novel, manhua) |
| `action_tiers.py` | 🟢/🟡/🔴 action-permission-tier classification an AI-driven feature checks before acting |

**ASR / transcription & alignment**
| File | Does |
|---|---|
| `asr_backend.py` | pluggable transcription: Whisper (default) vs Qwen3-ASR |
| `asr_benchmark.py` | Whisper vs Qwen3-ASR/ForcedAligner, one clip at a time |
| `audio_preprocess.py` | optional audio preprocessing before transcription |
| `forced_align.py` | Qwen3-ForcedAligner timing (alternative to `core.py`'s Whisper-diff alignment) |
| `word_align.py` | word-level forced alignment of Whisper's own transcribed text |
| `raw_transcript.py` | the untouched output of each transcription run, kept for reference |
| `resegment.py` | meaning-based subtitle re-segmentation |
| `sensevoice_tags.py` | optional audio-derived emotion and sound-event tags |
| `diarize.py` | who's speaking when (pyannote) |
| `voice_id.py` | recurring-voice suggestions ("SPEAKER_01 sounds like...") |

**Translation & quality**
| File | Does |
|---|---|
| `translate_engines.py` | Claude / DeepSeek / Gemini / DeepL / Google / Ollama / NLLB / LibreTranslate (+ an offline test engine) |
| `translation_guide.py` | style presets, term policies, translation notes |
| `translation_memory.py` | suggests a translation you already approved for an exact/near-identical line (never auto-applied) |
| `auto_qc.py` | flags a translation that drops or invents a number, date, name, amount or unit |
| `emotion.py` | emotional register detection and preservation |
| `bulk_translate.py` | the "Bulk (cheaper, slower)" translation mode |
| `live_translate.py` | near-live translation of an ongoing live stream |

**Dubbing, subtitles & video**
| File | Does |
|---|---|
| `dub.py` | TTS, voice cloning, track mixing |
| `video_export.py` | subtitle burn-in, softsub mux, dub muxing |
| `media_inspect.py` | probes a dropped file (duration/resolution/tracks) and suggests a pipeline, before a drama exists |
| `subtitle_formats.py` | WebVTT and ASS subtitle export, plus format checks |
| `video_download.py` | yt-dlp wrapper: fetch audio/video from a URL |

**OCR & scanlation**
| File | Does |
|---|---|
| `ocr.py` | Tesseract / PaddleOCR / manga-ocr |
| `scanlate.py` | bubble detection, inpainting, panels, webtoon strips |
| `hardsub_ocr.py` | extracts subtitle text burned directly into a video |
| `segment.py` | word segmentation (zh/ja/ko) |
| `dictionary.py` | CC-CEDICT + LLM definitions |
| `reader.py` | builds the interactive reader HTML |

**Story & learning**
| File | Does |
|---|---|
| `universe_wiki.py` | spoiler-bounded encyclopedia |
| `story_context.py` | character lookup, recaps, relationship maps |
| `qa.py` | ask questions about a drama |
| `line_tools.py` | explain / alternatives / improve / pronounce |
| `debug_view.py` | "what happened here?" per-line/per-job debugging view, bug record-and-replay |
| `adaptive_style.py` | learns your preferences from your edits |
| `vocab_export.py` | Anki decks |

**Discovery, sources & I/O**
| File | Does |
|---|---|
| `page_fetch.py` | fetching, JS-shell detection, render fallback |
| `metadata_lookup.py` | extract metadata from a listing page |
| `bulk_import.py` | many titles from one tag/ranking page |
| `title_library.py` | known-titles catalog + seed data |
| `known_sites.py` | directory of official platforms |
| `navigator.py` | translate a foreign site's labels + navigation steps |
| `epub_io.py` | EPUB import/export |
| `export_package.py` | per-drama archive bundle |

The `services/` and `api/` packages and `frontend/` (the React + FastAPI
migration's foundation, experimental, see `docs/migration-react-fastapi.md`)
are listed in the tree above. The `sources/` package (the adapter system proper, one file per supported
site under `sources/adapters/`) and the browser extension bridge
(`page_server.py` plus everything in `extension/`) are broken out in the
tree above rather than repeated here, since each is really its own
subsystem rather than a handful of top-level modules.

**Shared UI components (`ui/`)**
| File | Does |
|---|---|
| `ui/project_header.py` | the compact, always-visible project header |
| `ui/project_state.py` | the unified project-state model |
| `ui/status.py` | the shared background-job status block |
| `ui/workflow.py` | the pipeline-stage stepper |

## Rules of thumb

- **UI code goes in `tabs/`**, named `*_tab.py`. Logic lives at the top
  level (or in `sources/`/`ui/`) so it stays testable without Streamlit.
- **Logic a second UI will need goes in `services/`** (React/FastAPI
  migration): a plain function the Streamlit tab and an `api/routers/*_routes.py`
  file both call. Streamlit never calls the API over HTTP.
- **Nothing writes outside `library/`** except exports you explicitly download.
- **Optional dependencies are imported inside functions**, never at module
  top level — a missing package disables its own feature instead of
  stopping the app from starting.
- **`library/` is yours.** Back it up. It's gitignored for a reason.
- **Adding a new top-level module or `tabs/*.py` file? Update this file
  in the same step/PR.** See `CLAUDE.md`'s "Rules learned from real
  bugs" section — this drifted badly once already, which is why that
  rule exists.
