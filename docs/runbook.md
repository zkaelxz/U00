# Maintainer runbook (one page)

For the person who keeps Baihe running. Run commands from the project folder.

## 1. Refresh the installer lock (pinned Windows wheels)
Do this only after a dependency change (`requirements-core.txt`, `constraints.txt`, or a version bump).
1. On a Windows PC with Python 3.12 and internet: `python installer/build_installer.py --update-lock`
2. Read the change: `git diff installer/wheels.lock.txt`. Only the packages you meant to change should move.
3. Commit `installer/wheels.lock.txt` with the dependency change.
4. On GitHub: Actions -> "Windows Installer" -> Run workflow (set the version). The installer is the uploaded artifact.
5. To publish a version: push a tag such as `v0.1.0` (`git tag v0.1.0 && git push origin v0.1.0`). The workflow builds the installer, runs every smoke test, and only then attaches the `.exe` and its `.sha256` to a GitHub Release named after the tag (a version with a `-` is marked pre-release). Download it from the repository's Releases page. Check the download with `Get-FileHash <file> -Algorithm SHA256` against the `.sha256` file. The installer is not code-signed, so Windows SmartScreen warns on first run. Installed apps find the release through Settings -> App updates (it checks the public releases of the repository in `BAIHE_UPDATE_REPO`, default `zkaelxz/U00`, so the repository must stay public or installers move to a public one; the app sends no credentials, so private releases read as "no release found" and must be installed by hand as above). The `.sha256` it checks comes from the same release: it catches a broken download, not a tampered release.
- The build fails if a wheel's hash is missing or different from the lock. That is the check working.
- This proves the files match the lock. It does not prove the upstream packages are safe.
- Before bumping one package, test it in a throwaway venv: `python scripts/dependency_canary.py <package>` (runs the offline suite; `--write-pin` caps `constraints.txt` on FAIL; see `docs/testing-and-ci.md`).

