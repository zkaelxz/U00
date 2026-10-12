"""
services/setup_readiness_service.py -- what is installed and configured, as
booleans, so the UI can say why a control is off and where to fix it.

The facts come from the checks Diagnostics already makes (importability from
package metadata, `which`, one nvidia-smi query, one Ollama /api/tags probe).
Nothing here imports torch, spawns the CUDA probe or reads a version string:
the answer is only ever true or false, so it carries no path, URL or key and a
household member may read it.
"""

import shutil
import sys
import threading
import time

import diagnostics
import diagnostics_torch
import qwen3_native
import translate_engines
from services import settings_service

# Long enough that a page of controls asking at once costs one nvidia-smi and one
# Ollama probe, short enough that an install shows up on the next look.
CACHE_SECONDS = 5.0

_lock = threading.Lock()
_cached = None  # (taken at, answer)

# flag -> the import names that must all be importable.
_PACKAGES = {
    "faster_whisper": ("faster_whisper",),
    "torch": ("torch",),
    "torchaudio": ("torchaudio",),
    "uroman": ("uroman",),
    "soundfile": ("soundfile",),
    "audio_separator": ("audio_separator",),
    "demucs": ("demucs",),
    "pyannote": ("pyannote.audio",),
    "onnxruntime": ("onnxruntime",),
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "yt_dlp": ("yt_dlp",),
    "pypdf": ("pypdf",),
    "genanki": ("genanki",),
    "ebooklib": ("ebooklib",),
    "cv2": ("cv2",),
    "jieba": ("jieba",),
    "sudachi": ("sudachipy", "sudachidict_core"),
    "kiwi": ("kiwipiepy",),
    "nagisa": ("nagisa",),
    "soynlp": ("soynlp",),
    "pytesseract": ("pytesseract",),
    # paddleocr 3.x does not pull in the paddlepaddle wheel (module `paddle`), so the
    # first alone would report an OCR backend that fails on first use.
    "paddle_ocr": ("paddleocr", "paddle"),
}
_KEYS = ("claude", "deepseek", "gemini", "openai", "groq")


def clear_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def _safe(check, default=False) -> bool:
    """A check that fails reads as not ready: a broken probe must not break the page."""
    try:
        return bool(check())
    except Exception:
        return default


def _torch_cuda() -> bool:
    """Whether the installed torch is a CUDA build, from its package metadata (no import).
    A wheel with no build tag is PyPI's: CUDA on Linux, CPU-only on Windows and macOS."""
    build = diagnostics_torch.torch_family_versions()["torch"]
    if not build["version"]:
        return False
    if build["build"] is None:
        return sys.platform.startswith("linux")
    return build["build"] == "cuda"


def _tesseract_program_found() -> bool:
    return shutil.which(settings_service.get("tesseract_cmd") or "tesseract") is not None


def _ollama_state() -> tuple:
    """(server answers, the default model is pulled); the model is only asked after the server answers."""
    url = settings_service.resolve_key("ollama_url") or "http://localhost:11434"
    if not translate_engines.check_ollama_reachable(url):
        return False, False
    try:
        translate_engines.check_ollama_model_installed(url, translate_engines.effective_default_model("ollama"))
    except Exception:
        return True, False
    return True, True


def _compute() -> dict:
    out = {flag: all(_safe(lambda n=n: diagnostics.check_dependency(n)) for n in names)
           for flag, names in _PACKAGES.items()}
    out["transformers_ok"] = _safe(lambda: qwen3_native.transformers_problem("Qwen3") is None)
    out["qwen3_asr"] = out["torch"] and out["soundfile"] and out["transformers_ok"]
    try:
        ffmpeg = diagnostics.check_ffmpeg()
    except Exception:
        ffmpeg = {}
    out["ffmpeg"] = bool(ffmpeg.get("found"))
    # libass is reported as unknown (None) when ffmpeg's flags could not be read: not ready.
    out["ffmpeg_libass"] = out["ffmpeg"] and ffmpeg.get("libass") is True
    out["js_runtime"] = _safe(lambda: diagnostics.check_js_runtime().get("found"))
    out["tesseract_program"] = _safe(_tesseract_program_found)
    out["gpu_found"] = _safe(lambda: diagnostics_torch.nvidia_driver_info() is not None)
    out["torch_cuda"] = _safe(_torch_cuda)
    out["hf_token_configured"] = _safe(lambda: settings_service.resolve_key("hf_token"))
    for engine in _KEYS:
        out[f"key_{engine}"] = _safe(lambda e=engine: settings_service.resolve_key(e))
    try:
        out["ollama_reachable"], out["ollama_model_pulled"] = _ollama_state()
    except Exception:
        out["ollama_reachable"] = out["ollama_model_pulled"] = False
    return out


def get_readiness() -> dict:
    """{flag: bool}, cached CACHE_SECONDS. The lock is held while computing so a burst of
    callers shares one set of probes."""
    global _cached
    with _lock:
        now = time.monotonic()
        if _cached and now - _cached[0] < CACHE_SECONDS:
            return dict(_cached[1])
        answer = _compute()
        _cached = (now, answer)
        return dict(answer)
