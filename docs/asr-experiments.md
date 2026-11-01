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
| Whisper model | per title (Workspace) | `large-v3-turbo`, same on CPU and GPU | `whisper_models.DEFAULT_WHISPER_SIZE`. A model saved on a title is never replaced. |
| Japanese Whisper fine-tune `ja-1.5b` | per title (Workspace), Japanese titles only | not offered | Opt-in, never a default or part of a preset. See "whisper-ja 1.5B" below. |
| Japanese Whisper model `kotoba-v2` | per title (Workspace), Japanese titles only | not offered | Opt-in, never a default or part of a preset; not in Live. See "kotoba-whisper v2.0 in the app" below. |
| ASR backend | per title | `whisper` | `whisper`, `qwen3_asr`, `qwen3_asr_vad`, `qwen3_asr_long`, and the opt-in Japanese-only `anime_whisper` (see "anime-whisper in the app"). |
| Qwen3-ASR batch size | Settings > Performance and advanced > Advanced transcription (PC only; shown only while Developer Mode is on) | 1 | Range 1-16. |
| Refine line timing with the forced aligner | same card | off | |
| Mixed languages | same card | off | |
| Voice detector | the Transcribe stage's voice detector field (no Settings card holds it) | `auto` | Choices come from `settings_service._voice_detector_choices`. Used by the speech-detection backends. |
| Separate vocals first | per title | off | |
| Sensitivity | per title | `normal` | `sensitive` lowers the VAD threshold to 0.35 (`sensitivity_preset.py`). |
| Difficult audio | per title (Transcribe > Advanced > Switches) | off | Opt-in for screamed, whispered or quiet speech. See "Difficult audio" below. |
| Min silence, VAD threshold, Beam size | per title (Developer Mode) | 300 ms, 0.5, 5 | Whisper's own pass. Min silence is not used when "Split lines by sentences" is on. |
| Hallucination guard | per title (Developer Mode) | 0 (off) | Local Whisper only. Ignored by Fast mode, Groq, Mixed languages and the Qwen3-ASR backends. |
| Pause that can split a long line | per title | 0.35 s | `segment_splitting.MIN_WORD_GAP_SECONDS`. |
| Whisper fast mode, Repeat guard, Realign long segments | per title | off | Fast mode batches VAD chunks; Repeat guard bans repeated 3-token sequences (`core.WHISPER_REPEAT_GUARD_KWARGS`). |
| Split lines by sentences | per title | off | Whisper and `qwen3_asr` only. Uses `asr_backend.SENTENCE_SPLIT_MIN_SILENCE_MS` (2000 ms) instead of Min silence. |
| Name hint for Qwen3 ASR | per title | off | Sends character names and glossary terms as the model's vocabulary hint. |
| Groq | per title | off | Hosted Whisper; needs a Groq key. Whisper-only settings above do not apply. |

Saving the four experiment settings (batch size, forced-aligner refine, Mixed languages, Voice detector) is PC-only (`local_only()`); another device
sees "PC only". They are stored in `app_settings` (`services/asr_options_service.py`).

### ASR bench (`tools/asr_bench.py`)

`python -m tools.asr_bench TITLE_ID --config RECIPE [--config RECIPE ...] [--from S] [--to S] [--reference FILE.srt] [--yes] [--out FILE]`
runs one window of a title's stored audio through each recipe with the app's own transcription code and writes
`asr-bench-report.md` (data folder). Dry run unless `--yes`. Reads the title, writes nothing to it.

A recipe is `BACKEND[:MODEL][,KEY=VALUE]...`; the first is the baseline. Unknown backends, models, keys, values, a key the backend does not use,
or a model that `whisper_models.WHISPER_LANGUAGE_RULES` excludes for the title's language are refused before anything loads.

