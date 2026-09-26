"""
asr_backend.py -- pluggable transcription backends for the
"I don't have a transcript, let Whisper transcribe it" workflow in
tabs/workspace_tab.py (the _use_whisper_text branch).

Each backend's transcribe() returns the same shape core.transcribe_for_timing()
already produces: a list of {"start": float, "end": float, "text": str}
segments -- a drop-in replacement at that one call site.

WHY Qwen3ASRBackend REUSES WHISPER FOR SEGMENTATION: Qwen3-ASR has no VAD/
segmentation of its own in this project's usage -- Whisper's vad_filter is
the only thing that provides segment boundaries here. So Qwen3ASRBackend
takes Whisper's segments as input and re-transcribes each segment's audio
slice with Qwen3-ASR, REPLACING ONLY THE TEXT, not the timing.

This is deliberate, not a shortcut: it isolates the one thing actually in
question -- transcription accuracy, the open question from the original
Qwen3-ASR-vs-Whisper comparison, unverified specifically for Japanese --
from segmentation/timing, which Whisper still provides identically either
way. If Qwen3ASRBackend also changed segment boundaries, a quality
difference you saw in a side-by-side benchmark could come from either
change and you couldn't tell which one actually helped.

One consequence: whatever VAD-merge timing problems Whisper's segmentation
already has (see core.diagnose_line_coverage's long_lines check) carry over
unchanged into Qwen3ASRBackend's output too -- swapping the transcriber
doesn't fix mis-drawn segment boundaries, only what's written inside them.
If timing itself is the problem, that's forced_align.py's job, not this
module's -- and note forced_align.py needs a real reference transcript to
align against, which this whisper-text-only mode by definition doesn't have.

SETUP (not run inside this sandbox -- no GPU, no network; code is here to
run locally):
    pip install qwen-asr torch
Same Python 3.14/CUDA-wheel caveat as forced_align.py -- see that module's
docstring.
"""

import os
import tempfile

from core import (
    ModelDownloadError, _is_gpu_error, _is_network_error, diagnose_hostname,
    extract_audio_slice, transcribe_for_timing,
)
from forced_align import LANGUAGE_NAMES

# Not a documented Qwen3-ASR limit (the model card states no maximum
# duration for the base model) -- this is a defensive fallback for the
# same failure signature forced_align.py guards against with its own
# (aligner-documented) HARD_CAP_SECONDS: an abnormally long "segment" is
# almost always a VAD-merge artifact regardless of which model transcribes
# it, and re-transcribing several real minutes of merged dialogue as one
# blob would produce a similarly-merged, similarly-wrong result. Skipping
# it (falling back to Whisper's own text for just that one segment) is
# safer than guessing at an undocumented cap and failing the whole run.
SEGMENT_DURATION_WARNING_SECONDS = 300.0

_asr_model_cache = {}


class WhisperBackend:
    """Wraps the existing Whisper transcription path unchanged -- a pure
    refactor behind a common interface, not a behavior change. Existing
    callers of core.transcribe_for_timing() are unaffected; this exists
    so tabs/workspace_tab.py can pick a backend without an if/else on
    which model to call directly."""
    name = "whisper"

    def transcribe(self, audio_path, language, whisper_size="medium", use_gpu=False,
                    local_model_path=None, hf_token=None, initial_prompt="",
                    beam_size=5, min_silence_duration_ms=2000, on_gpu_fallback=None):
        return transcribe_for_timing(
            audio_path, whisper_size, language=language, use_gpu=use_gpu,
            local_model_path=local_model_path, hf_token=hf_token,
            initial_prompt=initial_prompt, beam_size=beam_size,
            min_silence_duration_ms=min_silence_duration_ms,
            on_gpu_fallback=on_gpu_fallback,
        )


def load_qwen3_asr(use_gpu: bool = False, model_size: str = "1.7B"):
    """Loads (and caches) the Qwen3-ASR model. Same GPU-fallback/network-
    error handling pattern as forced_align.load_qwen3_aligner() and
    core.load_whisper_model() -- see forced_align.py's docstring for why a
    transformers/torch model's CUDA failure surfaces here, at load time,
    rather than deferred to first inference like ctranslate2/faster-whisper.
    """
    cache_key = f"{model_size}_{'gpu' if use_gpu else 'cpu'}"
    if cache_key in _asr_model_cache:
        return _asr_model_cache[cache_key]

    import torch
    from qwen_asr import Qwen3ASRModel

    device = "cuda:0" if use_gpu else "cpu"
    model_id = f"Qwen/Qwen3-ASR-{model_size}"
    try:
        model = Qwen3ASRModel.from_pretrained(
            model_id, dtype=torch.bfloat16, device_map=device, max_new_tokens=256,
        )
    except Exception as exc:
        if use_gpu and _is_gpu_error(exc):
            model = Qwen3ASRModel.from_pretrained(
                model_id, dtype=torch.bfloat16, device_map="cpu", max_new_tokens=256,
            )
            cache_key = f"{model_size}_cpu"
        elif _is_network_error(exc):
            diag = diagnose_hostname("huggingface.co")
            if diag["status"] == "blocked":
                raise ModelDownloadError(
                    f"Couldn't download {model_id} -- huggingface.co is being blocked by "
                    f"a DNS blocker on your network.\n\n{diag['detail']}"
                ) from exc
            raise ModelDownloadError(
                f"Couldn't download the {model_id} model.\n\nThis is a network problem, not "
                "a problem with your audio. The model is fetched from Hugging Face the first "
                "time you use it."
            ) from exc
        else:
            raise

    _asr_model_cache[cache_key] = model
    return model


class Qwen3ASRBackend:
    name = "qwen3_asr"

    def __init__(self, model_size: str = "1.7B"):
        self.model_size = model_size

    def transcribe(self, audio_path, language, whisper_segments, use_gpu=False):
        """whisper_segments: the segmentation from WhisperBackend.transcribe()
        (or core.transcribe_for_timing() directly) -- see module docstring
        for why this backend needs Whisper's boundaries rather than
        producing its own."""
        if language not in LANGUAGE_NAMES:
            raise ValueError(
                f"Qwen3-ASR doesn't cover language={language!r} in this project's usage "
                f"(supported: {sorted(LANGUAGE_NAMES)}) -- use WhisperBackend instead."
            )
        if not whisper_segments:
            raise ValueError(
                "Qwen3ASRBackend re-transcribes Whisper's VAD segments (see module docstring) "
                "-- run WhisperBackend.transcribe() first to get segment boundaries."
            )

        model = load_qwen3_asr(use_gpu=use_gpu, model_size=self.model_size)
        language_name = LANGUAGE_NAMES[language]
        out = []
        with tempfile.TemporaryDirectory(prefix="baihe_qwen3_asr_") as tmp_dir:
            for i, seg in enumerate(whisper_segments):
                if seg["end"] - seg["start"] > SEGMENT_DURATION_WARNING_SECONDS:
                    # See SEGMENT_DURATION_WARNING_SECONDS -- almost certainly a
                    # VAD-merge artifact; keep Whisper's own text for just this
                    # one segment rather than failing (or mis-transcribing) the
                    # whole run over it.
                    out.append(seg)
                    continue
                slice_path = os.path.join(tmp_dir, f"seg_{i}.wav")
                extract_audio_slice(audio_path, seg["start"], seg["end"], slice_path)
                try:
                    results = model.transcribe(audio=slice_path, language=language_name)
                    text = results[0].text if results else ""
                finally:
                    if os.path.exists(slice_path):
                        os.unlink(slice_path)
                out.append({"start": seg["start"], "end": seg["end"], "text": text})
        return out
