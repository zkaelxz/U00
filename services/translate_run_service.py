"""
services/translate_run_service.py -- Streamlit-free, READ-ONLY half of the
per-drama Translate stage (tabs/workspace_tab.py's `with tab_translate:`
block). Migration Slice 39: get_translate_config() (everything the stage
needs to render its form) and estimate_translate_cost() (the pre-run cost
estimate / cap gating).

Migration Slice 40 adds start_translate_run(): a normal (non-bulk,
non-reflect) translation as a background job that does everything itself.

Step 97b adds an optional fallback chain to the start (see start_translate_run).

Migration Slice 41 adds reflect=True (Step 7's three-pass Reflect mode, live)
and bulk=True (Step 9/9d's Claude/Gemini batch APIs, DeepSeek off-peak, and
bulk Reflect) to the same start, plus resume_bulk_translations().

Parity X02/X22 add apply_workflow_tier() and save_translate_preset() (the
tab's "Apply tier" and "Save as preset" buttons); parity X03 adds
apply_translate_preset() ("Apply a preset" on an existing drama), and the
config's style presets carry their guidance text (X04).

Out of scope here: glossary review, preset rename/delete and characters CRUD.

Every knob (engine, model, context window, batch size, reflect, bulk, caps)
is a request-time parameter with the widget's own default as fallback -- no
new drama columns, so no db.py change. The API reads lines/config from the
DB, not unsaved browser state. D2: keys/secrets, client-supplied URLs and
the novel text are never returned -- booleans only.
"""
import inspect
import json
import os
import re
import sqlite3
from types import SimpleNamespace
from typing import Optional

import background_jobs
import bulk_translate
import core
import db
import translate_engines
import translation_guide
from services import library_service, settings_service, translate_service, workspace_job_service
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
    return settings_service.get_monthly_cap_usd()


def _cap_applies(engine_name: str, gemini_free_tier: bool = False) -> bool:
    return engine_name in _CAP_ENGINES and not (engine_name == "gemini" and gemini_free_tier)


def refuse_when_cap_spent(engine_name: str, gemini_free_tier: bool = False) -> None:
    """For a one-off LLM run with no per-run budget (glossary extraction,
    learn my style): UnsupportedOperationError when this month's spending
    cap is already used up and the engine is a capped one."""
    if not _cap_applies(engine_name, gemini_free_tier):
        return
    monthly = _monthly_cap()
    _cap, refusal = translate_engines.resolve_cost_cap(
        None, monthly, db.get_month_spend() if monthly else 0.0)
    if refusal:
        raise UnsupportedOperationError(refusal)


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
    free_tier = settings_service.get_gemini_free_tier()
    return {
        "drama_id": drama_id,
        "translation_engine": drama.get("translation_engine") or settings_service.get_default_engine(),
        "engines": translate_service.list_engines(),
        "style_presets": [{"key": k, "label": v["label"], "guidance": v["guidance"]}
                          for k, v in translation_guide.STYLE_PRESETS.items()],
        "default_style_preset": "novel" if is_novel else "audio_drama",
        "locales": list(LOCALES),
        "workflow_tiers": [
            {"key": k, "label": t["label"], "translation_engine": t["translation_engine"],
             "engine_model": t["engine_model"], "reflect": bool(t["reflect"]),
             "auto_qc": bool(t["auto_qc"])}
            for k, t in translate_engines.WORKFLOW_TIERS.items()],
        "defaults": get_translate_config_defaults(is_novel),
        "default_locale": settings_service.get_preference("default_locale"),
        "default_style_note": settings_service.get_preference("default_style_note"),
        "project_instructions": drama.get("project_instructions"),
        "series_instructions": drama.get("series_instructions"),
        "has_novel_reference": has_novel,
        "line_count": len(lines),
        "untranslated_count": bulk_translate.untranslated_line_count(drama_id),
        "last_translate_errors": _parse_errors(drama.get("last_translate_errors")),
        "previous_episode_summary_present": bool(drama.get("previous_episode_summary")),
        "monthly_cap_usd": monthly_cap,
        # Always the real spend: the form shows it with or without a cap.
        "month_spend": db.get_month_spend(),
        "cap_applies_by_engine": {name: _cap_applies(name, free_tier)
                                  for name in translate_engines.ENGINES},
        "bulk_supported_engines": [e for e in bulk_translate.BULK_ENGINES
                                   if not (e == "gemini" and free_tier)],
    }


