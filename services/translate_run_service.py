"""
services/translate_run_service.py -- Streamlit-free, READ-ONLY half of the
per-drama Translate stage (tabs/workspace_tab.py's `with tab_translate:`
block). Migration Slice 39: get_translate_config() (everything the stage
needs to render its form) and estimate_translate_cost() (the pre-run cost
estimate / cap gating).

Migration Slice 40 adds start_translate_run(): a normal (non-bulk,
non-reflect) translation as a background job that does everything itself.

Step 97b adds an optional fallback chain to the start (see start_translate_run).

Out of scope here: bulk/Reflect runs (Slice 41), glossary review, style presets CRUD and characters CRUD.

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

import adaptive_style
import background_jobs
import bulk_translate
import core
import db
import emotion
import translate_engines
import translation_guide
from services import settings_service, translate_service, workspace_job_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError,
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


def get_translate_config_defaults(is_novel: bool) -> dict:
    return {"context_window": 10 if is_novel else 6,
            "context_window_ahead": 6 if is_novel else 3,
            "batch_size": 30 if is_novel else 20}


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
        "defaults": get_translate_config_defaults(is_novel),
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


def _load_novel_reference(drama_id: int, drama: dict) -> Optional[str]:
    filename = drama.get("novel_reference_filename")
    if not filename:
        return None
    path = os.path.join(db.drama_dir(drama_id), filename)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _summary_engine():
    """Same default as `cli.py translate`: local Ollama; None (summary
    skipped) if it can't be built. Never fails the translation."""
    try:
        return translate_engines.get_engine(
            "ollama", None, base_url=settings_service.resolve_key("ollama_url") or None), "ollama"
    except Exception:
        return None, None


