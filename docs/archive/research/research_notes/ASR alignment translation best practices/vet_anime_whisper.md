# Vetting: anime-whisper (litagin/anime-whisper) as a Japanese ASR model for Baihe

Vetted 2026-10-11 by a read-only model-vetter; saved by the lead session because the vetter has no write tool. Pages fetched 2026-10-11. `whisper_models.py` changed during the vet (exact-repo cache match, `_download_model` helper); line numbers are from the then-current file.

## Verdict

Use with limits: experimental, Japanese only, treat as non-commercial, never the default. Baihe cannot use it properly without code changes.

1. **Licence conflict.** The card says MIT, but its training dataset (Galgame_Speech_ASR_16kHz) is GPL-3.0 and says models trained on it "cannot be used for any commercial purposes" and "must be open-sourced".
2. **No official faster-whisper build.** Community CT2 conversions exist (`quantumcookie/anime-whisper-ct2-fp16` / `-int8`, others), all third-party and unverified; the oldest (`flyfront/anime-whisper-faster`) has no `tokenizer.json` and its author says it often misrecognizes speech.
3. **Baihe's decoding clashes with the card.** Baihe always sends `word_timestamps=True` (`core.py:543`) but the model has a 2-layer decoder while the converted config's alignment heads point at layers 7-25; Baihe passes `initial_prompt` when set (`core.py:546-547`) but the card says a prompt causes hallucinations and severely degrades quality.
4. **Gains are in-domain only.** Author's own test (visual-novel dialogue, same domain as training): CER 13.0 vs 16.5 for large-v3. Third-party long-form TEDx: 59.4 vs 9.9 (sequential). ReazonSpeech: 26.6 vs 14.1. Nothing published on ASMR.
5. The Benchmark Lab cannot run it today (only `WHISPER_MODELS` names; offline-folder path not passed).

## Claims

