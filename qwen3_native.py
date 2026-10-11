"""
qwen3_native.py -- Qwen3-ASR and Qwen3-ForcedAligner through transformers' own
classes (the `-hf` checkpoints), instead of the `qwen-asr` package.

asr_backend.load_qwen3_asr and forced_align.load_qwen3_aligner own caching,
the GPU -> CPU fallback and download-error handling; this module only builds
the models and gives them the call shapes those two modules already use:

    model.transcribe(audio=path | [paths], language=name | None, prompt=None)
        -> [result with .text and .language]
    model.align(audio=path, text=str, language=name)
        -> [[unit with .text, .start_time, .end_time]]

Needs transformers >= MIN_TRANSFORMERS: 5.13 and 5.14 ship the classes, but
force the language through the system prompt and have no `prompt=`
(vocabulary hint); 5.15 prefills "language <NAME><asr_text>" as the model was
trained and adds `prompt=`.
"""

import importlib.metadata
import importlib.util
from dataclasses import dataclass
from typing import Optional

MIN_TRANSFORMERS = (5, 15, 0)

ASR_REPO_TEMPLATE = "Qwen/Qwen3-ASR-{size}-hf"
ALIGNER_REPO = "Qwen/Qwen3-ForcedAligner-0.6B-hf"

# Approximate first-use downloads, shown in Diagnostics: the -hf repos are new
# files on the Hub, so weights cached for the old Qwen/Qwen3-ASR-* repos are
# not reused.
APPROX_DOWNLOAD_GB = {"1.7B": 4.1, "0.6B": 1.6, "aligner": 1.8}

# One subtitle segment is a few seconds of speech; this matches the limit the
# qwen-asr package applied.
MAX_NEW_TOKENS = 256

SAMPLE_RATE = 16000

# Forced alignment tokenises these two languages with their own packages.
ALIGNER_LANGUAGE_PACKAGES = {"Japanese": "nagisa", "Korean": "soynlp"}
ALIGNER_LANGUAGE_NAMES = {"ja": "Japanese", "ko": "Korean", "zh": "Chinese"}


def aligner_language_problem(language: Optional[str]) -> Optional[str]:
    """None, or a plain sentence when the forced aligner needs a tokeniser
    package for `language` (a name like "Japanese" or a code like "ja")
    that isn't installed."""
    name = ALIGNER_LANGUAGE_NAMES.get(language, language)
    package = ALIGNER_LANGUAGE_PACKAGES.get(name)
    if package and importlib.util.find_spec(package) is None:
        return (f"{name} forced alignment needs the {package} package, which isn't "
                "installed; install it in Diagnostics.")
    return None


class TransformersUnavailableError(ImportError):
    """transformers (or a package its Qwen3 tokenisation needs) is missing or
    too old. An ImportError so existing `except ImportError` handlers report
    it as a missing dependency; str() is already a plain sentence."""


@dataclass
class AsrResult:
    text: str
    language: Optional[str]


@dataclass
class AlignedUnit:
    text: str
    start_time: float
    end_time: float


def installed_transformers_version() -> Optional[str]:
    try:
        return importlib.metadata.version("transformers")
    except importlib.metadata.PackageNotFoundError:
        return None


def installed_qwen_asr_version() -> Optional[str]:
    """The old qwen-asr package's version, or None. It pins transformers to
    4.57.6, so while it stays installed pip holds transformers below what the
    native classes need."""
    try:
        return importlib.metadata.version("qwen-asr")
    except importlib.metadata.PackageNotFoundError:
        return None


QWEN_ASR_REMOVAL_STEP = ("run `pip uninstall qwen-asr` in the app's Python, then update "
                         "transformers in Diagnostics")


def _older_than_minimum(version: str) -> bool:
    # Real version ordering: "5.15.0rc1" sorts before 5.15.0, which splitting
    # on dots and reading digits would call 5.15.
    import diagnostics  # lazy: it imports a lot, and this module loads on every transcription path
    version_mod = diagnostics._packaging()[0]
    try:
        return version_mod.Version(version) < version_mod.Version(
            ".".join(str(n) for n in MIN_TRANSFORMERS))
    except version_mod.InvalidVersion:
        return True


def transformers_problem(feature: str = "Qwen3-ASR") -> Optional[str]:
    """None when the installed transformers can run the Qwen3 models, else a
    plain sentence saying what to do."""
    need = ".".join(str(n) for n in MIN_TRANSFORMERS[:2])
    version = installed_transformers_version()
    if version is None:
        return (f"{feature} needs transformers {need} or newer, which isn't installed; "
                "install it in Diagnostics.")
    if _older_than_minimum(version):
        if installed_qwen_asr_version():
            # Diagnostics can't move transformers while qwen-asr's exact pin
            # is installed, so "update it" alone would be a dead end.
            return (f"{feature} needs transformers {need} or newer (this is {version}), and "
                    f"the old qwen-asr package holds it back: {QWEN_ASR_REMOVAL_STEP}.")
        return (f"{feature} needs transformers {need} or newer (this is {version}); "
                "update it in Diagnostics.")
    return None