def start_translate_run(drama_id: int, engine_name: str = None, model: str = None,
                        style_preset: str = None, style_note: str = "",
                        locale: str = "en-US", force_retranslate: bool = False,
                        context_window: int = None, context_window_ahead: int = None,
                        batch_size: int = None, line_ids: list = None,
                        gemini_free_tier: bool = False,
                        job_cost_cap_usd: float = None,
                        fallback_chain: list = None) -> dict:
    """Starts a normal translation (single pass; not bulk, not Reflect) as a
    background job that does everything, DB write included: field-scoped
    `en` writes by permanent line id (run_translate_job), then the shared
    finish_translation_run. Builds the same glossary / style guidelines /
    character names / locale as the Workspace button and `cli.py translate`.
    line_ids (optional) restricts the run to those lines; the rest are only
    context. Only empty-`en` lines are translated unless force_retranslate,
    so hand-edited translations survive (as in the tab).

    fallback_chain (Step 97b): optional ordered [{"engine", "model"}, ...] tried
    in turn -- for the rest of the run -- when the active engine fails with an
    auth error, rate limit, timeout or connection error (never a moderation
    refusal or a generic exception). The whole chain must be one class:
    all instruction-following or all TRANSLATION_ONLY_ENGINES, no duplicates.
    Each engine has its own cost cap and spend; the job result's
    "fallbacks" lists any switch that happened.

    NotFoundError (drama), InvalidInputError, UnsupportedOperationError
    (nothing to translate / monthly cap reached), DependencyUnavailableError
    (no key), ConflictError (already running)."""
    drama = _require_drama(drama_id)
    engine_name = engine_name or drama.get("translation_engine") or "claude"
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown translate engine.")
    if locale not in LOCALES:
        raise InvalidInputError("Unknown English variant.")
    is_novel = drama.get("content_mode") == "novel_narration"
    style_preset = style_preset or ("novel" if is_novel else "audio_drama")
    if style_preset not in translation_guide.STYLE_PRESETS:
        raise InvalidInputError("Unknown style preset.")
    if job_cost_cap_usd is not None and job_cost_cap_usd < 0:
        raise InvalidInputError("job_cost_cap_usd can't be negative.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    defaults = get_translate_config_defaults(is_novel)
    context_window = defaults["context_window"] if context_window is None else context_window
    context_window_ahead = (defaults["context_window_ahead"]
                            if context_window_ahead is None else context_window_ahead)
    batch_size = defaults["batch_size"] if batch_size is None else batch_size
    if min(context_window, context_window_ahead) < 0 or batch_size < 1:
        raise InvalidInputError("Context window and batch size are out of range.")

    lines = core.lines_from_rows(db.load_lines(drama_id))
    if not lines:
        raise UnsupportedOperationError("This drama has no lines to translate yet.")
    target_ids = None
    if line_ids is not None:
        target_ids = set(line_ids)
        if not target_ids or not target_ids <= {ln.id for ln in lines}:
            raise InvalidInputError("line_ids must be a non-empty list of this drama's line ids.")
    eligible = [ln for ln in lines if (ln.zh or "").strip()
                and (force_retranslate or not (ln.en or "").strip())
                and (target_ids is None or ln.id in target_ids)]
    if not eligible:
        raise UnsupportedOperationError("There are no lines to translate.")

    chain = [{"engine": engine_name, "model": model}] + [
        {"engine": f["engine"], "model": f.get("model")} for f in (fallback_chain or [])]
    if len({c["engine"] for c in chain}) != len(chain):
        raise InvalidInputError("A fallback chain can't repeat an engine.")
    if len(chain) > 1:
        if any(c["engine"] not in translate_engines.ENGINES for c in chain):
            raise InvalidInputError("Unknown translate engine.")
        if len({c["engine"] in translate_engines.TRANSLATION_ONLY_ENGINES
                for c in chain}) > 1:
            raise InvalidInputError(
                "A fallback chain can't mix instruction-following engines with "
                "translation-only ones.")
    monthly_cap = _monthly_cap()
    month_spend = db.get_month_spend() if monthly_cap else 0.0
    built, caps = [], []
    job_id = f"translate_{drama_id}"
    for c in chain:
        name = c["engine"]
        api_key = translate_service._resolve_api_key(name)
        if api_key is None and name != "nllb":
            raise DependencyUnavailableError(
                f"No {name} key is configured. Set one in Settings first.")
        free_tier = name == "gemini" and gemini_free_tier
        if free_tier and c["model"] in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS:
            raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
        cap = None
        if _cap_applies(name, gemini_free_tier):
            cap, refusal = translate_engines.resolve_cost_cap(
                job_cost_cap_usd, monthly_cap, month_spend)
            if refusal:
                raise UnsupportedOperationError(refusal)
        caps.append(cap)
        built.append((name, api_key, c["model"], free_tier))

    if background_jobs.is_running(job_id):
        raise ConflictError("A translation is already running for this drama.")

    engines = [translate_engines.get_engine(
        name, key, mdl, free_tier=free_tier,
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if name == "ollama" else None) for name, key, mdl, free_tier in built]
    if len(engines) > 1:
        engine = translate_engines.FallbackEngine(engines, [c["engine"] for c in chain], caps)
        cost_cap = None  # each engine's own cap is enforced by the FallbackEngine
    else:
        engine, cost_cap = engines[0], caps[0]

    series_id = drama.get("series_id")
    glossary_terms = db.list_glossary_terms(series_id) if series_id else None
    series_chars = db.list_series_characters(series_id) if series_id else []
    drama_chars = db.list_characters_with_series_names(drama_id)
    prof = db.get_style_profile(f"series:{series_id}" if series_id else "global")
    learned = adaptive_style.profile_to_prompt_block(prof.get("profile", {})) if prof else ""
    emap = db.load_emotions(drama_id)
    emotion_block = emotion.build_emotion_guidance(emap, [ln.idx for ln in lines]) if emap else ""
    style_guidelines = translation_guide.build_style_guidelines(
        style_preset, glossary_terms=glossary_terms,
        custom_notes="\n\n".join(b for b in (
            learned, emotion_block,
            translation_guide.build_character_gender_hints(series_chars, drama_chars)) if b))

    if force_retranslate and any(ln.en for ln in lines):
        db.save_line_history_snapshot(drama_id, lines, "before force re-translate")
    summary_engine, summary_choice = _summary_engine()

    started = background_jobs.start_job(
        job_id, workspace_job_service.run_translate_job,
        job_id, drama_id, lines, engine, drama, style_note or "",
        _load_novel_reference(drama_id, drama), force_retranslate, locale, glossary_terms,
        style_guidelines, engine_name, style_preset, context_window,
        None, reflect=False, cost_cap_usd=cost_cap,
        context_window_ahead=context_window_ahead, batch_size=batch_size,
        summary_engine=summary_engine, summary_engine_choice=summary_choice,
        target_ids=target_ids, gpu_touching=any(c["engine"] == "ollama" for c in chain),
        description=f"Translation (drama #{drama_id})")
    if not started:
        raise ConflictError("A translation is already running for this drama.")
    return {"job_id": job_id, "drama_id": drama_id, "engine": engine_name,
            "model": getattr(engines[0], "model", model), "target_line_count": len(eligible),
            "fallback_engines": [c["engine"] for c in chain[1:]]}
