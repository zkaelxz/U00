# Maintainer runbook (one page)

For the person who keeps Baihe running. Run commands from the project folder.

## 1. Refresh the installer lock (pinned Windows wheels)
Do this only after a dependency change (`requirements-core.txt`, `constraints.txt`, or a version bump).
1. On a Windows PC with Python 3.13 and internet: `python installer/build_installer.py --update-lock`
2. Read the change: `git diff installer/wheels.lock.txt`. Only the packages you meant to change should move.
3. Commit `installer/wheels.lock.txt` with the dependency change.
4. On GitHub: Actions -> "Windows Installer" -> Run workflow (set the version). The installer is the uploaded artifact.
5. To publish a version: push a tag such as `v0.1.0` (`git tag v0.1.0 && git push origin v0.1.0`). The workflow builds the installer, runs every smoke test, and only then attaches the `.exe`, its `.sha256` and, when the `BAIHE_RELEASE_SIGNING_KEY` secret is set, its `.sig` (section 1a) to a GitHub Release named after the tag (a version with a `-` is marked pre-release). Download it from the repository's Releases page. Check the download with `Get-FileHash <file> -Algorithm SHA256` against the `.sha256` file. The installer is not code-signed, so Windows SmartScreen warns on first run. Installed apps find the release through Settings -> App updates (it checks the public releases of the repository in `BAIHE_UPDATE_REPO`, default `zkaelxz/U00`, so the repository must stay public or installers move to a public one; the app sends no credentials, so private releases read as "no release found" and must be installed by hand as above). The `.sha256` it checks comes from the same release: it catches a broken download, not a tampered release. The `.sig` does catch a tampered release, once the installed copy has a release public key (section 1a).
- The build fails if a wheel's hash is missing or different from the lock. That is the check working.
- This proves the files match the lock. It does not prove the upstream packages are safe.
- Before bumping one package, test it in a throwaway venv: `python scripts/dependency_canary.py <package>` (runs the offline suite; `--write-pin` caps `constraints.txt` on FAIL; see `docs/testing-and-ci.md`).

