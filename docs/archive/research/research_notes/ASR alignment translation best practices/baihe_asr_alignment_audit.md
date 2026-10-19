# Baihe Subtitler: current ASR, VAD, hallucination, separation, alignment and timing implementation (audit)

Scope: repository `/home/user/U00` at `origin/baihe-subtitler` HEAD `a4308e32` (2026-10-11). Read-only audit. Every source below is a repo file and line (links are local paths). Tags on each finding:
- **[READ]**: read in code or docs at the line cited.
- **[RAN]**: verified by running the real function offline. Script: `/tmp/claude-0/-home-user-U00/e1f5a28a-5aba-5e80-80a7-d134605804c8/scratchpad/research-asr/experiments.py`. Full output: `experiments_output.txt` in the same folder. Command: `PYTHONDONTWRITEBYTECODE=1 python experiments.py /home/user/U00` on Python 3.13.16 with numpy 2.5.3. No models were loaded and faster-whisper, torch and qwen-asr are not installed here; the Qwen and aligner models were replaced with fakes.
- **[INFERRED]**: my reasoning from the code, not executed.

Doc drift from the code is called out as **DOC DRIFT**.

---

## 1. ASR backends, models, backend choice, device/compute, decoding parameters, prompt, language

### Takeaway
There are four selectable backends: `whisper`, `qwen3_asr`, `qwen3_asr_vad` and `qwen3_asr_long`. Groq is a separate flag on the Whisper path, not a backend. The defaults are decided per title:
- Backend: a zh or ja title that never saved a choice gets `qwen3_asr_long` when qwen-asr, torch and faster-whisper are all installed. Every other title gets `whisper` with `large-v3-turbo`.
- Whisper decoding: beam 5, `condition_on_previous_text=False`, word timestamps always on, language forced to the title's language, and faster-whisper's own temperature fallback and thresholds left untouched.
- Prompts: Qwen3-ASR gets no prompt or context. Whisper gets a glossary-plus-novel `initial_prompt`.

### Cited Findings
**Backend registry and choice**
- Choices are `("whisper", "qwen3_asr", "qwen3_asr_vad", "qwen3_asr_long")`. `moss_td` is listed as removed — [services/asr_options_service.py:40-43](/home/user/U00/services/asr_options_service.py) [READ]
- Registry `BACKENDS`/`get_backend` — [asr_backend.py:546-560](/home/user/U00/asr_backend.py) [READ]
- `stored_asr_backend` resolves the backend in this order [READ] — [services/asr_options_service.py:213-228](/home/user/U00/services/asr_options_service.py), `QWEN_LONG_DEFAULT_LANGUAGES = ("zh","ja")` at :49:
  - A saved choice wins, unless it is a removed backend.
  - Otherwise `qwen3_asr_long` when `use_groq` is off, `source_language` is in `("zh","ja")`, and `find_spec` succeeds for `qwen_asr`, `torch` and `faster_whisper`.
  - Otherwise `whisper`.
  - The default therefore depends on which packages are installed on the machine; it is not a fixed setting.
- `qwen3_asr` only applies in `transcript_mode == "whisper"`. `qwen3_forced_align` only applies in `have_transcript` mode and is refused in whisper mode — [services/transcribe_service.py:414-428](/home/user/U00/services/transcribe_service.py) [READ]
- A title with no stored `transcript_mode` is treated as `"have_transcript"` — [services/transcribe_service.py:519](/home/user/U00/services/transcribe_service.py) [READ]

**Whisper models**
- `WHISPER_MODELS` = small, medium, large-v3, large-v3-turbo. `DEFAULT_WHISPER_SIZE = "large-v3-turbo"` — [whisper_models.py:16-22](/home/user/U00/whisper_models.py) [READ]
- `_DEFAULT_TUNING["whisper_size"]` and `default_whisper_size()` return it on both CPU and GPU — [services/transcribe_service.py:69-81, 683-688](/home/user/U00/services/transcribe_service.py) [READ]
- A stored size outside the allowed set falls back to the default with a warning — :691-706 [READ]
- The allowed set also includes tiny, base and large-v1/v2 — `_MODEL_DOWNLOAD_SIZES` at [services/transcribe_pipeline.py:75-77](/home/user/U00/services/transcribe_pipeline.py) [READ]

**Device and compute type**
- Whisper: GPU uses `WhisperModel(..., device="cuda", compute_type="float16")`. It falls back to CPU `int8` on a non-network load error — [whisper_models.py:264-281](/home/user/U00/whisper_models.py) [READ]
- Whisper GPU failure at inference: CUDA errors only surface during the first decode, so `transcribe_for_timing` catches them there and retries on CPU — [core.py:578-595](/home/user/U00/core.py) [READ]
- Qwen3-ASR loads as `Qwen/Qwen3-ASR-1.7B` with `dtype=torch.bfloat16`, `device_map` `cuda:0` or `cpu`, and **`max_new_tokens=256`**. It falls back to CPU on a GPU error — [asr_backend.py:184-244](/home/user/U00/asr_backend.py) [READ]
- The Qwen3 aligner loads as `Qwen/Qwen3-ForcedAligner-0.6B` in bfloat16 — [forced_align.py:76, 107-120](/home/user/U00/forced_align.py) [READ]
- `release_gpu_models()` drops the Whisper, Qwen and aligner caches after each stage — [whisper_models.py:25-49](/home/user/U00/whisper_models.py), called at [services/transcribe_pipeline.py:632](/home/user/U00/services/transcribe_pipeline.py) [READ]
- The transcription job runs as a spawned child process — [services/transcribe_service.py:598-618](/home/user/U00/services/transcribe_service.py) [READ]
- `diagnostics_torch.py` only covers install diagnostics (GPU/torch variant, driver, pip pins); it does not choose a runtime device — [diagnostics_torch.py:17-191](/home/user/U00/diagnostics_torch.py) (function list) [READ]

**Whisper decode kwargs**, built in `core.transcribe_for_timing` ([core.py:539-550](/home/user/U00/core.py)) [READ]:
- `language` is the title's source language: zh, ja or ko. There is no auto-detect on the normal path.
- `vad_filter=True` with `vad_parameters={"min_silence_duration_ms", "threshold"}`.
- `beam_size` comes from the title.
- `word_timestamps=True`, always.
- `decode_kwargs(...)` supplies the anti-loop settings, at minimum `condition_on_previous_text=False` ([core.py:305](/home/user/U00/core.py)).
- `initial_prompt` is passed when it is non-empty.
- `hallucination_silence_threshold` is passed only when the title's value is non-zero, fast mode is off and faster-whisper accepts the kwarg.
- Not set anywhere: `temperature`, `compression_ratio_threshold`, `log_prob_threshold`, `no_speech_threshold`, `hotwords`, `patience`, `length_penalty`, `chunk_length`. A grep for these names outside tests and translation code found nothing [READ]. So faster-whisper's own defaults apply, including the temperature-fallback ladder.

**Beam size and fast mode**
- Beam default 5, allowed range 1-10 — [services/transcribe_service.py:71, 360-363](/home/user/U00/services/transcribe_service.py); column `beam_size INTEGER DEFAULT 5` at [db.py:1229](/home/user/U00/db.py) [READ]
- Fast mode uses `BatchedInferencePipeline` (`whisper_fast_mode`, default off) — [core.py:572-576](/home/user/U00/core.py) [READ]

