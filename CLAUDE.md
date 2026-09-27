# Baihe Subtitler — rules for AI sessions working in this repo

This app is being brought in line with a roadmap written in a separate
planning session. Read the roadmap before starting any step:

```
git fetch origin claude/baihe-subtitle-planning-95qyvq
git show FETCH_HEAD:docs/baihe-roadmap.md
```

It lists every step in build order, what each one changes, and its exit
condition. If you were told to "do Step X", that's Step X in this doc.

## Current work

- **User (Airbear):** Setting up local repo, integrating local files
- **Claude:** Ready to assist with roadmap steps

Update this section when starting or finishing work to prevent overlaps.

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

## If you were spawned directly by the planning session

If your very first message told you to "Build Step X from the roadmap" and
nothing else, you were most likely created directly by the planning
session (via its own `create_session` call), not started by the user
typing into a fresh chat. This carries standing authorization, confirmed
directly with the user (2026-09-27), for the ordinary gated-mode workflow
this file already describes: build on your own branch, run the tests, push,
and stop. **You do not need to separately ask the user's permission before
running a normal build/test/push action** — that authorization already
covers it, the same way it would if the user had typed the handoff into
this chat themselves. If your session's own permission settings still
prompt you before an action, that's a session-configuration detail (the
planning session may not always remember to request the more permissive
mode when creating you) — not a sign you need to seek separate approval;
answer the prompt and continue, don't stop the whole step over it. This
does **not** extend authorization beyond the ordinary workflow above — a
genuinely destructive or irreversible action, or anything outside what
this file's own gated-mode section already permits, still needs the
user's explicit go-ahead the normal way.

You can also tell if you were planning-session-spawned by checking whether
your session has a `parent_session_id` pointing back to it (visible in your
own session metadata) — if you ever need to report something proactively
before your step is finished (a genuine blocker, a finding worth flagging
immediately rather than waiting), you can reach the planning session
directly by that id rather than only ever waiting passively to be asked.

## Structural UI redesign steps: capture real before/after screenshots

**Narrowed on purpose (2026-09-27), after weighing this directly** — this
does NOT apply to every UI-touching step. A screenshot only proves one
static state renders; it doesn't catch the harder bugs (session-state
leaking across dramas, a value only wrong after switching lines) that
real code review already has to catch anyway, and starting a real
Streamlit server + headless browser costs real time on every step it
runs on. For a small, localized UI tweak (a relabeled button, a
moved caption, a tightened layout), the existing manual-check text is
enough — don't add browser automation just for that.

**Reserve this for structural redesign steps specifically** — the
Steps-13-through-18-scale rebuilds that replace a tab's whole layout
(stage tabs instead of an expander scroll, a whole section folded into a
popover, tabs merged together). For one of those: run the app in a real
browser and capture before/after screenshots, not just a text
description — a screenshot is what actually lets the planning session
and the user confirm a whole-layout change looks right, the same way
Step 14's own review needed a live browser probe to confirm `st.popover`
actually behaved correctly across a rerun. Chromium is pre-installed in a
cloud session (Playwright is already configured to find it); start the
app (`streamlit run app.py` or the project's own launch command), drive
it with Playwright to the affected screen, and save a screenshot before
your change (on the branch's base commit, or from a stashed diff) and
after (on your finished branch). Include both images (or their saved
paths, if this environment doesn't let you attach images directly) in
your step's finish-up summary, next to the manual-check note. If a real
browser genuinely isn't available in your environment, say so explicitly
in the summary rather than skipping this silently — same as this
project's standing rule for any other manual check that can't be run
from here. When in doubt whether your step counts as "structural," treat
it as not requiring this and let the planning session ask for a
screenshot specifically if it wants one.

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
- **Diarization (pyannote) needs a real, gated-access-accepted Hugging
  Face token to run outside the mocked test suite.**
  `pyannote/speaker-diarization-community-1` is a gated model — an
  `HF_TOKEN` alone isn't enough; the account it belongs to also has to
  have accepted that specific model's license on huggingface.co first
  (`diagnostics.check_pyannote_gated_access` is what checks this in-app;
  the CLI's own `--hf-token`/`HF_TOKEN`/`BAIHE_HF_TOKEN`, `cli.py:186-188`,
  and the UI's `settings_hf_token` both need the same accepted token). If
  a step touches diarization and needs a real (not mocked) run to verify
  — a real audio file, not `tests/`'s fake model classes — **ask the user
  for a real, gated-access-accepted token up front** rather than
  discovering the gap from a failed run.

## Background tasks — avoid stuck monitor loops

Multiple `while pgrep ...; do sleep N; done` loops have been left running
for hours (in one case 425+ minutes) after the thing they were waiting on
had already finished. Root cause, confirmed directly: a loop shaped like
`while pgrep -f "python run_tests.py" >/dev/null; do sleep 15; done; echo
DONE` **matches its own command line** — the bash process running the
loop has "python run_tests.py" sitting right there in its own `-c` string
(in the `pgrep` pattern and/or a later `echo`/`tail` line), so `pgrep -f`
finds it and the loop never sees "no process found," even after the real
test run exited. It then loops forever, silently.

- **Capture the PID once, don't re-search by pattern every iteration.**
  Launch the real command, save `$!` immediately, and poll that exact PID
  (`while kill -0 "$PID" 2>/dev/null; do sleep N; done`) — never
  `pgrep -f` a pattern that could also match the polling loop's own
  command text.
- **If you must `pgrep -f` a pattern, make sure the loop's own command
  line can't contain that same substring** (e.g. don't `echo` or `tail`
  a message that repeats the process name you're grepping for in the
  same `bash -c` string).
- **Don't stack a second monitor loop for the same wait.** Check running
  background tasks before starting another "wait for tests to finish"
  loop — several redundant ones for the same suite run is a sign
  something already went wrong, not a reason to add one more.
- **Before starting a new long-running background task, glance at
  already-running ones for anything that's been going far longer than
  the operation it's waiting on should take** (a full suite run is a few
  minutes; a loop still going after 20+ is stuck, not slow) and stop it
  with `TaskStop` rather than leaving it to accumulate.

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
