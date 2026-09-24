# Phase 1 — Architecture & Model Research

Status: proposal for review. No application code has been written yet, per the
project brief ("do not start by writing the full application"). This document
covers items 1–5 of the requested Phase 1 deliverable. Item 6 (first
implementation) starts only after this is agreed on.

All claims below are sourced from each project's own GitHub repo, Hugging Face
model card, or official blog post (linked inline). Where evidence was
insufficient to support a claim (e.g. cross-video voiceprint reliability), that
is stated explicitly rather than assumed.

---

## 1. Verdict on the proposed pipeline

The proposed shape (extract → VAD → diarize → ASR → align → translate →
subtitle) is directionally right, and Qwen3-ASR + Qwen3-ForcedAligner turn out
to be a genuinely good fit — they're real, open-weight, Apache-2.0, and built
for exactly this language set. But one ordering decision in the draft
conflicts with a hard requirement you stated, and needs to change:

> **Diarization and transcription must be decoupled, not sequential.**
> The draft pipeline runs diarization first, then presumably transcribes
> per-diarized-segment. But you explicitly require: *"reprocessing diarization
> without necessarily retranscribing everything."* If ASR is run on
> diarization-cut segments, the two stages are fused and you can't touch one
> without the other. The fix: run **VAD → ASR** and **VAD → diarization** as two
> independent branches over the same VAD segments, each producing its own
> artifact, and only **merge** them (assign a speaker to each word/segment by
> timestamp overlap) at a later, cheap, re-runnable step. This is the same
> technique WhisperX uses for its diarization integration. It also means a
> corrected diarization instantly re-applies to the existing transcript with no
> re-transcription, and vice versa.

Everything else below assumes this corrected shape.

---

## 2. Recommended architecture

```
                                   ┌─────────────────────────┐
                                   │   Source video/audio      │
                                   └────────────┬─────────────┘
                                                │ ffmpeg (stream copy / extract only)
                                   ┌────────────▼─────────────┐
                                   │  Audio (WAV 16k mono)      │  artifact: audio
                                   └────────────┬─────────────┘
                                                │ Silero VAD (CPU, cheap)
                                   ┌────────────▼─────────────┐
                                   │  VAD speech segments       │  artifact: vad_segments
                                   └──────┬───────────┬───────┘
                    ┌────────────────────┘           └────────────────────┐
                    ▼                                                     ▼
      ┌─────────────────────────┐                         ┌─────────────────────────────┐
      │  Diarization branch       │                         │  Transcription branch          │
      │  pyannote community-1     │                         │  Qwen3-ASR (0.6B/1.7B)          │
      │  (windowed + stitched     │                         │  → Qwen3-ForcedAligner          │
      │  for 20h+ audio)          │                         │  (word timestamps)               │
      └────────────┬─────────────┘                         └───────────────┬──────────────┘
     artifact: diarization_turns                            artifact: raw_transcript (RAW, never overwritten)
                    │                                                       │
                    └───────────────────────┬───────────────────────────────┘
                                            ▼
                              ┌───────────────────────────────┐
                              │  Merge: assign speaker per word  │  artifact: speaker_transcript
                              │  by timestamp overlap             │
                              └────────────────┬───────────────┘
                                               │  (manual speaker corrections → EDITED, RAW untouched)
                                               ▼
                              ┌───────────────────────────────┐
                              │  Context-aware translation        │  artifact: translation
                              │  local instruct LLM (Qwen2.5/3    │  (sliding dialogue window +
                              │  7B–14B) or pluggable API          │   glossary + speaker labels)
                              └────────────────┬───────────────┘
                                               │  (manual edits → EDITED)
                                               ▼
                              ┌───────────────────────────────┐
                              │  Subtitle segmentation/export     │  → SRT / ASS / VTT
                              └───────────────────────────────┘

  Optional side-channel: speaker embeddings (WeSpeaker/CAM++) extracted per
  diarized turn → stored as a per-project "voiceprint library" → cosine-
  similarity suggestion (never silent auto-assignment) when processing a new
  video with the same project's known speakers.
```

