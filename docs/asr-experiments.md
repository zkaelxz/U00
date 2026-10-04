# ASR experiments: Qwen3-ASR batching (Step 103) and MOSS-Transcribe-Diarize (Step 104)

Both are **off by default** and switched on in React **Settings > Transcription
experiments** (PC only). Nothing here changes a default. Whether either one
becomes a default is a later decision, made after the real comparison below.

## What was built

| | Step 103: Qwen3-ASR batching | Step 104: MOSS-Transcribe-Diarize |
|---|---|---|
| Setting | `qwen_asr_batch_size`, 1-16, default 1 (1 = the original one-segment-at-a-time run) | `moss_experimental`, default off |
| Applies when | a drama's ASR backend is **Qwen3 ASR** in Whisper-text mode | a drama's ASR backend is **MOSS-Transcribe-Diarize (experimental)** in Whisper-text mode |
| Code | `asr_backend.Qwen3ASRBackend.transcribe(batch_size=...)` | `asr_backend.MossTranscribeDiarizeBackend`, `BACKENDS`/`get_backend` |
| Written against | qwen-asr **0.0.6** (`transcribe(list)` returns one result per input, in order); any other installed version runs one segment per call | OpenMOSS/MOSS-Transcribe-Diarize commit **61bc29c** (package 0.1.0), HF model revision **704aa4a** |
| Safety | results keyed back by segment index; a batch with the wrong result count is redone one segment at a time; timing is always Whisper's | never picked automatically; refused unless the toggle is on and the package is installed; its own speaker labels are kept and pyannote is not chained over them |

### Before turning MOSS on

- **It runs downloaded code.** The model needs `trust_remote_code`: its Python
  files come from the Hugging Face repo `OpenMOSS-Team/MOSS-Transcribe-Diarize`
  and run inside the Baihe server process, with the same rights as the app
  (including access to stored API keys). The revision is pinned
  (`asr_backend.MOSS_HF_REVISION` = `704aa4a9c304e8520be88901e0d1960158ef5b15`),
  so a change on the Hub can't run new code here; moving the pin needs a review
  of the upstream diff.
- **Household members can start it.** The toggle is PC-only, but once it is on,
  anyone with `lines.edit` + `jobs.start` can set a drama to MOSS and run it
  (a multi-GB model download on first use), the same as Qwen3-ASR.
- **It replaces Qwen3.** It is not on PyPI; install it into the app's own
  environment from the tested commit:
  `pip install "git+https://github.com/OpenMOSS/MOSS-Transcribe-Diarize@61bc29cd4120be7b5d3b761b64cd5dff57263642"`.
  That upgrades Transformers to 5.x, which qwen-asr (pinned to 4.57.6) can't
  use, so Qwen3-ASR and Qwen3 forced alignment stop working until you go back
  (`pip install qwen-asr`). Diagnostics lists it (`moss-transcribe-diarize`) but
  doesn't offer a pip install.
- A run can't be stopped part-way (one blocking call); a Stop takes effect
  when it returns, before any line is replaced.

- **Batching needs the tested qwen-asr.** With any version other than 0.0.6
  installed, Qwen3-ASR sends one line at a time whatever the saved batch size;
  the Settings card says which applies.

### Remote-code check (lead security review, 2026-09-30)

Checked what `trust_remote_code` and the package actually load:

- **Hub repo `OpenMOSS-Team/MOSS-Transcribe-Diarize` at `704aa4a9c304e8520be88901e0d1960158ef5b15`:**
  - `config.json` and `processor_config.json` `auto_map` entries all point at
    modules in the same repo (`configuration_…`, `modeling_…`,
    `processing_…_moss_transcribe_diarize.MossTranscribeDiarize*`). There is no
    cross-repo `Org/repo--module.Class` entry.
  - `tokenizer_config.json` uses the built-in `Qwen2Tokenizer`, and
    `preprocessor_config.json` the built-in `WhisperFeatureExtractor`.
  - The three `.py` files import only `torch`, `numpy` and `transformers`
    (Qwen3/Whisper building blocks). None of them calls `from_pretrained`,
    `snapshot_download`, `hf_hub_download` or anything network or subprocess.
  - So everything that runs comes from the pinned revision.
- **Package `moss_transcribe_diarize` at GitHub commit `61bc29c`:**
  - Baihe imports only the top-level package, `inference_utils` and
    `transcript_parser` (with `subtitle`). None of them downloads anything.
  - The package's `app/` subpackage, its own CLI and web app, does call
    `from_pretrained(..., trust_remote_code=True)` with no revision and does
    network I/O (vLLM client). Baihe never imports `app/`.
  - `inference_utils` uses `transformers.audio_utils.load_audio`, which would
    fetch a URL if given one. Baihe only ever passes the drama's local audio
    path.
- **Moving either pin** (`MOSS_HF_REVISION`, or the install commit) needs this
  check redone.

## Step 101 manual check: speaker detection on the GPU

