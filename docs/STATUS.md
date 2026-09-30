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
7. Step 142 export
8. WP5 boot service
9. Step 141 build

Deferred: Step 108 (adapter interfaces), and the `db.py` and `api/schemas.py` splits.

Resource for the deferred manual Scanlate canvas editor: tldraw (github.com/tldraw/tldraw), an infinite-canvas SDK with custom shapes, tools and drawing. Check it again if that feature resumes. The Scanlate-specific image editing tools would still need custom work, and its repository says production use requires a license key, so check the license terms first.

## Next
- Remote access, steps 133-140 (other household members and phones use the PC's library). Sign-in, ownership and the D5 listeners are merged; left: the Caddy config, LAN test with a real certificate, router port last (140).
- Step 141: spec only (migration-architect) for the standalone PC shell and the "This PC" / "Connect to my PC" toggle.
- Step 142 (move a library out to a standalone install; 143, the import side, is merged in #534).

## Open bugs
- B-20, B-23, B-24: fixed in #466 (merged); B-23's music level still needs the user's listening check.
- Closed 2026-09-30 (user): B-11 (Streamlit-only; goes with the Streamlit deletion), B-17 (CORS GET-only is by design: the app and API are served from one origin, and Caddy keeps it that way).
- Parked, no work planned: Step 100 (Anki mining), Step 108 (adapter interfaces; see Deferred above), R1-full, R2, R3-full, R4, R7, the M8+ job queue, Docker, per-platform Live capture.

## Owed by the user (can't be checked from a cloud session)
Real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR/EPUBs, a gated-access Hugging Face token
for pyannote diarization, the Qwen3 (Slice 34) real-model check, the BGM-preserving dub listening check, and real-device phone checks.
