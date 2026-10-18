"""
services/benchmark_case_service.py -- runs one Benchmark Lab case against an
engine or a stage runner and returns its output, cost and timing. The run
loop, scoring and persistence stay in benchmark_lab_service.
"""

import json
import os
import re
import tempfile
import time

import audio_preprocess
import background_jobs
import benchmark
import db
import long_line_split
import sensitivity_preset as presets
import translate_engines
import whisper_models
from core import SOURCE_LANGUAGES
from engine_backends import llm_tasks, paid_calls
from lib import http
from lib.errors import DependencyUnavailableError, InvalidInputError
from services import (asr_options_service, asr_timing_service, difficult_audio_service as difficult,
                      timing_settings_service)


def redact(text, key=None):
    """Secrets (redact_secrets, plus the run's own key by value, since a key
    without a recognisable prefix would otherwise slip through) and absolute
    paths / the OS user name (diagnostics_report.redact_for_support, via
    jobs_service's never-raising wrapper): errors are shown to remote admins."""
    if not text:
        return text
    text = str(text)
    if key and len(key) >= 8:
        text = text.replace(key, "[redacted]")
    from services import jobs_service
    return jobs_service.redact_text(text)


# Recognisers the Lab offers besides Whisper, as asr_backend names. Qwen3-ASR is the
# speech-detection backend: the one that needs no Whisper model.
LAB_BACKENDS = {"qwen3_asr_vad": "Qwen3-ASR", "anime_whisper": "anime-whisper"}
_BACKEND_NAMES = {"qwen3_asr_vad": "Qwen3-ASR", "anime_whisper": "anime-whisper"}
_LAB_NEVER_DOWNLOADS = " Run it once from a title's Transcribe page to download it; the Lab does not download it for you."
# Options a recogniser does not read, so a result is never taken to have been tuned by them.
_IGNORED_OPTIONS = {
    "whisper": ("refine_timing",),
    "qwen3_asr_vad": ("beam_size", "min_silence_ms", "vad_threshold", "sensitivity_preset",
                      "hallucination_silence_sec", "whisper_repeat_guard", "initial_prompt", "speech_pad_ms"),
    "anime_whisper": ("beam_size", "min_silence_ms", "vad_threshold", "sensitivity_preset",
                      "hallucination_silence_sec", "initial_prompt", "speech_pad_ms", "refine_timing"),
}


def whisper_options() -> dict:
    """The Whisper choices the Lab offers, and the per-language table the picker reads."""
    return {"whisper_sizes": [*whisper_models.WHISPER_MODELS, benchmark.LOCAL_FOLDER_MODEL],
            "whisper_exclusions": whisper_models.whisper_language_exclusions(),
            "transcription_backends": [{"name": name, "label": label, "problem": backend_problem(name)}
                                       for name, label in LAB_BACKENDS.items()]}


def ignored_options(backend: str) -> list:
    return list(_IGNORED_OPTIONS.get(backend, ()))


def backend_problem(backend: str, language: str = None, refine: bool = None):
    """The plain sentence naming what a recogniser still needs on this PC, or None when it can
    run. Everything a real run would load counts, the forced aligner of timing refinement
    included (when `refine` is None, as Settings has it), because the loaders download whatever
    is missing and the Lab must not."""
    from services import model_readiness_service as ready
    from services.qwen3_requirements_service import require_qwen3_packages, require_vad_backend_packages
    from services.transcribe_options_service import _require_vad_packages, anime_whisper_package_problem
    name = _BACKEND_NAMES[backend]
    try:
        if backend == "qwen3_asr_vad":
            if language:
                require_vad_backend_packages(backend, language)
            else:
                require_qwen3_packages(name)
        else:
            problem = anime_whisper_package_problem()
            if problem:
                return problem
        _require_vad_packages()
    except DependencyUnavailableError as exc:
        return exc.message
    except ValueError:
        # find_spec raises for a module already in sys.modules without a __spec__ (a stub): it is
        # importable, so this is no reason to grey the recogniser out.
        pass
    if refine is None:
        refine = asr_options_service.get_vad_refine_timing()
    for need in ready._backend_needs(backend, refine, asr_options_service.get_mixed_languages()):
        if not need():
            what = "The Qwen3 forced aligner (used by timing refinement)" if need is ready.aligner_ready else name
            return f"{what} isn't downloaded yet." + _LAB_NEVER_DOWNLOADS
    return None


