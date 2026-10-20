# Open-source live captioning, live translation and fast subtitle pipelines (survey, October 2026)

Method note: the stars, last-push dates, licences and archived flags below come from the GitHub repository search API, queried on 2026-10-10. Feature claims come from each project's README or docs page, fetched the same day. Any figure not quoted from a page is marked as an inference or listed under Gaps.

## Live captioning and translation tools: architecture, engines, latency, maturity, licence

### Takeaway
The active open-source live tools fall into three groups.

1. **Windows LiveCaptions front-ends.** The leading one is LiveCaptions-Translator, which adds MT or LLM translation on top of the OS captions.
2. **Self-hosted streaming Whisper servers.** WhisperLiveKit and WhisperLive stream audio over WebSocket to a local server. WhisperLiveKit has moved to the SimulStreaming/AlignAtt policy plus distilled-NLLB translation and now supports SenseVoice, Qwen3-ASR and Voxtral backends.
3. **Embedded plugins.** OBS LocalVocal runs whisper.cpp inside OBS with CTranslate2 or cloud translation.

None of these READMEs gives a millisecond latency figure in text. The only quantitative speed claim is SimulStreaming's "~5-times faster" than WhisperStreaming.

### Cited Findings

**Maturity snapshot (GitHub API, 2026-10-10)**

| Repo | Stars | Last push | Licence | Archived |
|---|---|---|---|---|
| openai/whisper | 110,280 | 2026-08-31 | MIT | no |
| ggml-org/whisper.cpp | 54,270 | 2026-10-06 | MIT | no |
| SYSTRAN/faster-whisper | 25,801 | 2026-10-10 | MIT | no |
| m-bain/whisperX | 24,455 | 2026-09-26 | BSD-2-Clause | no |
| chidiwilliams/buzz | 21,909 | 2026-10-02 | MIT | no |
| jianchang512/pyvideotrans | 19,308 | 2026-10-07 | GPL-3.0 | no |
| immersive-translate/immersive-translate | 19,296 | 2026-09-30 | none declared | no |
| Huanshere/VideoLingo | 18,737 | 2026-09-30 | Apache-2.0 | no |
| WEIFENG2333/VideoCaptioner | 16,206 | 2026-09-12 | GPL-3.0 | no |
| k2-fsa/sherpa-onnx | 15,204 | 2026-10-09 | Apache-2.0 | no |
| SubtitleEdit/subtitleedit | 14,495 | 2026-10-10 | MIT | no |
| QuentinFuxa/WhisperLiveKit | 11,125 | 2026-10-01 | Apache-2.0 | no |
| snakers4/silero-vad | 10,395 | 2026-09-29 | MIT | no |
| KoljaB/RealtimeSTT | 10,172 | 2026-09-17 | MIT | no |
| thewh1teagle/vibe | 7,744 | 2026-10-06 | MIT | no |
| morpheus65535/bazarr | 4,353 | 2026-10-10 | GPL-3.0 | no |
| collabora/WhisperLive | 4,311 | 2026-10-07 | MIT | no |
| SakiRinn/LiveCaptions-Translator | 3,843 | 2026-08-17 | Apache-2.0 | no |
| ufal/whisper_streaming | 3,674 | 2025-11-12 | MIT | no (stale) |
| kyutai-labs/delayed-streams-modeling | 3,041 | 2026-01-26 | Apache-2.0 | no |
| linto-ai/whisper-timestamped | 2,854 | 2026-09-28 | AGPL-3.0 | no |
| jianfch/stable-ts | 2,282 | 2026-05-30 | MIT | **yes** |
| TypesettingTools/Aegisub | 2,006 | 2026-10-09 | NOASSERTION | no |
| gnehs/subtitle-translator-electron | 1,689 | 2026-08-01 | MIT | no |
| royshil/obs-localvocal | 1,630 | 2026-09-09 | GPL-2.0 | no |
| McCloudS/subgen | 1,490 | 2026-08-07 | MIT | no |
| ufal/SimulStreaming | 686 | 2026-07-12 | MIT | no |
| machinewrapped/llm-subtrans | 658 | 2026-10-08 | NOASSERTION (README says MIT) | no |
| LiveTL/LiveTL | 620 | 2026-10-10 | AGPL-3.0 | no |
| yazinsai/srt-ai | 292 | 2026-03-17 | none declared | no |

