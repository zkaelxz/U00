"""
qwen3_requirements_service.py -- what a Qwen3 choice (Qwen3-ASR, speech
detection, forced alignment) needs installed, as plain sentences.
transcribe_service re-exports these names, which other services, the CLI and
tests call through it.
"""

import importlib.util

import qwen3_native
from services.service_errors import DependencyUnavailableError

# A fixed sentence, never the ImportError text: that names a module and reads
# as a crash. The Diagnostics page is where the install and update buttons are.
MISSING_QWEN_MESSAGE = "Qwen3 speech recognition isn't installed yet. Open Diagnostics to install it."


def require_qwen3_packages(feature: str, aligner_language: str = None) -> None:
    """Raises DependencyUnavailableError when torch is missing or transformers
    is missing or older than qwen3_native.MIN_TRANSFORMERS (the message says
    what to do in Diagnostics), so a Qwen3 choice never silently degrades to
    plain Whisper. Pass the title's language code when the run will use the
    forced aligner: Japanese and Korean also need nagisa / soynlp, and
    finding that out only after the whole recognition pass wastes it."""
    if importlib.util.find_spec("torch") is None:
        raise DependencyUnavailableError(
            f"{feature} needs torch, which isn't installed yet. Open Diagnostics to install it.")
    # qwen3_native.load_audio_16k reads audio with soundfile, which the old
    # qwen-asr package used to bring in.
    if importlib.util.find_spec("soundfile") is None:
        raise DependencyUnavailableError(
            f"{feature} needs soundfile, which isn't installed yet. Open Diagnostics to install it.")
    problem = qwen3_native.transformers_problem(feature)
    if problem:
        raise DependencyUnavailableError(problem)
    if aligner_language:
        problem = qwen3_native.aligner_language_problem(aligner_language)
        if problem:
            raise DependencyUnavailableError(problem)


def require_vad_backend_packages(backend: str, language: str) -> None:
    """require_qwen3_packages for a speech-detection backend. The long-window
    backend always runs the forced aligner; the short-span one only with the
    timing-refinement option on."""
    from services import asr_options_service
    aligns = backend == "qwen3_asr_long" or asr_options_service.get_vad_refine_timing()
    require_qwen3_packages("Qwen3-ASR", language if aligns else None)


def import_failure_message(exc: Exception) -> str:
    """What a run that hit `exc` while loading a Qwen3 model tells the user:
    a start-time check's own sentence, qwen3_native's own sentence (both say
    what to do), else the fixed one."""
    if isinstance(exc, DependencyUnavailableError):
        return exc.message
    if isinstance(exc, qwen3_native.TransformersUnavailableError):
        return str(exc)
    return MISSING_QWEN_MESSAGE

