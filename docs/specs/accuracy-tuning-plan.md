# Accuracy tuning: learn from the owner's corrections, pick settings by measurement (plan)

Status: **plan, not started** (owner decision 2026-10-14). Scope: transcription first, then translation. "Training" here means tuning settings and feeding corrections back into prompts; no model weights change. Drafted against `baihe-subtitler` at 5caf079; re-check each line reference before building.

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
3. **No transcription reference cases come from corrected source lines.** The raw-to-edited CER of each title is free to compute and nothing computes it.
4. **Nothing sweeps settings.** Auto-tune (`services/autotune_service.py`) ranks min-silence candidates by long-line count, because it has no reference text.
5. **Translation leftovers:**
   - The context and batch defaults disagree: 10/6/30 in `engine_backends/engine_registry.py` against 6/3/20 in `services/workspace_job_service.py` and `bulk_translate.py`.
   - Temperature is never set.
   - Judge scores live in `app_settings` (`benchmark_judge.<run id>`), not beside the results.
   - Translation-memory matches reach Review as suggestions and never reach the prompt.

## Method

1. **Reference sets come from the owner's own work.**
   - Transcription: lines whose source text was corrected, with the audio clipped to the line.
   - Translation: `edit_samples` plus approved lines.
   - Minimum before a result counts: about 30 minutes or 200 lines of transcription, and 100 or more translation lines, from at least 3 titles each.
   - Mix the content types actually worked on: drama, ASMR and streamer.
2. **Split by title**, 70% for tuning and 30% for hold-out, so no title is in both halves.
3. **Measure the noise.** Run the default config twice; the gap between the runs is the noise floor.
4. **Sweep one setting at a time** (coordinate descent) from the current default, keeping each winner before moving to the next setting.
5. **Adopt a change only if it beats the default on the hold-out by the margin:**
   - Transcription: at least 2 CER points (the streamer runs showed about 2 points of noise) and at least twice the measured noise.
   - Translation: at least 1.5 chrF points, with the judge score no lower.
   - Otherwise the default stays.
6. **Promote only on the owner's approval**, per content type and language, the way `model_reeval_service` promotes a model.
7. **Repeat** every few reviewed titles: new corrections join the sets, and the existing re-evaluation schedule re-checks the winners.

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
| Translation-memory examples in the prompt | off | off, on | New (step T3) |
| Temperature | provider default | default, 0.3 | Never set (step T4) |

## Build steps

One PR each, in this order.

### A1. Lab transcription runs at real settings

- Extend the transcription branch of `check_config` and `benchmark.run_transcription_case` to take the per-title options that `services/transcribe_options_service.py` validates: beam, min silence, VAD threshold, sensitivity, repeat guard, difficult audio, hallucination silence and initial prompt.
- When an option is missing, default to the app's own defaults, not to the function defaults of `transcribe_for_timing`.
- Record the options used in `benchmark_sessions.context_settings`.

### A2. Transcription reference cases from corrections

- Add a set-builder path beside `benchmark_set_builder_service.py`:
  - find lines whose source text differs from `raw_transcript`;
  - clip each line's audio into `db.BENCHMARK_DIR`;
  - create a transcription case from each clip.
- Show each title's raw-to-edited CER, reusing `benchmark_scoring_service.error_rate`.
- A new route declares `require_permission(...)`; regenerate its row with `python tools/route_table.py --write`.

### A3. Sweep job

- Add a background job, launched through `jobs/`, that runs the coordinate-descent sweep for one stage and one golden set over the range table above. It repeats the default run to measure noise.
- It runs configs one after another, so `MAX_CONFIGS` does not limit it.
- Results go to `benchmark_sessions` and `benchmark_results`.
- The recommendation shows the hold-out result and the margin rule, with an Approve action modelled on `model_reeval_service`.
- UI: a card in `frontend/src/pages/benchmark/`.

### A4. Auto-tune uses references

When a title has reference lines, `autotune_service` scores its min-silence candidates by CER instead of by long-line count.

### T1. Translation sweep and one set of defaults

- The sweep covers batch size, context window, reflect, style preset and model.
- `workspace_job_service` and `bulk_translate` take the registry defaults instead of their own 6/3/20, unless the sweep says otherwise.

### T2. Judge scores beside the results

- Store judge scores in `benchmark_results`: an `ALTER TABLE ... ADD COLUMN` in `init_db`, with the column listed in `_INIT_DB_MIGRATED_COLUMNS` in `tests/test_db.py`.
- The sweep then ranks by chrF and judge together.

### T3. Translation-memory examples in the prompt

- A sweep knob, off by default: pass the top matches at 0.85 similarity or above from `translation_memory.py` as few-shot examples through `translation_guide.build_style_guidelines`.
- It becomes the default only if the T1 sweep shows a win.

### T4. Temperature as a sweep knob

- Add temperature as a sweep knob in `engine_backends/`.
- It becomes a user setting only if a measured win needs one, with the sentence that CLAUDE.md asks for in that PR.

### Later, only if the sweeps hit a ceiling

Real fine-tuning: LoRA on Whisper or on a local translation model. It needs a JSONL export of `edit_samples` and of the transcription cases, and a GPU.

## Verification

- **Each step:**
  - Run the area's tests (`tests/test_benchmark*.py`, `tests/test_autotune*.py` and so on) plus the guard set (`python tools/check_change.py`).
  - Frontend steps also run `npx tsc -b && npx vitest run`.
  - Tests are mocked and use `isolated_db`.
- **Real runs:** GPU Whisper and paid keys are owed by the owner. A2 and A3 each come with a real-run checklist: build a set from one corrected title, run a sweep, and confirm the recommendation and the hold-out numbers look sane.
