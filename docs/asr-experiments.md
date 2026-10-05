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

## Public benchmark: noisy and music-backed audio (2026-10-05)

Read speech with synthetic noise, music and reverb mixed in, scored against FLEURS reference transcripts. It answers "which intervention helps on degraded speech?", not "what is the error rate on real drama audio?". Clean-speech comparisons for Korean are in the section above; this one does not repeat them. (The Japanese and Chinese FLEURS clean-speech tables mentioned in the task brief are not in this file at the time of writing, so there is no clean-speech baseline here except the `clean` rows below.)

### Data

- **Speech:** FLEURS `google/fleurs` test splits `ko_kr`, `ja_jp`, `cmn_hans_cn` (CC BY 4.0), fetched without a token from the Hub parquet export, revision `168de341b3db6859a9bac1c50a2ef5e3b47647e0` (`refs/convert/parquet`), files `<lang>/test/0000.parquet`, sha256 `ko_kr` `1a8319fc61c7996e8c15acde633786de97054e28ae1e463eb13901716176a7ec` (the same file as the Korean section), `ja_jp` `e954b67e934b9a31d7a74a070a75225379660d50c8d1aacdb755852c57f23e6b`, `cmn_hans_cn` `87c0aebbe183f3a36ac87b5c3421b6ab57036824744ff695029a3f858e7622fd`. The dataset script was not run.
- **Rows:** the first 40 rows (0-39) of each file, so the Korean rows are the first 40 of the Korean section's 60. FLEURS `id`s in row order, Korean: 1883, 1929, 1701, 2000, 1801, 1662, 1705, 1959, 1715, 1907, 1850, 1702, 1826, 1743, 1993, 1829, 1992, 1742, 1907, 1849, 1709, 1845, 1674, 1765, 1677, 1662, 1716, 1757, 1708, 1771, 1996, 1910, 1894, 1804, 1697, 1666, 1701, 2005, 1776, 1866. Japanese: 1828, 1834, 1813, 1869, 1744, 1731, 1910, 1771, 1764, 2003, 1980, 1822, 1837, 1736, 1980, 1942, 1733, 1916, 1701, 1848, 1775, 1893, 1908, 1972, 1911, 1664, 1827, 1672, 1861, 1843, 1924, 1958, 1749, 1997, 1825, 1801, 1739, 1977, 1989, 1869. Chinese: 1906, 2006, 1883, 1852, 1734, 1890, 1721, 1869, 1805, 1965, 1903, 1953, 1830, 1910, 1902, 1874, 1763, 1837, 1905, 1772, 1779, 1792, 1780, 1962, 1884, 1958, 1871, 1738, 1698, 1989, 1725, 1712, 2002, 1804, 1661, 1700, 1951, 1697, 1821, 1971. Some ids repeat (different speakers, same sentence), so a row is identified by position.
- **Which rows were used where:** rows 0-19 per language (the **core set**, 60 utterances per condition) for every comparison. Rows 20-39 were added only for large-v3-turbo and Qwen3-ASR on raw audio (all conditions, 120 utterances), as a check on the core-set result. All audio is kept in a temp directory and is not committed.
- **Background material:** all synthesised locally with numpy/scipy, no external clips, nothing to licence-check. Each utterance gets its own deterministic seed.
  - *Noise:* pink plus brown noise at equal power.
  - *Music-like bed:* a random key and tempo (70-100 bpm), an Am-F-C-G pad of detuned additive-synth triads, a plucked bass, a pentatonic vibrato melody and soft off-beat noise "hats".
  - *Room:* a synthetic impulse response (exponentially decaying noise, RT60 0.55 s, faster high-frequency decay, four early reflections). The speech is convolved with it and re-scaled to the dry speech level; noise and music are added dry.
- **Conditions:** `clean`; `noise_*`; `music_*`; `both_*` (noise + music at equal power, plus reverberant speech); each at speech-to-background ratios +10, +5 and 0 dB (`_p10`, `_p5`, `_0`). The ratio is the RMS of the speech over Silero speech frames of the clean utterance, against the RMS of the background over those same samples (checked to ±0.01 dB). Every file, including `clean`, has 1.0 s of lead-in and tail added (digital silence for `clean`, background for the others) so the degraded files contain background-only stretches. Mixes are scaled down together if they would clip, which leaves the ratio unchanged. 28 minutes of audio per condition across the 120 files, of which about 17 minutes is speech.

### Method

