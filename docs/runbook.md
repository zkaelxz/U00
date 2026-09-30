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
- Before bumping one package, test it in a throwaway venv: `python scripts/dependency_canary.py <package>` (runs the offline suite; `--write-pin` caps `constraints.txt` on FAIL; see `docs/testing-and-ci.md`).

## 1b. Update the bundled Caddy or WinSW (installer boot services)
Only on purpose (a security release, or a module the template needs). Design: `docs/windows-installer-design.md` §11.
1. Caddy, with Go and internet: in `installer/caddy`, `go get github.com/caddyserver/caddy/v2@vX.Y.Z` (and the rate-limit module if needed), then `go mod tidy`. To change Go, edit the `toolchain` line in `go.mod`, `CADDY_GO_VERSION` in `installer/build_installer.py`, and `GO_URL`/`GO_SHA256` in `.github/workflows/windows-installer.yml` (the SHA-256 from https://go.dev/dl/?mode=json).
2. Build it once: `GOOS=windows GOARCH=amd64 GOAMD64=v1 CGO_ENABLED=0 GOFLAGS=-mod=readonly GOTOOLCHAIN=<go version> go build -trimpath -buildvcs=false -ldflags="-s -w" -o caddy.exe .` twice, from two different folders; both SHA-256s must match. Put that hash in `CADDY_SHA256` and `CADDY_VERSION` in `build_installer.py`.
3. Read `git diff installer/caddy/go.mod installer/caddy/go.sum`: only the modules you meant to change should move. Commit with the hash.
4. WinSW: change `WINSW_VERSION`, `WINSW_URL` and `WINSW_SHA256` together (the hash of the downloaded `WinSW.NET461.exe`), and `installer/licenses/WinSW-LICENSE.txt` if its licence changed.
5. Run the Windows Installer workflow: it rebuilds Caddy on Windows and fails if the hash differs. That is the check working.

## 2. Run the local test suite
- One area: `python -m pytest -q tests/test_<area>.py`
- Everything: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""` (takes 10+ minutes)
- Frontend: `cd frontend && npx tsc --noEmit && npx vitest run`
- Browser tests (`frontend/e2e/`) use the Chromium already installed. Never run `playwright install`.
- GitHub CI is the merge gate until GitHub Actions minutes end (about 2026-10-03); after that the full local suite is the gate.

## 2b. Check which source sites still answer
- `python scripts/source_probe.py` does one polite GET of each site's root (sequential, 2 s apart, no cookies) and prints reachable / challenge-page / http-error / dns-failure / timeout. `--json` for machine output; `--include-set-aside` also lists set-aside sites that answered again. It never edits a file and does nothing when `CI` is set.
- After re-vetting a site by hand, update its row in `docs/source-status.json` and run `python scripts/source_status.py` (`--check` verifies).

## 3. Restore a backup
Close other work first: a restore is refused while any job runs. Restores are PC-only.
**Automatic backups (Settings -> "Automatic backups")**
- Off until you turn on "Back up automatically". Defaults: daily, database only (text, translations, settings; small and fast). "Include media (audio, video, pages)" adds every file and can be large. "Back up now" adds a copy at once.
- Each run adds a new dated copy (`baihe_snapshot-YYYYMMDD-HHMMSS.zip`, UTC) and then prunes. Kept: one copy per UTC day for the last 2 days, plus the oldest copy of each of the last 2 ISO weeks. A failed backup deletes nothing.
- Each copy's manifest carries this library's id and a copy number, and the library remembers which copies it wrote. Pruning, "All copies" and a folder change touch only those (plus untagged copies dated before the id was made, in the default `backups/auto`). Another PC's copies in a shared folder (also a PC running a hand-copied library folder, which has the same id), untagged copies in a custom folder and unreadable files are listed under "Other or older copies (not managed)" and never pruned. If the folder has a copy with this library's id numbered above its own count (such a copied library, or a `library.db` put back by hand), that run deletes nothing and the log says so; copies it didn't write stay until you delete them by name.
- "Backup folder": empty means `backups/auto` in the library. Changing it moves this library's copies to the new folder (refused while a backup, restore, bulk delete or storage cleanup runs, or if the new folder has a file of the same name this library didn't make). The folder must exist, and be outside the library or in a folder of its own inside the library's `backups/` (not `backups/` itself, `backups/exports` or `backups/database`, where the manual backups go), and not inside another library's folder.
**One drama, from a copy**
1. Library -> "Backup & storage" -> "Automatic backup copies" -> "Restore one drama...".
2. "Restore from" lists the copies with date, kind (database only or with media) and size. The default is this library's highest copy number, not the newest date (clocks get corrected). If that can't be told for sure (another library's newer copy, untagged copies, equal numbers, a copy with this id numbered higher, dates far out of order, a locked file, a damaged file dated after the default) nothing is chosen: pick a copy. The API answers 409 with `details.reason: "choose_copy"` and the candidates. A copy marked "not managed" shows a warning; check its date and dramas.
3. Find and pick the drama, type RESTORE, confirm. Keep the tab open.
4. The drama keeps its id if free; otherwise it comes back as a new drama titled "... (restored <date>)". Other dramas are not touched. Files come back only from a copy made with media. The API result names the copy used (`snapshot`); the on-screen line does not, so note which copy you picked.
5. "Delete a copy..." removes one copy (default: this library's oldest) or all of this library's copies; type DELETE. A not-managed copy is deleted only when picked by name or with "All copies, including N not managed" (API: `include_unmanaged: true`), with a warning. Deleting all needs `all: true` in the API. Refused while a restore, bulk delete or storage cleanup runs. No undo.
**Whole library, from a .zip made by "Back up library"**
1. Library -> "Backup & storage" -> Restore -> choose the .zip, type RESTORE, press Restore. Keep the tab open.
2. This replaces the whole library (dramas, lines, media) with the backup. Everyone is signed out. Sign-in accounts, source settings and this library's backup id and copy number are kept. "Back up library" first if unsure.
- A failed restore leaves the current library in place.

## 4. Renew the certificate
- The Caddy template is `deploy/caddy/Caddyfile.template` and the setup steps are in `docs/household-access.md`. Until you have gone live with it there is nothing to renew; renewal monitoring (WP5) is not built yet.
- Caddy gets and renews certificates by itself once your domain points at this PC and ports 80/443 reach Caddy (or with a DNS-challenge build). If it fails, check:
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
- Installed app running as a service: its console output is in `library\logs\service\` (rolled at 10 MB), Caddy's in `caddy\logs\` in the data folder, and the service steps' own record in `launcher\service.log`. State: `...\app\installer\service.py status`.
- Diagnostics -> "Copy a report for a bug" copies or downloads a support report (secrets are removed).
- Break glass, on the PC: `python -m api grant-admin <email>` (make or reactivate an admin), `python -m api deactivate <email>` (block a user and end their sessions).