def _default_model(engine_name: str) -> Optional[str]:
    param = inspect.signature(translate_engines.ENGINES[engine_name].__init__).parameters.get("model")
    return param.default if param is not None and param.default is not inspect.Parameter.empty else None


def estimate_translate_cost(drama_id: int, engine_name: str = None, model: str = None,
                            reflect: bool = False, force_retranslate: bool = False,
                            bulk: bool = False, gemini_free_tier: bool = None,
                            job_cost_cap_usd: float = None) -> dict:
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = _require_drama(drama_id)
    engine_name = engine_name or drama.get("translation_engine") or settings_service.get_default_engine()
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(f"Unknown translate engine {engine_name!r}.")
    if reflect and engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(f"{engine_name} can't run Reflect mode.")
    _require_offered_model(engine_name, model)
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError(f"{model} isn't available on Gemini's free tier.")
    if bulk and engine_name == "gemini" and gemini_free_tier:
        raise UnsupportedOperationError("Bulk mode needs Claude, Gemini (paid) or DeepSeek.")
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


def _summary_engine(ollama_url: Optional[str] = None, allow_paid: bool = True):
    """The episode-summary engine from Settings (default local Ollama, as
    `cli.py translate`); (None, None), so the summary is skipped, if it
    can't be built or a cloud pick has no key. Never fails the translation.
    allow_paid=False (an API caller without engines.paid) also skips a pick
    outside translate_engines.FREE_ENGINES: it would spend the owner's key."""
    choice = settings_service.get_preference("episode_summary_engine")
    if not allow_paid and choice not in translate_engines.FREE_ENGINES:
        return None, None
    try:
        if choice == "ollama":
            return translate_engines.get_engine(
                "ollama", None,
                base_url=ollama_url or settings_service.resolve_key("ollama_url") or None), choice
        api_key = translate_service.resolve_api_key(choice)
        if not api_key:
            return None, None
        return translate_engines.get_engine(
            choice, api_key, free_tier=choice == "gemini" and settings_service.get_gemini_free_tier()
        ), choice
    except Exception:
        return None, None


_OLLAMA_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")


def _is_safe_ollama_model(model) -> bool:
    """Ollama model names are only sent to the local Ollama server (never a
    download), and users pull their own, so any name of this shape is
    accepted: no whitespace, no "..", no leading slash."""
    return (isinstance(model, str) and _OLLAMA_MODEL_RE.fullmatch(model) is not None
            and ".." not in model)


def _require_offered_model(engine_name: str, model) -> None:
    """InvalidInputError unless `model` is None or one this engine offers
    (translate_service.list_engines, the same list preset saving checks);
    ollama takes any safe-shaped name (_is_safe_ollama_model); an engine
    without a model list allows only its own default. A free-form model
    string would otherwise reach the engine as is (nllb hands it to
    transformers.pipeline as a Hugging Face repo id)."""
    if model is None:
        return
    if engine_name == "ollama":
        if _is_safe_ollama_model(model):
            return
        raise InvalidInputError("That model isn't offered for this engine.")
    models = next((e["models"] for e in translate_service.list_engines()
                   if e["name"] == engine_name), None)
    allowed = models if models is not None else [_default_model(engine_name)]
    if not isinstance(model, str) or model not in allowed:
        raise InvalidInputError("That model isn't offered for this engine.")


