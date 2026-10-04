# Windows installer/uninstaller — design and as-built reference

Design and as-built reference (the installer was built 2026-09-30).
`python -m api` serves the API and the prebuilt React screens from one
process on `http://127.0.0.1:8600/`. Read it with
[`archive/windows-installer-research-notes.md`](archive/windows-installer-research-notes.md),
whose 2026-09-28 decisions are folded in below.

Code: [`installer/`](../installer/) (Inno Setup script, payload builder, and
the two runtime scripts), [`portable.py`](../portable.py) (`data_dir()`),
and [`.github/workflows/windows-installer.yml`](../.github/workflows/windows-installer.yml).

**Not verified on a real Windows PC yet.** The script compiles with Inno
Setup 6.7.3, and the payload and runtime scripts are tested on Linux. The
Windows CI smoke test (silent install, start, `/api/health`, silent
uninstall) runs only on demand, and the user's first real install is still
owed (see §9).

---

## 1. Decisions this builds on

- **Inno Setup + bundled Python, not a frozen executable** (user-confirmed
  2026-09-28). Freezers fit badly with the app's many optional, sometimes
  mutually exclusive ML backends, and can't reuse the tiered
  `requirements-*.txt` files or Diagnostics' install buttons. Inno Setup is
  free, scriptable, supports silent install/uninstall and gives a real
  Settings → Apps entry. MSI/WiX and conda were ruled out; conda is the
  fallback only if embeddable Python + pip fails in practice.
- **The React screens ship inside the installer** (user, 2026-09-30). `frontend/dist` is
  built at package time and served by FastAPI (`api/static_frontend.py`); no
  Node.js is needed on the user's PC.
- **Heavier components stay opt-in** (research notes, decision 2): the installer
  installs the Basic tier only; the rest comes from Diagnostics.
- **Code signing is deferred** until a public release (research notes §8), so
  SmartScreen shows "Windows protected your PC" (More info → Run anyway).
- **`start.bat`/`start.ps1` stay the source-checkout path**; both run the same
  `python -m api` and share the same requirements files.

## 2. What gets installed, and where

Per-user install, no admin rights (`PrivilegesRequired=lowest`):

| Location | What | Written by | Replaced on upgrade? |
|---|---|---|---|
| `%LOCALAPPDATA%\Programs\Baihe Studio\python\` | Official embeddable Python 3.12.10 x64 (pinned by SHA-256), with `python312._pth` set to: stdlib zip, `.`, `Lib\site-packages`, `..\app`, `import site`. pip and everything pip installs live in its `Lib\site-packages`. | Installer + pip | Interpreter files: yes. Installed packages: kept, and core is upgraded in place by pip. |
| `...\Baihe Studio\app\` | The app's code (allow-by-default copy minus the exclusions in §3), `frontend\dist`, `installer\launcher.py`, `installer\postinstall.py`, and the `INSTALLED` marker | Installer | Yes, wholesale (`[InstallDelete]`), so a module deleted upstream can't linger |
| `...\Baihe Studio\manifest.json` | App version, Python version and hash, wheel list with hashes, installed-size estimate | Installer | Yes |
| **Data folder**, default `%LOCALAPPDATA%\Baihe Studio\` (chosen on the "Where to keep your library" page, or with `/DATADIR=`) | `library\` (database, dramas, media, backups, logs), `.env` (settings and API keys), `model_cache\` (Hugging Face, torch and audio-separator caches), `launcher\` (server pid, server log, install log) | The app at runtime | **Never** |

Why a separate data folder: the program folder is replaced on every upgrade
and removed on uninstall, and a multi-GB growing library doesn't belong next
to code. The user can choose another drive for the data folder. The installer
refuses a folder inside the install folder or a whole drive (it checks on the
page, and again in `PrepareToInstall` because silent installs skip the page;
`PrepareToInstall` also creates the folder and writes a probe file, so an
unplugged drive or an unwritable folder stops Setup before anything is replaced).
`postinstall.py` checks the same rules as a backstop.

**Data folder permissions.** A folder inside the user's profile is already
private. A **new** folder Setup creates elsewhere (say `D:\Baihe`) would
inherit that drive's permissions, which often let every account on the PC
read it, and `.env` holds the API keys. So the install step limits it to this
account, SYSTEM and Administrators (`icacls /inheritance:r`, by SID). An
**existing** folder outside the profile keeps its permissions; the data-folder
page warns that other accounts may be able to read it and asks before using it.
Setup records in the marker (`# created-by-setup`) whether it created the
folder, which is what lets a clean uninstall remove the folder itself (§8).
The lock-down runs only when Setup makes the folder; an update neither redoes
it (the user may have adjusted the permissions since) nor repeats the warning
for the folder the previous install already used.

**How the app finds its data**: `portable.data_dir()`:

