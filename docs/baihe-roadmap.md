# Baihe Subtitler — Gap Audit & Roadmap toward the Phase 1 Architecture

Status: agreed plan. This doc is written in the **planning** chat, and the
**implementing** chat carries it out on `baihe-subtitler`.

- Target design: [`phase1-architecture.md`](phase1-architecture.md).
- Audited code: branch `baihe-subtitler` at commit `7af8453`. Every `file:function` reference below is on that branch.

## Decisions already made

| Question (Phase 1 §10) | Decision |
|---|---|
| Default local translation model | **7B Q4** (e.g. `qwen2.5:7b`). 14B is opt-in, labelled "may not fit in 8 GB / expect CPU offload". |
| SenseVoice in benchmarks | **Excluded** until its weight license has been reviewed. |
| Later backend/UI stack | **SQLite + FastAPI + React**, deferred to M8+. Streamlit stays until then. |
| Rewrite vs. evolve | **Evolve** the existing app. There is no rewrite; logic moves into headless modules one milestone at a time. |

---

## 1. Current state vs. target

| Target (Phase 1 §) | Today on `baihe-subtitler` |
|---|---|
| Standalone Silero VAD artifact (§2, §4) | VAD is hidden inside faster-whisper (`core.transcribe_for_timing`, `vad_filter=True`), and Whisper segments set every boundary downstream. |
| ASR independent of Whisper | `asr_backend.Qwen3ASRBackend` re-transcribes **Whisper's** segments and replaces only the text. Segments over 300 s keep Whisper's text. |
| Immutable `raw_transcript` + versioned artifacts (§6) | `db.save_lines` deletes and re-inserts every line, and the `zh` column is overwritten by edits, re-transcribes and merges. The only safety nets are `line_history` (the last 10 snapshots, taken only before bulk actions) and `translation_versions`. |
| Diarization re-runnable without re-ASR (§1, M4) | Speaker turns are kept only in `st.session_state` and never saved, and there is no standalone diarization action. Pyannote **3.1** runs in one pass over the whole file (`diarize.diarize`) with no windowing. |
| Word timestamps | Line-level timing only. `forced_align.align_with_qwen3` aligns a *user-supplied* transcript, and `word_align` is experimental MMS alignment whose output is not stored. |
| One GPU model at a time (§7) | Module-level caches keep models loaded for the life of the process (`core._whisper_model_cache`, `asr_backend._asr_model_cache`, `forced_align._aligner_model_cache`). There is no `torch.cuda.empty_cache()` anywhere. |
| Per-segment checkpoint/resume (§6) | `background_jobs` is an in-memory thread plus a dict. A failed or killed transcription restarts from zero. |
| Context-aware translation keyed by id (§3.5) | `translate_engines.translate_lines_with_engine` sends batches of 20 with 6 lines of look-back and **no look-ahead**, and **never sends speaker labels**. Results are mapped back by `zip()` position, so a length mismatch silently shifts lines. The system prompt is hardcoded to "Chinese baihe" for every language, and `OllamaEngine` defaults to `qwen2.5:14b` with no request timeout. |
| SRT / ASS / VTT (§4) | SRT only (`core.lines_to_srt`, `core.lines_to_bilingual_srt`). |
| Full-pipeline benchmark (§8 M7) | `asr_benchmark.run_benchmark` covers ASR and alignment only, and accuracy is a difflib ratio, not CER/WER. |
| Headless pipeline | `cli.py` is partial: `cmd_align` requires a transcript; `cmd_translate` skips glossary, style and locale; `cmd_dub` drops `flag`/`flag_note`. |

Code-health issues that affect the plan:
- Lines have **no stable id**. They are keyed by `idx`, which changes after a merge, so `translation_notes`, `line_emotions`, `consistency_issues` and `reading_history` can end up pointing at the wrong line.
- `tabs/workspace_tab.py` is about 2,800 lines and holds most of the pipeline orchestration.
- `FILE_ORGANIZATION.md` is out of date: it says 285 tests, but there are 761.

---

## 2. Roadmap (in dependency order)

Rules for every milestone:
- Keep all existing tests green (`python run_tests.py`).
- Add mocked tests in the existing style: the `tests/conftest.py:isolated_db` fixture and fake model classes, with no GPU or real models in tests.
- Put new pipeline logic in headless modules callable from `cli.py`, and keep the Streamlit tab as a thin caller.
- Do one milestone per feature branch off `baihe-subtitler`. The next milestone starts only once the current one meets its exit condition.

### R0 — Line identity & safe saves
- Give `lines` a stable primary-key id that survives merges, edits and re-saves.
- Replace delete-all in `db.save_lines` with an upsert/diff by id. Also fix its double `conn.close()`.
- Move `translation_notes`, `line_emotions`, `consistency_issues` and `reading_history` from `idx` to line id, with a migration for existing rows.
- Add one shared row→`Line` loader and use it in `cli.cmd_translate`, `cli.cmd_dub` and `workspace_tab`. This fixes `cmd_dub` dropping `flag`/`flag_note`.

