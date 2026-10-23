# Windows installer/uninstaller — design and as-built reference

What `installer/` builds and why. `python -m api` serves the API and the
prebuilt React screens from one process on `http://127.0.0.1:8600/`. The
decisions that led here are in
[`archive/windows-installer-research-notes.md`](archive/windows-installer-research-notes.md);
cut narrative from this page is in
[`archive/windows-installer-design-history.md`](archive/windows-installer-design-history.md).

Code: [`installer/`](../installer/) (Inno Setup script, payload builder, and
the two runtime scripts), [`portable.py`](../portable.py) (`data_dir()`),
and [`.github/workflows/windows-installer.yml`](../.github/workflows/windows-installer.yml).

**What is tested where.** The script compiles with Inno Setup 6.7.3, and the
payload and runtime scripts are tested on Linux. The Windows CI smoke test
(silent install, start, `/api/health`, silent uninstall, the service) runs
when started by hand or on an `installer-v*` or `v*` tag, never on PRs (§9).
Anything that needs a person at a real Windows PC (wizard, SmartScreen, the
service menu) is not covered by either (§9).

---

## 1. Decisions this builds on

- **Inno Setup + bundled Python, not a frozen executable.** Freezers fit badly with the app's many optional, sometimes
  mutually exclusive ML backends, and can't reuse the tiered
  `requirements-*.txt` files or Diagnostics' install buttons. Inno Setup is
  free, scriptable, supports silent install/uninstall and gives a real
  Settings → Apps entry. MSI/WiX and conda were ruled out; conda is the
  fallback only if embeddable Python + pip fails in practice.
- **The React screens ship inside the installer.** `frontend/dist` is
  built at package time and served by FastAPI (`api/static_frontend.py`); no
  Node.js is needed on the user's PC.
- **Heavier components stay opt-in** (research notes, heavier-components tiering): the installer
  installs the Basic tier only; the rest comes from Diagnostics.
- **Code signing is not built** (deferred until a public release; research notes §8), so
  SmartScreen shows "Windows protected your PC" (More info → Run anyway).
- **`start.bat` stays the source-checkout path**; it runs the same
  `python -m api` and shares the same requirements files.

## 2. What gets installed, and where

Per-user install, no admin rights (`PrivilegesRequired=lowest`):

| Location | What | Written by | Replaced on upgrade? |
|---|---|---|---|
| `%LOCALAPPDATA%\Programs\Baihe Studio\python\` | Official embeddable Python 3.13.17 x64 (pinned by SHA-256), with `python313._pth` set to: stdlib zip, `.`, `Lib\site-packages`, `..\app`, `import site`. pip and everything pip installs live in its `Lib\site-packages`. | Installer + pip | Interpreter files: yes. Installed packages: kept while the Python minor version stays the same, and core is upgraded in place by pip. A full install deletes the folder first only when `python313.dll` is missing from it, i.e. when it still holds an older minor version. |
| `...\Baihe Studio\app\` | The app's code (allow-by-default copy minus the exclusions in §3), `frontend\dist`, `installer\launcher.py`, `installer\postinstall.py`, and the `INSTALLED` marker | Installer | Yes, wholesale (`[InstallDelete]`), so a module deleted upstream can't linger |
| `...\Baihe Studio\manifest.json` | App version, Python version and hash, wheel list with hashes, installed-size estimate | Installer | Yes |
| `...\Baihe Studio\wheels\` | The bundled core wheels and their `wheels.lock.txt`, kept for an offline restore after a failed Diagnostics install | Installer | Yes, wholesale (`[InstallDelete]`) |
| `%ProgramFiles%\Baihe Studio Services\` (admin-only; with the service task) | Copies of the service script, interpreter, WinSW and Caddy that run elevated (§11) | `service.py install` | Yes, by the service update |
| **Data folder**, default `%LOCALAPPDATA%\Baihe Studio\` (chosen on the "Where to keep your library" page, or with `/DATADIR=`) | `library\` (database, dramas, media, backups, logs), `.env` (settings and API keys), `model_cache\` (Hugging Face, torch and audio-separator caches), `launcher\` (server pid, server log, install log) | The app at runtime | **Never** |

Why a separate data folder: the program folder is replaced on every upgrade
and removed on uninstall, and a multi-GB growing library doesn't belong next
to code. The user can choose another drive for the data folder. Setup refuses
a folder inside the install folder or a whole drive, and one it can't write
to (checked on the page and again in `PrepareToInstall`, because silent
installs skip the page); `postinstall.py` checks the same rules as a backstop.

**Data folder permissions.** A folder inside the user's profile is already
private. A **new** folder Setup creates elsewhere (say `D:\Baihe`) would
inherit that drive's permissions, which often let every account on the PC
read it, and `.env` holds the API keys. So the install step limits it to this
account, SYSTEM and Administrators (`icacls /inheritance:r`, by SID). An
**existing** folder outside the profile keeps its permissions; the data-folder
page warns that other accounts may be able to read it and asks before using it.
Setup records in the marker (`# created-by-setup`) whether it created the
folder, which is what lets a clean uninstall remove the folder itself (§8).
The lock-down runs only when Setup makes the folder, so an update neither
redoes it nor repeats the warning.