**Initial prompt**
- `build_auto_initial_prompt` [READ] — [services/transcribe_service.py:228-251](/home/user/U00/services/transcribe_service.py), [core.py:317-349, 883-910](/home/user/U00/core.py):
  - Up to 40 series-glossary terms plus their aliases, joined with "、" and ended with "。".
  - Then the user's extra names.
  - Then an excerpt of up to 800 characters from `raw_novel_context.txt`.
  - The combination is capped at 900 characters, names first.
- A non-empty `initial_prompt` from the user replaces the automatic prompt entirely — :254-261 [READ]
- Qwen3-ASR gets no prompt or context. The calls are `model.transcribe(audio=..., language=...)` only — [asr_backend.py:328-329, 342, 508](/home/user/U00/asr_backend.py) [READ]
- Groq gets no prompt either. Only `model`, `language`, `response_format=verbose_json` and `timestamp_granularities[]=segment` are sent — [core.py:636-641](/home/user/U00/core.py) [READ]

**Groq**
- Uses `whisper-large-v3-turbo` with `timeout=600` — [core.py:598-599, 636](/home/user/U00/core.py) [READ]
- The output segments have no word timings, and Groq output is not passed through `filter_hallucinated_segments` — [core.py:656-660](/home/user/U00/core.py) [READ]

**`qwen3_asr` backend**
- It keeps Whisper's segment times and replaces only the text. A segment longer than 300 s keeps Whisper's text (`SEGMENT_DURATION_WARNING_SECONDS`) — [asr_backend.py:58, 259-312](/home/user/U00/asr_backend.py) [READ]
- Batching only applies when qwen-asr 0.0.6 is installed. A batch that fails or returns the wrong number of results is redone one segment at a time — :137-161, 314-348 [READ]
- Batch size is a global setting, 1-16, default 1 — [services/asr_options_service.py:32-59](/home/user/U00/services/asr_options_service.py) [READ]
- **`qwen3_asr` segments come back without `"words"`** (:309 rebuilds the dicts with start, end and text only). So lines from Whisper+Qwen store no word timings [READ, INFERRED].

**Language detection and mixed languages**
- Mixed languages is a global setting, default off — [services/asr_options_service.py:71-77](/home/user/U00/services/asr_options_service.py) [READ]
- On the Whisper path (not Groq), mixed languages replaces the normal decode with `mixed_language.transcribe_mixed_whisper` [READ] — [services/transcribe_pipeline.py:369-370, 472-481](/home/user/U00/services/transcribe_pipeline.py); [mixed_language.py:123-191](/home/user/U00/mixed_language.py):
  - Speech spans come from Silero with `vad_segments` defaults, not the title's tuning.
  - Language is detected per span with `model.detect_language` and limited to zh, ja, ko and en.
  - Each span is decoded with `vad_filter=False`, `word_timestamps=True` and the same anti-loop kwargs.
  - The words are dropped, so mixed Whisper lines have no word timings.
  - `hallucination_silence_threshold` is not passed.
- `qwen3_asr_vad` and `qwen3_asr_long` with mixed languages: Qwen detects the language per span, with a 0.3 s merge gap. The long backend then behaves like the short one, and forced-aligner refine is off — [asr_backend.py:428-450, 495-528](/home/user/U00/asr_backend.py) [READ]

### Inferences
- [INFERRED] `max_new_tokens=256` may cut text short ([asr_backend.py:215](/home/user/U00/asr_backend.py)). `qwen3_asr_long` sends windows of up to 30 s ([asr_backend.py:76](/home/user/U00/asr_backend.py)). Fast CJK speech at 6-8 characters per second gives 180-240 characters in 30 s, close to that cap; Chinese runs about one token per character or fewer. With `qwen3_asr` over Whisper segments of up to 300 s, the risk is higher. No test or doc measures truncation.
- [INFERRED] **DOC DRIFT (minor):** [docs/engine-backends.md:254](/home/user/U00/docs/engine-backends.md) shows the `whisper` choice as `asr_backend.WhisperBackend -> core.transcribe_for_timing`. In the code:
  - The pipeline calls `transcribe_for_timing` directly ([services/transcribe_pipeline.py:483](/home/user/U00/services/transcribe_pipeline.py)).
  - `WhisperBackend` is used only by `asr_benchmark.py:128`, and its defaults (`whisper_size="medium"`, `min_silence_duration_ms=2000`, [asr_backend.py:172-174](/home/user/U00/asr_backend.py)) are stale.
  - The signature defaults of `transcribe_for_timing` (`model_size="medium"`, `min_silence_duration_ms=2000`, [core.py:459-462](/home/user/U00/core.py)) are also not the app's defaults. Every caller passes its own values.
- [READ] `services/workspace_job_service.run_transcribe_job` (:216) has no caller outside tests. The pipeline's docstring still calls it the reference ([services/transcribe_pipeline.py:242-244](/home/user/U00/services/transcribe_pipeline.py)). It looks like dead code (pre-existing).

### Gaps
- I could not check faster-whisper's own defaults here because the package is not installed: temperature ladder, compression_ratio 2.4, log_prob -1.0, no_speech 0.6, and the VadOptions defaults. The values in this note come from general knowledge, not from this environment.
- No GPU numbers exist anywhere in the repo docs.

---

## 2. VAD and silence/hallucination handling, including "Check timing"

### Takeaway
Silero VAD is the only detector, plus an opt-in ASMR ONNX detector for the Qwen backends. It runs with five different parameter sets depending on the path.

The hallucination silence threshold is off by default (0). Old titles stored at 2.0 were reset to 0 once.

The loop guards are:
- `condition_on_previous_text=False`, always.
- A post-filter that collapses 4 or more identical consecutive segments.
- A filter that drops isolated stock credit phrases.
- A per-title repeat-guard toggle (`no_repeat_ngram_size=3`, `repetition_penalty=1.1`), off by default.

"Check timing" compares lines against a separate Silero scan at threshold 0.3, with fixed tolerances of 0.7 s, 40% speech and 0.1 s overlap.

