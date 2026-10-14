# Accuracy tuning: test the owner's own clips, pick models and settings by measurement (plan)

Status: **plan; step A1 built, A2 onward not started** (owner decision 2026-10-14). Scope: transcription first, then translation. The main input is a clip set the owner prepares (audio, transcript, translation); corrections made in Review top it up. "Training" here means tuning settings and feeding corrections back into prompts; no model weights change. Drafted against `baihe-subtitler` at 5caf079; re-check each line reference before building.

## Why

The owner wants better transcription and translation accuracy, and evidence for which numbers to use. Today's defaults come from small CPU experiments on public sets (FLEURS) and streamer VODs (`docs/asr-experiments.md`), never from the owner's own drama audio. The app already records most of what a measurement loop needs; the missing parts are listed below.

## What exists

- **Benchmark Lab.**
  - Scoring (`services/benchmark_scoring_service.py`): 1−CER for transcription (Chinese folded to Simplified first) and chrF or difflib for translation. Pass marks are 0.8, or 0.5 for chrF.
  - An LLM judge (`services/benchmark_judge_service.py`) scores accuracy, tone and naturalness.
  - Model Arena compares at most 4 configs per run (`MAX_CONFIGS`, `services/benchmark_lab_service.py`).
  - Scheduled re-evaluation with approve-to-promote (`services/model_reeval_service.py`).
- **The owner's English edits are already a dataset.**
  - A saved English edit is recorded in `edit_samples` and `translation_memory` (`services/lines_service.py`).
  - `services/benchmark_set_builder_service.py` turns edits and approvals into translation golden sets.
- **Untouched ASR output.** `raw_transcript.py` keeps every run's output, plus the settings that run used.
- **The richer transcription harness lives outside the database.** `tools/asr_bench.py` (with `tools/asr_bench_recipes.py`) varies backend, model, VAD, beam, prompt and alignment against a reference SRT and writes a Markdown report.

## Gaps

1. **Lab transcription arms vary only the Whisper model.** The transcription branch of `check_config` returns `{"engine": "whisper", "model": size}`.
2. **Lab transcription cases don't run at real settings.** `benchmark.run_transcription_case` calls `transcribe_for_timing` with its function defaults, so cases run with `min_silence_duration_ms=2000` where titles use 300, and with no initial prompt. A Lab transcription score is therefore not the score a real title would get.
3. **No way to add audio test cases.** `create_case` takes translation text only; no route or screen registers an audio file, so transcription cases can't be made at all. Nothing builds them from corrected source lines either, and each title's raw-to-edited CER is free to compute but never computed.
4. **Nothing sweeps settings.** Auto-tune (`services/autotune_service.py`) ranks min-silence candidates by long-line count, because it has no reference text.
5. **Results can't be cleaned up or taken out.**
   - A case can be deleted (Golden Sets card), but not edited or switched off, and a deleted case leaves older runs scored on a different set from newer ones.
   - There is no route or button to delete a bad run.
   - There is no export of runs or results.
6. **The scores don't measure everything a subtitle needs.**
   - The built-in CER counts punctuation and full-width/half-width differences as errors, while the jiwer path strips punctuation, so the same output scores differently depending on whether jiwer is installed.
   - Transcription is scored on joined text only: line timing and line splits are not scored in the Lab (`tools/asr_bench.py` measures them, outside the database).
   - Translation cases are translated alone (`translate_batch([source])` in `services/benchmark_case_service.py`), with no neighbouring lines, glossary, style profile or gender hints, so context-window and glossary settings can't be tested.
   - Translation is scored only from a perfect transcript; nothing measures the whole chain (audio → transcript → translation).
   - One reference translation per case: chrF marks down a valid wording that differs from it.
7. **Translation leftovers:**
   - The context and batch defaults disagree: 10/6/30 in `engine_backends/engine_registry.py` against 6/3/20 in `services/workspace_job_service.py` and `bulk_translate.py`.
   - Temperature is never set.
   - Judge scores live in `app_settings` (`benchmark_judge.<run id>`), not beside the results.
   - Translation-memory matches reach Review as suggestions and never reach the prompt.

## Method