CPU only (4 cores, 15 GB), one job at a time so timings do not contend, one run per cell. Versions: faster-whisper 1.2.1, ctranslate2 4.8.2, qwen-asr 0.0.6, transformers 4.57.6, torch 2.14.1+cpu, demucs 4.1.0, jiwer 4.0.0 (installed, not used for scoring; edit distance is `rapidfuzz` Levenshtein on characters, the same quantity). Model revisions: `Systran/faster-whisper-large-v3` `edaa852e`, `Systran/faster-whisper-medium` `08e178d4`, `mobiuslabsgmbh/faster-whisper-large-v3-turbo` `0a363e91`, `Qwen/Qwen3-ASR-1.7B` `7278e1e7`.

- **Whisper:** `core.load_whisper_model` (int8), then `model.transcribe` with the app's defaults as in the Korean section: language fixed per file, `vad_filter=True`, `min_silence_duration_ms=300`, `threshold=0.5`, `beam_size=5`, `word_timestamps=True`, `core.WHISPER_ANTI_LOOP_KWARGS`, default temperature fallback, no prompt unless stated. Each utterance is a separate call. The repeat-collapsing filter was not applied.
- **Qwen3-ASR plain:** `asr_backend.load_qwen3_asr(use_gpu=False)` (bfloat16), `model.transcribe` on the whole utterance with the language name from `forced_align.LANGUAGE_NAMES`. This is not the product path (`Qwen3ASRVadBackend`, which scored worse in the Korean section) and it returns no timestamps.
- **Vocal separation:** `audio_preprocess.separate_vocals_demucs` (Demucs `htdemucs`, 80 MB), then the vocals stem goes to the same ASR call. **The app's preferred backend, Mel-Band RoFormer, was not used for the results:** one timing run on a 14.5 s clip took 92 s including a 28 s model load (about RTF 4-6) on these 4 cores, which would have meant roughly 2 hours per condition. Demucs ran at RTF 0.44. So everything below says what *Demucs* does; RoFormer is cleaner on music beds per its own docs, but this experiment does not measure it. The Demucs RTF includes reloading the model for each file, as the app does per call.
- **Metric:** CER = total edit distance / total reference characters (corpus level, all languages pooled unless a table says otherwise). Reference = FLEURS `transcription`.
  - *Raw:* NFKC, lower-case, keep only letters and digits (punctuation and spaces removed), for reference and output.
  - *Normalised (the headline):* raw, then Chinese traditional to simplified (OpenCC `t2s`), and for Japanese and Chinese runs of kanji numerals converted to Arabic digits (`七十` to `70`, `二〇一一` to `2011`). Korean has no extra step, because spelled-out Korean numbers (`일만`) cannot be converted without ambiguity. So raw and normalised differ only for Japanese and Chinese, and by up to about 1 point in the table.
  - Hallucination = normalised output longer than 3 times the reference, or empty output counted separately.
- **Intervals:** 95% bootstrap CIs. A single cell's CI resamples its 60 utterances (2,000 resamples). Differences between two methods on the same utterances are paired (4,000 resamples); when several conditions are pooled the bootstrap resamples whole utterances, so one utterance's repeated conditions are not counted as independent. RTF = wall-clock seconds / audio seconds, including the padding, with Demucs time added for the "+ Demucs" rows.
- **Not committed:** the scripts that did this (audio synthesis, queue runner, scorer) are not in the repo because this change is docs only. The construction above, the seeds (SHA-256 of a string per utterance), and the settings are enough to rebuild them; say so if you want them under `scripts/`.

### Headline table (core set, 60 utterances per condition)

CER % with 95% CI is shown for every row. Rows `+ Demucs` include the separation time in RTF. large-v3 and medium were run only on the seven conditions below (no +5 dB).