def require_transformers(feature: str = "Qwen3-ASR") -> None:
    problem = transformers_problem(feature)
    if problem:
        raise TransformersUnavailableError(problem)


def asr_repo_id(model_size: str) -> str:
    return ASR_REPO_TEMPLATE.format(size=model_size)


def pick_dtype(torch, use_gpu: bool):
    """bfloat16, as before; on a GPU without bfloat16 support, float16."""
    if use_gpu and not torch.cuda.is_bf16_supported():
        return torch.float16
    return torch.bfloat16


def load_audio_16k(path: str):
    """A wav file as a 16 kHz mono float32 array. core.extract_audio_slice
    already writes 16 kHz mono; the resample is only a guard."""
    import numpy as np
    import soundfile as sf
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    if rate != SAMPLE_RATE and len(data):
        target = max(int(round(len(data) * SAMPLE_RATE / rate)), 1)
        data = np.interp(np.linspace(0, len(data) - 1, target), np.arange(len(data)),
                         data).astype("float32")
    return data


def _audio_list(audio) -> list:
    paths = [audio] if isinstance(audio, str) else list(audio)
    return [load_audio_16k(p) for p in paths]


def _prepare(inputs, processor, model):
    for name in getattr(processor, "unused_input_names", None) or []:
        inputs.pop(name, None)
    return inputs.to(model.device, dtype=model.dtype)


class NativeQwen3ASR:
    def __init__(self, model, processor, max_new_tokens: int = MAX_NEW_TOKENS):
        self.model = model
        self.processor = processor
        self.max_new_tokens = max_new_tokens

    @classmethod
    def from_pretrained(cls, model_id: str, device: str, dtype, max_new_tokens=MAX_NEW_TOKENS):
        require_transformers("Qwen3-ASR")
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        processor = AutoProcessor.from_pretrained(model_id)
        # No device_map: it needs accelerate, which is optional here, and a
        # failed move to the GPU surfaces from .to() inside the caller's
        # fallback handling just the same.
        model = AutoModelForMultimodalLM.from_pretrained(model_id, dtype=dtype)
        model = model.to(device).eval()
        return cls(model, processor, max_new_tokens)

    def transcribe(self, audio, language=None, prompt: Optional[str] = None) -> list:
        """One AsrResult per input, in input order. `language` is a name like
        "Chinese" (forced) or None (the model detects it, and the result's
        .language says what it heard). `prompt` is the optional "Vocabulary: ..."
        hint; left out of the request entirely when empty."""
        import torch
        arrays = _audio_list(audio)
        kwargs = {"prompt": prompt} if prompt else {}
        inputs = self.processor.apply_transcription_request(arrays, language=language, **kwargs)
        inputs = _prepare(inputs, self.processor, self.model)
        with torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens)
        generated = generated[:, inputs["input_ids"].shape[1]:]
        parsed = self.processor.decode(generated, return_format="parsed")
        if isinstance(parsed, dict):
            parsed = [parsed]
        if len(parsed) != len(arrays):
            raise RuntimeError(
                f"Qwen3-ASR returned {len(parsed)} results for {len(arrays)} clips.")
        # A forced language is in the prompt, not the output, so it isn't parsed back.
        return [AsrResult(text=p.get("transcription") or "",
                          language=p.get("language") or language) for p in parsed]


class NativeQwen3Aligner:
    def __init__(self, model, processor):
        self.model = model
        self.processor = processor

    @classmethod
    def from_pretrained(cls, model_id: str, device: str, dtype):
        require_transformers("Qwen3 forced alignment")
        from transformers import AutoModelForTokenClassification, AutoProcessor
        processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModelForTokenClassification.from_pretrained(model_id, dtype=dtype)
        model = model.to(device).eval()
        return cls(model, processor)

    def align(self, audio, text, language=None) -> list:
        """One list of AlignedUnit per input (words, or characters for CJK)."""
        import torch
        problem = aligner_language_problem(language)
        if problem:
            raise TransformersUnavailableError(problem)
        arrays = _audio_list(audio)
        texts = [text] if isinstance(text, str) else list(text)
        inputs, word_lists = self.processor.prepare_forced_aligner_inputs(
            arrays, texts, language=language)
        inputs = _prepare(inputs, self.processor, self.model)
        with torch.inference_mode():
            logits = self.model(**inputs).logits
        decoded = self.processor.decode_forced_alignment(
            logits, inputs["input_ids"], word_lists,
            timestamp_token_id=self.model.config.timestamp_token_id)
        return [[AlignedUnit(text=item["text"], start_time=float(item["start_time"]),
                             end_time=float(item["end_time"])) for item in items]
                for items in decoded]
