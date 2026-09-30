# Status

What's done, in flight and next. Checked against `git log origin/baihe-subtitler` (at 21e3872, Remove Streamlit #502) on 2026-09-30.
Each session replaces its own entry here when it finishes. Git and the PR list win over this file.

## Where the app is
- React + FastAPI is the only app: `python -m api` serves the API and the built React app (`start.bat` runs it).
  Every backend slice and every planned React page is merged; parity batches B1-B5 are merged (#454, #458, #459, #477, #480).
- Streamlit is deleted (#502). The `pre-streamlit-removal` tag and the `legacy/streamlit` branch keep the last version.
- Remote access: sign-in (Google OIDC, #412/#413), ownership (#414, #445), deny-by-default permissions, the D5 admin and household
  listeners (#526, #528), private-by-default sharing with an admin Sharing screen (#523, #530) and the admin audit log (#522) are merged.
  The route table in `docs/remote-access-decision.md` is enforced by `tests/test_api_permissions.py`.
  Don't expose the API beyond loopback until the Caddy/LAN checks (step 140) are done. No Caddy config is in the repo yet.
- Recently merged: Steps 36-44 (#464-#476, #473 auto-backups, refined in #516), 42 maintenance assistant (#532) and 72 fix-as-draft-PR (#479),
  80b Windows installer (#498, hash-pinned wheels #514), 143 import dramas from a backup file (#534), SSE push `GET /api/events` (#494),
  job reattach (#495), glossary-affected re-translate (#525, CLI `--term` #537), opt-in auto-resume of bulk batches (#513),
  `scripts/dependency_canary.py` (#531), maintainer runbook (#533), and the source-adapter steps 111-116 (#482-#490).

## In flight and queued
Queue, in order (lead session merges once CI is green):
1. WP2 #539
2. WP3 #543
3. WP4 #540
4. Backup ownership #542
5. Import cleanup #541
6. Comic pager #538
7. Step 142 export: done (#546, `POST /api/library/admin/backup/user`, PC only). Not included: tracked series and other `sources.db` data, the voice bank, settings, other profiles' reading data.
8. WP5 boot service
9. Step 141 build

Deferred: Step 108 (adapter interfaces), and the `db.py` and `api/schemas.py` splits.

Deferred until a need arises (owner decision 2026-09-30):
- A table-of-contents picker, a profile-management screen and a fixture-refresh command.
- Step 108 stays parked. Add the smallest per-site extension only when a real site needs login, scoped search, metadata or a scrape policy. Login goes through a persistent browser profile; the app never collects a username or password. Refactor the shared adapter contract only if repeated cases show it is awkward.
- AI-fallback extras: comics, batch confirm, automatic use of a saved profile, and the two text-only adapters.
- Structural debt: the `init_db` split, private-name reach-ins, import cycles, shared backup helpers, and consolidating the byte-capped reader and redactor.

Live capture and SSRF (owner decision 2026-09-30):
- #589 (a guarded egress proxy) is parked unmerged. `media.import_url` will be granted to household members (allowlisted Google accounts) and the risk accepted.
- Closing it is needed before granting it to anyone less trusted: ffmpeg whitelist `http,tcp,crypto` with the proxy doing TLS and rewriting playlists, or fetching in Python and piping to ffmpeg, or yt-dlp fetching through the proxy and piping.
- Live capture ignores a Windows system proxy.

Notes:
- #596 removed the Streamlit-only functions `eta_text`, `autotune_subprocess_worker`, `distinct_custom_tags`, `redundant_tts_install_warning`, `manual_lines_that_would_change`, `get_epub_chapter_count`, `lookup_metadata` (and `lookup_metadata_from_text`), `can_probably_embed`, `pages_to_pdf`, `line_audio_clip`, `parse_timestamp`, `unsaved_line_count` and `stage_statuses_from_index`. Docs and specs that still mention them are historical.
- The boot service's port is chosen with `BAIHE_API_PORT`; run Setup again so the service follows (`docs/windows-installer-design.md` §11).

Parked import and export follow-ups (owner decision 2026-09-30, revisit only if they cause trouble):
- The chapter list is fetched twice: the import job re-lists, and listing never seeds the raw cache. Only lightnovel_fun's volume walk repeats real page fetches.
- `media_export_service` builds in the system temp dir and `shutil.move`s to the final path; across drives that is a copy, so a failure can leave a half-copied file.
- Rows written before the at-rest redaction change (`access_attempts`, `source_health`, `tracked_series.last_check_error`, `job_records`) are only scrubbed on read. In-memory job messages are not query-stripped; check that the job API scrubs them.

Resource for the deferred manual Scanlate canvas editor: tldraw (github.com/tldraw/tldraw), an infinite-canvas SDK with custom shapes, tools and drawing. Check it again if that feature resumes. The Scanlate-specific image editing tools would still need custom work, and its repository says production use requires a license key, so check the license terms first.

## Next
- Remote access, steps 133-140 (other household members and phones use the PC's library). Sign-in, ownership and the D5 listeners are merged; left: the Caddy config, LAN test with a real certificate, router port last (140).
- Step 141: spec only (migration-architect) for the standalone PC shell and the "This PC" / "Connect to my PC" toggle.
- Step 142 and 143 are both merged (#546 exports one person's items, #534 imports). Decided by the owner (2026-09-30): tracked series and the voice bank do not travel (the person re-tracks; no dubbing planned), and the export stays PC only.

## Open bugs
- B-20, B-23, B-24: fixed in #466 (merged); B-23's music level still needs the user's listening check.
- Closed 2026-09-30 (user): B-11 (Streamlit-only; goes with the Streamlit deletion), B-17 (CORS GET-only is by design: the app and API are served from one origin, and Caddy keeps it that way).
- Parked, no work planned: Step 100 (Anki mining), Step 108 (adapter interfaces; see Deferred above), R1-full, R2, R3-full, R4, R7, the M8+ job queue, Docker, per-platform Live capture.

## Owed by the user (can't be checked from a cloud session)
Real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR/EPUBs, a gated-access Hugging Face token
for pyannote diarization, the Qwen3 (Slice 34) real-model check, the BGM-preserving dub listening check, and real-device phone checks.
