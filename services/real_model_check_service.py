"""
services/real_model_check_service.py -- the opt-in "real-model check" in
Diagnostics: one tiny transcription, one OCR read and one local-Ollama
translation with the models the user actually has set up. It complements
the "Test first" run, which only exercises mocked tests and so cannot see
GPU or model breakage.

Three independent checks, each reported as pass / fail / skipped /
could-not-check with a short redacted reason. A missing optional package, a
model that is not downloaded or an Ollama that is not running is "skipped",
never "failed": nothing here downloads or pulls a model. "Could not check"
is for when we cannot tell where a model lives, so we must not claim it is
missing. The samples ship in assets/smoke/ (the installer includes assets/
but not tests/), so this works on an installed copy.

The app runs it as one GPU-touching background job behind the same install
guard as the other Diagnostics actions; the `smoke` CLI command
(real_model_check_cli.py) calls run_checks directly, so both report the
same checks. Reasons never carry paths or URLs.
"""

import contextlib
import importlib.util
import os
import re
import shutil
import threading
import wave

import requests

import background_jobs
import diagnostics
import diagnostics_report
import memory_headroom
from engine_backends.local import strip_ollama_thinking
from services import diagnostics_gaps_service as gaps
from services import vram_service
from services.service_errors import ServiceError

JOB_ID = "real_model_check"
PASS, FAIL, SKIPPED, COULD_NOT_CHECK = "pass", "fail", "skipped", "could_not_check"

# Fixed Chinese samples: the app's default source language, and the clip is
# a plain tone, so a pass means the model loaded and ran, not that it
# recognised speech.
_LANGUAGE = "zh"
_SAMPLE_DIR = os.path.join(gaps.default_project_root(), "assets", "smoke")
_CLIP = os.path.join(_SAMPLE_DIR, "clip.wav")
_IMAGE = os.path.join(_SAMPLE_DIR, "bubble.png")
_TRANSLATE_TEXT = "你好"
# A check is a quick sanity run, not a transcription job.
_MAX_CLIP_BYTES = 50 * 1024 * 1024
_MAX_CLIP_SECONDS = 60
_DEFAULT_OLLAMA_URL = "http://localhost:11434"

_LOCK = threading.Lock()
_STATE = {"checks": [], "finished": False}


class _Skip(Exception):
    """The check cannot run on this machine (not a fault)."""


class _Failed(Exception):
    """A failure whose message we wrote ourselves, so it is safe to show.
    Any other exception can carry a URL, host or path in its text and is
    reported by class name only."""


class _CouldNotCheck(Exception):
    """We cannot tell whether the check's prerequisite is met, so it is
    neither run nor reported as missing."""


def _redact(text) -> str:
    return diagnostics_report.redact_for_support("" if text is None else str(text))[:300]


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _hf_repo_cached(fragment: str) -> bool:
    fragment = fragment.lower()
    return any(fragment in e["repo_id"].lower() for e in diagnostics.scan_hf_cache())


def _hf_repo_exact(repo_id: str) -> bool:
    # A substring match would let another Qwen model stand in for the one the
    # backend loads, and the load would then download the real one.
    return any(e["repo_id"].lower() == repo_id.lower() for e in diagnostics.scan_hf_cache())


@contextlib.contextmanager
def _offline_models():
    """The check must never download. The backends load with plain
    from_pretrained, so switch Hugging Face to offline for the run: the
    libraries read the env var at import, hence the constant as well."""
    names = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    saved = {n: os.environ.get(n) for n in names}
    os.environ.update({n: "1" for n in names})
    constants, old_flag = None, None
    try:
        from huggingface_hub import constants
        old_flag, constants.HF_HUB_OFFLINE = constants.HF_HUB_OFFLINE, True
    except ImportError:
        constants = None
    try:
        yield
    finally:
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
        if constants is not None:
            constants.HF_HUB_OFFLINE = old_flag


def _spoken_text(segments) -> str:
    return " ".join(str(s.get("text") or "") for s in segments or [] if isinstance(s, dict))


def _comparable(text: str) -> str:
    # ASR punctuation and spacing vary between backends; the words are
    # what the expected text is meant to pin down.
    return re.sub(r"[\W_]+", "", text.casefold())