| Part | Values | Applies to |
|---|---|---|
| `BACKEND` | `whisper`, `qwen3_asr`, `qwen3_asr_vad`, `qwen3_asr_long`, `anime_whisper` (Japanese titles only) | |
| `:MODEL` | any key of `whisper_models.WHISPER_MODELS` (default: the title's saved model) | `whisper`, `qwen3_asr` (its Whisper pass) |
| `vad=` | `normal`, `sensitive` (`sensitivity_preset.py`; default: the title's preset) | `whisper`, `qwen3_asr` |
| `beam=` | 1-20 (default: the title's value) | `whisper`, `qwen3_asr` |
| `prompt=` | `on` (default, the automatic name/glossary prompt), `off` | `whisper`, `qwen3_asr` |
| `align=` | `on` (forced aligner refines line times), `off` (default) | `whisper`, `qwen3_asr_vad` (`qwen3_asr_long` always aligns) |
| `detector=` | `standard`, `asmr` (default: the Settings choice) | `qwen3_asr_vad`, `qwen3_asr_long` |
| `difficult=` | `on`, `off` (default: the title's saved Difficult audio choice). The levelled clip, Whisper with no speech detector and the relaxed skip rule; see "Difficult audio" | `whisper`, `qwen3_asr` |

To compare the owner's loud-speech clip three ways: `--config whisper:large-v3 --config whisper:large-v3,vad=sensitive --config whisper:large-v3,difficult=on`.
`difficult=on` writes a levelled copy of the cut window next to it in the bench's own temporary folder (removed afterwards) and says in the recipe's
notes if ffmpeg was missing and the clip was used as it is.

Anything a recipe does not name is what the title already uses (min silence, VAD threshold, hallucination guard, repeat guard, fast mode), so the
bench matches a real run. Models are released between recipes, so every recipe pays its own load time; the wall time includes it.
Timing, merged and missed lines are measured against the reference lines fully inside the window, by time overlap (at least 30% of the shorter line).
Accuracy uses `benchmark_scoring_service.score_output`, the Benchmark Lab's scorer. Loops and stock phrases are counted after the app's own
repeat filter, so they show what survives it. "In silence" uses the app's default speech detector on the window.
Peak VRAM is whole-GPU memory above the level before the recipe, read from `nvidia-smi`, so other programs count too; it is n/a on CPU or when unreadable.
A new recipe backend is one `BackendSpec` entry in `tools/asr_bench_recipes.py`.

### Which backend a title gets

A saved `asr_backend_choice` wins. A title with none saved (or a removed one) gets
`whisper` (`stored_asr_backend`). Qwen3 is never picked automatically: it downloads
several GB of weights on first use, and transformers 5.15+ alone (which other
features install) is no sign the user wants that. Before the move off `qwen-asr`,
having that package installed made Chinese and Japanese titles default to
`qwen3_asr_long`; they now stay on Whisper until the backend is chosen.

| Backend | Boundaries and timing | Needs |
|---|---|---|
| `whisper` | Whisper's | `faster-whisper` |
| `qwen3_asr` | Whisper's; Qwen3-ASR replaces only the text. Cannot add lines Whisper missed. | transformers 5.15+, `torch`, plus Whisper's pass (`faster-whisper`, or Groq) |
| `qwen3_asr_vad` | Silero speech spans (about 15 s cap); the forced aligner only if "Refine line timing" is on | transformers 5.15+, `torch`, `faster-whisper` |
| `qwen3_asr_long` | Gentler speech detection, windows up to 30 s, one line per sentence, forced aligner always on | same |

### Batch size

- Used by `qwen3_asr` and by the non-mixed path of the two speech-detection backends.
  Timing is not affected, only speed.
- Runs on transformers 5.15+'s native Qwen3-ASR (`qwen3_native.py`), which returns one
  result per input in order; below 5.15 Qwen3-ASR can't run at all, and the Settings
  card says which applies.
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

### Timing check

Qwen3-ASR without Whisper (`qwen3_asr_vad`, `qwen3_asr_long`) gives good text but
line times that can sit in silence or run past the speech. The Review stage's
**Check timing** (Review > Fix > Flag lines for review) compares each line's start and end with the
speech Silero finds in the title's audio (the speech coverage check's chunked CPU scan:
no Whisper, no Qwen, no GPU; cancel from Jobs) and sets the `timing_drift` flag when a
line starts more than 0.7 s before speech, ends more than 0.7 s after it, is under 40%
speech, sits in silence, or overlaps the next line. The note is fixed text plus numbers
("Starts 1.4 s before speech."). The thresholds are constants in `timing_drift.py`.

- It runs by itself after a transcription with either Qwen-only backend, and on demand
  for any title (Check timing).
- Only `flag` and `flag_note` are written, only while a line's times are unchanged since
  they were judged. A line with another flag keeps it; a `timing_drift` flag that no
  longer applies is cleared. **Dismiss flag** on a timing flag is remembered
  (`timing_check.json` in the title's folder), so a re-check leaves that line alone;
  a new transcription forgets dismissals.
- **Snap to speech** (per line) and **Snap all flagged** shorten a flagged line to the
  speech it overlaps, at most 3 s per edge, after a history snapshot. Each is a
  compare-and-set on start, end and flag, so a time you edited since the check wins.
  Lines in silence and overlap-only lines get no suggestion.
- Streamer VODs are checked more loosely (1.5x tolerances) and the note says speech
  detection can mistake music for speech. If the detector finds almost no speech in the
  whole file (under 2%), no line is flagged and the title gets one notice instead.
- Unverified on real audio: the thresholds were chosen from the cases above, not tuned
  on recordings.

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

## Do not combine

The notes the Transcribe form and a finished run's details show, from `services/transcribe_options_service._ADVICE_RULES` (`tests/test_asr_advice.py` keeps this table in step with them).

| Note | Severity | Text |
|---|---|---|
| `groq_no_key` | blocked | Groq is on but no Groq key is saved. |
| `whisper_missing` | blocked | Whisper is not installed, so this run cannot start. |
| `anime_whisper_language` | blocked | Anime-whisper is for Japanese only. |
| `ja_model_language` | blocked | This Whisper model is for Japanese only. |
| `qwen_long_audio` | warn | Qwen3 ASR on long audio gives no word timings, fewer lines and "Timing uncertain · Split evenly" flags. Use Whisper. |
| `forced_align_needs_transcript` | warn | Qwen3 forced alignment needs a transcript to line up; without one it is not used. |
| `ja_15b_needs_gpu` | warn | Whisper ja-1.5B needs a GPU and is meant for anime and games. |
| `weak_whisper_size` | warn | Whisper small and medium read worse than large-v3. |
| `separate_vocals` | warn | Separate vocals first can drop quiet speech. Use it only if background music makes Whisper invent text, and compare line counts. |
| `fast_mode` | warn | Fast mode gives fewer, longer lines and a higher error rate. |
| `anime_whisper_scope` | tip | Anime-whisper is Japanese only, for ASMR and vocalisations, and its licence is non-commercial. |
| `ja_15b_scope` | tip | Whisper ja-1.5B is meant for anime and games. |

## Which Whisper model to pick

Default `large-v3`, by the owner's choice: on his streamer comparisons it reads better
than turbo without any Whisper setting adjusted. That is a readability preference, not a measured
accuracy gain. Baihe's own streamer VOD test (below) found no CER difference: turbo 16.4 against
large-v3 18.7 on the first 5 minutes, where differences under about 2 points are noise, and 20.4
against 20.6 over the full 15 minutes. `large-v3-turbo` stays the fast option: about twice as fast
as large-v3 and close in accuracy on clean speech. `medium` was never clearly ahead of turbo
(Chinese clean: 6.84 vs 7.15, within noise). Details by language (numbers in the snapshot):

| Language | What the tests showed |
|---|---|
| Korean | large-v3 was 0.5 points better than turbo (interval excludes zero), about twice as slow. Turbo and medium could not be told apart. |
| Japanese | Turbo and large-v3 indistinguishable on clean speech; medium about 1.9 points worse. No evidence turbo is weaker. |
| Chinese | Mixed. Clean speech: large-v3 better than turbo by 0.6-2.4 points. One drama clip: turbo ahead of large-v3. Medium wrote Traditional characters far more often. |
| Mixed languages | Per-span detection beat a fixed language or whole-file auto-detect for all three sizes. |
| Degraded audio | large-v3 beat turbo by 1.4 points at +10 dB and 2.4 at 0 dB; no difference on clean. Medium was worst everywhere. |

Download size: turbo ~1.6 GB, medium ~1.5 GB, large-v3 ~3 GB
(`transcribe_pipeline._MODEL_DOWNLOAD_SIZES`). On a CPU large-v3 takes about twice as long as turbo
(the Transcribe estimate uses 1.5-4x the audio length for large-v3 and 0.6-2x for turbo).

Which sizes the pickers offer is one function, `whisper_models.selectable_whisper_sizes(context)`: the
Transcribe stage and Compare transcription list `large-v3`, `large-v3-turbo` and the opt-in Japanese models;
`tiny`, `base`, `small` and `medium` are hidden everywhere outside Live, Developer Mode included. The API and saved titles
still accept the hidden sizes, and a title that saved one shows it as "medium (older choice, hidden)" with a
one-click switch to large-v3; it is never changed silently. Live keeps its own list
(`whisper_models.LIVE_WHISPER_SIZES`: tiny to medium and turbo, default small) and the Groq hosted model stays
`whisper-large-v3-turbo`.

Not combining options: for streamer titles use Whisper large-v3 with vocal separation off. On the owner's
Japanese test, separation dropped real lines (142 lines against 184), and Qwen3-ASR on long audio gave
"timing uncertain" on most lines. The Transcribe stage says so beside the model pick, shows one line
"This run will use: backend · model · GPU on/off" above the button (GPU off when the CUDA runtime is missing,
since Whisper then runs on the CPU), and turns off the Whisper-only options when the backend skips Whisper.

### Benchmarking a model that is not in the list

A candidate such as a Japanese fine-tune can be tried in the Benchmark Lab without
changing the model of any title:

1. On the PC, set Settings > Performance and advanced > Offline, paths and limits > Offline Whisper model folder to the downloaded
   faster-whisper (CTranslate2) model folder.
2. In the Lab, pick the Transcription stage and add an arm with the model
   `local-folder`. It is listed in the Whisper sizes and shows as "offline model
   folder" in results; the path is never sent in the request or stored.
3. Clear the setting afterwards, or every title keeps using that folder.

If the setting is empty or the folder is missing, the arm is refused with a message
pointing at the setting. Only runs started on the PC can use it. The memory-headroom
check is skipped for a folder, as it is for any local path.

#### whisper-ja 1.5B in the app

Which Whisper models a language may use is one table, `whisper_models.WHISPER_LANGUAGE_RULES`
(model, languages, reason). The server refuses an excluded pair (title settings, a run that names
another language, Compare transcription, Benchmark Lab), and the pickers read the same
table from the config and options payloads (`whisper_exclusions`), showing an excluded model disabled with
its reason. A title saved with a model that is no longer allowed falls back to the default and the
Transcribe stage says so (`whisper_size_notice`). A model is excluded for a language only where this
document shows it worse; Live's own size list is separate and not gated. Today:

| Model | Not selectable for | Evidence |
|---|---|---|
| `medium` | Japanese | "medium about 1.9 points worse" than turbo, and slower (RTF 0.84 vs 0.56), in the model table above. Korean and Chinese showed no clear gap, so it stays there. |
| `ja-1.5b` | Chinese, Korean | A Japanese fine-tune, only for Japanese titles. |
| `kotoba-v2` | Chinese, Korean | Trained on Japanese speech only (ReazonSpeech), so only for Japanese titles. |

Adding a model is a `WHISPER_MODELS` label plus, if it is language-bound, one
`LanguageRule`. `ja-1.5b` is never the default.

| | |
|---|---|
| Repository | `TransWithAI/whisper-ja-1.5B-ct2` (CTranslate2 build of efwkjn's whisper-ja-1.5B), pinned in `whisper_models.PINNED_CT2_REPOS`; clients send the key, never a repo id or revision |
| Revision | `1527314b14da5bdf0d14e7328649d4fa26840188` |
| Size | `model.bin` about 3.1 GB; in practice it needs a GPU (`memory_headroom` estimate 4.2 GB VRAM, 3.3 GB RAM, not measured) |
| `model.bin` sha256 | to be filled after the owner's first download (`tools/ja_whisper_models.py fetch ja-1.5b` prints it) |
| Licence | none declared; the training data terms are mixed. Opt-in, personal use only |
| File-set rule | Before the first download the repo's files at the pinned revision are listed (`lib/ct2_repo.py`, the same rule `tools/ja_whisper_models.py` uses). Only `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.json` or `vocabulary.txt` and `preprocessor_config.json` are downloaded (`allow_patterns`); README, LICENSE and `.gitattributes` are allowed in the listing but never fetched. Any other file, including any `.py`, `.pt`, `.pkl` or second `.bin`, refuses the whole download with a plain message, nothing fetched. faster-whisper loads it with no `trust_remote_code`. |

CPU sample test (40 clips per set, int8, beam 5; character error rate, lower is better; each cell has the two figures as reported by the sample run, and what separates them was not recorded here):

| Set | whisper-ja 1.5B | large-v3-turbo |
|---|---|---|
| Galgame shard 114 | 0.050 / 0.085 | 0.094 / 0.398 |
| joujiboi | 0.200 / 0.232 | 0.333 / 0.357 |

Word timestamps work out of the box and no repetition loops appeared. anime-whisper is an opt-in
backend of its own (its word timestamps break; see "anime-whisper in the app"); the 760M sibling is
not added. kotoba is in the app, see below.

#### anime-whisper in the app

`asr_backend_choice = anime_whisper` (`asr_backend.AnimeWhisperBackend`): opt-in, Japanese only (other
languages are refused in the picker, by the server and by the backend), never the default.
**Personal, non-commercial use only**: the model card says MIT, but the training data forbids commercial
use of models trained on it. Baihe never ships the weights; the first use downloads one pinned
CTranslate2 build (`whisper_models.ANIME_WHISPER_PIN`) into `model_cache/anime_whisper`.

Why it is not a Whisper size: the model writes one leading timestamp and never a closing one, so
faster-whisper's segment times are unusable (median end error 3.4 s), and `word_timestamps=True`
makes it seek back and re-decode, inventing text on 64 of 80 clips at 2-3x the time. So the backend
decodes whole windows with timestamps off, force-aligns the text with the model's own cross-attention
(heads calibrated for its 2-layer decoder: layer 1, heads 14, 8, 12) and places each word in the fine
Silero speech span that holds it. CPU test, 8 multi-utterance files: 0 merged and 1 of 40 utterances
missed (turbo path: 5 merged, 5 missed), start/end error 0.09/0.13 s median (turbo 0.33/0.29 s),
CER 0.162 (turbo 0.295).

Decode settings: `language="ja"`, beam 5, `without_timestamps=True`, `word_timestamps=False`,
`condition_on_previous_text=False`, `vad_filter=False`, windows of at most 30 s decoded independently
(the model collapses on long sequential decoding). No initial prompt is ever sent, not even the
title's glossary: the card says it causes hallucination. The repeat guard applies only when the title
turned it on. The pinned model.bin is byte-identical to `tools/ja_whisper_models.py convert-anime-whisper`.

Safety: the repository listing is checked against the CTranslate2 allow-list before any download (a
script, pickle or second `.bin` refuses the whole download), the revision is a full commit sha,
`model.bin` is checked against the pinned sha256, and the download is built in a side folder and moved
into place only after every check passed.

Compare it with `python -m tools.asr_bench` on a Japanese anime or ASMR clip:
`TITLE_ID --config whisper:large-v3-turbo --config qwen3_asr_vad --config anime_whisper`. Pass if CER is 2+
points better than turbo, no more hallucinated lines in silence, at most 10% missed lines, median
start/end error at most 0.3 s with 70%+ within 0.3 s, and RTF at most 1.5x turbo's.

#### kotoba-whisper v2.0 in the app

`kotoba-v2` is large-v3's encoder with a 2-layer decoder, trained on ReazonSpeech; its card claims about
6x large-v3's speed. Gated by the same `WHISPER_LANGUAGE_RULES` table as `ja-1.5b`, never the default,
not in Live (its size list waits for a benchmark).

| | |
|---|---|
| Repository | `kotoba-tech/kotoba-whisper-v2.0-faster`, pinned in `whisper_models.PINNED_CT2_REPOS` |
| Revision | `f44edd35eaeb2274e85ac7b31fb2c6f59ff1c4bc` (the revision `tools/ja_whisper_models.py` already pins) |
| Size | `model.bin` 1.51 GB (`memory_headroom` estimate 2.6 GB VRAM, 2.0 GB RAM, not measured) |
| Licence | the CTranslate2 repo's card says MIT; the original `kotoba-tech/kotoba-whisper-v2.0` says Apache-2.0 |
| File-set rule | the same as `ja-1.5b` (`lib/ct2_repo.py`) |
| Timing | segment timestamps only: `whisper_models.WORD_TIMESTAMP_UNSUPPORTED` makes `core.transcribe_for_timing` and the mixed-language runner pass `word_timestamps=False`. The stock `config.json` carries large-v3's `alignment_heads` (decoder layers 7-19) on a 2-layer decoder, which is what crashes word timestamps. The pipeline then refines the segment times with the Qwen3 forced aligner (`forced_align.refine_whisper_segments`); if it is not installed, errors, or the run is mixed-language, the segment times are kept, no line is flagged, and the job reports "Line timing was not refined (reason)" as a partial result. |

No benchmark of `kotoba-v2` in this app exists yet; the speed and accuracy claims above are the model card's.

#### Trying a Japanese fine-tune

`tools/ja_whisper_models.py` prepares the folder (run on the PC, in Baihe's Python; it
adds no dependency, and each subcommand imports only the libraries it needs):

```
venv\Scripts\python.exe tools\ja_whisper_models.py fetch ja-1.5b --out D:\models\ja-1.5b
venv\Scripts\python.exe tools\ja_whisper_models.py fetch kotoba --out D:\models\kotoba
venv\Scripts\python.exe tools\ja_whisper_models.py convert-anime-whisper --out D:\models\anime [--quant float16|int8]
venv\Scripts\python.exe tools\ja_whisper_models.py check D:\models\kotoba [--wav clip.wav]
```

- `fetch` downloads a pinned CTranslate2 build (`TransWithAI/whisper-ja-1.5B-ct2`,
  `kotoba-tech/kotoba-whisper-v2.0-faster`). For kotoba it saves `config.json.orig` and
  sets `alignment_heads` to layer 1, heads 0-19: the stock config points at decoder
  layers a 2-layer decoder does not have and crashes with word timestamps (kotoba-whisper
  issue 2; the workaround is the community's, not the vendor's).
- `convert-anime-whisper` downloads `litagin/anime-whisper` (safetensors only), converts it
  with CTranslate2 without `trust_remote_code`, writes `tokenizer.json`, copies
  `preprocessor_config.json`, sets the same heads, and writes `baihe-manifest.json`
  (source, revision, library versions, quantization, `model.bin` sha256; no paths).
- `check` verifies the files, that `alignment_heads` fit the decoder (read from the
  layer names in `model.bin`), then loads the folder with faster-whisper and transcribes a
  clip with word timestamps. It reports segments, words, bad word timings and the
  language token, and exits non-zero on any problem. Without `--wav` it uses a synthetic
  clip, which only proves the model does not crash.

Safety rules the tool enforces:

- Only CTranslate2 or safetensors files. It lists the repository first and refuses, naming
  the file, if anything is outside the allowlist; `.py`, `.pt`, `.pth`, `.pkl`, `.ckpt` and
  any `.bin` other than `model.bin` are never downloaded or loaded. The allowlist lives in
  `lib/ct2_repo.py` (shared with the app). Besides the model files it admits only inert
  Hugging Face metadata JSON (`added_tokens.json`, `generation_config.json`,
  `normalizer.json`, `special_tokens_map.json`, `tokenizer_config.json`), which is allowed in
  a listing but never fetched.
- Every download is at a pinned full commit sha, never a branch. A new revision means a
  reviewed change to the constants in the tool.
- No initial prompt for anime-whisper: its card says a prompt degrades the output.

Licence notes (personal local benchmarking only; none of this is legal advice):

- whisper-ja-1.5B declares no licence, and its training data terms are mixed.
- anime-whisper's card says MIT, but its training dataset forbids commercial use of
  models trained on it.
- kotoba-whisper is Apache-2.0; its ReazonSpeech data is offered under Japanese Copyright
  Act Article 30-4 terms.

Test a new folder in the Workspace on one 30 second clip before any Lab run: a crash
there only kills the worker, and `check` should pass first. Then set the folder, run a
`local-folder` arm, and clear the setting.

## Guidance from the benchmarks

Each row rests on the snapshot below (read speech, 60 clips per cell, CPU; the noisy-audio rows use 20 per language), so
differences under about 1 point are noise.

| Topic | Guidance | Current default |
|---|---|---|
| Speech-detection settings | Keep 300 ms and threshold 0.5. Silence 100/1000 ms changed nothing; threshold 0.7 was 3.2 points worse. Tested on single sentences only. | 300 ms, 0.5 for Whisper with "Split lines by sentences" off; with it on, silence is 2000 ms |
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
| Qwen3-ASR will not load | The start-of-job message names what is missing (torch, transformers 5.15+, nagisa for Japanese or soynlp for Korean alignment); Diagnostics warns at startup while the old `qwen-asr` package is installed (see "Qwen3 on transformers' own classes"). |
| Batch size has no effect | Mixed languages is on (one span per call), or the backend is `whisper`. |

## Qwen3 on transformers' own classes

Qwen3-ASR and Qwen3-ForcedAligner run on `Qwen/Qwen3-ASR-1.7B-hf`,
`Qwen/Qwen3-ASR-0.6B-hf` and `Qwen/Qwen3-ForcedAligner-0.6B-hf` (Apache-2.0)
through `qwen3_native.py`, not the `qwen-asr` package. The `qwen-asr` package
pinned transformers to 4.57.6; the native classes need **transformers 5.15 or
newer**. The floor is 5.15, not the 5.13 the model cards say: the released
5.13 and 5.14 wheels contain the classes but force the language through the
system prompt and have no `prompt=` argument, while 5.15 prefills
`language <NAME><asr_text>` as the models were trained and adds `prompt=`
(checked against the wheels). The aligner card's "install from source" note is
out of date: the released 5.13+ wheels include `Qwen3ASRForTokenClassification`,
its auto-mapping and `prepare_forced_aligner_inputs` / `decode_forced_alignment`.

- **Upgrading from `qwen-asr`.** The package is no longer used, but while it stays
  installed its exact `transformers==4.57.6` pin holds transformers below the floor.
  A startup warning and the job-start message say so; the fix is
  `pip uninstall qwen-asr` in the app's Python, then update transformers in
  Diagnostics. Diagnostics' update check ignores the pin (`IGNORED_REQUIRERS`).
- **No automatic Qwen3 default.** A title that never saved a backend uses Whisper.
  Before this change, having `qwen-asr` installed made Chinese and Japanese titles
  default to Qwen3 on long windows; with a plain transformers install that would
  have started a ~6 GB download nobody asked for, so Qwen3 is now only used when
  the title's backend is set to it.
- **Non-ASCII data folder (unchecked).** The old startup warning for a Windows data
  folder with non-English characters fired only when `qwen-asr` was installed, and
  its root cause isn't recorded in the repo, so it can't be ruled out for the
  native path. That path reads audio with soundfile (`qwen3_native.load_audio_16k`)
  and loads the models through Hugging Face, whose cache is under the data folder
  (`HF_HOME`). The warning was dropped with the package; nobody has run a Qwen3
  transcription from a non-ASCII Windows path since. Owner check: do that once.
- **Downloads are new.** Weights cached for the old `Qwen/Qwen3-ASR-1.7B` and
  `Qwen/Qwen3-ForcedAligner-0.6B` repos are not reused. First use downloads about
  4.1 GB (1.7B), 1.6 GB (0.6B) and 1.8 GB (the aligner) from Hugging Face; the old
  folders can be deleted in Diagnostics > Model cache.
- **Japanese and Korean alignment** need `nagisa` and `soynlp`; a missing one is
  reported in plain words, at job start, not as an ImportError after
  the recognition pass.
- **Name hint (off by default).** A per-title switch in the Transcribe stage's
  Advanced section sends
  `Vocabulary: a, b, c` as the processor's `prompt=`, built from the title's
  character names and series glossary (`services/vocabulary_hint_service.py`).
  At most 40 terms and 300 characters, because every segment's request carries
  it and a long list makes the model write the words where they weren't said.
  With the switch off, or no names, the request is exactly as without the feature.
- **Mixing with other voice engines.** OmniVoice (transformers >= 5.3) can share
  an environment with it.

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
`services/benchmark_lab_service` does. The Lab now also reports Traditional vs Simplified line and character counts per Chinese transcription run and case (`services/benchmark_script_service`); CER converts both texts to Simplified before comparing (OpenCC, or the one-script character table in `benchmark_script_service` when OpenCC is not installed; the run records which as `script_normaliser`), so it does not see script. Scores computed before that change were script-sensitive: a Traditional hypothesis against a Simplified reference lost a point per differing character, so do not compare them with newer Chinese scores. Differences under about 2 points are noise
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


## Difficult audio

A per-title opt-in (`dramas.difficult_audio`, Transcribe > Advanced > Switches, also a series default) for audio where the default drops speech:
someone screams or talks very loud, or two people whisper. Off, a run is identical to before: the same worker arguments, the same keywords to
faster-whisper. On, a transcription does three things:

1. **No speech detector for Whisper.** `transcribe_for_timing` passes `vad_filter=False` (not in Whisper fast mode, whose batched pipeline is cut
   by the detector), so Whisper hears the whole file. The title still runs with the Sensitive preset (`sensitivity_preset.difficult_audio_view`), which now
   only drops the repeat penalties for Whisper; the detectors of the Qwen3 recognizers ignore it as before. Measured on a 12-minute drunk VTuber stream
   with burned-in Chinese subtitles (2026-10-26, large-v3 on CPU): Silero scored her shouted speech 0.1-0.45, so at 0.5 the detector kept 212 s of the
   709 s and the run heard 42% of her 206 subtitle lines; 0.35 heard 51%, 0.15 heard 60%, and no detector 86% (character errors 69% to 46%), with six
   extra lines. A full Difficult audio run heard 80%: the levelled copy cost her lines there (86% without it) but gained the quieter caller's (58% against 46%), so it is kept.
2. **Levelled temporary copy.** `audio_preprocess.level_audio` runs one ffmpeg call (`lib.proc.run_captured`, timeout, polled cancel) with
   `dynaudnorm=f=150:g=5:p=0.9:m=8` into a 16 kHz mono WAV in the job's scratch folder in the library temp folder. The stored audio is never
   touched. The worker is killed as a whole process group on a cancel, so ffmpeg is started with `own_group=False` to stay in that group
   (a child in a group of its own would survive the kill; on Windows `taskkill /T` walks the process tree, which covers it either way). The parent removes the
   scratch folder when the job ends, and the startup sweep removes one a crash left. dynaudnorm levels frame by frame and caps
   the gain at 8x, so a whisper after a scream is lifted within about a second and silence is not pumped into something the detector takes for speech;
   loudnorm was measured on a quiet/loud test tone and also levels, but has no gain cap, works at 192 kHz internally and its accurate mode is a single gain for the
   whole file. The copy needs about 115 MB per hour of audio. If ffmpeg is missing, cannot start or fails, the disk is short of room, or
   Separate vocals first is on (the separation model should hear the original), the original is transcribed and the job's warning says why. The other two parts still apply.
3. **Relaxed Whisper skip rule.** `no_speech_threshold` and `log_prob_threshold` are passed to faster-whisper only when it accepts them
   (`core._accepts_transcribe_kwarg`; not in Whisper fast mode, whose batched pipeline is not probed). Whisper drops a stretch whose no-speech probability is above the first number unless its average word confidence is above
   the second; faster-whisper's defaults are 0.6 and -1.0, and the recipe uses 0.8 and -1.5. Both are Developer Mode rows in Settings > Timing and
   splitting (`difficult_no_speech_threshold`, `difficult_log_prob_threshold`); with Developer Mode off they read as these defaults. The hallucination guards
   (`filter_hallucinated_segments`, the stock-phrase and silence guards, and the title's repeat guard) still run on what comes back.

Every Whisper run, Difficult audio or not, then hears a segment again when it is at least 6 s long and holds fewer than 1.5 letters per second
(`whisper_transcribe._rehear_sparse`): faster-whisper sometimes closes a segment 20-80 s after it opened with a few words in it, having skipped the
speech between, and the detector's stitching of the speech it kept makes this more likely. The stretches go back to the same model in one call with
`clip_timestamps` and no detector, and a stretch's new segments replace the old one only when they hold more text. On the stream above this took
the default run from 42% to 62% of her lines and turbo from 43% to 68%; the cost is one more decode of those stretches (on CPU it about doubled the run).
Not in fast mode, and skipped for a faster-whisper without `clip_timestamps`.

Which recipe honours which part:

| Recognizer | Levelling | Sensitive detector | Skip rule |
|---|---|---|---|
| Whisper | yes | detector off | yes (not with Mixed languages on: the per-span run keeps Whisper's own rule, and the job says so) |
| `qwen3_asr` (Whisper boundaries, Qwen3 text) | yes (both passes hear the copy) | detector off | yes |
| `qwen3_asr_vad`, `qwen3_asr_long`, `anime_whisper` | yes | no (own detector) | no (not Whisper) |
| Groq, or a supplied transcript | ignored, with a note in the job result | | |

A supplied transcript or Groq ignores the option, including the detector preset. Not covered: re-transcribing selected lines, the fix-flag and compare runs and auto-tune use the title's Sensitivity and defaults as before.
