"""
services/translate_run_service.py -- Streamlit-free, READ-ONLY half of the
per-drama Translate stage (tabs/workspace_tab.py's `with tab_translate:`
block). Migration Slice 39: get_translate_config() (everything the stage
needs to render its form) and estimate_translate_cost() (the pre-run cost
estimate / cap gating).

Out of scope here: the start-translate job (Slice 40), bulk/Reflect runs
(Slice 41), glossary review, style presets CRUD and characters CRUD.

Every knob (engine, model, context window, batch size, reflect, bulk, caps)
is a request-time parameter with the widget's own default as fallback -- no
new drama columns, so no db.py change. The API reads lines/config from the
DB, not unsaved browser state. D2: keys/secrets, client-supplied URLs and
the novel text are never returned -- booleans only.
"""
import inspect
import json
import os
from types import SimpleNamespace
from typing import Optional

import bulk_translate
import db
import translate_engines
import translation_guide
from services import settings_service, translate_service
from services.service_errors import (InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)

# tabs/workspace_tab.py's "English variant" selectbox options.
LOCALES = ["en-US", "en-GB", "en-AU"]

# tabs/workspace_tab.py's _cap_applies: engines that report usage.
_CAP_ENGINES = ("claude", "deepseek", "gemini", "google", "deepl")


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    return drama


def _monthly_cap() -> float:
    raw = settings_service.resolve_key("monthly_cap_usd")
    try:
        return max(0.0, float(raw)) if raw else 0.0
    except (TypeError, ValueError):
        return 0.0


def _cap_applies(engine_name: str, gemini_free_tier: bool = False) -> bool:
    return engine_name in _CAP_ENGINES and not (engine_name == "gemini" and gemini_free_tier)


def _parse_errors(raw) -> Optional[list]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def get_translate_config(drama_id: int) -> dict:
    drama = _require_drama(drama_id)
    is_novel = drama.get("content_mode") == "novel_narration"
    filename = drama.get("novel_reference_filename")
    has_novel = bool(filename) and os.path.isfile(
        os.path.join(db.DRAMAS_DIR, str(drama_id), filename))
    lines = db.load_lines(drama_id)
    monthly_cap = _monthly_cap()
    return {
        "drama_id": drama_id,
        "translation_engine": drama.get("translation_engine") or "claude",
        "engines": translate_service.list_engines(),
        "style_presets": [{"key": k, "label": v["label"]}
                          for k, v in translation_guide.STYLE_PRESETS.items()],
        "default_style_preset": "novel" if is_novel else "audio_drama",
        "locales": list(LOCALES),
        "workflow_tiers": [
            {"key": k, "label": t["label"], "translation_engine": t["translation_engine"],
             "engine_model": t["engine_model"], "reflect": bool(t["reflect"]),
             "auto_qc": bool(t["auto_qc"])}
            for k, t in translate_engines.WORKFLOW_TIERS.items()],
        "defaults": {"context_window": 10 if is_novel else 6,
                     "context_window_ahead": 6 if is_novel else 3,
                     "batch_size": 30 if is_novel else 20},
        "project_instructions": drama.get("project_instructions"),
        "series_instructions": drama.get("series_instructions"),
        "has_novel_reference": has_novel,
        "line_count": len(lines),
        "untranslated_count": bulk_translate.untranslated_line_count(drama_id),
        "last_translate_errors": _parse_errors(drama.get("last_translate_errors")),
        "previous_episode_summary_present": bool(drama.get("previous_episode_summary")),
        "monthly_cap_usd": monthly_cap,
        "month_spend": db.get_month_spend() if monthly_cap else 0.0,
        "cap_applies_by_engine": {name: _cap_applies(name)
                                  for name in translate_engines.ENGINES},
        "bulk_supported_engines": list(bulk_translate.BULK_ENGINES),
    }


def _default_model(engine_name: str) -> Optional[str]:
    param = inspect.signature(translate_engines.ENGINES[engine_name].__init__).parameters.get("model")
    return param.default if param is not None and param.default is not inspect.Parameter.empty else None


def estimate_translate_cost(drama_id: int, engine_name: str = None, model: str = None,
                            reflect: bool = False, force_retranslate: bool = False,
                            bulk: bool = False, gemini_free_tier: bool = False,
                            job_cost_cap_usd: float = None) -> dict:
    drama = _require_drama(drama_id)
    engine_name = engine_name or drama.get("translation_engine") or "claude"
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(f"Unknown translate engine {engine_name!r}.")
    if reflect and engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(f"{engine_name} can't run Reflect mode.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError(f"{model} isn't available on Gemini's free tier.")
    if job_cost_cap_usd is not None and job_cost_cap_usd < 0:
        raise InvalidInputError("job_cost_cap_usd can't be negative.")

    free_tier = engine_name == "gemini" and gemini_free_tier
    resolved_model = model or _default_model(engine_name)
    cap_applies = _cap_applies(engine_name, gemini_free_tier)
    free = engine_name in translate_engines.FREE_ENGINES or free_tier

    lines = db.load_lines(drama_id)
    targets = [ln for ln in lines if (ln.get("zh") or "").strip()
               and (force_retranslate or not (ln.get("en") or "").strip())]

    estimated = None
    if targets:
        # The helpers only read name/model/free_tier, so a stand-in avoids
        # constructing a real engine (SDK import, key, network).
        stand_in = SimpleNamespace(name=engine_name, model=resolved_model, free_tier=free_tier)
        fn = (translate_engines.estimate_reflect_mode_cost if reflect
              else translate_engines.estimate_translation_cost)
        estimated = float(fn(stand_in, [ln["zh"] for ln in targets]))
        if bulk and not reflect and cap_applies:
            estimated *= bulk_translate.BATCH_PRICE_FACTOR

    monthly_cap = _monthly_cap() if cap_applies else 0.0
    spend = db.get_month_spend() if monthly_cap else 0.0
    effective_cap = None
    monthly_refusal = False
    if cap_applies:
        effective_cap, refusal = translate_engines.resolve_cost_cap(
            job_cost_cap_usd, monthly_cap, spend)
        monthly_refusal = bool(refusal)

    return {
        "engine": engine_name,
        "model": resolved_model,
        "estimated_usd": estimated,
        "target_line_count": len(targets),
        "free": free,
        "cap_applies": cap_applies,
        "effective_cap_usd": effective_cap,
        "monthly_refusal": monthly_refusal,
        "estimate_above_cap": bool(estimated is not None and effective_cap is not None
                                   and estimated > effective_cap),
    }