Each box with an `artifact:` label is a **separate row in the database**, versioned, never destructively overwritten (see §6). Any stage can be re-run in isolation and only invalidates its own downstream merge, not its siblings.

---

## 3. Model comparison

### 3.1 ASR

| Model | Params / VRAM | ja/zh/ko | Native timestamps | Diarization | License | Verdict |
|---|---|---|---|---|---|---|
| **Qwen3-ASR-1.7B** ([repo](https://github.com/QwenLM/Qwen3-ASR), [card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)) | ~2B params, bf16; comfortably <4 GB VRAM | Yes — 30 languages incl. ja/zh/ko/yue + 22 Chinese dialects | **No** — transcription-only, timestamps require the separate ForcedAligner | No | Apache-2.0 | **Primary.** Purpose-built for this exact language set, open weights, local via `transformers`/vLLM, small footprint leaves headroom for other stages. WER on FLEURS (4.90 avg) edges out Whisper-large-v3 (5.27). |
| **Qwen3-ASR-0.6B** | ~0.6B, <2 GB | Same coverage, lower accuracy | Same (needs aligner) | No | Apache-2.0 | Fast fallback / benchmark comparison point. |
| **Whisper large-v3** (via faster-whisper/CTranslate2) | ~3–3.5 GB int8, ~10 GB fp16 | Broad but weaker on ja/ko than purpose-built CJK models per community benchmarks | Segment-level native, word-level via WhisperX's wav2vec2 alignment | No | MIT | Mature, battle-tested, huge ecosystem (WhisperX, faster-whisper). Kept as **benchmark baseline** and fallback backend. |
| **Whisper large-v3-turbo** | ~1.5 GB int8 | ~4x faster, ~0.4pt WER regression vs large-v3 on average, larger regression reported specifically on Japanese/Korean | Same as above | No | MIT | Speed option for benchmark mode only; not recommended as default given JA/KO accuracy tradeoff on a quality-first project. |
| **SenseVoice(Small)** ([repo](https://github.com/QwenAudio/SenseVoice), FunASR ecosystem) | Small, CPU-viable, low VRAM | zh/yue/en/ja/ko explicitly | Native word timestamps | No | Code MIT; **weights under the FunASR Model Open Source License Agreement**, not Apache/MIT — check terms before commercial use | Strong CJK-specific alternative for the benchmark harness; skip as default only because its weight license is non-standard and needs explicit user sign-off. |

**Not selected:** the "Qwen3-ASR API" referenced by `QwenLM/Qwen3-ASR-Toolkit` is DashScope's **cloud** product — a different thing from the open-weight `Qwen/Qwen3-ASR-1.7B` model. We use the open-weight local model only; the cloud API stays available later as an optional pluggable backend, same as translation.

### 3.2 Forced alignment (word timestamps)

| Model | Coverage | Constraint | License |
|---|---|---|---|
| **Qwen3-ForcedAligner-0.6B** ([card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)) | Confirmed: zh, en, yue, fr, de, it, **ja, ko**, pt, ru, es (11 languages) | **Max 5 minutes of audio per call** — must be chunked per VAD segment/utterance, not run on raw 20h audio | Apache-2.0 |
| **wav2vec2-based alignment (WhisperX's method)** | Language-dependent phoneme model availability; JA/ZH/KO coverage is inconsistent across public wav2vec2 checkpoints | No hard duration cap but Issue #1247 on `m-bain/whisperX` reports word timestamps can be noticeably off vs. Montreal Forced Aligner | Model-dependent |

Qwen3-ForcedAligner is the better fit here specifically because it explicitly covers all three target languages and comes from the same model family as the ASR, but the **5-minute-per-call ceiling is a real engineering constraint** we must design the chunking logic around (align per-VAD-segment, not per-file).

### 3.3 Speaker diarization

| Model | DER quality | Overlap handling | VRAM | Local/offline | License |
|---|---|---|---|---|---|
| **pyannote/speaker-diarization-community-1** ([card](https://huggingface.co/pyannote/speaker-diarization-community-1)) | Reported by pyannoteAI to significantly outperform 3.1 (e.g. DER 11.7 AISHELL-4, 11.2 VoxConverse), scored with **no forgiveness collar and overlap counted against the score** — a stricter, more honest number than most published diarization DERs | Native overlapped-speech detection | Small (<2 GB typical for pyannote-class models) | **Yes** — "works without internet connection" after first download; requires accepting HF terms once | CC-BY-4.0 |
| **pyannote/speaker-diarization-3.1** | Established baseline, superseded by community-1 per pyannoteAI's own comparison | Yes, via overlapped-speech-detection sub-model | Similarly small | Yes, fully local | MIT |
| **NVIDIA NeMo Sortformer** ([models](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2)) | End-to-end transformer, competitive, natively supports up to 4 speakers (community forks like `Ultra-Sortformer` extend this) | Handled end-to-end by the architecture | Not separately documented for the diarizer alone; NeMo's own VRAM figures we found were for a full voice-agent stack (13–21 GB) including an LLM, not the diarizer in isolation — **cannot state a reliable standalone VRAM number** | Yes, `.nemo` checkpoint loads locally | NVIDIA/NeMo licensing, heavier install (full NeMo toolkit, more Linux-oriented tooling) |

**Recommendation:** `pyannote/speaker-diarization-community-1` as default — best published quality, genuinely free/local, small footprint, and pip-installable (important for a Windows-first desktop app where NeMo's heavier toolkit is more friction to package and support). Keep 3.1 as a lighter fallback. NeMo Sortformer noted as a future option if diarization quality on your actual content proves insufficient — not adopted now because we can't currently back a VRAM claim for it with evidence, and its install footprint is heavier for what should be a double-click Windows app.

**20+ hour audio:** none of these pipelines are validated for single-pass runs over multi-hour audio at once. The realistic approach — used broadly in the field, not sourced from a specific benchmark — is to diarize in overlapping windows (e.g. 10 minutes with a 30-second overlap) and stitch speaker clusters across windows by embedding similarity, rather than trusting a single 20-hour forward pass.

### 3.4 Speaker embeddings / cross-video voiceprints (bonus feature)

Both pyannote's pipeline (WeSpeaker embedding component) and ModelScope's **3D-Speaker/CAM++** ([repo](https://github.com/modelscope/3D-Speaker)) expose fixed-size speaker embeddings usable for cosine-similarity matching. CAM++ specifically is documented as faster and more accurate than ECAPA-TDNN/ResNet34 at speaker verification.

**What we can honestly promise:** extracting an embedding per diarized speaker turn, averaging into a per-speaker "voiceprint" centroid, storing it in a local project-scoped library, and computing cosine similarity against saved voiceprints when a new video is processed — surfaced as a **suggestion the user confirms**, never a silent auto-label.

**What we cannot promise:** that this reliably re-identifies the same person across different recordings, microphones, background noise levels, and emotional/vocal register changes typical of VOD content. No source we found benchmarks this specific use case (recurring streamers across sessions), and generic speaker-verification benchmarks (VoxCeleb-style) don't transfer cleanly to noisy long-form multi-speaker VOD audio. This ships as an **experimental, human-confirmed suggestion feature**, not a claimed capability, and should be validated against your actual content in the benchmark phase before you rely on it.

### 3.5 Translation

This is the one place the original proposal's implicit assumption (that a dedicated "translation model" is the right primary tool) doesn't hold up under research, and it materially changes the recommendation.

| Option | Context handling | ja/zh/ko | VRAM (local, quantized) | License | Verdict |
|---|---|---|---|---|---|
| **TranslateGemma** (4B/12B/27B, [card](https://huggingface.co/google/translategemma-12b-it)) | **Structured single-call schema only** — official chat template takes `type` / `source_lang_code` / `target_lang_code` / `text` fields, ~2,000 token context; free-form multi-turn "give it a dialogue window + glossary + speaker labels" prompting is explicitly *not* the officially supported use pattern | 55 languages incl. ja/zh/ko | 4B-Q4 ~3 GB, 12B-Q4 ~7 GB | Gated under Gemma Terms of Use (permissive-with-restrictions, not OSI-open) | **Not selected as primary.** It's a narrow, high-quality sentence/short-document MT model, not a conversational context engine. Using it would mean re-implementing the exact "translate each line in isolation" anti-pattern the brief explicitly rejects. Kept as an optional fast/cheap backend for the benchmark harness. |
| **Qwen2.5/Qwen3-Instruct (7B–14B), GGUF via llama.cpp/Ollama** | Full instruction-following — arbitrary prompt: N-line sliding dialogue window, speaker labels, glossary, explicit "preserve ambiguity, don't invent facts" instructions, strict JSON output mapping segment id → translation | Strong; Qwen models are explicitly multilingual-trained (JMMLU/KMMLU-benchmarked) | 7B Q4_K_M ≈ 4.5–5 GB (fits with headroom for KV cache); 14B Q4_K_M ≈ 8.7 GB — **does not comfortably fit inside 8 GB total VRAM alongside any context window**, so 14B is offered as an opt-in "if you accept partial CPU offload / slower speed" tier, not the default | Apache-2.0 | **Primary.** Only architecture here that actually satisfies the context-aware, glossary-aware, ambiguity-preserving translation requirement while running fully local and sequentially loadable. |
| **Qwen-MT** (DashScope API) | Purpose-built MT LLM, strong quality, benchmarked ahead of GPT-4.1-mini/Gemini-2.5-Flash by Alibaba's own comparison | 92 languages | N/A — **cloud API only**, no evidence found of open local weights | Cloud/proprietary | Optional pluggable cloud provider only, alongside Claude/OpenAI-compatible APIs — never the default, per the local-first requirement. |
| **NLLB-200** | Pure sentence-level MT, not prompt-steerable | Broad coverage | Moderate | CC-BY-NC (non-commercial) | Not selected — same "translate lines in isolation" limitation as TranslateGemma, plus a non-commercial license that's a poor fit even as a fallback. |

**Design implication:** the translation stage is not "run a translation model" but "run an instruction-tuned LLM with a carefully constructed prompt" — a sliding window of ±N subtitle lines with speaker attribution, the project glossary, and explicit instructions to preserve uncertainty rather than invent content, returning structured JSON keyed by segment id so timestamps never drift. `Huanshere/VideoLingo`'s public approach (LLM-based semantic segmentation + a terminology knowledge base for consistency) validates this general shape; we're not reusing its code, just the validated concept.

---

## 4. Recommended MVP (Phase 2 target)

The smallest pipeline that proves the ML architecture actually works, per the brief's own Phase 2 definition:

1. `ffmpeg` extracts mono 16kHz WAV from a video/audio file (no re-encoding of the source video).
2. Silero VAD segments speech.
3. Qwen3-ASR-1.7B transcribes each VAD segment (RAW artifact, immutable).
4. Qwen3-ForcedAligner produces word timestamps per segment.
5. pyannote/speaker-diarization-community-1 diarizes the same VAD segments independently.
6. Merge step assigns a speaker label to each word/segment by timestamp overlap → speaker-labeled transcript.
7. A local Qwen2.5/3-Instruct model (7B, Q4 GGUF) translates using a sliding dialogue-window prompt with speaker labels, returning structured JSON.
8. Subtitle writer emits SRT (ASS/VTT deferred to Phase 6 UI work — SRT alone is enough to validate the pipeline).

All of this as a **CLI**, operating on a 5–10 minute representative clip, with each stage's output written to disk as a distinct file (not just piped in-memory) — this is what makes the Phase 3 benchmark mode possible without extra plumbing.

---

## 5. Project structure

```
U00/
├── docs/
│   └── phase1-architecture.md          # this document
├── backend/
│   ├── pyproject.toml
│   ├── vod_pipeline/
│   │   ├── config.py                   # paths, model choices, GPU limits — all configurable
│   │   ├── models/
│   │   │   ├── model_manager.py        # load/unload/VRAM-aware sequencing
│   │   │   ├── asr.py                  # Qwen3-ASR + Whisper + SenseVoice backends (pluggable interface)
│   │   │   ├── alignment.py            # Qwen3-ForcedAligner backend
│   │   │   ├── diarization.py          # pyannote backend(s)
│   │   │   ├── embeddings.py           # speaker voiceprint extraction/matching
│   │   │   └── translation.py          # local LLM + pluggable API providers
│   │   ├── pipeline/
│   │   │   ├── audio_extraction.py
│   │   │   ├── vad.py
│   │   │   ├── merge_speakers.py       # timestamp-overlap merge of ASR + diarization
│   │   │   ├── translate_context.py    # sliding-window prompt construction
│   │   │   └── subtitles.py            # SRT/ASS/VTT writers + resegmentation
│   │   ├── jobs/
│   │   │   ├── queue.py                # SQLite-backed job table, checkpointing, resume
│   │   │   └── worker.py
│   │   ├── db/
│   │   │   ├── models.py               # SQLAlchemy models: Project, Video, Artifact, Segment, Speaker, Glossary
│   │   │   └── migrations/
│   │   └── api/
│   │       └── routes/                 # FastAPI routers (projects, jobs, media, export, glossary)
│   └── tests/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── pages/                      # Dashboard, NewProject, Import, Processing, Editor
│       ├── components/                 # timeline, subtitle list, video player, review filters
│       └── api/
├── benchmark/
│   └── run_benchmark.py                # Phase 3 harness: same clip, multiple configs, compare
├── data/                                # local project data (videos, audio, artifacts, exports) — gitignored
├── launcher/
│   └── start.ps1 / start.bat            # Phase 8: double-click → server → browser
└── README.md
```

---

## 6. Data model (artifact-based, non-destructive)

Every stage writes a new row, never mutates a prior one:

```
Project (source_lang, target_lang, expected_speakers, glossary, asr_model, translation_model, ...)
  └─ Video (original_path, duration, checksum)
        └─ Artifact (kind, version, status, created_at, params_used)
             kind ∈ {audio, vad_segments, diarization_turns, raw_transcript,
                     edited_transcript, speaker_transcript, translation,
                     edited_translation, subtitle_export}
```

`raw_transcript` is written once by ASR and is never overwritten. Corrections produce a new `edited_transcript` artifact that references its `raw_transcript` parent. Re-running diarization creates a new `diarization_turns` artifact and a new `speaker_transcript` merge — the underlying `raw_transcript` is untouched, satisfying the "reprocess diarization without retranscribing" requirement directly at the data-model level, not just as a UI convenience.

Jobs are tracked per-artifact-per-segment (not per-video), so a 20-hour job that fails at hour 18 resumes from the last completed segment, not from zero — this is what checkpointing means concretely here.

---

## 7. GPU/VRAM strategy (RTX 3070 Ti, 8 GB)

Nothing stays resident across stages. Approximate footprints (all fit comfortably alone in 8 GB; the point is they are **never loaded concurrently**):

| Stage | Model | Approx. VRAM |
|---|---|---|
| Transcription | Qwen3-ASR-1.7B | <4 GB |
| Alignment | Qwen3-ForcedAligner-0.6B | ~1–2 GB |
| Diarization | pyannote community-1 | <2 GB |
| Translation (default) | Qwen2.5/3-7B-Instruct Q4_K_M | ~4.5–5 GB |
| Translation (opt-in, slower) | Qwen2.5/3-14B-Instruct Q4_K_M | ~8.7 GB — will not comfortably coexist with a real KV-cache/context window on an 8 GB card; treat as "may spill to CPU offload, expect slowdown," not a clean fit |

`model_manager.py` owns explicit load → use → `torch.cuda.empty_cache()`/unload boundaries between stages, and refuses to load a second GPU-resident model while one is active. This is a real constraint to design around, not a nice-to-have: the 14B translation tier in particular must be presented to the user as "will not fit cleanly," not silently attempted.

---

## 8. Implementation milestones

Each milestone has an independent, testable exit condition — no milestone starts until the previous one has one.

1. **M1 — Audio extraction + VAD (CLI)**: given a video file, produce a WAV and a list of speech segments with timestamps. Test: run on a known clip, manually verify segment boundaries against the audio.
2. **M2 — ASR + alignment (CLI)**: transcribe VAD segments with Qwen3-ASR, align with Qwen3-ForcedAligner, write RAW transcript JSON with word timestamps. Test: WER spot-check against a hand-transcribed 1-minute sample in each of the 4 source languages.
3. **M3 — Diarization (CLI, independent of M2)**: diarize the same VAD segments, output speaker turns. Test: manually verify speaker boundaries on a known 2-speaker clip.
4. **M4 — Merge**: assign speakers to transcript words by timestamp overlap, producing the speaker-labeled transcript. Test: re-run diarization with a different `expected_speakers` value and confirm the transcript re-merges without re-running M2.
5. **M5 — Context-aware translation (CLI)**: sliding-window prompt, glossary substitution, structured JSON output, mapped back to original timestamps. Test: translate a short multi-speaker exchange containing an honorific/omitted-subject case and manually assess whether context was used correctly.
6. **M6 — Subtitle export (CLI)**: write SRT from the merged + translated artifacts. Test: load the SRT in a real video player against the source clip.
7. **M7 — Benchmark harness**: run M1–M6 across ASR/diarization/translation model combinations on one fixed 5–10 minute clip, report timing/VRAM/output side-by-side. This is the gate before any UI work starts, per the brief.
8. **M8+ — Backend job system, API, UI, editor, packaging**: scoped in detail once M1–M7 validate the ML pipeline on your actual content.

---

## 9. Explicit limitations / non-claims

- Qwen3-ForcedAligner's 5-minute-per-call ceiling means alignment must be chunked; this is a real constraint, not an implementation detail to hand-wave.
- No diarization pipeline here is validated for a single 20-hour forward pass; windowed diarization with cluster-stitching is the planned mitigation, not a proven-at-scale guarantee until benchmarked on your content.
- Cross-video speaker voiceprint recognition is experimental and human-confirmed only — no evidence found that it's reliable enough for silent auto-labeling on noisy multi-speaker VOD audio.
- SenseVoice's weights carry a non-standard license (FunASR Model Open Source License Agreement, not MIT/Apache) despite MIT-licensed code — flagged for your review before it's enabled even as a benchmark option.
- TranslateGemma and Qwen-MT are excluded as the *primary* translator specifically because they don't support the multi-turn contextual prompting this project requires — not because they're low quality at what they're actually designed for (short-form/document MT).
- 14B-class local translation models are not a clean fit in 8 GB VRAM; presenting that tier as "opt-in, expect degraded performance" rather than a supported default.

---

## 10. Open questions for you

1. Default local translation model size — start at 7B for headroom, or accept the 14B/CPU-offload tradeoff from the start?
2. Should SenseVoice be included in the Phase 3 benchmark harness given its non-standard weight license, or excluded until you've reviewed the license text yourself?
3. Any preference between SQLite+FastAPI+React (recommended) vs. an alternative stack, before Phase 2 scaffolding begins?
