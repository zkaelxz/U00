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

## Public benchmark: Japanese (2026-10-04)

Question: which speech-recognition model and settings should Japanese default to? Measured on public, clean read speech with reference transcripts. Results are CPU-only. Nothing here uses drama audio.

### Data
- **FLEURS Japanese** (`google/fleurs`, config `ja_jp`, test split, CC BY 4.0), fetched from the Hugging Face Hub anonymously (no token). The dataset script was not needed: I used the Hub's parquet export `https://huggingface.co/api/datasets/google/fleurs/parquet/ja_jp/test/0.parquet`, revision `refs/convert/parquet` = `168de341b3db6859a9bac1c50a2ef5e3b47647e0` (dataset `main` = `70bb2e84b976b7e960aa89f1c648e09c59f894dd`).
- **First 60 rows** of that file: 784.6 s (13.1 min) of audio, 3,010 reference characters after normalisation. Each clip is one file, transcribed separately, as the app does for one clip. Audio lived in a temp directory, not the repo.
- Reference text is `raw_transcription` (with punctuation), normalised the same way as the output.
- FLEURS `id` is a sentence id and repeats across speakers (60 rows, 55 distinct ids), so each row is identified by row index, id and audio filename:

<details><summary>Utterances (row index, id, audio file)</summary>

```
00 1828 10020345318418093976.wav
01 1834 10086073819084529371.wav
02 1813 10112379500600537933.wav
03 1869 10174984673310355687.wav
04 1744 10193386983118883224.wav
05 1731 10203675650837159328.wav
06 1910 10214476888438240131.wav
07 1771 10228875750894489453.wav
08 1764 10256418415643044307.wav
09 2003 10281646305484957883.wav
10 1980 10300959819649327821.wav
11 1822 1030930262884292674.wav
12 1837 10362598397973084151.wav
13 1736 1036356791308610933.wav
14 1980 10401481013809636276.wav
15 1942 10413033184319592580.wav
16 1733 10440318583992984584.wav
17 1916 10447557607007750141.wav
18 1701 10457758295680448532.wav
19 1848 10460389341605783008.wav
20 1775 10476284249516566719.wav
21 1893 10508563126832413352.wav
22 1908 10595483280529141687.wav
23 1972 10683203810610128933.wav
24 1911 10710420115318518641.wav
25 1664 10728607480570481117.wav
26 1827 10746508960655553792.wav
27 1672 10770212904668726521.wav
28 1861 10772833950120547925.wav
29 1843 10778761596814084242.wav
30 1924 10781591397234148217.wav
31 1958 10930212786007012251.wav
32 1749 1093334804512710473.wav
33 1997 10944803082099949565.wav
34 1825 10969509778910365671.wav
35 1801 10993407044765711136.wav
36 1739 1100824364670689611.wav
37 1977 11038445467160388040.wav
38 1989 11057063102259415865.wav
39 1869 11111871579326016200.wav
40 1667 11129391205542167306.wav
41 1742 11136364513873191636.wav
42 1876 11142098240457961087.wav
43 1943 11160903593055178935.wav
44 1718 11179370060482213220.wav
45 1725 11226361286302510768.wav
46 1690 11241695122085346615.wav
47 1955 11258446014622421491.wav
48 1959 11277998077872294009.wav
49 1721 11308293308024967180.wav
50 1978 11341239153936752827.wav
51 1968 11349680767362432921.wav
52 1718 11354811853117839821.wav
53 1974 11359030312765783730.wav
54 1920 11411683150648548702.wav
55 1733 11424574386275194933.wav
56 1902 11428890487331938751.wav
57 1718 11435168072299453493.wav
58 1750 11489399237032065700.wav
59 1983 11513130081392489537.wav
```
</details>

### Environment
Intel Xeon 2.8 GHz, 4 vCPU, 15 GB RAM, CPU only. Python 3.11.15, faster-whisper 1.2.1, ctranslate2 4.8.2, qwen-asr 0.0.6, torch 2.14.1+cpu, transformers 4.57.6, jiwer 4.0.0. Models were downloaded from the Hub on 2026-10-04 and are not pinned to a revision (Systran `faster-whisper-*` for Whisper, `Qwen/Qwen3-ASR-1.7B`). Whisper ran as the app runs it on a CPU: int8. Qwen ran in bfloat16 (the `load_qwen3_asr` default), `batch_size=1`, language ja, `use_gpu=False`. Memory was never a constraint.

