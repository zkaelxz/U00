# Windows installer/uninstaller — design and as-built reference

Roadmap Step 80 wrote the design (2026-09-27); Step 80b built it
(2026-09-30, user decision "Yes, start it"). The first version of this
document predates the React + FastAPI migration and described a Streamlit
app. This version describes the app as it is now: `python -m api` serves
the API and the prebuilt React screens from one process on
`http://127.0.0.1:8600/`. Read it with
[`archive/windows-installer-research-notes.md`](archive/windows-installer-research-notes.md),
whose 2026-09-28 decisions are folded in below.

Code: [`installer/`](../installer/) (Inno Setup script, payload builder, the
runtime scripts, and the pinned Caddy build in `installer/caddy/`), [`portable.py`](../portable.py) (`data_dir()`),
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
| `...\Baihe Studio\service\` | WinSW as `BaiheStudio.exe` (the boot service's wrapper), its generated `BaiheStudio.xml`, and `remote-access.json` while remote access is on (§11) | Installer + `service.py` | The wrapper: yes. The generated files: rewritten by `service.py install` |
| `...\Baihe Studio\caddy\` | WinSW as `BaiheCaddy.exe`, the bundled `caddy.exe`, the licence files of everything in them, and the generated `BaiheCaddy.xml` and `Caddyfile` (§11) | Installer + `service.py` | Same as above |
| `...\Baihe Studio\manifest.json` | App version, Python version and hash, wheel list with hashes, service binaries' versions and hashes, installed-size estimate | Installer | Yes |
| **Data folder**, default `%LOCALAPPDATA%\Baihe Studio\` (chosen on the "Where to keep your library" page, or with `/DATADIR=`) | `library\` (database, dramas, media, backups, logs; the boot service's own log in `library\logs\service\`), `.env` (settings and API keys), `model_cache\` (Hugging Face, torch and audio-separator caches), `launcher\` (server pid, server log, install log, `service.log`), `caddy\` (made by the service install, used only while remote access is on: Caddy's logs and, in `caddy\data`, its certificates and ACME account key) | The app at runtime | **Never** |

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
     `installer/` (except the runtime scripts `launcher.py`, `postinstall.py`
     and `service.py`), `.github`, `.claude`,
     `.streamlit`, `build`/`dist`, the source-checkout launchers (`start.bat`,
     `start.ps1`, `uninstall.bat`, `make_*.bat`, `uninstall_path_cleanup.ps1`),
     the `PORTABLE`/`PYTHON_VERSION`/`INSTALLED` markers, and developer files.
     (`run_tests.py` does ship: Diagnostics' file-completeness check expects it.)
3. Downloads the embeddable Python zip, checks its pinned SHA-256, extracts it,
   and rewrites the `._pth` (research notes §2).
4. Checks that `installer/wheels.lock.txt` pins every package named in
   `requirements-core.txt` and pip, then runs
   `pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --require-hashes --no-deps -r installer/wheels.lock.txt`.
   pip itself refuses a file whose hash isn't pinned. The build then re-checks
   every downloaded wheel on its own (see "Pinned wheels" below): a wheel whose
   SHA-256 differs, a wheel that isn't in the lock, and a locked package with no
   wheel each fail the build, naming the file. There is no unhashed fallback.
   Because the lock is complete and `--no-deps` is used, nothing is resolved at
   build time. `--update-lock` must run on Windows under Python 3.12, because
   pip evaluates environment markers for the machine it runs on (a Linux resolve
   drops Windows-only dependencies such as `tzdata`). The first lock was made on
   Linux instead: `uv pip compile --python-platform windows --python-version 3.12`
   for the versions, then `pip download --platform win_amd64 --no-deps` of those
   pins, each hash cross-checked against uv's. The Windows job is what confirms it.
5. Writes `manifest.json`, including the unpacked size of the wheels. That size
   is passed to Inno's `ExtraDiskSpaceRequired`, so the free-space check counts
   what pip will unpack (the disk-space preflight, research notes decision 2).
6. Compiles `installer/baihe.iss` with ISCC. Output:
   `build/installer/output/BaiheStudio-Setup-<version>.exe`, about 100 MB
   (measured locally with the current core requirements; roughly 300 MB once
   installed).

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
   `python installer/build_installer.py --update-lock`. It does an ordinary
   `pip download` of `requirements-core.txt` + pip (constrained by
   `constraints.lock.txt` if present, else `constraints.txt`) and writes the
   SHA-256 of each wheel it got.
2. The maintainer reads `git diff installer/wheels.lock.txt`, checking that only
   the intended packages and versions moved, and commits the file with the
   dependency change.
3. CI does not regenerate it. The Windows Installer workflow builds from the
   committed lock; it fails if the lock doesn't cover `requirements-core.txt`,
   if pip rejects a hash, or if the build's own check finds any mismatch.
   `tests/test_installer_payload.py` also checks on every PR (Linux) that the
   committed lock parses and covers `requirements-core.txt`.

What this proves, honestly: the wheels in the installer are the same files, byte
for byte, as the ones whose hashes were committed. It does not show that those
packages, or the versions the maintainer accepted in step 1, are safe; step 1
trusts whatever PyPI served at that moment, and the review of the diff is a
human's. Its value is that a later change on PyPI, a mirror or the build
machine's network path can't swap in different bytes unnoticed.

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
6. With the **"Run Baihe Studio in the background from startup"** task (on
   by default): the boot services (§11). Setup asks for administrator
   permission for this one step. Declined or failed: Setup says so, exits
   with **code 101**, and the app still starts from the Start menu.

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
  files. If the boot services exist, the old install's `service.py stop` stops
  them before that (an administrator prompt), and after the copy
  `service.py install` refreshes and restarts them (a second prompt). Remote
  access stays on across an update if it's still configured (§11).
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

1. If the boot services exist, `service.py uninstall` (an administrator
   prompt) stops and removes both services, the firewall rule and the
   service accounts' folder permissions, and gives Caddy's folder back the
   data folder's own permissions. If that can't be done, nothing is
   uninstalled (services must not be left pointing at deleted files).
   Then `[UninstallRun]` stops the server (`launcher.py --stop`).
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
- **Caddy's folder** (`<data>\caddy`, certificates included) goes too, deleted
  by `service.py uninstall --purge-caddy-data`, since only administrators and
  the Caddy service can open its certificate folder.
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
  exclusions, `._pth`, the wheel commands, the hash-lock parser and verifier
  (match, mismatch, extra, missing, malformed lines), the manifest and ISCC
  arguments.
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
  - Those two installs opt out of the boot service (`/MERGETASKS=!service`).
    The **service checks** then test the default install, into the default
    folders inside the runner account's profile (§11): the
    BaiheStudio service is Running, starts Automatic, runs as
    `NT SERVICE\BaiheStudio` without SeImpersonatePrivilege, answers
    `/api/health`, and keeps its library and log in the data folder; the
    BaiheCaddy service is Stopped and Disabled; nothing from the install
    listens beyond 127.0.0.1 and there is no firewall rule. An update over it
    keeps `.env`, the library and the running service. `enable-remote`
    without sign-in settings exits 2 and changes nothing; with placeholder
    settings (`https://baihe.invalid`, which never resolves) it starts Caddy
    and adds exactly the one rule (inbound TCP 443, `caddy.exe`, private and
    domain profiles), the household listener is loopback-only, and the client
    secret appears in no file outside `.env`; `disable-remote` undoes it all.
    A silent uninstall removes both services and the rule, keeps the data and
    takes the service account off the data folder; a `/CLEAN` uninstall with
    remote access on removes everything, Caddy's certificate folder included.
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

