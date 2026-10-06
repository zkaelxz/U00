# Status

What's done, in flight and next. Checked against `git log origin/baihe-subtitler` (the "Where the app is" lists below stop at 175d617, after #818; later merges are under Backlog) and the open PR list on 2026-10-06.
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
- Transcribe splits long lines at sentence ends and re-assigns speakers from saved turns (#671). Whisper's word timestamps are kept while the job runs: a line still too long after punctuation is cut at the real pauses between words (at least 0.25 s, nearest the middle among similar ones), timed by the first and last word of each piece. Whisper only, no new setting; checked with fake words only, not real audio or models. Each line's words are now also stored (`lines.word_timings`: text fingerprint plus character offsets and ms times, at most 1000 words / 32 KB, never in line lists, API responses, history snapshots or job records; kept in backups). Re-split presets, re-segmentation (rules, LLM and its preview) and the Review split dialog then cut at real pauses with real times, and the pieces keep their own words; merge joins them. Any text change drops them, so stale words are never used. Only lines transcribed after this change have them (no backfill); older lines keep the estimate.
- Diarization jobs report real stages and progress (#669).
- Transcription shows a time estimate and live elapsed/ETA, and no longer shows 100% before it is done (#668).
- Live capture takes a GPU slot only when Use GPU is on (#667).
- Disk usage view: see what takes space in the data folder, send items to a Trash folder, move backups (#657).
- Library "Get started" card with a first-run translator choice (#673).
- Source tab: the common transcribe path first, tuning folded, and, at the time, the medium model as the CPU default (#672; the CPU default is now large-v3-turbo, same as the GPU).
- A nested `db.get_conn()` no longer closes the caller's connection (#675).
- Idle job polls answer 200 with status idle instead of 404 (#666).
- Also merged since #603 and not listed one by one: see `git log origin/baihe-subtitler` (for example the MangaK source #645, removal of the Mangaz source #648 and of the LibreTranslate engine #621, removal of the Diagnostics bug-bundle helpers #638).

Merged 2026-10-04 and 2026-10-05 (#676-#742):
- Transcription and models:
  - The default Whisper model is `large-v3-turbo` (#730). The CPU fallback is still `medium`, and the label still says turbo is weaker on Japanese and Korean; the public benchmarks below don't fully support that (a follow-up is open).
  - Estimates use your last five runs per model and device, the recorded diarization time and per-stage medians (#721, #731). Every GPU-to-CPU fallback is reported in plain words.
  - Opt-in `qwen3_asr_vad` backend: Qwen3-ASR with its own speech detection (#713, #701). Zero-duration forced-aligner spans are repaired (#694). Speaker detection retries on CPU after a CUDA out-of-memory (#695). Cancel stops a transcription (#687). A cancelled model download no longer leaves a truncated checkpoint (#702).
  - Mixed languages (off by default): each line can carry its own spoken language (`Line.lang`, #727; chip, "Spoken language" select and "Set language" in Review) and transcription can detect the language per speech span (#734). Translation reads it: a line spoken in another language than the title's is tagged in the prompt, an English line is copied across, and NLLB translates each language with its own pipeline. The same holds for bulk (batch API, including its Reflect stages), the stronger-engine and blocked-line retries, the fix-flagged job and the single-line AI tools. Export wrapping is language-agnostic (caller caps); Re-split's audio alignment (English lines included; unverified on real audio) and word-level realign use each line's own language.
  - Measurements are in `docs/asr-experiments.md`: your Chinese and Japanese drama clips, a mixed Korean/Japanese/English clip, and public FLEURS benchmarks for Korean, Japanese and Chinese (#736, #738, #740, #742).
- Review and Characters: name a speaker once for every line, with Undo (#699); merge two speakers with one server-side undo (#728, it never deletes clip files); re-split long lines and a per-speaker time summary (#676); four folds for the lower tools (#696); stacked cards for Characters and Glossary tables on phones (#706).
- Library tools > Disk usage lists voice clips no speaker uses and moves them to the restorable Trash (#735).
- Look and layout: indigo palette, Atkinson font, one type scale (#697); sentence-case copy (#704); calmer phone spacing (#705); a sticky stage strip with a Next button (#690); a simpler Source stage (#715).
- Safety and robustness: every redirect hop in `fetch_static` is checked (#722); the remaining HTTP response reads are capped (#711, #725); turning the extension bridge off stops it at once (#726); `init_db` split into helpers (#679); `api/schemas.py` and `translate_engines.py` split into packages (#691, #700); a shared capped body reader (#677); the `api.auth` import cycle ended (#712).
- Housekeeping: Streamlit-only code removed (#686, #709); commenting standards in CLAUDE.md (#708) and the comment cleanups (#714, #723, #724, #733); roadmap ids removed from user-visible text (#739); verified-dead code removed (#716); Dependabot and a weekly audit are in the repo but only run once `baihe-subtitler` is the default branch (#720); smoke pack (#693, #718); the roadmap's pending manual checks are triaged in `docs/manual-check-triage.md` (#729).
- Plan only, not built: a main server with an optional second-machine GPU worker, `docs/specs/gpu-worker-plan.md` (#741).

Merged 2026-10-05 (#743-#818; the numbers between that this clone's history doesn't show are not listed):
- Navigation (design and status in `docs/design/navigation-proposal.md`):
  - One nav registry feeds the header, the rail and the drawer (#769); a left menu on wide screens (N2, #774); a left drawer below 1024 px replaces the header nav grid and gear menu (N3, #797).
  - The rail starts collapsed below 1280 px when no choice is saved (#787); a long title in the rail is cut off with an ellipsis (#811).
  - Diagnostics and Benchmark Lab show only with `admin.diagnostics`, and Jobs sits under System (#795).
  - Customize menu: each person can hide menu items for themselves; hidden items are also left out of Ctrl+K (#813).
  - Ctrl+K quick search for menu pages and the open title's stages (#814). Titles and lines are not searched yet.
  - Library header duplicates of the rail are dropped and Library tools folds regrouped (#794); list and card pages use an 1800 px column on desktop (#793).
- Jobs:
  - A Jobs page at `#/jobs` sharing one jobs list with the header popover and Diagnostics (#776); `drama_id` and `kind` on the job record (#770); a server-decided `page` field for title-less jobs, and `/api/diagnostics/job-history` is retired (#791).
  - Settings regrouped, with the Jobs section split in three (#801); a short "Confirm delete" label so arming a row doesn't reflow the table (#809).
  - Translation jobs report batch, Reflect and retry-wait progress (#780); fix-flagged and the long-line split honour Cancel (#782, #784).
- Cost: opt-in re-cost of past usage costs for mis-costed Claude models (#789), hardened with a required preview check and a fingerprint (#800); the monthly spend counter resets without raising the cap (#803); Sonnet 5.5, Opus 5.5 and Fable 5.1 added to pricing and unpriced models costed at their tier's highest rate (#790, #783).
- Review:
  - Tick boxes to select lines (#807); "Compare transcription" from the tick-box selection (#810, #818); Re-transcribe… in the line menu (#785).
  - The pop-out window is bigger and shows subtitle text (#777); its video fills the window (#806).
- Spoken language per line: Translate reads each line's own spoken language (#792), including bulk, retry and line-tool paths (#799); alignment and realignment use each line's language and export wrapping is pinned language-agnostic (#798); the Qwen3 aligner aligns English lines (#816).
- Transcription: Qwen3 speech detection detects the language automatically and closes most of the short-utterance accuracy gap (#766); long unpunctuated CJK lines split at spaces between phrases (#786); a 12 GB card no longer trips the low-memory speaker detection warning (#772).
- Benchmarks: a public benchmark on noisy and music-backed audio is in `docs/asr-experiments.md` (#812). Demucs helped only when the background is music alone, hurt with noise and did nothing on clean audio; the README and the Transcribe stage help now say so.
- CI: the browser tests run in four parallel shards (#802).
- Smaller: sticky workspace job pill (#773), live video fullscreen and a Larger video toggle (#775), balance links by the provider keys (#778), a Glossary Suggest terms bar (#779), one "AI engine" label (#804), Diagnostics lists each model engine once (#805), the Assistant off-state links to Settings (#796), Settings jump targets stay in view (#808), empty translations count as batch errors with batches capped at 60 (#781).
- Checked at 175d617: `npx vitest run` 1782 tests passed in 190 files, `npx tsc --noEmit` clean, `pytest --co` collects 10672 tests.

## In flight and queued
Open (lead session merges once CI is green):
- Open PRs at the time of writing (2026-10-06): #836 (re-time lines with the Qwen3 aligner), #838 (English cleanup, Step 173), #843 (waveform timeline, Step 164), #850 (saved upload size limit) and #851 (re-split sensitivity). The breadcrumb (#819) is merged; #640 and #660 (the roadmap-only planning branches) were closed and their content lives in the Backlog below. Referrer-aware Back and copy link (N8), palette titles and lines (N7) and folding the nav decisions into the guidelines (N9) are not built.
- Follow-up sessions listed here earlier (the browser extension check, five docs pages, a `docs/specs/` sweep, the dependency canary and constraints check, Whisper labels from the benchmarks, the `qwen-asr` install check, a note in the merge confirm, a CLI command to set a line's language, a portable ffprobe test fixture) were not re-checked against the code at 175d617; check `git log` before relying on them. The navigation proposal, Qwen3 automatic language and the noisy-audio benchmark are merged (above). No public mixed-language benchmark appears in `docs/asr-experiments.md` (only the one clip comparison).
- #589 (live capture through a guarded egress proxy) is merged (see Live capture and SSRF below).
- WP5 is merged except the owner's real-PC checks and network steps: forward router port 443, a domain/DDNS name, the firewall rule `enable-remote` prints, and the Google client values plus `BAIHE_PUBLIC_URL` in `.env`.
- Step 141 build (after its spec).

Source browser-tier status (owner-reported 2026-10; static fetch returned an empty SPA shell, the browser tier was never run for these):
- Miaoqumh, GoDaManhua/Baozimh (godamh.com), Kuaikan, Zero-Sum Online: browser-tier support unverified; the adapters' "no browser needed" notes were not confirmed against the live site.
- Bilibili Manga: chapter import reads the browser-rendered page only; unverified for the `mc<comic>/<episode>` reader (needs a real-site check, signed in for locked chapters).
- Piaotian: Cloudflare challenge on plain requests; stopped by design, not bypassed. Use a saved page from your own browser.

Deferred: Step 108 (adapter interfaces), and the `db.py` split (the `api/schemas.py` split is done: `api/schemas/` package).

Deferred, owner decision 2026-10-05: the merge confirm in Characters decides whether the leftover voice clip stays on disk with `leavesVoiceClip` (`mergeSpeakers.ts`), which copies the server's rule for when a clip moves (`db._folded_row`). If that rule changes, update both. A server-side boolean in the merge response would remove the duplication; skipped because the only effect of a mismatch is one line of confirm wording.

Deferred until a need arises (owner decision 2026-09-30):
- A table-of-contents picker, a profile-management screen and a fixture-refresh command.
- Step 108 stays parked. Add the smallest per-site extension only when a real site needs login, scoped search, metadata or a scrape policy. Login goes through a persistent browser profile; the app never collects a username or password. Refactor the shared adapter contract only if repeated cases show it is awkward.
- AI-fallback extras: comics, batch confirm, automatic use of a saved profile, and the two text-only adapters.
- Structural debt: the `init_db` split, private-name reach-ins, import cycles, shared backup helpers, and consolidating the byte-capped reader and redactor.
- NFO/poster sidecars on Send to Jellyfin (option A, parked); design in docs/archive/media-server-metadata-design.md
- A main server with an optional second-machine GPU worker (owner decision 2026-10-04): plan only, nothing built. See `docs/specs/gpu-worker-plan.md`; it reopens the parked M8+ job queue.

Live capture and SSRF (owner decision 2026-09-30, updated 2026-10-06):
- #589 is merged: live capture fetches the stream in Python through a guarded egress proxy (`services/egress_proxy.py`) and pipes it to ffmpeg's stdin (`services/live_fetch.py`); ffmpeg no longer opens any URL. The residual risks and the grant conditions are in `docs/remote-access-decision.md` ("Live capture residual risk").
- Owner decision 2026-10-06: do not grant `media.import_url` to household users until the follow-up hardening PR lands. Until then it stays off for them; starting live capture at the PC is unaffected. That PR is not in the open list above, so its scope is not recorded here (not verified). `docs/remote-access-decision.md` lists its own three conditions for granting; reconcile the two when the hardening PR is written.
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

## Backlog (audited 2026-10-06)
Roadmap Steps 144-182 with the owner's decisions, first checked against baihe-subtitler at 43ee3ec and updated at 43c4ac7. The planning docs for these steps sat on the roadmap-only drafts #640 and #660, which were closed; this section is the record.

Merged since 175d617 (each checked against `git log origin/baihe-subtitler`; the behaviour notes were also grepped in the code):
- Navigation and Review: a breadcrumb inside titles, the reader, comic and nested pages (#819); each line is heard in its own language when comparing and re-transcribing (#820); a hint and extra-names fields in Compare transcription (#835); preview a line-history snapshot before restoring (#831); playback speed and timing hotkeys (Step 159, #834); a cut-mark view in the split dialog (#852).
- Transcription: Whisper hallucination guards for silence and music (Step 161, #832): a 2 s silence threshold, on by default; `hallucination_silence_sec` 0 turns it off (the service accepts 0 or 0.5 to 10). Qwen3 shows the device it runs on and reports GPU fallbacks (#823); a cancel stops Qwen3 forced alignment at its next check (#849).
- Translate: batches start at scene breaks (Step 176, #833), on by default through the `scene_aware_batches` preference, read by the Workspace job and `cli.py translate`.
- Glossary: less noisy proposals with counts, confidence and a per-series ignore list (Step 148, #841), with the ignore list capped (#845).
- Media: re-uploading keeps the original, and a failed extraction keeps the upload (Step 144a, #842).
- Benchmark: chrF translation similarity, via optional sacrebleu (Step 150, #840). Pop-out: the caption scales with the window and has a Subtitle size control (#839).
- CLI: parity fixes in bulk translate, translate, dub and align (#825); new `transcribe`, `qc` and `glossary` commands (#837).
- Security and hygiene: four low redaction and SSRF findings (#822); filenames with spaces are redacted in support paths (#827); `updatePreferences` goes through `pcOnlyFetch` so a remote 403 marks the tab remote (#828); npm audit fix, source-map-js 1.2.2 (#848); frontend cleanups (#821).
- Live capture (#589): fetched in Python through a guarded egress proxy and piped to ffmpeg's stdin. `media.import_url` must not be granted to household users until the follow-up hardening PR lands (see Live capture and SSRF).
- Tests, CI and docs: docs and hygiene fixes (#824), CI hardening (#826), phone e2e twins (#829), e2e condition waits (#844), and fixes for the phone-spacing (#846) and breadcrumb (#847) flakes.

Still open (PRs, none merged at the time of writing):
- #836 re-time lines with the Qwen3 aligner; #838 English "Fix common errors" (Step 173); #843 waveform timeline (Step 164); #850 saved upload size limit; #851 re-split sensitivity.

Next wave, after the open PRs land: 171, 162 and 158. Later or low value: 145, 146, 149, 152, 154, 156, 157, 160, 163, 167, 169, 170, 174, 175, 177, 178, 182. Dropped or decided no: 155, 172, 147 (skip), 168 (privacy), 179 (upload half), 180 (wait for remote access), 181 (minisign when distributing beyond the owner), 166 (decide after 165), 151 (only if the owner wants local models). Rows for steps now built are removed from the table below.

| Step | What | Status | Decision |
|---|---|---|---|
| 171 | CBZ + full ComicInfo | Next wave | Build |
| 162 | Text-mask fallback | Next wave | Build Otsu with light/dark polarity; defer the ML detector |
| 158 | Manual timing shift | Next wave | Manual shift only; auto-sync waits for the re-time feature (#836, open) |
| 145 | Source hashes | Later | After 144 (144a is merged, #842) |
| 146 | OCR confidence | Later | Low value |
| 149 | Line provenance history | Later | Low value |
| 152 | Public benchmark sets | Later | 150 is merged (#840); 147 is not being built, so decide whether it still waits on 147 |
| 154 | Trash | Later | Extend the existing `baihe_trash` in `services/disk_usage_service.py`, no second trash |
| 156 | Timing/speaker benchmark | Later | Start with an offline scorer |
| 157 | Import existing subtitles | Later | Low value |
| 160 | Timing tidy-up | Later | Low value |
| 163 | Text/stroke colours | Later | After 162 |
| 167 | Metadata APIs | Later | Low value |
| 169 | MKV styled ASS | Later | Low value |
| 170 | Burn-in quality / NVENC | Later | Burn-in exists (`video_export.burn_subtitles`, `burn_ass`) with no codec or quality option; the clip and preview paths hard-code `libx264` |
| 174 | Per-series check intervals | Later | Low value |
| 175 | LabelPlus | Later | Low value |
| 177 | Hardsub change detection | Later | Low value |
| 178 | Two-page spreads | Later | Low value |
| 182 | JASSUB | Later | Low value |
| 153 | Late-file drift on long recordings | Re-check first | No diagnosis exists; re-measure after the Re-time-with-aligner feature (#836) lands |
| 151 | Generic OpenAI-compatible engine | Partly existing | `engine_backends/openai_compat.py` holds only `DeepSeekEngine` and `OpenAIEngine`; no generic engine id (`ollama` alone takes a `base_url`). Build only if the owner wants local models, and have Opus review it |
| 147 | Offer-after-correction benchmark flow | Not building | Skip |
| 155 | Finished-copy archive | Dropped | No |
| 166 | Force-align the ASR's own text | Undecided | Decide after 165; overlaps the existing Qwen3 aligner (`services/transcribe_service.py`, `qwen3_forced_align`) |
| 168 | Vision-LLM OCR | Not building | No: privacy |
| 172 | Broadcast timing / shot snapping | Dropped | No |
| 179 | Jellyfin upload through the API | Not building | The upload half is decided no |
| 180 | OPDS | Not building | Wait for remote access (Step 140) |
| 181 | Signed updates | Not building | minisign when distributing beyond the owner; the SHA-256 check stays |

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
