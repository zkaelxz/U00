# Task checklist for small-context models

Short window (16-32K tokens): search first, read little, change little.

1. Restate the task in one line. Name the symptom, UI text or setting.
2. Search before opening anything:
   - `git grep -n "<exact UI text or setting name>"`
   - `python tools/repo_map.py --find "<word>"` (names, signatures, docstrings)
   - `python tools/repo_map.py` for the layer map (~3.5k tokens).
3. Follow the layers down: `frontend/src/api` -> `api/routers` -> `services` ->
   root module -> `db.py`. Write the candidate file list.
4. Open the top two files only. List one first: `python tools/repo_map.py <module>`.
   Read only the needed functions by line range. Never open `db.py` whole:
   `python tools/repo_map.py db`, then `--part N`.
5. Read the package `README.md`. Find the tests:
   - `tests/test_<area>.py` for the area.
   - Guards: `tests/test_api_permissions.py` (one permission per route, route table),
     `tests/test_static_analysis.py` (40 KB module size guard, HTTP `timeout=`, layering),
     `tests/test_db.py` `_INIT_DB_MIGRATED_COLUMNS` (new `library.db` columns),
     CLI/app parity: a setting changed in the app must match `cli.py`.
6. More than about four files to change? Stop. Split the task and say so.
7. Make the smallest change. Run the matching test file first:
   `python -m pytest -q tests/test_<area>.py`. Then the full suite:
   `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`.
8. Rules that bite most:
   - Match LLM results to lines by id, never by position.
   - Pass error text through `translate_engines.redact_secrets`; keys in headers only.
   - Every HTTP call has `timeout=`.
   - Jobs save only their fields: `db.save_lines(id, lines, fields=("en",))`.
   - Every route declares exactly one permission.
   - Never grow a file on the size allowlist.
