"""
services/translate_run_service.py -- the per-drama Translate stage.
get_translate_config() (everything the stage
needs to render its form) and estimate_translate_cost() (the pre-run cost
estimate / cap gating).

It also provides start_translate_run(): a normal (non-bulk,
non-reflect) translation as a background job that does everything itself.

The start takes an optional fallback chain (see start_translate_run).

The start also takes reflect=True (three-pass Reflect mode, live)
and bulk=True (Claude/Gemini batch APIs, DeepSeek off-peak, and
bulk Reflect), plus resume_bulk_translations().

Parity X02/X22 add apply_workflow_tier() and save_translate_preset() ("Apply
tier" and "Save as preset"); parity X03 adds
apply_translate_preset() ("Apply a preset" on an existing drama), and the
config's style presets carry their guidance text (X04).

Out of scope here: glossary review, preset rename/delete and characters CRUD.

Every knob (engine, model, context window, batch size, reflect, bulk, caps)
is a request-time parameter with a built-in default as fallback -- none is
stored on the drama. The API reads lines/config from the
DB, not unsaved browser state. D2: keys/secrets, client-supplied URLs and
the novel text are never returned -- booleans only.
"""
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
from engine_backends.engine_registry import legacy_ids
from services import (engine_routing_service, library_service, settings_service,
                      translate_service, workspace_job_service)
from services.service_errors import (
    ConflictError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    UnsupportedOperationError,
)

# Engines that report usage, so the spending cap applies to them.
_CAP_ENGINES = ("claude", "deepseek", "gemini", "openai")


def require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if not drama:
        raise NotFoundError(f"Drama {drama_id} not found.")
    return drama


def month_cap_usd() -> float:
    return settings_service.get_monthly_cap_usd()


def engine_cap_applies(engine_name: str, gemini_free_tier: bool = False) -> bool:
    return engine_name in _CAP_ENGINES and not (engine_name == "gemini" and gemini_free_tier)


def refuse_when_cap_spent(engine_name: str, gemini_free_tier: bool = False) -> None:
    """For a one-off LLM run with no per-run budget (glossary extraction,
    learn my style): UnsupportedOperationError when this month's spending
    cap is already used up and the engine is a capped one."""
    if not engine_cap_applies(engine_name, gemini_free_tier):
        return
    monthly = month_cap_usd()
    _cap, refusal = translate_engines.resolve_cost_cap(
        None, monthly, db.get_month_spend() if monthly else 0.0)
    if refusal:
        raise UnsupportedOperationError(refusal)


# Claude replies are capped at 4000 output tokens; past this the JSON reply risks
# being cut off and the batch coming back empty.
MAX_BATCH_SIZE = 60


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
    drama = require_drama(drama_id)
    is_novel = drama.get("content_mode") == "novel_narration"
    filename = drama.get("novel_reference_filename")
    has_novel = bool(filename) and os.path.isfile(
        os.path.join(db.DRAMAS_DIR, str(drama_id), filename))
    lines = db.load_lines(drama_id)
    monthly_cap = month_cap_usd()
    free_tier = settings_service.get_gemini_free_tier()
    engine_name = (drama.get("translation_engine")
                   or engine_routing_service.resolve_capability("translation.cheap"))
    return {
        "drama_id": drama_id,
        "translation_engine": engine_name,
        "engines": translate_service.list_engines(),
        "style_presets": [{"key": k, "label": v["label"], "guidance": v["guidance"]}
                          for k, v in translation_guide.STYLE_PRESETS.items()],
        "default_style_preset": "novel" if is_novel else "audio_drama",
        "locales": list(settings_service.LOCALE_CHOICES),
        "workflow_tiers": [
            {"key": k, "label": t["label"], "translation_engine": t["translation_engine"],
             "engine_model": t["engine_model"], "reflect": bool(t["reflect"]),
             "auto_qc": bool(t["auto_qc"])}
            for k in translate_engines.WORKFLOW_TIERS
            for t in [translate_engines.effective_tier(k)]],
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
        # The form shows it as "X of $cap" (what the cap counts, since any
        # reset) or, with no cap, as the month's real spend.
        "month_spend": db.get_month_spend(since_reset=monthly_cap > 0),
        "cap_applies_by_engine": {name: engine_cap_applies(name, free_tier)
                                  for name in translate_engines.ENGINES},
        "bulk_supported_engines": [e for e in bulk_translate.BULK_ENGINES
                                   if not (e == "gemini" and free_tier)],
        # Probed only when the drama translates with Ollama; None = not checked.
        "ollama_reachable": ollama_reachable() if engine_name == "ollama" else None,
        "default_female_pronouns": _saved_bool(drama.get("default_female_pronouns")),
        "include_genre_notes": _saved_bool(drama.get("include_genre_notes")),
    }


