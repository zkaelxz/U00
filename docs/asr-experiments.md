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

## Japanese clip comparison (2026-10-04)

One 3-minute stretch (first 180 s) of a Japanese VTuber cooking-stream clip, scored against its burned-in Japanese subtitles. **The clip is private (shared by its owner for testing only) and is not in the repo**, so this can't be re-run from the repo alone. CPU only (4 cores, 15 GB), int8 for faster-whisper, bfloat16 for Qwen3-ASR, language `ja`. Whisper rows use `asr_backend.WhisperBackend` with the app's defaults (`vad_filter`, beam 5, 2000 ms min silence); the Qwen rows use `Qwen3ASRBackend` and `Qwen3ASRVadBackend(model_size="1.7B")`.

| Model | Char recall | Ref lines ≥80% (of 92) | Lines outside any subtitle | Segments | Median segment | Time (180 s audio) |
|---|---|---|---|---|---|---|
| faster-whisper medium | 58.5% | 36 | 0 | 60 | 8 chars / 1.5 s | 101 s |
| faster-whisper large-v3-turbo | 67.6% | 50 | 0 | 59 | 9 chars / 1.9 s | 52 s |
| faster-whisper large-v3 | 69.1% | 50 | 0 | 63 | 9 chars / 1.7 s | 169 s |
| Qwen3-ASR 1.7B, re-transcribing Whisper-medium segments | 57.6% | 36 | 0 | 60 | 9 chars / 1.5 s | 260 s (+101 s for the medium pass = 361 s) |
| Qwen3-ASR 1.7B VAD-first | 51.8% | 32 | 0 | 39 | 7 chars / 1.7 s (longest 49 chars) | 196 s |

Times exclude model download and load (10-29 s each). "Plain" Qwen3-ASR is read as the app's existing Qwen backend, which needs Whisper's segment boundaries; the whole clip was not fed to Qwen in one call.

**How the reference was made, and its limits.** One frame per second, subtitle band cropped. rapidocr read the English track cleanly but misread the stylised Japanese font. manga-ocr was better but still wrong on many lines, so the Japanese text was read by eye from the crops, with timing from the English track's change points (±1 s). The reference is 92 lines, 735 characters, and is a human reading of the hardsubs, not ground truth for the audio: the subtitles are the clipper's edit and may differ from what was said (laughter, cut-off words, a few edge-cropped lines were approximated). Recall is a time-constrained longest-common-subsequence (matches must be within 3 s), so a blob of text can't earn recall outside its own time. A control with the transcript shifted 60 s gives 7-10% recall, which is the chance level. Reference lines (and the opening teaser, which repeats later in the clip) are only as precise as that. No model produced a line outside the subtitled stretches because this clip is nearly continuous speech, so the hallucination column says nothing about silence or music; it needs a different clip.

**Reading the table.**
- The two large faster-whisper models are clearly ahead; large-v3-turbo is within 1.5 points of large-v3 at under a third of the time. With one 3-minute clip, a gap of that size is not a ranking.
- Qwen3-ASR did not beat Whisper on this clip. Re-transcribing Whisper-medium's segments gave the same recall as medium (it inherits medium's missed segments) and cost more time on CPU.
- The VAD-first Qwen backend scored lowest and shows the blob problem: it merges several subtitle lines into one segment (up to 49 characters) and dropped short interjections.
- I would make faster-whisper large-v3-turbo the default for Japanese: close to the best accuracy and the fastest here. This rests on one clip, one speaker pair and a hand-read reference, so treat it as a reason to check more material, not a settled result.

## Mixed Korean/Japanese/English clip comparison (2026-10-04)

One 131 s clip of a multi-speaker stream (Korean, Japanese and English speakers),
CPU only (4 cores, int8 for faster-whisper, bfloat16 for Qwen3-ASR 1.7B), one run
per cell. The clip is not kept in the repo and no transcript text is quoted here.
The VAD spans are the app's (`vad_segments.speech_spans/merge_close/cap_spans`):
19 spans, 107 s of speech, longest 14.9 s.

**No reference exists.** The only burned-in subtitles are English, laid over game
UI, and rapidocr found no Hangul or kana. They are a translation, not a
transcript of the Korean/Japanese speech, so there is no character recall per
language. Span labels are a *consensus*: each span's language is the majority of
votes (script of the output, plus the detected language where a mode reports one)
from the automatic and per-span runs, leaving out every run of the model being
scored. A span counts only if at least 70% of the votes agree: 12 of 19 spans
(8 English, 2 Korean, 2 Japanese); the other 7, mostly Korean-vs-Japanese
disagreements, are excluded as disputed. Zero Chinese-labelled spans.

