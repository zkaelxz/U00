# Baihe Subtitler — Gap Audit & Roadmap toward the Phase 1 Architecture

> **NEXT:** run Step 1c-pre in the implementing chat ("Do Step 1c-pre").
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
- Steps 6–11: transcription quality (6) → export formats (6b) → meaning-based re-segmentation (6c) → vertical/shorts export (6d) → reflect translation mode (7) → content-summary glossary extraction (7b) → recurring-voice suggestions (8) → cost controls & bulk discounts (9) → job ETAs/model disk/bulk translate/diagnostics redaction (9b) → drama presets (9c) → Windows launcher (10) → Scanlate ML detector/inpainting/OCR routing (11). Milestones R4 and R7 are deferred (see §3).

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
| 5 | Translate with Ollama and check it uses the 7B model. Run transcription then translation back-to-back with no out-of-memory error. |
| 6 | Transcribe an episode that used to get repeated-phrase loops, and check timings stay in sync to the end. |
| 6b | Export the same episode as SRT, VTT and ASS. Check all three play correctly in your usual player, and ASS shows different speakers in different colours. |
| 6c | Turn on re-segmentation for one drama and check line boundaries land at real sentence/clause breaks, not mid-thought, and timing still lines up. |
| 6d | Export a short clip vertically and check it's genuinely 9:16 with legible burned subtitles. |
| 7 | Translate one episode in "High quality" mode. Check the cost estimate shows first and the critiques appear as notes. |
| 7b | Run glossary auto-extraction on a drama and check the proposed terms make sense for who's actually in the story (not just generic terms). |
| 8 | Open a second episode of the same series. Check the voice suggestions are sensible and nothing is labelled until you confirm it. |
| 9 | Set a low cost cap and check the job stops at it. Run one Bulk-mode translation and check results arrive on the right lines. |
| 9b | Start a long transcription and check the ETA appears and looks reasonable. Open the model-cache panel and delete one entry. Select 2–3 dramas in a series in Library and run bulk translate. Set browser cookies in Settings and download a login-gated TikTok/Instagram/Bilibili clip. Use "Copy diagnostics for support" and check no path/username shows up. |
| 9c | Save a preset from one drama, apply it to a new one, and check every captured field is still editable afterward. |
| 10 | Double-click the desktop shortcut. The app should open in its own window. |
| 11 | On a real comic page, run Scanlate with the ML detector + LaMa-manga inpainting installed and compare the result against the OpenCV-only path — the ML version should have no visible edge where text was removed. Try a Japanese, Chinese and Korean page and check the OCR backend auto-picked is the right one for each. |

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
- Switch to `pyannote/speaker-diarization-community-1`, falling back to `3.1` if it can't load.
- **Out of scope for now:** windowed diarization for multi-hour audio. Add it only if long VODs become common.

**Exit:** changing `expected_speakers` and re-running relabels lines, and a test asserts that the ASR mock is never called.

### Step 5 — R3-lite: Local-model defaults
- Change the `OllamaEngine` default to `qwen2.5:7b`, offer 14B as an opt-in labelled as not fitting cleanly in 8 GB, and add a request timeout.
- After each GPU stage (transcription, alignment, diarization) finishes, clear the model caches and call `torch.cuda.empty_cache()` when CUDA is available.
- **Out of scope:** a full `model_manager` module. Build one only if out-of-memory crashes actually happen.

**Exit:** the Ollama default is 7B with a timeout, and a test shows the model caches are empty after a stage completes.

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

**Exit:** mocked tests cover the new Whisper kwargs, the smaller chunk size, the zero-duration fallback and separator backend selection.

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

