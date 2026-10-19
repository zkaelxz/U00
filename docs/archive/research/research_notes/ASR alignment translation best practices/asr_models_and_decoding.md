# ASR model choice and decoding settings for zh/ja/ko drama, audio-drama, ASMR and video content (as of 2026-10-11)

Research date: 2026-10-11. All URLs were fetched or searched on that date unless noted. Release dates come from the sources themselves. "Unverified" means the claim came from a secondary blog or search snippet that I could not confirm at a primary source.

## Model choice: Whisper variants vs Qwen3-ASR, FunASR/SenseVoice, Kotoba/anime fine-tunes, NVIDIA, commercial; when turbo is worse for CJK

### Takeaway
As of October 2026, Qwen3-ASR-1.7B (Apache 2.0, released 2026-01-29) is the strongest open model for Mandarin, Cantonese and Chinese dialects. It beats Whisper-large-v3 by a wide margin on conversational, noisy and dialect Chinese, and it now ships as the default ASR in at least two CJK subtitle pipelines (VideoLingo and WhisperJAV's "qwen" mode). For Japanese the picture is mixed: on read speech, Whisper-large-v3 is roughly equal to or slightly better than Qwen3-ASR-1.7B. Japanese-domain fine-tunes (kotoba-whisper, anime-whisper, whisper-ja-1.5B) win on in-domain spontaneous, anime or ASMR speech. For Korean, Qwen3-ASR-1.7B is slightly ahead of large-v3. Turbo is an 809M model with a 4-layer decoder. It performs "similarly to large-v2" and degrades most on some languages (Cantonese and Thai are named). I found no primary per-language zh/ja/ko CER delta between turbo and large-v3.

### Cited Findings
**Qwen3-ASR (Alibaba Qwen)**
- Qwen3-ASR-1.7B, Qwen3-ASR-0.6B and Qwen3-ForcedAligner-0.6B were released on 2026-01-29. Native Transformers support followed on 2026-06-26. ASR covers 30 languages (including Chinese, Cantonese, Japanese and Korean) plus 22 Chinese dialects and accents, and handles speech, singing and songs with background music. Backends are Transformers and vLLM. Streaming is vLLM-only, with no timestamps or batching in streaming mode. The ForcedAligner covers 11 languages, including zh, ja and ko, for up to 5 minutes of speech per call — [QwenLM/Qwen3-ASR README](https://github.com/QwenLM/Qwen3-ASR)
- The tech report (arXiv 2601.21337, v1 2026-01-29, Apache 2.0) claims "SOTA among open-sourced ASR models" — [arXiv abs](https://arxiv.org/abs/2601.21337)
- Mandarin CER/WER, Qwen3-ASR-1.7B vs Whisper-large-v3 (vs GPT-4o-transcribe, Gemini-2.5-Pro, Doubao). Source: [Qwen3-ASR tech report, Tables 3-4](https://arxiv.org/html/2601.21337)
  - WenetSpeech test-net / test-meeting: 4.97 / 5.88 (Qwen3-1.7B) vs 9.86 / 19.11 (Whisper-v3) vs 15.30 / 32.27 (GPT-4o) vs 14.43 / 13.47 (Gemini-2.5-Pro).
  - AISHELL-2: 2.71 vs 5.06. SpeechIO: 2.88 vs 7.56. Fleurs-zh: 2.41 vs 4.09. CommonVoice-zh: 5.35 vs 12.91. KeSpeech (dialects): 5.10 vs 28.79.
  - Cantonese: Fleurs-yue 3.98 vs 9.18. CV-yue 7.57 vs 16.23. WenetSpeech-Yue short/long: 5.82 / 8.85 vs 32.26 / 46.64.
  - Internal robustness sets, Qwen3-1.7B vs Whisper-v3: Dialog-Mandarin 6.54 vs 14.01; Dialog-Cantonese 4.12 vs 31.04; ExtremeNoise 16.17 vs 63.17; Dialog-Chinese-Dialects 15.94 vs 44.55.
  - Singing (Opencpop): 3.08 vs 9.52. Whole songs, zh: 13.91 (Whisper N/A; GPT-4o 34.86).
- Japanese and Korean, Qwen models only, from the report appendix (Table A.2) — [arXiv html](https://arxiv.org/html/2601.21337):
  - CommonVoice ja: 1.7B 11.64, 0.6B 14.96, proprietary Qwen3-ASR-Flash 9.31.
  - Fleurs ja: 5.20 / 8.33 / 3.09.
  - CommonVoice ko: 5.88 / 8.48 / 3.82.
  - Fleurs ko: 2.57 / 3.72 / 2.07.
  - MLC-SLM (conversational) ja 11.80 / ko 8.61 for the 1.7B model.
  - The report gives no per-language ja/ko figure for Whisper here.
- Language ID accuracy, averaged over 4 sets: Whisper-v3 94.1%, Qwen3-ASR-0.6B 96.8%, Qwen3-ASR-1.7B 97.9% — [Qwen3-ASR report, Table 6](https://arxiv.org/html/2601.21337)
- Third-party FLEURS CER, Q8_0 quantized: Korean Qwen3-ASR-1.7B 4.60 vs whisper-large-v3 4.89; Japanese whisper-large-v3 4.81 vs Qwen3-ASR-1.7B 5.29; Fun-ASR-MLT-Nano 2.32 on Japanese. Unverified third-party leaderboard — [Handy ko](https://models.handy.computer/languages/ko), [Handy ja](https://models.handy.computer/languages/ja)
- A vendor's own FLEURS run, Qwen3-ASR-1.7B vs Whisper large-v3-turbo: zh CER 5.69 vs 6.88; ja 5.39 vs 5.71. Self-described as read speech, not an independent benchmark — [WhisperNotes blog](https://whispernotes.app/blog/qwen3-asr-vs-whisper)

**Whisper large-v3 / turbo / medium**
- Model sizes from the OpenAI README: large has 1550M parameters, needs ~10 GB VRAM and runs at 1x relative speed. Turbo has 809M parameters, ~6 GB, ~8x. Medium has 769M, ~5 GB, ~2x. Speeds were measured on English on an A100. "Turbo is not trained for translation tasks" and returns the source language even with `--task translate` — [openai/whisper README](https://github.com/openai/whisper)
- The turbo announcement (2024-10-01) has 4 decoder layers vs 32 and was fine-tuned 2 more epochs on transcription data only. It "performs similarly to large-v2, though it shows larger degradation on some languages like Thai and Cantonese." It did better on a Taiwan-Mandarin subset, for reasons the maintainers did not determine. A user reports random English words inserted into Japanese output, "persists since v3, v2 doesn't have this problem" — [openai/whisper discussion #2363](https://github.com/openai/whisper/discussions/2363)
- A secondary write-up says turbo holds "the same level of accuracy as Large V2 for Japanese" — [AXINC Medium](https://medium.com/axinc-ai/whisper-large-v3-turbo-high-accuracy-and-fast-speech-recognition-model-be2f6af77bdc). A secondary blog with no methodology gives an aggregate multilingual WER of ~12.5% (v3) vs ~14.0% (turbo). Unverified — [vexascribe](https://vexascribe.com/whisper-large-v3-vs-turbo)
- Japanese CER, from the kotoba-whisper-v2.0 model card. Format: CommonVoice8 / JSUT-basic5000 / ReazonSpeech-test — [kotoba-tech/kotoba-whisper-v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
  - whisper-large-v3: 8.5 / 7.1 / 14.9
  - whisper-large-v2: 9.7 / 8.2 / 28.1
  - whisper-medium: 11.5 / 10.0 / 33.2
  - whisper-small: 15.1 / 14.2 / 41.5
  - kotoba-whisper-v2.0 (756M, distilled, "6.3x faster than large-v3"): 9.2 / 8.4 / 11.6
  - So on the in-domain set (ReazonSpeech is Japanese TV), large-v3 cuts CER roughly in half vs v2 and medium. The fine-tune wins there but loses slightly on read speech.
- Whisper large-v3 on CSJ (spontaneous Japanese): 16.5-18.4% CER. On CommonVoice zh: 12.8% CER. The paper describes v3 as noticeably weaker on Chinese than on English — [Whale paper, arXiv 2506.01439](https://arxiv.org/pdf/2506.01439)

**FunASR / SenseVoice / Fun-ASR-Nano**
- FunASR's own long-form benchmark (184 files, Chinese CER): SenseVoice-Small 7.81%, Fun-ASR-Nano 8.20%, Paraformer-Large 10.18%, Whisper-large-v3 20.02%. Self-reported by the vendor — [FunASR on PyPI](https://pypi.org/project/FunASR/), [FunASR docs index](https://docsearch.algolia.com/mcp/docs/repo/modelscope/funasr)
- SenseVoice supports Mandarin, Cantonese, English, Japanese and Korean — [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice). I found no ja/ko CER for SenseVoice vs Whisper (see Gaps).
- In the Qwen3-ASR report, Fun-ASR-MLT-Nano scores Fleurs-zh 3.51 and CV-zh 6.20. These are better than Whisper-v3 but worse than Qwen3-1.7B. On the multilingual Fleurs average it is much worse (10.03 vs Whisper-v3 5.27) — [arXiv html](https://arxiv.org/html/2601.21337)

**Japanese fine-tunes and domain models**
- WhisperJAV v1.9.4 is a Japanese adult, ASMR and whisper-speech subtitle tool. It lists these models:
  - whisper-ja-1.5B (CT2) with the "strongest results in our scene-length benchmarks"
  - Qwen3-ASR 1.7B/0.6B as the "qwen" mode default
  - litagin/anime-whisper
  - Kotoba via transformers
  - Whisper large-v2 as the classic-pipeline default, as "the most predictable performer on this domain"
  - A cited JA Anime-Galgame 1.7B fine-tune with a 27% relative CER reduction, as reported by its authors

  Source: [meizhong986/WhisperJAV](https://github.com/meizhong986/WhisperJAV)

**NVIDIA Parakeet / Canary**
- Parakeet-TDT-0.6B-v3 and Canary-1B-v2 cover 25 European languages only, with no zh/ja/ko — [arXiv 2509.14128](https://arxiv.org/pdf/2509.14128)
- ja and ko are covered by Riva-branded Canary-1B, Canary-0.6B-Turbo and parakeet-1.1b-rnnt-multilingual. Mandarin-specific Parakeet CTC models exist (zh-cn, zh-tw) — [NGC Canary Riva](https://catalog.ngc.nvidia.com/orgs/nvidia/riva/models/canary-riva-1b/-), [build.nvidia.com parakeet multilingual](https://build.nvidia.com/nvidia/parakeet-1_1b-rnnt-multilingual-asr), [Canary-0.6B-Turbo card](https://build.nvidia.com/nvidia/canary-0-6b-turbo-asr/modelcard)

**Commercial APIs**
- On Mandarin, GPT-4o-transcribe and Gemini-2.5-Pro are much worse than Qwen3-ASR and often worse than Whisper-v3. Examples: WenetSpeech-meeting 32.27 (GPT-4o) vs 19.11 (Whisper-v3); KeSpeech 26.87 / 24.71 vs 28.79; ExtremeNoise 36.11 / 29.06 vs 63.17. ByteDance Doubao is close to Qwen3 on Mandarin (AISHELL-2 2.85, SpeechIO 2.93) — [Qwen3-ASR report](https://arxiv.org/html/2601.21337). Note that these numbers are from a competitor's report.
- VideoLingo offers two hosted cloud ASR services (MAI-Transcribe-2 via Azure Speech or OpenRouter, and a hosted voice vendor) as cloud alternatives to its local Qwen3-ASR default — [Huanshere/VideoLingo](https://github.com/Huanshere/VideoLingo)

### Inferences
- **Mandarin, Cantonese, dialects, singing, noisy drama:** Qwen3-ASR-1.7B is the clear choice, and the gap is large (CER often halved vs Whisper-large-v3 on conversational and noisy sets). The 0.6B model is the speed and VRAM fallback and still beats Whisper-v3 on every Chinese set listed.
- **Japanese:** No single winner.
  - Read speech: large-v3 ≈ Qwen3-1.7B. Third-party Fleurs scores have large-v3 slightly ahead, but on Qwen's own Fleurs-ja run Qwen3-1.7B scores 5.20.
  - Spontaneous or anime speech: domain fine-tunes (kotoba, anime-whisper, whisper-ja-1.5B) are likely best.
  - Large-v2 is still preferred by one domain project for predictability, not peak accuracy.
- **Korean:** Qwen3-1.7B is slightly ahead of large-v3 on Fleurs, based on thin evidence.
- **Turbo for CJK:** Turbo is roughly v2-level. Kotoba's table shows v2 at about 9.7 vs 8.5 CER on CV8-ja and 28.1 vs 14.9 on ReazonSpeech-ja relative to v3. If turbo ≈ v2, it may be clearly worse on in-domain Japanese TV-style speech. That is an extrapolation, not a measurement. It is clearly worse for Cantonese (stated by OpenAI), and it cannot translate.
- **Hybrid design:** A sound default for a CJK subtitle app is Qwen3-ASR (zh/yue/ko and general ja) plus Qwen3-ForcedAligner for timestamps, with Whisper large-v3 (faster-whisper) kept as a fallback or alternative, especially for ja and for languages outside Qwen's 30.

### Gaps
- No primary-source per-language zh/ja/ko CER for large-v3-turbo vs large-v3. OpenAI's discussion has a chart image I could not read.
- No independent (non-vendor) head-to-head on real drama or ASMR audio for Qwen3-ASR vs Whisper vs SenseVoice.
- No ja/ko CER for SenseVoice. No ReazonSpeech ESPnet/NeMo model numbers were collected (ReazonSpeech appears here only as a test set).
- I did not verify any Whisper successor, or a newer Qwen ASR release, between June and October 2026. Searches surfaced none.
- No ja/ko numbers for GPT-4o-transcribe, Gemini or Doubao.

## Decoding settings and CJK hallucination/looping mitigations

### Takeaway
The library defaults are the same in openai-whisper and faster-whisper:
- Temperature fallback schedule 0.0-1.0 in 0.2 steps
- compression_ratio_threshold 2.4, log_prob_threshold -1.0, no_speech_threshold 0.6
- condition_on_previous_text=True, beam 5 (CLI / faster-whisper)

Subtitle pipelines usually override them in the same way: VAD on, condition_on_previous_text off, and often hallucination filters. The main CJK failure modes are repetition loops, which conditioning on previous text feeds, and stock "subtitle credit" phrases hallucinated over silence or music. Mixed-language segments get wrong-language or English-word insertions.

### Cited Findings
- **openai-whisper `transcribe()` defaults** — [whisper/transcribe.py](https://github.com/openai/whisper/blob/main/whisper/transcribe.py):
  - temperature=(0.0,0.2,0.4,0.6,0.8,1.0); compression_ratio_threshold=2.4; logprob_threshold=-1.0; no_speech_threshold=0.6; condition_on_previous_text=True; initial_prompt=None; carry_initial_prompt=False; word_timestamps=False; hallucination_silence_threshold=None.
  - The CLI defaults to beam_size=5 and best_of=5. The Python API leaves these unset, so it decodes greedily.
  - append_punctuations includes CJK 。，！？：、.
- **How the fallback works** (same source): a segment is re-decoded at the next temperature if compression ratio > 2.4 (repetitive) or avg logprob < -1.0. It is treated as silence if no_speech_prob > 0.6 and avg logprob < -1.0. Beam settings are dropped at temperature > 0. If the result's temperature is > 0.5, or conditioning is off, the prompt history is reset — [whisper/transcribe.py](https://github.com/openai/whisper/blob/main/whisper/transcribe.py)
- **faster-whisper `WhisperModel.transcribe()` defaults** — [faster_whisper/transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py):
  - beam_size=5, best_of=5, patience=1, the same temperature list and thresholds, condition_on_previous_text=True, prompt_reset_on_temperature=0.5.
  - hotwords=None, vad_filter=False, word_timestamps=False, hallucination_silence_threshold=None, repetition_penalty=1, no_repeat_ngram_size=0, language_detection_segments=1.
- **faster-whisper `BatchedInferencePipeline`** (same source): vad_filter=True, using VadOptions(max_speech_duration_s=chunk_length, min_silence_duration_ms=160). It internally forces condition_on_previous_text=False, has batch_size=8, and without_timestamps=True.
- **faster-whisper README:** Silero VAD's default only removes silence > 2 s. The README's distil-large-v3 example uses condition_on_previous_text=False — [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- **WhisperX** sets condition_on_prev_text=False by default "to reduce hallucination." It uses pyannote VAD with batched faster-whisper, and its CLI default model is small — [m-bain/whisperX](https://github.com/m-bain/whisperX)
- **stable-ts** (archived 2026-05-30, "development indefinitely paused") — [jianfch/stable-ts](https://github.com/jianfch/stable-ts):
  - Keeps condition_on_previous_text=True, noting that disabling it reduces loops but makes text less consistent across windows.
  - Adds suppress_silence=True and an optional Silero vad (threshold 0.35).
  - Its regroup preset splits on CJK punctuation (。？，), on 0.5 s gaps, and at segments > 70 chars.
  - Has a max_instant_words=0.5 filter and a denoiser option.
- **pyvideotrans** exposes beam_size/best_of (1-5), temperature, condition_on_previous_text ("may cause repetition"), VAD threshold/min-speech/min-silence/max-speech, compute_type and hotwords. Its anti-repetition advice is to raise the repetition penalty and lower compression_ratio_threshold. Default values are not on the page — [pyvideotrans advanced settings](https://en.pyvideotrans.com/adv)
- **Purfview faster-whisper-xxl** (the engine behind Subtitle Edit's "Purfview Faster-Whisper-XXL") offers vad_method silero_v3/v4, pyannote_v3, pyannote_onnx_v3, auditok and webrtc. To cut memory, try --best_of=1, --beam_size=1, -fallback=None — [Purfview/whisper-standalone-win](https://Github.com/Purfview/whisper-standalone-win). Community advice: `--vad_method pyannote_v3` fixes subtitles starting minutes late; beam 5/best_of 5 to start; some users run beam 2, temperature 0 — [VideoHelp forum](https://forum.videohelp.com/showthread.php?p=2784833), [VideoHelp](https://forum.videohelp.com/showthread.php?p=2785269)
- **Hallucination evidence:**
  - Whisper large-v3 produced non-empty transcripts for 40.3% of non-speech inputs, often "subtitle-style phrases" — [arXiv 2501.11378](https://export.arxiv.org/pdf/2501.11378) (as summarized in search results; not read in full).
  - Repetition loops are decoder-driven: 86% of insertions for Whisper-small under 30% masking were loops — [arXiv 2508.15882](https://arxiv.org/pdf/2508.15882) (search-snippet level).
  - A 2026 paper proposes hallucination-space projection — [arXiv 2609.04561](https://arxiv.org/pdf/2609.04561) (title only; not read).
- **WhisperJAV (Japanese ASMR/JAV) mitigations** — [WhisperJAV](https://github.com/meizhong986/WhisperJAV):
  - Japanese-trained WhisperSeg VAD by default, plus TEN VAD, Silero and FireRedVAD options.
  - Speech pieces capped at 4 s for Qwen3-ASR and anime-whisper.
  - Semantic scene detection before VAD.
  - Removal of hallucinations, repetition and moan/breath-only lines.
  - Enhancement and denoising off by default, because "blanket denoising can remove consonant detail."
  - Optional bs-roformer vocal isolation for music-heavy audio.
  - The recogniser is reloaded every 20 minutes of audio to contain a fault where it stops returning output.
  - Measured on 7 drama scenes: line-end timing gap shrank 10-24%, text error changed by -1.5% to +0.5%.
- **Language forcing:** Qwen3-ASR takes `language=None` for auto-detection or a forced language name, with per-file lists in batch mode — [Qwen3-ASR README](https://github.com/QwenLM/Qwen3-ASR). Its SFT includes context-biasing data, and context goes in the system prompt, but no biasing benchmark is published — [Qwen3-ASR report](https://arxiv.org/html/2601.21337)
- **Mixed-language audio:** VideoLingo uses one recognition and alignment language per segment and warns that accuracy is not guaranteed for mixed-language speech — [VideoLingo](https://github.com/Huanshere/VideoLingo). Whisper v3 and turbo inserting English words into Japanese output is a user-reported failure — [whisper #2363](https://github.com/openai/whisper/discussions/2363)

### Inferences
- **Recommended Whisper-family defaults for CJK drama/ASMR:**
  - Force the language. LID errors are 5-10% even on clean sets (Whisper-v3 LID 89-95% on conversational and Fleurs sets).
  - VAD on: Silero for general content, pyannote or a Japanese-trained segmenter for ASMR and whispered speech.
  - condition_on_previous_text=False, or at least prompt reset on fallback.
  - Beam 5 with the standard temperature fallback.
  - Set hallucination_silence_threshold (around 2 s) when using word timestamps.
  - Post-filter known stock phrases and repeated n-grams.
  - Use a glossary only through initial_prompt or hotwords, kept short. The prompt also sets script and punctuation style, e.g. simplified vs traditional.
- **compression_ratio_threshold on CJK:** CJK text is multi-byte UTF-8 and has fewer repeated byte patterns per character. My reasoning is that the default 2.4 may trip later on CJK loops than on English, which would explain pyvideotrans's "lower it" advice. I found no primary source that measures this.
- **VAD chunk length:** Short VAD chunks (≤ 4-15 s) reduce looping and silence hallucinations but remove cross-chunk context. Batched faster-whisper and WhisperX accept this trade-off by default.

### Gaps
- No primary source gives measured per-setting CER or hallucination-rate deltas for zh/ja/ko, e.g. condition_on_previous_text on vs off on Japanese drama.
- I did not verify that the specific phrase "ご視聴ありがとうございました" (and the zh equivalent "请不吝点赞 订阅…") is a documented hallucination in a primary source. It is widely reported anecdotally, but I found no citation.
- I did not obtain Subtitle Edit's and the XXL engine's actual shipped default values.
- I did not open the faster-whisper `vad.py` VadOptions defaults directly. Commonly cited values are threshold 0.5 and min_silence 2000 ms, but these are unverified here.

## What leading applications and open-source pipelines ship

### Takeaway
Chinese-community subtitle pipelines have moved from WhisperX/faster-whisper large-v3 to Qwen3-ASR plus ForcedAligner in 2026 (VideoLingo's default). Whisper-based tools (WhisperX, faster-whisper-XXL in Subtitle Edit, pyvideotrans, VideoCaptioner) remain common and pair faster-whisper with VAD and condition_on_previous_text off. Domain tools (WhisperJAV) layer specialised VAD, scene detection and post-filters on top of several backends.

### Cited Findings
- **VideoLingo:** the default is local Qwen3-ASR + Qwen3-ForcedAligner (word timestamps). Cloud options are two hosted ASR services (MAI-Transcribe-2 and a hosted voice vendor). WhisperX is no longer in the installer and needs a manual install. Vocal separation "may help" with BGM. Japanese and Chinese are supported inputs; Korean is not listed — [Huanshere/VideoLingo](https://github.com/Huanshere/VideoLingo)
- **VideoCaptioner:** offers the faster-whisper, whisper-cpp, whisper-api, bijian (必剪) and jianying (剪映, free online) engines. It uses word-level timestamps + VAD and LLM "semantic" sentence segmentation and subtitle optimisation. Default model and settings are not stated on the README — [WEIFENG2333/VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner)
- **WhisperX:** batched faster-whisper + pyannote VAD + wav2vec2 forced alignment. condition_on_prev_text=False. The CLI default model is small. It claims ~70x realtime with large-v2 and < 8 GB VRAM at beam 5. Characters missing from the alignment model's dictionary get no timing, which matters for CJK — [m-bain/whisperX](https://github.com/m-bain/whisperX)
- **stable-ts:** see the defaults above. Archived 2026-05-30 — [jianfch/stable-ts](https://github.com/jianfch/stable-ts)
- **pyvideotrans:** faster-whisper with exposed beam, best_of, temperature, VAD and hotwords settings, plus CJK-specific line length and traditional→simplified conversion — [pyvideotrans adv](https://en.pyvideotrans.com/adv)
- **WhisperJAV v1.9.4:** see the models and VAD above. The default mode is "balanced" (faster-whisper). The Japanese default segmenter is WhisperSeg — [WhisperJAV](https://github.com/meizhong986/WhisperJAV)
- **Subtitle Edit / Purfview XXL:** see above for VAD options and community settings — [Purfview](https://Github.com/Purfview/whisper-standalone-win)

### Inferences
- The common pattern among strong CJK pipelines is VAD segmentation → ASR with forced language → separate forced alignment for word timing (wav2vec2 in WhisperX, Qwen3-ForcedAligner in VideoLingo) → LLM re-segmentation and correction.
- Whisper's own timestamps are "utterance-level and can be off by several seconds" (WhisperX README), so a separate aligner is standard.
- The Qwen3-ForcedAligner reports 32 ms average absolute shift on human-labeled sets vs 101 ms for NeMo NFA. On concatenated 300 s audio (MFA-labeled) it scores 52.9 ms vs 2708 ms for WhisperX — [Qwen3-ASR report, Table 9](https://arxiv.org/html/2601.21337). These are vendor numbers.

### Gaps
- I did not fetch Buzz, MacWhisper, Whisper-WebUI or Descript defaults; this was limited by the call budget.
- No public primary documentation was found for YouTube's or Netflix's internal ASR or CJK subtitle workflows.

## Speed vs accuracy on consumer GPUs (8-12 GB) and CPU

### Takeaway
- **8 GB GPU:** large-v2/v3 runs comfortably in faster-whisper: about 4.5 GB fp16 or 2.9 GB int8, and about 13x realtime sequential or about 45x batched on an RTX 3070 Ti.
- **Turbo:** about 6 GB in openai-whisper and about 8x faster than large.
- **Qwen3-ASR:** much faster per GPU when run with vLLM batching. Consumer-GPU VRAM figures are not published.
- **CPU:** whisper.cpp or faster-whisper int8 are 3-4x faster than openai-whisper.

### Cited Findings
- **GPU benchmark:** 13 min of audio, large-v2, RTX 3070 Ti 8 GB, beam 5 — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper):

  | Implementation | Precision | Time | VRAM |
  |---|---|---|---|
  | openai-whisper | fp16 | 2m23s | 4708 MB |
  | whisper.cpp (flash attention) | fp16 | 1m05s | 4127 MB |
  | transformers (SDPA) | fp16 | 1m52s | 4960 MB |
  | faster-whisper | fp16 | 1m03s | 4525 MB |
  | faster-whisper, batch 8 | fp16 | 17s | 6090 MB |
  | faster-whisper | int8 | 59s | 2926 MB |
  | faster-whisper, batch 8 | int8 | 16s | 4500 MB |

- **CPU benchmark:** small model, i7-12700K, 8 threads, same source:

  | Implementation | Precision | Time | RAM |
  |---|---|---|---|
  | openai-whisper | fp32 | 6m58s | — |
  | whisper.cpp | fp32 | 2m05s | — |
  | whisper.cpp (OpenVINO) | fp32 | 1m45s | — |
  | faster-whisper | fp32 | 2m37s | — |
  | faster-whisper | int8 | 1m42s | 1477 MB |
  | faster-whisper, batch 8 | int8 | 51s | — |

- **Model sizes:** large 1550M / ~10 GB / 1x; turbo 809M / ~6 GB / ~8x; medium 769M / ~5 GB / ~2x (A100, English) — [openai/whisper](https://github.com/openai/whisper)
- **kotoba-whisper-v2.0:** 756M parameters, 6.3x faster than large-v3. Chunked decoding with chunk_length_s=15, batch_size=16 is up to 9x faster than sequential long-form; sequential is up to 0.5% WER more accurate — [kotoba-whisper-v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
- **Qwen3-ASR efficiency** (Table 2; the hardware is not given in my extract, presumably datacenter GPU with vLLM) — [Qwen3-ASR report](https://arxiv.org/html/2601.21337):
  - 0.6B offline, concurrency 1: RTF 0.0092, TTFT 92 ms.
  - 1.7B offline, concurrency 1: RTF 0.0148.
  - 0.6B at concurrency 128: about 2000 s of audio per second.
  - ForcedAligner-0.6B: RTF 0.0089 at concurrency 1.
  - The text claims aligner RTF "close to 0.001" at high concurrency, but Table 2 shows 0.197 at concurrency 128, an internal inconsistency.
- **WhisperX:** < 8 GB for large-v2 at beam 5; ~70x realtime batched — [whisperX](https://github.com/m-bain/whisperX)

### Inferences
- On 8-12 GB cards, large-v3 at fp16 or int8_float16 in faster-whisper is affordable, so turbo's speed gain matters less than its CJK accuracy loss for offline subtitle work. Turbo makes sense for previews or CPU use.
- Qwen3-ASR-1.7B weights are about 3.4 GB in bf16, from the parameter count. It plausibly fits 8 GB, but I found no measured consumer-GPU VRAM or RTF. vLLM's default gpu_memory_utilization will grab most of the VRAM. This is unverified.
- **CPU-only users:** whisper.cpp or faster-whisper int8 with small, medium or turbo. Qwen3-ASR has no official GGUF or CTranslate2 runtime listed in its README.

### Gaps
- No measured large-v3 or turbo numbers on 8-12 GB consumer GPUs from a primary source. The faster-whisper benchmark uses large-v2, though v3 has the same architecture and size as v2, so the cost should be similar.
- No Qwen3-ASR consumer-GPU VRAM, RTF or CPU figures. No community GGUF or CT2 port was verified.
- No CJK-specific speed data. Token counts per second differ for CJK, but this was not measured.