def _saved_bool(value) -> Optional[bool]:
    return None if value is None else bool(value)


def ollama_reachable() -> bool:
    """Parity X24: whether the configured Ollama server answers (the
    "Can't reach Ollama" warning). A boolean only; the URL never leaves
    the server. Cached briefly by translate_engines."""
    return bool(translate_engines.check_ollama_reachable(
        settings_service.resolve_key("ollama_url") or "http://localhost:11434"))


def _default_model(engine_name: str) -> Optional[str]:
    return translate_engines.effective_default_model(engine_name)


def validate_run_options(engine_name: str, model, *, locale: str, style_preset: str,
                         context_window: int, context_window_ahead: int, batch_size: int,
                         job_cost_cap_usd, gemini_free_tier: bool) -> None:
    """The checks on a translate run's own options, shared by
    start_translate_run and `cli.py translate` so both refuse the same
    values. Takes resolved values (defaults already applied). InvalidInputError
    / UnsupportedOperationError."""
    if locale not in settings_service.LOCALE_CHOICES:
        raise InvalidInputError("Unknown English variant.")
    if style_preset not in translation_guide.STYLE_PRESETS:
        raise InvalidInputError("Unknown style preset.")
    if job_cost_cap_usd is not None and job_cost_cap_usd < 0:
        raise InvalidInputError("job_cost_cap_usd can't be negative.")
    _require_offered_model(engine_name, model)
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    if min(context_window, context_window_ahead) < 0 or batch_size < 1:
        raise InvalidInputError("Context window and batch size are out of range.")
    if batch_size > MAX_BATCH_SIZE:
        raise InvalidInputError(f"Batch size can't be more than {MAX_BATCH_SIZE}: a larger "
                                f"batch's reply can be cut off by the engine's output limit.")


