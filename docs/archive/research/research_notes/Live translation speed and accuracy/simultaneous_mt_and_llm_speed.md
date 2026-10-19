# Simultaneous (streaming) MT and fast LLM translation serving for zh/ja/ko to English (as of Oct 2026)

Scope note: about 22 search/fetch calls. Most primary simultaneous-MT results are for English to X (en-de, en-zh, en-ja) or Czech to English, not zh/ja/ko to English. No paper found evaluates streaming ja/ko to en specifically, so the transfer to this project's direction is an inference throughout.

## Policies and their reported quality-latency trade-offs

### Takeaway
2025-2026 state of the art for live speech translation is a cascade: streaming ASR, then an LLM driven by a read/write policy (LocalAgreement, or AlignAtt adapted to decoder-only LLMs), at about 2-4 s lag. The LLM cascades beat the organisers' baselines by large BLEU margins on en-X, but English to Chinese is mixed, and no study measures zh/ja/ko to en streaming.

### Cited Findings
- IWSLT 2025 simultaneous track: unsegmented speech to text with pretrained LLMs and speech encoders, scored by BLEU or neural metrics against StreamLAAL. — [BeaverTalk, arXiv 2505.24016](https://arxiv.org/pdf/2505.24016)
- BeaverTalk (Oregon State, IWSLT 2025): VAD segmenter, then Whisper Large V2, then Gemma 3 12B for simultaneous translation. en-de reaches 24.64 BLEU at StreamLAAL 1837.86 ms and 27.83 BLEU at 3343.73 ms. — [arXiv 2505.24016](https://arxiv.org/pdf/2505.24016) (via search snippet)
- CUNI (IWSLT 2025, Macháček and Polák) uses offline Whisper with AlignAtt for ASR, then EuroLLM-9B-Instruct with LocalAgreement for text MT. AlignAtt was not possible for the MT step because CTranslate2 does not expose attention weights. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
  - Reported dev-set results:
    - en-zh: 46.44 BLEU (baseline 23.96) at StreamLAAL 3698 ms.
    - en-ja: 34.69 BLEU (baseline 16.19) at 4654 ms. This is above the 4000 ms high-latency limit the paper cites, and the text does not address the discrepancy.
    - en-de: 38.46 BLEU (baseline 25.64) at 3934 ms.
    - cs-en low latency: 18.49 BLEU (baseline 15.16) at 1763 ms.
  - Setup details:
    - The prompt is a chat template with a "simultaneous interpreting" system prompt and one example pair.
    - The context buffer is trimmed to 4096 tokens. Trimming by "Segments" worked better for zh/ja; trimming by "Sentences" caused hallucinations there.
  - Latency was computationally unaware: simulated only, with no hardware stated. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
- MLLP-VRAIN (IWSLT 2025) used Whisper Large-V3-Turbo with NLLB-3.3B and reported 29.8 BLEU preliminary on IWSLT25Instruct. — [IWSLT 2025 program / search snippet](https://iwslt.org/2025/program)
- NAIST (IWSLT 2025) used a Whisper encoder, a DeCo projector and Qwen as an end-to-end speech LLM. — [ACL Anthology 2025.iwslt-1.39](https://preview.aclanthology.org/setup/2025.iwslt-1.39). In an earlier year NAIST chose LocalAgreement because it beat AlignAtt with their earlier models (search snippet, not verified in full text).
- AlignAtt4LLM (IWSLT 2026, arXiv 2606.03967): described as the first AlignAtt for decoder-only LLMs.
  - Pipeline: Qwen3-ASR-1.7B plus Qwen3-ForcedAligner-0.6B, then Gemma-4 E4B-it, all on vLLM.
  - Policy: 8 translation-specific attention heads are selected offline. Draft tokens are accepted only while their attention stays on source that has already arrived.
  - Results on the IWSLT 2026 dev set (21 talks):

    | Pair, latency regime | Baseline BLEU / XCOMET-XL | AlignAtt4LLM BLEU / XCOMET-XL | AlignAtt4LLM LongYAAL |
    |---|---|---|---|
    | en-de, ~2 s | 22.35 / 0.748 | 28.76 / 0.875 | 2.00 s |
    | en-it, ~2 s | 30.63 / 0.683 | 40.10 / 0.805 | 1.98 s |
    | en-zh, ~2 s | 40.85 / 0.750 | 36.01 / 0.743 | 1.95 s |
    | en-zh, <4 s | 43.85 / 0.795 | 39.86 / 0.778 | 3.27 s |

  - en-zh is worse than the baseline, which the authors partly blame on Gemma-4 E4B's Chinese.
  - Speed: one NVIDIA A40. ASR real-time factor (RTF) 0.34; Voxtral, at RTF 1.34, was rejected as not real-time. MT decoding median 25.4 ms/token on the vLLM path, against 63.7 ms in HF Transformers eager mode.
  - The authors argue LocalAgreement generally adds more latency than AlignAtt but give no head-to-head numbers. — [alphaXiv 2606.03967](https://www.alphaxiv.org/abs/2606.03967.md)
- Policy definitions:
  - AlignAtt stops writing when the most-attended source position falls behind a threshold near the end of the available input.
  - LocalAgreement commits the longest common prefix of two consecutive outputs, each made after a new source chunk arrives. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
- SeamlessStreaming (Meta, Dec 2023) uses the EMMA policy on Fleurs speech-to-text at the default threshold 0.5.

  | Direction | SeamlessStreaming BLEU | AL | LAAL | Offline SeamlessM4T v2 BLEU |
  |---|---|---|---|---|
  | X to en | 20.0 | 1.68 s | 2.20 s | 23.7 |
  | en to X | 19.7 | 1.98 s | 2.12 s | 22.2 |

  That is roughly 3-4 BLEU lost to streaming. — [arXiv 2312.05187](https://arxiv.org/pdf/2312.05187). CUNI's authors suspected the SeamlessM4T baseline of hallucinating on long-form input. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
- LLM-based simultaneous MT, prompting and fine-tuning:
  - TransLLaMa prompts with the system message, the partial source and the partial translation so far. It uses a modified wait-k: the model may only write once the source has at least k words. It is LoRA fine-tuned on a 4-bit base. — [review of TransLLaMa](https://themoonlight.io/review/transllama-llm-based-simultaneous-translation-system)
  - The Simul-LLM framework found that higher wait-k during fine-tuning improves generalisation across wait-k values at inference. "Simultaneous masking" fine-tuning (arXiv 2405.10443) claims to match or beat prefix-rec and conversational baselines across latencies (BLEU, chrF++, LAAL), with one exception at wait-1 en-ro. — [arXiv 2405.10443](https://arxiv.org/pdf/2405.10443); [Slator summary](https://tech.slator.com/can-large-language-models-do-simultaneous-machine-translation/)
  - Conversational SimulMT (IWSLT 2025) frames simultaneous MT as a multi-turn dialogue so the KV cache is reused across read/write steps. — [review](https://themoonlight.io/review/conversational-simulmt-efficient-simultaneous-translation-with-large-language-models). I could not fetch its numbers; the Anthology URL returned 404.
  - SimulS2S-LLM (ACL 2025) does simultaneous speech-to-speech with speech LLMs: trained offline, with a test-time policy. It reports about +3 ASR-BLEU at similar latency on CVSS against methods using the same data. — [arXiv 2504.15509](https://arxiv.org/abs/2504.15509)

### Inferences
- For a transcript already segmented into lines by ASR, the cheapest workable policy with a cloud or local LLM is LocalAgreement at the segment level, or a simple "translate once the line is final" rule. AlignAtt needs attention access, which hosted APIs and Ollama do not expose.
- Reported LLM-cascade lags cluster around 2-4 s on the LAAL family of metrics. Expect the low-latency regime to cost BLEU and COMET; the AlignAtt4LLM en-de numbers show roughly +4 BLEU from ~2 s to ~3.5 s.
- SOV source languages (ja/ko) put the verb last, so word-level wait-k on ja/ko to en should need longer waits than en-de. No measured ja/ko to en simultaneous numbers were found to quantify this.

### Gaps
- I could not retrieve the official IWSLT 2025 or 2026 Findings papers, so track winners and rankings are unverified.
- No zh/ja/ko to en streaming results were found (all LLM-cascade numbers are en to X or cs to en).
- Numeric results for TransLLaMa, Conversational SimulMT and the Simul-LLM papers were not retrieved.
- Google's 2025-2026 streaming research (beyond the 2020-2021 retranslation work below) was not found.

## Retranslation vs commit: flicker and stability

### Takeaway
Google's retranslation line of work shows that inference-only tricks nearly eliminate flicker while keeping quality competitive with streaming models: mask the last k tokens, and bias the search towards the previous output. LocalAgreement is the commit-based equivalent used with LLMs.

### Cited Findings
- Erasure is the stability metric: the number of tokens deleted from the previous output to produce the next one. Google evaluates erasure alongside lag and BLEU, so a system cannot win on lag by constantly rewriting. — [arXiv 1912.03393](https://arxiv.org/pdf/1912.03393); [Google Research blog, Jan 2021](https://research.google/blog/stabilizing-live-speech-translation-in-google-translate/)
- Mask-k hides the last k target tokens of each retranslation, because the tail depends on source that has not arrived yet. A mask of 10 worked well but delays short sentences. Dynamic masking, which predicts k per step, gives a better flicker-latency curve than a fixed k. — [arXiv 2006.00249](https://arxiv.org/pdf/2006.00249)
- Biased beam search keeps the new hypothesis close to the previous prefix. It reduces flicker with little latency cost but can hurt quality. — [arXiv 2006.00249](https://arxiv.org/pdf/2006.00249)
- Google reports that mask-k plus biasing, at inference time with no special training, nearly eliminates erasure. With a small erasure budget, retranslation beats the zero-flicker streaming models MILk and wait-k. These are Google's own claims. — [Google Research blog](https://ai.googleblog.com/2021/01/stabilizing-live-speech-translation-in.html)
- A streaming joint ASR+MT model uses mask-k plus a constrained-decoding parameter F that limits how many tokens may change in the next retranslation. — [arXiv 2101.09149](https://arxiv.org/pdf/2101.09149)
- LocalAgreement as a commit rule: emit only the prefix on which two consecutive hypotheses agree. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)

### Inferences
- With an LLM, the practical equivalents are:
  - Show partial translations in a "tentative" style and commit only the LocalAgreement prefix, or commit when the ASR line is finalised.
  - Hide the tail (mask-k) of tentative text.
  - Pass the committed English prefix back to the model as a forced prefix (assistant prefill), or tell it not to alter committed text. This is the LLM analogue of biased search.
- Revising captions that are already committed should be rare and explicit, for example a later context-aware pass over finished lines, rather than continuous.

### Gaps
- No 2024-2026 measurements of erasure for LLM-based retranslation were found.

## Context and accuracy: windows, glossaries, pronouns, honorifics

### Takeaway
Document-level context helps LLM translation overall, but measured gains do not reliably reach pronouns. TranslateGemma's own human evaluation found a ja to en regression caused by named-entity mistranslation, which supports injecting a glossary and character names.

### Cited Findings
- Karpinska and Iyyer (2023), literary translation across 18 pairs including Japanese: paragraph-level (discourse) LLM translation makes fewer mistranslations, grammar errors and stylistic inconsistencies than sentence-level. Critical errors, including omissions, persist. — [arXiv 2304.03245](https://arxiv.org/pdf/2304.03245)
- Mohammed and Niculae (MT Summit 2025) tested nine LLMs with perturbation and attribution analysis. LLMs' better document-level translation over encoder-decoder models is not reflected in pronoun translation. The authors recommend context-aware fine-tuning focused on the relevant parts of the context. — [UvA record](https://dare.uva.nl/id/b11cc1d6-8078-47cf-919d-5d5bfae872ec). Whether Japanese was included is unverified.
- An IPSJ study of GPT-4o on Japanese to English inter-sentential zero anaphora (omitted arguments) found GPT-4o can still produce natural translations when it fails to resolve the anaphora. — [IPSJ record 2002814](https://ipsj.ixsq.nii.ac.jp/records/2002814) (year unverified)
- Zero-pronoun translation evaluated for Chinese in LLM document-level in-context learning (2024). — [arXiv 2406.07081](https://arxiv.org/pdf/2406.07081)
- TranslateGemma human MQM evaluation (lower is better), Japanese to English:

  | Model | MQM |
  |---|---|
  | TranslateGemma 27B | 13.4 |
  | TranslateGemma 12B | 15.7 |
  | Gemma 3 27B (base) | 11.6 |

  The authors trace the regression to named-entity mistranslation; other error categories improved. — [TranslateGemma Technical Report, arXiv 2601.09012](https://arxiv.org/pdf/2601.09012)
- CUNI found that how earlier context is trimmed matters for zh/ja: trimming by segments worked, trimming by sentences caused hallucinations. — [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)

### Inferences
- A short sliding window of earlier lines, plus explicit speaker and gender hints, is the most direct fix for ja/ko pro-drop. The literature shows document context does not fix pronouns by itself, so the hints should be explicit.
- Named entities are where translation-specialised models are weakest (TranslateGemma ja to en), so glossary and character-name injection is likely worth more than raw extra context.

### Gaps
- No measured study quantifies how many previous lines help ja/ko to en specifically.
- No measured study of honorific handling (-san, -nim, keigo) in LLM MT was found.
- Speaker or gender conditioning gains for CJK to en were not measured in the sources found.

## Speed of the LLM step

### Takeaway
At batch size 1, local decode speed for a 7-8B 4-bit model on a modern consumer GPU is roughly 75-300 tok/s, depending on the GPU. That is enough for short subtitle lines. Prompt caching on cloud APIs cuts time to first token and cost for a fixed system prompt and glossary prefix. Translation-specialised 4-12B models (Hunyuan-MT-7B, TranslateGemma) rival much larger general models on automatic metrics.

### Cited Findings
- **Translation-specialised models**
  - Hunyuan-MT-7B (Tencent, Sep 2025): claims first place in 30 of 31 WMT25 general-task pairs. The authors also report a Chimera-7B variant. Per-language ja/ko scores are not broken out.

    XCOMET-XXL, XX to EN:

    | Model | Score |
    |---|---|
    | Gemini-2.5-Pro | 0.9432 |
    | GPT-4.1 | 0.9405 |
    | Claude-Sonnet-4 | 0.9390 |
    | DeepSeek-V3 | 0.9319 |
    | Qwen3-235B-A22B | 0.9292 |
    | Hunyuan-MT-Chimera-7B | 0.9132 |
    | Hunyuan-MT-7B | 0.9018 |
    | Tower-Plus-72B | 0.9002 |
    | Seed-X-PPO-7B | 0.8442 |
    | Google Translator | 0.7761 |

    On WMT24pp, Hunyuan-MT-7B scores 0.8585 and Chimera 0.8787, above Gemini-2.5-Pro at 0.8250.

    Human 0-4 rating on zh to en: Hunyuan-MT-7B 3.258. On the zh/en average, Hunyuan-MT-7B scores 3.189 against Gemini-2.5-Pro 3.223 and Google Translator 2.344. — [Hunyuan-MT Technical Report, arXiv 2509.05209](https://arxiv.org/html/2509.05209v2)
  - TranslateGemma (Google, released 15 Jan 2026 in 4B, 12B and 27B): a Gemma 3 base with SFT, then RL against MetricX-QE and AutoMQM reward models.

    WMT24++ average across 55 pairs, MetricX (lower is better) / COMET22:

    | Size | Gemma 3 | TranslateGemma |
    |---|---|---|
    | 27B | 4.04 / 83.1 | 3.09 / 84.4 |
    | 12B | 4.86 / 81.6 | 3.60 / 83.5 |
    | 4B | 6.97 / 77.2 | 5.32 / 80.1 |

    MetricX by language pair (English to CJK; no into-English CJK rows were found in the table):

    | Pair | TranslateGemma 27B | 12B | 4B | Gemma 3 27B | 12B | 4B |
    |---|---|---|---|---|---|---|
    | en to ja | 3.53 | 3.82 | 4.44 | 4.11 | 4.30 | 5.09 |
    | en to ko | 2.81 | 2.97 | 3.93 | 3.43 | 3.79 | 4.72 |
    | en to zh | 1.86 | 2.07 | 2.66 | 2.47 | 2.61 | 3.27 |

    Human MQM: ja to en regressed against Gemma 3 27B (see the previous section). en to ko MQM: TranslateGemma 27B 3.1, 12B 4.6, Gemma 3 27B 3.8. — [arXiv 2601.09012](https://arxiv.org/pdf/2601.09012); release date from [Gemma releases page](https://ai.google.dev/gemma/docs/releases?hl=en)
  - Seed-X-PPO-7B (ByteDance) appears only as a baseline in the Hunyuan report, below Hunyuan-MT-7B on every column. — [arXiv 2509.05209](https://arxiv.org/html/2509.05209v2)
- **Local GPU throughput (llama.cpp CUDA scoreboard)**
  - Llama 2 7B Q4_0, `llama-bench -ngl 99`, thread started 1 Aug 2025. Prompt processing is pp512 and generation is tg128, both in tok/s with flash attention:

    | GPU | Prompt (pp512) | Generation (tg128) |
    |---|---|---|
    | RTX 5090 | ~14,970 | ~300 |
    | RTX 4090 | ~14,771 | ~189 |
    | RTX 3090 | ~5,560 | ~162 |
    | RTX 5070 Ti | ~8,420 | ~182 |
    | RTX 4070 Ti SUPER | ~7,612 | ~133 |
    | RTX 3060 | ~2,408 | ~77 |

  - Entries come from different commits, drivers and power limits. — [llama.cpp discussion #15013](https://github.com/ggml-org/llama.cpp/discussions/15013)
  - Third-party pages claiming Qwen3-8B Q4 on an RTX 4090 at ~141 tok/s at 4K context, falling to ~34 tok/s at 128K, look low-rigor and are not used. — [markaicode](https://markaicode.com/benchmarks/llamacpp-qwen-3-rtx-4090-throughput-benchmark/)
- **Serving stacks**
  - vLLM decoding of Gemma-4 E4B for simultaneous MT on an A40: 25.4 ms/token median (~39 tok/s), against 63.7 ms in Transformers eager mode. — [arXiv 2606.03967](https://www.alphaxiv.org/abs/2606.03967.md)
  - vLLM automatic prefix caching is enabled with `enable_prefix_caching=True`. It reuses hash-identified KV blocks and does not change outputs. — [vLLM docs v0.8.2](https://docs.vllm.ai/en/v0.8.2/design/v1/prefix_caching.html)
- **Anthropic prompt caching (current docs, fetched Oct 2026)**
  - Minimum cacheable prefix depends on the model: 512 tokens (Opus 5.5, Sonnet 5.5, Haiku 5.5 and the other 5.x models), 1,024 (Sonnet 5, Sonnet 4.x and others), 2,048 or 4,096 for some (Haiku 4.5 needs 4,096). Shorter prompts silently go uncached.
  - TTL is 5 minutes by default, refreshed on each read, with a 1-hour option. Writes cost 1.25x (5 min) or 2x (1 h); reads cost 0.1x, or 0.05x on Opus 5.5 and Sonnet 5.5. Up to 4 breakpoints are allowed.
  - Pre-warming with `max_tokens: 0` removes the cache-miss penalty on the first real request.
  - The cache is invalidated by changes to tools, by thinking or effort settings (model-specific), and by any change in the prefix. The docs state that hits reduce time to first token but give no number. — [Claude prompt caching docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching)
- **OpenAI prompt caching (current docs)**
  - Automatic, enabled by default. The minimum prefix is 1,024 tokens on GPT-5.6 and later; earlier models use fixed-interval breakpoints.
  - Reads cost 0.1x the uncached rate on GPT-5.6 and later, and writes 1.25x.
  - A prefix stays reusable for at least 30 minutes on GPT-5.6 and later; earlier models offer `in_memory` (5-10 minutes) or `24h`.
  - Pre-warming reduces time to first token. `prompt_cache_key` helps route requests to the same cache. No latency number is given. — [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)

### Inferences
- For 1-10 short lines (~20-40 output tokens each, so ~50-400 output tokens per batch), local 7-8B Q4 generation at 75-300 tok/s takes roughly 0.2-5 s. Prompt processing of a 2K-token glossary-plus-context prompt is well under a second on a 3060-class GPU and above.
- Keep the system prompt, glossary and style guide as a stable prefix, with variable lines last, so that llama.cpp/Ollama slot reuse, vLLM APC or cloud prompt caching applies. The cloud minimums (512-1,024+ tokens) mean a tiny prompt will not cache at all.
- Disable thinking or reasoning for live use. Thinking settings also invalidate the Anthropic cache when they change.
- Hunyuan-MT-7B or TranslateGemma 4B/12B are plausible local defaults for zh to en. For ja to en, TranslateGemma's named-entity regression argues for glossary injection or a general instruct model.

### Gaps
- No primary measured cloud time-to-first-token or tok/s numbers were found for Claude, OpenAI or Gemini at batch size 1. Vendors give only qualitative claims, so these need measuring in-app.
- Gemini context caching docs were not fetched.
- Ollama and llama.cpp cache-reuse specifics (`cache_prompt`, `--cache-reuse`, `keep_alive`) were not verified from docs.
- Speculative decoding speedups at batch 1 were found only from unsourced mirrors ("2-3x"), so they are not reported.
- MLX (Apple) throughput was not found; the llama.cpp Apple thread was not opened.
- No independent ja/ko to en numbers were found for Qwen3, Gemma 3/4, NLLB, MADLAD or Seed-X.
- Seed-X's own report and its WMT25 numbers were not retrieved.
- TranslateGemma license terms were not checked (the arXiv paper itself is CC BY 4.0).

## Evaluation of live translation quality

### Takeaway
Current research evaluates with neural metrics (XCOMET-XL/XXL, MetricX-24, COMET22, CometKiwi QE) plus BLEU and chrF, latency with StreamLAAL or LongYAAL (computation-aware and -unaware), and erasure for flicker. Human MQM spot checks catch failures that automatic metrics miss.

### Cited Findings
- IWSLT 2026 evaluates with BLEU, chrF and XCOMET-XL, plus LongYAAL in computation-unaware and computation-aware variants, using the Simulstream toolkit. — [arXiv 2606.03967](https://www.alphaxiv.org/abs/2606.03967.md)
- IWSLT 2025 used StreamLAAL. — [arXiv 2505.24016](https://arxiv.org/pdf/2505.24016); [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)
- WMT24++ (55 languages) uses MetricX-24 and MetricX-24-QE (reference-free) as its main metrics. — [arXiv 2502.12404](https://arxiv.org/pdf/2502.12404)
- Reference-free QE scores (CometKiwi, MetricX-QE) are reported alongside reference-based XCOMET in the Hunyuan-MT report. — [arXiv 2509.05209](https://arxiv.org/html/2509.05209v2)
- Automatic metrics and human MQM can disagree in direction. TranslateGemma improved on MetricX for every one of 55 pairs, yet human MQM showed a ja to en regression. — [arXiv 2601.09012](https://arxiv.org/pdf/2601.09012)
- Erasure (flicker), lag and quality should be reported together. — [Google Research blog](https://research.google/blog/stabilizing-live-speech-translation-in-google-translate/)

### Inferences
- A cheap regression harness would:
  - Keep a fixed set of a few hundred zh/ja/ko lines with references.
  - Score with chrF (no model needed), plus a QE metric such as CometKiwi or MetricX-QE when a GPU is available.
  - Log lag (line final to translation shown) and erasure for live mode.
  - Add periodic human or LLM-judge spot checks focused on names, pronouns and honorifics, which automatic metrics under-weight per the TranslateGemma finding.

### Gaps
- No measured comparison of LLM-as-judge against COMET for CJK to en live subtitles was found.
- No published guidance was found on sample sizes for detecting regressions.
