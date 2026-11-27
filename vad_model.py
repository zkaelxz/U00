"""Owns the pinned Silero VAD v6.2 weights file and swaps it into faster-whisper's voice detector."""
import hashlib
import logging
import os

from lib import ort_telemetry

log = logging.getLogger(__name__)

PINNED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "vad", "silero_vad_v6.2.onnx")
PINNED_SHA256 = "914fd98ac0a73d69ba1e70c9b1d66acb740eff90500dfde08b89a961b168a6a9"

_done = False


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def use_pinned_model() -> bool:
    """True once faster-whisper's detector runs on the pinned v6.2 file in this process.

    faster-whisper 1.2.1 ships the older v6 weights; the v6.2 file is the only thing that
    changed in the master build that scored better on long, noisy drama audio. Never
    raises: a missing or altered file, or a faster-whisper without this hook, keeps the
    stock detector, because a worse detector is better than a failed transcription.
    """
    global _done
    if _done:
        return True
    try:
        from faster_whisper import vad
        stock = getattr(vad, "get_vad_model", None)
        model_class = getattr(vad, "SileroVADModel", None)
        if stock is None or model_class is None:
            return False
        if _sha256(PINNED_PATH) != PINNED_SHA256:
            log.warning("The pinned voice-detector file doesn't match its hash; using faster-whisper's own.")
            return False
        stock_path = os.path.join(os.path.dirname(vad.__file__), "assets", "silero_vad_v6.onnx")
        if os.path.isfile(stock_path) and _sha256(stock_path) == PINNED_SHA256:
            _done = True   # a faster-whisper release that already ships v6.2: nothing to swap
            return True

        import functools

        @functools.lru_cache
        def pinned_model():
            return model_class(PINNED_PATH)

        vad.get_vad_model = pinned_model
    except Exception as exc:   # noqa: BLE001 -- see the docstring
        log.warning("Couldn't switch to the pinned voice-detector file: %s", exc)
        return False
    _done = True
    return True


def prepare() -> bool:
    """Every process that runs faster-whisper's detector calls this first: telemetry off, pinned file in."""
    ort_telemetry.disable()
    return use_pinned_model()
