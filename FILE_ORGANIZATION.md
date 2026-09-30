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
│   ├── design/                   ui-refresh-spec.md (React visual direction + per-page changes and
│   │                             rollout) and screens/{before,after}/ PNGs [spec — follow-up UI tasks]
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
│   ├── library_admin_service.py  E0 destructive/admin Library actions (router: library_admin_routes.py): bulk status/
│   │                             tags/delete, bulk translate start, export-zip and backup jobs,
│   │                             restore (validated first), storage scan/cleanup; typed confirms,
│   │                             running-job refusal, per-drama results, never returns paths
│   ├── workspace_job_service.py  Workspace/Library's background-job runner functions (Migration
│   │                             Slice 2 -- moved out of tabs/workspace_tab.py and tabs/library_tab.py
│   │                             unchanged; those tabs import them back and call them as before)
│   ├── workflow_service.py       Streamlit retirement M0a -- compute_workspace_stage_index (the
│   │                             pipeline-stage index, Step 19 invariant), moved out of workspace_tab
│   │                             + stage_statuses_from_index (from ui/workflow.py); get_drama_progress
│   │                             (per-stage state + counts for the API)
│   ├── scanlate_service.py       add_uploaded_pages -- save uploaded images/PDFs as a drama's next
│   │                             Scanlate pages (moved from tabs/scanlate_tab.py; Streamlit upload; API callers must pass
│   │                             client filename as .name + a synchronous read()/getbuffer(), and a future
│   │                             route must enforce a png/jpg/jpeg/pdf allowlist and a size cap)
│   ├── reader_service.py         Migration Slice 4 -- one page of a drama's Reader HTML, definitions
│   │                             from cache only, never a live/paid lookup or a DB write
│   ├── diagnostics_service.py    Migration Slice 5 -- read-only Diagnostics overview (deps, GPU,
│   │                             versions, running jobs, log tail); no admin action, no network call
│   ├── extension_service.py      API batch 1 -- browser-extension bridge (page_server) status, on/off
│   │                             (persists page_server_enabled) and token reveal; for local_only routes
│   ├── notification_service.py   Step 44 -- Discord webhook / ntfy push when a background job ends
│   │                             (hooked from background_jobs._notify_job_finished): URLs kept in .env like
│   │                             keys, SSRF-checked and pinned, burst-collapsed + per-minute cap, never raises
│   ├── jellyfin_service.py       Step 39 -- optional Jellyfin connector (off by default): settings (key in .env),
│   │                             test connection, read-only scan for items missing a subtitle language, send
│   │                             subtitles (+ optional video) into the library folder in Jellyfin's naming, refresh
│   │                             keys, SSRF-checked and pinned, burst-collapsed + per-minute cap, never raises;
│   │                             also the in-app list for the header bell (last 50 events, memory only,
│   │                             filtered by job visibility) and the jobs/new-chapters push categories
│   ├── diagnostics_gaps_service.py  M1 (Streamlit retirement) -- setup checks, model versions and cache,
│   │                             pyannote readiness, job history, support report, log tail; confirm-gated
│   │                             install/upgrade/reset wrappers (router: diagnostics_gaps_routes.py)
│   ├── benchmark_lab_service.py  Step 38 -- Benchmark Lab: golden-set tiers (public/application/regression),
│   │                             JSONL/TSV import, persistent per-run records (benchmark_sessions/results),
│   │                             Model Arena compare, CER/WER for ASR/OCR, cost estimate + monthly cap

