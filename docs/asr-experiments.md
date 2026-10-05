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

### Public benchmark: mixed languages (2026-10-05)

Question: which transcription settings cope best with several spoken languages
in one recording (ko, ja, zh, en)? The test uses public audio whose true
language per utterance is known.

**Data.** FLEURS test splits (`google/fleurs`, CC BY 4.0) for `ko_kr`, `ja_jp`,
`cmn_hans_cn`, `en_us`, read from the Hugging Face parquet export
(`refs/convert/parquet`) with no token. Pool: 30 utterances per language
(shuffled with a fixed seed, kept if 4-15 s long). 20 of the 30 were used. Four recordings,
20 utterances each, joined with 0.3-1.0 s of silence:
`mix1/2/3` (seeds 11/22/33, 5 utterances per language in random order, 210-239 s)
and `hard` (seed 44, ko, ja, zh, en repeating, so ja and zh sit side by side, 227 s).
The utterances were disjoint across recordings. The files and their ground truth
(language, time span, FLEURS id, text) live in a temp directory, not the repo.

**Settings.** CPU only (4 cores, int8), faster-whisper 1.2.1, greedy decoding
(`beam_size=1`, for time; the app default is 5), the app's anti-loop kwargs
(`core.WHISPER_ANTI_LOOP_KWARGS`), and Whisper's own VAD (2000 ms) for the
whole-file runs. (d) builds spans with
`vad_segments.cap_spans(merge_close(speech_spans(...)))` (<= 15 s), runs
`detect_language` on each span, takes the best of ko/ja/zh/en, and transcribes the span in
that language. Qwen3-ASR 1.7B ran in bfloat16 on CPU with `language=None`
(automatic). The repo's Qwen backends reject a missing language, so the harness
mapped a placeholder `"auto"` to `None` in memory; no repo code changed.
"Plain" is `Qwen3ASRBackend`, which re-reads Whisper's segments; here those were
medium (b)'s segments.

**Scoring.** Each output segment goes to the truth utterance it overlaps most;
under 30% overlap with any utterance counts as a hallucinated line. A segment's
script is the dominant script of its letters; it is "wrong" when outside the
true language's set (ko: Hangul; ja: kana or Han; zh: Han; en: Latin).
Cyrillic or other scripts would be counted separately (none appeared). CER
(ko, ja, zh; traditional mapped to simplified) and WER (en) are computed only on utterances that no
output segment spans together with a neighbour ("correctly aligned"), after
dropping punctuation and case. A boundary is missed when one segment overlaps
both neighbouring utterances. Ja and zh share Han, so script cannot tell them
apart; only CER shows that confusion.

| Config | CER ko % | CER ja % | CER zh % | WER en % | utts scored (of 80) | wrong-script segs | boundaries missed (of 76) | hallucinated lines | wall-clock, 4 files (min) |
|---|---|---|---|---|---|---|---|---|---|
| medium (a) auto-detect once | 75 | 88 | 94 | 100 | 32 | 51/93 (55%) | 34 | 0 | 7 |
| medium (b) multilingual=True | 55 | 84 | 80 | 96 | 33 | 30/92 (33%) | 34 | 0 | 4 |
| medium (c) fixed ko | 63 | 100 | 100 | 100 | 31 | 81/109 (74%) | 42 | 0 | 9 |
| medium (c) fixed ja | 102 | 81 | 89 | 100 | 30 | 43/80 (54%) | 34 | 0 | 4 |
| medium (c) fixed zh | 100 | 96 | 52 | 100 | 43 | 37/100 (37%) | 27 | 0 | 4 |
| medium (d) per-span detect ko/ja/zh/en | 6 | 14 | 10 | 32 | 80 | 0/101 (0%) | 0 | 0 | 11 |
| large-v3-turbo (a) auto-detect once | 44 | 100 | 126 | 49 | 12 | 53/98 (54%) | 56 | 1 | 5 |
| large-v3-turbo (b) multilingual=True | 13 | 100 | 154 | 47 | 10 | 71/137 (52%) | 59 | 2 | 3 |
| large-v3-turbo (c) fixed ko | 13 | 100 | 102 | 64 | 12 | 101/142 (71%) | 60 | 2 | 4 |
| large-v3-turbo (c) fixed ja | 100 | 37 | 92 | 73 | 11 | 62/126 (49%) | 59 | 2 | 3 |
| large-v3-turbo (c) fixed zh | 100 | 7 | 5 | 85 | 8 | 88/202 (44%) | 65 | 0 | 4 |
| large-v3-turbo (d) per-span detect ko/ja/zh/en | 4 | 11 | 11 | 24 | 80 | 0/102 (0%) | 0 | 0 | 14 |
| large-v3 (a) auto-detect once | 59 | 91 | 71 | 72 | 20 | 68/97 (70%) | 49 | 0 | 8 |
| large-v3 (b) multilingual=True | 43 | 68 | 61 | 100 | 13 | 27/75 (36%) | 52 | 2 | 4 |
| large-v3 (c) fixed ko | not run | | | | | | | | |
| large-v3 (c) fixed ja | not run | | | | | | | | |
| large-v3 (c) fixed zh | not run | | | | | | | | |
| large-v3 (d) per-span detect ko/ja/zh/en | 4 | 10 | 8 | 24 | 80 | 0/101 (0%) | 0 | 0 | 30 |
| Qwen3-ASR 1.7B plain (re-reads Whisper-medium (b) segments) | 55 | 84 | 72 | 91 | 33 | 6/92 (7%) | 34 | 0 | 7 |
| Qwen3ASRVadBackend 1.7B, auto language | 4 | 11 | 8 | 24 | 80 | 0/115 (0%) | 0 | 0 | 8 |

