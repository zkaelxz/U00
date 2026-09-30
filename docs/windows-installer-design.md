# Windows installer/uninstaller — design and as-built reference

Roadmap Step 80 wrote the design (2026-09-27); Step 80b built it
(2026-09-30, user decision "Yes, start it"). The first version of this
document predates the React + FastAPI migration and described a Streamlit
app. This version describes the app as it is now: `python -m api` serves
the API and the prebuilt React screens from one process on
`http://127.0.0.1:8600/`. Read it with
[`windows-installer-research-notes.md`](archive/windows-installer-research-notes.md),
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

- **Inno Setup + bundled Python, not a frozen executable** (Step 80 §2, user-confirmed
  2026-09-28). Freezers (PyInstaller/Nuitka) fit badly with the app's many
  optional, sometimes mutually exclusive ML backends, and they can't reuse the
  tiered `requirements-*.txt` files or Diagnostics' install buttons. Inno Setup is
  free, scriptable, supports silent install/uninstall natively, and gives a real
  Settings → Apps entry. MSI/WiX was ruled out (no fleet-management need), and so
  was conda (research notes §3: it would duplicate the requirements files). Conda
  is the fallback only if embeddable Python + pip fails in practice.
- **The React screens ship inside the installer** (user, 2026-09-30). There is no
  separate frontend zip for installed users: `frontend/dist` is built at package
  time and served by FastAPI (`api/static_frontend.py`). No Node.js is needed on
  the user's PC.
- **Heavier components stay opt-in** (research notes, decision 2). The installer
  installs the Basic tier only. Media and optional packages (GPU torch,
  diarization, OCR, TTS, …) are added afterwards from Diagnostics, as today.
- **Code signing is deferred** until a public release (research notes §8). The
  installer is unsigned, so SmartScreen shows "Windows protected your PC" and
  the user clicks More info → Run anyway.
