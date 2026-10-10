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
   `python -m pytest -q tests/test_<area>.py`. Then the quick guards:
   `python -m pytest -q tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py`.
   CI runs the full suite; don't run it locally.
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
Tests: `python -m pytest -q <files>`, then the quick guards (CI runs the full suite)
Done when: <tests pass with counts; for a split, AGENTS.md "Splitting files">

## Using a local coding agent (Ollama + OpenCode or Aider)

Work in a second clone, never in the checkout that runs the app: a test run or a bad edit then can't touch
the running app or its `library/` data.

Setup (Python 3.12 and Node 22, as in CI; the CI jobs are in `.github/workflows/tests.yml`):
```
git clone <repo url> baihe-dev && cd baihe-dev && git checkout baihe-subtitler
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-core.txt pytest pytest-xdist httpx -c constraints.txt
cd frontend && npm ci && cd ..
```
Tests are mocked, so no GPU, models or keys are needed. Optional libraries missing from the venv skip their tests.

Test commands (run from the repo root unless noted):
- One area: `python -m pytest -q tests/test_<area>.py` (areas: `docs/testing-and-ci.md`).
- Quick guards before pushing: `python -m pytest -q tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py`. CI runs the full suite.
- Frontend, from `frontend/`: `npx tsc --noEmit && npx vitest run`.
- Playwright (`frontend/e2e/`) only for a UI flow, with the Chromium already on the machine
  (`docs/testing-and-ci.md`). Never run `playwright install`.

Ollama's default context is small and it silently drops what doesn't fit (4k below 24 GiB VRAM; Ollama's docs
recommend at least 64k for coding tools). The agent's rules and tool output are lost without an error, so
raise it first:
```
OLLAMA_CONTEXT_LENGTH=65536 ollama serve     # Windows PowerShell: $env:OLLAMA_CONTEXT_LENGTH=65536; ollama serve
ollama pull <coder model>                    # e.g. a Qwen3-Coder 30B-A3B tag; check the tag on ollama.com
ollama ps                                    # CONTEXT must read 65536; PROCESSOR should say 100% GPU
```
Keep the model fully on the GPU where you can: a split with the CPU is much slower.

OpenCode: this repo's `opencode.json` points at llama-server. For Ollama use this in `opencode.json`
in the dev clone (the model id must equal the Ollama tag; `limit` tells OpenCode when to compact):
```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/<tag>",
  "provider": { "ollama": {
    "npm": "@ai-sdk/openai-compatible", "name": "Ollama (local)",
    "options": { "baseURL": "http://localhost:11434/v1" },
    "models": { "<tag>": { "name": "local coder", "limit": { "context": 65536, "output": 8192 } } }
  } }
}
```
OpenCode reads `AGENTS.md`, which tells it to read `CLAUDE.md`.

Aider (does not read `AGENTS.md`; give it the rules explicitly). `.aider.conf.yml` in the clone:
```yaml
model: ollama_chat/<tag>
read: [CLAUDE.md, AGENTS.md, docs/small-model-checklist.md]
auto-commits: false
test-cmd: python -m pytest -q -p no:cacheprovider -o addopts="" tests/test_<area>.py
```
and `.aider.model.settings.yml` (aider otherwise sizes `num_ctx` per request):
```yaml
- name: ollama_chat/<tag>
  extra_params:
    num_ctx: 65536
```
Set `OLLAMA_API_BASE=http://127.0.0.1:11434` if Ollama isn't on the default address. The three `read` files cost
about 5.5k tokens; at 16k to 32k context list only `CLAUDE.md` and this file.

Branch and PR flow: one task per branch off the latest `baihe-subtitler`
(`git fetch origin && git checkout -b <task-name> origin/baihe-subtitler`; roadmap steps use `step-<id>-<short-name>`).
Run the area test, then the quick guards, and the frontend commands if you touched `frontend/`. Push and open a
draft PR into `baihe-subtitler`; the owner merges when CI is green. Finish with a short summary: what
changed, commands run with pass counts, what you're unsure about.

Stop and ask the owner before touching:
- auth, sessions, permissions policy or remote access (`api/auth.py`, `action_tiers.py`, `docs/route-permissions.md`
  policy, anything that widens who can call a route);
- API keys, secrets, redaction, `.env`, or anything that logs, stores or returns error text from an outbound call;
- `db.py` splits, table rebuilds or any migration that isn't a plain `ADD COLUMN`;
- `background_jobs.py`, process-job workers, locks, threads, GPU slots;
- a test you want to skip, delete or loosen, or an `OVERSIZED_*` allowlist number you want to raise;
- a change needing more than about four files.

