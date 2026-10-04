# Status

What's done, in flight and next. Checked against `git log origin/baihe-subtitler` (at 22b3bdc, after #672) and the open PR list on 2026-10-04.
Each session replaces its own entry here when it finishes. Git and the PR list win over this file.

## Where the app is
- React + FastAPI is the only app: `python -m api` serves the API and the built React app (`start.bat` runs it).
  Every backend slice and every planned React page is merged; parity batches B1-B5 are merged (#454, #458, #459, #477, #480).
- Streamlit is deleted (#502). The `pre-streamlit-removal` tag and the `legacy/streamlit` branch keep the last version.
- Remote access: sign-in (Google OIDC, #412/#413), ownership (#414, #445), deny-by-default permissions, the D5 admin and household
  listeners (#526, #528), private-by-default sharing with an admin Sharing screen (#523, #530) and the admin audit log (#522) are merged.
  The route table in `docs/remote-access-decision.md` is enforced by `tests/test_api_permissions.py`.
  Don't expose the API beyond loopback until the Caddy/LAN checks (step 140) are done. The Caddy template (`deploy/caddy/Caddyfile.template`), the Caddy helper (`installer/caddy`) and the owner-run `enable-remote` / `disable-remote` / `status` (#592) are in the repo; the certificate/DDNS health banner is merged (#568).
- Recently merged: Steps 36-44 (#464-#476, #473 auto-backups, refined in #516), 42 maintenance assistant (#532) and 72 fix-as-draft-PR (#479),
  80b Windows installer (#498, hash-pinned wheels #514), 143 import dramas from a backup file (#534), SSE push `GET /api/events` (#494),
  job reattach (#495), glossary-affected re-translate (#525, CLI `--term` #537), opt-in auto-resume of bulk batches (#513),
  `scripts/dependency_canary.py` (#531), maintainer runbook (#533), and the source-adapter steps 111-116 (#482-#490).
- Merged since: WP2 #539, WP3 #543, WP4 #540, backup ownership #542, import cleanup #541, comic pager #538, step 142 export (#546,
  `POST /api/library/admin/backup/user`, PC only; not included: tracked series and other `sources.db` data, the voice bank, settings,
  other profiles' reading data), the WP5 boot service (#575) with its port selection (#597), Caddy and owner-run enable/disable/status (#592),
  the library temp folder (#595), the service's `set-port` command, the "Baihe Studio service" Start-menu menu and the Setup lock (#599), the Diagnostics "Ports" panel (#602), the Workspace and Sources e2e checks (#601) and the translation-memory e2e fix (#603).

Merged 2026-10-03 and 2026-10-04 (#661-#675):
- Transcription says plainly when it isn't installed and how to install it (#661).
- Transcribe splits long lines at sentence ends and re-assigns speakers from saved turns (#671).
- Diarization jobs report real stages and progress (#669).
- Transcription shows a time estimate and live elapsed/ETA, and no longer shows 100% before it is done (#668).
- Live capture takes a GPU slot only when Use GPU is on (#667).
- Disk usage view: see what takes space in the data folder, send items to a Trash folder, move backups (#657).
- Library "Get started" card with a first-run translator choice (#673).
- Source tab: the common transcribe path first, tuning folded, and the medium model as the CPU default (#672).
- A nested `db.get_conn()` no longer closes the caller's connection (#675).
- Idle job polls answer 200 with status idle instead of 404 (#666).
- Also merged since #603 and not listed one by one: see `git log origin/baihe-subtitler` (for example the MangaK source #645, removal of the Mangaz source #648 and of the LibreTranslate engine #621, removal of the Diagnostics bug-bundle helpers #638).

## In flight and queued
Open (lead session merges once CI is green):
- #677 (one shared capped body reader) and #676 (Review: re-split long lines and a per-speaker time summary) are open drafts. #640 and #660 are roadmap-only drafts.
- #589 is parked unmerged (see Live capture and SSRF below).
- WP5 is merged except the owner's real-PC checks and network steps: forward router port 443, a domain/DDNS name, the firewall rule `enable-remote` prints, and the Google client values plus `BAIHE_PUBLIC_URL` in `.env`.
- Step 141 build (after its spec).

Source browser-tier status (owner-reported 2026-10; static fetch returned an empty SPA shell, the browser tier was never run for these):
- Miaoqumh, GoDaManhua/Baozimh (godamh.com), Kuaikan, Zero-Sum Online: browser-tier support unverified; the adapters' "no browser needed" notes were not confirmed against the live site.
- Bilibili Manga: chapter import reads the browser-rendered page only; unverified for the `mc<comic>/<episode>` reader (needs a real-site check, signed in for locked chapters).
- Piaotian: Cloudflare challenge on plain requests; stopped by design, not bypassed. Use a saved page from your own browser.

Deferred: Step 108 (adapter interfaces), and the `db.py` split (the `api/schemas.py` split is done: `api/schemas/` package).

Deferred until a need arises (owner decision 2026-09-30):
- A table-of-contents picker, a profile-management screen and a fixture-refresh command.
- Step 108 stays parked. Add the smallest per-site extension only when a real site needs login, scoped search, metadata or a scrape policy. Login goes through a persistent browser profile; the app never collects a username or password. Refactor the shared adapter contract only if repeated cases show it is awkward.
- AI-fallback extras: comics, batch confirm, automatic use of a saved profile, and the two text-only adapters.
- Structural debt: the `init_db` split, private-name reach-ins, import cycles, shared backup helpers, and consolidating the byte-capped reader and redactor.
- NFO/poster sidecars on Send to Jellyfin (option A, parked); design in docs/archive/media-server-metadata-design.md
- A main server with an optional second-machine GPU worker (owner decision 2026-10-04): plan only, nothing built. See `docs/specs/gpu-worker-plan.md`; it reopens the parked M8+ job queue.

Live capture and SSRF (owner decision 2026-09-30):
- #589 (a guarded egress proxy) is parked unmerged. `media.import_url` will be granted to household members (allowlisted Google accounts) and the risk accepted.
- Closing it is needed before granting it to anyone less trusted: ffmpeg whitelist `http,tcp,crypto` with the proxy doing TLS and rewriting playlists, or fetching in Python and piping to ffmpeg, or yt-dlp fetching through the proxy and piping.
- Live capture ignores a Windows system proxy.

Notes:
- #596 removed the Streamlit-only functions `eta_text`, `autotune_subprocess_worker`, `distinct_custom_tags`, `redundant_tts_install_warning`, `manual_lines_that_would_change`, `get_epub_chapter_count`, `lookup_metadata` (and `lookup_metadata_from_text`), `can_probably_embed`, `pages_to_pdf`, `line_audio_clip`, `parse_timestamp`, `unsaved_line_count` and `stage_statuses_from_index`. Docs and specs that still mention them are historical.
- The boot service's port: `BAIHE_API_PORT` is used on a fresh install only (`docs/windows-installer-design.md` §11). The port is changed with Start menu > Baihe Studio service > Change port (`service.py set-port`, #599); once the service is installed its stored port wins and the launcher follows it.
- Auto backup (`services/auto_backup_service.py`) keeps 2 daily and 2 weekly copies, per library. The library `tmp` folder (`storage.TEMP_DIRNAME`: job work folders and partial exports, swept of leftovers at startup, #595) is left out of backups and kept across restores.

Parked import and export follow-ups (owner decision 2026-09-30, revisit only if they cause trouble):
- The chapter list is fetched twice: the import job re-lists, and listing never seeds the raw cache. Only lightnovel_fun's volume walk repeats real page fetches.
- `media_export_service` still builds in the system temp dir (`tempfile.TemporaryDirectory()`, not the library `tmp` folder) and `shutil.move`s to the final path; across drives that is a copy, so a failure can leave a half-copied file.
- Rows written before the at-rest redaction change (`access_attempts`, `source_health`, `tracked_series.last_check_error`, `job_records`) are only scrubbed on read. In-memory job messages are not query-stripped; check that the job API scrubs them.

Resource for the deferred manual Scanlate canvas editor: tldraw (github.com/tldraw/tldraw), an infinite-canvas SDK with custom shapes, tools and drawing. The editor's requirements are in section 5 of `docs/specs/scanlate-api-spec.md`. Check it again if that feature resumes. The Scanlate-specific image editing tools would still need custom work, and its repository says production use requires a license key, so check the license terms first.

## Next
- Remote access, steps 133-140 (other household members and phones use the PC's library). Sign-in, ownership, the D5 listeners, the boot service and the Caddy config with owner-run enable are merged; left: the owner's LAN test with a real certificate and the router port last (140).
- Step 141: spec only (migration-architect) for the standalone PC shell and the "This PC" / "Connect to my PC" toggle.
- Step 142 and 143 are both merged (#546 exports one person's items, #534 imports). Decided by the owner (2026-09-30): tracked series and the voice bank do not travel (the person re-tracks; no dubbing planned), and the export stays PC only.

## Open bugs
- B-20, B-23, B-24: fixed in #466 (merged); B-23's music level still needs the user's listening check.
- Closed 2026-09-30 (user): B-17 (CORS GET-only is by design: the app and API are served from one origin, and Caddy keeps it that way).
- Parked, no work planned: Step 100 (Anki mining), Step 108 (adapter interfaces; see Deferred above), R1-full, R2, R3-full, R4, R7, the M8+ job queue, Docker, per-platform Live capture.

## Owed by the user (can't be checked from a cloud session)
Real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR/EPUBs, a gated-access Hugging Face token
for pyannote diarization, the Qwen3 (Slice 34) real-model check, the BGM-preserving dub listening check, and real-device phone checks.