- **`start.bat`/`start.ps1` stay the source-checkout path**, unchanged. Two
  audiences, two entry points; both run the same `python -m api` and share the
  same requirements files.

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
`TORCH_HOME` and `BAIHE_AUDIO_SEP_MODEL_DIR` at `<data>\model_cache\…`
(Step 80 §5 item 3), always via `setdefault`, so a user's own `HF_HOME` wins.
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
     `.streamlit`, `build`/`dist`, the source-checkout launchers (`start.bat`,
     `start.ps1`, `uninstall.bat`, `make_*.bat`, `uninstall_path_cleanup.ps1`),
     the `PORTABLE`/`PYTHON_VERSION`/`INSTALLED` markers, and developer files.
     (`run_tests.py` does ship: Diagnostics' file-completeness check expects it.)
3. Downloads the embeddable Python zip, checks its pinned SHA-256, extracts it,
   and rewrites the `._pth` (research notes §2).
4. Runs `pip download --only-binary=:all: --platform win_amd64 --python-version 3.12`
   for `requirements-core.txt`, constrained by `constraints.lock.txt` if present,
   else `constraints.txt` (the same rule as `start.bat`), plus pip's own wheel.
   This must run on Windows under Python 3.12, because pip evaluates environment
   markers for the build machine (a Linux download silently drops `colorama` and
   `tzdata`). The script warns otherwise.
5. Writes `manifest.json`, including the unpacked size of the wheels. That size
   is passed to Inno's `ExtraDiskSpaceRequired`, so the free-space check counts
   what pip will unpack (the disk-space preflight, research notes decision 2).
6. Compiles `installer/baihe.iss` with ISCC. Output:
   `build/installer/output/BaiheStudio-Setup-<version>.exe`, about 100 MB
   (measured locally with the current core requirements; roughly 300 MB once
   installed).

`build/` is git-ignored.

## 4. Install flow

1. Wizard: install folder (default `%LOCALAPPDATA%\Programs\Baihe Studio`),
   then the data folder page, then an optional desktop shortcut.
   Free-space check. Setup refuses an install folder that already holds a
   `python\` or `app\` folder Baihe Studio didn't put there: an update would
   overwrite it and an uninstall would delete it. "Baihe Studio's own" means
   its `manifest.json` names the product and this installer's AppId, or this
   app's uninstall entry points at the folder; some other program's
   `manifest.json` doesn't count.
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
   3. `pip install --no-index --find-links <wheels> -r requirements-core.txt -c <constraints>`.
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
`BAIHE_API_PORT=8600` unless already set. `PYTHONNOUSERSITE=1` is set, and the
user's pip-redirecting variables are dropped, so Diagnostics' Install buttons
(`sys.executable -m pip install`) install into the bundled interpreter.

## 6. Tiers: Basic in the installer, the rest through Diagnostics

| Tier | How it gets installed |
|---|---|
| **Basic**: `requirements-core.txt` (FastAPI/uvicorn, requests, anthropic, pandas, …; Streamlit too, until it leaves `requirements-core.txt`) | The installer, offline, from bundled wheels |
| **Media**: `requirements-media.txt` | Diagnostics → the tier's bulk Install button (Step 62), into the bundled interpreter. Needs network. |
| **Optional / GPU torch / engines**: `requirements-optional.txt`, `diagnostics.TORCH_VARIANTS` | Diagnostics' per-package and GPU PyTorch buttons, as today. The CPU fallback and GPU reporting (research notes decision 2) are Diagnostics' existing behaviour; the installer adds no GPU detection of its own. |

This keeps the design's §7 promise: the installer reuses, rather than
replaces, the tiered requirements files and Diagnostics' install machinery.
The installer-side tier picker and prompted GPU/torch opt-in with size
estimates, from the original §3, are **not built**: installing them offline
would multiply the download to several GB. Diagnostics already does this
online with real pip output. Revisit if users want an all-in-one offline
installer.

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
  unusable. When that happens, the release must say "uninstall first" or add
  an `[InstallDelete]` for `python\Lib\site-packages`. This is not needed yet.
- There are no delta updates (research notes §9). An upgrade re-downloads the
  whole ~100 MB installer. Model files are in the data folder and are never
  re-downloaded. The per-component manifest the design keeps
  (`manifest.json`) records versions and hashes for the future updater; nothing
  reads it yet.

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

- Linux (the test suite): `tests/test_installer_payload.py` covers staging,
  exclusions, `._pth`, the wheel command, the manifest and ISCC arguments.
  `tests/test_installer_iss.py` statically checks the `.iss` (per-user install,
  no secrets, sources only from the payload, the data folder, opt-in deletion,
  two Pascal pitfalls that broke the compile) and the workflow (on-demand only,
  pinned Inno Setup, what the smoke test covers). `tests/test_installer_runtime.py`
  covers the launcher and post-install logic with fakes. `tests/test_portable.py`
  covers `data_dir()`.
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
  - **Stop check.** Before stopping, `installer/smoke_child.py` puts a
    long-running stand-in child (`ping`) into the server's Job Object. The
    smoke test asserts that `--stop` reports a clean shutdown, the child is
    gone, and no process from the install folder is left.
  - **Clean-uninstall check.** A second install into new folders is
    clean-uninstalled with `/CLEAN`. The smoke test asserts that the install
    folder, the data folder and a `%TEMP%\baihe_*` folder are gone. It also
    asserts that a non-Baihe `%TEMP%` folder, a file next to the folders, and
    the first install's data folder are untouched.
- Still owed, from a person: a real install on the user's PC (steps in the PR),
  the interactive wizard and uninstall dialog, SmartScreen, Edge app window, and
  the research notes' clean-Windows GPU matrix once a GPU tier is added through
  Diagnostics.

## 10. Still open / deferred

- An offline installer-side tier picker and GPU/torch opt-in (§6). Today these
  are online, through Diagnostics.
- An updater that reads `manifest.json`, and delta updates (§7).
- Code signing (§1).
- Moving an existing source-checkout library into the installed app. For now,
  point the data folder at the checkout folder (it holds `library\` and `.env`),
  or copy those two into the data folder.
- `start.bat`/`start.ps1` still tell a source-checkout user without
  `frontend\dist` to download the frontend zip from Releases. Now that installed
  users get the screens inside the installer (2026-09-30), whether that zip
  keeps being published for source checkouts is a planning decision (the
  alternative is `start.bat --build-frontend`).