| Condition | Method | n | CER % normalised (95% CI) | CER % raw | Halluc. / empty | RTF |
|---|---|---|---|---|---|---|
| clean | large-v3-turbo | 60 | 5.2 (3.7-6.9) | 5.2 | 0 / 0 | 0.31 |
| clean | large-v3-turbo + Demucs | 60 | 5.4 (3.9-7.2) | 5.5 | 0 / 0 | 0.77 |
| clean | large-v3 | 60 | 5.6 (3.6-7.8) | 5.7 | 0 / 0 | 0.69 |
| clean | large-v3 + Demucs | 60 | 5.1 (3.4-7.1) | 5.2 | 0 / 0 | 1.11 |
| clean | medium | 60 | 7.1 (5.0-9.4) | 7.8 | 0 / 0 | 0.45 |
| clean | Qwen3-ASR 1.7B plain | 60 | 3.1 (1.9-4.5) | 3.2 | 0 / 0 | 0.38 |
| noise_p10 | large-v3-turbo | 60 | 6.1 (4.5-7.9) | 6.1 | 0 / 0 | 0.31 |
| noise_p10 | large-v3-turbo + Demucs | 60 | 7.3 (5.6-9.2) | 7.3 | 0 / 0 | 0.76 |
| noise_p10 | large-v3 | 60 | 5.8 (4.0-8.0) | 6.0 | 0 / 0 | 0.67 |
| noise_p10 | large-v3 + Demucs | 60 | 6.0 (4.4-7.8) | 6.2 | 0 / 0 | 1.12 |
| noise_p10 | medium | 60 | 7.5 (5.8-9.5) | 8.3 | 0 / 0 | 0.44 |
| noise_p10 | Qwen3-ASR 1.7B plain | 60 | 4.7 (3.2-6.3) | 5.8 | 0 / 0 | 0.38 |
| noise_0 | large-v3-turbo | 60 | 14.4 (11.9-17.2) | 14.3 | 0 / 0 | 0.31 |
| noise_0 | large-v3-turbo + Demucs | 60 | 23.0 (19.2-27.2) | 23.1 | 0 / 0 | 0.77 |
| noise_0 | large-v3 | 60 | 12.5 (10.3-14.9) | 12.6 | 0 / 0 | 0.67 |
| noise_0 | large-v3 + Demucs | 60 | 20.3 (16.6-24.0) | 20.9 | 0 / 0 | 1.11 |
| noise_0 | medium | 60 | 18.4 (15.9-21.2) | 19.9 | 0 / 0 | 0.46 |
| noise_0 | Qwen3-ASR 1.7B plain | 60 | 10.8 (8.6-13.1) | 11.8 | 0 / 0 | 0.37 |
| music_p10 | large-v3-turbo | 60 | 6.2 (4.5-8.1) | 6.2 | 0 / 0 | 0.31 |
| music_p10 | large-v3-turbo + Demucs | 60 | 5.6 (4.0-7.4) | 5.6 | 0 / 0 | 0.75 |
| music_p10 | large-v3 | 60 | 5.5 (3.9-7.4) | 5.6 | 0 / 0 | 0.67 |
| music_p10 | large-v3 + Demucs | 60 | 5.1 (3.5-7.1) | 5.2 | 0 / 0 | 1.12 |
| music_p10 | medium | 60 | 6.8 (5.1-8.9) | 8.2 | 0 / 0 | 0.45 |
| music_p10 | Qwen3-ASR 1.7B plain | 60 | 3.3 (2.1-4.6) | 4.0 | 0 / 0 | 0.37 |
| music_0 | large-v3-turbo | 60 | 8.2 (6.4-10.1) | 8.2 | 0 / 0 | 0.31 |
| music_0 | large-v3-turbo + Demucs | 60 | 5.9 (4.3-7.7) | 5.9 | 0 / 0 | 0.75 |
| music_0 | large-v3 | 60 | 7.0 (5.3-9.0) | 7.2 | 0 / 0 | 0.65 |
| music_0 | large-v3 + Demucs | 60 | 5.4 (3.8-7.2) | 5.6 | 0 / 0 | 1.09 |
| music_0 | medium | 60 | 10.3 (8.1-12.7) | 11.9 | 0 / 0 | 0.44 |
| music_0 | Qwen3-ASR 1.7B plain | 60 | 4.3 (2.8-6.1) | 5.1 | 0 / 0 | 0.37 |
| both_p10 | large-v3-turbo | 60 | 16.1 (13.2-19.4) | 16.4 | 0 / 0 | 0.32 |
| both_p10 | large-v3-turbo + Demucs | 60 | 18.7 (15.8-22.0) | 19.5 | 0 / 0 | 0.75 |
| both_p10 | large-v3 | 60 | 12.8 (10.5-15.1) | 13.3 | 0 / 0 | 0.67 |
| both_p10 | large-v3 + Demucs | 60 | 16.3 (13.5-19.3) | 16.6 | 0 / 0 | 1.12 |
| both_p10 | medium | 60 | 18.6 (15.7-21.6) | 20.6 | 0 / 0 | 0.45 |
| both_p10 | Qwen3-ASR 1.7B plain | 60 | 8.9 (6.7-11.2) | 9.9 | 0 / 0 | 0.38 |
| both_0 | large-v3-turbo | 60 | 56.1 (49.8-62.4) | 56.8 | 0 / 0 | 0.38 |
| both_0 | large-v3-turbo + Demucs | 60 | 58.8 (52.1-65.4) | 59.0 | 0 / 1 | 0.84 |
| both_0 | large-v3 | 60 | 52.0 (45.1-59.3) | 53.2 | 0 / 1 | 0.79 |
| both_0 | large-v3 + Demucs | 60 | 57.3 (50.5-64.1) | 57.3 | 0 / 5 | 1.09 |
| both_0 | medium | 60 | 60.2 (53.6-66.8) | 62.0 | 0 / 7 | 0.58 |
| both_0 | Qwen3-ASR 1.7B plain | 60 | 43.9 (37.1-50.6) | 44.7 | 0 / 0 | 0.35 |

