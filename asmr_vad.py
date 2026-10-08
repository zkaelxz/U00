"""asmr_vad.py -- optional voice detector trained on Japanese ASMR.

Silero misses whispered and very soft speech, which is most of an ASMR track.
TransWithAI's Whisper-Vad-EncDec-ASMR (MIT, ~119 MB ONNX, CPU) scores every
20 ms frame of a 30 s window; this module turns those scores into speech spans
with separate start and stop thresholds, so a breathy word does not flicker the
span on and off. The result plugs into vad_segments.speech_spans(vad_fn=...).

It was trained on Japanese; nothing here has been measured on Chinese or Korean.
Everything optional fails soft: callers catch AsmrVadUnavailable and fall back
to Silero. The model is downloaded only when the user asks for it.
"""
import hashlib
import os
import time
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

import numpy as np

SAMPLE_RATE = 16000
WINDOW_S = 30
FRAME_S = 0.02
WINDOW_SAMPLES = SAMPLE_RATE * WINDOW_S
FRAMES_PER_WINDOW = 1500

# Hysteresis: a span starts at START and only ends once the score drops under
# STOP, so soft syllables inside a phrase do not cut it in two.
START_THRESHOLD = 0.5
STOP_THRESHOLD = 0.35

# Pinned to a commit so the checksum below stays valid.
MODEL_URL = ("https://huggingface.co/TransWithAI/Whisper-Vad-EncDec-ASMR-onnx/resolve/"
             "6ac29e2cbf2f4f8e9b639861766a8639dd666e9c/model.onnx")
MODEL_SHA256 = "cd47513515766d57f740e3094440dbbca9ab87e026b9cf21540d7ad588c0e047"
MODEL_BYTES = 119137398
MODEL_FILENAME = "model.onnx"

# The model file is served from a Hugging Face CDN host after a redirect.
_MAX_REDIRECTS = 5
_REQUEST_TIMEOUT = (15, 60)
_DEADLINE_S = 30 * 60
_CHUNK = 1 << 16


class AsmrVadUnavailable(Exception):
    """The ASMR detector can't run; the message is short and safe to show."""


def model_path() -> str:
    import portable
    return os.path.join(portable.data_dir(), "model_cache", "asmr_vad", MODEL_FILENAME)


def status() -> dict:
    import importlib.util
    return {"onnxruntime_installed": importlib.util.find_spec("onnxruntime") is not None,
            "model_downloaded": os.path.isfile(model_path())}


def hysteresis_spans(scores, start: float = START_THRESHOLD, stop: float = STOP_THRESHOLD,
                     frame_s: float = FRAME_S) -> list:
    """(start_s, end_s) runs where scores rise to `start` and stay at or above `stop`."""
    spans, begin = [], None
    for n, score in enumerate(scores):
        if begin is None:
            if score >= start:
                begin = n
        elif score < stop:
            spans.append((begin * frame_s, n * frame_s))
            begin = None
    if begin is not None:
        spans.append((begin * frame_s, len(scores) * frame_s))
    return spans


class AsmrVad:
    """Callable as vad_fn(audio, sr) -> [(start_s, end_s)] for 16 kHz mono audio."""

    def __init__(self, session, start: float = START_THRESHOLD, stop: float = STOP_THRESHOLD):
        if stop > start:
            raise ValueError("The stop threshold can't be above the start threshold.")
        self._session = session
        self._input = session.get_inputs()[0].name
        self.start, self.stop = start, stop
        self._extractor = None

    def _features(self, window):
        if self._extractor is None:
            # faster-whisper's extractor is the log-mel Whisper models expect.
            from faster_whisper.feature_extractor import FeatureExtractor
            self._extractor = FeatureExtractor(feature_size=80)
        # padding=0: the default adds a frame beyond the 3000 the model takes.
        return self._extractor(window, padding=0)[None].astype(np.float32)

    def scores(self, audio) -> np.ndarray:
        """Speech probability per 20 ms frame, one entry per frame of `audio`."""
        audio = np.asarray(audio, dtype=np.float32)
        out = []
        for first in range(0, audio.shape[0], WINDOW_SAMPLES):
            window = audio[first:first + WINDOW_SAMPLES]
            kept = int(np.ceil(window.shape[0] / (SAMPLE_RATE * FRAME_S)))
            if window.shape[0] < WINDOW_SAMPLES:
                window = np.pad(window, (0, WINDOW_SAMPLES - window.shape[0]))
            logits = self._session.run(None, {self._input: self._features(window)})[0][0]
            # The model's output is a logit per frame.
            out.append(1.0 / (1.0 + np.exp(-logits[:min(kept, FRAMES_PER_WINDOW)])))
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def __call__(self, audio, sr: int) -> list:
        if sr != SAMPLE_RATE:
            raise ValueError("The ASMR voice detector takes 16 kHz audio.")
        return hysteresis_spans(self.scores(audio), self.start, self.stop)