| Model | Mode | Script match | Wrong script | Empty | Kana on ko span / other-script spans | Lang-detect correct | Hallucinated lines | Segments | Median seg (s) | Time (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| medium | fixed ko | 4/12 | 7 | 1 | 0 / 1 | n/a | 3 | 53 | 2.0 | 570 |
| medium | fixed ja | 4/12 | 7 | 1 | 1 / 1 | n/a | 3 | 47 | 2.0 | 646 |
| medium | auto (multilingual) | 7/12 | 2 | 3 | 1 / 4 | n/a | 6 | 45 | 2.0 | 552 |
| medium | per-span detect (ko/ja/en/zh) | 11/12 | 1 | 0 | 1 / 0 | 11/12 | 3 | 65 | 1.5 | 693 |
| large-v3-turbo | fixed ko | 6/11 | 3 | 2 | 0 / 1 | n/a | 6 | 37 | 1.6 | 188 |
| large-v3-turbo | fixed ja | 4/11 | 5 | 2 | 1 / 2 | n/a | 0 | 45 | 2.0 | 247 |
| large-v3-turbo | auto (multilingual) | 5/11 | 1 | 5 | 0 / 3 | n/a | 0 | 21 | 4.0 | 156 |
| large-v3-turbo | per-span detect (ko/ja/en/zh) | 11/11 | 0 | 0 | 0 / 0 | 11/11 | 6 | 42 | 2.0 | 338 |
| large-v3 | fixed ko | 4/11 | 2 | 5 | 0 / 0 | n/a | 7 | 34 | 2.0 | 849 |
| large-v3 | fixed ja | 2/11 | 4 | 5 | 1 / 0 | n/a | 0 | 49 | 2.0 | 1297 |
| large-v3 | auto (multilingual) | 6/11 | 1 | 4 | 0 / 0 | n/a | 0 | 36 | 2.0 | 830 |
| large-v3 | per-span detect (ko/ja/en/zh) | 11/11 | 0 | 0 | 0 / 0 | 11/11 | 0 | 46 | 1.8 | 1082 |
| qwen3-asr-1.7B | plain, per span, auto | 11/12 | 1 | 0 | 0 / 1 | 10/12 | 0 | 19 | 4.2 | 318 |
| qwen3-asr-1.7B | repo VAD backend, auto | 11/12 | 1 | 0 | 0 / 1 | n/a | 0 | 21 | 4.2 | 284 |

"Script match" is the share of the scored spans whose output is in the labelled
script (Hangul, kana/kanji, Latin). "Wrong script" is output in a different script;
"Empty" is no text at all. Time is wall-clock for the whole run; the first medium
runs overlapped with a package install, so compare times only roughly. Whisper
sampling fallback is random: the first medium fixed-ko run gave 75 segments, the
rerun in the table 53.

Interpretation (small sample, consensus is not truth):
- Per-span detection restricted to ko/ja/en/zh scored best among the Whisper modes
  (11 of 11 or 11 of 12 scored spans in the right script for all three sizes,
  against 2-7 of 11-12 for fixed ko, fixed ja or whole-file automatic). Those runs
  also vote in the consensus through their detection, so they partly agree with
  themselves; treat this as favourable to them.
- Fixed ko or ja gave a wrong-script or empty result on roughly half to four fifths of
  the scored spans (5-9 of 11-12). Whole-file automatic mode
  (`multilingual=True`) is not a fix: it left 3-5 scored spans empty, and large-v3-turbo
  produced only 21 segments.
- Qwen3-ASR with no language set matched the script on 11 of 12 scored spans, but
  its detected language was right on 10 of 12 and across all 19 spans it
  reported Indonesian, Russian and Portuguese once each, and Chinese for 4. Run on
  the whole clip in one call it returned a short, single-language English text.
  The repo backend matched the same count (11 of 12) as the plain per-span run.
- `asr_backend.Qwen3ASRVadBackend.transcribe` raises for `language=None`, so
  automatic mode is not reachable through the backend as written; these runs swapped
  its language table for an "auto" entry to drive the unchanged code path.
- The three Whisper sizes' per-span detections agreed on 13 of 19 spans (ko 1-3,
  ja 3-5, zh 1-3 spans per size). The Korean and Japanese conclusions rest on 2 and
  2 scored spans, so they are weak.