def _check_speech_clip_size(path: str) -> None:
    if not os.path.isfile(path):
        raise _Failed("The speech clip was not found.")
    if os.path.getsize(path) > _MAX_CLIP_BYTES:
        raise _Failed(f"The speech clip is larger than {_MAX_CLIP_BYTES // (1024 * 1024)} MB; use a shorter one.")
    try:
        with wave.open(path, "rb") as w:
            seconds = w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        # Not a WAV: the size cap above is the only bound we can apply.
        return
    if seconds > _MAX_CLIP_SECONDS:
        raise _Failed(f"The speech clip is longer than {_MAX_CLIP_SECONDS} seconds; use a shorter one.")


def _check_asr(speech_clip=None, expected_text=None) -> str:
    from services import asr_options_service, settings_service, transcribe_service
    # No global ASR setting exists (the choice is per title), so this is
    # the backend a new Chinese title would get.
    backend = asr_options_service.stored_asr_backend({"source_language": _LANGUAGE})
    use_gpu = settings_service.get_use_gpu()
    if backend == "whisper":
        import core
        size = transcribe_service.default_whisper_size()
        if not _installed("faster_whisper"):
            raise _Skip("faster-whisper is not installed.")
        if not core.is_whisper_model_cached(size):
            raise _Skip(f"The Whisper {size} model is not downloaded.")
    else:
        if not _installed("qwen_asr"):
            raise _Skip("qwen-asr is not installed.")
    if not os.path.isfile(_CLIP):
        raise _Failed("The bundled audio sample is missing; reinstall the app.")
    if speech_clip:
        _check_speech_clip_size(speech_clip)

    import asr_backend
    runner = asr_backend.get_backend(backend)
    if backend != "whisper":
        import forced_align
        repos = [asr_backend.qwen3_asr_repo_id(runner.model_size)]
        # The aligner loads only once speech is found, so a tone never needs
        # it, but the speech clip and real titles do.
        if getattr(runner, "long_windows", False):
            repos.append(forced_align.ALIGNER_REPO_ID)
        if not all(_hf_repo_exact(r) for r in repos):
            raise _Skip("A Qwen3-ASR model is not downloaded.")
    loaded = []

    def finish(detail):
        # The Qwen backends return before loading anything when speech
        # detection finds none, so a plain tone proves nothing about the model.
        if backend != "whisper" and not loaded:
            raise _Skip("Tone only: speech detection found no speech, so the recognition model was not loaded.")
        return detail

    def transcribe(path):
        fallback = []
        if backend == "whisper":
            segments = runner.transcribe(path, _LANGUAGE, whisper_size=size, use_gpu=use_gpu,
                                         on_gpu_fallback=fallback.append)
        else:
            segments = runner.transcribe(path, _LANGUAGE, use_gpu=use_gpu,
                                         on_device=lambda *_: loaded.append(1),
                                         on_gpu_fallback=lambda *_: fallback.append(1))
        if fallback:
            # A silent CPU fallback is the breakage this check exists to surface.
            raise _Failed("The GPU could not be used, so it ran on the CPU instead.")
        return segments

    with _offline_models():
        segments = transcribe(_CLIP)
        detail = (f"{backend} ran on the {'GPU' if use_gpu else 'CPU'} ({len(segments or [])} segment(s)). "
                  "A pass means the model loaded and ran, not that words were recognised.")
        if speech_clip:
            heard = _spoken_text(transcribe(speech_clip))
            if not expected_text:
                detail += f" The speech clip produced {len(heard.strip())} character(s); no expected text was given to compare."
                return finish(detail)
            if _comparable(expected_text) not in _comparable(heard):
                raise _Failed("The speech clip was transcribed, but the text did not match the expected text.")
            detail += " The speech clip matched the expected text."
    return finish(detail)


def _paddle_models_state() -> str:
    """PaddleX keeps its models outside the Hugging Face cache, in
    <cache>/official_models, where <cache> is PADDLE_PDX_CACHE_HOME or
    ~/.paddlex. "present" when the exact folders ocr.py asks for are all
    there, "missing" when the folder holds models but not all of those, and
    "unknown" when it
    cannot be read or is empty: the layout differs between versions, so
    absence there is not proof."""
    root = os.environ.get("PADDLE_PDX_CACHE_HOME", "").strip()
    if not root:
        home = os.path.expanduser("~")
        if home == "~":
            return "unknown"
        root = os.path.join(home, ".paddlex")
    try:
        with os.scandir(os.path.join(root, "official_models")) as entries:
            names = [e.name.lower() for e in entries if e.is_dir()]
    except OSError:
        return "unknown"
    if not names:
        return "unknown"
    # Exact names: a korean_/japan_/latin_ rec folder or another PP-OCR
    # version would pass a suffix match, and PaddleOCR would then download
    # the Chinese model the call really loads.
    import ocr
    needed = {n.lower() for n in ocr._PADDLE_CH_MODELS.values()}
    return "present" if needed <= set(names) else "missing"


