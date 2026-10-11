# Open-weights Thai speech recognition models for video subtitles (as of 2026-10-11)

Scope: open models a local app could run for Thai speech to text, with how they load in faster-whisper/CTranslate2 (CT2), their licences, accuracy numbers, failure modes and decoding settings. Repo metadata (licence, commit sha, files, decoder layers, alignment_heads) was read directly from the Hugging Face API and the raw `config.json`/`generation_config.json` files on 2026-10-11 (high confidence). Accuracy numbers are quoted from model cards and papers. Each is labelled **[own]** when the model's authors report it and **[3rd]** when another group does. Thai has no spaces between words, so every number also names its metric.

Confidence key: **H** = read verbatim from a primary file or paper. **M** = a primary source, but the figure comes from a chart or summary or is ambiguous. **L** = inference.

---

## 1. Candidates: repo ids, licence, size, CT2 builds, word-timestamp readiness

### Takeaway
The Whisper-architecture Thai fine-tunes load in faster-whisper after a standard `ct2-transformers-converter` run:
- Typhoon Whisper large-v3 / turbo / medium (SCB 10X)
- Pathumma Whisper large-v3 (NECTEC)
- Thonburian Whisper (biodatlab)

Third-party CT2 builds exist only for Thonburian and Pathumma. None of them comes from the original authors, and **I found no CT2 build of any Typhoon Whisper model**, so the app would convert those itself. Every Thai fine-tune keeps the base model's `alignment_heads` unchanged, so word timestamps will *run*. Their quality was never re-validated after fine-tuning. Qwen3-ASR (lists Thai), the Typhoon Qwen/NeMo models, Pathumma's NeMo model, SeamlessM4T, MMS and wav2vec2 cannot be loaded by faster-whisper.

### Cited Findings

