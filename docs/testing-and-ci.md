# Testing, verification and CI

Owner of the project's testing and CI guidance. Shared principles and precedence: `docs/engineering-standards.md`.
Role files (`.claude/agents/*.md`) link here instead of restating it.

## Risk-based verification tiers

1. **While iterating:** run the test file or selection for what you touched (`python run_tests.py <path>` or `-k`; `run_tests.py` forwards arguments to pytest).
2. **When a change crosses a shared module** (database layer, `background_jobs`, `translate_engines`, API schemas, `services/`): also run the relevant subsystem tests.
3. **Before pushing:** the area's tests plus the quick guards, `python -m pytest -q tests/test_static_analysis.py tests/test_api_permissions.py tests/test_split_guards.py tests/test_file_organization.py`; for the frontend also `npx tsc --noEmit` and `npx vitest run` from `frontend/`, and the one Playwright spec for a changed UI flow. CI then runs the full suite (`python run_tests.py -q -n auto`, about 17 minutes) and the full frontend and e2e jobs on the PR; don't repeat the full suite locally. Run it locally only when CI is not the gate (below) or a reviewer asks for it: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`, and for the frontend `npm run lint`, `npm test`, `npm run build` and `npm run e2e` from `frontend/`.
   `npm run e2e` runs two Playwright projects: `desktop` (every spec except files matching `mobile.spec.ts`) and `phone` (390x844, touch, every `e2e/*mobile.spec.ts` match, such as `mobile.spec.ts`, `settings.mobile.spec.ts` and `review-results-mobile.spec.ts`: no sideways scroll and 44px nav links, stage tabs and primary buttons on Library, each workspace stage, Settings and Diagnostics). `--project=phone` runs just the phone checks. With a preinstalled Chromium set `PLAYWRIGHT_CHROMIUM_PATH` (e.g. `/opt/pw-browsers/chromium`); `E2E_API_PORT`/`E2E_WEB_PORT` move the servers off the default 8611/4174.
4. Do not re-run an identical check on an unchanged tree without a reason. Never weaken, skip or narrow a required check to save time or CI minutes.
5. A failure is not "environmental" or "flaky" until the mechanism is confirmed (a real background thread left running by an earlier test was the cause of the "database is locked" errors fixed in PR #259).
6. Tests must not leave real job threads or subprocesses running: wait for them or mock them.

## Current merge gate

CI is the merge gate while the repo is public. Only if the repo becomes private or Actions minutes run out does the gate become the full local suite
(`python -m pytest -q -n auto -p no:cacheprovider -o addopts=""`) plus the frontend commands (tier 3 above). Never skip or weaken a test.

## CI layout

`.github/workflows/tests.yml` runs four jobs on every PR: `guards` (the quick guard files, about a minute, so a broken rule is
red first), `test` (the full mocked suite with xdist; tests that failed on the branch's previous run go first through `--ff`
and a restored `.pytest_cache`, so a repeat failure shows in the first minute), `frontend` (build, vitest, lint) and
`e2e` (Playwright in four shards). `windows-bootstrap.yml` and `windows-installer.yml` cover the installer.

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
  (`diagnostics_report.check_pyannote_gated_access` is what checks this in-app;
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
only run from the repository's default branch, which is still `main`. They take effect when the default
branch is `baihe-subtitler`; until then the check in `tests.yml` (which runs on pull requests into
`baihe-subtitler`) is the only part that works.

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
- Don't stack a second monitor for the same wait, and stop (`TaskStop`) any background task running far longer than its work should take.
