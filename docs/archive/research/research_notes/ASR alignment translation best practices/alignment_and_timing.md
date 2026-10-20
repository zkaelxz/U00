# Forced alignment, word/character timestamps and subtitle timing for zh/ja/ko (as of 2026-10-11)

Research date: 2026-10-11. All sources were fetched on that date unless noted. Where a project page shows no version or date, that is stated. The researcher's knowledge cutoff is June 2026, so anything not confirmed by a fetched page is marked as unverified or listed under Gaps.

## Alignment methods and accuracy for CJK (WhisperX/wav2vec2, MMS ctc-forced-aligner, MFA, NeMo NFA, Qwen3-ForcedAligner, stable-ts, Whisper word_timestamps, Gentle, aeneas)

### Takeaway
The only published per-language zh/ja/ko timing-error numbers found are for Qwen3-ForcedAligner-0.6B (Jan 2026). Its accumulated average shift (AAS) is about 33 ms (zh), 42 ms (ja) and 37 ms (ko) on short audio, and stays at 36, 81 and 42 ms on 300 s concatenated audio. On the same benchmark, NeMo NFA scores about 110 ms (zh) and WhisperX about 92 ms (en), and both get worse on long audio. WhisperX handles zh/ja by treating each character as a word, using community XLSR-53 wav2vec2 CTC models, and gives digits and punctuation a wildcard column. stable-ts was archived on 2026-05-30.