The +5 dB conditions, for the three models that ran there:

| Condition | Method | n | CER % normalised (95% CI) | CER % raw | Halluc. / empty | RTF |
|---|---|---|---|---|---|---|
| noise_p5 | large-v3-turbo | 60 | 8.3 (6.5-10.5) | 8.3 | 0 / 0 | 0.32 |
| noise_p5 | large-v3-turbo + Demucs | 60 | 11.7 (9.5-14.0) | 11.7 | 0 / 0 | 0.78 |
| noise_p5 | Qwen3-ASR 1.7B plain | 60 | 6.6 (4.7-8.8) | 7.6 | 0 / 0 | 0.38 |
| music_p5 | large-v3-turbo | 60 | 7.2 (5.4-9.3) | 7.1 | 0 / 0 | 0.32 |
| music_p5 | large-v3-turbo + Demucs | 60 | 5.4 (3.9-7.3) | 5.5 | 0 / 0 | 0.76 |
| music_p5 | Qwen3-ASR 1.7B plain | 60 | 3.5 (2.4-4.8) | 4.2 | 0 / 0 | 0.38 |
| both_p5 | large-v3-turbo | 60 | 28.2 (23.8-33.2) | 28.6 | 0 / 0 | 0.33 |
| both_p5 | large-v3-turbo + Demucs | 60 | 32.9 (28.2-38.3) | 33.2 | 0 / 0 | 0.77 |
| both_p5 | Qwen3-ASR 1.7B plain | 60 | 17.2 (13.7-20.8) | 17.9 | 0 / 0 | 0.36 |

Hardest condition (`both_0`): every method is above 40% CER, so differences there carry large intervals and the model ranking should be read from the other rows.

### 1. Vocal separation on vs off

Δ is separated minus raw, in CER points (negative = separation helps), paired.

| Condition | turbo raw | turbo + Demucs | Δ sep−raw (95% CI) | large-v3 raw | large-v3 + Demucs | Δ sep−raw (95% CI) |
|---|---|---|---|---|---|---|
| clean | 5.2 | 5.4 | +0.23 (+0.08 to +0.43) | 5.6 | 5.1 | -0.43 (-1.72 to +0.40) |
| noise_p10 | 6.1 | 7.3 | +1.21 (+0.53 to +1.93) | 5.8 | 6.0 | +0.20 (-0.61 to +0.95) |
| noise_p5 | 8.3 | 11.7 | +3.36 (+1.69 to +5.20) | - | - | - |
| noise_0 | 14.4 | 23.0 | +8.60 (+6.19 to +11.21) | 12.5 | 20.3 | +7.78 (+5.00 to +10.79) |
| music_p10 | 6.2 | 5.6 | -0.55 (-1.24 to +0.12) | 5.5 | 5.1 | -0.39 (-0.88 to +0.08) |
| music_p5 | 7.2 | 5.4 | -1.80 (-2.86 to -0.85) | - | - | - |
| music_0 | 8.2 | 5.9 | -2.31 (-3.31 to -1.35) | 7.0 | 5.4 | -1.60 (-2.65 to -0.65) |
| both_p10 | 16.1 | 18.7 | +2.58 (+0.41 to +4.74) | 12.8 | 16.3 | +3.56 (+1.69 to +5.53) |
| both_p5 | 28.2 | 32.9 | +4.61 (+2.12 to +7.38) | - | - | - |
| both_0 | 56.1 | 58.8 | +2.70 (-1.08 to +6.69) | 52.0 | 57.3 | +5.24 (+1.07 to +9.51) |

