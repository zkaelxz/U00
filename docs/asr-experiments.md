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