def start_translate_run(drama_id: int, engine_name: str = None, model: str = None,
                        style_preset: str = None, style_note: str = "",
                        locale: str = "en-US", force_retranslate: bool = False,
                        context_window: int = None, context_window_ahead: int = None,
                        batch_size: int = None, line_ids: list = None,
                        gemini_free_tier: bool = None,
                        job_cost_cap_usd: float = None,
                        fallback_chain: list = None, reflect: bool = False,
                        bulk: bool = False, default_female_pronouns: bool = None,
                        include_genre_notes: bool = None,
                        allow_paid_summary: bool = True) -> dict:
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

    reflect (Slice 41): Step 7's three-pass Reflect mode through the same
    run_translate_job the tab uses (critiques saved as notes by line).
    bulk (Slice 41): submits through bulk_translate (Claude/Gemini batch API,
    DeepSeek off-peak schedule; with reflect, the three-stage bulk Reflect
    pipeline) inside job `bulk_translate_{id}`, which also polls the batch
    (each Reflect stage in turn) until applied, failed or cancelled; results
    are applied by line id by bulk_translate itself. Neither mode takes a
    fallback chain (Reflect calls call_llm_json, which FallbackEngine does
    not wrap; a batch is bound to one provider); bulk takes no line_ids.

    default_female_pronouns / include_genre_notes: the tab's "Default
    ambiguous pronouns to she/her" and "Include baihe/GL genre guidance"
    toggles (a preset's values, which the client holds; nothing links a
    drama to a preset in the DB). None means the tab's own widget defaults:
    she/her off, genre guidance on.

    NotFoundError (drama), InvalidInputError, UnsupportedOperationError
    (nothing to translate / cap refusal / mode not available for the
    engine), DependencyUnavailableError (no key), ConflictError (already
    running). gemini_free_tier None means the saved Settings value."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = _require_drama(drama_id)
    engine_name = engine_name or drama.get("translation_engine") or settings_service.get_default_engine()
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
    if fallback_chain and (reflect or bulk):
        raise InvalidInputError("A fallback chain only applies to a normal translation run.")
    if bulk and line_ids is not None:
        raise InvalidInputError("Bulk mode translates the whole drama; line_ids isn't supported.")
    if reflect and engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(f"{engine_name} can't run Reflect mode.")
    if bulk and (engine_name not in bulk_translate.BULK_ENGINES
                 or (engine_name == "gemini" and gemini_free_tier)):
        raise UnsupportedOperationError("Bulk mode needs Claude, Gemini (paid) or DeepSeek.")
    if bulk and reflect and engine_name == "deepseek":
        raise UnsupportedOperationError("Bulk Reflect needs Claude or Gemini's batch API.")

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
    chain_error = translate_engines.fallback_chain_error(c["engine"] for c in chain)
    if chain_error:
        raise InvalidInputError(chain_error)
    for c in chain:
        _require_offered_model(c["engine"], c["model"])
    monthly_cap = _monthly_cap()
    month_spend = db.get_month_spend() if monthly_cap else 0.0
    built, caps = [], []
    job_id = f"translate_{drama_id}"
    for c in chain:
        name = c["engine"]
        api_key = translate_service.resolve_api_key(name)
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

    if bulk:
        job_id = bulk_job_id(drama_id)
        if db.list_bulk_jobs(drama_id, statuses=db.BULK_PENDING_STATUSES + ("running",)):
            raise ConflictError("A bulk translation is already pending for this drama.")
        if engine_name != "deepseek" and caps[0] is not None:
            # DeepSeek off-peak runs as a normal run later and stops at the
            # cap; a submitted batch can't, so it's refused up front (as the tab).
            est = estimate_translate_cost(drama_id, engine_name, model, reflect=reflect,
                                          force_retranslate=force_retranslate, bulk=True,
                                          gemini_free_tier=gemini_free_tier,
                                          job_cost_cap_usd=job_cost_cap_usd)
            if est["estimate_above_cap"]:
                raise UnsupportedOperationError(
                    "Not submitted: a bulk batch can't be stopped part-way, and its estimate "
                    "is above your cap. Raise the cap, or run a normal translation.")
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
    glossary_terms, style_guidelines, _names = workspace_job_service.build_run_style_context(
        drama_id, drama, lines, style_preset,
        include_genre_notes=True if include_genre_notes is None else include_genre_notes,
        default_female_pronouns=default_female_pronouns)

    if force_retranslate and any(ln.en for ln in lines):
        db.save_line_history_snapshot(drama_id, lines, "before force re-translate")
    novel_reference = _load_novel_reference(drama_id, drama)
    if bulk:
        submit = _bulk_submitter(drama_id, drama, engines[0], engine_name, reflect,
                                 novel_reference, glossary_terms, style_guidelines,
                                 style_note or "", locale, style_preset, context_window,
                                 context_window_ahead, batch_size, force_retranslate,
                                 job_cost_cap_usd, series_id)
        started = background_jobs.start_job(
            job_id, run_bulk_translate_job, job_id, engines[0], engine_name, submit,
            monthly_cap or None,
            description=f"Bulk {'Reflect ' if reflect else ''}translation (drama #{drama_id})")
        if not started:
            raise ConflictError("A translation is already running for this drama.")
        return {"job_id": job_id, "drama_id": drama_id, "engine": engine_name,
                "model": getattr(engines[0], "model", model),
                "target_line_count": len(eligible), "fallback_engines": [],
                "reflect": reflect, "bulk": True}

    summary_engine, summary_choice = _summary_engine(allow_paid=allow_paid_summary)

    started = background_jobs.start_job(
        job_id, workspace_job_service.run_translate_job,
        job_id, drama_id, lines, engine, drama, style_note or "",
        novel_reference, force_retranslate, locale, glossary_terms,
        style_guidelines, engine_name, style_preset, context_window,
        settings_service.get_ollama_num_ctx_override() or None, reflect=reflect,
        cost_cap_usd=cost_cap,
        context_window_ahead=context_window_ahead, batch_size=batch_size,
        summary_engine=summary_engine, summary_engine_choice=summary_choice,
        summary_monthly_cap_usd=_monthly_cap() or None,
        target_ids=target_ids, gpu_touching=any(c["engine"] == "ollama" for c in chain),
        description=f"{'Reflect-mode t' if reflect else 'T'}ranslation (drama #{drama_id})")
    if not started:
        raise ConflictError("A translation is already running for this drama.")
    return {"job_id": job_id, "drama_id": drama_id, "engine": engine_name,
            "model": getattr(engines[0], "model", model), "target_line_count": len(eligible),
            "fallback_engines": [c["engine"] for c in chain[1:]],
            "reflect": reflect, "bulk": False}