### App defaults (recorded before varying anything)
Read from `services/transcribe_service.py` and `core.transcribe_for_timing`:
- Model: `large-v3-turbo` with the GPU on, `medium` on a CPU (`default_whisper_size`).
- `beam_size` 5, VAD on (Silero) with `min_silence_duration_ms` 300 and `threshold` 0.5, word timestamps on, `condition_on_previous_text` False, `no_repeat_ngram_size` 3, `repetition_penalty` 1.1 (`WHISPER_ANTI_LOOP_KWARGS`), faster-whisper's default temperature fallback list, no initial_prompt unless the drama has glossary terms (none here), then `filter_hallucinated_segments`.

### Method
- Whisper rows call `core.load_whisper_model` and `core.transcribe_for_timing` unchanged. Settings that function does not expose (VAD off, `condition_on_previous_text`, temperature) are applied by wrapping `model.transcribe` to override those keyword arguments; `beam_size` and `initial_prompt` go through the function's own arguments. One setting changes per row, against the app defaults above.
- The Japanese prompt is generic (`これは日本語の文章を読み上げた音声です。`). FLEURS has no proper nouns to prime, so this does not test the glossary-name case the prompt exists for.
- "Qwen3-ASR plain" is `load_qwen3_asr(...).transcribe(audio=clip, language="Japanese")` on the whole clip, with no segmentation. "App path" is `asr_backend.Qwen3ASRVadBackend(model_size="1.7B").transcribe(clip, "ja", use_gpu=False, batch_size=1)`, which adds Silero VAD spans, the 15 s cap, `split_long_segments` and `filter_hallucinated_segments`.
- Timing: sequential, one configuration at a time, 4 threads. Model load and one untimed warm-up clip (row 0) are excluded. RTF = wall-clock seconds over the 784.6 s of audio.
- **CER:** `jiwer` 4.0.0 `process_characters` (substitutions + deletions + insertions at character level). Normalisation: NFKC, casefold, drop whitespace and every Unicode punctuation (category P*) character. Kana and kanji are not converted, and numerals are not (so "30" vs "三十" and "80km" vs "80キロメートル" count as errors). Corpus CER = total edits / total reference characters; per-utterance CER is in the harness output.
- **Confidence intervals:** 2,000 bootstrap resamples of the 60 utterances (seed 0), 95% percentile interval on corpus CER. The "vs same-model default" column is a paired bootstrap of the CER difference in percentage points, same resamples for both rows.
- **Hallucination count:** normalised output longer than 3x the normalised reference (or any output when the reference is empty; no reference here is empty). Empty outputs were also counted and were 0 for every row.

### Results

CER is corpus-level, percent. CI is the 95% bootstrap interval. Δ is the paired difference in percentage points vs the same model's app defaults, with its 95% interval (a negative value is better).

