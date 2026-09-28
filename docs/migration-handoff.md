# Migration handoff (Streamlit -> React + FastAPI)

Durable status for the next session. Repo docs and code are the source of truth; this file is the index.
Last updated 2026-09-28. Base branch: `baihe-subtitler`. Slice detail lives in `docs/migration-review.md`.

## Verified status
- Merged slices: 19, 20, 21, 23, 25, 27, 35, 36, 39, 42, 43, 46, 47, 48, 49 (+ earlier 1-18).
- Hardening merged: H1 (drama, characters), H2 (glossary, ASS), H3 (read-only services).
- Full suite on the batch-1 merge state: 3805 passed, 86 skipped (`python run_tests.py`). Later PRs (#231-#237) ran focused tests only (each 228-326 passed); run the full suite before the next big merge.
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
22 cross-process cancel; 24 API-key writes (gated on loopback policy D5); 26 dub run (needs 49, done); 28 artifact download; 29 audiobook;
30 burned-in video; 31 media upload; 32 upload-then-transcribe; 33 chunk_and_tag; 34 qwen3 (gated on real-model check);
37 metadata auto-fill; 38 source attach + chapter OCR; 40 translate run; 41 translate bulk/reflect (Opus confirmation: Steps 9/9d);
44 review checks + AI jobs; 45 restructure + version restore (Opus confirmation: Step 6c); E0 Library remainder.
Held-roadmap fold-ins: Step 97b after 40 (never concurrent); Step 95 after 26; Step 43 (soft-delete) stays held (plug into `_hard_delete_drama`);
Steps 100-105, 40b, 42, 60, 72 stay held.

## Owed to the user (cannot verify here)
Real TTS, ffmpeg, Whisper on GPU, paid LLM keys, and a gated-access HF token for pyannote diarization.
Also for the planning session: candidate notes on Fanjiao/GLify/YuriAudio2Notion, and `sources/` gaps (ETag revalidation in
`chapter_check.py`, partial-import retry state).

## Known flags
- `gemini_free_tier` (Slice 23) is stored but unused; likely consumer `translate_engines.engine_picker_label`.
- `drama_service` now imports `background_jobs` (Slice 36); import weight unchecked.
- Slice 36: if the drama folder cannot be fully removed, the DB row is already deleted (500 without paths).
- Slice 43: `expected` compare and write are two steps, not atomic.
- Slice 49: API diarization always merges with `overwrite_manual=False` (no confirm step).