def bulk_job_id(drama_id: int) -> str:
    return f"bulk_translate_{drama_id}"


def _bulk_submitter(drama_id, drama, engine, engine_name, reflect, novel_reference,
                    glossary_terms, style_guidelines, style_note, locale, style_preset,
                    context_window, context_window_ahead, batch_size, force_retranslate,
                    job_cost_cap_usd, series_id):
    """A zero-arg callable that submits what the tab's _start_bulk_translation
    / _start_bulk_reflect submit (same translate_args, context and character
    names) and returns the bulk job id. Called inside the job so the provider
    call never blocks the request. Lines are re-read from the DB at submit
    time so each carries its permanent id and current English."""
    def submit() -> int:
        lines = db.load_line_objects(drama_id)
        if reflect:
            return bulk_translate.submit_reflect_pipeline(
                drama_id, lines, engine, engine_name,
                {"style_note": style_note, "drama_meta": drama, "novel_reference": novel_reference,
                 "locale": locale, "glossary_terms": glossary_terms,
                 "style_guidelines": style_guidelines, "style_preset": style_preset},
                batch_size=batch_size, force_retranslate=force_retranslate)
        if engine_name == "deepseek":
            return bulk_translate.schedule_offpeak_translation(
                drama_id, lines, engine_name, getattr(engine, "model", ""),
                {"style_note": style_note, "locale": locale, "glossary_terms": glossary_terms,
                 "style_guidelines": style_guidelines, "style_preset": style_preset,
                 "context_window": context_window, "cost_cap_usd": job_cost_cap_usd or None,
                 "novel_reference": novel_reference},
                force_retranslate=force_retranslate)
        character_names = translation_guide.build_speaker_labels(
            db.list_characters_with_series_names(drama_id),
            db.list_series_characters(series_id) if series_id else [])
        context = translate_engines.build_translation_context(
            engine, drama, style_note=style_note, novel_reference=novel_reference, locale=locale,
            glossary_terms=glossary_terms, style_guidelines=style_guidelines)
        return bulk_translate.submit_bulk_translation(
            drama_id, lines, engine, engine_name, context,
            translate_args={"glossary_terms": glossary_terms, "style_preset": style_preset},
            force_retranslate=force_retranslate, context_window=context_window,
            context_window_ahead=context_window_ahead, batch_size=batch_size,
            character_names=character_names)
    return submit