### Cited Findings
**VAD parameter sets in use** [READ]:
| Path | threshold | min silence | min speech | pad | merge gap | max span | Source |
|---|---|---|---|---|---|---|---|
| Whisper normal (`vad_filter`) | title `vad_threshold` (0.5; preset "sensitive" gives 0.35) | title `min_silence_ms` (default 300) | faster-whisper default | faster-whisper default | — | — | [core.py:540-542](/home/user/U00/core.py); [services/transcribe_service.py:72-73](/home/user/U00/services/transcribe_service.py) |
| Whisper with "Split lines by sentences" | same | **2000 ms** (title value ignored) | fw default | fw default | — | — | [asr_backend.py:86](/home/user/U00/asr_backend.py); [services/transcribe_pipeline.py:271-272, 488-489](/home/user/U00/services/transcribe_pipeline.py) |
| `qwen3_asr_vad`, mixed-language Whisper | 0.5 | 300 | 250 ms | 100 ms | 1.0 s (0.3 s when mixed) | 15 s, quietest cut in last 3 s | [vad_segments.py:62-64, 113-114](/home/user/U00/vad_segments.py); [asr_backend.py:63-65](/home/user/U00/asr_backend.py) |
| `qwen3_asr_long` | 0.35 | 300 | 0 | 300 ms | 3.0 s | 30 s, quietest cut in last 8 s | [asr_backend.py:74-77](/home/user/U00/asr_backend.py) |
| Check timing and gap scan | 0.3 | 300 | 250 | 0 | — | 600 s decode chunks | [services/speech_coverage_service.py:33, 49, 168-169](/home/user/U00/services/speech_coverage_service.py) |
- The Qwen VAD backends ignore the title's `vad_threshold` and `min_silence_ms` (comment at [asr_backend.py:413-414](/home/user/U00/asr_backend.py)). So does mixed-language Whisper ([mixed_language.py:159-162](/home/user/U00/mixed_language.py)) [READ].
- Qwen context padding: `CONTEXT_PAD_S = 2.0`. The model hears up to 2 s of the surrounding silence, but the line keeps its span's times — [asr_backend.py:63](/home/user/U00/asr_backend.py), [vad_segments.py:135-149](/home/user/U00/vad_segments.py) [READ]
- Setting ranges [READ]:
  - `min_silence_ms`: 100-3000 — [core.py:294-295](/home/user/U00/core.py)
  - `vad_threshold`: 0.1-0.9 — [services/transcribe_service.py:370-373](/home/user/U00/services/transcribe_service.py)
- "Sensitive" preset: it changes the threshold to 0.35 only when the stored threshold equals 0.5. It also removes the repeat penalties unless the guard toggle is on — [sensitivity_preset.py:16-54](/home/user/U00/sensitivity_preset.py) [READ]. [RAN] E10 output:
  - `(None,"normal") → 0.5`
  - `(0.5,"sensitive") → 0.35`
  - `(0.6,"sensitive") → 0.6`
  - Sensitive with guard off → `{"condition_on_previous_text": false}`
  - Guard on → `{"condition_on_previous_text": false, "no_repeat_ngram_size": 3, "repetition_penalty": 1.1}`
- ASMR detector: hysteresis with start 0.5 and stop 0.35, 30 s windows. `auto` selects it only for a Japanese title whose `media_type` is asmr and whose model is downloaded — [asmr_vad.py:24-33](/home/user/U00/asmr_vad.py); [services/asr_options_service.py:89-107](/home/user/U00/services/asr_options_service.py) [READ]

**Hallucination silence threshold**
- `DEFAULT_HALLUCINATION_SILENCE_SEC = 0.0`. The comment says it is off because it "dropped real fast or quiet CJK lines" — [core.py:311-314](/home/user/U00/core.py) [READ]
- Column default `REAL DEFAULT 0` — [db.py:1230](/home/user/U00/db.py) [READ]
- One-time migration: `UPDATE dramas SET hallucination_silence_sec = 0 WHERE hallucination_silence_sec = 2`, run when the `whisper_repeat_guard` column did not exist yet — [db.py:1265-1266](/home/user/U00/db.py) [READ]
- `create_drama` sets 0 because older databases have a column default of 2 — [db.py:2250](/home/user/U00/db.py) [READ]
- Backups restored from before the guard column get the same reset through `sensitivity_preset.older_row` — [sensitivity_preset.py:57-64](/home/user/U00/sensitivity_preset.py). [RAN] E10: `older_row({"hallucination_silence_sec": 2}) → 0`, while `{..., "whisper_repeat_guard": 0} → 2`.
- Valid values: 0, or 0.5-10 — [services/transcribe_service.py:378-382](/home/user/U00/services/transcribe_service.py) [READ]
- It is not passed in fast mode, with Groq, on mixed-language Whisper or on any Qwen backend — [core.py:548-550](/home/user/U00/core.py); [mixed_language.py:126-129](/home/user/U00/mixed_language.py) [READ]
- **DOC DRIFT (minor):** the UI help says "Whisper only: ignored by Qwen3-ASR, and by Fast mode" ([frontend/src/pages/workspace/stages/TranscribeStage.tsx:728](/home/user/U00/frontend/src/pages/workspace/stages/TranscribeStage.tsx)). It does not mention that mixed-language Whisper and Groq ignore it too.

**Post-decode guards**
- `filter_hallucinated_segments` [READ] — [core.py:352-445](/home/user/U00/core.py):
  - Collapses a run of 4 or more consecutive segments whose text is identical after removing whitespace.
  - Drops a segment whose whole text matches one of 10 stock-phrase regexes (Amara, "thanks for watching", 字幕由…提供, 请不吝点赞, ご視聴/ご清聴ありがとうございました, MBC뉴스, 시청해주셔서감사합니다), when it has at least `STOCK_PHRASE_ISOLATION_SEC = 5.0` s of silence on each side. A missing neighbour counts as silence.
- Where the filter runs [READ]:
  - Whisper: after `_collect` ([core.py:568-569](/home/user/U00/core.py)).
  - Mixed Whisper: [mixed_language.py:191](/home/user/U00/mixed_language.py).
  - Qwen VAD backends: on the split pieces ([asr_backend.py:471-479](/home/user/U00/asr_backend.py)).
  - Not run on Groq output or on `qwen3_asr` output.
- [RAN] E1 results:
  - 3 identical "好的" are kept; 4 are collapsed to 1.
  - 4 repeats that differ only in punctuation ("好的。"/"好的") are **not** collapsed.
  - 4 repeats that differ only in whitespace are collapsed.
  - A A B A A A A gives ["啊","啊","嗯","啊"].
  - A single 30 s segment "谢谢谢…" (22 chars) is untouched: there is no within-segment loop check.
  - "Thanks for watching!" with exactly 5.0 s gaps is dropped; with a 4.9 s gap before it, it is kept.
  - A stock phrase as the first or last segment of the file is dropped.
  - A stock phrase inside a longer line is kept.
  - `min_repeat_count=0` disables the collapse.
- Whisper `_collect` drops any segment with no letter, digit or CJK character — [core.py:557-559](/home/user/U00/core.py) [READ]
- Repeat guard [READ]:
  - Per title, default off, Developer Mode only in the UI — [core.py:306-308](/home/user/U00/core.py); [db.py:1259](/home/user/U00/db.py)
  - The comment explains why it is off: "a 3-token CJK sequence is often one common particle".
- Coverage warning: shown when text covers less than 15% of audio that is at least 30 s long — [asr_backend.py:88-122](/home/user/U00/asr_backend.py) [READ]. [RAN] E9: 29 s at 15% gives none; 30 s at 14.7% gives "Only 15% …"; 30 s at 15.0% gives none.

**Check timing** (`timing_drift.py`, `services/timing_check_service.py`) [READ]
- Constants — [timing_drift.py:20-38](/home/user/U00/timing_drift.py):
  - `EARLY_START_S = LATE_END_S = 0.7`, `MIN_SPEECH_FRACTION = 0.4`, `OVERLAP_TOLERANCE_S = 0.1`
  - `CONSERVATIVE_FACTOR = 1.5`, applied to streamer VODs: tolerances are multiplied by it and the fraction is divided by it
  - `BROKEN_AUDIO_SPEECH_FRACTION = 0.02`
  - Snap: max shift 3.0 s, pad 0.1 s, min shift 0.05 s, min duration 0.3 s
