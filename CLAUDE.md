# Baihe Subtitler

A local app for transcribing, translating, reviewing, dubbing and exporting subtitles for Chinese, Japanese and Korean audio dramas, novels, comics and videos, with a library of titles.

## Layout
- `start.bat` runs `python -m api`: FastAPI on 127.0.0.1:8600, which also serves the built React app from `frontend/dist`.
- Dev: `BAIHE_API_ENV=development python -m api`, and `cd frontend && npm ci && npm run dev` (Vite on :5173, proxies `/api`).
- Layers, top to bottom: `frontend/` (React) -> `api/` (routers in `api/routers/*_routes.py`, Pydantic models in `api/schemas/` (one module per domain; `from api.schemas import X` works for all; a shape shared by several domains lives in `common`), auth in `api/auth.py`) -> `services/*_service.py` (UI-free logic; raise the errors in `services/service_errors.py`) -> root domain modules -> `db.py`.
- `db.py`: plain sqlite3, no ORM. Schema changes go through `ALTER TABLE ... ADD COLUMN` in `init_db`; list each new column in `_INIT_DB_MIGRATED_COLUMNS` in `tests/test_db.py` so the upgrade test covers it (a guard test fails if you forget).
- `translate_engines.py`: every translation/LLM engine plus the id-keyed request, retry and redaction helpers.
- `background_jobs.py`: thread-based jobs. The in-memory dict is the authority, with a best-effort mirror in the `job_records` table.
- `sources/`: site adapters (`sources/adapters/`) and the fetch ladder. `cli.py`: headless batch runner.
- Current status and what's next: `docs/STATUS.md`.

## Tests
- While iterating: `python -m pytest -q tests/test_<area>.py`. Full suite: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`.
- Frontend: `cd frontend && npx tsc --noEmit && npx vitest run`; Playwright in `frontend/e2e/` (use the preinstalled Chromium; never `playwright install`).
- Tests are mocked: no network, GPU, real models or real keys. Use the `isolated_db` fixture for anything touching the database, and `pytest.importorskip` for optional libraries.
- Wait for background job threads before asserting. Poll a background process with `kill -0 <pid>`, never `pgrep -f` on a pattern that also matches your own command line.
- CI is the merge gate while the repo is public; if it becomes private or Actions minutes run out, the full local suite (`python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`) plus the frontend commands is the gate. Never skip or weaken a test.

## Rules learned from real bugs
- Match LLM results back to lines by explicit id, never by list position (`translate_engines.request_translations_with_retry`, `parse_id_keyed_json`).
- API keys go in headers, never in URLs, log lines or stored error messages. Pass any error text through `translate_engines.redact_secrets` before showing, storing or logging it. API responses never include secrets, filesystem paths or fetched URLs (booleans only).
- Every outbound HTTP call has a `timeout=`. `tests/test_static_analysis.py` enforces this for every file under `services/` and `api/` plus the other modules it lists; add a new HTTP-calling module outside those two packages to its list.
- Background jobs write only the fields they own: `db.save_lines(drama_id, lines, fields=("en",))`. A full sync (`fields=None`) makes the list the drama's lines: rows are updated in place by id, rows missing from the list are deleted, and a field is written when it differs from the Line's `orig`. Build Lines with `core.line_from_row` (it carries every field and `orig`) so flags, speaker and the like aren't wiped.
- `db.create_drama` / `db.update_drama` interpolate kwarg keys into SQL: services must whitelist keys. Services check drama/series ownership (`services/ownership_service.py`).
- Every API route declares exactly one of `require_permission(...)`, `public_route()` or `local_only()`; `tests/test_api_permissions.py` enforces it along with the route table in `docs/remote-access-decision.md`.
- CLI and app must behave the same (glossary, style guide, locale, character names). When you change one, check the other.
- A new optional dependency is registered in `diagnostics.OPTIONAL_DEPENDENCIES` in the same change.

## How to work
- One task per branch, off the latest `baihe-subtitler`. Roadmap steps use `step-<id>-<short-name>`.
- Re-check any claim from a doc or old note against the code before acting on it. If the code has moved on, say so.
- Keep changes to what the task needs. No new files, docs, settings or abstractions unless the task asks for them. If your change makes something unused, delete it. Pre-existing problems you notice go in your summary, not your diff.
- A new code comment states the constraint or the reason, never a Step, Slice, B- or PR id. Don't mass-rewrite old comments.
- Screenshots go on the PR as attachments, not in committed files.
- A new top-level module, `services/*.py` or `api/routers/*.py` file gets a line in `FILE_ORGANIZATION.md` (a hook warns).
- Finish with a short summary: what changed and what the user will notice, the commands you ran with pass counts, what you're unsure about, and follow-ups.
- Merging: push and open a draft PR into `baihe-subtitler`; the lead session merges once CI is green.
- Don't delegate by default. Use a subagent only for independent work that needs many files read, and brief it with the exact files and question.
- Model: Sonnet 5.5 at medium effort by default (sessions and agents). Use Opus 5.5 for security reviews and audits, auth/remote-access changes, and concurrency or data-integrity bugs; `security-reviewer` and `security-auditor` are pinned to Opus. (User decision 2026-09-30, for cost.)
- Commit trailers name the model actually used.