_NEXT_REFLECT_STAGE = {"faithful": "reflect", "reflect": "expressive"}


def run_bulk_translate_job(job_id, engine, engine_name, submit, monthly_cap_usd=None):
    """Background job: submit, then poll inside this job until the batch is
    applied (bulk_translate.run_bulk_poller; results applied by line id).
    A bulk Reflect pipeline's later stages are submitted by bulk_translate
    as each prior stage applies; this follows each one in turn. Cancelling
    this job cancels the pending bulk job (at the provider when it can)."""
    if background_jobs.is_cancel_requested(job_id):
        return
    first_id = current = submit()
    provider = bulk_translate.make_provider(engine_name, engine)
    while True:
        bulk_translate.run_bulk_poller(job_id, current, provider=provider, engine=engine,
                                       interval=bulk_translate.POLL_INTERVAL_SECONDS,
                                       monthly_cap_usd=monthly_cap_usd)
        job = db.get_bulk_job(current) or {}
        if background_jobs.is_cancel_requested(job_id):
            if job.get("status") in db.BULK_PENDING_STATUSES + ("running",):
                bulk_translate.cancel_bulk_job(current, provider)
                job = db.get_bulk_job(current) or {}
            break
        nxt_stage = (_NEXT_REFLECT_STAGE.get(job.get("stage"))
                     if job.get("kind") == "reflect" and job.get("status") == "applied" else None)
        nxt = bulk_translate._sibling_stage_job(job["pipeline_id"], nxt_stage) if nxt_stage else None
        if not nxt:
            break
        current = nxt["id"]
    background_jobs.set_result(job_id, {
        "bulk_job_id": first_id, "final_bulk_job_id": current, "status": job.get("status"),
        "stage": job.get("stage"), "summary": job.get("result_summary"),
        "last_error": job.get("last_error")})
    if job.get("status") in ("failed", "auth_error"):
        raise RuntimeError(job.get("last_error") or "The bulk job failed.")


def resume_bulk_translations(drama_id: int) -> dict:
    """After a restart: starts a poller for each of this drama's pending
    bulk jobs (bulk_translate.resume_pending, the call the tab's Bulk jobs
    panel makes), with engines built from server-side keys only."""
    _require_drama(drama_id)

    def factory(engine_name, model):
        key = translate_service.resolve_api_key(engine_name)
        return translate_engines.get_engine(engine_name, key, model or None) if key else None
    out = bulk_translate.resume_pending(drama_id, factory, _monthly_cap() or None)
    return {"drama_id": drama_id,
            "jobs": [{"bulk_job_id": k, "state": v} for k, v in sorted(out.items())]}


_BULK_CANCELLABLE = ("submitting",) + db.BULK_PENDING_STATUSES