- It starts automatically after a `qwen3_asr_vad` or `qwen3_asr_long` run (`after_run`) — [services/timing_check_service.py:37, 304-323](/home/user/U00/services/timing_check_service.py)
- The scan is a chunked Silero pass on the title's **original** audio file (`audio_filename`), not on `vocals.wav` — [services/timing_check_service.py:166-169, 241-249](/home/user/U00/services/timing_check_service.py)
- [RAN] E5. Speech spans (1-3), (4-6), (10-12), (14-20); audio 60 s:
  - Line [0.29, 3.0] is flagged "Starts 0.7 s before speech" (lead 0.71). Line [0.31, 3.0] (lead 0.69) is not.
  - Line [4.0, 6.71] is flagged "Ends 0.7 s after speech".
  - Line [7, 8] is flagged "Sits in silence", with no snap suggestion.
  - Line [15.0, 17.0] against a next start of 16.9 is flagged "Overlaps the next line by 0.1 s". The overlap is 0.10000000000000142, just over the 0.1 tolerance, because of floating-point arithmetic.
  - In conservative mode the 0.71 s lead and tail are no longer flagged.
  - Broken audio: 1.1 s of speech in 60 s gives `broken_audio=True`; 1.3 s gives False.

### Inferences
- [INFERRED] **DOC DRIFT:** the "Current default 300 ms, 0.5" row in the [docs/asr-experiments.md:137](/home/user/U00/docs/asr-experiments.md) guidance table holds only for Whisper titles with "Split lines by sentences" off. Here is what new titles actually get:
  - New zh and ja titles get `split_by_sentences=1` ([services/drama_service.py:102, 151-156](/home/user/U00/services/drama_service.py)), which forces 2000 ms min silence on Whisper.
  - If qwen-asr is installed, they get `qwen3_asr_long`, which runs at threshold 0.35 with no minimum span.
  - So for the target languages the documented 300 ms / 0.5 default is the exception, not the rule. Korean titles get Whisper at 300 ms / 0.5 with sentence split off.
- [INFERRED] The 4-repeat collapse compares exact text after removing whitespace. Loops whose segments differ by punctuation or one character pass through (E1). There is no compression-ratio-style check inside a single segment beyond faster-whisper's own internal thresholds.
- [INFERRED] For `qwen3_asr_long`, the hallucination defences are `filter_hallucinated_segments` plus "a merge gap of at most 3 s keeps long pauses out of windows" ([asr_backend.py:67-73](/home/user/U00/asr_backend.py)). The 2 s context padding feeds silence to the model on purpose, which is where its comments say Qwen invents text.

### Gaps
- None of the guards is measured on real long-form drama audio with music or silence. The docs say so ([docs/asr-experiments.md:320-324](/home/user/U00/docs/asr-experiments.md)).
- The Check timing thresholds are "not tuned on recordings" ([docs/asr-experiments.md:86-87](/home/user/U00/docs/asr-experiments.md)).

---

## 3. Vocal separation

### Takeaway
Vocal separation is opt-in per title (`separate_vocals_first`, default off). Backend `auto` tries Mel-Band RoFormer (`audio-separator`) and then Demucs `htdemucs`. Audio is processed in 30 s chunks with a 1 s crossfade. The result is written as `vocals.wav` and used only as the ASR input. Check timing, re-time and diarization keep using the original audio. Dubbing uses the same separator in reverse, subtracting the vocals to get a background bed.

### Cited Findings
- Backends: `{"auto","audio_separator","demucs"}`, model `vocals_mel_band_roformer.ckpt`, chunk 30 s, overlap 1.0 s — [audio_preprocess.py:45-67](/home/user/U00/audio_preprocess.py) [READ]
- `auto` order is (audio_separator, demucs). A cancel is never retried on the other backend. A memory check runs first — [audio_preprocess.py:369-402](/home/user/U00/audio_preprocess.py) [READ]
- Demucs default model `htdemucs` — [audio_preprocess.py:498-512](/home/user/U00/audio_preprocess.py) [READ]
- With `use_gpu=False`, Demucs is forced to `device="cpu"` — [audio_preprocess.py:527](/home/user/U00/audio_preprocess.py) [READ]
- Per-title columns: `separate_vocals_first INTEGER DEFAULT 0` and `separation_backend TEXT DEFAULT 'auto'` — [db.py:1233-1234](/home/user/U00/db.py) [READ]
- The separation-backend selector is Developer Mode only — [TranscribeStage.tsx:730](/home/user/U00/frontend/src/pages/workspace/stages/TranscribeStage.tsx) [READ]
- Pipeline: the output is moved to `<drama dir>/vocals.wav` and `audio_path = separated`, which then feeds every ASR backend and the MMS realign — [services/transcribe_pipeline.py:320-361](/home/user/U00/services/transcribe_pipeline.py) [READ]. CLI `align` does the same — [cli.py:488-491](/home/user/U00/cli.py) [READ]
- Diarization uses `diarize_audio_path = _drama_audio_path(...)`, which is the original file — [services/transcribe_service.py:527](/home/user/U00/services/transcribe_service.py) [READ]
- Re-time uses `_drama_audio_path`, the original — [services/retime_service.py:51](/home/user/U00/services/retime_service.py) [READ]
- Dubbing: `dub.py` calls `audio_preprocess.extract_background` (mix minus vocals) to keep the BGM — [dub.py:555](/home/user/U00/dub.py); [audio_preprocess.py:405-436](/home/user/U00/audio_preprocess.py) [READ]
- Measured cost in the repo docs:
  - Demucs RTF 0.44 on 4 CPU cores.
  - RoFormer took 92 s for a 14.5 s clip, about RTF 4-6, on CPU.
  - Demucs helped only with music-only backgrounds (−2.3 points at 0 dB for turbo) and hurt with noise (+8.6 at 0 dB).
  - Sources: [docs/archive/asr-experiments-history.md:952-985, 1130](/home/user/U00/docs/archive/asr-experiments-history.md); [docs/asr-experiments.md:311-313](/home/user/U00/docs/asr-experiments.md) [READ]

### Inferences
- [INFERRED] After separation, the stored line times come from `vocals.wav`. The timing check judges them against the original mix, at a lower threshold (0.3). Separation keeps the sample timeline, so times should line up, but the VAD's decisions can differ between the two signals.
- [INFERRED] The app's preferred separator (RoFormer) was never benchmarked for accuracy. Only Demucs was.

### Gaps
- No GPU separation timings are recorded.
- No RoFormer accuracy data exists.

---

## 4. Alignment and timing: forced alignment, word timings, re-timing, merge/split, min duration/gap/CPS

### Takeaway
Three aligners exist, each with a different job:
1. The default `whisper_diff` aligner diffs the user's transcript against Whisper's text characters and interpolates times proportionally.
2. The Qwen3-ForcedAligner (0.6B) is used three ways:
   - For supplied transcripts, in 60 s chunks after a coarse diff pass.
   - Always inside `qwen3_asr_long`.
   - In Review's "Re-time with Qwen3 aligner".
3. The MMS (torchaudio) CTC aligner runs only for "Realign long segments", which is off by default and runs on CPU only.

