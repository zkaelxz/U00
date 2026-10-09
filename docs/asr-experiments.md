# ASR options and transcription benchmarks

How to turn on each transcription option, what it needs, and what went wrong
before. How the backends fit together is in `engine-backends.md` section 3, not
here. Checked against the code on `baihe-subtitler` at `7bad622` (2026-10-08).

Numbers are only in "Measured results (snapshot)" at the end. They were measured
once, on one machine, before some defaults changed. Do not read them as current
behaviour. The full original write-up (data ids, hashes, harness scripts, every
table) is in `archive/asr-experiments-history.md`.

## Options

| Option | Where | Default | Notes |
|---|---|---|---|
| Whisper model | per title (Workspace) | `large-v3-turbo`, same on CPU and GPU | `core.DEFAULT_WHISPER_SIZE`. A model saved on a title is never replaced. |
| ASR backend | per title | `whisper`, except as below | `whisper`, `qwen3_asr`, `qwen3_asr_vad`, `qwen3_asr_long`. |
| Qwen3-ASR batch size | Settings > Transcription experiments (PC only) | 1 | Range 1-16. |
| Refine line timing with the forced aligner | same card | off | |
| Mixed languages | same card | off | |
| Separate vocals first | per title | off | |

Saving the three experiment settings is PC-only (`local_only()`); another device
sees "PC only". They are stored in `app_settings` (`services/asr_options_service.py`).

### Which backend a title gets

A saved `asr_backend_choice` wins. A title with none saved (or a removed one) gets
`qwen3_asr_long` when its source language is Chinese or Japanese, it does not use
Groq, and `qwen_asr`, `torch` and `faster_whisper` are all installed. Everything
else gets `whisper` (`stored_asr_backend`).

| Backend | Boundaries and timing | Needs |
|---|---|---|
| `whisper` | Whisper's | `faster-whisper` |
| `qwen3_asr` | Whisper's; Qwen3-ASR replaces only the text. Cannot add lines Whisper missed. | `qwen-asr`, `torch`, plus Whisper's pass (`faster-whisper`, or Groq) |
| `qwen3_asr_vad` | Silero speech spans (about 15 s cap); the forced aligner only if "Refine line timing" is on | `qwen-asr`, `torch`, `faster-whisper` |
| `qwen3_asr_long` | Gentler speech detection, windows up to 30 s, one line per sentence, forced aligner always on | same |

### Batch size

- Used by `qwen3_asr` and by the non-mixed path of the two speech-detection backends.
  Timing is not affected, only speed.
- Written against qwen-asr **0.0.6** (`asr_backend.QWEN_ASR_BATCH_TESTED_VERSION`). With
  any other version installed, one segment goes per call whatever the saved size;
  the Settings card says which applies.
- Results are matched back by segment index. A batch that raises (for example CUDA
  out of memory) or returns the wrong number of results is redone one segment at a time.
- Qwen3 ASR keeps Whisper's own text for a segment longer than 300 s
  (`SEGMENT_DURATION_WARNING_SECONDS`).
- No before/after comparison of batched vs unbatched text is recorded in the repo.
  Unverified: that batching leaves the text unchanged on real audio.

### Refine line timing

Only for `qwen3_asr_vad`. After transcribing, Qwen3-ForcedAligner tightens each
line inside its span; lines whose timing had to be estimated are flagged. Slower.
`qwen3_asr_long` always aligns. Ignored when Mixed languages is on (the aligner takes
one language per run).

### Mixed languages

For recordings where people speak more than one of Korean, Chinese, Japanese and
English. Slower: one language detection per speech span.

- Applies to the `whisper` backend (not with Groq) and to the speech-detection
  backends; the long backend then runs like `qwen3_asr_vad`, because detection needs
  short spans. `qwen3_asr` and Groq transcribe the whole file in one language.
- Language is decided per speech span. Whisper's detection is limited to the allowed
  languages (`mixed_language.pick_allowed_language`); the speech-detection backends have
  Qwen3-ASR detect it, and a language outside the four is retried.
- The text's script is checked against the language. A span that disagrees is retried
  once in the title's language; if it still disagrees the line keeps its text and gets
  the `language_uncertain` flag.
- A line gets `lang` only when it differs from the title's language.
- `Qwen3ASRVadBackend.transcribe(..., language=None)` is the same detection with no
  title language: every line gets its detected `lang`.

### Separate vocals first

Leave it off unless the background is music alone; the Transcribe stage says so.
Backend `auto` tries Mel-Band RoFormer (`audio-separator`) first and falls back to
Demucs. RoFormer was very slow on 4 CPU cores in one timing (see the snapshot).