1. Settings: turn on "Use the GPU for transcription".
2. Open a drama with audio > Transcribe > **Speakers** > **Detect speakers only**.
   While it runs, `nvidia-smi -l 1` should show a `python` process using a few
   GB of GPU memory with non-zero GPU-Util.
3. When it finishes, the line under Speakers should read "Last Detect speakers
   run (pyannote) used the GPU." (`diarization_turns.json` has
   `"device": "cuda"`.)
4. Turn "Use the GPU for transcription" off, run **Detect speakers only**
   again, and confirm the line now says it used the CPU.

## How to evaluate (the roadmap's method)

Use the same held-out, already-reviewed clips for every run: narration,
dialogue, music under speech, quiet speech, overlapping speakers. For each clip
record transcription error (CER against the reviewed text) and diarization
error (speaker labels against the reviewed speakers) separately, plus wall-clock
time and peak GPU memory (`nvidia-smi --query-gpu=memory.used --format=csv -l 1`).

- **Step 103:** run Qwen3 ASR with batch size 1, then 4 and 8, on the same
  clips. Batching may be adopted only if no clip's text changes for the worse.
- **Step 104:** run Whisper + speaker detection, then MOSS, on the same clips.
  Write the go/no-go recommendation here, with the numbers.

## Results

*Not run yet: needs the user's GPU PC.*

## Public benchmark: Korean (2026-10-04)

Clean read speech, so this understates the difficulty of drama audio. Use it to
compare models and settings, not as an absolute error rate for the product.

**Data.** FLEURS Korean (`google/fleurs`, config `ko_kr`, test split, CC BY 4.0), fetched without a
token from the Hub's parquet export (`refs/convert/parquet`, revision
`168de341b3db6859a9bac1c50a2ef5e3b47647e0`, file `ko_kr/test/0000.parquet`, sha256
`1a8319fc61c7996e8c15acde633786de97054e28ae1e463eb13901716176a7ec`). The first 60 rows, 741.5 s of
audio, were used (the dataset script itself, revision `70bb2e84b976b7e960aa89f1c648e09c59f894dd`, was not run). Audio was kept
outside the repo. The `id` column repeats in places (different speakers read the same sentence), so rows are identified by
position. Ids in row order: 1883, 1929, 1701, 2000, 1801, 1662, 1705, 1959, 1715, 1907, 1850, 1702, 1826, 1743, 1993, 1829, 1992, 1742, 1907, 1849, 1709, 1845, 1674, 1765, 1677, 1662, 1716, 1757, 1708, 1771, 1996, 1910, 1894, 1804, 1697, 1666, 1701, 2005, 1776, 1866, 1672, 1673, 1695, 1737, 1862, 1691, 1724, 1879, 1964, 1830, 1977, 1864, 1978, 1725, 1926, 1934, 1783, 1663, 1707, 1690.

**Method.** CPU only (4 cores, 15 GB), one configuration at a time so timings don't contend. Whisper
models went through `core.load_whisper_model` (int8) with the settings `core.transcribe_for_timing` uses:
`language="ko"`, `vad_filter=True` with `min_silence_duration_ms=300` and `threshold=0.5` (the app's
`_DEFAULT_TUNING`), `beam_size=5`, `word_timestamps=True`, and `core.WHISPER_ANTI_LOOP_KWARGS`
(`condition_on_previous_text=False`, `no_repeat_ngram_size=3`, `repetition_penalty=1.1`), faster-whisper's default
temperature fallback, no initial prompt. Each row changes one of these against that baseline. The call is
rebuilt in the benchmark script rather than calling `transcribe_for_timing`, because that function does not expose
`vad_filter`, `temperature` or `condition_on_previous_text`; it also skips the repeat-collapsing filter,
which would not fire on 60 separate utterances. The Korean prompt was the generic sentence
"다음은 한국어 음성을 받아쓴 문장입니다." Qwen3-ASR plain is `asr_backend.load_qwen3_asr` (bfloat16) on each whole
utterance with language Korean; the last row is `asr_backend.Qwen3ASRVadBackend(model_size="1.7B")`
(`use_gpu=False`, `batch_size=1`). Each utterance was transcribed separately. Model load time is not in RTF.

**Metric.** Character error rate (CER) with `jiwer` 4.0.0 (`process_characters`), reference = FLEURS
normalised `transcription`. Reference and output are both NFKC-normalised, lower-cased, and reduced to
letters and digits (punctuation and spaces removed). Corpus CER = total character errors / total reference
characters. The 95% CI is a bootstrap over the 60 utterances (2,000 resamples). A hallucination is an
empty reference with any output, or normalised output longer than 3x the reference. RTF = wall-clock
seconds / audio seconds. Versions: faster-whisper 1.2.1, ctranslate2 4.8.2, qwen-asr 0.0.6, transformers
4.57.6, torch 2.14.1+cpu.