def require_backend_ready(backend: str, cases, refine: bool = None) -> None:
    """A recogniser other than Whisper runs only when its packages and models are already on
    this PC: refused with the app's plain sentence otherwise, never downloaded from here."""
    for language in sorted({c.get("source_language") or "zh" for c in cases}):
        problem = backend_problem(backend, language, refine)
        if problem:
            raise DependencyUnavailableError(problem)


def require_separation_ready(configs, use_gpu) -> None:
    """Separate vocals first loads a separation model that is fetched on first use."""
    from services import model_readiness_service as ready
    if any(c["options"]["separate_vocals_first"] for c in configs) and not ready.separation_ready("auto", use_gpu):
        raise DependencyUnavailableError(
            "The vocal separation model isn't downloaded yet. Run Separate vocals first once on a title "
            "to download it; the Lab does not download it for you.")


def require_backend_fits_cases(backend: str, cases) -> None:
    """The language rule a title's backend choice has (anime-whisper is Japanese only)."""
    for case in cases:
        problem = asr_options_service.asr_backend_language_problem(
            backend, case.get("source_language") or "zh")
        if problem:
            raise InvalidInputError(problem)


def require_whisper_fits_cases(stage, configs, cases):
    """A Whisper model runs only on cases whose language it is recommended for."""
    if stage != "transcription":
        return
    for config in configs:
        for case in cases:
            problem = whisper_models.whisper_model_language_problem(
                config["model"], case.get("source_language") or "zh")
            if problem:
                raise InvalidInputError(problem)


def _translation_context(case: dict) -> dict:
    """The case's own source language, where every engine reads it (MT
    engines from source_language, LLM prompts from drama_meta); an empty
    context would make every engine treat Japanese/Korean as Chinese."""
    lang = case.get("source_language") if case.get("source_language") in SOURCE_LANGUAGES else "zh"
    return {"source_language": lang, "target_language": "en",
            "drama_meta": {"source_language": lang}}


def run_translation(engine, case: dict, key: str = None) -> dict:
    started = time.monotonic()
    usage = None
    stop_reason = ""
    try:
        # Same retry as a normal translation: a transient 429/5xx shouldn't
        # score 0 and skew the Arena.
        ctx = _translation_context(case)
        # Bounded, because a Cancel pressed while a hung engine is answering
        # would otherwise wait for the SDK's own timeout.
        out = translate_engines.call_with_backoff(
            lambda: llm_tasks.call_batch_bounded(
                engine, lambda: engine.translate_batch([case.get("source_text") or ""], ctx),
                on_late_result=lambda: paid_calls.record_call(
                    engine, getattr(engine, "name", ""), "benchmark",
                    getattr(engine, "last_usage", None))))
        output_text = out[0] if out else ""
        error = None
        usage = getattr(engine, "last_usage", None)
    except Exception as exc:
        output_text, error = "", redact(exc, key)
        stop_reason = paid_calls.stop_reason_for(exc)
    cost = 0.0
    if usage:
        cost = translate_engines.estimate_cost_for_engine(
            engine, usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0,
            usage.get("cache_read_tokens", 0) or 0, usage.get("cache_write_tokens", 0) or 0)
    return {"output_text": output_text, "error": error, "cost_usd": cost, "usage": usage or {},
            "duration_seconds": time.monotonic() - started, "stop_reason": stop_reason}


