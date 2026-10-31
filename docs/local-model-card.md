# Local model card

Read this first, then `CLAUDE.md` and `AGENTS.md` (about 10k tokens of rules together). `CLAUDE.md` wins on a conflict.

## Rules that matter most, and what enforces each
1. Match LLM results to lines by explicit id, never by position: `engine_backends.shared.parse_id_keyed_json`, `request_translations_with_retry`.
2. Every outbound HTTP call has `timeout=`: `tests/test_static_analysis.py`.
3. Keys go in headers; pass error text through `engine_backends.shared.redact_secrets`; API responses carry no secrets, paths or fetched URLs.
4. Every route declares exactly one of `require_permission(...)`, `public_route()`, `local_only()`, then run `python tools/route_table.py --write`: `tests/test_api_permissions.py`.
5. Jobs save only their own fields, `db.save_lines(id, lines, fields=("en",))`; build Lines with `core.line_from_row`.
6. A new `library.db` column is `ALTER TABLE ... ADD COLUMN` in `init_db`, listed in `_INIT_DB_MIGRATED_COLUMNS`: `tests/test_db.py`.
7. A file over 40 KB never grows and is read with `python tools/repo_map.py <module> --part N`: `tests/test_static_analysis.py`, `tests/test_split_guards.py`.
8. Tests are mocked (`isolated_db` for the database); never skip, loosen or delete one to get green.
9. Comments explain WHY only and never name a PR or step. No new files, settings or abstractions beyond the task: `tests/test_file_organization.py`.

## Stop and ask the owner before touching
- `db/` (splits, table rebuilds, any migration that is not a plain `ADD COLUMN`).
- `background_jobs.py`, process-job workers, and anything with locks, threads or GPU slots.
- Auth, sessions, permissions policy, remote access, keys and secrets.
- A test to skip, delete or loosen, or an `OVERSIZED_*` number to raise.
- Any change that needs more than about four files: say how to split it.
Rereading this list before each commit is cheap; these are the rules a long session forgets.

## Commands
- Find code: `python tools/repo_map.py --find "<word>"`, `python tools/repo_map.py --callers <name>`.
- Iterate: `python -m pytest -q tests/test_<area>.py`.
- Before pushing, print the lean pytest command for your diff (mapped tests plus the guard set): `python tools/check_change.py`
- Or run it, stopping with a non-zero exit at the first failure: `python tools/check_change.py --run`
- The guard set (`tools/check_change.py` `GUARDS`; details in `docs/testing-and-ci.md`, "Guard set and lean output"):
  `tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py tests/test_agent_docs.py tests/test_settings_schema.py tests/test_settings_parity.py tests/test_frontend_limit_parity.py tests/test_db.py tests/test_diagnostics_and_export.py`
- Frontend, from `frontend/`: `npx tsc -b && npx vitest run`.
- CI runs the full suite; don't run it locally.

## Running the model
Setup, the Ollama environment and the think-off method are in `docs/small-model-checklist.md`. Stop the coder model
before a transcription or translation job: it shares the GPU.