Each row links to its repo: [whisper](https://github.com/openai/whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [whisperX](https://github.com/m-bain/whisperX), [buzz](https://github.com/chidiwilliams/buzz), [pyvideotrans](https://github.com/jianchang512/pyvideotrans), [immersive-translate](https://github.com/immersive-translate/immersive-translate), [VideoLingo](https://github.com/Huanshere/VideoLingo), [VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner), [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [subtitleedit](https://github.com/SubtitleEdit/subtitleedit), [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit), [silero-vad](https://github.com/snakers4/silero-vad), [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT), [vibe](https://github.com/thewh1teagle/vibe), [bazarr](https://github.com/morpheus65535/bazarr), [WhisperLive](https://github.com/collabora/WhisperLive), [LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator), [whisper_streaming](https://github.com/ufal/whisper_streaming), [delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling), [whisper-timestamped](https://github.com/linto-ai/whisper-timestamped), [stable-ts](https://github.com/jianfch/stable-ts), [Aegisub](https://github.com/TypesettingTools/Aegisub), [subtitle-translator-electron](https://github.com/gnehs/subtitle-translator-electron), [obs-localvocal](https://github.com/royshil/obs-localvocal), [subgen](https://github.com/McCloudS/subgen), [SimulStreaming](https://github.com/ufal/SimulStreaming), [llm-subtrans](https://github.com/machinewrapped/llm-subtrans), [LiveTL](https://github.com/LiveTL/LiveTL), [srt-ai](https://github.com/yazinsai/srt-ai).

**LiveCaptions-Translator (Windows)**
- **ASR.** It uses Windows 11 LiveCaptions (available since 22H2) and "automatically invokes Windows LiveCaptions without opening separate windows". Enabling "Include microphone audio" lets it translate the user's own speech. — [README](https://github.com/SakiRinn/LiveCaptions-Translator)
- **MT engines.**
  - LLM: Ollama, any OpenAI-compatible API, OpenRouter.
  - Traditional: Google, DeepL, Youdao, Baidu.
  - Self-hosted: MTranServer, LibreTranslate.
  - The README recommends LLM engines because they handle incomplete sentences and context well. — [README](https://github.com/SakiRinn/LiveCaptions-Translator)
- **UI.** A borderless, transparent overlay window. History records source and translation and exports to CSV. Log Cards show recent lines, and an "Overlay Sentences" setting controls how many sentences show. — [README](https://github.com/SakiRinn/LiveCaptions-Translator)
- **Limitations.**
  - The source language must be changed inside Windows LiveCaptions.
  - LiveCaptions must be set to "Overlaid on screen" or a display bug appears.
  - Untested on older Windows or .NET.
  - No latency figures are published. — [README](https://github.com/SakiRinn/LiveCaptions-Translator)
- **Open issues show what users want.** These include:
  - #278 "Reading Mode: Keep subtitles stable while captions are being updated" (flicker and instability).
  - #234 full scrollable caption history.
  - #169 SRT export.
  - #252 switchable prompts.
  - #273 Ollama local API timing out when a Windows system proxy routes IPv6 loopback through the proxy.
  - #254 and #171 requests for more MT backends (Microsoft, Tencent).
  — [Issues page](https://github.com/SakiRinn/LiveCaptions-Translator/issues)

**WhisperLiveKit (self-hosted streaming server, Apache-2.0)**
- **Streaming policies.** `--backend-policy`: the default is SimulStreaming (AlignAtt), with LocalAgreement as the alternative. Voxtral uses its own policy. FunASR and Canary support LocalAgreement only. — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **ASR backends.** mlx-whisper, faster-whisper, whisper, openai-api, funasr (SenseVoiceSmall: Mandarin, Cantonese, English, Japanese, Korean, plus auto-detect), voxtral-mlx/voxtral (4B), qwen3-vllm, qwen3-streaming, canary (NeMo, 25 European languages). — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Translation.**
  - Default: NLLW, "based on distilled NLLB", covering 200 languages. Options are `--nllb-size` 600M (default) or 1.3B, on transformers or ctranslate2.
  - Alternative: `alignatt`, streaming LLM translation through a sidecar. — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Latency knobs.**
  - `--frame-threshold` (default 25; "lower = faster, higher = more accurate").
  - `--beams 1` (greedy decoding).
  - `--pause-segmentation-seconds 5.0` (a stable boundary at long pauses).
  - `--asr-coalesce-min-s` (fewer encoder passes, at the cost of less frequent updates).
  - VAD/VAC are on by default, and disabling them is marked "NOT ADVISED".
  - `--language auto` "tends to bias towards English". — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Qwen3-ASR streaming.**
  - An explicit `--language` is required.
  - Word timestamps are "interpolated estimates (~1 s precision)".
  - Causal mode encodes each ~2 s block once inside a 15 s window. The windowed default uses 12 s of left context.
  - The README recommends one realtime session per GPU. — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Diarization.** Streaming Sortformer is the default, with 1 to 4 speakers. Diart is "Not recommended". Each token is resolved to one speaker, so overlapping speech is not represented. — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Delivery.**
  - WebSocket at `/asr`, with `language`, `target_language`, `context` and `mode` (`full` or the experimental `diff`) as query parameters.
  - `--pcm-input` sends raw PCM through an AudioWorklet and skips FFmpeg.
  - A Chrome extension captures tab audio.
  - The server exposes OpenAI- and Deepgram-compatible APIs and handles multiple users with one shared engine. — [README](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Benchmarks.** Charts measure 6 minutes of LibriVox audio per language on an H100. The numbers exist only as images, so they could not be extracted. — [README](https://github.com/QuentinFuxa/WhisperLiveKit)

**SimulStreaming (ÚFAL, MIT)**
- It cascades Whisper large-v3 with EuroLLM-9B. The AlignAtt policy uses encoder-decoder attention to decide when to stop decoding. — [README](https://github.com/ufal/SimulStreaming)
- The README claims it is "~5-times faster than our previous release called WhisperStreaming" and the best system in the IWSLT 2025 Simultaneous Speech Translation shared task. No numbers appear on the README. — [README](https://github.com/ufal/SimulStreaming)
- **Terminology support.**
  - `--static_init_prompt` holds text that is never scrolled out, intended for document-wide terminology.
  - `--init_prompt` sets an initial prompt in the target language.
  - `--max_context_tokens` sets the context length.
  - The README says it supports in-domain terminology and RAG. — [README](https://github.com/ufal/SimulStreaming)
- **Hardware.** A GPU with 10 GB or more of VRAM is recommended for large-v3. CPU is "too slow for real time". — [README](https://github.com/ufal/SimulStreaming)
- **Superseded predecessor.** ufal/whisper_streaming (LocalAgreement) was last pushed 2025-11-12. — [GitHub](https://github.com/ufal/whisper_streaming)

**WhisperLive (Collabora, MIT)**
- **Backends.** faster_whisper, TensorRT, OpenVINO (Intel CPU and GPU), plus a ROCm path.
- **Protocol.** A WebSocket client and server. The client sends 16 kHz mono PCM. Callbacks deliver partial and committed transcripts (`on_committed_transcript`).
- **Other features.** Silero VAD (`use_vad`), a server-side translation thread (`enable_translation`, `target_language`), and Chrome and Firefox extensions.
- **Defaults.** `--max_clients` 4 and `--max_connection_time` 600 s.
- **Latency.** No figures published.
— [README](https://github.com/collabora/WhisperLive)

**OBS LocalVocal (GPL-2.0)**
- **ASR and translation.** whisper.cpp inside OBS, shipping the Tiny.en model, with GGML models selectable. Translation comes from cloud providers, Whisper's own translate mode, or CTranslate2 NMT.
- **Output.** OBS text sources, .txt or .srt files synced to the recording, and captions on the RTMP stream for YouTube and Twitch. Partial results are shown, and filter/replace rules can edit captions.
- **GPU.** CUDA, hipBLAS, Vulkan, OpenCL, Metal and CoreML. The default is CPU until the user picks a backend.
- **Not documented.** VAD and latency figures.
— [README](https://github.com/royshil/obs-localvocal)

**Vibe (MIT)**
- **Engine and models.** whisper.cpp, with Whisper, "Nemotron 3.5" and "Parakeet TDT v3" models.
- **Features.** Batch transcription, diarization, a realtime preview, and translation to English only.
- **Summaries and export.** Claude-API or Ollama summaries. Export to SRT, VTT, TXT, HTML, PDF, JSON and DOCX.
- **Other.** A CLI and an HTTP API with Swagger. Vulkan and CoreML GPU support.
— [README](https://github.com/thewh1teagle/vibe)

**Chrome built-in Translator and Language Detector APIs**
- Chrome 138 appears in the support table, with Edge 148 and no Firefox or Safari. Desktop only: they "don't work on mobile devices". — [Chrome docs](https://developer.chrome.com/docs/ai/translator-api)
- The model and language packs download on demand. Every pair is reported as "downloadable" until a site creates a translator for it, which hides fingerprinting signals. — [Chrome docs](https://developer.chrome.com/docs/ai/translator-api)
- `translateStreaming()` exists. Requests are processed sequentially, so large inputs block later ones, and Chrome advises chunking. — [Chrome docs](https://developer.chrome.com/docs/ai/translator-api)
- The API is not available in Web Workers. Cross-origin iframes need `allow="translator"`. — [Chrome docs](https://developer.chrome.com/docs/ai/translator-api)
- 39 BCP-47 codes are listed, including `zh` and `zh-Hant`. The page says the list is "subject to change". — [Chrome docs](https://developer.chrome.com/docs/ai/translator-api)
- The Language Detector API is desktop Chrome only and is paired with the Translator API for unknown source languages. — [Chrome docs](https://developer.chrome.com/docs/ai/translator-api)

**Browser-extension translators**
- immersive-translate/immersive-translate has 19,296 stars but declares no licence. Its description covers bilingual web pages, PDF, EPUB and subtitle-file translation. — [GitHub](https://github.com/immersive-translate/immersive-translate)
- LiveTL/LiveTL is AGPL-3.0 and active, last pushed 2026-10-10. Its description names "LiveTL, HyperChat, and YtcFilter". — [GitHub](https://github.com/LiveTL/LiveTL)

### Inferences
- **Recommended Live captions architecture for Baihe.** The strongest open design is WhisperLiveKit's: a streaming policy (AlignAtt) plus VAD plus a dedicated small MT model, delivered over WebSocket with partial and committed segments. For CJK it offers SenseVoiceSmall (FunASR) and Qwen3-ASR backends, which suit Baihe's Chinese, Japanese and Korean focus better than Whisper `small`.
- **Partial versus committed lines.** Several tools separate partial transcripts from committed ones: WhisperLive's `on_committed_transcript`, LocalVocal's partials, and LiveCaptions-Translator users asking for a stable "Reading Mode" in #278. Translating only committed segments, and showing partials in a muted style, is a cheap fix for flicker.
- **Static terminology prompt.** SimulStreaming's `--static_init_prompt` matches Baihe's glossary and character names. It pins series terms into the decoder context without letting them scroll out.
- **Chrome Translator API for the browser extension.** It is a plausible zero-cost, on-device MT fallback. Two limits apply: it is unavailable in Web Workers, and it processes requests sequentially. Whether it is available inside extension contexts is unverified.
- LocalVocal and WhisperLive are lower-ceiling references: they have fewer CJK-specific backends and no published latency.

### Gaps
- **Measured live latency.** No project page gave latency in milliseconds or seconds as text. WhisperLiveKit's benchmark values are only in images. The IWSLT 2025 numbers for SimulStreaming would need the paper, which was not fetched.
- **Apple Translation framework apps, VTuber/Twitch stream translator apps, YouTube live caption translator extensions other than LiveTL, and Chrome's built-in Live Caption.** Not researched within the tool budget.
- **LiveTL's mechanism.** It is likely chat-filter based rather than ASR, but this is inferred from memory and its description, not verified.
- **Immersive Translate.** Its subtitle or live-video mechanism and how open its source is were not verified. The repo declares no licence.
- **Extension availability of the Translator API.** The Chrome docs page does not address whether it is usable from an extension service worker.
- **RealtimeSTT, sherpa-onnx and Kyutai delayed-streams.** Only metadata was collected, not features.

## Offline subtitle pipelines: batching, context, glossary/names, line-count preservation, error recovery, cost, QC

### Takeaway
The mature offline pipelines share one shape:

1. VAD-gated, batched faster-whisper (or Qwen3-ASR or SenseVoice) with word timestamps or forced alignment.
2. LLM-based re-segmentation into readable lines.
3. Batched LLM translation with scene grouping, a growing terminology map, and resumable project files.

llm-subtrans documents the most explicit batching and terminology controls. VideoLingo and VideoCaptioner add "reflect and rewrite" translation passes. stable-ts is the reference for anti-hallucination and timing heuristics, but it was archived on 2026-05-30.

### Cited Findings

**llm-subtrans (llm-subtrans / gpt-subtrans)**
- **Batching.**
  - `--scenethreshold` is the gap in seconds that starts a new scene.
  - `--minbatchsize` and `--maxbatchsize` set batch bounds. The README trades larger batches (cheaper and faster) against "more desync risk".
  - `--preprocess` splits long-duration lines, and `--postprocess` runs after each request.
  — [README](https://github.com/machinewrapped/llm-subtrans)
- **Context and names.**
  - `--moviename` and `--description` supply context, with the warning "Less is generally more here, or the AI can start improvising".
  - `--names` lists character names.
  - `--build-terminology-map` accumulates names and terms across batches.
  - `--terminology` takes SOURCE::TRANSLATION seeds, and `--substitution` applies forced replacements.
  - The version history credits batch and scene summaries with helping context.
  — [README](https://github.com/machinewrapped/llm-subtrans)
- **Recovery and cost.**
  - `.subtrans` project files let a run resume.
  - `--reparse` re-reads a stored response without re-sending it.
  - `--retranslate` and `--reload` redo work.
  - `--preview` batches without API calls, and `--maxlines` caps the run.
  - `--ratelimit` caps requests per minute. `--temperature` defaults to 0.
  - Version 0.2's new prompting approach "greatly reduces desyncs" caused by the model merging lines.
  — [README](https://github.com/machinewrapped/llm-subtrans)
- **Providers and transcription.**
  - Providers: OpenRouter (default), Gemini, OpenAI/Azure, DeepSeek, Anthropic, Mistral, Bedrock, and OpenAI-compatible local servers.
  - Transcription: cloud services or a local "Qwen Local" (Qwen3-ASR, beta, about 6 GB download).
  - Transcription defaults: diarize on, align on.
  — [README](https://github.com/machinewrapped/llm-subtrans)
- **Licence conflict.** The README says MIT, but GitHub reports NOASSERTION. — [README](https://github.com/machinewrapped/llm-subtrans)

**VideoLingo (Apache-2.0)**
- **ASR.** The local default is now Qwen3-ASR plus Qwen3-ForcedAligner for word-level recognition and alignment. WhisperX is optional and requires a manual install. MAI-Transcribe-2 and other hosted services are cloud options. — [README](https://github.com/Huanshere/VideoLingo)
- **Pipeline.** NLP and AI segmentation with configurable length limits, "Custom + AI-generated terminology" (custom_terms.xlsx), and "direct translation with optional reflection and natural rewriting". — [README](https://github.com/Huanshere/VideoLingo)
- **Dubbing.** GPT-SoVITS, CosyVoice2, F5-TTS, Fish Audio, Edge TTS and others. — [README](https://github.com/Huanshere/VideoLingo)
- **Stated limits.**
  - "Numbers and symbols may lack reliable word timings".
  - Mixed-language speech is not guaranteed.
  - Dubbing gives each speaker the same voice.
  - LLM output must match a JSON schema, and failures are logged to `output/gpt_log/error.json`.
  — [README](https://github.com/Huanshere/VideoLingo)

**VideoCaptioner (GPL-3.0)**
- **Pipeline.** ASR, then LLM semantic sentence segmentation (断句), then an LLM optimization/correction pass, then translation, then video synthesis. — [README](https://github.com/WEIFENG2333/VideoCaptioner)
- **ASR.** faster-whisper, whisper-cpp, Whisper API, and free Bijian and Jianying cloud endpoints, using word-level timestamps plus VAD. — [README](https://github.com/WEIFENG2333/VideoCaptioner)
- **Translation.** Context-aware LLM translation with a reflection mechanism (反思优化), or free Bing and Google. — [README](https://github.com/WEIFENG2333/VideoCaptioner)
- **Not documented in the README.** Glossary handling, line-count checks, and speed numbers. — [README](https://github.com/WEIFENG2333/VideoCaptioner)

**pyVideoTrans (GPL-3.0)**
- **ASR.** Faster-Whisper ("Recommended") is the default. WhisperX and Parakeet add alignment and diarization. Cloud options are Qwen3-ASR and ByteDance Volcano, "excellent for Chinese". — [README](https://github.com/jianchang512/pyvideotrans)
- **MT.** DeepSeek, ChatGPT, Claude, Gemini, Ollama, M2M100 offline, Google and Microsoft. — [README](https://github.com/jianchang512/pyvideotrans)
- **TTS.** Many engines, including F5-TTS, CosyVoice, GPT-SoVITS and Index-TTS cloning. Multi-role dubbing assigns a voice per speaker. — [README](https://github.com/jianchang512/pyvideotrans)
- **Workflow.** It pauses at the recognition, translation and dubbing stages for manual proofreading. — [README](https://github.com/jianchang512/pyvideotrans)
- **Packaging.** NVIDIA-only GPU support in the packaged build (CUDA 12.8, cuDNN 9.11). — [README](https://github.com/jianchang512/pyvideotrans)

**faster-whisper (benchmarks, RTX 3070 Ti, 13-minute audio, large-v2, beam 5)**
- openai/whisper fp16: 2m23s, 4708 MB.
- faster-whisper fp16: 1m03s, 4525 MB.
- faster-whisper `batch_size=8` fp16: 17s, 6090 MB.
- faster-whisper `batch_size=8` int8: 16s, 4500 MB.
- whisper.cpp with Flash Attention fp16: 1m05s.
— [README](https://github.com/SYSTRAN/faster-whisper)

**faster-whisper (CPU, small model, i7-12700K, 8 threads)**
- whisper.cpp: 2m05s.
- faster-whisper int8: 1m42s.
- faster-whisper `batch_size=8` int8: 51s.
- openai/whisper: 6m58s.
— [README](https://github.com/SYSTRAN/faster-whisper)

**faster-whisper (options)**
- distil-large-v3, batch 16, on YT Commons: faster-whisper took 25m50s with WER 13.527, versus transformers at 46m12s with WER 14.801. — [README](https://github.com/SYSTRAN/faster-whisper)
- `vad_filter` uses Silero and by default "only removes silence longer than 2 seconds". It is tunable through `vad_parameters`. — [README](https://github.com/SYSTRAN/faster-whisper)
- `BatchedInferencePipeline` is a drop-in replacement, and "Vad filter is enabled by default for batched transcription". — [README](https://github.com/SYSTRAN/faster-whisper)

**WhisperX (BSD-2)**
- It claims "70x realtime with large-v2" through batched faster-whisper. — [README](https://github.com/m-bain/whisperX)
- VAD preprocessing reduces hallucination and enables batching with "no WER degradation". — [README](https://github.com/m-bain/whisperX)
- Wav2vec2 forced alignment gives word timestamps. Whisper's own timestamps "can be off by several seconds". Tokens missing from the aligner dictionary, such as "2014." or "£13.60", cannot be aligned. Each language needs its own aligner. — [README](https://github.com/m-bain/whisperX)
- "Overlapping speech is not handled particularly well". "Diarization is far from perfect". — [README](https://github.com/m-bain/whisperX)
- The paper is arXiv 2303.00747 (INTERSPEECH 2023). — [README](https://github.com/m-bain/whisperX)

**stable-ts (MIT, archived)**
- "Archived by the owner on May 30, 2026 … Development on this repository is indefinitely paused." — [README](https://github.com/jianfch/stable-ts)
- **Anti-hallucination and timing options.**
  - `suppress_silence` (default True).
  - `nonspeech_skip` ("Reduce text and timing hallucinations in non-speech sections").
  - `vad` (Silero, threshold 0.35).
  - `denoiser` and `only_voice_freq`.
  - `gap_padding` (reduces early timestamps).
  - `avg_prob_threshold` (drops low-confidence segments).
  - `remove_repetition()`.
  - `max_instant_words` 0.5 (removes segments full of zero-length words).
  — [README](https://github.com/jianfch/stable-ts)
- **Line shaping and timing.**
  - The default regroup preset `da` (`cm_sp=.* /。/?/？_sg=.5_sp=,* /，++++50_sl=70_cm`) splits on CJK and Latin punctuation, gaps and length.
  - `refine()` mutes audio slices to tighten word boundaries.
  - `align()` times known text against audio.
  — [README](https://github.com/jianfch/stable-ts)

**SubGen (MIT)**
- **Inputs.** Webhook-driven from Plex, Jellyfin, Emby and Tautulli. It also acts as a Bazarr Whisper provider and exposes OpenAI-compatible endpoints. — [README](https://github.com/McCloudS/subgen)
- **Engine.** stable-ts plus faster-whisper. — [README](https://github.com/McCloudS/subgen)
- **Language detection.** `DETECT_LANGUAGE_OFFSET` skips past theme songs. `FORCE_DETECTED_LANGUAGE_TO` overrides repeated misdetection. — [README](https://github.com/McCloudS/subgen)
- **Late-start fix.** It corrects late-starting audio (for example Amazon WEB-DL) by detecting the offset with ffprobe and shifting timestamps. — [README](https://github.com/McCloudS/subgen)
- **Load and coverage.** `CONCURRENT_TRANSCRIPTIONS` 2 and `CLEAR_VRAM_ON_COMPLETE`. Skip rules avoid redoing covered media. — [README](https://github.com/McCloudS/subgen)
- **Caveat.** The README admits "occasionally … mistranslations or hallucinations based on background noise". — [README](https://github.com/McCloudS/subgen)

**Subtitle Edit (MIT)**
- Version 5.x is cross-platform:
  - Windows 10 22H2 or newer.
  - macOS 12 or newer, with a notarized dmg from 5.2.0.
  - Linux Flatpak, or tar.gz builds that need mpv and ffmpeg.
- The README lists speech-to-text, translation and OCR as optional online services but names no engines.
— [README](https://github.com/SubtitleEdit/subtitleedit)

### Inferences
- **Upgrade path for Baihe's transcription.** Switch to faster-whisper `BatchedInferencePipeline` with int8 and batch 8. In the published benchmark that is about 4x faster than unbatched (1m03s to 16-17s on large-v2), with VAD on by default. On CPU, batching roughly halves the time (1m42s to 51s for small int8).
- **CJK backends.** Projects aimed at CJK audiences have moved off Whisper as the default: VideoLingo uses Qwen3-ASR plus a forced aligner, and pyVideoTrans recommends Qwen3-ASR or Volcano for Chinese. Baihe should evaluate Qwen3-ASR and SenseVoice on its own drama audio.
- **Translation features worth copying from llm-subtrans.** Scene splitting by time gap, min/max batch sizes, an auto-growing terminology map seeded from the user glossary, `--reparse` of stored responses (no re-billing), and a dry-run `--preview`. These fit Baihe's id-keyed translation design. Baihe already avoids position matching, which is the desync source llm-subtrans fought.
- **stable-ts is archived.** Its regroup and silence techniques are worth reimplementing directly rather than depending on it.

### Gaps
- **Subtitle Edit specifics.** Whisper engines, OCR engines, waveform sync and the "Fix common errors" behaviour were not verified: the nikse.dk page returned no content and the GitHub README omits them.
- **Mechanisms not documented in READMEs.** VideoCaptioner's segmentation and reflection prompts, and llm-subtrans's exact line-numbering and summary format, would need the source code.
- **Bazarr, whisper-timestamped, Aegisub workflows, gpt-subtrans forks, SubtitleTranslator variants.** Only metadata was collected.
- **WhisperX's 2026 preprint.** The fetch summary mentions a "2026 context-aware batching preprint (arXiv 2608.31170)". This was not verified and should be checked before citing.

## Highly valued features and where they are done notably better

### Takeaway
The features users and maintainers emphasise most are:
- word-level timing through forced alignment;
- LLM re-segmentation into readable lines;
- glossary and terminology that persist across batches;
- human pause points for proofreading;
- per-speaker dubbing voices;
- resumable projects;
- stable live overlays with history and export.

### Cited Findings
- **Forced alignment.** WhisperX (wav2vec2) and VideoLingo (Qwen3-ForcedAligner) both add a separate aligner because Whisper's timestamps "can be off by several seconds". — [WhisperX](https://github.com/m-bain/whisperX); [VideoLingo](https://github.com/Huanshere/VideoLingo)
- **Semantic segmentation.** VideoCaptioner and VideoLingo both make LLM segmentation a first-class step before translation. — [VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner); [VideoLingo](https://github.com/Huanshere/VideoLingo)
- **Terminology.** llm-subtrans auto-builds a map. VideoLingo combines user and AI-generated terms. SimulStreaming offers a static terminology prompt for live use. — [llm-subtrans](https://github.com/machinewrapped/llm-subtrans); [VideoLingo](https://github.com/Huanshere/VideoLingo); [SimulStreaming](https://github.com/ufal/SimulStreaming)
- **Human review checkpoints.** pyVideoTrans pauses after recognition, translation and dubbing. — [pyVideoTrans](https://github.com/jianchang512/pyvideotrans)
- **Multi-speaker dubbing.** pyVideoTrans assigns a voice per speaker. VideoLingo does not. — [pyVideoTrans](https://github.com/jianchang512/pyvideotrans); [VideoLingo](https://github.com/Huanshere/VideoLingo)
- **Live overlay.** LiveCaptions-Translator's open requests are for stable reading mode (#278), full selectable history (#234) and SRT export (#169). — [Issues](https://github.com/SakiRinn/LiveCaptions-Translator/issues)
- **Media-server automation.** SubGen and Bazarr use skip rules so already-subtitled media is not redone. — [SubGen](https://github.com/McCloudS/subgen)

### Inferences
- Baihe's review UI and id-keyed translation already cover two of the strongest differentiators.
- The clearest gaps against peers:
  - a separate forced-alignment pass for CJK;
  - an auto-growing terminology map that feeds later batches;
  - a stable, committed-only live overlay with saved history and SRT export.

### Gaps
- No usage telemetry or user surveys ranking features were found. The ranking above is inferred from README emphasis and issue requests, not measured.
- Hardsub OCR and burn-in quality comparisons were not researched.

## Recurring failure modes and the fixes projects adopted

### Takeaway
Hallucination in silence and non-speech is the most documented failure. The consensus fix is VAD gating plus `condition_on_previous_text=False`, with post-filters (stable-ts) or a "bag of hallucinations" phrase filter as backups.

The other recurring failures, and their fixes:
- **Desync from LLM line merging.** Fixed by smaller batches and better prompting in llm-subtrans.
- **Timestamps on numbers and symbols.** Aligner dictionary gaps.
- **Late-start audio offsets.** SubGen's ffprobe shift.
- **Language auto-detect bias.**
- **Overlapping speakers.**

### Cited Findings
- **The non-speech hallucination study (arXiv 2501.11378).**
  - Whisper hallucinates on non-speech audio, and a set of hallucinations recurs across sound types.
  - The authors build a "bag of hallucinations" for post-processing and claim it reduces WER.
  - The abstract gives no magnitude.
  — [arXiv 2501.11378](https://arxiv.org/abs/2501.11378)
- **Practical guidance.** Use `vad_filter=True` with `condition_on_previous_text=False`. VAD keeps silence out, and disabling conditioning stops one repeated line from snowballing. — [Dograh blog](https://www.dograh.com/feeds/blog/whisper-hallucination-silence)
- **VAD caveats.** Search results report that Silero can miss near-zero-amplitude regions and can drop quiet or noisy speech. These came from search snippets of GitHub issues and blogs and were not individually opened. — [faster-whisper issue 843](https://github.com/SYSTRAN/faster-whisper/issues/843); [whisper-timestamped issue 74](https://github.com/linto-ai/whisper-timestamped/issues/74)
- **stable-ts countermeasures.** `nonspeech_skip`, `suppress_silence`, `avg_prob_threshold`, `remove_repetition()` and `gap_padding`. Disabling `condition_on_previous_text` makes the model "less prone to getting stuck in a failure loop". — [stable-ts](https://github.com/jianfch/stable-ts)
- **Line-merge desync.** llm-subtrans warns that larger batches raise desync risk, and its v0.2 prompting "greatly reduces desyncs" from merged lines. — [llm-subtrans](https://github.com/machinewrapped/llm-subtrans)
- **Malformed LLM output.** VideoLingo requires strict JSON and logs failures to `error.json`. — [VideoLingo](https://github.com/Huanshere/VideoLingo)
- **Timing on numbers and symbols.** WhisperX cannot align "2014." or "£13.60". VideoLingo warns that numbers and symbols may lack timings. — [WhisperX](https://github.com/m-bain/whisperX); [VideoLingo](https://github.com/Huanshere/VideoLingo)
- **Late-start offsets.** SubGen fixes them with an ffprobe offset shift. — [SubGen](https://github.com/McCloudS/subgen)
- **Language detection.** WhisperLiveKit notes that `--language auto` "tends to bias towards English". SubGen offers a detection offset and a forced language. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit); [SubGen](https://github.com/McCloudS/subgen)
- **Overlapping speech.** Neither Whisper nor WhisperX handles it well. WhisperLiveKit assigns each token to one speaker. — [WhisperX](https://github.com/m-bain/whisperX); [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- **Live connectivity and stability.** LiveCaptions-Translator #273 (an Ollama timeout behind a proxy) and #278 (unstable captions). — [Issues](https://github.com/SakiRinn/LiveCaptions-Translator/issues)

### Inferences
- **Fixes for Baihe's audio dramas.** These have long music and SFX stretches. Default VAD gating plus `condition_on_previous_text=False` should be on, and a known-hallucination phrase filter is cheap insurance. Typical targets are the stock "subscribe" and "subtitles by" phrases in CJK.
- **Aligner gaps.** Numbers and symbols are a known gap, so word timings for those tokens should fall back to interpolation.

### Gaps
- **Rate-limit failures.** Only llm-subtrans's `--ratelimit` was found. No user reports were collected.
- **Name inconsistency.** No direct user report was opened. Projects address it through terminology maps, but that evidence is indirect.
- **Hallucination study numbers.** The full numeric results of arXiv 2501.11378 were not read.

## Projects with measurable benchmarks

### Takeaway
Only faster-whisper publishes text benchmark tables (time, memory and one WER pair). WhisperLiveKit has benchmark charts as images. SimulStreaming and WhisperX point to papers. No project reviewed publishes BLEU or COMET for its subtitle translation, or CER for CJK.

### Cited Findings
- faster-whisper publishes GPU and CPU time and memory tables, plus distil-large-v3 WER (13.527 versus 14.801) on YT Commons. — [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- WhisperLiveKit has LibriVox benchmark charts on an H100, but only as images. — [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- SimulStreaming claims ~5x speed over WhisperStreaming and an IWSLT 2025 win, with figures in the paper. — [SimulStreaming](https://github.com/ufal/SimulStreaming)
- WhisperX says it has "no WER degradation" from VAD batching. Its paper is arXiv 2303.00747. Its benchmarking code is listed as a TODO. — [WhisperX](https://github.com/m-bain/whisperX)

### Inferences
- Baihe would need its own small CJK test set to compare ASR backends on drama audio (CER) and to compare MT settings (COMET or human review). No public benchmark covers that domain.

### Gaps
- The IWSLT 2025 SimulStreaming paper, the WhisperX paper tables, and the Qwen3-ASR and SenseVoice CER reports were not fetched.
- No BLEU or COMET for LLM subtitle translators was found.