def run_ocr(cfg: dict, case: dict) -> dict:
    if not case.get("input_filename"):
        return {"output_text": "", "error": "This case has no input file.", "duration_seconds": 0.0}
    prepared = dict(case, input_path=os.path.join(db.BENCHMARK_DIR, case["input_filename"]))
    r = benchmark.run_ocr_case(prepared, backend=cfg["engine"])
    r["error"] = redact(r.get("error"))
    return r


class _Reporter:
    """The reporter difficult_audio_service.prepare wants; the Lab shows progress per case."""

    def __init__(self, cancelled):
        self.cancelled = cancelled

    def progress(self, frac, message=""):
        pass


def _whisper_kwargs(options: dict, timing: dict, tuning, progress_cb) -> dict:
    row = {"vad_threshold": options["vad_threshold"], "sensitivity_preset": options["sensitivity_preset"],
           "difficult_audio": options["difficult_audio"]}
    # A Difficult audio run uses the Sensitive preset, as a title's run does.
    run_row = presets.difficult_audio_view(row)
    return {
        "initial_prompt": options["initial_prompt"], "beam_size": options["beam_size"],
        "min_silence_duration_ms": options["min_silence_ms"],
        "vad_threshold": timing_settings_service.vad_threshold_for(run_row),
        "hallucination_silence_sec": options["hallucination_silence_sec"],
        "speech_pad_ms": options["speech_pad_ms"],
        "repeat_guard": options["whisper_repeat_guard"],
        "sensitivity_preset": presets.normalize(run_row.get("sensitivity_preset")),
        "filter_hallucination_repeats": tuning.repeat_min_repeats,
        "stock_isolation_sec": tuning.stock_isolation_sec,
        "progress_cb": progress_cb,
        **difficult.whisper_kwargs(timing),
    }


def _backend_kwargs(backend: str, options: dict, tuning, language: str, cancel_check) -> dict:
    detector = asr_options_service.resolve_voice_detector({}, language)
    if backend == "anime_whisper":
        return {"detector": detector, "repeat_guard": options["whisper_repeat_guard"], "cancel_check": cancel_check}
    return {"batch_size": asr_options_service.get_qwen_asr_batch_size(),
            "refine_timing": options["refine_timing"],
            "mixed_languages": asr_options_service.get_mixed_languages(),
            "detector": detector, "tuning": tuning, "cancel_check": cancel_check}


def run_transcription(cfg: dict, case: dict, use_gpu: bool, folder: str = None, cancelled=None) -> dict:
    """One transcription case at the options of `cfg` (a checked config: engine is
    "whisper" or a LAB_BACKENDS name, "options" the complete set from
    transcribe_options_service.resolve_lab_options). Mirrors what a title's run does to
    the audio first (levelling, vocal separation) and which values it hands the
    recogniser, so the score is the one a real title would get. The text score ignores
    line timing; a case with reference times (`ref_start`, `ref_end`) also carries a
    "timing" measure of where the recognised lines sit against them. Realigning and
    mixed-language splitting are not part of a Lab run.

    Anything that goes wrong for this case, preparing its audio included, is its own
    error: one unreadable clip must not end the run for every arm. `cancelled()` is
    polled where the work can stop; the caller sees the cancel and drops the result."""
    started = time.monotonic()
    if not case.get("input_filename"):
        return {"output_text": "", "error": "This case has no input file.", "duration_seconds": 0.0}
    options, backend = cfg["options"], cfg["engine"]
    language = case.get("source_language") or "zh"
    cancelled = cancelled or (lambda: False)

    def cancel_check():
        if cancelled():
            raise background_jobs.JobCancelled()

    try:
        with tempfile.TemporaryDirectory(prefix="benchmark_") as scratch:
            timing = difficult.mark(timing_settings_service.values(), options)
            prepared = difficult.prepare(
                _Reporter(cancelled), os.path.join(db.BENCHMARK_DIR, case["input_filename"]), scratch, "whisper",
                backend, False, asr_options_service.get_mixed_languages(), timing, own_group=True)
            audio = prepared.audio_path
            if options["separate_vocals_first"]:
                audio = audio_preprocess.separate_vocals(
                    audio, os.path.join(scratch, "vocals.wav"), use_gpu=use_gpu, cancel_check_cb=cancelled)
            tuning = timing_settings_service.asr_tuning(prepared.timing)
            if backend == "whisper":
                kwargs = _whisper_kwargs(options, prepared.timing, tuning, lambda frac: cancel_check())
            else:
                kwargs = _backend_kwargs(backend, options, tuning, language, cancel_check)
            result = benchmark.run_transcription_case(
                dict(case, input_path=audio), whisper_size=cfg["model"], use_gpu=use_gpu,
                local_model_path=folder if cfg["model"] == benchmark.LOCAL_FOLDER_MODEL else None,
                options=kwargs, backend=backend)
            result["timing"] = _timing_of(result.pop("segments", None), case, options, audio)
    except Exception as exc:
        result = {"output_text": "", "error": str(exc) or type(exc).__name__}
    result["duration_seconds"] = time.monotonic() - started
    result["error"] = redact(result.get("error"))
    return result


