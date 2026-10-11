# Subtitle line segmentation and automatic quality flagging for CJK subtitles (as of October 2026)

Research date: 2026-10-11. All URLs were accessed on that date. "Unverified" marks claims I could not check in a primary source. Items dated after June 2026 are recent preprints and have not been peer reviewed unless noted.

## Segmentation: rule-based vs linguistic vs neural vs LLM splitting, and quantified comparisons

### Takeaway
I found no published, quantified comparison showing that LLM segmentation beats a rule-based or MLM-based splitter on readability, timing, or meaning preservation. Neither the research literature nor the IWSLT subtitling track treats LLM segmentation as a benchmarked advantage. Segmentation evidence is mostly pre-LLM, and the strongest cheap method that guarantees verbatim text is constraint-respecting scoring: a punctuation-likelihood MLM or rules, picking among candidate break points. LLMs show up in subtitling pipelines mainly for rephrasing and compression, and for translation, not for choosing break points.

### Cited Findings
**Style constraints (the targets any splitter must meet)**
- Netflix Japanese: 16 characters per line (SDH 18), at most 2 lines. Reading speed is up to 9 chars/sec for adult programs and 7 for children's (SDH 11 and 9). Keep text on one line unless it exceeds the limit. Prefer a bottom-heavy pyramid, and avoid leaving only one or two words alone on the top line. Never end a line with 、. The guide gives no particle-level break rules. — [Netflix Japanese Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007-Japanese-Timed-Text-Style-Guide)
- Netflix Simplified Chinese: 16 characters per line (SDH 18), 2 lines, 9 cps adult and 7 children's (SDH 11 and 9). Bottom-heavy pyramid. Use one speaker per line in dual-speaker events. Never end a line with an ideographic comma, a comma, or any period, and don't use periods in general. Full-width punctuation only. — [Netflix Simplified Chinese Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215994807-Simplified-Chinese-Timed-Text-Style-Guide)
- Netflix Korean: the fetched page reported 42 characters per line and 17 cps (adult; SDH 20). Its break rules ("don't separate a noun from its article…") look like the English template. UNVERIFIED and possibly a mis-extraction: I remember Korean limits as much lower (about 16 characters, about 12 cps). Re-check before relying on it. — [Netflix Korean Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215600497-Korean-Timed-Text-Style-Guide)