### Step 6c — Meaning-based subtitle re-segmentation *(idea from VideoLingo)*
A different, upstream problem from Step 6b's export-time character wrapping. Today's line boundaries come entirely from Whisper's VAD (silence gaps) — a line can end mid-sentence just because the speaker paused, or run two separate thoughts together because they didn't. Confirmed VideoLingo's actual mechanism by reading `core/_3_1_split_nlp.py` and `_3_2_split_meaning.py`, not just its README:
1. **Rule-based passes first** (their real order, via spaCy): split on hard punctuation, then on commas, then on sentence boundaries, then split anything still too long at its grammatical root. Adopt the concept using what's already in `segment.py` (jieba/sudachipy/kiwipiepy, already per-language) rather than adding spaCy as a new dependency.
2. **One LLM pass**, only for lines still too long after the rule-based passes — asks it to return `[br]` break markers in a JSON response, not to rewrite or translate anything. Their matching step is worth adopting directly: the model's suggested break text is matched back to the *original* text with `SequenceMatcher` (>0.9 similarity required), retried up to 3 times on a bad match, so a hallucinated or reworded response can't corrupt the real text — it can only fail to find a good split, never silently rewrite content.
3. Re-run alignment (Step 2/3's existing timing-reconstruction path) against the new boundaries so start/end times still line up.
4. Optional per drama, off by default — this changes line boundaries, which is a bigger structural change than Step 6b's export-time wrapping, so it needs to be something the user opts into per drama, not silently different from today's output.

**Exit:** a mocked test shows a run-on ASR segment gets split at a genuine clause boundary (not mid-word, not arbitrarily by length), and a normal segment passes through unchanged; timing stays continuous across a split.

### Step 6d — Vertical/shorts export *(idea from OpenCreator's "Portrait Render" and ZastTranslate's "Viral Shorts Studio")*
Directly relevant to the "clip streamer" look you asked about earlier — that culture is built around vertical/shorts format specifically, not just subtitle styling.
1. Given a drama (or a selected time range within one), render a 9:16 vertical version: centre-crop by default, with a manual crop-position adjustment per drama rather than trying to auto-detect a face/subject.
2. Burn subtitles using Step 6b's ASS styling (so the "Streamer clip" preset and per-speaker colours carry over), sized and positioned for the vertical frame.
3. Export as its own video file alongside the existing horizontal export — this is additive, not a replacement for the existing "Export full subtitled episode" step.

**Exit:** a vertical export of a short clip plays correctly, is genuinely 9:16, and its burned subtitles are legible without manual repositioning.

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
- This matches Phase 1 §3.4: the feature is experimental, and nobody has shown it works reliably across different recordings.

**Exit:** a test with fake embeddings produces the correct ranking, respects the threshold, and never assigns a name without confirmation.

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
- A test shows the stable prompt part is byte-identical across batches of the same drama.

### Step 9b — Job ETAs, model disk management, bulk series translate, Live chunk guard
Independent, additive gaps found while reviewing for efficiency and missing functionality — no shared code between them, grouped here to keep the step count down.

1. **Time estimate on long jobs.** `background_jobs` already tracks `started_at` and a `progress` fraction (`update_progress(job_id, frac, message)`); nothing currently uses them together. Add a simple ETA next to the existing progress bar: `elapsed = now - started_at`, `remaining ≈ elapsed * (1 - frac) / frac`, shown as "~N min remaining" once `frac` is past a small threshold (too noisy right at the start). Pure UI addition — no new job-tracking fields needed.
2. **Downloaded-model disk management.** `storage.py` reports disk usage for the app's own `library/` folder, but Whisper/pyannote/Qwen3-ASR/ForcedAligner/F5-TTS weights live in Hugging Face's own cache (`~/.cache/huggingface` by default) with no visibility or cleanup from inside the app. Across several ASR/TTS backends this can reach tens of GB. Add a small panel (Diagnostics, next to the existing dependency checks) that lists what's in the HF cache with each entry's size, and a delete button per entry — a thin wrapper over `huggingface_hub.scan_cache_dir()`, which already gives size-per-revision without reimplementing cache-format parsing.
   - Same Diagnostics screen, same PR: add a **"Copy diagnostics for support"** button that runs the existing key/token redaction (`translate_engines.redact_secrets`, Step 1b) plus a pass that also strips local file paths and the OS username from the output *(idea from OpenCreator's "redacted diagnostics" — Step 1b already redacts keys from stored errors, but nothing currently redacts what a "copy for support" action would show, and a raw library path can leak the machine's username)*.
3. **Bulk "translate everything untranslated" across a series.** Library's existing bulk actions (`tabs/library_tab.py` ~line 127, `library_bulk_select`) cover status, delete and export, but not starting a job — translating multiple dramas still means opening each one individually. Add a bulk action that starts a `run_translate_job` per selected drama with no translation yet, each drama's own saved engine/glossary/style/locale settings (same as its own Workspace tab would use), queued one at a time rather than all at once (see Step 3's/Step 1c's GPU-load reasoning — avoid starting several GPU-touching jobs simultaneously). Respect the Step 1b line-writing job guard per drama; skip (don't queue) a drama that already has one running.
4. **Cookie-based login for downloads, as a real setting, not just an error hint.** The app already isn't YouTube-locked — `video_download.py`'s own docstring says "YouTube and the many other sites yt-dlp supports," and yt-dlp itself supports Bilibili, TikTok and Instagram natively — but `--cookies-from-browser` is currently only *mentioned* inside a YouTube-specific error message (`live_translate.py`), not exposed as something the user can turn on. TikTok and Instagram in particular block plain unauthenticated requests far more aggressively than YouTube does. Add a Settings field (which browser to pull cookies from, or a cookies file path), pass it through to yt-dlp in both `video_download.py` and `live_translate.py`, and update the "no formats found" error and Diagnostics copy to mention it generally rather than only for YouTube. *(Idea prompted by comparing platform coverage against OpenCreator/302_video_translation — the download capability was already mostly there; this closes the practical reliability gap, not a missing extractor.)*
5. **A stale-chunk guard for Live translation** *(idea confirmed by reading Echoly's actual `content.js`, not just its README — its real "token-guarded async" pattern: a counter incremented once per session/setting change, captured by closure in each async call, checked before every state mutation; a callback whose captured value no longer matches the current counter is discarded)*. `live_translate.py`'s chunk loop has no equivalent: if the user changes a setting or stops/restarts mid-stream while an older chunk's transcribe/translate call is still in flight, that call's result can land after the newer state has already moved on. Add a simple integer "generation" counter on the live session, incremented on every stop/restart/setting change; each chunk's async result is applied only if the generation it was started under still matches current.

**Exit:**
- A test shows the ETA display appears once progress is non-trivial and disappears/holds sensibly at 0% and 100%.
- A test shows the model-cache panel lists entries with sizes and that deleting one actually frees the space (using a fake cache dir, not the real HF cache).
- A test shows the bulk translate action starts one job per eligible selected drama and skips any drama with a job already running, using each drama's own settings.
- A test shows the cookies setting reaches yt-dlp's options in both `video_download.py` and `live_translate.py`.
- A test shows the "copy diagnostics" output contains no file path or username, even when the raw diagnostics do.
- A test shows a chunk result that finishes after a generation bump is discarded, and one that finishes before the bump is applied.

### Step 9c — Drama/project presets *(idea from OpenCreator's "creation templates")*
Library's own framing is managing "dozens of titles," but every new drama starts from scratch: engine, model, style preset, locale, content-type defaults all get re-picked by hand each time. A template captures a full Workspace configuration — engine/model choice, style preset, locale, default speaker-gender-default setting, glossary scope — as a named, reusable preset.
1. **"Save as preset"** in Workspace, from the current drama's settings.
2. **"Apply preset"** when creating a new drama (or on an existing one), which fills in the same fields, still editable afterward — never silently locks anything.
3. Store presets at the library level (not per-series), so one preset works across unrelated series/projects.

**Exit:** a test shows applying a preset to a new drama sets all its captured fields, and none of them are frozen against later manual changes.

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

**Exit:** on a clean Windows machine with Python and ffmpeg installed, all of these work with no typed commands. Check them by hand; this can't be unit-tested.
- Double-clicking `start.bat`, or the desktop shortcut, opens the app in its own window.
- Running it a second time just opens the window again.
- The shortcut shows the app icon.

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
5. All of this stays optional/opt-in, same spirit as the existing `detect_bubbles_ml()` docstring — the free OpenCV heuristic (detection) and plain `cv2.inpaint` (inpainting) remain the zero-install default; these are better backends for someone who installs the extra weight, not a replacement that changes default behaviour.

**Exit:**
- A test shows `detect_bubbles_ml()` actually loads and runs the named model (mocked, no real network/weights in tests) and falls back cleanly to `detect_bubbles_cv()` when the model isn't installed.
- A test shows the LaMa-manga inpainting backend is selected when available, OpenCV inpainting when it isn't.
- A test shows OCR backend selection follows source language by default and can still be overridden manually.
- Manual check (real weights, on your own PC): run detection + inpainting on a real page with both the ML and OpenCV-only paths and compare — the ML inpainting result should show no visible box edge where text was removed.

---

## 3. Deferred: revisit only if a real need appears

| Milestone | Why it's deferred | Revisit when |
|---|---|---|
| **R4** — standalone VAD, Qwen3-ASR independent of Whisper, word timestamps, resumable jobs | The biggest and riskiest change. Whisper already works, and nobody has shown Qwen3-ASR is better on this content. | Whisper transcripts are clearly poor, or long jobs keep failing partway through. |
| **R7** — full-pipeline benchmark | A developer tool whose main use is deciding R4. | R4 is being reconsidered. |
| **Live capture for Bilibili/TikTok Live**, beyond the download-only support Step 9b adds | `live_translate.py`'s live-stream chunking is YouTube (and Twitch) specific; each platform's live/HLS quirks differ enough that this is real, separate work, and the app's actual focus is VOD audio dramas, not live streaming. *(Surveyed while comparing against OpenCreator/302_video_translation/Synthalingua — none of them make this look like a small add either.)* | Live capture from one of these platforms is actually wanted, not just downloading an already-finished VOD (which Step 9b's cookie support already covers). |
| **R1-full** — a general artifact and versioning system | R1-lite covers the need that matters (not losing the original). | Several stages need a history of versions. |
| **R2 windowing** — diarizing long audio in windows | Only matters for streams several hours long. | Long VODs become a regular input. |
| **R3-full** — a single-slot model manager | Only matters if the GPU runs out of memory. | Out-of-memory crashes occur. |
| **M8+** — FastAPI + React, a persistent job queue, voiceprint suggestions | A large migration with no current pain driving it. | Streamlit becomes the bottleneck. |

---

## 4. Working agreement between the two chats

- **Planning chat:** branch `claude/baihe-subtitle-planning-95qyvq`, docs only. Roadmap changes go here.
- **Implementing chat:** follows this pull request flow for every step.
  1. Build the step on its own branch off the latest `baihe-subtitler`. Push it, report which step is done and anything that changed the plan, then **stop**.
  2. The user asks the planning chat to "check Step X", and it reviews the branch against this roadmap. Put any fixes on the **same branch**.
  3. Once the planning chat approves it, the user says "create a PR for this step". Then open a pull request **into `baihe-subtitler`** with a short plain-English summary. **Don't merge it yourself.**
  4. The user merges it on GitHub.
  5. Start the next step only after the previous one is merged, branching off the updated `baihe-subtitler`.
- **Status** (updated on every review — see §5 for how to keep this accurate; last checked against the real branch state on 2026-09-24). **Manual check** tracks the user's own real-model check from §2's table, separately from merge status — a step can be merged with its manual check still pending. It moves to ✅ only when the user says "manual check passed for Step X"; the planning chat doesn't infer it.

  | Step | Branch | Merged | Manual check |
  |---|---|---|---|
  | 1 — R5 translation fixes | `claude/r5-translation-fixes` (deleted post-merge) | ✅ Merged | ⏳ Pending |
  | 1b — Safety fixes | `step-1b-safety-fixes` (deleted post-merge) | ✅ Merged (PR #2) | ⏳ Pending |
  | 1c-pre — AI setup | *(drafted here, not yet copied in)* | Not started | — |
  | 1c — Dependency fixes | — | Not started | — |
  | 1d — Free testing engines | — | Not started | — |
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
- **After Step 10:** copy this roadmap into `baihe-subtitler`'s own `docs/` folder, with a final status for every step, so the plan stays with the code. The planning branch can be deleted after that.
- To read this doc from the implementing chat:
  ```
  git fetch origin claude/baihe-subtitle-planning-95qyvq
  git show FETCH_HEAD:docs/baihe-roadmap.md
  ```

---

## 5. How each step gets reviewed

So a review is consistent step to step, and survives a context reset or a
different session picking up reviews later:

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

---

## 6. Sources for Steps 1c, 6–9c and 11
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
