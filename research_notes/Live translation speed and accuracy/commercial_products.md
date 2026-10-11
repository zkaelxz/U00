# Commercial and platform live speech translation products (as of October 2026), with Apple Live Translation as the anchor

Research date: 2026-10-10. About 18 search and fetch calls. Where a finding comes only from a search-result summary and not a page I fetched, it is marked "(snippet)". No latency number below is my own estimate. Each one is quoted from the cited source.

## Apple: what Live Translation is in 2026, its architecture, languages, latency and limits ("airport" vs AirPods)

### Takeaway
Apple's "Live Translation" is the iOS 26 feature (expanded in iOS 27 / 2026) in AirPods, Messages, Phone and FaceTime. Apple describes it as on-device and built on Apple Intelligence generative models. AirPods support covers en/fr/de/it/pt/es plus Mandarin (Simplified and Traditional), Japanese and Korean. Neither Apple nor any reviewer I found publishes a latency figure. Reviewers describe a noticeable pause that grows with sentence length, because the system waits for (near) sentence end. I found no airport deployment. "Airport" almost certainly means AirPods.

### Cited Findings
- **Airport vs AirPods:** I found no report of Apple deploying Live Translation at airports. There is nothing on kiosks, gate announcements or airline pilots. Travel coverage frames it as a traveller's personal tool. Apple's other travel features at WWDC25 (Digital ID from passport) were announced alongside it, which may explain the association. — [Skift, 2025-06-09](https://skiftstaging.dev/2025/06/09/apples-travel-upgrades-new-digital-passport-live-translation-for-messages-and-calls); [Skift, 2025-09-09](https://skift.com/2025/09/09/apple-rolls-out-live-translation-on-airpods/) (snippet-level)
- **AirPods languages (Apple Newsroom, 4 Nov 2025):** English, French, German, Portuguese, Spanish, Italian, Chinese (Simplified and Traditional Mandarin), Japanese, Korean. Requires AirPods Pro 3, AirPods Pro 2 or AirPods 4 with ANC, paired with an Apple Intelligence iPhone on iOS 26+. — [Apple Newsroom IE](https://www.apple.com/ie/newsroom/2025/11/live-translation-on-airpods-expands-to-the-eu/)
- **How it works in AirPods mode:** ANC lowers the other person's voice so you hear the translation. If the other person has no AirPods, the iPhone shows a live transcription in their language. Apple says it protects privacy "with on-device processing." — [Apple Newsroom IE](https://www.apple.com/ie/newsroom/2025/11/live-translation-on-airpods-expands-to-the-eu/)
- **EU:** Delayed for Digital Markets Act engineering, with the EU expansion announced for December 2025. — [Apple Newsroom IE](https://www.apple.com/ie/newsroom/2025/11/live-translation-on-airpods-expands-to-the-eu/)
- **Messages languages:** en (US, UK), nl, fr, de, it, ja, ko, pt-BR, es-ES, zh (Simplified and Traditional), tr, vi. Phone and FaceTime (one-on-one calls) use a subset without nl, tr and vi. — [Apple Newsroom IE](https://www.apple.com/ie/newsroom/2025/11/live-translation-on-airpods-expands-to-the-eu/)
- **2026 / iOS 27 status:**
  - Hardware expanded to AirPods 4 ANC, AirPods 5, AirPods Pro 2/3 and AirPods Max 2, on iPhone 15 Pro or later.
  - Language models are downloaded to the iPhone so conversations are processed on-device.
  - Apple warns that it uses generative models and "translations may be inaccurate."
  - iOS 27 "does not fundamentally change the concept."
  - Hindi is not supported for AirPods.
  - Source: Live From A Lounge, 17 Sep 2026, a secondary source citing Apple's feature-availability page. — [Live From A Lounge](https://livefromalounge.com/apple-live-translation-gets-more-useful-for-travellers-with-ios-27/)
- **Latency, Macworld (English-German):** The AirPods "often waited until near the end of a sentence before translating." German subclauses make the delay "substantial." A Spanish test showed it cannot translate "in true real time." Translations were largely accurate, with minor errors such as boarding pass rendered as "card." — [Macworld](https://www.macworld.com/article/3012435/airpods-live-translation-english-german-iphone-testing.html) (snippet)
- **Latency, Which? (UK consumer test):** "Small delays" in-ear, worse with long or complex sentences. Testers ended up reading the on-screen translation in the app because it was faster than the audio. — [Which?](https://www.which.co.uk/news/article/we-tested-apples-live-translation-feature-does-it-really-work-aClEt6o0zOut) (snippet)
- **Japanese-English field test (NYT, reprinted by The Nightly, 14 Feb 2026, trip in autumn 2025):**
  - In quiet one-on-one settings it "felt natural and almost effortless."
  - It struggled in train stations, izakayas and with rapid-fire speakers.
  - At a crowd event it picked up bystanders' speech.
  - A chef guide noted minor errors on seafood and sushi terminology and a few incorrect pronouns.
  - No latency was measured. — [The Nightly / NYT](https://thenightly.com.au/lifestyle/travel/we-tested-apples-airpod-translation-in-tokyo-heres-what-we-found-c-21147816)
- **Developer building blocks (WWDC25 session 277, SpeechAnalyzer, iOS/macOS 26):**
  - The transcriber can emit "volatile" results, an immediate rough guess delivered almost as soon as the words are spoken, which later passes replace with a finalized result for that audio range.
  - Without volatile mode, only finals are emitted.
  - Apple gives no latency figure. — [Apple WWDC25 #277](https://developer.apple.com/videos/play/wwdc2025/277/)
- **SpeechAnalyzer gotchas reported by third parties:**
  - As of iOS 26.0 there is no equivalent of the old contextual-strings vocabulary biasing.
  - The model is a system asset that may need downloading on first run. — [dev.to](https://dev.to/simple_memo/ios-26s-speechanalyzer-on-a-live-mic-the-5-things-the-docs-dont-tell-you-2ng5) (snippet, third-party)
- **Unreliable source:** A macos-tahoe.com "complete guide" quotes specific latency and accuracy figures that appear unsourced. I excluded them. — [macos-tahoe.com](https://macos-tahoe.com/blog/apple-live-translation-macos-tahoe-complete-guide-2025/)

### Inferences
- The behaviour reviewers describe, waiting for sentence end and lag growing with clause length, fits a cascaded, segment-level pipeline: on-device ASR, then endpointing, then MT, then TTS. It does not fit a streaming end-to-end speech-to-speech model like Google's (below). Apple has not published the architecture. This is an inference from behaviour.
- The Which? finding (text faster than audio) means the TTS stage adds noticeable latency. Captions-first output is inherently faster, which is good news for a subtitle app, since it does not need TTS.
- For a desktop app, Apple's public pieces (SpeechAnalyzer volatile/final results and the Translation framework) let a macOS app reproduce the "rough now, refine later" caption pattern locally.

### Gaps
- I found no measured latency figure (seconds) for Apple Live Translation from Apple or any reviewer.
- I could not find Apple ML research describing the Live Translation model architecture, whether it is cascaded or end-to-end, or its model size.
- I did not verify Apple's current feature-availability page directly. The 2026 language and device list comes via Live From A Lounge.
- I found no reviewer tests of Korean or Mandarin specifically, and none of slang, drama dialogue or names.

## Google, Samsung, Meta, Microsoft, DeepL, Zoom/Teams and hardware: latency, architecture and CJK coverage

### Takeaway
Only Google and Meta publish concrete latency figures for shipped products. Google's end-to-end streaming speech-to-speech model (Meet, Nov 2025) runs at about 2 s versus 4 to 5 s for its earlier cascade. Meta's on-glasses cascade went from more than 5 s to 2.7 s. Google's Gemini 3.5 Live Translate (June 2026) claims 70+ languages but publishes no latency numbers. Microsoft, Samsung and DeepL give only qualitative claims. CJK is supported by Microsoft's Teams Interpreter, DeepL Voice and Samsung, but not by Google's 2025 end-to-end Meet model or by Meta glasses.

### Cited Findings
**Google**
- **Google Research, "Real-time speech-to-speech translation" (19 Nov 2025):**
  - Architecture: an end-to-end streaming model built on AudioLM-style transformers, SpectroStream codec and RVQ audio tokens.
  - Latency: "only 2 second delay," versus 4 to 5 s for earlier cascaded ASR, MT and TTS systems.
  - Delay mechanism: a fixed lookahead, created by shifting target tokens right in training. A 2 s lookahead is standard for conversation, and longer lookahead improves quality at the cost of responsiveness.
  - Speed: int8/int4 quantization.
  - Quality: measured by BLEU on a text token the model emits alongside its audio. No human eval is reported.
  - Pairs: en to and from es, de, fr, it, pt.
  - Stated limitation: a fixed lookahead is a problem for languages with very different word order, and future work targets a dynamic lookahead. — [Google Research](https://research.google/blog/real-time-speech-to-speech-translation/)
- **Deployment, same post:**
  - Google Meet runs the model server-side.
  - Pixel 10's on-device "Voice Translate" uses a **cascade** "to maximize language coverage."
  - The two share data and architecture.
  - Users are told the voice is synthetic. — [Google Research](https://research.google/blog/real-time-speech-to-speech-translation/)
- **Training-data tricks, same post:**
  - Time-synchronized synthetic data: ASR, then MT, then voice-preserving TTS, with three forced-alignment passes, and poorly aligned examples are filtered out.
  - Chunks of up to 60 s.
  - Noise and reverb augmentation.
  - The encoder summarizes the previous 10 s of input. — [Google Research](https://research.google/blog/real-time-speech-to-speech-translation/)
- **Meet target latency:** Google's Meet team cites 2 to 3 s as the target sweet spot. Meet's help page warns that translations are "a few seconds delayed for completeness." — [Google blog: How AI made Meet's translation possible](https://blog.google/products/workspace/google-meet-langauge-translation-ai/) (snippet); [Slator](https://tech.slator.com/google-reveals-secret-sauce-behind-ai-live-speech-translation/) (snippet)
- **Gemini 3.5 Live Translate (`gemini-3.5-live-translate-preview`, June 2026):**
  - Described as a "low-latency, audio-to-audio model optimized for real-time translation."
  - Takes audio in and returns audio plus a text transcript.
  - Available in preview through the Live API.
  - The docs list no languages and no latency numbers. — [Google AI for Developers](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-live-translate-preview?hl=en)
- **Gemini 3.5 coverage and behaviour:**
  - Coverage reports 70+ languages with auto-detection, and over 2,000 combinations in Meet (private preview for enterprises).
  - Output stays "a few seconds behind the speaker."
  - The model "balances waiting for context against translating immediately."
  - The model card defines initial latency and word-level latency but publishes no figures. — [Implicator, 9 Jun 2026](https://www.implicator.ai/google-expands-meet-speech-translation-from-5-languages-to-70-with-gemini-3-5/); [MultiLingual](https://multilingual.com/google-launches-gemini-3-5-live-translate-real-time-speech-capabilities/) (snippet). Whether CJK is in the 70+ was not confirmed by any source I read.

**Meta**
- **Meta blog (14 Nov 2025):**
  - Architecture: a three-stage cascade (ASR, then MT, then TTS) that runs **entirely on the glasses**, with no server.
  - Latency: cut from more than 5 s to **2.7 s** by streaming, where the model "understands, translates, and generates speech together, within a few words, without waiting for a full sentence."
  - Hardware: a five-mic beamforming array separates the wearer from the partner.
  - Offline: works in airplane mode via downloaded packs.
  - Text: a near-real-time two-language transcript appears in the Meta AI app.
  - Languages: en, fr, de, it, pt, es on Ray-Ban Meta and Oakley Meta, and only en, fr, it, es on Ray-Ban Display. No CJK.
  - Meta admits "noticeable latency" and imperfect accuracy, and publishes no accuracy metrics.
  - Each language needs bespoke per-device training. — [Meta blog](https://www.meta.com/blog/live-translation-ai-glasses-deep-dive/)

**Samsung**
- **Galaxy AI Live Translate and Interpreter:**
  - Samsung says the on-device model works without external servers.
  - Language count is inconsistent across Samsung sources: 13 vs 16 languages (Arabic, Indonesian and Russian added). Samsung's lists include Chinese, Japanese and Korean.
  - Interpreter works offline with downloaded packs.
  - I found no measured latency. A pre-launch Nikkei claim of "almost no delay" is marketing, not a measurement. — [Samsung support: Live Translate languages](https://www.samsung.com/ae/support/mobile-devices/how-many-languages-does-the-live-translate-feature-support/); [Samsung Research on-device MT](https://developer.samsung.com/sdp/news/en/2024/05/24/may-2024-newsletter-galaxy-ai-on-device-ai-research-introducing-mels-tts-the-new-text-to-speech-system-and-other-latest-news) (snippets)

**Microsoft**
- **Azure Speech Translation Live Interpreter API (public preview, Sept 2025):**
  - Continuous language identification, with no need to set the input language.
  - "Low latency" speech-to-speech translation.
  - 76 input languages and 143 locales.
  - Microsoft claims it is "on par with human interpreters," with no published method or numbers. — [Windows Report](https://windowsreport.com/microsoft-lanuches-live-interpreter-api-in-public-preview/); [Neowin](https://www.neowin.net/news/microsoft-unveils-breakthrough-live-interpreter-api-for-instant-multilingual-communication/) (snippets)
- **Teams Interpreter agent:**
  - Nine languages: zh (Mandarin), en, fr, de, it, **ja, ko**, pt, es.
  - In Teams Rooms on Windows it needs a Rooms Pro license and includes 20 h/month.
  - Microsoft says interpretation "may introduce a short delay" and that latency work is ongoing, with no figures.
  - Reaching Teams calls (Jan 2026) and a consecutive mode (May 2026) are reported by a third-party blog only and are unverified. — [Microsoft message center MC1188221](https://mc.merill.net/message/MC1188221); [Intermind](https://intermind.com/blog/teams-live-translation) (snippets)

**DeepL**
- **DeepL Voice (launched Nov 2024):** It covers Meetings (captions in Teams, with Zoom integration announced) and Conversations. Spoken input included ja and ko at launch, and Mandarin was added later. DeepL claims "minimal latency" without figures. — [DeepL press release](https://www.deepl.com/en/press-release/deepl-expands-real-time-voice-translation-capabilities-with-new-features-and-upcoming-zoom-integration); [DeepL Voice](https://www.deepl.com/en/products/voice) (snippets)
- **Seoul demo:** A live demo showed a visible delay of one to two **sentences**, and the report names word order as a fundamental challenge. The date and outlet are unverified, and the report came via a search summary. — search result, source not pinned (treat as unverified)

**Timekettle and other hardware**
- **Timekettle W4 Pro:** The manufacturer claims a 0.2 s delay (retailer listing). A competitor's blog says 3 to 5 s. Timekettle's own WT2 Edge figure is 0.5 to 3 s, depending on network, phone and the speaker's pauses. — [B&H](https://www.bhphotovideo.com/c/product/1858741-REG/timekettle_tk_w4_01_w4_pro_ai_interpreter.html); [Avantree](https://avantree.com/ja/blogs/knowledge/what-are-the-best-translation-earbuds-available); [Timekettle](https://www.timekettle.co/blogs/news/timekettle-wt2-edge-translation-earbuds-redefined?page=5) (snippets)
- **W4 English-Chinese hands-on (IFA 2025):** Described as impressively accurate on a noisy show floor. It was not timed. — [Techlicious](https://www.techlicious.com/blog/ifa-2025-timekettle-w4-brings-near-instant-language-translation/) (snippet)

**Academic reference point**
- **IWSLT 2025 simultaneous track:**
  - Directions include en to zh and en to ja.
  - Latency is measured in StreamLAAL, with defined low and high regimes. The CUNI paper cites a 4,000 ms high-latency limit for en-zh.
  - CUNI's best en-ja system reached 34.69 BLEU at 4,654 ms StreamLAAL on dev, versus a 16.19 BLEU baseline.
  - These are research systems on talks, not products. — [IWSLT 2025 simultaneous task](https://iwslt.org/2025/simultaneous); [CUNI IWSLT 2025](https://arxiv.org/pdf/2506.17077); [Findings of IWSLT 2025](https://preview.aclanthology.org/setup/2025.iwslt-1.44) (snippets)

### Inferences
- The shipped state of the art for conversational speech-to-speech is roughly **2 to 3 s** of lag (Google about 2 s, Meta 2.7 s, Meet target 2 to 3 s). Systems that wait for segment or sentence end (Apple per reviewers, the DeepL demo) lag by a whole sentence or more.
- The CJK-to-English pairs are where streaming end-to-end products are weakest or absent. Google explicitly names word-order divergence as the reason a fixed lookahead fails, and IWSLT en-ja systems at about 4 to 5 s StreamLAAL still score far below offline quality. Products that cover CJK (Apple, Samsung, Microsoft, DeepL) appear to be cascades that wait for more context.
- Two architectures persist. Cloud end-to-end (Meet) gives the lowest latency and preserves voice. On-device cascade (Pixel, Apple, Samsung, Meta) gives offline use, privacy and wider language coverage. Google itself chose the cascade on-device "to maximize language coverage."

### Gaps
- I found no independent, instrumented latency tests (RTINGS, The Verge or Tom's Guide style) for any product in this set.
- I have no confirmed CJK list for Gemini 3.5 Live Translate and no published latency numbers from its model card.
- I found no Samsung, Microsoft, DeepL or Zoom latency figures. I did not research Zoom's native translated captions.
- I could not get the per-system tables from the IWSLT 2025 findings. The IWSLT 2026 results were not checked.
- WMT human-evaluation results for ja, ko and zh to English were not checked.

## UX tricks that hide latency

### Takeaway
The documented tricks are:
1. Show text before audio.
2. Emit rough partial ("volatile") captions immediately and replace them with finals.
3. Stream with a fixed, small lookahead rather than waiting for a full sentence.
4. Use hardware and audio tricks (ANC ducking, beamforming) so the delay is less jarring.

I found no vendor documenting retranslation flicker control in detail.

### Cited Findings
- **Volatile then final results:** Apple's SpeechAnalyzer emits immediate rough "volatile" text, replaced by later passes and committed as a final result per audio range. Apple says it is for "immediate UI feedback." — [Apple WWDC25 #277](https://developer.apple.com/videos/play/wwdc2025/277/)
- **Recommended display for partials:** A third-party pattern is to render volatile text dimmed, swap it in place when the final arrives, and persist only finals. — [dev.to](https://dev.to/simple_memo/ios-26s-speechanalyzer-on-a-live-mic-the-5-things-the-docs-dont-tell-you-2ng5) (snippet)
- **Fixed lookahead:** Google's model is trained with a fixed 2 s lookahead. More lookahead gives more quality and less immediacy, and a dynamic lookahead is future work. — [Google Research](https://research.google/blog/real-time-speech-to-speech-translation/)
- **Streaming within a few words:** Meta streams "within a few words" rather than waiting for the full sentence, which accounts for most of its 5 s to 2.7 s reduction. — [Meta blog](https://www.meta.com/blog/live-translation-ai-glasses-deep-dive/)
- **Text is faster than audio:** Which? testers preferred reading the in-app translation because it beat the audio. Apple and Meta both show a live two-language transcript on the phone. — [Which?](https://www.which.co.uk/news/article/we-tested-apples-live-translation-feature-does-it-really-work-aClEt6o0zOut); [Meta blog](https://www.meta.com/blog/live-translation-ai-glasses-deep-dive/)
- **ANC ducking:** AirPods ANC lowers the original speaker's voice so the translated audio dominates. — [Apple Newsroom IE](https://www.apple.com/ie/newsroom/2025/11/live-translation-on-airpods-expands-to-the-eu/)
- **"Delayed for completeness":** Google Meet explicitly trades a few seconds of delay for completeness. Gemini 3.5 is described as balancing waiting for context against translating immediately. — [Implicator](https://www.implicator.ai/google-expands-meet-speech-translation-from-5-languages-to-70-with-gemini-3-5/)

### Inferences
- For captions, the transferable pattern is: a dimmed partial source transcript right away, then the translation committed per segment, and never rewriting committed lines. This matches the volatile/final split. A short display delay before committing the translation, roughly one clause, trades a little latency for no flicker. That is my inference, not a vendor-documented rule.

### Gaps
- I found no vendor description of retranslation or flicker-control policies (for example, Google's earlier "re-translation with stability" work) in a 2025 or 2026 product context.
- I found no figures on chunk sizes used by Apple, Samsung or Microsoft.

## Independent reports on CJK accuracy, slang, drama dialogue and names

### Takeaway
The evidence is thin and anecdotal. Japanese-English on AirPods is good in quiet one-on-one use. It fails with noise and rapid speech and makes domain-term and pronoun errors. I found no independent CJK benchmark of any shipped product on slang, drama dialogue or proper names.

### Cited Findings
- **AirPods Japanese-English:** Minor seafood and sushi terminology errors and a few incorrect pronouns. Struggles with rapid-fire speakers, train stations and izakayas. Picks up bystanders in crowds. — [The Nightly / NYT](https://thenightly.com.au/lifestyle/travel/we-tested-apples-airpod-translation-in-tokyo-heres-what-we-found-c-21147816)
- **AirPods German and Spanish:** "Largely accurate" with small lexical errors. — [Macworld](https://www.macworld.com/article/3012435/airpods-live-translation-english-german-iphone-testing.html) (snippet)
- **Timekettle W4 English-Chinese:** Judged accurate in a noisy show-floor demo (qualitative, vendor-hosted). — [Techlicious](https://www.techlicious.com/blog/ifa-2025-timekettle-w4-brings-near-instant-language-translation/) (snippet)
- **Accuracy caveats from vendors:** Apple says translations may be inaccurate (generative models). Meta says accuracy is imperfect and publishes no metrics. Microsoft claims parity with human interpreters but gives no published evaluation. — [Live From A Lounge](https://livefromalounge.com/apple-live-translation-gets-more-useful-for-travellers-with-ios-27/); [Meta blog](https://www.meta.com/blog/live-translation-ai-glasses-deep-dive/); [Windows Report](https://windowsreport.com/microsoft-lanuches-live-interpreter-api-in-public-preview/)

### Inferences
- Pronoun errors in Japanese reflect zero-pronoun (dropped-subject) Japanese. Expect the same in Korean and Chinese drama dialogue, where context from earlier lines matters. Segment-at-a-time live systems lack that context, and an offline subtitle pipeline can supply it.

### Gaps
- I found no independent tests on slang, drama or audio-drama dialogue, or character names, for any product.
- I found no Korean or Mandarin hands-on accuracy reports.

## What a local desktop app that translates streams and videos can learn or match

### Takeaway
A local app can realistically match the text-caption side of these products: a dimmed partial ASR transcript almost instantly, then committed translations about 2 to 4 s behind speech. It cannot match Google's 2 s voice-preserving speech-to-speech output locally. For CJK to English it should expect to wait for clause or sentence boundaries, as even the best systems do. For recorded video it can beat all of these products on accuracy by using full context, glossaries and character names.

### Cited Findings
- **Captions avoid the TTS cost:** Captions-only output avoids the TTS stage, which reviewers found slower than on-screen text. — [Which?](https://www.which.co.uk/news/article/we-tested-apples-live-translation-feature-does-it-really-work-aClEt6o0zOut)
- **Cascade is viable on-device:** Pixel and Apple use a cascade on-device for language coverage, and Meta's cascade reaches 2.7 s on glasses-class hardware. — [Google Research](https://research.google/blog/real-time-speech-to-speech-translation/); [Meta blog](https://www.meta.com/blog/live-translation-ai-glasses-deep-dive/)
- **Research latency for en-ja and en-zh:** Research simultaneous systems for en-ja and en-zh operate at about 4 s StreamLAAL in the "high latency" regime. — [CUNI IWSLT 2025](https://arxiv.org/pdf/2506.17077)
- **Lookahead and word order:** Longer lookahead raises quality, and divergent word order (as in CJK) makes a fixed short lookahead fail. — [Google Research](https://research.google/blog/real-time-speech-to-speech-translation/)

### Inferences
- **Live streams:** Use streaming ASR with volatile/final partials, translate per final segment (sentence or clause), and keep a short context window of previous lines plus a glossary or name list in the MT prompt. A realistic target for ja/ko/zh to English is about 2 to 5 s caption lag. The low end matches the products. The high end matches IWSLT CJK systems.
- **Recorded video:** No latency constraint exists, so use full-file ASR and whole-scene context translation. That is a structural accuracy advantage over all the live products above, especially for pronouns, names and drama register.
- **Things a local app should not try to copy:** Voice-preserving end-to-end speech-to-speech output (Google's cloud model) and per-language bespoke on-device models (Meta).

### Gaps
- I have no measured numbers for local open models (e.g. Whisper-class streaming ASR plus a local LLM MT) on consumer GPUs. Those are out of scope here and should come from the adjacent research thread.
