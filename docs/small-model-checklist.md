# Task checklist for small-context models

Context target: 64K tokens. The Qwen3.6-35B-A3B runs at 64K on the current 12 GB card (`opencode.json` context
65536 and `tools/start-local-coder.ps1` `-c 65536`), and the owner's target for the future 32 GB card is also 64K.
Search first, read little, change little. Module files stay under 40 KB (`MAX_MODULE_BYTES` in
`tests/test_static_analysis.py`; about 10K tokens), so one file fits a single read; the files over it are listed
there and shrink only.

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

## Running with a local model (Windows)

Install OpenCode once with `npm i -g opencode-ai`. Then, from PowerShell in the
repo, run `.\tools\start-local-coder.ps1` (or double-click `start-local-coder.bat`; add `-LlamaDir <dir>` if
`llama-server.exe` is not in `E:\llama`). It starts llama-server on port 8080,
waits for `/health`, and opens OpenCode's web UI in the browser (add `-Terminal` for
the terminal UI; the web UI needs the repo added once via Add project). It reads `opencode.json` and
`AGENTS.md`. The first start downloads the model.

## Task brief (paste into a local-model session)
Objective: <one line: symptom, or what moves where>
Allowed files: <paths; anything else means stop and ask>
Must not change: <behaviour, public names, API shapes, schema>
Callers: <from `git grep -nw <name>`>
Tests: `python -m pytest -q <files>`, then <wave suite or full suite>
Done when: <tests pass with counts; for a split, AGENTS.md "Splitting files">
