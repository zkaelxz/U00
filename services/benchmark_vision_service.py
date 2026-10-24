"""
services/benchmark_vision_service.py -- vision engines (Claude, Gemini, OpenAI,
Ollama) as arms of the Benchmark Lab's OCR stage: a model check shared with
translation arms, the worst-case estimate and one read of a case's stored
picture. UI-free. The run loop, cap and result rows stay in benchmark_lab_service.
"""
import os
import time

import background_jobs
import db
import translate_engines
from engine_backends import llm_tasks
from engine_backends.vision import VISION_ENGINES
from services import benchmark_hardsub_read_service as hardsub
from services import image_text_service, page_import_limits, settings_service, translate_service
from services.benchmark_case_service import redact, run_translation
from services.benchmark_scoring_service import score_output
from services.service_errors import InvalidInputError, MissingKeyError, UnsupportedOperationError


def checked_model(entry: dict, engine: str, model):
    """The model a translation or vision arm may use, or None for the engine's own default."""
    if model is None:
        return None
    if engine == "ollama":
        # Only ever sent to the local Ollama server; shape-checked.
        if not model or len(model) > 100 or any(ch.isspace() for ch in model) \
                or ".." in model or model.startswith("/"):
            raise InvalidInputError("That model isn't offered for this engine.")
    elif entry["models"] is None and model == translate_engines.effective_default_model(engine):
        return None   # an engine without a model picker: its built-in model
    elif entry["models"] is None or model not in entry["models"]:
        raise InvalidInputError("That model isn't offered for this engine.")
    return model


def is_vision_arm(stage: str, cfg: dict) -> bool:
    return stage == "ocr" and cfg.get("engine") in VISION_ENGINES


def _checked_engine_model(engine: str, model) -> dict:
    entry = next((e for e in translate_service.list_engines() if e["name"] == engine), None)
    if entry is None:
        raise InvalidInputError("Unknown translate engine.")
    return {"engine": engine, "model": checked_model(entry, engine, model)}


def checked_vision_config(engine: str, model, translate=None) -> dict:
    """A vision arm; with `translate` ({engine, model}) it also translates what it reads and is
    scored on the translation as well as on the read."""
    checked = _checked_engine_model(engine, model)
    if translate is not None:
        checked["translate"] = _checked_engine_model(translate.get("engine"), translate.get("model"))
    return checked


def translate_arm(cfg: dict):
    return cfg.get("translate")


def require_translation_references(stage: str, configs: list, cases: list) -> None:
    """A read-then-translate arm is scored against a reference translation, so every case it runs needs one."""
    if not any(translate_arm(c) for c in configs if is_vision_arm(stage, c)):
        return
    missing = [c for c in cases if not (hardsub.load(c) or {}).get("translation")]
    if missing:
        raise UnsupportedOperationError(
            f"{len(missing)} of these cases have no reference translation, so a read-then-translate "
            "arm can't be scored on them.")


def _case_image_size(case: dict) -> tuple:
    """(width, height) of a case's stored picture; the largest size a read is priced at when
    it can't be opened, so the estimate never comes out low."""
    from PIL import Image
    try:
        with Image.open(os.path.join(db.BENCHMARK_DIR, case["input_filename"])) as im:
            return im.size
    except Exception:
        return image_text_service.MAX_EDGE, image_text_service.MAX_EDGE


def paid_arm(stage: str, cfg: dict) -> bool:
    """Arms that call a provider with a key: every translation arm and each vision arm."""
    return stage == "translation" or is_vision_arm(stage, cfg)


def resolve_keys(stage: str, configs: list) -> list:
    """One API key per config (None for a local arm); a paid arm without a key refuses the run."""
    keys = []
    for cfg in configs:
        key = translate_service.resolve_api_key(cfg["engine"]) if paid_arm(stage, cfg) else None
        if key is None and paid_arm(stage, cfg):
            raise MissingKeyError(cfg["engine"])
        if is_vision_arm(stage, cfg) and translate_arm(cfg) and not translate_service.resolve_api_key(cfg["translate"]["engine"]):
            raise MissingKeyError(cfg["translate"]["engine"])
        keys.append(key)
    return keys


def needs_gpu(stage: str, configs: list) -> bool:
    """Whether a run may touch the GPU before Ollama is considered: any local recogniser does."""
    return stage != "translation" and not all(is_vision_arm(stage, c) for c in configs)


def arm_capped(cfg: dict, applies) -> bool:
    """Whether the monthly and run caps govern this arm: its own engine, or the engine it translates with."""
    return applies(cfg["engine"]) or bool(translate_arm(cfg) and applies(cfg["translate"]["engine"]))


def estimate_cost(stage: str, cfg: dict, cases: list, price=None) -> float:
    """Worst-case cost of reading every case's picture with a vision arm (every picture of a hard-sub
    case) plus, for a read-then-translate arm, the translation priced by `price(config, cases)`; 0 for
    any other arm."""
    if not is_vision_arm(stage, cfg):
        return 0.0
    total = 0.0
    for case in cases:
        width, height = _case_image_size(case)
        pictures = len((hardsub.load(case) or {}).get("frames") or [None])
        total += pictures * (image_text_service.estimate(
            width, height, cfg["engine"], cfg["model"])["estimated_usd"] or 0.0)
    if translate_arm(cfg) and price:
        total += price(cfg["translate"], [{"source_text": c.get("reference_text")} for c in cases])
    return total


