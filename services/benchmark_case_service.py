"""
services/benchmark_case_service.py -- runs one Benchmark Lab case against an
engine or a stage runner and returns its output, cost and timing. The run
loop, scoring and persistence stay in benchmark_lab_service.
"""

import time

import benchmark
import db
import translate_engines
import whisper_models
from core import SOURCE_LANGUAGES
from engine_backends import llm_tasks, paid_calls
from lib.errors import InvalidInputError


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


def whisper_options() -> dict:
    """The Whisper choices the Lab offers, and the per-language table the picker reads."""
    return {"whisper_sizes": [*whisper_models.WHISPER_MODELS, benchmark.LOCAL_FOLDER_MODEL],
            "whisper_exclusions": whisper_models.whisper_language_exclusions()}


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


def run_file_case(stage: str, cfg: dict, case: dict, use_gpu: bool) -> dict:
    import os
    if not case.get("input_filename"):
        return {"output_text": "", "error": "This case has no input file.", "duration_seconds": 0.0}
    prepared = dict(case, input_path=os.path.join(db.BENCHMARK_DIR, case["input_filename"]))
    if stage == "transcription":
        r = benchmark.run_transcription_case(prepared, whisper_size=cfg["model"], use_gpu=use_gpu)
    else:
        r = benchmark.run_ocr_case(prepared, backend=cfg["engine"])
    r["error"] = redact(r.get("error"))
    return r