| Model + setting | CER % | 95% CI | Δ vs same-model defaults (pp) | Hallucinations | RTF |
|---|---|---|---|---|---|
| Whisper large-v3-turbo, app defaults | 6.61 | 4.9-8.6 | (baseline) | 0 | 0.56 |
| Whisper large-v3-turbo, VAD off | 6.61 | 4.9-8.6 | +0.00 [-0.44, +0.38] | 0 | 0.56 |
| Whisper large-v3-turbo, beam 1 | 6.68 | 5.1-8.4 | +0.07 [-0.70, +0.65] | 0 | 0.52 |
| Whisper large-v3-turbo, condition_on_previous_text on | 6.61 | 4.9-8.6 | +0.00 [+0.00, +0.00] | 0 | 0.58 |
| Whisper large-v3-turbo, no temperature fallback | 6.61 | 4.9-8.6 | +0.00 [+0.00, +0.00] | 0 | 0.58 |
| Whisper large-v3-turbo, Japanese initial_prompt | 6.71 | 4.8-9.3 | +0.10 [-0.75, +1.20] | 0 | 0.58 |
| Whisper medium, app defaults | 8.47 | 6.6-10.7 | (baseline) | 0 | 0.84 |
| Whisper medium, VAD off | 8.80 | 6.9-11.0 | +0.33 [-0.46, +1.29] | 0 | 0.84 |
| Whisper medium, beam 1 | 8.44 | 6.7-10.3 | -0.03 [-1.01, +0.84] | 0 | 0.57 |
| Whisper medium, condition_on_previous_text on | 8.47 | 6.6-10.7 | +0.00 [+0.00, +0.00] | 0 | 0.84 |
| Whisper medium, no temperature fallback | 8.47 | 6.6-10.7 | +0.00 [+0.00, +0.00] | 0 | 0.84 |
| Whisper medium, Japanese initial_prompt | 8.84 | 6.9-11.0 | +0.37 [-0.40, +1.10] | 0 | 0.86 |
| Whisper large-v3, app defaults | 6.54 | 4.8-8.7 | (baseline) | 0 | 1.31 |
| Whisper large-v3, VAD off | 7.64 | 5.2-10.8 | +1.10 [-0.14, +2.75] | 0 | 1.30 |
| Whisper large-v3, beam 1 | 8.04 | 5.3-11.4 | +1.50 [+0.00, +3.55] | 0 | 0.96 |
| Whisper large-v3, condition_on_previous_text on | not run | | | | |
| Whisper large-v3, no temperature fallback | not run | | | | |
| Whisper large-v3, Japanese initial_prompt | 6.25 | 4.5-8.1 | -0.30 [-0.83, +0.17] | 0 | 1.32 |
| Qwen3-ASR 1.7B plain (whole clip, no VAD) | 5.38 | 3.8-7.3 |  | 0 | 1.20 |
| `Qwen3ASRVadBackend` 1.7B (app path) | 6.64 | 4.7-8.9 |  | 0 | 1.07 |

Pairwise, paired bootstrap on CER (pp, 95% interval): Qwen plain vs turbo defaults -1.23 [-3.35, +0.92]; Qwen plain vs large-v3 defaults -1.16 [-3.44, +1.11]; Qwen plain vs the app-path Qwen backend -1.26 [-2.76, -0.07]; large-v3 vs turbo defaults -0.07 [-0.90, +0.79]; medium vs turbo defaults +1.86 [+0.82, +3.07]; app-path Qwen vs turbo defaults +0.03 [-1.67, +1.86].

Skipped: large-v3 with `condition_on_previous_text` on and with temperature fallback off. Both had output identical to the defaults on turbo and medium, and each large-v3 run takes about 17 min. Nothing else in the planned grid was skipped. Vocal separation, `min_silence_duration_ms`, `vad_threshold`, `fast_mode` and the GPU were not varied.

### Reading the numbers
- Only one gap is clearly real: medium is worse than turbo and large-v3 by about 1.9 pp CER (interval excludes zero), and slower than turbo (RTF 0.84 vs 0.56). On this data the CPU default (`medium`) is worse on both accuracy and speed than `large-v3-turbo`.
- large-v3-turbo and large-v3 are indistinguishable (-0.07 pp), while large-v3 is about 2.3x slower. The "weaker on Japanese/Korean" note on `WHISPER_MODELS["large-v3-turbo"]` is not supported by clean speech; it may still hold for harder audio.
- Qwen3-ASR plain has the lowest CER (5.38%) but its interval overlaps the Whisper models and the gap to turbo is not significant. It is about 2x slower than turbo (RTF 1.20). The app-path Qwen backend equals turbo (6.64% vs 6.61%) and is worse than plain Qwen by 1.3 pp (interval just excludes zero). Spot checks of the largest differences are mostly orthography (Arabic vs kanji numerals, `km` vs `キロメートル`, katakana vs kanji spellings), which CER counts as errors; I did not isolate the cause of the plain vs app-path gap.
- For Japanese on this data I would default to `large-v3-turbo`, VAD on, beam 5, no prompt: it matches large-v3 at about 43% of the time. On a CPU I would also pick it over `medium`, subject to the model download (about 1.6 GB vs 1.5 GB) and the larger memory use, neither of which I measured here.
- Settings: none moved CER beyond noise on turbo or medium. `beam_size` 1 cut time by about 7% (turbo) and 32% (medium) at no measurable accuracy cost (intervals about ±0.8 pp), so it is an option for speed, but on large-v3 it was 1.5 pp worse (interval [0.00, +3.55]), so keep 5 there. VAD off made no difference on turbo or medium and was 1.1 pp worse on large-v3 (interval includes zero); keep it on, which also matters for music and silence that this data lacks. The generic prompt was within noise on all three models (-0.30 to +0.37 pp).
- `condition_on_previous_text` and the temperature fallback are untested in practice: every clip fits in one decoding window and the fallback never triggered (identical outputs on turbo and medium), so these rows say nothing. They need long-form audio.
- Hallucinations were 0 in every configuration, expected on clean speech with no silence or music.
- This is clean, single-speaker read speech. It understates drama audio (overlapping speech, music, noise, quiet or emotional delivery, long silences), where VAD, hallucination handling, the anti-loop settings and the prompt are expected to matter far more. Treat these results as a model-ranking sanity check, not a tuning result.
- 60 clips (3,010 characters) is small: intervals on a single configuration are about ±1.5 to ±2 pp, so differences under about 1 pp should not be acted on. Timings come from one run each on a shared 4-vCPU VM.