def _timing_of(segments, case: dict, options: dict, audio: str):
    """The case's timing measure, or None without reference times or a result. The recognised
    lines are cut at the arm's split limits first, the way a title's lines are."""
    ref_start, ref_end = case.get("ref_start"), case.get("ref_end")
    if segments is None or ref_start is None or ref_end is None:
        return None
    lines = long_line_split.split_long_segments(
        [s for s in segments if (s.get("text") or "").strip()],
        max_seconds=options["split_max_seconds"], max_cjk_chars=options["split_max_chars"])
    return asr_timing_service.case_timing(lines, ref_start, ref_end, asr_timing_service.speech_spans(audio))


def app_version() -> str:
    """The installed version, else the checked-out commit, so a result can be tied to the code that made it."""
    from services import update_service
    from services.auto_backup_files import _app_version
    return update_service.current_version() or _app_version()


_DIGEST = re.compile(r"^(sha256:)?[0-9a-f]{1,100}$")


def _ollama_digest(model: str):
    """The digest Ollama reports for a pulled tag, or None when the server or the tag can't be read:
    a tag can be re-pulled to different weights under the same name, so the name alone doesn't identify a result."""
    from services import settings_service
    base_url = (settings_service.resolve_key("ollama_url") or "http://localhost:11434").rstrip("/")
    wanted = model if ":" in model else f"{model}:latest"
    try:
        # guard=None: the address is the Ollama server the owner configured, as for every other Ollama call.
        resp = http.get(f"{base_url}/api/tags", timeout=3, max_bytes=2 * 1024 * 1024, guard=None, trust_env=False)
        if resp.status >= 400:
            return None
        for entry in json.loads(resp.body).get("models") or []:
            if isinstance(entry, dict) and wanted in (entry.get("name"), entry.get("model")):
                digest = entry.get("digest")
                return digest if isinstance(digest, str) and _DIGEST.match(digest) else None
    except Exception:
        return None
    return None


def model_identity(stage: str, cfg: dict) -> dict:
    """What model a config ran, without a path: a Whisper size, the offline folder by name only, a
    model repo and revision, an Ollama tag with its digest, or a provider's model name."""
    engine, model = cfg["engine"], cfg.get("model")
    if stage == "transcription":
        if engine == "qwen3_asr_vad":
            import qwen3_native
            return {"model": qwen3_native.asr_repo_id("1.7B")}
        if engine == "anime_whisper":
            return {"model": whisper_models.ANIME_WHISPER_PIN["repo"],
                    "revision": whisper_models.ANIME_WHISPER_PIN["revision"]}
        if model == benchmark.LOCAL_FOLDER_MODEL:
            return {"model": benchmark.LOCAL_FOLDER_LABEL}
        return {"model": model}
    if stage == "ocr":
        return {"model": engine}
    resolved = model or translate_engines.effective_default_model(engine)
    identity = {"model": resolved}
    if engine == "ollama" and resolved:
        identity["digest"] = _ollama_digest(resolved)
    return identity