## Which Whisper model to pick

Default `large-v3-turbo`. It was about twice as fast as large-v3 and close in
accuracy on clean speech, and `medium` was never clearly ahead of it (Chinese clean: 6.84 vs 7.15, within noise), so the CPU default is
turbo too. Details by language (numbers in the snapshot):

| Language | What the tests showed |
|---|---|
| Korean | large-v3 was 0.5 points better than turbo (interval excludes zero), about twice as slow. Turbo and medium could not be told apart. |
| Japanese | Turbo and large-v3 indistinguishable on clean speech; medium about 1.9 points worse. No evidence turbo is weaker. |
| Chinese | Mixed. Clean speech: large-v3 better than turbo by 0.6-2.4 points. One drama clip: turbo ahead of large-v3. Medium wrote Traditional characters far more often. |
| Mixed languages | Per-span detection beat a fixed language or whole-file auto-detect for all three sizes. |
| Degraded audio | large-v3 beat turbo by 1.4 points at +10 dB and 2.4 at 0 dB; no difference on clean. Medium was worst everywhere. |

Download size: turbo ~1.6 GB, medium ~1.5 GB, large-v3 ~3 GB
(`transcribe_service._MODEL_DOWNLOAD_SIZES`).

## Guidance from the benchmarks

Each row rests on the snapshot below (read speech, 60 clips per cell, CPU; the noisy-audio rows use 20 per language), so
differences under about 1 point are noise.

| Topic | Guidance | Current default |
|---|---|---|
| Speech-detection settings | Keep 300 ms and threshold 0.5. Silence 100/1000 ms changed nothing; threshold 0.7 was 3.2 points worse. Tested on single sentences only. | 300 ms, 0.5 |
| Beam size | 1 saved 7-35% of time with no clear accuracy cost in most cells. Exceptions: medium Korean +0.5, large-v3 Japanese +1.5 points (borderline). | 5 |
| Initial prompt | Use only names that occur in the series and keep it short. A prompt of the utterance's own names (a ceiling, not a glossary) gave -0.7 points on clean audio and -3.8 on very bad audio; a shared glossary was not distinguishable from no prompt; unrelated names did nothing on clean audio and added 4.6 points on very bad audio. | glossary feeds it |
| Mixed audio | Do not use one fixed language or whole-file auto-detect on a mixed file; use Mixed languages. | off |
| Qwen3 numbers | Cut tight to the speech, Qwen3-ASR writes numbers as words instead of digits. The speech-detection backend therefore gives it up to 2 s of surrounding silence per span (`CONTEXT_PAD_S`), never into a neighbouring span, and `qwen3_asr_vad` joins spans up to 1 s apart (0.3 s when detecting languages); `qwen3_asr_long` joins spans up to 3 s apart (`LONG_MERGE_GAP_S` in `asr_backend.py`). Lines keep their span's times. | built in |
| Vocal separation | Helped only with music-only backgrounds; hurt with noise (+8 points at 0 dB) and adds RTF 0.44 (Demucs). | off |

## Known failure modes

| Symptom | Cause and fix |
|---|---|
| Whisper repeats a phrase after music or silence | `condition_on_previous_text=False` (`WHISPER_ANTI_LOOP_KWARGS`) and `filter_hallucinated_segments` guard it. The stronger repeat guard (`no_repeat_ngram_size`, `repetition_penalty`) is the per-title `whisper_repeat_guard` toggle, off by default because a 3-token CJK sequence is often one common particle. |
| "Only N% of the audio has text" | `asr_backend.coverage_warning`: speech missed. Try another engine, turn vocal separation on, or check the language. With `qwen3_asr` it cannot add lines Whisper missed. |
| Wrong script or empty lines on a mixed recording | A fixed language rewrote the other languages into its script. Turn on Mixed languages. |
| Mixed languages still wrong on short spans | Short or ambiguous spans were detected as English. The script check cannot see this (English output is allowed). Longer spans help. |
| Chinese output in Traditional characters (medium most often) | Use turbo or large-v3. A Chinese initial prompt cut it in one test. |
| Qwen3-ASR will not load | Diagnostics warns when transformers 5 or newer is installed with qwen-asr (use 4.57.6 or uninstall), and on Windows when the data folder path has non-ASCII characters (move it to a plain path). |
| Qwen3-ASR install fails building `sox` | See "Installing qwen-asr". |
| Batch size has no effect | Installed qwen-asr is not 0.0.6, Mixed languages is on (one span per call), or the backend is `whisper`. |

## Installing qwen-asr