1. `BAIHE_DATA_DIR`, if set (an override for anyone).
2. Otherwise, for an installed copy (an `app\INSTALLED` marker exists; only
   the marker counts, so a source checkout is never taken for an install), the
   first non-comment line of that marker (an absolute path; UTF-8 with BOM, so
   non-ASCII user names work), falling back to `%LOCALAPPDATA%\Baihe Studio`.
   An install whose marker is missing (an interrupted upgrade) is refused by
   the launcher ("run the installer again to repair it"), so it never falls
   back to keeping its library in the program folder.
3. Otherwise, the app folder, which is exactly what a source checkout always did.

`db.LIBRARY_DIR`, `dictionary.CEDICT_PATH` and `settings_service`'s `.env` path
come from `data_dir()`. The app log already follows `db.LIBRARY_DIR`.
For an installed copy, `activate_portable_mode()` also points `HF_HOME`,
`TORCH_HOME` and `BAIHE_AUDIO_SEP_MODEL_DIR` at `<data>\model_cache\…`, always via `setdefault`, so a user's own `HF_HOME` wins.
Portable mode (the `PORTABLE` marker) is unchanged.

**Keys stay on the PC.** The payload never contains a `.env` (see §3). Keys
the user types into Settings are written by the running app to `<data>\.env`,
server-side only, as for a source checkout. Nothing in the installer reads,
copies or uploads them. The launcher starts the server on loopback
(`BAIHE_API_HOST=127.0.0.1`, forced) with the PC-only key form enabled, the
same as `start.bat`.

## 3. Building the installer

`python installer/build_installer.py --version <v>` (on Windows; CI does this):

1. Builds `frontend/dist` (`npm ci && npm run build`, reusing
   `scripts/build_release.py`), unless `--skip-frontend-build` is passed.