def estimate_translate_cost(drama_id: int, engine_name: str = None, model: str = None,
                            reflect: bool = False, force_retranslate: bool = False,
                            bulk: bool = False, gemini_free_tier: bool = None,
                            job_cost_cap_usd: float = None, line_ids=None) -> dict:
    """line_ids (optional): estimate only these lines (of those the run
    would translate)."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = require_drama(drama_id)
    engine_name = (engine_name or drama.get("translation_engine")
                   or engine_routing_service.resolve_capability("translation.cheap"))
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
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
    cap_applies = engine_cap_applies(engine_name, gemini_free_tier)
    free = engine_name in translate_engines.FREE_ENGINES or free_tier

    lines = db.load_lines(drama_id)
    targets = [ln for ln in lines if (ln.get("zh") or "").strip()
               and (force_retranslate or not (ln.get("en") or "").strip())
               and (line_ids is None or ln.get("id") in line_ids)]

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

    monthly_cap = month_cap_usd() if cap_applies else 0.0
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


def load_novel_reference(drama_id: int, drama: dict) -> Optional[str]:
    filename = drama.get("novel_reference_filename")
    if not filename:
        return None
    path = os.path.join(db.drama_dir(drama_id), filename)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def pick_summary_engine(ollama_url: Optional[str] = None, allow_paid: bool = True):
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
    except Exception as exc:
        from applog import get_logger
        get_logger().warning("Episode summary engine %s could not be built: %s", choice,
                             translate_engines.redact_secrets(str(exc)))
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
    string would otherwise reach the engine as is."""
    if model is None:
        return
    if engine_name == "ollama":
        if _is_safe_ollama_model(model):
            return
        raise InvalidInputError("That model isn't offered for this engine.")
    models = next((e["models"] for e in translate_service.list_engines()
                   if e["name"] == engine_name), None)
    allowed = models if models is not None else [
        translate_engines.builtin_default_model(engine_name), _default_model(engine_name)]
    if not isinstance(model, str) or model not in (*allowed, *legacy_ids(engine_name)):
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
                        allow_paid_summary: bool = True,
                        own_lines_only: bool = False, expected_en: dict = None) -> dict:
    """Starts a normal translation (single pass; not bulk, not Reflect) as a
    background job that does everything, DB write included: field-scoped
    `en` writes by permanent line id (run_translate_job), then the shared
    finish_translation_run. Builds the same glossary / style guidelines /
    character names / locale as the Workspace button and `cli.py translate`.
    line_ids (optional) restricts the run to those lines; the rest are only
    context. Only empty-`en` lines are translated unless force_retranslate,
    so hand-edited translations survive.

    fallback_chain: optional ordered [{"engine", "model"}, ...] tried
    in turn -- for the rest of the run -- when the active engine fails with an
    auth error, rate limit, timeout or connection error (never a moderation
    refusal or a generic exception). The whole chain must be one class:
    all instruction-following or all TRANSLATION_ONLY_ENGINES, no duplicates.
    Each engine has its own cost cap and spend; the job result's
    "fallbacks" lists any switch that happened.

    reflect: three-pass Reflect mode through the same
    run_translate_job (critiques saved as notes by line).
    bulk: submits through bulk_translate (Claude/Gemini batch API,
    DeepSeek off-peak schedule; with reflect, the three-stage bulk Reflect
    pipeline) inside job `bulk_translate_{id}`, which also polls the batch
    (each Reflect stage in turn) until applied, failed or cancelled; results
    are applied by line id by bulk_translate itself. Neither mode takes a
    fallback chain (Reflect calls call_llm_json, which FallbackEngine does
    not wrap; a batch is bound to one provider); bulk takes no line_ids.

    default_female_pronouns / include_genre_notes: the "Default
    ambiguous pronouns to she/her" and "Include baihe/GL genre guidance"
    toggles (a preset's values, which the client holds; nothing links a
    drama to a preset in the DB). None means the title's saved choice, or
    without one the defaults: she/her off, genre guidance on. A value passed
    is saved for the title once the run starts.

    own_lines_only (with line_ids): run_translate_job's own_lines_only -- a
    line edited while the job runs keeps the edit.
    expected_en (with line_ids): {line id: English} the caller chose the
    lines by; a line whose English differs once loaded here was edited
    since and is dropped (ConflictError if none is left). With line_ids,
    the result's line_ids are the ids the job will actually translate.

    NotFoundError (drama), InvalidInputError, UnsupportedOperationError
    (nothing to translate / cap refusal / mode not available for the
    engine), DependencyUnavailableError (no key), ConflictError (already
    running). gemini_free_tier None means the saved Settings value."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = require_drama(drama_id)
    engine_name = (engine_name or drama.get("translation_engine")
                   or engine_routing_service.resolve_capability("translation.cheap"))
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    is_novel = drama.get("content_mode") == "novel_narration"
    style_preset = style_preset or ("novel" if is_novel else "audio_drama")
    defaults = get_translate_config_defaults(is_novel)
    context_window = defaults["context_window"] if context_window is None else context_window
    context_window_ahead = (defaults["context_window_ahead"]
                            if context_window_ahead is None else context_window_ahead)
    batch_size = defaults["batch_size"] if batch_size is None else batch_size
    validate_run_options(
        engine_name, model, locale=locale, style_preset=style_preset,
        context_window=context_window, context_window_ahead=context_window_ahead,
        batch_size=batch_size, job_cost_cap_usd=job_cost_cap_usd,
        gemini_free_tier=gemini_free_tier)
    if fallback_chain and (reflect or bulk):
        raise InvalidInputError("A fallback chain only applies to a normal translation run.")
    if own_lines_only and line_ids is None:
        raise InvalidInputError("own_lines_only needs line_ids.")
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
        if expected_en is not None:
            target_ids = {ln.id for ln in lines if ln.id in target_ids
                          and (ln.en or "") == expected_en.get(ln.id)}
            if not target_ids:
                raise ConflictError("The chosen lines were edited since they were picked. "
                                    "Pick them again.", details={"reason": "stale_preview"})
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
    for c in chain[1:]:  # the main engine's model is checked by validate_run_options
        _require_offered_model(c["engine"], c["model"])
    monthly_cap = month_cap_usd()
    month_spend = db.get_month_spend() if monthly_cap else 0.0
    built, caps = [], []
    job_id = f"translate_{drama_id}"
    for c in chain:
        name = c["engine"]
        api_key = translate_service.resolve_api_key(name)
        if api_key is None:
            raise MissingKeyError(name)
        free_tier = name == "gemini" and gemini_free_tier
        if free_tier and c["model"] in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS:
            raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
        cap = None
        if engine_cap_applies(name, gemini_free_tier):
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
            # cap; a submitted batch can't, so it's refused up front.
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
        include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns)

    if force_retranslate and any(ln.en for ln in lines):
        db.save_line_history_snapshot(drama_id, lines, "before force re-translate")
    novel_reference = load_novel_reference(drama_id, drama)
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
        save_style_toggles(drama_id, include_genre_notes, default_female_pronouns)
        return {"job_id": job_id, "drama_id": drama_id, "engine": engine_name,
                "model": getattr(engines[0], "model", model),
                "target_line_count": len(eligible), "fallback_engines": [],
                "reflect": reflect, "bulk": True}

    summary_engine, summary_choice = pick_summary_engine(allow_paid=allow_paid_summary)

    started = background_jobs.start_job(
        job_id, workspace_job_service.run_translate_job,
        job_id, drama_id, lines, engine, drama, style_note or "",
        novel_reference, force_retranslate, locale, glossary_terms,
        style_guidelines, engine_name, style_preset, context_window,
        settings_service.get_ollama_num_ctx_override() or None, reflect=reflect,
        cost_cap_usd=cost_cap,
        context_window_ahead=context_window_ahead, batch_size=batch_size,
        summary_engine=summary_engine, summary_engine_choice=summary_choice,
        summary_monthly_cap_usd=month_cap_usd() or None,
        target_ids=target_ids, own_lines_only=own_lines_only,
        gpu_touching=any(c["engine"] == "ollama" for c in chain),
        description=f"{'Reflect-mode t' if reflect else 'T'}ranslation (drama #{drama_id})")
    if not started:
        raise ConflictError("A translation is already running for this drama.")
    save_style_toggles(drama_id, include_genre_notes, default_female_pronouns)
    started = {"job_id": job_id, "drama_id": drama_id, "engine": engine_name,
               "model": getattr(engines[0], "model", model), "target_line_count": len(eligible),
               "fallback_engines": [c["engine"] for c in chain[1:]],
               "reflect": reflect, "bulk": False}
    if target_ids is not None:
        # expected_en may have dropped some of the caller's ids.
        started["line_ids"] = sorted(ln.id for ln in eligible)
    return started


def save_style_toggles(drama_id: int, include_genre_notes=None,
                       default_female_pronouns=None) -> None:
    """Stores the toggles a run was started with as the title's choice, so
    every later run that is not handed them (retry, glossary re-translate,
    line AI, CLI) and the Translate stage use the same values. None leaves
    a stored value as it is."""
    saved = {}
    if include_genre_notes is not None:
        saved["include_genre_notes"] = int(bool(include_genre_notes))
    if default_female_pronouns is not None:
        saved["default_female_pronouns"] = int(bool(default_female_pronouns))
    if saved:
        db.update_drama(drama_id, **saved)


def bulk_job_id(drama_id: int) -> str:
    return f"bulk_translate_{drama_id}"


def _bulk_submitter(drama_id, drama, engine, engine_name, reflect, novel_reference,
                    glossary_terms, style_guidelines, style_note, locale, style_preset,
                    context_window, context_window_ahead, batch_size, force_retranslate,
                    job_cost_cap_usd, series_id):
    """A zero-arg callable that submits the bulk translation (or bulk
    Reflect) batch (same translate_args, context and character names as a
    normal run) and returns the bulk job id. Called inside the job so the provider
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
        nxt = bulk_translate.sibling_stage_job(job["pipeline_id"], nxt_stage) if nxt_stage else None
        if not nxt:
            break
        current = nxt["id"]
    background_jobs.set_result(job_id, {
        "bulk_job_id": first_id, "final_bulk_job_id": current, "status": job.get("status"),
        "stage": job.get("stage"), "summary": job.get("result_summary"),
        "last_error": job.get("last_error")})
    if job.get("status") in ("failed", "auth_error"):
        raise RuntimeError(job.get("last_error") or "The bulk job failed.")