Tool facts checked against current docs (2026-10): OpenCode's Ollama provider block and `limit.context`/`limit.output`;
Ollama's `OLLAMA_CONTEXT_LENGTH`, `ollama ps` and 4k default; aider's `ollama_chat/` prefix, `OLLAMA_API_BASE`,
`.aider.model.settings.yml` `extra_params.num_ctx`, and the `read`, `auto-commits`, `test-cmd` keys. Not checked: the
Qwen model tags (names change; look them up on ollama.com), OpenCode's `~/.config` global file, and how well any
given model follows tool calls; if tool calls fail, raise the context first.

## Prompting a 64k local model

A 64k window holds the rules (about 5k tokens), the task and a few files; long sessions fill it with old tool output
and the model starts forgetting the rules. These habits keep it fresh.

- **One task per session, fresh context.** Finish, commit, start a new session for the next task. Don't "continue"
  a long session; paste the brief again.
- **Fixed task template.** Paste this, filled in (the shorter `Task brief` above is the minimum):
  ```
  Goal: <1-2 sentences: the symptom or the change>
  Files: <exact paths and function names; edit only these>
  Test: python -m pytest -q tests/test_<area>.py   (write the failing test first)
  Rules that apply: <3-6 from CLAUDE.md, e.g. "every HTTP call has timeout=">
  Do not touch any other file. Stop and tell me if you need to (more than ~4 files, or anything on the stop list).
  ```
- **Find code, don't browse.** `git grep -n "<exact UI text>"`, `python tools/repo_map.py --find "<word>"`, then read
  by line range (`Read file offset=120 limit=60`), never a whole file over a few hundred lines.
- **Test-first loop.** Write the failing test, run it, make the smallest change, run again. When pasting a failure
  back, paste only the last 30 lines (`... 2>&1 | tail -30`), not the whole run.
- **Edit blocks, not rewrites.** Ask for search/replace edits (OpenCode's edit tool, Aider's `diff` format) with
  enough surrounding lines to be unique. A whole-file rewrite burns context and silently drops code.
- **Thinking off for edits.** Reasoning tokens eat the window and add nothing to a one-function change. Leave
  thinking on only to plan a task you haven't scoped yet, and do that in a separate session.
- **Set the context explicitly.** Ollama's default is 4096 tokens. Set `OLLAMA_CONTEXT_LENGTH=65536` (or `num_ctx`
  in Aider's `extra_params`, or `/set parameter num_ctx 65536` in `ollama run`). To fit more on the card, start the
  server with `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` (allowed values `f16`, `q8_0`, `q4_0`;
  the cache type is global and only helps with flash attention). Check with `ollama ps`. Names checked against
  docs.ollama.com/faq on 2026-10-08; a quantised cache can lower quality, so keep `q8_0` before trying `q4_0`.
- **Commit often, review the diff yourself.** `git diff` after every task: look for files you didn't name, deleted
  tests, loosened asserts, and comments naming a PR or Step. A local model that "fixes" a test by editing it is wrong.
- **Stop and ask** for the list in "Stop and ask the owner" above: auth and remote access, `db.py` splits and
  migrations, concurrency, keys and error text, frozen files (size allowlists), skipped or loosened tests.

Example (a real small fix: `docs/STATUS.md` says the CPU Whisper fallback is still `medium`, but
`transcribe_service.default_whisper_size()` returns the same turbo default on CPU and GPU):
```
Goal: docs/STATUS.md, "Where the app is" > "Transcription and models", says the CPU fallback is still `medium` and that
the label says turbo is weaker on Japanese and Korean. The code now uses large-v3-turbo on CPU too, and the label is
the per-language note in whisperModelWarning. Make that sentence true.
Files: docs/STATUS.md only. Read `default_whisper_size` in services/transcribe_service.py and `whisperModelWarning` in frontend/src/pages/workspace/sourceForm.ts first.
Test: python -m pytest -q tests/test_file_organization.py tests/test_agent_docs.py
Rules: docs say what is true now; keep the edit to that one sentence; no PR or Step ids in code comments.
Do not touch any other file. Stop and tell me if the code says something different from what I described.
```
A good result: a one-sentence diff in one file, still citing `#730`, the two tests pass with counts shown, and the
final message says what the code lines showed. A bad result: other STATUS lines "tidied", or a new section.