Suggested starting point for the "Mixed languages" option (not a verdict): detect
the language per VAD span, restricted to the languages the user ticked, then
transcribe each span with that language; do not use a single fixed language or
whole-file automatic mode on mixed audio. Prefer large-v3-turbo for speed (3-5x faster
than large-v3 here at the same scored result). Re-measure on a clip with a
human transcript, with more than a few Korean and Japanese spans, before relying on
this; keep the Korean/Japanese span detection under review, since the disputed spans
are where it matters.

## Public benchmark: Chinese (2026-10-04)

Clean read speech, so this understates the difficulty of drama audio. Use it to compare models and settings, not
as an absolute error rate for the product.

**Data.** FLEURS Mandarin (`google/fleurs`, config `cmn_hans_cn`, test split, CC BY 4.0), fetched without a token
from the Hub's parquet export (`refs/convert/parquet`, revision `168de341b3db6859a9bac1c50a2ef5e3b47647e0`, file
`cmn_hans_cn/test/0000.parquet`, sha256 `87c0aebbe183f3a36ac87b5c3421b6ab57036824744ff695029a3f858e7622fd`). The
dataset script itself (revision `70bb2e84b976b7e960aa89f1c648e09c59f894dd`) was not run. The first 60 rows, 632.8 s of
audio (10.5 min, not 12), were used; audio was kept outside the repo. The `id` column repeats (1721 and 1906 each
appear twice, read by different speakers), so rows are identified by position. Ids in row order: 1906, 2006, 1883, 1852, 1734, 1890, 1721, 1869, 1805, 1965, 1903, 1953, 1830, 1910, 1902, 1874, 1763, 1837, 1905, 1772, 1779, 1792, 1780, 1962, 1884, 1958, 1871, 1738, 1698, 1989, 1725, 1712, 2002, 1804, 1661, 1700, 1951, 1697, 1821, 1971, 1808, 1914, 1868, 1729, 1802, 1760, 1816, 1723, 2003, 1906, 1686, 1880, 1797, 1834, 1721, 1785, 1691, 1728, 1666, 1704.
<details><summary>Audio file stems in row order</summary>

10026684690566417990, 10040380210557600780, 10048525650290665384, 10053956375630517392, 10056338555786085046, 10073099671796350432, 10081186303620187437, 10104380129516909856, 10112798408996336578, 10134195680525011353, 10142207404499844309, 10144930991730701959, 10147383166642115307, 10154699825088384418, 1015891181557233386, 1017060882143751441, 10182944938509562796, 10234433636571543908, 10279011772105822622, 10325486695909573217, 10325559490685159122, 10343405611041314630, 10369927130798325266, 10388181945260949729, 10400579561005332584, 10480388653191520950, 10481669967989866997, 10540148133747674396, 10544065594858743613, 1055783299672050865, 10558410879827163654, 10563386955236607431, 10571681330551714048, 10604423531103587528, 1061358881165519725, 10651933140664828475, 1065812790870009684, 10674527227632117693, 10694001904489787463, 10695273702291556205, 10700749092778973179, 10702444759150940582, 10730178299232374639, 10777625043982141593, 10785184604222814212, 10797488489095533876, 10800216992470799626, 10824731430851864186, 10836453965609915738, 10837263830977293517, 10852536777113131618, 10885647054729330005, 10892793653125285181, 10949290146151676233, 1095027899793949587, 10952847248994110499, 10970596972697016742, 11027229860710500775, 11031078104467854733, 11044189448827036819
</details>

