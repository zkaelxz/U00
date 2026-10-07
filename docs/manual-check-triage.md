# Manual-check triage

Triage of the manual-check table in section 2 of `docs/baihe-roadmap.md` (branch `claude/baihe-subtitle-planning-95qyvq`), checked against `baihe-subtitler` at `d3b83d9` on 2026-10-04. No code changed.

The table has 174 rows. 10 are not pending (listed at the end), leaving **163**. Rows 80b and 83 to 96 have no row in that table, so they are not triaged here.

| Group | Count |
|---|---|
| 1. Obsolete | 27 |
| 2. Verified here | 11 |
| 3. Needs the owner | 33 |
| 4. Needs a specific machine or account | 87 |
| Unsure | 5 |
| Total pending | 163 |

"Verified" means something real ran in this sandbox (real SQLite, ffmpeg or Chromium), not a mocked test. Sandbox: Python 3.11, no torch, no ebooklib, no network use. Nothing here is ticked in the roadmap.

## Commands behind the "verified" rows (all 2026-10-04)

- `cd frontend && PLAYWRIGHT_CHROMIUM_PATH=/opt/pw-browsers/chromium npx playwright test --project=desktop e2e/library.spec.ts e2e/library-page.spec.ts e2e/review-search-jump.spec.ts e2e/review-popout.spec.ts e2e/theme-menu.spec.ts e2e/routing.spec.ts e2e/diagnostics.spec.ts e2e/settings-jump-links.spec.ts e2e/source-modes.spec.ts e2e/lab-benchmark.spec.ts`: 26 passed. These drive the real seeded API; cited as evidence only for 20, 25z and 38.
- `python -m pytest -q -p no:cacheprovider -o addopts="" "tests/test_background_jobs.py::TestNotifyOnCompletion::test_failed_job_notifies_with_error_status"` ten times: 10 x `1 passed` (step 49).
- Throwaway scripts (not committed) against a temporary library: backup/restore (25k, 52), merge and notes (2, 25l), re-segmentation preview/apply (6f), find & replace after a merge (25o), `media_inspect` on an ffmpeg-made file (59), `auto_qc` (12b), `render_vertical_clip` on an ffmpeg-made clip (6e), `import db` in a clean `git archive` (51), top-level `.py` vs `FILE_ORGANIZATION.md` (56).
- A temporary Playwright spec (deleted) swept 16 routes in dark scheme for light surfaces: none found; the same sweep in light scheme did find them (46). Another swept 7 pages for the removed hosted-cloning vendor's name: none (11d).

## 1. Obsolete