<details><summary>Harness (<code>run_bench.py</code> and <code>score.py</code>)</summary>

Setup used: `python -m venv --system-site-packages`, `pip install faster-whisper imageio-ffmpeg pyarrow soundfile numpy jiwer`, `pip install torch --index-url https://download.pytorch.org/whl/cpu`, `pip install qwen-asr`. `ffmpeg` was already on PATH. The first 60 parquet rows were written to `wav/NN.wav` and `refs.json` (`idx`, `id`, `file`, `ref`, `dur`). Then `python run_bench.py "<model>|<setting>" ...` for each row, and `python score.py`.

`run_bench.py`:

```python
import json, os, sys, time
sys.path.insert(0, "/home/user/U00")
S = os.path.dirname(os.path.abspath(__file__))
refs = json.load(open(f"{S}/refs.json"))
JA_PROMPT = "これは日本語の文章を読み上げた音声です。"
os.makedirs(f"{S}/results", exist_ok=True)

def whisper_cfg(size, name):
    over = {}      # extra faster-whisper kwargs, applied under the app's own call
    call = dict(beam_size=5, min_silence_duration_ms=300, initial_prompt="")
    if name == "novad": over["vad_filter"] = False
    if name == "beam1": call["beam_size"] = 1
    if name == "condprev_on": over["condition_on_previous_text"] = True
    if name == "temp0": over["temperature"] = 0.0
    if name == "prompt_ja": call["initial_prompt"] = JA_PROMPT
    return over, call

def run_whisper(size, name):
    import core
    model = core.load_whisper_model(size, use_gpu=False)
    over, call = whisper_cfg(size, name)
    orig = model.transcribe
    def patched(audio, **kw):
        kw.update(over)
        return orig(audio, **kw)
    model.transcribe = patched
    def one(i):
        return core.transcribe_for_timing(f"{S}/wav/{i:02d}.wav", size, language="ja", use_gpu=False, **call)
    try:
        one(0)  # warm-up, untimed
        out = []; t0 = time.time()
        for r in refs:
            segs = one(r["idx"]); out.append("".join(s["text"] for s in segs))
        return out, time.time() - t0
    finally:
        model.transcribe = orig

def run_qwen_plain():
    import asr_backend
    model = asr_backend.load_qwen3_asr(use_gpu=False, model_size="1.7B")
    def one(i):
        res = model.transcribe(audio=f"{S}/wav/{i:02d}.wav", language="Japanese")
        return res[0].text if res else ""
    one(0)
    out = []; t0 = time.time()
    for r in refs: out.append(one(r["idx"]))
    return out, time.time() - t0

def run_qwen_vad():
    import asr_backend
    b = asr_backend.Qwen3ASRVadBackend(model_size="1.7B")
    def one(i):
        segs = b.transcribe(f"{S}/wav/{i:02d}.wav", "ja", use_gpu=False, batch_size=1)
        return "".join(s["text"] for s in segs)
    one(0)
    out = []; t0 = time.time()
    for r in refs: out.append(one(r["idx"]))
    return out, time.time() - t0

def main(keys):
    for key in keys:
        path = f"{S}/results/{key}.json"
        if os.path.exists(path): continue
        print(time.strftime("%H:%M:%S"), "start", key, flush=True)
        if key == "qwen_plain": hyp, wall = run_qwen_plain()
        elif key == "qwen_vad": hyp, wall = run_qwen_vad()
        else:
            size, name = key.split("|"); hyp, wall = run_whisper(size, name)
        json.dump({"key": key, "wall": wall, "hyp": hyp}, open(path, "w"), ensure_ascii=False)
        print(time.strftime("%H:%M:%S"), "done", key, round(wall), "s", flush=True)

if __name__ == "__main__":
    main(sys.argv[1:])
```