**Method.** CPU only (4 cores, 15 GB), one configuration at a time so timings don't contend. Whisper models went
through `core.load_whisper_model` (int8) with the settings `core.transcribe_for_timing` uses: `language="zh"`,
`vad_filter=True` with `min_silence_duration_ms=300` and `threshold=0.5` (the app's `_DEFAULT_TUNING`),
`beam_size=5`, `word_timestamps=True`, and `core.WHISPER_ANTI_LOOP_KWARGS` (`condition_on_previous_text=False`,
`no_repeat_ngram_size=3`, `repetition_penalty=1.1`), faster-whisper's default temperature fallback, no initial prompt,
followed by `core.filter_hallucinated_segments`. The app's default model is large-v3-turbo; the CPU default in
`transcribe_service` is medium. Each row changes one setting against that baseline. The call is rebuilt in the
benchmark script because `transcribe_for_timing` does not expose `vad_filter`, `temperature` or
`condition_on_previous_text`; on 3 utterances the rebuilt baseline gave text identical to `transcribe_for_timing`.
The Chinese prompt was the generic sentence "以下是普通话的句子。". Qwen3-ASR plain is `asr_backend.load_qwen3_asr`
(bfloat16, which fit in memory) on each whole utterance with language Chinese; the second row is
`asr_backend.Qwen3ASRVadBackend(model_size="1.7B")` (`use_gpu=False`, `batch_size=1`). Each utterance was
transcribed separately. Model load time is not in wall time. Wall time is the sum over the 60 files; RTF is wall
time over 632.8 s of audio.

**Metric.** Character error rate (CER) from a plain Levenshtein distance over characters (my own 10-line
implementation, not a library), corpus-level = total edits / total reference characters. Reference = FLEURS
`raw_transcription`. Both sides: NFKC, remove every Unicode punctuation (P), separator (Z) and control (C) character, convert to Simplified
with `opencc` `t2s` (opencc-python-reimplemented 0.1.7), lowercase. Two further choices you should know about:
(1) four references carry translator-added Latin glosses in parentheses, e.g. "(Sintra)", that nobody speaks; I
removed them from the reference, which lowers every system by about 3 points (e.g. turbo 10.30% with them, 7.15%
without). (2) FLEURS writes numbers as digits ("1990年"); Qwen's VAD path and some Whisper output write numerals
("一九九零年"), which counts as errors. The "no-digit utts" column rescopes to the 48 utterances whose reference has
no digit. "Traditional" counts outputs (before conversion) that contained any Traditional-only character. "Halluc."
counts non-empty outputs longer than 1.5x the reference or with a 2-12 character unit repeated 4+ times in a row;
no output was empty.

| Configuration | CER | CER, no-digit utts | Traditional | Halluc. | Wall s | RTF |
|---|---|---|---|---|---|---|
| Qwen3-ASR 1.7B plain | 3.60% | 2.30% | 1/60 | 0 | 716 | 1.13 |
| Qwen3ASRVadBackend 1.7B | 5.30% | 2.58% | 0/60 | 0 | 645 | 1.02 |
| large-v3 baseline | 5.71% | 5.30% | 6/60 | 0 | 957 | 1.51 |
| large-v3 beam 1 | 5.86% | 5.37% | 7/60 | 0 | 671 | 1.06 |
| large-v3 no VAD | 5.86% | 5.37% | 6/60 | 0 | 922 | 1.46 |
| large-v3 zh prompt | 5.55% | 5.23% | 6/60 | 0 | 917 | 1.45 |
| large-v3-turbo baseline (app default) | 7.15% | 6.62% | 3/60 | 0 | 435 | 0.69 |
| turbo beam 1 | 7.30% | 6.62% | 6/60 | 0 | 391 | 0.62 |
| turbo no VAD | 7.20% | 6.69% | 5/60 | 0 | 440 | 0.70 |
| turbo cond. on previous text | 7.15% | 6.62% | 3/60 | 0 | 432 | 0.68 |
| turbo temperature 0 (no fallback) | 7.15% | 6.62% | 3/60 | 0 | 438 | 0.69 |
| turbo zh prompt | 6.99% | 6.48% | 4/60 | 0 | 425 | 0.67 |
| medium baseline (CPU default) | 6.84% | 6.97% | 11/60 | 0 | 595 | 0.94 |
| medium beam 1 | 7.46% | 7.39% | 14/60 | 0 | 391 | 0.62 |
| medium no VAD | 7.10% | 7.39% | 12/60 | 0 | 576 | 0.91 |
| medium cond. on previous text | 6.84% | 6.97% | 11/60 | 0 | 569 | 0.90 |
| medium temperature 0 | 6.84% | 6.97% | 11/60 | 0 | 585 | 0.92 |
| medium zh prompt | 7.46% | 7.18% | 5/60 | 0 | 592 | 0.94 |

**Reading the table.**
- Qwen3-ASR plain is the most accurate here (3.60%; 2.30% on utterances without digits). Paired bootstrap over
  utterances: its CER is 1.4 to 5.5 points below turbo's (95% interval) and 0.2 to 3.8 below large-v3's.
- large-v3 beats turbo by 0.6 to 2.4 points (95% interval) at 2.2x the wall time. medium and turbo cannot be
  told apart (interval -1.4 to +0.8).
- The `Qwen3ASRVadBackend` gap to plain Qwen (5.30% vs 3.60%) is mostly numerals: on the digit-free utterances they are
  2.58% vs 2.30%. Each FLEURS clip is one sentence, so the VAD span step has little to do here; it is not tested on
  multi-speaker or long audio.
- Whisper settings barely matter on this data. All turbo variations are within 0.3 points, which is inside the noise
  at 60 utterances (a few thousand characters). `condition_on_previous_text` and temperature 0 change nothing because every clip
  is a single sub-30 s window, so there is no previous text and no fallback fired; this says nothing about long audio.
  Beam 1 saves 10-35% of time for about 0.15 points on turbo and large-v3 and 0.6 on medium.
- The Chinese prompt changed CER by at most 0.6 points (large-v3 and turbo slightly better, medium worse), but it cut
  medium's Traditional-script outputs from 11 to 5 of 60. Medium produces Traditional text most often (11/60 by default);
  Qwen almost never (1/60).
- No hallucinations on any configuration; short clean clips do not trigger them, so this does not clear the settings
  that guard against them on music or silence.

**What I would default to for Chinese.** Keep faster-whisper large-v3-turbo as the Whisper default: it is the fastest
(RTF 0.69), it is 1.4 points of CER behind large-v3, which takes 2.2x as long, and no setting change is supported by this data. large-v3 is the choice when accuracy matters more than time, and medium is not better than turbo on this data.
Qwen3-ASR 1.7B was clearly best on clean sentence-length clips, so it is worth offering for Chinese, but this data
cannot tell whether the lead holds on drama audio, long files or segmentation by VAD. Do not read the 0.1-0.3 point
differences between settings as rankings.

**Against the earlier private-clip finding** (one 3-minute drama clip, character recall against hardsubs: turbo 86.8%,
medium 83.2%, Qwen 1.7B 79.4%, large-v3 77.7%): the public data does not agree on the order. Here large-v3 is clearly
better than turbo and Qwen is best, while turbo is no better than medium. It agrees only that turbo is competitive
with the larger models and much faster. The two tests differ in audio (clean read speech vs a drama with music and
overlap), reference (written text vs hardsubs), metric (CER vs recall, which ignores insertions) and size (60 clips
vs one), so neither overrules the other. Check on more drama material before changing a default.

**Not run.** Beam sizes above 5, VAD threshold or silence settings, temperature fallback with long audio, fast mode,
GPU, a Simplified-forcing prompt for medium on its own, repeated runs for timing noise (RTF of large-v3 ranged
1.06 to 1.51 across settings that should cost about the same, so treat times as rough), and the 60-to-12-minute
shortfall in the brief (60 utterances are 10.5 min).

## Qwen3 speech-detection backend: closing the accuracy gap, and automatic language (2026-10-05)

**Why.** On single-sentence FLEURS utterances `Qwen3ASRVadBackend` scored worse than plain Qwen3-ASR on the whole file
(Korean, Chinese and Japanese above and in the brief), although the VAD has almost nothing to cut there.

**Data and scorer.** The same first 60 test rows as the Korean and Chinese sections (same parquet revision; sha256 of
`ko_kr` and `cmn_hans_cn` files matched those recorded above). Japanese (`ja_jp/test/0000.parquet`, sha256
`e954b67e934b9a31d7a74a070a75225379660d50c8d1aacdb755852c57f23e6b`, 784.6 s) has no section in this file, so its row ids
are here: 1828, 1834, 1813, 1869, 1744, 1731, 1910, 1771, 1764, 2003, 1980, 1822, 1837, 1736, 1980, 1942, 1733, 1916, 1701,
1848, 1775, 1893, 1908, 1972, 1911, 1664, 1827, 1672, 1861, 1843, 1924, 1958, 1749, 1997, 1825, 1801, 1739, 1977, 1989,
1869, 1667, 1742, 1876, 1943, 1718, 1725, 1690, 1955, 1959, 1721, 1978, 1968, 1718, 1974, 1920, 1733, 1902, 1718, 1750, 1983.
CPU, bfloat16, qwen-asr 0.0.6, 1.7B. CER is a plain Levenshtein over characters, with this file's normalisation (Korean and
Japanese: NFKC, lower-case, letters and digits only, reference `transcription`; Chinese: the Chinese section's
`raw_transcription` rules). My plain-Qwen numbers differ a little from the sections above (Korean 3.11 vs 3.34, Chinese
3.16 vs 3.60) because of the scorer (no jiwer) and bfloat16 CPU nondeterminism; compare rows within this table only.

