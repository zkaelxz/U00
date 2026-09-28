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

Kept current by the planning session and by implementing sessions
themselves — update it when you start or finish a step, so a session
picking up next (human or AI) can see what's already in flight without
re-deriving it from git state. This is a live coordination board, not
part of the roadmap's own tracked history — the roadmap's own NEXT
pointer and status table remain the source of truth for what's actually
merged.

- **User (Kae):** exploring bringing Codex in alongside Claude to split
  work — flag anything that's a good isolated candidate for it, or a
  merged PR worth an independent second review pass.
- **Planning session:** reviewing/merging PRs as they land, keeping the
  roadmap in sync.
- **Streamlit-to-React/FastAPI migration:** status, recipe, decisions and queue are in
  `docs/migration-handoff.md` (merge helpers in `scripts/migration/`). Read it first.
  Frontend (React) phase plan: `docs/migration-frontend-plan.md`.
- See the roadmap's own NEXT pointer for exactly which steps are
  currently sent/in-progress/ready — don't duplicate that list here, it
  goes stale faster than the roadmap does.

## How to work

- **One step, one branch, off the latest `baihe-subtitler` — with one
  explicit exception: a batch of steps the planning session's handoff
  names together** (e.g. "Build Steps 50, 51, 52 from the roadmap, in one
  session"). Only batch when the handoff says to; don't decide on your own
  to fold an unrequested step into the one you were given. See "Coding-
  session efficiency" below for what makes steps batchable and how to keep
  them separately reviewable even when built together.
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

## Lead session: delegating to subagents

The session the user talks to is the **lead orchestrator**. The delegation
policy lives in `.claude/CLAUDE.md` (it complements this file; this file
and the roadmap win where they overlap) and the project agents live in
`.claude/agents/`:

- `codebase-analyst`: read-only map of current behavior, data flow, tests
- `migration-architect`: read-only migration map and thin-slice plan
- `roadmap-planner`: read-only step analysis; give it the roadmap text or
  a readable path, since it has no shell to fetch the planning branch
- `implementer`: edits only the files it is assigned; no git writes
- `code-reviewer`: read-only review; give it the diff, base commit, and
  changed-file list (inline if small, a saved patch file if large)
- `qa-runner`: runs the assigned checks and diagnoses failures; no edits

In short: delegate substantial research, planning, review, and QA; run
independent tasks concurrently in the background; assign file ownership
before any parallel edits (one writer per file); brief each agent
completely; keep tiny tasks, tightly coupled changes, and integration in
the lead; report agent status, verify results, and review the final diff.
Delegation never widens the user's requested scope.

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
- **Fresh environment:** `pytest` itself isn't preinstalled — `pip install
  -r requirements.txt` covers it (it's listed there), or install it plus
  `requirements-core.txt` directly if you're skipping the heavy optional
  extras.
- **Known false-failure gotcha:** if `tests/test_sources_mangaz.py`'s RSA
  tests fail with `pyo3_runtime.PanicException: Python API call failed` /
  `ModuleNotFoundError: No module named '_cffi_backend'`, that's a missing
  `cffi` package (a runtime dependency of `cryptography` that a
  system/apt-installed `cryptography` doesn't always pull in via pip), not
  an app bug — `pip install cffi` fixes it. Confirmed by reproducing the
  failure, installing `cffi`, and seeing all 20 tests in that file pass.
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

## GitHub Actions minutes

- **Before a discretionary CI run** (re-running a job on a hunch, a
  speculative push just to see what happens), check the org's remaining
  monthly Actions minutes allowance and its reset date first. GitHub's
  own billing usage page is the source of truth, not an estimate from
  workflow-file line counts — different runner types (Linux/Windows/
  macOS) bill minutes at different multipliers, so the same job costs a
  different amount depending on where it runs.
- **Iterate locally, reserve Actions for what actually gates a merge.**
  Run focused/relevant tests locally (per the Tests section above and
  the project's own test-cadence agreement) while working on a change;
  push to trigger CI once for the PR/merge-required checks, not once
  per intermediate edit.
- **Avoid duplicate runs**: batch related commits into one push rather
  than pushing each small edit separately. Both workflows also cancel
  their own superseded runs automatically (`concurrency:` with
  `cancel-in-progress: true`, on `${{ github.workflow }}-${{
  github.ref }}`) — checked safe for this repo specifically because
  neither `tests.yml` nor `windows-bootstrap.yml` does anything
  irreversible mid-run (no deployment, no publish step); re-check that's
  still true before adding a workflow that does, and don't rely on
  auto-cancellation for a run whose own completion something else
  depends on (a release, a required deployment gate) — those need it
  turned off for that job, not just assumed safe.
- **Never weaken a check to save minutes.** Skipping, shortening, or
  narrowing a required check or real test coverage to cut CI cost is
  not an acceptable trade — minutes are cheaper than a regression a
  weakened check would have caught.
- **Set each job's `timeout-minutes` from its own real observed
  duration, not a guess.** `test`/`frontend` (`tests.yml`) and
  `bootstrap` (`windows-bootstrap.yml`) already carry one, sized from
  actual GitHub Actions run history (roughly 2-3x the slowest observed
  run, not a round number picked on instinct) — re-derive it the same
  way (`actions_list`/`actions_get`'s workflow-run and workflow-job
  methods, not the workflow file's own step count) if runtime shifts
  meaningfully, rather than leaving a stale number or widening it
  without checking first.
- **Job grouping (merging jobs to cut per-job startup overhead) is not
  worth reviewing at this repo's current job count** (2 in `tests.yml`,
  1 in `windows-bootstrap.yml`) — each already does one coherent thing,
  and merging any of them would cost the failure-isolation and
  parallelism a split gives, for a few seconds of saved startup at most.
  Revisit only if CI actually grows several more small jobs, and
  benchmark the real before/after duration then — don't merge jobs on
  a hunch that it should be faster.
- **Caching a dependency install is only worth it if the measured
  install time clearly exceeds the cache's own restore+save overhead.**
  Checked directly for this repo (2026-09-28): `tests.yml`'s Python
  installs (`pip install -r requirements-core.txt ...`) measured ~20-26
  seconds across several real runs — short enough that a cache's own
  round-trip overhead likely meets or exceeds what it would save, so
  none was added. `frontend`'s `npm ci` already caches via
  `actions/setup-node`'s built-in `cache: npm` and stays that way. If a
  requirements file grows enough to push the Python install past
  roughly a minute, re-measure before adding `actions/cache`/
  `setup-python`'s own `cache: pip` — don't add one on the assumption
  that caching is free.
- **Before changing triggers, matrices, concurrency limits, or caching**
  in a workflow file, inspect which jobs are actually consuming the
  minutes (the billing usage page's own per-workflow/per-job breakdown,
  not a guess) and state the real trade-off being made — what coverage
  or turnaround time is given up for what minutes saved — before making
  the change.

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
  A full sync (`db.save_lines(..., fields=None)`, which replaces every
  line for a drama) can race another job or the user's own edits. A
  background job should scope its own writes to the specific fields it
  owns (`db.save_lines(..., fields=("en",))`, `fields=("flag",
  "flag_note")`, etc.) rather than doing a full sync — Step 2 (permanent
  line IDs) made this the real guard, replacing the older
  `any_line_writing_job`/`other_line_writing_job` collision check, which
  no longer exists in `background_jobs.py`. A field-scoped write only
  updates rows that still exist and never inserts/deletes, so it can't
  resurrect a line the user merged away or delete one they just added.
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
- **Update `FILE_ORGANIZATION.md` in the same step/PR that adds a new
  top-level module or a new `tabs/*.py` file.** This doc is the map an
  AI session (or a person) uses to understand the app's shape without
  reading every file first — including, eventually, an AI session
  working through an API/local model rather than a paid coding-agent
  subscription, which won't have this conversation's accumulated
  context to fall back on. It drifted badly once already (Step 56,
  2026-09-27: listed 7 tabs when there were 10, and 6 top-level `.py`
  files when there were 58) specifically because no rule required
  keeping it current, unlike `OPTIONAL_DEPENDENCIES` above. Add your new
  file to its tree listing, in the right subsystem grouping, as part of
  finishing the step that creates it — not a separate cleanup pass.

## Coding-session efficiency

Added 2026-09-27, from an external review of this project's own governance
that the user requested and relayed. The goal isn't "use as few messages
as possible" — a fast session that ships a wrong fix is worse than a
longer one that gets it right. The goal is spending session time on
**inspect → implement → test → fix → verify**, not on re-deriving context
this file, the roadmap, or the repo itself already hand you.

1. **Prefer continuing an existing session over starting a fresh one** when
   it still has relevant context — e.g. you just finished Step 50 and were
   handed Step 51 which touches the same file, or you're the project's own
   long-running implementing session and the planning session resumed you
   rather than spawning new. A fresh session pays a real "context tax"
   (read this file, fetch the roadmap, re-orient in the repo) that a
   continuation skips.
2. **When the planning session's handoff names a batch of steps together,
   build them in one session, one at a time, each still its own commit and
   still checked against its own exit condition** — don't merge their
   diffs into one undifferentiated change. This is only safe, and only
   happens, when the planning session has already judged the steps
   closely related, same-file, compatible in scope, and not needing a
   human decision between them (see the roadmap's own working agreement
   for what "needing a decision between them" rules out, e.g. an urgent
   fix always gets its own session even if it'd technically fit a batch).
   Don't self-assemble a batch by pulling in a nearby step nobody asked
   for.
3. **Start with the smallest sufficient context.** Roadmap step → the
   files it names → the relevant functions/tests → only search wider if
   the step's own description turns out to be incomplete or wrong once
   you're in the code. Don't read an entire large file (`tabs/
   workspace_tab.py` is 2,800+ lines — see "Where things are" below) or
   sweep the whole repo when the step names exactly what it touches.
4. **Don't restate what's already written down.** This file and the
   roadmap already carry the workflow, the model-recommendation table, the
   "rules learned from real bugs," and every step's own background — your
   summary to the planning session should report what you found and did,
   not re-explain rules you were already given.
5. **Don't reopen a settled architectural or product decision** (Streamlit
   now/React only if justified, capability-based routing instead of a
   giant model router, no automatic model switching, a native Windows
   installer over Docker as the primary deploy path, etc. — see the
   roadmap's "Decisions already made" and the planning `CLAUDE.md`'s
   "Product preferences") **unless the step you're building reveals a
   concrete, specific conflict with it.** If it does, say so and flag it
   for the planning session rather than silently working around the
   decision or silently re-litigating it in your own summary.
6. **Keep handoffs short and don't expect long ones.** "Build Step 4k from
   the roadmap" is normally the whole handoff, on purpose — this file and
   the roadmap already carry the rest. If a handoff you receive is much
   longer than that, it's because the step name alone was genuinely
   ambiguous or the user added something new; don't infer a standing
   requirement for longer handoffs from one example.
7. **If you finish early and another compatible, already-handed-off task
   is waiting, continuing straight into it (staying within whatever mode —
   autonomous or gated — you were already told to use for it) is fine**;
   don't idle a session that has clearly finished its assigned work and
   has more of the same kind of work queued, but also don't self-assign
   work nobody handed you.
8. **None of the above ever trades away testing or review.** Batching
   steps together still means each one passes the full suite and meets
   its own exit condition; "gated mode" still means stopping for the
   planning session's check at whatever boundary the roadmap's working
   agreement puts it at. Efficiency is about not re-deriving context that
   already exists, never about skipping verification to move faster.

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