2. Stages the app into `build/installer/payload/app/` from the **tracked** files
   (`git ls-files`; a tree with no git metadata falls back to a walk), so an
   untracked file in a developer's checkout never ships. `is_excluded()` is the
   one rule; then `check_payload()` re-checks the staged tree and fails the
   build if anything below slipped through, or if a key file is missing.
   - **Never shipped**: `.env` and `.env.*` anywhere (files or folders), `cookies*.txt`,
     `*.key`/`*.pem`/`*.pfx`/`*.p12`/`*.crt`, `library/` and `model_cache/`
     anywhere, `venv`/`.venv`, `__pycache__`/`*.pyc`, logs and database files,
     `tests/`, `docs/`, `scripts/`, `frontend/` (except `frontend/dist`),
     `installer/` (except the two runtime scripts), `.github`, `.claude`,
     `build`/`dist`, the source-checkout launchers (`start.bat`,
     `start.ps1`, `uninstall.bat`, `make_*.bat`, `uninstall_path_cleanup.ps1`),
     the `PORTABLE`/`PYTHON_VERSION`/`INSTALLED` markers, and developer files.
     (`run_tests.py` does ship: Diagnostics' file-completeness check expects it.)
3. Downloads the embeddable Python zip, checks its pinned SHA-256, extracts it,
   and rewrites the `._pth` (research notes §2).
4. Checks that `installer/wheels.lock.txt` pins every package named in
   `requirements-core.txt` and pip, then runs
   `pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --require-hashes --no-deps -r installer/wheels.lock.txt`.
   The build then re-checks every downloaded wheel (see "Pinned wheels"): a
   wheel with a different SHA-256, a wheel not in the lock, and a locked package
   with no wheel each fail the build, naming the file. There is no unhashed
   fallback and nothing is resolved at build time. `--update-lock` must run on
   Windows under Python 3.12, because pip evaluates environment markers for the
   machine it runs on (a Linux resolve drops Windows-only dependencies such as
   `tzdata`).
5. Writes `manifest.json`, including the unpacked size of the wheels, which is
   passed to Inno's `ExtraDiskSpaceRequired` for the free-space check.
6. Compiles `installer/baihe.iss` with ISCC. Output:
   `build/installer/output/BaiheStudio-Setup-<version>.exe`, about 100 MB
   (roughly 300 MB installed).

`build/` is git-ignored.

### Pinned wheels

`installer/wheels.lock.txt` lists `name==version` and `--hash=sha256:...` for
every distribution the installer bundles: `requirements-core.txt`, pip, and all
transitive dependencies, one win_amd64 / CPython 3.12 wheel each. The
verifier (`postinstall.parse_lock` / `verify_wheels`, shared by the build and the
install step) rejects an unpinned line, a package without a hash, a duplicate,
markers or other options, a hash that isn't 64 hex digits, and any wheel that
is extra or missing compared with the lock.

Regenerating on an intentional dependency change (a change to
`requirements-core.txt`, `constraints.txt`, or a version bump):

1. A maintainer, on Windows with Python 3.12 and network access to PyPI, runs
   `python installer/build_installer.py --update-lock` (an ordinary
   `pip download` of `requirements-core.txt` + pip, constrained by
   `constraints.lock.txt` if present, else `constraints.txt`; it writes each
   wheel's SHA-256).
2. The maintainer reads `git diff installer/wheels.lock.txt`, checks that only
   the intended packages and versions moved, and commits it with the dependency
   change.
3. CI does not regenerate it. The Windows Installer workflow builds from the
   committed lock and fails on a coverage gap, a rejected hash or a mismatch;
   `tests/test_installer_payload.py` checks on every PR that the lock parses and
   covers `requirements-core.txt`.

The lock proves the installed wheels are byte-for-byte the files whose hashes
were committed, so a later change on PyPI or the network path can't swap in
different bytes. It does not show the packages themselves are safe; that is the
maintainer's review of the diff.

## 4. Install flow

1. Wizard: install folder (default `%LOCALAPPDATA%\Programs\Baihe Studio`),
   then the data folder page, then an optional desktop shortcut.
   Free-space check. Setup refuses an install folder that already holds a
   `python\` or `app\` folder Baihe Studio didn't put there (its
   `manifest.json` must name the product and this AppId, or this app's
   uninstall entry must point at the folder).
2. `PrepareToInstall`: check both folders again, note whether the data folder
   is new, check it can be written, and stop a server a previous install
   started (`launcher.py --stop`) so its files can be replaced.
3. Copy files. The wheels go to `{tmp}` and are deleted afterwards.
4. `postinstall.py`, run by the bundled `python.exe -s`:
   1. Writes `app\INSTALLED` with the data folder, then creates the folder.
      The marker comes first, and the launcher refuses to start an install
      without it, so even a failed install never puts the library in the
      program folder.
   2. Bootstraps pip by running it as a module from its own wheel (`runpy`,
      the equivalent of `python -m pip`; running `pip.whl\pip` directly fails
      on Windows, where pip refuses to modify itself unless run as `-m pip`).
      No network is needed, and nothing is fetched from bootstrap.pypa.io.
   3. Re-checks every bundled wheel against the shipped copy of the lock
      (`<wheels>\wheels.lock.txt`; exit code 6 on a mismatch, an extra wheel or
      a missing one), then
      `pip install --no-index --find-links <wheels> --require-hashes --no-deps -r <wheels>\wheels.lock.txt`.
      `PIP_USER`, `PIP_REQUIRE_VIRTUALENV`, `PIP_INDEX_URL` and similar variables
      from the user's environment are dropped first, and `-s` keeps the user's
      own site-packages out (research notes §1, the ComfyUI `-s` lesson).
   4. Limits a new data folder outside the profile to this account (§2).
   5. Checks that the core packages import, then runs `check_setup.py`
      (ffmpeg, JS runtime, CUDA) for the log only.
   6. Logs everything to `<data>\launcher\install.log`.
   If this step fails, Setup shows a plain-words error with the log path.
   It exits with **code 100** (outside Inno's own 1-8) so a silent install can detect the failure, and the
   "Start Baihe Studio now" option is skipped.
5. Shortcuts: Start menu → Baihe Studio → **Baihe Studio** (runs
   `pythonw.exe -s app\installer\launcher.py`) and **Stop Baihe Studio**
   (`… --stop`).

## 5. Launching (installer/launcher.py)

This is the same behaviour as `start.bat` for a source checkout, minus the setup:

- If `/api/health` already answers on the port, it just opens a window.
- If something else holds the port, it shows a message box saying so and how
  to choose another port (`BAIHE_API_PORT`).
- Otherwise it starts `python.exe -s -m api` in its own minimized console
  window (closing it stops the app; the window keeps the title Windows gives
  it), and waits up to 90 s for `/api/health`. If the server exits early, it stops
  waiting at once. Once its own server is healthy and still running, it
  records the pid in `<data>\launcher\server.pid`. A start lock
  (`starting.lock`) makes a second click during a slow first start wait for
  the first server instead of starting another one that would lose the port
  and leave `--stop` pointing at the wrong pid.
- It opens the app in its own window: Edge `--app`, then Chrome `--app`, then
  the default browser.
- Errors appear in a message box (under `pythonw.exe` there is no console),
  except with `--no-browser`, which always prints to stderr so an unattended
  run can't hang on a box.
- `--no-browser`: start with no window, log to `<data>\launcher\server.log`,
  exit 0 once healthy (for CI).
- `--stop` (the "Stop Baihe Studio" shortcut, the uninstaller, and an upgrade):
  see "Stopping" below.

**Stopping stops everything.**

- **Job Object.** `python -m api` (however it's started: this launcher or
  `start.bat`) puts itself into a Windows Job Object with "kill on job close"
  (`process_guard.py`). Every process it starts joins the job: ffmpeg,
  yt-dlp, Playwright's Node driver and its Chromium, lncrawl, pip. When the
  server ends for any reason, Windows ends them all, and nothing else. The
  launcher passes a per-install name in `BAIHE_PROCESS_GROUP_NAME` so
  `--stop` can end the job by name; from `start.bat` the job is anonymous.
- **Closing the server's window** (or Windows shutting down) runs the same
  clean stop as below first, in the ~5 s Windows allows
  (`SetConsoleCtrlHandler`); then the process ends and the job takes its
  children. Ctrl+C in `start.bat` stops uvicorn and then runs the clean stop.
- **The clean stop** (`services/shutdown_service.py`): no new scheduled
  chapter check, backup tick or browser; cancel every running and queued job
  through the normal cancel path; an open site sign-in window closes itself;
  wait for the jobs; stop the browser-extension endpoint (`page_server`).
- **Shutdown token.** The launcher also gives the server a fresh one-time
  token (`BAIHE_SHUTDOWN_TOKEN`, kept in `<data>\launcher\shutdown.token`).
- **What `--stop` does:**
  1. **Clean shutdown.** It sends `POST /api/system/shutdown` with that token.
     The server runs the clean stop, giving jobs up to 6 s, and exits. The
     launcher waits up to 10 s.
  2. **Forced stop.** If the server is still running, the launcher ends it.
     The recorded pid is opened once, and its image is checked to be this
     install's own `python.exe`. Waiting and ending both go through that
     handle, so a pid that Windows reuses before or during the stop can never
     be signalled.
  3. **Sweep.** Whatever happened, it then ends this install's Job Object by
     name, which catches any leftover child. Nothing outside this install's
     job or pid is ever signalled.
- **Loopback calls.** The launcher's loopback calls bypass any system proxy,
  so the token only ever reaches this PC's server.
- **Token lifetime.** The server takes the token (and the job name) out of its
  environment at startup, so the processes it starts don't inherit them.
- **Graceful-shutdown cap.** uvicorn's graceful shutdown is capped at 3 s, so
  an open media stream can't hold the clean stop past the launcher's grace.
- **Routes.** The shutdown route is `local_only()` and needs the token. A
  server not started by the installed launcher has no token, so the route is
  a 404 there (`start.bat` stops through its window or Ctrl+C).

The environment is the same as `start.bat`'s: `BAIHE_API_HOST=127.0.0.1`
(forced), `BAIHE_API_ALLOW_KEY_WRITES=1` unless already set,
`BAIHE_API_PORT=8600` unless already set (`setx BAIHE_API_PORT <port>` changes it; the health probe, window and `--stop` follow it). With this install's boot service installed (the service's `config.json` names this install folder), the launcher uses the service's stored port instead and the variable is ignored (an unusable stored port means 8600, as for the service); a service another install owns is ignored; "Baihe Studio service" in the Start menu changes that port (§11, "Choosing the port"). `PYTHONNOUSERSITE=1` is set, and the
user's pip-redirecting variables are dropped, so Diagnostics' Install buttons
(`sys.executable -m pip install`) install into the bundled interpreter.

## 6. Tiers: Basic in the installer, the rest through Diagnostics

| Tier | How it gets installed |
|---|---|
| **Basic**: `requirements-core.txt` (FastAPI/uvicorn, requests, anthropic, pandas, …; `installer/wheels.lock.txt` still pins `streamlit==1.64.0`, which `requirements-core.txt` no longer lists) | The installer, offline, from bundled wheels |
| **Media**: `requirements-media.txt` | Diagnostics → the tier's bulk Install button (Step 62), into the bundled interpreter. Needs network. |
| **Optional / GPU torch / engines**: `requirements-optional.txt`, `diagnostics.TORCH_VARIANTS` | Diagnostics' per-package and GPU PyTorch buttons, as today. The CPU fallback and GPU reporting (research notes decision 2) are Diagnostics' existing behaviour; the installer adds no GPU detection of its own. |

The installer reuses the tiered requirements files and Diagnostics' install
machinery. An installer-side tier picker and GPU/torch opt-in are **not built**:
bundling them would multiply the offline download to several GB.

Known limit: Diagnostics' Step 66 "will this upgrade break the app?" check runs
the test suite in a throwaway venv. An installed copy has no `tests/` (it is
excluded), and the embeddable Python has no `venv`/`ensurepip`, so that check
reports "incomplete" on an installed copy. It still works from a source checkout.

## 7. Upgrade

Running a newer `BaiheStudio-Setup-<v>.exe`:

- It keeps the same `AppId`, so Windows sees one app. The install folder is
  reused (`UsePreviousAppDir`), and the data folder is reused through
  `GetPreviousData('DataDir')` (`/DATADIR=` overrides it).
- It stops the running server first (`PrepareToInstall` → `launcher.py --stop`).
  Inno's Restart Manager (`CloseApplications=yes`) covers anything else holding
  files.
- `app\` is replaced wholesale. The `python\` interpreter files are overwritten,
  and its `site-packages` is kept, so optional packages added through
  Diagnostics survive. Then `postinstall.py` runs `pip install` again against
  the new bundled wheels, which changes only what changed.
- The data folder is never touched.
- A Python minor-version bump (3.12 → 3.13) would make the kept `site-packages`
  unusable; the release would then need "uninstall first" or an
  `[InstallDelete]` for `python\Lib\site-packages`.
- There are no delta updates: an upgrade re-downloads the whole ~100 MB
  installer; model files in the data folder are never re-downloaded.

## 8. Uninstall

Settings → Apps → Baihe Studio → Uninstall (or `unins000.exe`):

1. `[UninstallRun]` stops the server (`launcher.py --stop`).
2. A dialog lists the data folder with four boxes, **all unticked by default**:
   delete my library; delete my saved settings and API keys (`.env`); delete
   downloaded AI models (`model_cache`); and **Remove everything (clean
   uninstall)**, which ticks the other three and asks a second time, naming
   the data folder, before going ahead. When Setup made that folder, the
   warning says plainly that the whole folder goes, including any files the
   user put there. Answering No puts the other boxes back the way they were.
   Cancel aborts the uninstall.
3. The program is removed: `{app}\python` (including pip-installed packages)
   and `{app}\app`, but only when Baihe Studio put them there (the install
   has its `manifest.json`); then the shortcuts and the uninstall entry.
   `{app}` itself is removed if it is empty.
4. Only the ticked items are deleted, by name (`<data>\library`, `<data>\.env`,
   `<data>\model_cache`). The data folder's other contents are never touched,
   since it may be a folder the user picked and shares with other files. The
   launcher's own files (`launcher\server.pid`, `starting.lock`, `server.log`,
   `install.log`) are always removed by name, then `launcher\` if it is empty.
   The data folder itself is removed only if it is then empty.
5. **A silent uninstall (`/VERYSILENT`) never deletes user data**, unless
   `/CLEAN` is also passed.

**Clean uninstall** ("Remove everything", or `/VERYSILENT /CLEAN`):

- **The data folder.** If Setup created it (the marker's `# created-by-setup`
  line), the whole folder goes: library (projects, backups including
  `backups\auto`, logs, `source_cache`, browser profiles,
  `extension_token.txt`, Piper voices), `.env`, `model_cache`, and the
  launcher's files. If the user picked an existing folder, only those named
  items go, and the folder is removed only if that leaves it empty.
- **Temporary items.** Baihe Studio's own named items in `%TEMP%` are removed:
  `baihe_*` work folders, `baihe_page_*` files and `baihe-torch-pins-*` files.
  Nothing else in `%TEMP%` is removed. Temporary folders Python names itself
  (`tmp*`) can't be told apart from other programs', so they're listed as left.
  Close any other running copy of Baihe Studio first, because its live
  `baihe_*` folders would go too.
- **Shared, left in place and listed as such:**
  - Playwright's browsers (`%LOCALAPPDATA%\ms-playwright`);
  - pip's download cache;
  - Hugging Face's default cache in `%USERPROFILE%\.cache`;
  - Deno in `%USERPROFILE%\.deno`;
  - automatic backups pointed at a folder outside the data folder.
- **Failures.** Anything that can't be deleted (a file in use) is listed as
  such, never reported as removed.
- **Summary.** A summary of what was removed and what was left is shown, and
  written to the uninstall log.

If the marker can't be read or names an invalid folder, no user data is
deleted. Out of scope, as with `uninstall.bat`: system-wide ffmpeg, Ollama,
CUDA drivers, and Hugging Face/torch caches outside the data folder.

## 9. Testing

- Linux (the test suite): `tests/test_installer_payload.py` (staging, exclusions,
  `._pth`, wheel commands, lock parser and verifier, manifest, ISCC arguments),
  `tests/test_installer_iss.py` (static checks of the `.iss` and the workflow),
  `tests/test_installer_runtime.py` (launcher and post-install logic with fakes)
  and `tests/test_portable.py` (`data_dir()`).
- Windows CI, **on demand** (Actions → Windows Installer → Run workflow, or push
  an `installer-v*` tag; never on PRs, because of the minutes budget): it builds
  the `.exe`, uploads it as an artifact, and smoke-tests it. The smoke test runs
  a silent install to a temp folder with `/DATADIR=`, checks the `INSTALLED`
  marker, and checks that no `.env`, `library`, `tests` or `model_cache` got
  installed. It runs `launcher.py --no-browser`, checks `/api/health`, checks
  that `/` is HTML and the JS bundle is served as JavaScript, and checks that
  `/api/library/dramas` creates `library.db` in the data folder, not the
  program folder. Then it runs `--stop`, a silent uninstall, confirms the
  program is gone, and confirms the library and `.env` survived.
  - **Stop check.** `installer/smoke_child.py` puts a stand-in child into the
    server's Job Object; `--stop` must report a clean shutdown with the child
    and every install-folder process gone.
  - **Breakaway check.** `smoke_child.py --breakaway-check` runs a stand-in
    server with the real `process_guard.contain_children()`: its job must refuse
    `CREATE_BREAKAWAY_FROM_JOB` except inside `breakaway_allowed()`.
  - **Clean-uninstall check.** A second install is removed with `/CLEAN`; its
    folders and a `%TEMP%\baihe_*` folder must be gone while a non-Baihe
    `%TEMP%` folder, a neighbouring file and the first install's data folder
    are untouched.
- Only the Windows job proves that pip accepts `installer/wheels.lock.txt` for
  the real win_amd64 downloads and installs, and that the pinned wheels are the
  ones the Windows pip picks.
- Still owed, from a person: a real install on the user's PC (steps in the PR),
  the interactive wizard and uninstall dialog, SmartScreen, Edge app window, and
  the research notes' clean-Windows GPU matrix once a GPU tier is added through
  Diagnostics.

## 10. Still open / deferred

- An offline installer-side tier picker and GPU/torch opt-in (§6). Today these
  are online, through Diagnostics.
- Delta updates, and an updater that reads the per-component `manifest.json` (§7).
  What exists: Settings → App updates checks the public GitHub Releases
  (`BAIHE_UPDATE_REPO`, default `zkaelxz/U00`; no token) for a newer `v*`
  release, and on the user's clicks downloads the whole installer into
  `<data>\library\updates\`, checks it against the release's `.sha256` and
  opens its normal Setup from a private temp copy (hashed again; the server's
  environment minus the shutdown token, job name and secret-named variables;
  let out of the server's Job Object only for that launch). It picks the
  newest `v*` release that has the installer and its `.sha256`, so other
  releases (`frontend-v*`) don't hide it. The app keeps running
  until the user clicks Install in Setup, which stops the server as in §7; a
  cancelled Setup changes nothing. That SHA-256 comes
  from the same release as the installer, so it detects a broken or truncated
  download, not a tampered release: it is not a signature. The version shown
  is `manifest.json`'s `app_version`. A daily check is a setting, off by
  default; nothing is ever downloaded or installed without a click.
  Private releases: the app sends no credentials (no token, `~/.netrc`
  ignored), so it can only read public releases. If the releases repository
  goes private, GitHub answers the check with a 404 and the card says no
  release was found. Keep in-app updates on public releases (a separate
  public, installers-only repository set in `BAIHE_UPDATE_REPO` works), and
  use the manual route for private ones: download the installer and its
  `.sha256` from the releases page, check it with `Get-FileHash`, run Setup
  (docs/runbook.md §1). Shipping a token in the app is not an option: every
  installed copy would hold the same secret.
- Code signing (§1).
- Moving an existing source-checkout library into the installed app. For now,
  point the data folder at the checkout folder (it holds `library\` and `.env`),
  or copy those two into the data folder.
- `start.bat`/`start.ps1` still tell a source-checkout user without
  `frontend\dist` to download the frontend zip from Releases. Now that installed
  users get the screens inside the installer (2026-09-30), whether that zip
  keeps being published for source checkouts is a planning decision (the
  alternative is `start.bat --build-frontend`).

## 11. Boot service

Owner decision (2026-09-30): the app runs as a **Windows service started at
boot**, installed and removed by this installer, tested by the Windows
Installer workflow. Code: [`installer/service.py`](../installer/service.py)
(standard library only), the `service` task in `baihe.iss`, and the WinSW
staging in `build_installer.py`. **Not run on Windows yet:**
`tests/test_installer_service.py` drives it against a fake `sc`/`icacls`;
only the workflow's "Service --" steps prove it on Windows.

**What it does.** A `BaiheStudio` service runs `python -s -m api` on
`127.0.0.1:8600` (or the port chosen with `set-port`, below) and nothing else, starting at boot (no sign-in needed),
restarted after 10 s, 30 s, then every 60 s if it fails. It stops with
Ctrl+C, so the server's clean stop (§5) runs and its Job Object ends every
child. The wrapper is WinSW 2.12.0 (MIT, pinned by SHA-256). The task is on by
default and is the only step that needs administrator permission (one prompt
on install, two on an update, one on uninstall); declined or failed, Setup
exits with code 101 and the Start-menu launcher still works. Setup stays
per-user.

- **Account.** `NT SERVICE\BaiheStudio`, not LocalSystem, with its privileges
  cut to `SeChangeNotifyPrivilege`, `SeCreateGlobalPrivilege` and
  `SeIncreaseWorkingSetPrivilege` (no `SeImpersonatePrivilege`).
- **Permissions (`icacls`, by SID).** Read and run the install folder; change
  the data folder (refused unless it holds only Baihe Studio's own items).
  Everything run with administrator rights after the install (the script,
  its interpreter, the wrapper) is copied to `%ProgramFiles%\Baihe Studio
  Services`, which only administrators can change, and the later commands
  (`stop`, `uninstall`) run only from there, at the location Windows reports
  for Program Files. **Known gap:** the first `install` (and an update's) is
  run elevated by Setup from its own extraction, `{app}\service`, in the
  user-writable program folder, before it is copied; a process running as the
  user could replace those files in that window and have them run as
  administrator. Fixing it needs Setup to be elevated or to extract
  somewhere only administrators can write. **Owner decision (2026-09-30):
  accepted for now; revisit if the PC gets other users.**
  Uninstall takes the account off both folders again and removes the admin
  folder (files still loaded are moved aside, inside Program Files, and
  deleted at the next restart).
- **Environment and who can use it.** Every `BAIHE_API_*` setting is written
  into the service, so a machine-wide variable can't add a listener or change
  the port. **Any account on the PC can open `http://127.0.0.1:8600` while
  the service runs:** sign-in is off there (the app treats every direct
  loopback request as its owner), as it is when the launcher runs it, and the
  service keeps running when nobody is signed in. So on a PC with other
  Windows accounts, they can use the library and its actions. The engine-key
  form is **off** unless the data folder's `.env` sets
  `BAIHE_API_ALLOW_KEY_WRITES=1`, for that reason (the launcher turns it on
  by default). Per-account sign-in for this listener is not done. **Owner
  decision (2026-09-30): accepted on the condition that only the owner uses
  this PC. If it has other Windows accounts, untick the service task (it is
  ticked by default for now).**
- **Caddy and a crashed Baihe.** If Baihe's process exits on its own, Caddy keeps forwarding to the household port until the service restarts (about 10 s). Another program on this PC could bind that port in the gap and receive household requests. Accepted on the same condition: only the owner uses this PC.
- **Choosing the port.** The service's port is stored in the admin-only
  `helper\config.json` and written into the service definition; `status`,
  `enable-remote`, the health check and the Start-menu launcher read it from
  there, so the launcher opens the service rather than starting a second
  server on the same data folder (with no service, the launcher uses
  `BAIHE_API_PORT`, or 8600). The data folder's `.env` and the machine or
  user environment can't change it while the service runs. To change it,
  open **"Baihe Studio service"** in the Start menu and choose "Change Baihe
  Studio's port", or run `set-port N` (Commands, below) in an administrator
  prompt: any port from 1024 to 65535 except 8501, 8756, 8610 and the
  household port while remote access is on. A port that is refused or
  already in use changes nothing (exit code 2), and so does running it while
  the launcher's own server holds the service's port (stop it first); the
  new port is written to `config.json` and the service definition, the
  service is restarted (Caddy stopped first and started after, if remote
  access is on), and if `/api/health` doesn't answer on the new port the old
  configuration and services are put back (exit code 1; also on Ctrl+C).
  If remote access was on (Caddy running or set to start with Windows) and
  the household listener doesn't come back on the new port, remote access is
  turned off, as an update does, and the message says so. Setup passes the
  user's `BAIHE_API_PORT`, if set, as `install --port N` on every run, but
  `install` uses it only when `config.json` holds no port: a fresh install,
  or a service from before the port was stored. An update keeps the stored
  port, ignores `--port` without checking it (so an unusable variable can't
  make the update fail) and logs "kept the stored port N; use set-port".
  **Warning: changing or deleting `BAIHE_API_PORT`, or running Setup again
  with it set, does not change the service's port.** Once the service is
  installed, its stored port wins and only "Baihe Studio service" /
  `set-port` changes it. Without the service, removing the variable just
  returns the launcher to 8600.
- **Update and uninstall.** An update stops the service through the old admin
  copy, replaces the files, and starts it again; if any step fails, the old
  admin files come back, a service the run created is removed, and an existing
  one is restarted. Uninstall stops and removes the service first; if it can't,
  nothing is uninstalled.

**Commands.** The Start-menu item **"Baihe Studio service"** (with the
service task) opens a console menu, `installer/service_menu.ps1`, that runs
these for you: show the status, change the port, turn remote access on or
off, show where the service's logs are (it prints the folders rather than
opening one from its elevated window). It asks for administrator rights once,
and runs only the admin folder's copy of itself with the admin folder's
Python; the elevated command lines hold only fixed words and checked
numbers. By hand, in an administrator prompt (`status` needs none):

