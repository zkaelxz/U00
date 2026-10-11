# Hosted Thai ASR vs open models, and how to benchmark Thai ASR (as of 2026-10-11)

Scope note: this pass used 16 web calls. Each fact below has a source, and each is marked **[vendor]** (the vendor's own claim), **[independent]** (third-party or academic) or **[docs]** (official product documentation). I could not fetch the dataset cards for Common Voice, FLEURS, Gowajee, TSync or Thai Elderly Speech. Any statement about those that rests on prior knowledge, not a fetched source, is marked **[unverified, prior knowledge]**, and the Gaps sections list it.

## 1. Hosted options: Thai quality, price, limits, timestamps, retention

### Takeaway
Several hosted engines support Thai. The independent evidence only partly agrees with vendor claims:
- ElevenLabs Scribe v2, Azure and Google Chirp 3 beat Whisper large-v3 on one independent leaderboard.
- Gemini 3.0 Flash roughly ties Whisper large-v3 there.
- gpt-4o-transcribe does much worse.

For subtitles, timestamp support decides more than accuracy does:
- Google Chirp 3 gives Thai word timestamps only in batch mode, capped at 20 minutes per request.
- OpenAI gives word timestamps only on `whisper-1`.
- ElevenLabs Scribe advertises character-level timestamps.

Every hosted option means uploading the owner's audio to a third party.

### Cited Findings
**Google Chirp 3 (Cloud Speech-to-Text V2)**
- Thai (`th-TH`) is listed only as **Preview** for Chirp 3 transcription, not GA. It is listed for StreamingRecognize, Recognize and BatchRecognize. **[docs]** — [Google Chirp 3 docs](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3)
- Word-level timestamps are optional. They exist only in Recognize and BatchRecognize, and Google says "some transcription degradation is expected" when they are on. **[docs]** — [Google Chirp 3 docs](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3)
- Duration limits **[docs]** — [Google Chirp 3 docs](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3):
  - Recognize: under 1 minute.
  - BatchRecognize: 1 minute to 1 hour, but only up to **20 minutes with word timestamps**.
  - Chirp 3 does not support diarization for Thai.
- Price **[docs]** — [Google Speech-to-Text product page](https://cloud.google.com/speech-to-text):
  - The V2 API lists **$0.016/min (about $0.96/h)**.
  - A third-party aggregator lists dynamic batch at about $0.003/min (about $0.18/h); I did not confirm this on an official page — [costbench](https://costbench.com/software/ai-transcription-apis/google-speech-to-text/).
  - Billing rounds up to the nearest 1 s, per older release notes — [release notes](https://docs.cloud.google.com/speech-to-text/docs/release-notes).

**Google Gemini audio models**
- Independent results:
  - GigaSpeechBench Thai WER: Gemini 3.0 Flash 26.58%, against Whisper large-v3 at 27.02%. This is roughly a tie. **[independent]** — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench)
  - Typhoon paper, Gemini 3 Pro CER: 10.95 on TVSpeech, 12.50 on GigaSpeech2-Typhoon, 11.35 on FLEURS original references and 6.91 on FLEURS normalised references. **[independent but from a competing vendor, SCB10X]** — [Typhoon ASR Real-time paper, arXiv 2601.13044](https://arxiv.org/html/2601.13044)
  - The Typhoon authors argue that general-purpose models are not tuned for strict verbatim transcription, which inflates their CER under strict normalisation. — [search summary of arXiv 2601.13044](https://arxiv.org/pdf/2601.13044)
- Gemini 2.5 Pro scored 10.20 CER on the Isan-dialect test set. **[independent/SCB10X]** — [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
- I did not fetch Gemini pricing, timestamp reliability, file limits or data-use terms in this pass (see Gaps).

**OpenAI**
- File limit is **25 MB** (mp3, mp4, mpeg, mpga, m4a, wav, webm). **[docs]** — [OpenAI speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text)
- Models **[docs]** — [OpenAI speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text):
  - `gpt-transcribe` is now the recommended model.
  - `gpt-4o-transcribe` and `gpt-4o-mini-transcribe` remain for existing integrations.
  - `gpt-4o-transcribe-diarize` adds speaker labels.
  - **Only `whisper-1` supports `timestamp_granularities[]`**, that is, word or segment timestamps for subtitles.
- Prices (third-party sources):
  - gpt-4o-transcribe and whisper-1: $0.006/min ($0.36/h).
  - gpt-4o-mini-transcribe: $0.003/min.
  - gpt-transcribe: $0.0045/min. — [Holori](https://holori.com/openai-pricing-guide/), [TokenMix](https://tokenmix.ai/blog/gpt-4o-transcribe-speech-to-text-api-guide-2026), [digitalapplied](https://www.digitalapplied.com/blog/openai-gpt-transcribe-stt-model-comparison-2026)
- GigaSpeechBench Thai WER for gpt-4o-transcribe is **48.78%**, the worst of the 14 listed systems and far behind Whisper large-v3 (27.02%). **[independent]** — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench)

**ElevenLabs Scribe**
- Price **$0.40/h**, with character-level timestamps, diarization and audio-event tagging (e.g. music or laughter tags). Thai is in the "Good" tier, defined as >10% to ≤25% WER. **[vendor]** — [ElevenLabs Thai STT page](https://elevenlabs.io/speech-to-text/thai)
- The vendor's FLEURS Thai WER table lists Scribe v1 11.6%, Gemini 2.0 Flash 17.9%, Whisper large-v3 35.6% and Deepgram Nova-2 76.1%. **[vendor]** — [ElevenLabs Thai STT page](https://elevenlabs.io/speech-to-text/thai)
- The same page also claims "3.1% WER on FLEURS", which contradicts its own table and tier. Treat the 3.1% figure as unreliable. **[vendor, self-contradictory]** — [ElevenLabs Thai STT page](https://elevenlabs.io/speech-to-text/thai)
- GigaSpeechBench Thai WER for Scribe v2 is **13.90%**, the best hosted system there and second overall. **[independent]** — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench)

**Azure Speech**
- GigaSpeechBench Thai WER for Azure is **15.66%**. **[independent]** — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench)
- I did not fetch Azure pricing, limits or timestamp docs (see Gaps).

**Other hosted or semi-hosted systems on GigaSpeechBench (Thai WER)** **[independent]** — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench):

| System | Thai WER |
|---|---|
| FunASR-realtime | 10.76% |
| Qwen3.5-Omni-Plus | 15.10% |
| Qwen3-ASR-1.7B (open weights) | 15.14% |
| Qwen3-ASR-Flash | 17.08% |
| Chirp 3 | 17.52% |
| FunASR-MLT-Nano | 20.75% |
| Dolphin-small | 24.40% |
| Meta OmniASR-3B | 30.72% |

**Soniox**
- Price $0.10/h async and $0.12/h streaming, flat rate including timestamps, diarization and confidence. Regional deployment in US, EU and JP. The comparison page claims "60+ languages" but does not list them or give any Thai numbers. **[vendor]** — [Soniox compare page](https://soniox.com/compare-stt), [Soniox pricing](https://soniox.com/pricing)

**Deepgram, AssemblyAI, Speechmatics: prices only (third-party), Thai support not confirmed**
- Deepgram Nova-3 is about $0.0077/min ($0.46/h). — [AssemblyAI pricing blog](https://www.assemblyai.com/blog/speech-to-text-api-pricing)
- AssemblyAI is quoted at $0.15–$0.21/h batch, depending on model. — [AssemblyAI pricing blog](https://www.assemblyai.com/blog/speech-to-text-api-pricing), [convertaudiototext](https://convertaudiototext.com/blog/speech-to-text-api-pricing-2026)
- Speechmatics is quoted at $0.80–$1.04/h batch, though another source says pricing is sales-only. — [convertaudiototext](https://convertaudiototext.com/blog/speech-to-text-api-pricing-2026), [vexascribe](https://vexascribe.com/compare/best-transcription-api-for-developers)
- Deepgram Nova-2 Thai WER was 76.1% on FLEURS, per ElevenLabs. **[vendor, competitor's claim]** — [ElevenLabs Thai page](https://elevenlabs.io/speech-to-text/thai)

**Typhoon (SCB10X)**
- SCB10X publishes **open-weight** Thai ASR models: Typhoon Whisper Large-v3, Typhoon Whisper Turbo, Typhoon ASR Realtime (a FastConformer-Transducer) and an Isan-dialect model. These run locally, so no audio leaves the PC. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044), [HF model card](https://huggingface.co/scb10x/typhoon-asr-realtime), [Typhoon Isan blog](https://opentyphoon.ai/blog/en/typhoon-isan-release)
- A later paper adds a streaming model steerable with shallow fusion. I could not read its contents (only the PDF metadata was available). — [arXiv 2609.14991](https://arxiv.org/pdf/2609.14991)

### Inferences
- **Best evidence-backed hosted pick for hard clips:** ElevenLabs Scribe v2. It is ahead on both the vendor's own table and an independent leaderboard, costs about $0.40/h, and returns sub-word timestamps that suit subtitle timing.
- **Fallback:** Google Chirp 3 batch, with word timestamps and clips of 20 minutes or less. Thai is still Preview, and Google warns that enabling timestamps degrades accuracy.
- **Avoid for Thai subtitles:** OpenAI's gpt-4o family. It ranks worst on Thai in the independent table and has no word timestamps. `whisper-1` has timestamps but is roughly Whisper large-v2/v3-class quality, which the owner can already run locally for free.
- **Strongest local alternative:** a Thai fine-tuned open model, Typhoon Whisper Large-v3 or Pathumma-Whisper. On SCB10X's own benchmark these reach about 5–6% CER, *lower* than Gemini 3 Pro (11–12.5% CER). That benchmark comes from a vendor of the winning model, so confirm it on the owner's own bakeoff.
- **Privacy trade-off:** every hosted option above requires uploading the owner's audio to the provider. The default should stay local, with hosted ASR as an opt-in for specific clips.

### Gaps
- Not fetched or verified in this pass:
  - Gemini API audio pricing, file and duration limits, and timestamp support.
  - Azure Speech Thai pricing, limits and word timestamps.
  - AWS Transcribe `th-TH` support, price and limits.
  - Deepgram, AssemblyAI, Soniox and Speechmatics Thai support (none of the fetched pages listed Thai).
  - Typhoon's *hosted* API terms.
  - Botnoi Voice and VISTEC/AIResearch hosted ASR services.
- Data-retention and training-use terms: not verified for any vendor. No fetched page stated them, so the owner must read each vendor's data-use policy before uploading anything.
- Handling of music and background noise: no source gave Thai-specific evidence. Only ElevenLabs advertises audio-event tagging. TVSpeech (YouTube media) is the closest robustness proxy, and no hosted engine other than Gemini 3 Pro was scored on it.
- ElevenLabs page dating: the page appears partly stale (Scribe v1 era, real-time "coming soon"). Its comparison numbers use older competitor versions.

## 2. Independent Thai ASR comparisons (2024–2026): numbers, metrics, datasets

### Takeaway
I found only two usable comparisons that include hosted engines on Thai:
- **GigaSpeechBench**, an independent leaderboard that reports Thai as WER with an undocumented tokenisation.
- **The Typhoon ASR paper** (SCB10X, January 2026), which reports CER on TVSpeech, GigaSpeech2 and FLEURS under a strict normalisation, but includes only one hosted model, Gemini 3 Pro.

Metrics, normalisation and test sets all differ, so numbers are not comparable across sources.

### Cited Findings
- **GigaSpeechBench (SpeechColab)**, "Low-Resource Southeast Asian" module, Thai column, labelled WER %. **[independent]** — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench)

  | System | Thai WER |
  |---|---|
  | FunASR-realtime | 10.76 |
  | ElevenLabs Scribe v2 | 13.90 |
  | Qwen3.5-Omni-Plus | 15.10 |
  | Qwen3-ASR-1.7B | 15.14 |
  | Azure | 15.66 |
  | Qwen3-ASR-Flash | 17.08 |
  | Chirp 3 | 17.52 |
  | Gemini 3.0 Flash | 26.58 |
  | Whisper large-v3 | 27.02 |
  | gpt-4o-transcribe | 48.78 |

  - The page does not name the Thai test set, does not define Thai word tokenisation, and does not document normalisation. Segments are filtered to longer than 0.5 s. The page dates the results only as "© 2026" and links a technical report as arXiv 2606.28884, which I did not fetch.
  - Against Whisper large-v3, the gains are: Scribe v2 −13.1 points (about 49% relative), Azure −11.4 points, Chirp 3 −9.5 points, Gemini 3.0 Flash −0.4 points (a tie). gpt-4o-transcribe is +21.8 points worse.
- **Typhoon ASR Real-time paper** (arXiv 2601.13044, SCB10X, metadata dated 2026-01). Metric is CER %. Whisper-based numbers are from Thai fine-tunes. **[academic, from a vendor of competing models]** — [arXiv 2601.13044 HTML](https://arxiv.org/html/2601.13044)

  | Model | TVSpeech | GigaSpeech2-Typhoon | FLEURS original | FLEURS normalised |
  |---|---|---|---|---|
  | Gemini 3 Pro | 10.95 | 12.50 | 11.35 | 6.91 |
  | Pathumma-Whisper Large-v3 | 10.36 | 5.84 | 6.29 | 7.88 |
  | Biodatlab Distil-Whisper Large | 13.82 | 8.24 | 6.77 | 8.63 |
  | Biodatlab Whisper Large | 18.96 | 13.22 | 16.50 | 15.26 |
  | Typhoon ASR Realtime (streaming) | 9.99 | 6.81 | 13.87 | 9.68 |
  | Typhoon Whisper Turbo | 6.85 | 4.79 | 10.52 | 7.08 |
  | Typhoon Whisper Large-v3 | 6.32 | 4.69 | 9.98 | 5.69 |

  - The paper does not report vanilla OpenAI Whisper large-v3.
  - It says WER is "inapplicable to Thai" and uses CER, but does not define the CER computation (space or punctuation handling).
- **Normalisation changes rankings.** On FLEURS, Gemini 3 Pro's CER falls from 11.35 to 6.91 when references are normalised to Typhoon's guidelines. This shows that reference conventions (digits, ๆ, transliteration) move scores by several points. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
- **ElevenLabs FLEURS Thai WER table** (Scribe v1 11.6, Gemini 2.0 Flash 17.9, Whisper large-v3 35.6). **[vendor]** — [ElevenLabs Thai page](https://elevenlabs.io/speech-to-text/thai)
- **Another arXiv paper** reportedly gives Whisper large-v3 at 20.44 CER on GigaSpeech2 and 6.02 on Common Voice. This comes from a search-result summary only; I did not open the paper to confirm the numbers or the metric. — [arXiv 2505.22063](https://arxiv.org/pdf/2505.22063)
- **Related benchmarks** that may contain Thai rows were found but not read: SEA-SpeechBench and SEABED (both September 2026). — [SEA-SpeechBench arXiv 2609.09672](https://arxiv.org/pdf/2609.09672), [SEABED arXiv 2609.22586](https://arxiv.org/pdf/2609.22586)
- **Artificial Analysis** runs a general STT leaderboard; a Thai breakdown was not confirmed. — [Artificial Analysis STT](https://artificialanalysis.ai/speech-to-text)

### Inferences
- **Hosted models that beat vanilla Whisper large-v3 on Thai (independent evidence, GigaSpeechBench only):**
  - Scribe v2: much better.
  - Azure: much better.
  - Chirp 3: clearly better.
  - Gemini 3.0 Flash: no meaningful gain.
  - gpt-4o-transcribe: worse.
- **Thai fine-tuned open Whisper models** (Typhoon, Pathumma) appear competitive with or better than the hosted LLM-style engines. This comes from a non-neutral source, so it is a hypothesis for the owner's own bakeoff to test, not a settled fact.
- **"WER" for Thai** without a stated tokenizer (GigaSpeechBench, ElevenLabs) cannot be compared with CER numbers, nor with WER from another tokenizer.

### Gaps
- No neutral, documented Thai CER bakeoff covering several hosted APIs exists in what I found. GigaSpeechBench's Thai protocol is undocumented on the README, and its tech report was not read.
- Benchmarks without Thai numbers in what I found: Azure and AWS have no vendor-published Thai figures that I saw, and no independent Thai benchmark covered Soniox, Speechmatics, AssemblyAI or AWS.
- Contents of SEA-SpeechBench, SEABED and Typhoon ASR Streaming (2609.14991) were not extracted.

## 3. Public datasets for a small Thai test set

### Takeaway
For a quick bakeoff, use three sets:
- **FLEURS Thai** (read speech, clean): the standard comparison point.
- **GigaSpeech2 Thai TEST**: 10 h of human-transcribed, YouTube-sourced, spontaneous and noisy speech, the closest to video. Access is gated and non-commercial.
- **Common Voice Thai** (read, crowd-recorded, varied microphones).

TVSpeech from the Typhoon benchmark is the best "hard YouTube media" set if it is downloadable, since it is CC-BY and 3.75 h. Score locally from references; never redistribute the audio.

### Cited Findings
- **GigaSpeech2 Thai.** About 12,901.8 h raw and 10,262.0 h refined of training data, which is automatically transcribed and so has noisy labels. DEV and TEST are 10 h each per language, transcribed by **professional human annotators**. — [GigaSpeech2 paper arXiv 2406.11546](https://arxiv.org/html/2406.11546v2), [HF card mirror](https://huggingface.co/datasets/vanmanhnew/dataset/blob/main/README.md)
- **GigaSpeech2 licence is ambiguous.**
  - The HF repository is tagged Apache-2.0.
  - Audio access is gated, restricted to non-commercial research and education, and SpeechColab does not own the audio copyright.
  - The authoritative terms are on the official repository: [voxkitchen catalog](https://voxkitchen.readthedocs.io/en/latest/datasets/gigaspeech2/), [github.com/SpeechColab/GigaSpeech2](https://github.com/SpeechColab/GigaSpeech2).
- **TVSpeech** (Typhoon benchmark, "Robustness track"):
  - 570 utterances, 3.75 h, rated "Hard".
  - Drawn from public YouTube media channels, covering finance, technology and variety vlogs dense with domain terms and proper names.
  - Licence stated as **CC-BY**.
  - References were re-normalised to Typhoon's guidelines.
  - Source: [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
- **GigaSpeech2-Typhoon:** a fixed 1,000-utterance (1.01 h) subset of GigaSpeech2, re-normalised. Its licence is not stated. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
- **SCB 10X Thai Dialect Isan Dataset** has a held-out test set; its size and licence are not stated in the paper. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
- **FLEURS Thai** is used as the standard benchmark by Google, ElevenLabs and Typhoon. — [Gemini report arXiv 2312.11805](https://arxiv.org/pdf/2312.11805), [ElevenLabs](https://elevenlabs.io/speech-to-text/thai), [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
  - The HF card fetch failed. **[unverified, prior knowledge]:** config `th_th`, CC-BY-4.0, read speech of FLoRes Wikipedia sentences, about 3 recordings per sentence, `raw_transcription` and `transcription` (normalised) fields, test split roughly a few hundred sentences per language.

### Inferences
- **Recommended quick bakeoff, about 40 clips:**
  - 15 clips from the GigaSpeech2 Thai TEST set, or TVSpeech if obtainable. These are spontaneous YouTube speech, the most like the owner's videos.
  - 15 clips from the FLEURS `th_th` test set, for clean read speech and comparability with published numbers.
  - 10 clips from the Common Voice Thai test split, for crowd microphones and accents.
  - Optionally add 5–10 of the owner's *own* hard clips, hand-transcribed, scored only locally and never uploaded unless the owner accepts the privacy cost.
- **Sampling:**
  - Fix a random seed.
  - Stratify by duration: keep 3–20 s, so a 16 kHz mono FLAC or 64 kbps Opus clip stays under 1 MB.
  - Prefer distinct speakers.
  - Exclude clips whose references contain digits or Latin script unless testing normalisation on purpose.
- **Avoid redistribution:** store only dataset IDs, utterance IDs and a manifest script. Each user downloads the audio from the source under its own terms, and the manifest commits no audio.
- **Licence note:** GigaSpeech2 is non-commercial, which is fine for a hobbyist's private eval but should not be bundled. Uploading GigaSpeech2 or Common Voice clips to a hosted API for evaluation is a separate question for the terms of use; public read datasets carry no personal-privacy issue of the owner's own.

### Gaps
- Dataset cards not fetched in this pass:
  - Common Voice Thai: version and hour count. CC0 is **[unverified, prior knowledge]**; the latest Thai hours and version number are unknown.
  - FLEURS Thai: licence and size. Prior knowledge only.
  - Gowajee: a Chulalongkorn command-style corpus. Licence and size not verified.
  - TSync1/TSync2 (NECTEC TTS corpora, read single-speaker): not verified.
  - Thai Elderly Speech: not verified.
  - Thai TV or YouTube subtitled corpora other than TVSpeech and GigaSpeech2: none found.
- **TVSpeech availability:** whether it is published as a downloadable HF dataset (and under what repo id) was not confirmed. Only the paper's description was seen.
- **Common Voice access:** whether Common Voice is still on HF or only on Mozilla Data Collective in 2026 was not checked.

## 4. Thai scoring protocol (CER vs WER, normalisation, tools)

### Takeaway
Use **CER as the primary metric**, computed after a fixed, documented normalisation applied to *both* reference and hypothesis. Optionally add WER on PyThaiNLP `newmm` word segmentation as a secondary number.

The normalisation choices move scores by several points (Gemini 3 Pro on FLEURS: 11.35 to 6.91 CER), so report the normalisation alongside every number.

### Cited Findings
- The Typhoon benchmark uses CER as primary because Thai has no explicit word boundaries, treats WER as secondary or inapplicable, and notes that CER can penalise valid stylistic choices. — [emergentmind Typhoon benchmark summary](https://www.emergentmind.com/topics/typhoon-asr-benchmark), [arXiv 2601.13044](https://arxiv.org/html/2601.13044)
- Google's Gemini report scores Thai (with Mandarin, Japanese and Korean) on FLEURS with CER because these are "segmented" languages. — [Gemini report arXiv 2312.11805](https://arxiv.org/pdf/2312.11805)
- **Typhoon normalisation rules**, from their guidelines (Na-Thalang et al. 2025) — [arXiv 2601.13044](https://arxiv.org/html/2601.13044):
  - Digits are written as spoken Thai words, for example 10150 becomes หนึ่งศูนย์หนึ่งห้าศูนย์.
  - Ranges are spelled out, so 6-7 becomes หกถึงเจ็ด.
  - Times are spelled out.
  - **ๆ is expanded** to a repeated word (เก่งๆ becomes เก่ง เก่ง).
  - Foreign words are transliterated into Thai (website becomes เว็บไซต์).
  - Space handling is not stated explicitly.
- GigaSpeechBench uses WER for "alphabetic" languages and CER for CJK, yet scores Thai as WER with no stated tokenizer. — [GigaSpeechBench](https://github.com/SpeechColab/GigaSpeechBench)

### Inferences (recommended protocol for the owner)
1. **Unicode NFC.** Then remove zero-width characters (U+200B and similar) and normalise common sara-am encoding variants (ํา to ำ).
2. **Remove punctuation and all whitespace before CER.** Thai spaces mark phrase or sentence breaks, and engines insert them inconsistently.
3. **Digits.** Map Thai digits ๐–๙ to Arabic 0–9 in both reference and hypothesis.
   - Simpler for a hobby eval than Typhoon's convention of spelling numbers out as words; PyThaiNLP's `num_to_thaiword` / `thai_digit_to_arabic_digit` can support either.
   - Either choice is valid, but apply the same one to both sides.
4. **ๆ.** Either expand it to the repeated preceding word (Typhoon convention, needs a word segmenter) or strip it on both sides. Expanding matches the Typhoon references.
5. **Keep tone marks and vowel diacritics.** They are meaningful characters, and dropping them would hide real errors. Optionally report a secondary "diacritic-insensitive" CER.
6. **English or Latin words.** Lowercase them and keep them as-is.
   - Do not force transliteration unless the reference set does, which Typhoon's does.
   - Flag clips with Latin script so mismatched conventions can be inspected by hand.
7. **CER.** Use `jiwer.cer(ref, hyp)` on the normalised strings. **Secondary WER:** segment both sides with `pythainlp.tokenize.word_tokenize(text, engine="newmm")`, join with spaces, then run `jiwer.wer`. Always state "WER (newmm)".
8. **Reporting.** Give corpus-level CER (total edits divided by total reference characters), not the mean of per-clip CERs, plus per-set breakdowns. Keep the raw, unnormalised hypotheses for review.
9. **Subtitles.** Separately check timestamp usefulness: how often segment boundaries fall within about 300 ms of reference boundaries. No source above benchmarks Thai timestamp accuracy.

Items 1–9 are my recommendations synthesised from the sources above plus standard practice. The specific PyThaiNLP and jiwer function names are **[unverified in this pass, prior knowledge]**; check them against the installed library versions.

### Gaps
- No primary source fetched here gives a canonical, community-agreed Thai ASR normalisation spec with code. Typhoon's guideline paper (Na-Thalang et al. 2025) was cited but not read.
- jiwer and PyThaiNLP API docs were not fetched in this pass.