def _ocr_requirement(backend: str) -> None:
    import ocr
    if backend == "tesseract":
        if not _installed("pytesseract"):
            raise _Skip("pytesseract is not installed.")
        if not shutil.which("tesseract"):
            raise _Skip("The Tesseract program is not installed.")
    elif backend == "manga_ocr":
        if not _installed("manga_ocr"):
            raise _Skip("manga-ocr is not installed.")
        if not _hf_repo_cached("manga-ocr-base"):
            raise _Skip("The manga-ocr model is not downloaded.")
    elif backend == "paddle_vl_manga":
        problem = ocr.paddle_vl_manga_problem()
        if problem:
            raise _Skip(problem)
        if not (_hf_repo_exact(ocr._PADDLE_VL_MANGA_REPO)
                and _hf_repo_exact(ocr._PADDLE_VL_PROCESSOR_REPO)):
            raise _Skip("The PaddleOCR-VL manga model is not downloaded.")
    else:
        if not _installed("paddleocr"):
            raise _Skip("paddleocr is not installed.")
        state = _paddle_models_state()
        if state == "missing":
            raise _Skip("Some PaddleOCR models are not downloaded.")
        if state == "unknown":
            raise _CouldNotCheck(
                "Could not check whether the PaddleOCR models are downloaded: none were found in "
                "the folder PaddleX normally uses (set PADDLE_PDX_CACHE_HOME if you keep them "
                "elsewhere). Nothing was downloaded.")


def _check_ocr() -> str:
    from services import settings_service
    backend = settings_service.resolve_ocr_backend(_LANGUAGE)
    _ocr_requirement(backend)
    if not os.path.isfile(_IMAGE):
        raise _Failed("The bundled image sample is missing; reinstall the app.")
    import ocr
    # Offline for the same reason as the ASR check: from_pretrained and
    # MangaOcr() would otherwise refetch a cached main revision.
    with _offline_models():
        text = ocr.extract_text_from_images([_IMAGE], backend=backend, source_language=_LANGUAGE)
    if not text.strip():
        raise _Failed(f"{backend} found no text in a sample that has text.")
    return f"{backend} read {len(text.strip())} character(s)."


def _check_translate() -> str:
    import translate_engines
    from services import settings_service
    base_url = (settings_service.resolve_key("ollama_url") or _DEFAULT_OLLAMA_URL).rstrip("/")
    model = translate_engines.OLLAMA_DEFAULT_MODEL
    try:
        translate_engines.check_ollama_model_installed(base_url, model)
        # A real translation honours Keep free graphics memory before the
        # model loads; the check must refuse the same way.
        memory_headroom.check_ollama(base_url, model)
        reply = translate_engines._ollama_chat(base_url, {
            "model": model, "stream": False,
            "messages": [{"role": "user", "content":
                          f"Translate to English, reply with the translation only: {_TRANSLATE_TEXT}"}],
            "options": {"num_ctx": 2048},
        })
    except translate_engines.OllamaUnavailableError as exc:
        if exc.reason == "ollama_timeout":
            raise _Failed(exc.message) from None
        raise _Skip(exc.message) from None
    except requests.exceptions.HTTPError:
        # Ollama answers 500 when the model does not fit in GPU memory. The
        # exception text ends with the request URL, so it is never shown.
        raise _Failed(f"Ollama refused the request or ran out of memory loading {model}.") from None
    except requests.exceptions.InvalidURL:
        raise _Failed("The Ollama address in Settings is not valid.") from None
    except (requests.exceptions.InvalidSchema, requests.exceptions.MissingSchema):
        raise _Failed("The Ollama address in Settings is not valid.") from None
    except requests.exceptions.RequestException:
        raise _Failed("Could not reach Ollama.") from None
    answer = strip_ollama_thinking(
        str((reply.get("message") or {}).get("content") or ""))
    if not answer:
        raise _Failed(f"{model} returned an empty answer.")
    return f"{model} translated one line."