## 2. Run the local test suite
- One area: `python -m pytest -q tests/test_<area>.py`
- Everything: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""` (takes 10+ minutes)
- Frontend: `cd frontend && npx tsc --noEmit && npx vitest run`
- Browser tests (`frontend/e2e/`) use the Chromium already installed. Never run `playwright install`.
- GitHub CI is the merge gate while the repo is public. If the repo becomes private or Actions minutes run out, the full local suite plus the frontend commands above is the gate.

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
1. Library -> Library tools -> "Backup & storage" -> "Automatic backup copies" -> "Restore one drama...".
2. "Restore from" lists the copies with date, kind (database only or with media) and size. The default is this library's highest copy number, not the newest date (clocks get corrected). If that can't be told for sure (another library's newer copy, untagged copies, equal numbers, a copy with this id numbered higher, dates far out of order, a locked file, a damaged file dated after the default) nothing is chosen: pick a copy. The API answers 409 with `details.reason: "choose_copy"` and the candidates. A copy marked "not managed" shows a warning; check its date and dramas.
3. Find and pick the drama, type RESTORE, confirm. Keep the tab open.
4. The drama keeps its id if free; otherwise it comes back as a new drama titled "... (restored <date>)". Other dramas are not touched. Files come back only from a copy made with media. The API result names the copy used (`snapshot`); the on-screen line does not, so note which copy you picked.
5. "Delete a copy..." removes one copy (default: this library's oldest) or all of this library's copies; type DELETE. A not-managed copy is deleted only when picked by name or with "All copies, including N not managed" (API: `include_unmanaged: true`), with a warning. Deleting all needs `all: true` in the API. Refused while a restore, bulk delete or storage cleanup runs. No undo.
**Whole library, from a .zip made by "Back up library"**
1. Library -> Library tools -> "Backup & storage" -> Restore -> choose the .zip, type RESTORE, press Restore. Keep the tab open.
2. This replaces the whole library (dramas, lines, media) with the backup. Everyone is signed out. Sign-in accounts, source settings and this library's backup id and copy number are kept. "Back up library" first if unsure.
- A failed restore leaves the current library in place.

## 3b. Update a server install safely
For an installed copy (Setup .exe) that serves household users. "Data folder" is `%LOCALAPPDATA%\Baihe Studio` unless you chose another in Setup. Steps marked *(not verified)* were read from the code, not run on Windows.
**How an update works (`services/update_service.py`, `installer/baihe.iss`, `docs/windows-installer-design.md` §7, §11)**
1. Update only at the server PC: every `/api/system/update/*` route is PC-only. Settings -> App updates checks GitHub releases (daily only if you turned auto-check on), downloads to `<library>\updates\` and checks the SHA-256 from the same release (catches a bad download, not a tampered release; the installer is unsigned). "Install" starts Setup and Setup stops the server (running jobs are cancelled). Running `BaiheStudio-Setup-<version>.exe` yourself does the same.
2. Setup replaces the program folder `app\` wholesale and keeps the data folder (`library\` with `library.db`, `sources.db`, media, `backups\`, `logs\`; `.env`; `model_cache\`). Python's `site-packages` is kept. It asks for administrator rights for the service step, and an update keeps remote access on only if its `.env` settings still pass and the household listener comes back, else it turns it off and says so.
3. Dev copy, not the server: `start.bat` fast-forwards a git checkout on `baihe-subtitler` before launch (never when edited, on another branch, or already running). Put a file named `NOUPDATE` next to `start.bat`, or run `start.bat --no-update`, to hold it back. An installed copy never does this.
**Before you update**
4. Tell the household, then open Jobs and wait until nothing is `running` or `queued`, or cancel what is left. Do not update during a backup or restore. Stopping cancels jobs (6 s grace, then the process ends); nothing resumes on its own (`docs/background-jobs.md` "States"). A run cut short is closed as cancelled with an "interrupted" message, so start it again afterwards.
5. Provider batch translations (Bulk) are the exception: after the restart open the drama's bulk batches panel on the Translate stage and press "Resume pending batches" (or turn on `bulk.auto_resume`, off by default).
6. Back up. In the app at the PC: Library -> Library tools -> "Backup & storage" -> "Back up library" (zip in `<data folder>\library\backups\`). That zip leaves out `.env`, sign-in sessions, `extension_token.txt`, saved site sign-ins, source profiles and `source_cache\`, and *(not verified)* copies `sources.db` as a plain file while the app runs. A database-only backup has no `sources.db`.
7. For a copy that is certain to be whole, stop Baihe and copy the folder by hand. Stop both services from an administrator prompt: `"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" stop`. Then: `robocopy "%LOCALAPPDATA%\Baihe Studio" "D:\BaiheBackup\before-update" /E /XD model_cache`. That includes `.env` (API keys, Google secret): keep it private. Setup starts the services again after the update.
**Why backing up matters: upgrades are one-way**
8. On the first start after an update, `db.init_db` adds new columns to `library.db` (`ALTER TABLE ... ADD COLUMN`) and, once, rebuilds the three line-reference tables (`_migrate_line_refs_to_ids`; it keeps `_backup_step2_*` copies inside `library.db`). `sources.db` gets its own added columns when first opened. Nothing undoes this, and I found no check that stops an older app opening a newer database. Never run an older version on a data folder a newer one has opened.
**After**
9. Open Diagnostics and the app log (`<library>\logs\app.log`), check one drama opens, and check Remote health. After a Baihe update `docs/household-access.md` says to restart Caddy so a changed template is loaded. Sign in once from a phone on mobile data.
**Roll back**
10. Can be undone: the program files (run the older Setup .exe; *not verified*: Setup does not appear to block an older installer) and the data (restore). Cannot be undone: the schema step in 8, and anything household users saved after the update. So roll back the data too.
11. Stop the services (step 7), run the older Setup, then put the data back. Whole library: Library tools -> "Backup & storage" -> Restore -> pick the zip -> type RESTORE (section 3). It keeps the current sign-in users and permissions and signs everyone out. Or stop Baihe and `robocopy "D:\BaiheBackup\before-update" "%LOCALAPPDATA%\Baihe Studio" /MIR /XD model_cache`. `/MIR` deletes files not in the backup, so check both paths first.
**Try the new version on a copy of the server's data first (on the dev PC)**
12. Copy the server's backup zip (step 6) to the dev PC. Never copy the server's `.env`, `extension_token.txt` or `%ProgramFiles%\Baihe Studio Services` (Caddy certificates, service config), and never copy the server's `library.db` over a dev one. Users, sessions and the Google sign-in settings must stay with the server.
13. Make a separate data folder so the dev copy's own data is not touched: `set BAIHE_DATA_DIR=D:\baihe-test` and `set BAIHE_API_PORT=8602`, then `start.bat --no-update` (or the checkout of the new version), in the same console. `BAIHE_DATA_DIR` moves `library\`, `.env` and `model_cache\` (`portable.data_dir`). Do not point it at the server's data folder, a network share of it, or a synced copy of it: the test copy would run migrations on the real library and write to it.
14. In the test copy: Library tools -> "Backup & storage" -> Restore -> the zip. Then, in Settings, check that no folder setting (automatic-backup folder, saved-comics folder) names a path from the server, and leave automatic backup and paid engines off *(not verified: whether those settings come across in a restore)*. The test copy has no household users and no remote listener (`start.bat` forces `127.0.0.1`); who owns each drama after a full restore is *not verified*.
15. Open the dramas that matter and run one small job. If it all works, update the server (steps 4-9). Delete `D:\baihe-test` when done.
**Remote access, what is stored where**
16. Sign-in settings (`BAIHE_GOOGLE_CLIENT_ID`, `BAIHE_GOOGLE_CLIENT_SECRET`, `BAIHE_PUBLIC_URL`): the data folder's `.env`. Users, permissions, sessions (hashes only) and the audit log: `library.db` (`api/auth.py`). Household port and the service's port: the service definition and the admin-only `helper\config.json`. Certificates: `%ProgramFiles%\Baihe Studio Services\caddy-data`. The browser-extension token: `<library>\extension_token.txt`, for the PC's own bridge on 8756 only. Tokens for a browser extension used remotely: not yet built, so there is nothing of that kind to copy or lose.

## 4. Renew the certificate
- The Caddy template is `deploy/caddy/Caddyfile.template` and the setup steps are in `docs/household-access.md`. Until you have gone live with it there is nothing to renew. Once remote access is on, Baihe's health banner and Diagnostics show the certificate's days left (warns under 14).
- Caddy gets and renews certificates by itself once your domain points at this PC and TCP 443 reaches Caddy (port 80 isn't opened by the guide; Caddy's TLS-based challenge over 443 is expected to work but is untested here). If it fails, check:
  1. Caddy's logs for certificate errors.
  2. The dynamic DNS record still shows your current home IP.
  3. The router's port forward for 443 is still there.
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
- Header "Report a problem" -> "Copy a report for a bug" copies or downloads a support report (secrets are removed).
- Break glass, on the PC: `python -m api grant-admin <email>` (make or reactivate an admin), `python -m api deactivate <email>` (block a user and end their sessions).
