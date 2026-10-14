"""One-run hardware self-test: `python -m tools.self_test [--out FILE] [--model NAME]`.

Prints PASS / FAIL / SKIPPED with a one-line reason per check and writes the
same text to a report the owner can paste to the assistant. The report holds
no key values, filesystem paths or URLs. SKIPPED means the hardware or an
optional component is absent, which is not a fault; only FAIL fails the run
(exit code 1). Reuses the app's own probes; it adds no engine code.
"""
import argparse
import re
import datetime
import os
import platform
import shutil
import sys
import tempfile
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/self_test.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, SKIPPED = "PASS", "FAIL", "SKIPPED"
REPORT_NAME = "self-test-report.txt"
REPORT_TITLE = "Baihe self-test report"
# Smallest multilingual model: this is a "does the GPU path work" probe, not a quality test.
DEFAULT_MODEL = "tiny"
OLLAMA_DEFAULT_URL = "http://localhost:11434"
OLLAMA_TIMEOUT_SECONDS = 3
MAX_REASON_CHARS = 200

_URL = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_SHORTENED_PATH = re.compile(r"\.\.\./\S+")
_KEY_LABELS = {"claude": "Claude", "deepseek": "DeepSeek", "gemini": "Gemini", "openai": "OpenAI",
               "groq": "Groq", "hf_token": "Hugging Face token", "ollama_url": "Ollama address"}


def _strip_locations(text: str) -> str:
    # redact_for_support keeps a URL's host and a path's last segment, which is
    # more than a report for pasting should show.
    import diagnostics_report
    text = _URL.sub("[URL]", text)
    text = diagnostics_report.redact_for_support(text)
    return _SHORTENED_PATH.sub("[path]", text)


def _clean(text) -> str:
    """One line with secrets, URLs, paths and the OS user name removed."""
    text = " ".join(_strip_locations(str(text or "")).split())
    return text if len(text) <= MAX_REASON_CHARS else text[:MAX_REASON_CHARS - 1] + "…"


def _write_silence_wav(path: str, seconds: float = 1.0, rate: int = 16000) -> None:
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(seconds * rate))


def _local_model_folder(model: str):
    """The model's folder if it is fully downloaded at its pinned revision, else
    None. Looked up on disk only, so the check never downloads."""
    import whisper_models
    return whisper_models._generic_snapshot(model)


def check_gpu_whisper(model: str = DEFAULT_MODEL):
    import diagnostics
    import ollama_unload
    import whisper_models
    if not diagnostics.check_dependency("faster_whisper"):
        return SKIPPED, "faster-whisper is not installed"
    cuda = whisper_models.gpu_status().get("ctranslate2_cuda_devices")
    if not cuda:
        return SKIPPED, "no CUDA GPU visible to faster-whisper"
    folder = _local_model_folder(model)
    if folder is None:
        return SKIPPED, f"the '{model}' model is not fully downloaded (no download is attempted; use --model)"
    try:
        # A scope without a local Ollama keeps the loader from unloading the
        # owner's Ollama models and from reading app settings (which would
        # create the library folder and database).
        with ollama_unload.job_scope(ollama_unload.JobScope(local_ollama=False)):
            loaded = whisper_models.load_whisper_model(model, use_gpu=True, local_model_path=folder)
            info = whisper_models.get_whisper_device_info(model, use_gpu=True, local_model_path=folder)
            if info.get("device") != "cuda":
                return FAIL, ("model loaded on the CPU, not the GPU: "
                              + _clean(info.get("gpu_error") or "no reason given"))
            with tempfile.TemporaryDirectory() as tmp:
                clip = os.path.join(tmp, "clip.wav")
                _write_silence_wav(clip)
                segments, _ = loaded.transcribe(clip)
                count = len(list(segments))
    finally:
        # Otherwise the model keeps its VRAM and skews every figure read after it.
        whisper_models.release_gpu_models()
    return PASS, f"loaded '{model}' on the GPU ({info.get('compute_type')}) and transcribed a 1 s clip ({count} segment(s))"


def check_torch_cuda():
    import dependency_updates
    import diagnostics
    import diagnostics_torch
    if not diagnostics.check_dependency("torch"):
        return SKIPPED, "torch is not installed"
    version = dependency_updates.installed_dist_version("torch") or "unknown"
    gpu = diagnostics_torch.get_gpu_status()
    if gpu.get("available"):
        return PASS, f"torch {version}, CUDA build {gpu.get('torch_cuda_version')}, GPU visible"
    if "torch_cuda_version" not in gpu:
        return FAIL, f"torch {version} is installed but could not be imported"
    return SKIPPED, f"torch {version} sees no CUDA GPU ({_clean(gpu.get('message'))})"


def check_ffmpeg():
    import diagnostics
    ff = diagnostics.check_ffmpeg()
    if not ff.get("found"):
        return FAIL, "ffmpeg is not on PATH"
    return PASS, _clean(ff.get("version"))


def check_libass():
    import diagnostics
    ff = diagnostics.check_ffmpeg()
    if not ff.get("found"):
        return FAIL, "ffmpeg is not on PATH, so no subtitle burn-in"
    if ff.get("libass") is None:
        return SKIPPED, "could not read ffmpeg's build flags"
    if not ff["libass"]:
        return FAIL, "this ffmpeg build lacks libass (burned-in subtitles will not work)"
    return PASS, "ffmpeg is built with libass"