Per background type, pooled over the ratios present for both models (utterance-level bootstrap):

| Background | turbo Δ sep−raw | large-v3 Δ sep−raw |
|---|---|---|
| noise | +4.91 (+3.67 to +6.33) | +3.99 (+2.46 to +5.71) |
| music | -1.43 (-2.02 to -0.91) | -1.00 (-1.62 to -0.41) |
| both | +2.64 (+0.39 to +4.87) | +4.40 (+2.26 to +6.46) |

Per language, pooled over all degraded conditions (the seven conditions run for large-v3, without clean; turbo / large-v3, Δ sep−raw):

| Language | turbo | large-v3 |
|---|---|---|
| ko_kr | +1.57 (-0.18 to +3.32) | +2.67 (+0.86 to +4.58) |
| ja_jp | +2.75 (+1.64 to +3.96) | +1.94 (+0.35 to +3.51) |
| cmn_hans_cn | +1.60 (-0.24 to +3.36) | +2.97 (+1.50 to +4.52) |

Separation cost: Demucs htdemucs RTF 0.44 on the same 4 CPU cores (included in the '+ Demucs' RTF above).

### 2. VAD settings (large-v3-turbo, one setting changed at a time)

Conditions `both_0`, `music_0`, `both_p5` (0 and +5 dB), 60 utterances each. The utterances are single sentences of about 12 s, so the silence-length setting rarely has anything to split.

| Setting | both_0 CER % | music_0 CER % | both_p5 CER % | Pooled CER % | Δ vs default (95% CI) | Halluc. / empty |
|---|---|---|---|---|---|---|
| default (300 ms, 0.5) | 56.1 | 8.2 | 28.2 | 30.8 | - | 0 / 0 |
| silence 100 ms | 56.2 | 8.2 | 28.4 | 30.9 | +0.10 (+0.00 to +0.27) | 0 / 0 |
| silence 1000 ms | 56.2 | 8.2 | 28.4 | 30.9 | +0.10 (-0.47 to +0.76) | 0 / 0 |
| threshold 0.3 | 54.6 | 8.4 | 28.9 | 30.6 | -0.18 (-1.14 to +0.76) | 0 / 0 |
| threshold 0.7 | 62.8 | 9.1 | 30.2 | 34.1 | +3.23 (+1.65 to +5.12) | 0 / 1 |

### 3. Turbo vs large-v3 vs medium vs Qwen3-ASR

Paired differences, CER points (first minus second), 95% CI from an utterance-level bootstrap; 60 utterances x conditions pooled as stated.

| Comparison | clean | ratio +10 dB (3 types) | ratio 0 dB (3 types) |
|---|---|---|---|
| large-v3 − turbo | +0.35 (-0.74 to +1.72) | -1.41 (-2.31 to -0.64) | -2.37 (-3.87 to -0.96) |
| medium − turbo | +1.88 (+0.75 to +3.19) | +1.53 (+0.35 to +2.66) | +3.44 (+1.81 to +5.00) |
| large-v3 − medium | -1.53 (-3.27 to +0.37) | -2.93 (-4.13 to -1.84) | -5.81 (-7.33 to -4.31) |
| Qwen3 − turbo | -2.07 (-3.57 to -0.78) | -3.82 (-5.88 to -1.97) | -6.52 (-9.52 to -3.78) |
| Qwen3 − large-v3 | -2.42 (-4.57 to -0.72) | -2.41 (-4.30 to -0.71) | -4.15 (-6.89 to -1.65) |

Same comparison per background type at 0 dB:

| Comparison | noise | music | both + reverb |
|---|---|---|---|
| large-v3 − turbo | -1.88 (-3.50 to -0.35) | -1.17 (-2.43 to +0.04) | -4.07 (-8.44 to +0.48) |
| medium − turbo | +4.03 (+2.13 to +5.77) | +2.15 (+0.65 to +3.74) | +4.15 (+0.46 to +7.73) |
| large-v3 − medium | -5.91 (-7.84 to -4.08) | -3.32 (-4.83 to -1.92) | -8.21 (-12.11 to -3.84) |
| Qwen3 − turbo | -3.56 (-6.90 to -0.54) | -3.83 (-6.04 to -1.47) | -12.16 (-18.31 to -6.52) |
| Qwen3 − large-v3 | -1.68 (-4.55 to +1.11) | -2.66 (-4.80 to -0.36) | -8.10 (-14.17 to -2.19) |