| Model | Setting | CER % | 95% CI | Hallucinations | RTF |
|---|---|---|---|---|---|
| faster-whisper large-v3-turbo | app defaults | 4.60 | 2.9-6.6 | 0 | 0.36 |
| faster-whisper large-v3-turbo | vad_filter off | 4.56 | 2.9-6.4 | 0 | 0.47 |
| faster-whisper large-v3-turbo | beam_size 1 | 4.60 | 2.9-6.6 | 0 | 0.43 |
| faster-whisper large-v3-turbo | condition_on_previous_text on | 4.60 | 2.9-6.7 | 0 | 0.47 |
| faster-whisper large-v3-turbo | temperature fallback off (0.0) | 4.60 | 2.9-6.6 | 0 | 0.47 |
| faster-whisper large-v3-turbo | Korean initial_prompt | 4.56 | 2.9-6.6 | 0 | 0.42 |
| faster-whisper medium | app defaults | 4.93 | 3.2-7.0 | 0 | 0.53 |
| faster-whisper medium | vad_filter off | 4.71 | 3.2-6.6 | 0 | 0.53 |
| faster-whisper medium | beam_size 1 | 5.41 | 3.5-7.5 | 0 | 0.40 |
| faster-whisper medium | condition_on_previous_text on | 4.93 | 3.1-7.1 | 0 | 0.52 |
| faster-whisper medium | temperature fallback off (0.0) | 4.93 | 3.2-6.9 | 0 | 0.53 |
| faster-whisper medium | Korean initial_prompt | 5.37 | 3.5-7.5 | 0 | 0.54 |
| faster-whisper large-v3 | app defaults | 4.08 | 2.5-5.9 | 0 | 0.88 |
| faster-whisper large-v3 | vad_filter off | 5.11 | 3.3-7.1 | 0 | 0.89 |
| faster-whisper large-v3 | beam_size 1 | 4.11 | 2.5-6.0 | 0 | 0.69 |
| faster-whisper large-v3 | condition_on_previous_text on | 4.08 | 2.5-5.9 | 0 | 0.84 |
| faster-whisper large-v3 | temperature fallback off (0.0) | 4.08 | 2.5-5.8 | 0 | 0.82 |
| faster-whisper large-v3 | Korean initial_prompt | 4.63 | 2.9-6.6 | 0 | 0.82 |
| Qwen3-ASR 1.7B | plain, whole utterance | 3.34 | 1.9-5.1 | 0 | 0.47 |
| Qwen3ASRVadBackend 1.7B | Silero VAD spans, batch 1 | 4.82 | 3.0-6.9 | 0 | 0.47 |

Paired bootstrap on the CER difference (same utterances, 4,000 resamples):

| Comparison | Difference (points) | 95% CI |
|---|---|---|
| large-v3 vs large-v3-turbo (defaults) | -0.52 | -0.98 to -0.11 |
| Qwen3-ASR plain vs large-v3-turbo | -1.26 | -2.52 to -0.14 |
| Qwen3-ASR plain vs large-v3 | -0.74 | -1.75 to +0.29 |
| large-v3-turbo vs medium | -0.33 | -2.01 to +1.14 |
| large-v3, vad_filter off vs on | +1.04 | -0.11 to +2.56 |
| large-v3, Korean prompt vs none | +0.56 | -0.34 to +1.96 |
| medium, beam 1 vs 5 | +0.48 | +0.04 to +1.12 |
| Qwen3ASRVadBackend vs Qwen3-ASR plain | +1.48 | +0.26 to +2.98 |

**What I would take from it**

- For Korean I would default to large-v3 over the current default large-v3-turbo: 4.08% vs 4.60%, and the paired
  difference excludes zero. It costs about twice the time (RTF 0.82-0.89 vs 0.36-0.47 on this CPU).
- Keep the app's other defaults: vad_filter on, beam 5, condition_on_previous_text off. No setting beat them by more than noise on this audio.
- Turning the VAD off made large-v3 worse (5.11%), although the interval includes zero; on turbo and medium it changed nothing.
  This audio has little non-speech to filter, so it is not evidence about VAD on drama audio.
- A generic Korean initial_prompt did not help (it hurt large-v3 and medium, no change on turbo). It says nothing about a prompt of character names.
- beam 1 only hurt medium (+0.48 points, a borderline interval). Temperature fallback and condition_on_previous_text had no effect.
- Qwen3-ASR plain was the lowest CER (3.34%), clearly better than turbo but not distinguishable from large-v3. It gives no timings, and the product path
  (`Qwen3ASRVadBackend`) scored 4.82%, no better than turbo; the VAD cut spans on utterances that were already single sentences.
- No hallucinations in any run. That is expected on clean read speech and says nothing about music or silence, where Whisper loops.
- About 105 of turbo's 124 character errors are in utterances containing digits or Latin text (names, brands, number spelling that
  differs from the FLEURS reference). Part of the CER is reference convention, not mishearing, and it applies to every model equally.
- Only 60 utterances: differences under about 1 point are within noise. RTF is from one run per row on a shared container, and
  the turbo baseline (0.36, the first run) vs its variants (0.42-0.47) shows timing noise of that size. The container was restarted
  mid-run; interrupted configurations were re-run from scratch.
- Not tested: noisy or overlapping speech, music under speech, long files (VAD and looping behaviour), GPU.
