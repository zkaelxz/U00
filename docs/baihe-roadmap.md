# Baihe Subtitler — Gap Audit & Roadmap toward the Phase 1 Architecture

> **NEXT:** Step 1d (`step-1d-free-testing-engines`) is reviewed and
> approved — diff and all 5 exit conditions checked directly against the
> real branch. Create its PR and merge into `baihe-subtitler` (this last
> gated merge — everything from Step 1e through Step 10 is now
> **autonomous mode**, see §4: the implementing chat builds, tests, opens
> the PR, and merges it itself, moving straight to the next step with no
> stop for review or a merge go-ahead, except the existing
> Opus-confirmation stop for Steps 2/6c/9).
> **Before Step 1e starts:** the root `CLAUDE.md` on `baihe-subtitler`
> still predates the Opus-confirmation-before-switching rule and the
> dead/redundant-code cleanup rules (checked directly — `git show
> origin/step-1d-free-testing-engines:CLAUDE.md` still lacks both). Have
> the implementing session re-copy the current
> `docs/ai-setup/CLAUDE.md` from this planning branch into
> `baihe-subtitler`'s root before or as part of starting Step 1e — this
> matters more now that autonomous mode means no per-step review will
> catch a rule the implementing chat doesn't know about. Also still open
> from last time: confirm with the user whether
> `step-1c-pre-ai-setup`/`step-1c-dependency-fixes` being gone from
> origin was intentional or GitHub's merge-UI default.
> *(Kept accurate per §5 rule 1 — checked against real branch state, not
> memory, as of 2026-09-24. If this line is stale, the status table below
> it is the source of truth.)*

Status: agreed plan (**shortened version**). This doc is written in the
**planning** chat, and the **implementing** chat carries it out on
`baihe-subtitler`.

- Target design: [`phase1-architecture.md`](phase1-architecture.md).
- Audited code: branch `baihe-subtitler` at commit `7af8453`. Every `file:function` reference below is on that branch.

**Build order:**
- Steps 1–5: R5 → safety fixes (1b) → **AI setup (1c-pre)** → dependency fixes (1c) → free testing engines (1d) → character pronouns (1e) → R0 → R1-lite → R2 → R3-lite.
- Steps 6–20: transcription quality (6) → export formats (6b) → meaning-based re-segmentation (6c) → vertical/shorts export (6d) → reflect translation mode (7) → content-summary glossary extraction (7b) → recurring-voice suggestions (8) → cost controls & bulk discounts (9) → job ETAs/model disk/bulk translate/diagnostics redaction (9b) → drama presets (9c) → Windows launcher (10) → Scanlate ML detector/inpainting/OCR routing (11) → novel narration TTS quality (11b) → dub timing: clamped time-stretch fallback (11c) → GUI polish and Streamlit performance (12) → UI foundation: components & project state (13) → Workspace shell rebuild (14) → Reader tab declutter (15) → Settings consolidation (16) → Discover/Navigator merge (17) → Diagnostics narrowing (18) → full click-through UX test (19) → UX polish: shortcuts, toasts, transcript search (20). Milestones R4 and R7 are deferred (see §3).

## Decisions already made

| Question | Decision |
|---|---|
| Default local translation model | **7B Q4** (e.g. `qwen2.5:7b`). 14B is opt-in, labelled "may not fit in 8 GB / expect CPU offload". |
| SenseVoice in benchmarks | **Excluded** until its weight license has been reviewed. |
| Later backend/UI stack | **SQLite + FastAPI + React**, deferred to M8+. Streamlit stays until then. |
| Rewrite vs. evolve | **Evolve** the existing app. There is no rewrite. |
| Scope | **Shortened plan.** Only fix what the user would notice: translation quality, line identity, keeping the original transcript, and re-runnable speaker detection. Full artifact versioning, the ASR pipeline rebuild, extra export formats and the benchmark harness are deferred. |

---

## 1. Current state vs. target (audit)

| Target (Phase 1 §) | Today on `baihe-subtitler` |
|---|---|
| Standalone Silero VAD artifact (§2, §4) | VAD is hidden inside faster-whisper (`core.transcribe_for_timing`, `vad_filter=True`), and Whisper segments set every boundary downstream. |
| ASR independent of Whisper | `asr_backend.Qwen3ASRBackend` re-transcribes **Whisper's** segments and replaces only the text. |
| Immutable `raw_transcript` (§6) | `db.save_lines` deletes and re-inserts every line, and the `zh` column is overwritten by edits, re-transcribes and merges. The only safety net is `line_history`: the last 10 snapshots, taken only before bulk actions. |
| Diarization re-runnable without re-ASR (§1) | Speaker turns are kept only in `st.session_state` and never saved, and there is no standalone diarization action. Pyannote **3.1** runs in one pass over the whole file (`diarize.diarize`). |
| One GPU model at a time (§7) | Module-level caches keep models loaded for the life of the process (`core._whisper_model_cache`, `asr_backend._asr_model_cache`, `forced_align._aligner_model_cache`). There is no `empty_cache`. |
| Context-aware translation keyed by id (§3.5) | `translate_engines.translate_lines_with_engine`:<br>• sends batches of 20 with 6 lines of look-back and **no look-ahead**;<br>• **never sends speaker labels**;<br>• maps results back by `zip()` position, so a length mismatch silently shifts lines;<br>• uses a system prompt hardcoded to "Chinese baihe";<br>• `OllamaEngine` defaults to `qwen2.5:14b` with no request timeout. |
| SRT / ASS / VTT | SRT only (`core.lines_to_srt`). |
| Full-pipeline benchmark | `asr_benchmark.run_benchmark` covers ASR and alignment only. |

Code-health issues that affect the plan:
- Lines have **no stable id**. They are keyed by `idx`, which changes after a merge, so `translation_notes`, `line_emotions`, `consistency_issues` and `reading_history` can point at the wrong line.
- `cli.cmd_dub` drops `flag`/`flag_note` when it saves.
- `cli.cmd_translate` skips glossary, style and locale.
- `FILE_ORGANIZATION.md` says there are 285 tests; there are now 761.

---

## 2. Roadmap: build in this order

Rules for every milestone:
- Keep all existing tests green (`python run_tests.py`).
- Add mocked tests in the existing style: the `tests/conftest.py:isolated_db` fixture and fake model classes, with no GPU or real models in tests.
- Do one milestone per feature branch off `baihe-subtitler`. The next milestone starts only once the current one meets its exit condition.
- Keep changes minimal. Don't add abstractions the milestone doesn't need.
- After a step is merged, the user runs its **manual check** from the table below on their own PC, with real models and real audio. Mocked tests can't catch problems that only show up with real models. Report anything that looks wrong back to the planning chat.

**Manual checks (5–10 minutes each, on a short real episode):**

| Step | What to try in the app |
|---|---|
| 1 | Translate a short episode that has named characters. Check that pronouns and honorifics match who's speaking, and that no line got another line's translation. |
| 1b | Start Translate, then try "Find lines to flag" and editing a line. Both should be refused with a message. Make a database-only backup and a full backup, then open Diagnostics and check the log shows the job. |
| 1c-pre | Start a fresh cloud session on `baihe-subtitler` after this merges. Confirm it runs `python run_tests.py` with no manual install step first. |
| 1c | Download a short YouTube clip. Run speaker detection. Translate with Ollama if you use it. Generate an edge-tts dub line. |
| 1d | Using only the 🧪 Free engines (Test mode, then Ollama or Gemini free tier), run every AI button once: translate, flag, consistency, emotion, notes, Q&A. Each should produce a result or a clear "not supported by this engine" message, never a silent empty result. Export a subtitle made with Test mode and check the warning appears. |
| 1e | In a drama **without** a series, set a character's pronouns in section 6 and translate. Then set pronouns right in the glossary area's "People & pronouns" when adding someone new. Check the translation uses them, including they/them. |
| 2 | Add a note to a line, merge it with its neighbour, and check the note is still on the right line. |
| 3 | Edit a few lines, then use "Compare with original" / "Restore original" on one of them. |
| 4 | Change the number of speakers and press "Re-run speaker detection". Check the speakers change and the transcript text doesn't. |
| 5 | Translate with Ollama and check it uses the `qwen3:8b` model. Run transcription then translation back-to-back with no out-of-memory error. |
| 6 | Transcribe an episode that used to get repeated-phrase loops, and check timings stay in sync to the end. Run SenseVoice's emotion/event pass on a scene with clear emotional dialogue and check its tags actually appear next to (not merged into) the existing text-based emotion tag. |
| 6b | Export the same episode as SRT, VTT and ASS. Check all three play correctly in your usual player, and ASS shows different speakers in different colours. |
| 6c | Turn on re-segmentation for one drama and check line boundaries land at real sentence/clause breaks, not mid-thought, and timing still lines up. |
| 6d | Export a short clip vertically and check it's genuinely 9:16 with legible burned subtitles. |
| 7 | Translate one episode in "High quality" mode. Check the cost estimate shows first and the critiques appear as notes. |
| 7b | Run glossary auto-extraction on a drama and check the proposed terms make sense for who's actually in the story (not just generic terms). |
| 8 | Open a second episode of the same series. Check the voice suggestions are sensible and nothing is labelled until you confirm it. |
| 9 | Set a low cost cap and check the job stops at it. Run one Bulk-mode translation and check results arrive on the right lines. |
| 9b | Start a long transcription and check the ETA appears and looks reasonable. Open the model-cache panel and delete one entry. Open the model/engine version panel and check the versions shown match what's actually installed (spot-check one against the roadmap's §7 registry). Select 2–3 dramas in a series in Library and run bulk translate. Set browser cookies in Settings and download a login-gated TikTok/Instagram/Bilibili clip. Use "Copy diagnostics for support" and check no path/username shows up, and that the version panel's content is included. |
| 9c | Save a preset from one drama, apply it to a new one, and check every captured field is still editable afterward. |
| 10 | Double-click the desktop shortcut. The app should open in its own window. Run `uninstall.bat` and check it asks separately (defaulting No) before touching your library. Copy the app folder to another PC with Python installed and confirm `start.bat` still works there. |
| 11 | On a real comic page, run Scanlate with the ML detector + LaMa-manga inpainting installed and compare the result against the OpenCV-only path — the ML version should have no visible edge where text was removed. Try a Japanese, Chinese and Korean page and check the OCR backend auto-picked is the right one for each. If LaMa-manga still leaves a visible edge or texture mismatch on a real page, that's the trigger to add item 6's FLUX.2 Klein fallback. On a Japanese page, compare `manga-ocr` against PaddleOCR-VL-For-Manga and pick whichever actually reads the text correctly more often as the default. |
| 11b | Paste in a short story and generate its narration with OmniVoice cloning, then GPT-SoVITS. Compare naturalness against the edge-tts default. Try voice design (describe a voice, no reference audio) for a character with no clip. Generate a scene with mixed emotional lines through Chatterbox and check angry/excited lines actually sound different from calm ones. Try TADA on a long chapter. Time a longer narration before and after parallelization. Open the M4B export in an audiobook player and check chapters show up. |
| 11c | Dub an episode with a few lines whose translation is noticeably longer or shorter than the original. Check the audio doesn't sound sped-up/chipmunked, and that a line still allowed to overflow its window plays at a natural pace rather than being crushed to fit. |
| 12 | Switch between light and dark theme in Settings and check both look deliberately designed. Change a style control in the live preview and check it updates without the whole page visibly redrawing. Check the installed Streamlit version in Diagnostics/§7. Open each tab and check section labels are specific (no bare "Details") and consistent in tone, every collapsible section behaves the same way, and text reads at a consistent, comfortable size throughout — not just in Workspace. Open a video drama in Read & Watch and turn on captions — check Source/English/Bilingual tracks are all there and actually in sync with the video for QC. |
| 13 | Confirm the app runs exactly as before — no visible change in any tab yet, since this step is scaffolding only. |
| 14 | Open a drama partway through its pipeline. Confirm the stage tabs (Source/Transcript/Diarize/Translate/Review/Dub/Export) hold every control that used to be in the old numbered expanders, the project header shows correct pipeline progress, and completing a task in one stage doesn't force a scroll through unrelated stages. |
| 15 | Open Read & Watch. Confirm the reading/watching view is visible without scrolling past Story-tools/wiki/Q&A first, and that all of those are still reachable from a Story panel. |
| 16 | Open Settings. Confirm Common vs. Advanced are clearly separated, every setting that used to be scattered elsewhere (e.g. OCR backend choice) is there, and Diagnostics no longer shows a duplicate API-key view. |
| 17 | Try to complete a task that used to require Navigator, starting only from Discover. Confirm Navigator's tab is gone and nothing is missing. |
| 18 | Open Diagnostics for a routine check. Confirm the danger-zone reset and accuracy-benchmark tool aren't sitting in the same view as routine health checks. |
| 19 | Run through the 12 workflows listed in Step 19 yourself and confirm each one is obvious: where you are, what to do next, no unnecessary scrolling, related controls together. |
| 20 | In Review & edit, try the save/next-flagged/prev-flagged/play-pause shortcuts and confirm they don't fire while typing in a text field. Save a line edit and confirm it shows a toast, not a static message. Search for a word you know appears in only one line and confirm it jumps to the right page. |

### Step 1 — R5: Translation fixes *(highest user impact)*
- Ask for id-keyed JSON output (`{"<id>": "<translation>"}`), check that the returned ids match the batch, and retry the missing ones. Remove positional `zip()` mapping.
- Until R0 lands, the id can be the line's position within the batch, as long as it is validated.
- Include the speaker or character name for each line in the prompt, using the drama's `characters` names where they are set. This drives correct pronouns and honorifics.
- Add N lines of look-ahead context (default 3) next to the existing look-back.
- Build the system prompt from the drama's source language and content type, not a hardcoded "Chinese baihe".
- Bring `cli.cmd_translate` up to UI parity: glossary terms, style guidelines and locale.

**Exit:** a fake engine that returns lines out of order, too few lines, or extra lines never assigns a translation to the wrong line, and the prompt contains speaker names.

### Step 1b — Safety fixes (from code review of `55f142d`)
Do these right after Step 1 and before Step 2. They're small, and they protect existing work while the later steps are built.

1. **Concurrent jobs overwrite each other.**
   - The problem: `run_translate_job`, `run_flag_job` and `run_fix_flagged_lines_job` (`tabs/workspace_tab.py`) each get their own copy of the lines and save all of them through `db.save_lines`, which deletes and re-inserts every line. Since commit `8997242` these jobs can run at the same time, so the last job to save wins. Starting "Find lines to flag" during a translation wipes the translations done so far, and the translation's next batch wipes the flags.
   - Short-term fix: refuse to start a line-writing job (translate, flag, fix-flagged) while another one is running for the same drama, and show a clear message saying why.
   - Also lock the user's own edits (added after review): while a translate/flag/fixflag job is running for a drama, disable that drama's controls that save lines from the page itself. That covers the line editor's save, merge lines, per-line re-transcribe/fix, bulk line tools and undo/restore. Show one short message saying editing unlocks when the job finishes.
   - Proper fix (land it in Step 2): each job writes only the fields it owns — translation writes `en`; flagging writes `flag`/`flag_note`. Step 2 then removes both the job guard and the edit lock.
2. **API keys leak into errors.**
   - The problem: the Gemini calls (`translate_engines.py` around lines 253 and 456, and `qa.py`) and Google Translate (around line 391) put the key in the URL (`params={"key": ...}`). A `raise_for_status()` failure then includes the full URL, key included, in the error message. That message is shown in the UI and stored in `dramas.last_translate_errors`.
   - Fix: send the key in a header (`x-goog-api-key` for Gemini; Google Translate v2 accepts `X-Goog-Api-Key` as well), and redact anything that looks like a key or token from error strings before they're shown or stored.
3. **Requests with no time limit.** Add `timeout=` to every `requests.post`/`requests.get` in `translate_engines.py` and `qa.py`: Gemini, Google, Ollama and Q&A. Without one, a server that stops responding leaves the job stuck at "running" forever.
4. *(Minor)* Tests that hard-import optional libraries (`jieba`, `pytesseract`, `cv2`) should use `pytest.importorskip`, so a core-only install gives a clean test run.
5. **Safe backups** (`tabs/library_tab.py`, "Create backup .zip").
   - The problem: the backup zips the whole library, videos included, into an in-memory `BytesIO`, then keeps extra copies of it (`getvalue()`, `session_state`, `download_button`). A few-GB library can exhaust RAM. It also copies `library.db` as a plain file while the database is open in WAL mode, so recent changes still sitting in `library.db-wal` can be missing, or the copy can be inconsistent.
   - Snapshot the database with SQLite's backup API (`sqlite3.Connection.backup`) into a temp file, and zip that instead of the live file.
   - Stream the zip to a file on disk (e.g. `library/backups/<timestamp>.zip`) instead of memory, and tell the user where it is.
   - Add a quick **"Database only"** backup option: the database is small, and it's the part that can't be re-downloaded. Keep the full backup (with media) as a separate option.
6. **Log file.**
   - The problem: the app has no logging at all (no `logging` usage anywhere). Background-job failures leave only whatever was shown on screen.
   - Configure a `RotatingFileHandler` once at startup, writing to `library/logs/app.log`.
   - Log job start, finish and failure (with the traceback) in `background_jobs`, plus every external API error, after the key redaction from item 2.
   - Diagnostics shows the last ~50 log lines and has a "copy log" button.

**Exit:**
- A test shows a second line-writing job is refused while one is running.
- A test shows the manual save/merge controls are disabled while a line-writing job is running.
- A test shows a failed Gemini request's stored error contains no key.
- Every HTTP call has a timeout (a test or static check).
- A test shows the backup's database snapshot includes a write made just before the backup, and that the backup is written to disk.
- A test shows a failing background job writes its traceback to the log file.

### Step 1c-pre — AI setup: CLAUDE.md and a session-start hook
So every future session (in this repo, cloud or local) picks up the working rules and doesn't waste its first minutes reinstalling dependencies. Files are drafted on the planning branch and copied in here.

1. **`CLAUDE.md`** at the repo root. Claude Code reads this automatically at the start of every session in this repo. Contents (already drafted — copy from the planning branch, adjust only if something in it is now wrong):
   - how to read the roadmap and which step means what;
   - the one-step-one-branch / minimal-diff / re-verify-before-fixing working rules;
   - the pull request rule (only on explicit "create a PR for this step", never self-merge);
   - how to run and write tests (mocked only, `isolated_db`, `importorskip` for optional libs);
   - the running list of "rules learned from real bugs" (id-keyed matching, no keys in URLs/logs, HTTP timeouts, the line-writing job guard, `db.save_lines` field carry-through, CLI/UI parity) so these don't get reintroduced;
   - a short map of where things are (`tabs/workspace_tab.py` is large — read only the relevant section; `translate_engines.py`, `background_jobs.py`, `db.py`).