Per language, degraded conditions pooled (CER %; the six degraded conditions run for large-v3):

| Language | turbo | large-v3 | medium | Qwen3 |
|---|---|---|---|---|
| ko_kr | 15.5 | 13.9 | 18.4 | 14.4 |
| ja_jp | 15.6 | 14.8 | 20.0 | 12.9 |
| cmn_hans_cn | 24.3 | 20.2 | 23.3 | 9.9 |

Same at clean:

| Language | turbo | large-v3 | medium | Qwen3 |
|---|---|---|---|---|
| ko_kr | 2.4 | 2.2 | 3.1 | 1.8 |
| ja_jp | 5.2 | 7.4 | 8.5 | 3.2 |
| cmn_hans_cn | 9.0 | 7.2 | 10.2 | 4.8 |

The 40-utterance (120 per condition) check for turbo against Qwen3, which agrees with the core set:

40-utterance (extended) check for turbo vs Qwen3 on every condition (CER % normalised, 120 utterances):

| Condition | turbo | Qwen3 | Δ Qwen3−turbo (95% CI) |
|---|---|---|---|
| clean | 6.2 | 4.4 | -1.84 (-2.75 to -0.99) |
| noise_p10 | 7.0 | 5.9 | -1.09 (-2.34 to +0.13) |
| noise_p5 | 9.4 | 8.0 | -1.47 (-3.22 to +0.23) |
| noise_0 | 15.7 | 13.4 | -2.33 (-4.69 to -0.17) |
| music_p10 | 6.8 | 4.8 | -1.94 (-3.10 to -0.85) |
| music_p5 | 7.8 | 4.9 | -2.85 (-4.02 to -1.72) |
| music_0 | 9.1 | 5.7 | -3.38 (-4.99 to -1.65) |
| both_p10 | 17.5 | 10.2 | -7.29 (-9.68 to -4.99) |
| both_p5 | 29.2 | 18.7 | -10.51 (-13.62 to -7.60) |
| both_0 | 58.2 | 44.4 | -13.81 (-18.01 to -9.97) |

### 4. Names in the initial prompt (large-v3-turbo)

Prompts are built with `core.build_initial_prompt` (names joined with 、 and ended with 。, the form `build_auto_initial_prompt` produces). 20 utterances per language; the terms were chosen by hand from the reference text (proper nouns, product names and numbers that appear in it): 46 utterances have at least one term, 78 term mentions in all. *Oracle* = only the terms of the utterance being transcribed. *Shared* = the union of the terms of all 20 utterances of that language (23-26 terms), which is closer to a drama glossary. *Wrong* = 12 names taken from other FLEURS sentences (rows 20-39) of the same language, none in the 20 test references. Recall = the normalised term appears in the normalised output.

Condition clean (turbo, 60 utterances; 78 term mentions in 46 utterances):

| Prompt | CER % all utts (95% CI) | Δ vs none (95% CI) | CER % on utts with names | Name recall (hits/mentions) |
|---|---|---|---|---|
| no prompt | 5.2 (3.7-6.9) | - | 5.5 | 79% (62/78) |
| names in this utterance (oracle) | 4.5 (3.2-6.0) | -0.74 (-1.48 to -0.18) | 4.6 | 90% (70/78) |
| shared glossary of the 20 utterances | 5.0 (3.6-6.8) | -0.16 (-0.92 to +0.55) | 5.3 | 90% (70/78) |
| wrong names | 5.1 (3.6-6.8) | -0.08 (-0.61 to +0.50) | 5.4 | 79% (62/78) |

Condition both_0 (turbo, 60 utterances; 78 term mentions in 46 utterances):

| Prompt | CER % all utts (95% CI) | Δ vs none (95% CI) | CER % on utts with names | Name recall (hits/mentions) |
|---|---|---|---|---|
| no prompt | 56.1 (49.8-62.4) | - | 57.1 | 19% (15/78) |
| names in this utterance (oracle) | 52.2 (45.8-58.7) | -3.83 (-6.85 to -0.89) | 52.1 | 68% (53/78) |
| shared glossary of the 20 utterances | 53.5 (46.9-60.6) | -2.62 (-6.12 to +0.65) | 53.7 | 41% (32/78) |
| wrong names | 60.7 (52.4-69.9) | +4.58 (+0.23 to +10.04) | 62.3 | 15% (12/78) |

