# Vetting: kotoba-whisper v2.x as a Japanese ASR model for Baihe

Vetted 2026-10-11 by a read-only model-vetter; saved by the lead session because the vetter has no write tool. All claims fetched 2026-10-11 from primary sources unless marked.

## Verdict

Reject the stock faster-whisper build. Benchmark a patched local copy only if wanted, for Japanese titles only.

1. **The shipped faster-whisper build crashes when word timestamps are on, and Baihe always turns them on.**
   - The build's `config.json` keeps large-v3's `alignment_heads` (decoder layers 7 to 25) but the decoder has 2 layers.
   - CTranslate2 writes `_alignment_heads[layer]` with no bounds check (`src/layers/transformer.cc`, `src/models/whisper.cc`).
   - Open since 2024-12-04 as kotoba-tech/kotoba-whisper issue #2, no maintainer reply: access violation (0xC0000005) with `word_timestamps=True`.
   - Baihe hard-codes `word_timestamps: True` at `core.py:543` and `mixed_language.py:128`; line tightening, `hallucination_silence_threshold` and pause splitting depend on word timings.
2. **No evidence it beats what Baihe has.** Vendor table: better than large-v3 only in-domain (ReazonSpeech 11.6 vs 14.9 CER), worse on CommonVoice 8 (9.2 vs 8.5) and JSUT (8.4 vs 7.1). Baihe's own tests (`docs/asr-experiments.md:122`, `:236`) found turbo about equal to large-v3 on Japanese, and Japanese titles already default to `qwen3_asr_long` when installed (5.38 whole-clip CER vs turbo 6.61 in that doc). No primary source covers anime, ASMR, whispered or noisy speech.
3. Japanese only.

## Claims

| Claim | Value | Verified |
|---|---|---|
| Repos | `kotoba-tech/kotoba-whisper-v2.0` (transformers), `-v2.1`, `-v2.2`; no later; newest created 2024-10-18 | yes (HF API) |
| CTranslate2 build | only `kotoba-tech/kotoba-whisper-v2.0-faster` (2024-09-29); none for v2.1/v2.2 | yes |
| Licence | Apache-2.0 (v2.x); the `-faster` card says MIT (mismatch, effect unverified) | card metadata |
| Training data | ReazonSpeech (CDLA-Sharing-1.0, "SOLELY FOR ... JAPANESE COPYRIGHT ACT ARTICLE 30-4"); effect on weights unverified | yes / legal effect no |
| Size | 756M params; encoder 32 layers, decoder 2 layers; turbo has 4 decoder layers | yes |
| Download | `model.bin` about 1.51 GB fp16 | yes |
| CER normalised (CV8 / JSUT / Reazon) | v2.0 9.2 / 8.4 / 11.6; large-v3 8.5 / 7.1 / 14.9; medium 11.5 / 10 / 33.2 | yes (card); GitHub copy lists large-v3 15.1 on Reazon |
| Speed | vendor: 6.3x faster than large-v3 (HF pipeline); in faster-whisper on an M2 Mac it was the slowest of three runtimes; no CUDA numbers | vendor claim |
| Vendor decoding | `language="ja"`, `chunk_length=15`, `condition_on_previous_text=False` | yes |
| Punctuation | v2.1/v2.2 add `punctuators` post-processing (transformers pipeline only), v2.2 adds diarization; none in the CT2 build | yes |
| Anime / ASMR / hallucination / initial_prompt | nothing in cards, README or issues | unverified (no evidence) |
| VRAM | estimate fp16 about 2.4-2.6 GB, int8 CPU about 1.6-1.9 GB RAM (scaled from `memory_headroom.py:39`) | estimate |

## Baihe fit (from the code)

- `WHISPER_MODELS` is a fixed dict (`whisper_models.py:16-21`); `stored_whisper_size` rejects any other name on purpose (`services/transcribe_service.py:676-706`).
- It can run today only through Settings > Offline Whisper model folder (`whisper_model_path`, `whisper_models.py:249`), which is global (replaces Whisper for every title and language) and would crash on the stock config.
- Benchmark Lab cannot test it: `check_config` allows only `WHISPER_MODELS` names (`services/benchmark_lab_service.py:442-447`) and `benchmark.run_transcription_case` ignores `local_model_path` (`benchmark.py:95-97`).
- To offer it in the picker: entry in `whisper_models.py:16`, `_MODEL_DOWNLOAD_SIZES` (`services/transcribe_pipeline.py:75`), `memory_headroom._WHISPER_MB`, `is_whisper_model_cached`, restrict to `ja`, and either patch `alignment_heads` after download (fragile) or turn word timestamps off for this model.

## Benchmark protocol (needs benchmark local-path plumbing, or a manual run)

1. Download `-faster`; set `alignment_heads` in its `config.json` to `[[1,0],[1,1],...,[1,19]]` (community workaround from issue #2, unconfirmed); point Settings > Offline Whisper model folder at it; clear afterwards.
2. Crash check: one 30 s Japanese clip, GPU on and off; pass = no crash, `words` present.
3. Sets: (a) clean read speech (60 FLEURS ja), (b) 3 min drama/stream clip, (c) 10+ min anime with music/SFX, (d) 10+ min ASMR or whispered audio with long silences.
4. Configs: large-v3-turbo, large-v3, kotoba; defaults otherwise; repeat once with a name-list `initial_prompt`; also `qwen3_asr_long` if installed.
5. Metrics: 1 - CER, RTF, peak VRAM, hallucinated lines in silences of (d) by hand, repeat loops, line timing error against 20 hand-marked lines of (b), prompt names.
6. Pass: CER at least 1 point better than turbo on (b) and (c) with no more than 0.5 regression on (a); no more silence hallucinations than turbo; timing and RTF no worse.
7. Fail: any crash, missing word timings, visibly worse timing, more silence hallucinations, or no gain over turbo (or over Qwen3-ASR where installed).

## Open questions for the owner

1. Is Qwen3-ASR installed on the PC? Then kotoba must beat `qwen3_asr_long`, not just turbo.
2. Short anime and ASMR clips with hand-checked Japanese transcripts (no primary source covers these domains).
3. Comfort with the ReazonSpeech "Article 30-4 only" data terms and the MIT/Apache mismatch (not verified legally).
4. If it passes, accept a Japanese-only entry that needs a patched config or word timestamps off?