```
"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" COMMAND
```

- `status`: both services, every port Baihe uses (its own port and where it comes from, the household port and HTTPS 443 while remote access is on, the extension bridge 8756), remote access, the firewall rule.
- `set-port N`: move the service to port N; put back if N doesn't answer.
- `enable-remote [--household-port N]`: household access through Caddy (sign-in settings in `.env` first).
- `disable-remote`: household access off, Caddy stopped and disabled.
- `stop`: stop both services.
- `install [--port N]`: Setup's step; creates or refreshes the service and starts it (`--port` only on a fresh install; an update keeps the stored port).
- `uninstall`: the uninstaller's step; removes both services and the admin folder.

**One at a time with Setup.** `set-port`, `enable-remote` and
`disable-remote` take Setup's own mutex (`SetupMutex` in `baihe.iss`, both
`BaiheStudioSetupMutex` and `Global\BaiheStudioSetupMutex`) for as long as
they run: Setup started meanwhile says it is already running, and while
Setup (or another of those three) holds it they are refused with exit code 2,
changing nothing ("Setup is running ...; try again when it has finished").
The mutex they create lets everyone open it for `SYNCHRONIZE`, because Setup
runs unelevated and checks with `OpenMutex`. `install`, `stop` and
`uninstall` don't take it (Setup runs them while it holds it), and neither
does the read-only `status`.

