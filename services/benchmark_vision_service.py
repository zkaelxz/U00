"""
services/benchmark_vision_service.py -- vision engines (Claude, Gemini, OpenAI,
Ollama) as arms of the Benchmark Lab's OCR stage: a model check shared with
translation arms, the worst-case estimate and one read of a case's stored
picture. UI-free. The run loop, cap and result rows stay in benchmark_lab_service.
"""
import os
import time

import db
import translate_engines
from engine_backends.vision import VISION_ENGINES
from services import image_text_service, settings_service, translate_service
from services.benchmark_case_service import redact
from services.service_errors import InvalidInputError, MissingKeyError


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


def checked_vision_config(engine: str, model) -> dict:
    entry = next((e for e in translate_service.list_engines() if e["name"] == engine), None)
    if entry is None:
        raise InvalidInputError("Unknown translate engine.")
    return {"engine": engine, "model": checked_model(entry, engine, model)}


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
        keys.append(key)
    return keys


def needs_gpu(stage: str, configs: list) -> bool:
    """Whether a run may touch the GPU before Ollama is considered: any local recogniser does."""
    return stage != "translation" and not all(is_vision_arm(stage, c) for c in configs)


def estimate_cost(stage: str, cfg: dict, cases: list) -> float:
    """Worst-case cost of reading every case's picture with a vision arm; 0 for any other arm."""
    if not is_vision_arm(stage, cfg):
        return 0.0
    total = 0.0
    for case in cases:
        width, height = _case_image_size(case)
        total += image_text_service.estimate(width, height, cfg["engine"], cfg["model"])["estimated_usd"] or 0.0
    return total


def run_case(engine, cfg: dict, case: dict, api_key, budget) -> dict:
    """One vision read of a case's stored picture, as a result row. A failed read is this case's
    error (redacted), never the job's; a read whose worst-case price passes `budget` is not sent."""
    started = time.monotonic()
    result = {"output_text": "", "cost_usd": 0.0}
    try:
        if not case.get("input_filename"):
            raise InvalidInputError("This case has no input file.")
        with open(os.path.join(db.BENCHMARK_DIR, case["input_filename"]), "rb") as fh:
            content = fh.read()
        use_free_tier = cfg["engine"] == "gemini" and bool(settings_service.get("gemini_free_tier"))
        read = image_text_service.read_stored_image(
            engine, cfg["engine"], cfg["model"], content, case.get("source_language") or "",
            use_free_tier, budget)
        if read.get("over_budget"):
            result["over_budget"] = True
        else:
            result.update(output_text=read["text"], cost_usd=read["cost_usd"])
    except Exception as exc:
        result["error"] = redact(exc, api_key)
        result["cost_usd"] = getattr(exc, "cost_usd", 0.0)
    result["duration_seconds"] = time.monotonic() - started
    return result