| Claim | Value | Status |
|---|---|---|
| Repo | `litagin/anime-whisper`, sha 22e2008a8182b357da3922a6308d095008f72973, 2024-11-24 | verified |
| Model licence | MIT (card metadata) | verified |
| Dataset terms | GPL-3.0; commercial use prohibited; trained models may not be used commercially and must be open-sourced; upstream OOPPEENN/Galgame_Dataset gated (401) | verified / upstream unverified |
| Training data | Galgame_Speech_ASR_16kHz, about 5,300 h, about 3.73M files | verified |
| Base | kotoba-tech/kotoba-whisper-v2.0 (Apache-2.0; ReazonSpeech CDLA-Sharing-1.0, Article 30-4 only) | verified |
| Size | 756,405,760 params (F32, about 3.03 GB); 32 encoder / 2 decoder layers | verified |
| Official CT2 build | none mentioned in card, README or eval | verified (absence) |
| Community CT2 | `flyfront/anime-whisper-faster` (fp16, no tokenizer.json); `quantumcookie/anime-whisper-ct2-fp16` (1.51 GB), `-int8` (0.77 GB), `quantumcookie/anime-whisper-ct2`, `Guilty00/anime-whisper-ct2` (fp32); `longisland3/anime-whisper-faster` (CC-BY-NC-4.0) | files verified; quality unverified |
| faster-whisper loads a repo id | yes (an id containing "/") | verified |
| Missing tokenizer.json | falls back to openai/whisper-tiny tokenizer (vocab 51865 vs this model's 51866) | verified; that it explains flyfront's poor results is inference |
| Alignment heads | `[[7,0],[10,17],...,[25,6]]` inherited from kotoba, past the 2-layer decoder; CT2 converter does not bounds-check | verified; effect on word_timestamps (crash or garbage) unverified |
| Card decoding | HF pipeline fp16, `chunk_length_s=30`, `language="Japanese"`, `no_repeat_ngram_size=0`, `repetition_penalty=1.0`; if it loops use n-gram 5-10 or penalty >1 | verified |
| Prompt | card: do not set an initial prompt | verified |
| Timestamps | card silent; Discussion #3: user got timestamps via whisper_timestamped but "a significant amount of hallucinations" | anecdotal |
| Output quirks | final 。 almost always dropped; single "…"; half-width digits and "!"; game-specific kanji for names; some slurs masked; transcribes laughs, breaths, screams | verified |
| Author benchmark (5 visual novels, about 75k files, CER) | anime-whisper 13.0; large-v3 16.5; turbo 16.9; kotoba-v2.0 18.8; reazonspeech-nemo-v2 23.6 | author's own, same domain |
| Third-party (efwkjn BENCH.md) | jsut 7.8 vs 7.2; ReazonSpeech 26.6 vs 14.1; fleurs 7.9 vs 4.7; TEDx sequential 59.4 vs 9.9; galgame sets about 10-18 vs turbo 18-48 | numbers verified; that row is this model and train/test overlap unverified |
| ASMR / noisy / silence hallucination | not covered by any primary source | unverified |
| VRAM (estimate) | fp16 about 2.9 GB upper bound (1.51 weights + 1.44 overhead); CPU int8 about 2.1 GB RAM; Baihe budget for turbo is 2600 MB / 2000 MB (`memory_headroom.py:39`) | estimate |

## Baihe fit and code changes needed (not made)

- Today only via Settings > Offline Whisper model folder (global, overrides every title and language), with `word_timestamps=True` and any initial prompt still sent; Benchmark Lab ignores it (`benchmark.py:95-97`, `services/benchmark_lab_service.py:445`).
- To register: key in `WHISPER_MODELS`, `TranscribeStage.tsx:61` list, `_MODEL_DOWNLOAD_SIZES` (`services/transcribe_pipeline.py:75`), `memory_headroom._WHISPER_MB`, cache check fix (`whisper_models.py:134`), restrict to `ja`, pin a revision (or convert from litagin sha 22e2008 after generating `tokenizer.json`), per-model decode overrides in `core.transcribe_for_timing` (never send `initial_prompt`; consider `no_repeat_ngram_size=5`, `chunk_length=15`; settle whether word timestamps work), CLI parity (`cli.py:1005`).
- Fit risks: dropped final 。 weakens punctuation-first line splitting; breaths and laughs come out as kana and pass the letter filter (`core.py:559`) so ASMR may produce extra lines; weak sequential long-form decoding matters because faster-whisper concatenates VAD speech into 30 s windows.

## Benchmark protocol (after the changes, or via the offline-folder workaround on a ja-only title with the prompt empty)

1. Japanese cases with references: (a) 3+ anime/drama clips of 1-3 min; (b) 3+ ASMR clips (whispering, breaths, long silences); (c) one 20+ min file with music and silences; (d) one clean narration control.
2. Configs: large-v3-turbo, large-v3, anime-whisper fp16 GPU; once more on CPU int8 for speed and RAM.
3. Beam 5, default VAD, no initial prompt; repeat guard off, then `no_repeat_ngram_size=5`.
4. Metrics: 1 - CER and duration; by hand per file: hallucinated lines in silent or breath-only stretches, loops (4+ repeated segments), bad word timings, median start offset on 20 lines.
5. Pass: CER at least 2 points better than turbo on (a) and (b); no more than 1 point worse on (c) and (d); no more silence/breath hallucinations; no crash with word timestamps on; median timing offset within 0.3 s of turbo; speed within 1.5x of turbo.
6. Fail: word-timestamp crash or garbage; long-form CER more than 1 point worse; more ASMR silence hallucinations; tokenizer/special-token garbage (English output, translate task).

## Open questions for the owner

1. Is any Baihe output used commercially? (Dataset terms forbid commercial use of models trained on it.)
2. Trust a third-party CT2 conversion (pinned sha) or convert our own from litagin's sha 22e2008?
3. If word timestamps prove broken, is timing from VAD spans or the Qwen3 aligner acceptable for a ja-only model?
4. Test whisper-ja-1.5B or kotoba-whisper-v2.0-faster first?