_CHECKS = (("asr", "Transcription", _check_asr),
           ("ocr", "OCR", _check_ocr),
           ("translate", "Translation (Ollama)", _check_translate))


class _Halt(Exception):
    """A memory refusal: the GPU cannot take the next model either, so the
    remaining checks must not run."""

    def __init__(self, result):
        super().__init__()
        self.result = result


def _run_check(check_id: str, label: str, fn, **options) -> dict:
    try:
        status, reason = PASS, fn(**options)
    except _Skip as exc:
        status, reason = SKIPPED, str(exc)
    except _CouldNotCheck as exc:
        status, reason = COULD_NOT_CHECK, str(exc)
    except background_jobs.JobCancelled:
        raise
    except (vram_service.InsufficientVramError, memory_headroom.HeadroomError) as exc:
        raise _Halt({"id": check_id, "label": label, "status": FAIL,
                     "reason": _redact(exc)}) from None
    except ModuleNotFoundError as exc:
        status, reason = SKIPPED, f"A required package is not installed ({exc.name or 'unknown'})."
    except ImportError:
        # find_spec succeeded, so the package is there but broken (a DLL that
        # will not load); calling that "skipped" would hide it behind exit 0.
        status, reason = FAIL, "A required package is installed but could not be loaded; reinstall it in Diagnostics."
    except Exception as exc:
        # Raw exception text can carry URLs, hosts or paths; only our own
        # _Failed messages are shown.
        status, reason = FAIL, (str(exc) if isinstance(exc, _Failed)
                                else f"Unexpected error ({type(exc).__name__}).")
    return {"id": check_id, "label": label, "status": status, "reason": _redact(reason)}


def run_checks(speech_clip=None, expected_text=None, before_check=None, on_result=None) -> list:
    """Runs every check in order and returns their results. The app's job and
    the CLI both call this, so they cannot report different checks.
    before_check(index, label) may raise to stop the run; on_result gets
    each result as it lands."""
    results = []
    options = {"asr": {"speech_clip": speech_clip, "expected_text": expected_text}}
    try:
        for i, (check_id, label, fn) in enumerate(_CHECKS):
            if before_check:
                before_check(i, label)
            halted = False
            try:
                result = _run_check(check_id, label, fn, **options.get(check_id, {}))
            except _Halt as halt:
                result, halted = halt.result, True
            results.append(result)
            if on_result:
                on_result(result)
            if halted:
                break
    finally:
        # The models stay resident otherwise, holding VRAM the user was
        # told to free for this check.
        try:
            import core
            core.release_gpu_models()
        except Exception:
            pass
    return results


def _job():
    def before_check(i, label):
        if background_jobs.is_cancel_requested(JOB_ID):
            raise background_jobs.JobCancelled()
        background_jobs.update_progress(JOB_ID, i / len(_CHECKS), f"Checking {label.lower()}")

    def on_result(result):
        with _LOCK:
            _STATE["checks"].append(result)

    try:
        run_checks(before_check=before_check, on_result=on_result)
    finally:
        with _LOCK:
            _STATE["finished"] = True
    with _LOCK:
        failed = any(c["status"] == FAIL for c in _STATE["checks"])
    background_jobs.set_result(JOB_ID, {"status": FAIL if failed else PASS})


def get_state() -> dict:
    job = background_jobs.get_status(JOB_ID)
    with _LOCK:
        checks = [dict(c) for c in _STATE["checks"]]
        finished = _STATE["finished"]
    return {
        "job_id": JOB_ID,
        "job": ({"status": job.get("status"), "progress": float(job.get("progress") or 0.0),
                 "message": _redact(job.get("message") or ""),
                 "error": _redact(job.get("error")) if job.get("error") else None}
                if job else None),
        "checks": checks,
        "finished": finished,
    }


def start(confirm: bool = False) -> dict:
    """PC only. 409 while anything else runs (the GPU must be free)."""
    gaps.guard(confirm)
    with _LOCK:
        started = background_jobs.start_job(JOB_ID, _job, gpu_touching=True,
                                            description="Real-model check")
        if started:
            _STATE.update(checks=[], finished=False)
    if not started:
        raise gaps.AdminActionJobsRunning("A real-model check is already running.")
    return {"job_id": JOB_ID, "started": True}
