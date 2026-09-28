# Migration handoff (Streamlit -> React + FastAPI)

Durable status for the next session. Repo docs and code are the source of truth; this file is the index.
Last updated 2026-09-28. Base branch: `baihe-subtitler`. Slice detail lives in `docs/migration-review.md`.

## Verified status
- Merged slices: 19-23, 25-33, 35-40, 42-44, 46-49 (+ earlier 1-18), plus E0 (Library remainder) and Step 97b (translate fallback chain, API level).
- Hardening merged: H1 (drama, characters), H2 (glossary, ASS), H3 (read-only services).
- Full suite: batch 1 = 3805 passed, 86 skipped. Batch 2 (base through PR #244) = 4028 passed, 86 skipped, 1 setup error
  (`test_run_already_running_is_409`, `sqlite3 database is locked` while a real `transcribe_1` thread lingered; did not reproduce in 3 isolated
  and 2 wider re-runs, so treated as a load flake -- if it recurs, make that test wait for/mock the job thread). Slices merged after #244 (29-30, 32, 37, 38, 44, E0, 97b)
  ran focused tests only (each 177-346 passed); run the full suite before the next big merge.
- CI is red on every PR since #212 because GitHub Actions minutes are exhausted (fails within seconds). Standing rule from the user: merge once the local suite passes.

## Slice pattern (keep using it)
UI-free `services/<x>_service.py` (plain dicts; errors from `services/service_errors.py`: NotFound 404, InvalidInput 422,
UnsupportedOperation 400, Conflict 409, DependencyUnavailable 503) + thin `api/routers/<x>_routes.py` + Pydantic models appended
at the end of `api/schemas.py` + a line in `api/server.py` + paragraph in `docs/migration-review.md` + `FILE_ORGANIZATION.md`.
Writes are POST (DELETE only for Slice 36/43 notes). Secrets, paths and URLs are never returned (booleans only).
Background work = "job does everything" (`background_jobs.start_job`, DB write included). Process jobs use `on_done` (Slice 49).
Field-scoped writes only (`db.save_lines(..., fields=(...))`), never `fields=None` from a stale list.
`db.create_drama`/`update_drama` interpolate kwarg keys into SQL: services must whitelist. Verify drama/series ownership in services.

## Parallel recipe
1. One implementer/worktree agent per slice, each on its own branch off the latest `baihe-subtitler`, no PR.
2. Lead merges sequentially: `git merge origin/baihe-subtitler`, then
   `python scripts/migration/resolve_slice.py <service_stem> <router_stem>` (server, FILE_ORGANIZATION, schemas append, docs) for new-service slices,
   or `python scripts/migration/keepboth.py <files>` for append-only conflicts (hardening or edits without a new router).
   Check `import api.server`, run focused tests, push, PR, squash-merge.
3. Never `pgrep -f`/`pkill -f` a pattern in your own wrapper command; poll a captured PID with `kill -0`.

## Decisions taken (user, 2026-09-28)
Job-apply: job does everything. Tuning knobs: persisted (columns added in init_db). Destructive actions: match today's UI bar (typed confirm).
Uploads/exports: multipart + drama-folder outputs. `use_gpu`: persisted, default off. Process-job results: `on_done` hook (Slice 49).

## Queue (not yet built)
24 API-key writes (gated on loopback policy D5); 34 qwen3 backends (gated on a real-model check);
41 translate bulk/batch + Reflect (needs Opus confirmation: Steps 9/9d); 45 restructure + version restore (needs Opus confirmation: Step 6c);
Step 95 BGM-preserving dub (held roadmap step; adjacent to Slice 26, now unblocked).
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
