# Baihe Subtitler — Gap Audit & Roadmap toward the Phase 1 Architecture

Status: agreed plan (**shortened version**). This doc is written in the
**planning** chat, and the **implementing** chat carries it out on
`baihe-subtitler`.

- Target design: [`phase1-architecture.md`](phase1-architecture.md).
- Audited code: branch `baihe-subtitler` at commit `7af8453`. Every `file:function` reference below is on that branch.

**Build order:**
- Steps 1–5: R5 → safety fixes (1b) → dependency fixes (1c) → R0 → R1-lite → R2 → R3-lite.
- Steps 6–10: transcription quality (6) → reflect translation mode (7) → recurring-voice suggestions (8) → cost controls & bulk discounts (9) → Windows launcher (10). Milestones R4, R6 and R7 are deferred (see §3).

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
4. **edge-tts 403 errors.**
   - Microsoft periodically blocks edge-tts; the latest report is a 403 on the WebSocket handshake in January 2026. The fix is usually `pip install -U edge-tts`.
   - Catch this in `dub._edge_tts_synthesize` and show "Microsoft blocked the request — run `pip install -U edge-tts`".
   - When Piper is installed, offer it as an automatic offline fallback.

**Exit:**
- Mocked tests cover both pyannote output shapes, the Ollama request's `num_ctx` and `format` fields, and the edge-tts 403 message.
- Diagnostics reports whether a JS runtime is present.

