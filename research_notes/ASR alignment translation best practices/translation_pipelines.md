# LLM subtitle translation pipelines, zh/ja/ko to English (as of 2026-10-11)

Scope note: research was done on 2026-10-11 within a budget of about 20 tool calls. Model names and prices below (for example Claude Opus 5.5, Gemini 3.8 Flash, DeepSeek V4.1-Flash) were read from vendor pages on that date. They come after the assistant's June 2026 knowledge cutoff, so treat them as verified-on-fetch, not from memory. arXiv IDs starting 2609.* are September 2026 preprints and have not been peer reviewed.

## Pipeline structure: context, batching, glossary, speakers, honorifics, output format, caching, batch pricing, thinking, passes

### Takeaway
The strongest documented gains come from (1) a persistent memory or glossary of terms and character names carried across batches and episodes, and (2) scene-sized batches with surrounding context. A second refine or review pass gives modest gains, mostly in fluency, style and terminology, not adequacy. Turning on "thinking" for plain translation has little or no measured benefit. Prompt caching, batch APIs and off-peak pricing are cost levers with no quality effect. Id-keyed output with validate-and-retry is standard practice in tools, but I found no controlled study of it.

### Cited Findings
**Context, memory and batching**
- Breaking Babel / SMART (Amazon + UIUC, arXiv 2609.38660v2, 1 Oct 2026) is a long-form subtitle multi-agent system. Its components are SRT parsing with neighbouring lines, scene metadata and a series profile; regrouping of interrupted sentences that are then split back to their timestamps; a series memory of terminology, character profiles and an idiom bank that persists across episodes; routing to five translator styles (faithful, natural, expressive, length-aware, colloquial); a judge that scores 0-10, with a rescue pass below 8.0; and a consistency pass over overlapping 5, 10 and 20-line windows with 50% overlap — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)
- SMART ablations (mean SubMQM Overall penalty added when a component is removed; lower is better; the full system's mean is about 0.37-1.11): memory +0.78, mixture-of-agents +0.61, contextual retrieval and idiom bank +0.56, sliding-window consistency pass +0.47, self-evolution +0.36, dynamic router +0.27. Memory (terminology and character profiles) is the largest single contributor — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)
- SMART's headline result: locale→English mean Overall falls from 0.41 (TransAgent) to 0.37, 10% relative. zh→en is 0.44 vs 0.50. Single-call baselines are much worse on en→locale (GPT-5.5 1.44, Claude Opus 4.8 1.55 vs SMART 1.11). Caveats: the benchmark source is English OpenSubtitles, there is no Japanese locale, the judge is an LLM (SubMQM), the cost appendix was not visible in what I fetched, and the paper is a preprint — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)
- Backbone matters less with scaffolding: with Gemma 3 4B as backbone, en→zh Overall improves from 1.46 (single call) to 0.67 under SMART; with DeepSeek-V3.2 it goes from 0.93 to 0.53 — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)
- A context-aware framework for translated conversations (arXiv 2412.04205, v2 June 2025) found that the best context window size differs by language pair and that adding more dialogue turns did not hurt — [arXiv 2412.04205](https://arxiv.org/html/2412.04205v2)
- Context via a summary of earlier dialogue plus recent turns (en-ko customer-support chat, 2024) — [arXiv 2410.16775](https://arxiv.org/abs/2410.16775)
- llm-subtrans (formerly gpt-subtrans) splits subtitles into scenes by a time gap (`--scenethreshold`, seconds between lines) and into batches bounded by `--minbatchsize` and `--maxbatchsize`. It states: "Larger batches are more cost-effective but increase the risk of the AI desynchronising, triggering expensive retries." Context comes from batch and scene summaries, a movie name and description, a names list, and a terminology map (`--build-terminology-map`) "provided to subsequent batches so that consistent translations can be used throughout" — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)
- VideoLingo config: `reflect_translate: true`, `summary_length: 8000` (a whole-video summary used as context), `max_split_length: 20` (rough-cut words), `max_length: 75` characters per subtitle line, `target_multiplier: 1.2`, `max_workers: 4`, and `pause_before_translate` to edit the extracted terminology table before translating — [VideoLingo config.yaml](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/config.yaml)
- VideoLingo advertises a 3-step "Translate-Reflect-Adaptation" process with WhisperX alignment and NLP segmentation. Known limitations are weak speaker separation and dubbing timing issues — [VideoLingo README](https://github.com/Huanshere/VideoLingo)

**Id-keyed vs positional output; line-count mismatch**
- pyvideotrans 2.52 switched AI translation from line-by-line to sending the whole SRT, to address "mismatched line counts between the original content and the translated results" — [pyvideotrans prompt docs](https://en.pyvideotrans.com/en/prompt)
- Cerlancism/chatgpt-subtitle-translator retries a batch on any line-count mismatch. In timestamp mode it retries only on time-boundary mismatch, and the output entry count may differ from the input — [repo](https://github.com/Cerlancism/chatgpt-subtitle-translator)
- TransLora (aimed at small local models) validates each batch for block count, the model's own numbering and unchanged timestamps, then retries and recursively splits batches that keep failing, to catch "the skip/merge failures LLMs make on long .srt files" — [TransLora](https://github.com/SloMR/TransLora)
- Subtitle Edit issue #15602 (open about a week before 2026-10-11): ChatGPT's default merge mode loses subtitle-entry boundaries. The proposed patch still depends on the model preserving line boundaries — [SubtitleEdit #15602](https://github.com/SubtitleEdit/subtitleedit/issues/15602)
- An SRT translate-and-validate tool's prompt forbids merging and requires one source ID to map to exactly one target ID, which reflects cascading index shifts as the common failure — [VjayC/SRT-Subtitle-Translator-Validator](https://github.com/VjayC/SRT-Subtitle-Translator-Validator)
- llm-subtrans v0.2 introduced a prompting approach that "greatly reduces desyncs caused by GPT merging together source lines" — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)

**Two-pass, refinement and self-correction**
- "What Does LLM Refinement Actually Improve?" (Tan et al., Amazon, arXiv 2605.13368, 13 May 2026; 9 LLMs, 7 language pairs, literary documents). The best recipe is document-level translation followed by segment-level refinement. A simple general refine prompt beat error-specific prompts and evaluate-then-refine schemes. In human evaluation, gains were mainly in fluency, style and terminology, and adequacy gains were "limited and less consistent". Refinement pulls outputs toward the refiner's own distribution rather than doing targeted error repair — [arXiv 2605.13368](https://arxiv.org/abs/2605.13368)
- TEaR (estimate-then-refine using MQM-style self-annotation): average +0.38 COMET-Kiwi and +0.42 xCOMET on WMT22, which are small gains — [arXiv 2402.16379](https://arxiv.org/html/2402.16379v2)
- A two-stage "diagnose then repair" design with a separate evaluator and post-editor beat one-stage judge-and-refine on COMET-22 and CometKiwi across 7 LLMs and 7 languages. It argues one-stage refine causes paraphrastic drift (preprint, Sept 2026) — [arXiv 2609.22793](https://arxiv.org/html/2609.22793)
- LLM-only refinement raised MQM error-free segments by just 2.2%, vs 12.2% for human post-editing — [arXiv 2410.11056](https://arxiv.org/pdf/2410.11056)
- A review of the MAATS multi-agent MQM system points out that its comparison lacked a budget-matched single-agent baseline (multi-agent uses more calls) — [Pith review of 2505.14848](https://pith.science/paper/2505.14848)

**Thinking/reasoning on vs off**
- "thinking tokens" do not help LRMs translate: across 10 directions there was no significant benefit, with non-thinking slightly better on MetricX, usually by less than 0.5 point — [arXiv 2510.11919](https://arxiv.org/html/2510.11919)
- Counter-evidence: LRMs win on complex, long and high-difficulty texts by COMET and MQM analysis — [arXiv 2505.19987 (Pith)](https://pith.science/paper/2505.19987). CoT helps context-aware translation, more so for stronger models ("wise get wiser") — [arXiv 2510.18077](https://arxiv.org/html/2510.18077v1)
- llm-subtrans defaults OpenAI reasoning effort to "low" — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)

**Speakers, pro-drop and honorifics**
- A Korean→English discourse test set (COLING 2025, 600 instances, 6 phenomena including zero anaphora) found that GPT-4o still struggles with some phenomena. It introduced the AZPT (zero-pronoun accuracy) metric — [ACL 2025.coling-main.110](https://aclanthology.org/2025.coling-main.110.pdf)
- A Chinese zero-pronoun study found that AZPT's automatic alignment had low validity on manual inspection — [arXiv 2605.31056](https://arxiv.org/pdf/2605.31056)
- Japanese→English zero-pronoun evaluation dataset (subject, object and possessive omission) — [LREC 2020](https://aclanthology.org/2020.lrec-1.447/)
- JP-TL-Bench (bidirectional ja-en, anchored pairwise LLM judging) notes frequent formality errors in EN→JA — [arXiv 2601.00223](https://arxiv.org/pdf/2601.00223)
- WMT25 lists "gender choice/agreement" among the remaining robustness failures of state-of-the-art systems — [WMT25 findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- SMART carries character profiles in memory, and removing memory is the largest ablation hit (above) — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)

**Cost levers (vendor pages, fetched 2026-10-11)**
- Anthropic Batch API: "50% discount on both input and output tokens". Caching multipliers: 5-minute write 1.25x, 1-hour write 2x, read 0.1x base (0.05x on Opus 5.5 and Sonnet 5.5). Batch and caching discounts stack. Prices per MTok input/output: Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Sonnet 4.6 $3/$15, Haiku 5.5 $0.10/$0.50 for prompts up to 100k tokens, Haiku 4.5 $1/$5. Claude 4.7+ uses a tokenizer that yields about 30% more tokens for the same text — [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- DeepSeek: `deepseek-flash` (V4.1-Flash) per 1M tokens off-peak/peak: cache hit $0.003/$0.006, miss $0.15/$0.30, output $0.60/$1.20. `deepseek-v4-pro`: miss $0.66/$1.32, output $1.98/$3.96. "Off-peak rates are half of the peak rates". Peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday. No effective date is shown — [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing). Third-party coverage disagrees on whether the August 2026 change was a cut or a rise — [aipricing.guru](https://www.aipricing.guru/news/deepseek-api-pricing-update-peak-off-peak-august-2026/)
- Gemini per 1M tokens, standard input/output and batch: 3.8-flash $0.75/$3.75 (batch $0.375/$1.875), 3.5-flash-lite $0.30/$2.50, 3.1-flash-lite $0.25/$1.50, 3.1-pro-preview $2/$12, 2.5-flash-lite $0.10/$0.40. Context caching costs about 0.1x input plus storage of $1/1M tokens/hour. The page says many prices double on 1 Jan 2027 — [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)

### Inferences
- Evidence-backed: persistent glossary and character memory, scene-level context with a series or episode summary, and a sliding-window consistency pass (SMART ablations; llm-subtrans and VideoLingo designs). Partly evidenced: one refine pass, which helps fluency and terminology but not meaning, and works best done segment-wise after document-level translation. Weak or folklore for plain translation: thinking mode, self-consistency or majority voting (I found no subtitle-specific evidence), and elaborate error-typed critique prompts (beaten by a simple refine prompt in arXiv 2605.13368).
- Id-keyed output plus a count/id validator plus retry-with-smaller-batch is the convergent engineering answer across TransLora, chatgpt-subtitle-translator and llm-subtrans. Positional mapping is the documented failure, as in the SubtitleEdit merge bug.
- Rough cost model (my arithmetic, not sourced): a 1-hour drama has about 700-1,000 lines. At about 30-40 source tokens and 15-25 English tokens per line, plus about 2-4x input overhead for context and glossary, translation costs well under US$0.10 per hour on DeepSeek Flash or Gemini Flash-Lite, and roughly $0.20-$1 on Sonnet-class models. A refine pass roughly doubles that. Batch, off-peak and caching discounts cut it by about 50% further. For non-premium models the deciding factor is quality, not cost.

### Gaps
- No controlled study found comparing id-keyed JSON with positional or plain SRT output for drop and merge rates.
- No zh/ja/ko→en subtitle study on self-consistency (sample-and-vote).
- Honorifics policy for ja/ko (keep -san or -nim vs localize) has no empirical study; it is a style decision (see the last section).
- Not researched within budget: LLPlayer, Immersive Translate, DeepL and Google subtitle features, Netflix vendor (Originator/LSP) workflows.

## Reference implementations and their documented pitfalls

### Takeaway
Open-source tools converge on scene- or gap-based batching, a summary plus a terminology map, an optional reflect pass, and validate-and-retry on count or numbering mismatches. The recurring pitfalls are merged or skipped lines (index shift), desync that grows with batch size, and weak speaker separation.

### Cited Findings
- VideoLingo: Translate-Reflect-Adapt, terminology extraction with a manual pause, Netflix-style single-line subtitles, max 75 characters, weak speaker separation — [README](https://github.com/Huanshere/VideoLingo), [config](https://raw.githubusercontent.com/Huanshere/VideoLingo/main/config.yaml)
- llm-subtrans: scene and batch splitting, summaries, terminology map, names, custom instructions, a project file for resume, `--reparse` of saved responses, many providers (OpenRouter by default, Gemini, OpenAI, DeepSeek, Anthropic, Mistral, OpenAI-compatible local servers such as LM Studio). The README remarks that translation is "not Claude's strongest suit" (a maintainer opinion, undated) — [llm-subtrans](https://github.com/machinewrapped/llm-subtrans)
- llm-subtrans PR #451 adds speaker-aware line assembly for its transcription path — [PR #451](https://github.com/machinewrapped/llm-subtrans/pull/451)
- pyvideotrans: sends the whole SRT (since v2.52) to avoid line-count mismatch, with an editable prompt — [docs](https://en.pyvideotrans.com/en/prompt)
- rockbenben/subtitle-translator: whole-season translation of srt/ass/vtt/lrc, 27 LLM providers, "timing untouched" — [repo](https://github.com/rockbenben/subtitle-translator)
- yigitkonur/subtitle-llm-translator: dynamic context windows and DeepL fallback — [repo](https://github.com/yigitkonur/subtitle-llm-translator)
- The same line-count mismatch bug class appears in comic translation (BallonsTranslator #861) — [issue](https://github.com/dmMaze/BallonsTranslator/issues/861)
- Subtitle Edit merge-mode boundary loss — [#15602](https://github.com/SubtitleEdit/subtitleedit/issues/15602)

### Inferences
- Sending a whole SRT (pyvideotrans) trades fewer boundary errors for higher risk on long files. The batch-plus-validator approach (TransLora) is the more robust pattern for small local models.

### Gaps
- Not verified this session: LLPlayer, Immersive Translate, DeepL and Google subtitle products, Netflix/LSP pipelines.

## Model choice in 2026 and benchmark evidence

### Takeaway
WMT25 has no zh/ja/ko→English direction. Its closest CJK evidence is en→zh, en→ja, en→ko and ja→zh, where frontier LLMs (Gemini 2.5 Pro, Claude 4, GPT-4.1, DeepSeek-V3) and Hunyuan-MT are top and often beat human references. Subtitle-specific evidence (SMART) shows scaffolding narrows backbone gaps. Specialist open models (TranslateGemma) beat same-size Gemma 3 by a clear MetricX margin.

### Cited Findings
- WMT25 General MT: 16 human-evaluated pairs including Japanese→Simplified Chinese, English→Chinese, English→Japanese and English→Korean (new), but no CJK→English. Evaluation used ESA, with MQM for en→ko and ja→zh. Domains included a speech domain (ASR input), dialogue and social; test sets were document-level and difficulty-sampled — [WMT25 findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- WMT25 headline findings: "Automatic scores are biased" (Shy-hunyuan-MT topped automatic rankings in all but one pair but scored much lower with humans). Human references were in the winning cluster for only 6 of 15 pairs. Best overall was Gemini 2.5 Pro, in the top cluster for 14 pairs. "Speech domain was the most challenging" (ASR errors). Systems still struggle with terminology and gender agreement — [WMT25 findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- WMT25 human scores per search snippet (not re-verified in the PDF table): en→zh Claude-4 86.9, DeepSeek-V3 85.0, GPT-4.1 84.0, Gemini-2.5-Pro 83.8, human reference 82.1. en→ja Gemini-2.5-Pro 90.7, GPT-4.1 89.5, Claude-4 88.8, human 82.8 — [WMT25 findings](https://aclanthology.org/anthology-files/pdf/wmt.real/2025.wmt-1.1.pdf); preliminary automatic ranking — [arXiv 2508.14909](https://arxiv.org/pdf/2508.14909)
- TranslateGemma (arXiv 2601.09012) WMT24++ MetricX (lower is better), for 27B/12B/4B vs Gemma 3: en→ja 3.53/3.82/4.44 vs 4.11/4.30/5.09; en→ko 2.81/2.97/3.93 vs 3.43/3.79/4.72. The zh row was not retrieved, and these are en→X, not X→en — [TranslateGemma report](https://arxiv.org/pdf/2601.09012)
- Subtitle-specific (SMART paper, MuSC benchmark): baselines included NLLB-3.3B, MADLAD-10B, Google Translate, GPT-4o, Qwen-Max, DeepSeek-V3.1/R1, GPT-5 and Qwen2.5-14B; SMART won on all directions. Single-call baselines on Subtitle Arena: Gemma 3 4B, DeepSeek-V3.2, Claude Sonnet 4.6, Claude Opus 4.8, GPT-5.5 — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)
- Gemini API lists Gemma 4 as free on the free tier and not available on the paid tier — [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)

### Inferences
- For zh/ja/ko→en subtitles, any frontier or near-frontier model (Claude Sonnet-class, Gemini Flash/Pro, GPT-5.x, DeepSeek V4) is likely adequate when context and glossary are good. Spend goes further on scaffolding (memory, review) than on the most expensive model. Local small models (4B-12B) need the batch validator and heavier scaffolding.
- NLLB and MADLAD are sentence-level and context-free, a poor fit for pro-drop dialogue; they serve as baselines only.

### Gaps
- No WMT25 or 2026 human evaluation for zh/ja/ko→en. WMT26 results (late 2026) were not found.
- No head-to-head data found for Qwen3 or Gemma 4 on Ollama for CJK→en subtitles.
- No published cost per hour of video or per 1,000 lines from a primary source; only my estimate above.

## Evaluation: what to trust for zh/ja/ko subtitles

### Takeaway
Use human MQM or ESA as the gold standard. Learned neural metrics (MetricX-24/25, xCOMET, CometKiwi) and GEMBA-style LLM judges correlate similarly with humans at segment level, but WMT25 showed automatic metrics can mis-rank systems badly. BLEU and chrF are weak for paraphrastic subtitle English. Pair metric checks with cheap structural checks (count, id, untranslated script, length/CPS) and a subtitle-adapted MQM.

### Cited Findings
- WMT25: automatic rankings were biased toward Shy-hunyuan-MT compared with human evaluation — [WMT25 findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- WMT24 metrics averages: MetricX-24-Hybrid 0.721, xCOMET 0.719, gemba_esa 0.711, essentially tied — [WMT24 metrics](https://aclanthology.org/2024.wmt-1.2.pdf)
- MetricX-25 (Gemma 3-based regressor that predicts MQM and ESA) beats MetricX-24 at segment level but is mixed at system level. GemSpanEval is competitive with xCOMET in error-span detection — [arXiv 2510.24707](https://arxiv.org/pdf/2510.24707)
- Reasoning models as MT judges "overthink" simple cases and overestimate scores — [arXiv 2510.20780](https://arxiv.org/html/2510.20780)
- SubMQM: 7 dimensions and 19 error types, weighted Accuracy 0.30, Terminology 0.20, Fluency 0.20, Audience Appropriateness 0.12, Linguistic Conventions 0.08, Technical (line breaks, characters per line, lines per box) 0.06, Locale 0.04 — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)
- IWSLT subtitling uses SubER plus compliance rates: CPS ≤ 21, CPL ≤ 42, and ≤ 2 lines per block — [IWSLT 2023 subtitling](https://iwslt.org/2023/subtitling)
- AZPT zero-pronoun metric (Korean) — [COLING 2025](https://aclanthology.org/2025.coling-main.110.pdf); with validity concerns — [arXiv 2605.31056](https://arxiv.org/pdf/2605.31056)
- Practitioner claim (secondary, unverified): GPT-class judges are weaker on Korean than on Western European pairs — [futureagi blog](https://futureagi.com/blog/evaluating-llm-translation-quality-2026/)

### Inferences
- Cheap proxies that catch the documented failures: input and output id sets equal; no empty translations; a regex for leftover Han, Kana or Hangul in English output (untranslated segments); output/input length-ratio outliers per line (merges show as one long line next to one empty or duplicated line); duplicate adjacent outputs; CPS and CPL over budget; a glossary hit rate (source term present implies target term present); speaker-name consistency against the character list. A reference-free QE model (CometKiwi or MetricX QE) can rank lines for human review rather than score systems.

### Gaps
- Overall correlations for the WMT25 metrics task were not retrieved. No study found validating metrics specifically on zh/ja/ko→en subtitles.

## Subtitle-specific constraints

### Takeaway
English targets: at most 42 characters per line, at most 2 lines, about 20 CPS for adults (17 for children), and keep source timing. Constrain length in the prompt and check it afterwards. Condense with an LLM rewrite only for out-of-budget lines.

### Cited Findings
- Netflix English (USA) style guide: 42 characters per line; 20 CPS adult, 17 children; maximum 2 lines, preferring one line; bottom-heavy pyramid breaks; break after punctuation and before conjunctions or prepositions, keeping names and verb groups together. SDH sound effects go in lowercase square brackets. Italicize unfamiliar foreign words and off-screen media voices. Honorifics are not addressed — [Netflix guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-USA-Timed-Text-Style-Guide)
- IWSLT 2026 subtitling dropped the separate compression track and set tighter limits for some targets, for example Chinese at 16 characters per line and 9 CPS. One 2026 system flags out-of-limit segments and rewrites them with an LLM — [IWSLT 2026 paper](https://aclanthology.org/2026.iwslt-1.10/)
- AppTek (IWSLT 2025) used MT with length control to condense subtitles and reported good BLEU, BLEURT and SubER — [AppTek 2025](https://preview.aclanthology.org/setup/2025.iwslt-1.21)
- SMART includes a "length-aware" translator role and a constraint-check tool — [arXiv 2609.38660](https://arxiv.org/html/2609.38660)

### Inferences
- Honorifics (ja -san/-senpai, ko -nim/oppa/hyung): no standard exists in Netflix's English guide. The choice should be a per-title glossary or style setting applied consistently, which is where a memory or glossary is evidenced to matter.

### Gaps
- Netflix minimum duration and gap rules are in a separate "Subtitle Timing Guidelines" page, not fetched.
