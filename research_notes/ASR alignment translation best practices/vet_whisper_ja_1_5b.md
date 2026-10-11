# Vetting: whisper-ja-1.5B (efwkjn) as a Japanese ASR model for Baihe

Vetted 2026-10-11 by a read-only model-vetter; saved by the lead session because the vetter has no write tool. Pages fetched 2026-10-11.

## Verdict

Use with limits: an opt-in, Japanese-only model the user downloads themselves. Not the default, not bundled. Do not adopt until it beats large-v3-turbo in the Benchmark Lab on Baihe's own anime, drama and ASMR samples. Not usable in Baihe today without a code change.

For: the author's benchmarks put it at or near the best on short-form anime/game sets (Nekopara CER 21.0 vs 62.3 for large-v3 and 62.2 for turbo); unlike large-v3 and kotoba it does not collapse when decoding is conditioned on previous text; a CT2 build exists that faster-whisper loads by repo id; WhisperJAV ships it as a Balanced-pipeline option.

Against / limits: no licence on any repo (source, CT2 conversions, siblings); training data terms mixed (ReazonSpeech CDLA-Sharing-1.0, Article 30-4 only; game, visual-novel and ASMR data of unclear licence); the card warns of "occasional repetition"; no evidence on ASMR or whispered speech; speed about large-v3's, roughly 2x slower than turbo (inference); CT2 builds predate the author's 2026-09-18 alignment-heads fix (effect on word timestamps unverified); one user report (WhisperJAV #425) says "noticeably more omitted lines" than Qwen3-ASR and misaligned start times (one user, no numbers).

## Identity and siblings

- Source: `efwkjn/whisper-ja-1.5B` (weights 2026-02-13; last commit 2026-09-18 changed only `alignment_heads`, 20 heads to 9).
- CT2 build WhisperJAV ships: `TransWithAI/whisper-ja-1.5B-ct2` (sha 1527314, 2026-06-12, bfloat16, `model.bin` 3,087,284,237 B, pre-fix 20 alignment heads, Balanced pipeline only). Others: `aikobin/whisper-ja-1.5B-ct2`, `Jim6789/whisper-ja-1.5B-ct2` (not inspected).
- Same author's newer models (updated 2026-09-18): `efwkjn/whisper-ja-760M` (large-v3-turbo fine-tune, about 45k hours, "slightly better than 1.5B on short form, worse on long form"; own CT2 build `efwkjn/faster-whisper-ja-760M`, 1.51 GB, needs ctranslate2 >= 4.7.1), `whisper-ja-22M` / `-51M`, `whisper-ja-anime-v0.1`-`v0.3`, and non-Whisper `cohere-asr-ja*` (not loadable by faster-whisper). A further pruned 488M model is said to be in training.

## Claims

| Claim | Value | Status |
|---|---|---|
| Base | fine-tune of openai/whisper-large-v3; 32+32 layers, 128 mels, vocab 51866 | verified |
| Parameters | 1,543,490,560 (F32 906.5M + F16 637.0M), dense | verified |
| Licence | none declared on source, TransWithAI CT2 or 760M | verified (none declared) |
| Training data | OOPPEENN, Reazon, 小虫哥_, Common Voice 20, deepghs (names only) | names only |
| OOPPEENN/ASMR_Dataset | GPL-3.0; whether the model used it is unverified | partly |
| Card caveats | "worse long form, equal short form"; "occasional repetition" | verified |
| WhisperJAV | `TransWithAI/whisper-ja-1.5B-ct2`, Balanced only; Fidelity pipeline cannot load CT2; ASMR advice is Fidelity or anime-whisper at Aggressive, not whisper-ja; claim of "strongest results in our scene-length benchmarks" has no published numbers | verified as stated |
| faster-whisper loading | name containing "/" is a HF repo id; downloads config, preprocessor_config, model.bin, tokenizer.json, vocabulary | verified |
| bf16 model.bin with compute_type float16/int8 | expected from CT2 load-time conversion | unverified |
| VRAM (estimate, scaled from large-v2 vendor figures) | fp16 beam 5 about 4.5 GB; int8 about 2.9 GB; fp16 batched (batch 8) about 6.1 GB; fits 8-12 GB alone | estimate |

## Accuracy (CER %, author's BENCH.md; columns and normalisation undefined)

- Common Voice 20 test, beam 5: 1.5B 24.1; large-v3 25.4; turbo 28.0; kotoba-v2 26.6; anime-whisper 28.6; 760M 25.4.
- Short-form (FLEURS / JSUT / Reazon / BlueArchive / Nekopara): 1.5B 5.0 / 6.4 / 15.9 / 6.5 / 21.0; large-v3 4.7 / 7.2 / 14.1 / 10.8 / 62.3; turbo 4.9 / 7.3 / 10.3 / 10.5 / 62.2; kotoba-v2 7.0 / 8.4 / 11.4 / 8.6 / 27.6; anime-whisper 7.9 / 7.8 / 26.6 / 7.3 / 22.2. Weaker than turbo and kotoba on Reazon (TV-style speech, closest to drama).
- Game sets: 1.5B about level with anime-whisper (air 14.0 vs 14.6, clannad 11.3 vs 11.2, kanon 12.7 vs 12.6), far ahead of turbo (21.0 / 17.8 / ... ).
- Long form, beam 1, temp 0 (tedx-seq / tedx-prompt / book-seq / book-prompt, "prompt" = previous-text conditioning): 1.5B 11.7 / 13.2 / 14.7 / 12.4; large-v3 9.9 / 65.9 / 19.4 / 64.5; turbo 10.7 / 24.3 / 19.3 / 19.9. anime-whisper and kotoba collapse (43-99).
- No ASMR, whispered-speech, breath-only or non-speech hallucination test exists in any primary source.

## Decoding

- Baihe today: beam 5, Silero VAD 0.5 with 2000 ms min silence, `word_timestamps=True`, `condition_on_previous_text=False`, repeat guard off by default (`no_repeat_ngram_size=3`, `repetition_penalty=1.1`), `hallucination_silence_threshold` off (`core.py:305`, `:308`, `:314`, `:539-545`).
- WhisperJAV faster-whisper preset (conservative / balanced / aggressive): beam 2, best_of 2, temperature [0.0], compression 2.2 / 2.4 / 2.2, logprob -0.84 / -1.0 / -1.0, no_speech 0.54 / 0.65 / 0.72, repetition_penalty 1.8 / 1.5 / 1.5, `no_repeat_ngram_size=3`, reloads the model every 20 min of audio (stall workaround, #394). Whether the preset applies to whisper-ja is unverified (the file defaults to large-v2).
- The author's benchmark shows `no_repeat_ngram_size=5` and `<|notimestamps|>` barely change CER; beam 5 vs 1 differs by 0.3-0.6. Test arms: Baihe defaults, repeat guard on, beam 2.

## Baihe fit and code changes needed (not made)

- Cannot be used today: `WHISPER_MODELS` fixed dict (`whisper_models.py:16-21`); `stored_whisper_size` refuses other names on purpose (`services/transcribe_service.py:676-706`); Benchmark Lab refuses unknown sizes (`services/benchmark_lab_service.py:442-447`) and never passes `local_model_path` (`benchmark.py:95-97`). Only no-code route: Settings > Offline Whisper model folder (global, Workspace only).
- Changes: short key (e.g. `ja-1.5b`) in `WHISPER_MODELS` mapped to a pinned repo id inside `load_whisper_model` (never accept repo ids from clients); fix `is_whisper_model_cached`; `_MODEL_DOWNLOAD_SIZES` "~3.1 GB" (`services/transcribe_pipeline.py:75`); frontend picker (`TranscribeStage.tsx:61-64`, `transcribeEstimate.ts:13,21`, `sourceForm.ts:163`) offered only when the source language is ja; gate to `ja` in `services/transcribe_service.py` and the Lab; CLI picks it up from `WHISPER_MODELS` (`cli.py:1005`); optionally re-convert from efwkjn's current revision for the alignment-heads fix; if the 760M sibling is chosen, require ctranslate2 >= 4.7.1 (`constraints.txt` pins only `faster-whisper<2`).

## Benchmark protocol (after the changes)

1. Japanese cases with references tagged by type: 10+ anime/game clips, 10+ live-action drama clips, 10+ ASMR/whispered clips, 10+ non-speech clips (breath, SFX, music) with empty references scored by output characters, 2-3 long clips of 10+ min for loops.
2. Arms: large-v3-turbo (baseline), large-v3, ja-1.5b, ja-760m if added; GPU, `fast_mode` off; Lab defaults; re-run ja-1.5b with the repeat guard on.
3. Metrics: 1 - CER per content type; wall time per audio minute; peak VRAM; segments with a repeated 3-gram 4+ times; characters on the non-speech set; on 2 titles with known-good timings, median start-time error.
4. Pass: CER at least 2 points better than turbo on anime and ASMR; no worse than turbo +0.5 on drama; no more non-speech characters than turbo; no surviving loop; time at most 2.5x turbo; fits 8 GB fp16; median onset error no worse than turbo +100 ms.
5. Fail: worse than turbo on any content type; any loop in long clips; OOM on 8 GB; more non-speech hallucination than turbo.

## Open questions for the owner

1. Accept a model with no declared licence and training data of unclear terms as a user-downloaded option, or ask efwkjn for a licence first?
2. 1.5B (what WhisperJAV ships) or the author's own 760M CT2 build (smaller, turbo-speed, current alignment heads, needs ctranslate2 >= 4.7.1)?
3. Reference-transcribed ASMR clips? That tier decides the verdict.
4. TransWithAI conversion pinned to a revision, or re-convert from efwkjn's current revision?
