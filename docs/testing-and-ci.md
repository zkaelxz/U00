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

## Current merge gate

CI is the merge gate while the repo is public. If the repo becomes private or Actions minutes run out, the gate is the full local suite
(`python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`) plus the frontend commands (tier 3 above). Never skip or weaken a test.

## Tests

- Run with `python run_tests.py` (wraps `pytest`).
- **Fresh environment:** `pytest` itself isn't preinstalled — `pip install
  -r requirements.txt` covers it (it's listed there), or install it plus
  `requirements-core.txt` directly if you're skipping the heavy optional
  extras.
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
  the CLI's own `--hf-token`/`HF_TOKEN`/`BAIHE_HF_TOKEN` (`cli.py diarize`)
  and the app's saved Hugging Face token setting both need the same accepted token). If
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

- Iterate locally; push once per PR for the merge-required checks, and batch related commits into one push.
- Workflows cancel superseded runs (`concurrency:` with `cancel-in-progress: true`, grouped by workflow and ref; in `tests.yml` a push gets its own group keyed on the commit). Don't rely on that for a run something else depends on (a release).
- Never weaken a check to save minutes.
- Set each job's `timeout-minutes` from observed run durations (roughly 2-3x the slowest), not a guess.
- Before changing triggers, matrices, concurrency or caching, check the billing usage page's per-job breakdown and state the trade-off.

## Background tasks: avoid stuck monitor loops

A `while pgrep -f "<pattern>"; do sleep N; done` loop matches its own command line and never ends.

- Save the PID (`$!`) and poll that exact PID: `while kill -0 "$PID" 2>/dev/null; do sleep N; done`.
- Don't stack a second monitor for the same wait, and stop (`TaskStop`) any background task running far longer than its work should take.
