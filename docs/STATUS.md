# Status

What's done, in flight and next. Checked against `git log origin/baihe-subtitler` and the open PRs on 2026-09-30.
Each session replaces its own entry here when it finishes. Git and the PR list win over this file.

## Where the app is
- React + FastAPI is the app: `python -m api` serves the API and the built React app (`start.bat` runs it).
  Every backend slice and every planned React page is merged; parity batches B1-B4 are merged (#454, #458, #459, #477).
- Remote access: sign-in (Google OIDC, #412/#413), ownership (#414, #445) and deny-by-default permissions are merged.
  The route table in `docs/remote-access-decision.md` is enforced by `tests/test_api_permissions.py`.
  Don't expose the API beyond loopback until the Caddy/LAN checks (step 140) are done.
- Streamlit (`app.py`, `tabs/`, `ui/`, `ui_theme.py`, `common.py`) is frozen and being deleted by 2026-10-30
  (`docs/streamlit-retirement-plan.md`). Only crash/data-loss fixes that block the migration, deletions, and moves into `services/`.
- Streamlit-only features, decided 2026-09-30 (user): learned-style opt-out is ported (#493 pins it);
  auto-resume of pending batches and EPUB image placeholders are dropped, not ported.
- Recently merged: Steps 36 (#469), 37 (#464), 38 Benchmark Lab (#471), 39 Jellyfin (#474), 41 (#476),
  43 auto-backups (#473), 44 follow-up (#470), 99 (#475), 101-105 (#468), 106 (#460), 107 (#467), 110 (#461);
  parity B5 (#480) and Sources SO06/SO09/SO10 (#457); Steps 142-143 proposed (#496).

## In flight (open PRs into baihe-subtitler)
- Roadmap steps: 40 #478 and 40b #481 (stacked); 42 #465; 60 #472; 72 #479; 80b Windows installer #498;
  111 #486; 112 #485; 113 Fanjiao #489; 114 #482; 115 lightnovel.fun #488; 115b lncrawl #490; 116 #484.
- Platform: SSE push `GET /api/events` #494; React job reattach #495; test isolation from the real library #492.
- Bug fixes: steps 122-131 #466; small review bugs bundle #491; undo restores flags/SFX #487.
- Parity: learned-style opt-out #493.
- Other: Scanlate automatic path #463; Benchmark jiwer scoring #483.
- SSE push (`GET /api/events`: jobs, the bell and Live pushed; polling only as fallback) #494, branch `migration-sse-push`.

## Next
1. Land the open PRs above (lead session merges once CI is green).
2. Streamlit deletion: parity re-check, the user's go-ahead, `pre-streamlit-removal` tag + `legacy/streamlit` branch, then the deletion PRs (retirement plan, section 9).
3. Remote access, steps 133-140 (user needs this, 2026-09-30: other household members and phones use the PC's library). Sign-in (133-134) and ownership are merged; left: the D5 admin listener (PC-only admin, before anyone else logs in), Caddy config, LAN test with a real certificate, router port last (140).
4. Step 141: spec only (migration-architect) for the standalone PC shell and the "This PC" / "Connect to my PC" toggle.
5. Steps 142-143 (proposed, not scheduled): move a library between the hosted setup and a standalone install (143 reuses Step 43's single-drama restore).

## Open bugs
- B-20, B-23, B-24: being fixed in #466 (B-23's music level also needs the user's listening check).
- Closed 2026-09-30 (user): B-11 (Streamlit-only; goes with the Streamlit deletion), B-17 (CORS GET-only is by design: the app and API are served from one origin, and Caddy keeps it that way).
- Parked, no work planned: Step 100 (Anki mining), Step 108 (adapter interfaces, after Streamlit), R1-full, R2, R3-full, R4, R7, the M8+ job queue, Docker, per-platform Live capture.

## Owed by the user (can't be checked from a cloud session)
Real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR/EPUBs, a gated-access Hugging Face token
for pyannote diarization, the Qwen3 (Slice 34) real-model check, the BGM-preserving dub listening check, and real-device phone checks.
