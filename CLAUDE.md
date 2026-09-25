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

- **One step, one branch, off the latest `baihe-subtitler`.**
- **Check the roadmap's §4 "Working agreement" for which mode you're in.**
  Steps 1e–10 are **autonomous mode** (as of 2026-09-24, at the user's
  request): build, test, open the PR, and **merge it yourself**, then
  start the next step immediately off the updated branch — no stop to
  wait for review or a merge go-ahead. Step 11 onward goes back to the
  original **gated mode**: stop after pushing and wait for "create a PR
  for this step." If the roadmap's own working-agreement section
  disagrees with this summary, the roadmap is the source of truth — it
  may have changed since this file was last copied in.
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
- **Remove what your own change makes dead.** If the step's change leaves
  behind an unused import, a function/variable nothing calls anymore, a
  branch that can no longer be reached, or a helper that only existed for
  the code you just replaced, delete it as part of the same commit — that's
  cleanup of your own change, not an extra refactor, and it's still in
  scope under "keep changes minimal" above.
- **Flag pre-existing dead/redundant code you notice, don't silently fix
  it.** If you spot unrelated dead code, duplicated logic, or an unused
  dependency while reading files for this step, name it in the step's
  finish-up summary (file/function, why it looks dead) instead of either
  ignoring it or fixing it inline — fixing it would violate "keep changes
  minimal" and widen the diff the planning session has to review. The
  planning session decides whether it's worth its own step.
- **Default to Sonnet. Check the roadmap's §4 "Model recommendation per
  step" table before starting each step** (reversed 2026-09-26 from an
  earlier "run everything on Opus" decision — cost was higher than
  expected). If the step you're about to start is listed there, stop and
  ask the user to confirm switching to Opus for that step specifically
  before starting it, then switch back to Sonnet once it's done. If the
  roadmap's own table disagrees with this summary, the roadmap is the
  source of truth — it may have changed since this file was last copied
  in.
- **Re-verify before fixing.** The roadmap was written by reading the code
  at a point in time; re-read the files it names and confirm the problem
  still exists as described before changing anything. If the code has
  moved on or the roadmap is wrong, say so instead of forcing a fix that
  no longer applies.
- **When you finish a step, in autonomous mode (Steps 1e–10):** run the
  full suite (`python run_tests.py`), open a pull request into
  `baihe-subtitler` with a short plain-English summary (what changed,
  what the user will notice, anything you're unsure about), **merge it
  yourself**, then start the next step off the updated branch. Post the
  summary either way, but don't wait for a reply before continuing. If a
  step's own exit conditions genuinely can't be met, or something looks
  wrong, stop and say so instead of merging around it — autonomous mode
  means no one else is checking, so this is the one place to be careful
  rather than fast.
- **When you finish a step, in gated mode (Step 11 onward):** run the full
  suite, push the branch, give the same summary, then **stop**. Don't
  create a pull request yet.
- **Pull requests in gated mode:** only when the user explicitly says
  "create a PR for this step" (that means the planning session has
  already reviewed and approved the branch). Open it into
  `baihe-subtitler`. Don't merge it yourself.

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
- **Register every new optional dependency in `diagnostics.py`'s
  `OPTIONAL_DEPENDENCIES` dict, in the same step/PR that adds it.**
  Diagnostics' dependency panel, and by extension `start.bat`'s own
  "print anything missing in plain words" check (Step 10), only know
  about a package if it's in that dict. A step that adds a new `pip
  install`-able extra (an OCR backend, a TTS engine, a canvas/UI
  component, a notification library, etc.) without adding it here means
  Diagnostics silently won't report it as missing, and the launcher's
  output stays incomplete without anyone noticing.

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
