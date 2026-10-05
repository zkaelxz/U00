# File organization

Almost every filename in this project is unique, even across folders —
if you're downloading files individually, a file's name tells you
unambiguously where it belongs: anything starting `test_` goes in `tests/`, everything else
sits at the top level or in one of the subsystem packages below. The
one expected exception is `__init__.py` (a marker in every package --
empty, or a docstring mapping the package's modules; `sources/adapters/`'s
also holds the list of built-in adapters to load).
The other exception to "Python" is `extension/`, which is
browser-side JavaScript loaded by Chrome rather than anything Python
imports, and `frontend/`, a
TypeScript/React app built with npm that talks to `api/` over HTTP
(`python -m api` serves its built `frontend/dist`).

```
baihe-subtitler/
│
├── __init__.py                   (empty)
├── cli.py                        headless batch runner
│                                 START HERE: `python -m api` (api/server.py) runs the app, see api/ below
├── run_tests.py                  test runner wrapper
│
├── requirements-core.txt         minimum to launch + translate text
├── requirements-media.txt        audio/video: align, dub, burn subtitles
├── requirements-optional.txt     per-feature extras
├── requirements.txt              everything, in one shot -- just the three files above combined
├── constraints.txt               upper bounds for packages that have broken this app before
├── start.bat                     one-click Windows launcher: runs `python -m api`, opens http://127.0.0.1:8600/
├── start.ps1                     PowerShell version of the launcher (start.bat is primary)
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
├── .github/
│   ├── pull_request_template.md
│   └── workflows/                tests.yml (core-only suite), dependency-canary.yml (weekly unpinned install), windows-bootstrap.yml (launcher check),
│                                 windows-installer.yml (on demand / installer-v* tags: builds the
│                                 Setup .exe and smoke-tests a silent install + uninstall)
│
├── .claude/                      session-start hook, settings + project subagents (agents/) for AI coding sessions
│
├── assets/
│   └── app_icon.ico              used by make_shortcut.bat / packaging
│
├── installer/                    Windows installer (Step 80b; docs/windows-installer-design.md)
│   ├── baihe.iss                 Inno Setup 6 script: per-user install, data-folder page,
│   │                             shortcuts, uninstaller (user data kept unless a box is ticked)
│   ├── build_installer.py        build-time: stages the app + frontend/dist (no .env/library/
│   │                             tests), pinned embeddable Python, core wheels, WinSW, manifest; runs ISCC
│   ├── caddy/                    go.mod, go.sum, main.go: the bundled Caddy (stock + rate_limit),
│   │                             built by build_installer.py and pinned by SHA-256 (LF line endings)
│   ├── launcher.py               runtime (ships as app\installer\): the Start-menu shortcut --
│   │                             starts `python -m api` on loopback, opens the window; --stop
│   ├── postinstall.py            runtime: writes app\INSTALLED (the data folder), bootstraps
│   │                             pip from its wheel, installs requirements-core offline
│   ├── service_menu.ps1          runtime (ships with service.py): the "Baihe Studio service" Start-menu console menu over
│   │                             service.py's commands; asks for administrator rights once
│   ├── wheels.lock.txt           the pinned, hashed wheel list the installer build downloads (docs/runbook.md, "Refresh the installer lock")
│   ├── service.py                runtime (elevated; ships in the payload's service\helper\, not app\): the
│   │                             BaiheStudio boot service (WinSW, virtual account, 127.0.0.1:8600 only); BaiheCaddy (off
│   │                             until the owner runs enable-remote; never adds a firewall rule)
│   ├── licenses/WinSW-LICENSE.txt   MIT licence shipped with the WinSW wrapper
│   └── smoke_child.py            CI only (not shipped): a stand-in child process for the
│                                 workflow's "Stop ends every child" check
├── deploy/caddy/Caddyfile.template   Caddy config template for household access: TLS, proxy to the
│                                 household listener only, PC-only routes refused (docs/household-access.md;
│                                 checked by tests/test_caddyfile_template.py)
├── scripts/
│   ├── build_release.py          packages the built React app as a release zip (baihe-frontend-<version>.zip)
│   ├── check_constraints.py      fails when requirements, constraints.txt and installer/wheels.lock.txt disagree
│   │                             (run by tests.yml; docs/testing-and-ci.md)
│   ├── dependency_canary.py      tests one package upgrade in a throwaway venv against the offline suite;
│   │                             --write-pin caps constraints.txt on FAIL (docs/testing-and-ci.md)
│   ├── smoke_pack.py             run on your PC (GPU, real models): checks the transcription pipeline on your own clip
│   │                             against an approved baseline before/after an upgrade (docs/testing-and-ci.md)
│   ├── source_status.py          regenerates the board in docs/known-working-sources.md from the adapter registry +
│   │                             docs/source-status.json; --check exits 1 if the doc is stale
│   ├── source_probe.py           manual reachability probe (one GET per source host, 2 s apart); prints a table or
│   │                             --json, never edits files, off in CI (docs/runbook.md)
│   └── migration/                keepboth.py, resolve_slice.py -- merge-conflict helpers for migration slices
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
│   ├── known-working-sources.md  quick "can I point the app at this site" status board, a
│   │                             short companion to content-sources.md's full technical detail
│   │                             [generated by scripts/source_status.py from source-status.json —
│   │                             edit the data file, not the tables]
│   ├── source-status.json        per-source status, last_verified, reason, notes (the board's data)
│   ├── RELEASE.md                how the Windows installer and the frontend release zip are built and published [reference]
│   ├── asr-experiments.md        Qwen3-ASR batching and MOSS-Transcribe-Diarize (both off by default): what is built, what
│   │                             was compared [reference]
│   ├── remote-access-decision.md remote-access design as decided and built: listeners, sign-in, permissions, ownership, and the
│   │                             route table that tests/test_api_permissions.py enforces [source of truth]
│   ├── react-ui-guidelines.md    concise-UI rules for the React app and the per-screen change list written against the old
│   │                             Streamlit UI [design record; its Streamlit comparisons are historical]
│   ├── sources-credential-audit.md  audit of how the source adapters handle credentials and cookies (Step 110; guard:
│   │                             tests/test_sources_credential_audit.py) [audit record]
│   ├── specs/                    earlier proposals for Discover/Sources/Live, Scanlate, Step 141 (PC shell) and the Workspace/
│   │                             Review UX; written against the Streamlit tabs [proposals, partly historical]
│   ├── STATUS.md                 current state, in-flight PRs, what's next [live status; each
│   │                             session replaces its own entry]
│   ├── archive/                  historical records, not sources of truth: migration review,
│   │                             handoff and phase log, old roadmap master tracker, Streamlit test
│   │                             triage, superseded remote-access/extension handoffs, installer
│   │                             research notes, Step 19 click-through audit, unbuilt Jellyfin/Plex metadata design
│   ├── engineering-standards.md  shared principles: precedence, scope, review policy,
│   │                             verification, git/safety [authoritative; role files link here]
│   ├── testing-and-ci.md         test commands, gotchas, current merge gate, CI-minutes notes
│   ├── runbook.md                one-page maintainer steps: installer lock, tests, restore, certificate, benchmark [reference]
│   ├── household-access.md       step-by-step guide to expose Baihe to the household through Caddy:
│   │                             user-only vs Claude steps, checks, rollback [reference]
│   ├── technical-notes.md        engineering changelog: real bugs found during development, how
│   │                             they were diagnosed and fixed [audit record, append-only;
│   │                             deliberately kept separate from README.md so that stays
│   │                             focused on using the app]
│   └── windows-installer-design.md   Windows installer/uninstaller: Step 80's design, updated
│                                 for React + FastAPI and built in Step 80b (installer/)
│                                 [design + as-built reference; the research notes are in archive/]
│
│   Note: the numbered build-order roadmap (`docs/baihe-roadmap.md`) and its own status table
│   don't live in this repo — they're tracked on the separate planning branch
│   `claude/baihe-subtitle-planning-95qyvq` until the roadmap's own final step copies the file
│   in (see `docs/README.md`). A branch,
│   `claude/baihe-subtitler-tracker-gzuzhg`, once carried a copy of `docs/baihe-roadmap.md` and a
│   `docs/README.md` committed straight into this repo — deliberately not merged: the roadmap
│   copy was a stale snapshot (missing several already-merged steps) and duplicating the file
│   here at all is exactly the two-copies-drift the "doesn't live in this repo yet" convention
│   above exists to prevent. `docs/README.md` was instead rewritten from scratch against
│   verified-current state, pointing at the roadmap by fetch command rather than by a copied-in
│   file. If a `docs/README.md` or `docs/baihe-roadmap.md` copy turns up again on another
│   branch, re-verify it the same way before trusting or merging any of it.
│
├── sources/                   ← the site-adapter system (roadmap Step 23 and its sub-steps).
│   ├── __init__.py               (package docstring: a map of the modules below)
│   ├── base.py                    the adapter interface every site implements
│   ├── models.py                  shared vocabulary (result/chapter/etc. types) for the system
│   ├── registry.py                which adapters exist and which are switched on
│   ├── pipeline.py                hands fetched content to the rest of the app
│   ├── detect.py                  names what happened when a fetch didn't go as expected
│   ├── domains.py                 domain lists for sites that move: ordered failover, last good domain,
│   │                              a redirected-to host that passes verify_site becomes a pending proposal
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
│   └── adapters/                  one file per supported site (19 sites)
│       ├── __init__.py            BUILTIN: which adapter modules get loaded
│       └── 52shuku.py, baozimh.py, bilibili.py, bilibili_manga.py, fanjiao.py, guazimanhua.py,
│           kuaikan.py, lightnovel_fun.py, mangak.py, manhuagui.py, manhuaku.py, miaoqumh.py,
│           missevan.py, piaotian.py, ranobes.py, syosetu.py, toonkor.py, xbanxia.py, zerosumonline.py
│
├── services/                   ← UI-INDEPENDENT application services (React/FastAPI migration).
│   ├── __init__.py               (empty, marks the package)   Called by api/ and cli.py alike;
│   ├── service_errors.py         error types every service raises   never imports fastapi.
│   ├── library_service.py        Library list/filter + one drama's details
│   ├── library_admin_service.py  E0 destructive/admin Library actions (router: library_admin_routes.py): bulk status/
│   │                             tags/delete, bulk translate start, export-zip and backup jobs,
│   │                             restore (validated first), storage scan/cleanup; typed confirms,
│   │                             running-job refusal, per-drama results, never returns paths
│   ├── auto_backup_service.py    Step 43 (redefined 2026-09-29): opt-in automatic backup writing a
│   │                             new dated copy each run (temp file, validated, fsynced, renamed in)
│   │                             and rotating old ones (one per day for the last 2 days + the first of
│   │                             each of the last 2 weeks), due-check (startup + hourly via
│   │                             api/background.py), restore one drama from a chosen copy (same id,
│   │                             or a new "(restored <date>)" copy); router: backup_routes.py
│   ├── disk_usage_service.py     Disk usage: bounded scan of the data folder (relative paths, links never
│   │                             followed), move-to-Trash clear, Trash restore / permanent delete and automatic-backup folder move with
│   │                             server-enforced protected paths; router: disk_usage_routes.py
│   ├── backup_import_service.py  Step 143: import chosen dramas from an uploaded backup file (snapshot
│   │                             copy, manual backup zip or library.db) as new dramas owned by the acting
│   │                             user; reuses auto_backup_service._copy_drama; router: backup_routes.py
│   ├── workspace_job_service.py  Workspace/Library's background-job runner functions (Migration
│   │                             Slice 2)
│   ├── workflow_service.py       compute_workspace_stage_index (the
│   │                             pipeline-stage index, Step 19 invariant)
│   │                             + get_drama_progress
│   │                             (per-stage state + counts for the API)
│   ├── reader_service.py         Migration Slice 4 -- one page of a drama's Reader HTML, definitions
│   │                             from cache only, never a live/paid lookup or a DB write
│   ├── diagnostics_service.py    Migration Slice 5 -- read-only Diagnostics overview (deps, GPU,
│   │                             versions, running jobs, log tail); no admin action, no network call
│   ├── extension_service.py      API batch 1 -- browser-extension bridge (page_server) status, on/off
│   │                             (persists page_server_enabled) and token reveal; for local_only routes
│   ├── notification_service.py   Step 44 -- Discord webhook / ntfy push when a background job ends
│   │                             (hooked from background_jobs._notify_job_finished): URLs kept in .env like
│   │                             keys, SSRF-checked and pinned, burst-collapsed + per-minute cap, never raises
│   ├── remote_health_service.py  remote-access health: Caddy's certificate expiry (local TLS read), optional
│   │                             public-name vs public-IP check, household listener up; scheduled by
│   │                             api/background.py only while remote access is on; alerts once per change;
│   │                             the check address is set from Settings (PC only, .env, never returned)
│   ├── jellyfin_service.py       Step 39 -- optional Jellyfin connector (off by default): settings (key in .env),
│   │                             test connection, read-only scan for items missing a subtitle language, send
│   │                             subtitles (+ optional video) into the library folder in Jellyfin's naming, refresh
│   │                             keys, SSRF-checked and pinned, burst-collapsed + per-minute cap, never raises;
│   │                             also the in-app list for the header bell (last 50 events, memory only,
│   │                             filtered by job visibility) and the jobs/new-chapters push categories
│   ├── notion_service.py         roadmap 112 -- Notion export (companion to the Reader's Anki export): target
│   │                             database/page in app_settings, token in .env (write-only), test connection,
│   │                             export job notion_export_<id> that creates the drama's page once and then
│   │                             updates only Baihe's own properties and "Baihe transcript" block in place
│   │                             (page id in dramas.notion_page_id); fixed host, throttled, chunked, 429 back-off
│   ├── asr_options_service.py    Steps 103/104 -- experimental transcription settings: Qwen3-ASR batch size, MOSS backend toggle
│   ├── web_search_service.py     item 114 -- optional web-search fallback (off by default): the user's own SearXNG
│   │                             (base URL in app_settings), links only (never fetches a result), capped, no redirects
│   ├── diagnostics_gaps_service.py  M1 -- setup checks, model versions and cache,
│   │                             pyannote readiness, job history, support report, log tail; confirm-gated
│   │                             install/upgrade/reset wrappers (router: diagnostics_gaps_routes.py)
│   ├── job_checkpoint_service.py Step 41 -- per-unit checkpoints so a re-run resumes an interrupted
│   │                             job (narration tagging uses it) + an opt-in result cache keyed on
│   │                             (kind, input hash, model, settings) (glossary-from-novel uses it)
│   ├── job_timing_service.py     Step 41 -- per-stage duration + estimated spend of every real job
│   │                             (background_jobs starts/finishes a run; jobs call mark_stage)
│   ├── line_provenance_service.py Step 41 -- per-line engine/model/prompt/glossary/software version
│   │                             of the latest translation (recorded by the translate job)
│   ├── vram_service.py           Step 41 -- free-VRAM fit check before a GPU model load (dub loaders)
│   ├── benchmark_lab_service.py  Step 38 -- Benchmark Lab: golden-set tiers (public/application/regression),
│   │                             JSONL/TSV import, persistent per-run records (benchmark_sessions/results),
│   │                             Model Arena compare, CER/WER for ASR/OCR (jiwer when installed, else built-in;
│   │                             scorer recorded per result), cost estimate + monthly cap
│   ├── model_registry_service.py Step 40 -- model deprecation assistant: configured models vs the shipped
│   │                             registry (model_registry.json) and a manual, cached provider model-list
│   │                             check; user-confirmed preset model switch (never automatic)
│   ├── model_registry.json       Step 40 -- sourced lifecycle facts (current/legacy/deprecated/retired)
│   ├── model_reeval_service.py   Step 40b -- scheduled model re-evaluation: user-added candidates vs the
│   │                             production model through the Benchmark Lab, report, recorded decisions,
│   │                             explicit promotion only (scheduler: api/background.py)
│   ├── diagnostics_installs_service.py  Q02/Q06 -- Deno install (winget, or the official release zip
│   │                             from a static table: allowlisted https hops, timeouts, byte cap,
│   │                             .sha256sum check) and "Test first" for an update target, both as
│   │                             background jobs (deno_install, upgrade_check) behind the install guard
│   ├── voice_bank_audio_service.py L19 -- a voice-bank entry's clip for streaming: audio types only,
│   │                             must resolve inside the voice-bank folder, symlinks refused
│   ├── maintenance_assistant_service.py  Step 42 -- in-app AI maintenance assistant, read-only v1: a fixed
│   │                             table of read-only tools (list/read/search code, git status/log/diff, redacted
│   │                             log, job history, support report, dependency/model checks, one test file),
│   │                             a TOOL-line chat loop over qa._dispatch_chat, proposed fixes returned as
│   │                             patch text only, Developer Mode, backlog, changelog (router: assistant_routes.py)
│   ├── assistant_pytest_guard.py  pytest plugin for the assistant's run_tests: throwaway library, empty .env
│   ├── assistant_github_service.py  Step 72 -- deliver an assistant proposed fix as a draft GitHub PR: off by
│   │                             default, token in .env (never returned), strict pure-Python patch apply,
│   │                             new baihe-assistant/ branch only (router: assistant_github_routes.py)
│   ├── assistant_roles_service.py  Step 60 -- the assistant's implement -> independent review roles: reviewer
│   │                             prompt, verdict parsing, cross-provider check (off by default; same read-only tools)
│   ├── jobs_service.py           Migration Slice 8 -- read-only, cross-process job list (reads
│   │                             db.job_records, Slice 7's mirror); also cancels, deletes and clears finished jobs
│   ├── shutdown_service.py       Step 80b -- the API's clean stop: stops schedulers and new browsers,
│   │                             cancels this process's jobs, stops page_server; the launcher's token-gated
│   │                             POST /api/system/shutdown, a closed console window and Ctrl+C run it
│   ├── update_service.py         app updates from the public GitHub Releases (BAIHE_UPDATE_REPO): check (no token),
│   │                             download + SHA-256 check into library/updates, start the verified Setup on a click
│   │                             (Windows, installed copy); optional daily check, off by default (router: update_routes.py)
│   ├── event_stream_service.py   SSE push broker behind GET /api/events: background_jobs/notification_service
│   │                             hooks name what changed, each stream re-reads it through the GET routes'
│   │                             service calls with its own principal; stream caps, bounded pending set -> resync,
│   │                             job_records sweep for other processes' jobs
│   ├── settings_service.py       Migration Slice 10 -- ENV_NAMES + resolve_key/key_status/
│   │                             get_settings_overview + Slice 24 set/clear_engine_key (atomic .env writer); server-side key resolution;
│   │                             never returns a key value over an API (D2)
│   ├── engine_routing_service.py Step 36 -- capability-based task routing: resolve_capability("translation.cheap"|
│   │                             "translation.high_quality"|"llm.instructions"|"summary.episode"|"research.grounded_search")
│   │                             -> the engine chosen in Settings (else a default; never switches on its own);
│   │                             per-engine status + one-call Test (router: engine_routing_routes.py)
│   ├── stronger_engine_service.py Step 99 -- suggest (never switch to) the "translation.high_quality" engine for a
│   │                             hard line (QC flag, glossary conflict, ambiguous term); single-line try that
│   │                             returns text only, cap-checked, spend logged (router: stronger_engine_routes.py)
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
│   │                             start), Slice 21 adds
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
│   ├── translate_run_service.py  Migration Slice 39 -- per-drama Translate stage:
│   │                             get_translate_config + estimate_translate_cost (advisory cost
│   │                             estimate / cap gating); start_translate_run starts the job;
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
│   ├── glossary_retranslate_service.py Lines a glossary change affects (term/alias in the
│   │                             source, or a banned translation in the English), with a
│   │                             hand-edited flag from line provenance; re-translates only the
│   │                             chosen ones through the normal translate job (stale preview 409)
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
│   ├── scanlate_pages_service.py Scanlate S1/S2: panel config, page detail by stable region id, run notes,
│   │                             add_page_images (upload + link imports; the import limits live here), the
│   │                             scanlate_<id> job id, upload claim, shared pipeline lock, note cleaner
│   ├── scanlate_run_service.py   Scanlate S5: detect + OCR + id-keyed translate + render as one job per drama
│   │                             (modes missing/page/all), conditional per-page writes, predecessor context
│   ├── scanlate_render_service.py Scanlate S6/S8: typeset one page from DB regions; render and ZIP/PDF export jobs
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
│   │                             text; delete version, series character, preset, voice bank
│   ├── url_guard.py              B-25 -- shared public-address check (http(s), every resolved IP global) for services and sources/http
│   ├── capped_body.py            shared byte-capped, deadline-capped read of a streamed HTTP body (closes the response)
│   ├── safe_fetch.py             Migration Slice 54 -- shared static-only public page text fetch
│   │                             (wraps metadata_service SSRF checks; hop/byte caps, needs_manual, no browser)
│   ├── live_service.py           Live capture L-1 -- per-session start/stop/poll over live_translate (per-session
│   │                             temp dir, use_gpu, max_minutes stop, redacted cues); router: live_routes.py
│   ├── sources_search_service.py Sources S-3 -- search and series jobs with error mapping, scrubbed
│   │                             results, known-chapter helper (router: sources_search_routes.py)
│   ├── sources_import_service.py Sources S-4 -- chapter import into an existing drama by chapter id
│   │                             (per-drama sourceimport_ job, idempotent via store.imported_chapters);
│   │                             S-5 novel text and SO06 comic pages from a pasted URL
│   ├── sources_save_service.py   Saving a comic series' chapters as CBZ files (default <data dir>/saved_comics,
│   │                             or a PC-chosen folder); Open folder; auto-save from the chapter check
│   ├── saved_comics_service.py   Reading saved CBZ chapters in the app: series, chapters, pages, page images
│   ├── sources_url_service.py    Sources S-5 -- pasted-URL public check and the paste-a-URL preview job
│   ├── sources_tools_service.py  Sources SO02/SO03/SO08/SO16 -- site check job, pasted page source preview and
│   │                              import, identify-media job (+ PC-only full resource URL), pasted-URL diagnostics
│   ├── page_import_limits.py     the comic page-upload rules (types, per-image bytes/pixels, per-import files/bytes,
│   │                             strip slicing, EXIF orientation); used by the SO06 import, later the Scanlate upload
│   ├── sources_extraction_service.py Sources parity SO09/SO10 -- the pasted-URL AI fallback engine (opt-in, key
│   │                             on the PC) and Review extraction (per-drama in-memory review, corrections, profile save)
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
│   ├── source_domains_service.py  source domain lists (read/edit/reset), confirm or dismiss proposed
│   │                             hosts (host names only), one assistant backlog item per unreachable source
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
├── api/                        ← HTTP API (FastAPI). Serves the React app, same library/.
│   ├── __init__.py               (empty, marks the package)
│   ├── __main__.py               `python -m api` -- starts uvicorn with BAIHE_API_* settings (plus the household
│   │                             listener on BAIHE_API_HOUSEHOLD_PORT, same process, when set);
│   │                             `grant-admin` / `add-user` / `deactivate` / `revoke-admin` / `grant` / `list-users` (local user admin)
│   ├── server.py                 create_app(): routers, error handlers, dev-only CORS; listener="household" (D5)
│   ├── api_config.py             BAIHE_API_HOST/PORT/ENV/CORS_ORIGINS/ALLOW_KEY_WRITES/SERVE_FRONTEND/AUTH/COOKIE_SECURE/BACKGROUND,
│   │                             HOUSEHOLD_PORT (household_settings, check_household_bind_safety),
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
│   ├── schemas/                  the API contract (Pydantic models, API_VERSION), one module per domain;
│   │                             domain modules import only from common
│   │   ├── __init__.py     re-exports every module, so `from api.schemas import X` works
│   │   ├── common.py       error envelope, API_VERSION and models shared by several domains
│   │   ├── system.py       health, settings, diagnostics/setup, jobs, updates, notifications, extension, ports, bug reports
│   │   ├── review.py       line views and edits, records/versions, restructure and resegment, Review AI extras
│   │   ├── characters.py   characters, glossary and series-person models
│   │   ├── translate.py    standalone translator, translate runs, workflow tiers, presets, bulk jobs
│   │   ├── library.py      dramas, series, bulk actions, storage, media and export
│   │   ├── sources.py      sources registry/config, Discover, tracked series
│   │   ├── reader.py       Reader, novel text and novel file models
│   │   ├── voice.py        dubbing, narration and voice-clone setup
│   │   └── transcribe.py   transcribe runs/config, diarization, autotune, re-transcribe, live sessions
│   ├── comic_schemas.py          comic viewer request/response models (kept apart from schemas.py)
│   ├── scanlate_schemas.py       automatic Scanlate request/response models (kept apart from schemas.py)
│   ├── job_stage_schemas.py      Step 41 per-stage job timing models (kept apart from schemas.py)
│   ├── disk_usage_schemas.py     Disk usage scan / Trash / move models (relative paths only)
│   ├── backup_schemas.py         automatic backup / snapshot restore models (kept apart from schemas.py)
│   ├── sources_import_schemas.py import-state models (Step 107; kept apart from schemas.py)
│   ├── engine_routing_schemas.py Step 36 "Which engine does what" request/response models
│   ├── stronger_engine_schemas.py Step 99 stronger-engine suggestion models
│   ├── metadata_research_schemas.py  grounded research models (Step 37; kept apart from schemas.py)
│   ├── jellyfin_schemas.py       Jellyfin connector models (Step 39; kept apart from schemas.py)
│   ├── notion_schemas.py         Notion export models (roadmap 112; kept apart from schemas.py)
│   ├── web_search_schemas.py     web-search fallback models (item 114; kept apart from schemas.py)
│   ├── sharing_schemas.py        Sharing models: item list, private flag, share-by-default
│   ├── notification_schemas.py   Step 44 notification categories + in-app list models (apart from schemas.py)
│   ├── benchmark_schemas.py      Benchmark Lab request/response models (Step 38; kept apart from schemas.py)
│   ├── model_registry_schemas.py Step 40 model status / preset switch models (kept apart from schemas.py)
│   ├── model_reeval_schemas.py   Step 40b re-evaluation models (kept apart from schemas.py)
│   ├── diagnostics_install_schemas.py Deno install / Test first models (kept apart from schemas.py)
│   ├── sources_tools_schemas.py  Sources tools + Discover pasted listing models (kept apart from schemas.py)
│   ├── assistant_schemas.py      maintenance assistant request/response models (kept apart from schemas.py)
│   ├── admin_users_schemas.py    user administration + audit log view models (kept apart from schemas.py)
│   ├── asr_options_schemas.py    experimental transcription settings models (kept apart from schemas.py)
│   ├── sources_extraction_schemas.py pasted-URL extraction and review models (SO09/SO06/SO10; kept apart from schemas.py)
│   ├── saved_comics_schemas.py   saved-manga folder and reader models (kept apart from schemas.py)
│   └── routers/
│       ├── __init__.py
│       ├── system_routes.py      /api/health, /api/meta (incl. `local`: viewer is at the PC)
│       ├── update_routes.py      /api/system/update[/check|/settings|/download|/install]: all local_only;
│       │                         tests/test_api_update.py
│       ├── auth_routes.py        /api/auth/login, /callback, /logout, /me -- Google sign-in (step 134, A1);
│       │                         404 with auth off except /me (the local owner); tests/test_auth_login.py
│       ├── admin_users_routes.py /api/admin/users (list, deactivate, activate, revoke-sessions, revoke-admin) and
│       │                         /api/admin/audit (read-only, paged); reads admin.users.read, writes admin.users; tests/test_api_admin_users.py
│       ├── library_routes.py     /api/library/dramas[/{id}]
│       ├── library_admin_routes.py /api/library/admin/* (route batch 2A): bulk status/tags/delete/
│       │                         translate, export + backup jobs, artifacts[/info] download, restore
│       │                         (multipart), storage scan/clean; tests/test_api_library_admin.py
│       ├── backup_routes.py      /api/backups/* (Step 43): auto-backup settings, back up now, snapshot
│       │                         info/dramas, restore one drama, import from a backup file, delete snapshot; all local_only;
│       │                         tests/test_api_backups.py
│       ├── disk_usage_routes.py  /api/data-usage: scan a data-folder folder, move an item to Trash, list / restore / delete from Trash, move the backup folder; all local_only;
│       │                         tests/test_api_disk_usage.py
│       ├── reader_routes.py      /api/reader/dramas/{id}/page (Migration Slice 4); overview, progress, notes, media, captions, lookup, vocab + exports, story tools, wiki, ask (route batch 2B, M4)
│       ├── diagnostics_routes.py /api/diagnostics (Migration Slice 5, read-only)
│       ├── jobs_routes.py        /api/jobs[/{id}] (Migration Slice 8), POST /{id}/cancel (#350); records carry a redacted result + outcome (#378)
│       ├── events_routes.py      GET /api/events (SSE, library.read): job / job_gone / notifications / live /
│       │                         resync / ping events, 15 s heartbeat, session re-checked every <= 5 s (services/event_stream_service.py)
│       ├── job_stage_routes.py   GET /api/jobs/{id}/stages (library.read, job visibility): per-stage timing (Step 41)
│       ├── settings_routes.py    /api/settings (Slices 10, 23, 24: GET overview, POST non-secret bool toggles, write-only key set/clear, off by default)
│       ├── engine_routing_routes.py /api/settings/engine-routing (Step 36): GET capabilities + engine status
│       │                         (admin.settings); PC-only POST capabilities/{capability}, engines/{engine}/test
│       ├── stronger_engine_routes.py /api/stronger-engine/dramas/{id} (Step 99): GET suggestions (lines.read),
│       │                         POST lines/{line_id}/try (review.use + paid-engine gate; writes nothing)
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
│       ├── translate_run_routes.py /api/translate-run/dramas/{id}/config, .../estimate, POST .../run,
│       │                         .../bulk (list, resume, cancel), presets (Migration Slice 39)
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
│       ├── model_registry_routes.py /api/models/status (GET, admin.diagnostics), check and
│       │                         presets/{id}/switch (POST, local_only; Step 40)
│       ├── model_reeval_routes.py /api/models/reeval (GET), decisions (GET), estimate (POST) admin.diagnostics;
│       │                         settings, candidates, reject/reopen/promote, run (POST) local_only (Step 40b)
│       ├── comic_routes.py       /api/scanlate/dramas/{id}/pages, pages/{pid}/image (GET/HEAD, media.stream),
│       │                         pages/{pid}/regions, progress (GET/POST) -- comic viewer; tests/test_api_comic_viewer.py
│       ├── scanlate_routes.py    /api/scanlate/dramas/{id}/config, run-notes, pages/{pid} (GET); pages (upload, PC-only),
│       │                         run, render, export (jobs.start) -- tests/test_api_scanlate_auto.py
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
│       ├── saved_comics_routes.py /api/saved-comics: save folder (local only), series, chapters, pages, page image
│       ├── sources_import_routes.py POST /api/sources/url/preview, /url/import, /{name}/import, GET /{name}/import-state
│       │                         (sources.import; specs S-4, S-5)
│       ├── sources_tools_routes.py  /api/sources/url/preflight|preview-pasted|import-pasted|identify-media(/resource)|
│       │                            extractions; /api/discover/bulk-extract/pasted (capped pasted bodies, 413)
│       ├── sources_extraction_routes.py GET /api/sources/url/ai-engines, POST /url/import-comic, Review
│       │                         extraction under /dramas/{drama_id}/extraction (profile writes local_only; SO09/SO06/SO10)
│       ├── sources_local_routes.py POST /api/sources/settings/proxy, /{name}/signin/open|forget,
│       │                         /{name}/tier-test (all local_only; spec S-6, SO17, SO18)
│       ├── source_domains_routes.py /api/source-domains (GET), /proposals (GET), /proposals/confirm|dismiss,
│       │                         /{name}, /{name}/reset (POST; all local_only); tests/test_source_domains.py
│       ├── assistant_github_routes.py /api/assistant/github (GET), /settings|token|token/clear|test|preview|
│       │                         deliver (POST; all local_only; Step 72); tests/test_assistant_github.py
│       ├── assistant_routes.py   /api/assistant/settings|tools|ask|changelog|backlog(/clear|/{backlog_id}/delete)
│       │                         (all local_only; Step 42); tests/test_maintenance_assistant.py
│       ├── diagnostics_gaps_routes.py /api/diagnostics/setup-checks|model-cache|pyannote|job-history|log|
│       │                         support-report|install-presets|gpu-torch (GET) and gpu-torch/check,
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
│       ├── notification_routes.py /api/settings/notifications (GET, admin.settings: booleans only); /categories, /test,
│       │                         /{channel}, /{channel}/clear (POST, local_only; set/clear also use the
│       │                         key-write gate; Step 44)
│       ├── asr_options_routes.py /api/settings/asr-options (GET admin.settings, POST local_only;
│       │                         Steps 103/104)
│       ├── jellyfin_routes.py    /api/jellyfin/config (GET/POST), /key, /key/clear, /test, /scan,
│       │                         /dramas/{id}/send -- all local_only (Step 39)
│       ├── notion_routes.py      /api/notion/config (GET/POST), /token, /token/clear, /test,
│       │                         /dramas/{id} (GET), /dramas/{id}/export -- all local_only (roadmap 112)
│       ├── web_search_routes.py  /api/web-search/status, /search (library.read); /config (GET/POST), /test
│       │                         (local_only; address change also key-write gate) -- item 114
│       ├── sharing_routes.py     /api/sharing/items (admin.library), /{dramas|series}/{id}/private (lines.edit;
│       │                         owner or admin), /share-by-default (GET library.read, POST lines.edit)
│       └── notification_center_routes.py /api/notifications (GET, library.read): the header bell's recent
│                                 job-ended and new-chapter events (Step 44 item 5)
│
├── frontend/                   ← REACT APP (Vite + TypeScript). Not a Python package.
│   ├── package.json, vite.config.ts, tsconfig*.json, index.html
│   ├── src/api/                   client.ts (all HTTP) + types.ts (mirrors api/schemas/); one <area>.ts per
│   │                              API area, e.g. review.ts, restructure.ts (add/delete/merge/split/re-segment/
│   │                              restore), media.ts (Range stream URLs), libraryAdmin.ts (Library admin +
│   │                              preset/voice-bank deletes), pcOnly.ts (PC-only mode store + pcOnlyFetch:
│   │                              X-Baihe-Local header, 403 -> remote); types in src/types/<area>.ts
│   ├── src/components/            LibraryList (+ libraryFilters.ts: the More filters, pure), DramaDetailPanel, Section, Field, ErrorBanner, Sheet (<dialog>;
│   │                              bottom sheet on phones), TypedConfirm (type-a-word destructive confirm),
│   │                              ConfirmButton (two-step delete), VoiceBankPlayButton (Play/Stop one
│   │                              voice-bank clip; Library, Characters, Voices), errorMessages.ts (error copy per code),
│   │                              ErrorBoundary (page crash fallback, resets on route change) +
│   │                              errorFallbackText.ts; clipboard.ts (copyText: the one Copy helper, falls back
│   │                              to execCommand on plain http, never throws); src/bootFallback.ts (last-resort message in #root
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
│   ├── src/hooks/                 useJob (push, polling fallback), useEventStream (the tab's shared SSE stream,
│   │                              src/api/eventStream.ts: reconnect with backoff, resync, poll fallback),
│   │                              useMediaQuery, useShortcut (list keyboard shortcuts),
│   │                              useReattachJob (a stage revisited mid-job picks its job up again;
│   │                              per-stage job ids in src/pages/workspace/stageJobIds.ts),
│   │                              usePersistedState (per-viewer prefs in localStorage),
│   │                              usePcOnly ('local'|'remote'|'unknown' from /api/meta `local`), useMossExperimental (Step 104 toggle)
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
│   │                              src/api/notifications.ts. RemoteAccessSection + remoteIpCheck.ts (Settings >
│   │                              Remote access: the public-address check, set/clear/test, PC only,
│   │                              configured yes/no only); API in src/api/diagnostics.ts. PreferencesSections + preferences.ts (Settings >
│   │                              Translation style, Spending, OCR, Offline and performance,
│   │                              Downloads, Server addresses; persisted PC-side, PC only); API in
│   │                              src/api/settings.ts. src/theme.ts: system/light/dark/sepia theme (localStorage,
│   │                              <html data-theme>, applied in index.html and main.tsx; the header button is
│   │                              components/ThemeMenu.tsx; the header cogwheel, components/GearMenu.tsx, opens Settings, Admin
│   │                              (pages/Admin.tsx: Users, Audit log, Remote access) and Diagnostics; its items and the header's main nav
│   │                              come from src/nav/navItems.ts, the one navigation list with permission and PC-only flags; from 1024px up the header nav and
│   │                              cogwheel give way to the left rail, nav/SideNav.tsx + nav/sideNav.css, rendered from the same list). ApiKeysCard (Settings > API
│   │                              keys: one Set/Missing row per engine, SettingsKeyForm opens in place);
│   │                              settings.css (the page's Card stack and status rows).
│   │                              NotionSection + notion.ts (Settings > Notion, roadmap 112: token set/clear,
│   │                              target database/page link, test connection; PC only, unit-tested helpers);
│   │                              API in src/api/notion.ts (types/notion.ts). Export > Export to Notion is
│   │                              src/pages/workspace/stages/ExportNotion.tsx (job + "Open in Notion" link).
│   │                              TranscriptionExperimentsCard (Settings > Transcription experiments, Steps
│   │                              103/104: Qwen3-ASR batch size, MOSS toggle; PC only); API in src/api/asrOptions.ts
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
│   │                              sourcesToolsFormat.ts, sources-tools.css (api/sourcesTools.ts, types/sourcesTools.ts);
│   │                              AiFallback + useAiEngines (SO09 AI fallback picker), NovelUrlImport,
│   │                              ComicUrlImport (SO06), ExtractionReview (SO10), extractionFormat.ts
│   │                              (pure, unit-tested), extraction.css
│   ├── src/pages/Discover.tsx     Discover page (#/discover): one AI-engine picker, the known-titles catalogue
│   │                              (search, filters, add to Library, PC-only remove), platform search links,
│   │                              baihehub search, navigation helper, add a title (from a URL or by hand),
│   │                              bulk import; api/discover.ts, types/discover.ts
│   ├── src/pages/discover/        CatalogPanel, FindPanel (+ PlatformList), BaihehubPanel, NavigationHelp,
│   │                              AddTitle, BulkImport (+ PastedListing), ExternalLink (http(s)-only links), useDiscoverJob
│   │                              (fixed-id job polling via pollSourcesJob), discoverFormat.ts (pure,
│   │                              unit-tested), discover.css
│   ├── src/pages/workspace/stages/  also DiarizationDeviceNote (Transcribe > Speakers: GPU/CPU of the last
│   │                              pyannote run, Step 101; API in src/api/asrOptions.ts), AutoTune (Transcribe > Advanced), NovelGlossary (GlossaryExtract:
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
├── tests/                      ← 290+ test files, 7,000+ test functions. Run: python run_tests.py
│   ├── __init__.py
│   ├── conftest.py                fixtures (isolated temp database, etc.)
│   ├── fake_engine.py             key-free "fake" translation engine (tests and the e2e server; not part of the app)
│   ├── sources_helpers.py         shared fakes for adapter tests (clock, scripted HTTP, PNGs)
│   ├── manhuagui_fixtures.py      offline stand-ins for manhuagui pages
│   ├── lightnovel_fun_fixtures.py offline stand-ins for lightnovel.fun pages
│   └── test_*.py                  one or more files per module above, named to match
│
└── library/                    ← YOUR DATA. Created automatically. Gitignored.
    ├── library.db                dramas, lines, glossaries, progress, jobs and users (sources.db beside it
    │                             holds the Discover/Sources registry; other folders sit alongside)
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
| `cli.py` | headless batch runner (kept in parity with the app) |
| `run_tests.py` | test runner wrapper |
| `core.py` | timing, alignment, SRT formatting, line merging |
| `db.py` | all database access (plain `sqlite3`, no ORM) |
| `background_jobs.py` | background-job tracker: thread and child-process jobs, in-memory dict as the authority, mirrored to `job_records` |
| `applog.py` | a single rotating log file for the whole app |
| `diagnostics.py` | environment self-check: which optional dependencies/models are available |
| `check_setup.py` | `start.bat`/`start.ps1`'s "print anything missing in plain words" check |
| `process_guard.py` | Windows Job Object that ends every child process (ffmpeg, Playwright's Node and Chromium, pip...) with the API server, however it was started, plus the console-close handler that runs the clean stop first; `launcher.py --stop` can end an install's whole group (Step 80b) |
| `portable.py` | lets the whole app folder be copied/moved and still work; `data_dir()` is where library/, .env and (installed copies) model caches live -- the app folder for a source checkout, the per-user data folder for an installed copy (Step 80b) |
| `storage.py` | disk usage, cache cleanup |
| `benchmark.py` | the case runners the Benchmark Lab (services/benchmark_lab_service.py) builds on: regression tracking against your own reference cases, across every content type (audio drama, streamer VOD, novel, manhua) |
| `action_tiers.py` | 🟢/🟡/🔴 action-permission-tier classification an AI-driven feature checks before acting |

**ASR / transcription & alignment**
| File | Does |
|---|---|
| `asr_backend.py` | pluggable transcription (BACKENDS/get_backend): Whisper (default), Qwen3-ASR (optional batching, Step 103), MOSS-Transcribe-Diarize (experimental, Step 104) |
| `asr_benchmark.py` | Whisper vs Qwen3-ASR/ForcedAligner, one clip at a time |
| `audio_preprocess.py` | optional audio preprocessing before transcription |
| `mixed_language.py` | per-speech-span language detection for titles that mix spoken languages, with a script check that retries a span in the title's language |
| `vad_segments.py` | pure VAD speech-span builder: Silero spans via faster-whisper, long spans cut at the quietest point (not yet wired into the pipeline) |
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
| `translate_engines.py` | Claude / DeepSeek / Gemini / OpenAI / Ollama / NLLB (+ an offline test engine); a facade that re-exports everything from `engine_backends/` below |
| `engine_backends/__init__.py` | package docstring: a map of the modules below |
| `engine_backends/pricing.py` | model lists, per-million-token prices, cost estimates |
| `engine_backends/shared.py` | usage totals, retry/backoff, secret redaction, id-keyed request and parsing, content-moderation detection |
| `engine_backends/prompts.py` | prompt builders shared by the LLM engines |
| `engine_backends/claude.py` | the Claude engine |
| `engine_backends/openai_compat.py` | the DeepSeek and OpenAI engines |
| `engine_backends/gemini.py` | the Gemini engine, its rate-limit status text and free-tier limits |
| `engine_backends/local.py` | the local NLLB and Ollama engines, Ollama reachability check |
| `engine_backends/llm_tasks.py` | `call_llm_json` and the single-prompt features: speaker tagging, pacing, consistency, summaries, flagging |
| `engine_backends/engine_registry.py` | `ENGINES`, capability tags, notes, model overrides, `get_engine` |
| `engine_backends/fallback.py` | the translate fallback chain |
| `engine_backends/standalone.py` | standalone text translation |
| `engine_backends/translate_pipeline.py` | Reflect mode and the per-run translate loop |
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
| `debug_view.py` | "what happened here?" per-line/per-job debugging view |
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

The `services/` and `api/` packages and `frontend/` (the React + FastAPI
migration; history in `docs/archive/migration-react-fastapi.md`)
are listed in the tree above. The `sources/` package (the adapter system proper, one file per supported
site under `sources/adapters/`) and the browser extension bridge
(`page_server.py` plus everything in `extension/`) are broken out in the
tree above rather than repeated here, since each is really its own
subsystem rather than a handful of top-level modules.

## Rules of thumb

- **UI code goes in `frontend/`** (React). Logic lives at the top level (or in
  `sources/`) so it stays testable without a UI.
- **Logic the API and the CLI both need goes in `services/`**: a plain function
  an `api/routers/*_routes.py` file and `cli.py` both call.
- **Nothing writes outside `library/`** except exports you explicitly download.
- **Optional dependencies are imported inside functions**, never at module
  top level — a missing package disables its own feature instead of
  stopping the app from starting.
- **`library/` is yours.** Back it up. It's gitignored for a reason.
- **Adding a new top-level module, `services/*.py` or `api/routers/*.py`
  file? Update this file in the same PR** (root `CLAUDE.md`, "How to
  work"; a hook warns) — this drifted badly once already.
