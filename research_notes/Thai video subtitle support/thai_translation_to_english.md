# Thai -> English translation quality for subtitle lines (as of 2026-10-11)

Scope note: 15 tool calls. I found very little independent, current, Thai->English, dialogue-specific evidence. Most current numbers are either English->Thai, general Thai NLU/exam scores, or vendor claims without published tables. Treat every ranking below as low-to-medium confidence.

## Which engines/LLMs handle Thai -> English best, with evidence, confidence and cost

### Takeaway
I found no independent 2026 benchmark that ranks current frontier LLMs (Claude 4.x/5.x, GPT-5.x, Gemini 3, Gemma 4) on Thai->English dialogue. The strongest independent signal is the April 2026 Alconost subtitle benchmark, which tested the opposite direction (EN->TH). In it, TranslateGemma-12B, Gemini Flash Lite and Claude Sonnet led, GPT-5.4 was close behind, and DeepSeek was clearly worst on Thai. Thai->English is the easier direction for LLMs. For local use, the Thai-specialised Typhoon Translate 4B is the most relevant small model, but its quality claims come only from the vendor.

### Cited Findings
- **Alconost AI subtitle benchmark (independent; published 2026-04-07; EN->TH, not TH->EN).**
  - Systems tested: TranslateGemma-12b, Gemini Flash Lite, Claude Sonnet, GPT-5.4, GPT-5.4 Mini and DeepSeek (the post gives no exact versions).
  - Test material: one English subtitle file of 1,002 segments.
  - Metrics: reference-free MetricX-24 and COMETKiwi, combined into a "TQI" index, with a human review afterwards.
  - Overall rank: TranslateGemma > Gemini Flash Lite > Claude Sonnet > GPT-5.4 > GPT-5.4 Mini > DeepSeek.
  - Thai was the second-hardest language after Japanese, and model choice mattered most there.
  - DeepSeek got the lowest score of any model-language pair, on Thai.
  - TranslateGemma kept its lead on Thai but varied more from segment to segment.
  - The authors recommend post-editing (MTPE) for Thai.
  - The authors say the metrics ignore line length and reading speed.
  - Per-engine Thai numbers did not load in the fetched page.
  - [Alconost](https://alconost.com/en/blog/ai-subtitle-translation-benchmark)
- **TranslateGemma (Google; vendor paper; arXiv 2601.09012, January 2026).**
  - Built on Gemma 3.
  - Automatic evaluation on WMT24++ (55 language pairs). The paper claims "consistent and substantial gains over the baseline Gemma 3 models across all sizes", and says smaller TranslateGemma models often match larger baselines.
  - The abstract gives no per-language Thai numbers, so I could not confirm that Thai is among the 55 pairs.
  - [arXiv 2601.09012](https://arxiv.org/abs/2601.09012)
  - Press coverage says the 12B model beat the Gemma 3 27B baseline on WMT24++: [InfoQ](https://infoq.com/news/2026/01/google-translategemma-models)
- **BHASA suite (independent academic, 2023; older models).** Reports ChrF++ and COMET-22 for both TH->EN and EN->TH with GPT-3.5 and GPT-4. Translation *into* English scored higher than translation *from* English, and GPT-4 beat GPT-3.5 on Thai. [arXiv 2309.06085](https://arxiv.org/pdf/2309.06085)
- **ThaiLLM Leaderboard (SCB10X with VISTEC and SEACrowd; raw results file).**
  - Llama-3-Typhoon-v1.5x-70B-Instruct scored:

    | FLORES-200 direction | BLEU | chrF++ |
    |---|---|---|
    | TH->EN | ~47.9 | ~59.0 |
    | EN->TH | ~24.6 | ~52.1 |

  - This confirms TH->EN is the easier direction. It is an older model; I did not see the leaderboard's current table.
  - [ThaiLLM-Leaderboard results JSON](https://huggingface.co/datasets/ThaiLLM-Leaderboard/results/raw/fffbb84f67172f9e92840bd94b8dc2ed4f854e15/NLG/llama-3-typhoon-v1.5x-70b-instruct/results.json)
- **SEA-HELM leaderboard (AI Singapore; page dated 2026-09-18).**
  - Lists 75 open-weight and 16 closed-weight models, including Gemma 4 (31B, E2B), Qwen 3.5/3.6, SEA-LION v4.5/v4.8 and GPT OSS.
  - The fetched page showed no Thai or translation scores, and Typhoon and Llama were not visible.
  - [SEA-HELM leaderboard](https://leaderboard.sea-lion.ai/)
- **Typhoon Translate 1.5 4B (SCB10X; vendor claim).**
  - Translates Thai <-> English. The model card says it is built on Qwen3.
  - It adds "promptable translation rules" (custom terminology).
  - Evaluation used GPT-4o-mini as an "AI judge" against "other top systems" that are not named; no scores appear in the card text.
  - The license is inconsistent: the metadata says Apache-2.0, the description says Gemma License.
  - Recommended prompt: "Translate the following {Thai} text into {English}, strictly following the rules below and return only the translated text. {rules} Source Text: {text}". The card also recommends a low temperature and a context under 8,192 tokens.
  - [HF model card](https://huggingface.co/scb10x/typhoon-translate1.5-4b)
- **Typhoon 2.5 (Qwen3-4B base) (SCB10X).** A Thai/English instruct model with a 256K context and function calling. I found no translation scores for it. [HF card](https://huggingface.co/scb10x/typhoon2.5-qwen3-4b)
- **NLLB vs a general LLM, EN->TH only (independent; arXiv 2410.17145, October 2024).**
  - NLLB-3.3B beat Llama-3-8B on the SCB set: BLEU 0.244 vs 0.173, METEOR 0.449 vs 0.371.
  - On the code-switched medical set the gap was about 1%.
  - Llama-3-8B at 4-bit quantisation dropped moderately (BLEU 0.421 -> 0.392 on the CS set). At 2-bit it collapsed to BLEU ~0.
  - [arXiv 2410.17145](https://arxiv.org/html/2410.17145v1)
- **Older Typhoon claims (vendor/press).** Original Typhoon beat GPT-3.5 at Thai translation in a Thai cultural context. [Thairath](https://www.thairath.co.th/money/tech_innovation/tech_companies/2759166)
- **Agentic EN->TH pipeline (2024 academic).** Typhoon was more accurate than GPT-3.5 Turbo. A reflection step, with Typhoon translating and Claude reflecting, improved BERTScore, COMET, METEOR and BLEU. [BCU open access](https://www.open-access.bcu.ac.uk/16138/)
- **MEET-MR (EACL 2026 short paper).** An English-Thai MT benchmark with MQM error annotations and human preference rankings across nine domains, with a public dataset. I could not fetch the paper (404), so its systems and results are unverified. [ACL Anthology entry](https://preview.aclanthology.org/credits/2026.eacl-short.41)
- **JAI-1 (Thai-centric LLM report).** Reports FLORES EN->TH BLEU (GPT-4o 46.5 in its table). These are BLEU only, not COMET. [arXiv 2510.08620](https://arxiv.org/pdf/2510.08620)

### Inferences
- Tentative ranking for Thai->English dialogue (medium-low confidence, largely extrapolated from EN->TH and general multilingual strength):
  1. Gemini (Flash or Pro tier), Claude Sonnet and GPT-5.x are roughly at parity. All are acceptable, and differences likely come from prompting and context more than from the model.
  2. TranslateGemma / Gemma-family models are strong on automatic metrics. Human review in Alconost found script-variant errors those metrics missed, so the metric lead may overstate real quality.
  3. DeepSeek is weakest on Thai in the only independent subtitle test, so avoid it as the default for Thai.
- The app's Reflect mode is consistent with the 2024 agentic-translation finding that a reflection pass helps Thai.
- NLLB-200 and classic MT (Google, DeepL, Microsoft) translate sentence by sentence with no prompt. They cannot take a glossary, speaker gender or neighbouring cues, so for dialogue they are likely below a prompted LLM. That is an inference; I found no 2026 comparison. DeepL added Thai only recently (unverified in this session).

### Gaps
- No independent current TH->EN scores (COMET, chrF++ or human) for Claude 4.x/5.x, GPT-5, Gemini 3, Gemma 4, Qwen3, SeaLLM, Sailor or OpenThaiGPT.
- The SEA-HELM per-language translation scores did not render.
- The Typhoon Translate 1.5 comparison numbers are not published on its card. The OpenTyphoon release blog was not found.
- **Cost per million tokens was not verified in this session** for any provider. The report writer should take prices from the vendor pricing pages, not from these notes. Thai text is token-heavy in many tokenisers (not measured here).
- Google Translate, DeepL and Microsoft TH->EN quality: no 2026 primary source found.

## Thai-specific pitfalls and how prompts should handle them

### Takeaway
Primary-source evidence is thin. The general pro-drop literature shows that dialogue drops pronouns far more often than news text, and that MT systems often fail to restore them. Thai politeness particles (ครับ/ค่ะ/คะ/นะ) encode speaker gender and softening rather than lexical content. Most other pitfalls listed here come from linguistic knowledge, not tested evidence, and are marked as inference.

### Cited Findings
- **Pronoun dropping in dialogue.**
  - In Chinese, a comparable pro-drop language, about 26.55% of English pronouns correspond to dropped source pronouns in the dialogue domain, against 7.35% in newswire.
  - Standard SMT and NMT often fail to recover them.
  - [Wang et al., arXiv 1801.03257](https://ar5iv.arxiv.org/html/1801.03257)
  - Restoring dropped pronouns in subtitle data gave +1.58 BLEU: [arXiv 1604.06285](https://arxiv.org/pdf/1604.06285)
- **Politeness particles.**
  - krap (ครับ) is the male form and ka (ค่ะ/คะ) the female form. Both are politeness softeners. ka is the usual choice with strangers, elders and people in authority.
  - Some women also use krap.
  - [Transparent Thai blog](https://blogs.transparent.com/thai/?p=191); [thethailandlife](https://www.thethailandlife.com/krap-ka)
  - These are learner sources, not academic ones.
- **Particle นะ (ná) and other imperative particles.**
  - ná can seek agreement ("it is hot today, isn't it?"): [ThaiPod101](https://www.thaipod101.com/lesson/thai-teachers-answer-your-questions-20-what-are-the-sentencefinal-particles-in-thai)
  - ná is one of the sentence-final imperative particles (sì, ná, nâa, tɤ̀, nɔ̀i, dâi) that soften face-threatening acts: [Rutherford 2022, JSEALS](https://doaj.org/article/98cbbf3555f64e0dbce8fe329d8916f4)
- **Dialects.** Typhoon translated the text correctly but did not render the local dialect features, per a 2025 Thai-dialect LLM evaluation. Isan and Northern dialect speech is therefore a risk area. [arXiv 2504.05898](https://arxiv.org/pdf/2504.05898)
- **Code-switching.** Code-switched English terms inside Thai behave differently in MT. Task-specific metrics are needed to see whether the English keywords are preserved. [arXiv 2410.17145](https://arxiv.org/html/2410.17145v1)
- **Subtitle constraints.** Line length and reading speed are part of subtitle quality and are not captured by COMET-type metrics. [Alconost](https://alconost.com/en/blog/ai-subtitle-translation-benchmark)

### Inferences
These are linguistic knowledge only, not tested in a cited source.

- **Particles.**
  - Tell the model that ครับ/ค่ะ/คะ/จ้ะ/จ๊ะ/นะ/เนาะ/สิ/เถอะ/หรอก carry tone, gender or politeness and should not be translated literally ("krap", "na").
  - Render them through tone instead ("..., okay?", "please", "right?"), or drop them.
  - The particles reveal speaker gender, so they are a cue for resolving the pronouns "he" and "she" when the speaker's partner is mentioned.
- **Dropped subjects.** The prompt should ask the model to infer subjects and objects from the surrounding cues and the speaker list, and to avoid defaulting to "I" or "you".
- **Thai pronouns and kin terms.**
  - Thai pronouns (ผม/ฉัน/หนู/กู/มึง/เค้า) and kin terms used as pronouns (พี่/น้อง/แม่/ป้า, nicknames in the third person) are register and relationship signals.
  - "พี่" used as "you" or "I" is a frequent mistranslation risk.
- **Nicknames (ชื่อเล่น).**
  - Short nicknames such as ต้น, มิ้นท์, เต้ have several possible romanisations (Ton/Tonn, Mint/Mind, Tae/Tay).
  - A glossary keeps them consistent across cues and episodes.
  - Royal transcription (RTGS) differs from the spellings stars actually use.
  - The glossary should hold the canonical English spelling of each name.
- **Royal vocabulary (ราชาศัพท์) and monk vocabulary.** Translate these by meaning with an elevated register, not word by word.
- **Internet slang.**
  - 555 = "hahaha" ("5" is "ha" in Thai).
  - Other slang the prompt should cover: จุงเบย, ปัง, ชิป, เกรงใจ (no English equivalent), แก (casual "you").
- **Repetition mark ๆ.** It doubles the word for intensity or plurality ("very", "really really"). An ASR line may contain it or a spelled-out duplicate.
- **Classifiers.** Usually drop them in English.
- **Missing punctuation.**
  - Thai has no sentence punctuation, and spaces mark clause or phrase breaks.
  - ASR output (e.g. Whisper) may split cues mid-clause, so the model must infer sentence boundaries.
  - This is the main argument for the app's translate-by-sentence mode on Thai.

### Gaps
- I found no Thai-specific study measuring LLM errors on particles, pronouns, nicknames or ASR fragments in TH->EN subtitles.
- MEET-MR's MQM error categories (EN-TH) could give Thai-specific error types, but I could not access the paper.

## Prompt design: language name, glossary, speaker/register notes, surrounding cues, sentence vs line

### Takeaway
Evidence from general and document-level MT research supports giving surrounding context and translating whole sentences rather than isolated fragments. A speaker and terminology step is the main remedy for pronoun errors in LLM subtitling. The Baihe features (glossary, style notes, translate-by-sentence, Reflect, id-keyed JSON) line up with what the literature recommends. I found no Thai-specific A/B evidence.

### Cited Findings
- **Context vs isolated segments.**
  - In WMT 2024 chat translation, using context outperformed translating isolated segments: [WMT24 paper](https://aclanthology.org/2024.wmt-1.101.pdf)
  - Sentence-level LLM translation can lack coherence across a document: [arXiv 2304.02210](https://ar5iv.labs.arxiv.org/html/2304.02210)
- **Context-aware prompting.** Selecting relevant earlier sentences and summarising them reduced incoherence. [arXiv 2406.07081](https://arxiv.org/html/2406.07081v1)
- **Survey.** A 2025 survey covers prompting and fine-tuning approaches for context-aware LLM MT. [arXiv 2506.07583](https://arxiv.org/html/2506.07583v1)
- **Hermes subtitling framework (WWW 2026, arXiv 2602.00597).**
  - Names pronoun and terminology translation as persistent subtitling problems.
  - Adds speaker diarization, terminology identification and expressiveness enhancement modules to an LLM subtitling pipeline.
  - Claims state-of-the-art diarization and "contextually coherent" translations. Gains could not be quantified from the abstract, and language pairs were not stated.
  - [arXiv 2602.00597](https://arxiv.org/abs/2602.00597)
- **Typhoon Translate 1.5 terminology rules.** The model accepts explicit translation rules (terminology) in the prompt, a vendor feature that matches the glossary approach. [HF card](https://huggingface.co/scb10x/typhoon-translate1.5-4b)
- **Reflection.** A reflection pass improved Thai translation in an agentic pipeline (EN->TH). [BCU](https://www.open-access.bcu.ac.uk/16138/)

### Inferences
- **Name the source language explicitly ("Thai").** Thai script is unambiguous, so the gain is probably small. It does cheaply prevent mixing with Lao or Khmer when lines are short, slang or numerals ("555").
- **Glossary.** The highest-value prompt addition for Thai is a glossary of nicknames mapped to canonical English spellings, plus each character's gender and relationships (who is พี่ to whom).
- **Register notes.** Ask the model to keep register (rude กู/มึง vs polite) in English tone, to translate particles by tone, and to infer dropped subjects from context.
- **Translation unit.**
  - Use translate-by-sentence (merge fragments, then redistribute across cues) for Thai ASR output, because cue boundaries often fall mid-clause.
  - Line-by-line with a ±N-cue context window is the fallback when timing alignment matters more.
- **Batching.** Keep the id-keyed JSON replies. Batch roughly 20-50 cues with neighbouring context, rather than one cue per call.

### Gaps
- I found no Thai-specific experiment comparing sentence-level and line-level subtitle translation.
- I found no evidence on whether adding speaker gender measurably improves Thai pronoun rendering with current LLMs.

## Local options (<=12B, 8-12 GB GPU)

### Takeaway
Candidates for an 8-12 GB GPU, in priority order:

1. **Typhoon Translate 4B.** On Ollama as `scb10x/typhoon-translate-4b`, Q4_K_M, 2.5 GB. It is a dedicated Thai<->English model, but it expects its own fixed prompt, so it fits poorly with glossary/JSON batching.
2. **TranslateGemma 12B.** Top of the independent EN->TH subtitle benchmark.
3. **The owner's Gemma 4 (E4B or larger) via Ollama.** Gemma 4 has no published Thai translation numbers that I could find.

Avoid quantisation below 4-bit.

### Cited Findings
- **Ollama `scb10x/typhoon-translate-4b:latest`.**
  - 2.5 GB, Q4_K_M, 128K context.
  - The readme says it is based on Gemma3-4B. This conflicts with the v1.5 HF card's "Qwen3" base, which suggests the Ollama build is the older v1.0 (unverified).
  - It requires the fixed prompt "Translate the following text into English." followed by the text.
  - It is used under the OpenTyphoon Terms.
  - [Ollama](https://ollama.com/scb10x/typhoon-translate-4b)
- **Typhoon Translate 1.5 4B on Hugging Face.** Lists 3 quantisations (format not confirmed as GGUF). The vendor recommends vLLM/SGLang. [HF](https://huggingface.co/scb10x/typhoon-translate1.5-4b)
- **TranslateGemma-12b.** Ranked first in the 2026 EN->TH subtitle benchmark on metrics, with higher segment variance on Thai: [Alconost](https://alconost.com/en/blog/ai-subtitle-translation-benchmark). Built on Gemma 3; vLLM images exist: [Docker Hub](https://hub.docker.com/r/aistaging/translategemma-vllm)
- **Gemma 4 sizes.** E2B, E4B, 26B-A4B MoE and 31B dense, according to a third-party post (unverified): [Medium](https://medium.com/@ccibeekeoc42/tuning-gemma-4-for-multilingual-and-tool-aware-language-understanding-cb7850fc1989). Gemma 4 31B and E2B appear on SEA-HELM, but their scores did not render: [SEA-HELM](https://leaderboard.sea-lion.ai/)
- **Typhoon2.5-Qwen3-4B.** A general Thai instruct model (256K context). It could follow glossary/JSON instructions better than a translate-only model, but has no translation scores. [HF](https://huggingface.co/scb10x/typhoon2.5-qwen3-4b)
- **Quantisation.** Quantising an 8B general LLM to 4-bit gave a moderate drop for EN<->TH; 3-bit and 2-bit degraded severely, with 2-bit failing completely. [arXiv 2410.17145](https://arxiv.org/html/2410.17145v1)

### Inferences
- **Gemma 4 for the app's prompts.** For the app's JSON, id-keyed and glossary prompts, a general instruct model such as Gemma 4 E4B/26B-A4B or Typhoon2.5-Qwen3-4B is easier to integrate than translate-only models.
- **Typhoon Translate.** Typhoon Translate is best used for plain-text, one-sentence-per-call translation, or as a comparison baseline.
- **Fit on 12 GB.** The 26B-A4B MoE may fit in 12 GB only at aggressive quantisation; I did not verify this.
- **Quantisation level.** Prefer Q4_K_M or higher; Q5/Q6 if VRAM allows.

### Gaps
- I found no published Thai->English scores for Gemma 4 (any size), Qwen3 at 8B or below, Sailor2, SeaLLM 3 or OpenThaiGPT.
- I could not check the exact Ollama tag names for TranslateGemma and Gemma 4.
- Whether the Ollama Typhoon Translate build is v1.0 or v1.5 is unconfirmed.
