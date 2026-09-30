# Testing, verification and CI

Owner of the project's testing and CI guidance. Shared principles and precedence: `docs/engineering-standards.md`.
Role files (`.claude/agents/*.md`) link here instead of restating it.

## Risk-based verification tiers

1. **While iterating:** run the test file or selection for what you touched (`python run_tests.py <path>` or `-k`; `run_tests.py` forwards arguments to pytest).
2. **When a change crosses a shared module** (database layer, `background_jobs`, `translate_engines`, API schemas, `services/`): also run the relevant subsystem tests.
3. **At the integration boundary** (before handing work back or merging): run the full suite, `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`, on the integrated result; for the frontend also `npm run lint`, `npm test`, `npm run build` and `npm run e2e` from `frontend/`.
   `npm run e2e` runs two Playwright projects: `desktop` (every spec except `e2e/mobile.spec.ts`) and `phone` (390x844, touch, only `e2e/mobile.spec.ts`: no sideways scroll and 44px nav links, stage tabs and primary buttons on Library, each workspace stage, Settings and Diagnostics). `--project=phone` runs just the phone checks. With a preinstalled Chromium set `PLAYWRIGHT_CHROMIUM_PATH` (e.g. `/opt/pw-browsers/chromium`); `E2E_API_PORT`/`E2E_WEB_PORT` move the servers off the default 8611/4174.
4. Do not re-run an identical check on an unchanged tree without a reason. Never weaken, skip or narrow a required check to save time or CI minutes.
5. A failure is not "environmental" or "flaky" until the mechanism is confirmed (a real background thread left running by an earlier test was the cause of the "database is locked" errors fixed in PR #259).
6. Tests must not leave real job threads or subprocesses running: wait for them or mock them.

## Current merge gate (2026-09-30)

GitHub Actions minutes work while the repo is public (until about 2026-10-03), so CI is the merge gate. If minutes run out later,
the local full suite (and the local frontend checks) is the gate. Never skip or weaken a test.

The sections below were moved here unchanged from the root `CLAUDE.md` on 2026-09-29. Measurements and workflow numbers in them are dated snapshots:
they cannot currently be re-derived from Actions run history, so re-verify before relying on them.

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
  touches the database or `db.LIBRARY_DIR`, so each test gets a fresh
  library. Underneath that, `tests/conftest.py` points the whole run at a
  temp library before any test module loads, and fails a test that opens
  an absolute path in the real `library/` folder
  (`tests/test_library_isolation_guard.py`). The guard is a backstop, not a
  sandbox: it can't see sqlite `file:` URI connects, relative paths or
  writes from a subprocess, and a hit in a module- or class-scoped fixture
  is cleared by the next test's setup. A repo-wide `os.walk` must skip
  `library`.
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

### Testing a dependency upgrade (canary)

Diagnostics' "Test first" checks an upgrade from inside the app (it has no
rollback: it only reports). The same idea from a source checkout, for any
package, is `scripts/dependency_canary.py` (standard library only):

    python scripts/dependency_canary.py <package> [<version>|latest] [--quick] [--with-optional] [--write-pin]

It installs `requirements-core.txt` (plus `requirements-optional.txt` with
`--with-optional`) under `constraints.txt` into a throwaway venv, upgrades only
that package, and runs the offline suite (`--quick`: `tests/test_static_analysis.py`
plus tests whose file name or imports mention the package). No API keys are
passed to it and it uses a temp data folder. Verdicts: PASS (exit 0), FAIL (1),
ERROR (2), PREEXISTING (3, the same tests fail on the known-good version).

Manual steps that stay with you:

- On FAIL, `--write-pin` appends `pkg<failing-version` to `constraints.txt`
  (only when the failing version is newer than the known-good one). Review and
  commit it yourself.
- The installer lock `installer/wheels.lock.txt` is never edited; refresh it
  separately (docs/windows-installer-design.md).
- If you already upgraded your real environment, pin it back with the printed
  command, e.g. `pip install "pkg==<last good>"`. The script never touches it.

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
