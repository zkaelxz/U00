# services/

UI-free application logic shared by `api/routers/` and `cli.py`. Mostly
`<area>_service.py`; a few helpers have other names (`safe_fetch.py`, `egress_proxy.py`).
Domain-free helpers (`errors.py`, `url_guard.py`, `capped_body.py`) live in `lib/`.

## Start here
- `python tools/repo_map.py services` (two parts) then `python tools/repo_map.py services/<name>`.
- `lib/errors.py` (also importable as `services.service_errors`): the only errors to raise (`InvalidInputError`, `NotFoundError`,
  `ConflictError`, `ForbiddenError`, `DependencyUnavailableError`, ...).
- `ownership_service.py`: who may see or change a drama or series.
- `drama_service.py`: example of whitelisting client-chosen keys before `db.update_drama`.
- `workspace_job_service.py`, `translate_run_service.py`: background job bodies.

## Rules
- Never import `api` (enforced). Talk to `db.py` and root domain modules.
- Whitelist any client-chosen key passed to `db.create_drama` / `db.update_drama`.
- Check ownership for drama/series access.
- Jobs write only their own fields: `db.save_lines(drama_id, lines, fields=("en",))`;
  build Lines with `core.line_from_row`.
- Every HTTP call has `timeout=` (enforced for all of `services/`). Fetch user-given
  URLs through `safe_fetch.py` / `lib/url_guard.py`.
- Pass error text through `translate_engines.redact_secrets` before storing or returning it.
- CLI and app must match: if `cli.py` uses the same setting, check it too.

## Tests
- `tests/test_<name>_service.py` or `tests/test_<area>.py`, e.g. `test_drama_service.py`,
  `test_ownership_service.py`, `test_translate_run_service.py`, `test_workspace_job_service.py`
- `tests/test_static_analysis.py` (timeouts, `TestLayering`, module size)

## Do not touch
- Files on the size allowlist in `tests/test_static_analysis.py` may shrink, never grow.