Recall per language, no prompt vs oracle vs shared vs wrong at both_0 and clean:

| Language | no prompt | names in this utterance (oracle) | shared glossary of the 20 utterances | wrong names |
|---|---|---|---|---|
| ko_kr | 48% | 75% | 57% | 48% |
| ja_jp | 57% | 93% | 81% | 56% |
| cmn_hans_cn | 41% | 67% | 57% | 37% |

Oracle recall is partly the prompt handing the model the answer, so it is a ceiling, not what a glossary gives. The term list is mine, so the recall numbers depend on what I counted as a name.

### 5. Turbo/Qwen3 agreement as a confidence signal

420 (utterance, condition) cells: 7 conditions x 60 utterances. Agreement = 1 − edit distance between the normalised turbo and Qwen3 outputs / longer length.

Spearman correlation of agreement with per-utterance CER: turbo -0.80, Qwen3 -0.60, large-v3 -0.71 (negative = low agreement goes with high CER).

Lowest-agreement 20% = agreement <= 0.74 (84 cells).

| | Lowest 20% | Rest | All |
|---|---|---|---|
| turbo CER % | 50.8 | 8.2 | 16.0 |
| Qwen3 CER % | 38.6 | 5.1 | 11.3 |
| large-v3 CER % | 45.4 | 7.5 | 14.5 |

Share of cells in the lowest 20%, by condition: clean 0%, noise_p10 2%, noise_0 17%, music_p10 0%, music_0 3%, both_p10 25%, both_0 93%.

Re-running lines with large-v3 (simulated with the measured large-v3 outputs; cost in RTF counts turbo + Qwen3 for the signal, plus large-v3 on the re-run share):

| Policy | Base | Re-run share | CER % (95% CI) | Cost, RTF |
|---|---|---|---|---|
| turbo only | turbo | 0% | 16.0 (14.1-18.1) | 0.32 |
| large-v3 only | large-v3 | 100% | 14.5 (12.6-16.6) | 0.69 |
| Qwen3 only | Qwen3 | 0% | 11.3 (9.5-13.1) | 0.37 |
| re-run lowest-agreement 20% | turbo | 20% | 15.0 (13.1-17.1) | 0.83 |
| re-run a random 20% (mean of 200 draws) | turbo | 20% | 15.7 | 0.46 (no Qwen3 needed) |
| re-run the 20% with the highest CER (oracle) | turbo | 20% | 15.0 (13.1-17.0) | 0.46 |
| re-run lowest-agreement 20% | Qwen3 | 20% | 12.5 (10.6-14.6) | 0.83 |
| re-run a random 20% (mean of 200 draws) | Qwen3 | 20% | 11.9 | 0.46 (no Qwen3 needed) |
| re-run the 20% with the highest CER (oracle) | Qwen3 | 20% | 11.2 (9.3-13.2) | 0.46 |

Fixed agreement thresholds (no ranking over the pool needed), base = turbo:

| Re-run when agreement < | Re-run share | CER % | Cost, RTF |
|---|---|---|---|
| 0.5 | 11% | 15.5 | 0.76 |
| 0.7 | 19% | 15.0 | 0.82 |
| 0.8 | 23% | 14.8 | 0.85 |
| 0.9 | 45% | 14.4 | 1.00 |
| 0.95 | 63% | 14.3 | 1.13 |

Same signal on the clean condition alone (for how it behaves when audio is easy): 10% of clean cells have agreement < 0.90; 97% at both_0.

### Findings