def _bulk_entry(job: dict) -> dict:
    """Public view of one bulk_jobs row: no prompts, no translate_args, no
    provider batch id, no raw error text beyond the redacted last_error."""
    summary = job.get("result_summary")
    err = job.get("last_error")
    return {
        "bulk_job_id": job["id"], "engine": job["engine"], "model": job.get("model"),
        "kind": job.get("kind") or "translate", "stage": job.get("stage"),
        "pipeline_id": job.get("pipeline_id"), "status": job["status"],
        "pending": job["status"] in db.BULK_PENDING_STATUSES + ("submitting", "running"),
        "cancellable": job["status"] in _BULK_CANCELLABLE,
        "line_count": db.count_bulk_job_lines(job["id"]),
        "scheduled_for": job.get("scheduled_for"),
        "result_summary": summary if isinstance(summary, dict) else None,
        "last_error": translate_engines.redact_secrets(err) if err else None,
        "submitted_at": job.get("submitted_at"), "updated_at": job.get("updated_at"),
    }


def list_bulk_translations(drama_id: int) -> dict:
    """Read-only: this drama's bulk jobs (newest first) with the status
    last recorded in the database. Never contacts a provider; polling stays
    with resume_bulk_translations."""
    _require_drama(drama_id)
    return {"drama_id": drama_id,
            "jobs": [_bulk_entry(j) for j in db.list_bulk_jobs(drama_id)]}


def cancel_bulk_translation(drama_id: int, bulk_job_id: int) -> dict:
    """The tab's Cancel button: stops polling, marks the job cancelled and
    asks the provider to cancel when a server-side key exists (best effort,
    same as bulk_translate.cancel_bulk_job)."""
    _require_drama(drama_id)
    job = db.get_bulk_job(bulk_job_id)
    if not job or job["drama_id"] != drama_id:
        raise NotFoundError(f"Bulk job {bulk_job_id} not found for drama {drama_id}.")
    if job["status"] not in _BULK_CANCELLABLE:
        raise ConflictError(f"Bulk job {bulk_job_id} is {job['status']} and cannot be cancelled.")
    key = translate_service.resolve_api_key(job["engine"])
    engine = translate_engines.get_engine(job["engine"], key, job.get("model") or None) if key else None
    provider = bulk_translate.make_provider(job["engine"], engine) if engine else None
    note = bulk_translate.cancel_bulk_job(bulk_job_id, provider)
    return {"drama_id": drama_id, "bulk_job": _bulk_entry(db.get_bulk_job(bulk_job_id)),
            "message": note}


# ---------------------------------------------------------------------------
# Parity X02/X22: apply a workflow tier, save the current settings as a preset.
# ---------------------------------------------------------------------------

def apply_workflow_tier(drama_id: int, tier: str) -> dict:
    """tabs/workspace_tab.py apply_workflow_tier: the tier's engine goes onto
    the drama row (the only field with a per-drama DB home); the model,
    Reflect and Auto QC are returned for the client to put into its form,
    which Streamlit did through session_state. Starts nothing."""
    drama = _require_drama(drama_id)
    t = translate_engines.WORKFLOW_TIERS.get(tier) if isinstance(tier, str) else None
    if t is None:
        raise InvalidInputError("Unknown workflow tier.")
    if drama.get("translation_engine") != t["translation_engine"]:
        db.update_drama(drama_id, translation_engine=t["translation_engine"])
    return {"drama_id": drama_id, "tier": tier, "label": t["label"],
            "translation_engine": t["translation_engine"], "engine_model": t["engine_model"],
            "reflect": bool(t["reflect"]), "auto_qc": bool(t["auto_qc"])}