**OpenAI baselines**
- `openai/whisper-large-v3`: licence apache-2.0, sha `06f233fe06e7`. 32 encoder and 32 decoder layers, 128 mel bins, 10 alignment heads. — [HF API](https://huggingface.co/api/models/openai/whisper-large-v3), [generation_config](https://huggingface.co/openai/whisper-large-v3/raw/main/generation_config.json) (H)
- `openai/whisper-large-v3-turbo`: licence MIT, sha `41f01f3fe87f`. 4 decoder layers, 6 alignment heads (`[[2,4],[2,11],[3,3],[3,6],[3,11],[3,14]]`). — [HF API](https://huggingface.co/api/models/openai/whisper-large-v3-turbo) (H)
- OpenAI README lists turbo at 809M params, about 6 GB VRAM and about 8x the speed of large, "with a minimal degradation in accuracy". The same README says turbo "is not trained for translation tasks". — [openai/whisper README](https://github.com/openai/whisper/blob/main/README.md) (H)
- CT2 builds of the baselines:
  - `Systran/faster-whisper-large-v3`: MIT, sha `edaa852ec7e1`. — [HF API](https://huggingface.co/api/models/Systran/faster-whisper-large-v3) (H)
  - `deepdml/faster-whisper-large-v3-turbo-ct2`: MIT, sha `4df90f75321148c3`, alignment heads kept. — [HF config](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/raw/main/config.json) (H)
  - `mobiuslabsgmbh/faster-whisper-large-v3-turbo`: did not return API JSON in this session (unverified).

**Typhoon (SCB 10X, org `typhoon-ai`)**

Paper: [arXiv 2601.13044](https://arxiv.org/abs/2601.13044). Code: [scb-10x/typhoon-asr](https://github.com/scb-10x/typhoon-asr).
- `typhoon-ai/typhoon-whisper-large-v3`: MIT, sha `748e8a418697d5920e62bced10590072dce90da5`, last modified 2026-06-11. Full large-v3 with 32 decoder layers, about 1.55B params, safetensors in transformers format. alignment_heads are identical to large-v3. — [HF API](https://huggingface.co/api/models/typhoon-ai/typhoon-whisper-large-v3), [card](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3) (H)
- `typhoon-ai/typhoon-whisper-turbo`: MIT, sha `3c03fa84c26f172944422ceb8a4e88a2dbc08b10`. 4 decoder layers. alignment_heads are the turbo heads `[[2,4],[2,11],[3,3],[3,6],[3,11],[3,14]]`. — [HF API](https://huggingface.co/api/models/typhoon-ai/typhoon-whisper-turbo), [card](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo) (H)
- `typhoon-ai/typhoon-whisper-medium`: MIT, sha `bc70ae0`, 769M params. Full fine-tune of whisper-medium on about 10,846 h of Thai. The repo also ships a decoder LoRA for contextual biasing. — [card](https://huggingface.co/typhoon-ai/typhoon-whisper-medium) (H)
- The Typhoon cards say "By using this model, you agree to the OpenTyphoon Terms and Conditions", even though the licence is MIT. — [card](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo) (H)
- Training data: 10,999 h in total, of which 10,329.5 h is GigaSpeech2, 631 h internal curated media, 35.4 h Common Voice 17 and 3.2 h internal TTS. — [arXiv 2601.13044 Table 2](https://arxiv.org/html/2601.13044) (H)
- GigaSpeech2's ethics statement restricts the dataset to "non-commercial research and educational purposes". — [GigaSpeech2 paper](https://arxiv.org/html/2406.11546) (H)
- HF searches for "typhoon-whisper ct2", "typhoon whisper faster", "typhoon-whisper-turbo-ct2" and "typhoon-whisper-large-v3-ct2" found no CT2 conversion. The derivatives that do exist are ONNX (`supersuphot/typhoon-whisper-turbo-timestamped`, exported with cross-attentions for word timestamps), MLX, CoreML and GGML. — [HF search API](https://huggingface.co/api/models?search=typhoon-whisper) (H)
- `typhoon-ai/typhoon-whisper-large-v3-ctc`: apache-2.0, sha `4b7b7b836fb98d58972a1a44cf2b8ced56564345`, modified 2026-09-08.
  - A 29M character-CTC head on top of the **frozen** typhoon-whisper-large-v3 encoder, pinned to revision `748e8a4`. It gives Thai word- and character-level timestamps through forced alignment. Words are grouped with pythainlp `newmm`.
  - Its bundled `standalone_align.py` runs in PyTorch, not CT2.
  - Reported [own]: greedy CER 6.72% in ASR mode on gigaspeech2-typhoon, onset p50 25 ms against TTS ground truth, and word-onset p50 48.3 ms on human-annotated C-COST interviews.
  - — [card](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3-ctc) (H)
- `typhoon-ai/typhoon-asr-qwen-1.7b-ctx` and `typhoon-asr-qwen-0.6b-ctx`: apache-2.0, LoRA fine-tunes merged into Qwen3-ASR-*-hf (2.07B and 0.80B params). They accept a bias list in the system prompt. Format is transformers/Qwen3-ASR, not CT2. — [card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)
- `typhoon-ai/typhoon-asr-realtime`: CC-BY-4.0, NeMo FastConformer-Transducer with 115M params (the paper says 115M, the qwen-ctx card table says 109M). It is streaming. — [HF API](https://huggingface.co/api/models/typhoon-ai/typhoon-asr-realtime), [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H)
- Isan dialect models:
  - `typhoon-ai/typhoon-isan-asr-whisper`: apache-2.0, 769M, fine-tuned from `biodatlab/whisper-th-medium-combined`. — [card](https://huggingface.co/typhoon-ai/typhoon-isan-asr-whisper) (H)
  - `typhoon-ai/typhoon-isan-asr-realtime` (NeMo). — [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H)
- `typhoon-ai/monsoon-whisper-medium-gigaspeech2`: apache-2.0. An older (2024) whisper-medium fine-tuned on GigaSpeech2. — [card](https://huggingface.co/typhoon-ai/monsoon-whisper-medium-gigaspeech2) (H)

**Pathumma (NECTEC)**
- `nectec/Pathumma-whisper-th-large-v3`: apache-2.0, sha `6d5e4f2fc7fc2c05447b3626dd346b1793682eb9`, last modified 2025-05-22.
  - Languages are listed as th and en. alignment_heads are identical to large-v3.
  - Its `generation_config.json` forces `[[1,50289],[2,50360],[3,50364]]`, i.e. `<|th|>`, `<|transcribe|>`, `<|notimestamps|>`.
  - The card's "Model Description" and "Limitations" sections read "Additional information is needed".
  - — [HF API](https://huggingface.co/api/models/nectec/Pathumma-whisper-th-large-v3), [generation_config](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3/raw/main/generation_config.json), [card](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3) (H)
- Pathumma CT2 builds (third party):
  - `s2p2/Pathumma-whisper-th-large-v3-ct2`: apache-2.0, sha `7acfaee09636a673d15773dad0b1345121b98d4a`, dated 2024-10-25. Ships `model.bin`, alignment heads kept, 100 lang ids. Its card is 84 bytes, so it gives no conversion details. — [HF API](https://huggingface.co/api/models/s2p2/Pathumma-whisper-th-large-v3-ct2) (H)
  - `PogusTheWhisper/Faster-Pathumma-whisper-th-large-v3-ct2`: gated (`auto`), no licence field. Its config returned 401. — [HF API](https://huggingface.co/api/models/PogusTheWhisper/Faster-Pathumma-whisper-th-large-v3-ct2) (H)
- `nectec/Pathumma-stt-th-fastconformer-rnnt-large`: CC-BY-4.0, NeMo, modified 2026-09-06. A Thai–English model aimed at noise, real-time use and code-switching. — [card](https://huggingface.co/nectec/Pathumma-stt-th-fastconformer-rnnt-large) (H)

**Thonburian Whisper (biodatlab, Mahidol University, with Looloo Technology)**

Paper: "Thonburian Whisper: Robust Fine-tuned and Distilled Whisper for Thai", ICNLSP 2024 — [ACL Anthology](https://aclanthology.org/2024.icnlsp-1.17). I found no arXiv version. Full text not read (M).
- `biodatlab/whisper-th-large-v3-combined`: apache-2.0, sha `ba7197f618400e41b4826c18b2f48e0bc45ed3ca`, dated 2024-02-20. Trained on Common Voice 13 th, google/fleurs and curated data. — [card](https://huggingface.co/biodatlab/whisper-th-large-v3-combined) (H)
- `biodatlab/whisper-th-medium-combined`: apache-2.0, sha `eebf84255cc7f242a504f64ec09ec33d32903fe1`. — [HF API](https://huggingface.co/api/models/biodatlab/whisper-th-medium-combined) (H)
- `biodatlab/distill-whisper-th-large-v3`: MIT, sha `62df42cecab9f484226ad5f9afdb557552021bbb`.
  - The card calls it "based on the Whisper Large Turbo V3 architecture": 4 decoder layers, the turbo alignment heads, 809M params.
  - Training data: CV13, Gowajee, Thai Elderly Speech, scraped data and SLSCU Thai-Central dialect.
  - — [card](https://huggingface.co/biodatlab/distill-whisper-th-large-v3), [Thonburian README](https://github.com/biodatlab/thonburian-whisper) (H)
- `biodatlab/whisper-th-medium-timestamp`: MIT, sha `b88b11d7d4ce7362ff055baf65914fa28613c3f1`.
  - Trained on 30 s concatenations of Porjai clips, with pseudo-timestamps from Silero VAD.
  - The README says it is "Suitable for subtitle creation", but its WER is 15.57 against 7.42 for the non-timestamp medium.
  - — [card](https://huggingface.co/biodatlab/whisper-th-medium-timestamp), [README](https://github.com/biodatlab/thonburian-whisper) (H)
- Other Thonburian checkpoints: small, large-v2 (`whisper-th-large-combined`), distilled small and distilled medium. — [README](https://github.com/biodatlab/thonburian-whisper) (H)
- Thonburian CT2 builds (all third party, none from biodatlab):
  - `Vinxscribe/biodatlab-whisper-th-large-v3-faster`: MIT label, sha `bcbcc289272ad91e2641cd06160d7ad0b0ca62a1`, 10 alignment heads kept. The licence label differs from the apache-2.0 base. — [HF API](https://huggingface.co/api/models/Vinxscribe/biodatlab-whisper-th-large-v3-faster) (H)
  - `Vinxscribe/biodatlab-whisper-th-medium-faster`: sha `704c0db154f842e8957a386323a69e70a630cb10`. — [HF API](https://huggingface.co/api/models/Vinxscribe/biodatlab-whisper-th-medium-faster) (H)
  - `Avocaduu14/whisper-th-large-v3-ct2`: apache-2.0, sha `4ac21c3d2b48f846cd787272777d3f5e6156571d`. int8 quantisation; "format conversion only". — [card](https://huggingface.co/Avocaduu14/whisper-th-large-v3-ct2) (H)
  - `pariya47/distill-whisper-th-large-v3-ct2`: MIT, sha `8c2a2e1caf92c41fb01e3a67f99a473e9817edfa`, float16, turbo alignment heads kept. — [card](https://huggingface.co/pariya47/distill-whisper-th-large-v3-ct2) (H)
  - `Thaweewat/whisper-th-medium-ct2`: apache-2.0, sha `928ce1ae8acf6c14bfa0afc0ec5fe308a07f0f1a`, dated 2023-12-27. Its config.json lists **192 alignment heads** (`[[12,0],[12,1],...]`), which is CT2's fallback of "every head in the second half of the decoder" rather than the 6 tuned heads. No tokenizer.json. — [config](https://huggingface.co/Thaweewat/whisper-th-medium-ct2/raw/main/config.json) (H)
  - `mort666/whisper-large-v3-th-f16-faster`: MIT, sha `974c34759955a465e7ad24cf9456ec6671b57d47`, converted from `mort666/whisper-large-v3-th-fp16`, a base model I could not identify. — [card](https://huggingface.co/mort666/whisper-large-v3-th-f16-faster) (H)

**Qwen3-ASR (Alibaba Qwen)**
- `Qwen/Qwen3-ASR-1.7B`: apache-2.0, sha `7278e1e70fe206f11671096ffdd38061171dd6e5`. The 0.6B and `-hf` variants also exist. The card lists Thai (th) among its 30 languages plus 22 Chinese dialects. — [card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) (H)
- **Qwen3-ForcedAligner-0.6B does not support Thai.** Its 11 languages are zh, en, yue, fr, de, it, ja, ko, pt, ru and es. — [card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B), [Qwen3-ASR tech report arXiv 2601.21337](https://arxiv.org/html/2601.21337) (H)
- Parameter counts from the Typhoon card: Qwen3-ASR-1.7B is 2.07B and Qwen3-ASR-0.6B is 0.80B. — [typhoon-asr-qwen card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)

**Other multilingual models**
- `facebook/seamless-m4t-v2-large`: **CC-BY-NC-4.0**, includes Thai (tha) speech input. — [HF API](https://huggingface.co/api/models/facebook/seamless-m4t-v2-large) (H)
- `facebook/mms-1b-all`: **CC-BY-NC-4.0**, includes Thai. — [HF API](https://huggingface.co/api/models/facebook/mms-1b-all) (H)
- `airesearch/wav2vec2-large-xlsr-53-th`: CC-BY-SA-4.0, a CTC model trained on Common Voice 7. — [card](https://huggingface.co/airesearch/wav2vec2-large-xlsr-53-th) (H)
- `nvidia/canary-1b-v2` and `nvidia/parakeet-tdt-0.6b-v3`: CC-BY-4.0. Each lists 25 European languages and **no Thai**. — [canary API](https://huggingface.co/api/models/nvidia/canary-1b-v2), [parakeet API](https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3) (H)
- `Thanabordee/Qwen3-ASR-0.6B-Thai-Engram`: CC-BY-SA-4.0. A hobby-scale delta: an 8.2M-parameter n-gram memory for a frozen Qwen3-ASR-0.6B. CER fell from 17.81% to 9.04% on 300 Porjai clips [own]. — [card](https://huggingface.co/Thanabordee/Qwen3-ASR-0.6B-Thai-Engram) (H)

**Memory and speed**
- Thonburian README, measured with fp32 transformers on an A100 at batch size 1, for 1 min of audio:

  | Model | Params | Memory | Time per 1 min audio |
  |---|---|---|---|
  | small | 242M | 932 MB | 0.50 s |
  | medium | 764M | 2,923 MB | 0.83 s |
  | large | 1,540M | 6,026 MB | 1.89 s |
  | distilled large | 809M | 3,120 MB | 4.25 s |

  The distilled models are *slower* at batch size 1 because they "are not fully saturating the GPU". — [Thonburian README](https://github.com/biodatlab/thonburian-whisper) (H)
- Typhoon ASR Realtime is claimed to cost about 45x less compute than Whisper large-v3. Typhoon publishes speed against accuracy only as a Pareto image, with no numbers in the text. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H for the claim, figure values not extracted)

**faster-whisper behaviour**
- For language `th`, `split_to_word_tokens` splits "words" at Unicode code-point boundaries (as for zh, ja, lo, my, yue), not at Thai word boundaries. So faster-whisper's `word_timestamps=True` gives character-cluster units, not dictionary words. — [faster-whisper tokenizer.py (v1.2.1)](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/tokenizer.py) (H)

### Inferences
- **Typhoon Whisper turbo and large-v3** are the most promising faster-whisper candidates. They are plain WhisperForConditionalGeneration checkpoints with 128 mel bins and the stock alignment_heads, so `ct2-transformers-converter --model typhoon-ai/typhoon-whisper-turbo --revision 3c03fa84... --copy_files tokenizer.json preprocessor_config.json --quantization float16` should work. That command has not been tested here (L).
- **Word timestamps are a risk for every fine-tune.** Each one reuses the base model's alignment_heads, but those heads were chosen for OpenAI's weights. After a full fine-tune, especially one trained in `<|notimestamps|>` mode (Pathumma forces it), the cross-attention alignment may drift. This is the same class of problem as kotoba-whisper and anime-whisper, unverified for Thai (L). The Typhoon whisper-large-v3-ctc aligner is the authors' own answer to Thai timestamps. That it exists suggests Whisper-native timestamps were not good enough for them (L).
- VRAM in CT2 should be roughly half the Thonburian fp32 figures at fp16 and less again at int8. That is about 3 GB for large-v3 at fp16 and about 1.5–2 GB for turbo-size models. These figures are not measured (L).
- Several third-party CT2 repos carry a licence label different from their base model (Vinxscribe: MIT on an apache-2.0 base). The app should credit and honour the base model's licence (L).

### Gaps
- No pinned CT2 build of any Typhoon model exists, and none is author-made for any Thai model.
- The `mobiuslabsgmbh/faster-whisper-large-v3-turbo` metadata did not load.
- No published test of word-timestamp quality for any Thai Whisper fine-tune under faster-whisper or openai-whisper DTW.
- `mort666/whisper-large-v3-th-fp16` provenance is unknown.

---

## 2. Reported accuracy (metric definitions, own vs independent, overlap risks)

### Takeaway
On the newest same-protocol comparison, Typhoon Whisper large-v3 leads the open models: CER 4.69 on GigaSpeech2 and 6.32 on the TVSpeech in-the-wild TV/talk-show set. Typhoon Whisper turbo is close behind (4.79 / 6.85), then Pathumma (5.84 / 10.36). The vanilla Qwen3-ASR models score 6.09–8.20 / 10.63–11.45. Thonburian large-v3 does worst on in-the-wild audio, because of repetition loops.
- All of these numbers come from SCB 10X, and TVSpeech and gigaspeech2-typhoon are SCB 10X's own benchmarks.
- Typhoon's training data is mostly GigaSpeech2, so the GigaSpeech2 column is in-domain for Typhoon.
- Older numbers (Thonburian on CV13, Pathumma's hidden table) use WER with different Thai word tokenizers (deepcut, newmm), so they cannot be compared with them.

### Cited Findings

**Metric definitions**
- Typhoon paper: CER is the primary metric because "WER is inapplicable to Thai" (no spaces). No tokenizer is used.
  - Normalisation writes numbers in spoken form ("10" becomes สิบ), expands ๆ (เก่งๆ becomes เก่ง เก่ง) and transliterates foreign words (website becomes เว็บไซต์).
  - FLEURS is reported both against the original references and against references normalised to these rules.
  - — [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H)
- Thonburian: WER with the **deepcut** tokenizer, after small normalisations (เเ→แ, ํา→ำ). — [README](https://github.com/biodatlab/thonburian-whisper) (H)
- Pathumma: "WER calculated with **newmm** tokenizer". — [card](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3) (H)
- airesearch wav2vec2 reports WER with PyThaiNLP 2.3.1 newmm, WER with deepcut, and CER side by side. The same output scores 13.63 WER under newmm, 8.15 under deepcut and 2.81 CER. Tokenizer choice alone can swing WER by more than 5 points. — [card](https://huggingface.co/airesearch/wav2vec2-large-xlsr-53-th) (H)
- GigaSpeech2 paper: CER for Thai. Normalisation is NFKC, uppercasing, punctuation removal, and **Arabic numerals converted to words**. — [arXiv 2406.11546](https://arxiv.org/html/2406.11546) (H)
- Qwen3-ASR report: "CER for character-based languages" (examples given: zh, yue, ko). It does not say which metric it uses for Thai. — [arXiv 2601.21337](https://arxiv.org/html/2601.21337) (M)

**Typhoon paper Table 6, CER % [own]**

TVSpeech and GigaSpeech2 are Typhoon's own test sets. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H)

| Model | TVSpeech | GigaSpeech2 | FLEURS orig | FLEURS norm |
|---|---|---|---|---|
| Gemini 3 Pro (proprietary) | 10.95 | 12.50 | 11.35 | 6.91 |
| Biodatlab Whisper Large | 18.96 | 13.22 | 16.50 | 15.26 |
| Biodatlab Distil-Whisper Large | 13.82 | 8.24 | 6.77 | 8.63 |
| Pathumma-Whisper Large-v3 | 10.36 | 5.84 | 6.29 | 7.88 |
| Typhoon ASR Realtime (115M) | 9.99 | 6.81 | 13.87 | 9.68 |
| Typhoon Whisper Turbo | 6.85 | 4.79 | 10.52 | 7.08 |
| Typhoon Whisper Large-v3 | 6.32 | 4.69 | 9.98 | 5.69 |

On FLEURS *original* references, Pathumma (6.29) and the Thonburian distil model (6.77) beat Typhoon (9.98). Typhoon wins only after normalising to its spoken-form rules. The paper puts the gap down to orthography (digits against spoken numbers), not to phonetic errors.

**typhoon-asr-qwen-1.7b-ctx card, CER % [own, SCB 10X]**

Same protocol as above. — [card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)

| Model | GigaSpeech2 | TVSpeech |
|---|---|---|
| typhoon-asr-qwen-1.7b-ctx | 4.86 | 6.87 |
| typhoon-asr-qwen-0.6b-ctx | 5.06 | 7.28 |
| Qwen3-ASR-1.7B | 6.09 | 10.63 |
| Qwen3-ASR-0.6B | 8.20 | 11.45 |
| typhoon-whisper-large-v3 | 4.69 | 6.32 |
| typhoon-whisper-medium | 4.81 | 7.66 |
| typhoon-asr-realtime | 6.89 | 9.92 |
| Pathumma large-v3 | 5.84 | 10.36 |
| biodatlab large-v3-combined | 15.78 | 14.91 |

The card's footnote on the biodatlab GigaSpeech2 figure: "repetition-loop utterances dominate … Thonburian: three, excluding them 8.39".

**GigaSpeech2 paper Table 3, Thai CER % [3rd party, SpeechColab and others]**

Independent of every Thai fine-tuner, and the best independent baseline for vanilla Whisper. — [arXiv 2406.11546](https://arxiv.org/html/2406.11546) (H)

| Model | GigaSpeech2 test | Common Voice th | FLEURS th |
|---|---|---|---|
| Whisper large-v3 | 20.44 | 6.02 | 11.55 |
| Whisper large-v2 | 22.47 | 8.79 | 15.50 |
| MMS L1107 | 31.75 | 14.49 | 23.07 |
| Azure Speech | 17.25 | 10.20 | 13.35 |
| Google USM Chirp v2 | 49.70 | 14.75 | 63.35 |
| GigaSpeech2 zipformer (152M) | 12.46 | 4.15 | 10.54 |

**Qwen3-ASR tech report [own]**
- FLEURS Thai: Qwen3-ASR-0.6B 8.34, Qwen3-ASR-1.7B 6.32 (metric unstated, probably CER).
- MLC-SLM Thai: 19.51 (0.6B) and 14.34 (1.7B).
- No per-language Thai number for Whisper-large-v3.
- — [arXiv 2601.21337 Table A.2](https://arxiv.org/html/2601.21337) (M on the metric, H on the values)

**Monsoon card [own, SCB 10X 2024]**, GigaSpeech2 / CV17. — [card](https://huggingface.co/typhoon-ai/monsoon-whisper-medium-gigaspeech2) (H)

| Model | WER (GS2 / CV17) | CER (GS2 / CV17) |
|---|---|---|
| whisper-large-v3 | 37.02 / 22.63 | 24.03 / 8.49 |
| biodatlab medium | 31.00 / 14.25 | 21.20 / 5.69 |
| biodatlab large-v3 | 29.02 / 15.72 | 19.96 / 6.32 |
| monsoon-whisper-medium | 22.74 / 20.79 | 14.15 / 6.92 |

**Thonburian [own], deepcut WER on CV13 test**

| Model | WER |
|---|---|
| small | 11.0 |
| medium | 7.42 |
| large-v2 | 7.69 |
| large-v3 | 6.59 |
| distil small | 11.2 |
| distil medium | 7.6 |
| distil large | 6.82 |
| medium-timestamp | 15.57 |

- The distil-large card also reports FLEURS at 10.65 for the fine-tune against 25.56 for its base. — [README](https://github.com/biodatlab/thonburian-whisper), [distil card](https://huggingface.co/biodatlab/distill-whisper-th-large-v3) (H)

**Pathumma [own], newmm WER**

This table sits in an HTML comment, so it does not render on the card. — [card source](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3/raw/main/README.md) (H, but hidden by the authors)

| Model | CV18 | Gowajee | LOTUS-TRD | Thai Dialect | Elderly | GigaSpeech2 | FLEURS | Distant Meeting | Podcast |
|---|---|---|---|---|---|---|---|---|---|
| Pathumma large-v3 | 8.68 | 9.84 | 15.47 | 19.85 | 1.53 | 21.66 | 15.65 | 51.56 | 36.47 |
| Thonburian large-v3 | 7.62 | 22.06 | 41.95 | 26.53 | 1.63 | 25.22 | 13.90 | 64.68 | 32.42 |
| whisper-large-v3 | 18.75 | 46.59 | 48.14 | 57.82 | 12.27 | 33.26 | 24.08 | 72.57 | 41.24 |

**Isan dialect, CER % [own, Typhoon]**

| Model | CER |
|---|---|
| Typhoon-Whisper-Medium-Isan | 8.85 |
| Gemini 2.5 Pro | 10.20 |
| Typhoon Isan Realtime (stage 2) | 10.65 |
| Whisper-Medium-Dialect | 17.72 |
| SLSCU Korat | 70.08 |

— [arXiv 2601.13044 Table 7](https://arxiv.org/html/2601.13044) (H)

**Contextual biasing benchmark (third-party dataset `wayu-ai/thai-contextasr-bench`, scored by SCB 10X)**
- With no bias list, CER is 4.83 for typhoon-whisper-large-v3, 5.23 for Pathumma, 5.68 for Thonburian large-v3 and 5.25 for Qwen3-ASR-1.7B. — [card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)

**Train/test overlap**
- Thonburian trained on CV13 and google/fleurs, so its CV and FLEURS test numbers are in-distribution. — [card](https://huggingface.co/biodatlab/whisper-th-large-v3-combined) (H)
- Typhoon trained on GigaSpeech2 and CV17. The card calls gigaspeech2-typhoon "in-domain" and TVSpeech "out-of-domain". — [typhoon-whisper-medium card](https://huggingface.co/typhoon-ai/typhoon-whisper-medium) (H)

### Inferences
- The Typhoon TVSpeech column (YouTube TV/talk shows, CC-BY) is the closest published proxy for "Thai video audio", but it was built by the Typhoon team (L).
- Typhoon's numbers are low partly because its output convention (spoken-form numbers, transliterated English) matches its own normalisation. A subtitle app that wants "10" and "iPhone" written as such would see a different trade-off (L).
- For vanilla large-v3 the only independent figures are CER 6.02 on CV and 11.55 on FLEURS, from the GigaSpeech2 paper. They are consistent with Thai fine-tunes cutting in-the-wild CER roughly in half or better (L).

### Gaps
- No independent Thai number for **large-v3-turbo**. OpenAI's per-language chart is an image, and Typhoon's turbo baseline appears only in a Pareto image.
- No public Thai ASR leaderboard was found in this session.
- No numbers for SeamlessM4T v2 Thai.
- The full Thonburian ICNLSP paper was not read.
- Qwen's metric for Thai is not stated.

---

## 3. Known failure modes on real video

### Takeaway
The documented Thai failures are:
- **repetition loops** (Thonburian large-v3 on GigaSpeech2; Typhoon large-v3's prompt path)
- **number, date and term hallucination** in encoder-decoder models
- **orthographic mismatch** for numbers and English words
- **dialect gaps**, which only dedicated Isan models address

There is little primary evidence about music, laughter or silence specific to Thai.

### Cited Findings
- **Repetition loops:**
  - biodatlab/whisper-th-large-v3-combined scores 15.78 CER on GigaSpeech2, mostly from three repetition-loop utterances (8.39 without them). Typhoon-asr-qwen-0.6b also had one such utterance. — [typhoon-asr-qwen-1.7b-ctx card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)
  - typhoon-whisper-large-v3's prompt pathway "collapses on any list (echo loops, CER 200+)". **Do not use `initial_prompt` with it.** — [same card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)
- **Hallucination:** the Typhoon paper says Whisper-style models are prone to hallucinating numbers, dates and domain terms, and have unpredictable latency. — [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H)
- **Code-switching and numbers in Typhoon output:**
  - Typhoon models "map English to Thai script rather than Latin script".
  - Numbers come out in spoken Thai form.
  - — [arXiv 2601.13044](https://arxiv.org/html/2601.13044) (H)
  - Pathumma's card tags th and en. NECTEC's NeMo model explicitly targets Thai–English code-switching. — [Pathumma card](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3), [Pathumma STT card](https://huggingface.co/nectec/Pathumma-stt-th-fastconformer-rnnt-large) (H)
- **Dialects:**
  - Typhoon cards warn that performance "may vary on dialects or accents not well-represented". Isan needs `typhoon-isan-asr-whisper`. — [card](https://huggingface.co/typhoon-ai/typhoon-whisper-turbo), [Isan card](https://huggingface.co/typhoon-ai/typhoon-isan-asr-whisper) (H)
  - Pathumma's hidden table shows "Thai Dialect" WER of 19.85 for Pathumma, 26.53 for Thonburian and 57.82 for vanilla large-v3. — [card source](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3/raw/main/README.md) (H)
  - I found no open model reported on Southern or Northern Thai.
- **Read speech vs real-world audio:** Pathumma's hidden table shows WER rising from 8.68 on CV18 (read) to 36.47 on podcasts and 51.56 on distant meetings. — [card source](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3/raw/main/README.md) (H)
- **Distilled models on long audio:** a faster-whisper maintainer said distil models "are not good for long-form transcription", in a report of duplicated words on long videos. — [faster-whisper #811](https://github.com/SYSTRAN/faster-whisper/issues/811) (M, via search snippet, not Thai-specific)
- **Long-form timestamps:** Thonburian found that VAD, a Thai word tokenizer and chunking for long-form gives "less insertion error rate" than Whisper's own timestamp mode. — [Thonburian README](https://github.com/biodatlab/thonburian-whisper) (H)
- **Isolated fine-tune quality:** the Thonburian timestamp model loses accuracy (15.57 against 7.42 WER). — [README](https://github.com/biodatlab/thonburian-whisper) (H)

### Inferences
- For TV-drama audio with music, VAD gating before the decoder is the main defence. That is common Whisper practice, but no Thai-specific evidence was found (L).
- If the app needs numbers as digits and English in Latin script, Pathumma or vanilla large-v3 fit that better than Typhoon (L).

### Gaps
- No primary source measures Thai hallucination specifically on music, laughter or silence.
- GitHub issue search was blocked in this session (the API is scoped to configured repos), so no Thai-specific faster-whisper or whisper issues were reviewed.

---

## 4. Recommended decoding settings for Thai

### Takeaway
The model authors give little explicit guidance. What exists says to force `language='th'` and `task='transcribe'`, chunk at 30 s, and use VAD for long-form audio. Avoid `initial_prompt` on Typhoon large-v3. Use bias prompts only in the exact carrier format the model was trained on (typhoon-whisper-medium LoRA) and keep the lists short.

### Cited Findings
- Thonburian: `generate_kwargs={"language":"<|th|>","task":"transcribe"}` with `chunk_length_s=30`. VAD plus chunking recommended for long-form. — [README](https://github.com/biodatlab/thonburian-whisper) (H)
- Pathumma's generation_config already forces `<|th|>` with `<|notimestamps|>`. — [generation_config](https://huggingface.co/nectec/Pathumma-whisper-th-large-v3/raw/main/generation_config.json) (H)
- Prompting and bias lists:
  - Whisper's prompt is capped at 224 tokens within the 448-token decoder window, so bias lists beyond about 15–20 entries "degrade sharply".
  - typhoon-whisper-medium's LoRA must use the exact carrier `"คำศัพท์ที่เกี่ยวข้อง: " + " ".join(entries)`.
  - — [typhoon-whisper-medium card](https://huggingface.co/typhoon-ai/typhoon-whisper-medium) (H)
  - typhoon-whisper-large-v3 echo-loops with any prompt list. — [qwen-ctx card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)
- Qwen bias prompts: the system message is the space-joined bias list followed by `Thai` on its own line, with ≤50 entries recommended. — [typhoon-asr-qwen-1.7b-ctx card](https://huggingface.co/typhoon-ai/typhoon-asr-qwen-1.7b-ctx) (H)
- Qwen's own evaluation used greedy decoding with no language specified. — [Qwen3-ASR card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) (H)
- faster-whisper 1.2.1 defaults: `beam_size=5`, `condition_on_previous_text=True`, `compression_ratio_threshold=2.4`, `no_speech_threshold=0.6`, `repetition_penalty=1`, `no_repeat_ngram_size=0`. A `hallucination_silence_threshold` option is available. — [transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py) (H)
- A faster-whisper maintainer suggested `hallucination_silence_threshold=2` for duplicated-word output. — [faster-whisper #811](https://github.com/SYSTRAN/faster-whisper/issues/811) (M)

### Inferences
- The following settings are standard Whisper practice, untested on these Thai models (L):
  - `language="th"`, `vad_filter=True`
  - `condition_on_previous_text=False`, to limit the loops seen above
  - `beam_size=5`
  - no `initial_prompt` on Typhoon large-v3
  - `compression_ratio_threshold` left at 2.4
- The compression-ratio heuristic is computed on UTF-8 text. Thai characters take 3 bytes, which may shift what 2.4 means for Thai (L, unverified).

### Gaps
- No author of a Thai Whisper fine-tune publishes faster-whisper settings.
- No source compares `condition_on_previous_text` on and off, or beam 1 against beam 5, for Thai.

---

## 5. Models that look relevant but cannot be loaded by faster-whisper/CTranslate2

### Cited Findings
- **Qwen3-ASR-0.6B/1.7B and typhoon-asr-qwen-*-ctx**: audio encoder plus Qwen3 LLM decoder. They run through the `qwen-asr` package, transformers (`-hf`), vLLM, GGUF (llama.cpp) or MLX, not CT2 Whisper. — [Qwen card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B), [HF search](https://huggingface.co/api/models?search=Qwen3-ASR) (H)
  - Timestamps would need an aligner, and Qwen3-ForcedAligner has no Thai. — [Qwen card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) (H)
- **NeMo `.nemo` checkpoints** (FastConformer RNNT/CTC, not Whisper), which need NVIDIA NeMo:
  - typhoon-asr-realtime (CC-BY-4.0)
  - typhoon-isan-asr-realtime
  - typhoon-asr-realtime-nemo-ctc
  - typhoon-asr-streaming-* (115m, nemotron-0.6b)
  - nectec/Pathumma-stt-th-fastconformer-rnnt-large (CC-BY-4.0)

  — [typhoon-asr-realtime](https://huggingface.co/typhoon-ai/typhoon-asr-realtime), [Pathumma STT](https://huggingface.co/nectec/Pathumma-stt-th-fastconformer-rnnt-large) (H)
- **typhoon-whisper-large-v3-ctc**: a CTC head on the Whisper encoder, run through its bundled PyTorch script. It could serve as a separate Thai aligner after CT2 decoding, at the cost of a second encoder pass in PyTorch. — [card](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3-ctc) (H)
- **CTC wav2vec2 models, not Whisper:**
  - SeamlessM4T v2: UnitY architecture, **non-commercial licence**. — [HF API](https://huggingface.co/api/models/facebook/seamless-m4t-v2-large) (H)
  - MMS-1b-all: **non-commercial licence**. — [HF API](https://huggingface.co/api/models/facebook/mms-1b-all) (H)
  - airesearch wav2vec2-XLSR-53-th: CC-BY-SA-4.0. — [card](https://huggingface.co/airesearch/wav2vec2-large-xlsr-53-th) (H)
- **NVIDIA Canary and Parakeet**: no Thai. — [canary-1b-v2](https://huggingface.co/api/models/nvidia/canary-1b-v2), [parakeet-tdt-0.6b-v3](https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3) (H)
- **GigaSpeech2 zipformer** (k2/icefall): not CT2. The dataset is non-commercial. — [arXiv 2406.11546](https://arxiv.org/html/2406.11546) (H)
- **PogusTheWhisper/Faster-Pathumma-whisper-th-large-v3-ct2**: CT2 format but gated with no licence. Not usable without accepting its terms. — [HF API](https://huggingface.co/api/models/PogusTheWhisper/Faster-Pathumma-whisper-th-large-v3-ct2) (H)

### Inferences
- Shortlist for the owner to test, both loadable by faster-whisper after self-conversion (L):
  1. **typhoon-ai/typhoon-whisper-turbo @ `3c03fa8`** (MIT): best speed/accuracy on in-the-wild Thai, but Thai-script numbers and English.
  2. **nectec/Pathumma-whisper-th-large-v3 @ `6d5e4f2`** (Apache-2.0): keeps digits and Latin script per the FLEURS-original result, with the best open dialect numbers among the general models. `s2p2/Pathumma-whisper-th-large-v3-ct2 @ 7acfaee` is a ready conversion.
  - Keep vanilla `Systran/faster-whisper-large-v3` as the baseline.
  - Validate word timestamps against `typhoon-whisper-large-v3-ctc` alignments.

### Gaps
- Not verified by running anything: CT2 conversion of the Typhoon checkpoints, real VRAM, real speed relative to large-v3, and word-timestamp drift.