def _translator(cfg: dict):
    """The engine and key of a read-then-translate arm's translation step."""
    t = cfg["translate"]
    key = translate_service.resolve_api_key(t["engine"])
    if key is None:
        raise MissingKeyError(t["engine"])
    engine = translate_engines.get_engine(
        t["engine"], key, t["model"], free_tier=t["engine"] == "gemini" and settings_service.get("gemini_free_tier"),
        base_url=settings_service.ollama_url() if t["engine"] == "ollama" else None)
    return engine, key


def _read(engine, cfg: dict, case: dict, budget, cancelled) -> dict:
    """{"text", "cost_usd"} (text None when a hard-sub case could not be read the same twice) or
    {"over_budget": True, "cost_usd"}."""
    use_free_tier = cfg["engine"] == "gemini" and bool(settings_service.get("gemini_free_tier"))
    details = hardsub.load(case)
    if details is not None:
        return hardsub.read_case(engine, cfg, case, details, use_free_tier, budget, cancelled)
    if not case.get("input_filename"):
        raise InvalidInputError("This case has no input file.")
    path = os.path.join(db.BENCHMARK_DIR, os.path.basename(case["input_filename"]))
    if os.path.getsize(path) > page_import_limits.MAX_IMAGE_BYTES:
        raise InvalidInputError("This picture is over the size limit.")
    with open(path, "rb") as fh:
        content = fh.read(page_import_limits.MAX_IMAGE_BYTES + 1)
    read = image_text_service.read_stored_image(
        engine, cfg["engine"], cfg["model"], content, case.get("source_language") or "", use_free_tier, budget)
    return read if read.get("over_budget") else {"text": read["text"], "cost_usd": read["cost_usd"]}


def run_case(engine, cfg: dict, case: dict, api_key, budget, job_id: str, price=None) -> dict:
    """One vision read of a case's stored picture(s), as a result row; a read-then-translate arm also
    translates it. A failed read is this case's error (redacted), never the job's; a request whose
    worst-case price passes `budget` is not sent (`price(config, cases)` prices the translation). The calls
    run inside the job's cancel scope, so Cancel is seen mid-request and no retry follows it, and a Cancel
    between the read and the translation sends no translation."""
    started = time.monotonic()
    result = {"output_text": "", "cost_usd": 0.0}
    cancelled = lambda: background_jobs.is_cancel_requested(job_id)  # noqa: E731
    try:
        with llm_tasks.bounded_llm_calls(job_id, cancelled):
            read = _read(engine, cfg, case, budget, cancelled)
            result["cost_usd"] = read.get("cost_usd", 0.0)
            text = read.get("text")
            if read.get("over_budget"):
                result["over_budget"] = True
            elif text is None and hardsub.load(case) is not None:
                result.update(unreadable=True, error="Unreadable: the subtitle did not read the same in two pictures.")
            else:
                result.update(output_text=text, recognised_text=redact(text, api_key))
                if translate_arm(cfg) and text and not cancelled():
                    _translate(cfg, case, text, budget, result, price)
    except Exception as exc:
        result["error"] = redact(exc, api_key)
        result["cost_usd"] = getattr(exc, "cost_usd", 0.0)
    result["duration_seconds"] = time.monotonic() - started
    return result


def _translate(cfg: dict, case: dict, text: str, budget, result: dict, price) -> None:
    """The translation step of a read-then-translate arm, added to `result`."""
    t_engine, t_key = _translator(cfg)
    if budget is not None and price and result["cost_usd"] + price(cfg["translate"], [{"source_text": text}]) > budget:
        result["over_budget"] = True
        return
    r = run_translation(t_engine, dict(case, source_text=text), t_key)
    if (r["cost_usd"] or r["usage"]):
        usage = r["usage"]
        db.log_usage(None, cfg["translate"]["engine"], getattr(t_engine, "model", "") or "", "benchmark",
                     usage.get("input_tokens", 0) or 0, usage.get("output_tokens", 0) or 0, r["cost_usd"],
                     usage.get("cache_read_tokens", 0) or 0)
    result["cost_usd"] += r["cost_usd"]
    result["output_text"] = r["output_text"]
    if r.get("error"):
        result["error"] = redact(r["error"], t_key)


def score(stage: str, cfg: dict, r: dict, case: dict) -> tuple:
    """(score, metric, scorer) of a result. A hard-sub read is compared with every caption its time window
    could show; a read-then-translate arm is scored on the translation and keeps the read's own score in
    r["read_score"]; an unreadable case has no score."""
    language = case.get("source_language")
    if r.get("unreadable"):
        return None, "cer", None
    details = hardsub.load(case) if is_vision_arm(stage, cfg) else None
    if details is None:
        return score_output(stage, r.get("output_text") or "", case.get("reference_text"), language)
    if translate_arm(cfg):
        r["read_score"] = hardsub.window_score(r.get("recognised_text") or "", case, details)[0]
        return score_output("translation", r.get("output_text") or "", details.get("translation"), language)
    return hardsub.window_score(r.get("output_text") or "", case, details)


def unreadable_note(count: int):
    if not count:
        return None
    return f"{count} {'case was' if count == 1 else 'cases were'} unreadable and left out of the scores."
