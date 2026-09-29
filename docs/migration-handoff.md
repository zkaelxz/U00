# Migration handoff (Streamlit -> React + FastAPI)

Durable status for the next session. Repo docs and code are the source of truth; this file is the index.
Last updated 2026-09-28. Base branch: `baihe-subtitler`. Slice detail lives in `docs/migration-review.md`.

## Verified status
- Merged slices: 19-23, 25-33, 35-49 (+ earlier 1-18), plus E0 (Library remainder), Step 95 (BGM-preserving dub) and Step 97b (translate fallback chain, API level). Slices 41 and 45 were built on Opus at the user's request (#266, #267).
- Frontend (React): every planned slice is merged (F, A, B, C, I, D, E, G, H, F2; PRs #254-#271), see `docs/migration-frontend-plan.md`.
- Serving (branch `migration-serve-frontend`, not merged): `python -m api` serves `frontend/dist` at `/` (`api/static_frontend.py`, `BAIHE_API_SERVE_FRONTEND`); `start-react.bat` launches it on Windows (written without a Windows machine, untested).
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
24 API-key writes (user requirement 2026-09-29: keys stay on the main PC and are never sent to other devices; remote devices use server-side keys, so no remote key-write endpoint; entry on the PC stays in Streamlit until a PC-only admin listener exists, D5); 34 qwen3 backends (built with mocks; the real-model check is still owed by the user).
Small bug/cleanup steps 121-132: see `docs/baihe-roadmap-master.md` section 2 (bug tracker).
Deferred inside merged slices: E0 destructive bulk/backup/restore/storage clean (need server-side typed confirm + running-job refusal);
transcribe/narration docs and docstrings that still say chunk_and_tag or audiobook/auto-fill are out of scope are stale (cosmetic cleanup step).
Held-roadmap fold-ins: Step 43 (soft-delete) stays held (plug into `_hard_delete_drama`); Steps 100-105, 40b, 42, 60, 72 stay held.
After the service queue: frontend (React) slices and Streamlit retirement -- not yet planned in this repo; ask the migration-architect.

## Owed to the user (cannot verify here)
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
- Slice 49: API diarization always merges with `overwrite_manual=False` (no confirm step).