1. **Vocal separation: leave it off by default.** With Demucs it helped only when the background was music alone (turbo -2.3 points, large-v3 -1.6 at 0 dB; at +10 dB the gain is -0.5 and -0.4, within noise). With noise it made things much worse (+8.6 and +7.8 points at 0 dB), with noise plus music plus reverb worse still (+2.6 to +5.2), and on clean speech it changed nothing meaningful. It also adds RTF 0.44. In the app, keep `separate_vocals_first` off and describe it as "background is music only"; do not suggest it for noisy or echoey sources.
2. **VAD settings: keep 300 ms and 0.5.** Silence 100 or 1000 ms changed CER by +0.1 (no effect), threshold 0.3 by -0.2 (within noise), threshold 0.7 by +3.2 points (worse). No setting beat the defaults by more than noise, and the sweep covers only three conditions with single-sentence utterances; it says nothing about long recordings.
3. **Turbo vs large-v3: large-v3 wins on degraded audio, not on clean.** Clean: +0.4 points (no difference). Background at +10 dB: -1.4 points (interval excludes zero); at 0 dB: -2.4. The advantage is largest with noise plus music plus reverb (12.8% vs 16.1% at +10 dB). It costs about 2.2 times the time (RTF 0.67 vs 0.31). A reasonable app change is to keep turbo as the default and offer large-v3 as a per-drama option for sources with game audio, crowd noise or echo. Medium was worst everywhere and has no reason to be chosen.
4. **The clean-speech ranking does not fully hold.** Qwen3-ASR plain was lowest at every ratio and every background, and its lead grows as the audio gets worse (against turbo: -2.1 points clean, -3.8 at +10 dB, -6.5 at 0 dB; -13.8 at `both_0` on 120 utterances). The gap is largest on Chinese (9.9% vs 24.3% pooled over degraded conditions). Do not read this as "switch the default to Qwen3": it was run on whole single-sentence utterances with no timestamps, while the product path (Whisper segment boundaries, or the VAD backend) was worse than plain in the Korean section and did not beat Whisper on the Japanese stream clip. The useful follow-up is to test Qwen3 re-transcribing Whisper segments on degraded audio.
5. **A glossary prompt helps only if its names are the right ones, and the right ones only help a little on clean audio.** Oracle names: -0.7 points on clean, -3.8 at `both_0`; recall 79% to 90% on clean and 19% to 68% at `both_0`. A shared glossary recovers part of it (recall 90% on clean, 41% at `both_0`; CER difference not distinguishable from zero). Wrong names did nothing on clean audio (-0.1 points), but on the hard audio raised CER by +4.6 points (interval +0.2 to +10.0) without improving recall; in one `both_0` Korean line the output was the wrong-name list itself, the only hallucination (output over three times the reference) in any run. So keep the prompt to names that really occur in the series, keep it short, and do not pad it with unrelated names, especially for bad audio.
6. **Agreement between turbo and Qwen3 is a good error indicator but a poor reason to re-run.** It correlates with CER (Spearman -0.80 with turbo's CER, -0.60 with Qwen3's), and the lowest-agreement 20% of lines have 51% CER against 8% for the rest. But 93% of the `both_0` lines fall in that bottom 20% and none of the clean lines, so it mostly says "this file is bad", and re-running those lines with large-v3 improves turbo from 16.0% to 15.0% at RTF 0.83, which is slower than running large-v3 on everything (14.5% at 0.69) and worse than Qwen3 alone (11.3%). The oracle that re-runs the true worst 20% gets the same 15.0%, because large-v3 itself has 45% CER on those lines. Ignore it as a routing rule; at most, flag low-agreement lines for human review if both models are already being run.
7. **Ignore for now:** the silence-length slider as a tuning target, threshold above 0.5, medium, and automatic separation.
8. **Hallucinations were rare.** Across 5,340 transcriptions there was one output over three times the reference length (the wrong-name prompt echoing its list, above), and 17 empty outputs, all in `both_0` (medium 7 of 60, large-v3 + Demucs 5, one each for large-v3, turbo + Demucs, and the oracle, shared and threshold-0.7 turbo runs). Background-only stretches of one second did not trigger loops, so this benchmark says nothing about long silence or music, where Whisper loops.

### Limits

- Read speech with synthetic backgrounds, not drama or stream audio: no overlapping speakers, no emotion, no real music with vocals, and a synthetic music bed that is easier for a separator and for Whisper to tell apart from speech than real mixes. The absolute CERs, and the `both_0` row especially (where the combined noise, music and reverb is far harsher than most real tracks), are not product error rates.
- One run per cell. Whisper's temperature fallback is random, so a re-run would move cells by a fraction of a point; do not read differences under about 1 point. RTF comes from one run per cell on a shared container; compare it roughly.
- Core set is 60 utterances per cell; the 120-utterance check covers only turbo and Qwen3. large-v3 and medium were not run at +5 dB. The VAD sweep used three conditions and the prompt test two. The Chinese and Japanese per-language splits are 20 utterances each.
- Separation is Demucs, not the app's preferred RoFormer. The prompt term list is hand-picked by one person (me). Korean spelled-out numbers are not normalised.
- CPU only, int8 for Whisper, bfloat16 for Qwen3; no GPU numbers.