**Stage-by-stage experiment.** Same utterances, same loaded model, forced language, one call per input, each step
changing one thing from the whole-file call:

| CER % | Korean | Chinese | Japanese |
|---|---|---|---|
| Plain: whole file, one call | 3.11 | 3.16 | 5.08 |
| One span: first speech start to last speech end (+100 ms), ffmpeg slice | 4.00 | 4.64 | 6.55 |
| Same, but slice taken from the full-precision waveform in memory (no 16-bit round trip) | 3.67 | 4.64 | 6.65 |
| Backend before: VAD spans, `merge_close` 0.3 s, `cap_spans` 15 s | 4.97 | 4.85 | 6.68 |
| Same, full-precision slices | 4.74 | 4.85 | 6.78 |
| **Backend after** (this change) | **3.34** | **3.78** | **5.42** |
| After, only utterances without digits (plain / before in brackets) | 2.54 (2.75 / 3.10) | 1.94 (1.94 / 2.22) | 4.32 (4.32 / 5.18) |

Not responsible: the hallucination filter and `split_long_segments` (output with and without them identical in all
60 x 3 files), language forcing, batch size and dtype (plain and the backend call the same `load_qwen3_asr` model with
the same forced language, one input per call). The 16-bit ffmpeg round trip costs 0.3 points on Korean only, whose FLEURS
audio is quiet (median peak 0.046 of full scale); on Chinese and Japanese it is zero.