def apply_translate_preset(drama_id: int, preset_id: int) -> dict:
    """tabs/workspace_tab.py "Apply a preset" on an existing drama (parity
    X03): the preset's engine (if it saved one) goes onto the drama row,
    the one field with a per-drama DB home; style, locale, the two toggles
    and the model are returned for the client's form, as Streamlit's
    apply_preset_to_session put them in session_state. Starts nothing.
    Raises NotFoundError (unknown drama or preset)."""
    _require_drama(drama_id)
    preset = next((p for p in db.list_presets() if p["id"] == preset_id), None)
    if preset is None:
        raise NotFoundError(f"No preset with id {preset_id}.")
    engine = preset.get("translation_engine")
    if engine not in translate_engines.ENGINES:
        engine = None   # an engine this build no longer has: leave the drama's own
    if engine:
        db.update_drama(drama_id, translation_engine=engine)
    style = preset.get("style_preset")
    locale = preset.get("locale")
    pronouns = preset.get("default_female_pronouns")
    genre = preset.get("include_genre_notes")
    return {
        "drama_id": drama_id, "preset_id": preset["id"], "name": preset["name"],
        "translation_engine": engine,
        "engine_model": (preset.get("engine_model") or None) if engine else None,
        "style_preset": style if style in translation_guide.STYLE_PRESETS else None,
        "locale": locale if locale in LOCALES else None,
        "default_female_pronouns": bool(pronouns),
        "include_genre_notes": True if genre is None else bool(genre),
    }


def dismiss_translate_errors(drama_id: int) -> dict:
    """tabs/workspace_tab.py "Dismiss this notice": clears only the drama's
    persisted record of the last run's failed batches
    (db.update_drama(last_translate_errors=None)); lines are untouched, so a
    later "Translate all lines" still retries the missing ones. NotFoundError
    for an unknown drama; `dismissed` is False when there was nothing to clear."""
    drama = _require_drama(drama_id)
    had = bool(drama.get("last_translate_errors"))
    if had:
        db.update_drama(drama_id, last_translate_errors=None)
    return {"drama_id": drama_id, "dismissed": had}


_PRESET_NAME_MAX = 100   # services/library_service._clean_name's limit


def save_translate_preset(name: str, translation_engine: str, engine_model: Optional[str] = None,
                          style_preset: Optional[str] = None, locale: Optional[str] = None,
                          default_female_pronouns: bool = False,
                          include_genre_notes: bool = True, overwrite: bool = False) -> dict:
    """tabs/workspace_tab.py "Save as preset": db.save_preset with the
    Translate form's engine, model, style, locale and the two toggles.
    Streamlit silently replaced a preset of the same name; here that needs
    overwrite=True (otherwise ConflictError). Replacing is effectively a
    delete, so the route only allows overwrite from the PC. engine_model is None for engines without a
    model picker, and None (engine default) is allowed for the others."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > _PRESET_NAME_MAX:
        raise InvalidInputError(f"A preset name is 1-{_PRESET_NAME_MAX} characters.")
    if translation_engine not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown translation engine.")
    models = next((e["models"] for e in translate_service.list_engines()
                   if e["name"] == translation_engine), None)
    if engine_model is not None and not (
            _is_safe_ollama_model(engine_model) if translation_engine == "ollama"
            else models is not None and engine_model in models):
        raise InvalidInputError("That model isn't offered for this engine.")
    if style_preset is not None and style_preset not in translation_guide.STYLE_PRESETS:
        raise InvalidInputError("Unknown style preset.")
    if locale is not None and locale not in LOCALES:
        raise InvalidInputError("Unknown English variant.")
    fields = dict(translation_engine=translation_engine, engine_model=engine_model,
                  style_preset=style_preset, locale=locale,
                  default_female_pronouns=bool(default_female_pronouns),
                  include_genre_notes=bool(include_genre_notes))
    # Insert-only first: the UNIQUE(name) constraint decides, not a prior
    # lookup, so two concurrent saves can't both think the name is free.
    try:
        preset_id, replaced = db.insert_preset(name, **fields), False
    except sqlite3.IntegrityError:
        if not overwrite:
            raise ConflictError("A preset with that name already exists; save again with "
                                "overwrite to replace it.")
        preset_id, replaced = db.save_preset(name, **fields), True
    preset = next(p for p in library_service.list_presets() if p["id"] == preset_id)
    return {"preset": preset, "replaced": replaced}
