# VAD, silence handling, audio preprocessing and vocal separation for CJK subtitle ASR (as of Oct 2026)

Research date: 2026-10-11. Sources were fetched on that date. 15 tool calls, so coverage is broad but shallow in places, and the Gaps sections list what was not verified. I did not read the Baihe repository, by instruction. One search hit was a Baihe PR (zkaelxz/U00 #832, "Whisper hallucination guards for silence/music"), and I did not open it.

## VAD: engines, versions, defaults, and the effect on errors and hallucination

### Takeaway
Silero VAD is the de facto standard: v6 is current, faster-whisper bundles v6, and v6.2.3 came out in Sep 2026. In the one controlled study found, Silero cut Whisper's hallucinations on non-speech audio from about 21% to 0.2%. WebRTC VAD only roughly halved them. faster-whisper's defaults are very conservative (they only remove silences longer than 2 s, with 400 ms padding). WhisperX defaults to pyannote with onset 0.5 and offset 0.363, merged into chunks of up to 30 s. TEN VAD is a lighter, faster-reacting alternative but publishes no numeric accuracy. For whispered or ASMR speech, the only guidance found is generic: lower the threshold to 0.3–0.4. No ASMR-specific evaluation exists.

### Cited Findings
**Silero VAD versions**
- v6.0 ("New v6 VAD Released"): "16% less errors on noisy real-life data" and "11% less errors on multi-domain validation". The release also added a comparison with TEN VAD. Known issues: "music with voice-like instruments" and very high-pitched voices (cartoons, small children). — [silero-vad releases](https://github.com/snakers4/silero-vad/releases)
- v6.2: reworked training, a "slight quality improvement (no metrics update)" and "higher stability on OOD / rare / strange / unique data". The named edge cases are unusual, child, cartoon and muted voices, muted speech, and low-quality phone calls. These matter for anime, audio drama and whispered content. — [silero-vad releases](https://github.com/snakers4/silero-vad/releases)
- v6.2.2 added an offline ONNX *sequence* model (`silero_vad_16k_sequence.onnx`, 16 kHz only). v6.2.3 (23 Sep, presumably 2026) made torchaudio optional, so audio I/O now needs the `[audio]`, `[codec]` or `[all]` extras. v6.2.1 made onnxruntime optional. — [silero-vad releases](https://github.com/snakers4/silero-vad/releases) (the releases page shows day and month only, so the years are inferred from order. Unverified.)
- Library defaults of `get_speech_timestamps`: threshold 0.5, sampling_rate 16000, min_speech_duration_ms 250, max_speech_duration_s inf, min_silence_at_max_speech 98, window 512 samples. `neg_threshold` defaults to `max(threshold - 0.15, 0.01)`, which gives hysteresis. — [silero-vad utils_vad.py](https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/utils_vad.py). The min_silence_duration_ms and speech_pad_ms defaults were not shown in the fetch. My recollection is 100 ms and 30 ms, which is unverified, so treat it as a gap.

**faster-whisper (SYSTRAN)**
- `VadOptions` defaults: threshold 0.5, neg_threshold None, **min_speech_duration_ms 0**, max_speech_duration_s inf, **min_silence_duration_ms 2000**, **speech_pad_ms 400**, min_silence_at_max_speech 98, use_max_poss_sil_at_max_speech True. The bundled model is **`silero_vad_v6.onnx`**. — [faster-whisper vad.py](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/vad.py)
- The README says the default VAD "only removes silence longer than 2 seconds". It shows `vad_parameters=dict(min_silence_duration_ms=500)` as the tuning example. "Vad filter is enabled by default for batched transcription" (`BatchedInferencePipeline`). Plain `WhisperModel.transcribe` examples pass `vad_filter=True` explicitly. — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)

**WhisperX**
- `load_model` defaults: `vad_method="pyannote"`, `vad_onset 0.500`, `vad_offset 0.363`, `chunk_size 30`. A code comment says chunk_size is "needed by silero since binarization happens before merge_chunks", so Silero is also selectable. — [whisperX asr.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/asr.py)
- The WhisperX paper (Bain et al., 2023) says VAD Cut & Merge "enables a twelve-fold transcription speedup via batched inference" and "improves transcription quality". It frames sliding-window decoding as the cause of drift, hallucination and repetition. Only the abstract was read, so I have no WER numbers. — [arXiv 2303.00747](https://arxiv.org/abs/2303.00747)

**TEN VAD**
- Claims "superior precision compared to both WebRTC VAD and Silero VAD", but only as precision-recall curves, with no numbers. It says Silero lags by "several hundred milliseconds" at speech-to-silence transitions and TEN reacts faster. Hop is 160 or 256 samples (10 or 16 ms) at 16 kHz only. RTF is 0.0086 vs Silero's 0.0127 on a Xeon Gold 6348. The library is about 306 KB vs about 2.2 MB for Silero. License is Apache 2.0 with extra conditions (LPCNet-derived pitch code). Open-sourced in June 2025. Evaluation used LibriSpeech, GigaSpeech and DNS Challenge, with no CJK-specific evaluation stated. — [TEN-framework/ten-vad](https://github.com/TEN-framework/ten-vad)
- pyvideotrans (a popular Chinese video-translation tool) is a conflicting case. A search snippet says "After version 3.92, the default VAD model is ten-vad". The English VAD doc page I fetched says "The current version uses silero as the default VAD model". The same page lists its subtitle-oriented settings: threshold 0.5, max speech 5 s, min speech 2000 ms (merges short segments rather than dropping them), silence split 140 ms, and a "Non-speech Threshold" that "reduces hallucinations but may miss words". — [pyvideotrans VAD doc](https://en.pyvideotrans.com/vad); conflicting snippet from [pyvideotrans.com/vad](https://pyvideotrans.com/vad)

**WebRTC vs Silero, measured** (Barański et al., Whisper large-v3, English, non-speech plus speech-augmented sets)
- Detected-hallucination rate (NO || OL conditions): no VAD 21.3 || 20.3%; **WebRTC VAD 12.5 || 15.4%**; **Silero VAD 0.2 || 0.2%**.
- WER: no VAD 104.8 || 112.0; WebRTC 68.3 || 75.4; Silero **8.0 || 10.8**. — [arXiv 2501.11378](https://arxiv.org/html/2501.11378v1)

**Whispered or soft speech**
- Generic tuning guide: the default threshold of 0.5 suits most audio. Lower it to 0.3–0.4 if speech is lost, and raise it to 0.6–0.7 if noise is detected as speech. — [pyvideotrans VAD doc](https://pyvideotrans.com/vad) (via search snippet)
- whisper.rn shows a soft-speech configuration: threshold 0.3, min speech 100 ms, padding 50 ms. — [whisper.rn VAD options](https://www.mintlify.com/mybigday/whisper.rn/api-reference/vad-options) (via search snippet)
- A Korean production pipeline (SceneMaker, Jul 2026, faster-whisper large-v3 on broadcast content) uses Silero with min speech 1.0 s, max speech 30 s and min silence 0.3 s, with `BatchedInferencePipeline`, batch size 16 and `vad_filter=True`. — [SceneMaker blog (2026-07-10)](https://doc.scenemaker.solbox.com/blog/4/)

**Faster-Whisper-XXL** (Purfview, widely used for anime and JAV subtitling) offers the VAD methods silero_v3, v4 and v5, pyannote_v3, pyannote_onnx_v3, auditok and webrtc. The README does not state the default. — [Purfview/whisper-standalone-win](https://github.com/Purfview/whisper-standalone-win)

### Inferences
- With Whisper-family models, a neural VAD (Silero v5/v6 or pyannote) is the single biggest hallucination guard on content with silence or BGM. WebRTC is clearly inferior.
- faster-whisper's default of 2000 ms min silence and 400 ms padding keeps context but leaves long pauses inside segments. Subtitle pipelines usually lower min_silence to 300–500 ms (SceneMaker 300 ms, README example 500 ms) and cap segment length at about 30 s for Whisper's window, or about 5 s for subtitle-sized pieces (pyvideotrans).
- For ASMR and whispering, a lower threshold (0.3–0.35) plus a small min speech (around 100–250 ms) is the direction the guidance points. Expect breaths and mouth noises to pass as speech, so pair it with post-filters. This is untested on ASMR.
- Silero v6's known weakness on "voice-like instruments" and high-pitched voices bears directly on music-heavy drama and anime-style voices. Consider vocal separation before the VAD for those (see below).

### Gaps
- No numbers found for pyannote segmentation-3.0 vs Silero on CJK drama, or for NeMo MarbleNet. Neither was researched in depth.
- I found no published evaluation of any VAD on whispered or ASMR speech, Japanese or otherwise.
- I found no study quantifying VAD-induced timestamp drift.
- The silero `min_silence_duration_ms` and `speech_pad_ms` library defaults were not confirmed in the fetch.
- The Faster-Whisper-XXL default VAD method and its `--ff_*` filter defaults could not be confirmed. The README points to a Discussions thread.

## Silence and hallucination guards

### Takeaway
Best practice stacks four guards: a neural VAD, Whisper's decoding fallbacks (no_speech 0.6, logprob -1.0, compression ratio 2.4), `condition_on_previous_text=False` (WhisperX's default) or repetition penalties, and a post-filter. The post-filter de-loops repeated text and removes known stock phrases (a "bag of hallucinations") when there is no speech. In Barański et al., VAD plus this post-filter brought WER on noise-contaminated speech from 104.8 down to 6.5. CJK stock phrases include ご視聴ありがとうございました and Amara subtitle credits.

### Cited Findings
- openai-whisper defaults: `no_speech_threshold 0.6`, which marks a segment silent only when the average log probability is also below `logprob_threshold` (-1.0). Also `compression_ratio_threshold 2.4`, where a gzip ratio above it is treated as failed and retried at a higher temperature, and `condition_on_previous_text True`. Per the docstring, disabling that option makes the text less consistent across windows but makes the model "less prone to failure loops". `hallucination_silence_threshold` defaults to None. — [openai/whisper transcribe.py](https://raw.githubusercontent.com/openai/whisper/main/whisper/transcribe.py)
- How `hallucination_silence_threshold` works: it requires `word_timestamps=True`. Each of a segment's first 8 words adds to an anomaly score: +1 if its probability is below 0.15, +(0.133 - duration) × 15 if shorter than 0.133 s, +(duration - 2.0) if longer than 2 s. A segment is anomalous if the score is at least 3 or roughly equals the word count. Anomalous segments bordered by silence longer than the threshold are skipped, and seeking moves past leading or trailing silence. — [openai/whisper transcribe.py](https://raw.githubusercontent.com/openai/whisper/main/whisper/transcribe.py)
- WhisperX defaults: `no_speech_threshold 0.6`, `condition_on_previous_text False`, `hallucination_silence_threshold None`, `suppress_numerals False`. — [whisperX asr.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/asr.py)
- Barański et al. (Jan 2025): Whisper large-v3 hallucinated on 40.3% of 301,317 non-speech inferences, and 9.1% of those were looping. The top outputs were "thank you" (24.8%), "thanks for watching" (10.3%), "so", "thank you for watching", "the", "you", "oh" and "subtitles by the amara org community" (0.46%). The study was English-only. Their delooping plus Bag-of-Hallucinations removal took WER from 104.8 to 17.1 without VAD, and from 8.0 to 6.5 with Silero. — [arXiv 2501.11378](https://arxiv.org/html/2501.11378v1)
- Calm-Whisper: 3 of 20 decoder heads cause over 75% of non-speech hallucinations (UrbanSound). Fine-tuning them gives more than 80% reduction with less than 0.1% WER degradation on LibriSpeech. — [arXiv 2505.12969](https://arxiv.org/html/2505.12969v1)
- Japanese: Whisper often returns "ご視聴ありがとうございました" regardless of input. Commenters attribute this to subtitle credits in the training data, and suggest suppressing the token. — [openai/whisper Discussion #2377](https://github.com/openai/whisper/discussions/2377)
- Mitigations seen in CJK projects: blocklists of outro phrases, applied only when VAD confirms no speech (one Japanese voice-input PR), and a silence threshold of 2.0 s in faster-whisper. — [yosinn1-blip/api-voice-input PR #1](https://github.com/yosinn1-blip/api-voice-input/pull/1); [AviUtl2-WhisperAutoSub](https://github.com/nkopikaso/AviUtl2-WhisperAutoSub) (via search snippet; the 2.0 s attribution to a specific project is unverified)
- Korean production guard set (SceneMaker, Jul 2026):
  - Decoding: `beam_size 5`, `condition_on_previous_text=False`, `repetition_penalty 1.2`, `no_repeat_ngram_size 3`, plus the standard thresholds.
  - Segment filters: drop segments with average logprob below -1.0, and drop Korean segments with a Hangul ratio below 0.3.
  - Short segments: for segments under 3 s whose LID result is not Korean, transcribe twice (as Korean and as the detected language) and drop the segment if both logprobs are below -0.6. Force Korean when LID confidence is below 0.5.
  - Observed ghost subtitles: ご視聴ありがとうございました, "Thanks for watching", Arabic credits.
  
  — [SceneMaker blog](https://doc.scenemaker.solbox.com/blog/4/)
- Qwen3-ASR-Toolkit post-processes its output to "remove common ASR hallucinations and repetitions". — [MarkTechPost, 2025-09-19](https://www.marktechpost.com/2025/09/19/qwen3-asr-toolkit-an-advanced-open-source-python-command-line-toolkit-for-using-the-qwen-asr-api-beyond-the-3-minutes-10-mb-limit/)

### Inferences
- A CJK blocklist should at least include ご視聴ありがとうございました, Amara credits ("subtitles by the amara.org community" and its zh/ja/ko translations), and thanks-for-watching variants. It should drop a match only when VAD or no_speech shows the segment is likely non-speech, because these phrases occur legitimately in drama dialogue.
- `condition_on_previous_text=False` is the consensus for long-form subtitling (WhisperX default, SceneMaker). It trades cross-window consistency for resistance to loops.
- `hallucination_silence_threshold` only works with word timestamps and is off by default everywhere checked. Its anomaly heuristic would also flag very short CJK characters, which is untested.

### Gaps
- I found no published zh/ko-specific bag-of-hallucinations list with frequencies. The Korean closing "시청해 주셔서 감사합니다" and Chinese "字幕由Amara.org社区提供" are widely reported anecdotally, but no primary source was fetched.
- I found no data on hallucination rates for Qwen3-ASR or other LLM-based ASR over silence and music.

## Vocal separation (Demucs, MDX/UVR, Roformer, Spleeter)

### Takeaway
Evidence is thin and mixed. No controlled study measures vocal separation before Whisper or Qwen3-ASR on conversational CJK drama. On lyrics, HTDemucs hurt Whisper v2 and OWSM but helped Whisper v3, and separation reduced substitutions while sometimes increasing hallucinations. Practitioners use it selectively (Faster-Whisper-XXL ships MDX23 Kim_vocal_v2) for loud BGM. Qwen3-ASR's built-in robustness to singing and BGM reduces the need for it.

### Cited Findings
- Lyrics transcription benchmark (Jam-ALT): HTDemucs separation raised Whisper v2 WER from 34.5 to 41.4, lowered Whisper v3 WER from 59.0 to 52.3, and hurt OWSM v3.1. — [arXiv 2408.06370](https://arxiv.org/pdf/2408.06370) (Table 4, via search snippet)
- "Exploiting Music Source Separation for Automatic Lyrics Transcription with Whisper" (Demucs mdx/mdx_extra plus faster-whisper large-v2): separation reduced substitution errors but "can sometimes increase hallucinations". — [Moonlight review](https://www.themoonlight.io/en/review/exploiting-music-source-separation-for-automatic-lyrics-transcription-with-whisper) (secondary source)
- In an openai/whisper discussion, users asked whether Demucs distorts speech enough to hurt recognition, and no data-backed answer was found. — [openai/whisper Discussion #2125](https://github.com/openai/whisper/discussions/2125)
- Faster-Whisper-XXL can preprocess audio with the MDX23 Kim_vocal_v2 model, a UVR-family vocal model. — [Purfview/whisper-standalone-win](https://github.com/Purfview/whisper-standalone-win)
- Mel-RoFormer reports state-of-the-art vocal separation, measured by SDR only with no ASR evaluation. The MVSep MelBand Roformer vocal model likewise reports SDR only. — [arXiv 2409.04702](https://arxiv.org/pdf/2409.04702); [MVSep](https://mvsep.com/algorithms/23)
- Qwen3-ASR-1.7B (released 2026-01-29) reports these WERs:

  | Benchmark | Qwen3-ASR-1.7B | Whisper-large-v3 | GPT-4o |
  |---|---|---|---|
  | ExtremeNoise (Mandarin) | 16.17 | 63.17 | 36.11 |
  | EntireSongs-zh (songs with BGM) | 13.91 | not given | not given |
  | Opencpop (singing) | 3.08 | not given | not given |

  These are vendor-reported numbers. — [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)

### Inferences
- Separation helps only when BGM energy is comparable to speech (insert songs, scored action scenes). It risks artifacts, lost breathy or whispered consonants and new hallucinations otherwise. For ASMR, where the "background" is often the intended triggers and the voice is soft, separation is likely to hurt or destroy content. This is inference, with no data.
- A defensible pattern: run VAD on the separated vocal stem (Silero v6 struggles with voice-like instruments), but transcribe from the original or a mix. Or make separation an opt-in for music-heavy titles. This mirrors SceneMaker's dual-stream design (raw for VAD and LID, enhanced for ASR), but neither variant has been benchmarked.
- With Qwen3-ASR as the recognizer, the case for separation is weaker given its singing and BGM scores.

### Gaps
- I found no speed or VRAM figures for htdemucs, htdemucs_ft, MDX-Net or BS/Mel-Roformer in this pass. Spleeter was not researched.
- I found no evidence on what dubbing pipelines (e.g. pyvideotrans, VideoLingo) or ASMR subtitle communities measure. VideoLingo is known to use Demucs before WhisperX, but this was not verified this session.

## Preprocessing: resampling, loudness normalization, denoising, filters

### Takeaway
16 kHz mono is the universal input (Whisper, Silero, TEN VAD). Denoising before a large pretrained ASR model usually hurts: two 2025–26 studies found that enhancement raised WER for Whisper, and the effect grows with model size. Loudness normalization and high-pass filtering have no ASR evidence either way in what was found.

### Cited Findings
- SAM-Audio enhancement before zero-shot Whisper improved signal metrics but *increased* WER and CER. The degradation grew with larger Whisper models and affected most of the audio, not outliers. — [arXiv 2603.04710 (Mar 2026)](https://arxiv.org/abs/2603.04710)
- MetricGAN+ denoising before four ASR systems including Whisper, on 500 medical recordings under 9 noise conditions: "original noisy audio achieves lower semWER than enhanced audio in all 40 tested configurations". — [arXiv 2512.17562 (Dec 2025)](https://arxiv.org/abs/2512.17562v1)
- SceneMaker uses the opposite approach. It runs DeepFilterNet v3 at 48 kHz with `atten_lim_db=-30` (to preserve singing and speech), then downsamples to 16 kHz mono float32 for ASR only, and keeps raw audio for VAD, LID and diarization. LID accuracy fell from 95.2% on raw audio to 93.4% on denoised audio, which is why LID stays on raw. No ASR WER comparison is given. — [SceneMaker blog](https://doc.scenemaker.solbox.com/blog/4/)
- TEN VAD requires 16 kHz input, and other rates must be resampled. — [ten-vad](https://github.com/TEN-framework/ten-vad). Qwen3-ASR-Toolkit resamples to 16 kHz mono via FFmpeg. — [MarkTechPost](https://www.marktechpost.com/2025/09/19/qwen3-asr-toolkit-an-advanced-open-source-python-command-line-toolkit-for-using-the-qwen-asr-api-beyond-the-3-minutes-10-mb-limit/)

### Inferences
- The default should be no denoising for Whisper or Qwen3-ASR. If denoising is offered, limit attenuation (as DeepFilterNet's atten_lim does) and never feed denoised audio to LID or VAD.
- Whispered and ASMR speech is the case where aggressive denoising is most likely to erase speech.

### Gaps
- I found no ASR evidence for loudnorm, peak normalization, RNNoise or high-pass filtering on CJK content. Faster-Whisper-XXL's `--ff_*` filters could not be confirmed.

## Chunking long audio and context carry-over

### Takeaway
Two families exist. Whisper's sequential 30 s sliding window with optional conditioning on previous text is prone to drift and loops. VAD cut-and-merge into chunks of 30 s or less, decoded independently in batches (WhisperX, faster-whisper batched), is faster (up to 12x) and avoids loops, at the cost of cross-chunk context. LLM-based ASR such as Qwen3-ASR accepts longer inputs, and its API toolkit splits at VAD silences into about 120 s chunks.

### Cited Findings
- WhisperX: VAD Cut & Merge into chunks of up to 30 s (`chunk_size 30`) with `condition_on_previous_text False`, giving a twelvefold speedup via batching. — [whisperX asr.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/asr.py); [arXiv 2303.00747](https://arxiv.org/abs/2303.00747)
- faster-whisper `BatchedInferencePipeline` enables VAD by default. — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- openai-whisper: `condition_on_previous_text=True` makes the text consistent across windows but prone to failure loops. — [transcribe.py](https://raw.githubusercontent.com/openai/whisper/main/whisper/transcribe.py)
- Qwen3-ASR-Toolkit splits at silent pauses into chunks targeting `--vad-segment-threshold-d` = 120 s (the API limit is 3 min), runs 4 threads in parallel by default, and reassembles the results in order. — [MarkTechPost](https://www.marktechpost.com/2025/09/19/qwen3-asr-toolkit-an-advanced-open-source-python-command-line-toolkit-for-using-the-qwen-asr-api-beyond-the-3-minutes-10-mb-limit/)
- Qwen3-ASR (open weights) states long-audio support but gives no maximum duration or VAD method, and advises raising `max_new_tokens`. Qwen3-ForcedAligner-0.6B aligns up to 5 min of speech in 11 languages including zh, ja and ko, with about 43–53 ms mean alignment error. Streaming (vLLM only) gives no timestamps. — [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)
- SceneMaker caps VAD segments at 30 s for faster-whisper. pyvideotrans caps them at 5 s and merges segments under 2 s for subtitle-sized output. — [SceneMaker](https://doc.scenemaker.solbox.com/blog/4/); [pyvideotrans](https://en.pyvideotrans.com/vad)

### Inferences
- For subtitles, VAD-segmented chunks of 30 s or less, decoded independently, are the mainstream choice. A glossary or initial prompt (rather than previous-text conditioning) is the usual way to recover cross-chunk consistency of names. That is inference here, because prompt-based carry-over was not researched in this pass.
- For Qwen3-ASR plus the ForcedAligner, chunks should stay under 5 min so the aligner can produce timestamps.

### Gaps
- I found no study comparing overlapping-chunk stitching against VAD segmentation on CJK WER or timestamp error.
- I found no data on how chunk length affects Qwen3-ASR accuracy.