**Root cause.** Cutting the clip tight to the detected speech removes the silence around it, and Qwen3-ASR then writes
numbers as words ("천구백사십년") instead of digits, which the FLEURS references count as errors; on utterances
without digits the gap is only 0.3 to 0.9 points. For Korean, splitting a sentence at an internal pause under about 1 s costs
about 1 more point (3.67 to 4.74).

**Fix** (`asr_backend.py`, `vad_segments.context_windows`): the model hears each span with up to 2 s of the surrounding
silence (never into a neighbouring span; lines keep the span's times), and the non-language-detecting path joins spans
separated by up to 1 s before the 15 s cap (0.3 s as before when detecting languages, so a quick change of speaker and language
is not merged). Tuning on digit-containing utterances (hypotheses with digits, plain / one span / +0.5 s / +1 s / +2 s):
Korean 13 / 11 / 9 / 12 / 12 of 15, Chinese 8 / 3 / 1 / 4 / 5 of 12, Japanese 23 / 17 / 20 / 22 / 23 of 24; 0.5 s of padding is
not enough (Korean got worse), 2 s is the largest tried. A 1 s or 2 s merge gap gave the same Korean result (3.82) and no
change on Chinese or Japanese.

**What is left.** Lower CER is better. The old backend was 1.5-1.8 points worse than plain; the new one is 0.2-0.6 points
worse (Korean +0.22, Chinese +0.61, Japanese +0.33). So the change removed most of the gap but not all of it: plain Qwen on
the whole file is still the best score in all three languages, and the new backend is the closest we have that also gives
timestamps. Against the old backend the improvement is real in all three (95% interval excludes zero: Korean -1.63, Chinese
-1.07, Japanese -1.26 points). Against plain only Japanese is clearly worse; Korean and Chinese could be noise at 60
utterances. Almost all of what is left comes from numbers: on utterances without digits the new backend ties plain (Chinese,
Japanese) or is within 0.2 points (Korean). Whether digits or spelled-out numbers are better for subtitles is a style
choice; FLEURS happens to use digits. Not tested: music, overlapping speakers, long files. Padding adds up to 4 s of audio
per span, so a run is a little slower on audio with long pauses.

**Automatic language (`language=None`).** One 318 s file: 24 FLEURS test utterances (rows 60-65 of `ko_kr`, `ja_jp`,
`cmn_hans_cn` and `en_us`, sha256 of `en_us` `6428a4d04d3aac29e16b45e039bb1470a8bd7aa334cf92f7984c9c520d1f234d`),
interleaved ko, ja, en, zh with 2 s of silence between. `Qwen3ASRVadBackend().transcribe(path, None)` returned 31 segments
and every one carried the right language (ko 8, ja 12, en 4, zh 7; 0 flagged). The Silero VAD found no speech in 3 of the 6
English utterances, so they have no lines; that is a speech-detection miss, not a language error. One file, one run, 4
languages: it shows the plumbing works, not a robust detection rate, and it is clean read speech with long silences.
