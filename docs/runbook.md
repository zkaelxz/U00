# Maintainer runbook (one page)

For the person who keeps Baihe running. Run commands from the project folder.

## 1. Refresh the installer lock (pinned Windows wheels)
Do this only after a dependency change (`requirements-core.txt`, `constraints.txt`, or a version bump).
1. On a Windows PC with Python 3.12 and internet: `python installer/build_installer.py --update-lock`
2. Read the change: `git diff installer/wheels.lock.txt`. Only the packages you meant to change should move.
3. Commit `installer/wheels.lock.txt` with the dependency change.
4. On GitHub: Actions -> "Windows Installer" -> Run workflow (set the version). The installer is the uploaded artifact.
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
**One drama, from the automatic backup**
1. Library -> "Backup & storage" -> "Automatic backup snapshot" -> "Restore one drama...", pick the drama, type RESTORE, press "Restore drama".
2. Only one snapshot (`baihe_snapshot.zip`) is kept; each new backup replaces it. Schedule it in Settings -> "Automatic backups" (off by default; default folder is `backups/auto` in the library).
3. The result names the drama. It keeps its id if free; otherwise it comes back as a new drama titled "... (restored <date>)". Other dramas are not touched.
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
