"""Owns the pinned Silero VAD v6.2 weights and the gate that adds the speech only it hears, far from any other speech."""
import contextlib
import hashlib
import logging
import os
import threading

log = logging.getLogger(__name__)

PINNED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "vad", "silero_vad_v6.2.onnx")
PINNED_SHA256 = "914fd98ac0a73d69ba1e70c9b1d66acb740eff90500dfde08b89a961b168a6a9"

# v6.2 used on its own hears laughter, fillers and noise beside the speech the installed
# detector already keeps, and that cost more than it gained. Its stretches that stand alone,
# away from any installed stretch, were the only extras that helped on every clip measured.
STANDALONE_GAP_S = 1.5
MIN_EXTRA_S = 0.5

_lock = threading.Lock()
_local = threading.local()
_installed = False
_pinned = None


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pinned_model(vad):
    """The v6.2 detector, loaded once; None when the file is missing or altered."""
    global _pinned
    if _pinned is None:
        if not os.path.isfile(PINNED_PATH) or _sha256(PINNED_PATH) != PINNED_SHA256:
            log.warning("The pinned voice-detector file is missing or doesn't match its hash; "
                        "using faster-whisper's own detector alone.")
            return None
        _pinned = vad.SileroVADModel(PINNED_PATH)
    return _pinned


def standalone_extras(installed: list, pinned: list, sampling_rate: int) -> list:
    """`installed` plus each `pinned` stretch with no installed stretch within STANDALONE_GAP_S.

    A pinned stretch that overlaps or sits next to installed speech only widens it, which is
    where the laughter and noise were; one on its own is usually soft speech the installed
    detector missed."""
    gap, shortest = STANDALONE_GAP_S * sampling_rate, MIN_EXTRA_S * sampling_rate
    kept = [p for p in pinned if p["end"] - p["start"] >= shortest
            and all(p["start"] - i["end"] >= gap or i["start"] - p["end"] >= gap for i in installed)]
    return sorted([*installed, *kept], key=lambda s: s["start"])


def _install(vad, transcribe_module) -> None:
    """Routes faster-whisper's detector lookup and its transcribe's speech timestamps through
    this thread's choice, so a gated run never changes what another thread's run hears."""
    global _installed
    with _lock:
        if _installed:
            return
        stock_model, stock_timestamps = vad.get_vad_model, transcribe_module.get_speech_timestamps

        def get_vad_model():
            chosen = getattr(_local, "model", None)
            return stock_model() if chosen is None else chosen

        def get_speech_timestamps(audio, vad_options=None, sampling_rate=16000, **kwargs):
            installed = stock_timestamps(audio, vad_options, sampling_rate, **kwargs)
            pinned_model = getattr(_local, "gate", None)
            if pinned_model is None:
                return installed
            _local.model = pinned_model
            try:
                pinned = stock_timestamps(audio, vad_options, sampling_rate, **kwargs)
            finally:
                _local.model = None
            return standalone_extras(installed, pinned, sampling_rate)

        vad.get_vad_model = get_vad_model
        transcribe_module.get_speech_timestamps = get_speech_timestamps
        _installed = True


@contextlib.contextmanager
def gated():
    """Inside, a faster-whisper transcribe on this thread with its detector on also keeps the
    stand-alone stretches the v6.2 detector hears. Yields False, and leaves the detector as it
    is, when the pinned file or faster-whisper's hooks are unavailable: a run without the gate
    is better than a failed one."""
    try:
        from faster_whisper import transcribe as transcribe_module
        from faster_whisper import vad
        model = _pinned_model(vad)
        if model is not None:
            _install(vad, transcribe_module)
    except Exception as exc:   # noqa: BLE001 -- see the docstring
        log.warning("Couldn't add the v6.2 voice detector's stand-alone speech: %s", exc)
        model = None
    _local.gate = model
    try:
        yield model is not None
    finally:
        _local.gate = None
