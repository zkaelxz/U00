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


def require_qwen3_packages(feature: str) -> None:
    """Raises DependencyUnavailableError when torch is missing or transformers
    is missing or older than qwen3_native.MIN_TRANSFORMERS (the message says
    what to do in Diagnostics), so a Qwen3 choice never silently degrades to
    plain Whisper."""
    if importlib.util.find_spec("torch") is None:
        raise DependencyUnavailableError(
            f"{feature} needs torch, which isn't installed yet. Open Diagnostics to install it.")
    problem = qwen3_native.transformers_problem(feature)
    if problem:
        raise DependencyUnavailableError(problem)


def import_failure_message(exc: ImportError) -> str:
    """What a run that hit `exc` while loading a Qwen3 model tells the user:
    qwen3_native's own sentence (it says what to do), else the fixed one."""
    if isinstance(exc, qwen3_native.TransformersUnavailableError):
        return str(exc)
    return MISSING_QWEN_MESSAGE