`score.py`:

```python
import json, glob, os, unicodedata, random, sys
import jiwer
S = os.path.dirname(os.path.abspath(__file__))
refs = json.load(open(f"{S}/refs.json")); TOTAL = sum(r["dur"] for r in refs)

def norm(t):
    t = unicodedata.normalize("NFKC", t or "").casefold()
    return "".join(c for c in t if not c.isspace() and not unicodedata.category(c).startswith("P"))

R = [norm(r["ref"]) for r in refs]

def edits(h, r):
    if not h: return len(r)
    o = jiwer.process_characters(r, h)
    return o.substitutions + o.deletions + o.insertions

def score(hyp):
    H = [norm(h) for h in hyp]
    e = [edits(h, r) for h, r in zip(H, R)]
    n = [len(r) for r in R]
    halluc = sum(1 for h, r in zip(H, R) if (not r and h) or (r and len(h) > 3 * len(r)))
    empty = sum(1 for h in H if not h)
    return e, n, halluc, empty

def cer(e, n, idx): return sum(e[i] for i in idx) / sum(n[i] for i in idx)

def boot(e, n, B=2000, seed=0):
    rng = random.Random(seed); k = len(e); out = []
    for _ in range(B):
        idx = [rng.randrange(k) for _ in range(k)]
        out.append(cer(e, n, idx))
    out.sort(); return out[int(.025 * B)], out[int(.975 * B)]

def paired(e1, e0, n, B=2000, seed=1):
    rng = random.Random(seed); k = len(e1); d = []
    for _ in range(B):
        idx = [rng.randrange(k) for _ in range(k)]
        d.append(cer(e1, n, idx) - cer(e0, n, idx))
    d.sort(); return d[int(.025 * B)], d[int(.975 * B)]

res = {}
for p in sorted(glob.glob(f"{S}/results/*.json")):
    d = json.load(open(p)); e, n, h, em = score(d["hyp"]); res[d["key"]] = (d, e, n, h, em)
out = []
for key, (d, e, n, h, em) in res.items():
    lo, hi = boot(e, n)
    base = key.split("|")[0] + "|default"
    dl = ""
    if "|" in key and not key.endswith("|default") and base in res:
        a, b = paired(e, res[base][1], n); dl = f"{(cer(e,n,range(60))-cer(res[base][1],n,range(60)))*100:+.2f} [{a*100:+.2f}, {b*100:+.2f}]"
    out.append(dict(key=key, cer=cer(e, n, range(60)) * 100, lo=lo * 100, hi=hi * 100, halluc=h, empty=em,
                    wall=d["wall"], rtf=d["wall"] / TOTAL, delta=dl, utt_cer=[ (e[i]/n[i]) for i in range(60)]))
json.dump(out, open(f"{S}/summary.json", "w"), ensure_ascii=False, indent=1)
print(f"{'config':32} {'CER%':>6} {'95% CI':>14} {'hall':>4} {'empty':>5} {'wall_s':>7} {'RTF':>5}  delta vs default (pp, paired CI)")
for o in out:
    print(f"{o['key']:32} {o['cer']:6.2f} [{o['lo']:5.2f},{o['hi']:5.2f}] {o['halluc']:4d} {o['empty']:5d} {o['wall']:7.0f} {o['rtf']:5.2f}  {o['delta']}")
```
</details>
