# Testing, verification and CI

Owner of the project's testing and CI guidance. Shared principles and precedence: `docs/engineering-standards.md`.
Role files (`.claude/agents/*.md`) link here instead of restating it.

## Risk-based verification tiers

1. **While iterating:** run the test file or selection for what you touched (`python run_tests.py <path>` or `-k`; `run_tests.py` forwards arguments to pytest).
2. **When a change crosses a shared module** (database layer, `background_jobs`, `translate_engines`, API schemas, `services/`): also run the relevant subsystem tests.
3. **Before pushing:** the area's tests plus the guard set ("Guard set and lean output" below; `python tools/check_change.py` prints both for your diff); for the frontend also `npx tsc -b` (`tsc --noEmit` checks nothing here: the root tsconfig only has project references) and `npx vitest run` from `frontend/`, and the one Playwright spec for a changed UI flow. CI then runs the jobs the change needs (see "Which jobs a change runs"; the full suite is `python run_tests.py -q -n auto`) on the PR; don't repeat the full suite locally. Run it locally only when CI is not the gate (below) or a reviewer asks for it: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`, and for the frontend `npm run lint`, `npm test`, `npm run build` and `npm run e2e` from `frontend/`.
   `npm run e2e` runs three Playwright projects: `desktop` (every spec except files matching `mobile.spec.ts` and the tests tagged `@catalogue`), `catalogue` (only the `@catalogue` tests in `e2e/discover.spec.ts`, against a second build with the Discover Catalogue tab switched on: that flag is compiled into the bundle, so the default build cannot show the tab; `E2E_CATALOGUE_PORT` moves its server off 4175) and `phone` (390x844, touch, every `e2e/*mobile.spec.ts` match, such as `mobile.spec.ts`, `settings.mobile.spec.ts` and `review-results-mobile.spec.ts`: no sideways scroll and 44px nav links, stage tabs and primary buttons on Library, each workspace stage, Settings and Diagnostics). `--project=phone` runs just the phone checks. With a preinstalled Chromium set `PLAYWRIGHT_CHROMIUM_PATH` (e.g. `/opt/pw-browsers/chromium`); `E2E_API_PORT`/`E2E_WEB_PORT` move the servers off the default 8611/4174.
4. Do not re-run an identical check on an unchanged tree without a reason. Never weaken, skip or narrow a required check to save time or CI minutes.
5. A failure is not "environmental" or "flaky" until the mechanism is confirmed (a real background thread left running by an earlier test was the cause of the "database is locked" errors fixed in PR #259).
6. Tests must not leave real job threads or subprocesses running: wait for them or mock them.

## Current merge gate

CI is the merge gate while the repo is public. Only if the repo becomes private or Actions minutes run out does the gate become the full local suite
(`python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`) plus the frontend commands (tier 3 above). Never skip or weaken a test.

## CI layout

`.github/workflows/tests.yml` runs these jobs on every PR, and on `merge_group` (the merge queue's temporary branch, which
`changes` treats like a push to the base: everything runs): `changes` (decides which of the others are needed), `guards`
(the five quick guard files, about a minute, so a broken rule is red first), `test-shard` (the mocked suite with xdist, minus
the five guard files that `guards` already runs, in four parallel shards; tests that failed on the shard's previous run go first
through `--ff` and a restored `.pytest_cache`), `test` (the one required check: it passes when every shard passed or the
matrix was skipped, and fails if any shard failed or `changes` did; it exists so branch protection keeps its `test` name),
`frontend` (build, vitest, lint), `e2e` (Playwright in four shards), `smoke` (see "Real-app smoke test" below; runs unless the change is
notes-only, and `test` fails if it failed) and `windows-scripts` (see "Windows script checks" below; `test`
also fails if it failed, and passes when it was skipped). `windows-bootstrap.yml` (its own workflow with one job,
`bootstrap`, path-filtered to `start.bat`, `requirements-core.txt`, `constraints.txt`, `check_setup.py`, `api/**`, `frontend/**` and itself)
and `windows-installer.yml` (job `installer`, plus `release` for a tag) cover the installer. `dependency-canary.yml` and
`dependency-audit.yml` are weekly scheduled runs, never a PR gate (see "Testing a dependency upgrade" below). The five guard files are ignored only in the workflow's `test-shard` command lines;
`python run_tests.py` and the local commands above still run everything.

Each shard prints its slowest 25 tests and its wall time in the job summary (information only, never a gate). A frontend/docs-only
change runs a single shard of the few frontend-reading tests instead of four.

### Test durations (shard balance)

`pytest-split` (pinned in the workflow, CI-only) assigns whole tests to shards from `.test_durations`, so shards finish together.
A test missing from the file is costed at the average, so a new test never breaks a run; it only makes the split a little less even.
Refresh the file when the shard wall times in the job summaries drift apart by more than a couple of minutes, or after a large
batch of new tests:

    pip install "pytest-split==0.10.0" pytest-xdist
    python -m pytest -q -n auto -p no:cacheprovider -o addopts="" --store-durations --clean-durations \
      --ignore=tests/test_static_analysis.py --ignore=tests/test_api_permissions.py \
      --ignore=tests/test_split_guards.py --ignore=tests/test_file_organization.py --ignore=tests/test_agent_docs.py

Commit the regenerated `.test_durations` in its own small PR. Do not edit it by hand.

### Caches

`actions/setup-python` caches pip (keyed on `requirements-core.txt` and `constraints.txt`), `actions/setup-node` caches npm (keyed on
`frontend/package-lock.json`), and the `e2e` job caches the Playwright browser (keyed on the Playwright version, which the lockfile
fixes). A cache miss only costs the download. The ffmpeg apt install is not cached: apt state is root-owned and the packages are small.

### Which jobs a change runs

`tools/ci_changes.py` (tested by `tests/test_ci_changes.py`) classifies the changed files of a PR. A job that is skipped
counts as passed for branch protection, as the notes-only skip always has. `guards` and `frontend` run on every PR except
notes-only ones (which run only `guards`).

The table has three columns; a fourth output, `run_windows_scripts` (the `windows-scripts` job), is true for every row that says
"everything" (a non-PR event, the `full-ci` label, a change under `.github/` or to the dependency files, an empty file list, a
classifier error), and otherwise only when a changed file is under `installer/`, is a `*.bat` or `*.ps1` file, is `check_setup.py`,
is `tools/windows_script_checks.py` or is the restore code (`services/library_restore_service.py`, `services/library_restore_sql.py`,
`db/library_folder.py`).

| Rule | `notes_only` | `run_e2e` (4 `e2e` shards) | `run_full_pytest` (`test-shard`) |
|---|---|---|---|
| Push to `baihe-subtitler`, or any event that is not a pull request (a `merge_group` included) | false | true | true |
| PR labelled `full-ci` | false | true | true |
| PR touches `.github/`, `tools/ci_changes.py`, `tests/conftest.py`, `run_tests.py`, `requirements*.txt` (top level), `constraints.txt`, `pytest.ini`, `frontend/package.json`, `frontend/package-lock.json`, `frontend/playwright.config.ts` or `frontend/vite.config.ts` | false | true | true |
| Every changed file is under `reports/`, `research_notes/` or `docs/session-harvest/` | true (only `guards` runs, plus the word scan) | false | true, but `test-shard` is skipped by `notes_only` |
| No changed file under `frontend/` or `api/` (services, db, adapters, installer, scripts, tools, tests, docs...) | as above | false | true |
| Every changed file is under `frontend/` or `docs/` | false | true if any is under `frontend/` | false: `test-shard` runs only the tests that mention `frontend` (found by `ci_changes.py --frontend-tests`) plus `TestHostedCloningRemoved` |
| Anything else | false | true | true |
| The file list is empty or the classifier errors | false | true | true |

Rows combine by "any rule that says true wins". A rename counts as touching both paths. To force everything on a PR, add the
label `full-ci` (the workflow also listens for `labeled`, so adding it starts a new full run). Anything that makes you doubt a
skipped job: add the label, or push a trivial change under `.github/`.

## Windows script checks

`tools/windows_script_checks.py` runs the real `setup.bat`, `start.bat` and `uninstall.bat` on a `windows-latest` runner, from a
copy of the checkout in a folder with a space in its name, with each prompt answered through stdin. Three subcommands, one
workflow step each:

- `setup`: `setup.bat` with winget taken off PATH, so it can only report. Checks Git and Python are reported found, nothing is
  installed, and it reaches its summary.
- `install`: `start.bat --ci` in a fresh folder creates the venv, installs `requirements-core.txt` and runs `check_setup.py`.
  Another listener holds the port, so the script stops at its "port already in use" exit, before it would launch the server (no
  server is ever started; the run is killed with `taskkill /T` after a timeout). Then the app's own `init_db` creates the library
  and a drama with a line is saved. Upgrade: the files are copied over the install, `start.bat --ci` runs again (it keeps the venv
  and installs nothing) and again with `BAIHE_FORCE_DEPS=1` (it reinstalls over the venv); after each the drama and line are read back.
  Last, a restore round trip in the installed copy: a full backup zip of that library, a title added afterwards, then the app's own
  restore (`library_backup_restore_service.restore_backup`), which on Windows takes the `msvcrt` restore lock, fsyncs the staged databases
  opened `r+b` and renames the folders. The added title is gone, the drama, line and a media file are back, and no
  `.pre_restore_*`, staging folder or `.restore-lock` file is left.
- `uninstall`: in a throwaway user profile, so the runner's own shortcuts and caches are never touched. Answering `n` to the first
  prompt removes nothing. Otherwise the venv, `model_cache\` and both shortcuts go, and the library prompt follows the typed-DELETE
  rule: `DELETE` removes `library\`; `y`, `yes`, `delete` and Enter keep it. Each with and without `BAIHE_DATA_DIR` set (its library
  is never touched, also when `DELETE` is typed and there is no library next to the script). The "Left on this PC" list must name the
  Hugging Face, PyTorch, Ollama and pip caches and the Python on PATH, and delete none of them. Every run also checks that
  `vtuber_names.json` and `vtuber_names.json.part` are removed (another `.part` file in the folder is not), and the DELETE rule extends to what a restore leaves beside the library: `DELETE` also removes
  `library.restore-lock`, `library.pre_restore_*` and `.restore_staging_*`, while `y`, `yes` and Enter keep them and the "Left on this
  PC" list names them. A reset copy (`library.pre_reset_*`) is kept and listed. With `BAIHE_DATA_DIR` set, that folder's
  library, lock, copies and `vtuber_names.json` are untouched and the list names the folder. A junction standing in for `library` or a
  copy is removed as a link and what it points to survives. Restore copies with no library folder are never deleted, and a `!` in the
  folder path works.

Needs the network only for the pip install that `windows-bootstrap.yml` already does. Not covered, still for a person on a real PC:
a real GPU or driver, winget actually installing Git, Python, ffmpeg or Node.js (the `y` path of `setup.bat`), the desktop window and
shortcut icon from `make_shortcut.bat` / `start.bat` without `--ci`, `uninstall_path_cleanup.ps1` (the PATH editing is not run),
and the Inno Setup installer and its uninstaller (`installer/baihe.iss`, which needs ISCC): `windows-installer.yml` builds and
smoke-tests that on demand, so it is not repeated here. Add a case to the script when one of these scripts gets a new prompt or branch.

## Real-app smoke test

`python -m tools.smoke_test` (the `smoke` job in `tests.yml`) starts the real server (`python -m api`, no mocks) on a throwaway data
folder and drives it over HTTP, so a failure that only a running app shows (an import that breaks at startup, a job stuck in
"running", a restore that loses data, a route left open) is red on the PR. Steps, in order: create a title (Developer Mode on, which is
what lists the Echo engine); transcribe `assets/smoke/speech-zh.wav` with the tiny Whisper model on the CPU; translate with Echo
(a free engine that returns `[EN] <text>`, no key or model); edit a line and export SRT; back up the library, change the line, restore
and check the edit came back; start a second transcription and cancel it; then stop the server, add a member to the library, restart
with `BAIHE_API_AUTH=on` and check a signed-out request gets 401, the member can read jobs, is refused two `admin.library` routes
(403) and is refused a write without the CSRF token. It asserts that transcription produced lines, not what they say: tiny Whisper on
a synthetic voice is not a quality check. The only download is `Systran/faster-whisper-tiny` (about 75 MB), cached in CI by the hash
of `model_pins.py`; it needs `faster-whisper` and ffmpeg. About 25 s on a warm machine; the CI step is limited to 5 minutes. A red
smoke blocks the `v0.1.0` tag (owner decision 2026-10-13). `--keep` leaves the data folder and server logs for a look. On failure
it prints the last 40 lines of the server log.

`assets/smoke/speech-zh.wav` (10 s, mono, 16 kHz) was made with
`espeak-ng -v cmn -s 130 "你好，今天天气很好。我们一起去公园散步吧。"` and converted with `ffmpeg -ac 1 -ar 16000 -c:a pcm_s16le`;
it is synthetic and carries no third-party content. `python -m tools.self_test` also runs a "Library round trip" check
(save a line and export SRT in a throwaway library; the real one is not opened).

## Guard set and lean output

The guard set is what to run before pushing, next to the area's tests. CI's `guards` job runs the first five; the rest are
tests that CI's shards run and that a short list tends to miss (`tests/test_diagnostics_and_export.py` is the only test of
`expected_files.py`, so a new top-level `.py` fails there; `tests/test_db.py` holds `_INIT_DB_MIGRATED_COLUMNS`):

    tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py
    tests/test_agent_docs.py tests/test_settings_schema.py tests/test_settings_parity.py tests/test_frontend_limit_parity.py
    tests/test_db.py tests/test_diagnostics_and_export.py

`pytest.ini` sets `-v`, so a passing run prints hundreds of lines. The lean form prints a few hundred bytes and is what
`tools/start-local-coder.ps1` exports as `PYTEST_ADDOPTS` for the local model:

    python -m pytest -q -o addopts= -W ignore::DeprecationWarning -W ignore::SyntaxWarning --no-header -x --tb=short <files>

Keep a single `-q` (`-q -q` drops the "N passed" line). Never use a blanket `-W ignore` or `-p no:warnings`: they also switch off
`pytest.ini`'s `error::PytestUnhandledThreadExceptionWarning`, and a test with a crashing thread then passes
(`tests/test_local_coder_tools.py` shows both). `-v` stays in `pytest.ini` so CI logs show each test name.

Output flooding to avoid: `python tools/route_table.py` with no flag prints the whole 36 KB table (use `--write`, then
`git diff`), bare `npx oxlint` prints about 32 KB (pass one file), and `.test_durations` is marked `-diff` in `.gitattributes`
so `git grep` shows one "Binary file" line for it.

## Focused checks per area

Area names match the "Where to look" table in `AGENTS.md`. Run the pytest line while iterating, the vitest line for a
frontend change, and the Playwright spec only for a UI flow. Run from the repo root for pytest and from `frontend/` for the
other two (`npx vitest run <paths>`, `npx playwright test <spec>`; Playwright needs `PLAYWRIGHT_CHROMIUM_PATH`, see tier 3).
A folder path runs every test under it. Add `tests/test_api_permissions.py` whenever you add or change a route.

| Area | pytest (`python -m pytest -q ...`) | vitest (`npx vitest run ...`) | Playwright (`npx playwright test ...`) |
|---|---|---|---|
| Library / titles | `tests/test_library_service.py tests/test_drama_service.py tests/test_api_dramas.py tests/test_title_library.py` | `src/api/library.test.ts src/pages/libraryForm.test.ts src/pages/libraryParity` | `e2e/library.spec.ts` |
| Transcription (ASR) | `tests/test_transcribe_service.py tests/test_asr_backend.py tests/test_diarize.py tests/test_segment.py tests/test_compare_transcription.py` | `src/api/asrOptions.test.ts` | `e2e/transcribe-card.spec.ts` |
| Translation + engines | `tests/test_translate_engines.py tests/test_translate_run_service.py tests/test_bulk_translate.py tests/test_engine_routing.py` | `src/api/translate.test.ts src/pages/translateFile.test.ts src/pages/workspace/translateForm.test.ts` | `e2e/translate.spec.ts` |
| Review / lines | `tests/test_lines_service.py tests/test_review_lines_service.py tests/test_api_lines.py` | `src/api/review.test.ts src/pages/workspace/stages/review` | `e2e/review-stage.spec.ts` |
| Characters / glossary | `tests/test_characters_service.py tests/test_glossary_service.py tests/test_api_glossary.py` | `src/api/characters.test.ts src/pages/workspace/stages/glossaryExtract.test.ts` | `e2e/characters-rename.spec.ts` |
| Live translate | `tests/test_live_service.py tests/test_live_translate.py tests/test_live_fetch.py tests/test_api_live.py` | `src/api/live.test.ts src/pages/live` | `e2e/live.spec.ts` |
| Dubbing | `tests/test_dub.py tests/test_dub_service.py tests/test_api_dub.py tests/test_voice_id.py` | `src/api/dub.test.ts src/pages/workspace/stages/dubForm.test.ts` | `e2e/dub-stage.spec.ts` |
| Scanlate / OCR | `tests/test_scanlate.py tests/test_ocr.py tests/test_hardsub_ocr.py tests/test_api_comic_viewer.py` | `src/api/scanlate.test.ts src/pages/comic` | `e2e/scanlate.spec.ts` |
| Sources / adapters | `tests/test_sources_core.py tests/test_api_sources_search.py tests/test_source_service.py` (an adapter: `tests/test_sources_<site>.py`) | `src/api/sources.test.ts src/pages/sources` | `e2e/sources.spec.ts` |
| Benchmark lab | `tests/test_benchmark_lab.py tests/test_benchmark.py tests/test_model_reeval.py tests/test_api_benchmark.py` | `src/api/benchmark.test.ts src/pages/benchmark` | `e2e/lab-benchmark.spec.ts` |
| Diagnostics / installs | `tests/test_diagnostics_service.py tests/test_api_diagnostics_installs.py tests/test_install_presets.py tests/test_install_plan.py tests/test_pending_install.py tests/test_api_pending_install.py tests/test_upgrade_check.py` | `src/api/diagnostics.test.ts src/pages/diagnostics` | `e2e/diagnostics.spec.ts` |
| Jobs | `tests/test_background_jobs.py tests/test_jobs_service.py tests/test_api_job_cancel.py tests/test_gpu_process_job.py tests/test_job_process_result.py tests/test_cancellable_lock.py tests/test_ytdlp_child.py` | `src/api/jobs.test.ts src/pages/jobs` | `e2e/jobs.spec.ts` |
| Settings / backups | `tests/test_settings_service.py tests/test_settings_schema.py tests/test_auto_backup_service.py tests/test_api_backups.py tests/test_user_backup.py` | `src/api/settings.test.ts src/pages/settings` | `e2e/settings.spec.ts e2e/backups.spec.ts` |
| Auth / permissions | `tests/test_api_permissions.py tests/test_auth_service.py tests/test_auth_login.py tests/test_api_admin_users.py` | `src/api/auth.test.ts src/hooks/useSession.test.ts` | `e2e/signin.spec.ts` |
| Installer / updates | `tests/test_installer_service.py tests/test_installer_iss.py tests/test_update_service.py` | `src/pages/settings/updateModel.test.ts` | `e2e/app-updates.spec.ts` |
| Database / migrations | `tests/test_db.py tests/test_save_lines_write_race.py` | n/a | n/a |
| Shared helpers (`lib/`) | `tests/test_lib_http.py tests/test_lib_proc.py tests/test_cancellable_lock.py tests/test_settings_schema.py` | n/a | n/a |
| Repo guards | `tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py tests/test_agent_docs.py tests/test_repo_map.py` (the first five are the CI `guards` job) | n/a | n/a |

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
  library (a copy of a schema-only template built once per xdist worker; a test
  that needs a database from before the current schema builds its own folder, as
  `TestInitDbSchema` in `tests/test_db.py` does). Underneath that, `tests/conftest.py` points the whole run at a
  temp library before any test module loads, and fails a test that opens
  an absolute path in the real `library/` folder
  (`tests/test_library_isolation_guard.py`). The guard is a backstop, not a
  sandbox: it can't see sqlite `file:` URI connects, relative paths or
  writes from a subprocess, and a hit in a module- or class-scoped fixture
  is cleared by the next test's setup. A repo-wide `os.walk` must skip
  `library`. The failure message names the thread and `PYTEST_CURRENT_TEST`:
  a background job thread that outlives its test shows up in the next test.
- `./library/` is the real library (the default `db.LIBRARY_DIR`) and may hold
  real data; never delete it to make a test pass. Repo-wide file walks in the
  tests skip `library` (`_SKIP_DIRS` in `tests/test_split_guards.py`), because
  the isolation guard fails any test that scans it.
- Test-first runs (`upgrade_check.py`) give the pytest child a private `TMP`
  inside the throwaway work folder, so the suite's `baihe_test_*` folders go
  with it; `tests/conftest.py` also retries its own temp cleanup on Windows (POSIX gets one pass).
- A test that needs an optional library (`jieba`, `pytesseract`, `cv2`,
  `paddleocr`, ...) should `pytest.importorskip` it, not hard-import it,
  so a core-only install still gets a clean run.
- **Diarization (pyannote) needs a real, gated-access-accepted Hugging
  Face token to run outside the mocked test suite.**
  `pyannote/speaker-diarization-community-1` is a gated model — an
  `HF_TOKEN` alone isn't enough; the account it belongs to also has to
  have accepted that specific model's license on huggingface.co first
  (`diagnostics_report.check_pyannote_gated_access` is what checks this in-app;
  the `HF_TOKEN`/`BAIHE_HF_TOKEN` environment variables
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

- After a PASS, run the smoke pack on your PC before upgrading for real (next section).
- On FAIL, `--write-pin` appends `pkg<failing-version` to `constraints.txt`
  (only when the failing version is newer than the known-good one). Review and
  commit it yourself.
- The installer lock `installer/wheels.lock.txt` is never edited; refresh it
  separately (docs/windows-installer-design.md).
- If you already upgraded your real environment, pin it back with the printed
  command, e.g. `pip install "pkg==<last good>"`. The script never touches it.

### Bumping constraints and the installer lock

Dependabot raises the `>=` floors in `requirements-*.txt` but never edits `constraints.txt` or
`installer/wheels.lock.txt`. `scripts/check_constraints.py` (a step in `tests.yml`, so it gates every PR)
fails when a pin in either file is older than a requirements floor, or a cap in `constraints.txt` leaves
no version a floor allows. Its output lists each package to bump. By hand:

1. Edit the `==` pin (or loosen the cap) in `constraints.txt` to a version the requirements allow.
2. Regenerate the installer lock (`python installer/build_installer.py --update-lock`, see
   `docs/windows-installer-design.md`) so `tests/test_constraints_lock_parity.py` still passes.
3. Run `python scripts/check_constraints.py` and `tests/test_constraints_lock_parity.py`; CI runs the full suite on the PR.

The weekly `dependency-canary.yml` workflow installs the newest versions the requirements and the caps in
`constraints.txt` allow (exact pins dropped) into a clean venv, runs the mocked suite and
`smoke_pack.py versions`, and reports only as a failing run: it opens no PRs, pushes nothing and has no
secrets. A failure means a new release broke something; reproduce with `scripts/dependency_canary.py <package>`.

Scheduled and manually dispatched workflows (`dependency-canary.yml`, `dependency-audit.yml`) and Dependabot
only run from the repository's default branch, which is `baihe-subtitler` (the old `main` branch no longer exists).

### Before and after an upgrade on your PC (smoke pack)

The canary above runs mocked tests. It cannot tell whether transcription still
works on your GPU with your real models, and neither can CI. The smoke pack
does that on your PC with a 2-5 minute clip of your own content (the clip and
the results stay local; `smoke_pack/` is gitignored):

    python scripts/smoke_pack.py init --audio clip.wav --language zh [--profile NAME] [--separate-vocals] [--diarization] [--forced-align]
    python scripts/smoke_pack.py run [--name NAME] [--update-baseline]
    python scripts/smoke_pack.py versions

1. Before upgrading: `init`, read `smoke_pack/<name>/expected.json`, fix anything
   wrong and set `"approved": true`.
2. Upgrade, then `run`. It prints PASS/WARN/FAIL per check and the key packages
   whose version changed, so a failure points at the upgrade. Exit code 0 PASS,
   1 FAIL, 2 ERROR (clip or baseline missing), 3 WARN only.
3. Happy with the new behaviour? `run --update-baseline`.

Checks (tolerances live in `profile.json`): text within 8% character error rate
of the baseline; line count within 15%; no line over 8 s or 40 characters beyond
the baseline's own plus 2; times in order, none empty, no overlap over 0.2 s;
speaker count equal when speaker detection is on; the same device per stage (a
GPU stage that fell back to CPU is a hard FAIL); each stage no more than 40%
slower (WARN, timings are noisy).

The real-model check in Diagnostics ships two samples in `assets/smoke/`: `clip.wav` (5 s, mono, 16 kHz, a plain tone) and `bubble.png` (260x100, the three characters 你好吗 in a system font). No generation script was recorded; by inspection both are synthetic and carry no third-party content. Because the clip is a tone, the Qwen3-ASR check reports "Tone only ... model was not loaded" (skipped) rather than a pass.

## GitHub Actions minutes

- Iterate locally; push once per PR for the merge-required checks, and batch related commits into one push.
- Workflows cancel superseded runs (`concurrency:` with `cancel-in-progress: true`, grouped by workflow and ref; in `tests.yml` a push gets its own group keyed on the commit). Don't rely on that for a run something else depends on (a release).
- Never weaken a check to save minutes.
- Set each job's `timeout-minutes` from observed run durations (roughly 2-3x the slowest), not a guess.
- Before changing triggers, matrices, concurrency or caching, check the billing usage page's per-job breakdown and state the trade-off.

## Background tasks: avoid stuck monitor loops

A `while pgrep -f "<pattern>"; do sleep N; done` loop matches its own command line and never ends.

- Save the PID (`$!`) and poll that exact PID: `while kill -0 "$PID" 2>/dev/null; do sleep N; done`.
  PowerShell: `$p = Start-Process ... -PassThru`, then `while (Get-Process -Id $p.Id -ErrorAction SilentlyContinue) { Start-Sleep N }`.
- Don't stack a second monitor for the same wait, and stop (`TaskStop`) any background task running far longer than its work should take.