def _bulk_engine_factory(engine_name, model):
    key = translate_service.resolve_api_key(engine_name)
    return translate_engines.get_engine(engine_name, key, model or None) if key else None


def resume_bulk_translations(drama_id: int) -> dict:
    """After a restart: starts a poller for each of this drama's pending
    bulk jobs (bulk_translate.resume_pending), with engines built from server-side keys only."""
    require_drama(drama_id)
    out = bulk_translate.resume_pending(drama_id, _bulk_engine_factory, month_cap_usd() or None)
    return {"drama_id": drama_id,
            "jobs": [{"bulk_job_id": k, "state": v} for k, v in sorted(out.items())]}


def _spend_cap_used_up(engine_name: str) -> bool:
    if not engine_cap_applies(engine_name, settings_service.get_gemini_free_tier()):
        return False
    monthly = month_cap_usd()
    _cap, refusal = translate_engines.resolve_cost_cap(
        None, monthly, db.get_month_spend() if monthly else 0.0)
    return bool(refusal)


def resume_interrupted_at_startup() -> dict:
    """API startup, only while the "bulk.auto_resume" setting is on: resumes
    every drama's pending bulk jobs through resume_pending, the same call as
    the manual resume. A job whose engine has no key, or whose month's
    spending cap is used up, is left pending and one notification says so.
    Nothing starts while a restore or maintenance holds the library. Never
    raises; returns {"enabled", "resumed", "skipped"} counts."""
    out = {"enabled": False, "resumed": 0, "skipped": 0}
    try:
        if not settings_service.get_bulk_auto_resume():
            return out
        out["enabled"] = True
        pending = db.list_bulk_jobs(statuses=("submitted", "scheduled", "running"))
        if not pending:
            return out
        if background_jobs.exclusive_active() or background_jobs.maintenance_active():
            out["skipped"] = len(pending)
            _notify_bulk_resume_skipped("the library is busy with a restore or maintenance")
            return out
        why = {}

        def factory(engine_name, model):
            if _spend_cap_used_up(engine_name):
                why[engine_name] = "the monthly spending cap is used up"
                return None
            engine = _bulk_engine_factory(engine_name, model)
            if engine is None:
                why[engine_name] = "no API key is set for the engine"
            return engine
        cap = month_cap_usd() or None
        for drama_id in sorted({j["drama_id"] for j in pending}):
            for state in bulk_translate.resume_pending(drama_id, factory, cap).values():
                if state == "polling":
                    out["resumed"] += 1
                elif state == "needs_key":
                    out["skipped"] += 1
        if out["skipped"]:
            _notify_bulk_resume_skipped("; ".join(sorted(set(why.values()))) or "not possible")
    except Exception as exc:
        try:
            from applog import get_logger
            get_logger().warning("Resuming bulk batches at startup failed: %s",
                                 translate_engines.redact_secrets(str(exc)))
        except Exception:
            pass
    return out