1. **Reference sets.**
   - **Main source: the owner's clip set,** uploaded in the app. On the Benchmark Lab page the owner picks or drops audio files in the browser and fills a table with each clip's transcript, optional translation, language, title and tags (text can be pasted for many rows). No folder path is typed and no manifest file is needed. **Optional extra:** a manifest, one JSON line per clip, fills the same table (and the import accepts it too): `{"audio": "clip01.wav", "language": "zh", "transcript": "…", "translation": "…", "title": "…", "tags": ["music", "whisper"]}`. Clips from one scene keep their order so translation can be tested with context. `start`/`end` per line (or an SRT beside the clip) are optional and enable timing scores. What a line can test depends on what it has:

     | Clip has | Tests |
     |---|---|
     | transcript and translation (best) | transcription, translation, and the whole chain |
     | transcript only | transcription |
     | translation only | the whole chain only (translation alone needs a source text) |

   - **Top-up:** lines corrected in Review (source text against `raw_transcript`, and `edit_samples` for English).
   - Size before a result counts: 50–100 clips of 5–30 s (about 20–30 minutes), and 100 or more translation lines, from at least 3 titles. Mix what is actually worked on: quiet dialogue, music, whispering or ASMR, crosstalk.
   - References are written exactly as spoken; a wrong reference counts against every setup.
   - Clips used as worked examples in the translation prompt (step T4) are never also test cases.
2. **Split by title**, 70% for tuning and 30% for hold-out, so no title is in both halves. The hold-out is only used to confirm a winner, never to pick one.
3. **Measure the noise.** Run the default setup twice; the gap is the noise floor.
4. **Sweep one setting at a time** (coordinate descent) from the current default, keeping each winner before moving on.
5. **Adopt a change only if it beats the default on the hold-out by the margin:**
   - Transcription: at least 2 CER points and at least twice the measured noise.
   - Translation: at least 1.5 chrF points, with the judge score no lower.
   - **Results are kept separate per language** and never averaged across Chinese, Japanese and Korean. Each language gets its own scores and its own recommendation, because the best setting can differ: the earlier tests found beam 1 cost large-v3 1.5 CER on Japanese but not on Chinese.
   - Results are also shown per tag (music, whisper, crosstalk…), so a setting that helps one kind of audio and hurts another is promoted only for that kind. A tag group with fewer than about 15 clips is shown as too small to decide on.
   - Otherwise the default stays.
6. **Compare like with like.** Two runs are compared only on the clips both scored; excluding or deleting a clip never skews a comparison.
7. **Promote only on the owner's approval**, per content type and language, as `model_reeval_service` promotes a model. The approval is recorded.
8. **Repeat.** New clips and corrections join the sets; after an app update or a provider model change, the baseline is re-run so a drop is caught.

## Starting numbers and sweep ranges

"Start at" is the best evidence available today. All of it comes from `docs/asr-experiments.md`: CPU, one run per cell, and no real drama audio.

| Knob | Start at | Sweep | Evidence |
|---|---|---|---|
| Whisper model | large-v3 | large-v3, turbo; Japanese adds kotoba-v2 and anime-whisper | turbo matched large-v3 on streamer audio |
| `beam_size` | 5 (1 when speed matters) | 1, 3, 5 | Beam 1 was 7-35% faster at about the same accuracy, except large-v3 Japanese (+1.5 CER) |
| `min_silence_ms` | 300 | 200, 300, 500, 800 | 300 was best on FLEURS |
| `vad_threshold` | 0.5; 0.35 (Sensitive preset) for whispered or ASMR audio | 0.35, 0.45, 0.5, 0.6 | 0.7 was 3.2 points worse |
| Initial prompt | short, series glossary names only | off, names, names plus novel excerpt | Unrelated names added 4.6 CER on bad audio |
| `hallucination_silence_sec` | 0 | 0, 1, 2 | Never swept |
| no_speech / log_prob (Difficult audio) | 0.8 / −1.5 | 0.6 / −1.0 against 0.8 / −1.5 | Never swept |
| Repeat guard | off; on for loop-prone audio | off, on | Measured only before the 2026-10-07 anti-loop change |
| Vocal separation | off, unless the music is heavy and the speech clean | off, on | Added 8 CER at 0 dB noise |
| Qwen context pad | 2.0 s | 1, 2, 3 s | Chinese CER went from 4.85 to 3.78 |
| Translation batch size | 30 (60 for large models) | 15, 30, 60 | Unmeasured |
| Context lines before / after | 10 / 6 | 3/1, 6/3, 10/6, 20/10 | Unmeasured; the defaults disagree |
| Reflect pass | on for final passes | off, on | Unmeasured |
| Translation-memory examples in the prompt | off | off, on | New (step T4) |
| Temperature | provider default | default, 0.3 | Never set (step T4) |

