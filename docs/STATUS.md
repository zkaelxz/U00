# Status

History lives in `git log`; this file is the current state only.
Checked against `origin/baihe-subtitler` and the PR list on 2026-10-10; those win over this file.

## Where the app is

What works:
- React + FastAPI is the only app: `python -m api` serves the API and the built React app (`start.bat` runs it). Streamlit is gone (`pre-streamlit-removal` tag, `legacy/streamlit` branch).
- Remote access: Google sign-in, ownership, deny-by-default permissions, the D5 admin and household listeners, private-by-default sharing with an admin Sharing screen and the admin audit log are merged. `docs/route-permissions.md` is enforced by `tests/test_api_permissions.py`. Sign-in is optional and off by default (`BAIHE_API_AUTH`); with it off only direct loopback requests are served. The Caddy template (`deploy/caddy/`), the Caddy helper (`installer/caddy`), owner-run `enable-remote` / `disable-remote` / `status` and the certificate/DDNS banner are in the repo. Don't expose the API beyond loopback until the owner's LAN and router checks are done.
- Windows installer with hash-pinned wheels, the boot service with its stored port (`service.py set-port`; `BAIHE_API_PORT` only on a fresh install), the Setup lock, auto-backups (2 daily and 2 weekly per library), one person's export and import from a backup file (PC only; tracked series, the voice bank, settings and other profiles' data don't travel), Disk usage with a restorable Trash, and a library temp folder swept at startup and left out of backups.
- Transcription: Whisper (default `large-v3-turbo` on CPU and GPU, `default_whisper_size()`), the opt-in Qwen3-ASR backend with its own speech detection and automatic language, forced alignment, re-timing of existing lines with the Qwen3 aligner, word timings stored per line and used by every split, per-line spoken language (mixed-language titles), hallucination guards (silence threshold off by default), time estimates from your own runs, the optional ASMR voice detector, "Check timing", "Transcribe this gap", bulk re-transcribe, and a waveform timeline in Review. Measurements: `docs/asr-experiments.md`.
- Translation: Claude, Gemini, DeepSeek, OpenAI and Ollama (local and cloud models); DeepL, Google, LibreTranslate and NLLB are removed. Batches start at scene breaks, thinking is off by default with a per-run option, glossary proposals with counts and a per-series ignore list, built-in language-pack glossaries, bounded glossary LLM calls with Cancel, cost tracking with a monthly cap, and chrF similarity in Benchmark Lab (optional sacrebleu).
- Review: tick-box selection, Compare transcription, re-split presets with a dry-run preview, the English "Fix common errors" pass, speaker naming and merging with undo, line history with a preview before restore, playback and timing hotkeys, and a pop-out window.
- Live: capture fetched in Python through a guarded egress proxy (`services/egress_proxy.py`, `live_fetch.py`) and piped to ffmpeg's stdin; ffmpeg never opens a URL. LocalAgreement-2 streaming recognition, transcript first and translation later, captions over the video.
- Sources: the adapters in `sources/adapters/`, per-source pace levels with automatic slowdown, the browser extension with per-device tokens and whole-chapter capture, subtitle file import (SRT/VTT/ASS/LRC) and LRC export, and lightnovel-crawler as a separate program.
- Navigation: one nav registry, a collapsible left rail, a drawer on narrow screens, Customize menu, Ctrl+K for pages and stages (not titles or lines), a Jobs page, and a Settings upload size limit (default 20 GB; library restore stays capped at 2 GB).
- CLI (`cli.py`): transcribe, translate, bulk translate, align, diarize, dub, qc, glossary, clean-en, set-language, import/export and export-video, at parity with the app for glossary, style guide, locale and names.

