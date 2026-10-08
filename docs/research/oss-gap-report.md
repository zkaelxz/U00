# Open-source gap report: what Baihe is missing or doing worse

Read-only research, 2026-10-08, against `baihe-subtitler` at 68595ce. Question from the owner: across the top ~100 GitHub repos tagged `video-translation`, `video-transcription` and `asr` plus the must-review extras, what is Baihe missing or doing worse? Focus is video translation/dubbing, transcription/subtitles and ASR, with CJK and ASMR relevance first.

Method: stage 1 read READMEs, licence files and last-commit dates of every kept repo (shallow clones, never run). Stage 2 read the actual pipeline code of about 60 repos, including the five must-review ones. Every Baihe claim below was re-checked against the code at the paths cited. Third-party code was never executed or copied; only ideas are described. "Unverified" means seen in a README or not checked in code.

## 1. Top 10 gaps, ranked for the owner (CJK + ASMR subtitling first)

| # | Gap in Baihe | Seen in | What to build | Effort | Risk |
|---|---|---|---|---|---|
| 1 | **No subtitle/transcript file import.** Only plain text (`core.split_user_transcript`, API `transcript_text`, CLI `--transcript`) is accepted, and its timing is re-derived from audio. No SRT/VTT/ASS/LRC parser exists; export has SRT, bilingual SRT, VTT, ASS only. | VideoLingo (`_2_import_subtitles`), YouDub-webui, dub-studio, OpenCreator, Voxa, erasedub, asmr-dubber, faster-whisper-GUI, ArisuLoveASMR, KikoFlu, Doujin-Audio, Eara | Parser for the four formats (encoding detection, ASS tag stripping, LRC multi-stamps, bilingual split), validation at upload (overlap, order, zero length), import as source or as translated text, optional re-alignment, plus sidecar discovery (`track.mp3.vtt`, `track.ja.vtt`) and sidecar-named export. Details in Q3 and Q5. | M | Low. Write parsers from the public specs; several reference repos are GPL/AGPL. |
| 2 | **Benchmark Lab cannot test ASMR.** Transcription cases score one number (1-CER/1-WER on joined text), always through `core.transcribe_for_timing` with default settings and only the model size varied. No missed-speech, hallucination or timing metric; a clip with an empty reference is stored as no reference and scores nothing. `docs/asr-experiments.md` uses read speech and says it "says nothing about long silence". | OpenTranscribe (word-level speaker error, cached fixtures), SpeechColab, speechio, YouDub-webui (honest per-path status), OpenCreator (PASS/FAIL/BLOCKED_ENV/NOT_RUN) | A whisper-heavy golden set with timed reference SRTs, new metrics (missed speech seconds, hallucinated characters in no-speech spans, start/end offset median and p95, S/D/I counts), settings arms (VAD, pre-gain, channel split, backend). Full spec in Q1. Everything below that touches ASR defaults should wait for it. | M | Low. Clip rights: use own or CC recordings, keep audio outside git. |
| 3 | **No ASMR/whisper transcription preset, and two VAD knobs are not exposed.** Baihe passes only `threshold` and `min_silence_duration_ms` to faster-whisper (`core.py:817`); `speech_pad_ms`, `min_speech_duration_ms` and `no_speech_threshold` stay at library defaults. Three ASMR tools (subforge, EaraAsmrPlayer, ArisuLoveASMR) use or advise a VAD threshold of 0.15 to 0.4, and two add longer start/end padding and 15 to 20 s span caps; subforge and ArisuLoveASMR turn `condition_on_previous_text` off. None measured error. | subforge, EaraAsmrPlayer, ArisuLoveASMR, openlrc, VideoLingo `speech_edges`, FireRedVAD | After #2: expose pad/min-speech/no-speech as per-title settings and test a named "whisper/ASMR" preset against the defaults. Do not make it the default without the benchmark. See Q2. | S-M | Medium. Lower threshold raises hallucination on breaths; Baihe's anti-loop kwargs and repeat filter are the only guard. |
| 4 | **Stereo is thrown away before ASR.** Audio is extracted `-ac 1 -ar 16000` (`core.py:996`, `core.py:1016`). Binaural ASMR alternates speech between ears; subforge saw a mono downmix drop a 30 s block. | subforge (L/R split + merge by time), ASMR-Audio-Preprocessor (repair a faulty channel), asmr-enhancer (measure loudness multichannel) | Detect lopsided L/R energy; offer "transcribe L and R separately and merge" (with near-duplicate text dedupe, which subforge lacks) or "use the cleaner channel". Opt-in, 2x ASR cost. Test as an arm in #2. | M | Medium. Merge heuristic is the hard part. |
| 5 | **No stereo/spatial output outside dubbing, and none inside it.** `dub.py` has no panning, spatial or loudness code (grep for stereo/pan/spatial/loudnorm returns nothing). `mix_original_background` overlays a static-gain background (`dub.py:1278-1280`). The owner wants the stereo track and spatial following usable without dubbing too. | asmr-dubber (`spatial_cues.py`, `spatial_rtf.py`, `audio.py` ~468-620, `experimental_mix.py`; MIT; known, only compared), ASMR3D (HRTF render), Eara (balance/channel processors) | Split asmr-dubber's ideas into a standalone "stereo track builder": inputs are timed clips from any source (TTS, a user recording, a replacement take), output is a stereo track that follows the original's interaural time/level cues and is loudness-matched to the source with a boost cap (asmr-dubber's own comment: quiet ASMR must not be pushed to broadcast loudness). Dubbing then calls it. | L | High. Experimental, single-speaker assumption in the reference code, listening-only validation, which I cannot do. |
| 6 | **Translation context is local to a batch.** Defaults for video titles are 20 lines per batch, 6 recent translated pairs back and 3 source lines ahead (novels: 30, 10 and 6), all user-settable (batch up to 60; `translate_run_service.py:95-97`); bulk mode only sees translations that exist at submission. No whole-episode guideline (names, tone, who addresses whom) and no scene-aware split. | openlrc (token budget ~1000, hard split at a 30 s gap, split at the largest gap, one up-front guideline), Friend-Xu (deterministic "bible"), YouDub-webui (summary + hotwords pre-pass), VideoLingo (one-call theme summary) | Token- and gap-aware batch builder (S; keep ids; same code in live and bulk per the CLI/app rule; bump `TRANSLATE_PROMPT_VERSION`), and an optional episode guideline pre-pass (M; cache-stable text so provider prompt caching still hits). | S + M | Low-medium. Changes cached prompts. |
| 7 | **No post-ASR correction of names.** Baihe only primes Whisper with `initial_prompt`; its own data shows wrong names hurt on hard audio and right names help little. | FunASR (`postprocess_hotwords.py`: explicit `wrong=>right` plus fuzzy pinyin), sherpa-onnx (homophone replacer), VoxCPM-translator and Voxa (ASR-correction lists) | Review-time suggestion pass: fuzzy-match glossary and character names against transcribed lines (pinyin for zh, kana for ja), show as accept/reject like translation memory. Never auto-apply. | M | Low. Needs ja/ko matcher design; reference is zh-only. |
| 8 | **Speaker assignment is coarse.** `diarize.assign_speaker_to_line` picks the single longest-overlapping turn, returns `None` if no turn overlaps (`diarize.py:356-363`), has no nearest-turn fallback and no smoothing of 1-3-word wrong-speaker islands. Long ASMR gaps leave lines unlabelled. | whisperX (summed overlap per speaker, nearest-turn fill), OpenTranscribe (island smoothing, word-level speaker error), Friend-Xu (drift detector, diarization self-check) | Sum overlap per speaker, add nearest-turn fallback, a drift/fragment self-check on the result. Per-word speakers and splitting a line at a speaker change only if word timings are kept (#9). | S | Low |
| 9 | **No reviewable word-level timing.** `Line` stores no word times; word alignment (`word_align.py`, MMS_FA) is opt-in, runs only on oversized segments during transcription, and the editor has no waveform or word view. See Q4. | whisperX, whisper-timestamped (per-word confidence), faster-whisper-GUI, LLPlayer sidebar, mimiuchi (waveform seek bar) | Persist word timings (ADD COLUMN, add to `_INIT_DB_MIGRATED_COLUMNS`), an "align words for selected lines" job reusing `forced_align`/`word_align`, a word/waveform timing editor. | L | Medium. Wildcard handling of digits/symbols; storage growth. |
| 10 | **Dubbing quality loop is reactive.** `stretch_for_window` measures a clip and stretches it; overflow has no recovery. Others predict fit before synthesis, shorten text with an LLM, score each take, borrow neighbouring silence, duck the background and match loudness. Baihe has no loudness code outside `video_export` volume (grep for loudnorm/dynaudnorm/sidechain empty). | dub-studio (`fit.rs`: per-language chars/s calibrated from real clips, LLM shorten loop, take history), OpenCreator (estimator, chunk-level speed), jryang1997 (calibrated duration model, dub-track QC), Voxa and PersoDub (TTS round-trip WER gate, wrong-speaker gate), yuanshanhua and langswap (borrow neighbour silence), bluez-dubbing (sidechain ducking), youtube-auto-dub and VideoLingo (gated loudnorm) | Predictive fit verdict per line, "shorten to fit" as a reviewable flagged pass, neighbour-borrow before stretch, ducking envelope (opt-in; may pump on ASMR beds), loudness match to source. | M | Medium. Shortening changes meaning; keep it flagged. Lower owner priority (subtitling first). |

Smaller items, not ranked: content-keyed ASR result cache (VideoLingo, ~M); stage-signature resume (Voxa, M); render manifest with frame hashes (mikey1384/translator, S-M); target-text "source-script leakage" QC rule (translator, S); LRC export for audio-only titles (openlrc, S); merge cues shorter than ~1.2 s and collapse runaway repeats such as あああああ (openlrc `opt.py`, S); a "VAD removed X% of the audio" warning (openlrc, S; Baihe already has `diagnose_line_coverage` large-gap detection in `core.py:1217`); clip-by-selection (FunClip, M, needs an id-keyed LLM output instead of regexed text); script-share language sanity check (Han/Kana/Hangul, S); the SenseVoice no-speech tag as a cheap hallucination signal for Whisper lines (S, unproven); ten-vad or FireRedVAD as VAD options (M, FireRedVAD's F1 claim is README-only).

## 2. Feature matrix (Baihe vs the field)

Status: **Has** = in Baihe and checked; **Partial**; **Missing**; **Better** = Baihe ahead.

| Feature | Baihe | Repos that have it |
|---|---|---|
| VAD tuning | Has: Silero threshold 0.1-0.9, min silence 300-3000 ms per title; pad/min-speech/no-speech not exposed | subforge (preset), Eara, FunASR (FSMN), sherpa-onnx (ten-vad, hysteresis), FireRedVAD |
| Hallucination guard | Has: anti-loop kwargs + repeat filter (4+). No out-of-range or phrase list | subforge (clamp past media end), jryang1997 and dub-studio (phrase blacklist), CrisperWhisper (rewind-and-escape; needs a CTranslate2 fork) |
| Word-level alignment | Partial: Qwen3 aligner for user text, MMS_FA opt-in for oversized segments | whisperX (all segments), whisper-timestamped, Fun-ASR (CTC chars) |
| Unaligned-line flag | Better: `timing_uncertain` (`forced_align.py`) | whisperX leaves failures silent with Whisper timing |
| Diarization | Has: pyannote, hints, GPU/CPU placement, voice bank. Coarse assignment (gap 8) | whisperX, OpenTranscribe, whisper-diarization |
| Line splitting (CJK) | Better: `resegment.py` rules + LLM, CJK-aware | VideoLingo (spaCy-based), FunClip (hard 8 s/30 tokens), sherpa (VAD = cue) |
| Translation context | Better on robustness (id-keyed, 3 reflect passes with stored critiques, glossary by policy, TM, QC). Partial on long-range context (gap 6) | openlrc, Friend-Xu bible |
| Terminology | Better: per-series glossary, extraction from novel/lines, hard substitution, review step | VideoLingo (one call over the first 8000 chars) |
| Subtitle formats out | Better: SRT, bilingual, VTT, ASS styles, burn/mux. No LRC | openlrc (LRC), faster-whisper-GUI (LRC, SMI) |
| Subtitle import | **Missing** | 12+ repos (gap 1) |
| Sidecar discovery/naming | Missing | KikoFlu, Eara, Doujin-Audio, Kikoeru |
| Stereo-aware ASR | Missing (mono 16 kHz) | subforge, Preprocessor |
| Pre-ASR loudness lift | Missing (no loudnorm anywhere) | subforge (-12 LUFS), openlrc, VideoLingo, Eara |
| Denoise before ASR | Missing by design; own data says it hurts (Demucs, noise) | openlrc (optional), asmr-enhancer (listening only, no ASR evidence) |
| Post-ASR hotword fix | Missing | FunASR, sherpa-onnx |
| Text normalisation for CER | Partial: lowercase, punctuation, whitespace only | SpeechColab (NFKC, t2s, numerals, fillers), speechio (zh numerals) |
| Missed-speech / timing metrics | Missing | none found complete; OpenTranscribe has word speaker error |
| Real-media acceptance | Partial: `scripts/smoke_pack.py` (own clip, own baseline) | OpenCreator (status vocabulary), YouDub-webui (says it has none) |
| Dub fit | Partial: reactive stretch, 3 statuses | dub-studio, OpenCreator, jryang1997, yuanshanhua, Voxa |
| Dub QC / take scoring | Missing | Voxa, PersoDub, jryang1997 |
| Ducking / loudness match | Missing (static gain) | bluez-dubbing, youtube-auto-dub, VideoLingo, erasedub |
| Stereo / spatial dub | Missing | asmr-dubber, ASMR3D |
| Lip-sync | Missing, off-scope for audio dramas | video-translator, VoxCPM-translator, lingxiao |
| Hard-sub erase | Partial: `hardsub_ocr.py` reads, does not erase | erasedub, PersoDub, dub-studio |
| Batch / resume | Better: DB-backed, per-batch save, `job_records` | Voxa (stage signatures), VideoLingo (ASR cache) |
| Clip by selection | Missing | FunClip |
| Live / streaming | Partial: `live_translate.py` (15-30 s chunks) | LiveTranslate, auto-caption, LLPlayer |
| Review UI | Better: React editor, speakers, flags, compare re-transcribe | LLPlayer (sidebar search, translate-ahead), linto-studio (collaborative edit) |

## 3. Owner questions

### Q1. ASMR test set: what the Benchmark Lab needs
What exists (`services/benchmark_lab_service.py`, `benchmark.py`): golden sets with tiers, per-run records, model arena, CER/WER via jiwer, pass at 0.8. A transcription case is an audio file plus optional reference text. `_run_file_case` calls `benchmark.run_transcription_case(case, whisper_size=cfg["model"], use_gpu)`, which runs `core.transcribe_for_timing` with its signature defaults (min silence 2000 ms there, not the 300 ms the app uses per title) and joins all segment text. The result is one score.

What is missing, in build order:
1. **Timed references.** Case gets an optional reference SRT (or span list with "no speech" spans marked). Without time, missed speech and timing cannot be computed.
2. **Metrics.** Missed speech (reference speech seconds with no hypothesis line), hallucination (characters or lines emitted inside reference no-speech spans, plus empty-reference silent clips: today `create_case` turns an empty reference into None, so such a clip scores nothing), timing error (median and p95 start/end offset after matching lines), CER/WER with S/D/I counts (the edit-distance code already exists; SpeechColab reports them per utterance), and a normaliser option (NFKC full/half width, traditional/simplified, numeral forms; nothing exists for ja/ko in the reference repos, so that part is new work). Keep normalisation opt-in and record its version in the run so old scores stay comparable.
3. **Settings arms.** Configs currently vary engine and model only. Add: backend (Whisper, `qwen3_asr_vad`; candidates GLM-ASR-Nano, Fun-ASR-Nano, SenseVoice via sherpa-onnx, ReazonSpeech/Anime-Whisper for ja), VAD threshold/min silence/pad, pre-VAD gain (a test arm only), L/R split, vocal separation on/off.
4. **The clips** (30-90 s each, own or CC recordings, kept outside git with only reference SRTs and a manifest in the repo): whisper ja; soft voice with long pauses; whisper over rain/brush/tapping; binaural speech alternating ears; breaths and mouth sounds with no words (hallucination check); music bed under voice; a normal-voice control; one zh and one ko clip; a fully silent clip. Eight to twelve is enough to see large effects, not small ones.
5. **Status table** in the style of OpenCreator's acceptance report: rows language x content type x stage, values PASS / FAIL / BLOCKED_ENV / NOT_RUN with date, model, clip id; a path is called supported only when a PASS exists. This is the part YouDub-webui lacks: it states in its README that Japanese has regression coverage but "has not yet completed model-quality acceptance with real Japanese media", and its CI fails the build if real model libraries get installed.

### Q2. VAD and silence settings for ASMR
- Today: faster-whisper's Silero VAD, threshold 0.5 and min silence 300 ms per title (`transcribe_service._DEFAULT_TUNING`; accepted ranges 0.1-0.9 and 300-3000 ms). `qwen3_asr_vad` uses the `vad_segments` defaults (0.5, 300 ms, min speech 250 ms, 100 ms pad, 15 s cap, 2 s context) and ignores the saved per-title values (`asr_backend.py:343`). Not exposed: speech pad, min speech, no-speech threshold.
- Baihe's own evidence (`docs/asr-experiments.md`, VAD sweep): threshold 0.3 and silence 100/1000 ms changed nothing beyond noise, 0.7 was worse by 3.2 points. That was read single-sentence speech in three conditions; it does not cover whispers, breaths or long pauses.
- Other tools: subforge ASMR preset (threshold 0.2, min speech 100 ms, min silence 300 ms, pad 600 ms, span cap 20 s, no-speech 0.3, loudnorm to -12 LUFS before VAD; its stated reason is that whispers sit around -40 to -50 dB and read as silence), EaraAsmrPlayer (0.15, 150/200 ms, 250/350 ms pre/post-roll, split over-long regions at the quietest point), ArisuLoveASMR (onset 0.3-0.4 advised). None measured error. GLM-ASR-Nano advertises quiet-speech training (README claim only).
- **Answer:** a conservative preset is plausible and cheap, but not yet justified. Users can already try threshold 0.1-0.3 per title with no code change. Order of work: build Q1, run the grid (threshold 0.15/0.3/0.5, pad 100/400/600 ms, no-speech 0.3/0.6, with and without a capped gain), then add the preset only if missed speech drops without a hallucination rise. Denoising and loudness normalisation must not be defaults: Baihe's own noisy-audio benchmark found Demucs made noisy audio worse, asmr-enhancer has no ASR evidence, and spectral gating can erase breathy consonants. Treat gain as a test arm only.

### Q3. Importing an existing transcript or subtitle file: what exists
| Capability | Today | Evidence |
|---|---|---|
| Plain text transcript, one line per row or sentence-split | Has | `core.split_user_transcript`; API `transcript_text` (`api/routers/transcribe_routes.py:73`); CLI `--transcript` (`cli.py:422`) |
| Timing for that text | Derived from audio: diff against Whisper segments (`core.align_transcript_to_timing`) or Qwen3-ForcedAligner (`forced_align.align_with_qwen3`, with `timing_uncertain`) | Timestamps in the file are not read |
| SRT / VTT / ASS / LRC parse | Missing | No parser in `*.py` (grep); frontend upload accepts jsonl/json/tsv/txt only for golden sets, zip/db for backups |
| Bilingual file split, encoding detection, tag stripping | Missing | |
| Validation (overlap, order, zero length) | Missing for import; export has `clamp_overlaps` | `subtitle_formats.py:90` |
| Export | Has SRT, bilingual SRT, VTT, ASS; no LRC | `core.lines_to_srt`, `subtitle_formats.py` |
| Sidecar discovery next to media | Missing | |
Design points from the field: parse and reject at upload time (YouDub-webui); choose whether the file is source text or already translated (YouDub-webui, dub-studio and OpenCreator each have that switch); keep file timing as authoritative but offer re-alignment; when a hand-edited file comes back, re-attach speaker and source by more than 50% time overlap (erasedub); detect a file's language from its characters rather than its name (Kikoeru); keep `track.mp3.vtt` style names on export.

### Q4. Word-level alignment as a reviewable timing step
Exists: `word_align.py` (MMS_FA CTC over jieba/sudachi/kiwi-segmented words; opt-in `realign_long_segments`; oversized segments only; fails soft per line); `forced_align.py` (Qwen3 aligner, chunked, span repair, `timing_uncertain` flag with a reason string in `engine_backends/llm_tasks.py`); Re-split with audio alignment (`resegment.py`); "Compare transcription" and "Re-transcribe…" per line; Player with play/loop line; start/end editing in the line row; `diagnose_line_coverage`. Not found: stored word times (`core.Line` has none), a waveform, a word-level view, any way to accept or reject a re-alignment per line. I did not verify whether Review filters lines by `timing_uncertain`.
Needed: (1) a words column or table with an `ADD COLUMN` migration and an entry in `_INIT_DB_MIGRATED_COLUMNS` (`tests/test_db.py`); (2) a job "align words for these lines" writing only the fields it owns (`db.save_lines(..., fields=...)`), reusing `forced_align` for user text and `word_align` otherwise; (3) result stored as a candidate that Review shows next to the current timing with accept/reject (the same pattern as Compare transcription's `apply_compare`); (4) UI: peaks waveform, word chips with nudge, play-word; (5) digits and symbols: whisperX's current code keeps out-of-vocabulary characters as wildcard emissions and interpolates untimed words, which suggests a fix for the known weakness (VideoLingo's README says numbers and symbols have no reliable timings; the same applies to Baihe's aligners, unverified on real audio); (6) per-word confidence if faster-whisper's word probability is kept (unverified whether `core.py` keeps it; it uses `tighten_to_words` on word start/end only). Effort L; risk is mostly storage size and the UI.

### Q5. ASMR player/library ideas that fit
Topic `asmr`: 104 repos, 60 visible on pages 1-3 as plain web pages ("Load more" not available); of those, 23 were cloned and read, the rest dropped (downloaders, keyboard-sound toys, generators).
| Idea | From | Fit | Effort |
|---|---|---|---|
| Sidecar binding rules: strip subtitle ext, optional language token, optional media ext; nearest folder wins; language rank; normalise full/half width, brackets, `TR` prefix, SE-variant suffixes; fuzzy fallback at 0.85; show "2 candidates" instead of silent binding | KikoFlu, Eara, KikoeruManager, whisplayer | Yes (gap 1) | S-M |
| Subtitle language from content (kana/hangul/han/latin counts) | Kikoeru (ValoHalo) | Yes | S |
| Write sidecars beside media on export (`<audio>.<lang>.srt`, `.translated.<lang>.srt`) | Eara, Doujin-Audio | Yes | S |
| Link a translated edition to its original title (Baihe series) | KikoFlu, KikoeruManager | Yes | S-M |
| Embed bilingual lyrics, cover and VA/circle tags into exported audio | asmr-one-flac-helper, Doujin-Audio | Maybe; Baihe already exports m4b chapters (`dub.export_narration_m4b`) | S-M |
| Include/exclude library search terms (`-term`, quoted phrases) | neokikoeru, KikoFlu | Yes; Baihe already filters by voice actor and studio (README) | S |
| Re-run a time window as a candidate | subforge | Mostly exists: Re-transcribe… and Compare transcription with apply | - |
| Feed the title's own script file to translation on demand | Eara | Maybe; Baihe has novels, story context | M |
| Waveform seek bar showing pauses | mimiuchi | Yes, with #9 | S-M |
| Resume per-chunk when aligning a supplied script | Doujin-Audio | Check `forced_align` resumability | S-M |
Not fit: downloaders and asmr.one clients, streaming servers and accounts, listening-quality enhancement, sleep timers, balance/EQ/reverb, HRTF rendering (except as input to gap 5), real-time per-line translation while playing. None of the players has chapters beyond file-level tracks.

## 4. Per-repo notes (stage 2)

Licence is the repo's licence file; model weights can differ and were not checked. Dates are last commits.

### Must-review
| Repo | Finding |
|---|---|
| Huanshere/VideoLingo (Apache-2.0, 2026-10-01) | Default ASR is now Qwen3-ASR + Qwen3-ForcedAligner; WhisperX is a manual install. With Demucs on it recognises the raw track and aligns on the vocal stem. Splitting is spaCy + LLM; reflection is folded into one rewrite call with fuzzy chunk matching; glossary is one call over the first 8000 characters. **Baihe matches or beats:** transcription options, CJK splitting, three id-keyed reflect passes with stored critiques, terminology (`glossary_service.py`, `translation_guide.py`), formats, resume, UI. **VideoLingo has, Baihe lacks:** SRT import, content-keyed ASR cache, pause/resume, dub chunk merge with a syllable duration estimator and LLM trim-to-fit, gated loudnorm on the dub, sample-timeline hygiene (aresample async, ffmpeg-encoded stems), one-call theme summary. **README limits that also apply:** unreliable timing on numbers and symbols, one language per window (Baihe's per-span detection is off by default), speed-only dub fitting. |
| zh-plus/openlrc (MIT, 2026-08-10) | Uses faster-whisper, not WhisperX. Chunks translation by ~1000 tokens, 30 lines, hard split at a 30 s gap, soft split at the largest gap; an up-front guideline and rolling summaries carry context. Baihe loses long-range context inside an episode (gap 6); its defaults are configurable, not fixed. Optional denoise + ffmpeg-normalize before ASR (Baihe has neither). Its proofreader agent is defined but never called. Cleans output (merges short cues, collapses runaway repeats). Exports LRC. |
| m-bain/whisperX (BSD-2, 2026-09-26) | Separate stages: VAD (pyannote or Silero) then chunk merge to 30 s, batched ASR, wav2vec2 alignment per language, pyannote diarization, word-level speaker assignment. Its README says numbers cannot be aligned but current code aligns them loosely with wildcard emissions. Failed segments keep Whisper timing with no flag; Baihe's `timing_uncertain` is better. No fallback for languages without an aligner (Baihe has one). ja/zh are aligned per character with XLSR models; `word_align.py` already argues MMS is safer on rare kanji. Worth adding: summed per-speaker assignment and nearest-turn fill (gap 8), wildcard for out-of-vocabulary characters. Not worth adding: pyannote VAD hysteresis, 30 s chunk gluing, XLSR aligners. |
| liuzhao1225/YouDub-webui (Apache-2.0, 2026-09-21) | Tests are about 20 pytest files with fakes; CI installs light dependencies only and fails if torch/whisper/demucs are importable. It has no test media and no acceptance job; the Japanese tests only check that the language code reaches the model. Its real separation is a written status per path. SRT import: no new stage; each of `asr`, `asr_fix` and `translate` checks for an uploaded `.srt` and writes its normal artifact from it. The SRT is treated as already-translated text, with one fixed speaker; it is parsed at upload and rejected if malformed, but overlap and order are not validated. Baihe equivalent: Q1 (acceptance set + status table) and Q3. Baihe's `scripts/smoke_pack.py` already covers the upgrade tripwire (own clip, own approved baseline) but cannot catch a wrong baseline. |
| asmr-dubber (EveningStudy, MIT) | Compared only. Features live in `vad.py` (ASMR VAD, condensed analysis audio), `spatial_cues.py`/`spatial_rtf.py`, `audio.py` (loudness match, boost cap 12 dB), `experimental_mix.py` (replacement bed), `indextts25_worker.py`, `services/batch*.py`, `transcript_import.py` (SRT/VTT/ASS/LRC). Reusable under MIT for gaps 1 and 5. |

### Transcription and alignment repos
| Repo | Licence | Takeaway |
|---|---|---|
| whisper-diarization | BSD-2 | Snaps speaker changes to sentence ends. Punctuation model covers 12 European languages and ASCII sentence enders only; does not work for CJK as written. |
| whisper-timestamped | AGPL | DTW word times, per-word confidence, `[*]` marker for speech Whisper omitted (experimental, ideas only). |
| CrisperWhisper | MIT code, non-commercial weights | Rewind-and-escape repetition handling beats `no_repeat_ngram`, but needs a CTranslate2 fork that conflicts with stock faster-whisper. Not adoptable. |
| WhisperS2T | MIT, stale 2024 | Stitches VAD spans into ~27 s windows for batching; join risk for hallucination. |
| faster-whisper-GUI | AGPL | Reads SRT/JSON back into a segment table (the import gap); exports LRC/SMI. |
| FunASR | MIT | Post-ASR hotword correction, ITN grammars for zh/ja/ko (pynini, heavy), `merge_vad` at 15 s, FSMN-VAD. |
| SenseVoice | MIT | Baihe's `sensevoice_tags.py` already matches the full tag set; the no-speech tag is unused; 30 s windows with 2 s overlap and text overlap merge for audio with no clear silences. |
| sherpa-onnx | Apache-2.0 | ONNX runtime for SenseVoice/Paraformer/FireRed/Zipformer-ReazonSpeech, ten-vad, Silero hysteresis and threshold raise on overlong spans, punctuation, diarization. Candidate torch-free engine (register in `diagnostics.OPTIONAL_DEPENDENCIES`). |
| FireRedASR / ASR2S | Apache-2.0 | zh/en only; needs VAD chunking (60 s limit); FireRedVAD adds start padding and speech/singing/music labels (F1 claim README-only). |
| GLM-ASR | Apache-2.0 | Only model README claiming quiet/whispered-speech training; fixed 30 s chunks, no timestamps; ja/ko unverified. First candidate for the ASMR benchmark. |
| Mega-ASR / whispr | none / AGPL | Mega-ASR LoRA is trained on English and Chinese only and returns text without timestamps; whispr's "degraded audio" mode is just that swap, no preprocessing. |
| speechio, SpeechColab | MIT / no licence file | Normalise both sides before scoring (NFKC, t2s, numerals, fillers, erhua); per-utterance C/S/D/I. SpeechColab drops empty hypotheses before scoring, which Baihe must not copy. No ja/ko rules exist. |
| FunClip | MIT | Text-selection and LLM clipping; hard 8 s/30-token sentence cap; unsafe `eval()` in state loading. |
| LLPlayer | GPL-3.0 | Translate-ahead around the playhead, sidebar search. Ideas only. |
| zenstory video-recap-skills | MIT | Sentence-boundary gate for cut edges; burned-subtitle band locator. |
| auto-caption, LiveTranslate | MIT | Live captioning with Silero + SenseVoice/Fun-ASR/Anime-Whisper; low latency, no context. |

### Dubbing and video-translation repos
| Repo | Licence | Takeaway |
|---|---|---|
| dub-studio | MIT | Best dub-fit design: per-line fit verdict from calibrated chars/s, LLM shorten loop, take history, subtitle QC profiles, SRT/ASS import. Windows + NVIDIA only. |
| OpenCreator (ex-KrillinAI) | Apache-2.0 | Estimator with punctuation/number weights, chunk-level speed, SRT import with source/translated flag, acceptance report vocabulary. |
| jryang1997/video-translate-dub | Apache-2.0 | Self-calibrating duration model, dry-run placement, dub-track QC by re-transcription, clone-reference picker, TTS cache by fingerprint. |
| Voxa | MIT | `--subtitles` skips ASR, cumulative stage signatures for resume, TTS quality gate with best-of-N, no-drift placement. |
| PersoDub | AGPL | Wrong-speaker gate, original-voice leakage gate, keeps breaths/laughter only if Whisper confirms no words (closest ASMR idea). Clean-room only. |
| erasedub | Apache-2.0 | `prepare`/`render` split with an editable SRT that is authoritative; speech-band cut of the original under the dub. |
| Friend-Xu/Translate-video-WebUI | Apache-2.0 | Translation "bible", speaker-drift detector, diarization self-check, checkpoint manifest. |
| bluez-dubbing, yuanshanhua, youtube-auto-dub, video-translator | Apache-2.0 / MIT / MIT / none | Ducking envelope; neighbour-silence borrowing; loudness match to source; pad/trim when the stretch ratio is absurd. video-translator has no clear licence. |
| mikey1384/translator | MIT | Subtitle QC codes (unpaired brackets, source-script leakage, glossary miss), per-export manifest with frame hashes. |
| VoxCPM-translator | MIT | Immutable keys per batch, halve the batch on repeated failure, save failed replies; bracket sound markers never voiced. |
| ZastTranslate | none | `srt_cleaner` rule list and measured chars/s per language; ideas only. |
| OpenTranscribe | AGPL | Island smoothing of word-level speaker labels, word-level speaker-error benchmark. Ideas only. |
| langswap, ComeCut | AGPL | Ideas only (VAD-first segmentation, LRC I/O). |
| CreatorBox, xiaoniu, GhostCut, Chenyme-AAVT, YouDub (original), kekedubing, shang-zhu/violin | various | Nothing usable: obfuscated or README-only (CreatorBox, xiaoniu), cloud API scripts (GhostCut), stale or weaker than Baihe (AAVT, YouDub, kekedubing); violin's only extra is slowing the video to fit speech. |

### ASMR repos
| Repo | Licence | Takeaway |
|---|---|---|
| subforge | MIT | ASMR preset, per-channel transcription, candidate re-run flow, creators/tags as entities. 3 stars; weaker than Baihe on hallucination handling, alignment, QC. |
| EaraAsmrPlayer | none | Most careful ASMR VAD (above), sidecar binding rules, script-aware translation by tool calls. Ideas only. |
| KikoFlu, Doujin-Audio, Kikoeru (ValoHalo) | GPL-3.0 | Multi-format parse, fuzzy matching, sidecar naming, content-based language detection. Ideas only. |
| KikoeruManager, whisplayer, ASMR-Audio-Preprocessor, asmr-enhancer | MIT | Pairing by track number + LLM; zip lyric packs; channel-fault repair; listening-quality processing without ASR evidence. |
| neokikoeru, Asmr-Player, KK-Maid, Baimo, mimiuchi | proprietary / none | Docs-only or README-only; behaviour unverified in code. |

## 5. Where Baihe is already ahead (do not chase)
- Translation robustness: id-keyed batches with retry and bisect, three separate reflect passes with stored critiques, glossary by policy with hard substitution, translation memory, auto QC, cost caps, per-line spoken language. VideoLingo, openlrc and every dub repo match on position or fuzzy text, or fold reflection into one call.
- CJK line splitting (`resegment.py`) and subtitle formats (ASS styles, speaker colours, wrapping, dense-line flag); LRC is the only format others have that Baihe lacks.
- Honest alignment failure: `timing_uncertain` flag plus a fallback chain for languages without an aligner (whisperX silently keeps Whisper timing, then raises for unsupported languages).
- Anti-loop decode settings plus a repeat filter; Demucs kept off by default on measured evidence; benchmarks on real FLEURS ko/ja/zh and noisy audio (`docs/asr-experiments.md`) that most repos lack entirely.
- Resume and data model: DB-backed lines with permanent ids, per-batch saves, job records, ownership and permissions; the editor and speaker tools (merge, naming with undo, compare re-transcription).
- Glossary/terminology extraction across the whole text, not the first 8000 characters.
- Voice bank and voice ID for speakers; most dub repos fix one speaker.

## 6. Licence red flags
- **AGPL-3.0, ideas only (clean-room):** OpenTranscribe, PersoDub, whispr, whisper-timestamped, faster-whisper-GUI, langswap, ComeCut, fossiaorg/transcribeit, shizi.
- **GPL-3.0, ideas only:** LLPlayer, Easy-Voice-Toolkit, KikoFlu, Doujin-Audio, Kikoeru (ValoHalo), Kikoenai, ArisuLoveASMR, Leg2Sub.
- **No licence file (all rights reserved):** EaraAsmrPlayer, KK-Maid, Asmr-Player, Baimo ASMR-Player, mimiuchi, CreatorBox, xiaoniu, GhostCut, ZastTranslate, video-translator (pyproject says MIT, README says educational/personal use), SpeechColab/Leaderboard, Mega-ASR, pyannote-whisper, Purfview binaries, VideoLingo-OneClick.
- **Proprietary:** neokikoeru (paid licence), Mosael.
- **Model weights are separate from code:** CrisperWhisper (non-commercial research or paid), FunASR family (separate model licence), FireRed, GLM-ASR, Fun-ASR, ReazonSpeech, Mega-ASR. Check before shipping any as a default.
- Permissive and reusable with notice: VideoLingo, openlrc, whisperX, whisper-diarization, YouDub-webui, dub-studio, OpenCreator, Voxa, erasedub, asmr-dubber, subforge, sherpa-onnx, FunClip. Even here, this report recommends reimplementing ideas rather than copying code.

## 7. What I could not verify
- No model was run and no audio was heard. Nothing here is a quality claim: every accuracy figure (FireRedVAD F1, GLM-ASR quiet speech, CrisperWhisper timing, whisperX gains) is the repo's own README claim.
- None of the ASMR VAD settings was measured by its authors; I did not measure them either.
- Whether the Review UI filters on `timing_uncertain`, whether the `word_align.py` soft-failure path sets it, and whether word probabilities are kept anywhere: not checked.
- Whether Baihe's Demucs path has the encoder-delay drift VideoLingo documents, and whether `forced_align` can resume per chunk: not checked.
- Whether dub output keeps stereo through `build_dub_track`: only the absence of spatial/loudness code was confirmed.
- GitHub stars were taken from the owner's lists; repos not on those lists show "n/g". Weight licences were not checked. The asr topic list was extended only with page 6 (20 repos, "Load more" unavailable); pages 7+ were not tried, so the list is the top ~120 visible. Of the ~120 video-translation repos, the ~60 zero-to-three-star ones not named by the owner were skipped. The `asmr` topic shows 104 repos but only 60 loaded.
- creator tools whose core is obfuscated (CreatorBox) and README-only repos (xiaoniu, GhostCut scripts, Asmr-Player, neokikoeru) could not be read in code.
- STATUS.md was last checked at 175d617; the base is 68595ce. The claims above were re-read against the code at the base, not taken from STATUS.md.

## Appendix A. Stage 1 repos kept (README level)

Stars from the owner's lists. Stage-2 repos are in section 4; their licences and dates are in the same notes.

| repo | stars | licence | last commit | what |
|---|---|---|---|---|
| stronghamjji/PersoDub | 39 | AGPL-3.0 | 2026-09-29 | Desktop (mac/win) video dubbing, "ElevenLabs/HeyGen alternative", local |
| johunsang/kekedubing | 20 | MIT | 2026-05-21 | Korean-authored local video translate+dub web app (FastAPI, single-file HTML) |
| deijing/shiyibao | 15 | none | 2026-08-15 | 视译宝: Chinese AI video translate+dub workbench (FastAPI + React) |
| akshinmrv/Voxa | 15 | MIT | 2026-08-20 | Single-file Python dubber (pipx voxa-dub) whose selling point is zero drift |
| fispurring/lightVT | 8 | MIT | 2026-06-07 | Lightweight GUI subtitle translator using a local LLM |
| kadirb4rut/video-dubbing-translator | 6 | MIT | 2026-09-28 | Local-first dubbing with voice-reference cloning, optional lip-sync, browser GUI + CLI |
| Friend-Xu/Translate-video-WebUI | 4 | Apache-2.0 | 2026-08-03 | Subtitle extract > translate > TTS > bilingual dubbed video, CLI + React WebUI |
| sting11k/erasedub | 3 | Apache-2.0 | 2026-09-28 | Erase burned-in (hard) subtitles with video inpainting, then transcribe/translate/dub/re-subtit |
| bzcsk2/VoxCPM-translator | 2 | MIT | 2026-07-01 | Chinese drama video -> English dubbed video, research release |
| highboyvulpes597/xiaohu-video-translate | 2 | MIT | 2026-10-07 | Agent "skills" (scripts + instructions): download > Whisper > translate to Chinese > polish > b |
| jryang1997/video-translate-dub | 1 | Apache-2.0 | 2026-09-20 | Mac-local English->Chinese voice-clone dub as CLI/agent skill |
| devrim/mid-voice | 1 | MIT | 2026-08-07 | Browser app: video -> English dub in speaker's cloned voice, fully local |
| ruiflow-team/lingxiao | 1 | Apache-2.0 | 2026-06-17 | Local EN->Chinese dub + Wav2Lip lip-sync, desktop GUI |
| dngrs-dev/dublaro | 1 | MIT | 2026-09-24 | Open CLI dubbing pipeline |
| RaviKachhwaha/AutoDub-Pro | n/g | none | 2026-07-29 | Gemini + Edge-TTS dubbing with acoustic tricks |
| franckferman/whispr | 3 | AGPL-3.0 | 2026-08-20 | CLI + web UI over whisper.cpp / faster-whisper / OpenAI API / optional "Mega-ASR" for degraded  |
| 111aaa327/shipin-zhuanlu | 2 | MIT | 2026-08-22 | Chinese long-video chunked transcription with resume |
| charles1018/NemoScribe | 1 | MIT | 2026-08-12 | NVIDIA NeMo Parakeet-TDT SRT generator, VAD presets, optional LLM name/homophone fix |
| attevon-llc/OpenTranscribe | 99 | AGPL-3.0 | 2026-10-06 | Large self-hosted transcription platform (Docker, GPU) |
| linto-ai/linto-studio | 59 | AGPL-3.0 | 2026-09-28 | Transcript platform: diarization, cross-recording speaker ID, collaborative editor |
| AliAkrami1375/Li-Translate | 47 | none | 2026-07-19 | Single-page browser subtitler: in-browser ffmpeg.wasm audio extract, two agents (transcribe, tr |
| modelscope/FunClip | 6.4k | MIT | 2026-09-16 | Alibaba FunASR-based clipping tool: Gradio, LLM-driven clip selection, SRT per segment |
| k2-fsa/sherpa-onnx | 15.2k | Apache-2.0 | 2026-10-06 | ONNX runtime for ASR, TTS, VAD, diarization, punctuation, KWS, source separation, speech enhanc |
| umlx5h/LLPlayer | 4.3k | GPL-3.0 | 2026-07-19 | WPF media player for language learning with dual subtitles, Whisper ASR subtitles, LLM context- |
| 0xShug0/audio.cpp | 3.4k | Apache-2.0 | 2026-10-07 | ggml/GGUF C++ runtime for 110+ audio model families (TTS, ASR, VC, separation, alignment, VAD,  |
| CheshireCC/faster-whisper-GUI | 3k | AGPL-3.0 | 2024-12-06 | PySide6 GUI over faster-whisper + whisperX; ships a "Prompt and Hotwords" PDF guide |
| linto-ai/whisper-timestamped | 2.9k | AGPL-3.0 | 2026-09-28 | Word timestamps + confidence from DTW over Whisper cross-attention |
| FireRedTeam/FireRedASR | 2k | Apache-2.0 | 2026-02-25 | Industrial Mandarin/dialect/English ASR, singing-capable |
| handy-computer/transcribe.cpp | 2k | MIT | 2026-10-07 | ggml/GGUF C++ STT library, 16 model families, 60+ variants, with a benchmark catalog (catalog.d |
| QwenAudio/Fun-ASR | 1.6k | Apache-2.0 | 2026-09-10 | Tongyi Fun-ASR-Nano (zh/en/ja + dialects) and MLT-Nano (31 langs), llama.cpp single-binary runn |
| nyrahealth/CrisperWhisper | 1.4k | MIT | 2026-09-22 | Whisper-derived verbatim ASR (fillers, stutters, laughter) with "verbatim" and "intended" modes |
| R3gm/SoniTranslate | 1.4k | Apache-2.0 | 2026-08-28 | Gradio video translation + dubbing (WhisperX, diarization, Piper/XTTS/OpenAI TTS) |
| yeyupiaoling/Whisper-Finetune | 1.2k | Apache-2.0 | 2026-05-08 | Whisper fine-tune + CTranslate2/GGML acceleration recipes, zh-focused |
| xzf-thu/Mega-ASR | 1.1k | none (no LICENSE f | 2026-09-02 | Foundation ASR trained on 7 atomic + 54 compound acoustic conditions for in-the-wild robustness |
| zai-org/GLM-ASR | 856 | Apache-2.0 | 2026-03-06 | GLM-ASR-Nano-2512 (1.5B): dialects (Cantonese) and low-volume / "whisper/quiet speech" robustne |
| TheDeathDragon/LiveTranslate | 743 | MIT | 2026-08-17 | Windows live translation: WASAPI loopback to VAD to ASR to LLM to overlay |
| speechio/chinese_text_normalization | 741 | MIT | 2023-03-18 | Rule-based Chinese text normalization (TN) for scoring and speech pipelines |
| FireRedTeam/FireRedASR2S | 701 | Apache-2.0 | 2026-06-02 | All-in-one: FireRedASR2 + FireRedVAD + FireRedLID + FireRedPunc |
| HiMeditator/auto-caption | 589 | MIT | 2026-02-10 | Cross-platform real-time caption overlay with translation |
| shashikg/WhisperS2T | 580 | MIT | 2024-08-25 | Fast Whisper pipeline with pluggable VAD, batching, TensorRT-LLM / CTranslate2 backends |
| SpeechColab/Leaderboard | 557 | no LICENSE file se | 2025-03-29 | SpeechIO ASR benchmark: testset zoo, model zoo, benchmarking pipeline |
| MahmoudAshraf97/whisper-diarization | 5.7k | BSD-2-Clause | 2026-08-15 | Whisper + VAD + speaker embedding diarization, vocals extracted first |
| m-bain/whisperX | 24.4k | BSD-2-Clause | 2026-09-26 | Batched Whisper (70x realtime) + wav2vec2 forced alignment + VAD pre-segmentation + diarization |
| modelscope/FunASR | 20.6k | MIT | 2026-10-02 | Industrial ASR toolkit: ASR, VAD (fsmn), punctuation, speaker (cam++), emotion and audio-event  |
| FunAudioLLM/SenseVoice | 9.5k | MIT | 2026-09-30 | SenseVoiceSmall: ASR + language ID + speech emotion + audio event tags, non-autoregressive |
| reazon-research/ReazonSpeech | 402 | Apache-2.0 | 2026-06-10 | Japanese speech corpus + models, plus AVista (noise-robust audio-visual ASR) |
| altunenes/parakeet-rs | 406 | MIT | 2026-10-08 | Rust ONNX/burn runtime for NVIDIA Parakeet, Nemotron streaming, Sortformer diarization |


## Appendix B. Dropped repos (one-line reason)

| repo | stars | reason |
|---|---|---|
| Alndaly/Mosael | 15 | proprietary, generative-video editor not subtitle tool. (Idea only: ready-made workflow templates) |
| alex2844/youtube-translate | 12 | API-client wrapper, no ASR |
| LuckBots/voxsail | 4 | SaaS advert. (Idea: standalone SRT lint) |
| HSU-OverLang/OverLang | 1 | course-style project; (idea: click-a-word learning cards, OCR overlay) |
| IlhamJayK/VideoSyncMaster | 1 | no content |
| Phong-EpicMind/video-translate-pro | n/g | generic glue, Vietnamese focus, nothing Baihe lacks |
| cola11011/SimvooAI_Video_Localized_Translation | n/g | no code/model. Topic (short-drama localisation) relevant; idea only |
| fantomcheg/vot-cli-live | 43 | API client wrapper |
| adhikary97/Sharetape-Speech-To-Text | 135 | stale 2023 glue, English only |
| KIRVO-REPORTING/video-to-notes | 105 | note-taking tool, not subtitles (YouTube/Bilibili only) |
| HuangYincan/VideoNote-MCP | 96 | MCP note generator |
| fossiaorg/transcribeit | 19 | basic, roadmap items not done |
| qianzhu18/Muku | 12 | knowledge-base scraper (Bilibili/Douyin focus). Idea: NDJSON progress |
| wangjs-jacky/video2text | 8 | downloader+whisper glue |
| yanlingLabs/video-extract-mcp | 7 | agent tool; (idea: keyframe dedup for comics/video) |
| 6Morpheus6/whisper-webui | 5 | installer wrapper (upstream Whisper-WebUI is a separate candidate) |
| Air000000/bilibili-content | 4 | scraper. (idea: detect misaligned platform subs) |
| Ceryunus/Subpipe | 3 | generic pipeline, nothing new |
| YannJY02/AutoTranscribe | 3 | meeting app, macOS-only. (mixed zh-en detection idea) |
| gejiangren/shizi | 2 | Mac downloader/transcriber, no translation or dub |
| coelhoxyz/vidscribe | 2 | generic |
| Paulogb98/Leg2Sub | 2 | generic, Portuguese user; (idea: SRT/VTT -> translate-only entry; Baihe has no subtitle import) |
| ukolov-dev/LocalScribe | 1 | Mac-only desktop dictation-ish |
| nkmnhan/whisper-burner | 1 | live-caption overlay, different product |
| artificemachine/vidistiller | 1 | docs generator |
| BrendownFerreira/transcriber | 147 | generic |
| 169/intelli-video | 74 | stale 2024, glue |
| KaanGoker/GetSubtitlesApp | 11 | generic (idea: vertical short-caption splitting) |
| katkaypettitt/video-to-text-japanese | 6 | stale cloud notebooks (Japanese) |
| codeonthespectrum/AISubs | 5 | 5 stars glue |
| NVIDIA-NeMo/NeMo | 18.6k | Low as a dependency; models are reachable via lighter runtimes (sherpa-onnx, parakeet-rs, transcribe.cpp). Reason: heavy |
| alphacep/vosk-api | 15.2k | Weak CJK accuracy vs Whisper/SenseVoice; older tech |
| PaddlePaddle/PaddleSpeech | 12.7k | Low; Paddle stack, superseded by FunASR/sherpa. Only the zh text-frontend / punctuation ideas matter |
| speechbrain/speechbrain | 11.9k | Low; training-oriented toolkit |
| wenet-e2e/wenet | 5.2k | Medium-low; same niche as FunASR/sherpa, only zh/en |
| ahmetoner/whisper-asr-webservice | 3.4k | None: API wrapper |
| Purfview/whisper-standalone-win | 3.2k | Medium as a community reference for "good Whisper presets"; closed binaries, nothing to port |
| AutoArk/GPA | 3.1k | Low: research model, no ja/ko claims in README |
| FluidInference/FluidAudio | 3k | Low for a Windows/Python app (Apple only); the ITN module is the interesting idea but English-oriented |
| coqui-ai/STT | 2.6k | None: unmaintained (README says so) |
| harry0703/AudioNotes | 2.5k | Low: notes app. Only the FunASR hotword idea |
| k2-fsa/sherpa-ncnn | 1.8k | Low: superseded by sherpa-onnx |
| mkiol/dsnote | 1.7k | Low: desktop dictation app |
| byjlw/video-analyzer | 1.6k | None: video description tool |
| ictnlp/StreamSpeech | 1.3k | Low: research code, fr/es/de to en |
| k2-fsa/sherpa | 999 | Low: superseded by sherpa-onnx |
| yeyupiaoling/PPASR | 870 | Low: training-only, zh |
| Spr-Aachen/Easy-Voice-Toolkit | 874 | Low: voice-conversion dataset pipeline |
| yeyupiaoling/MASR | 728 | Low: training toolkit |
| yinruiqing/pyannote-whisper | 675 | None: simple glue, Baihe already has diarize.py |
| abhirooptalasila/AutoSub | 651 | None: abandoned, English |
| sooftware/kospeech | 636 | None: README says archived and recommends Whisper |
| RapidAI/RapidASR | 611 | Low: merged upstream |
| zenstory-ai/video-recap-skills | 555 | Low: cloud API pipeline, not local |
| AudarAI/Audar-ASR-V1 | 508 | None: Arabic only (not cloned beyond README) |
| Picovoice/leopard | 485 | None: proprietary engine, requires internet key validation |
| Evil0ctal/Fast-Powerful-Whisper-AI-Services-API | 475 | None: API server |
| jim60105/docker-whisperX | 457 | None: packaging |
| revdotcom/reverb | 440 | Low: English focus |
| roothch/PreenCut | 418 | Low: clipping tool |