def _notify_bulk_resume_skipped(reason: str) -> None:
    from services import notification_service
    notification_service.record_event(
        "job_failed", f"Translation batches were not resumed after the restart: {reason}. "
        "Resume them from the drama's Translate page.")


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
    require_drama(drama_id)
    return {"drama_id": drama_id,
            "jobs": [_bulk_entry(j) for j in db.list_bulk_jobs(drama_id)]}


def cancel_bulk_translation(drama_id: int, bulk_job_id: int) -> dict:
    """Cancel: stops polling, marks the job cancelled and
    asks the provider to cancel when a server-side key exists (best effort,
    same as bulk_translate.cancel_bulk_job)."""
    require_drama(drama_id)
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
    """The tier's engine goes onto the drama row (the only field with a
    per-drama DB home); the model, Reflect and Auto QC are returned for the
    client to put into its form. Starts nothing."""
    drama = require_drama(drama_id)
    t = translate_engines.effective_tier(tier)
    if t is None:
        raise InvalidInputError("Unknown workflow tier.")
    if drama.get("translation_engine") != t["translation_engine"]:
        db.update_drama(drama_id, translation_engine=t["translation_engine"])
    return {"drama_id": drama_id, "tier": tier, "label": t["label"],
            "translation_engine": t["translation_engine"], "engine_model": t["engine_model"],
            "reflect": bool(t["reflect"]), "auto_qc": bool(t["auto_qc"])}