Known limits and open decisions:
- `media.import_url` is not granted to household users until the live-capture hardening PR lands (owner decision 2026-10-06; `docs/remote-access-decision.md` lists its own conditions, reconcile the two when that PR is written). Live capture ignores a Windows system proxy.
- Word timings: undo (restore a version, line history) and undoing a re-transcribe bring text back without words; "Merge short lines" and single-line re-transcribe store none; lines transcribed before the feature keep the estimate.
- Hallucination silence threshold: titles stored at the old default 2.0 were reset to 0 once; a deliberately chosen 2.0 must be set again.
- Browser-tier status (owner-reported 2026-10): Miaoqumh, GoDaManhua/Baozimh, Kuaikan and Zero-Sum Online are unverified against the live site; Bilibili Manga is unverified for the `mc<comic>/<episode>` reader; Piaotian's Cloudflare challenge is stopped by design (use a saved page).
- Parked import/export follow-ups (2026-09-30): the chapter list is fetched twice (only lightnovel_fun repeats real fetches); `media_export_service` builds in the system temp dir and moves across drives by copy; rows written before at-rest redaction are scrubbed on read only.
- The Characters merge confirm duplicates `db._folded_row`'s clip rule in `mergeSpeakers.ts` (`leavesVoiceClip`); update both if it changes.
- The `db.py` split is deferred; `api/schemas.py` and `translate_engines.py` are already packages (`api/schemas/`, `engine_backends/`).

## Next
- Remote access, steps 133-140: left is the owner's LAN test with a real certificate and the router port last (140); WP5 network steps (port 443, DDNS, the firewall rule `enable-remote` prints, Google client values and `BAIHE_PUBLIC_URL` in `.env`).
- Step 141: spec only (`docs/specs/step-141-pc-shell-and-connect.md`) for the standalone PC shell and the "This PC" / "Connect to my PC" toggle.
- Next wave: 171 CBZ + full ComicInfo; 162 text-mask fallback (Otsu with light/dark polarity, ML detector deferred); 158 manual timing shift (auto-sync waits on re-timing).
- Follow-ups: `docs/local-agent-backlog.md`. A second-machine GPU worker is plan only (`docs/specs/gpu-worker-plan.md`).

## Backlog decisions (owner, audited 2026-10-06)
- Later or low value: 145 source hashes (after 144), 146, 149, 152 (150 is merged; decide whether it still waits on 147), 154 Trash (extend `baihe_trash` in `services/disk_usage_service.py`, no second trash), 156 (start with an offline scorer), 157, 160, 163 (after 162), 167, 169, 170 (burn-in exists with no codec or quality option; clip and preview paths hard-code `libx264`), 174, 175, 177, 178, 182.
- Re-check first: 153 late-file drift (re-measure now that re-timing is merged). Partly existing: 151 generic OpenAI-compatible engine (`engine_backends/openai_compat.py` holds DeepSeek and OpenAI only; `ollama` alone takes a `base_url`; build only if the owner wants local models, with an Opus review).
- Not building or dropped: 147, 155, 168 (privacy), 172, 179 (upload half), 180 (wait for remote access), 181 (minisign only when distributing beyond the owner; the SHA-256 check stays). 166 undecided until after 165 (overlaps the Qwen3 aligner).
- Deferred until needed (2026-09-30): a table-of-contents picker, a profile screen, a fixture-refresh command; Step 108 adapter interfaces (smallest per-site extension when a real site needs it; login through a persistent browser profile, never a collected password); AI-fallback extras; structural debt (private-name reach-ins, import cycles, shared backup helpers, one byte-capped reader and redactor); NFO/poster sidecars on Send to Jellyfin (`docs/archive/media-server-metadata-design.md`).
- Parked: Step 100 (Anki mining), R1-full, R2, R3-full, R4, R7, the M8+ job queue, Docker, per-platform Live capture. B-17 closed (CORS GET-only is by design). A manual Scanlate canvas editor would start from tldraw (`docs/archive/scanlate-api-spec.md` section 5; check its license first).

## Owed by the user (can't be checked from a cloud session)
Real TTS, ffmpeg/libass, Whisper on GPU, paid LLM keys, real OCR/EPUBs, a gated-access Hugging Face token for pyannote diarization, the Qwen3 real-model check, the BGM-preserving dub listening check, real-device phone checks, and B-23's music level listening check.