**Exit:** after a merge, a note that was attached to a line is still attached to the same line.

### R1 — Artifact store
- Add a new table:
  `artifacts(id, drama_id, kind, version, parent_id, params_json, path, status, created_at)`.
- Payloads live on disk as `drama_dir/artifacts/<kind>/v<n>.json`, with `kind ∈ {vad_segments, raw_transcript, diarization_turns, speaker_transcript, translation, subtitle_export}`.
- Transcription writes an **immutable** `raw_transcript` artifact. The `lines` table becomes the edited working view, recording which raw artifact it came from.
- Fold `translation_versions` into this model over time; it doesn't need to change in R1.

**Exit:** after lines are edited, re-transcribed or merged, the `raw_transcript` file is byte-identical to the original.

### R2 — Decoupled diarization
- Save pyannote output as a `diarization_turns` artifact.
- Add a pure `merge_speakers(lines, turns)` step that reuses `diarize.assign_speaker_to_line` and `label_lines_with_speakers`.
- Add a **"Re-run diarization"** UI action and a `cli.py diarize` command. Both use the stored audio and never re-run ASR.
- Switch to `pyannote/speaker-diarization-community-1`, falling back to `3.1`.
- For long audio, diarize in windows (default 10 min, 30 s overlap) and join speaker clusters across windows by embedding cosine similarity.

**Exit:** changing `expected_speakers` relabels lines, and a test asserts that the ASR mock is never called.

### R3 — Model manager & VRAM
- Add a `model_manager.py` that allows one GPU-resident model at a time, with explicit unload plus `torch.cuda.empty_cache()`.
- Route the Whisper, Qwen3-ASR, aligner, pyannote, MMS and NLLB loaders through it.
- Change the `OllamaEngine` default to `qwen2.5:7b`, offer 14B as an opt-in labelled as not fitting cleanly in 8 GB, and add a request timeout.

**Exit:** a test shows that loading model B unloads model A.

### R4 — Standalone VAD, independent ASR, checkpoints
- Add a Silero VAD stage that writes a `vad_segments` artifact.
- Qwen3-ASR transcribes VAD segments directly and no longer depends on Whisper. Whisper stays a selectable backend.
- Qwen3-ForcedAligner runs per VAD segment, within its 5-minute cap, and stores **word timestamps** in `raw_transcript`.
- Save progress per segment into the artifact so a restarted job skips segments that are already done.

**Exit:** a job killed partway through resumes from the last completed segment, not from zero.

### R5 — Translation robustness
- Ask for id-keyed JSON output (`{"<line_id>": "<translation>"}`), check that the returned ids match the batch, and retry the missing or extra ones. Remove positional `zip()` mapping.
- Include the speaker or character name for each line, and add N lines of look-ahead context alongside the existing look-back.
- Build the system prompt from the drama's source language and content type, not a hardcoded "Chinese baihe".
- Bring `cli.cmd_translate` up to UI parity: glossary terms, style guidelines and locale.
- Give `live_translate.process_chunk` a small rolling context instead of translating each segment in isolation.

**Exit:** a fake engine that returns lines out of order, too few lines, or extra lines never assigns a translation to the wrong line.

### R6 — Export formats
- Add VTT and ASS writers next to `core.lines_to_srt`.
- Split long cues and wrap lines to character-per-line limits that can be configured per language.

**Exit:** the exported files load correctly in mpv or VLC against the source clip.

### R7 — Full-pipeline benchmark
- Extend `asr_benchmark.py` with VAD, diarization and translation stages. Report DER when a reference RTTM is provided.
- Replace the difflib ratio with **CER** for zh/ja/ko and WER for other languages (e.g. `jiwer`).
- Output the side-by-side timing, peak VRAM and quality comparison as JSON and Markdown. SenseVoice stays excluded.

**Exit:** a single command benchmarks a fixed 5–10 minute clip across configurations.

### Deferred — M8+
- Migrate to FastAPI + React on top of the headless pipeline built in R0–R7.
- Add a persistent job queue to replace the in-memory `background_jobs`.
- Add cross-video voiceprint suggestions (Phase 1 §3.4), which remain experimental and require the user to confirm each match.

---

## 3. Working agreement between the two chats

- **Planning chat:** branch `claude/baihe-subtitle-planning-95qyvq`, docs only. Roadmap changes go here.
- **Implementing chat:** one feature branch per milestone off `baihe-subtitler`. Report back which milestone is done and anything that changed the plan.
- To read this doc from the implementing chat:
  ```
  git fetch origin claude/baihe-subtitle-planning-95qyvq
  git show FETCH_HEAD:docs/baihe-roadmap.md
  ```