## Build steps

One PR each, in this order.

### A1. Clip-set import and clean-up controls

- **Upload first, in the app** (built): the Lab's "Your own clips" section takes audio files by multi-select or drag and drop, with a table for transcript, optional translation, language, title and tags, and a paste box for many rows. A folder plus manifest (format in Method) is an optional extra the same import also accepts. Clips are copied into `db.BENCHMARK_DIR` under generated names; each clip makes a transcription case and, when it has a translation, a translation case, with title, tags and scene order kept. Caps: 250 clips and 1 GB per save, 50 MB per clip (`services/benchmark_clip_service.py`); paths and file names are never returned in API responses.
- **Language per clip, not per import.** `language` (`zh`, `ja` or `ko`) is required on every manifest line, so one clip set can mix all three. Today's text import takes one `source_language` for the whole import; the clip import does not.
- **One text normalisation for both scorers**, so results from the first import are comparable across PCs: drop punctuation, fold full-width to half-width, ignore spacing; keep Traditional→Simplified for Chinese. Today the built-in CER counts punctuation while the jiwer path strips it. (OCR keeps punctuation, per the 2026-09-30 decision.)
- **Mismatch check on import.** Kana in a transcript points to Japanese and Hangul to Korean. When the text doesn't match the label, the import warns and names the clip rather than silently relabelling it.
- **Exclude or include per case.** Safer than deleting: a suspect clip drops out of scoring and can be brought back later.
- **Edit a reference in place.** Fix a case's transcript or translation without deleting it, so its past results stay tied to it. The edit is recorded (when and what changed), so a score from before the fix can be told apart from one after it.
- **Delete a run** from the Runs list, with a confirmation, for runs that crashed, used the wrong settings or were otherwise bad. Its results go with it (`ON DELETE CASCADE`). Deleting a case stays as it is today.
- **Compare only the clips both runs share.** Older runs still include a clip that was later removed, excluded or edited. The Arena view, run comparisons and the sweep's "beats the default" decision score only the cases both runs have, with the same reference version, and show how many were left out.
- **Export** a run or a comparison as CSV or JSON: the setup, the scores and each case's output.
- New routes are `local_only()` like the other Lab writes; regenerate `docs/route-permissions.md` with `python tools/route_table.py --write`. A new column (excluded flag, tags) is an `ALTER TABLE ... ADD COLUMN` in `init_db`, listed in `_INIT_DB_MIGRATED_COLUMNS` in `tests/test_db.py`.

### A2. Lab transcription runs at real settings, with more models

- Extend the transcription branch of `check_config` and `benchmark.run_transcription_case` to take the per-title options that `services/transcribe_options_service.py` validates: beam, min silence, VAD threshold, sensitivity, repeat guard, difficult audio, hallucination silence, initial prompt, vocal separation.
- A missing option takes the app's default, not `transcribe_for_timing`'s.
- Offer Qwen3-ASR and anime-whisper as Lab models.
- Record the options in `benchmark_sessions.context_settings`, plus the app version and the model's identity (Whisper size or folder, Ollama digest, API model name), so a result can be traced and re-run.

### A3. Line timing scores and timing settings

- **Scored only for clips with reference timings.** Timings must be accurate, or the app is tuned toward the mistakes.
- **Timing references are made in Review's waveform**, not in a second editor in the Lab. Review already shows the waveform (`frontend/src/pages/workspace/stages/review/Waveform.tsx`, peaks from `/api/dramas/{id}/peaks`) with draggable line edges and nudge keys. The owner fixes a title's timing there, then adds that title's lines, with their start and end times and audio, to a test set (the A5 path). Clips that need timing are imported as a title first, timed in Review, then added.
- **Measures:** median start and end error (ms); share of lines whose start and end are within 0.3 s; lines wrongly merged or split; missed lines; lines placed in silence. Reuse what `tools/asr_bench.py` already measures (`count_in_silence` and the merged/missed counts).
- **Settings the sweep can tune for timing:** min silence and VAD threshold (where lines break); Whisper's speech pad (not passed today, so the library default of about 0.4 s applies); "Refine line timing" with the forced aligner, on or off; the split limits of 8 s and 40 characters (`segment_splitting.py`).
- **Check the timing-flag thresholds** in `timing_drift.py` (`EARLY_START_S` and `LATE_END_S`, both 0.7 s) against the reference timings, instead of leaving them as guesses.
- A timing win is judged on its own margin (to be set from the measured noise) and never traded silently for a worse CER.