## 11. Boot services

Owner decisions (2026-09-30): the app runs as a **Windows service started at
boot**, installed and removed by this installer; **Caddy is bundled**; both
are tested by the Windows Installer workflow. Code:
[`installer/service.py`](../installer/service.py) (standard library only; it
ships as `app\installer\service.py`), the `service` task and the service
steps in `baihe.iss`, and the WinSW/Caddy staging in `build_installer.py`.
**Not run on Windows yet:** everything below is checked on Linux with a fake
`sc`/`icacls`/`netsh` (`tests/test_installer_service.py`); only the Windows
Installer workflow's service steps (§9) prove it on Windows.

### The two services

| | BaiheStudio | BaiheCaddy |
|---|---|---|
| Runs | `python\python.exe -s -m api` in `app\` | `caddy\caddy.exe run --config caddy\Caddyfile --adapter caddyfile` |
| Listens | 127.0.0.1:8600 only (`BAIHE_API_HOST=127.0.0.1` forced, as the launcher does); plus 127.0.0.1:`<household port>` while remote access is on | 443 and 80 on every interface, only while remote access is on |
| Start | Automatic (at boot, no sign-in needed) | **Disabled** until `enable-remote`; then Automatic |
| Account | `NT SERVICE\BaiheStudio` | `NT SERVICE\BaiheCaddy` |
| Logs | `<data>\library\logs\service\` (WinSW: the server's output and its own log, rolled at 10 MB, 5 kept) | `<data>\caddy\logs\` (the same, plus Caddy's filtered access log) |
| Stop | Ctrl+C to python first (`stopparentprocessfirst`), 15 s: the server's clean stop runs (§5), then its Job Object ends every child | Ctrl+C, 10 s |
| After a failure | Restart after 10 s, 30 s, then every 60 s (`sc failure`, also on an error exit: `sc failureflag`); the count resets after a day | Same |

BaiheStudio gets the environment the launcher gives the server (§5), set in
the generated `BaiheStudio.xml`; `.env` is still read by the app itself, from
the data folder. One difference: the service can't see a user's own
variables, so the PC-only key form is always on there
(`BAIHE_API_ALLOW_KEY_WRITES=1`; key writes still need a direct loopback
request from the PC), where the launcher lets an explicit `0` opt out. Opening the Start-menu shortcut while the service runs just opens
a window on it (`launcher.py` sees `/api/health` answer).

### Wrapper: WinSW 2.12.0

`python -m api` is a console program, not a service, so something must answer
the Service Control Manager for it. Compared against this installer's stack
(Inno Setup, embeddable Python, hash-pinned downloads from #514):

- **WinSW 2.12.0, chosen.** MIT licence; one 640 KB file (`WinSW.NET461.exe`,
  on the .NET Framework 4.8 that Windows 10 and 11 include), downloaded at
  build time and refused unless its SHA-256 is `WINSW_SHA256`; configured by
  an XML file next to it, which `service.py` writes; stops the child with
  Ctrl+C, rolls its logs by size, and needs nothing installed into Python. 2.12
  is the last stable release (3.x is still alpha). The same binary is staged
  twice, as `BaiheStudio.exe` and `BaiheCaddy.exe` (WinSW reads the `.xml`
  named like itself).
- **NSSM**, not chosen: public domain and similar in function, but its last
  release is 2.24 (2014, a 2017 pre-release), its download site is often
  unreachable, and antivirus products flag it because malware reuses it.
- **pywin32** (`win32serviceutil`), not chosen: it would add a wheel to the
  lock, and its service host (`pythonservice.exe`) needs a post-install DLL
  registration that is known to be fragile with the embeddable Python.

### Least-privilege accounts

Each service runs as its own **virtual account** (`NT SERVICE\<name>`): no
password, nothing to manage, a SID of its own (`service.service_sid`, derived
from the name, so permissions are granted by SID in any Windows language),
and none of LocalSystem's rights. `sc privs` then cuts each one's privileges
to `SeChangeNotifyPrivilege` (passing through folders it can't list, since
the data folder is usually inside a user profile), `SeCreateGlobalPrivilege`
and `SeIncreaseWorkingSetPrivilege`. SeImpersonatePrivilege, which service
accounts otherwise get and which lets code turn itself into LocalSystem, is
dropped.

Folder permissions (`icacls`, by SID; removed again on uninstall):

- BaiheStudio: read and run the install folder; change `python\Lib\site-packages`
  and `python\Scripts` (Diagnostics' Install buttons run pip inside the
  server); change the data folder (library, `.env`, model cache, logs).
- BaiheCaddy: read and run `caddy\`; change `<data>\caddy`. Nothing else in
  the data folder, so not `.env`. Its certificates and ACME account key are in
  `<data>\caddy\data`, which inherits nothing: only Caddy, SYSTEM and
  Administrators.

The trade-off, recorded: the install stays per-user (§2), so the program
folder the services run from can be changed by the signed-in user. Anyone who
can do that can already read the data folder and `.env`, and a changed file
runs as the low-privilege service account without SeImpersonatePrivilege,
not as LocalSystem, so no rights are gained. A machine-wide install in
`Program Files` would close that, at the cost of every existing per-user
install (a different uninstall entry) and of the `PrivilegesRequired=lowest`
design; not chosen for now.

Behaviour that changes because the server runs as its own account, not as the
signed-in user:

- It sees the machine's `PATH`, not the user's: a program installed for one
  user only (for example ffmpeg through a per-user winget install) isn't
  found. Diagnostics shows what's missing; install it for all users.
- Per-user caches outside the data folder (Playwright's browsers in
  `%LOCALAPPDATA%\ms-playwright`, Deno in `%USERPROFILE%\.deno`) are the
  service account's, not the user's; Diagnostics installs them again there.
  The clean uninstall's `%TEMP%` sweep covers the user's `%TEMP%` only.
- Files the service creates are owned by `NT SERVICE\BaiheStudio`; the user
  keeps full access through the data folder's inherited permissions.

### Elevation

Setup stays per-user (`PrivilegesRequired=lowest`). Only `service.py` runs
elevated: directly when Setup already is (`IsAdmin()`, as on CI runners),
otherwise through the Windows prompt (`ShellExec('runas', …)`). One prompt on
a first install, two on an update (stop before the copy, install after), one
on uninstall. Declined on install: exit code 101, Start-menu mode. Declined on
uninstall: nothing is uninstalled. Unticking the task on an update removes the
services.

### Caddy: bundled, pinned, off by default

- **Which Caddy.** The template needs Caddy's `rate_limit` module
  (`github.com/mholt/caddy-ratelimit`), which the official release binaries
  don't include, and caddyserver.com's custom-build download isn't
  reproducible (it builds the plugin's latest commit). So the installer builds
  it: [`installer/caddy/`](../installer/caddy/) is a three-file Go module
  (Caddy 2.11.4 and the rate-limit module at a pinned commit; `go.sum` holds
  the hash of every module, as `wheels.lock.txt` does for the wheels).
  `build_installer.py` builds it for windows/amd64 with Go 1.26.8 (the
  workflow downloads that Go pinned by SHA-256), `-mod=readonly`, `-trimpath`,
  `-buildvcs=false`, no cgo, and **refuses the result unless its SHA-256 is
  `CADDY_SHA256`**. The build is reproducible (measured 2026-09-30: the same
  bytes from different folders and caches), so the hash pins the binary as
  well as its sources. The licence and notice files of every module compiled
  in, and Go's, ship in `caddy\licenses\`, with WinSW's.
- **Off by default.** The BaiheCaddy service is installed **Disabled** and
  nothing is written for it but its XML. A default install listens on nothing
  beyond 127.0.0.1, has no firewall rule, and no Caddyfile.
- **`enable-remote`** (at the PC, from an administrator prompt; no API route):

  ```
  "%LOCALAPPDATA%\Programs\Baihe Studio\python\python.exe" -s "%LOCALAPPDATA%\Programs\Baihe Studio\app\installer\service.py" enable-remote [--household-port 8610]
  ```

  It first checks everything and **changes nothing, exit code 2, if remote
  access isn't configured**: `BAIHE_GOOGLE_CLIENT_ID`,
  `BAIHE_GOOGLE_CLIENT_SECRET` and `BAIHE_PUBLIC_URL` must be in the data
  folder's `.env` (the service can't see variables set with `setx`), and the
  household listener's own startup checks must pass (`api_config`:
  https, a loopback port that isn't 8600 or 8756, punycode names); then
  Caddy's: the public URL must be a DNS name (not an IP or localhost) on the
  default https port, and the household port can't be 80 or 443. Only then it
  writes `caddy\Caddyfile` from `deploy/caddy/Caddyfile.template` (the three
  `{$…}` settings written in; it refuses a template that no longer says
  `admin off` or that needs any other setting), restarts BaiheStudio with
  `BAIHE_API_HOUSEHOLD_PORT`, sets BaiheCaddy to Automatic, adds the firewall
  rule and starts Caddy, checking that 443 answers. If a step fails, it undoes
  them all. `disable-remote` stops and disables Caddy, removes the rule, and
  restarts BaiheStudio without the household listener. `status` shows both
  services, remote access and the rule (no administrator rights needed).
- **Firewall.** One rule, **"Baihe Studio remote access - Caddy HTTPS"**:
  inbound, allow, TCP **443** only, for the bundled `caddy.exe` only, on the
  **private and domain** profiles only (a home network marked Public gets no
  access; mark it Private). Added by `enable-remote`, removed by
  `disable-remote` and by uninstall. There is no rule for port 80 (Caddy then
  gets its certificate with the TLS-ALPN challenge on 443, and outside
  `http://` doesn't redirect), for 8600, the household port, 8756 or python.