2. **A SessionStart hook** (`.claude/hooks/session-start.sh` + `.claude/settings.json`), already drafted and verified against this repo (873 passed, 2 skipped, ~40s):
   - runs only in a remote/cloud session (`$CLAUDE_CODE_REMOTE`), a no-op locally;
   - installs `requirements-core.txt` plus the light optional libraries whose tests would otherwise skip (`jieba`, `pypinyin`, `opencc-python-reimplemented`, `opencv-python-headless`, `pytesseract`, `numpy`, `pillow`) — heavy/GPU extras (torch, pyannote, whisper, f5-tts, paddleocr) are deliberately left out, since tests mock those;
   - uses `constraints.lock.txt`/`constraints.txt` once Step 1c adds them;
   - sets `PYTHONPATH` via `$CLAUDE_ENV_FILE`.
3. **A pull request template** (`.github/pull_request_template.md`), already drafted. Fixed sections — Roadmap step, What changed, Testing, Exit condition, Manual check (copied in from this doc's table), Anything uncertain — so every PR is reviewable in the same shape and the manual-check step doesn't get forgotten.
4. Copy all three from the planning branch:
   ```
   git fetch origin claude/baihe-subtitle-planning-95qyvq
   git checkout FETCH_HEAD -- docs/ai-setup/CLAUDE.md docs/ai-setup/.claude docs/ai-setup/.github
   mv docs/ai-setup/CLAUDE.md .
   mv docs/ai-setup/.claude .
   mv docs/ai-setup/.github .
   rmdir docs/ai-setup
   ```
5. Run the hook once by hand (`CLAUDE_CODE_REMOTE=true ./.claude/hooks/session-start.sh`) and the full suite, to confirm it still works against the current `baihe-subtitler`.

**Exit:** `CLAUDE.md`, `.claude/hooks/session-start.sh`, and `.github/pull_request_template.md` exist at the repo root; a fresh remote session runs `python run_tests.py` successfully with no manual install step first (see the manual check below); a local run is unaffected (the hook exits immediately when `$CLAUDE_CODE_REMOTE` isn't set).

### Step 1c — Dependency fixes (from the known-issues research)
These are things that are broken now, or that break without warning.

1. **yt-dlp needs a JavaScript runtime for YouTube.**
   - Since late 2025, YouTube downloads need an external JS runtime through yt-dlp's EJS system. Deno is the default; Node, Bun and QuickJS also work. Without one, formats go missing.
   - This is likely the real cause behind the "try alternate player clients" workaround in `live_translate.py`.
   - Add a Diagnostics check for `deno` (or another supported runtime) on PATH, with install instructions.
   - Pass `js_runtimes` in the yt-dlp options in `video_download.py` and `live_translate.py`.
   - When formats are missing, show an error that suggests installing Deno and running `pip install -U yt-dlp`.
2. **pyannote.audio 4 compatibility.**
   - In 4.x, `pipeline(audio)` returns a `DiarizeOutput` dataclass instead of an `Annotation`, so `diarize.diarize`'s `diarization.itertracks(...)` breaks. `requirements.txt` allows 4.x (`>=3.1`).
   - Handle both result types: `ann = getattr(out, "speaker_diarization", out)`.
   - pyannote 4 needs **Python 3.10+** and reads audio with ffmpeg through torchcodec. Update the README, which currently says 3.9+.
   - Add a mocked test covering both output shapes.
3. **Ollama silently truncates long prompts.**
   - Ollama's default context window can be as small as 2–4k tokens, and when a prompt is longer it drops the *start* of the prompt with no error. `OllamaEngine` puts the system instructions, glossary and reference novel at the start and sets no `num_ctx`.
   - Pass `options.num_ctx`, sized from the estimated prompt length, with a floor of at least 16k and a Settings field to override it.
   - Warn when the estimated prompt is bigger than the limit.
   - Use Ollama's `format` parameter with a JSON schema for structured replies. This pairs with Step 1's id-keyed JSON.
   - **Check Ollama is actually reachable before a button that uses it is enabled.** Right now Ollama is exempted from the API-key check (`_needs_key`, `workspace_tab.py` ~line 1430) with no reachability check in its place, so clicking Translate with the local server stopped starts a background job that only fails once the new 300-second timeout expires. Add a short (2–3s timeout) `GET <base_url>/api/tags` health check, and disable the Ollama-dependent buttons with "⚠️ Can't reach Ollama at `<url>` — is it running?" when it fails. Cache the result briefly (a few seconds) so it isn't re-checked on every rerun.
4. **edge-tts 403 errors.**
   - Microsoft periodically blocks edge-tts; the latest report is a 403 on the WebSocket handshake in January 2026. The fix is usually `pip install -U edge-tts`.
   - Catch this in `dub._edge_tts_synthesize` and show "Microsoft blocked the request — run `pip install -U edge-tts`".
   - When Piper is installed, offer it as an automatic offline fallback.
5. **Automatic test run on every pull request (GitHub Actions).**
   - Add `.github/workflows/tests.yml`. On every pull request into `baihe-subtitler`, and on every push to it, it should:
     - set up Python 3.11;
     - install `requirements-core.txt` and `pytest` (using the constraints file from item 6 once it exists);
     - run `python run_tests.py`.
   - Optional-library tests already skip cleanly (Step 1b), so the core install is enough. No GPU, models or network access to AI services are needed.
   - The pull request page then shows a ✅/❌ next to the Merge button. **Don't merge a red ❌.**
6. **Known-good versions (constraints file).**
   - The requirements files only give minimum versions (`>=`), so a fresh install silently pulls in new major versions. That's how pyannote 4 broke diarization.
   - Add `constraints.txt` with upper bounds on the major versions of packages known to have broken before, or that are likely to, e.g. `pyannote.audio<5`, `transformers<6`, `torch`/`torchaudio` capped at the next major, `streamlit<2`, `faster-whisper<2`. Keep `yt-dlp` and `edge-tts` **uncapped**: they must stay current to keep working.
   - Install with `pip install -r requirements-core.txt -c constraints.txt`. The README, the CI workflow and Step 10's launcher all use this.
   - Add `make_lock.bat`: it runs `pip freeze > constraints.lock.txt`, so the user can snapshot a setup that's working on their PC. When that file exists, it replaces `constraints.txt`, and the user commits it.

**Exit:**
- Mocked tests cover both pyannote output shapes, the Ollama request's `num_ctx` and `format` fields, and the edge-tts 403 message.
- Diagnostics reports whether a JS runtime is present.
- The CI workflow runs on the Step 1c pull request itself and passes.
- `constraints.txt` exists, and the README install command uses it.
- A test shows the Ollama-dependent buttons are disabled with the reachability message when the health check fails, and enabled when it succeeds.

### Step 1d — Free engines for testing (clearly labelled)
The goal: every AI feature can be tried for free before spending money on a paid engine, and free output is always clearly marked as such.

1. **Bug: Ollama silently does nothing for most AI features.**
   - `translate_engines.call_llm_json` handles Claude (`client.messages`), OpenAI-style clients (DeepSeek) and `GeminiEngine`, but not `OllamaEngine`, which has no `client`. For every other engine it returns the `fallback` value.
   - So with Ollama, flagging (`flag_uncertain_lines`), consistency (`check_consistency_llm`), emotion detection, translation notes, novel speaker tagging (`tag_speakers_llm`) and the dub-pacing rewrite (`rewrite_for_pacing_llm`) all silently return nothing. Check Q&A (`qa.py`) and any other `call_llm_json` callers too.
   - Fix: add an Ollama branch (`/api/chat`, with the timeout from Step 1b, and `num_ctx`/`format` from Step 1c).
   - Change the final fallback so an unsupported engine **raises a clear error** ("<engine> can't run this feature") instead of silently returning an empty result.
2. **Test mode covers every feature.** Check that `TestOfflineEngine` returns plausible **fake** results, in the right shape, for every feature above, so the whole app can be clicked through with no AI and no cost. Add what's missing.
3. **Which engine can do what.**
   - Add one small table in code: feature → supported engines.
   - NLLB, LibreTranslate, DeepL and Google are **translation-only**. In every non-translation feature's engine picker, show them greyed out with the reason instead of letting them fail.
4. **Labels.** Group the free options in every engine picker under **"🧪 Free — for testing"**, with these descriptions:

   | Option | Label shown |
   |---|---|
   | Test mode (`test_offline`) | 🧪 **Test mode — fake output, no AI.** Checks the app works; never use for real subtitles. |
   | Ollama | 🧪 **Free — local AI on your GPU.** Private and unlimited, but lower quality than paid engines. |
   | Gemini (free-tier key) | 🧪 **Free — Google free tier.** Rate-limited (about 10 requests/minute on Flash). Google may use your text to improve its products, and people may read it. |
   | NLLB | 🧪 **Free — offline, translation only.** Non-commercial licence. |
   | LibreTranslate | 🧪 **Free — translation only.** Basic quality. |

   - Gemini uses the same engine for free and paid keys. Add a **"My Gemini key is free-tier"** checkbox in Settings. When it's ticked, show the label and warning above, and **slow requests down automatically** to stay under the free limits, instead of hitting rate-limit errors.
   - Paid engines keep their normal labels.
5. **Mark what free engines produced.**
   - Translation versions made with a free engine get `[testing: <engine>]` in their label.
   - Lines produced by **Test mode** are marked. Exporting subtitles, a video or a package that still contains Test-mode lines shows a warning: "some lines are fake test output".
   - Cost shows **$0.00 (free)** for these engines.

**Exit:**
- With a fake Ollama server and with Test mode, a test shows every `call_llm_json` feature returns a non-empty, correctly shaped result.
- An unsupported engine for a feature raises a clear error, never an empty result.
- Translation-only engines are disabled in non-translation pickers.
- A test shows export warns when Test-mode lines are present.
- A test shows free-tier Gemini pacing keeps to ≤10 requests per minute.

### Step 1e — Character pronouns you can actually find and use
The problem, reported by the user and confirmed in the code on `baihe-subtitler`:
- Pronouns can only be set in **Workspace → 3 → "Known characters in this series"**, on a character's card *after* it has been added. `series_characters.gender`, the selectbox at `workspace_tab.py` around line 1355.
- The "Add a known character" form only asks for a name.
- A drama **without a series** has no way to set pronouns at all: `build_character_gender_hints` only reads `db.list_series_characters(drama["series_id"])`.
- The help text on "Default ambiguous pronouns to she/her" (around line 1178) says gender can also be set "under 6. Name your characters", but section 6 has no such control.
- Only `female`/`male` are supported. There's no they/them or custom option.
- `cli.cmd_translate` never adds the gender hints block, so CLI translations ignore pronouns (a UI parity gap).

Changes:
1. **Pronoun options:** she/her, he/him, they/them, plus **Custom…** (free text, e.g. "xe/xem"). Store the value as the pronoun text itself (e.g. "they/them"); keep existing `female`/`male` values working by mapping them.
2. **Set it where you'd look:**
   - Add a Pronouns field to the "Add a known character" form.
   - Rename that area **"People & pronouns"** (inside the glossary section) so it's easy to spot.
   - Add a Pronouns field to **section 6's per-drama characters**, so standalone dramas work too. Store it on the per-drama `characters` row, with the linked series character's value used as the default.
3. **Use it everywhere:**
   - `build_character_gender_hints` merges series and per-drama characters, with the per-drama value winning.
   - Step 1's speaker prefix includes the pronouns, e.g. `[Xiaoling (she/her)] 你好`, so the translator sees them on the exact line.
   - `cli.cmd_translate` sends the same block as the UI.
4. **Fix the section 5 help text** so it points to the real places.

**Exit:**
- Tests show:
  - a standalone drama's per-drama pronouns reach the prompt;
  - they/them and custom values reach it;
  - the per-drama value overrides the series value;
  - old `female`/`male` rows still produce she/her and he/him;
  - the CLI sends the same hints as the UI.

### Step 2 — R0: Permanent line IDs
- Give `lines` a stable primary-key id that survives merges, edits and re-saves.
- Replace delete-all in `db.save_lines` with an upsert/diff by id. Also fix its double `conn.close()`.
- Move `translation_notes`, `line_emotions`, `consistency_issues` and `reading_history` from `idx` to line id, with a one-time migration for existing projects.
  - **Guardrail:** back up the affected tables (a straight copy, same idea as Step 1b's `db.snapshot_database`) before the migration runs, and make the migration idempotent/resumable — safe to re-run if the app closes or crashes partway through, rather than needing a hand restore from the backup. This runs against every existing project's real data, so it needs to survive being interrupted.
- Add one shared row→`Line` loader and use it in `cli.cmd_translate`, `cli.cmd_dub` and `workspace_tab`. This fixes `cmd_dub` dropping flags.
- Switch R5's translation ids to the real line ids.
- Make background jobs save only the fields they own (see Step 1b #1), then remove the short-term "one job at a time" block.
- Also cover a background **transcription** job that finishes while a translate/flag job is running. Its completion handler replaces all lines on the page, which Step 1b doesn't lock; it's rare, but the same kind of race.

**Exit:** after a merge, a note or flag that was attached to a line is still attached to the same line.

### Step 3 — R1-lite: Keep the original transcript
- When transcription finishes, write the untouched output once to `drama_dir/raw_transcript.json` (segments, text, timings, backend and model used). Never overwrite it; a new transcription run writes `raw_transcript.<timestamp>.json` instead.
- Add a small "Compare with original" / "Restore original text for this line" option in the review step.
- **Out of scope:** a general artifacts table, versioning for every stage, and parent/child lineage.

**Exit:** after lines are edited, merged or re-transcribed, the raw transcript file is byte-identical to the original, and a single line can be restored from it.

### Step 4 — R2: Re-run speaker detection on its own
- Save pyannote output to `drama_dir/diarization_turns.json` instead of keeping it only in `st.session_state`.
- Add a pure `merge_speakers(lines, turns)` step that reuses `diarize.assign_speaker_to_line` and `label_lines_with_speakers`.
- Add a **"Re-run speaker detection"** button in the UI and a `cli.py diarize` command. Both use the stored audio and never re-run ASR.
  - **Guardrail:** re-running can silently overwrite speaker labels the user already hand-corrected. Track which lines have a manually-set speaker (a boolean flag, set whenever a line's speaker is changed by hand rather than by `merge_speakers`), and either skip those lines on re-merge by default, or show a confirmation naming how many manually-corrected lines would be affected before proceeding. Never overwrite a manual correction silently.
- Switch to `pyannote/speaker-diarization-community-1`, falling back to `3.1` if it can't load.
- **Out of scope for now:** windowed diarization for multi-hour audio. Add it only if long VODs become common.

**Exit:** changing `expected_speakers` and re-running relabels lines, and a test asserts that the ASR mock is never called. A test shows a manually-corrected line's speaker survives a re-run unless the user explicitly confirms overwriting it.

### Step 5 — R3-lite: Local-model defaults
- Change the `OllamaEngine` default to **`qwen3:8b`** (checked directly against the originally-planned `qwen2.5:7b`, prompted by the user asking whether the current model choices are still the best available — Qwen3-8B is confirmed to outperform Qwen2.5-7B on translation specifically: FLORES+ COMET scores, e.g. Chinese→Arabic 19.74 vs. 17.39, and a literary-translation CEA100 score of 65.77 vs. 63.97, at essentially the same parameter count. A like-for-like swap, not a bigger model, so it doesn't reopen the "fewer options" question). Offer 14B as an opt-in labelled as not fitting cleanly in 8 GB, and add a request timeout. **Confirm actual quantized VRAM footprint against real hardware before shipping the default** — search-level VRAM figures for Qwen3-8B were inconsistent between sources and weren't independently verified to the depth this repo's other model claims are.
- After each GPU stage (transcription, alignment, diarization) finishes, clear the model caches and call `torch.cuda.empty_cache()` when CUDA is available.
- **Out of scope:** a full `model_manager` module. Build one only if out-of-memory crashes actually happen.

**Exit:** the Ollama default is `qwen3:8b` with a timeout, and a test shows the model caches are empty after a stage completes.

### Step 6 — Transcription quality
1. **Whisper settings** (`core.transcribe_for_timing`).
   - Default to `condition_on_previous_text=False`, and add `no_repeat_ngram_size=3` and a mild `repetition_penalty` (about 1.1). This prevents repeated-phrase loops at the source.
   - Keep `filter_hallucinated_segments` as a backstop.
   - Add `large-v3-turbo` as a model choice. It's much faster, but reported weaker on Japanese and Korean, so label it that way and keep `large-v3`/`medium` as the default for ja/ko.
   - Add an opt-in "fast mode" using faster-whisper's `BatchedInferencePipeline`, which is roughly 4× faster.
2. **Qwen3-ForcedAligner reliability** (`forced_align`).
   - The known issues are zero-duration word spans (Qwen3-ASR #197) and timing that drifts out of sync after about 30 s on longer inputs.
   - Lower the chunk target in `_bucket_into_chunks` from about 280 s to about 60 s.
   - Detect zero-duration or non-increasing timings. For those lines, fall back to the diff alignment (`core.align_transcript_to_timing`) and flag them for review.
3. **Vocal separation** (`audio_preprocess`).
   - Demucs's original repo is archived and no longer maintained.
   - Add an optional `audio-separator` backend using a Mel-Band RoFormer vocal model, which gives cleaner vocals on content with background music.
   - Keep Demucs as the fallback.
4. **Optional audio-derived emotion/event tagging alongside Whisper** — [`FunAudioLLM/SenseVoice`](https://github.com/FunAudioLLM/SenseVoice), checked directly: real, actively maintained (9.4k stars), code MIT-licensed (model weights under the separate FunASR Model Open Source License — note this in Settings next to the option, same pattern as other model-license caveats already in this doc). `SenseVoiceSmall` natively outputs 7-category emotion tags (happy/sad/angry/neutral/fearful/disgusted/surprised) and 8 audio-event tags (BGM/speech/applause/laughter/cry/sneeze/breath/cough) as part of transcription, covering exactly Baihe's actual content languages (Mandarin, Cantonese, English, Japanese, Korean) — not a broader multilingual claim than that, so **not** a Whisper replacement. This is a second, audio-derived emotion signal distinct from `emotion.py`'s existing text-based detection (already wired to Chatterbox TTS delivery in Step 11b) — add as an optional pass alongside Whisper transcription, surfaced next to the existing text-based emotion tag rather than merged with it, since the two can disagree and the user should see both rather than one silently overriding the other.

**Exit:** mocked tests cover the new Whisper kwargs, the smaller chunk size, the zero-duration fallback and separator backend selection, and a test shows SenseVoice's emotion/event tags are surfaced alongside (not merged into) the existing text-based emotion detection.

### Step 6b — Export formats (VTT and ASS) *(un-deferred by request — was R6)*
SRT already works everywhere, so this is additive, not a fix.
1. **VTT writer** next to `core.lines_to_srt`/`lines_to_bilingual_srt` — same structure, WebVTT's header and timestamp format.
2. **ASS writer** with real style controls — this is the format that makes styled/"clip streamer"-look subtitles possible; VTT and SRT can't carry per-line font/colour/position. Expose, with sensible defaults so nobody has to touch them:
   - font family (a short list of fonts that are commonly preinstalled, plus a free-text field for any font installed on the machine doing the export/burn — note in the UI that an uncommon font must be installed locally or it silently falls back);
   - size, bold and italic on/off, primary (fill) colour, outline colour and outline width, and a basic text-alignment choice (bottom/top, left/centre/right) *(these last two — italic and alignment — confirmed against VideoTranscriber's actual `app.py`, whose ASS style dict already exposes exactly these fields as plain Streamlit widgets, so they're cheap to add now rather than defer)*;
   - one colour per known character (from `characters`/`series_characters`), reusing Step 1e's character list, so each speaker is visually distinct without the user setting each one by hand;
   - a couple of starting **presets** the user can tune from, not a fixed look — e.g. "Clean" (today's plain default) and "Streamer clip" (bold, larger size, thick high-contrast outline — the common shape of Japanese clip-channel subtitles: legible over busy video/gameplay, not an exact copy of any one channel's style). Position/margin stay at sensible defaults, not exposed yet.
3. **Long-cue handling**, shared by all three writers: split a cue over a configurable character-per-line limit (default per source language, e.g. tighter for CJK) at a sentence or clause boundary where one exists, otherwise at the nearest space; never mid-word.
   - Add a defensive overlap clamp right before writing any of the three formats: if line N's end is after line N+1's start (possible after a manual edit or merge, even though `core._lines_from_char_times` already guarantees non-overlap at alignment time), trim N's end back to N+1's start and flag the line for review rather than exporting an invalid cue.
4. **Subtitle reading-speed (CPS) warning** *(idea from Whishper's editor and ZastTranslate's per-script CPS table; genuinely missing — confirmed against the code, since the app's existing `chars_per_second` check in `core.diagnose_line_coverage` only flags mis-segmented ASR on the source side, never whether the translated text is readable in the time it's shown)*.
   - Starting CPS ceiling, read directly out of ZastTranslate's `fitted_cps_config.py` (the one file of theirs that actually resolved when fetched — the rest of that project's claims are unverified, see §6): CJK ≈ 6.0–7.0, Arabic/Hebrew/Hindi/Thai and similar scripts ≈ 7.0, most Latin-script languages ≈ 8.0–9.0, default fallback 7.5. A starting table, not a precision model — adjustable later.
   - Flag any line whose **translated** text exceeds its script's ceiling for its own duration, using the existing flag mechanism so it shows up in the review queue like any other flagged line.
   - Surface the same check next to the style controls in this step's export panel, so a line that's fine to read in the app but too dense as a subtitle gets caught before export, not after.
5. Add format choice (SRT/VTT/ASS) and the style controls above next to the existing export button, defaulting to SRT + "Clean" so nothing changes for anyone who doesn't touch the new controls.
   - **GUI gap:** with this many style knobs (font, size, bold/italic, two colours, outline width, alignment, presets), nobody should have to export-and-check repeatedly to see what they picked. Add a small live preview — render one sample line (a real line from the drama, not placeholder text) with the current style settings as an image or styled HTML block, updating as controls change, so the look is visible before committing to a full export.
6. **Fix: expose hardsub (burned-in) styling, which already exists in the backend but is invisible in the UI.** `video_export.burn_subtitles` already accepts `font_size`/`font_color`/`outline_color`, but section 10's "Export full subtitled episode" calls it with no arguments (`workspace_tab.py` ~line 2819), so every hardsub export silently uses the same fixed look regardless of what's picked elsewhere. Two changes:
   - wire the same style controls from item 2 into that call;
   - when the chosen format is ASS, burn the `.ass` file directly (`ffmpeg -vf "subtitles=file.ass"`, no `force_style` needed — libass reads the per-speaker styles straight from the file) instead of building one flat `force_style` string, so hardsub gets per-speaker colours too, not just softsub.

**Exit:**
- A VTT and an ASS export of the same drama both load and play correctly in mpv and VLC, with ASS showing distinct per-speaker colours and the chosen font/size/outline.
- A test shows a long line is split at a sensible boundary, not mid-word.
- SRT export is byte-identical to before this step.
- A hardsub export using the "Streamer clip" preset visibly differs from one using "Clean" (checked by hand — burning video isn't something a unit test can judge).
- A test shows burning an ASS file skips `force_style` entirely.
- A test shows a manually-created overlap gets clamped and flagged, not exported as-is.
- A test shows a translated line that's too dense to read in its duration gets flagged, and a normal-length line doesn't.
- Manual check: changing a style control (font, colour, preset) visibly updates the live preview without exporting.

### Step 6c — Meaning-based subtitle re-segmentation *(idea from VideoLingo)*
A different, upstream problem from Step 6b's export-time character wrapping. Today's line boundaries come entirely from Whisper's VAD (silence gaps) — a line can end mid-sentence just because the speaker paused, or run two separate thoughts together because they didn't. Confirmed VideoLingo's actual mechanism by reading `core/_3_1_split_nlp.py` and `_3_2_split_meaning.py`, not just its README:
1. **Rule-based passes first** (their real order, via spaCy): split on hard punctuation, then on commas, then on sentence boundaries, then split anything still too long at its grammatical root. Adopt the concept using what's already in `segment.py` (jieba/sudachipy/kiwipiepy, already per-language) rather than adding spaCy as a new dependency.
2. **One LLM pass**, only for lines still too long after the rule-based passes — asks it to return `[br]` break markers in a JSON response, not to rewrite or translate anything. Their matching step is worth adopting directly: the model's suggested break text is matched back to the *original* text with `SequenceMatcher` (>0.9 similarity required), retried up to 3 times on a bad match, so a hallucinated or reworded response can't corrupt the real text — it can only fail to find a good split, never silently rewrite content.
3. Re-run alignment (Step 2/3's existing timing-reconstruction path) against the new boundaries so start/end times still line up.
4. Optional per drama, off by default — this changes line boundaries, which is a bigger structural change than Step 6b's export-time wrapping, so it needs to be something the user opts into per drama, not silently different from today's output.
   - **Guardrail:** re-segmenting after translation, notes or flags already exist orphans them — the old line ids (Step 2) no longer map to sensible boundaries. Before running, check whether the drama has any translated lines. If it does, warn plainly ("N lines are already translated — re-segmenting will require re-translating the affected lines") and, on confirmation, clear `.en`/flags/notes only for the lines whose boundaries actually changed, leaving untouched lines alone rather than blanking the whole drama.

**Exit:** a mocked test shows a run-on ASR segment gets split at a genuine clause boundary (not mid-word, not arbitrarily by length), and a normal segment passes through unchanged; timing stays continuous across a split. A test shows re-segmenting a drama with existing translations warns first and only clears translations/notes/flags for lines whose boundaries actually changed.

### Step 6d — Vertical/shorts export *(idea from OpenCreator's "Portrait Render" and ZastTranslate's "Viral Shorts Studio")*
Directly relevant to the "clip streamer" look you asked about earlier — that culture is built around vertical/shorts format specifically, not just subtitle styling.
1. Given a drama (or a selected time range within one), render a 9:16 vertical version: centre-crop by default, with a manual crop-position adjustment per drama rather than trying to auto-detect a face/subject.
2. Burn subtitles using Step 6b's ASS styling (so the "Streamer clip" preset and per-speaker colours carry over), sized and positioned for the vertical frame.
3. Export as its own video file alongside the existing horizontal export — this is additive, not a replacement for the existing "Export full subtitled episode" step.
   - **Guardrail:** nothing stops re-encoding a full multi-hour episode vertically by accident, which is slow and heavy. Before starting, show an estimated time/output size based on the selected range's duration (reuse Step 9b's ETA approach), and for a selection longer than a set threshold (e.g. 20 minutes) show a clear prompt suggesting a shorter clip range instead of a hard block — this is a real, if less common, use case, so don't disable it outright.

**Exit:** a vertical export of a short clip plays correctly, is genuinely 9:16, and its burned subtitles are legible without manual repositioning. A test shows the time/size estimate appears before the job starts, and a long selection shows the "consider a shorter clip" prompt.

### Step 7 — Reflect translation mode *(new feature; needs Step 1)*
- **Correction after reading VideoLingo's actual code** (`core/translate_lines.py`), not just its README: it's a **two**-call pipeline, not three — `get_prompt_faithfulness()` produces a `"direct"` (literal) translation, then `get_prompt_expressiveness()` takes that plus the source and produces a `"free"` (natural-reading) one; reflection/self-critique is folded into that second prompt internally, not a separate visible pass. pyvideotrans's own three-step framing is closer to what's below.
- Add an optional "High quality" setting as a genuine **three** separate calls anyway — a deliberate choice, not a copy of VideoLingo's two-call version: making the middle critique its own pass means it can be **stored and shown**, not just silently folded into a rewrite prompt where the reasoning disappears.
  1. **Faithfulness pass:** translate the batch preserving exact meaning, not yet polished for how it reads.
  2. **Reflection pass:** the same engine critiques *that specific translation* — where it's technically correct but reads unnaturally, plus accuracy, pronouns/gender, glossary use, tone and register.
  3. **Expressiveness pass:** rewrite using the reflection, now optimizing for how it reads as a subtitle, not just correctness.
- It works through the same engine interface for every LLM engine: Claude, Gemini, DeepSeek and Ollama.
- It costs about 3× as much, so show the estimated cost (`translate_engines.estimate_cost`) before the user starts it.
- Every pass keeps the id-keyed JSON from Step 1 — **deliberately not** VideoLingo's own matching method, which uses `SequenceMatcher` fuzzy-similarity (>0.9 threshold, errors below it) to associate returned lines back to source lines rather than an id. That's strictly less robust than Step 1's exact id match, so nothing here adopts it.
- Save the reflection critique as translation notes, so the reasons for the final wording can be reviewed.

**Exit:** a mocked engine test shows 3 calls per batch, with ids kept at every pass, and the reflection critique stored as notes.

### Step 7b — Whole-document-first glossary extraction *(idea from VideoLingo)*
`translation_guide.extract_terms_llm` already exists and works — confirmed it extracts terms chunk by chunk, each chunk processed in isolation (only "already in glossary" carried between chunks). Checked VideoLingo's real `core/_4_1_summarize.py`, and their approach is more specific than "summarize first": `get_summary()` takes the **combined, whole-document** source content in one call and directly produces the terminology list — `{src, tgt, note}` per term — so the model sees the full story at once before proposing any term, rather than only ever seeing one chunk at a time. That combined-context view, not a separate prose synopsis, is the actual mechanism worth adopting.
1. Add a first pass that runs `extract_terms_llm`-style extraction over a **combined sample across the whole drama** (not per-chunk) — enough lines from spread-out points in the drama to see the range of names/relationships, not just its first few hundred lines.
2. Feed that whole-document term list into each chunk's existing per-chunk extraction pass as known context (same `known_terms` parameter `extract_terms_llm` already accepts), so later, more-detailed chunk passes stay consistent with what the whole-document pass already established.
3. This is one extra whole-document call per run, not one per chunk.

**Exit:** a mocked test shows the whole-document pass samples from spread-out points in the drama (not just the start), its output reaches every chunk's extraction as known context, and existing `extract_terms_llm` tests still pass.

### Step 8 — Recurring-voice suggestions *(new feature, experimental; needs Step 4)*
- With pyannote 4, `DiarizeOutput.speaker_embeddings` gives one voice fingerprint per detected speaker. Save them with the diarization turns.
- Keep an averaged fingerprint for each series character in `series_characters`.
- On a new episode, suggest "SPEAKER_01 sounds like <name> (similarity 0.82)" by cosine similarity, above a threshold you can adjust.
- **The user always confirms each match; nothing is labelled automatically.**
  - **GUI (was implicit, now explicit):** show each suggestion in the speaker-review step (next to the existing diarization UI from Step 4) as its own row — the suggested name, the similarity score, and **Accept**/**Reject** buttons per row. Accept sets the character name on that speaker label the same way a manual assignment would; Reject dismisses just that suggestion and never re-shows it for that speaker/episode pair.
- This matches Phase 1 §3.4: the feature is experimental, and nobody has shown it works reliably across different recordings.

**Exit:** a test with fake embeddings produces the correct ranking, respects the threshold, and never assigns a name without confirmation. A UI test shows Accept sets the character name and Reject dismisses the suggestion without changing anything.

### Step 9 — Cost controls & bulk discounts *(needs Step 1's id-keyed output **and** Step 2's permanent line IDs)*
1. **Spending cap per job.**
   - Before a job starts, estimate its cost from the line count and the average tokens per line (`translate_engines.estimate_cost`).
   - Show the estimate, and let the user set "stop if this job would cost more than $X". Also allow an optional monthly cap, checked against the existing `usage` log.
   - While the job runs, add up the actual cost after each batch and stop cleanly (keeping finished work) once the cap is reached.
2. **"Bulk (cheaper, slower)" mode for work nobody is waiting on.**
   - Applies to full-drama translation, the review-queue flagging, consistency checks, emotion detection, translation notes and reflect mode (Step 7).
   - Does **not** apply to Q&A, single-line fixes, Live translation or the Site Navigator, where someone is waiting.
   - **Claude — Message Batches API** (`client.messages.batches.create`):
     - 50% off all tokens.
     - Most batches finish within an hour; the maximum is 24 hours.
     - Up to 100,000 requests or 256 MB per batch; results are kept for 29 days.
     - Prompt caching works inside batches too.
   - **Gemini — Batch API:** 50% off, with a target turnaround of 24 hours. Context caching can be combined with it.
   - **DeepSeek — no batch API, but off-peak pricing:** about 50% cheaper outside its peak UTC hours. Add a "run in the next off-peak window" option that schedules the job.
   - How it works:
     - Submit every batch at once, with `custom_id = "<drama_id>:<batch_index>"`. Inside each request, number the lines by their **permanent line id** from Step 2, not their position.
     - Save the batch id on the drama, together with each submitted line's id and a hash of its source text, so a restarted app can pick results up again.
     - Poll in a background job, then apply results **by line id, never by position**. Results can arrive hours later and in any order.
     - When applying a result:
       - if a line was deleted or merged since submission, drop its result;
       - if a line's source text changed since submission, don't apply its result; flag the line for review instead.
     - While a bulk job is pending, line editing stays allowed, because the line-id and hash checks make it safe.
   - **GUI gap:** a batch can stay pending up to 24 hours, possibly across an app restart, with nowhere to see it. Add a small "Bulk jobs" panel (Library or the drama's own Workspace) listing each drama's pending/recent bulk submissions — engine, submitted time, status — with a manual "Check now" action and a "Cancel" action (cancel via the provider's own batch-cancel endpoint where offered; otherwise just stop polling and drop the stored batch id, results that still arrive are ignored). If polling hits an auth error (e.g. the API key was rotated after submission), surface that clearly on the panel rather than failing silently and leaving the batch id orphaned.
   - One trade-off to show in the UI: because every batch is submitted at the same time, the look-back context can only use *source* lines, not earlier translations. That's slightly less consistent than live mode. Optionally, add a second consistency pass (the consistency check, also batched) to make up for it.
3. **Make prompt caching count** (all engines; no quality change).
   - The prompt should always start with the same stable part (system instructions → style guide → glossary → reference novel), with the changing lines after it. Then Claude's `cache_control`, Gemini's implicit caching and DeepSeek's automatic prefix caching all hit. Cache reads cost about 10% of the normal input price.
   - Check this from each response's usage (`cache_read_input_tokens` on Claude), and show the cache-hit share next to the cost in the usage view.
   - Nothing that changes between requests (timestamps, batch numbers) may go in the stable part.

**Exit:**
- A mocked test shows a job stops at the cap and keeps its finished lines.
- A mocked batch client returning results out of order still assigns every translation to the right lines.
- If a line is merged or edited between submission and results, its result is dropped or flagged, never applied to the wrong line.
- A restarted app picks up a pending batch by its saved id.
- A test shows the Bulk jobs panel lists a pending batch, Cancel stops polling it, and a polling auth error shows on the panel instead of failing silently.
- A test shows the stable prompt part is byte-identical across batches of the same drama.

### Step 9b — Job ETAs, model disk management, bulk series translate, Live chunk guard
Independent, additive gaps found while reviewing for efficiency and missing functionality — no shared code between them, grouped here to keep the step count down.

1. **Time estimate on long jobs.** `background_jobs` already tracks `started_at` and a `progress` fraction (`update_progress(job_id, frac, message)`); nothing currently uses them together. Add a simple ETA next to the existing progress bar: `elapsed = now - started_at`, `remaining ≈ elapsed * (1 - frac) / frac`, shown as "~N min remaining" once `frac` is past a small threshold (too noisy right at the start). Pure UI addition — no new job-tracking fields needed.
2. **Downloaded-model disk management.** `storage.py` reports disk usage for the app's own `library/` folder, but Whisper/pyannote/Qwen3-ASR/ForcedAligner/F5-TTS weights live in Hugging Face's own cache (`~/.cache/huggingface` by default) with no visibility or cleanup from inside the app. Across several ASR/TTS backends this can reach tens of GB. Add a small panel (Diagnostics, next to the existing dependency checks) that lists what's in the HF cache with each entry's size, and a delete button per entry — a thin wrapper over `huggingface_hub.scan_cache_dir()`, which already gives size-per-revision without reimplementing cache-format parsing.
   - Same Diagnostics screen, same PR: add a **"Copy diagnostics for support"** button that runs the existing key/token redaction (`translate_engines.redact_secrets`, Step 1b) plus a pass that also strips local file paths and the OS username from the output *(idea from OpenCreator's "redacted diagnostics" — Step 1b already redacts keys from stored errors, but nothing currently redacts what a "copy for support" action would show, and a raw library path can leak the machine's username)*.
   - **Model/engine version panel, same Diagnostics screen** *(idea prompted by the user asking how they'd know when a model they use has an update — answered on the planning side with the roadmap's own §7 registry, but the user then separately asked for the pinned versions to also show in-app)*. A static table, next to the HF-cache list above: one row per AI model/engine actually wired into the app (Whisper, Qwen3-ASR, SenseVoice, `manga-ocr`, PaddleOCR-VL-For-Manga, the Scanlate detector/inpainting models, OmniVoice, GPT-SoVITS, Chatterbox, TADA, pyannote, and the active Ollama tag), each showing what identifies it (a pip package version via `importlib.metadata.version()` where it's a real package, or a hardcoded HF repo id + revision string for a bare model checkpoint that isn't pip-versioned at all) and a link to its source page. **No outbound network calls** — this only reports what's actually installed/configured locally, the same no-surprise-network-calls posture as the rest of Diagnostics; it does not check anywhere for whether something newer exists. Include it in "Copy diagnostics for support" too, so a support conversation doesn't need a separate question about what's installed.
3. **Bulk "translate everything untranslated" across a series.** Library's existing bulk actions (`tabs/library_tab.py` ~line 127, `library_bulk_select`) cover status, delete and export, but not starting a job — translating multiple dramas still means opening each one individually. Add a bulk action that starts a `run_translate_job` per selected drama with no translation yet, each drama's own saved engine/glossary/style/locale settings (same as its own Workspace tab would use), queued one at a time rather than all at once (see Step 3's/Step 1c's GPU-load reasoning — avoid starting several GPU-touching jobs simultaneously). Respect the Step 1b line-writing job guard per drama; skip (don't queue) a drama that already has one running.
   - **GUI gap:** with several dramas queued, there's nothing showing overall progress — just each drama's own bar once you're on its page. Add a small combined status line in Library while a bulk-series job is active: "Translating 2/5 — *current drama name* (60%)", with the same Cancel behaviour as a single job (stops the current drama's job, leaves the rest of the queue un-started).
4. **Cookie-based login for downloads, as a real setting, not just an error hint.** The app already isn't YouTube-locked — `video_download.py`'s own docstring says "YouTube and the many other sites yt-dlp supports," and yt-dlp itself supports Bilibili, TikTok and Instagram natively — but `--cookies-from-browser` is currently only *mentioned* inside a YouTube-specific error message (`live_translate.py`), not exposed as something the user can turn on. TikTok and Instagram in particular block plain unauthenticated requests far more aggressively than YouTube does. Add a Settings field (which browser to pull cookies from, or a cookies file path), pass it through to yt-dlp in both `video_download.py` and `live_translate.py`, and update the "no formats found" error and Diagnostics copy to mention it generally rather than only for YouTube. *(Idea prompted by comparing platform coverage against OpenCreator/302_video_translation — the download capability was already mostly there; this closes the practical reliability gap, not a missing extractor.)*
5. **A stale-chunk guard for Live translation** *(idea confirmed by reading Echoly's actual `content.js`, not just its README — its real "token-guarded async" pattern: a counter incremented once per session/setting change, captured by closure in each async call, checked before every state mutation; a callback whose captured value no longer matches the current counter is discarded)*. `live_translate.py`'s chunk loop has no equivalent: if the user changes a setting or stops/restarts mid-stream while an older chunk's transcribe/translate call is still in flight, that call's result can land after the newer state has already moved on. Add a simple integer "generation" counter on the live session, incremented on every stop/restart/setting change; each chunk's async result is applied only if the generation it was started under still matches current.

**Exit:**
- A test shows the ETA display appears once progress is non-trivial and disappears/holds sensibly at 0% and 100%.
- A test shows the model-cache panel lists entries with sizes and that deleting one actually frees the space (using a fake cache dir, not the real HF cache).
- A test shows the bulk translate action starts one job per eligible selected drama and skips any drama with a job already running, using each drama's own settings.
- A test shows the combined status line reflects which drama in the queue is currently running and its progress.
- A test shows the cookies setting reaches yt-dlp's options in both `video_download.py` and `live_translate.py`.
- A test shows the "copy diagnostics" output contains no file path or username, even when the raw diagnostics do.
- A test shows the model/engine version panel lists each wired-in backend with a version or repo+revision string, makes no network call, and its content is included in the "copy diagnostics" output.
- A test shows a chunk result that finishes after a generation bump is discarded, and one that finishes before the bump is applied.

### Step 9c — Drama/project presets *(idea from OpenCreator's "creation templates")*
Library's own framing is managing "dozens of titles," but every new drama starts from scratch: engine, model, style preset, locale, content-type defaults all get re-picked by hand each time. A template captures a full Workspace configuration — engine/model choice, style preset, locale, default speaker-gender-default setting, glossary scope — as a named, reusable preset.
1. **"Save as preset"** in Workspace, from the current drama's settings.
2. **"Apply preset"** when creating a new drama (or on an existing one), which fills in the same fields, still editable afterward — never silently locks anything.
3. Store presets at the library level (not per-series), so one preset works across unrelated series/projects.
4. **GUI gap: manage what you saved.** Save/Apply alone means presets only ever accumulate. Add a small "Manage presets" list (Settings or Library) showing every saved preset with its captured fields at a glance, and **Rename**/**Delete** actions per preset. Deleting a preset never touches any drama it was already applied to — presets are a one-time fill-in, not a live link.

**Exit:** a test shows applying a preset to a new drama sets all its captured fields, and none of them are frozen against later manual changes. A test shows deleting a preset doesn't change any drama it was previously applied to, and renaming updates the name without touching its fields.

### Step 10 — One-click Windows launcher, own window & desktop shortcut *(convenience)*
- Add a `start.bat` (plus an optional `start.ps1`) that:
  - creates or activates the venv on first run;
  - installs `requirements-core.txt` if it's missing, using the known-good versions from Step 1c (`-c constraints.lock.txt` if it exists, else `-c constraints.txt`);
  - runs Diagnostics' dependency check and prints anything missing in plain words (ffmpeg, the JS runtime, CUDA);
  - starts `streamlit run app.py --server.headless true`, so no extra browser tab opens;
  - opens the app in its **own window**.
- **Own window** (it looks like a desktop program, with no tabs or address bar):
  - Use Microsoft Edge's app mode: `msedge --app=http://localhost:<port>`. Edge ships with Windows, so nothing new needs installing.
  - Fall back to Chrome's `--app=` flag, then to the default browser, if Edge isn't found.
  - Wait until the server answers before opening the window, so it doesn't show a "can't connect" page.
- It's safe to run twice: if the app is already running on the port, it just opens the window.
- **Desktop shortcut:**
  - Add a `make_shortcut.bat` (or a first-run prompt in `start.bat`) that creates a **"Baihe Subtitler"** shortcut on the desktop pointing to `start.bat`.
  - Use a bundled `.ico` app icon, and set the shortcut to start minimized so the console window stays out of the way.
  - Create it with PowerShell's `WScript.Shell` `CreateShortcut`; no extra dependencies.
- **Out of scope:** a packaged `.exe` (PyInstaller or similar), because it would be multi-GB and couldn't add optional features later. The app is for personal use only, so the launcher is enough.
- Add a short "Double-click `start.bat`, or the desktop shortcut" section at the top of the README's Installation section.
- **Uninstaller.** This app genuinely has very little system footprint to clean up in the first place — no registry entries, nothing installed to Program Files, no PATH changes, since it's just a folder with a Python virtual environment and a desktop shortcut inside it. Add `uninstall.bat` that:
  - removes the desktop shortcut and Start Menu entry (if `make_shortcut.bat` created one);
  - removes the `venv`/virtual environment folder and any downloaded model weights under the app's own folder;
  - **asks explicitly, as a separate step, before touching `library/`** — "Also delete your library (all your projects, translations, audio/video, backups)? This cannot be undone." Defaults to **No**. Deleting the app's own installed files never implies deleting the user's data; those are two different confirmations, not one.
  - does **not** try to remove separately-installed system tools the launcher may have prompted for (ffmpeg, Deno, Ollama, CUDA) — those are the user's own system-wide installs from other installers, not something this app's uninstaller should touch or could safely remove without risking something else on the machine that depends on them. State this plainly in the uninstaller's own output, so it's not read as silently incomplete.
- **Portable mode.** A genuinely portable single-file/USB-stick build (bundled Python interpreter, all ML dependencies included, no install step) hits the same multi-GB problem that already ruled out a packaged `.exe` — not pursued, for the same reason. What's realistic and worth building: keep every path the app writes to relative to its own folder rather than a fixed OS location (so `library/`, model caches the app manages, and settings all live inside the one folder), so the whole folder can be copied or moved to another PC — a USB stick, an external drive, a new machine — and just works there, as long as that machine already has Python. Add a `--portable` flag (or detect a marker file) that keeps this behaviour explicit rather than assumed. State the real limit plainly in the README: this moves *the app and its data*, not *the need for Python and system tools already being present*.

**Exit:** on a clean Windows machine with Python and ffmpeg installed, all of these work with no typed commands. Check them by hand; this can't be unit-tested.
- Double-clicking `start.bat`, or the desktop shortcut, opens the app in its own window.
- Running it a second time just opens the window again.
- The shortcut shows the app icon.
- Running `uninstall.bat` removes the shortcut and venv, asks separately about `library/`, defaults that question to No, and leaves ffmpeg/Deno/Ollama/CUDA untouched.
- Copying the app's folder to a second PC (with Python already installed there) and running `start.bat` from the new location works, with the original library data intact.

### Step 11 — Scanlate: finish the ML detector, add real inpainting, auto-route OCR
`scanlate.py`'s own comments already point at the right fix — a `detect_bubbles_ml()` stub naming a real model that was never wired up, and an honest note that inpainting is "a plain rectangular inset, not a shape-aware mask." Checked seven comparable open-source manga/comic translators' actual code and license (not just their READMEs) to find what's real, free, and safe to build on:

| Project | License | Verdict |
|---|---|---|
| [manga-image-translator](https://github.com/zyddnys/manga-image-translator), [Kites](https://github.com/Unheat/Kites) | GPL-3.0 | **Not usable** — would force this app's whole codebase open |
| [gnurt2041/MangaOCR](https://github.com/gnurt2041/MangaOCR) | No license stated | **Not usable** — unlicensed means all rights reserved by default |
| [comic-translate](https://github.com/ogkalu2/comic-translate) | Apache-2.0 | Ideas only (see item 4) — its own demo images (Frieren, etc.) have no stated license separate from the code and are from currently-published, copyrighted manga; **not** brought in as test fixtures |
| [koharu](https://github.com/koharu-rs/koharu) | MIT/Apache-2.0 (app); its detection *model* has a training-data caveat, see item 3 | Its actual detection model is the real find here (item 3) |
| [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) | Apache-2.0 | Already a Baihe dependency, nothing new to add |
| [EasyScanlate](https://github.com/Liiesl/EasyScanlate) | MIT | Rust + a GUI framework — architecture doesn't transfer to this Python app |

1. **Finish `detect_bubbles_ml()`, and fix a real dependency mismatch found along the way.** The docstring names `ogkalu/comic-text-and-bubble-detector` (Hugging Face, Apache-2.0, 3 classes: bubble / text-in-bubble / text-outside-bubble, boxes only — confirmed real) as the intended model, but `requirements.txt` already installs `ultralytics` for it — and that model is **RT-DETR-v2, not a YOLO model**, so `ultralytics` can't actually load it. That mismatch is plausibly *why* the hook was never finished. Swap to whatever this model's real inference stack needs (its Hugging Face card shows ONNX/safetensors, loadable through `transformers` or plain ONNX Runtime — confirm against the current card when implementing) and wire it into `detect_bubbles_ml()` as an optional, better-than-OpenCV-heuristic backend.
2. **Add real ML inpainting**, replacing the current plain OpenCV `cv2.inpaint` as the default when it's installed: [`mayocream/lama-manga`](https://huggingface.co/mayocream/lama-manga) — MIT licensed, a Big-LaMa checkpoint fine-tuned on ~300k manga/anime images specifically (not generic photo-LaMa), ~989MB safetensors, 4-channel input (RGB + mask) → 3-channel RGB output. This is the direct, concrete fix for the "plain rectangular inset, not shape-aware" limitation the code already names — most of the visible improvement comes from this one model, independent of item 1 or item 3.
3. **Optional, more advanced: real shape-aware masking**, which item 1's detector does *not* provide on its own (it gives boxes, not masks) — [`mayocream/koharu-layout-rfdetr-seg-2xl-1152`](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152), the actual detection model behind koharu (a separate Hugging Face artifact from koharu's own Rust app — usable from Python without needing any Rust/WebGPU code). Real segmentation output (text / SFX / bubble / panel), ~40MB safetensors, ships its own Python `load_model.py` loader. **License caveat, stated plainly, same as the one already implicit in Baihe's existing `manga-ocr` dependency:** its training data includes Manga109, which carries academic-use terms — fine for this app's personal use, worth knowing if it's ever redistributed or used commercially. Mark this as an optional upgrade over item 1's box-only detector, not a requirement.
4. **Auto-route the OCR backend by source language**, the way comic-translate does, but with maintained choices — `manga-ocr` for Japanese (already a dependency), `paddleocr` for Chinese, `paddleocr`/`tesseract` for Korean. **Explicitly not** adopting comic-translate's choice of Pororo for Korean: checked its maintenance status and even a Hugging Face mirror of just its OCR piece exists specifically because people are worried about the main library's long-term upkeep — a fragile dependency not worth taking on. Keep manual backend override available; auto-routing is just a better default.
   - **Opt-in second Japanese OCR option to try: [`jzhang533/PaddleOCR-VL-For-Manga`](https://huggingface.co/jzhang533/PaddleOCR-VL-For-Manga)**, checked directly — Apache-2.0, a manga-specific fine-tune of PaddleOCR-VL (1.0B params, BF16), 70% full-sentence accuracy on Manga109-s crops vs. base PaddleOCR-VL's 27%, specifically aimed at the vertical-Japanese-text degradation that hurts general OCR models. **Its own model card only benchmarks against base PaddleOCR-VL, not against `manga-ocr`** — there's no confirmed number showing it beats what Baihe already uses, so this is not a default swap. Add as a second selectable Japanese backend (same opt-in pattern as items 2/3/6) and let the manual check below settle it with a real head-to-head.
5. All of this stays optional/opt-in, same spirit as the existing `detect_bubbles_ml()` docstring — the free OpenCV heuristic (detection) and plain `cv2.inpaint` (inpainting) remain the zero-install default; these are better backends for someone who installs the extra weight, not a replacement that changes default behaviour.
6. **Conditional fallback if item 2's LaMa-manga quality turns out not to be enough in real testing:** [`meangrinch/MangaTranslator`](https://github.com/meangrinch/MangaTranslator) (Apache-2.0, read directly — not README-only) uses FLUX-diffusion inpainting (FLUX.2 Klein, Apache-2.0, clean; or FLUX.1 Kontext [dev], non-commercial-only license — prefer Klein) instead of LaMa, which is a genuinely more advanced technique on the manual-check page in Step 2's exit list. Not adopted by default — it's a much heavier dependency (`diffusers`, `sdnq`, `spandrel`, a multi-GB checkpoint vs. LaMa-manga's ~989MB) for a gain that hasn't been confirmed to matter on Baihe's actual pages. **Trigger to revisit:** if the manual check below shows LaMa-manga leaving a visible edge or texture mismatch that matters on real pages, swap in FLUX.2 Klein as a third, heavier inpainting option, same opt-in pattern as items 2/3 — not a replacement for LaMa-manga, an escalation above it.

**Exit:**
- A test shows `detect_bubbles_ml()` actually loads and runs the named model (mocked, no real network/weights in tests) and falls back cleanly to `detect_bubbles_cv()` when the model isn't installed.
- A test shows the LaMa-manga inpainting backend is selected when available, OpenCV inpainting when it isn't.
- A test shows OCR backend selection follows source language by default and can still be overridden manually.
- Manual check (real weights, on your own PC): run detection + inpainting on a real page with both the ML and OpenCV-only paths and compare — the ML inpainting result should show no visible box edge where text was removed. On a Japanese page, run `manga-ocr` and PaddleOCR-VL-For-Manga side by side and compare which reads the actual text correctly more often — no benchmark number settles this for Baihe's content, so this comparison is what decides whether PaddleOCR-VL-For-Manga becomes the default Japanese backend or stays a manual-override option.

### Step 11b — Novel narration TTS quality *(the user's priority is TTS for novels specifically, not video/audio dubbing — every item here is scoped to that, not to matching an external video's timing)*
Re-reviewed the dubbing code in projects already surveyed, filtered specifically for what helps long-form novel narration rather than dubbing-to-a-fixed-video-timeline (which most of these projects are actually built for, and which doesn't apply here — a novel narration has no external timing to hit). `dub.build_narration_track` was read directly first, to know what's already there before proposing anything.

1. **OmniVoice as the primary new narration TTS backend** — `k2-fsa/OmniVoice`, **Apache-2.0**, `pip install omnivoice`. Confirmed directly (not from VoiceStudio's marketing of it — VoiceStudio's own app code is AGPL-3.0 and isn't used at all; only this separate, cleanly-licensed engine is). Zero-shot voice cloning, 600+ languages, claims 40× real-time inference. Unlike F5-TTS (already in Baihe, but CC-BY-NC — non-commercial only) and ElevenLabs (already in Baihe, but a paid cloud API), this is free, local and unrestricted. Add as `synthesize_line_omnivoice`, selectable the same way `synthesize_line_cloned`/`synthesize_line_elevenlabs` already are.
   - **Also add its voice-design capability**, confirmed in its own README (`model.generate(text=..., instruct="female, low pitch, british accent")` — gender/age/pitch/style attributes, no reference audio needed at all). This fills a real gap on its own: novel narration today only offers the fixed default voice or cloning from real reference audio, but most novel characters have no reference audio to clone from. Add as a third per-character voice option in `character_voice_map` — "describe a voice" alongside "pick a fixed voice" and "clone from reference audio."
2. **GPT-SoVITS as a second cloning backend** — `RVC-Boss/GPT-SoVITS`, **MIT**, needs only a 3–10 second reference clip. Independently used by both pyvideotrans and VideoLingo, so it's a well-established, not experimental, choice. Not a replacement for OmniVoice — different tradeoffs (OmniVoice: faster, broader language coverage, voice design; GPT-SoVITS: more battle-tested, usable from even less reference audio). Checked for emotion/style control specifically and confirmed it has none — it's on GPT-SoVITS's own TODO list, not implemented, so this is a cloning-quality option only, nothing more to wire up beyond the engine itself.
3. **Parallelize `build_narration_track`'s clip generation**, which is currently a strictly sequential `for` loop — fine for one audio-drama episode's line count, slow for a whole novel. Confirmed via VideoLingo's actual `_10_gen_audio.py` real pattern (process the first few clips sequentially as a "warm up," then a `ThreadPoolExecutor` for the rest) — but keep Baihe's own existing per-clip caching (`if not os.path.exists(clip_path)`), which VideoLingo's own code does **not** have (confirmed — it overwrites every run with no resume). Baihe is already ahead there; this only adds the parallelization on top of what's already resumable. Force single-threaded for any backend that can't handle concurrent calls safely (VideoLingo forces this for GPT-SoVITS specifically; check OmniVoice/edge-tts/ElevenLabs on their own before assuming they're safe to parallelize).
4. **Decouple TTS-chunk size from subtitle-cue size for narration mode.** `core.chunk_novel_text` already does a decent job (paragraph-first, sentence-split fallback, `max_chars=200`) — this isn't a bug, but that same 200-char chunk currently becomes both the audio-generation unit *and* the on-screen subtitle cue, and those have different ideal sizes: a subtitle cue needs to stay short and readable (Step 6b's CPS work), but audio generation quality generally benefits from more continuous context (better cross-sentence prosody) up to what the TTS engine handles well. For narration mode specifically, raise the text unit actually sent to TTS (e.g. a full un-split paragraph, or several sentences), and only split it into shorter subtitle cues *afterward*, reusing Step 6b's existing long-cue splitting rather than building a second splitter.
5. **Proper audiobook export: M4B with chapter markers**, alongside the existing flat `narration_track.wav`. Not sourced from any one project's code — the general "this should be a real audiobook file, not one giant WAV" idea these narration-focused tools are all built around. Chapter breaks come from `chunk_novel_text`'s existing paragraph boundaries, or the novel's own chapter markers if the source text has them.
6. **Chatterbox for emotion-conditioned delivery, and actually wire it up** — `resemble-ai/chatterbox`, **MIT**, checked directly: a real `exaggeration` parameter (0.0–1.0+, recommended range 0.4–0.7) that scales emotional intensity in the generated voice, plus its own zero-shot cloning, on a small 0.5B backbone. This is what fills the gap below — an engine with a real emotion API to wire into, not a third overlapping cloning-only option. Add as `synthesize_line_chatterbox`, and:
   - **Wire it to the emotion tags `emotion.py` already computes.** Right now those tags are detected and shown for translation guidance but never reach TTS delivery at all (confirmed — no connection in `dub.py`/`workspace_tab.py`). Map each line's detected emotion + intensity to Chatterbox's `exaggeration` value (e.g. a calm/neutral line stays near the low end, a line flagged high-intensity anger or excitement pushes toward the top of the recommended range) so narration audio actually sounds different for an angry line versus a calm one, instead of every line reading in the same flat register regardless of what was detected.
   - Note Chatterbox's built-in PerTh neural watermarking on generated audio — mention this in the UI/docs so the user knows generated narration carries it, not something to strip or work around.
7. **Hume AI's TADA as an option for long, unattended narration specifically** — `HumeAI/tada`, code MIT (model weights under Meta's Llama 3.2 Community License — its own small print, note this in Settings next to the option, same spirit as F5-TTS's/VoxCPM-era licence notes elsewhere in this doc). Its real strength isn't emotion — it's speed and reliability on very long, unattended runs: a genuinely different text-acoustic tokenization aimed at not "going off script" partway through a long chapter, and claimed >5× faster generation. Add as a fourth backend choice, positioned specifically for "narrating a very long novel/chapter and want it to stay on-script the whole way," not as the default — OmniVoice/GPT-SoVITS/Chatterbox remain the defaults for normal-length narration and emotion control respectively.

**Confirmed, but not actionable right now:** ~~Baihe's existing emotion detection (`emotion.py`) does not currently reach TTS delivery at all (checked `dub.py`/`workspace_tab.py` directly — no connection exists). That's a real gap, but neither OmniVoice nor GPT-SoVITS expose real emotion-conditioning as an API to wire it into — OmniVoice's only style control is a single "whisper" flag, not general emotion.~~ **Resolved by item 6 above** — Chatterbox gives a real emotion-conditioning API, and the wiring is now in scope, not deferred.

**Checked and not added, with reasons:** `coqui-ai/tts`'s XTTS v2 — its own code is MPL-2.0, but the actual voice model is locked to the Coqui Public Model License (non-commercial only), and Coqui Inc. shut down, so there's no path to a commercial license even if wanted — same pattern as F5-TTS. `myshell-ai/OpenVoice` — genuinely clean (MIT, no restriction), but redundant once OmniVoice and GPT-SoVITS are both in; not adding a third overlapping cloning engine. `calesthio/OpenMontage` — AGPL-3.0, a much larger, differently-scoped whole video-production tool with no TTS engine of its own (it just calls out to Piper/ElevenLabs/others). `unslothai/unsloth` — not a TTS or dubbing tool at all; it's for fine-tuning LLMs, which doesn't fit how Baihe uses models (existing pretrained/API models, no local training). `jianchang512/pyvideotrans` — GPL-3.0, already the acknowledged inspiration for this app generally; nothing new beyond what Step 7 already took from it. `IndexTTS2` — has real, finer-grained emotion control (an 8-value emotion vector, fully independent of the cloned voice's identity — technically the better fit than Chatterbox's single exaggeration dial) but its license is bilibili's own custom Model Use License Agreement, not a standard one; flagging rather than adopting until that license is read carefully, the same caution already applied to koharu's Manga109-trained detector.

**Exit:**
- A mocked test shows OmniVoice, GPT-SoVITS, Chatterbox and TADA selection all follow the same pattern as the existing cloned-voice/ElevenLabs paths.
- A test shows a line's detected emotion and intensity maps to a Chatterbox `exaggeration` value within its recommended range, and a line with no detected emotion falls back to a sensible neutral default.
- A test shows a character with no reference audio can still get a described (voice-design) voice, distinct per description.
- A test shows narration generation runs a warm-up batch then parallel batches, while still skipping any clip whose file already exists, and that GPT-SoVITS forces single-threaded.
- A test shows the audio-generation text unit for narration mode can be longer than the exported subtitle cue for the same passage.
- A test shows the M4B export has chapter markers matching the narration's paragraph/chapter breaks.
- Manual check (real weights, on your own PC): generate a short story's narration with OmniVoice cloning and with GPT-SoVITS, compare naturalness against the existing edge-tts default; try voice design with no reference audio; time a longer narration run before/after parallelization; open the M4B export in a real audiobook/podcast player and confirm chapters show up.

### Step 11c — Dub timing: clamped time-stretch fallback *(idea from `mazzasaverio/youtube-auto-dub`, video/audio dubbing only — not novel narration, which has no external timing to hit)*
Found while doing a full README+code pass on repos surfaced by a `github.com/topics/*` discovery search, not from a link the user supplied. Baihe's only current defence against a translated/dubbed line not fitting its original time window is `dub.rewrite_for_pacing_llm` — asking the LLM to write a shorter line. That's a good first line of defence (keeps the words natural, no audio artifacts) but has no fallback for a line that's still too long or too short after rewriting: right now that line just runs over or under with nothing correcting it further. **Re-verify against the current `dub.py` before implementing** — confirm no post-synthesis timing step already exists that this would duplicate.

1. **Add a clamped, pitch-preserving time-stretch as the fallback *after* `rewrite_for_pacing_llm`, not a replacement for it.** `youtube-auto-dub`'s `stages/synchronize.py` (MIT) was read directly: it wraps FFmpeg's `atempo` filter, computes a per-segment stretch factor as `clip_duration / window_duration`, and clamps it — capped at a `max_speedup` (its default: 1.4×, to avoid "chipmunk audio") and a `max_slowdown` floor (its default: 0.85×), skipping the stretch entirely when the factor is within 1e-3 of 1.0. Past the cap, it explicitly lets the line overflow its window rather than degrade audio quality further — the same "clean over compressed" judgment call Baihe already makes elsewhere. Baihe already shells out to ffmpeg (`video_export.py`), so this is a small addition, not a new dependency.
2. **Apply it only to video/audio dubbing (the fixed-timeline case), not novel narration** — Step 11b already establishes narration has no external timing to hit, so this fallback has nothing to correct there.
3. Keep the clamp values configurable (not hardcoded to `youtube-auto-dub`'s own defaults) — a first pass can start with the same 1.4×/0.85× figures and adjust from real dubbed output if they sound wrong.

**Checked and not added from the same batch of README+code passes:**
- `rockbenben/subtitle-translator` (MIT) — real, working batching/caching (per-file translation, IndexedDB caching, timing kept untouched by only ever sending dialogue text to the LLM) — but this is architecturally the same separation Baihe's own `translate_engines` already relies on, and it's a Next.js web app, not portable Python. Nothing incremental to adopt.
- `meangrinch/MangaTranslator` (Apache-2.0) — real pipeline (YOLOv8m/RT-DETR-v2/SAM detection, Manga OCR/PaddleOCR-VL, FLUX-based diffusion inpainting). Its inpainting is a genuinely more advanced technique than the LaMa-manga model Step 11 already chose, but: FLUX.1 Kontext [dev] is non-commercial-only (another license caveat to track, on top of Manga109/Llama-license ones already in this doc), FLUX.2 Klein is Apache-2.0 and clean, and either way this adds a heavy diffusion-model dependency (`diffusers`, `sdnq`, `spandrel`, a multi-GB checkpoint) for a quality gain over the already-good, much lighter LaMa-manga MIT checkpoint that hasn't been confirmed to matter in practice. Given the standing "fewer options" preference and that Step 11's inpainting is explicitly optional/opt-in already, not adding a second, heavier inpainting backend without a concrete complaint about LaMa-manga's output quality first.
- `nganlinh4/oneclick-subtitles-generator` (MIT) — broad overlap with Baihe's whole scope (transcribe → translate → voice-clone narration → styled burn-in via a native Rust renderer), but Baihe's own `video_export.burn_subtitles` already accepts the same font/color/outline styling (Step 6b just needs to wire the UI to it) — nothing new to adopt.
- `enrell/animesubs` — GPL-3.0, 8 stars. Its one interesting idea — auto-skip OP/ED/karaoke/signs/music lines before translation — is a genuinely useful feature idea for a future step, but the repo itself is too small and copyleft to be a code source; noting the idea, not adopting any code.
- `Abrechen2/sublarr` — GPL-3.0, a self-hosted *arr-ecosystem subtitle manager (library-wide automation across Sonarr/Radarr). Different product shape from Baihe's per-drama workspace; not a fit regardless of license.

**Exit:**
- A test shows a line whose synthesized clip exceeds its window by a modest amount gets sped up by a clamped factor, and one within 1e-3 of 1.0 is left untouched.
- A test shows a line needing more than the speed-up cap is capped and allowed to overflow, not force-crushed.
- A test confirms `rewrite_for_pacing_llm` still runs first, and the time-stretch only applies to whatever gap is left after that rewrite.

### Step 12 — GUI polish and Streamlit performance
Found by asking directly "anything else to improve — models, performance, GUI appearance?" rather than from a specific gap already on file. Independent, low-risk items — no shared code between them, grouped to keep the step count down, same pattern as Step 9b.

1. **A real visual theme, not Streamlit's default.** Checked directly: nothing in this project currently touches `.streamlit/config.toml`'s theming, so the app runs on Streamlit's stock red-accent look. Streamlit's own theming supports a light/dark pair the user can switch between in settings (`[theme]` / `[theme.dark]` in `config.toml`) — add a real palette for both (not just default-inherit), consistent with `awesome-streamlit-themes`' general pattern of a considered palette rather than the stock one (referenced for the *idea* of a deliberate palette, not its code — this is a config file, not a dependency). Purely additive, no functional risk.
2. **`@st.fragment` for the app's frequently-updated small widgets, so a keystroke doesn't rerun all of `workspace_tab.py`.** `tabs/workspace_tab.py` is 2,800+ lines (per this doc's own file map) and, on stock Streamlit, any widget interaction anywhere on the page reruns the whole script top to bottom. `@st.fragment` (stable since Streamlit 1.30) scopes a rerun to just the fragment's own block. **Re-verify against the current file before implementing** — confirm which specific widgets actually cause a visible full-page rerun lag before wrapping them; don't wrap speculatively. The most likely real candidates, based on what's already in this roadmap: Step 6b's live style preview (re-renders on every font/color/size change), Step 9b's ETA/progress display (updates on a timer, not user input, but currently forces the whole page to redraw with it), and Step 9b's bulk-series combined status line.
3. **Confirm Streamlit version headroom.** `constraints.txt` caps at `streamlit<2` (Step 1c), so Streamlit 1.63.0 (confirmed real, released September 1, 2026 — a 20% caching-layer speedup and a searchable multi-select widget for large option lists, useful for a drama with many characters/glossary terms) should already be installable without a constraints change. Just confirm the actual installed version isn't pinned lower somewhere else, and bump the dev environment if it is — this is a version check, not new code.
4. **Out of scope:** replacing Streamlit's own manual module-level model caches (`core._whisper_model_cache` etc.) with `st.cache_resource`. They already work and Baihe is single-process/local, so `st.cache_resource`'s main advantage (avoiding the multiworker-deployment cache-duplication problem) doesn't apply here — not worth the churn of touching working cache logic for no functional gain.
5. **Consistent collapsible-section pattern across the whole app**, prompted by the user directly asking for consistent collapsible tabs. `tabs/workspace_tab.py` was read directly (not guessed at): it already has a real numbered-step structure (steps 1–10, each `st.expander("N. emoji label", expanded=False)`), but applied inconsistently — **Step 1 ("Choose a drama") uses `st.subheader` instead of an expander** like every other step (line 352 vs. the expander pattern used for steps 2–10), and **Step 10 ("Export full subtitled episode") is nested *inside* Step 9's expander** rather than being its sibling (confirmed by indentation), so the numbering implies 10 parallel steps when there are really 9, one with a sub-step. Fix: make Step 1 a consistently-styled expander like the rest, and either promote Step 10 to a true sibling or fold it in explicitly as Step 9's own sub-item so the numbering matches the real hierarchy. Extend the same expander idiom to the other 8 tabs (`library_tab.py`, `reader_tab.py`, `scanlate_tab.py`, `navigator_tab.py`, `discover_tab.py`, `live_tab.py`, `diagnostics_tab.py`, `settings_tab.py`) wherever a tab currently mixes plain sections with expanders, so every tab uses one collapsible-section pattern rather than each inventing its own layout. **Only `workspace_tab.py` was read to this depth this pass — re-verify each of the other 8 tabs' actual current structure before changing them.**
6. **Remove redundant text and duplicated controls, and audit for more of the same pattern**, prompted by the user directly asking for no redundant text/buttons/areas. Two concrete duplicates confirmed by reading the actual file: the exact same caption — "Running in the background -- safe to switch tabs, use other dramas, or close the browser tab. Come back and this will show current progress." — and the exact same "🔄 Refresh progress" button are copy-pasted twice (~line 1629 for the transcription job, ~line 1844 for translation) instead of shared. Separately, at least six nested `st.expander("Details")` calls (~lines 1762, 1866, 2248, 2310, 2380, 2429, 2593) all share the single bare label "Details" for completely different content (line-coverage findings, consistency-check findings, adaptive-style details, translation-version comparisons, and more) — meaningless once more than one exists on a page, since a collapsed section's label is the only information available before opening it (confirmed against 2026 accordion-UI guidance: labels must be specific, not generic, for exactly this reason). Fix: factor the background-job-status block (progress bar + caption + refresh button) into one shared helper function used by every job type — this also satisfies `CLAUDE.md`'s existing dead/redundant-code cleanup rule — and rename every generic "Details" expander to name what's actually inside. While doing this, audit the other 8 tabs for the same two patterns (copy-pasted status/caption blocks, generic single-word expander labels); not confirmed there yet, but worth checking given how consistently they repeated within just this one file.
7. **A real typographic scale, defined once and applied everywhere**, prompted by the user directly asking for consistent font size and wording. Checked current (2026) guidance directly: 16px is the standard web body-text size, with 17–18px cited as more comfortable specifically for reading-heavy screens — relevant here since Baihe's Read & Watch tab and the Workspace review section (Step 5 of the numbered flow) are both reading-heavy; a ~1.25 modular scale for heading-size steps is the common practical convention so size differences look deliberate rather than arbitrary. Define this once (alongside item 1's theme work): one size/weight for a top-level step label, one for a sub-section label, one for body/caption text — and apply it consistently instead of the current mix of `st.subheader`/bold-markdown/`st.caption` used interchangeably in `workspace_tab.py` for what's structurally the same kind of element. Keep caption wording tone consistent too (length, whether it ends with a period, "--" vs. an em dash) — currently inconsistent across the file. **Re-verify against each tab's actual current usage before rewriting its headers** — only `workspace_tab.py` was read to this depth.
8. **Live subtitles in Reader & Watch's video player, for in-UI QC**, at the user's request ("can the video viewer show subs playing in real time so I can QC easier"). Confirmed directly: `tabs/reader_tab.py`'s "▶️ Watch / listen" block already calls `st.video(p)` on the drama's original video with no `subtitles=` argument, and Streamlit's own `st.video` API genuinely supports one — it takes a string/bytes/`Path`/`io.BytesIO` of raw SRT or VTT content directly (not just a file path), or a `dict` of `{label: content}` for multiple switchable tracks shown via the browser's native CC menu (confirmed against Streamlit's own docs, not assumed). Baihe already has everything needed to build this with no new dependency: `core.lines_to_srt(lines, field=...)` and `core.lines_to_bilingual_srt(lines, ...)` both exist and already produce valid SRT strings (used today for subtitle export). Wire the video call to `st.video(p, subtitles={"Source": lines_to_srt(rlines, rlang), "English": lines_to_srt(rlines, "en"), "Bilingual": lines_to_bilingual_srt(rlines)})`, built from the page's current in-session `rlines` (not a stale export file) so edits made in Workspace show up on next Reader-tab load without a separate export step. **Only offer tracks for languages that actually have content** (skip a language whose lines are all empty, same spirit as Step 1d's "don't offer what can't work" pattern) — check this directly against the drama's current translation state, don't assume both are filled. Audio-only dramas can't use this (`st.audio` has no subtitles parameter) — this only applies when `media_path[0] == "video"`.

**Exit:**
- Manual check: the app's dark and light themes both look deliberately designed, not the Streamlit default, and switching between them in Settings works.
- A test (or manual timing check, since Streamlit rerun speed isn't easily unit-tested) shows the live style preview and ETA display update without visibly re-rendering unrelated parts of the Workspace tab.
- Manual check: confirm the installed Streamlit version and note it in the §7 registry.
- A test or manual check confirms every top-level section in `workspace_tab.py` (and the other tabs, once audited) uses the same expander pattern, including the former Step 1; Step 10 either has its own top-level slot or is clearly Step 9's sub-item, not an orphaned nested "10".
- A test shows the background-job-status block is rendered by one shared function for both the transcription and translation jobs.
- A static-analysis test (same pattern as `tests/test_static_analysis.py`'s existing checks) confirms no two `st.expander` calls share the bare label "Details" in the same file.
- Manual check: open each tab and confirm section labels are specific and consistent in tone, collapsed-by-default sections behave the same way everywhere, and body/caption text reads at a consistent, comfortable size throughout — not just in Workspace.
- A test shows `st.video`'s `subtitles` argument is only built from languages that actually have non-empty lines, and is omitted entirely for an audio-only drama.
- Manual check: open a video drama in Read & Watch, confirm the CC menu offers Source/English/Bilingual tracks, and that captions are actually in sync with playback for QC purposes.

### Step 13 — UI foundation: shared components and a unified project-state model
Prompted by the user's request for a full information-architecture audit and redesign, not just visual polish ("Make the UI consistent with collapsible tabs... I want you to rethink the application's information architecture and workflow while preserving all existing functionality"). This is Phase 1–2 of that request: the audit itself, and the scaffolding the rest of the redesign (Steps 14–19) builds on — no visible behavior change in this step.

**Audit, confirmed via direct `git show origin/baihe-subtitler:...` reads, not assumed:**
- `app.py` renders 8 flat top-level tabs via one `st.tabs([...])` call, each its own file: Library (302 lines), Workspace (2,899), Read & Watch/`reader_tab.py` (445), Scanlate (319), Navigator (76), Discover (267), Live (115), Diagnostics (363). `settings_tab.py` (184) renders in the sidebar.
- **Organized by feature, not by workflow.** Navigator (a site-navigation helper for finding a title on official platforms) and Discover (search/import titles) are both "find content to import" but are separate top-level tabs. Reader stacks 7 largely-unrelated sections vertically in one page: Watch/listen player → series glossary → "Story tools" → "Universe wiki" → "Line tools" (per-line fix/re-transcribe) → "My notes" → "Vocabulary export" → "Ask about this drama" (Q&A).
- **Duplicated "current project" concept, confirmed:** `st.session_state.active_drama_id` is the app-wide "current drama" pointer, but `reader_tab.py` keeps its own separate `reader_drama_pick` selectbox with dedicated reconciliation code (`reader_resume_pending`/`reader_resume_banner`) just to stay in sync when the user arrives via a "Resume" click from Library. Two sources of truth for the same thing.
- **Duplicated information display, confirmed:** API keys are shown/editable in `settings_tab.py`'s "API keys & endpoints" expander, and shown *again*, read-only, in `diagnostics_tab.py`'s "API keys (from Settings)" section.
- **Session state is moderately, not wildly, fragmented** — confirmed ~34 distinct top-level `st.session_state.*` keys app-wide via a full-app grep, not hundreds. The "feels disconnected" complaint is mostly an information-architecture/layout problem, not raw state sprawl — though the `active_drama_id`/`reader_drama_pick` split above is a real state-model instance of it.
- **Diagnostics is a kitchen sink:** setup checks, running jobs, a log viewer, an accuracy-benchmark tool, a "danger zone" (destructive full-library reset), core requirements, project files, API keys (duplicate, see above), and dependency tiers — 9 unrelated concerns in one flat page.
- **Settings already has the right shape to build on:** grouped labeled expanders (Reading experience / Offline / OCR / Performance / Defaults / API keys) — close to a common/advanced split already, just needs the settings currently scattered into other tabs consolidated in, and a real common/advanced split applied.
- **No shared "where am I in the pipeline" indicator anywhere**, and no persistent status while navigating between tabs.
- **On UI inspiration from previously-reviewed GitHub repos** (the user asked directly): checked what's actually reusable — almost all of the repos surveyed this project are Gradio, React/Next.js, Tauri/Rust, or CLI-first (MangaTranslator/Gradio, oneclick-subtitles-generator/Rust+React, animesubs/Tauri, sublarr/React) — none share Streamlit's component model, so there's no code to port. What is directly applicable and Streamlit-native: `awesome-streamlit-themes`/`Paldom/streamlit-custom-style` for the visual system (already Step 12 item 1's job), and Streamlit's own `st.tabs`/`st.expander`/`st.popover`/`st.dialog`/`st.status` primitives, which this redesign is built from. The actual inspiration here is architectural (workflow-stage IA, project-centric state, one primary action per screen), not lifted code, since no reviewed repo shares the stack.

1. **New `ui/` component layer** (mirrors the user's own suggested shape, adapted to what actually exists):
   - `ui/project_header.py` — a compact, always-visible header: project name, pipeline-progress indicator, settings shortcut — reading from the unified project-state model (item 2 below) instead of ad-hoc `active_drama_id` lookups scattered per tab.
   - `ui/workflow.py` — the pipeline-stage stepper/status indicator (✓ done / ● current / ○ not started per stage), reusable in Workspace's header and in Home/Library's per-project list.
   - `ui/status.py` — the rich background-job status block. This *is* the shared helper Step 12 item 6 already calls for around the copy-pasted transcription/translation status code — build it once here, Step 12 and every later step reuse it rather than it being built twice.
   - Existing business logic (`core.py`, `translate_engines.py`, `dub.py`, `scanlate.py`, etc.) is untouched — this layer only changes how the UI calls into them.
2. **Unified project-state model.** Introduce one coherent `st.session_state.project` holder carrying `drama_id`, `current_stage`, per-stage status, selected segment/line, active background job, recent errors/warnings — replacing the `active_drama_id`/`reader_drama_pick` split with one source of truth every tab reads from. Narrowly-scoped keys (reader display prefs, settings keys, widget-local keys) stay exactly as they are — this only consolidates *project-identity* state, not every session-state key in the app.
3. **Re-verify this audit against the current files before starting** — Steps 1e through 12 may have already changed `workspace_tab.py` and others by the time this step is picked up; confirm the line numbers and structure above still hold, and note what's changed if not.

**Exit:**
- The app runs identically to before this step — no tab's visible behavior changes yet.
- A test shows `ui/project_header.py`, `ui/workflow.py`, and `ui/status.py` render correctly in isolation against a fake project-state fixture.
- A test shows the new `st.session_state.project` model round-trips `drama_id`/`current_stage` correctly and that reading/writing it doesn't touch any of the narrowly-scoped keys listed above.

### Step 14 — Workspace shell rebuild: stage tabs instead of a 10-expander scroll
The single biggest change in this redesign — replaces Workspace's 2,899-line, 10-`st.expander` vertical scroll (confirmed in Step 12/13's audits: Step 1 breaks the expander pattern, Step 10 is mis-nested inside Step 9) with a stage-based workspace. **Needs Step 13's `ui/` layer and project-state model.** Re-verify against the current file before starting — Step 12 may have already changed the expander numbering/nesting this step assumed at audit time.

1. **Stage navigation via `st.tabs`** inside Workspace: Source / Transcript / Diarize / Translate / Review / Dub / Export, each tab holding only that stage's controls — mapping directly onto the current numbered steps (1 Choose-drama+2 Content-source → Source; 3 Reference-novel+recognition-settings folds into Source or Transcript as appropriate; 4 Diarization → Diarize; 5 Translation → Translate; 6 Characters/voices → Translate or its own sub-tab; 7 Review/edit → Review; 8 AI dub → Dub; 9+10 Export → Export, fixing the mis-nesting so Export's video and subtitle exports are true siblings, not one nested inside the other).
2. **Project header always visible** (Step 13's `ui/project_header.py`): project name, pipeline-progress stepper, settings shortcut — not buried in the scroll.
3. **Advanced/rarely-touched options move into a `st.popover` or nested `st.expander`** within their stage tab, not permanently on-screen — e.g. Whisper's chunking parameters, Ollama's `num_ctx` override, VRAM settings.
4. **One clear primary action per stage tab** (Transcribe / Translate / Export, etc.); secondary actions visually recede; destructive actions (full reset, overwriting manual corrections) stay visually distinct, reusing the confirmation-guardrail pattern already established (Step 10's uninstaller, Step 4's speaker-overwrite guard) — don't design a new pattern for this.
5. **Background-job status uses Step 13's `ui/status.py`** everywhere a job runs in Workspace, and stays visible via a small persistent indicator even if the user switches to a different stage tab or leaves Workspace entirely.

**Exit:**
- A full click-through test (or manual check) confirms every one of Workspace's current 10 sections' functionality is reachable and working under the new stage tabs — nothing lost, per the user's explicit "don't destroy functionality" requirement.
- A test shows the project header's pipeline-progress stepper reflects real stage completion state for a drama at various points in its pipeline.
- Manual check: open a drama with lines already translated partway through and confirm the stepper correctly shows Transcribe/Diarize done, Translate in progress, Review/Dub/Export not started.

### Step 15 — Reader tab declutter: primary reading view + a secondary Story panel
`reader_tab.py`'s 7 stacked sections (confirmed in Step 13's audit) get split: the actual reading/watching experience (the player, with Step 12 item 8's live captions, and the paginated bilingual reader) stays primary and immediate; "Story tools," "Universe wiki," and "Ask about this drama" (Q&A) move into a secondary "Story" panel reachable from Read & Watch rather than always stacked below the reader. "Line tools" (per-line fix/re-transcribe) moves into Workspace's Review stage (Step 14) — it's the same job as Workspace's own line-review work, currently split across two tabs for no functional reason. "My notes" and "Vocabulary export" stay attached to the reading view itself, since they're genuinely reading-adjacent.

**Exit:**
- A test or manual check confirms every current Reader feature is still reachable somewhere in the new layout — nothing merged away, only relocated.
- Manual check: open Read & Watch and confirm the primary reading experience is visible without scrolling past Story-tools/wiki/Q&A content first.

### Step 16 — Settings consolidation: Common vs. Advanced
Builds on `settings_tab.py`'s existing grouped-expander structure (Reading experience / Offline / OCR / Performance / Defaults / API keys — already close to what's needed) rather than starting over. Absorb settings currently only reachable from inside other tabs where that makes sense — e.g. OCR backend options currently only in Scanlate move to Settings' OCR section, with Scanlate keeping only a per-page override, not the only place to set a default. Split into two tiers: **Common** (source/target language, model choice, translation quality, speaker detection on/off, output format — the things changed often) and **Advanced** (context window, temperature, batch size, VRAM settings, prompt configuration, alignment settings, debug/experimental options — changed rarely, shouldn't dominate the normal settings view). Remove Diagnostics' duplicate read-only API-key display (confirmed in Step 13's audit) — Settings stays the one place to see/edit them.

**Exit:**
- A test or manual check confirms every setting that existed before this step still has a working home somewhere in Common or Advanced — nothing dropped.
- Manual check: confirm Diagnostics no longer shows API keys, and that Settings' single copy still works correctly from wherever it moved.

### Step 17 — Discover/Navigator merge
Fold Navigator's "known official platforms" reference material and translated-page-label steps into Discover's existing search/import flow as a sub-section, since both tabs are "find and bring in a title" (confirmed in Step 13's audit — this is the clearest single case of the same job split across two tabs). Removes one top-level tab (8 → 7) rather than just reorganizing within it.

**Exit:**
- A test or manual check confirms every Navigator feature is still reachable from within Discover.
- Manual check: a user who previously used Navigator to find a platform link can complete the same task starting from Discover.

### Step 18 — Diagnostics narrowing
Separate Diagnostics' 9 currently-flat concerns (confirmed in Step 13's audit) into clearly distinct groups: routine health checks (setup checks, running jobs, log, dependency tiers) stay as the default view; the accuracy-benchmark tool and the "danger zone" (destructive full-library reset) move behind their own clearly-separated sub-section so a routine "is Ollama reachable" check doesn't sit next to a destructive reset button in the same scroll.

**Exit:**
- A test or manual check confirms all current Diagnostics functionality is intact, just regrouped — nothing dropped.
- Manual check: opening Diagnostics for a routine health check no longer shows the danger-zone reset button in the same view without an extra step.

### Step 19 — Full click-through UX test
The user's own requested final check (their spec's §21), run once Steps 13–18 have landed: deliberately work through the 12 workflows they listed — import a video, configure transcription, run transcription, review transcript, run speaker diarization, configure translation, translate, review/edit translation, export subtitles, return to the project later, resume an incomplete project, handle a failed processing task — asking for each: where am I, what's the next action, is the primary action obvious, is anything unnecessary on screen, do I have to scroll unnecessarily, are related controls together, can I get back easily, is the current project obvious. Fix anything that still fails one of these, rather than treating Steps 13–18 as automatically sufficient.

**Exit:**
- A written pass/fail note per workflow (not just "looks fine") against the questions above, with any failures fixed before this step closes.
- Manual check: the user runs the same 12 workflows themselves and confirms the experience matches what Step 19's own pass found.

### Step 20 — UX polish: keyboard shortcuts, toast feedback, transcript search
Found by asking directly "anything else to improve UX?", separate from the information-architecture work in Steps 13–19 — these are smaller, additive interaction improvements, not IA changes, so they can land as their own step rather than folding into an already-large step.

1. **Native keyboard shortcuts for Review & edit's most-repeated actions.** Confirmed directly: Streamlit 1.52+ supports a native `shortcut=` parameter on `st.button` (e.g. `st.button("Save", shortcut="ctrl+s")`) — no third-party component needed, and Baihe should already be well past 1.52 (Step 12 confirmed 1.63.0 headroom). Checked the app for any existing use — none (`grep`'d every file for `shortcut=`, zero hits). Add shortcuts to the handful of actions a reviewer repeats dozens of times per session: save the current line's edit, jump to the next/previous flagged line (reusing the existing `_jump_to_line_button` helper's logic), play/pause the current line's audio clip. **Re-verify the installed Streamlit version supports `shortcut=` before implementing** — it's a recent addition, don't assume without checking against the actual installed version.
2. **`st.toast` for transient confirmations, instead of (or alongside) static inline messages.** Confirmed directly: `st.toast` is a real, stable Streamlit API, and its 2026 update fixed it staying visible across an immediately-following `st.rerun()` — relevant here because several of Baihe's own actions call `st.rerun()` right after showing a message. Checked `workspace_tab.py` for current usage: zero `st.toast` calls anywhere in the app, but 48 `st.success`/`st.balloons` calls in `workspace_tab.py` alone — most of these are small "saved"/"done" confirmations that would read better as a transient toast than a message that sits in the page until the next rerun clears it. Not a blanket replace-everything pass — keep `st.success`/`st.error` for anything the user needs to actually read and act on (a real warning, an error with next steps); use `st.toast` only for pure confirmations ("Line saved," "Export complete").
3. **Full-text search across a drama's transcript.** Confirmed directly: today, jumping to a specific line only works from a flagged-item list (`_jump_to_line_button`, used from pacing/consistency/coverage check results) — there's no way to type a word or phrase and jump to where it appears. For a long drama, finding "that one line with X" currently means paging through the Review table manually. Add a search box in Review & edit that filters/jumps using the same underlying line list `_jump_to_line_button` already works against, reusing its page-jump logic rather than building a second navigation mechanism.
4. **Checked and not pursued: a dynamic browser tab title reflecting background-job progress** (e.g. "42% — Translating..." in the tab bar, so the user can tell status while alt-tabbed away). Checked directly: this is a real, long-standing, unresolved Streamlit limitation (multiple open GitHub issues going back years, no clean native mechanism — `st.set_page_config`'s title can only be set once, early in the script). Available workarounds are JS-injection hacks with known reliability problems. Not worth building a fragile workaround for a nice-to-have when Step 14's persistent in-app status indicator (Step 13's `ui/status.py`) already covers the "can I tell what's running" need while the user is actually in the app.

**Exit:**
- Manual check: the shortcut keys work for save/next-flagged/prev-flagged/play-pause in Review & edit, and don't fire while typing in a text field (native `shortcut=` behavior, per Streamlit's own docs — re-verify this is actually true of the installed version before relying on it).
- A test shows a pure-confirmation action (e.g. saving a line edit) shows a toast, while a real warning/error still shows a message the user has to see and act on, not just a toast.
- A test shows searching for a word/phrase in Review & edit's search box jumps to the correct line, using the same page-jump logic as the existing flagged-item jump buttons.
- Manual check: confirm the transcript search actually finds a term known to exist only in one specific line of a real drama, and lands on the right page.

---

## 3. Deferred: revisit only if a real need appears

| Milestone | Why it's deferred | Revisit when |
|---|---|---|
| **R4** — standalone VAD, Qwen3-ASR independent of Whisper, word timestamps, resumable jobs | The biggest and riskiest change. Whisper already works, and nobody has shown Qwen3-ASR is better on this content. *(Checked directly, prompted by the user asking whether current model choices are still current: [`QwenLM/Qwen3-ASR`](https://github.com/QwenLM/Qwen3-ASR), Apache-2.0, released January 29, 2026 with a June 26, 2026 update adding native Transformers support — both after this milestone was originally written. The release itself doesn't establish it's better *on Baihe's content specifically*, so this doesn't change the deferral, but a future revisit should start from this release, not an older one.)* | Whisper transcripts are clearly poor, or long jobs keep failing partway through. |
| **R7** — full-pipeline benchmark | A developer tool whose main use is deciding R4. | R4 is being reconsidered. |
| **Live capture for Bilibili/TikTok Live**, beyond the download-only support Step 9b adds | `live_translate.py`'s live-stream chunking is YouTube (and Twitch) specific; each platform's live/HLS quirks differ enough that this is real, separate work, and the app's actual focus is VOD audio dramas, not live streaming. *(Surveyed while comparing against OpenCreator/302_video_translation/Synthalingua — none of them make this look like a small add either. Also checked, on request, whether any project proves the underlying yt-dlp-live + VAD-chunked-Whisper approach actually works reliably: [`ionic-bond/stream-translator-gpt`](https://github.com/ionic-bond/stream-translator-gpt), MIT, 214★/28 forks, actively forked/maintained — Silero-VAD dynamic-threshold slicing, yt-dlp for live-stream URL extraction, own README claims "stable everyday use." [`antor44/livestream_video`](https://github.com/antor44/livestream_video), GPL-3.0, 18★ — a circular-buffer timeshift approach with whisper.cpp, an alternative pattern to VAD-chunking. Neither confirms Baihe's own `live_translate.py` works today — that's untested, separately — but both are real evidence the general technique is sound and reusable reference points if this milestone is picked up.)* | Live capture from one of these platforms is actually wanted, not just downloading an already-finished VOD (which Step 9b's cookie support already covers). |
| **R1-full** — a general artifact and versioning system | R1-lite covers the need that matters (not losing the original). | Several stages need a history of versions. |
| **R2 windowing** — diarizing long audio in windows | Only matters for streams several hours long. | Long VODs become a regular input. |
| **R3-full** — a single-slot model manager | Only matters if the GPU runs out of memory. | Out-of-memory crashes occur. |
| **M8+** — FastAPI + React, a persistent job queue, voiceprint suggestions | A large migration with no current pain driving it. | Streamlit becomes the bottleneck. |

---

## 4. Working agreement between the two chats

- **Planning chat:** branch `claude/baihe-subtitle-planning-95qyvq`, docs only. Roadmap changes go here.
- **Implementing chat — two different flows depending on where the roadmap is:**
  - **Steps 1e through 10 (autonomous mode, starting 2026-09-24 at the user's request — "most of the checks have been good, let the AI go through the steps without additional checks"):** build the step on its own branch off the latest `baihe-subtitler`, run the full suite, open a pull request into `baihe-subtitler` **and merge it yourself**, then start the next step off the updated branch — no stop for the planning chat's diff review and no stop for the user's merge go-ahead in between steps. Still push a short plain-English summary of what changed with each step, for the record, but don't wait for a reply before continuing. **No per-step model-switch stop either** — the whole remaining run (this range and Step 11 onward) is on Opus throughout, at the user's explicit request (2026-09-24: "can I let the whole process run with opus"), so there's nothing to confirm per step; see the table below for why those particular steps would otherwise have needed it. If a step's own exit conditions can't be met, or something looks genuinely wrong (not just "the planning chat would nitpick this"), stop and say so rather than merging around it.
  - **Steps 11 onward (back to the original gated flow):** once the roadmap's current list (through Step 10) is done, later additions go back to review-gated: build on its own branch, push, report, then **stop**; the user asks the planning chat to "check Step X"; once approved, the user says "create a PR for this step"; open the PR but **don't merge it yourself**; the user merges on GitHub; start the next step only after the previous one is merged. The user's own framing for this: once the current list is finished, further changes are "smaller scale," worth going back to a closer look before they land. **This explicitly includes Steps 13–19 (the UI/UX redesign)** — confirmed with the user that these start only after Steps 1e–12 are fully done, not interleaved with the currently-running autonomous batch, given the redesign touches every tab and the shared session-state model.
- **Model recommendation per step (now informational only — see the note above).** The whole remaining run is on Opus at the user's request, so this table no longer gates anything; it documents *why* these particular steps would have been worth the extra care if the run were on a cheaper model, for anyone revisiting that cost/quality tradeoff later. Originally: everything not listed here would be fine on Sonnet — these are the steps with either a schema/data migration touching every existing project, correctness that depends on getting an edge case right rather than following a clear spec, or several interacting moving parts in one step:

  | Step | Why it needs the extra care |
  |---|---|
  | 2 — R0 permanent line IDs | A schema migration that runs against every existing project's real data, plus the new backup/resumability guardrail — getting this wrong is hard to walk back cleanly. |
  | 6c — Meaning-based re-segmentation | Changes line boundaries directly, and the guardrail added this session (detect exactly which lines' boundaries changed, clear only those, warn first) is the kind of edge-case-heavy logic that's easy to get almost right. |
  | 9 — Cost controls & bulk discounts | Batch results can come back hours later and must be matched by id with a source-text hash check, with correct handling for a line that was merged, deleted or edited in between — several ways to subtly misassign a result if any check is skipped. |
  | 11b — Novel narration TTS quality | Has grown into the step with the most interacting parts: four TTS backends with different capabilities (only one does voice design, only one does emotion), parallelized generation with a per-backend single-threaded exception, and the emotion→delivery mapping — a lot of places for one backend's quirk to leak into another's behaviour. |

  Everything else — including Step 11's model-swap fix, Step 10's uninstaller — is normal-risk, well-specified work; Sonnet has already handled comparable steps (1, 1b, 1c) correctly.
- **Status** (last checked against the real branch state on 2026-09-24). For Steps 1e–10 in autonomous mode, there's no per-step "check Step X" request to trigger a table update — the planning chat should re-sync this table by checking real git state (§5 rule 1) whenever asked, or on its own initiative when picking the thread back up, rather than waiting to be told a step finished. **Manual check** tracks the user's own real-model check from §2's table, separately from merge status — a step can be merged with its manual check still pending, and that's expected to lag further behind in autonomous mode since steps land back-to-back. It moves to ✅ only when the user says "manual check passed for Step X"; the planning chat doesn't infer it.

  | Step | Branch | Merged | Manual check |
  |---|---|---|---|
  | 1 — R5 translation fixes | `claude/r5-translation-fixes` (deleted post-merge) | ✅ Merged | ⏳ Pending |
  | 1b — Safety fixes | `step-1b-safety-fixes` (deleted post-merge) | ✅ Merged (PR #2) | ⏳ Pending |
  | 1c-pre — AI setup | `step-1c-pre-ai-setup` (deleted after merge) | ✅ Merged | ⏳ Pending |
  | 1c — Dependency fixes | `step-1c-dependency-fixes` (deleted after merge) | ✅ Merged | ⏳ Pending |
  | 1d — Free testing engines | `step-1d-free-testing-engines` | ✅ Reviewed & approved, PR pending | ⏳ Pending |
  | 1e — Character pronouns | — | Not started | — |
  | 2 — R0 permanent line IDs | — | Not started | — |
  | 3 — R1-lite original transcript | — | Not started | — |
  | 4 — R2 speaker detection | — | Not started | — |
  | 5 — R3-lite local-model defaults | — | Not started | — |
  | 6 — Transcription quality | — | Not started | — |
  | 6b — Export formats (VTT/ASS) | — | Not started | — |
  | 6c — Meaning-based re-segmentation | — | Not started | — |
  | 6d — Vertical/shorts export | — | Not started | — |
  | 7 — Reflect translation mode | — | Not started | — |
  | 7b — Content-summary glossary extraction | — | Not started | — |
  | 8 — Recurring-voice suggestions | — | Not started | — |
  | 9 — Cost controls & bulk discounts | — | Not started | — |
  | 9b — Job ETAs, model disk management, bulk series translate | — | Not started | — |
  | 9c — Drama/project presets | — | Not started | — |
  | 10 — Windows launcher | — | Not started | — |
  | 11 — Scanlate ML detector/inpainting/OCR routing | — | Not started | — |
  | 11b — Novel narration TTS quality | — | Not started | — |
  | 11c — Dub timing: clamped time-stretch fallback | — | Not started | — |
  | 12 — GUI polish and Streamlit performance | — | Not started | — |
  | 13 — UI foundation: components & project state | — | Not started | — |
  | 14 — Workspace shell rebuild | — | Not started | — |
  | 15 — Reader tab declutter | — | Not started | — |
  | 16 — Settings consolidation | — | Not started | — |
  | 17 — Discover/Navigator merge | — | Not started | — |
  | 18 — Diagnostics narrowing | — | Not started | — |
  | 19 — Full click-through UX test | — | Not started | — |
  | 20 — UX polish: shortcuts, toasts, transcript search | — | Not started | — |
- **After Step 10:** copy this roadmap into `baihe-subtitler`'s own `docs/` folder, with a final status for every step, so the plan stays with the code. The planning branch can be deleted after that.
- To read this doc from the implementing chat:
  ```
  git fetch origin claude/baihe-subtitle-planning-95qyvq
  git show FETCH_HEAD:docs/baihe-roadmap.md
  ```

---

## 5. How each step gets reviewed

**Steps 1e–10 run in autonomous mode (§4) — these rules apply when the
planning chat is asked to spot-check something, re-sync the status table,
or review Step 11 onward, not as a mandatory per-step gate through Step 10.**
So a review is consistent whenever it does happen, and survives a context
reset or a different session picking up reviews later:

1. **Never trust memory for branch/PR/merge state.** Before saying a step is
   done, merged, or ready for review, `git fetch origin` and check the actual
   branches — `git log --oneline <base>..<branch>` for what a branch adds,
   `git merge-base --is-ancestor <branch> baihe-subtitler` for whether it's
   merged. The status table in §4 is only accurate if every update comes from
   this check, not from what was last said in chat.
2. **Read the diff, not just the commit message.** The summary can be
   accurate and still miss something the diff shows — check both.
3. **Check the diff against this step's own exit condition**, line by line.
4. **Check the diff against the "rules learned from real bugs" list** (in the
   copy of `CLAUDE.md` from Step 1c-pre, once it exists) — id-keyed matching,
   no keys in URLs/logs, HTTP timeouts, the line-writing job guard,
   `db.save_lines` field carry-through, CLI/UI parity. These are exactly the
   mistakes that have already slipped through once each.
5. **Run the tests locally** against an archive of the branch — don't rely
   solely on the implementing session's reported pass count.
6. **Probe the specific fix, not just the general area**, when practical —
   e.g. calling the changed function directly with the input that used to
   break it. This caught real bugs in Steps 1 and 1b that reading the diff
   alone didn't.
7. **State findings as pass/fail against the exit condition**, not general
   impressions — then either approve, or list exactly what needs fixing on
   the same branch.
8. Once a PR is merged, update the §4 status table (per rule 1) **and** the
   roadmap's build order if the step turned up something that changes a
   later step's scope.
9. **When the user asks for something new, or a real gap surfaces that a
   current library can't cleanly solve, check whether a better answer
   already exists in the wild before designing one from scratch** —
   `github.com/topics/<tag>` (e.g. `topics/text-to-speech`,
   `topics/manga-translator`), sortable by stars/recent activity, or a
   targeted search naming the topic plus "github" and the current year.
   This is how every engine/model/technique added to this doc so far was
   actually found. Read the real code and license before recommending
   anything this way, the same discipline every survey in §6 already
   follows — a repo's star count and README are a lead, not a verdict.

---

## 6. Sources for Steps 1c, 6–9c and 11–11b
- yt-dlp — [External JS runtime now required](https://github.com/yt-dlp/yt-dlp/issues/15012), [EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- pyannote — [releases (4.0 breaking changes)](https://github.com/pyannote/pyannote-audio/releases), [community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1), [community-1 blog](https://www.pyannote.ai/blog/community-1)
- Ollama — [context length docs](https://docs.ollama.com/context-length), [silent truncation write-up](https://particula.tech/blog/ollama-num-ctx-silent-prompt-truncation)
- edge-tts — [403 handshake issue #458](https://github.com/rany2/edge-tts/issues/458)
- faster-whisper — [repo (BatchedInferencePipeline)](https://github.com/SYSTRAN/faster-whisper), [repetition issue #987](https://github.com/SYSTRAN/faster-whisper/issues/987), [turbo discussion](https://github.com/openai/whisper/discussions/2363)
- Qwen3-ASR — [ForcedAligner zero-duration spans #197](https://github.com/QwenLM/Qwen3-ASR/issues/197)
- Vocal separation — [Demucs repo (archived)](https://github.com/facebookresearch/demucs), [audio-separator](https://pypi.org/project/audio-separator/)
- pyvideotrans — [FAQ](https://en.pyvideotrans.com/faq)
- Gemini free tier — [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [free vs paid data use](https://ampm-aiops.com/en/guides/gemini-free-tier-data-tradeoff-2026/)
- Bulk discounts — [Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api), [DeepSeek off-peak pricing overview](https://devtk.ai/en/blog/deepseek-api-pricing-guide-2026/) (check DeepSeek's own pricing page for the current hours and rates). Claude Message Batches: 50% off, most batches within 1 hour and at most 24 hours, prompt caching supported (Anthropic API docs).
- **Comparable-project survey — first pass was README-only and got corrected on a second, source-code pass** (Steps 6c, 6d, 7, 7b, and 9b's diagnostics-redaction/generation-guard items). Ideas only — no code copied from any of these; three are AGPL-3.0, which would force this app's whole codebase open if their code were reused.
  - **[VideoLingo](https://github.com/Huanshere/VideoLingo)** — read directly, not summarized: [`core/_3_1_split_nlp.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/_3_1_split_nlp.py), [`_3_2_split_meaning.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/_3_2_split_meaning.py) (Step 6c), [`_4_1_summarize.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/_4_1_summarize.py) (Step 7b), [`_4_2_translate.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/_4_2_translate.py) and [`translate_lines.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/translate_lines.py) (Step 7). This corrected the first pass: it's a real two-call translation pipeline (`direct` → `free`, reflection folded into the second prompt), not three separate calls, and its own line-matching is fuzzy `SequenceMatcher` similarity, not id-based — both facts are called out explicitly in Step 7 rather than left as an inflated claim.
  - **[OpenCreator/KrillinAI](https://github.com/krillinai/OpenCreator)** — confirmed a real, substantial monorepo (`apps/web`, `apps/daemon`, `apps/desktop`, `packages/`, `skills/`), Codex-orchestrated. Its "Portrait Render" (Step 6d) and "redacted diagnostics" (Step 9b) features are real and named in its docs, but implemented as agent skills/prompts driving ffmpeg through its daemon, not as a discrete function to read — so those two roadmap items are inspired by the *feature*, not lifted from readable source, unlike VideoLingo's items above.
  - **[Echoly](https://github.com/sonpiaz/echoly)** — read [`content.js`](https://raw.githubusercontent.com/sonpiaz/echoly/main/content.js) directly (the first pass guessed this code lived in `background.js`, which was wrong — `background.js` only handles session-cookie auth). Confirmed the real "token-guarded async" pattern with actual code (Step 9b's generation-counter guard).
  - **[ZastTranslate](https://github.com/zast57/ZastTranslate)** — smaller and less established than it looked from its own README: 13 stars, 1 fork, 48 files (confirmed via its file tree), not "highly popular" in the sense the others are. One real, useful file resolved on direct fetch — [`fitted_cps_config.py`](https://raw.githubusercontent.com/zast57/ZastTranslate/main/fitted_cps_config.py) (Step 6b's CPS table, values taken directly from it). Its README's other headline claims (an "8-stage subtitle stabilization pipeline," a "222-rule domain dictionary") could **not** be verified against source after several direct attempts — treated as unconfirmed marketing copy, not cited as fact anywhere else in this doc.
  - **Checked, no new gap found beyond what's already here:** [Whishper](https://github.com/pluja/whishper) (AGPL-3.0) — confirmed real 4-service Docker split (`transcription-api`/`backend`/`frontend`), but couldn't retrieve the actual Faster-Whisper wrapper source, so nothing beyond the README-level LibreTranslate/CPS-editor points already used. [VideoTranscriber](https://github.com/DataAnts-AI/VideoTranscriber) — read `app.py` directly; confirmed its ASS style dict's `italic`/`alignment` fields, now added to Step 6b; its diarization (likely pyannote, via an HF-token-gated `utils.diarization` import) and audio-file-keyed caching don't add anything Baihe doesn't already do or already plan (Step 3). [302_video_translation](https://github.com/302ai/302_video_translation) (AGPL-3.0) — confirmed a pure frontend wrapper with all subtitle/translation logic delegated to 302.AI's proprietary API; no pipeline code exists in the repo to learn from. [Synthalingua](https://github.com/cyberofficial/Synthalingua) (AGPL-3.0) — confirmed its overlap/"padded audio" chunk-context mechanism is real (`modules/stream_transcription_module.py`), but its own code explicitly does **not** deduplicate genuinely repeated overlapping text, only suppresses near-duplicates — a known limitation of theirs, not something to copy; comparable to what Baihe's Live tab already does. [Live-YT-Translator](https://github.com/petergpt/Live-YT-Translator) — small Chrome extension delegating everything to OpenAI's Realtime API; architecture doesn't transfer to a downloaded-VOD batch tool.

- **Scanlate/OCR survey (Step 11)** — licenses and pipeline stages checked directly for every project, not from README claims. GPL-3.0, code not usable: [manga-image-translator](https://github.com/zyddnys/manga-image-translator), [Kites](https://github.com/Unheat/Kites). No license stated, not usable: [gnurt2041/MangaOCR](https://github.com/gnurt2041/MangaOCR). Apache-2.0/MIT, checked for ideas (not code, given the language/architecture mismatch for koharu and EasyScanlate): [comic-translate](https://github.com/ogkalu2/comic-translate) (its demo images are not usable as test material — no separate license from currently-published, copyrighted manga), [koharu](https://github.com/koharu-rs/koharu) (Rust/WebGPU app, MIT/Apache-2.0 — but see its detection *model* below, which is the real find), [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) (Apache-2.0, already a Baihe dependency), [EasyScanlate](https://github.com/Liiesl/EasyScanlate) (MIT, Rust + Iced GUI). Models used directly in Step 11: [`ogkalu/comic-text-and-bubble-detector`](https://huggingface.co/ogkalu/comic-text-and-bubble-detector) (Apache-2.0, RT-DETR-v2, boxes only), [`mayocream/lama-manga`](https://huggingface.co/mayocream/lama-manga) (MIT, manga/anime-finetuned LaMa inpainting), [`mayocream/koharu-layout-rfdetr-seg-2xl-1152`](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152) (koharu's actual detection+segmentation model, usable from Python independent of koharu's Rust app — Manga109 training-data caveat noted in Step 11 item 3). Checked and deliberately not adopted: Pororo for Korean OCR (comic-translate's choice) — maintenance concerns confirmed, not brought in.

- **"Are my current models still the best?" check (transcription, translation, OCR), at the user's request.** Verified each candidate directly (repo/model card/license file), not left at search-summary level. **Adopted:** [`Qwen/Qwen3-8B`](https://qwenlm.github.io/blog/qwen3/) as Step 5's new Ollama default (was `qwen2.5:7b`) — confirmed same-parameter-class translation improvement (FLORES+ COMET, literary CEA100 benchmarks) over Qwen2.5-7B, a like-for-like swap. [`FunAudioLLM/SenseVoice`](https://github.com/FunAudioLLM/SenseVoice) as a new optional item in Step 6 — real, 9.4k stars, code MIT (weights under the separate FunASR Model Open Source License, noted as a caveat like others in this doc), confirmed native 7-category emotion + 8-type audio-event tagging covering exactly Baihe's actual content languages (Mandarin/Cantonese/English/Japanese/Korean) — added as a second, audio-derived emotion signal alongside `emotion.py`'s existing text-based one, not a Whisper replacement. [`jzhang533/PaddleOCR-VL-For-Manga`](https://huggingface.co/jzhang533/PaddleOCR-VL-For-Manga) as a second opt-in Japanese OCR backend in Step 11 — confirmed Apache-2.0 directly, but its own model card only benchmarks against base PaddleOCR-VL (70% vs. 27% full-sentence accuracy), not against `manga-ocr`, so it's not a default swap — added pending a real head-to-head in Step 11's manual check. **Checked and not changed:** [`QwenLM/Qwen3-ASR`](https://github.com/QwenLM/Qwen3-ASR) — confirmed Apache-2.0 and a real January 29, 2026 release (plus a June 26, 2026 update) postdating R4's original deferral, noted in §3's R4 row, but the release alone doesn't establish it beats Whisper *on this content*, so R4 stays deferred. Qwen3-32B — confirmed via search (~19.5GB VRAM at Q4_K_M) to be a different hardware class entirely from Step 5's existing 8GB-class budget, not a drop-in default; Qwen3-8B was checked instead and adopted, since it stays in the same class Step 5 was already designed around.

- **`FoundMantisWay/translator-app-enhancer` — not used as a source, flagged instead.** Checked directly: 183 stars, 0 forks, 28 commits, but the repository contains **only a README.md, no source code**, a description that doesn't match its own translator-related topic tags, and a download link to a non-GitHub domain for a Windows executable. Pattern consistent with a star-farmed lure repo, not a real project. Not reviewed further, nothing borrowed, and the external download link was not visited.

- **Novel narration TTS survey (Step 11b), round 1** — re-checked the dubbing code already read for earlier steps, filtered for long-form narration rather than fitting a fixed video timeline (most of what follows is built for the latter, which doesn't apply to a novel with no external timing target). VideoLingo's real [`core/_10_gen_audio.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/_10_gen_audio.py) and [`_8_2_dub_chunks.py`](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/core/_8_2_dub_chunks.py) — confirmed a real warm-up-then-`ThreadPoolExecutor` parallelization pattern (Step 11b item 3, adopted), and confirmed it has **no caching/resume**, unlike Baihe's own `build_narration_track` (Baihe is ahead here, not something to change); its gap-preservation and line-merge-for-duration logic is specifically about fitting a video's timing and doesn't apply to narration, so not adopted. OpenCreator/KrillinAI's dubbing pipeline (subtitle merging, spoken-duration estimation, timeline alignment, LLM rewrite-if-too-long) confirmed to be the same video-timeline-fitting pattern Baihe already has its own version of (`rewrite_for_pacing_llm`); nothing new adopted from it for narration specifically.

- **Novel narration TTS survey, round 2 (engine choice, at the user's request)** — checked licenses and actual capabilities directly, not README marketing. **Adopted:** [`k2-fsa/OmniVoice`](https://github.com/k2-fsa/OmniVoice) (Apache-2.0, `pip install omnivoice`) — confirmed via its own README, not [`debpalash/VoiceStudio`](https://github.com/debpalash/VoiceStudio)'s marketing of it (VoiceStudio's own app code is AGPL-3.0 and is **not** used at all — only this separately-licensed engine is; checked VoiceStudio's real file tree to confirm it's a legitimate, actively-developed project first, not another fake repo). OmniVoice's own README confirmed both the cloning claim and its distinct voice-design feature (`instruct="female, low pitch, british accent"`, no reference audio). [`RVC-Boss/GPT-SoVITS`](https://github.com/RVC-Boss/GPT-SoVITS) (MIT) — read its actual `inference_webui.py`: real `get_tts_wav()` API, 3–10 second reference clip; checked specifically for emotion/style control and confirmed it has none (on their own TODO list, not implemented). **Checked and not added:** [`coqui-ai/tts`](https://github.com/coqui-ai/tts) — its code is MPL-2.0, but XTTS v2's weights are under the Coqui Public Model License (non-commercial only), and Coqui Inc. shut down in January 2024 with no path to a commercial license — same pattern as F5-TTS, not usable. [`myshell-ai/OpenVoice`](https://github.com/myshell-ai/OpenVoice) — genuinely clean (MIT), but redundant once OmniVoice and GPT-SoVITS are both in; the user asked for fewer options, not more. [`calesthio/OpenMontage`](https://github.com/calesthio/OpenMontage) — AGPL-3.0, a much larger and differently-scoped video-production tool with no TTS engine of its own (calls out to Piper/ElevenLabs/others). [`unslothai/unsloth`](https://github.com/unslothai/unsloth) — confirmed not a TTS/dubbing tool at all; it's for fine-tuning LLMs, which doesn't fit how Baihe uses models. [`jianchang512/pyvideotrans`](https://github.com/jianchang512/pyvideotrans) — GPL-3.0, already the app's acknowledged general inspiration; nothing new beyond what Step 7 already took from it. Also checked: whether Baihe's own `emotion.py` output reaches TTS delivery at all — confirmed it does not (no hits in `dub.py`/`workspace_tab.py`); resolved in round 3 below, not left as a dead-end.

- **Novel narration TTS survey, round 3 (emotion control, at the user's request)** — [`resemble-ai/chatterbox`](https://github.com/resemble-ai/chatterbox) (MIT) — confirmed a real, documented `exaggeration` parameter (0.0–1.0+, recommended 0.4–0.7), adopted as the answer to the emotion-to-TTS gap above, wired to Baihe's existing `emotion.py` output. [`HumeAI/tada`](https://github.com/HumeAI/tada) — code MIT, weights under Meta's Llama 3.2 Community License (noted plainly in Step 11b and in the Settings UI, not hidden); added as a long-narration-reliability option, not an emotion answer — its real strength is staying on-script over long unattended runs, confirmed from Hume's own release material, not marketing claims from a third party. `index-tts/index-tts` (IndexTTS2) — checked directly: genuinely finer emotion control (an independent 8-value vector) than Chatterbox's single dial, but under bilibili's own custom Model Use License Agreement rather than a standard one — flagged, not adopted, pending an actual read of that license text.

- **Private-repo GPL/AGPL re-check (done once, after the repo went private).** GPL/AGPL code was excluded everywhere above on the grounds that this repo was public and GPL's copyleft trigger is distribution. Once the repo was made private, re-examined every project excluded on license grounds — manga-image-translator, Kites, pyvideotrans, OpenMontage, VoiceStudio's own app, Whishper, 302_video_translation, Synthalingua — to see if any is genuinely worth pulling in for real now that private single-user use carries no distribution or AGPL network trigger. **manga-image-translator** (GPL-3.0) turned out to be real, substantive code — a working, importable `manga_translator` package with real detector and OCR options — but Step 11 already gets equivalent-or-better capability from permissively licensed sources already chosen (`ogkalu/comic-text-and-bubble-detector`, Apache-2.0; `mayocream/lama-manga`, MIT; auto-routed Apache-2.0/MIT OCR), so adding GPL-3.0 code here would add copyleft for no incremental capability. **pyvideotrans** (GPL-3.0) — checked its actual segmentation/sync mechanism (VAD/silence-based splitting during transcription, a fixed 20-character default for CJK single-line subtitles, a 3–8s duration window): a well-known, generic heuristic, not sophisticated enough to justify taking GPL code for; already mined for the Step 7 idea. Kites and VoiceStudio's own app are JS/Electron (architecture mismatch regardless of license); OpenMontage has no TTS of its own; Whishper adds nothing beyond what's already cited above; 302_video_translation has no pipeline code; Synthalingua's technique was already judged comparable-or-worse than Baihe's own. **Outcome: none changed status.** Private use does legitimately unlock GPL/AGPL reuse in principle, but every one of these was excluded (or stays excluded) for a second, independent reason — redundant with a permissive source already in the roadmap, architecture mismatch, already mined, or no real code — so no GPL/AGPL code was adopted anywhere in this roadmap.

- **`github.com/topics/*` discovery pass (OCR, AI translation, TTS, transcription, manga/anime translation, YouTube translation/dubbing, subtitles) — full README+code review round, per standing rule §5.9.** A broad topic search first (not the user handing over links) turned up a shortlist; each was then read at README-plus-actual-source depth, not left at README level. **Adopted:** [`mazzasaverio/youtube-auto-dub`](https://github.com/mazzasaverio/youtube-auto-dub) (MIT) — its `stages/synchronize.py` clamped FFmpeg-`atempo` time-stretch, confirmed via direct source read, is the source for Step 11c. Its diarization (token-free clustering by default, optional pyannote) and Wav2Lip lip-sync integration were noted but not adopted — Baihe already has its own diarization (Step 3/4), and lip-sync is out of this app's scope. **Checked and not added, with reasons (full detail in Step 11c):** [`rockbenben/subtitle-translator`](https://github.com/rockbenben/subtitle-translator) (MIT) — real batching/caching, but redundant with Baihe's existing timing-safe translation architecture and not portable (Next.js). [`meangrinch/MangaTranslator`](https://github.com/meangrinch/MangaTranslator) (Apache-2.0) — real FLUX-diffusion inpainting pipeline, genuinely more advanced than Step 11's chosen LaMa-manga, but heavier (multi-GB diffusion checkpoint, `diffusers`/`sdnq`/`spandrel` deps) and partly gated behind FLUX.1 Kontext [dev]'s non-commercial license (FLUX.2 Klein is Apache-2.0 and clean) — not swapped in by default, but now written into Step 11 as an explicit conditional fallback (item 6), triggered if LaMa-manga's real-page quality turns out insufficient during Step 11's manual check. [`nganlinh4/oneclick-subtitles-generator`](https://github.com/nganlinh4/oneclick-subtitles-generator) (MIT) — broad scope overlap, but its styled-burn-in renderer duplicates what `video_export.burn_subtitles` already supports (just needs UI wiring, per Step 6b). [`enrell/animesubs`](https://github.com/enrell/animesubs) (GPL-3.0, 8 stars) — too small to be a code source; its OP/ED/karaoke/signs auto-skip idea is worth remembering for a future step, not its code. [`Abrechen2/sublarr`](https://github.com/Abrechen2/sublarr) (GPL-3.0) — a library-wide *arr-ecosystem subtitle manager, different product shape from Baihe's per-drama workspace, not a fit regardless of license. Also surveyed and found to be either already-covered ground or not novel enough to write up separately: docTR, WhisperX, `DCY1117/MangaQuick`, `koharu` (detection model already sourced in Step 11).

- **GUI/performance survey (Step 12), at the user's request ("anything else to improve — models, performance, GUI appearance?").** Confirmed directly, not from search summaries alone: Streamlit's own `@st.fragment` (stable since 1.30) scopes a rerun to one block instead of the whole script — a real fit given `workspace_tab.py`'s size (2,800+ lines). Streamlit 1.63.0's September 1, 2026 release notes (20% caching-layer speedup, a searchable multi-select widget) confirmed real; `constraints.txt`'s existing `streamlit<2` cap already allows it, so this is a version check, not a code change. [`jmedia65/awesome-streamlit-themes`](https://github.com/jmedia65/awesome-streamlit-themes) and [`dataprofessor/streamlit-custom-theme`](https://github.com/dataprofessor/streamlit-custom-theme) checked for the *idea* only (a deliberate light/dark palette via `config.toml`, not code — theming is configuration, not a dependency to adopt). Considered and explicitly left out: swapping Baihe's existing manual module-level model caches for `st.cache_resource` — its main advantage (avoiding cache duplication across multiple worker processes) doesn't apply to a single-process local app, so there's no functional reason to touch working code.

- **UI consistency pass (Step 12, items 5–7), at the user's direct request** ("make the UI consistent with collapsible tabs," "no redundant text or buttons or areas," "consistent font size, wording, easy on the eyes... inspo from public popular webpages"). `tabs/workspace_tab.py` read directly via `git show origin/baihe-subtitler:tabs/workspace_tab.py` (not grep-only) to confirm real line numbers and patterns, not assumed ones: the existing numbered-step/`st.expander` structure, Step 1's `st.subheader` inconsistency, Step 10's nesting inside Step 9, the two byte-identical "Running in the background..." captions, and six bare "Details" expander labels. The other 8 tabs (`library_tab.py`, `reader_tab.py`, `scanlate_tab.py`, `navigator_tab.py`, `discover_tab.py`, `live_tab.py`, `diagnostics_tab.py`, `settings_tab.py`) were only checked at grep-level structure counts (button/expander/header counts per file) this pass, confirmed `workspace_tab.py` as the clear outlier in size but **not** confirmed to share the same specific redundancy patterns — flagged in Step 12 item 6 as needing its own re-verification, not assumed to be clean. Typography/accordion research: a ~16–18px body-text convention and ~1.25 modular heading scale (general 2026 web-typography guidance, not a specific site's code), and accordion-UI guidance directly confirming that a collapsed section's label must be specific rather than generic — which independently validates the "Details" finding above rather than being a separate, unrelated recommendation.

- **Live subtitle playback for QC (Step 12 item 8), at the user's direct request.** Checked Streamlit's own `st.video` documentation directly, not from memory: `subtitles` accepts a string/bytes/`Path`/`io.BytesIO` of raw SRT or VTT content (not only a file path) or a `dict` of `{label: content}` for multiple switchable browser-native CC tracks — real, current, not a proposed/unreleased feature. Confirmed `tabs/reader_tab.py`'s existing `st.video(p)` call (no `subtitles=` arg) and that `core.lines_to_srt`/`core.lines_to_bilingual_srt` already exist and produce valid SRT strings, so this needs no new dependency and no dependency on Step 6b's VTT export work landing first.

- **Full UI/UX information-architecture audit (Steps 13–19), at the user's explicit request** for a complete redesign, not a visual-only pass. Read `app.py` and all 9 tab files directly via `git show origin/baihe-subtitler:...` (line counts, every `st.header`/`st.subheader`/`st.expander`/`st.tabs` call per file) plus a full-app grep of every `st.session_state.*` key (confirmed 34 distinct top-level keys, not hundreds) — not a guess at what's fragmented. Confirmed findings: `active_drama_id` vs. Reader's own separate `reader_drama_pick` (two sources of truth for "current project," with reconciliation code that exists only to patch over the split); API keys shown in both `settings_tab.py` and, duplicated read-only, in `diagnostics_tab.py`; Navigator and Discover both being "find a title to import" split across two top-level tabs; Reader stacking 7 largely-unrelated sections vertically; Diagnostics mixing 9 unrelated concerns (routine checks next to a destructive full-library-reset "danger zone"). Checked whether any previously-reviewed repo's UI code is directly reusable — confirmed no: MangaTranslator (Gradio), oneclick-subtitles-generator (Rust+React), animesubs (Tauri), sublarr (React) — none share Streamlit's component model, so the redesign draws on architectural principles (workflow-stage IA, one project-state source of truth, one primary action per screen) rather than any specific repo's code; the only Streamlit-native, directly-applicable prior research is Step 12's own `awesome-streamlit-themes`/`streamlit-custom-style` findings for the visual layer.

- **UX polish survey (Step 20), at the user's request ("anything else to improve UX?").** Checked directly, not from memory: Streamlit 1.52+ has a native `shortcut=` parameter on `st.button` (confirmed via Streamlit's own docs/release notes — no third-party component needed; community components `streamlit-hotkeys` and `streamlit-shortcuts` exist for more advanced cases but aren't needed for the basic per-button shortcuts this step adds). `st.toast` confirmed real, stable, and fixed in a 2026 update to stay visible across an immediately-following `st.rerun()` — relevant since Baihe's own code frequently reruns right after a message. Grepped the actual app for both: zero `shortcut=` usage anywhere, zero `st.toast` usage anywhere, but 48 `st.success`/`st.balloons` calls in `workspace_tab.py` alone — confirms both are genuine, unexploited gaps, not already covered. Also checked and explicitly **not pursued**: a dynamic browser-tab-title progress indicator — confirmed this is a real, long-standing, unresolved Streamlit limitation (multiple open GitHub issues, no clean native mechanism), not worth a fragile JS-injection workaround given Step 13's in-app status indicator already covers the same need while the user is in the app.

---

## 7. Model & engine registry

Answers "am I on the latest version of what I'm using, and when did I last check?" without needing an in-app feature — every row below traces back to a §6 citation, and `git log docs/baihe-roadmap.md` gives a dated history of every time a row here changed. When you want a fresh check on any of these (or the whole table), just ask — that's what happened for the Step 5/6/11 rows below, all checked the same day at your request.

**How to read "Pinned":** for a Hugging Face model or GitHub repo, this is the commit/release/date actually verified against source (not a marketing claim). For a pip package, it's whatever `constraints.txt`/`requirements.txt` already pins — those already have their own update path (`pip list --outdated`) and aren't duplicated here unless they're the specific version a roadmap decision was based on.

| Role | Model / engine | Source | License | Pinned (as verified) | Status | Last verified |
|---|---|---|---|---|---|---|
| Transcription (default) | Whisper (`large-v3`/`medium`, `large-v3-turbo` optional) | OpenAI, via `faster-whisper` | MIT | `faster-whisper<2` (constraints.txt) | Default | 2026-09-24 (Step 6) |
| Transcription (re-transcribe only, not independent) | Qwen3-ASR 0.6B/1.7B + Qwen3-ForcedAligner-0.6B | [`QwenLM/Qwen3-ASR`](https://github.com/QwenLM/Qwen3-ASR) | Apache-2.0 | Jan 29 2026 release, Jun 26 2026 update (native Transformers support) | In use (R4 — full independence deferred) | 2026-09-24 |
| Transcription (optional, new) | SenseVoiceSmall | [`FunAudioLLM/SenseVoice`](https://github.com/FunAudioLLM/SenseVoice) | Code MIT; weights under FunASR Model Open Source License | `funasr==1.4.14` per repo | Added, opt-in (Step 6) | 2026-09-24 |
| Diarization | `pyannote/speaker-diarization-community-1` (fallback `3.1`) | pyannote | — (not re-checked this pass) | — | Default (Step 4) | not re-checked |
| Translation (local default) | `qwen3:8b` (was `qwen2.5:7b`) | Alibaba Qwen, via Ollama | Apache-2.0 | Qwen3 series | Default (Step 5) | 2026-09-24 |
| Translation (local, opt-in) | `qwen2.5:14b` | Alibaba Qwen, via Ollama | Apache-2.0 | — | Opt-in (Step 5) | not re-checked this pass |
| Translation (cloud) | Claude / Gemini / DeepSeek | Anthropic / Google / DeepSeek | — | Whatever key/model the user configures — not version-pinned here | Default options | n/a (cloud APIs, not pinned) |
| Translation (free/offline) | NLLB, LibreTranslate, DeepL, Google Translate | Meta / LibreTranslate / DeepL / Google | Varies (NLLB: non-commercial) | — | 🧪 Free-tier options (Step 1d) | not re-checked |
| OCR — Japanese (default) | `manga-ocr` | [`kha-white/manga-ocr`](https://github.com/kha-white/manga-ocr) | Apache-2.0 | Already a dependency | Default (Step 11) | not re-verified this pass |
| OCR — Japanese (opt-in, new) | PaddleOCR-VL-For-Manga (1.0B, BF16) | [`jzhang533/PaddleOCR-VL-For-Manga`](https://huggingface.co/jzhang533/PaddleOCR-VL-For-Manga) | Apache-2.0 | Current HF revision as of check date | Added, opt-in pending manual check (Step 11) | 2026-09-24 |
| OCR — Chinese/Korean | `paddleocr`, `tesseract` fallback | PaddlePaddle / Tesseract | Apache-2.0 / Apache-2.0 | `paddleocr` (heavy extra, not capped in constraints.txt) | Default (Step 11) | not re-verified this pass |
| Scanlate detection | `comic-text-and-bubble-detector` (RT-DETR-v2) | [`ogkalu`](https://huggingface.co/ogkalu/comic-text-and-bubble-detector) | Apache-2.0 | Boxes-only checkpoint, confirmed real | Default (Step 11) | earlier this project |
| Scanlate inpainting (default) | LaMa-manga (Big-LaMa, manga/anime-finetuned) | [`mayocream/lama-manga`](https://huggingface.co/mayocream/lama-manga) | MIT | ~989MB safetensors | Default (Step 11) | earlier this project |
| Scanlate inpainting (optional, heavier) | FLUX.2 Klein (via MangaTranslator's pipeline) | [`meangrinch/MangaTranslator`](https://github.com/meangrinch/MangaTranslator) | Apache-2.0 | — | Conditional fallback, not adopted by default (Step 11 item 6) | 2026-09-24 |
| Scanlate segmentation (optional, advanced) | koharu layout/seg model | [`mayocream`](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152) | — (Manga109 academic-use caveat) | ~40MB safetensors | Optional (Step 11 item 3) | earlier this project |
| Narration TTS (primary) | OmniVoice (cloning + voice design) | [`k2-fsa/OmniVoice`](https://github.com/k2-fsa/OmniVoice) | Apache-2.0 | `pip install omnivoice` | Default (Step 11b) | earlier this project |
| Narration TTS (secondary cloning) | GPT-SoVITS | [`RVC-Boss/GPT-SoVITS`](https://github.com/RVC-Boss/GPT-SoVITS) | MIT | — | Option (Step 11b) | earlier this project |
| Narration TTS (emotion) | Chatterbox | [`resemble-ai/chatterbox`](https://github.com/resemble-ai/chatterbox) | MIT | — | Option (Step 11b) | earlier this project |
| Narration TTS (long-run reliability) | TADA | [`HumeAI/tada`](https://github.com/HumeAI/tada) | Code MIT; weights under Llama 3.2 Community License | — | Option (Step 11b) | earlier this project |
| Dub timing | FFmpeg `atempo`, clamped stretch | technique from [`youtube-auto-dub`](https://github.com/mazzasaverio/youtube-auto-dub) | MIT (source of the technique) | — | Step 11c (not yet built) | 2026-09-24 |

A row with "not re-checked" or "earlier this project" isn't stale by default — it just wasn't part of this particular pass. Ask for a re-check on any row (or the whole table) whenever you want one; each check becomes its own dated §6 entry and updates this row, same pattern as today's.
