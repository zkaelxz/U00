# Bulk (offline) LLM subtitle translation and fast offline transcription for zh/ja/ko -> en (state as of October 2026)

Research cutoff: 2026-10-10. About 17 tool calls (searches and fetches). Where a number came from a search-engine summary rather than a fetched primary page, these notes say so. Treat every vendor blog number as a claim, not a measurement.

## Batching and context: lines per request, sliding context, summaries, ID-keyed JSON, structured output, repair

### Takeaway
No study I found measures the best batch size for LLM subtitle translation. The most widely used open-source tool (llm-subtrans) uses scene-based batches with min/max line counts, rolling per-batch and per-scene summaries, and a terminology map passed forward. Its own docs say larger batches are cheaper and faster but desync more often, which triggers costly retries. For output, provider-side JSON-schema constrained decoding (Anthropic structured outputs, GA) removes schema-violation retries. It does not stop the model returning the wrong number of lines, so ID matching and per-ID repair are still needed.

### Cited Findings
- llm-subtrans splits by scene, using `--scenethreshold` (the time gap in seconds that starts a new scene), then batches with `--minbatchsize` (the line count at which a new batch may start) and `--maxbatchsize` (a hard cap). The README says "Higher values typically result in faster and cheaper translations but increase the risk of desyncs", and that larger batches risk "desynchronising, triggering expensive retries". It gives no numeric defaults (they are in `Options.py`). — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)
- llm-subtrans generates a summary of each batch and scene and passes it forward, and users can edit these by hand. `--build-terminology-map` collects names, titles and terms into a "terminology map that is provided to subsequent batches so that consistent translations can be used throughout", and `--terminology` seeds it with `SOURCE::TRANSLATION` pairs. Temperature defaults to 0, and `OPENAI_REASONING_EFFORT` defaults to low. — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)
- The same README's opinions on models (author opinion, not a benchmark): Gemini Flash is "likely the leading model for speed and fluency" but sometimes refuses content, `deepseek-chat` is cheap and "the only sensible choice" among DeepSeek models, Anthropic translation is described as weaker and expensive, and "You should not expect particularly good results from local models." — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)
- On document context, DocHPLT (arXiv 2508.13079) reports that substantial context matters, but the benefit of larger context windows on WMT24++ was inconsistent: smaller windows, or even sentence-level fine-tuning, sometimes beat the larger-context models. (Seen via a search summary; full paper not fetched.) — [DocHPLT, arXiv 2508.13079](https://arxiv.org/pdf/2508.13079)
- A 2026 on-device subtitle paper (LocalSubs, arXiv 2607.09957) describes interactive subtitle translation as one current cue plus at most three preceding cues, run at batch size one with output limited to the current cue. This is the live-translation pattern, the opposite of bulk. — [arXiv 2607.09957](https://arxiv.org/html/2607.09957)
- Anthropic structured outputs: the schema is passed in `output_config.format` (`type: "json_schema"`) and enforced by constrained decoding, giving "always valid" JSON with "no retries needed for schema violations", qualified as "in most cases". Output can still be invalid on `stop_reason: "refusal"` or `"max_tokens"` (truncated). — [Anthropic structured outputs docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- Structured-output limits that matter for subtitle arrays: array `minItems` only 0 or 1, and no `maxItems`, `minLength` or `maxLength`. So the schema cannot enforce "exactly N lines", and line counts must be checked in code. The grammar compiles on the first request (extra latency) and is cached for 24 h. Changing `output_config.format` invalidates the prompt cache. The schema adds a system prompt (more input tokens). — [Anthropic structured outputs docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

### Inferences
- For bulk work, a scene-bounded batch (cut at silence gaps) with a hard cap fits both the llm-subtrans design and the DocHPLT finding that more context is not always better. The cap should be tuned per model by measuring the desync/retry rate, not assumed.
- Structured outputs plus explicit line IDs is the robust combination: the decoder guarantees parseable JSON, and ID matching handles missing or merged lines. The app already follows the "match by explicit id" rule; constrained decoding mainly removes the JSON-repair branch. Keep the schema stable across a job so neither the prompt cache nor the grammar cache is invalidated.
- Repairing only the missing IDs (re-request those lines with neighbouring context) is cheaper than retrying the whole batch, which is what llm-subtrans warns makes desyncs "expensive". This is my design inference, not a measured result.

### Gaps
- I found no peer-reviewed or community benchmark of lines per request against quality or failure rate for LLM subtitle translation, nor of ID-keyed JSON failure rates by model.
- llm-subtrans numeric defaults (scene threshold, min/max batch) were not visible on the README.
- I did not verify OpenAI strict JSON-schema mode or Gemini `responseSchema` docs in this pass. I could not confirm from the fetched page whether structured outputs work inside Anthropic Message Batches (the relevant part of the page was cut off).
- I found no study comparing "translate by merged sentence, then re-split" against line-by-line subtitle translation.

## Cost and speed levers: caching, batch APIs, tiering, thinking, rate limits, model choice

### Takeaway
The two biggest cost levers are batch APIs (50% off input and output, results within 24 h) and prompt caching of the stable prefix (instructions, glossary, character sheet, episode summary). On current Anthropic models, cached reads cost 0.05x to 0.1x base and the discounts stack with batch. WMT25 human evaluation puts frontier general LLMs (Gemini 2.5 Pro, Claude 4, DeepSeek-V3) and dedicated LLM-based MT systems (Shy-hunyuan-MT, Algharb) at or above human references. However, WMT25 did not evaluate zh/ja/ko -> en, so no shared-task ranking exists for the app's actual directions.

### Cited Findings
- Anthropic cache pricing (fetched October 2026): 5-minute cache write 1.25x base input, 1-hour write 2x, reads 0.1x. Reads are 0.05x on Opus 5.5 and Sonnet 5.5, and 0.025x on Fable 5.1 and Mythos 5.1. The 5-minute TTL refreshes free on each hit. Up to 4 breakpoints are allowed. Minimum cacheable prefix is 512 tokens for Opus 5.5, Sonnet 5.5 and Haiku 5.5; 1,024 for Sonnet 4.5/4.6 and Sonnet 5; 4,096 for Haiku 4.5 and Opus 4.5/4.6. Shorter prompts are silently not cached. The multipliers "stack with other pricing modifiers such as the Batch API discount". A 1-hour entry must come before 5-minute entries. — [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Selected Anthropic prices in USD per million input tokens (base / cache read): Haiku 5.5 $0.10 / $0.01 (up to 100K-token prompts; $0.50 / $0.05 above), Sonnet 5.5 $2 / $0.10, Opus 5.5 $4 / $0.20, Haiku 4.5 $1 / $0.10. Output prices were not in the fetched section. — [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- `max_tokens: 0` cache pre-warming is rejected inside a Message Batches request. — [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Anthropic Message Batches: 50% discount on input and output tokens, results in under 24 hours. The original 2024 announcement says up to 10,000 queries per batch; a later third-party guide says 100,000. The sources conflict, so check the current docs. No streaming. — [Anthropic announcement](https://anthropic.com/news/message-batches-api); [Respan guide (third-party)](https://www.respan.ai/articles/anthropic-message-batches-api)
- OpenAI, Gemini and DeepSeek caching (secondary sources only; they conflict): OpenAI caching is automatic above 1,024 tokens, with discounts reported from 50% (gpt-4o) to 90% (GPT-5.x). Gemini 2.5 implicit caching is reported at 75% off, and explicit caching at up to 90% plus a per-hour storage fee. DeepSeek caching is automatic, with cache-hit prices reported at about 90% off ($0.028 vs $0.28 per M on older pricing) up to 98-99% on V4. Some of these aggregator pages name models I could not verify. — [leanlm.ai](https://leanlm.ai/blog/prompt-caching); [ai-cost-estimator](https://ai-cost-estimator.com/blog/what-is-prompt-caching-deepseek-claude-gpt-cache-hit-api-bill-savings); [tokencost.app](https://tokencost.app/blog/prompt-caching-pricing-2026)
- WMT25 General MT (human evaluation with ESA; MQM for en->ko and ja->zh). The CJK directions were en->ja, en->ko, en->zh and ja->zh. Per a search summary of the findings: unconstrained winner Gemini 2.5 Pro for en->ja, en->ko and ja->zh; best constrained system Shy-hunyuan-MT; Algharb top for en->zh. Human references were in the winning cluster for only 6 of 15 pairs. Reported en->zh scores: Algharb 88.4, Claude-4 86.9, DeepSeek-V3 85.0, Human 82.1. The extracted tables were messy, and one version says final human results come in the camera-ready version, so verify the PDF before quoting these numbers. — [WMT25 findings (Kocmi et al.)](https://aclanthology.org/anthology-files/pdf/wmt.real/2025.wmt-1.1.pdf); [machinetranslate.org WMT25](https://machinetranslate.org/wmt25)
- llm-subtrans exposes `--ratelimit` (requests per minute) and says speed depends heavily on the model and larger batches are more cost-effective. — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)

### Inferences
- Prompt layout for caching: put the fixed instructions, glossary, character sheet and series summary first, behind one breakpoint (1h TTL when a series job runs longer than 5 minutes between hits). Then the episode summary behind a second breakpoint, and the per-batch lines last. With Sonnet 5.5 or Opus 5.5 at 0.05x reads, a long glossary prefix becomes almost free after the first batch.
- For "whole episode or series overnight" jobs, batch API plus cache gives the lowest price. For jobs a user is waiting on, use the synchronous API with caching. Batch results can take up to 24 h, so the app should offer batch as an opt-in "cheap, slow" mode.
- Tiering (a cheap model first, a strong model only on flagged lines) fits the pricing: Haiku 5.5 is 20x cheaper per input token than Sonnet 5.5. I found no study measuring the quality of tiered pipelines for subtitles.
- WMT25 lacks into-English CJK directions, so the app's model choice should rest on its own small human-checked sample per language, using WMT only as a rough signal that frontier general LLMs and LLM-based MT models (Hunyuan-MT) are both competitive.

### Gaps
- I did not fetch official OpenAI, Gemini or DeepSeek pricing or caching pages, so those figures are secondary and conflicting.
- I found no 2025-2026 evidence on whether extended thinking or reasoning improves dialogue translation quality enough to justify its cost. The only data point is llm-subtrans defaulting OpenAI reasoning effort to "low".
- I found no community subtitle benchmark (for example a ja->en anime or drama set) ranking current models. JP-TL-Bench (2026, below) exists, but I did not fetch its rankings.
- Anthropic output prices and current Batch limits were not captured.

## Accuracy levers: glossary, pronouns and speakers, honorifics, QE and LLM-judge routing, consistency

### Takeaway
The best-supported levers are terminology maps carried forward across batches (as llm-subtrans does) and QE-based routing or selection. Choosing among candidate translations with COMETKiwi improved COMET in all six languages tested at WMT25, including en->ja and en->zh. QE-guided span "fill-in" editing made quality worse. Zero pronouns in Japanese -> English remain a documented failure mode that context helps but does not solve, and BLEU-type metrics miss it.

### Cited Findings
- WMT25 QE-informed error correction (arXiv 2511.13884, 17 Nov 2025), English into zh, cs, ja, is, ru and uk, 1,000 segments each:
  - Selection ("Best MT Wins"): six LLMs retranslated, and COMETKiwi (wmt22-cometkiwi-da) picked the best, original included. Average ΔCOMET was +0.0201 (ja +0.0103, zh +0.0184), and the method ranked first on the subtask leaderboard.
  - Span editing ("Fill in the Blanks"): QE-flagged spans were masked and refilled by TowerPlus-9B. Average ΔCOMET was −0.0108, worse in every language (ja −0.0134, zh −0.0130).
  - The authors call the selection approach costly or impractical because it needs many full hypotheses. Evaluation was automatic metrics only, with no human evaluation.
  — [arXiv 2511.13884](https://arxiv.org/html/2511.13884v1)
- APE systems are "prone to overcorrecting". Deoghare et al. (2025) restrict edits to word-level-QE-flagged segments. — [WMT25 APE/QE paper](https://aclanthology.org/2025.wmt-1.73.pdf)
- The WMT24 Metrics task found learned metrics (COMET, xCOMET) better than statistical metrics for evaluating LLM output (via search summary). — [futureagi blog summarizing WMT24](https://futureagi.com/blog/evaluating-llm-translation-quality-2026/)
- MQM-APE: all tested LLMs improved over the original translation on CometKiwi and BLEURT20 when using MQM-style error annotation followed by post-editing. — [MQM-APE, arXiv 2409.14335](https://arxiv.org/pdf/2409.14335)
- Japanese zero pronouns: resolving them needs discourse topic and old information, or world knowledge. In a speech-dialogue MT study, context recovered a dropped "she" but no system recovered a dropped "they". BLEU struggles to measure zero-pronoun success. — [arXiv 2305.09210](https://arxiv.org/pdf/2305.09210); [arXiv 2107.00318](https://arxiv.org/pdf/2107.00318); [Shimazu et al., LREC 2020](https://aclanthology.org/2020.lrec-1.447)
- JP-TL-Bench (2026, pairwise LLM judging for ja<->en) reports that ja->en systems often produce misgendered pronouns or swapped thematic roles, which sentence-level metrics miss. Seen via a search summary; methodology not checked. — [JP-TL-Bench, arXiv 2601.00223](https://arxiv.org/pdf/2601.00223)

### Inferences
- A character sheet in the cached prefix (name, gender, how each character addresses others) plus speaker labels per line is the most direct fix for misgendering. The evidence above shows context helps but is not sufficient, so pronoun checks belong in review flags.
- For routing, use QE or an LLM judge to flag lines, then retranslate those lines with a stronger model and choose by QE. Do not ask a model to patch flagged spans, since the WMT25 span-fill approach lowered scores. Checking 2 candidates only on flagged lines keeps the cost of selection bounded.
- COMETKiwi wmt22 is licensed CC-BY-NC-SA and its large variants are heavy. This is my background knowledge, unverified here, and matters for a local app's licensing and size budget.

### Gaps
- I found no study measuring glossary post-substitution against prompt injection against constrained decoding of terms for LLM subtitle MT.
- I found nothing measured on honorifics policy (keep -san, or translate it) for ja/ko->en; it is a style choice with no benchmark found.
- I found no evidence that back-translation checks improve LLM subtitle quality, and no measured COMETKiwi or xCOMET thresholds for CJK->EN dialogue.
- I did not verify GEMBA-MQM performance on CJK->EN dialogue in this pass.

## Transcription: faster-whisper, model choice for CJK, VAD, alignment, diarization, speed

### Takeaway
faster-whisper's `BatchedInferencePipeline` gives roughly 3.7x speed-up over sequential decoding (large-v2: 63 s to 17 s for 13 min of audio on an RTX 3070 Ti) and turns Silero VAD on by default. For CJK accuracy, Whisper large-v3 and turbo are close to each other. Newer models (Qwen3-ASR-1.7B, SenseVoice, Fun-ASR-MLT-Nano) beat Whisper on some CJK sets, though the comparisons come from mixed sources. Qwen3-ForcedAligner reports far tighter word timing than WhisperX, especially on long audio. pyannote community-1 is the open diarization default, and precision-2 (paid API) is better.

### Cited Findings
- faster-whisper README, 13 min of audio, RTX 3070 Ti 8 GB, CUDA 12.4, beam 5:
  - large-v2 fp16: 1m03s sequential, 17s batched (batch 8, 6,090 MB).
  - large-v2 int8: 59s sequential, 16s batched (batch 8, 4,500 MB).
  - openai/whisper fp16 took 2m23s.
  - CPU, i7-12700K, 8 threads, `small` int8: 1m42s sequential, 51s batched (batch 8).
  - `BatchedInferencePipeline.transcribe` is a drop-in replacement. The VAD filter (Silero) "is enabled by default for batched transcription"; the default removes only silences longer than 2 s.
  — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- A faster-whisper issue reports, for 13 min of LibriSpeech at int8, large-v3-turbo 19.6 s (1,545 MB) against large-v3 52.6 s (GPU not named). — [faster-whisper issue #1030](https://github.com/SYSTRAN/faster-whisper/issues/1030)
- Vendor claims (unverified, sales pages): turbo on an RTX 4090 at about 80x real time for a single stream and about 175x batched at 16. A different vendor lists large-v3 int8 at about 25x on an RTX 4090. The two do not agree. — [gigagpu](https://gigagpu.com/rtx-4090-24gb-whisper-benchmark/); [spheron](https://www.spheron.network/blog/faster-whisper-gpu-cloud-production-deployment-guide/)
- CJK accuracy:
  - CommonVoice CER from the SenseVoice paper, reposted by an app vendor: Mandarin SenseVoice-Small 10.78% vs Whisper-large-v3 12.55%; Korean 8.28% vs 5.59%; Japanese 11.96% vs 10.34%.
  - FLEURS CER via Handy (Q8_0 quantized): Korean Qwen3-ASR-1.7B about 4.6%, large-v3 about 4.9%, turbo about 5.2%. Japanese Fun-ASR-MLT-Nano about 2.3%, large-v3 and turbo about 4.8%, Qwen3-ASR-1.7B about 5.3%.
  - Cantonese (vendor's own read-speech test): Qwen3-ASR 6.00% against about 37% for turbo and SenseVoice.
  — [whispernotes SenseVoice](https://whispernotes.app/blog/sensevoice-fastest-cjk-transcription); [Handy ko](https://models.handy.computer/languages/ko); [Handy ja](https://models.handy.computer/languages/ja); [whispernotes Qwen3-ASR](https://whispernotes.app/blog/qwen3-asr-vs-whisper)
- The faster-whisper README's distil-large-v3 example uses `language="en"`, and distil-large-v3 scored YT Commons WER 13.527 (English). — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- Qwen3-ForcedAligner-0.6B (Qwen3-ASR Technical Report, arXiv 2601.21337) claims a 67-77% relative cut in accumulated average shift. On MFA-labelled data the average shift is 42.9 ms vs WhisperX 133.2 ms and NFA 129.8 ms. On 300 s concatenated audio it is 52.9 ms vs WhisperX 2,708.4 ms. It covers 11 languages. These are author-reported figures. — [Qwen3-ASR report](https://arxiv.org/pdf/2601.21337)
- An independent September 2026 paper (arXiv 2609.15218) benchmarks Qwen3-FA against WhisperX, CrisperWhisper and Canary-v2. Per-language CJK numbers were not seen. — [arXiv 2609.15218](https://arxiv.org/pdf/2609.15218)
- pyannote DER (no collar, overlap included), legacy 3.1 / community-1 / precision-2: AMI-IHM 18.8 / 17.0 / 12.9; CALLHOME 28.5 / 26.7 / 16.6; VoxConverse 11.2 / 11.2 / 8.5; REPERE 7.9 / 8.9 / 7.4. On an H100, 1 h of audio takes 31-37 s with community-1 and 14 s with precision-2. Precision-2 is a paid pyannoteAI server model; community-1 has open weights. — [pyannote-audio 4.0.4 PyPI](https://pypi.org/project/pyannote-audio/4.0.4/); [community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1); [precision-2 blog](https://www.pyannote.ai/blog/precision-2)

### Inferences
- Use batched faster-whisper with turbo for CJK by default. Its CJK CER is within about 0.3 points of large-v3 on FLEURS ja/ko, and it ran about 2.7x faster in the int8 issue benchmark. Keep large-v3 as a quality option. Distil models are not an option for CJK (the README example is English; that they are English-only is my background knowledge, unverified here).
- Batched mode turns VAD on by default, which also reduces hallucinations on silence. When VAD is not used (non-batched), set `vad_filter=True` explicitly.
- For timing, run Qwen3-ForcedAligner after ASR. Its claimed advantage is largest on long audio, where WhisperX drifts by seconds.
- pyannote community-1 is the free default. It needs an H100-class GPU to reach about 2 min per hour of audio; on an RTX 30/40 card it will be slower, by how much I did not measure.

### Gaps
- I found no controlled real-time-factor numbers for turbo or large-v3 on RTX 30/40-series cards for CJK, or for CPU int8 large or turbo. The README covers only large-v2 on a 3070 Ti and `small` on CPU.
- stable-ts and ctc-forced-aligner were not checked. I found no per-segment language-ID evidence for mixed-language audio.
- I found no independent zh/ja/ko alignment-error comparison of Qwen3-FA and WhisperX.
- I found no diarization DER on CJK drama audio, and no consumer-GPU speeds for community-1.

## Hardsub and manga OCR

### Takeaway
PaddleOCR-VL (0.9B) leads both its own multilingual benchmark (Japanese edit distance 0.096) and an independent study of vertically written Japanese. For manga speech bubbles, manga-ocr remains the specialist baseline, and a newer 115M distilled model (Baberu OCR) claims to beat it. I found no head-to-head on hardsubs.

### Cited Findings
- PaddleOCR-VL, a 0.9B VLM, reports the lowest edit distance across evaluated scripts, Japanese 0.096 (in-house dataset). — [PaddleOCR-VL, arXiv 2510.14528](https://arxiv.org/pdf/2510.14528)
- An independent evaluation of multimodal LLMs on vertical Japanese text found PaddleOCR-VL achieved the highest scores of all models evaluated. — [arXiv 2511.15059](https://arxiv.org/pdf/2511.15059)
- Baberu OCR (115M, a manga-bubble reader) claims to beat manga-ocr, the model it was distilled from, on a public Japanese benchmark. This is self-reported. — [Baberu OCR model card](https://huggingface.co/genshiai-daichi/baberu-ocr)
- Surya: no CJK accuracy numbers found. One blog praises its layout analysis only. — [LlamaIndex blog](https://llamaindex.ai/blog/best-ocr-libraries-for-developers)

### Inferences
- For hardsub OCR (horizontal text on video frames), PaddleOCR or PaddleOCR-VL is the strongest-evidenced choice. For manga bubbles, keep manga-ocr and evaluate Baberu OCR as a smaller drop-in.

### Gaps
- I found no hardsub-specific benchmark (moving backgrounds, outlined subtitle fonts), and no measured speeds for PaddleOCR-VL against classic PaddleOCR on consumer GPUs.
- EasyOCR and Qwen-VL-class models were not benchmarked in any source I found.