### A4. Sweep job

- A background job (`jobs/` launch pattern) runs the coordinate-descent sweep over the range table for one stage and one set, repeating the default to measure noise.
- Runs one test at a time, like `_run_plan`; the loaded Whisper model stays cached between passes, and passes are grouped by model so each model loads once. It is marked GPU-touching, so it waits for other GPU work.
- **A time estimate before it starts**, from the total clip length, the models in the sweep and the number of passes (CPU or GPU). For paid engines it also shows the cost, as the Lab's `/estimate` already does, and it keeps to the Lab's cost cap.
- **Stop and resume.** A long sweep can be cancelled and picked up later without redoing finished passes. Every finished pass is already saved as a run, so resuming skips the setups that have a finished run on the same cases.
- **Winners are never applied automatically.** The recommendation shows the hold-out result, the margin rule and the per-tag breakdown. The app applies it only when the owner approves, and records the approval, as `model_reeval_service` does for models (`model_decisions`).
- UI: a card in `frontend/src/pages/benchmark/`.

### A5. References from corrections and auto-tune

- Build transcription cases from lines whose source text differs from `raw_transcript` (audio clipped per line), beside `benchmark_set_builder_service.py`.
- Show each title's raw-to-edited CER, reusing `benchmark_scoring_service.error_rate`.
- When a title has reference lines, `autotune_service` scores its min-silence candidates by CER instead of long-line count.

### T1. Translation tested the way titles are translated

- Translation cases from one scene run as a batch in order, with the configured context window, and with a chosen series' glossary, style profile and hints, so these settings can be tested.
- The sweep covers model, batch size, context window, reflect and style preset.
- `workspace_job_service` and `bulk_translate` take the registry defaults instead of their own 6/3/20, unless the sweep says otherwise.

### T2. Judge scores and your own picks

- Store judge scores in `benchmark_results` (ADD COLUMN, listed in `_INIT_DB_MIGRATED_COLUMNS`).
- **Blind A/B:** the owner picks the better of two outputs without seeing which setup made them; the picks are stored and shown beside chrF and the judge. They also check whether the judge agrees with the owner.
- Not now: more than one reference translation per case. Writing a second translation for every clip costs the owner more than it gains, and the blind A/B picks cover most of what it would fix.

### T3. End-to-end score

- Translate each clip's own transcription output (not the reference transcript) and score it against the reference translation, so a transcription setting is judged by what reaches the viewer.

### T4. Translation-memory examples and temperature

- Sweep knob, off by default: the top matches at 0.85 or above from `translation_memory.py` as few-shot examples through `translation_guide.build_style_guidelines`. On by default only if the sweep shows a win.
- Temperature as a sweep knob in `engine_backends/`; a user setting only if a measured win needs one, with the sentence CLAUDE.md asks for.

## Where results are kept

- **Runs and results.** Every run is saved in `library.db`: `benchmark_sessions` (the setup and totals) and `benchmark_results` (each case's output and score). Today the judge scores are the exception, in `app_settings`; T2 moves them in with the results.
- **Clips.** Clips go in `library/benchmark_cases/`, the folder transcription cases already use (`db.BENCHMARK_DIR`). No transcription case can be added today, so every run so far is translation only.
- **Backups.** A full library backup with media copies both the database tables and the clip folder, and a restore brings `benchmark_cases/` back. A per-user backup leaves the benchmark tables empty by design (`services/library_user_backup_service.py`: "this PC's test set"). A database-only backup has the tables but not the clips.
- **Export.** None today. Step A1 adds CSV and JSON export.

## Later, only if the sweeps hit a ceiling

Real fine-tuning: LoRA on Whisper or a local translation model, from a JSONL export of the clip set and `edit_samples`, on a GPU. Speaker-label accuracy (diarization) is also not scored by any step here.

## Verification

- **Each step:**
  - Run the area's tests (`tests/test_benchmark*.py`, `tests/test_autotune*.py` and so on) plus the guard set (`python tools/check_change.py`).
  - Frontend steps also run `npx tsc -b && npx vitest run`.
  - Tests are mocked and use `isolated_db`.
- **Real runs:** GPU Whisper and paid keys are owed by the owner. A1, A2 and A4 each come with a real-run checklist: import a small clip set, run the default twice and one Arena comparison, run a short sweep, stop and resume it, and confirm the estimate, the recommendation and the hold-out numbers look sane.