def load_detector() -> AsmrVad:
    """Raises AsmrVadUnavailable (never ImportError or OSError) when it can't run."""
    try:
        import onnxruntime
        import faster_whisper.feature_extractor  # noqa: F401 -- fails here, not mid-run
    except ImportError as exc:
        raise AsmrVadUnavailable(
            "The ASMR voice detector needs onnxruntime and faster-whisper installed.") from exc
    path = model_path()
    if not os.path.isfile(path):
        raise AsmrVadUnavailable("The ASMR voice detector model is not downloaded.")
    try:
        session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
        return AsmrVad(session)
    except Exception as exc:   # noqa: BLE001 -- a bad model file must not fail the job
        raise AsmrVadUnavailable("The ASMR voice detector model could not be loaded.") from exc


def vad_fn_or_fallback(on_notice: Optional[Callable[[str], None]] = None):
    """The ASMR detector, or None (meaning Silero) with a short notice why."""
    try:
        return load_detector()
    except AsmrVadUnavailable as exc:
        if on_notice:
            on_notice(f"{exc} Used the Standard detector instead.")
        return None


def _check_hop(url: str) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or parts.port not in (None, 443) or parts.username
            or not (host == "huggingface.co" or host.endswith(".hf.co"))):
        raise AsmrVadUnavailable("The model download was redirected somewhere unexpected.")


def download_model(progress_cb: Optional[Callable[[float], None]] = None,
                   cancel_check: Optional[Callable[[], None]] = None) -> None:
    """Fetches the model to model_path(), checking its size and SHA-256 before
    it is put in place. Raises AsmrVadUnavailable with a message free of URLs
    and paths. Only ever called from an explicit user action."""
    import requests
    path = model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    digest, got = hashlib.sha256(), 0
    deadline = time.monotonic() + _DEADLINE_S
    url = MODEL_URL
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            _check_hop(url)
            try:
                resp = requests.get(url, stream=True, timeout=_REQUEST_TIMEOUT,
                                    allow_redirects=False,
                                    headers={"User-Agent": "Baihe-Subtitler (voice detector)"})
            except requests.RequestException as exc:
                raise AsmrVadUnavailable("Could not reach the model download.") from exc
            try:
                if resp.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, resp.headers.get("Location") or "")
                    continue
                if resp.status_code != 200:
                    raise AsmrVadUnavailable(
                        f"The model download failed (HTTP {resp.status_code}).")
                with open(part, "wb") as out:
                    for chunk in resp.iter_content(_CHUNK):
                        got += len(chunk)
                        if got > MODEL_BYTES:
                            raise AsmrVadUnavailable("The model download is larger than expected.")
                        if time.monotonic() > deadline:
                            raise AsmrVadUnavailable("The model download took too long.")
                        if cancel_check:
                            cancel_check()
                        digest.update(chunk)
                        out.write(chunk)
                        if progress_cb:
                            progress_cb(got / MODEL_BYTES)
            except requests.RequestException as exc:
                raise AsmrVadUnavailable("The model download was interrupted.") from exc
            finally:
                resp.close()
            break
        else:
            raise AsmrVadUnavailable("The model download was redirected too many times.")
        if got != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
            raise AsmrVadUnavailable("The downloaded model did not match its checksum; "
                                     "nothing was installed.")
        os.replace(part, path)
    finally:
        if os.path.exists(part):
            os.unlink(part)


def coverage(audio_path: str, load_asmr=load_detector) -> dict:
    """Speech seconds, total seconds and their ratio for each detector, so the
    two can be compared on one clip. A detector that can't run is reported
    with an "unavailable" reason instead of numbers."""
    import vad_segments
    from asr_backend import load_audio_16k
    audio = load_audio_16k(audio_path)
    total = len(audio) / SAMPLE_RATE
    out = {"total_s": round(total, 2)}
    detectors = {"standard": lambda: None, "asmr": load_asmr}
    for name, make in detectors.items():
        try:
            spans = vad_segments.speech_spans(audio, SAMPLE_RATE, vad_fn=make())
        except (AsmrVadUnavailable, vad_segments.VadNotInstalledError) as exc:
            out[name] = {"unavailable": str(exc)}
            continue
        speech = sum(e - s for s, e in spans)
        out[name] = {"speech_s": round(speech, 2), "spans": len(spans),
                     "coverage": round(speech / total, 3) if total else 0.0}
    return out


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m asmr_vad", description="ASMR voice detector tools.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download", help="download the ~119 MB model (needs internet)")
    cov = sub.add_parser("coverage", help="print speech coverage for an audio file, "
                                          "Standard (Silero) vs ASMR")
    cov.add_argument("audio")
    args = parser.parse_args(argv)
    if args.command == "download":
        try:
            download_model(progress_cb=lambda f: print(f"\r{f * 100:3.0f}%", end="", flush=True))
        except AsmrVadUnavailable as exc:
            print(f"\n{exc}")
            return 1
        print("\nDownloaded.")
        return 0
    result = coverage(args.audio)
    print(f"Audio length: {result['total_s']} s")
    for name in ("standard", "asmr"):
        row = result[name]
        print(f"{name:9}", row["unavailable"] if "unavailable" in row else
              f"{row['speech_s']} s speech in {row['spans']} spans ({row['coverage'] * 100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
