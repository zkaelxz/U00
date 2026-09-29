# Migration handoff (Streamlit -> React + FastAPI)

Durable status for the next session. Repo docs and code are the source of truth; this file is the index.
Last updated 2026-09-29. Base branch: `baihe-subtitler`. Slice detail lives in `docs/migration-review.md`.

## Session handoff (2026-09-29, late): read this first
The previous lead session handed over to a new session so that the new project agents can load
(`.claude/agents/`: merge-integrator, api-slice-builder, react-page-builder, security-reviewer,
security-auditor, parity-auditor, bug-investigator, test-author, docs-steward, source-vetter; skill `/merge-slice`).

**Merged today (newest first):** #354 agents 2 + FILE_ORGANIZATION hook + xdist in cloud setup; #353 invariant tests 2 (logs B-27);
#352 slices 51+52 (bulk batch list/cancel, media playback with Range, `media.stream`); #351 agents + `/merge-slice`;
#350 B-04/B-05 job cancel; #349 service invariant tests; #348 React Source details/modes; #347 B-26; #339 step 133 auth.

**Still in flight in the OLD session** (it finishes and merges these; don't start duplicates, and check `git log origin/baihe-subtitler` and the open PRs before touching these areas):
- merge-integrator landing, in order: `library-admin-service`, `reader-service`, `transcribe-autotune-glossary-service`, `fix-b27-retry-engine` (B-27: a line-scoped retry no longer overwrites the drama's engine), then one full parallel suite run.
- `api-routes-batch-1` (one writer of `api/`): API startup hook, `GET /api/workflow/dramas/{id}/progress`, Live routes, Discover D-2, Sources S-3, Diagnostics gaps routes. Pushed without a PR, for review and merge.
- Read-only audits, reports in the old session's scratchpad (their findings are summarized to the user, not committed): whole-system security audit (required before household access) and parity audit (before Streamlit deletions).

**Next queue for the new session:**
1. Once the old session's items are merged: route batch 2 over the new services: library admin (bulk delete, restore and storage clean are `local_only`; downloads of backups and exports are `local_only`), Reader (the permission contract is in the reader-service merge PR), auto-tune and glossary-from-novel (`jobs.start`, plus `engines.paid` when the engine is paid).
2. Delete routes that have no API yet (all PC-only, `local_only` plus confirm): remove audio, remove raw novel, delete version, series character, bug bundle, preset, voice bank. Their Streamlit tests stay until these exist.
3. Workspace shell + Review editor rebuild (spec `docs/specs/ux-workspace-shell-and-review.md`), after the progress endpoint merges; then the React pages for the new routes (react-page-builder).
4. Act on the security-audit and parity-audit findings; then extract the 36 tab functions (`docs/streamlit-test-triage.md`); then the `pre-streamlit-removal` tag + `legacy/streamlit` branch + the deletion PRs.
5. Step 141 (user approved, 2026-09-29): have migration-architect write a spec for the standalone PC shell and the "This PC" / "Connect to my PC" toggle, after remote access (step 140). See the step 141 row in `docs/baihe-roadmap-master.md`. Spec only; no build yet.

**Test gate:** `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""` gives the full suite in about 4 minutes (4686 passed, 86 skipped on #352).

## Verified status
- Merged slices: 19-23, 25-33, 35-49 (+ earlier 1-18), plus E0 (Library remainder), Step 95 (BGM-preserving dub) and Step 97b (translate fallback chain, API level). Slices 41 and 45 were built on Opus at the user's request (#266, #267).
- Frontend (React): every planned slice is merged (F, A, B, C, I, D, E, G, H, F2; PRs #254-#271), see `docs/migration-frontend-plan.md`.
- Serving (merged, #287; 405 fix #294): `python -m api` serves `frontend/dist` at `/` (`api/static_frontend.py`, `BAIHE_API_SERVE_FRONTEND`); `start-react.bat` launches it on Windows (written without a Windows machine, untested).
- Concise UI (merged): shared Section/Field blocks (#293), then Dub #295, Export #296, Translate #297, Review #298, Source #299, Translate Reflect/Bulk/Resume pending batches #300, Settings + Diagnostics #301, NovelPanel + Library new-drama form #302 (Library table-first #285). Every React page/stage now has the concise treatment (collapsible Section, Field with tooltips, remembered state). Also merged: GPU/CPU reporting and job progress messages (#283), Slice 24 key writes (#289), Slice 34 with fakes (#290), bug batches (#286, #291).
- Tests: frontend vitest 138 tests / 23 files; Playwright 40 specs; `pytest --collect-only` = 4342 tests collected (2026-09-29; collection only, not a pass count).
- Hardening merged: H1 (drama, characters), H2 (glossary, ASS), H3 (read-only services).
- Full suite: batch 1 = 3805 passed, 86 skipped. Batch 2 (base through PR #244) = 4028 passed, 86 skipped, 1 setup error
  (`test_run_already_running_is_409`, `sqlite3 database is locked`). That turned out to be an order-dependent race, not a load flake; fixed in #259 (the test now waits for the job thread).
  Later slices ran focused tests only; the full-suite result on the current base is recorded in `docs/baihe-roadmap-master.md` section 1 (snapshot).
- CI is red on every PR since #212 because GitHub Actions minutes are exhausted (fails within seconds). Standing rule from the user: merge once the local suite passes.

## Slice pattern (keep using it)
UI-free `services/<x>_service.py` (plain dicts; errors from `services/service_errors.py`: NotFound 404, InvalidInput 422,
UnsupportedOperation 400, Conflict 409, DependencyUnavailable 503) + thin `api/routers/<x>_routes.py` + Pydantic models appended
at the end of `api/schemas.py` + a line in `api/server.py` + paragraph in `docs/migration-review.md` + `FILE_ORGANIZATION.md`.
Writes are POST (DELETE only for Slice 36/43 notes). Secrets, paths and URLs are never returned (booleans only).
Background work = "job does everything" (`background_jobs.start_job`, DB write included). Process jobs use `on_done` (Slice 49).
Field-scoped writes only: see root `CLAUDE.md` "Rules learned from real bugs".
`db.create_drama`/`update_drama` interpolate kwarg keys into SQL: services must whitelist. Verify drama/series ownership in services.

## Parallel recipe
1. One implementer/worktree agent per slice, each on its own branch off the latest `baihe-subtitler`, no PR.
2. Lead merges sequentially: `git merge origin/baihe-subtitler`, then
   `python scripts/migration/resolve_slice.py <service_stem> <router_stem>` (server, FILE_ORGANIZATION, schemas append, docs) for new-service slices,
   or `python scripts/migration/keepboth.py <files>` for append-only conflicts (hardening or edits without a new router).
   Check `import api.server`, run focused tests, push, PR, squash-merge.
3. Background-wait rules: `docs/testing-and-ci.md`.

## Decisions taken (user, 2026-09-28)
Job-apply: job does everything. Tuning knobs: persisted (columns added in init_db). Destructive actions: match today's UI bar (typed confirm).
Uploads/exports: multipart + drama-folder outputs. `use_gpu`: persisted, default off. Process-job results: `on_done` hook (Slice 49).

## Queue (not yet built)
24 API-key writes (user requirement 2026-09-29: keys stay on the main PC and are never sent to other devices; remote devices use server-side keys) -- **built, OFF by default** (`BAIHE_API_ALLOW_KEY_WRITES=1` to enable): write-only `POST /api/settings/keys/{engine}` (body `{value, confirm:true}`) and `POST /api/settings/keys/{engine}/clear`; response is only `{engine, configured}`. Guards (all must hold, else a generic 403): flag on; TCP peer is loopback; Host header is 127.0.0.1/localhost/[::1]; none of X-Forwarded-For/Host/Proto, Forwarded, X-Real-IP, Tailscale-User-Login, Cf-Connecting-Ip, Cf-Ray, Via present; Origin, if present, is loopback; then `confirm=true`, a known secret engine, and a clean value (no whitespace/quotes/control chars, max 512). **Honest limit: this is a safeguard, not authentication** -- a header/peer check can be defeated by a proxy that strips headers or a local process; real admin isolation is the separate admin listener (D5), still to be built.; 34 qwen3 backends -- **built with fakes (#290)**, real-model check still owed by the user.
Next (React and API gaps): metadata auto-fill and media-analysis UI; OCR/EPUB import UI; per-line improve/why-this; media playback with Range; dub download; SSE/job push; a pending-batch list endpoint (none exists, so the Translate "Resume pending batches" control cannot list batches with poll/cancel until one is added); B-04/B-05 job-cancel staleness; B-09 part 2 (async extraction); B-08 SSRF pinning; frontend phases 7-10; deferred roadmap steps 106-132 per the master doc.
Small bug/cleanup steps 121-132: see `docs/baihe-roadmap-master.md` section 2 (bug tracker).
Deferred inside merged slices: E0 destructive bulk/backup/restore/storage clean (need server-side typed confirm + running-job refusal);
transcribe/narration docs and docstrings that still say chunk_and_tag or audiobook/auto-fill are out of scope are stale (cosmetic cleanup step).
Held-roadmap fold-ins: Step 43 (soft-delete) stays held (plug into `_hard_delete_drama`); Steps 100-105, 40b, 42, 60, 72 stay held.
After the service queue: frontend (React) slices and Streamlit retirement -- not yet planned in this repo; ask the migration-architect.

## Remote access (decided 2026-09-29)
D6 is replaced: Caddy on the always-on PC with a real Baihe login (Google OIDC + users allowlist), deny-by-default permissions, admin on the D5 loopback listener. Full decision, repo work list and open verifications: `docs/remote-access-decision.md`. Proposed steps 133-140 in `docs/baihe-roadmap-master.md` section 5. Nothing is built; do not expose the API beyond loopback until step 133 (auth and permissions) is merged and tested on the LAN.

## Owed to the user (cannot verify here)
Not blocking: GPU check output from the user's PC (venv python: ctranslate2 CUDA device count, `torch.cuda.is_available()`); Slice 34 real-model check; real-hardware checks.
Real TTS, ffmpeg, Whisper on GPU, paid LLM keys, and a gated-access HF token for pyannote diarization.
Also for the planning session: candidate notes on Fanjiao/GLify/YuriAudio2Notion, and `sources/` gaps (ETag revalidation in
`chapter_check.py`, partial-import retry state).

## Known flags
- Step 97b: the fallback switch is immediate on the first qualifying error (no backoff on the primary); CLI/Streamlit do not expose a chain.
- Slices 29-30 and 38: real ffmpeg/libass and real OCR/EPUBs never run; Settings `tesseract_cmd` is not passed to chapter OCR.
- `scripts/migration/resolve_slice.py`: keep-both is unsafe for `api/schemas.py` when a branch edits a class in place (Step 97b); rebuild as base + appended block, or hand-merge ours-first.
- Stale stub line `**Next candidates:** the` near line 876 of docs/migration-review.md.
- `gemini_free_tier` (Slice 23) is stored but unused; likely consumer `translate_engines.engine_picker_label`.
- `drama_service` now imports `background_jobs` (Slice 36); import weight unchecked.
- Slice 36: if the drama folder cannot be fully removed, the DB row is already deleted (500 without paths).
- Slice 43: `expected` compare and write are two steps, not atomic.
- Slice 49: API diarization merges with `overwrite_manual=False` by default; explicit `overwrite_manual=true` needs `confirm=true` (B-13, fixed).