Word timings are stored per line only for the plain Whisper path. Every Qwen backend, Groq, mixed-language and MMS-realigned line stores none. Splitting:
- Default cap: 8 s / 40 CJK characters.
- Long lines are cut at word pauses of at least 0.35 s.
- Pieces must be at least 0.8 s and 4 CJK characters long.

There is no enforced minimum line duration, minimum gap or CPS rule at transcription time or export. CPS and overlap are flags only, except that exports trim overlaps.

### Cited Findings
**Diff alignment (`whisper_diff`, default)**
- How it works [READ] — [core.py:850-876](/home/user/U00/core.py):
  - Whisper's characters are given evenly spaced times inside each segment.
  - `difflib.SequenceMatcher(autojunk=False)` matches the user's characters to them.
  - A line takes the min and max of its matched times.
- `lines_from_char_times` fills unmatched lines from their neighbours, enforces a minimum end of `start+0.5`, and on overlap sets `start = prev.end + 0.05` — [core.py:812-847](/home/user/U00/core.py) [READ]
- [RAN] E7. Whisper segments [0-4 "今天天气很好我们去公园", 5-9 "然后一起吃饭吧", 20-22 "好的"]:
  - "好的。" becomes **20.0-21.0**, while speech runs to 22.0.
  - "然后一起吃饭吧。" becomes 5.0-**8.429**, while the segment ends at 9.0.
  - The unmatched "完全不同的一句话。" was interpolated to 3.636-5.0.
  - So a line ends at the *start* of its last character's time slot, which is systematically early by one character's share of the segment.
- `transcript_mode` default is `have_transcript`, and alignment defaults to `whisper_diff` — [services/transcribe_service.py:519, 544](/home/user/U00/services/transcribe_service.py) [READ]

