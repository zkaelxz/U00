# Thai speech timing accuracy: word/segment timestamps, forced alignment and VAD (as of 2026-10-11)

Baihe's current stack, checked in the repo: faster-whisper with word_timestamps=True, Silero VAD through faster-whisper, optional Demucs, and Qwen3-ForcedAligner through transformers (`qwen3_native.py`: `ALIGNER_REPO = "Qwen/Qwen3-ForcedAligner-0.6B-hf"`, `MIN_TRANSFORMERS = (5, 15, 0)`, `ALIGNER_LANGUAGE_NAMES = {"ja","ko","zh"}`, with nagisa/soynlp as tokenisers for Japanese/Korean). Repo facts are from the local code, not from a web source.

## Whisper / faster-whisper word timestamps for Thai

### Takeaway
Whisper has no Thai word segmentation. For Thai (and zh, ja, lo, my, yue) its "words" are cut wherever the BPE tokens decode to whole Unicode code points, so a "word" is a fragment of a few characters and can split a Thai syllable or word. The timing comes from cross-attention DTW and is rough. I found no published measurement of Whisper's word-timestamp error on Thai. For subtitles, segment-level times with VAD-trimmed edges are usable, and word-level times should be treated as approximate.

### Cited Findings
- openai-whisper `split_to_word_tokens`: `if self.language in {"zh", "ja", "th", "lo", "my", "yue"}:` it returns `split_tokens_on_unicode(tokens)`. The code comment reads: "These languages don't typically use spaces, so it is difficult to split words without morpheme analysis. Here, we instead split words at any position where the tokens are decoded as valid unicode points." — [openai/whisper tokenizer.py](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py)
- biodatlab's Thai Whisper-medium timestamp fine-tune says its timestamps only roughly show when words occur, and that ASR quality may drop because of the pseudo-timestamp training. — [biodatlab/whisper-th-medium-timestamp](https://huggingface.co/biodatlab/whisper-th-medium-timestamp)
- stable-ts features: `suppress_silence` is on by default and moves word timestamps using detected non-speech; `vad=True` uses Silero VAD for that mask; `regroup` rebuilds segments from punctuation and gaps; `refine()` mutes audio around each word to tighten start and end; `load_faster_whisper()` is the faster-whisper integration (refine is noticeably slower there); `nonspeech_skip` skips long non-speech and can cut text and timing hallucinations; `condition_on_previous_text=False` reduces repetition loops at some cost to consistency; `model.align(audio, text, language=...)` aligns a known transcript. The README has no Thai-specific notes. — [jianfch/stable-ts](https://github.com/jianfch/stable-ts)
- stable-ts is archived. The README says "Development on this repository is indefinitely paused", and the repo was archived on 30 May 2026 and is read-only. — [jianfch/stable-ts](https://github.com/jianfch/stable-ts)
- Whisper's Thai text accuracy is itself a limit. A KMITL/KMUTT study of Thai legal news reports homophone errors, with 48% of 94 words transcribed incorrectly in its evaluation. That is a small, domain-specific sample. — [KMITL dspace](https://dspace.kmitl.ac.th/items/40705e90-1ea1-4351-8e9f-1d762dfc8aa3/full)

### Inferences
- Baihe's faster-whisper `word_timestamps=True` output for Thai will produce sub-word "words" (sometimes a single cluster, sometimes several syllables merged). Line-splitting logic that relies on word objects should regroup with a Thai segmenter before using them as break points. PyThaiNLP `newmm` would be a new dependency.
- `condition_on_previous_text=False` and a `hallucination_silence_threshold` (faster-whisper option; I did not fetch its docs this session) are the usual anti-hallucination settings. Both are generic and not Thai-specific.
- stable-ts is a new dependency and is now unmaintained. That argues against adding it. Its useful ideas (silence suppression of word edges using the VAD mask) could be done in Baihe's own code with Silero output it already has.

### Gaps
- I found no paper or GitHub issue that measures Whisper/faster-whisper word-boundary error (ms) for Thai. The general claim that "timestamps are worse for non-space languages" rests on the code behaviour above, not on a measurement.
- I could not confirm whisper-timestamped or WhisperX behaviour on Thai from issues. WhisperX falls back to Whisper times only if no align model is given; see the next section.
- I did not re-verify the faster-whisper `hallucination_silence_threshold` docs this session (the option exists in faster-whisper's `transcribe` signature from prior knowledge, which is unverified here).

## Forced aligners that support Thai

### Takeaway
Qwen3-ForcedAligner, which Baihe already uses, does not support Thai. It lists 11 languages and Thai is not among them, although Qwen3-ASR itself transcribes Thai. The strongest Thai-specific option found is typhoon-ai/typhoon-whisper-medium-ctc (Apache-2.0, char-CTC head on a frozen Whisper-medium encoder, self-reported onset p50 20 ms). Other options are a WhisperX-style alignment with the Thai wav2vec2 model `airesearch/wav2vec2-large-xlsr-53-th` (CC-BY-SA-4.0), MMS-based aligners (uroman romanisation; the default ctc-forced-aligner weights are CC-BY-NC), and MFA's Thai acoustic models. Every one needs new dependencies and, in practice, a Thai word segmenter (PyThaiNLP).

### Cited Findings
- **Qwen3-ForcedAligner:** it "supports timestamp prediction for arbitrary units within up to 5 minutes of speech in 11 languages": Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish. **Thai is not listed.** Accuracy as AAS (accumulated average shift): 27.8 ms average on human-labelled data; 42.9 ms on MFA-labelled data versus 129.8 ms NFA, 133.2 ms WhisperX and 161.1 ms Monotonic-Aligner. — [Qwen/Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
- Qwen3-ASR lists Thai (th) for ASR, but the aligner has the same 11 languages. Native transformers support (`-hf` checkpoints, including `Qwen/Qwen3-ForcedAligner-0.6B-hf`) arrived in a June 2026 update. The README does not document per-language tokenisation (nagisa/soynlp are not mentioned there). — [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)
- **WhisperX:** `DEFAULT_ALIGN_MODELS_TORCH` covers en, fr, de, es, it, and `DEFAULT_ALIGN_MODELS_HF` covers about 35 languages. **There is no 'th' entry.** For a language with no default, `load_align_model` raises ValueError unless a `model_name` is passed. `LANGUAGES_WITHOUT_SPACES = ["ja", "zh"]`, which does not include Thai, so Thai text would be split on spaces. — [whisperX alignment.py](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py)
- **Thai wav2vec2 candidate for WhisperX:** `airesearch/wav2vec2-large-xlsr-53-th` (VISTEC-depa) is a fine-tune of XLSR-53 on Common Voice 7.0 Thai (133 validated hours). Test-unique CER is 2.81%, WER is 13.63% with PyThaiNLP tokenisation and 8.15% with deepcut. The training transcripts were word-tokenised with PyThaiNLP. Licence: CC-BY-SA-4.0. — [airesearch/wav2vec2-large-xlsr-53-th](https://huggingface.co/airesearch/wav2vec2-large-xlsr-53-th)
- **typhoon-whisper-medium-ctc:** a 29M-parameter character-CTC transformer head on the frozen `typhoon-ai/typhoon-whisper-medium` encoder, at 50 Hz (20 ms grid) over an 80-symbol Thai character vocabulary. It force-aligns given text to word or character timestamps, grouping words with PyThaiNLP `newmm` or caller-supplied segmentation. Self-reported results:
  - onset p50 is 20 ms against frame-exact TTS ground truth, with a correctable constant bias of about −15 ms;
  - 0.09% of errors exceed 100 ms;
  - word-onset p50 on human-annotated interviews is 54.0 ms, including a +53 ms convention offset against MAUS; bias-discounted jitter is 21.3 ms;
  - pause-instrument per-speaker r is 0.979, versus XLSR 0.961, MFA-Thai 0.767 and MMS 0.752.
  It also does greedy CTC ASR with CER 6.86% on gigaspeech2-typhoon. Licence: Apache-2.0. Dependencies: torch, torchaudio, transformers, safetensors, huggingface_hub, soundfile, pythainlp. It includes a `standalone_align.py`. — [typhoon-ai/typhoon-whisper-medium-ctc](https://huggingface.co/typhoon-ai/typhoon-whisper-medium-ctc)
- **torchaudio MMS_FA:** the transcript must be normalised and romanised first (uroman) to a-z plus apostrophe. The tutorial demonstrates German, Chinese, Polish, Portuguese and Italian, but not Thai. For non-space scripts it says word-level alignment needs a separate word tokenizer. The tutorial warns that its APIs "are deprecated in 2.8 and will be removed in 2.9" (TorchAudio is in maintenance mode). — [torchaudio multilingual forced alignment tutorial](https://docs.pytorch.org/audio/main/tutorials/forced_alignment_for_multilingual_data_tutorial.html)
- **ctc-forced-aligner:** its default model is `MahmoudAshraf/mms-300m-1130-forced-aligner`. It takes ISO 639-3 language codes (Thai would be `tha`), claims about 1,129 languages, and has a `--romanize` flag that is needed for non-Latin scripts with the default model. `--split_size` is sentence/word/char. The code is BSD-2-Clause, but **the default model weights are CC-BY-NC 4.0**, so the README says to use another model for commercial use. It has no tagged releases. — [MahmoudAshraf97/ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)
- **Montreal Forced Aligner:** Thai acoustic models exist: Thai CV v2.0.0 (XPF phones, CC-0), and Thai MFA v2.0.0, v2.0.0a and v3.0.0 (MFA phones, CC BY 4.0). The acoustic index page does not mention a Thai dictionary or segmentation. — [MFA models: acoustic index](https://mfa-models.readthedocs.io/en/latest/acoustic/index.html)
- typhoon's comparison table puts MFA-Thai (r 0.767) and MMS (r 0.752) well below XLSR and its own head on its pause-instrument metric. — [typhoon-whisper-medium-ctc](https://huggingface.co/typhoon-ai/typhoon-whisper-medium-ctc)

### Inferences
- Baihe's Qwen3 aligner path must refuse or skip Thai. Today `ALIGNER_LANGUAGE_NAMES` has only ja/ko/zh, and `forced_align.py` raises "doesn't cover language=..." for others. Adding "th" there would be wrong; Thai is not a supported aligner language.
- Ranked for Baihe:
  1. **typhoon-whisper-medium-ctc.** It is Thai-specific, Apache-2.0, has the best published Thai numbers (self-reported), and reuses torch/transformers. The new dependencies are pythainlp plus about a Whisper-medium encoder download.
  2. **airesearch wav2vec2 Thai through a WhisperX-style CTC alignment.** Only transformers is needed; Baihe would write its own CTC trellis or use `torchaudio.functional.forced_align`, which is deprecated in 2.9. The CC-BY-SA licence is fine for local use. Word grouping still needs pythainlp.
  3. **ctc-forced-aligner/MMS.** It adds uroman romanisation (lossy for Thai tones and vowel length) and the default weights are non-commercial.
  4. **MFA.** It is a heavy conda/Kaldi-based install, scored worst in typhoon's comparison, and is not suitable as an optional pip dependency.
- Subtitle cues need line-level start/end, not phone-level. Even about 50 ms median error is far inside what viewers notice. The practical gain over Whisper is in fixing gross errors (hundreds of ms to seconds, from hallucinated or drifted segments), which CTC alignment against the final text does well.
- A Thai word segmenter (PyThaiNLP `newmm`, or deepcut) plays the role nagisa/soynlp play for ja/ko. It would be a new optional dependency registered in `diagnostics.OPTIONAL_DEPENDENCIES`.

### Gaps
- I found no independent (non-self-reported) benchmark of Thai boundary error in ms for any aligner. The typhoon numbers are from its own model card and a benchmark (thai-aligner-bench) I did not inspect. I did not read the related arXiv paper 2601.13044 (Typhoon ASR Real-time) in full; its abstract has no alignment numbers.
- I found no published MMS paper number for Thai alignment, and no Thai-specific uroman quality evaluation.
- The NeMo NFA / CTC-segmentation Thai models were not investigated (out of tool budget).
- I did not confirm that a Thai MFA pronunciation dictionary exists; only the acoustic models were seen.
- I did not verify how Qwen3-ForcedAligner tokenises Thai text if it is forced anyway; no source documents it.

## VAD for Thai video (Silero, pyannote, Whisper) and vocal separation

### Takeaway
Silero VAD is language-agnostic (trained on corpora covering 6000+ languages), and nothing Thai-specific is published. faster-whisper's defaults (min_silence 2000 ms, pad 400 ms) are tuned for ASR chunking, not cue edges. Tighter values are needed when VAD defines subtitle boundaries. Evidence that Demucs-style separation helps speech ASR under music is mixed: it helps at low SNR and hurts on clean speech.

### Cited Findings
- faster-whisper `VadOptions` defaults: `threshold=0.5`, `neg_threshold=None`, `min_speech_duration_ms=0`, `max_speech_duration_s=inf`, `min_silence_duration_ms=2000`, `speech_pad_ms=400`. — [faster-whisper vad.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py)
- Silero VAD "was trained on huge corpora that include over 6000 languages" and "performs well on audios from different domains with various background noise and quality levels". It supports 8 kHz and 16 kHz input, and a 30+ ms chunk takes under 1 ms on one CPU thread. The README describes no music-specific behaviour. The licence shown is MIT, though one badge reads CC BY-NC 4.0 (conflicting). — [snakers4/silero-vad](https://github.com/snakers4/silero-vad)
- In BCN2BRNO (Albayzin 2020, Spanish TV), Demucs voice-stem extraction "significantly eliminated the background music" but gave a small overall WER increase. Separation helped only in certain SNR ranges and was detrimental on clean samples. — [arXiv 2101.12729](https://arxiv.org/pdf/2101.12729)
- Syed et al. use music source separation with Whisper for lyrics. For short-form audio they report a consistent WER reduction (with a concatenation method). For long-form audio they use the separated vocals as a vocal-activity detector to derive segment boundaries, and report SOTA open-source results on Jam-ALT without training. — [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)

### Inferences
- For Thai TV with music beds (lakorn, variety shows), the most defensible use of Demucs in Baihe is **running VAD on the vocal stem** to get cue boundaries, as in Syed et al. Feeding separated audio to ASR should not be the default, because separation can raise WER on clean speech (BCN2BRNO). Baihe already has optional Demucs, so this needs no new dependency.
- Suggested starting thresholds for Thai conversational cues are generic, not Thai-validated:
  - `min_silence_duration_ms` about 300–500 ms, so turn-taking pauses split cues;
  - `speech_pad_ms` about 100–200 ms, so cues don't start long before speech;
  - threshold 0.5, raised toward 0.6 on music-heavy audio.
  These are my inference from the defaults and their docstrings. No source measures them on Thai.
- Silero tends to fire on sung vocals (it is a voice detector). Theme songs in Thai dramas will produce speech regions; separation does not remove singing either. Recognising songs needs a separate music/singing decision. I found no source quantifying this.

### Gaps
- I found no Thai-specific VAD evaluation for Silero, pyannote or Whisper's internal no-speech detection, and no published recommended Thai thresholds.
- pyannote was not investigated in depth (out of tool budget). I found no primary source comparing pyannote and Silero on music-bed TV audio.
- I found no paper measuring Demucs/MDX-then-Whisper on Thai.

## Thai prosody facts that affect cue timing

### Takeaway
Thai rhythm class is contested: it is classed as syllable-timed by some metrics and stress-timed by others. Phrase-final and prosodic-word-final syllables carry prominence and extra length, which tends to push cue end times later than a text-length estimate would. I could not find a published Thai syllables-per-second figure for TV or spontaneous speech.

### Cited Findings
- Rhythm classification is disputed. Luangthongkum (1977) suggested Thai is stress-timed and several PVI studies agree, while Grabe & Low (2002) place Thai among syllable-timed languages. — [Palahan & Tongvivat 2025 (Sunderland)](https://sure.sunderland.ac.uk/id/eprint/19067/1/2025_Palahan%26Tongvivat_Rhythm.pdf)
- A pedagogical (non-experimental) source says Standard Thai gives the last full syllable of a prosodic word the greatest prominence and that phrase ends commonly get extra timing. Earlier syllables may reduce to short "minor syllables". — [elon.io Thai rhythm and stress](https://elon.io/grammar/thai/pronunciation/rhythm-and-stress)
- Vowel durations in Thai shrink as speaking rate increases (Thai has phonemic vowel length). — [UConn dissertation, effects of speech rate on Thai vowel length](https://digitalcommons.lib.uconn.edu/dissertations/AAI8710281)
- Coupé et al. (2019, Science Advances) include Thai (Tai-Kadai) among 17 languages, using read texts with pauses over 150 ms excluded. The cross-language mean speech rate is 6.63 syllables/s (SD 1.15); individual speakers range from about 4.3 to 9.1 syllables/s; mean information rate is about 39 bits/s. **Thai's own value is only in the figures and supplement, which I could not retrieve.** — [Coupé et al. 2019 (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6984970/)

### Inferences
- With roughly 6–7 syllables/s as a cross-language read-speech mean (not Thai-specific), and Thai script averaging several characters per syllable, a character-per-second reading-speed limit tuned for Latin text will be exceeded on fast Thai dialogue. Reading speed for Thai should be calibrated per syllable or per word, not per raw character (Thai combining vowel and tone marks inflate character counts). This is an inference, not a sourced number.
- Final particles (ครับ/ค่ะ/นะ/จ้ะ) are utterance-final and often lengthened. That fits the phrase-final lengthening described above, but I found no measured particle durations. Practically, aligners that end a cue at the last character's CTC spike may cut off a lengthened particle, so a small end pad (about 100–200 ms) after the aligned end is reasonable.

### Gaps
- I found no Thai-specific syllables/second figure for TV or spontaneous speech (Coupé et al.'s Thai value was not retrievable; the science.org page returned 403).
- I found no measured final-particle lengthening durations. Primary phonetic studies (e.g. Tingsabadh & Abramson) were not retrieved.
- No source found covers Thai subtitle reading-speed norms (characters or syllables per second).
