# Baihe Subtitler — Gap Audit & Roadmap toward the Phase 1 Architecture

Status: agreed plan (**shortened version**). This doc is written in the
**planning** chat, and the **implementing** chat carries it out on
`baihe-subtitler`.

- Target design: [`phase1-architecture.md`](phase1-architecture.md).
- Audited code: branch `baihe-subtitler` at commit `7af8453`. Every `file:function` reference below is on that branch.

**Build order:** R5 → safety fixes (Step 1b) → R0 → R1-lite → R2 → R3-lite. Milestones R4, R6 and R7 are deferred (see §3).

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
   - Proper fix (land it in Step 2): each job writes only the fields it owns — translation writes `en`; flagging writes `flag`/`flag_note`.
2. **API keys leak into errors.**
   - The problem: the Gemini calls (`translate_engines.py` around lines 253 and 456, and `qa.py`) and Google Translate (around line 391) put the key in the URL (`params={"key": ...}`). A `raise_for_status()` failure then includes the full URL, key included, in the error message. That message is shown in the UI and stored in `dramas.last_translate_errors`.
   - Fix: send the key in a header (`x-goog-api-key` for Gemini; Google Translate v2 accepts `X-Goog-Api-Key` as well), and redact anything that looks like a key or token from error strings before they're shown or stored.
3. **Requests with no time limit.** Add `timeout=` to every `requests.post`/`requests.get` in `translate_engines.py` and `qa.py`: Gemini, Google, Ollama and Q&A. Without one, a server that stops responding leaves the job stuck at "running" forever.
4. *(Minor)* Tests that hard-import optional libraries (`jieba`, `pytesseract`, `cv2`) should use `pytest.importorskip`, so a core-only install gives a clean test run.

**Exit:**
- A test shows a second line-writing job is refused while one is running.
- A test shows a failed Gemini request's stored error contains no key.
- Every HTTP call has a timeout (a test or static check).

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
- **Implementing chat:** one feature branch per step off `baihe-subtitler`. Report back which step is done and anything that changed the plan.
- To read this doc from the implementing chat:
  ```
  git fetch origin claude/baihe-subtitle-planning-95qyvq
  git show FETCH_HEAD:docs/baihe-roadmap.md
  ```