def apply_translate_preset(drama_id: int, preset_id: int) -> dict:
    """Applies a preset to an existing drama (parity X03): the preset's
    engine (if it saved one) goes onto the drama row, the one field with a
    per-drama DB home; style, locale, the two toggles and the model are
    returned for the client's form. Starts nothing.
    Raises NotFoundError (unknown drama or preset)."""
    require_drama(drama_id)
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
        "locale": locale if locale in settings_service.LOCALE_CHOICES else None,
        "default_female_pronouns": bool(pronouns),
        "include_genre_notes": True if genre is None else bool(genre),
    }


def dismiss_translate_errors(drama_id: int) -> dict:
    """Dismisses the failed-batches notice: clears only the drama's
    persisted record of the last run's failed batches
    (db.update_drama(last_translate_errors=None)); lines are untouched, so a
    later "Translate all lines" still retries the missing ones. NotFoundError
    for an unknown drama; `dismissed` is False when there was nothing to clear."""
    drama = require_drama(drama_id)
    had = bool(drama.get("last_translate_errors"))
    if had:
        db.update_drama(drama_id, last_translate_errors=None)
    return {"drama_id": drama_id, "dismissed": had}


_PRESET_NAME_MAX = 100   # services/library_service._clean_name's limit


def save_translate_preset(name: str, translation_engine: str, engine_model: Optional[str] = None,
                          style_preset: Optional[str] = None, locale: Optional[str] = None,
                          default_female_pronouns: bool = False,
                          include_genre_notes: bool = True, overwrite: bool = False) -> dict:
    """Saves the Translate form as a preset: db.save_preset with the Translate form's engine,
    model, style, locale and the two toggles. Replacing a preset of the
    same name needs overwrite=True (otherwise ConflictError). Replacing is effectively a
    delete, so the route only allows overwrite from the PC. engine_model is None for engines without a
    model picker, and None (engine default) is allowed for the others."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > _PRESET_NAME_MAX:
        raise InvalidInputError(f"A preset name is 1-{_PRESET_NAME_MAX} characters.")
    if translation_engine not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(translation_engine))
    models = next((e["models"] for e in translate_service.list_engines()
                   if e["name"] == translation_engine), None)
    if engine_model is not None and not (
            _is_safe_ollama_model(engine_model) if translation_engine == "ollama"
            else models is not None and engine_model in models):
        raise InvalidInputError("That model isn't offered for this engine.")
    if style_preset is not None and style_preset not in translation_guide.STYLE_PRESETS:
        raise InvalidInputError("Unknown style preset.")
    if locale is not None and locale not in settings_service.LOCALE_CHOICES:
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