`...\Baihe Studio\service\` holds the staged service files Setup copies into the admin folder; it is replaced like `app\`.

**How the app finds its data**: `portable.data_dir()`:

1. `BAIHE_DATA_DIR`, if set (an override for anyone).
2. Otherwise, for an installed copy (an `app\INSTALLED` marker exists; only
   the marker counts, so a source checkout is never taken for an install), the
   first non-comment line of that marker (an absolute path; UTF-8 with BOM, so
   non-ASCII user names work), falling back to `%LOCALAPPDATA%\Baihe Studio`.
   An install whose marker is missing (an interrupted upgrade) is refused by
   the launcher ("run the installer again to repair it"), so it never falls
   back to keeping its library in the program folder (§4 relies on this).
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
     `uninstall.bat`, `make_*.bat`, `uninstall_path_cleanup.ps1`),
     the `PORTABLE`/`PYTHON_VERSION`/`INSTALLED` markers, and developer files.
     (`run_tests.py` does ship: Diagnostics' file-completeness check expects it.)
3. Downloads the embeddable Python zip, checks its pinned SHA-256, extracts it,
   and rewrites the `._pth` (research notes §2).
4. Checks that `installer/wheels.lock.txt` pins every package named in
   `requirements-core.txt` and pip, then runs
   `pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --require-hashes --no-deps -r installer/wheels.lock.txt`.
   The build then re-checks every downloaded wheel (see "Pinned wheels"): a
   wheel with a different SHA-256, a wheel not in the lock, and a locked package
   with no wheel each fail the build, naming the file. There is no unhashed
   fallback and nothing is resolved at build time. (`--update-lock` should run
   on Windows under Python 3.13; see "Pinned wheels".)
5. Stages the boot service (§11): downloads WinSW (pinned by SHA-256), builds
   `caddy.exe` from `installer/caddy` with Go and checks it against
   `CADDY_SHA256`, and lays out the `service\` payload folder with its own
   copy of the embeddable Python.
6. Writes `manifest.json`, including the unpacked size of the wheels, which is
   passed to Inno's `ExtraDiskSpaceRequired` for the free-space check.
7. Compiles `installer/baihe.iss` with ISCC. Output:
   `build/installer/output/BaiheStudio-Setup-<version>.exe`, about 100 MB
   (roughly 300 MB installed).

`build/` is git-ignored.

### Pinned wheels

`installer/wheels.lock.txt` lists `name==version` and `--hash=sha256:...` for
every distribution the installer bundles: `requirements-core.txt`, pip, and all
transitive dependencies, one win_amd64 / CPython 3.13 wheel each. The
verifier (`postinstall.parse_lock` / `verify_wheels`, shared by the build and the
install step) rejects an unpinned line, a package without a hash, a duplicate,
markers or other options, a hash that isn't 64 hex digits, and any wheel that
is extra or missing compared with the lock.

Regenerating on an intentional dependency change (a change to
`requirements-core.txt`, `constraints.txt`, or a version bump):

1. A maintainer, on Windows with Python 3.13 (pip evaluates environment
   markers for the machine it runs on, so a Linux resolve would drop
   Windows-only dependencies such as `tzdata`) and network access to PyPI, runs
   `python installer/build_installer.py --update-lock` (an ordinary
   `pip download` of `requirements-core.txt` + pip, constrained by
   `constraints.txt`; it writes each
   wheel's SHA-256).
2. The maintainer reads `git diff installer/wheels.lock.txt`, checks that only
   the intended packages and versions moved, and commits it with the dependency
   change.
3. CI does not regenerate it. The Windows Installer workflow builds from the
   committed lock and fails on a coverage gap, a rejected hash or a mismatch;
   `tests/test_installer_payload.py` checks on every PR that the lock parses and
   covers `requirements-core.txt`.

The lock proves the installed wheels are the bytes whose hashes were
committed, not that the packages are safe; that is the maintainer's review
of the diff.

## 4. Install flow

1. Wizard: install folder (default `%LOCALAPPDATA%\Programs\Baihe Studio`),
   then the data folder page, then an optional desktop shortcut.
   Free-space check. Setup refuses an install folder that already holds a
   `python\` or `app\` folder Baihe Studio didn't put there (its
   `manifest.json` must name the product and this AppId, or this app's
   uninstall entry must point at the folder).
2. `PrepareToInstall`: check both folders again, note whether the data folder
   is new, check it can be written (a probe file is written and deleted, so an unplugged
   drive stops Setup before anything is replaced), and stop the background service (§11) and
   any server a previous install started (`launcher.py --stop`) so its files
   can be replaced.
3. Copy files. The wheels go to `{app}\wheels` and stay there (an update
   replaces the folder): when a Diagnostics install fails and its restore
   must put a core package back, `pending_install.restore_commands` takes that
   exact version from here with `--no-index --require-hashes`, the hashes taken
   from the app's own `installer\wheels.lock.txt` (never the copy beside the
   wheels), so it works with the network down. Only an installed copy run by
   its bundled Python looks for this folder. Packages with no kept wheel come
   from the index as before.
4. `postinstall.py`, run by the bundled `python.exe -s`:
   1. Writes `app\INSTALLED` with the data folder, then creates the folder.
      The marker comes first, and the launcher refuses to start an install
      without it, so even a failed install never puts the library in the
      program folder.
   2. Limits a new data folder outside the profile to this account (§2); this
      runs right after the log header, before the wheel check.
   3. Re-checks every bundled wheel against the shipped copy of the lock
      (`<wheels>\wheels.lock.txt`; exit code 6 on a mismatch, an extra wheel or
      a missing one) before anything is installed.
   4. Bootstraps pip by running it as a module from its own wheel (`runpy`,
      the equivalent of `python -m pip`; running `pip.whl\pip` directly fails
      on Windows, where pip refuses to modify itself unless run as `-m pip`).
      No network is needed, and nothing is fetched from bootstrap.pypa.io.
   5. Installs the wheels:
      `pip install --no-index --find-links <wheels> --require-hashes --no-deps -r <wheels>\wheels.lock.txt`.
      `PIP_USER`, `PIP_REQUIRE_VIRTUALENV`, `PIP_INDEX_URL` and similar variables
      from the user's environment are dropped first, and `-s` keeps the user's
      own site-packages out.
   6. Checks that the core packages import, then runs `check_setup.py`
      (ffmpeg, JS runtime, CUDA) for the log only.
   7. Logs everything to `<data>\launcher\install.log`.
   If this step fails, Setup shows a plain-words error with the log path.
   It exits with **code 100** (outside Inno's own 1-8) so a silent install can detect the failure, and the
   "Start Baihe Studio now" option is skipped. Its own exit codes are 2 to 6
   (6 is the wheel check). Code **101** means the service step failed or was
   declined (§11).
5. With the service task ticked (the default), Setup then runs the elevated
   service step (§11).
6. Shortcuts: Start menu → Baihe Studio → **Baihe Studio** (runs
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
  `--stop` can end the job by name; from `start.bat` the job is anonymous. The
  server removes the name from its own environment, so the processes it starts
  don't inherit it.
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
- **Safeguards.** The launcher's loopback calls bypass any system proxy, so
  the token only reaches this PC's server. The server takes the token out of
  its environment at startup, so child processes don't inherit it. uvicorn's
  graceful shutdown is capped at 3 s, so an open media stream can't hold the
  clean stop past the launcher's grace.
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
| **Basic**: `requirements-core.txt` (FastAPI/uvicorn, requests, anthropic, numpy, pillow, authlib, httpx, …) | The installer, offline, from bundled wheels |
| **Media**: `requirements-media.txt` | Diagnostics → the tier's bulk Install button, into the bundled interpreter. Needs network. |
| **Optional / GPU torch / engines**: `requirements-optional.txt`, `diagnostics_torch.TORCH_VARIANTS` | Diagnostics' per-package and GPU PyTorch buttons. The CPU fallback and GPU reporting are Diagnostics' behaviour; the installer adds no GPU detection of its own. |

The installer reuses the tiered requirements files and Diagnostics' install
machinery. An installer-side tier picker and GPU/torch opt-in are **not built**
(§10): bundling them would multiply the offline download to several GB.

Known limit: Diagnostics' "will this upgrade break the app?" check runs
the test suite in a throwaway venv. An installed copy has no `tests/` (it is
excluded), and the embeddable Python has no `venv`/`ensurepip`, so that check
reports "incomplete" on an installed copy. It still works from a source checkout.

## 7. Upgrade

Running a newer `BaiheStudio-Setup-<v>.exe`:

- It keeps the same `AppId`, so Windows sees one app. The install folder is
  reused (`UsePreviousAppDir`), and the data folder is reused through
  `GetPreviousData('DataDir')` (`/DATADIR=` overrides it).
- It stops the background service and any running server first
  (`PrepareToInstall` → `launcher.py --stop`); the service is restarted after
  the files are replaced (§11).
  Inno's Restart Manager (`CloseApplications=yes`) covers anything else holding
  files.
- `app\` is replaced wholesale. The `python\` interpreter files are overwritten,
  and its `site-packages` is kept, so optional packages added through
  Diagnostics survive. Then `postinstall.py` runs `pip install` again against
  the new bundled wheels, which changes only what changed.
- The data folder is never touched.
- A Python minor-version bump makes the kept `site-packages` unusable, so `baihe.iss` deletes the `python\` folder (`[InstallDelete]`, `Check: PythonIsOtherVersion`) when the bundled interpreter's DLL (`python313.dll` for 3.13, passed by `build_installer.py` as `/DPythonDll`) isn't already there. The 3.12 → 3.13 move did this once; optional packages added through Diagnostics (torch and the like) must be installed again afterwards. Upgrades within 3.13 keep them, and an app-only update never replaces `python\`.
- There are no delta updates: an upgrade re-downloads the whole ~100 MB
  installer; model files in the data folder are never re-downloaded.

### Release signature (in-app updates)

The release job signs each `v*` release with an Ed25519 key held in the
`BAIHE_RELEASE_SIGNING_KEY` Actions secret, and attaches
`BaiheStudio-Setup-<v>.exe.sig` next to the `.exe` and `.sha256`. Without the
secret it publishes exactly as before and its log says the release is
UNSIGNED; with a malformed secret it publishes nothing. The job still checks
out no repository code: it signs with the runner's `openssl`. It runs in the
`release` environment, so the key can be an environment secret behind a
required reviewer and a `v*` tag rule (a repository secret also works), and
it logs the installer's SHA-256 whether it signs or not, so hand-signing
can be checked against the tag run's own record (docs/runbook.md §1a).

- **What is signed:** the text `baihe-studio-release-signature-v1`, the
  installer's file name and its SHA-256 (`lib/release_signature.signed_message`),
  not the raw bytes. The name carries the version, so an old, correctly
  signed installer can't be re-published under a newer tag (a signed
  downgrade). The hash is taken while streaming, so there's no need to hold a
  600 MB installer in memory (Ed25519 is one-shot). The SHA-256 is computed
  locally from the bytes that are kept and later run. It is never taken from
  the `.sha256` file.
- **Trust anchor:** `installer/release_public_key.txt`. It is shipped in the
  payload (`RUNTIME_INSTALLER_FILES`, required by `check_payload`, which also
  refuses a file holding a private key) and read only from the installed
  app's own folder. It is never fetched. With no key line, updates are
  SHA-256 only and the card says "Not signature-checked". A key file that is
  present but unreadable or malformed refuses every update, and does not fall
  back to SHA-256 only.
- **Checks:** the `.sig` (capped at 1 KB) comes from the same release as the
  `.exe` and `.sha256`, and it is fetched before the installer, so an unsigned
  release is refused before a large download starts. The kept file is
  verified after download, and the private temp copy is verified again
  immediately before Setup starts from it. A missing, malformed or wrong
  signature is refused, nothing is run, and the download is deleted.
  `cryptography` (an installer dependency through `authlib`) does the
  Ed25519; when it is missing and a key is set, updates are refused.
- **Keys, rotation and a leaked key:** docs/runbook.md §1a. A leaked key
  can't be revoked from inside the app. Installed copies trust it until a new
  installer, run by hand, replaces the key file.

### App-only updates

When a release changes only the app (Python code and the built frontend),
an installed copy can take just that layer instead of the ~100 MB installer.
Code: `installer/app_swap.py` (unpack checks, the switch, rollback and
recovery; standard library only), `installer/build_app_zip.py` (the release
side) and `services/app_update_service.py` (offer, download, hand-off).

- **Layers.** The base layer is everything the full installer puts in place
  besides `app\`: the bundled Python, the pinned wheels, WinSW and Caddy.
  The app layer is `BaiheStudio-App-<v>.zip`: the payload's `app\` plus
  `app-manifest.json` (version, `min_base_version`, the base it was built
  against as `installer/app_swap.base_fingerprint` of the payload's
  `manifest.json`, and the fingerprint of a fresh library database's tables
  and columns). The Windows Installer workflow builds it next to the
  installer, and the release job hashes and signs it exactly like the
  installer (same key, same environment rule; the signed text names
  `BaiheStudio-App-<v>.zip`, so an installer signature can't pass for it, or
  the reverse). A copy of the manifest, `BaiheStudio-App-<v>.json`, is
  attached too, for the offer only.
- **Offer.** After each check the app reads that copy. "Update app only" is
  offered when its base equals this install's and this install's base
  version (`base_version` in `manifest.json`, else its `app_version`) is at
  least `min_base_version`; otherwise the status says the release needs the
  full installer, whose flow is unchanged. Only a server the Start-menu
  launcher started can apply it: the boot service's account can't write the
  install folder, and stopping the service needs an administrator, so under
  the service the status says the full installer is needed. No elevated
  step is built for it (owner decision 2026-10-14: leave as is).
- **Download.** Like the installer: size-capped (400 MB), SHA-256 checked,
  and with a release key the `.sig` is fetched first and verified; a missing,
  malformed or wrong one is refused and the download deleted.
- **Apply** (the owner's click; refused while any job runs, so jobs are
  finished or cancelled by the owner, never by the update). A private copy of
  the zip is hashed and signature-checked again; its entries are checked
  (relative paths inside the app folder only, no `..`, absolute or drive
  paths, no links or special files, no Windows device names or names Windows
  would change, no name twice in any case, no `.env`, `library\`,
  `model_cache\`, venv, `INSTALLED` or `PORTABLE`, the files the app needs
  present, at most 1 GB written, counted as written) and the zip's own
  manifest must name the same base. It is unpacked through
  `app-<v>.partial` into a new `<install>\app-<v>`, never into `app\`, and
  the current `INSTALLED` marker is copied in. The server then writes the
  journal (`<install>\app-update\swap.json`, with the server's port),
  copies its own `app_swap.py` there and starts it outside its Job Object.
  One apply runs at a time; a second click is refused until the first has
  handed over or failed. The helper keeps the server's environment except
  its one-time shutdown token, so keys set as `*_KEY` variables reach the
  restarted server.
- **Switch.** The helper stops the app (`launcher.py --stop`) and waits
  until nothing answers on the journal's port, so the server that answers
  next is the new one; an app that won't stop is left running and nothing
  is switched. It then renames
  `app` to `app-<old>` and `app-<new>` to `app`, and runs `launcher.py
  --no-browser` (up to 90 s for `/api/health`, after any queued package
  install). `app\` keeps its name for whatever version is current because
  `python313._pth`, the shortcuts, the boot service and Setup all name it.
  Healthy: `manifest.json` gets the new `app_version` (keeping
  `base_version`), the app's window opens, and every older `app-<v>` copy is
  deleted except the one just replaced (only folders named `app-` plus a
  version number; any other `app-` folder is left alone). Not healthy, or
  a rename that fails: the new version is stopped, the folders are renamed back, the old version
  is started and its window opened, and the next status says what happened
  in plain words (`app-update\result.json`; details in `swap.log`). Until
  the new version has answered, `manifest.json` still names the old one, so
  the library records the old version and the old app can open it again.
- **Locked files and crashes.** A rename Windows refuses for 30 s (a file
  in use) is undone and queued: the old version starts again, and the next
  start from the Start menu runs the switch before anything loads from
  `app\` (the pending-install pattern; if the copied helper is gone, the
  launcher copies `app\installer\app_swap.py` again). Each state and each
  of the two renames is written to the journal, and a rollback works from
  the recorded rename plus what the folders show; it refuses, and deletes
  nothing, when they don't agree. `launcher.py` settles an unfinished
  update before it starts anything: done is finished, anything earlier is rolled back (stopping a
  server the helper left running). For the moment `app\` doesn't exist, the
  helper registers itself under HKCU `RunOnce` (as `!BaiheStudioAppUpdate`:
  the `!` keeps the entry until the command has run, and runs it in Safe
  Mode too), so the next sign-in finishes the rollback. An OS file lock
  keeps the launcher from acting while the helper is still at work. A full
  install deletes `app-update` (the journal), the `RunOnce` value and, only
  in a folder that is a Baihe Studio install, the kept `app-<version>`
  folders, checked by name; uninstall removes the same with the program
  folders.
- **Database.** `db.init_db` only ever adds tables and columns, and the old
  version can't be trusted with a database the new one changed. When the
  manifest's database fingerprint differs from this library's, apply is
  refused unless an automatic backup ("Back up now" in Automatic backups)
  finished within the last day, and the message says so. The swap never
  touches the data folder (`library\`, `.env`, `model_cache\`); the app
  only reads the library's list of tables and columns.
- **Trust.** Unchanged: the same key file, read only from the installed app,
  never fetched; the app layer can replace `installer\release_public_key.txt`
  exactly as the full installer does, and only when signed by the key it
  replaces.

## 8. Uninstall

Settings → Apps → Baihe Studio → Uninstall (or `unins000.exe`):

1. The data-choices dialog runs first, then the background service is
   removed (the uninstall stops if it can't), then `[UninstallRun]` stops the
   server (`launcher.py --stop`).
2. The dialog lists the data folder with four boxes, **all unticked by default**:
   delete my library; delete my saved settings and API keys (`.env`); delete
   downloaded AI models (`model_cache`); and **Remove everything (clean
   uninstall)**, which ticks the other three and asks a second time, naming
   the data folder, before going ahead. When Setup made that folder, the
   warning says plainly that the whole folder goes, including any files the
   user put there. Answering No puts the other boxes back the way they were.
   Cancel aborts the uninstall. Ticking the library box (or Remove everything)
   then asks for the library's size and the exact word `DELETE` (case-sensitive,
   backups live inside the library); anything else keeps the library and
   cancels Remove everything. Same rule as `uninstall.bat`.
3. The program is removed: `{app}\python` (including pip-installed packages),
   `{app}\app` and `{app}\service`, but only when Baihe Studio put them there (the install
   has its `manifest.json`); then the shortcuts and the uninstall entry.
   `{app}` itself is removed if it is empty. Every folder the uninstaller deletes
   (program, data, Trash, temp) goes through `DelTreeNoLinks`: a junction or symbolic
   link anywhere inside, or the named folder itself being one, is removed as a link and
   what it points to is never entered. A folder whose own attributes can't be read is
   left and reported, never deleted. The summary says when a named folder was only a link.
   Residual: a folder swapped for a link between that check and its delete (which needs
   write access to the folder being uninstalled) is not caught.
4. Only the ticked items are deleted, by name (`<data>\library`, `<data>\.env`,
   `<data>\model_cache`, `<data>\baihe_trash`). The data folder's other contents are never touched,
   since it may be a folder the user picked and shares with other files. Two files are removed by
   name on every uninstall: `vtuber_names.json` (the downloaded VTuber list) and
   `vtuber_names.json.part`; no `*.part` pattern is ever deleted there. When the library is
   deleted and a library existed beforehand, what a restore leaves beside it goes too:
   `library.restore-lock`, `library.pre_restore_*` and `.restore_staging_*` (a junction is removed
   as a link, never followed), and the typed-DELETE prompt names them and counts their size.
   With no library folder those copies are never deleted: one may hold the real library. A
   `library.pre_reset_*` copy is kept. Every interactive uninstall ends with a "Left on this PC"
   box (library, settings by name, models, shared caches, Ollama, winget programs); a silent one
   only logs it. The
   launcher's own files (`launcher\server.pid`, `shutdown.token`, `starting.lock`,
   `server.log`, `install.log`) are always removed by name, then `launcher\` if it is empty.
   The data folder itself is removed only if it is then empty.
5. **A silent uninstall (`/VERYSILENT`) never deletes user data**, unless
   `/CLEAN` is also passed.

**Clean uninstall** ("Remove everything", or `/VERYSILENT /CLEAN`):

- **The data folder.** If Setup created it (the marker's `# created-by-setup`
  line), the whole folder goes: library (projects, backups including
  `backups\auto`, logs, `source_cache`, browser profiles,
  `extension_token.txt`), `.env`, `model_cache`, and the
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
  - Hugging Face's cache (`HF_HOME`, else `%USERPROFILE%\.cache\huggingface`),
    PyTorch's (`TORCH_HOME`, else `.cache\torch`) and Ollama's models
    (`OLLAMA_MODELS`, else `.ollama\models`), each with its size;
  - Python, Git, ffmpeg and Node.js, if installed with winget;
  - Deno in `%USERPROFILE%\.deno`;
  - automatic backups pointed at a folder outside the data folder.
