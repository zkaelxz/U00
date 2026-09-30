# Maintainer runbook (one page)

For the person who keeps Baihe running. Run commands from the project folder.

## 1. Refresh the installer lock (pinned Windows wheels)
Do this only after a dependency change (`requirements-core.txt`, `constraints.txt`, or a version bump).
1. On a Windows PC with Python 3.12 and internet: `python installer/build_installer.py --update-lock`
2. Read the change: `git diff installer/wheels.lock.txt`. Only the packages you meant to change should move.
3. Commit `installer/wheels.lock.txt` with the dependency change.
4. On GitHub: Actions -> "Windows Installer" -> Run workflow (set the version). The installer is the uploaded artifact.
5. To publish a version: push a tag such as `v0.1.0` (`git tag v0.1.0 && git push origin v0.1.0`). The workflow builds the installer, runs every smoke test, and only then attaches the `.exe` and its `.sha256` to a GitHub Release named after the tag (a version with a `-` is marked pre-release). Download it from the repository's Releases page. Check the download with `Get-FileHash <file> -Algorithm SHA256` against the `.sha256` file. The installer is not code-signed, so Windows SmartScreen warns on first run.
- The build fails if a wheel's hash is missing or different from the lock. That is the check working.
- This proves the files match the lock. It does not prove the upstream packages are safe.

## 2. Run the local test suite
- One area: `python -m pytest -q tests/test_<area>.py`
- Everything: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""` (takes 10+ minutes)
- Frontend: `cd frontend && npx tsc --noEmit && npx vitest run`
- Browser tests (`frontend/e2e/`) use the Chromium already installed. Never run `playwright install`.
- GitHub CI is the merge gate until GitHub Actions minutes end (about 2026-10-03); after that the full local suite is the gate.

## 3. Restore a backup
Close other work first: a restore is refused while any job runs. Restores are PC-only.
**Automatic backups (Settings -> "Automatic backups")**
- Off until you turn on "Back up automatically". Defaults: daily, database only (text, translations, settings; small and fast). "Include media (audio, video, pages)" adds every file and can be large. "Back up now" adds a copy at once.
- Each run adds a new dated copy (`baihe_snapshot-YYYYMMDD-HHMMSS.zip`, UTC) and then prunes. Kept: one copy per UTC day for the last 2 days, plus the oldest copy of each of the last 2 ISO weeks. A failed backup deletes nothing.
- "Backup folder": empty means `backups/auto` in the library. Changing it moves all existing copies to the new folder (refused while a backup runs; the folder must exist and be outside the library).
**One drama, from a copy**
1. Library -> "Backup & storage" -> "Automatic backup copies" -> "Restore one drama...".
2. "Restore from" lists the copies, newest first, with date, kind (database only or with media) and size. The default is the newest readable copy; pick an older one if the drama was still fine then.
3. Find and pick the drama, type RESTORE, confirm. Keep the tab open.
4. The drama keeps its id if free; otherwise it comes back as a new drama titled "... (restored <date>)". Other dramas are not touched. Files come back only from a copy made with media. The API result names the copy used (`snapshot`); the on-screen line does not, so note which copy you picked.
5. "Delete a copy..." removes one copy (default: the oldest) or "All copies"; type DELETE. Deleting all needs `all: true` in the API; the UI asks for the confirmation. No undo.
**Whole library, from a .zip made by "Back up library"**
1. Library -> "Backup & storage" -> Restore -> choose the .zip, type RESTORE, press Restore. Keep the tab open.
2. This replaces the whole library (dramas, lines, media) with the backup. Everyone is signed out. Sign-in accounts and source settings are kept. "Back up library" first if unsure.
- A failed restore leaves the current library in place.

## 4. Renew the certificate (not set up yet)
- Not built: the Caddy config template and the certificate steps (work packages WP4/WP5) are not in the repo yet. There is nothing to renew today.
- General guidance for later: Caddy gets and renews certificates by itself once your domain points at this PC and ports 80/443 reach Caddy. If it fails, check:
  1. Caddy's logs for certificate errors.
  2. The dynamic DNS record still shows your current home IP.
  3. The router's port forward for 80/443 is still there.
  4. The PC's clock is correct.
- User-only steps: router changes and the DNS account are yours to do by hand. Nothing here opens a router port for you.

## 5. Compare local and hosted translation (Benchmark lab)
1. Install a local model: `ollama pull <model>` (Ollama must be running).
2. Diagnostics -> Benchmark Lab -> "Golden sets": use "Import golden set" or "Add one case" (source text plus a reference translation, so it can be scored). Put both runs on the same set.
3. "Run a benchmark": Stage = translation; pick the golden set; under "Engines to compare" use "Add engine" for Ollama (your model) and the hosted engine.
4. Press "Estimate cost" first. Paid engines are capped by the monthly cap and stop at it.
5. Press "Start arena". When done, tick the runs in the recent-runs list and press "Compare" to see scores side by side (Model Arena).
6. Use the winner: pick that engine on the drama's Translate page. (Not verified: whether the drama remembers it as its own setting.)

## Logs, support report, locked-out admin
- Log: `logs/app.log` in the library folder (installed default: `%LOCALAPPDATA%\Baihe Studio\library\logs`). Diagnostics -> "Log" shows recent lines.
- Diagnostics -> "Copy a report for a bug" copies or downloads a support report (secrets are removed).
- Break glass, on the PC: `python -m api grant-admin <email>` (make or reactivate an admin), `python -m api deactivate <email>` (block a user and end their sessions).