**Qwen3 forced alignment**
- Constants: `MAX_CHUNK_SECONDS = 60.0` (comment: the model "drifts out of sync after roughly 30 s"); `HARD_CAP_SECONDS = 300.0`, at which a line raises ValueError — [forced_align.py:58-63, 149-180](/home/user/U00/forced_align.py) [READ]
- Languages: zh, ja, ko, plus English for the aligner only — :65-71 [READ]
- Repair of bad aligner output [READ] — :183-212, 296-306, 352-365:
  - Zero-length spans (issue #197) are spread over their neighbours.
  - Units that go backwards are clamped.
  - Lines with bad units fall back to the coarse diff times and are flagged `timing_uncertain`. Repaired lines get `TIMING_REPAIRED_NOTE`.
- A ValueError from forced alignment makes the pipeline fall back to diff alignment, and the result reports `forced_align_error` — [services/transcribe_pipeline.py:621-625](/home/user/U00/services/transcribe_pipeline.py) [READ]
- `refine_segment_timing` (used by the VAD backends and re-time) keeps times inside each span and never overlapping — [forced_align.py:369-427](/home/user/U00/forced_align.py) [READ]
- [RAN] E6a: `_repair_unit_spans([(0,.5),(.9,.9),(.9,.9),(1.5,2)],0,3)` gives `[(0,.5),(.5,1),(1,1.5),(1.5,2)]`; a zero-length tail gives `(1.0,3.0)`.
- [RAN] E6b, fake aligner:
  - A group with good per-character times gives 10.5-11.45 and 11.5-12.45.
  - A group whose aligner output is **all** zero-length comes back as the whole span 20.0-23.0, flagged `timing_uncertain` with the *repaired* note, not the fallback note. An all-zero result is "repaired" by spreading it evenly over the span.

**Qwen backends' line timing**
- `qwen3_asr_vad`: line times are the span bounds. A span longer than 8 s or 40 CJK characters is split by character share, with no words. The forced-aligner refine runs only when the global "Refine line timing" setting is on (default off) — [asr_backend.py:440-493](/home/user/U00/asr_backend.py); [services/asr_options_service.py:62-68](/home/user/U00/services/asr_options_service.py) [READ]
- `qwen3_asr_long` [READ] — [asr_backend.py:79-83, 450, 483-492](/home/user/U00/asr_backend.py):
  - Text is split one sentence per line with `SENTENCE_SPLIT_RULES` (8 s / 40 CJK, `per_sentence=True`).
  - The aligner always runs, except with mixed languages.
  - The last 10% of progress belongs to the aligner.
- [RAN] E11, fakes. Raw spans (1-6), (8-14), (20-26):
  - The long backend produced 2 windows: 0.7-14.3, after 300 ms padding and a 3 s merge, and 19.7-26.3.
  - The first window became 3 lines: "我们今天去公园。" / "然后一起吃饭，看电影，" / "再回家休息一下吧。好。". "好。" was folded into the previous line as a stub, and a clause cut was made because the estimated time exceeded 8 s.
  - `qwen3_asr_vad` with refine off produced 3 lines with span-bound times 0.9-6.1, 7.9-14.1 and 19.9-26.1.
  - `encode_line_words(...)` returned `null` for every line: **Qwen lines store no word timings**.
- [RAN] E4, VAD span building on a synthetic 60 s waveform:
  - Detector spans (0.5-0.7), (1-5), (5.8-9), (10.5-52), (54-54.2).
  - `qwen3_asr_vad` dropped both 200 ms spans (min speech 250 ms). After merging it had (0.9-9.1) and (10.4-52.1). Capping at 15 s cut at 25.31 (lowest-RMS noise frame, not a pause) and at 38.02 (the planted dip).
  - The long backend merged everything into one span, 0.2-54.5, and cut it at 26.23 (the planted dip at 26.2).
  - The context window of the first span reached exactly to the next span's start: 0.0-10.4.

**Word timings storage**
- Column `lines.word_timings TEXT` — [db.py:1183-1187](/home/user/U00/db.py) [READ]
- Payload format: `{"h": sha1(text)[:16], "w": [[char_start, char_end, start_ms, end_ms], ...]}`. Caps: 1000 words and 32,000 bytes. Words must spell the text exactly, ignoring whitespace, or nothing is stored — [segment_splitting.py:447-472](/home/user/U00/segment_splitting.py) [READ]
- On read they are checked against the fingerprint and against the line's time span — :488-516 [READ]
- The words are cleared when `zh` changes without new words — [db.py:2477-2481, 2551-2556, 2649-2657](/home/user/U00/db.py) [READ]
- Lines get words in the pipeline via `encode_line_words(seg["text"], seg.get("words"))` — [services/transcribe_pipeline.py:580-589](/home/user/U00/services/transcribe_pipeline.py) [READ]
- Lines with no words: Qwen (all three backends), Groq, mixed-language Whisper, MMS-realigned segments ([word_align.py:160-193](/home/user/U00/word_align.py) builds new dicts without words), and anything cut by even split [READ/RAN].
- Undo and single-line re-transcribe lose words: [docs/STATUS.md:26](/home/user/U00/docs/STATUS.md) [READ]
- [RAN] E3: Whisper words in a different script from the text (Traditional words, Simplified text) give `encode_line_words → null`, and the splitter falls back.

**`tighten_to_words`**
- Narrows a Whisper segment to its first and last word with non-zero length, never widening it — [segment_splitting.py:551-564](/home/user/U00/segment_splitting.py) [READ]
- [RAN] E2: `(0,10)` with words 1.2-7.8 gives `(1.2,7.8)`; zero-length words are ignored; a word outside the chunk does not widen it.

**Splitting long lines**
- Constants: `SPLIT_MAX_SECONDS = 8.0`, `SPLIT_MAX_CJK_CHARS = 40`, `MIN_PIECE_SECONDS = 0.8`, `MIN_PIECE_CJK_CHARS = 4`, `MIN_PIECE_WORDS = 2`, `MIN_WORD_GAP_SECONDS = 0.35` (range 0.1-2.0, per-title `min_pause_sec`), `_SIMILAR_GAP_RATIO = 0.75` — [segment_splitting.py:16-42](/home/user/U00/segment_splitting.py); column at [db.py:1231](/home/user/U00/db.py) [READ]
- Cut order: sentence end, then comma/clause, then a space next to CJK, then word pauses. Without words, punctuation pieces get times by character share. `long_line_split` then cuts any piece still over the character cap into equal parts, flagged `timing_uncertain` — [segment_splitting.py:135-244](/home/user/U00/segment_splitting.py); [long_line_split.py:23-57](/home/user/U00/long_line_split.py) [READ]
- [RAN] E3, a 22-character 11.26 s line with no punctuation and 8 words, gaps [0.1, 0.3, 0.1, 0.5, 0.2, 0.36, 0.1]:
  - At min_pause 0.35 it is cut once, at the 0.5 s gap: 0.0-5.3 and 5.8-11.26.
  - At min_pause 0.6 it is not cut and stays one 11.26 s line.
  - With no words and no punctuation it stays whole even through `long_line_split`. The even split only triggers on the *character* cap, so a 12 s, 22-character line survives.
  - A 12 s line with punctuation and no words is cut at the sentence end with proportional times 0-7.846 and 7.846-12, and **no** `timing_uncertain` flag.
  - 50 identical CJK characters in 6 s are even-split into 0-3 and 3-6, both flagged.
  - `SENTENCE_SPLIT_RULES` on "好。是的。走吧，我们现在就出发去那个地方。" over 4 s stays one line, because the stubs "好。" and "是的。" are folded in.
- MMS realign (`realign_long_segments`, per title, default off) [READ] — [word_align.py:160-193, 260-300](/home/user/U00/word_align.py); [services/transcribe_pipeline.py:510-534](/home/user/U00/services/transcribe_pipeline.py):
  - Applies only to segments of at least 12 s.
  - Groups words at pauses of at least 0.6 s.
  - The pipeline calls it without a `device`, so it is **always CPU** (default `"cpu"`).
  - Its MMS model is about 1.1 GB.
  - It is skipped on VAD backends and mixed-language runs.

**Re-timing existing lines** ("Re-time with Qwen3 aligner", Review) [READ] — [services/retime_service.py:24-31, 72-163, 165-256](/home/user/U00/services/retime_service.py):
- Selected neighbouring lines in one language are grouped into chunks of at most 60 s.
- Each group's outer edges are widened by up to 3 s into neighbouring gaps.
- Each group goes through `refine_segment_timing`.
- Proposals are clamped so they do not overlap their neighbours (minimum line 0.1 s; moves under 0.01 s are ignored).
- Apply writes only start and end, as a compare-and-set, after a history snapshot.

**Merge short lines (Review)**
- Defaults `min_duration=1.2`, `max_gap=0.5`, `max_chars=80`; the merged result must be shorter than 2.5 × min_duration — [core.py:749-798](/home/user/U00/core.py); [services/review_extras_service.py:85-86](/home/user/U00/services/review_extras_service.py) [READ]
- [RAN] E8: [0-0.6 "嗯"], [0.8-1.5 "好的"] become 0-1.5 "嗯好的"; a 0.7 s gap is not merged.

**Duration, gap and CPS rules**
- Reading speed is a flag only [READ] — [subtitle_formats.py:137-164, 200-242](/home/user/U00/subtitle_formats.py):
  - CPS limits: cjk 6.5, latin 8.5, abugida/RTL 7.0, default 7.5.
  - Relaxed mode about +40%; streamer mode relaxed × 1.5.
  - Cues under 1.5 s, or with fewer than 11 CJK / 15 Latin characters, are never flagged.
  - Per-title `reading_speed_mode` normal/relaxed/off.
- [RAN] E12 on the zh field: 16 characters over 1.4 s (11.4 cps) is **not** flagged because it is under 1.5 s; 16 over 2.0 s (8.0 cps) is flagged at the 6.5 limit; 13 over 2.0 s (6.5) is not.
- Overlaps: `clamp_overlaps` trims a cue's end to the next cue's start in every export and flags `timing_overlap` — [subtitle_formats.py:88-110](/home/user/U00/subtitle_formats.py); [services/export_service.py:275, 374, 390-410](/home/user/U00/services/export_service.py) [READ]
- Line character limits zh/ja 16, ko 20, en 42. The re-split threshold is 2× that — [subtitle_formats.py:31-32](/home/user/U00/subtitle_formats.py); [resegment.py:68-73](/home/user/U00/resegment.py) [READ]
- Long-line diagnosis: longer than 12 s and more than 2.5× the expected time at 4.5 chars/s — [core.py:945-990](/home/user/U00/core.py) [READ]
- No minimum cue duration, minimum inter-cue gap (frame gap), or maximum duration is enforced on output. The only floors are 0.5 s in `lines_from_char_times`, 0.8 s and 4 characters for split pieces, 0.3 s for snapping, and 0.1 s for re-time — [READ, grep for min_duration/min_gap].

### Inferences
- [INFERRED] Review re-split of lines from the default backend for zh/ja (`qwen3_asr_long`) can never cut at real word pauses, because those lines store no word timings. It falls back to punctuation with proportional times, or an even split.
- [INFERRED] The forced aligner returns per-unit times inside `_align_chunk`, but they are reduced to line min/max and thrown away. Persisting them as `word_timings` would be possible but is not done.
- [INFERRED] Proportional cuts made without words are not flagged (E3), while even splits are. So some estimated times look as trustworthy as word-timed ones in Review.

### Gaps
- No measurement in the repo of timing accuracy (boundary error in ms) for any aligner. The streamer VOD table records line counts and lengths, not boundary error. The forced-aligner refine option and Whisper+Qwen together were "Not tested" ([docs/asr-experiments.md:380-381](/home/user/U00/docs/asr-experiments.md)).

---

## 5. Settings surface (UI Basic vs More options, Settings, Developer Mode, hard-coded)

### Takeaway
The model picker, the transcript and the language are visible by default. Most tuning sits in Transcribe's collapsible "More options". Beam, VAD threshold, the hallucination guard, the separation backend and the repeat guard are Developer Mode only. Qwen batch, refine timing and mixed languages are global and PC-only, in Settings > Transcription experiments. The voice detector is global but shown in Transcribe. Every span, merge, pad, split, alignment and check-timing constant is hard-coded.

### Cited Findings
**Per-title settings** (dramas columns at [db.py:1222-1260](/home/user/U00/db.py); validation at [services/transcribe_service.py:334-411](/home/user/U00/services/transcribe_service.py)) [READ]:
| Setting | Default | Range | UI location |
|---|---|---|---|
| `whisper_size` | large-v3-turbo | allowed set | Transcribe, visible by default ([TranscribeStage.tsx:570-578](/home/user/U00/frontend/src/pages/workspace/stages/TranscribeStage.tsx)) |
| `asr_backend_choice` | per-language rule (§1) | 4 choices | More options (:724) |
| `alignment_method` | whisper_diff | + qwen3_forced_align | More options (:719) |
| `sensitivity_preset` | normal | normal/sensitive | More options (:715) |
| `min_silence_ms` | 300 | 100-3000 | More options (:717); Auto-tune tries [300, 800, 1500] ([core.py:300](/home/user/U00/core.py)) |
| `min_pause_sec` | 0.35 | 0.1-2.0 | More options (:718) |
| `separate_vocals_first` | off | bool | More options (:758-762) |
| `realign_long_segments` | off | bool | More options (:763) |
| `whisper_fast_mode` | off | bool | More options (:764) |
| `split_by_sentences` | off; **on for new zh/ja titles** ([services/drama_service.py:102, 151-156](/home/user/U00/services/drama_service.py)) | bool | More options (:765-771) |
| `use_groq` | off | bool | More options (:778) |
| `beam_size` | 5 | 1-10 | **Developer Mode** (:726) |
| `vad_threshold` | 0.5 | 0.1-0.9 | **Developer Mode** (:727) |
| `hallucination_silence_sec` | 0 | 0, or 0.5-10 | **Developer Mode** (:728) |
| `separation_backend` | auto | auto/audio_separator/demucs | **Developer Mode** (:730) |
| `whisper_repeat_guard` | off | bool | **Developer Mode** (:773-777) |
| `reading_speed_mode` | normal | normal/relaxed/off | [db.py:1253](/home/user/U00/db.py) |
| Extra names / replacement prompt | empty | text | More options (:738-757); not persisted ([services/transcribe_service.py:476-479](/home/user/U00/services/transcribe_service.py)) |

**Global settings** (`app_settings`) [READ]:
- `qwen_asr_batch_size` 1 (1-16), `qwen_vad_refine_timing` false and `mixed_languages` false sit in Settings > Transcription experiments ([frontend/src/pages/Settings.tsx:276](/home/user/U00/frontend/src/pages/Settings.tsx)). That card is not gated by Developer Mode.
- `voice_detector` "auto" is shown in the Transcribe stage (`VoiceDetectorField`, [TranscribeStage.tsx:725](/home/user/U00/frontend/src/pages/workspace/stages/TranscribeStage.tsx)) but stored globally.
- Writes are `local_only()` ([api/routers/asr_options_routes.py:29, 36](/home/user/U00/api/routers/asr_options_routes.py)). Reads need `admin.settings`.
- Other global settings: `use_gpu` and `whisper_model_path` (offline model folder) — [services/transcribe_service.py:565, 606](/home/user/U00/services/transcribe_service.py) [READ]
- **DOC DRIFT (minor):** the options table in [docs/asr-experiments.md:14-21](/home/user/U00/docs/asr-experiments.md) omits `voice_detector`, `min_pause_sec`, `split_by_sentences`, `realign_long_segments`, the sensitivity preset and fast mode. [docs/engine-backends.md:292-297](/home/user/U00/docs/engine-backends.md) does cover `split_by_sentences`.

**Hard-coded constants a user cannot change** [READ]:
- Anti-loop `condition_on_previous_text=False`.
- Repeat collapse count 4; stock-phrase isolation 5.0 s.
- `word_timestamps=True`.
- Qwen VAD sets (threshold, min speech, pad, merge 1.0/0.3/3.0 s, caps 15/30 s, search 3/8 s, context pad 2 s).
- Sentence-mode min silence 2000 ms.
- Split caps 8 s / 40 CJK; piece floors 0.8 s / 4 characters.
- Forced-aligner chunk 60 s / hard cap 300 s.
- MMS 12 s / 0.6 s.
- All `timing_drift` thresholds.
- Coverage 15% / 30 s.
- Qwen `max_new_tokens=256`; Qwen model size 1.7B (the `model_size` parameter is never set by any caller).
- Separation chunk 30 s / 1 s overlap.
- CPS tables.

**CLI parity**
- `cli.py align` uses the title's saved Whisper tuning, separation, realign, Groq and alignment method. It always requires a transcript, so it never uses Qwen ASR backends — [cli.py:445-597](/home/user/U00/cli.py) [READ]
- `cli.py transcribe` calls `transcribe_service.start_transcribe_run` (:794-809), so it shares the app pipeline [READ].

### Inferences
- [INFERRED] The settings most relevant to timing quality are either hidden or fixed: VAD threshold, hallucination guard, repeat guard, every Qwen-path VAD constant, and Check timing tolerances. The visible controls are min silence, which sentence mode ignores, min pause, backend and model.

### Gaps
- I did not open `sourceForm.ts` or `advancedSummary` to check how "Reset to defaults" restores each field, nor `TranscriptionExperimentsCard.tsx` wording beyond the toggles.

---

## 6. Data flow, audio file to timed lines (as implemented)

### Takeaway
`start_transcribe_run` resolves the title's settings and spawns `_transcribe_worker`, which runs these steps:
1. Optional vocal separation.
2. One of: Qwen VAD backend, Groq, mixed Whisper, or Whisper `transcribe_for_timing` (optionally followed by Qwen re-text).
3. Optional MMS realign.
4. `long_line_split.split_long_segments`.
5. Building Lines, with words encoded.

The parent then does a full-sync save, writes the raw transcript, runs Check timing when the backend is Qwen-only, and optionally starts diarization. In have_transcript mode, the Whisper segments are only a timing reference for diff or forced alignment.

### Cited Findings
- Entry: [services/transcribe_service.py:456-637](/home/user/U00/services/transcribe_service.py), spawn at :598-618 [READ]
- Pipeline steps — [services/transcribe_pipeline.py](/home/user/U00/services/transcribe_pipeline.py) [READ]:
  - Separation :320-361
  - VAD backend :374-421
  - Groq :422-435
  - Whisper load and decode :436-505, mixed at :472-481
  - MMS realign :510-539
  - Qwen re-text :545-579
  - Split and Line build :580-592, with sentence rules only when `sentence_lines`
  - have_transcript alignment :593-628
  - Outcome and `run_config` :633-655
- Apply step: `db.save_lines(drama_id, lines)` (full sync), then `raw_transcript.write_raw_transcript`, then `timing_check_service.after_run` — [services/transcribe_service.py:803-816](/home/user/U00/services/transcribe_service.py) [READ]
- Inside `transcribe_for_timing`, for each faster-whisper segment [READ] — [core.py:552-570](/home/user/U00/core.py):
  - Drop it if it has no alphanumeric character.
  - `tighten_to_words`.
  - Attach the word dicts.
  - Then the hallucination filter runs over the list.

### Inferences
- [INFERRED] For a new Chinese or Japanese title on a machine with qwen-asr installed, the default path is:
  1. Decode to 16 kHz.
  2. Silero at 0.35, padding 300 ms.
  3. Merge gaps of 3 s or less.
  4. Cut into windows of at most 30 s at the quietest 20 ms frame.
  5. Qwen3-ASR 1.7B on each window plus 2 s of context.
  6. One line per sentence (8 s / 40 characters).
  7. Hallucination filter.
  8. Qwen3-ForcedAligner per window.
  9. Lines without word timings.
  10. Automatic Check timing.

  Without qwen-asr, the same title runs Whisper turbo with a 2000 ms VAD min silence and lines cut by sentence rules using Whisper word timings, which are stored.

---

## 7. Measured results already in the repo, what they support, and how stale they are

### Takeaway
All numbers are single-run, CPU-only (4 cores), and mostly FLEURS read speech, measured 2026-10-04 to 05 and 2026-10-09. The defaults changed on 2026-10-07 (#882) and 2026-10-09, so several rows describe settings that are no longer the default. The only real-audio comparison of the current default zh backend is one private 15-min Chinese streamer VOD. There `qwen3_asr_long` scored *worse* than Whisper turbo (22.0 vs 16.4 reference-side CER in the first 5 min).

### Cited Findings
- Snapshot conditions — [docs/asr-experiments.md:210-219](/home/user/U00/docs/asr-experiments.md) [READ]:
  - 2026-10-04/05, one CPU container, Whisper int8, Qwen3-ASR 1.7B bf16.
  - faster-whisper 1.2.1, ctranslate2 4.8.2, qwen-asr 0.0.6, transformers 4.57.6, torch 2.14.1+cpu.
  - The scripts are not in the repo, so none of it can be re-run.
- Staleness notes in the docs themselves [READ] — [docs/asr-experiments.md:221-227](/home/user/U00/docs/asr-experiments.md):
  - The Whisper rows used the old `no_repeat_ngram_size=3` / `repetition_penalty=1.1` defaults.
  - The CPU default was medium at the time.
  - The VAD-backend rows predate the padding/merge fix.
- Git dates [READ, `git log -S`]:
  - `qwen3_asr_long`, its zh/ja default, the hallucination guard switched off, the repeat-guard toggle and split-by-sentences arrived in e5a9302d on 2026-10-07 (#882).
  - The zh/ja sentence-split default arrived in 01edaf98 on 2026-10-09.
  - The streamer VOD doc arrived in b37fd998 on 2026-10-09.
  - So none of the FLEURS rows measure `qwen3_asr_long` or the current Whisper sentence mode.
- Clean FLEURS, 60 utterances per language, CER % for Korean / Japanese / Chinese [READ] — [docs/asr-experiments.md:229-247](/home/user/U00/docs/asr-experiments.md):
  - turbo 4.60 / 6.61 / 7.15
  - medium 4.93 / 8.47 / 6.84
  - large-v3 4.08 / 6.54 / 5.71
  - Qwen3 whole clip 3.34 / 5.38 / 3.60
  - Old VAD backend 4.82 / 6.64 / 5.30
  - Supports: turbo as the default over medium, and large-v3 slightly better on ko/zh. Gaps under about 1 point are noise.
- Padding fix (Levenshtein scorer), ko / zh / ja [READ] — [docs/asr-experiments.md:249-263](/home/user/U00/docs/asr-experiments.md); [docs/archive/asr-experiments-history.md:797-856](/home/user/U00/docs/archive/asr-experiments-history.md):
  - Plain 3.11 / 3.16 / 5.08
  - Before 4.97 / 4.85 / 6.68
  - After 3.34 / 3.78 / 5.42
  - Root cause: tight cuts made Qwen write numbers as words.
  - Supports: `CONTEXT_PAD_S = 2.0` and merge gap 1 s.
- Mixed-language, 80 utterances [READ] — [docs/asr-experiments.md:276-293](/home/user/U00/docs/asr-experiments.md):
  - Per-span detection gave 0% wrong-script segments, against 33-74% for the other approaches.
  - The 0 missed boundaries are "largely by construction".
  - On 2 s clips, language detection was right for only 48 of 80.
- Noisy and music audio, 60 utterances (20 per language) [READ] — [docs/asr-experiments.md:295-318](/home/user/U00/docs/asr-experiments.md); archive :952-1137:
  - VAD sweep: 100/1000 ms min silence moved CER by +0.1; threshold 0.3 by −0.18 (noise); threshold 0.7 by +3.23 (worse).
  - large-v3 beat turbo on degraded audio by 1.4 points at +10 dB and 2.4 at 0 dB.
  - Demucs: −2.3 with music only, +8.6 with noise.
  - Hallucinations were rare: 1 over-length output and 17 empty outputs in 5,340 transcriptions, all in the hardest condition.
  - The archive itself warns: "Background-only stretches of one second did not trigger loops, so this benchmark says nothing about long silence or music, where Whisper loops" (:1137).
- Streamer VOD, 2026-10-09, private, reference-side CER against PaddleOCR'd hardsubs, one run per row [READ] — [docs/asr-experiments.md:329-381](/home/user/U00/docs/asr-experiments.md):
  - First 5 min:
    - turbo default 16.4 (150 lines, mean 1.4 s, max 4.6 s); beam 1 18.3; beam 8 16.6
    - silence 2000 ms 16.2; threshold 0.7 17.0
    - fast mode 24.0 (44 lines, max 23.9 s)
    - hallucination silence 2 s 16.4; repeat guard 17.5; Split lines by sentences 16.2
    - medium 17.5; large-v3 18.7
    - `qwen3_asr_vad` 21.1 (61 lines, max 7.8 s); `qwen3_asr_long` 22.0 (79 lines, mean 3.6 s, **max 16.0 s**); Qwen over Whisper lines 25.1
  - Full 15 min: turbo 20.4; turbo with sentence split 19.6; large-v3 20.6.
  - The doc says differences under about 2 points are noise, and the reference covers only about 58% of the characters heard.
- **Re-check of a guidance-table claim:** "Beam size: 1 saved 7-35% of time … Current default 5" ([docs/asr-experiments.md:138](/home/user/U00/docs/asr-experiments.md)) still matches the code (`beam_size` 5). The "Initial prompt … glossary feeds it" row also matches `build_auto_initial_prompt` [READ].

### Inferences
- [INFERRED] The repo's own data does not support `qwen3_asr_long` as the zh/ja default on real audio. On the one real clip it was about 5.6 points worse than turbo, with lines up to 16 s; the doc's noise threshold there is 2 points. The comment justifying the default cites the Qwen3-ASR model card and FLEURS whole-clip runs, not product-path runs ([services/asr_options_service.py:44-49](/home/user/U00/services/asr_options_service.py)). The 16 s maximum also exceeds the 8 s sentence-split cap. That suggests the cap was bypassed: perhaps a single "sentence" with no clause marks and no word timings, which E3 shows is never cut on duration alone.
- [INFERRED] The data supports: turbo over medium; keeping the 0.5 threshold over 0.7; leaving separation off by default; leaving the repeat guard off (streamer 17.5 vs 16.4, within noise); and a hallucination silence threshold of 0 vs 2 being neutral on that clip (16.4 vs 16.4).
- [INFERRED] The data does not establish the timing accuracy of any path, the cost or benefit of the forced aligner, any GPU behaviour, or behaviour on long silence or music.

### Gaps
- Harness scripts, private clips and per-row outputs are not in the repo, so nothing in sections 7.x could be re-run or re-verified here.
- No measurement exists for: `qwen3_asr_long` on FLEURS; the refine-timing option; batching vs no batching; MMS realign; Check timing precision or recall.
