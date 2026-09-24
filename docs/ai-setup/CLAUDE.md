# Baihe Subtitler — rules for AI sessions working in this repo

This app is being brought in line with a roadmap written in a separate
planning session. Read the roadmap before starting any step:

```
git fetch origin claude/baihe-subtitle-planning-95qyvq
git show FETCH_HEAD:docs/baihe-roadmap.md
```

It lists every step in build order, what each one changes, and its exit
condition. If you were told to "do Step X", that's Step X in this doc.

## How to work

- **One step, one branch, off the latest `baihe-subtitler`.** Don't start
  the next step until the current one is reviewed and merged.
- **Branch name:** `step-<id>-<short-name>`, where `<id>` is the roadmap's
  own step id exactly as it appears there (`1b`, `1c-pre`, `6b`, `9b`, ...)
  and `<short-name>` is a few lowercase hyphenated words describing the
  step, e.g. `step-2-permanent-line-ids`, `step-6b-export-formats`. Earlier
  branches (`claude/r5-translation-fixes`, `step-1b-safety-fixes`) predate
  this convention — don't rename those, just follow it going forward. The
  point is that the roadmap's status table and branch names always match
  at a glance.
- **Keep changes minimal.** Only what the step's roadmap entry asks for —
  no extra refactors, no new features, no dependency upgrades beyond what
  the step names.
- **Before starting a step, check the roadmap's §4 "Model recommendation
  per step" table.** If the step you're about to start is listed there,
  stop and ask the user to confirm they've switched this chat to Opus
  before you write any code for it — then wait for that confirmation.
  Every other step is fine on whatever model the chat is already on.
- **Re-verify before fixing.** The roadmap was written by reading the code
  at a point in time; re-read the files it names and confirm the problem
  still exists as described before changing anything. If the code has
  moved on or the roadmap is wrong, say so instead of forcing a fix that
  no longer applies.
- **When you finish a step:** run the full suite (`python run_tests.py`),
  push the branch, and give a short plain-English summary — what changed,
  what the user will notice in the app, anything you're unsure about —
  then **stop**. Don't create a pull request yet.
- **Pull requests:** only when the user explicitly says "create a PR for
  this step" (that means the planning session has already reviewed and
  approved the branch). Open it into `baihe-subtitler`. Don't merge it
  yourself.

## Tests

- Run with `python run_tests.py` (wraps `pytest`).
- Tests are mocked throughout: fake model classes, no GPU, no real models,
  no network calls to AI services. Follow that pattern for new tests —
  don't add a test that needs a real API key, a GPU, or a downloaded
  model.
- Use the `isolated_db` fixture (`tests/conftest.py`) for anything that
  touches the database or `db.LIBRARY_DIR`, so tests never touch a real
  library folder.
- A test that needs an optional library (`jieba`, `pytesseract`, `cv2`,
  `paddleocr`, ...) should `pytest.importorskip` it, not hard-import it,
  so a core-only install still gets a clean run.

## Rules learned from real bugs — don't reintroduce these

- **Never match AI results back to lines by list position.** An LLM
  response that's short, reordered, or has an extra item will silently
  misassign a result to the wrong line if you `zip()` it back by
  position. Match by an explicit id instead (see
  `translate_engines._request_translations_with_retry` /
  `_parse_id_keyed_json` for the pattern this app uses).
- **Never put an API key in a URL, log line, or stored error message.**
  Send keys as headers. Anything that looks like a key or token must be
  stripped before an error is shown, stored, or logged — see
  `translate_engines.redact_secrets`.
- **Every `requests.post`/`.get` needs a `timeout=`.** A hung server
  should never leave a background job stuck at "running" forever.
  `tests/test_static_analysis.py` checks this statically for
  `translate_engines.py` and `qa.py` — extend that check if you add HTTP
  calls elsewhere.
- **A background job must not silently overwrite another job's work.**
  Jobs that call `db.save_lines()` (which replaces every line for a
  drama) can race each other and race the user's own edits. Check
  `background_jobs.any_line_writing_job(drama_id)` /
  `other_line_writing_job(...)` before starting or allowing a new one —
  this is a short-term guard until Step 2 (permanent line IDs) lets each
  job write only the fields it owns.
- **`db.save_lines` deletes and re-inserts every line for a drama.** Any
  code building the `Line` list to pass to it must carry through
  `flag`/`flag_note`/`speaker` explicitly, or those fields silently get
  wiped. (This exact bug hit `cli.cmd_dub` — see git history if you want
  the details.)
- **CLI/UI parity matters.** `cli.py`'s commands are meant to do the same
  thing as their Workspace-tab equivalent (same glossary, style
  guidelines, locale, character names). When you touch one, check the
  other.

## Where things are

- `docs/baihe-roadmap.md` does not live in this repo yet — it's on the
  planning branch (see the fetch command above) until the roadmap's final
  step, which copies it in.
- `tabs/workspace_tab.py` is large (2,800+ lines) and holds most of the
  UI and orchestration for a single drama. Read only the section you're
  changing; don't load the whole file into a plan unless the step
  actually touches multiple sections of it.
- `translate_engines.py` holds every translation/LLM-call engine and the
  shared id-keyed request/retry/redaction helpers.
- `background_jobs.py` is the in-memory job tracker (thread + dict, no
  persistence across a process restart).
- `db.py` is plain `sqlite3`, no ORM. Schema changes go through
  `ALTER TABLE ... ADD COLUMN` in `init_db`, not a migrations folder.
