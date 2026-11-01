# Baihe Studio

A local app for transcribing, translating, reviewing, dubbing and exporting subtitles for Chinese, Japanese and Korean audio dramas, novels, comics and videos, with a library of titles.

## Layout
- `start.bat` runs `python -m api`: FastAPI on 127.0.0.1:8600, which also serves the built React app from `frontend/dist`.
- Dev: `BAIHE_API_ENV=development python -m api`, and `cd frontend && npm ci && npm run dev` (Vite on :5173, proxies `/api`).
- Layers, top to bottom: `frontend/` (React) -> `api/` (routers in `api/routers/*_routes.py`, Pydantic models in `api/schemas/` (one module per domain; `from api.schemas import X` works for all; a shape shared by several domains lives in `common`) plus the `api/*_schemas.py` modules beside it, which routers import directly; new models go in `api/schemas/`, auth in `api/auth.py`) -> `services/*_service.py` (UI-free logic; raise the errors in `lib/errors.py`) -> root domain modules -> `db/` (a package: `db/__init__.py` plus the domain submodules moved out of it so far). `lib/` (shared helpers with no domain knowledge: errors, `url_guard`, `capped_body`) sits below every layer and imports none of them.
- `db/`: plain sqlite3, no ORM; callers use `db.<name>`. Schema changes to `library.db` go through `ALTER TABLE ... ADD COLUMN` in `init_db` (`db/__init__.py`) (a change that can't be an ADD COLUMN is a table rebuild in one transaction, as the line-id migration does); list each new column in `_INIT_DB_MIGRATED_COLUMNS` in `tests/test_db.py` so the upgrade test covers it (a guard test fails if you forget). `sources.db` is a separate file with its own `_ADDED_COLUMNS` in `sources/store.py`, which that guard test does not cover.
- `engine_backends/`: translation engines by provider, plus the id-keyed request, retry and redaction helpers in `engine_backends/shared.py`. Import the module that defines a name; tests patch the module that uses it.
- `background_jobs.py`: thread-based jobs (progress, results, status, cancel); starting and running them live in `jobs/` (`job_launch`, `job_runner`). The in-memory dict (`jobs/job_table.py`) decides what a job this process runs does next; `jobs/job_store.py` is the only writer of its `job_records` row, which is what other processes see.
- `sources/`: site adapters (`sources/adapters/`) and the fetch ladder.
- Current status and what's next: `docs/STATUS.md`.
- Small context window? Follow `docs/small-model-checklist.md`; `python tools/repo_map.py` prints the symbol map on demand. Local models (OpenCode) read `AGENTS.md`, not this file: when you change a rule AGENTS.md repeats, change it there too.

## Tests
- While iterating: `python -m pytest -q tests/test_<area>.py`. Before pushing: the area's tests plus the guard set (the five CI guards and the hidden ones: `docs/testing-and-ci.md`, section "Guard set and lean output"; `python tools/check_change.py` prints the command for your diff). CI runs the full suite on every PR (about 17 minutes); don't run it locally as well.
- Frontend: `cd frontend && npx tsc -b && npx vitest run`; Playwright in `frontend/e2e/` (use the preinstalled Chromium; never `playwright install`).
- Tests are mocked: no network, GPU, real models or real keys. Use the `isolated_db` fixture for anything touching the database, and `pytest.importorskip` for optional libraries.
- Wait for background job threads before asserting. Poll a background process with `kill -0 <pid>` (PowerShell: `Get-Process -Id <pid>`), never `pgrep -f` on a pattern that also matches your own command line.
- CI is the merge gate while the repo is public. Only if it becomes private or Actions minutes run out does the full local suite (`python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`) plus the frontend commands become the gate. A test is never skipped, loosened or deleted to get CI green. Removing or rewriting one is fine when its behaviour is gone, when another test (named in the PR) pins the same behaviour, when it pinned an implementation detail (a patch target, a call shape) and a behaviour test replaces it, or when it could not fail. The only test of a behaviour that still exists stays. The PR summary lists each removed test and which of these applies.

## Rules learned from real bugs
- Match LLM results back to lines by explicit id, never by list position (`engine_backends.shared.request_translations_with_retry`, `parse_id_keyed_json`).
- API keys go in headers, never in URLs, log lines or stored error messages. Pass any error text through `engine_backends.shared.redact_secrets` before showing, storing or logging it. API responses never include secrets, filesystem paths or fetched URLs (booleans only), except a `display_url`-cleaned source page link (scheme, host, path only).
- Every outbound HTTP call has a `timeout=`; fetches the app makes itself go through `lib.http.get`/`post`, which apply it. `tests/test_static_analysis.py` enforces this for every file under `services/` and `api/` plus the other modules it lists; add a new HTTP-calling module outside those two packages to its list.
- Background jobs write only the fields they own: `db.save_lines(drama_id, lines, fields=("en",))`, and declare them at launch (`job_launch.start_job(..., line_fields=("en",))`, `()` for none, `db.FULL_SYNC` to replace the lines); the save refuses anything else. A full sync (`fields=None`) makes the list the drama's lines: rows are updated in place by id, rows missing from the list are deleted, and a field is written when it differs from the Line's `orig`. Build Lines with `core.line_from_row` (it carries every field and `orig`) so flags, speaker and the like aren't wiped.
- `db.create_drama` / `db.update_drama` interpolate kwarg keys into SQL: services must whitelist any keys a client can choose (`drama_service` does); other services pass fixed literal keys. Services check drama/series ownership (`services/ownership_service.py`).
- Every API route declares exactly one of `require_permission(...)`, `public_route()`, `local_only()` or `authenticated()`; `tests/test_api_permissions.py` enforces it, and its row in `docs/route-permissions.md` is regenerated with `python tools/route_table.py --write`.
- A new optional dependency is registered in `diagnostics.OPTIONAL_DEPENDENCIES` in the same change.

## Commenting Standards

- Comments should explain **WHY**, not **WHAT**.
- Do not comment code when the code is already reasonably self-documenting.
- Explain non-obvious business rules, design decisions, constraints, invariants,
  compatibility workarounds, and reasons for seemingly unusual code.
- If code looks redundant or counterintuitive but is intentionally required,
  document why.
- Do not use comments as a substitute for architecture or design documentation.
- Put broader system explanations, workflows, and architectural decisions in
  `docs/` rather than duplicating them throughout the codebase.
- Prefer clear naming, structure, and tests over explanatory comments.
- Keep comments concise and maintain them when the underlying reasoning changes.

**Rule of thumb:**
- Code explains WHAT.
- Comments explain WHY.
- Documentation explains HOW THE SYSTEM FITS TOGETHER.
- Decision records explain WHY THE SYSTEM WAS DESIGNED THIS WAY.

## How to work
- One task per branch, off the latest `baihe-subtitler`. Roadmap steps use `step-<id>-<short-name>`.
- Re-check any claim from a doc or old note against the code before acting on it. If the code has moved on, say so.
- Keep changes to what the task needs. No new files, docs, settings or abstractions unless the task asks for them. If your change makes something unused, delete it. Pre-existing problems you notice go in your summary, not your diff.
- A PR that changes user-visible behaviour updates the user guide line and the STATUS entry in the same PR.
- Lighter tier for Benchmark Lab, Scanlate, Live, Sources and the browser extension (Kae, 2026-10-31). Exception to the line above: a PR whose change stays inside one of these areas does not edit the user guide or STATUS; the lead updates both in the weekly docs pass before a release tag. A new screen in these areas still gets its user guide line in the PR. They keep their tests and CI and the rest of the rules here. Keep one Playwright flow per area rather than a spec per change, and attach no before/after screenshots to the PR. Behaviour doesn't change without the task asking.
- Removing a feature removes all of it in one PR: routes and their `docs/route-permissions.md` rows, service, domain code, settings, tests, docs and the menu entry. Deleting a test whose behaviour is gone is not weakening a test.
- Prefer a helper to a guard test. A rule that one shared function can make impossible (a timeout, a capped read, a decoded child process) gets the function; a static-analysis test is for what code structure can't enforce, and its docstring names what would retire it.
- A new setting needs a sentence in the PR saying who asked for it and what the default fails to do; developer knobs go behind Developer Mode, not into Settings.
- A new code comment states the constraint or the reason, never a Step, Slice, B- or PR id. Don't rewrite old comments in passing; fix them only in a dedicated comments-only PR (one area at a time, behaviour unchanged) under Commenting Standards.
- Screenshots go on the PR as attachments, not in committed files.
- A new module starts with a one-line docstring saying what it owns; `python tools/repo_map.py` prints the module map from those (`--find`, `--part N`).
- Splitting a very large file: 64 KB is a soft limit that only warns (`SOFT_MODULE_BYTES` in `tests/test_static_analysis.py`), not a target; each new file must own a domain or workflow and cut what a typical task reads. Paste the definition of done from "Splitting files" in `AGENTS.md` into the brief.
- Finish with a short summary: what changed and what the user will notice, the commands you ran with pass counts, what you're unsure about, and follow-ups.
- Merging: push and open a draft PR into `baihe-subtitler`; the lead session merges once CI is green.
- Don't delegate by default. Use a subagent only for independent work that needs many files read, and brief it with the exact files and question.
- Model: Sonnet 5.5 at medium effort by default (sessions and agents). Use Opus 5.5 for security reviews and audits, auth/remote-access changes, and concurrency or data-integrity bugs; `security-reviewer` and `security-auditor` are pinned to Opus. (User decision 2026-09-30, for cost.)
- Commit trailers name the model actually used.