**Research on segmentation**
- The standard rule-based lower bound breaks before a 42-character limit. Papi et al. compare a seq2seq segmenter against this baseline. — [Papi et al. 2022, arXiv 2209.10608](https://arxiv.org/pdf/2209.10608)
- Sigma is a segmentation-specific metric derived from a BLEU upper bound on break boundaries. The authors argue that plain string metrics are inappropriate for judging subtitle segmentation. — [Karakanta et al., LREC 2022](https://aclanthology.org/2022.lrec-1.328.pdf)
- SubER is an edit distance with shifts that adds a time-overlap condition, so it scores segmentation and timing together with text. It was the primary IWSLT subtitling metric. — [Wilken et al., IWSLT 2022](https://aclanthology.org/2022.iwslt-1.1.pdf)
- Unsupervised MLM segmentation (Ponce, Etchegoyhen and Ruiz, ACL 2023) places line ends and subtitle breaks where punctuation is most likely, among candidate points that satisfy the length constraints. It is "competitive" on segmentation metrics, fully preserves the original text and complies with length constraints. Supervised in-domain models with audio access can be more accurate, but the MLM method is "highly portable across languages and domains". (The abstract gives no numbers; the tables are in the PDF.) — [ACL Anthology 2023.acl-short.67](https://aclanthology.org/2023.acl-short.67/)
- Other learned segmenters predate LLMs, for example the heterogeneous-data segmenter in "Point Break". — [CEUR-WS Vol-2769](https://ceur-ws.org/Vol-2769/paper_78.pdf)
- Eye-tracking evidence on readability: syntactically segmented two-line subtitles affect reading. — [The impact of text segmentation on subtitle reading, PMC7901653](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7901653/)

**IWSLT and LLMs**
- IWSLT 2024 findings: "using LLMs for rephrasing has emerged as one of the promising approaches", used by all subtitling participants. That is rephrasing and compression, not segmentation. — [Findings of IWSLT 2024, arXiv 2411.05088](https://arxiv.org/pdf/2411.05088) (quoted via search snippet)
- AppTek's IWSLT 2025 cascade optimizes line segmentation and subtitle templates for character limits, line counts and reading speed. Its MT uses length control to condense text. It reports results on BLEU, BLEURT and SubER. — [AppTek IWSLT 2025](https://preview.aclanthology.org/setup/2025.iwslt-1.21)
- An Estonian TV subtitling paper notes that SubER ignores paraphrase (the same meaning in different words), which penalizes LLM rewording. — [arXiv 2501.05234](https://arxiv.org/html/2501.05234v1)
- "Breaking Babel" / SMART (Jin et al., arXiv 2609.38660, v1 29 Sep 2026, v2 1 Oct 2026; not peer reviewed) is a multi-agent LLM subtitle translation system. It includes a "subtitle constraint validation" tool and SubMQM, which has 7 dimensions and 19 categories. According to a search summary (UNVERIFIED, not on the abstract page), the "Technical" dimension (line breaking, characters per line, lines per box) carries weight 0.06. It reports no segmentation or timing results, and it aligns human references by timestamp rather than penalizing re-segmentation. — [arXiv abs 2609.38660](https://arxiv.org/abs/2609.38660)

**LLM-adjacent alignment work**
- LLM-ForcedAligner keeps the transcript fixed and has an LLM predict the indices of timestamp slots. It reports zh and ja results. This is alignment, not resegmentation. — [arXiv 2601.18220](https://arxiv.org/html/2601.18220v1)
- An industrial LLM-ST paper lists the conventional pipeline as segment → timestamp → ASR → ITN → translate → realign. — [arXiv 2312.13585](https://arxiv.org/html/2312.13585v1)
- Japanese TV subtitles diverge from speech through timing lag, simplified written forms and omitted on-screen text. These are pitfalls for any realignment. — [arXiv 2103.14736](https://arxiv.org/pdf/2103.14736)

**What breaks in LLM pipelines and how tools guard against it**
- llm-subtrans (formerly gpt-subtrans) calls line merging by the model a "desync" failure mode. Larger batches are cheaper but carry more desync risk and can trigger retries. v0.2 changed prompting specifically to reduce desyncs "caused by GPT merging together source lines". It offers `--reparse`, `--preprocess` (splits long-duration lines from auto-transcription), `--postprocess` (adds line breaks to long lines), `--scenethreshold` (a gap in seconds that starts a new scene) and temperature 0 by default. — [llm-subtrans README](https://github.com/machinewrapped/llm-subtrans)
- VideoLingo advertises "NLP and AI-powered subtitle segmentation" with configurable length limits. Its word timings come from Qwen3-ASR plus Qwen3-ForcedAligner. The README warns that "Numbers and symbols may lack reliable word timings". — [VideoLingo README](https://github.com/Huanshere/VideoLingo)

### Inferences
- The cheap, safe architecture is: the LLM, or rules, or an MLM, only proposes break indices into the fixed character sequence, and code checks that the concatenated output equals the input. Timing is then re-derived from word or character timestamps. The MLM paper and LLM-ForcedAligner both work by never letting the model rewrite text. That design makes "changed text / hallucinated words / lost alignment" impossible by construction rather than something to detect after the fact.
- For CJK, the hard constraints (16 characters, 2 lines, about 9 cps for ja and zh, no comma or period at line end) are tight enough that a constrained search over candidate breaks scored by punctuation, particles or clause boundaries plus balance probably captures most of the readability gain. This is my inference; I found no CJK-specific benchmark.
- LLMs are most defensible for condensation, when CPS is exceeded and text must shrink. That is a translation-side task, as the IWSLT trend shows.

### Gaps
- No quantified LLM-vs-rule segmentation comparison (readability, timing, meaning, tokens) was found for any language, let alone zh, ja or ko.
- No published Japanese bunsetsu or Korean eojeol subtitle line-break study was retrieved. I did not reach the IWSLT 2025 findings tables. Punctuation-restoration models for CJK were not researched in this pass.
- The Netflix Korean figures need re-verification.
- The full numbers from Ponce et al. 2023 are only in the PDF, which I did not read.

## Splitting long ASR segments in tools (WhisperX, stable-ts, Subtitle Edit, others): algorithms and defaults

### Takeaway
stable-ts has the most explicit, documented regrouping pipeline, and it handles CJK punctuation (。，？) by default. WhisperX's README documents VAD-based chunking but not a sentence or length splitter: sentence segmentation is still listed as a TODO there. Subtitle Edit's defaults are only documented by users (about 42–43 characters per line, at most about 25 cps), not officially in what I could fetch.

### Cited Findings
- stable-ts default regrouping is the string `"da"` = `isp_cm_sp=.* /。/?/？_sg=.5_sp=,* /，++++50_sl=70_cm`. That expands to: ignore_special_periods → clamp_max → split_by_punctuation on `. 。 ? ？` → split_by_gap(0.5 s) → split_by_punctuation on `, ，` only when the segment is at least 50 characters → split_by_length(70 characters) → clamp_max. — [stable-ts README](https://github.com/jianfch/stable-ts)
- stable-ts method defaults:
  - `split_by_gap` max_gap=0.1 (the preset uses 0.5).
  - `split_by_length` even_split=True.
  - `merge_by_gap` min_gap=0.1.
  - `clamp_max` medium_factor=2.5.
  - `refine()` prob_threshold=0.5, rel_prob_decrease=0.3, abs_prob_decrease=0.05. These adjust timestamps by measuring the drop in token probability while audio is muted.
  - `remove_words_by_str(min_prob=…)` and `custom_operation` can act on word probability.

  — [stable-ts README](https://github.com/jianfch/stable-ts)
- WhisperX:
  - Uses "VAD-based segment transcription" ("reduces hallucination & batching with no WER degradation").
  - `condition_on_prev_text` is False by default, to reduce hallucination.
  - Uses wav2vec2 alignment, with defaults for en, fr, de, es and it and other languages via Hugging Face.
  - "Sentence-level segments (nltk toolbox)" is on the TODO list.
  - Words with characters outside the alignment dictionary (e.g. "2014.", "£13.60") get no timing, and overlapping speech is handled poorly.

  The README does not discuss CJK character alignment (`return_char_alignments=False` appears in the example). — [WhisperX README](https://github.com/m-bain/whisperX)
- Subtitle Edit, user-reported practice only (UNVERIFIED as official defaults):
  - "Single line max length" is set under Settings → General, with 42 or 43 characters recommended.
  - Keep reading speed at or below 25 cps, ideally 10–20.
  - "Merge short lines" merges distinct timed subtitles.
  - "Unbreak subtitles shorter than 43" plus the Fix Common Errors option "Remove line breaks in short texts".

  — [VideoHelp forum](https://forum.videohelp.com/showthread.php?p=2604084); [GoTranscript Subtitle Edit transcript](https://gotranscript.com/public/mastering-subtitle-edit-efficient-line-breaking-and-splitting-techniques)
- A non-LLM realignment recipe: Whisper transcription → DTW alignment of subtitle cues to words → re-segmentation with corrected timestamps. — [arXiv 2606.07608](https://arxiv.org/pdf/2606.07608)

### Inferences
- stable-ts's 70- and 50-character thresholds are tuned for Latin text. For ja and zh they are about 4× too long against Netflix's 16 characters per line. A CJK pipeline should pass max_chars of about 16–32 (1–2 lines) and a CPS check rather than rely on the defaults.
- The ordering stable-ts uses (sentence punctuation → pause gap → comma only if long → hard length cap → duration clamp) is a reasonable, documented default sequence to copy for a rule-based splitter.

### Gaps
- I did not fetch the Subtitle Edit source or official help for its CJK auto-break logic (the nikse.dk help page returned no content), or its official defaults.
- I did not obtain the internals of VideoLingo's LLM split (spaCy versus LLM, prompts, verification) or llm-subtrans's ID and validation scheme. Both READMEs were silent on these.

## Quality flagging: which signals catch wrong lines, and at what precision and cost

### Takeaway
Whisper's built-in scalars (avg_logprob < −1.0, compression_ratio > 2.4, no_speech_prob > 0.6) are cheap, standard filters but weak hallucination classifiers. A 2026 study found that classifiers built on them reach only about 24% F1, and that probing the decoder's internal states works best. Translation QE error-span detectors (xCOMET, CometKiwi, GEMBA-MQM, reasoning-LLM judges) reach only about 17–27 character-level F1 on WMT24. Their highlights are 2–4× more likely than chance to land on real errors, but they are still mostly wrong (precision 0.20–0.28). LLM judges beat xCOMET only modestly, at far higher cost.

### Cited Findings
**ASR signals**
- Whisper's fallback defaults are compression_ratio_threshold 2.4, logprob_threshold −1.0 and no_speech_threshold 0.6. A segment counts as silent only if no_speech_prob is high AND avg_logprob is low. Compression ratio uses zlib, so repetitive text compresses well and scores high. — [Whisper transcribe API docs (mirror)](https://www.mintlify.com/openai/whisper/api/transcribe); [Deepgram, Exploring Whisper](https://deepgram.com/learn/exploring-whisper)
- Jasiński et al. (arXiv 2606.23060, 22 June 2026, accepted at Interspeech 2026) studied Whisper large-v3 hallucinations on real, human-annotated speech:
  - Text-based classifiers have high recall but degrade without references.
  - LLM-based detection improves precision with domain-specific prompts but is less competitive than lightweight text methods.
  - Decoder internal-state probes are strongest, and late fusion of text and probe outputs is best.

  A search summary reported that a logistic regression on avg_logprob, compression ratio and no_speech reached only 23.6% F1. That figure is UNVERIFIED against the paper body: the abstract page has no numbers. — [arXiv 2606.23060](https://arxiv.org/abs/2606.23060)
- Using the default −1.0 and 0.6 thresholds on non-speech audio, hallucination rates of about 73–87% were reported across models and splits. That shows these filters do not prevent hallucinations on silence and noise (search-summary figure, UNVERIFIED in the body). — [arXiv 2606.07473](https://arxiv.org/pdf/2606.07473)
- Deepgram notes that no_speech_threshold needs tuning per model size and noise level, and recommends pairing it with VAD. — [Deepgram](https://deepgram.com/learn/exploring-whisper)
- Community tools stack stricter ad-hoc filters, such as avg_logprob < −0.9, text length versus duration, and no_speech combined with compression ratio. These are unvalidated heuristics. — [whisper-webui parameter file (HF Space)](https://huggingface.co/spaces/LAP-DEV/Demo/blob/786cdf2aae01779daa8665fde3968c7c88a7c11a/modules/whisper/whisper_parameter.py)

**Translation QE and LLM judges**
- On a refined WMT24 ESA set, a reference-free reasoning-model detector reached about 27 character-level F1 versus about 17.1 for reference-based XCOMET-XXL (WMT 2025 paper). — [aclanthology 2025.wmt-1.62](https://aclanthology.org/2025.wmt-1.62.pdf) (via search snippet; tables not read)
- xCOMET combines a sentence score with error-span detection and is competitive with GPT-4 GEMBA at system level. — [xCOMET, arXiv 2310.10482](https://arxiv.org/pdf/2310.10482)
- GEMBA-MQM (GPT-4) reached 96.5% system-level pairwise accuracy versus 95.2% for XCOMET-Ensemble on WMT23. XCOMET had the better meta-correlation (0.825 vs 0.802). — [GEMBA-MQM, arXiv 2310.13988](https://arxiv.org/pdf/2310.13988)
- GemSpanEval (2025), a generative span detector, is reported as "competitive" with XCOMET. — [arXiv 2510.24707](https://arxiv.org/pdf/2510.24707v1)
- QE4PE measured highlight precision and recall against real post-edits (En→It and En→Nl, NMT output):
  - xCOMET supervised highlights: precision 0.28 and 0.24, recall 0.22 and 0.33.
  - Unsupervised MC-dropout uncertainty highlights: precision 0.25 and 0.20, recall 0.21 and 0.36.
  - Human "oracle" highlights: precision 0.37 and 0.26, recall 0.45 and 0.53.
  - Supervised and unsupervised did not differ significantly.
  - Highlighted spans were about 2–4× more likely to be edited (likelihood ratio Λ ≈ 2.0–3.4).

  — [QE4PE, Sarti et al., TACL 2025, arXiv 2503.03044](https://arxiv.org/html/2503.03044v2)

**Rule checks for subtitles**
- SubMQM (Breaking Babel, Sep 2026) treats line breaking, characters per line and lines per box as a "Technical" error dimension. SMART runs a "subtitle constraint validation" tool, which amounts to rule checks inside an agent loop. — [arXiv 2609.38660](https://arxiv.org/abs/2609.38660)
- WhisperX leaves numbers and symbols outside the alignment dictionary unaligned, which makes "untimed token" a cheap, high-precision flag. — [WhisperX README](https://github.com/m-bain/whisperX)

### Inferences
- Cheap and effective: deterministic rule checks (CPS above the limit, characters per line, duration too short or long, overlaps, source-script characters left in the English target, number and name mismatch between source and target, failed alignment). They have near-perfect precision for the property they test. Then n-gram repetition and compression ratio for looping hallucinations, and VAD disagreement (text where VAD says no speech) for silence hallucinations.
- Expensive and weak: per-line LLM-as-judge or MQM annotation and back-translation. Published span-F1 for the best LLM judges is still around 27, and LLM detection lost to lightweight text methods on Whisper hallucinations.
- Mid-cost and moderately useful: CometKiwi/xCOMET-QE as a sentence-level ranking signal, not as span highlights.
- Ensemble disagreement between two ASR models or two translations is plausible and cheap if both outputs already exist. I found no precision or recall numbers for it (gap).

### Gaps
- No subtitle-specific or CJK-specific precision/recall numbers for any flagging signal were found. All QE numbers are for European target languages on news or social text, and the ASR study is on Whisper large-v3, not CJK drama audio.
- No sourced data on back-translation as a flagging signal, on MetricX-QE versus CometKiwi for zh/ja/ko→en, or on two-ASR disagreement.
- The exact F1 figures from arXiv 2606.23060 and 2606.07473 are unverified (they came from search summaries).

## Human-in-the-loop review: presenting flags so reviewers fix more errors per minute; flag fatigue

### Takeaway
The best controlled evidence is QE4PE (42 professional post-editors), and it is sobering. Error highlights from QE models had only about 25% precision, most translators did not find highlights useful, and the share of highlighted text was a main driver of longer editing time. Benefit depended more on domain, language and the individual editor than on the highlighting method. This supports few, high-precision, ranked flags over dense span highlighting.

### Cited Findings
- QE4PE, effect of highlights on editing:
  - En→It editors with highlights edited more than twice as much as those without. En→Nl editors with highlights edited about 33% less.
  - Highlights significantly increased En→It speed (p<0.001) but had minimal effect for En→Nl.
  - On biomedical text about two-thirds of translators were up to 2× slower with highlights. On social-media text the same share was up to 3× faster.

  — [QE4PE](https://arxiv.org/html/2503.03044v2)
- QE4PE, flag fatigue:
  - Regression found the proportion of highlighted characters was a main driver of longer editing times, which the authors interpret as cognitive load.
  - Translators said highlights were "more of an eye distraction, as they often weren't actual mistakes", and some "disregarded the highlights".
  - Most translators did not find highlights useful for productivity or quality, even oracle highlights.

  — [QE4PE](https://arxiv.org/html/2503.03044v2)
- QE4PE, critical errors: highlighted conditions led to 16–20% more inserted critical errors being edited than no highlights. Unsupervised uncertainty highlights covered all inserted critical errors. — [QE4PE](https://arxiv.org/html/2503.03044v2)
- QE4PE: the gap between automated and human-made highlights was modest, which the authors read as a gap between QE accuracy and practical usefulness. — [QE4PE TACL page](https://preview.aclanthology.org/credits/2025.tacl-1.64)

### Inferences
- Flag precision matters more than recall for throughput. Surfacing about 25%-precision span highlights across all text slows people down, but flags still help catch critical errors.
- Implication for design:
  - Flag at line level, not span level.
  - Rank lines by a combined risk score (rule violations first, then ASR confidence and QE) under a review budget, for example "review the top N% riskiest".
  - Show the reason for each flag (e.g. "CPS 14 > 9", "repeated phrase", "no speech detected").
  - Keep the default visible flag rate low.

  These are inferences; I found no controlled subtitle-review UI study.

### Gaps
- No empirical study of subtitle-review UIs (ranking, thresholds, review budgets, errors fixed per minute) was found.
- No flag-fatigue study for ASR-confidence highlighting in transcript editors was found. QE4PE is MT post-editing for European languages only.
