# Streaming and low-latency ASR for zh/ja/ko/en (as of October 2026)

Research date: 2026-10-10. Every number below names its source. "Measured (third party)" means someone other than the model's maker ran it. "Vendor/author" means the maker's own model card, README or paper. Hardware is given where the source states it. Where it isn't stated, these notes say so.

## Whisper-family streaming approaches (whisper_streaming, SimulStreaming, WhisperLiveKit, whisper.cpp, faster-whisper, distil/turbo): latency, accuracy, failure modes

### Takeaway
SimulStreaming (AlignAtt policy over Whisper large-v3, MIT) is now the reference Whisper streaming policy. Its own authors have put the older whisper_streaming (LocalAgreement-2, about 3.3 s latency) into maintenance. WhisperLiveKit wraps SimulStreaming plus non-Whisper backends (Qwen3-ASR, Voxtral, Canary, SenseVoice) behind one server. For fast offline passes, faster-whisper's batched pipeline is about 4x faster than its sequential mode on an 8 GB consumer GPU. Whisper's main failure for drama audio is hallucinated text on non-speech: large-v3 emits text for about 40% of non-speech inputs. VAD gating is therefore mandatory.

### Cited Findings
- **whisper_streaming (ufal):** uses LocalAgreement-n with n=2, so a prefix is committed once two consecutive updates on growing audio agree. Its paper abstract reports "3.3 seconds latency on unsegmented long-form speech". The README gives no hardware for that figure and recommends faster-whisper on GPU. Buffer trimming is "segment" (default) or "sentence", with `--buffer_trimming_sec`. Optional `--vac` (Silero voice-activity controller) is recommended. The README says naive fixed chunks "can split a word in the middle". It also says the project "is becoming outdated" in 2025, replaced by SimulStreaming, "much faster and higher quality". MIT licence. — [ufal/whisper_streaming README](https://github.com/ufal/whisper_streaming)
- **SimulStreaming (ufal, IWSLT 2025):** merges Simul-Whisper and Whisper-Streaming. The AlignAtt policy uses encoder-decoder cross-attention and stops decoding when attention reaches the last `--frame_threshold` frames of the buffer (1 frame = 0.02 s for large-v3), then resumes on the next chunk. Author claims: "~5-times faster" than WhisperStreaming, and the best system in the IWSLT 2025 Simultaneous track. Recommends at least 10 GB VRAM for large-v3 and says CPU is "too slow for real time". MIT licence. Output is JSONL partials with `is_final: true` at the end of a voiced segment. The translation side has a confirmed `text` field, which only grows, and an `unconfirmed_text` field, which may change. `--vac` Silero gating needs torchaudio. The README gives no latency numbers. — [ufal/SimulStreaming README](https://github.com/ufal/SimulStreaming)
- **CUNI IWSLT 2025 system paper:** Whisper large-v3 with AlignAtt for ASR. En→{De,Zh,Ja} is transcription followed by EuroLLM-9B-Instruct with LocalAgreement, in the 4–5 s latency regime. Cs→En targets the 2 s and 4 s regimes. Latency is validated computation-unaware, so compute time is excluded. The authors report +2 BLEU (Cs-En) and +13–22 BLEU (En→De/Zh/Ja) over the organisers' baseline on dev sets. They also propose Continuous Levenshtein Alignment for measuring ASR latency. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
- **CUNI IWSLT 2026 (June 2026):** moved the SimulStreaming backbone from Whisper to Canary-1B-v2 with AlignAtt plus Silero VAD. LongYAAL, computation-unaware: about 1.7–2.0 s in the low regime and 3.3–3.8 s in the high regime (En→De low 1677 ms, high 3761 ms). Cs→En BLEU with Canary is 32.01 (high) versus 24.20 for SimulStreaming-Whisper. Failure modes the authors name for offline models used simultaneously:
  - hallucination on partial source sentences
  - no left-only context
  - re-encoding the whole buffer on every chunk
  - stalling (no output) when both a forced prefix and context are injected

  Silero VAD is used "to avoid hallucinations". No hardware is stated. Canary covers only European languages (see below), so this exact setup does not cover zh/ja/ko. — [CUNI IWSLT 2026, arXiv 2606.03948](https://www.alphaxiv.org/abs/2606.03948.md)
- **WhisperLiveKit (QuentinFuxa, Apache-2.0, about 11.1k stars):** default policy is SimulStreaming/AlignAtt, with LocalAgreement as the alternative. Backends:
  - faster-whisper, mlx-whisper, vanilla whisper, openai-api
  - `funasr` (SenseVoiceSmall: zh/yue/en/ja/ko)
  - `voxtral` / `voxtral-mlx`
  - `qwen3-vllm`, `qwen3-streaming`
  - `canary` (NeMo, 25 European languages)

  Diarization is Sortformer (default, max 4 speakers, one speaker per token) or diart (marked not recommended). VAD/VAC on is the default, and turning it off is flagged "NOT ADVISED". A stable transcript boundary is forced after a 5.0 s pause by default. The README says Whisper `auto` language detection "biases toward English". Qwen3-streaming word timestamps are estimates to about 1 s, and its causal mode is English-only. It suggests one real-time Qwen3 session per GPU. Built-in translation is NLLW (distilled NLLB 600M/1.3B, about 200 languages). The README publishes no latency figures; its benchmarks are on an H100, with other hardware results requested. — [WhisperLiveKit README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **faster-whisper offline speed (vendor README, measured by SYSTRAN):**
  - Large-v2, beam 5, RTX 3070 Ti 8 GB, CUDA 12.4, on the README's 13-minute benchmark file:

    | Implementation | Precision | Time | VRAM |
    |---|---|---|---|
    | openai/whisper | fp16 | 2m23s | 4708 MB |
    | whisper.cpp + flash attention | fp16 | 1m05s | 4127 MB |
    | faster-whisper | fp16 | 1m03s | 4525 MB |
    | faster-whisper, batch_size=8 | fp16 | 17s | 6090 MB |
    | faster-whisper | int8 | 59s | 2926 MB |
    | faster-whisper, batch_size=8 | int8 | 16s | 4500 MB |

  - Small model, i7-12700K, 8 threads:

    | Implementation | Precision | Time | RAM |
    |---|---|---|---|
    | whisper.cpp | fp32 | 2m05s | 1049 MB |
    | faster-whisper | int8 | 1m42s | 1477 MB |
    | faster-whisper, batch_size=8 | int8 | 51s | 3608 MB |

  - distil-large-v3: YT Commons WER 13.527 (faster-whisper) versus 14.801 (transformers).
  - Batched mode turns on Silero VAD by default. Otherwise the VAD filter is off and only removes silences longer than 2 s, unless `min_silence_duration_ms` is tuned (the example uses 500). MIT licence. — [SYSTRAN/faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- **Whisper accuracy on Japanese, measured (third party, 2026-09-01, one L40S 46 GB, FLEURS ja test, 650 clips / 8511 s):**

  | Model | Normalised CER | RTF | Peak VRAM (batch 64) |
  |---|---|---|---|
  | Whisper large-v3 | 4.49% (raw 7.51%) | 0.0077 | 20.1 GB |
  | large-v3-turbo | 4.81% | 0.0048 | 11.1 GB |
  | Kotoba-Whisper v2.2 (Japanese distil) | 6.92% | 0.0022 | 6.5 GB |

  These VRAM figures are at large batch, not single-stream. — [Classmethod, L40S Japanese ASR benchmark](https://dev.classmethod.jp/articles/japanese-asr-l40s-fleurs-benchmark/)
- **Whisper large-v3 on FLEURS, from the Voxtral Realtime paper (Mistral, Feb 2026, so a competitor-reported number):** en WER 4.00, ja CER 4.97, ko WER 14.30, zh CER 7.94. — [Voxtral Realtime, arXiv 2602.11298](https://arxiv.org/html/2602.11298v2)
- **Hallucination on non-speech:** Whisper large-v3 produces non-empty output for 40.3% of non-speech inputs. Typical outputs are repeated acknowledgements, subtitle-credit phrases, fillers, applause markers and onomatopoeia, and the rate varies with input duration. — [Barański et al. 2025, arXiv 2501.11378](https://arxiv.org/pdf/2501.11378). Calm-Whisper (Interspeech 2025) found 3 of 20 decoder heads cause more than 75% of non-speech hallucinations on UrbanSound. Fine-tuning those heads cut non-speech hallucination by more than 80% for less than 0.1% WER loss on LibriSpeech. — [Calm-Whisper, arXiv 2505.12969](https://arxiv.org/pdf/2505.12969)

### Inferences
- For live use, SimulStreaming (directly or via WhisperLiveKit) supersedes whisper_streaming. Expect roughly 2–4 s commit latency for Whisper-based streaming. That comes from the IWSLT regimes and the 3.3 s LocalAgreement figure; both are computation-unaware or have no stated hardware, so real end-to-end latency on a consumer GPU will be higher.
- The 40% non-speech hallucination rate means drama audio (music, SFX) needs VAD gating before Whisper, `condition_on_previous_text=False`, and a post-filter for stock phrases (subtitle credits, "thank you for watching").
- large-v3-turbo is a good speed/accuracy point for Japanese: about 0.3 CER points worse than large-v3 at roughly 1.6x the speed and about half the VRAM, on FLEURS on an L40S.

### Gaps
- No primary-source latency numbers were found for whisper.cpp's `stream` example, insanely-fast-whisper, or distil-whisper on zh/ja/ko.
- No measured time-to-first-partial for SimulStreaming or WhisperLiveKit on consumer GPUs (RTX 3060–4090). Both READMEs give none.
- Whisper language flips and repetition loops on CJK in streaming mode are widely reported anecdotally. These notes cite no quantified source for them beyond the WhisperLiveKit "auto biases toward English" note.

## Newer models: Qwen3-ASR, Parakeet/Canary, Kyutai, Moonshine, Voxtral, Japanese-, Chinese- and Korean-specific models

### Takeaway
Only three open models found cover zh, ja and ko in one model with streaming:
- Qwen3-ASR (Apache-2.0, 0.6B/1.7B, streaming through vLLM only)
- Voxtral Mini 4B Realtime (Apache-2.0, configurable 240–2400 ms delay)
- SenseVoiceSmall (non-autoregressive and very fast, but not natively streaming; usable through LocalAgreement in WhisperLiveKit)

NVIDIA's multilingual Canary-1B-v2 and Parakeet-TDT-0.6B-v3 cover European languages only, though a separate Japanese Parakeet exists. Kyutai STT is English/French only. For Japanese, Whisper large-v3 and turbo still lead on FLEURS among the models measured on one GPU. A Japanese Parakeet is about 12x faster than large-v3 at about 1.5 CER points worse.

### Cited Findings
- **Qwen3-ASR-1.7B / 0.6B (Alibaba Qwen, paper 29 Jan 2026, Apache-2.0):**
  - Coverage: 30 languages plus 22 Chinese dialects, including zh, en, ja, ko, Cantonese, Wu and Minnan.
  - Streaming and offline use the same model. Streaming is only through the vLLM backend, with no batching and no timestamps in streaming.
  - Vendor numbers (the card labels Chinese results as WER):

    | Benchmark | 1.7B score |
    |---|---|
    | AISHELL-2 test | 2.71 |
    | WenetSpeech net / meeting | 4.97 / 5.88 |
    | SpeechIO | 2.88 |
    | Fleurs-zh | 2.41 |
    | Fleurs-yue | 3.98 |
    | LibriSpeech clean / other | 1.63 / 3.38 |
    | LibriSpeech clean / other, streaming | 1.95 / 4.51 |
    | Average, streaming vs offline | 3.33 vs 2.69 |

  - The model card gives no ja/ko figures in text.
  - Open ASR Leaderboard RTFx is 147.93 for 1.7B, hardware unstated. The vLLM example uses `gpu_memory_utilization=0.7` and recommends FlashAttention 2; no VRAM figure is given. — [Qwen3-ASR-1.7B model card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
  - Vendor abstract: Qwen3-ASR-0.6B has "average TTFT as low as 92ms" and handles 2000 s of audio per second at concurrency 128. Hardware is not stated in the abstract. — [arXiv 2601.21337](https://arxiv.org/abs/2601.21337)
  - Third party (Baseten): 1.7B stays real-time at up to 64 concurrent streams on RTX 6000 or H100, with P95 finalisation latency under 0.5 s. This comes from a search snippet; the page was not fetched. — [Baseten Qwen3-ASR page](https://www.baseten.co/library/qwen3-asr-1-7b/)
- **Qwen3-ForcedAligner-0.6B:** non-autoregressive, 11 languages including zh, en, yue, ja and ko, and handles up to 5 minutes of audio. Vendor numbers for mean absolute alignment shift: 42.9 ms on MFA-labelled data versus 129.8 ms for NeMo Forced Aligner and 133.2 ms for WhisperX; 27.8 ms on human-labelled data versus 101.2 ms for NFA. — [Qwen3-ASR model card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- **Qwen-Audio-3.0-ASR (arXiv 2609.07549, Sept 2026):** a separate MoE LLM-ASR covering 30 languages and 16 Chinese dialect varieties, with a "-Streaming" variant. The abstract page gives no numbers and no open-weights licence. A search-engine summary attributed streaming figures to it: 400 ms algorithmic latency, zh CER 9.24% streaming versus 8.03% offline on internal sets, first token under 200 ms. These could not be confirmed on the abstract page, so treat them as unverified. Open weights are also unconfirmed. — [arXiv 2609.07549](https://arxiv.org/abs/2609.07549)
- **Voxtral Mini 4B Realtime (Mistral, Feb 2026, Apache-2.0, about 4.4B parameters):** one model runs at any delay that is a multiple of 80 ms, trained on 80–2400 ms. It emits one token per 80 ms frame, with padding tokens while waiting, and streams token deltas over a WebSocket. It covers 13 languages including zh, ja and ko. Author-reported FLEURS (zh and ja are CER):

  | Delay | en WER | ja CER | ko WER | zh CER |
  |---|---|---|---|---|
  | 240 ms | 5.91 | 15.17 | 17.56 | 13.84 |
  | 480 ms | 4.90 | 9.59 | 15.74 | 10.45 |
  | 960 ms | 4.34 | 6.80 | 14.90 | 8.99 |
  | 2400 ms | 4.05 | 5.50 | 14.30 | 8.48 |

  The same paper reports offline Whisper at ja 4.97, ko 14.30, zh 7.94. At 960 ms and above, Voxtral Realtime beats the commercial Scribe v2 Realtime (zh 16.82, ko 11.90) on zh, and GPT-4o-mini-transcribe (zh 15.43, ko 19.46) on zh and ko. The paper does not state hardware or VRAM. — [Voxtral Realtime, arXiv 2602.11298](https://arxiv.org/html/2602.11298v2); delay "configurable down to sub-200ms" and a roughly 2.4 s setting suited to subtitling, per [Mistral announcement](https://mistral.ai/news/voxtral-transcribe-2)
- **NVIDIA Canary-1B-v2 and Parakeet-TDT-0.6B-v3:** both cover 25 European languages only, so no zh/ja/ko. — [arXiv 2509.14128](https://arxiv.org/pdf/2509.14128); WhisperLiveKit also lists "Canary: 25 European languages" ([README](https://github.com/QuentinFuxa/WhisperLiveKit)). Canary's licence is CC-BY-4.0 (same README).
- **Japanese models, measured (third party, 2026-09-01, one L40S, FLEURS ja):**

  | Model | Normalised CER | RTF | Peak VRAM |
  |---|---|---|---|
  | Parakeet TDT-CTC 0.6B ja (CC-BY-4.0) | 5.94% | 0.0004 (about 2482 audio-hours per hour) | 5.5 GB |
  | ReazonSpeech K2 v2 (Apache-2.0) | 9.40% | 0.0032 | 22.1 GB |
  | NVIDIA Nemotron 3.5 ASR offline (OpenMDW-1.1) | 13.31% | 0.0011 | 18.0 GB |
  | Nemotron 3.5 ASR streaming (1120 ms cache-aware chunks) | 13.46% | not reported | 7.1 GB |

  For Nemotron streaming, offline was 1.64x faster than streaming. The author notes Nemotron's training data includes FLEURS, and that FLEURS is clean read speech, unlike drama. First-token latency was not measured. — [Classmethod](https://dev.classmethod.jp/articles/japanese-asr-l40s-fleurs-benchmark/)
  - Earlier distilled ReazonSpeech/Kotoba-Whisper work: the distil model reached CER 9.2 / 8.4 / 11.6 on CommonVoice8 / JSUT / ReazonSpeech-test, versus large-v3 at 8.5 / 7.1 / 14.9. This is from a search summary of the Kotoba repo and was not opened directly. — [japanese-asr README space](https://japanese-asr-readme.static.hf.space/)
- **Kyutai STT (delayed streams modelling):**

  | Model | Languages | Parameters | Delay |
  |---|---|---|---|
  | stt-1b-en_fr | English, French | about 1B | 500 ms, with a semantic VAD that is in the Rust server only |
  | stt-2.6b-en | English only | about 2.6B | 2.5 s |

  No CJK. — [Kyutai STT](https://kyutai.org/stt), [HF model card](https://huggingface.co/kyutai/stt-1b-en_fr)
- **SenseVoiceSmall (FunAudioLLM):** zh, yue, en, ja, ko, plus a "nospeech" option. Vendor claim: 70 ms for 10 s of audio, "15x faster than Whisper-Large", hardware unstated. It also tags audio events (BGM, applause, laughter, crying, coughing). Its benchmark tables are images only. It has a custom "model-license" and no native streaming; the example uses fsmn-vad to segment, with a 30 s maximum. — [SenseVoiceSmall card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall). WhisperLiveKit runs it through LocalAgreement only ([README](https://github.com/QuentinFuxa/WhisperLiveKit)).
- **FunASR Paraformer (Chinese):**
  - Offline Paraformer-large reports CER 1.95 (AISHELL-1 test), 2.85 (AISHELL-2 ios) and 6.97 (WenetSpeech meeting). — [FunASR paper, arXiv 2305.11013](https://arxiv.org/pdf/2305.11013)
  - A third-party 2026 paper lists streaming Paraformer on AISHELL-1 at about 3.06 CER and about 585 ms mean latency. The extracted table was garbled, so these figures are tentative. — [X2Streaming-ASR, arXiv 2609.08672](https://arxiv.org/pdf/2609.08672)
- **Korean:** no 2025–26 open-weight Korean-specific streaming model with published CER was found. The only Korean multi-model number found is FLEURS-ko via the Voxtral paper: Whisper 14.30, Voxtral Realtime 14.30–17.56, Scribe 11.90. Older baseline: KoSpeech (2020) at 10.31% CER on KsponSpeech, non-streaming. KsponSpeech is under KOGL Type 4, which works like CC BY-NC-ND. — [KoSpeech, arXiv 2009.03092](https://arxiv.org/pdf/2009.03092)
- **Microsoft Research:** benchmarked more than 50 CPU configurations in batch, chunked and streaming modes, including Qwen3-ASR. It found NVIDIA Nemotron Speech Streaming the strongest for real-time English on constrained hardware. This is from a search snippet only. — [Microsoft Research](https://www.microsoft.com/en-us/research/?p=1169047)

### Inferences
- **Best fit for a CJK live pipeline, ranked:**
  1. Qwen3-ASR-1.7B through vLLM. Best published zh numbers, sub-0.5 s finalisation in third-party serving, one forced aligner for timing.
  2. Voxtral Realtime at 960–2400 ms delay. A single tunable model; ja and zh approach Whisper at 2.4 s.
  3. SenseVoiceSmall for cheap, fast zh/ja/ko. It also flags BGM, which suits drama audio.
- Ja/ko quality for Qwen3-ASR is not published in text. It should be checked on our own drama clips before switching.
- For Japanese offline speed, Parakeet TDT-CTC 0.6B ja is the throughput outlier: about 19x faster than large-v3 on the same GPU, for about 1.5 CER points (FLEURS).
- Korean is the weakest language across all open options (FLEURS-ko about 14% WER even for Whisper).

### Gaps
- **Moonshine:** no 2026 data was retrieved on CJK coverage or streaming latency (it was historically English-focused, from background knowledge and unverified).
- **Qwen3-ASR:** ja/ko accuracy, and streaming chunk size or partial/final semantics.
- **Hardware:** for Voxtral Realtime and the Qwen3 TTFT figure.
- **Korean-specific open models:** for example Whisper fine-tunes and ETRI models; none with verified numbers.
- **Out-of-domain measurements:** all accuracy numbers are on read or broadcast benchmarks (FLEURS, AISHELL, LibriSpeech). None found on drama audio with background music.

## VAD and chunking: Silero, TEN, WebRTC VAD; chunk size, endpointing, avoiding hallucination on music and silence

### Takeaway
Every serious streaming stack gates the ASR with Silero VAD:
- SimulStreaming and whisper_streaming via `--vac`
- WhisperLiveKit (on by default)
- the faster-whisper batched pipeline
- CUNI 2026, to avoid hallucinations

TEN VAD claims lower latency at speech-to-silence transitions and a much smaller footprint than Silero. That claim is vendor-only. Chunking parameters in practice:
- about 1 s minimum chunk
- a 500 ms minimum silence when tuned (versus faster-whisper's conservative 2 s default)
- a forced segment boundary after a 5 s pause in WhisperLiveKit
- AlignAtt's attention-frame threshold, which keeps words at the buffer edge from being committed

### Cited Findings
- Silero-based VAC skips unvoiced audio and is recommended. Removing torchaudio disables it. — [SimulStreaming](https://github.com/ufal/SimulStreaming), [whisper_streaming](https://github.com/ufal/whisper_streaming)
- whisper_streaming example `--min-chunk-size 1` (seconds). The README warns that fixed chunks without a policy split words. — [whisper_streaming](https://github.com/ufal/whisper_streaming)
- faster-whisper VAD default removes only silences longer than 2 s. The example tunes `min_silence_duration_ms=500`. — [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- WhisperLiveKit: VAD on by default ("disabling NOT ADVISED"). Pause-based segmentation commits at pauses over 5.0 s (default). `--never-fire` keeps incomplete words from being truncated. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- TEN VAD (vendor README):
  - claims better precision than WebRTC VAD and Silero, from PR curves on LibriSpeech, GigaSpeech and DNS-Challenge-derived annotated data
  - says Silero has "a delay of several hundred milliseconds" at speech-to-non-speech transitions and misses short gaps between speech segments
  - library is about 306 KB, versus about 2.2 MB for Silero
  - CPU RTF about 0.009–0.016
  - the default threshold of 0.5 should be tuned per domain

  No independent benchmark was found. — [TEN VAD](https://github.com/TEN-framework/ten-vad)
- SenseVoice pipeline example: fsmn-vad with `max_single_segment_time=30000` ms, `merge_vad=True`, `merge_length_s=15`. — [SenseVoiceSmall card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)
- The Kyutai 1B model ships a semantic VAD (end-of-turn), only in its Rust server. — [Kyutai STT](https://kyutai.org/stt)
- Hallucination mitigations with evidence:
  - VAD gating (CUNI 2026)
  - head fine-tuning for Whisper: more than 80% fewer non-speech hallucinations ([Calm-Whisper](https://arxiv.org/pdf/2505.12969))
  - knowing the typical hallucination strings (subtitle credits, acknowledgements, applause) so they can be post-filtered ([arXiv 2501.11378](https://arxiv.org/pdf/2501.11378))
  - a 2026 method, "Hallucination Space Projection", which was found in search but not read — [arXiv 2609.04561](https://arxiv.org/pdf/2609.04561)

### Inferences
- **For drama audio:**
  - Use Silero (or trial TEN) VAD with a lowered threshold tuned on music-heavy clips.
  - Use a minimum silence of about 300–500 ms for endpointing.
  - Use SenseVoice's BGM event tag, or a "nospeech" check, as a second gate against music-only segments before Whisper decodes them.
  - Use a phrase blacklist post-filter.
- VAD alone does not stop hallucination on music. Speech-like music passes VAD, so model-level gating or the second gate above is needed.

### Gaps
- No quantified head-to-head of Silero, TEN and WebRTC VAD on music or SFX-heavy audio.
- No published study of the chunk-size versus word-cut-rate trade-off for CJK.
- WebRTC VAD numbers were not retrieved.

## Practical numbers: end-to-end latency on consumer GPU and CPU; VRAM budget when ASR and a local LLM share a card

### Takeaway
No source gave a measured end-to-end (mic to committed subtitle) latency on an RTX 3060–4090. Best available proxies:
- **Computation-unaware algorithmic latency:** about 2 s (low) or 3.5 s (high) for AlignAtt, about 3.3 s for LocalAgreement-2, and 0.24–2.4 s configurable for Voxtral Realtime.
- **Datacenter serving:** under 0.5 s P95 finalisation for Qwen3-ASR (Baseten, RTX 6000 / H100).
- **VRAM at batch 1:** Whisper large-v2 fits in about 3–4.5 GB (int8 or fp16, single stream). SimulStreaming recommends at least 10 GB for large-v3. A 1.7B Qwen3-ASR under vLLM takes whatever fraction you assign it.

### Cited Findings
- faster-whisper large-v2 on an RTX 3070 Ti 8 GB, 13-minute file, single stream: fp16 63 s at 4.5 GB, int8 59 s at 2.9 GB. Batched (batch 8): fp16 17 s at 6.1 GB, int8 16 s at 4.5 GB. — [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- CPU, i7-12700K, 8 threads, small model: whisper.cpp 2m05s at 1.0 GB, faster-whisper int8 1m42s, batched int8 51s at 3.6 GB. These are offline passes; real-time large-v3 on CPU is "too slow" per SimulStreaming. — [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [SimulStreaming](https://github.com/ufal/SimulStreaming)
- SimulStreaming targets one or two GPUs: Whisper large-v3 plus EuroLLM-9B, with at least 10 GB recommended for large-v3. — [SimulStreaming](https://github.com/ufal/SimulStreaming)
- WhisperLiveKit suggests one real-time Qwen3-streaming session per GPU, and says heavy extras conflict and need separate environments. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- Qwen3-ASR vLLM example: `gpu_memory_utilization=0.7`, so the rest of the card can be left for other work. — [Qwen3-ASR card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- Large-batch peak VRAM on an L40S for Japanese:

  | Model | Peak VRAM | Batch |
  |---|---|---|
  | large-v3 | 20 GB | 64 |
  | turbo | 11 GB | 64 |
  | Kotoba-Whisper v2.2 | 6.5 GB | 64 |
  | Parakeet ja | 5.5 GB | 32 |

  These are upper bounds, not single-stream needs. — [Classmethod](https://dev.classmethod.jp/articles/japanese-asr-l40s-fleurs-benchmark/)

### Inferences
- **On a 12 GB card (RTX 3060)** running ASR plus a local LLM: an int8 Whisper turbo or large-v3 (about 3 GB), or SenseVoiceSmall or Parakeet-ja (small), leaves room for a 7–8B LLM at 4-bit. Qwen3-ASR under vLLM needs `gpu_memory_utilization` capped (for example 0.3–0.4) so it shares the card.
- **On a 24 GB card (4090)**, SimulStreaming large-v3 plus a 7–9B LLM fits comfortably, matching ufal's own one-to-two-GPU target.
- The latency ranges above are inferred from the computation-unaware figures, not measured.

### Gaps
- No measured end-to-end latency on RTX 3060, 3090 or 4090 for any streaming stack.
- No measured CPU-only real-time streaming for CJK.
- No measured VRAM for Voxtral Realtime 4B or Qwen3-ASR at batch 1.

## Accuracy levers: prompting/glossary biasing, language hints, diarization cost, forced alignment

### Takeaway
Pin the language, since Whisper `auto` biases toward English. Use forced alignment for subtitle timing: Qwen3-ForcedAligner reports about 3x lower timestamp shift than WhisperX and NFA across zh/ja/ko/en. Glossary biasing through prompts exists but has no quantified 2026 evidence found here. Diarization in WhisperLiveKit uses Sortformer, capped at 4 speakers, with one speaker per token.

### Cited Findings
- Whisper `auto` language "biases toward English". Voxtral auto-detects per chunk. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- whisper_streaming / SimulStreaming accept prompts. The IWSLT 2025 Cs→En system used beam search plus prompts. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
- CUNI 2026: injecting both a forced prefix and context made Canary stall and produce no output. Prompt and context levers can backfire on models not trained for them. — [arXiv 2606.03948](https://www.alphaxiv.org/abs/2606.03948.md)
- Qwen3-ForcedAligner-0.6B: mean shift of 42.9 ms (MFA-labelled) or 27.8 ms (human-labelled), versus about 100–133 ms for NFA and WhisperX. 11 languages including zh, ja, ko and yue. Not available during Qwen3 streaming, which has no timestamps there. — [Qwen3-ASR card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- Qwen3-streaming word timestamps in WhisperLiveKit are only about 1 s precise. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- Diarization: Sortformer (default, at most 4 speakers, no overlapping-speech output); diart is not recommended. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)

### Inferences
- **Timing for subtitles:** do a two-pass design. Stream partials for display, then re-time committed segments with a forced aligner (Qwen3-ForcedAligner for CJK) before export.
- **Glossary and character names:** the safest lever is post-ASR correction against the glossary, or the LLM translation step. Prompt biasing can stall or destabilise streaming models (CUNI 2026).

### Gaps
- No quantified results for Whisper `initial_prompt` or hotword biasing on CJK proper nouns.
- No measured diarization latency or VRAM cost (Sortformer or pyannote) in a streaming setting.
- No contextual-biasing support documented for Qwen3-ASR or Voxtral Realtime.
