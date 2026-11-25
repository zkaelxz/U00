"""text_only_whisper.py -- runs Whisper fine-tunes that write no timestamps (Thonburian, Thai).

They run through transformers behind the interface faster-whisper's WhisperModel has.

Thonburian (biodatlab/whisper-th-large-v3-combined) was trained without timestamp tokens.
On the owner's Thai stream test (V100, 2026-11-25) it read best as plain text through
transformers (CER 0.204, against 0.263 for large-v3), but asked for timestamps it wrote
one chunk with no times for ten minutes, and its CTranslate2 build lost about a quarter
of the speech and looped. So the speech detector finds where speech is, each span (at most
SPAN_CAP_S) is decoded as text only, and the span's own start and end become the line's.
The lines carry no word times: a long one is cut later by length, with estimated times.

load() returns a TextOnlyWhisper whose transcribe() and detect_language() take the calls
whisper_transcribe, Live and mixed_language already make, so a model key from
whisper_models.TEXT_ONLY_REPOS works wherever a faster-whisper size does.
"""

import os
from typing import NamedTuple, Optional

SAMPLE_RATE = 16000
# Silero spans capped like the Qwen3 speech-detection backend: Whisper hears 30 s at once,
# and a shorter span keeps a subtitle line from being one long guess at its timing.
SPAN_CAP_S = 15.0
SPAN_PAD_MS = 200
# With the detector off, back-to-back windows of Whisper's own length.
WINDOW_S = 30.0
# Spans decoded together. Batch 8 peaked at 15 GB of VRAM on the V100 through the pipeline;
# 2 keeps the model near large-v3's footprint so it can run beside Live.
BATCH_SIZE = 2
# A 15 s span holds well under this many tokens of Thai; a decode that reaches it is looping.
MAX_NEW_TOKENS = 220
# Files the model loads, all inert data. The repo also holds training logs and
# training_args.bin (a pickle), which are never fetched.
REQUIRED_FILES = ("config.json", "generation_config.json", "model.safetensors", "preprocessor_config.json",
                  "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
                  "added_tokens.json", "special_tokens_map.json", "normalizer.json")


class Segment(NamedTuple):
    start: float
    end: float
    text: str
    words: Optional[list] = None


class Info(NamedTuple):
    duration: float
    language: str
    language_probability: float = 1.0


def snapshot_folder(hub_dir: str, repo: str, revision: str) -> Optional[str]:
    """The download at exactly `revision`, or None while any required file is missing."""
    folder = os.path.join(hub_dir, "models--" + repo.replace("/", "--"), "snapshots", revision)
    return folder if all(os.path.isfile(os.path.join(folder, n)) for n in REQUIRED_FILES) else None


def fetch(repo: str, revision: str, token=None) -> str:
    import huggingface_hub
    return huggingface_hub.snapshot_download(repo, revision=revision, token=token,
                                             allow_patterns=list(REQUIRED_FILES), etag_timeout=30)


def _audio_array(audio):
    import numpy as np
    if isinstance(audio, (str, os.PathLike)):
        from faster_whisper import decode_audio
        return decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
    return np.asarray(audio, dtype="float32")


def speech_windows(audio, vad_filter: bool = True, vad_parameters=None, clip_timestamps=None) -> list:
    """(start_s, end_s) pairs to decode: the clip_timestamps pairs when given, else Silero's
    speech spans capped at SPAN_CAP_S, else fixed WINDOW_S windows over the whole audio."""
    import vad_segments
    duration = len(audio) / SAMPLE_RATE
    if clip_timestamps:
        times = list(clip_timestamps)
        return [(max(0.0, float(s)), min(duration, float(e))) for s, e in zip(times[::2], times[1::2])
                if float(e) > float(s)]
    if not vad_filter:
        return [tuple(s) for s in vad_segments.fixed_spans(duration, WINDOW_S)]
    params = vad_parameters or {}
    spans = vad_segments.speech_spans(
        audio, SAMPLE_RATE, threshold=float(params.get("threshold", vad_segments.THRESHOLD)),
        min_silence_ms=int(params.get("min_silence_duration_ms", vad_segments.MIN_SILENCE_MS)),
        pad_ms=SPAN_PAD_MS)
    return [tuple(s) for s in vad_segments.cap_spans(spans, audio, SAMPLE_RATE, max_s=SPAN_CAP_S)]


class TextOnlyWhisper:
    def __init__(self, model, processor, device: str, dtype, language: str):
        self.model, self.processor, self.device, self.dtype = model, processor, device, dtype
        self.language = language

    def detect_language(self, audio=None, **_kwargs):
        """The model's own language: a Thai fine-tune hears everything as Thai, so the
        app's wrong-language check and per-span detection see the title's language."""
        return self.language, 1.0, [(self.language, 1.0)]

    def _decode(self, waves: list) -> list:
        import torch
        features = self.processor.feature_extractor(
            waves, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features
        features = features.to(self.device, dtype=self.dtype)
        with torch.inference_mode():
            ids = self.model.generate(features, language=self.language, task="transcribe",
                                      return_timestamps=False, max_new_tokens=MAX_NEW_TOKENS)
        return [t.strip() for t in self.processor.batch_decode(ids, skip_special_tokens=True)]

    def transcribe(self, audio, language=None, vad_filter: bool = True, vad_parameters=None,
                   clip_timestamps=None, **_ignored):
        """(segments, info) as faster-whisper returns them; segments is a generator, one
        batch decoded per step. Beam size, prompts and decoding thresholds are not used:
        the test that chose this model decoded greedily with none of them."""
        wave = _audio_array(audio)
        windows = speech_windows(wave, vad_filter, vad_parameters, clip_timestamps)
        info = Info(duration=len(wave) / SAMPLE_RATE, language=self.language)

        def segments():
            for i in range(0, len(windows), BATCH_SIZE):
                batch = windows[i:i + BATCH_SIZE]
                texts = self._decode([wave[int(s * SAMPLE_RATE):int(e * SAMPLE_RATE)] for s, e in batch])
                for (start, end), text in zip(batch, texts):
                    if text:
                        yield Segment(start, end, text)

        return segments(), info


def load(folder: str, use_gpu: bool, language: str) -> tuple:
    """(TextOnlyWhisper, device info dict as whisper_models records it). float16 on CUDA;
    float32 on the CPU, where float16 is not supported for every operation."""
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    processor = WhisperProcessor.from_pretrained(folder, local_files_only=True)
    gpu_error = None
    if use_gpu:
        try:
            if not torch.cuda.is_available():
                raise RuntimeError("torch does not see a CUDA device")
            model = WhisperForConditionalGeneration.from_pretrained(
                folder, dtype=torch.float16, local_files_only=True).to("cuda").eval()
            return (TextOnlyWhisper(model, processor, "cuda", torch.float16, language),
                    {"device": "cuda", "compute_type": "float16", "gpu_error": None})
        except Exception as exc:
            from whisper_models import short_reason
            gpu_error = short_reason(exc)
    model = WhisperForConditionalGeneration.from_pretrained(
        folder, dtype=torch.float32, local_files_only=True).eval()
    return (TextOnlyWhisper(model, processor, "cpu", torch.float32, language),
            {"device": "cpu", "compute_type": "float32", "gpu_error": gpu_error})