`service.py --help` prints the same list. The CI checks the menu only through
its non-interactive `-Status`; the menu, its prompts and the elevation
prompt are not run in CI.

**What it does not do.**

- No other listener, no firewall rule, no certificate, no DNS or router
  change, and no household or remote access until the owner runs
  `enable-remote` (next subsection). `docs/remote-access-decision.md` and the
  API permissions are unchanged.
- The server runs as its own account, so it sees the machine's `PATH`, not the
  user's, and Diagnostics' Install buttons can't add packages to the
  read-only program folder while the service runs; per-user caches outside the
  data folder (Playwright, Deno) are the service account's.
- The "Stop Baihe Studio" shortcut stops only a server the launcher started;
  stop the "Baihe Studio" service in Services instead.
- The program folder stays user-writable, so a changed file there runs as the
  low-privilege service account, not as LocalSystem.
- No health monitoring or banner.
- `set-port` can race the launcher: its check for a launcher-started server on the service's port runs once, so a server the launcher starts during the move isn't caught.
- The elevated menu's PowerShell host reads the user's environment (as any elevated console does); it passes only fixed words and checked numbers to the admin folder's Python.

**Caddy and remote access (owner's opt-in).** Four rules, owner decisions:

1. Baihe never exposes its own API to the network. The household listener is
   opt-in and binds `127.0.0.1` only (`api_config.check_household_bind_safety`);
   remote requests reach it only through Caddy.
2. Caddy is the only internet-facing component: it gets and renews the HTTPS
   certificate and rate-limits sign-in (`rate_limit`, from
   `deploy/caddy/Caddyfile.template`). The template still refuses every
   `local_only()` route (`@pc_only`, checked by `tests/test_caddyfile_template.py`).
   `render_caddyfile` refuses a template that lost `admin off`, that refusal,
   or the loopback `reverse_proxy`.
3. **Nothing here creates a firewall rule or opens a port.** `enable-remote`
   and `status` print the exact command for the owner to run by hand in an
   administrator prompt (`firewall_rule_command`): inbound TCP 443, for
   `caddy.exe` only, private and domain profiles. Forwarding 443 on the router
   and the domain name (and any dynamic DNS) are the owner's steps
   (`docs/household-access.md`). The script only ever runs `netsh ... show rule`.
4. The service stays local-only until `enable-remote` is run.

The owner's step-by-step guide, with a table of which ports are opened or forwarded (only 443; port 80 is neither), is `docs/household-access.md`.

A second service, `BaiheCaddy` (WinSW, `NT SERVICE\BaiheCaddy`, same privilege
cut, depends on `BaiheStudio`), is installed **disabled and stopped**, with
`caddy.exe` built from `installer/caddy`: stock Caddy plus `rate_limit`, every
module pinned by `go.sum`, Go `go1.26.8` pinned by SHA-256 in the workflow, and
the binary pinned by `CADDY_SHA256` in `build_installer.py`. Its certificates
and ACME key live in `%ProgramFiles%\Baihe Studio Services\caddy-data`
(Caddy, SYSTEM and Administrators only).

`enable-remote [--household-port 8610]` (administrator prompt) refuses, exit
code 2 and nothing changed, unless the data folder's `.env` has the Google
sign-in settings and a `BAIHE_PUBLIC_URL` that is just `https://` and an ASCII
DNS name on the default port, and the port is free and not one of Baihe's own
(`settings_service.baihe_own_ports()`, 8601 and the service's own port). The server no longer refuses
to start over a bad household setting (it skips the household listener and
keeps the PC one), so these checks run first. Then it sets the household
port in the `BaiheStudio` service definition (the only place it can come
from: every other `BAIHE_API_*` value is blanked), restarts the service,
waits for the household listener to answer `/api/meta` for the public name,
writes the Caddyfile from the template with the domain and port filled in (no
secret goes in it), and starts Caddy; any failure turns it all off again.
`disable-remote` undoes it. An update keeps remote access on if its settings
still pass and the household listener comes back, else turns it off and says
so. Uninstall removes both services and Caddy's folders (with `rmtree` before
the rest of the admin folder, so a link Caddy made in them is never followed)
and tells the owner the command to delete a rule they added.

*Reproducible Caddy build.* The same `go build -trimpath -buildvcs=false`
gives `e09cc7eb...` on Linux and on Windows only from the same source bytes:
CRLF line endings in `installer/caddy/main.go` (what a Windows checkout gives
without `.gitattributes`) produce `03e740b8...`. `.gitattributes` pins
`installer/caddy/*` to LF, and a test checks it.