### Step 2 — R0: Permanent line IDs
- Give `lines` a stable primary-key id that survives merges, edits and re-saves.
- Replace delete-all in `db.save_lines` with an upsert/diff by id. Also fix its double `conn.close()`.
- Move `translation_notes`, `line_emotions`, `consistency_issues` and `reading_history` from `idx` to line id, with a one-time migration for existing projects.
- Add one shared row→`Line` loader and use it in `cli.cmd_translate`, `cli.cmd_dub` and `workspace_tab`. This fixes `cmd_dub` dropping flags.
- Switch R5's translation ids to the real line ids.
- Make background jobs save only the fields they own (see Step 1b #1), then remove the short-term "one job at a time" block.

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

### Step 7 — Reflect translation mode *(new feature; needs Step 1)*
- Add an optional "High quality" setting:
  1. translate the batch;
  2. the same engine critiques its own translation, covering accuracy, pronouns and gender, glossary use, tone and register;
  3. it rewrites using the critique.
- It works through the same engine interface for every LLM engine: Claude, Gemini, DeepSeek and Ollama.
- It costs about 3× as much, so show the estimated cost (`translate_engines.estimate_cost`) before the user starts it.
- Every step keeps the id-keyed JSON from Step 1.
- Save the critique as translation notes, so the reasons for changes can be reviewed.
- The idea comes from pyvideotrans's three-step "reflection" translation.

**Exit:** a mocked engine test shows 3 calls per batch, with ids kept at every step, and critiques stored as notes.

### Step 8 — Recurring-voice suggestions *(new feature, experimental; needs Step 4)*
- With pyannote 4, `DiarizeOutput.speaker_embeddings` gives one voice fingerprint per detected speaker. Save them with the diarization turns.
- Keep an averaged fingerprint for each series character in `series_characters`.
- On a new episode, suggest "SPEAKER_01 sounds like <name> (similarity 0.82)" by cosine similarity, above a threshold you can adjust.
- **The user always confirms each match; nothing is labelled automatically.**
- This matches Phase 1 §3.4: the feature is experimental, and nobody has shown it works reliably across different recordings.

**Exit:** a test with fake embeddings produces the correct ranking, respects the threshold, and never assigns a name without confirmation.

### Step 9 — Cost controls & bulk discounts *(needs Step 1's id-keyed output)*
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
     - Submit every batch at once, with `custom_id = "<drama_id>:<batch_index>"`.
     - Save the batch id on the drama, so a restarted app can pick results up again.
     - Poll in a background job, then apply results **by id, never by position**. Results come back in any order; Step 1's id-keyed JSON makes this safe.
   - One trade-off to show in the UI: because every batch is submitted at the same time, the look-back context can only use *source* lines, not earlier translations. That's slightly less consistent than live mode. Optionally, add a second consistency pass (the consistency check, also batched) to make up for it.
3. **Make prompt caching count** (all engines; no quality change).
   - The prompt should always start with the same stable part (system instructions → style guide → glossary → reference novel), with the changing lines after it. Then Claude's `cache_control`, Gemini's implicit caching and DeepSeek's automatic prefix caching all hit. Cache reads cost about 10% of the normal input price.
   - Check this from each response's usage (`cache_read_input_tokens` on Claude), and show the cache-hit share next to the cost in the usage view.
   - Nothing that changes between requests (timestamps, batch numbers) may go in the stable part.

**Exit:**
- A mocked test shows a job stops at the cap and keeps its finished lines.
- A mocked batch client returning results out of order still assigns every translation to the right lines.
- A restarted app picks up a pending batch by its saved id.
- A test shows the stable prompt part is byte-identical across batches of the same drama.

### Step 10 — One-click Windows launcher, own window & desktop shortcut *(convenience)*
- Add a `start.bat` (plus an optional `start.ps1`) that:
  - creates or activates the venv on first run;
  - installs `requirements-core.txt` if it's missing;
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

---

## 3. Deferred: revisit only if a real need appears

| Milestone | Why it's deferred | Revisit when |
|---|---|---|
| **R4** — standalone VAD, Qwen3-ASR independent of Whisper, word timestamps, resumable jobs | The biggest and riskiest change. Whisper already works, and nobody has shown Qwen3-ASR is better on this content. | Whisper transcripts are clearly poor, or long jobs keep failing partway through. |
| **R6** — VTT and ASS export | SRT plays everywhere. | Styled or colour-per-speaker subtitles are wanted. |
| **R7** — full-pipeline benchmark | A developer tool whose main use is deciding R4. | R4 is being reconsidered. |
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
- **Status:**
  - Step 1 is merged into `baihe-subtitler` and was reviewed OK.
  - Step 1b (`step-1b-safety-fixes`) is reviewed; it's pending the edit-lock follow-up, then the pull request.
- To read this doc from the implementing chat:
  ```
  git fetch origin claude/baihe-subtitle-planning-95qyvq
  git show FETCH_HEAD:docs/baihe-roadmap.md
  ```

---

## 5. Sources for Steps 1c and 6–9
- yt-dlp — [External JS runtime now required](https://github.com/yt-dlp/yt-dlp/issues/15012), [EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- pyannote — [releases (4.0 breaking changes)](https://github.com/pyannote/pyannote-audio/releases), [community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1), [community-1 blog](https://www.pyannote.ai/blog/community-1)
- Ollama — [context length docs](https://docs.ollama.com/context-length), [silent truncation write-up](https://particula.tech/blog/ollama-num-ctx-silent-prompt-truncation)
- edge-tts — [403 handshake issue #458](https://github.com/rany2/edge-tts/issues/458)
- faster-whisper — [repo (BatchedInferencePipeline)](https://github.com/SYSTRAN/faster-whisper), [repetition issue #987](https://github.com/SYSTRAN/faster-whisper/issues/987), [turbo discussion](https://github.com/openai/whisper/discussions/2363)
- Qwen3-ASR — [ForcedAligner zero-duration spans #197](https://github.com/QwenLM/Qwen3-ASR/issues/197)
- Vocal separation — [Demucs repo (archived)](https://github.com/facebookresearch/demucs), [audio-separator](https://pypi.org/project/audio-separator/)
- pyvideotrans — [FAQ](https://en.pyvideotrans.com/faq)
- Bulk discounts — [Gemini Batch API](https://ai.google.dev/gemini-api/docs/batch-api), [DeepSeek off-peak pricing overview](https://devtk.ai/en/blog/deepseek-api-pricing-guide-2026/) (check DeepSeek's own pricing page for the current hours and rates). Claude Message Batches: 50% off, most batches within 1 hour and at most 24 hours, prompt caching supported (Anthropic API docs).