| Step | Title | Replacement / why |
|---|---|---|
| 4b | torchcodec failure fix | The torchcodec dependency was dropped entirely (diarization reads audio with soundfile); the roadmap itself marks 4b superseded by 4c. See `diarize.py`. |
| 4j | Switching dramas resets loaded lines | Streamlit session-state bug. The React Workspace is addressed by URL (`#/drama/<id>/<stage>`), state lives in `frontend/src/pages/workspace/useDrama.ts`. |
| 6d | Post-merge stale Review layout, accepted text survives reload | Streamlit widget-state bugs. Review now reads lines from the API and sends `expected_line_ids` with merge/restore (`services/restructure_service.py`, `review/LinesPanel.tsx`). |
| 9g | Un-nest "Save as preset" in Translation | Streamlit expander layout. Presets now live in the Library create form and the Translate stage (`Library.tsx`, `workspace/stages/TranslateStage.tsx`). |
| 9h | Translated text needs a hard refresh | Streamlit stale-state bug. The Workspace refetches on job completion and gets pushed events (`workspace/stages/useRunStatus.ts`, `GET /api/events`). |
| 9i | Stale-state audit, Library bulk-translate gap, Refresh button | The Streamlit toolbar "Refresh" button no longer exists (no such control in `WorkspaceShell.tsx`); the stale-state class went with Streamlit. |
| 10b | CI check of the fresh-machine bootstrap | The step's concrete action (break `app.py`) is gone. `.github/workflows/windows-bootstrap.yml` now runs `start.bat --ci`, which waits on `/api/health`. |
| 10c | Wire the app icon into the running app | It checked Streamlit's default favicon. The React app declares its icons in `frontend/index.html` (`public/favicon.ico`, `icon-32.png`, `icon-192.png`); `assets/app_icon.ico` is for the shortcut. |
| 10d | Push CI fix to old merged branches | One-off maintenance of old step branches. `git ls-remote --heads origin` (2026-10-04) lists 12 branches and none is a `step-*` branch, including `step-10b-ci-bootstrap-check`. |
| 10e | LAN client/server access: print the real network URL | `start.bat` is loopback-only by design. Network access is now sign-in plus Caddy (`docs/remote-access-decision.md`); the owner's LAN test is step 140 in `docs/STATUS.md`. |
| 12 | GUI polish and Streamlit performance | Theme, section labels and rerun flicker were Streamlit work; theme now has its own e2e (`theme-menu.spec.ts`). The video caption sync part is carried by 12c. |
| 13 | UI foundation: components and project state | Scaffolding for the Streamlit UI. Replaced by `frontend/src/components/` and the Workspace shell. |
| 14 | Workspace shell rebuild | The stage tabs are now Source / Translate / Review / Dub / Export (`workspace/stages.ts`); the Review sub-features are folds in `workspace/stages/ReviewStage.tsx`. |
| 15 | Reader tab declutter | Replaced by `pages/Reader.tsx` (route `#/read/<id>`) with `reader/ReaderStory.tsx` for the story tools. |
| 16 | Settings consolidation | Replaced by `pages/Settings.tsx` (Jobs, Engines, Defaults, Alerts, Sharing, Integrations, Advanced, Experimental). |
| 17 | Discover/Navigator merge | There is no Navigator page. `pages/Discover.tsx` holds the find/add/navigation-help panels. |
| 25b | Switching dramas copies character names/voices | Streamlit session-state leak. Characters are loaded per drama id from the API in React. |
| 25i | Uploaded EPUB leaks into the next drama | Streamlit file-uploader state. React uploads are explicit per-drama actions (`workspace/stages/NovelFilePanel.tsx`). |
| 25j | Library Resume carries previous drama's edits | Streamlit session-state entry point; Library now links to `#/drama/<id>` and Review loads that drama's lines. |
| 25p | Three upload widgets leak across drama switches | Streamlit uploaders; no equivalent in React. |
| 25t | Tab-bar styling selector is dead | There is no Streamlit tab bar; navigation is the React header in `App.tsx`. |
| 25v | mangaz.com adapter bugs | The Mangaz source was removed (#648); there is no `sources/adapters/mangaz.py`. |
| 33 | `requirements-install.bat` hardcodes a personal path | The file no longer exists. Installs go through `start.bat`, `start.ps1` and the installer (`installer/`). |
| 43 | Universal soft-delete with a Deleted-items view | Redefined 2026-09-29 as Backups (`docs/remote-access-decision.md`): auto-backup copies and per-drama restore (`services/auto_backup_service.py`, Settings > `AutoBackupCard.tsx`). No soft-delete or Deleted-items view exists. |
| 45 | Stage stepper stuck on Diarize | The Streamlit stepper is gone; the React stepper takes its states from the progress API (`workspace/stages.ts` `stageStates`). |
| 65 | Tab-name decision vs FILE_ORGANIZATION | Streamlit tab naming; `tabs/` no longer exists. |
| 73 | Minimum Streamlit version | Streamlit was removed (#502); `requirements*.txt` no longer pins it. |

## 2. Verified here

| Step | Title | Evidence |
|---|---|---|
| 2 | Notes follow a line through a merge | Real SQLite via `restructure_service.merge_lines`: the note on the absorbed line moved to the merged line, the other note stayed on its line at its new position. Run 2026-10-04 (script outputs in PR). |
| 6e | Vertical 9:16 export | Real ffmpeg via `video_export.render_vertical_clip` on a generated 1280x720 clip: output 405x720 (exactly 9:16), burned caption visible in the extracted frame. Synthetic footage; a look at real footage is still nice to have. |
| 6f | Apply real re-segmentation: counts match the preview | Real SQLite: preview said 6 to 9 lines, the job applied 9, ids unique, 9 rows in `lines` (no leftovers). |
| 12b | Auto QC flags mistranslated numbers/dates/names | Real `auto_qc` code on hand-built lines, plus glossary names: a wrong date (2019 to 2021) and a missing name were flagged, correct lines were not. Money amounts are deliberately not compared (currency conversion). Restoring a flagged line uses the same restore path as 25l's run. |
| 25k | Restore from a wrong file keeps the library | Real SQLite and zip via `library_admin_service.restore_backup`: garbage bytes and a random zip were refused and the drama was still there; a real backup restored. |
| 25o | Find & replace stale match after a merge | Real SQLite: previewed two matches, merged one into a neighbour, applied: 1 applied, 1 skipped as stale, the merged text was untouched. (Service level, not the React button.) |
| 49 | Flaky notify-on-completion test | `test_failed_job_notifies_with_error_status` run 10 times in a row, 10 passes (the check is the repeat run itself). |
| 51 | `import db` has no side effects | Exported a clean `git archive` of HEAD and ran `python -c "import db"`: no `library/` and no `library.db` created. |
| 52 | Restore of a valid backup under the size limits | Real backup zip written with `write_backup_zip` and restored with `restore_backup`: `{'restored': True}`, drama present. |
| 56 | FILE_ORGANIZATION lists every top-level .py | Compared `git ls-tree -r HEAD` top-level `.py` files against the file: none missing. `tabs/` no longer exists, so that half is moot. |
| 59 | Analyze: reported duration/resolution/audio language | Real ffprobe via `media_inspect.probe_media` on a generated file with a `jpn` audio tag: 5.0 s, 1280x720, 24 fps, `jpn` all match `ffprobe`. The pipeline suggestion is filename-keyword based; judged only on a synthetic file. |

## 3. Needs the owner

| Step | Title | What to try now |
|---|---|---|
| 1b | Safety fixes: refuse edits during jobs, backups, log | Start a translation in Workspace > Translate, then try to edit or merge a line in Review; the controls should be disabled (`useDramaJobRunning.ts`). Then run Library > Admin backups (full and database-only) and check Diagnostics shows the job. |
| 1e | Character pronouns | In a drama with no series, set a character's pronouns in Workspace > Source > Characters (also try Series cast), translate a few lines and check they/them and he/she are used. |
| 3 | Compare with / restore original line text | In Review, edit a line, open its Origin panel (`LineOrigin.tsx`) and use "Restore original text". The old "Compare with original" label no longer exists. |
| 4f | Expected speakers field resets to 0 | In Workspace > Source set speakers to 2 under the speaker detection options, leave the drama and come back; the field should still read 2. A mocked spec exists (`transcribe-speakers.spec.ts`), no real-flow one. |
| 6g | Speech-splitting default 300 ms, per-drama value persists | Open a new drama's Source stage > tuning and read the speech-splitting default and help text; change it, switch stages and back. |
| 9c | Drama presets | Save a preset from a drama in the Translate stage, create a new drama from Library with that preset, and check the captured fields are still editable. |
| 11d | Hosted cloning vendor removed | A real-Chromium text sweep of Settings, Diagnostics and the drama Source/Translate/Dub/Export pages found no mention of the vendor (2026-10-04). Still to check: dub a drama that has a character with an old hosted clone and read the message (`dub.REMOVED_CLONE_MESSAGE`). |
| 12c | Review linkage: seek, timestamp box, burned preview, SFX | In Review with a drama that has a video, select a row and check the player seeks, try the timestamp box and Play segment, make a burned preview (Extras) and check captions match the style, mark a line as an SFX cue and export. |
| 18 | Diagnostics narrowing | Open Diagnostics: the Danger zone (`DangerZone.tsx`) should sit apart from routine checks, and Benchmark Lab is its own page (`#/benchmark`). |
| 19 | Full click-through UX test | Walk the workflows listed in Step 19 in the React app (Library, Workspace stages, Sources, Settings) and note anything unclear. |
| 20 | UX polish: shortcuts, toasts, transcript search | In Review try the shortcut sheet keys (they must not fire while typing) and save a line to see the toast. The search-jump part is already verified (`e2e/review-search-jump.spec.ts`, real API, 3 passed 2026-10-04). |
| 21 | Review: per-line audio and save-status | In Review play a line's audio snippet with the Player, edit lines without saving, navigate away and back, and check the unsaved indicator. |
| 22 | Series-level library view | Create two dramas of different media types in one series with a shared character; open the series in Library and check both are listed with the character source. |
| 22b | Anime media type, assign series at create | In Library > New drama pick media type "anime" and an existing series, create it and check it shows in the series view; add an anime movie to a show's series. |
| 23c | Novel-workflow gaps (EPUB images, regex, bulk glossary) | Import an EPUB with images and re-export, run Review > Find & replace with regex on a novel drama, and bulk-delete / bulk-set-gender glossary terms (`GlossaryPanel`). EPUB code needs `ebooklib`, not installed here. |
| 24 | Translation memory, benchmark A/B, Favorite | Translate a batch with a repeated line and look for the memory suggestion, run a Benchmark Lab case through two engines, and mark a drama Favorite in Library then filter by it. |
| 25m | Narration prepare snapshots lines first | Prepare narration on a drama that already has translations (Workspace > Narration) and check Review > Versions and history has a "before chunk & tag speakers" snapshot. Needs an LLM engine for the tagging pass. |
| 25q | ToS-prohibited sources excluded from search and background check | Mark a source ToS-prohibited in Sources domains/Settings, then search in Sources and check it is absent; check the tracked-series check skips it. |
| 25s | Sources import into an existing drama needs confirmation | In Sources pick an already-transcribed drama in the drama picker and import another video; an explicit confirm should appear and Decline must leave audio and lines alone. |
| 25y | EPUB export escapes & and < | Export a drama whose text contains `&` and `<` to EPUB and open it in a strict reader or run `epubcheck`. Not run here: `ebooklib` and `epubcheck` are not installed. |
| 25z | Four destructive actions need confirmation | Try Delete drama, Remove audio/video, Remove raw novel context and Clear translate history without confirming (nothing should happen), then confirm. Delete-drama-with-typed-confirmation is already exercised by `e2e/library-page.spec.ts`. |
| 27 | Dependency freshness check and Upgrade | In Diagnostics > Packages with an old package installed, run the freshness check, check both versions show, press Upgrade and check the row updates without a restart. |
| 46 | Dark mode on every tab | A real-Chromium sweep of 16 routes in dark scheme found no light surfaces (control run in light scheme found them, 2026-10-04). Still to look at: Translate page > run one translation and read the "Source text" box. |
| 55 | Failed consistency batches are reported | In Review run the consistency check with a deliberately wrong API key; the UI should say batches failed, not "no issues found". |
| 58 | "What happened here?" provenance | Open a flagged line's Origin/provenance panel in Review and cross-check one field (engine, glossary hash) against the database. |
| 64 | Install/Upgrade button design, table count | Check Diagnostics > Packages reads as intended. The "stated table count" no longer exists in `FILE_ORGANIZATION.md` (db.py has 54 CREATE TABLE). |
| 66 | Upgrade-safety check on a real package | Run Diagnostics > Packages upgrade test (`UpgradeTest.tsx`) on `huggingface_hub` and compare its verdict to the test suite. Needs network for pip. |
| 68 | Dark mode: App Assistant and Translate history | Turn on Developer mode, open Assistant in dark theme, and check Translate page history shows a Translation column value. The generic dark sweep did not cover Assistant. |
| 71 | Confirm before deleting versions, presets, glossary terms, characters | Try deleting a translation version, a preset, a glossary term (single and bulk) and a series character; each should ask first. The saved bug bundle helper was removed (#638). |
| 75 | Install paths on a fresh clone | On a fresh clone run the install path(s) (`start.bat` / installer) and check no `ModuleNotFoundError`. The mangaz.com part is moot (adapter removed). |
| 76 | `pip install qwen-asr` works | In a clean venv run `pip install qwen-asr` and open the Qwen3-ASR option; it should work or be marked not functional. |
| 77 | Voice bank delete needs confirmation | In the clone/voice bank panel try to delete an entry; one click must not delete it. |
| 81 | Post-cleanup click-through | Click through Workspace transcribe, translate, export, Library and the comic reader once and note any error. |

## 4. Needs a specific machine or account

| Step | Title | Why |
|---|---|---|
| 1 | Translation pronouns/honorifics from real model output | real LLM |
| 1c | Dependency fixes: YouTube clip, speaker detection, Ollama, edge-tts | real site, models, audio |
| 1d | Free engines run every AI button | real Ollama/NLLB/Gemini free tier; the "Test mode" engine no longer exists (free set is `ollama`, `nllb`, `engine_registry.py`) |
| 1f | Gemini free-tier rate checker | real Gemini key vs AI Studio dashboard |
| 4 | Re-run speaker detection with a new speaker count | needs saved diarization from real audio |
| 4d | Real mid-run stop for speaker detection | real long audio, CPU/GPU monitor |
| 4e | Mid-run stop for dub and re-segmentation | real TTS run |
| 4g | Vocal separation progress and cancel | real audio and model |
| 4h | Word-level realignment dependency message | needs torch/torchaudio installed with only `uroman` missing |
| 4i | Large job result completes | real diarization or dub on 250+ lines |
| 5 | Ollama uses qwen3:8b, no OOM | real GPU; the roadmap already resolved the general figures |
| 5b | Ollama URL, merge preview, Live capture js_runtimes | real Ollama and a live YouTube stream |
| 5c | Global GPU-job guard | real GPU jobs (a transcription and an OCR) |
| 6 | Transcription quality, SenseVoice, Groq ASR | real audio, models, Groq key |
| 6b | Export SRT/VTT/ASS, play in a player | watching check in mpv/VLC (ffmpeg burn of ASS worked, but playback is the check) |
| 6c | Meaning-based re-segmentation | real transcript and engine |
| 6h | Auto-tune speech splitting | real transcription runs |
| 6i | Cloud ASR through Groq | real Groq key |
| 7 | Reflect translation mode | real LLM |
| 7b | Glossary auto-extraction | real LLM |
| 8 | Recurring-voice suggestions | real audio embeddings |
| 8b | Name your characters: sample lines, clone-ref reason | real diarized audio |
| 9 | Cost cap, Bulk mode, Gemini Flash-Lite | real API keys |
| 9b | ETA, model cache, versions, bulk translate, cookies, diagnostics copy, pyannote gating | real HF account and login-gated sites. Redaction itself checked: `redact_for_support` stripped a Windows path, a `/home` path and `/root` from a probe text on 2026-10-04 |
| 9d | Bulk-mode flagging/consistency/emotion/notes, Bulk Reflect | real batch API |
| 9e | Reflect with a reference novel | real LLM |
| 10 | Windows launcher, uninstall, copy to another PC | Windows |
| 11 | Scanlate ML detector, LaMa-manga, OCR per language | real comic pages and models |
| 11b | Novel narration TTS quality | listening check, real TTS models |
| 11c | Dub timing stretch | listening check |
| 11e | Dub clip cache after a text edit | real TTS |
| 12d | Scanlate structured regions, batch, SFX | real comic pages and OCR |
| 12e | Project instructions, Draft/Standard/Release presets | real LLM output |
| 18b | App Assistant answers | real LLM |
| 18c | Install buttons, Install GPU PyTorch | real pip installs, real GPU |
| 20b | Anki export with audio | real audio drama and Anki |
| 23 | Source-adapter interface, generic URL import | real sites |
| 23b | manhuagui adapter | real site |
| 23d | Bilibili video adapter | real site |
| 23e | 52shuku and xbanxia adapters | real sites |
| 23f | Bilibili Manga candidate | real account and browser |
| 23g | Adaptive AI extraction and profiles | real LLM and site |
| 23h | ToonKor, guazimanhua, miaoqumh adapters | real sites |
| 23i | baozimh/godamh and Kuaikan adapters | real sites |
| 23j | manhuaku browser-tier adapter | real site and browser |
| 23k | Authenticated browser-assisted extraction | real account |
| 23l | zerosumonline adapter, generic import of five sites | real sites (the mangaz half is gone, #648) |
| 25 | Transcript paste then Transcribe & Align after closing the tab | real audio and alignment model |
| 25c | Offline Piper voice produces audio | nothing to check: Piper was removed |
| 25f | Navigator translate-page, baihehub search | real key and site |
| 25g | ToS-refused import; two unvoiced characters dub differently | real TTS for the second half |
| 25u | Discover translation with Ollama and no key | real Ollama |
| 25w | Cost cap with Google/DeepL, CLI GPU guard | real keys and GPU |
| 25x | Why this / Alternatives / Grammar stale results | real LLM engine |
| 26 | Voice bank reuse across dramas | real TTS clone |
| 26b | Standalone translate KR/JP/CN to EN | real engine |
| 26c | Narrate in original language, bilingual subtitles | listening check, real TTS |
| 28 | ToS-prohibited source buttons disabled, Bilibili import | real Bilibili site |
| 29 | Kill dub worker mid-synthesis | real TTS |
| 30 | Glossary aliases and banned translation | real LLM output |
| 31 | Gemini content-filter block and retry | real Gemini key |
| 32 | Look-ahead slider on a novel chapter | real LLM |
| 35 | ML bubble detector count, colour-art message | real ML weights and pages |
| 36 | Capability-based engine routing, Test per key | real keys |
| 37 | Gemini grounding research | real key |
| 38 | Benchmark Lab with two real engines | real engines (the offline-engine flow passed in `e2e/lab-benchmark.spec.ts`) |
| 39 | Jellyfin integration | real Jellyfin server |
| 40 | Retired model message | real provider rejection |
| 40b | Model candidate re-evaluation | real models |
| 41 | Resume a killed transcription | real transcription |
| 42 | Maintenance assistant diagnosis | real LLM |
| 44 | Discord/ntfy notifications | real webhook and ntfy |
| 47 | Model install actions and redundancy warning | real model installs |
| 48 | Live capture client retries | real YouTube stream |
| 50 | Reference passages for a mid-novel batch | real LLM and retrieval |
| 53 | start.bat skips install only when core packages are present | Windows |
| 54 | No romantic bias on non-romantic drama | real LLM |
| 57 | Maintenance assistant 🔴-tier gating | real LLM |
| 60 | Independent review role catches a flawed fix | real LLMs |
| 61 | Install on Python 3.14 without raw pip traceback | Python 3.14, real pip |
| 62 | Deno install action, bulk install with failure | a machine without Deno |
| 63 | start.ps1 skips install only when core packages are present | Windows PowerShell |
| 67 | Full suite on Windows | Windows |
| 72 | Deliver as GitHub PR | real GitHub token and repo |
| 74 | Series summary used for episode 2 | real LLM |
| 78 | Profiles, GPU yield to Jellyfin transcode | real second app and profiles |
| 79 | start.bat Store-alias message, `--python-version` | Windows with two Pythons |

## Unsure

| Step | Title | Why |
|---|---|---|
| 1c-pre | Fresh cloud session runs `python run_tests.py` | `.claude/hooks/session-start.sh` and `run_tests.py` exist, but I cannot tell from inside this session whether the hook ran on start. |
| 9f | Live job cancel race behind a GPU job | The row itself says it is a code-review check; Live now takes a GPU slot only with Use GPU on (#667), so the original race may have moved. Not decided. |
| 25l | Undo a merge: notes on the right lines | Real SQLite run: notes on non-merged lines stayed correct, but a note that sat on the absorbed line stays on the merged line after restoring the snapshot, because the restored line gets a new id. May be by design (docstring in `restructure_service.py`), not decided. |
| 25n | Failed Scanlate page translation blanks bubbles | A malformed engine response cannot be forced from the app; only a regression test can cover it. |
| 69 | Transient db error in a background job | Cannot be triggered by hand; I did not run a real concurrent test. |

## Not pending (excluded)

| Step | Why |
|---|---|
| 4c | Already confirmed 2026-09-27 in the roadmap. |
| 4k | Roadmap row: no separate manual check. |
| 25d | Roadmap row: no manual check (documentation bundle). |
| 25e | Roadmap row: no manual check. |
| 25h | Roadmap row: no manual check. |
| 25r | Roadmap row: no dedicated manual check. |
| 34 | Reserved slot, not scoped. |
| 70 | Decided, not pending. |
| 80 | Design-only step. |
| 82 | No running-app check: a diff of CLAUDE.md against a staging copy I do not have. |