Whole-file CER/WER rest on 1-20 utterances (the "utts scored" column), because
the others were merged with a neighbour or dropped, so do not compare those
cells with each other; rely on the wrong-script and missed-boundary columns for them. The
three (c) rows for large-v3 were not run (time). Wall-clock
times are rough: the runs shared the machine with other jobs and one VM restart,
and a fixed-language run that drops speech looks faster than it is. For (d),
0 wrong-script and 0 missed boundaries are largely by construction (VAD splits at the
gaps, and the language was detected right); they say nothing independent about
transcription quality. Per-span language detection: medium 102/102, turbo
102/102, large-v3 101/102 (one ja span read as ko); no ko/ja/zh/en confusion
elsewhere. File-level detection, medium: ko, ko, ja, zh for mix1, mix2, mix3, hard
(true mix: all four languages in every recording).
large-v3-turbo: ko, ko, en, en. large-v3: ko for all four.

**Guard check** (retry a span once in the title's language if its output script
is outside the title's script plus Latin). On the (d) spans it fired zero
times, so it fixed nothing: detection was already right. To probe the case where
detection is not, a stress run cut the first 2 s of every utterance (80 clips),
detected and transcribed them, and applied the guard:

| 2 s clips, 80 each | detection correct | wrong-script outputs | guard retries that fixed something |
|---|---|---|---|
| medium | 48/80 | 12/80 | 0 |
| large-v3-turbo | 48/80 | 24/80 | 1 (title ko: 9 to 8) |

(large-v3 not run on the stress clips.) Nearly all misses were ko, ja or zh
speech labelled `en` (medium 32, turbo 30). The guard cannot see this, because
an English-looking output is inside the allowed set when English is allowed. This
is the weak point of the idea; it only catches output in an unexpected script (for
example kana on a Korean title). Restricting detection to the title language
plus en does not help either, since `en` is the wrong answer.

**Recommendations** (clean read speech, utterances concatenated rather than
switching naturally, no music or noise, about 20 utterances per language, one
speaker set; differences of a few CER points between the best rows are within noise).

- Transcribe each VAD span separately and pass its own language: ko 4-6%, ja
  10-14%, zh 8-11% CER, en 24-32% WER (digits and names hurt English here).
  Medium, large-v3-turbo, large-v3 and Qwen3ASRVadBackend land in the same range; do not pick
  among them from this data.
- Qwen3ASRVadBackend in automatic language mode matched the Whisper per-span rows
  with the fewest steps and was the fastest of the good rows here (8 min, CPU). It has no
  language setting to get wrong, but it was not checked on short clips.
- Avoid one language setting for a whole mixed file: auto-detect once gave 54-70%
  wrong-script segments, and a fixed language rewrote the other languages into it
  (ja/zh/en CER about 100% under fixed ko on medium).
- `multilingual=True` was the best whole-file option for medium and large-v3
  (33-36% wrong-script) but is still unusable on its own, and turbo was no better (52%).
- Whole-file runs also merge utterances (27-65 of 76 boundaries missed) even with
  0.3-1.0 s gaps; span-first processing avoids this.
- Do not rely on the script guard for the common failure: short or ambiguous spans
  detected as English. Prefer longer spans (merge close ones), and try confidence
  thresholds, or the title's language for low-confidence spans, in a harder test.
- Qwen "plain" over Whisper's segments inherits Whisper's merged boundaries
  (34/76 missed) and is not a substitute for the VAD backend.
- Not tested: music or noise, overlapping or very short speech, natural
  code-switching inside a sentence, accented or telephone audio, GPU speed.