## 1a. Release signing key (in-app updates)
An installed copy with a key line in `installer/release_public_key.txt` (in its own app folder, shipped by the installer, never fetched) accepts an in-app update only if the release's `BaiheStudio-Setup-<v>.exe.sig` verifies with that key: Ed25519 over the installer's name (so its version) and SHA-256 (`lib/release_signature.py`). It checks once after download and again on the temp copy just before Setup starts. A missing, malformed or wrong `.sig` is refused and the download deleted. A copy with no key line checks the SHA-256 only, and Settings -> App updates says "Not signature-checked". Keys come from `python tools/release_signing.py` (needs `cryptography`, already in the installer).
**Turn it on (once)**
1. On a PC you trust, in your own terminal: `python tools/release_signing.py generate`. It prints the private key once and the public key. Nothing is written to disk. Never run it through an agent (Claude, OpenCode) and never pipe or log its output: anything that keeps the output keeps the key. It refuses when its output isn't a terminal; don't override that with `--force` unless you know where the output goes.
2. Store the private key on GitHub. Recommended: repository Settings -> Environments -> `release` (the release job already names it; GitHub creates it on the first tag run, or create it yourself first) -> Deployment branches and tags: "Selected branches and tags", add the tag rule `v*` -> Required reviewers: you -> Environment secrets -> Add secret: name `BAIHE_RELEASE_SIGNING_KEY`, value the whole `ed25519-private:...` line. It must be an environment secret on `release`, not a repository secret, for the reviewer and tag rules to protect it. Then each tag's release job waits for your approval before it can use the key. Fallback that also works: Settings -> Secrets and variables -> Actions -> New repository secret with the same name and value. A repository secret reaches the job without the environment's rules. Either way, keep an offline copy (password manager or USB stick), then clear the terminal. Never commit it, paste it into an issue or chat, or put it in the public key file (the installer build refuses a key file holding one, or a key line that isn't one public key).
3. Put the `ed25519-public:...` line in `installer/release_public_key.txt` (below the comments; one key line only), commit and push. Copies installed from the next release on require signed updates. Older copies are still SHA-256-only until they install that release.
4. After the next `v*` tag, open the release job's log. It prints `Installer: BaiheStudio-Setup-<v>.exe sha256 <hash>` and `Signed BaiheStudio-Setup-<v>.exe with ed25519-public:...`. The public key must equal the committed line. Note the hash. Without the secret, the log says `UNSIGNED`, the release has no `.sig`, and copies with a key refuse that update. That is the availability cost of a missing secret: until someone adds the `.sig` by hand (step 5), in-app updates to that version stop working for every signed copy. Unsigned copies are unaffected, so nothing unsafe gets through.
5. Sign by hand (no secret on GitHub, or a missed `.sig`): download the release's `.exe`. Check its SHA-256 (`Get-FileHash <file> -Algorithm SHA256`) against the hash in the log of the tag run you started yourself (step 4), or against an installer you built locally from the same tag. Don't trust only the `.sha256` asset of the same release: whoever can replace the `.exe` there can replace that too. Then set the `BAIHE_RELEASE_SIGNING_KEY` environment variable (or let the tool ask for the key), run `python tools/release_signing.py sign BaiheStudio-Setup-<v>.exe`, then `python tools/release_signing.py verify BaiheStudio-Setup-<v>.exe`, and upload the `.sig` to the same release.
**App-only updates.** The release job also hashes and signs `BaiheStudio-App-<v>.zip` (the app layer, built next to the installer) and attaches its `.sha256`, `.sig` and `BaiheStudio-App-<v>.json`. Its log prints `App update: BaiheStudio-App-<v>.zip sha256 <hash>` and `Signed BaiheStudio-App-<v>.zip ...`. Sign a missed one by hand exactly as in step 5 (`python tools/release_signing.py sign BaiheStudio-App-<v>.zip`). Settings -> App updates offers "Update app only" when the release was built against the same bundled Python, wheels and Caddy as the installed copy and Baihe was started from the Start menu; otherwise it's the full installer as before. Under the boot service it always says the full installer is needed: that is deliberate (no elevated step), not a fault. A failed app update goes back to the previous version by itself and the card says so; `<install folder>\app-update\swap.log` has the steps. If it says it couldn't finish or undo the update, run the full installer: it replaces the app folder and keeps the data folder (docs/windows-installer-design.md, "App-only updates").
**Limits.** A GitHub Actions secret can't be read back, but a workflow run on this repository can use it. Without the `release` environment's reviewer rule, someone who takes over the GitHub account can push a tag and have the workflow sign their build. With the rule, they also need your approval, but they can approve it themselves with your login unless that login is better protected than the key (2FA). Keeping the key off GitHub and signing every release by hand (step 5) narrows it further. But a stolen login could still change the `.exe` or rewrite run logs before you check them. So the hash you sign is only as trustworthy as where you got it: a local build from the tag is the strongest source. The installer is still not Authenticode code-signed (SmartScreen).
**Rotate the key (planned)**
1. Generate a new pair. Put the NEW public line in `installer/release_public_key.txt` and commit it.
2. Release that version signed with the OLD key (leave the old secret in place). Installed copies verify it with the old key they have, and installing it gives them the new key.
3. Replace the `BAIHE_RELEASE_SIGNING_KEY` secret with the new private key. Releases from then on are signed with the new key. Copies that skipped step 2's release refuse them: install that release first, or update by hand.
**If the private key leaks** (seen anywhere it shouldn't be, or GitHub account taken over while the secret was set):
1. Delete the secret on GitHub straight away. From now on, treat any in-app update signed by the old key as untrusted, including ones already published after the leak.
2. Generate a new pair and commit the new public line. Build the release with the new secret (or sign by hand).
3. Don't update installed copies through the app (they still trust the old key). On each PC, download the new installer from the Releases page, check `Get-FileHash` against its `.sha256` and its `.sig` (`python tools/release_signing.py verify ... --public-key <file with the new public line>`), then run it by hand. That installs the new key.
4. Delete or mark untrusted any release published after the leak that you didn't build yourself.

## 2. Run the local test suite
- One area: `python -m pytest -q tests/test_<area>.py`
- Everything: `python -m pytest -q -n auto -p no:cacheprovider -o addopts=""` (takes 10+ minutes)
- Frontend: `cd frontend && npx tsc -b && npx vitest run`
- Browser tests (`frontend/e2e/`) use the Chromium already installed. Never run `playwright install`.
- GitHub CI is the merge gate while the repo is public. If the repo becomes private or Actions minutes run out, the full local suite plus the frontend commands above is the gate.

- Optional pre-push checks: `pip install pre-commit && pre-commit install`, then `pre-commit run` (changed files) before pushing. Hooks: ruff (`ruff.toml`: syntax errors plus unused imports, variables and names, f-strings with no fields, repeated dict keys), trailing whitespace and end-of-file fixing for `*.py`, and a warning for files over 1 MB. CI does not run these; they are advisory. Avoid `--all-files` for now: about 20 existing `.py` files lack a final newline and it would rewrite them.

## 2b. Check which source sites still answer
- `python scripts/source_probe.py` does one polite GET of each site's root (sequential, 2 s apart, no cookies) and prints reachable / challenge-page / http-error / dns-failure / timeout. `--json` for machine output; `--include-set-aside` also lists set-aside sites that answered again. It never edits a file and does nothing when `CI` is set. In a cloud sandbox, whose only way out is a proxy, add `--use-env-proxy` (honours `HTTPS_PROXY` and `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE`, TLS verification stays on); it checks reachability only, a 200 can be a challenge page, so real adapter checks still need the owner's PC.
- After re-vetting a site by hand, update its row in `docs/source-status.json` and run `python scripts/source_status.py` (`--check` verifies).

## 2c. Run the hardware self-test

On the PC: `python -m tools.self_test`. It prints PASS / FAIL / SKIPPED per check and writes `self-test-report.txt` in the data folder (`--out FILE` to choose another). The report holds no key values, paths or URLs; paste it to the assistant. SKIPPED means the hardware or an optional engine is absent; only FAIL makes the exit code 1. `--model NAME` picks the Whisper model for the GPU check; it must already be downloaded, since the test never downloads.

The 13 owner checks (from the 2026-10-10 triage's "Owner checks" items 6-18, a session note since removed from the repo; this table is their only copy) map to it like this. Anything marked "listening/looking only" needs a person with real audio, a stream, a site or a Windows install.

| # | Check | Self-test line |
|---|---|---|
| 6 | Gemma 4 on real Ollama | "Ollama": reachable only; listening/looking only for output quality |
| 7 | OmniVoice (transformers 5.3+) | "TTS engines" |
| 8 | PaddleOCR-VL-For-Manga | "OCR engines" and "torch / CUDA": installed only; page quality, speed and VRAM are looking only |
| 9 | Data folder on UNC / linked folders | listening/looking only |
| 10 | Live delay on a YouTube stream | listening/looking only |
| 11 | Runbook 3b steps 7, 11, 13 | listening/looking only |
| 12 | Live with a real stream, Ollama `think`, DeepSeek | "Ollama" and "API key presence": setup only; the stream is looking only |
| 13 | twmanga tainted-canvas fallback | listening/looking only |
| 14 | Live chunk-1 stall (VRAM) | "GPU faster-whisper" and "VRAM / RAM headroom": the GPU loads and how much is free; the live run is looking only |
| 15 | Title #25 Qwen-only "Check timing" | "torch / CUDA": setup only; thresholds on real audio are listening only |
| 16 | Cancel mid-batch with a real engine | "API key presence": setup only; listening/looking only |
| 17 | Recalibrate `CHRF_PASS_THRESHOLD` | listening/looking only |
| 18 | OCR defaults and offline model paths | "OCR engines": installed only; real-page reading is looking only |

## 3. Restore a backup
Close other work first: a restore is refused while any job runs. Restores are PC-only.
**Automatic backups (Library tools -> "Backup" tab -> "Automatic backups"; Settings -> "Automatic backups" links there)**
- Off until you turn on "Back up automatically". Defaults: daily, database only (text, translations, settings; small and fast). "Include media (audio, video, pages)" adds every file and can be large. "Back up now" adds a copy at once.
- Each run adds a new dated copy (`baihe_snapshot-YYYYMMDD-HHMMSS.zip`, UTC) and then prunes. Kept: one copy per UTC day for the last 2 days, plus the oldest copy of each of the last 2 ISO weeks. A failed backup deletes nothing.
- Each copy's manifest carries this library's id and a copy number, and the library remembers which copies it wrote. Pruning, "All copies" and a folder change touch only those (plus untagged copies dated before the id was made, in the default `backups/auto`). Another PC's copies in a shared folder (also a PC running a hand-copied library folder, which has the same id), untagged copies in a custom folder and unreadable files are listed under "Other or older copies (not managed)" and never pruned. If the folder has a copy with this library's id numbered above its own count (such a copied library, or a `library.db` put back by hand), that run deletes nothing and the log says so; copies it didn't write stay until you delete them by name.
- "Backup folder": empty means `backups/auto` in the library. Changing it moves this library's copies to the new folder (refused while a backup, restore, bulk delete or storage cleanup runs, or if the new folder has a file of the same name this library didn't make). The folder must exist, and be outside the library or in a folder of its own inside the library's `backups/` (not `backups/` itself, `backups/exports` or `backups/database`, where the manual backups go), and not inside another library's folder.
**One drama, from a copy**
1. Library -> Library tools -> "Backup" tab -> "Saved automatic copies" -> "Restore one title...".
2. "Restore from" lists the copies with date, kind (database only or with media) and size. The default is this library's highest copy number, not the newest date (clocks get corrected). If that can't be told for sure (another library's newer copy, untagged copies, equal numbers, a copy with this id numbered higher, dates far out of order, a locked file, a damaged file dated after the default) nothing is chosen: pick a copy. The API answers 409 with `details.reason: "choose_copy"` and the candidates. A copy marked "not managed" shows a warning; check its date and dramas.
3. Find and pick the drama, type RESTORE, confirm. Keep the tab open.
4. The drama keeps its id if free; otherwise it comes back as a new drama titled "... (restored <date>)". Other dramas are not touched. Files come back only from a copy made with media. The API result names the copy used (`snapshot`); the on-screen line does not, so note which copy you picked.
5. "Delete a copy..." removes one copy (default: this library's oldest) or all of this library's copies; type DELETE. A not-managed copy is deleted only when picked by name or with "All copies, including N not managed" (API: `include_unmanaged: true`), with a warning. Deleting all needs `all: true` in the API. Refused while a restore, bulk delete or storage cleanup runs. No undo.
**Whole library, from a .zip made by "Back up library"**
1. Library -> Library tools -> "Backup" tab -> Restore -> choose the .zip, type RESTORE, press Restore. Keep the tab open.
2. This replaces the whole library (dramas, lines, media) with the backup. Everyone is signed out. Sign-in accounts, source settings and this library's backup id and copy number are kept. "Back up library" first if unsure.
- A failed restore leaves the current library in place.

## 3b. Update a server install safely
For an installed copy (Setup .exe) that serves household users. "Data folder" is `%LOCALAPPDATA%\Baihe Studio` unless you chose another in Setup. Steps marked *(not verified)* were read from the code, not run on Windows.
**How an update works (`services/update_service.py`, `installer/baihe.iss`, `docs/windows-installer-design.md` §7, §11)**
1. Update only at the server PC: every `/api/system/update/*` route is PC-only. Settings -> App updates checks GitHub releases (daily only if you turned auto-check on), downloads to `<library>\updates\` and checks the SHA-256 from the same release (catches a bad download, not a tampered release) and, once the copy has a release public key, the release's Ed25519 `.sig` (catches a tampered release; section 1a). The installer is not code-signed. "Install" starts Setup and Setup stops the server (running jobs are cancelled). Running `BaiheStudio-Setup-<version>.exe` yourself does the same.
2. Setup replaces the program folder `app\` wholesale and keeps the data folder (`library\` with `library.db`, `sources.db`, media, `backups\`, `logs\`; `.env`; `model_cache\`). Python's `site-packages` is kept. It asks for administrator rights for the service step, and an update keeps remote access on only if its `.env` settings still pass and the household listener comes back, else it turns it off and says so.
3. Dev copy, not the server: `start.bat` fast-forwards a git checkout on `baihe-subtitler` before launch (never when edited, on another branch, or already running). Put a file named `NOUPDATE` next to `start.bat`, or run `start.bat --no-update`, to hold it back. An installed copy never does this.
**Before you update**
4. Tell the household, then open Jobs and wait until nothing is `running` or `queued`, or cancel what is left. Do not update during a backup or restore. Stopping cancels jobs (6 s grace, then the process ends); nothing resumes on its own (`docs/background-jobs.md` "States"). A run cut short is closed as cancelled with an "interrupted" message, so start it again afterwards.
5. Provider batch translations (Bulk) are the exception: after the restart open the drama's bulk batches panel on the Translate stage and press "Resume pending batches" (or turn on `bulk.auto_resume`, off by default).
6. Back up. In the app at the PC: Library -> Library tools -> "Backup" tab -> "Back up library" (zip in `<data folder>\library\backups\`). That zip leaves out `.env`, sign-in sessions, `extension_token.txt`, saved site sign-ins, source profiles and `source_cache\`, and *(not verified)* copies `sources.db` as a plain file while the app runs. A database-only backup has no `sources.db`.
7. For a copy that is certain to be whole, stop Baihe and copy the folder by hand. Stop both services from an administrator prompt: `"%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" stop`. Then: `robocopy "%LOCALAPPDATA%\Baihe Studio" "D:\BaiheBackup\before-update" /E /XD model_cache`. That includes `.env` (API keys, Google secret): keep it private. Setup starts the services again after the update.
**Why backing up matters: upgrades are one-way**
8. On the first start after an update, `db.init_db` adds new columns to `library.db` (`ALTER TABLE ... ADD COLUMN`) and, once, rebuilds the three line-reference tables (`_migrate_line_refs_to_ids`; it keeps `_backup_step2_*` copies inside `library.db`). `sources.db` gets its own added columns when first opened. Nothing undoes this, and I found no check that stops an older app opening a newer database. Never run an older version on a data folder a newer one has opened.
**After**
9. Open Diagnostics and the app log (`<library>\logs\app.log`), check one drama opens, and check Remote health. After a Baihe update `docs/household-access.md` says to restart Caddy so a changed template is loaded. Sign in once from a phone on mobile data.
**Roll back**
10. Can be undone: the program files (run the older Setup .exe; *not verified*: Setup does not appear to block an older installer) and the data (restore). Cannot be undone: the schema step in 8, and anything household users saved after the update. So roll back the data too.
11. Stop the services (step 7), run the older Setup, then put the data back. Whole library: Library tools -> "Backup" tab -> Restore -> pick the zip -> type RESTORE (section 3). It keeps the current sign-in users and permissions and signs everyone out. Or stop Baihe and `robocopy "D:\BaiheBackup\before-update" "%LOCALAPPDATA%\Baihe Studio" /MIR /XD model_cache`. `/MIR` deletes files not in the backup, so check both paths first.
**Try the new version on a copy of the server's data first (on the dev PC)**
12. Copy the server's backup zip (step 6) to the dev PC. Never copy the server's `.env`, `extension_token.txt` or `%ProgramFiles%\Baihe Studio Services` (Caddy certificates, service config), and never copy the server's `library.db` over a dev one. Users, sessions and the Google sign-in settings must stay with the server.
13. Make a separate data folder so the dev copy's own data is not touched: `set BAIHE_DATA_DIR=D:\baihe-test` and `set BAIHE_API_PORT=8602`, then `start.bat --no-update` (or the checkout of the new version), in the same console. `BAIHE_DATA_DIR` moves `library\`, `.env` and `model_cache\` (`portable.data_dir`). Do not point it at the server's data folder, a network share of it, or a synced copy of it: the test copy would run migrations on the real library and write to it.
14. In the test copy: Library tools -> "Backup" tab -> Restore -> the zip. Then, in Settings, check that no folder setting (automatic-backup folder, saved-comics folder) names a path from the server, and leave automatic backup and paid engines off *(not verified: whether those settings come across in a restore)*. The test copy has no household users and no remote listener (`start.bat` forces `127.0.0.1`); who owns each drama after a full restore is *not verified*.
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

## 5b. Tesla V100 (Volta, compute 7.0)
- Diagnostics -> "GPU PyTorch" recommends the CUDA 12.6 build (`cu126`, same torch 2.11.0 / torchvision 0.26.0 / torchaudio 2.11.0) for any GPU below compute 7.5. The CUDA 12.8 wheels no longer contain Volta kernels, so a cu128 install fails the setup check with "no kernels for your GPU".
- Volta needs a CUDA 12 wheel index; torch 2.14 is the last PyTorch release that has one. Moving past it means a different GPU or a CPU build.
- Transcription (faster-whisper / CTranslate2) runs on a V100 in float16 and int8_float16 and needs only cuBLAS 12. Whisper asks CTranslate2 which compute types the card supports and takes the first of float16, int8_float16, int8_float32, int8, float32 (`whisper_models.gpu_compute_type`), so a GTX 10xx (compute 6.x, no float16) runs int8_float32 on the GPU instead of falling back to the CPU. Volta has no native bfloat16 (torch only emulates it, slowly), so Qwen3 ASR and PaddleOCR-VL load in float16 there.
- Video burn-in always re-encodes on the CPU (libx264): the V100 has no NVENC, and Baihe never asks for it.
- Hardware: the card is passively cooled (it needs case airflow or a duct), takes EPS (CPU-style) power rather than PCIe 8-pin, and the motherboard needs Above 4G Decoding on. NVIDIA's R580 driver branch is the last data-center branch that lists the V100.
- Not re-verified for this page: the driver-branch and torch-2.14 statements come from the owner's research notes; check NVIDIA's and PyTorch's current pages before relying on them.

## Branches to keep
Never delete these when cleaning up branches; they are the only record of the work they hold.
- `legacy/streamlit` (and the `pre-streamlit-removal` tag): the removed Streamlit app.
- `claude/baihe-subtitle-planning-95qyvq`: the earlier planning steps, which exist nowhere else.
- `baihe-subtitler` is the default branch; the old `main` branch no longer exists. Open every PR into `baihe-subtitler`.

## Logs, support report, locked-out admin
- Log: `logs/app.log` in the library folder (installed default: `%LOCALAPPDATA%\Baihe Studio\library\logs`). Diagnostics -> "Log" shows recent lines.
- Header "Report a problem" -> "Copy a report for a bug" copies or downloads a support report (secrets are removed).
- Break glass, on the PC: `python -m api grant-admin <email>` (make or reactivate an admin), `python -m api deactivate <email>` (block a user and end their sessions).
