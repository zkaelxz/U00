# Local-agent backlog

Open follow-ups sorted by who can do them, so free tiers and a local 64k model take the cheap rows and a paid
subscription burst is spent only on the last section. Checked 2026-10-08 against `origin/baihe-subtitler` at 708d50f
and the open PR list; git and the PR list win over this file. Delete a row when it merges. How to prompt a local
model: `docs/small-model-checklist.md` ("Prompting a 64k local model").

Verdicts: **LOCAL-OK** = one or two files, no auth/permissions/routes/db/concurrency, a failing test can be written
first. **FREE-TIER-OK** = bigger but well bounded; a free Claude/Codex session with a precise brief. **NEEDS-SUBSCRIPTION**
= security, auth, remote access, routes, db splits, concurrency, many files, or real parallel review (brief at the bottom).

## Table

| # | Task | Files | Lines | Risk | Verdict | Why |
|---|---|---|---|---|---|---|
| 1 | STATUS says the CPU Whisper fallback "is still `medium`" and the label warns about turbo on ja/ko; the code uses turbo on CPU (`default_whisper_size`) and the label is the per-language note (`whisperModelWarning`) | `docs/STATUS.md` | ~3 | none | LOCAL-OK | One sentence; example prompt in the checklist |
| 2 | STATUS "Open PRs" lists #836, #838, #843, #850, #851, which are in neither the open list nor this clone's history; "follow-up sessions not re-checked" includes the CLI language command, which exists (`cli.py` `set-language`) | `docs/STATUS.md` | ~15 | none | FREE-TIER-OK | Needs GitHub PR lookups the local agent can't make |
| 3 | UI text audit 3/6 to 6/6 (1/6 Settings #926 and 2/6 Diagnostics #949 are merged). The repo doesn't name areas 3-6: owner to name one page per session. Rule from 1/6 and 2/6: text only, no keys, ids, aria-labels, field names; update specs that matched the old wording | one page's `frontend/src/pages/<page>/` plus its vitest/e2e specs | 15-40 each | low (spec wording) | LOCAL-OK per page of about 10 strings; FREE-TIER-OK if specs fan out | Text only; run `npx vitest run <paths>` and the page's Playwright spec |
| 4 | Comment-only cleanups, one area per PR, behaviour unchanged, under Commenting Standards (previous rounds: #714, #723, #724, #733) | one package per PR | 30-100 | low | LOCAL-OK | Diff must show comment lines only; never reword old comments elsewhere |
| 5 | Small test additions: pick an untested branch of one function (find with `python -m pytest --cov=<module> --cov-report=term-missing tests/test_<area>.py`). No target is recorded in the repo | one test file | 20-60 | low | LOCAL-OK | New test only; production code unchanged. A test that needs a production change is a bug report instead |
| 6 | Simple UI wording fixes in labels/help text (not security or permission messages) | one `.ts`/`.tsx` plus its spec | <20 | low | LOCAL-OK | Any wording on a refused action, sign-in, sharing or keys is NEEDS-SUBSCRIPTION |
| 7 | Docs refresh after a batch of merges (`FILE_ORGANIZATION.md`, `docs/STATUS.md`, `docs/route-permissions.md`) | docs | 20-80 | none | FREE-TIER-OK | Needs `git log` and PR lookups; the `docs-steward` agent covers it |
| 9 | Source pacing vetting: check the per-source pace levels against each site's terms and behaviour (code is draft #956) | `docs/known-working-sources.md`, `docs/source-status.json` | docs | low | FREE-TIER-OK | Web research from primary sources (`source-vetter` brief); no code. Start after #956 merges |
| 10 | ASMR preset on top of the optional ASMR voice detector (draft #942) | `services/transcribe_service.py`, tuning defaults, one UI preset | ~60 | low-medium | FREE-TIER-OK after #942 merges | Depends on #942; preset values come from a real-audio check the owner runs |
| 11 | Translation-context measuring (does more context help?) | `benchmark_lab_service.py` and a doc | n/a | medium | FREE-TIER-OK after Benchmark Lab phase 3 | Blocked on phase 3 (row 12); needs real engines the owner runs |
| 12 | Benchmark Lab phases 2-6 (scope from the owner; no phase plan is in the repo; related drafts #910 and #840 chrF merged) | `benchmark_lab_service.py`, routes, schemas, frontend | 500+ | medium | NEEDS-SUBSCRIPTION | Routes, schemas and UI across many files; brief B1 |
| 13 | Live streaming recognition PRs B-F (A is queued separately): LocalAgreement-2 in `live_agreement.py`, ring buffer in `live_audio.py`, `live_streaming.py` | new `live_*.py`, `live_service.py`, `live_fetch.py`, routes, UI | 600+ | high | NEEDS-SUBSCRIPTION | Threads, audio buffering, GPU slot, new routes; brief B2 |
| 14 | Stereo tracks A1, A2, B (B1 DSP library is draft #947; A1/A2 not started) | audio/dub modules, routes, UI | 400+ | medium | NEEDS-SUBSCRIPTION | Many files, ffmpeg/DSP needing listening checks and review; brief B3 |
| 15 | `db.py` split DB-1 to DB-8 (open drafts #917, #919, #920, #943 are the neighbouring splits and the DB-0 guard/rename) | `db.py` to `db/` package | 1000+ | high | NEEDS-SUBSCRIPTION | Data integrity, every caller, size guards; brief B4 |
| 16 | Thinking toggle for bulk translation (live translation already has reply-without-thinking, draft #985) | `engine_backends/local.py`, `bulk_translate.py`, settings, `cli.py`, UI | ~150 | medium | NEEDS-SUBSCRIPTION | App and CLI parity, engines, settings across more than four files; brief B5 |
| 17 | Jellyfin-aware GPU headroom (extends "Keep free" settings, draft #984) | GPU slot code, `services/`, settings | ~120 | medium-high | NEEDS-SUBSCRIPTION | GPU slots and concurrency; brief B6 |
| 18 | Hardening PR for live capture's `media.import_url` before household users get it (owner decision 2026-10-06; its scope isn't recorded) | `services/egress_proxy.py`, `services/live_fetch.py`, `docs/remote-access-decision.md` | unknown | high | NEEDS-SUBSCRIPTION | SSRF and remote access; Opus security review; brief B7 |

## Open compliance items

Found while recording the 2026-10-09 pacing vetting (`docs/source-status.json`). Not fixed; each is for the owner to decide.

- `ranobes`: the adapter fetches `/chapters/*/page/*` for pages 2 and later, which robots.txt disallows.
- `guazimanhua`: the adapter's search uses `/category.php?*keyword=`, which robots.txt disallows.
- `syosetu`: the adapter scrapes HTML, while the terms (Art. 14 item 23) allow automated access only through the official なろうデベロッパー API.
- `toonkor`: `toonkor0.org` now redirects to `toonkor3.org`, so `BASE_URL` is stale.
- `mangak`: the adapter ships although the site's terms of service (section 4) forbid bots.
- `manhuagui`: the site footer prohibits downloading.

## Briefs for NEEDS-SUBSCRIPTION rows

Each brief goes into one session on its own branch off the latest `baihe-subtitler`. Before running, re-check the row
against the code and open PRs, and answer the "Decide first" line. Every session also follows `CLAUDE.md` and ends with
its summary (changes, commands with pass counts, doubts, follow-ups). Models: Opus 5.5 for B2, B4, B6, B7; Sonnet 5.5
otherwise (CLAUDE.md "How to work").

### B1 Benchmark Lab phases 2-6
- Decide first: what each phase delivers (the repo has no plan; take it from the owner), one phase per branch.
- Scope: one phase. Files: `benchmark_lab_service.py`, its router and `api/schemas/`, the Benchmark Lab page.
- Rules: new routes declare one permission and a `docs/route-permissions.md` row; no secrets or paths in responses;
  `ADD COLUMN` only for `library.db` plus `_INIT_DB_MIGRATED_COLUMNS`; every HTTP call has `timeout=`.
- Tests: `tests/test_benchmark_lab*.py`, `tests/test_api_permissions.py`, then the full suite and frontend commands.
- Done: the phase's exit criterion met by tests, no file over 40 KB, `FILE_ORGANIZATION.md` lines for new modules.

### B2 Live streaming recognition B-F
- Decide first: confirm PR A's merge state; each of B-F is its own PR.
- Scope: LocalAgreement-2 in `live_agreement.py`, ring buffer in `live_audio.py`, orchestration in `live_streaming.py`, then
  wiring into `live_service.py` and the Live page. Files per PR: at most the module it names, its test, and the wiring.
- Rules: jobs write only their fields; use the GPU slot only when Use GPU is on; Cancel takes effect promptly; live
  capture fetches in Python only (`services/live_fetch.py`), ffmpeg never opens a URL.
- Tests: fake audio chunks and a fake recogniser; wait for threads before asserting; no real model.
- Done: agreement and buffer behaviour pinned by unit tests; the owner's real-stream check listed as not verified.

### B3 Stereo tracks A1, A2, B
- Decide first: what A1 and A2 are (not defined in the repo), and whether B builds on draft #947 (B1 DSP library).
- Scope: one track per PR; DSP in the library, renderers, loudness; ffmpeg filters built from fixed strings.
- Rules: no user text in ffmpeg arguments; outputs go through the library temp folder; app and CLI behave the same.
- Tests: mocked ffmpeg and numeric checks on tiny arrays; the owner's listening check is listed as owed.
- Done: unit tests for each cue and renderer; docs say what was not listened to.

### B4 `db.py` split DB-1 to DB-8
- Decide first: confirm DB-0 (#943) is merged and read the open split PRs for the target layout.
- Scope: move one domain per PR into `db/`, public names unchanged (re-exported), no behaviour change.
- Rules: `get_conn()` nesting stays safe; schema changes stay `ADD COLUMN` in `init_db`; update `_INIT_DB_MIGRATED_COLUMNS`
  only if a column is added; each new file owns a domain and stays under 40 KB (paste "Splitting files" from `AGENTS.md`).
- Tests: `tests/test_db.py` and the area suites, then the full suite; `tests/test_static_analysis.py`.
- Done: callers untouched, the full suite green, `python tools/repo_map.py db` parts match.

### B5 Thinking toggle for bulk translation
- Decide first: default (off for local models?) and where the setting lives (global or per title).
- Scope: `engine_backends/local.py` and the other engines that support it, `bulk_translate.py`, settings schema,
  `cli.py translate`, Settings UI. Reuse what draft #985 added for live translation.
- Rules: match results to lines by id; strip thinking text before parsing (`strip_ollama_thinking`); CLI and app parity.
- Tests: `tests/test_local_model_defaults.py`, bulk translate tests, CLI parity tests.
- Done: setting read by both app and CLI, replies with thinking parse the same as without.

### B6 Jellyfin-aware GPU headroom
- Decide first: how Jellyfin load is detected on Windows and whether it is opt-in.
- Scope: extend the "Keep free" setting and pre-load check from draft #984; GPU slot acquisition only.
- Rules: `background_jobs.py` locks and slots stay deadlock-free; nothing waits on the GPU while holding the db connection.
- Tests: fake GPU readings; two jobs racing for a slot; cancel while waiting.
- Done: a job that would starve Jellyfin waits or refuses with plain wording; the owner's real-card check listed as owed.

### B7 Live capture hardening
- Decide first: reconcile the owner's 2026-10-06 condition with the three conditions in `docs/remote-access-decision.md`.
- Scope: `services/egress_proxy.py`, `services/live_fetch.py`, tests, and the decision doc only. Do not grant `media.import_url`.
- Rules: every redirect hop checked; capped reads; error text through `redact_secrets`; API responses carry no URLs.
- Tests: SSRF cases (private ranges, redirects to loopback, DNS rebinding), `tests/test_api_permissions.py`.
- Done: Opus security review run on the diff; residual risks written down.
