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
- Recently merged: Steps 36 (#469), 37 (#464), 38 Benchmark Lab (#471), 39 Jellyfin (#474), 44 follow-up (#470).

## In flight (open draft PRs into baihe-subtitler)
- Parity: B5 Review gaps #480; Sources SO06/SO09/SO10 #457.
- Roadmap steps: 40 #478 and 40b #481 (stacked on #471); 41 #476; 42 #465; 43 auto-backups #473; 60 #472; 72 #479;
  101-105 #468; 106 #460; 107 #467; 110 #461; 111 #486; 112 #485; 114 #482; 116 #484.
- Bug fixes: steps 122-131 (B-02..B-07, B-20, B-23, B-24) #466.
- Other: Scanlate automatic path #463; Benchmark jiwer scoring #483.
- This docs cleanup: branch `docs-instruction-diet`.

## Next
1. Land the open PRs above (lead session merges once CI is green).
2. Streamlit deletion: `pre-streamlit-removal` tag + `legacy/streamlit` branch, then the deletion PRs (retirement plan, section 9).
3. Remote access: step 140 (Caddy config, LAN test with a real certificate, router port last).
4. Step 141: spec only (migration-architect) for the standalone PC shell and the "This PC" / "Connect to my PC" toggle.

## Open bugs not covered by a PR
- B-11: API exports read saved DB lines, not unsaved Streamlit edits (goes away with Streamlit).
- B-17: API CORS allows only GET cross-origin; the Vite proxy is the only supported dev path.

## Owed by the user (can't be checked from a cloud session)
Real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR/EPUBs, a gated-access Hugging Face token
for pyannote diarization, the Qwen3 (Slice 34) real-model check, the BGM-preserving dub listening check, and real-device phone checks.
