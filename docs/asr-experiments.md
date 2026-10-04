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