### Cited Findings
**Qwen3-ForcedAligner-0.6B (LLM-based, non-autoregressive)**
- Supports 11 languages, including Chinese, Cantonese, Japanese and Korean. It returns word- or character-level timestamps for "arbitrary units within up to 5 minutes of speech." — [HF model card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
- AAS (ms, lower is better) on MFA-labelled raw audio: Chinese 33.1, Japanese 42.2, Korean 37.2, English 37.5, average 42.9. Baselines: NFA 109.8 (zh) and 129.8 (avg); WhisperX 92.1 (en) and 133.2 (avg); Monotonic-Aligner 161.1 (zh). WhisperX and NFA have no ja/ko entries. — [HF model card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
- On 300 s concatenated audio (long-form drift test), AAS is zh 36.5, ja 81.3, ko 42.2 and avg 52.9. Baselines degrade: NFA zh 235.0 (avg 246.7), WhisperX avg 2708.4, Monotonic-Aligner zh 1742.4. On human-labelled sets the average is 32.4 ms, against 101.2 for NFA and 141.3 for MA. The card does not expand "NFA"; the assumption that it means NeMo Forced Aligner is the researcher's. — [HF model card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
- Described in the Qwen3-ASR Technical Report (arXiv 2601.21337, v1 29 Jan 2026, Apache 2.0) as an "LLM based NAR timestamp predictor" that outperforms "the three strongest force alignment models." The companion ASR models cover 52 languages and dialects. — [arXiv 2601.21337](https://arxiv.org/abs/2601.21337)
- The method paper is LLM-ForcedAligner (arXiv 2601.18220, Jan 2026, Mu, Shi, Wang et al.). It recasts forced alignment as slot-filling, inserting timestamp tokens as slots that hold discrete time indices. Loss is computed only at slots, and inference is non-autoregressive "to avoid hallucinations." It claims a 69–78% relative reduction in accumulated averaging shift and frames prior methods as "prone to cumulative temporal shifts" on long-form audio. Code is in the QwenLM/Qwen3-ASR repo. — [arXiv 2601.18220](https://arxiv.org/abs/2601.18220)

**WhisperX (Whisper + VAD + wav2vec2 CTC phoneme/character alignment)**
- Whisper's timestamps are utterance-level, so WhisperX adds wav2vec2 forced alignment to get word timing. It is 70x realtime with large-v2 when batched and was accepted at INTERSPEECH 2023. The README admits that overlapping speech is handled poorly and that "Diarization is far from perfect." — [WhisperX README](https://github.com/m-bain/whisperX)
- Paper (Bain, Huh, Han, Zisserman; arXiv 2303.00747, rev. 11 Jul 2023): buffered or sliding-window Whisper on long audio "drift[s]" and repeats. VAD Cut & Merge pre-segmentation improves quality and enables a 12x speedup via batching. — [arXiv 2303.00747](https://arxiv.org/abs/2303.00747)
- Default CJK alignment models in `alignment.py` (main branch): ja `jonatasgrosman/wav2vec2-large-xlsr-53-japanese`, zh `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn`, ko `kresnik/wav2vec2-large-xlsr-korean`. There is no default for yue (Cantonese), so `load_align_model` raises a ValueError unless a model is passed. — [whisperx/alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)
- `LANGUAGES_WITHOUT_SPACES = ["ja","zh"]`: each character is treated as a word. Korean is not on the list, so it is aligned per space-delimited eojeol. — [alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)
- Characters missing from the model dictionary (digits, punctuation, foreign script) become wildcards mapped to an extra emission column, which holds each frame's best non-blank score. Segments with no matching characters keep their original Whisper timestamps and log a warning. Words still left without timings are filled by interpolation (default `interpolate_method="nearest"`). `return_char_alignments=True` adds per-character start, end and score. — [alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)
- The README still says words like "£13.60" cannot be aligned and get no timing. That is older than the wildcard code above, so the README and the code disagree. Trust the code. — [WhisperX README](https://github.com/m-bain/whisperX); [alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)

**MMS-based ctc-forced-aligner (MahmoudAshraf97)**
- Default model is `MahmoudAshraf/mms-300m-1130-forced-aligner` (1,130 languages). `--romanize` is "required when using the default model," so CJK text is aligned via romanization. Other options: `<star>` token insertion (`--star_frequency` edges|segment), chunking with `--window_size` 30 s and `--context_size` 2 s overlap, and a claim of "at least 5X less memory" than the TorchAudio API. No CJK-specific notes, and no release or version is shown. — [ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)

**NeMo Forced Aligner (NFA)**
- NFA applies Viterbi decoding to CTC log-probs from NeMo ASR models. NVIDIA's documentation says it handles audio of "1+ hours" and supports NeMo checkpoints in 14+ languages. The Interspeech 2023 paper claims the best word-alignment accuracy and speed among the aligners compared, but the comparison tables were not retrieved. — [NVIDIA NFA docs](https://docs.nvidia.com/nemo/speech/latest/tools/nemo_forced_aligner.html); [Rastorgueva et al., Interspeech 2023](https://www.isca-archive.org/interspeech_2023/rastorgueva23_interspeech.pdf)
- On Qwen's benchmark, NFA's zh error roughly doubles on 300 s audio (109.8 to 235.0 ms). — [HF model card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)

**stable-ts (cross-attention DTW on Whisper)**
- Word timestamps come from cross-attention plus DTW, then silence suppression. The mask is built from the waveform, or from Silero VAD when `vad=True` (default `vad_threshold` 0.35). `model.align()` aligns plain text, and `align_words()` keeps words inside existing segment bounds. `refine()` mutes audio around each timestamp and tracks token probability (default `precision` 0.1 s). — [stable-ts README](https://github.com/jianfch/stable-ts)
- The default regroup preset splits on sentence-ending punctuation (full-width 。，？ included), on gaps of 0.5 s or more, and on commas in segments over 50 characters, with a 70-character cap. — [stable-ts README](https://github.com/jianfch/stable-ts)
- Status: "Development on this repository is indefinitely paused." The repo was archived and made read-only on 2026-05-30. No accuracy figures are published. — [stable-ts README](https://github.com/jianfch/stable-ts)

### Inferences
- For zh/ja/ko the strongest documented choice in Oct 2026 is Qwen3-ForcedAligner. Its 5-minute window means long files still need chunking, for example by VAD or ASR segments, and each chunk is aligned separately. Since each chunk resets the time origin, drift inside a chunk is bounded.
- WhisperX's per-character output for zh/ja maps naturally onto CJK subtitles, which have no spaces. Korean is aligned by eojeol, which is also the natural subtitle break unit.
- MMS ctc-forced-aligner aligns CJK only after romanization. Kanji readings are ambiguous (on/kun) and homophones are common, so this is likely weaker for Japanese than a native-script model. No numbers were found to confirm it.
- stable-ts was archived in May 2026, so new pipelines should not depend on it.

### Gaps
- Montreal Forced Aligner (Mandarin/Japanese/Korean acoustic models, MFA 3.x), Gentle (English-only, Kaldi) and aeneas (TTS+DTW, sentence-level) were not fetched in this session, so no cited numbers are given. From training knowledge (unverified): Gentle is English-only, and aeneas gives sentence- or fragment-level sync, not word-level.
- Whisper's built-in `word_timestamps=True` (DTW on alignment heads) was not fetched, so no published ms error figures are given here.
- No source was found giving WhisperX or NFA timing error on ja or ko. The Qwen card lists ja/ko results only for its own model.
- The independence of the Qwen numbers is unverified: they are vendor-reported, and the "MFA-labelled" reference uses MFA output as ground truth.
- Behaviour on singing, ASMR, whisper and overlap: WhisperX admits weakness on overlap ([README](https://github.com/m-bain/whisperX)). No benchmark for whispered or ASMR CJK speech was found.

## Re-timing existing subtitles / sync (ffsubsync, alass, transcript-to-audio, translation-to-source)

### Takeaway
ffsubsync and alass fix global offsets, framerate mismatch and (alass, ffsubsync experimentally) ad-break splits by matching speech/non-speech patterns. They are language-agnostic but do not fix per-line timing. For per-line accuracy, run forced alignment of the transcript (WhisperX `align`, stable-ts `align`, Qwen3-ForcedAligner). A translation should inherit the source cue timings rather than be aligned to audio.

### Cited Findings
- ffsubsync discretizes audio and subtitles into 10 ms speech/non-speech windows (VAD: WebRTC, or auditok, or "fused" WebRTC+Silero). It finds the best offset via FFT cross-correlation in O(n log n) and tries common framerate ratios (`--gss` golden-section search). The author says it handles over 95% of cases. Mid-file breaks need the experimental `--split-penalty` ("alass-style"). It takes 20–30 s per sync, and under 1 s when syncing to a reference subtitle. — [ffsubsync README](https://github.com/smacke/ffsubsync)
- alass corrects constant offsets, splits (ad breaks, director's cuts) and framerate differences, and is language-agnostic. `--split-penalty` defaults to 7, with 5–20 the useful range; `--no-splits` only shifts. It accepts a reference video or a reference subtitle (srt/ass/idx). Against a reference: 50% of lines within 50 ms, 80% within 100 ms, 90% within 400 ms, 95% within 800 ms. — [alass README](https://github.com/kaegi/alass)
- stable-ts `align()` aligns plain text to audio, and `align_words()` keeps words inside existing segment times (useful for re-timing an existing cue list). — [stable-ts README](https://github.com/jianfch/stable-ts)
- Netflix's Korean guide: when a word is repeated, "time the subtitle to the audio but translate it only once," so timing follows the source audio, not the translated text. — [Netflix Korean TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/216001127)

### Inferences
- Recommended order: (1) a global sync (ffsubsync or alass) for a subtitle file from another release, then (2) forced alignment of the source-language text for per-line accuracy, then (3) carrying translated lines over by cue id onto source timings, then (4) applying CPS, minimum-duration and gap rules to the result.
- alass's 90%-within-400 ms figure means a global sync alone can leave lines visibly off, so it is not a replacement for forced alignment.

### Gaps
- No primary source was found on automatic alignment of a translation (different language) directly to audio. The established practice of inheriting source timing is inferred, not cited from a standard.
- Release versions and dates for ffsubsync and alass are not shown on their pages.

## Subtitle timing standards for zh/ja/ko (Netflix, general guidance) and what open-source tools enforce

### Takeaway
Netflix rules: minimum duration 5/6 s (20 frames at 24 fps), or 500 ms in the Japanese guide; maximum 7 s; a 2-frame minimum gap, with 3–11 frame gaps closed to 2; 12-frame (0.5 s) shot-change snapping; out-time at least 0.5 s after audio when no subtitle follows. Per-language CPL/CPS: zh 16 CPL and 9 CPS (adult), ja 13 CPL horizontal (11 vertical) and 4 CPS, ko 16 CPL and 12 CPS. Half-width, Latin or punctuation counts 0.5 in ja and ko.

### Cited Findings
**General requirements and timing guidelines (Netflix)**
- Minimum duration: "5/6 (five-sixths) of a second per subtitle event (e.g. 20 frames for 24fps)". Maximum duration: 7 s. Two lines maximum, kept to one line unless the text exceeds the limit. Translator credit lasts up to 5 s. Frame-gap sections were removed from this page on 2020-07-24 and moved to the timing guidelines. — [Netflix TTSG General Requirements](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617)
- Timing guidelines use a 24 fps reference: half a second is 12 frames (15 at 30 fps, 30 at 60 fps), and the minimum gap is 2 frames at every frame rate. Minimum duration is 20 frames, kept for 1–2-word subtitles. Gaps of 3–11 frames must be closed to 2 frames by extending the previous out-time ("chaining"). Acceptable gaps are 2 frames or at least 12 frames. — [Netflix Subtitle Timing Guidelines](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines)
- In-time goes on the first frame of audio (within 1–2 frames, using the waveform). With no following subtitle, out-time goes at least 0.5 s after the audio ends. If the out-time is already 0.5 s past the audio, do not extend it further; re-segment or merge instead. — [Netflix Timing Guidelines](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines)
- Shot changes: if dialogue starts on or up to 0.5 s after a cut, snap the in-time to the cut. If an out-time is within 0.5 s before a cut, extend it to the cut minus 2 frames. Subtitles that cross a cut start at least 0.5 s before it; if dialogue starts 9–11 frames before a cut, move the in-time to 0.5 s before it or to the cut. Out-times sit 2 frames before a cut or at least 0.5 s after it. Last resort: 12 frames after the cut. On-screen text follows the text's own timing, with fades timed at their midpoint. — [Netflix Timing Guidelines](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines)

**Japanese (latest change-log entry 2025-12-19)**
- 13 full-width characters per horizontal line and 11 per vertical line. Half-width characters, spaces and punctuation count 0.5. SDH allows 16 horizontal. Two lines maximum. — [Netflix Japanese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517)
- Reading speed: up to 4 characters per second for subtitles and 7 CPS for SDH. There is no separate children's limit. — [Netflix Japanese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517)
- Minimum duration is 500 ms (12 frames at 24 fps), shorter than the general 5/6 s. — [Netflix Japanese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517); compare [General Requirements](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617)
- No 。 or 、: 、 becomes a half-width space and 。 a full-width space. ？ and ！ are full-width. Ruby is given on first instance only (every instance for children's content) and is never kanji. Numbers: single digits full-width and two or more digits half-width (horizontal); no commas; 万 above four digits. — [Netflix Japanese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517)

**Korean (latest change-log entry 2025-07-07)**
- 16 characters per line, with Latin, spaces and punctuation counting 0.5. Two lines maximum. Reading speed is 12 CPS for adults and 9 for children (SDH 14 and 11). — [Netflix Korean TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/216001127)
- Never end a line with a period or comma, and use a comma between two sentences in one subtitle. Use the single ellipsis character (U+2026) for pauses of 2 s or more or interruptions, but do not use ellipses or dashes when one sentence is split across continuous subtitles. — [Netflix Korean TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/216001127)

**Chinese**
- Simplified (article 215986007): 16 CPL, 2 lines, 9 CPS adult and 7 children (SDH 18 CPL, 11/9 CPS). No commas or periods (use a single space). Half-width Arabic numerals; do not mix numerals and Chinese numerals; ellipsis U+2026; no !?, ?? or !!. — [Netflix Simplified Chinese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007)
- Traditional (article 215994807): 16 CPL and 9/7 CPS. Full-width punctuation, and no line may end with a comma, 、 or period. Do not convert ROC years. — [Netflix Traditional Chinese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215994807)
- A third-party blog attributes 17–20 CPS figures to Netflix generally. That is not a Netflix document and not CJK-specific, so it should not be used. — [convertaudiototext blog via search](https://convertaudiototext.com/blog/subtitle-translation-for-video)

**Open-source tool defaults found**
- stable-ts regroups at a 70-character segment cap, splits on 0.5 s gaps and treats full-width punctuation as sentence ends. It has no CJK-specific CPL or CPS. — [stable-ts README](https://github.com/jianfch/stable-ts)
- WhisperX produces per-character timing for zh/ja but enforces no subtitle-length or CPS rules in alignment. — [alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)

### Inferences
- A CJK subtitle pipeline should store per-language profiles: zh 16/9, ja 13/4 with a 500 ms minimum, ko 16/12. It should count half-width characters as 0.5 (ja/ko) and apply the 2-frame and 12-frame rules relative to the video frame rate, not fixed ms.
- Japanese 4 CPS is very strict compared with zh (9) and ko (12). Japanese subtitles must be condensed much more, which affects whether translation output fits the timings.
- Shot-change snapping requires scene-cut detection. Audio-only content such as audio dramas has no shots, so only the gap, duration and lead-out rules apply.

### Gaps
- BBC subtitle guidelines, EBU-TT and ISO guidance were not fetched this session. No cited BBC or EBU CJK figures are given.
- Subtitle Edit, Aegisub and pyvideotrans defaults (min/max duration, CPS, gap ms) were not fetched. From training knowledge (unverified): Subtitle Edit has per-profile settings for maximum CPL, CPS, minimum gap and minimum and maximum duration, including Netflix presets, and Aegisub has a CPS display. Verify these before citing them.
- The Netflix English TTSG (42 CPL, 20 CPS adult per training knowledge) was not fetched, so that figure is unverified.

## How top pipelines combine ASR segment timestamps with forced alignment, and which settings matter

### Takeaway
WhisperX's recipe is: VAD-segment the audio, merge segments into chunks no longer than 30 s for batched Whisper, keep the segment-level times, then forced-align each segment's text with a per-language wav2vec2 CTC model. Fall back to the segment timing (and interpolation) where alignment fails. The settings that matter are the alignment model per language (zh/ja/ko defaults are community XLSR-53 models), the character vs word unit, wildcard handling for digits and punctuation, and the interpolation method.

### Cited Findings
- VAD Cut & Merge pre-segmentation yields a 12x speedup via batching and avoids long-form drift and repetition from sliding-window Whisper. — [arXiv 2303.00747](https://arxiv.org/abs/2303.00747)
- Alignment runs per segment. A segment with no alignable characters keeps its ASR timestamps, and missing word times use "nearest" interpolation. zh/ja are aligned per character, with wildcards for out-of-vocabulary characters. — [alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)
- For an unlisted language, the user must supply a phoneme- or character-based model from the HF hub. Cantonese has no default. — [WhisperX README](https://github.com/m-bain/whisperX); [alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)
- ctc-forced-aligner chunks long audio into 30 s windows with 2 s context overlap. — [ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)
- Qwen3-ForcedAligner is limited to 5 minutes per call. Its long-form (300 s) error stays low (zh 36.5 ms), while WhisperX's average on the same test is 2708 ms. — [HF model card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
- stable-ts uses silence suppression (waveform or Silero VAD, threshold 0.35) to snap word edges out of silence, and `refine()` for tighter edges. — [stable-ts README](https://github.com/jianfch/stable-ts)

### Inferences
- A practical 2026 CJK pipeline: VAD, then ASR segments, then forced alignment per segment (or per window of 5 minutes or less) with a native-script model (Qwen3-ForcedAligner, or WhisperX's per-language model as a fallback), then re-segmentation into cues by punctuation, pause (0.5 s or more) and CPL/CPS, then the Netflix-style gap and duration pass.
- The "WhisperX avg 2708 ms on 300 s" result suggests that aligning WhisperX over whole long files without VAD segmentation drifts badly. Keeping alignment per short segment is what makes it accurate.

### Gaps
- The exact WhisperX VAD defaults (`chunk_size`=30, `vad_onset`/`vad_offset` values, pyannote vs Silero default in 2026) were not shown on the README fetched. Check `whisperx/vads` or `asr.py` before quoting them.
- No source quantified how much ASR text errors degrade forced-alignment timing on CJK.

## Overlapping cues and simultaneous speakers

### Takeaway
Little primary material was retrieved. WhisperX states that overlap is poorly handled. Netflix guides allow hyphenated dual-speaker subtitles (Traditional Chinese explicitly allows half-width hyphens for dual speakers) rather than overlapping cue timings.

### Cited Findings
- "Overlapping speech is not handled well by Whisper or WhisperX." — [WhisperX README](https://github.com/m-bain/whisperX)
- Traditional Chinese guide: full-width punctuation only, "except for hyphens in dual-speaker subtitles." — [Netflix Traditional Chinese TTSG](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215994807)
- The Netflix General Requirements page fetched does not address overlapping dialogue directly. — [General Requirements](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617)

### Inferences
- In SRT, VTT or Netflix delivery, simultaneous speakers are normally combined into one two-line event with a dash per speaker, which respects the 2-line limit. Overlapping time ranges are mainly an ASS/karaoke or fansub practice (separate styles or positions). Format-level overlap rules were not verified this session.

### Gaps
- ASS (Aegisub collision handling), WebVTT overlap semantics and Netflix's exact dual-speaker dash rules for zh/ja/ko were not fetched.
- No benchmark of aligners on overlapping CJK speech was found.