- **Failures.** A folder or file that can't be deleted (in use) is listed as
  such, never reported as removed. (Known gap: a `.env` that can't be deleted
  is currently reported in neither list.)
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
  and `tests/test_portable.py` (`data_dir()`). Also related:
  `tests/test_installer_service.py` (the service against a fake `sc`, `icacls`
  and `netsh`), `tests/test_caddyfile_template.py`, `tests/test_shutdown.py`,
  `tests/test_update_service.py` and `tests/test_constraints_lock_parity.py`.
- Windows CI, **on demand** (Actions → Windows Installer → Run workflow, or push
  an `installer-v*` or `v*` tag; never on PRs, because of the minutes budget):
  it builds the `.exe`, uploads it as an artifact, and smoke-tests it. A `v*`
  tag also attaches the `.exe` and its `.sha256` to a GitHub Release once every
  smoke test has passed (`docs/RELEASE.md`). The smoke test runs
  a silent install to a temp folder with `/DATADIR=`, checks the `INSTALLED`
  marker, and checks that no `.env`, `library`, `tests` or `model_cache` got
  installed. It runs `launcher.py --no-browser`, checks `/api/health`, checks
  that `/` is HTML and the JS bundle is served as JavaScript, and checks that
  `/api/library/dramas` creates `library.db` in the data folder, not the
  program folder. Then it runs `--stop`, a silent uninstall, confirms the
  program is gone, and confirms the library and `.env` survived.
  - **Job Object checks.** `installer/smoke_child.py` puts a stand-in child in
    the server's Job Object (`--stop` must end it and every install-folder
    process) and, with `--breakaway-check`, confirms the job refuses
    `CREATE_BREAKAWAY_FROM_JOB` except inside `breakaway_allowed()`.
  - **Clean-uninstall check.** A second install is removed with `/CLEAN`: its
    folders and a `%TEMP%\baihe_*` folder must be gone while a non-Baihe
    temp folder, a neighbouring file and the first install's data are untouched.
  - **Service steps.** Six "Service --" steps install the service by default
    and check it runs at boot on loopback, an update keeps the data, the
    service and its stored port, `set-port` moves it and back, `enable-remote`
    refuses without settings and the Caddyfile validates, and uninstall removes
    the service and keeps the data (§11).