- **Never the router.** Nothing in `service.py` or the installer opens a
  router port or uses UPnP, NAT-PMP or any other way to be reachable from
  outside (`tests/test_installer_service.py` checks the source for them).
  Forwarding 443 on the router stays the owner's own last step
  ([`household-access.md`](household-access.md)).
- **Secrets.** Caddy needs none: Google sign-in is Baihe's. The client secret
  stays in `<data>\.env` (this user, SYSTEM, Administrators and
  BaiheStudio); it is never written to the Caddyfile, the service XML, a
  command line or `service.log` (checked by the tests and by the workflow).
  Caddy's only secrets, its certificate keys and ACME account key, are in
  `<data>\caddy\data` (Caddy, SYSTEM and Administrators only). A DNS-challenge
  token would go there too, not in the Caddyfile; no DNS-challenge build is
  bundled.
- **Admin endpoint.** `admin off` is in the template, and `service.py` refuses
  to render a template without it.
- An update keeps remote access on if it is still configured (the Caddyfile is
  rendered again from the new template); if it no longer is, the update turns
  it off and says so.

### Still open

- A real install on the owner's PC (prompts, a reboot, a Windows update
  restart), and the first certificate through the router.
- The "Stop Baihe Studio" shortcut stops only a server the launcher started;
  with the service it does nothing (stop the "Baihe Studio" service in
  Services instead). Service control from the shortcut would need the user to
  be granted stop and start rights on the service.
- Health monitoring and a banner are separate work (WP5 monitoring).