def check_tts():
    from services import dub_service
    ready = [e["label"] for e in dub_service.TTS_ENGINES
             if dub_service._engine_install_problem(e["key"]) is None]
    if not ready:
        return SKIPPED, "no TTS engine is installed"
    return PASS, "available: " + ", ".join(ready)


def check_ocr():
    import diagnostics
    found = []
    if diagnostics.check_dependency("pytesseract") and shutil.which("tesseract"):
        found.append("Tesseract")
    # Both packages, as hardsub_ocr.paddle_installed requires; that module itself needs cv2 to import.
    if diagnostics.check_dependency("paddleocr") and diagnostics.check_dependency("paddle"):
        found.append("PaddleOCR")
    if diagnostics.check_dependency("manga_ocr"):
        found.append("manga-ocr")
    if not found:
        return SKIPPED, "no OCR engine is installed"
    return PASS, "available: " + ", ".join(found)


def check_ollama():
    import memory_headroom
    from services import settings_service
    if not settings_service.ollama_endpoint_is_loopback():
        return SKIPPED, "the configured Ollama address is not on this PC, so it was not contacted"
    base = (settings_service.resolve_key("ollama_url") or OLLAMA_DEFAULT_URL).rstrip("/")
    from lib.http import FetchError, ResponseTooLarge
    try:
        # memory_headroom's reader already sets timeout= and a byte cap.
        data = memory_headroom._ollama_json(f"{base}/api/tags")
    except ResponseTooLarge:
        return FAIL, "Ollama's reply was larger than expected"
    except FetchError:
        return SKIPPED, "Ollama is not running or not reachable on this PC"
    except Exception as exc:
        return FAIL, "Ollama answered but the reply was not usable: " + type(exc).__name__
    return PASS, f"reachable, {len(data.get('models') or [])} model(s) installed"


def check_memory():
    import memory_headroom
    parts = []
    for name, reading in memory_headroom.status().items():
        if reading.get("state") == "ok":
            gb = 1024 ** 3
            parts.append(f"{name.upper()} {reading['free_bytes'] / gb:.1f}/{reading['total_bytes'] / gb:.1f} GB free")
    if not parts:
        return SKIPPED, "memory could not be read"
    return PASS, ", ".join(parts)


def check_keys():
    from services import settings_service
    status = settings_service.key_status()
    return PASS, "; ".join(f"{_KEY_LABELS.get(k, k)}: {'set' if v else 'not set'}" for k, v in status.items())


def _checks(model: str):
    return [
        # First: the torch check opens a CUDA context that would shrink the free-VRAM reading.
        ("VRAM / RAM headroom", check_memory),
        ("GPU faster-whisper load + transcription", lambda: check_gpu_whisper(model)),
        ("torch / CUDA", check_torch_cuda),
        ("ffmpeg", check_ffmpeg),
        ("libass (subtitle burn-in)", check_libass),
        ("TTS engines", check_tts),
        ("OCR engines", check_ocr),
        ("Ollama (this PC only)", check_ollama),
        ("API key presence", check_keys),
    ]


def run_checks(model: str = DEFAULT_MODEL) -> list:
    results = []
    for name, check in _checks(model):
        try:
            status, reason = check()
        except Exception as exc:
            status, reason = FAIL, f"{type(exc).__name__}: {exc}"
        results.append((name, status, _clean(reason)))
    return results


def format_report(results: list) -> str:
    lines = [REPORT_TITLE,
             f"Date: {datetime.date.today().isoformat()}",
             f"Python {platform.python_version()} on {platform.system()}", ""]
    lines += [f"{status:<7} {name}: {reason}" for name, status, reason in results]
    counts = {s: sum(1 for _, st, _ in results if st == s) for s in (PASS, FAIL, SKIPPED)}
    lines += ["", f"{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIPPED]} skipped"]
    # Whole-text pass as well as per line: a name or label must not be able to leak either.
    return _clean_report("\n".join(lines))


def _clean_report(text: str) -> str:
    return _strip_locations(text)


def default_report_path() -> str:
    import portable
    return os.path.join(portable.data_dir(), REPORT_NAME)


def _refuse_to_overwrite(out: str, title: str = REPORT_TITLE) -> bool:
    """True if `out` exists and is not a report this tool wrote. --out could
    otherwise name .env or library.db, and write_text follows symlinks."""
    if not os.path.lexists(out):
        return False
    if os.path.islink(out) or not os.path.isfile(out):
        return True
    try:
        with open(out, "rb") as fh:
            first_line = fh.read(128).split(b"\n", 1)[0].rstrip(b"\r")
    except OSError:
        return True
    return first_line.decode("utf-8", "replace") != title


def _console_safe() -> None:
    # A cp1252 Windows console raises on characters the app's text can carry.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.self_test", description=__doc__.splitlines()[0])
    parser.add_argument("--out", help=f"report file (default: {REPORT_NAME} in the data folder)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Whisper model for the GPU check (must be downloaded)")
    args = parser.parse_args(argv)
    _console_safe()
    out = args.out or default_report_path()
    if _refuse_to_overwrite(out):
        print("Refusing to write the report: that file exists and is not a previous self-test report.")
        return 2
    import portable
    # An installed copy keeps its models under <data>/model_cache; the checks must look there.
    portable.activate_portable_mode()
    results = run_checks(args.model)
    report = format_report(results)
    try:
        Path(out).write_text(report + "\n", encoding="utf-8")
        saved = f"Report written to {Path(out).name}"
    except OSError:
        saved = "Could not write the report file; copy the text above instead."
    print(report)
    print("\n" + saved)
    return 1 if any(status == FAIL for _, status, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