- Only the Windows job proves that pip accepts `installer/wheels.lock.txt` for
  the real win_amd64 downloads and installs, and that the pinned wheels are the
  ones the Windows pip picks.
- Only a person on a real Windows PC can check: the interactive wizard and
  uninstall dialog, SmartScreen, the Edge app window, the service menu and
  elevation prompt, and the research notes' clean-Windows GPU matrix once a GPU
  tier is added through Diagnostics.

## 10. Still open / deferred

- An offline installer-side tier picker and GPU/torch opt-in (§6): not built.
- Delta updates, and an updater that reads the per-component `manifest.json` (§7).
  What exists: Settings → App updates checks the public GitHub Releases
  (`BAIHE_UPDATE_REPO`, default `zkaelxz/U00`; no token) for a newer `v*`
  release, and on the user's clicks downloads the whole installer into
  `<data>\library\updates\`, checks it against the release's `.sha256` and,
  once the copy has a release public key, its Ed25519 `.sig` (§7), and
  opens its normal Setup from a private temp copy (hashed and signature-checked again; the server's
  environment minus the shutdown token, job name and secret-named variables;
  let out of the server's Job Object only for that launch). It picks the
  newest `v*` release that has the installer and its `.sha256`, so other
  releases (`frontend-v*`) don't hide it. The app keeps running
  until the user clicks Install in Setup, which stops the server as in §7; a
  cancelled Setup changes nothing. That SHA-256 comes
  from the same release as the installer, so it detects a broken or truncated
  download, not a tampered release: the `.sig` is what catches that. The version shown
  is `manifest.json`'s `app_version`. A daily check is a setting, off by
  default; nothing is ever downloaded or installed without a click.
  The app sends no credentials, so it reads public releases only; a private
  releases repository answers 404 and the card says no release was found.
  Keep in-app updates on public releases (a separate public, installers-only
  repository in `BAIHE_UPDATE_REPO` works) or use the manual route in
  docs/runbook.md §1. Shipping a token in the app is not an option: every
  installed copy would hold the same secret.
- Code signing (§1): not built.
- Moving an existing source-checkout library into the installed app. For now,
  point the data folder at the checkout folder (it holds `library\` and `.env`),
  or copy those two into the data folder.
- `start.bat` still tells a source-checkout user without
  `frontend\dist` to run `start.bat --build-frontend`; installed users don't
  need it.

## 11. Boot service

The app can run as a **Windows service started at boot**, installed and
removed by this installer, tested by the Windows Installer workflow. Code: [`installer/service.py`](../installer/service.py)
and its `service_*.py` siblings (standard library only), the `service` task in `baihe.iss`, and the WinSW
staging in `build_installer.py`. `tests/test_installer_service.py` drives it
against a fake `sc`/`icacls`/`netsh`; only the workflow's "Service --" steps
prove it on Windows.

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
  somewhere only administrators can write. **Accepted for now; revisit if
  the PC gets other users.**
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
  by default). Per-account sign-in for this listener is **not built**.
  **Accepted on the condition that only the owner uses this PC. If it has
  other Windows accounts, untick the service task (it is ticked by default
  for now).**
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
  prompt: any port from 1024 to 65535 except 8756, 8610 and the
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
  Once the service is installed, its stored port wins: changing or deleting
  `BAIHE_API_PORT`, or running Setup again with it set, does not change it.
  Without the service, removing the variable returns the launcher to 8600.
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

The CI checks the menu only through its non-interactive `-Status`.

**What it does not do.**

- No other listener, certificate, DNS or router change, and no household or
  remote access until the owner runs `enable-remote` (below).
  `docs/remote-access-decision.md` and the API permissions are unchanged.
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
   and `status` print the exact command for the owner to run by hand
   (`firewall_rule_command`: inbound TCP 443, for `caddy.exe` only, private and
   domain profiles) to run by hand in an administrator prompt; the script only ever runs `netsh ... show rule`.
   Forwarding 443 and the domain name (and any dynamic DNS) are the owner's steps
   (`docs/household-access.md`).
4. The service stays local-only until `enable-remote` is run.

The owner's step-by-step guide, with a table of which ports are opened or forwarded (only 443; port 80 is neither), is `docs/household-access.md`.

A second service, `BaiheCaddy` (WinSW, `NT SERVICE\BaiheCaddy`, same privilege
cut, depends on `BaiheStudio`), is installed **disabled and stopped**, with
`caddy.exe` built from `installer/caddy`: stock Caddy plus `rate_limit`, every
module pinned by `go.sum`, Go `go1.26.9` pinned by SHA-256 in the workflow, and
the binary pinned by `CADDY_SHA256` in `build_installer.py`. Its certificates
and ACME key live in `%ProgramFiles%\Baihe Studio Services\caddy-data`
(Caddy, SYSTEM and Administrators only).

`enable-remote [--household-port 8610]` (administrator prompt) refuses, exit
code 2 and nothing changed, unless the data folder's `.env` has the Google
sign-in settings and a `BAIHE_PUBLIC_URL` that is just `https://` and an ASCII
DNS name on the default port, and the port is free and not one of Baihe's own
(`service.household_port_problem`: 8600, 8601, 8756 and the service's own
port). The server doesn't refuse to start over a bad household setting (it
skips the household listener and keeps the PC one), so these checks run first. Then it sets the household
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
gives `8cf980cd...` (`CADDY_SHA256`) on Linux and on Windows only from the
same source bytes: CRLF line endings in `installer/caddy/main.go` (what a
Windows checkout gives without `.gitattributes`) give a different hash
(`d37ffe33...` when last tried; no test pins that value). `.gitattributes` pins
`installer/caddy/*` to LF, and a test checks it.