│   ├── diagnostics_installs_service.py  Q02/Q06 -- Deno install (winget, or the official release zip
│   │                             from a static table: allowlisted https hops, timeouts, byte cap,
│   │                             .sha256sum check) and "Test first" for an update target, both as
│   │                             background jobs (deno_install, upgrade_check) behind the install guard
│   ├── voice_bank_audio_service.py L19 -- a voice-bank entry's clip for streaming: audio types only,
│   │                             must resolve inside the voice-bank folder, symlinks refused
│   ├── jobs_service.py           Migration Slice 8 -- read-only, cross-process job list (reads
│   │                             db.job_records, Slice 7's mirror); no cancel (needs its own design)
│   ├── settings_service.py       Migration Slice 10 -- ENV_NAMES + resolve_key/key_status/
│   │                             get_settings_overview + Slice 24 set/clear_engine_key (atomic .env writer); server-side key resolution shared with
│   │                             tabs/settings_tab.py; never returns a key value over an API (D2)
│   ├── engine_routing_service.py Step 36 -- capability-based task routing: resolve_capability("translation.cheap"|
│   │                             "translation.high_quality"|"llm.instructions"|"summary.episode"|"research.grounded_search")
│   │                             -> the engine chosen in Settings (else a default; never switches on its own);
│   │                             per-engine status + one-call Test (router: engine_routing_routes.py)
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
│   │                             per-request style); audiobook/burned-in-video export live in media_export_service (Slices 29-30)
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
│   │                             step, same as Whisper's own text). chunk_and_tag lives in narration_service
│   │                             (Slice 33); qwen3 backends still stay out of scope
│   │                             start_retranscribe_line/get_retranscribe_result/apply_retranscribe_line:
│   │                             one line's window re-run through Whisper; the job only proposes
│   │                             (text read back via GET, never in job records), apply writes that
│   │                             line's zh by id with compare-and-set (parity B1, R23)
│   ├── dub_service.py            Migration Slice 25 -- get_dub_config/get_dub_pacing (read-only:
│   │                             engines, per-speaker voices, pacing of the last run; no paths)
│   ├── drama_service.py          Migration Slice 35 -- create_drama (optional series/preset) and
│   │                             update_drama_metadata (whitelisted partial update); Slice 36
│   │                             delete_drama (typed-confirm, refused while a job runs);
│   │                             cover upload stays out of scope (auto-fill is metadata_service, Slice 37)
│   ├── translate_run_service.py  Migration Slice 39 -- READ-ONLY per-drama Translate stage:
│   │                             get_translate_config + estimate_translate_cost (advisory cost
│   │                             estimate / cap gating); start-translate job is a later slice;
│   │                             parity X02/X22: apply_workflow_tier, save_translate_preset
│   ├── characters_service.py     Migration Slice 42 -- per-drama speakers' character/voice config:
│   │                             list/update (None = leave alone, "" = clear), series-character
│   │                             list, clone-engine picklist (Step 26c language rule), voice bank
│   │                             list/apply; no paths returned; ref-audio upload stays out of scope
│   ├── glossary_service.py       Migration Slice 46 -- series glossary terms (ownership-checked
│   │                             CRUD, confirm-gated delete), project/series instructions, and
│   │                             read-only option catalogues; glossary proposals from the novel
│   │                             or (parity X10) the source lines, as jobs; apply by term text
│   │                             with optional per-term edits
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
│   ├── url_media_service.py      Workspace "From a URL" -- yt-dlp download job urlmedia_ (public-URL check,
│   │                             size/time/live/playlist caps, no cookies, temp dir, field-scoped write)
│   ├── media_playback_service.py Migration Slice 52 -- contained path lookup for audio/video playback
│   ├── comic_view_service.py     comic viewer: page list, contained page-image lookup (magic-byte type,
│   │                             no symlinks, 50 MB cap, no PIL), visible text regions, page progress
│   ├── narration_service.py      Migration Slice 33 -- get_narration_config/start_narration_run:
│   │                             novel chunk_and_tag as a job-does-everything background job
│   ├── metadata_service.py       Migration Slice 37 -- ffprobe media analysis + metadata auto-fill
│   │                             suggestion/apply (public-host-only URL fetch, whitelisted fields),
│   │                             romanize credits (writes only the *_romanized fields)
│   ├── metadata_research_service.py  Step 37 -- "Research online": Gemini Google Search grounding,
│   │                             per-field cited sources, entity-keyed cache, daily free-search budget,
│   │                             Keep/Replace/Save-both apply with per-field provenance
│   ├── cover_art_service.py      Drama cover art (P14): checked PNG/JPEG/WebP upload re-encoded without
│   │                             metadata, stored as cover.<ext>; resolves the file to serve
│   ├── discover_catalog_service.py Migration Slice 55 -- Discover known-titles catalog (no network/LLM)
│   ├── delete_service.py         PC-only deletes (handoff queue item 2): remove audio/video, raw novel
│   │                             text; delete version, series character, bug bundle, preset, voice bank
│   ├── url_guard.py              B-25 -- shared public-address check (http(s), every resolved IP global) for services and sources/http
│   ├── safe_fetch.py             Migration Slice 54 -- shared static-only public page text fetch
│   │                             (wraps metadata_service SSRF checks; hop/byte caps, needs_manual, no browser)
│   ├── live_service.py           Live capture L-1 -- per-session start/stop/poll over live_translate (per-session
│   │                             temp dir, use_gpu, max_minutes stop, redacted cues); router: live_routes.py
│   ├── sources_search_service.py Sources S-3 -- search and series jobs with error mapping, scrubbed
│   │                             results, known-chapter helper (router: sources_search_routes.py)
│   ├── sources_import_service.py Sources S-4 -- chapter import into an existing drama by chapter id
│   │                             (per-drama sourceimport_ job, idempotent via store.imported_chapters);
│   │                             S-5 novel text from a pasted URL
│   ├── sources_url_service.py    Sources S-5 -- pasted-URL public check and the paste-a-URL preview job
│   ├── sources_tools_service.py  Sources SO02/SO03/SO08/SO16 -- site check job, pasted page source preview and
│   │                              import, identify-media job (+ PC-only full resource URL), pasted-URL diagnostics
│   ├── sources_tracking_service.py Sources S-7 -- "Check now" (the sources_chapter_check job the scheduler
│   │                             also uses) and which drama a tracked series auto-imports into
│   ├── sources_signin_service.py Sources S-6/SO17 (PC only) -- sign-in window job, forget the saved profile,
│   │                             per-tier "Test now" job; page URL must be public and on the source's site
│   ├── discover_lookup_service.py    Discover D-2 -- query translation, baihehub search, import suggestion,
│   │                              bulk extract/commit, navigation help (safe_fetch only; router: discover_lookup_routes.py)
│   ├── novel_attach_service.py   Migration Slice 38 -- attach novel text/safe-EPUB text (optional chapter
│   │                             range), chapters imported in Sources as narration text, chapter OCR job
│   ├── lncrawl_service.py        Step 115b -- optional "Import with lightnovel-crawler": finds the
│   │                             user-installed GPL-3.0 `lncrawl` program (PATH or Settings lncrawl_cmd;
│   │                             never imported), runs it as a separate process (fixed argv, timeout,
│   │                             cancel, output/size caps, redacted tail) and attaches its EPUB through
│   │                             novel_attach_service (router: novel_routes.py, local_only)
│   ├── review_jobs_service.py    Migration Slice 44 -- Review AI jobs (consistency, emotion,
│   │                             notes, flag, fix-flagged): background jobs that write themselves,
│   │                             field-scoped by line id; reuse workspace_job_service runners
│   ├── line_ai_service.py        Migration Slice 50 -- per-line Improve translation / Why this?
│   │                             (synchronous, read-only suggestions; id-addressed); also
│   │                             Alternatives / Grammar (review parity R17/R18)
│   ├── line_tools_service.py     Review parity R19/R28 -- Pronounce (bounded edge-tts MP3 of a line's
│   │                             source) and auto-shorten overlong lines (snapshot, then `en`-only
│   │                             compare-and-set per line)
│   ├── translation_version_service.py  Review parity R39 -- make a saved translation version the current
│   │                             English (snapshot, then `en`-only write by line id; refuses restructured lines)
│   ├── blocked_retry_service.py  Review parity R10 -- retry one content-blocked line with another engine
│   │                             (synchronous, id-keyed result, compare-and-set write of en/flag/flag_note)
│   ├── series_people_service.py  Parity X15-X17 -- add a series person and edit name/pronouns/aliases/
│   │                             notes by id (field-scoped UPDATE; taken name = 409)
│   ├── media_export_service.py   Migration Slices 29+30 -- audiobook (.m4b) and burned-in video
│   │                             export as thread jobs; ffmpeg via fixed arg lists, output via artifact_service
│   ├── restructure_service.py    Migration Slice 45 -- add/delete/merge/split lines, re-segmentation
│   │                             preview + apply job, version-history restore (snapshot first,
│   │                             expected_line_ids 409, running-job refusal, refs follow line ids)
│   ├── review_extras_service.py  Review AI extras (R46/R37/R35/R03) -- auto-merge short lines (read-only
│   │                             preview; apply re-checks ids + groups, snapshot first), learn my style +
│   │                             apply toggle/reset, SenseVoice tag job + side-by-side rows, burned
│   │                             preview clip job (capped, fixed file name in the drama folder)
│   ├── auth_service.py           Step 133 -- users allowlist, permission catalogue (deny by default),
│   │                             hashed server-side sessions + CSRF, audit log, login rate limiter
│   ├── oidc_service.py           Step 134 (A1) -- Google sign-in: PKCE/state/nonce single-use login
│   │                             transactions, id_token check against Google's JWKS (Authlib), user
│   │                             resolution by sub then first-login email binding; tests/test_auth_login.py
│   ├── ownership_service.py      Auth slice B1 -- drama/series visibility (owner, private flag,
│   │                             admin/local owner see all; denied = 404), share-by-default setting
│   ├── sources_registry_service.py Migration Slice 56 -- Sources catalog/status (list, detail,
│   │                             attempts, settings, profiles, tracked, notifications) and config
│   │                             writes; URLs reduced to scheme+host+path, text scrubbed, proxy = bool
│   ├── voice_clone_service.py    Voice-clone setup (parity blocker #7; C01/C03/C09/C13) -- reference
│   │                             clip upload/remove (ffprobe-checked), extract candidates per speaker
│   │                             (job voiceref_<id>, files only), choose, save to voice bank, series link
│   ├── bug_report_service.py     "Report a problem" reports stored as files in <library>/bug_reports/
│   │                             <UTC stamp>_<n>/ (report.json, report.md, screenshot); every text redacted
│   │                             (secrets, tokens, user names, paths), image metadata stripped
│   │                             (router: bug_report_routes.py)
│   └── novel_files_service.py    Parity B1 #3/#4 -- set/replace/status of the English novel reference
│                                 (novel_reference.txt) and raw novel (raw_novel_context.txt); reference
│                                 removal; upload or pasted text; encoding fallback; 409 while a drama job or
│                                 (raw novel) any Sources import runs (router: novel_files_routes.py)
│
├── api/                        ← HTTP API (FastAPI), EXPERIMENTAL. Runs alongside Streamlit, same library/.
│   ├── __init__.py               (empty, marks the package)
│   ├── __main__.py               `python -m api` -- starts uvicorn with BAIHE_API_* settings;
│   │                             `grant-admin` / `add-user` / `deactivate` / `grant` / `list-users` (local user admin)
│   ├── server.py                 create_app(): routers, error handlers, dev-only CORS
│   ├── api_config.py             BAIHE_API_HOST/PORT/ENV/CORS_ORIGINS/ALLOW_KEY_WRITES/SERVE_FRONTEND/AUTH/COOKIE_SECURE/BACKGROUND,
│   │                             BAIHE_GOOGLE_CLIENT_ID/SECRET + BAIHE_PUBLIC_URL (sign-in; also read from .env)
│   ├── background.py             startup hook (lifespan): chapter-check scheduler + extension endpoint (if enabled);
│   │                             off in tests (BAIHE_API_BACKGROUND=0); tests/test_api_background.py
│   ├── static_frontend.py        serves the built React app (frontend/dist) at / on the same origin as /api;
│   │                             no-op (API only) if dist is missing; traversal-safe; tests/test_api_static_frontend.py
│   ├── auth.py                   Step 133 -- require_permission/public_route/local_only/authenticated (one per route,
│   │                             tests/test_api_permissions.py), __Host- session + CSRF cookies (csrf_failed code),
│   │                             client_ip (rightmost X-Forwarded-For behind a loopback proxy), EarlyAuthGate (auth on),
│   │                             LoopbackOnlyGate (auth off: direct loopback requests only),
│   │                             LocalOnlyCrossSiteGate (local_only routes refuse cross-site requests; #372)
│   ├── llm_slots.py              shared cap for synchronous LLM/ffmpeg work in a request (2 server-wide,
│   │                             1 per caller, 429 when busy): Reader LLM routes and the blocked-line retry
│   ├── error_handlers.py         one JSON error shape; no tracebacks/secrets to clients
│   ├── schemas.py                the API contract (Pydantic models, API_VERSION)
│   ├── comic_schemas.py          comic viewer request/response models (kept apart from schemas.py)
│   ├── engine_routing_schemas.py Step 36 "Which engine does what" request/response models
│   ├── metadata_research_schemas.py  grounded research models (Step 37; kept apart from schemas.py)
│   ├── jellyfin_schemas.py       Jellyfin connector models (Step 39; kept apart from schemas.py)
│   ├── notification_schemas.py   Step 44 notification categories + in-app list models (apart from schemas.py)
│   ├── benchmark_schemas.py      Benchmark Lab request/response models (Step 38; kept apart from schemas.py)

│   ├── diagnostics_install_schemas.py Deno install / Test first models (kept apart from schemas.py)
│   ├── sources_tools_schemas.py  Sources tools + Discover pasted listing models (kept apart from schemas.py)
│   └── routers/
│       ├── __init__.py
│       ├── system_routes.py      /api/health, /api/meta (incl. `local`: viewer is at the PC)
│       ├── auth_routes.py        /api/auth/login, /callback, /logout, /me -- Google sign-in (step 134, A1);
│       │                         404 with auth off except /me (the local owner); tests/test_auth_login.py
│       ├── library_routes.py     /api/library/dramas[/{id}]
│       ├── library_admin_routes.py /api/library/admin/* (route batch 2A): bulk status/tags/delete/
│       │                         translate, export + backup jobs, artifacts[/info] download, restore
│       │                         (multipart), storage scan/clean; tests/test_api_library_admin.py
│       ├── reader_routes.py      /api/reader/dramas/{id}/page (Migration Slice 4); overview, progress, notes, media, captions, lookup, vocab + exports, story tools, wiki, ask (route batch 2B, M4)
│       ├── diagnostics_routes.py /api/diagnostics (Migration Slice 5, read-only)
│       ├── jobs_routes.py        /api/jobs[/{id}] (Migration Slice 8), POST /{id}/cancel (#350); records carry a redacted result + outcome (#378)
│       ├── settings_routes.py    /api/settings (Slices 10, 23, 24: GET overview, POST non-secret bool toggles, write-only key set/clear, off by default)
│       ├── engine_routing_routes.py /api/settings/engine-routing (Step 36): GET capabilities + engine status
│       │                         (admin.settings); PC-only POST capabilities/{capability}, engines/{engine}/test
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
│       │                         (Migration Slice 20), .../autotune, POST/GET .../lines/{line_id}/retranscribe, POST .../retranscribe/apply
│       ├── dub_routes.py         /api/dub/dramas/{id}/config, .../pacing (Migration Slice 25, read-only), .../track (Slice 53, WAV download)
│       ├── drama_routes.py       POST /api/dramas (create), POST /api/dramas/{id}/metadata
│       │                         (Migration Slice 35), DELETE /api/dramas/{id} (Slice 36), POST|GET
│       │                         /api/dramas/{id}/cover (upload local_only, read library.read)
│       ├── translate_run_routes.py /api/translate-run/dramas/{id}/config, .../estimate
│       │                         (Migration Slice 39, read-only)
│       ├── characters_routes.py  /api/characters/dramas/{id}[/clone-engines], POST .../character,
│       │                         POST .../voice-bank/apply, /series/{id}/characters, /voice-bank
│       │                         (Migration Slice 42)
│       ├── glossary_routes.py    /api/glossary/dramas/{id}/terms (GET/POST, DELETE .../{term_id}
│       │                         ?confirm=true), .../instructions[/project|/series], /catalogues
│       │                         (Migration Slice 46); .../from-novel[/apply] (batch 2C) and
│       │                         .../from-lines[/apply] (parity X10)
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
│       │                         only) (Migration Slice 31); GET/HEAD .../audio|video Range playback (Slice 52)
│       ├── narration_routes.py   /api/narration/dramas/{id}/config, POST .../run (Migration Slice 33)
│       ├── metadata_routes.py    POST /api/metadata/dramas/{id}/analyze-media, .../autofill, .../autofill/apply
│       │                         (Migration Slice 37), .../romanize-credits (admin.library + engines check)
│       ├── metadata_research_routes.py  GET /api/metadata/research/budget, POST .../dramas/{id}/research,
│       │                         .../research/apply, GET .../provenance (Step 37)
│       ├── novel_routes.py       /api/novel/dramas/{id}/attach-text|attach-epub|attach-from-sources|ocr-chapter,
│       │                         GET status (Slice 38)
│       ├── review_jobs_routes.py /api/review-jobs/dramas/{id}/consistency|emotion|notes|flag|
│       │                         fix-flagged (POST, start job; Migration Slice 44)
│       ├── line_ai_routes.py     /api/line-ai/dramas/{id}/lines/{lid}/improve|explain (POST; Slice 50)
│       ├── translation_version_routes.py /api/review/dramas/{id}/versions/{vid}/activate (POST, lines.edit, confirm=true; R39)
│       ├── blocked_retry_routes.py /api/lines/dramas/{id}/lines/{lid}/retry-blocked (POST, jobs.start + engine gate; R10)
│       ├── series_people_routes.py POST /api/characters/series/{id}/characters[/{cid}] (add / edit, lines.edit; X15-X17)
│       ├── delete_routes.py      POST .../remove|.../delete for the delete_service deletes (local_only, confirm=true)
│       ├── benchmark_routes.py   /api/benchmark/options|cases|sets|runs|runs/{id}|arena (GET) + estimate (POST),
│       │                         admin.diagnostics; cases, import, regression, runs (POST) local_only (Step 38)
│       ├── comic_routes.py       /api/scanlate/dramas/{id}/pages, pages/{pid}/image (GET/HEAD, media.stream),
│       │                         pages/{pid}/regions, progress (GET/POST) -- comic viewer; tests/test_api_comic_viewer.py
│       ├── discover_routes.py    /api/discover/titles (GET/POST), titles/seed|{id}/delete|{id}/import-to-library (POST), platforms, search-links (GET; Slice 55)
│       ├── restructure_routes.py /api/restructure/dramas/{id}/lines/add|lines/{lid}/delete|merge|
│       │                         lines/{lid}/split|resegment(/preview)|history(/{hid}/restore) (Slice 45)
│       ├── review_extras_routes.py /api/review-extras/dramas/{id}/merge-short/preview|apply, style(/learn|
│       │                         /apply|/reset), sensevoice (POST job, GET rows), burn-preview (POST job,
│       │                         /info, /clip Range); tests/test_api_review_extras.py
│       ├── sources_catalog_routes.py /api/sources registry/status GETs + config POSTs (Slice 56; not the Workspace Source stage above)
│       ├── workflow_routes.py    GET /api/workflow/dramas/{id}/progress (stage bar state + counts; API batch 1)
│       ├── live_routes.py        /api/live/sessions (POST start, GET list), /{id} (GET poll), /{id}/stop (spec L-1; API batch 1)
│       ├── discover_lookup_routes.py /api/discover/translate-query|baihehub-search|import-suggestion|bulk-extract[/result]|
│       │                         bulk-commit|navigation-help[/result] (spec D-2; API batch 1)
│       ├── sources_search_routes.py POST /api/sources/search, /api/sources/{name}/series (jobs), GET
│       │                         /api/sources/jobs/{job_id}/result (spec S-3; API batch 1)
│       ├── sources_import_routes.py POST /api/sources/url/preview, /url/import, /{name}/import
│       │                         (sources.import; specs S-4, S-5)
│       ├── sources_tools_routes.py  /api/sources/url/preflight|preview-pasted|import-pasted|identify-media(/resource)|
│       │                            extractions; /api/discover/bulk-extract/pasted (capped pasted bodies, 413)
│       ├── sources_local_routes.py POST /api/sources/settings/proxy, /{name}/signin/open|forget,
│       │                         /{name}/tier-test (all local_only; spec S-6, SO17, SO18)
│       ├── diagnostics_gaps_routes.py /api/diagnostics/setup-checks|model-cache|pyannote|job-history|log|
│       │                         support-report|bug-bundles|install-presets|gpu-torch (GET) and gpu-torch/check,
│       │                         package-updates/check (POST, on click), all admin.diagnostics;
│       │                         dependencies/{pkg}/install|upgrade, gpu-torch/setup, reset-library,
│       │                         model-cache/hf|piper/{name}/delete (POST, local_only + confirm; API batch 1,
│       │                         react-misc-parity)
│       ├── diagnostics_installs_routes.py /api/diagnostics/deno, /upgrade-check (GET, admin.diagnostics);
│       │                         /deno/install, /dependencies/{pkg}/test-upgrade (POST, local_only +
│       │                         confirm; background jobs, polled)
│       ├── voice_bank_audio_routes.py GET/HEAD /api/library/voice-bank/{entry_id}/audio (media.stream)
│       ├── extension_routes.py   /api/extension/status (GET), /enabled, /token (POST; all local_only;
│       │                         token only with confirm=true and Cache-Control: no-store; API batch 1)
│       ├── voice_clone_routes.py /api/characters/dramas/{id}/reference-clip[/remove] (local_only),
│       │                         .../reference-clips/extract|candidates[/{cid}/audio|/choose],
│       │                         .../voice-bank/save (admin.library), .../series-link (voice-clone setup)
│       ├── bug_report_routes.py  /api/diagnostics/bug-reports: POST (library.read, multipart report;
│       │                         screenshot PC only), GET list and GET {id} (admin.diagnostics),
│       │                         POST {id}/delete (local_only + confirm + folder stamp)
│       ├── novel_files_routes.py /api/novel/dramas/{id}/reference (GET/POST, .../text, .../remove) and
│       │                         /raw-novel (GET/POST, .../text); paste bodies streamed with a 32 MB cap
│       ├── notification_routes.py /api/settings/notifications (GET, admin.settings: booleans only); /test,
│       │                         /{channel}, /{channel}/clear (POST, local_only; set/clear also use the
│       │                         key-write gate; Step 44)
│       └── jellyfin_routes.py    /api/jellyfin/config (GET/POST), /key, /key/clear, /test, /scan,
│                                 /dramas/{id}/send -- all local_only (Step 39)
│       │                         /categories, /{channel}, /{channel}/clear (POST, local_only; set/clear
│       │                         also use the key-write gate; Step 44)
│       └── notification_center_routes.py /api/notifications (GET, library.read): the header bell's recent
│                                 job-ended and new-chapter events (Step 44 item 5)
│
├── frontend/                   ← REACT APP (Vite + TypeScript), EXPERIMENTAL. Not a Python package.
│   ├── package.json, vite.config.ts, tsconfig*.json, index.html
│   ├── src/api/                   client.ts (all HTTP) + types.ts (mirrors api/schemas.py); one <area>.ts per
│   │                              API area, e.g. review.ts, restructure.ts (add/delete/merge/split/re-segment/
│   │                              restore), media.ts (Range stream URLs), libraryAdmin.ts (Library admin +
│   │                              preset/voice-bank deletes), pcOnly.ts (PC-only mode store + pcOnlyFetch:
│   │                              X-Baihe-Local header, 403 -> remote); types in src/types/<area>.ts
│   ├── src/components/            LibraryList (+ libraryFilters.ts: the More filters, pure), DramaDetailPanel, Section, Field, ErrorBanner, Sheet (<dialog>;
│   │                              bottom sheet on phones), TypedConfirm (type-a-word destructive confirm),
│   │                              ConfirmButton (two-step delete), VoiceBankPlayButton (Play/Stop one
│   │                              voice-bank clip; Library, Characters, Voices), errorMessages.ts (error copy per code),
│   │                              ErrorBoundary (page crash fallback, resets on route change) +
│   │                              errorFallbackText.ts; src/bootFallback.ts (last-resort message in #root
│   │                              when React never mounts; index.html also holds a static no-JS note).
│   │                              src/labels.ts: display labels for status, media type, language and engine
│   │                              codes (unknown codes title-cased; one source of truth; unit-tested).
│   │                              Design kit (docs/design/ui-refresh-spec.md): Toggle (role=switch), Button
│   │                              (ButtonLink), Badge (pill), Card, components/labels.ts (humanize via src/labels.ts, status
│   │                              tones), uiClasses.ts (buttonClass/badgeClass); tokens in src/index.css
│   ├── scripts/design-screens.mjs  dark desktop/phone screenshots of the main screens (docs/design/screens/)
│   ├── src/report/                "Report a problem": capture.ts (ring buffers of console errors, window
│   │                              errors, failed API calls (method/path/status/code only) and route history;
│   │                              installed in main.tsx), ReportProblem.tsx (header button + dialog),
│   │                              reportDialogStore.ts (openReportDialog()), reportBundle.ts (pure: report,
│   │                              markdown, GitHub issue link); API in src/api/bugReports.ts
│   ├── public/                    favicon.ico (copy of assets/app_icon.ico), icon-32/192.png
│   ├── src/hooks/                 useJob, useMediaQuery, useShortcut (list keyboard shortcuts),
│   │                              usePersistedState (per-viewer prefs in localStorage),
│   │                              usePcOnly ('local'|'remote'|'unknown' from /api/meta `local`)
│   ├── src/pages/libraryAdmin/    Library admin: SelectionBar (bulk status/list/translate/export/delete),
│   │                              AdminSection (Backup & storage), AdminJobLine, useAdminJob,
│   │                              libraryAdmin.ts (pure, unit-tested)
│   ├── src/pages/diagnostics/     Diagnostics: SetupSection (Setup card, OK/Problem badges), PackagesSection (PC-only
│   │                              Install…/Update to X…, synchronous; installed versions, "Check for
│   │                              updates" + packageUpdates.ts; "Install by task" presets, approx.
│   │                              sizes, PyPI Source links; GpuTorchPanel + gpuTorch.ts: GPU/driver,
│   │                              installed torch family, matched-set setup), PyannoteSection, ModelCacheSection,
│   │                              JobHistorySection, LogSection (+ CopyBlock), SupportReportSection
│   │                              ("Copy a report for a bug" card: copy, download .txt, preview rows
│   │                              via supportReport.ts, pure, unit-tested),
│   │                              BugBundlesSection (saved bug bundles, PC-only delete),
│   │                              DangerZone (typed-RESET library reset), diagnosticsAdmin.ts (pure,
│   │                              unit-tested, + useDetailsOpen), installPresets.ts (pure task/size
│   │                              helpers, unit-tested), DenoInstall (Setup card: Install Deno job +
│   │                              denoInstallText.ts), UpgradeTest ("Test first" result + upgradeTestText.ts),
│   │                              useServerJobStatus + jobPoll.ts (poll a server job's status),
│   │                              diagnostics.css; API in src/api/diagnostics.ts and
│   │                              src/api/diagnosticsInstalls.ts (types/diagnosticsInstalls.ts)
│   ├── src/pages/settings/        ExtensionSection (Settings > Browser extension: on/off, show token;
│   │                              the token lives in component state only); API in src/api/extension.ts.
│   │                              NotificationsSection + notifications.ts (Settings > Notifications, Step 44:
│   │                              Discord/ntfy set/clear/send test, PC only, configured yes/no only); API in
│   │                              src/api/notifications.ts. PreferencesSections + preferences.ts (Settings >
│   │                              Appearance, Defaults for new dramas, Spending, OCR, Offline and performance,
│   │                              Downloads, Server addresses; persisted PC-side, PC only); API in
│   │                              src/api/settings.ts. src/theme.ts: light/dark/system theme (localStorage,
│   │                              <html data-theme>, applied in main.tsx). ApiKeysCard (Settings > API
│   │                              keys: one Set/Missing row per engine, SettingsKeyForm opens in place);
│   │                              settings.css (the page's Card stack and status rows)
│   ├── src/pages/workspace/stages/review/  Review editor: LinesPanel (active line, edit mode, structure
│   │                              edits), LineRow, ReviewToolbar, Player, LineActionsSheet (+ SplitDialog,
│   │                              MergeConfirm, AddLineForm), StructureSection, ShortcutSheet, RecordsPanel,
│   │                              reviewLogic.ts (pure, unit-tested); AI results and checks: ReviewFindings
│   │                              (consistency, emotion), ReviewChecks (coverage/pacing, tendencies, version
│   │                              compare, notes Markdown link), LineOrigin (per-line provenance + original
│   │                              text) with RetranscribeLine (one-line re-transcribe job,
│   │                              retranscribeLogic.ts), FindingList, reviewResults.ts (pure, unit-tested);
│   │                              AiExtras (+ AiExtrasMerge, AiExtrasStyle, AiExtrasSenseVoice,
│   │                              AiExtrasBurnPreview, aiExtrasLogic.ts pure): auto-merge short lines, learn my
│   │                              style, SenseVoice tags, burned preview clip; API in src/api/reviewExtras.ts;
│   │                              LineTools (alternatives, grammar, pronounce), ShortenOverlong (pacing
│   │                              auto-shorten), tmDismiss.ts (per-session TM dismissals)
│   ├── src/pages/Reader.tsx       Reader page (#/read/<id>[?page=N]) over /api/reader: page HTML in a sandboxed
│   │                              iframe, pager, resume, Watch / listen; api/reader.ts, types/reader.ts
│   ├── src/pages/reader/          ReaderPrefs (Aa popover/sheet), ReaderWords (Words, Vocabulary, Glossary),
│   │                              ReaderStory (story tools, wiki, Q&A), ReaderEngine, ReaderAction +
│   │                              useReaderAction (per-action error/429 retry), readerPrefsStore.ts and
│   │                              readerErrors.ts (pure, unit-tested), reader.css
│   ├── src/pages/Sources.tsx      Sources page (#/sources): search the enabled sources and open a series (paced
│   │                              jobs), New chapters, PC-only Source settings; api/sources.ts, types/sources.ts
│   ├── src/pages/Live.tsx         Live page (#/live): paste a stream link, start a live capture session, poll
│   │                              its transcript + translation, stop; api/live.ts (client + pure helpers,
│   │                              unit-tested), types/live.ts, pages/live.css; e2e/live*.spec.ts + liveMocks.ts
│   ├── src/pages/sources/         FindModeSwitch (search | link), SearchPanel, SeriesPanel, NewChapters (Check now, auto-import drama),
│   │                              SourceSettings, SourceDetail, SourceAccess (sign-in, per-tier tests),
│   │                              PacingForm, ProxyForm, useSourcesJob (job-result polling + reattach),
│   │                              sourcesFormat.ts (pure, unit-tested), sources.css; Sources tools:
│   │                              SiteCheck, PastedSource, IdentifyMedia, RecentExtractions,
│   │                              sourcesToolsFormat.ts, sources-tools.css (api/sourcesTools.ts, types/sourcesTools.ts)
│   ├── src/pages/Discover.tsx     Discover page (#/discover): one AI-engine picker, the known-titles catalogue
│   │                              (search, filters, add to Library, PC-only remove), platform search links,
│   │                              baihehub search, navigation helper, add a title (from a URL or by hand),
│   │                              bulk import; api/discover.ts, types/discover.ts
│   ├── src/pages/discover/        CatalogPanel, FindPanel (+ PlatformList), BaihehubPanel, NavigationHelp,
│   │                              AddTitle, BulkImport (+ PastedListing), ExternalLink (http(s)-only links), useDiscoverJob
│   │                              (fixed-id job polling via pollSourcesJob), discoverFormat.ts (pure,
│   │                              unit-tested), discover.css
│   ├── src/pages/workspace/stages/  also AutoTune (Transcribe > Advanced), NovelGlossary (GlossaryExtract:
│   │                              Glossary > From novel / From lines, and the novel one on Source),
│   │                              GlossaryProposals (editable proposal table/cards), GlossaryReview
│   │                              (Translate: review glossary before translating), useGlossaryRun
│   │                              (shared run state across mounts), glossaryExtract.ts (pure,
│   │                              unit-tested; types in src/types/glossaryHelpers.ts), SeriesCast (Characters > Series cast: list, add, inline edit of
│   │                              name/pronouns/aliases/notes, bulk pronouns, PC-only remove; seriesPeopleForm.ts pure,
│   │                              unit-tested), SeriesAssign (series picker + "Create series" in the
│   │                              glossary box), transcribeEstimate.ts (pure, unit-tested: Transcribe /
│   │                              Detect speakers time captions), useRunStatus (per-drama run
│   │                              polling), autotuneGlossary.ts (pure, unit-tested); API in
│   │                              src/api/autotuneGlossary.ts + src/api/stageDeletes.ts (PC-only deletes via pcOnlyFetch)
│   │                              + src/api/seriesPeople.ts (add/edit series people)
│   │                              VoiceClonePanel (Dub > Voices and cloning: clip upload/extract/pick, voice
│   │                              bank, voice actor, series link, clone warnings) + voiceClone.ts (pure,
│   │                              unit-tested); API in src/api/voiceClone.ts, types in src/types/voiceClone.ts
│   │                              NovelFilePanel (novel reference in Translate, raw novel in Transcribe;
│   │                              PC-only upload or paste, remove) + novelFile.ts + novelFileEvents.ts (shared
│   │                              "changed" counter NovelPanel's glossary link reads); src/api/novelFiles.ts,
│   │                              types/novelFiles.ts
│   │                              CreditsCoverPanel (Source > Credits & cover: bilingual credits, Romanize,
│   │                              PC-only cover upload) + ../preambleForm.ts (pure, unit-tested; also the
│   │                              EPUB chapter range NovelPanel uses) + preamble.css
│   │                              LncrawlPanel (Source > Novel text > Import with lightnovel-crawler; shown
│   │                              only when lncrawl is installed and on the PC) + lncrawlForm.ts (pure,
│   │                              unit-tested)
│   │                              VoiceSuggestions (Characters > "sounds like X": accept/reject) + characters.css;
│   │                              CharactersPanel's sample lines, custom pronouns and "Remember in this series"
│   │                              use characterForm.ts (pure, unit-tested); API in src/api/characters.ts,
│   │                              types in src/types/characters.ts
│   │                              stageBlockers.ts (pure, unit-tested: why Translate/Export can't run yet,
│   │                              shown under the disabled primary with a one-tap fix)
│   ├── e2e/                       Playwright end-to-end test + seeded-API launcher
│   │                              (stageLineMocks.ts: give the 0-line seeded dramas a line count)
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
| `benchmark.py` | the case runners the Benchmark Lab (services/benchmark_lab_service.py) builds on: regression tracking against your own reference cases, across every content type (audio drama, streamer VOD, novel, manhua) |
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