Diagnostics > Packages > Install runs `python -m pip install --no-cache-dir
--disable-pip-version-check qwen-asr` with the app's own interpreter, plus `-c
constraints.txt` when that file exists and a temporary constraints file pinning the
installed torch family (pip refuses a package that would replace torch). The `sox`
fallback below passes the same constraints. Optional packages install from PyPI at click time. The Windows
installer's hash-pinned `wheels/` cover `requirements-core.txt` only.

qwen-asr 0.0.6 depends on `sox`, a source-only package pip must build. That fails when
build tools are missing, too old or unreachable. Baihe does not use `sox`, so when the
install fails building it the app installs the other dependencies
(`diagnostics.QWEN_ASR_FALLBACK_DEPS`) and then `qwen-asr --no-deps`. If that fails too,
the result carries `SOX_BUILD_HINT`: update `pip setuptools wheel`, check the
connection or proxy, retry.

Manual check on Windows:

1. Diagnostics > Packages. If Qwen3-ASR is installed, uninstall it first
   (`python -m pip uninstall qwen-asr sox`).
2. Install Qwen3-ASR (several GB with PyTorch). Either the plain install finishes, or
   the output starts with "Installing Qwen3-ASR without its `sox` dependency" and then
   finishes. A red result shows the hint above.
3. Run `python -c "import qwen_asr.inference.qwen3_asr"` in Baihe's Python, then select
   Qwen3-ASR on a short clip and transcribe; the package row shows installed.

## Speaker detection on the GPU (manual check)

1. Settings: turn on "Use the GPU for transcription".
2. Open a title with audio > Transcribe > Speakers > **Detect speakers only**. While it
   runs, `nvidia-smi -l 1` should show a `python` process with GPU memory in use.
3. Afterwards the Speakers section reads "Last Detect speakers run (pyannote) used the
   GPU." (`diarization_turns.json` has `"device": "cuda"`).
4. Turn the setting off, run it again; the line says it used the CPU.

## Evaluating a change

Use the same reviewed clips for every run (narration, dialogue, music under speech,
quiet speech, overlapping speakers). Record transcription error (CER against the
reviewed text) and diarization error (speaker labels) separately, plus wall-clock
time and peak GPU memory (`nvidia-smi --query-gpu=memory.used --format=csv -l 1`).
To judge batching, run Qwen3 ASR at batch size 1, 4 and 8 on the same clips; adopt
it only if no clip's text gets worse. `asr_benchmark.py` compares Whisper and
Qwen3-ASR on one clip.

## Removed

The MOSS-Transcribe-Diarize backend (`moss_td`) was removed on 2026-10-07. A title
saved with it opens with its default backend (see "Which backend a title gets") and a
notice; the saved value is left as it was (`REMOVED_ASR_BACKENDS`).

## Measured results (snapshot)

Measured **2026-10-04 and 05** on one CPU-only container (4 cores, 15 GB; Whisper int8,
Qwen3-ASR 1.7B bfloat16), one run per cell, with faster-whisper 1.2.1, ctranslate2
4.8.2, qwen-asr 0.0.6, transformers 4.57.6, torch 2.14.1+cpu. Sources: the public
FLEURS test splits (`google/fleurs`, CC BY 4.0, first 60 rows per language for the clean
runs, read speech; the noisy runs used the first 20 per language, and the mixed
recordings 20 random 4-15 s utterances per language) plus three private clips that are
not in the repo. The scripts are not in the
repo either, so none of this can be re-run from here. I could not verify any figure
below against code or data in this repo; the exact sources, row ids, file hashes and
harness are in the archive file.

Changed since: on 2026-10-07 the anti-loop settings
`no_repeat_ngram_size=3` and `repetition_penalty=1.1` moved out of the default
(`WHISPER_ANTI_LOOP_KWARGS` is now only `condition_on_previous_text=False`; the rest
is the opt-in repeat guard), and the CPU default model is now turbo (it was medium
when these were run). The Whisper rows below used the old settings. The
speech-detection backend rows predate the context padding and merge-gap fix (second
table).

### Clean read speech, 60 utterances per language

CER %, corpus level, lower is better. RTF is wall time over audio time (lower is faster).
Gaps under about 1 point are within the 95% intervals.

| Model | Korean CER | Japanese CER | Chinese CER | RTF ko / ja / zh |
|---|---|---|---|---|
| Whisper large-v3-turbo | 4.60 | 6.61 | 7.15 | 0.36 / 0.56 / 0.69 |
| Whisper medium | 4.93 | 8.47 | 6.84 | 0.53 / 0.84 / 0.94 |
| Whisper large-v3 | 4.08 | 6.54 | 5.71 | 0.88 / 1.31 / 1.51 |
| Qwen3-ASR 1.7B, whole clip | 3.34 | 5.38 | 3.60 | 0.47 / 1.20 / 1.13 |
| `Qwen3ASRVadBackend` (before the fix below) | 4.82 | 6.64 | 5.30 | 0.47 / 1.07 / 1.02 |

The Whisper settings tried one at a time (VAD off, beam 1, condition on previous
text, no temperature fallback, a generic language prompt) did not clearly beat the
defaults anywhere. The largest moves, all with intervals touching zero except medium beam 1 Korean
(+0.5, +0.04 to +1.12), large-v3 without VAD (+1.0 Korean, +1.1 Japanese), large-v3 beam 1
(+1.5 Japanese). Condition-on-previous-text and the temperature fallback changed
nothing because every clip fits one 30 s window.

### Speech-detection backend: padding and merge-gap fix

Same 60 rows, a different scorer (plain Levenshtein, so plain Qwen differs slightly
from the table above). CER %.

| | Korean | Chinese | Japanese |
|---|---|---|---|
| Qwen3-ASR, whole clip | 3.11 | 3.16 | 5.08 |
| Backend before (tight spans) | 4.97 | 4.85 | 6.68 |
| Backend after (2 s context, 1 s merge gap) | 3.34 | 3.78 | 5.42 |

Most of what was left came from numbers written as words. Padding of 0.5 s was not
enough; 2 s was the largest tried. With `language=None`, one 318 s file of 24 utterances
in four languages came back as 31 segments, every one with the right language; speech
detection missed 3 of 6 English utterances. One file shows the plumbing, not a rate.

### Drama and stream clips (private, character recall against hardsubs, higher is better)

| Clip | Result |
|---|---|
| Japanese, 3 min cooking stream, 92 reference lines | large-v3 69.1%, turbo 67.6%, medium 58.5%; Qwen3 over medium's segments 57.6%; speech-detection Qwen3 (old) 51.8% |
| Chinese, 3 min drama | turbo 86.8%, medium 83.2%, Qwen3 1.7B 79.4%, large-v3 77.7% |

The Japanese reference was read by eye from burned-in subtitles, so it is not ground
truth. The Chinese clip disagrees with the clean-speech order above; neither overrules
the other. A 131 s Korean/Japanese/English clip had no usable reference.

### Mixed-language audio

80 FLEURS utterances (ko, ja, zh, en, 20 each) joined into four recordings of 210-239 s.

| Approach | Wrong-script segments | Utterance boundaries missed (of 76) |
|---|---|---|
| Auto-detect once, per file | 54-70% | 34-56 |
| `multilingual=True` | 33-52% | 34-59 |
| Fixed language | 37-74% | 27-65 |
| Detect per speech span (turbo, medium, large-v3) | 0% | 0 |
| `Qwen3ASRVadBackend`, automatic language | 0% | 0 |

Per-span CER was ko 4-6%, ja 10-14%, zh 8-11%, en WER 24-32% for all of those; do not
pick among them from this data. Per-span language detection was right on 101-102 of 102
spans. The 0 for per-span approaches is largely by construction (the speech detector
splits at the gaps). A stress test on 2 s clips detected the language correctly on only
48 of 80, mostly CJK speech labelled English. The script check did not help
(0-1 retries fixed something).

### Noisy and music-backed audio

FLEURS ko/ja/zh, 60 utterances, synthetic noise, music bed and reverb mixed in at
speech-to-background ratios of +10, +5 and 0 dB ("both" = noise + music + reverb).
CER % (normalised); "+ Demucs" runs Demucs vocal separation before the same model.

| Condition | turbo | turbo + Demucs | large-v3 | medium | Qwen3-ASR (whole clip) |
|---|---|---|---|---|---|
| clean | 5.2 | 5.4 | 5.6 | 7.1 | 3.1 |
| noise, 0 dB | 14.4 | 23.0 | 12.5 | 18.4 | 10.8 |
| music, 0 dB | 8.2 | 5.9 | 7.0 | 10.3 | 4.3 |
| both, +10 dB | 16.1 | 18.7 | 12.8 | 18.6 | 8.9 |
| both, 0 dB | 56.1 | 58.8 | 52.0 | 60.2 | 43.9 |
| RTF | 0.31 | 0.77 | 0.69 | 0.45 | 0.38 |

- Qwen3-ASR was lowest everywhere and its lead grew with the noise. It ran on whole
  single-sentence clips with no timestamps, so this is not the product path.
- Demucs, not the app's preferred RoFormer, was used: RoFormer took 92 s for a 14.5 s
  clip (about RTF 4-6) on 4 CPU cores.
- A turbo/Qwen3 disagreement score tracked error well but mostly flagged bad files;
  re-running low-agreement lines with large-v3 was slower than running large-v3 on
  everything. Not useful as a routing rule.
- Hallucinations were rare: one output over 3x the reference (a wrong-name prompt
  echoing its list) and 17 empty outputs, all in the hardest condition (both, 0 dB).

### Limits of all of the above

- Clean or synthetic read speech, not real drama audio: no overlapping speakers,
  emotion, real music with vocals or long silences, so loops and VAD behaviour on long
  files are untested.
- 60 clips per cell; one run per cell; the temperature fallback is random.
- CPU only; no GPU speed or memory figures.
- Model revisions were pinned only in the noisy-audio runs.

### Streamer VOD against burned-in subtitles (2026-10-09, private clip)

One 15 min Chinese streamer VOD with burned-in Traditional Chinese subtitles. CPU
only (4 cores, 15 GB), faster-whisper 1.2.1 int8, qwen-asr 0.0.6 bfloat16, one run per
row. The harness is not in the repo.

Reference: PaddleOCR (`chinese_cht`) on the white glyph fill of the subtitle band at
2 fps, repeated frames merged into 244 lines (1,419 characters; 513 in the first
5 min). A spot check of 30 lines against the on-screen English found the meaning right
in every readable one and a wrong character in about 8 of them (roughly 5% of
characters). The subtitles cover about 58% of the characters Whisper heard
(1,419 of about 2,450), so whole-text CER is 63-80% and says nothing; the figure below
is reference-side CER: each reference line is matched to the best stretch of output
text within 2 s of it, both sides converted to Simplified and normalised as
`services/benchmark_lab_service` does. Differences under about 2 points are noise
(one run per row, 513 or 1,419 reference characters).

First 5 min, large-v3-turbo unless noted (default = beam 5, silence 300 ms, threshold 0.5):

| Configuration | CER % | Lines | Line s mean / max | Wall s |
|---|---|---|---|---|
| default | 16.4 | 150 | 1.4 / 4.6 | 145 |
| beam 1 | 18.3 | 147 | 1.5 / 5.8 | 127 |
| beam 8 | 16.6 | 141 | 1.5 / 3.8 | 145 |
| silence 800 ms | 17.0 | 156 | 1.4 / 4.2 | 139 |
| silence 2000 ms | 16.2 | 145 | 1.4 / 4.3 | 154 |
| threshold 0.35 | 17.3 | 147 | 1.4 / 4.6 | 139 |
| threshold 0.7 | 17.0 | 142 | 1.5 / 5.2 | 126 |
| fast mode (batched) | 24.0 | 44 | 5.7 / 23.9 | 152 |
| hallucination silence 2 s | 16.4 | 150 | 1.4 / 4.6 | 129 |
| repeat guard | 17.5 | 150 | 1.3 / 3.5 | 112 |
| Split lines by sentences | 16.2 | 145 | 1.4 / 4.3 | 133 |
| small | 23.4 | 140 | 1.6 / 6.0 | 92 |
| medium | 17.5 | 145 | 1.6 / 5.7 | 236 |
| large-v3 | 18.7 | 138 | 1.6 / 5.8 | 388 |
| Qwen3-ASR 1.7B, `qwen3_asr_vad` | 21.1 | 61 | 3.8 / 7.8 | 576 |
| Qwen3-ASR 1.7B, `qwen3_asr_long` | 22.0 | 79 | 3.6 / 16.0 | 584 |
| Qwen3-ASR 1.7B over the default Whisper lines | 25.1 | 150 | 1.4 / 4.6 | 728 |

Full 15 min:

| Configuration | CER % | Lines | Line s mean / max | Wall s |
|---|---|---|---|---|
| turbo default | 20.4 | 384 | 1.7 / 10.4 | 475 |
| turbo, Split lines by sentences | 19.6 | 408 | 1.5 / 5.7 | 451 |
| turbo beam 1 | 21.4 | 384 | 1.6 / 8.8 | 371 |
| medium | 21.1 | 401 | 1.8 / 7.1 | 962 |
| large-v3 | 20.6 | 385 | 1.7 / 7.3 | 1080 |

The Qwen wall times include model loading. The worst-scoring lines of the best
configurations were mostly short interjections Whisper left out and reference OCR
mistakes, not wrong words. Not tested: GPU, the forced-aligner refine option,
Whisper plus Qwen combined.
