"""
services/benchmark_case_service.py -- runs one Benchmark Lab case against an
engine or a stage runner and returns its output, cost and timing. The run
loop, scoring and persistence stay in benchmark_lab_service.
"""

import time

import benchmark
import db
import translate_engines
from core import SOURCE_LANGUAGES


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
    try:
        # Same retry as a normal translation: a transient 429/5xx shouldn't
        # score 0 and skew the Arena.
        ctx = _translation_context(case)
        out = translate_engines.call_with_backoff(
            lambda: engine.translate_batch([case.get("source_text") or ""], ctx))
        output_text = out[0] if out else ""
        error = None
        usage = getattr(engine, "last_usage", None)
    except Exception as exc:
        output_text, error = "", redact(exc, key)
    cost = 0.0
    if usage:
        cost = translate_engines.estimate_cost_for_engine(
            engine, usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0,
            usage.get("cache_read_tokens", 0) or 0, usage.get("cache_write_tokens", 0) or 0)
    return {"output_text": output_text, "error": error, "cost_usd": cost, "usage": usage or {},
            "duration_seconds": time.monotonic() - started}


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
