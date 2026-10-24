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

The config's style presets carry their guidance text (X04). Applying a tier or
preset and saving one live in services/translate_preset_service.py.

Out of scope here: glossary review, preset rename/delete and characters CRUD.

Every knob (engine, model, context window, batch size, reflect, bulk, caps)
is a request-time parameter with a built-in default as fallback -- none is
stored on the drama. The API reads lines/config from the
DB, not unsaved browser state. D2: keys/secrets, client-supplied URLs and
the novel text are never returned -- booleans only.
"""
import json
import re
import threading
from types import SimpleNamespace
from typing import Optional

import background_jobs
from jobs import job_launch
import bulk_providers
import bulk_poll
import bulk_reflect
import bulk_translate
import translation_finish
import core
import db
import translate_engines
import translation_guide
from engine_backends.engine_registry import legacy_ids
from services import (bulk_job_view, bulk_submit_service, engine_routing_service,
                      novel_files_service, ownership_service, profile_service,
                      run_settings_service, settings_service, translate_service,
                      translate_run_options, workspace_job_service)
from services.translate_run_options import ollama_reachable  # also read as translate_run_service.ollama_reachable
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    MissingPackageError,
    NotFoundError,
    UnsupportedOperationError,
)

# Engines that report usage, so the spending cap applies to them.
_CAP_ENGINES = ("claude", "deepseek", "gemini", "openai")


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


# Serialises writes to dramas.last_translate_errors: the dismiss route and a
# retry run's prune both read-modify-write it.
ERRORS_LOCK = threading.Lock()


def _parse_errors(raw) -> Optional[list]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def get_translate_config_defaults(is_novel: bool, engine=None, model=None) -> dict:
    return translate_engines.default_translate_options(engine, model)


def get_translate_config(drama_id: int) -> dict:
    drama = ownership_service.require_drama(drama_id)
    is_novel = drama.get("content_mode") == "novel_narration"
    has_novel = novel_files_service.has_reference(drama_id, drama)
    lines = db.load_lines(drama_id)
    monthly_cap = month_cap_usd()
    free_tier = settings_service.get("gemini_free_tier")
    engine_name = (drama.get("translation_engine")
                   or engine_routing_service.resolve_capability("translation.cheap"))
    return {
        "drama_id": drama_id,
        "translation_engine": engine_name,
        "line_ai_engine": engine_routing_service.line_tool_engine(drama),
        "engines": (engines := translate_service.list_engines()),
        "batch_size_defaults": translate_engines.batch_size_defaults(engines),
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
        "defaults": get_translate_config_defaults(is_novel, engine_name),
        "default_locale": settings_service.get("default_locale"),
        "default_style_note": settings_service.get("default_style_note"),
        "project_instructions": drama.get("project_instructions"),
        "series_instructions": drama.get("series_instructions"),
        "has_novel_reference": has_novel,
        "line_count": len(lines),
        "untranslated_count": translation_finish.untranslated_line_count(drama_id),
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
        "translate_by_sentence": _saved_bool(drama.get("translate_by_sentence")),
        "translate_start": profile_service.translate_start(drama),
    }


def _saved_bool(value) -> Optional[bool]:
    return None if value is None else bool(value)


def validate_run_options(engine_name: str, model, *, locale: str, style_preset: str,
                         context_window: int, context_window_ahead: int, batch_size: int,
                         job_cost_cap_usd, gemini_free_tier: bool) -> None:
    """The checks on a translate run's own options, shared by
    start_translate_run and the other translate entry points so all refuse
    the same values. Takes resolved values (defaults already applied). InvalidInputError
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
                            job_cost_cap_usd: float = None, line_ids=None,
                            batch_size: int = None) -> dict:
    """line_ids (optional): estimate only these lines (of those the run
    would translate). batch_size (optional): the run's own batch size, which
    sets how many times the instructions are sent; omitted, the engine's default."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = ownership_service.require_drama(drama_id)
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
    resolved_model = model or translate_engines.effective_default_model(engine_name)
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
        if batch_size is None:
            batch_size = get_translate_config_defaults(
                drama.get("content_mode") == "novel_narration", engine_name, model)["batch_size"]
        estimated = float(fn(stand_in, [ln["zh"] for ln in targets], batch_size))
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
    return novel_files_service.read_reference_text(drama_id, drama)


def pick_summary_engine(ollama_url: Optional[str] = None, allow_paid: bool = True,
                        run_engine: Optional[str] = None, run_model: Optional[str] = None):
    """The episode-summary engine from Settings (default local Ollama); (None, None), so the summary is skipped, if it
    can't be built or a cloud pick has no key. Never fails the translation.
    allow_paid=False (an API caller without engines.paid) also skips a pick
    outside translate_engines.FREE_ENGINES: it would spend the owner's key.
    run_engine/run_model: an Ollama run's summary reuses its model, so no second one loads."""
    choice = settings_service.get("episode_summary_engine")
    if not allow_paid and choice not in translate_engines.FREE_ENGINES:
        return None, None
    try:
        if choice == "ollama":
            return translate_engines.get_engine(
                "ollama", None, run_model if run_engine == "ollama" else None,
                base_url=ollama_url or settings_service.ollama_url()), choice
        api_key = translate_service.resolve_api_key(choice)
        if not api_key:
            return None, None
        return translate_engines.get_engine(
            choice, api_key, free_tier=choice == "gemini" and settings_service.get("gemini_free_tier")
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
        translate_engines.builtin_default_model(engine_name), translate_engines.effective_default_model(engine_name)]
    if not isinstance(model, str) or model not in (*allowed, *legacy_ids(engine_name)):
        raise InvalidInputError("That model isn't offered for this engine.")


def start_translate_run(drama_id: int, engine_name: str = None, model: str = None,
                        style_preset: str = None, style_note: str = None,
                        locale: str = None, force_retranslate: bool = False,
                        context_window: int = None, context_window_ahead: int = None,
                        batch_size: int = None, line_ids: list = None,
                        gemini_free_tier: bool = None, job_cost_cap_usd: float = None,
                        fallback_chain: list = None, reflect: bool = False,
                        bulk: bool = False, default_female_pronouns: bool = None,
                        include_genre_notes: bool = None, allow_paid_summary: bool = True,
                        own_lines_only: bool = False, expected_en: dict = None, thinking: bool = None,
                        record_run: bool = True) -> dict:
    """Starts a normal translation (single pass; not bulk, not Reflect) as a
    background job that does everything, DB write included: field-scoped
    `en` writes by permanent line id (run_translate_job), then the shared
    finish_translation_run. Builds the same glossary / style guidelines /
    character names / locale as the Workspace button.
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
    drama = ownership_service.require_drama(drama_id)
    opts = _resolve_run_options(
        drama, engine_name, model, style_preset, style_note, locale, context_window,
        context_window_ahead, batch_size, job_cost_cap_usd, gemini_free_tier)
    engine_name, model, locale = opts.engine_name, opts.model, opts.locale
    opts.record_run = record_run
    translate_run_options.check_mode_options(engine_name, gemini_free_tier, reflect, bulk, fallback_chain,
                        own_lines_only, line_ids)

    lines, target_ids, eligible = translate_run_options.select_lines(
        drama_id, line_ids, expected_en, force_retranslate)
    chain, built, caps = _build_engine_chain(
        engine_name, model, fallback_chain, gemini_free_tier, job_cost_cap_usd)

    job_id = f"translate_{drama_id}"
    if bulk:
        translate_run_options.require_bulk_ready(
            drama_id, engine_name, model, caps[0], reflect, force_retranslate,
            gemini_free_tier, job_cost_cap_usd, estimate_translate_cost)
        job_id = bulk_job_id(drama_id)
    if background_jobs.is_running(job_id):
        raise ConflictError("A translation is already running for this title.")

    engines, engine, cost_cap = translate_run_options.instantiate_engines(built, chain, caps)

    resp, run_settings, novel_reference, glossary_terms, style_guidelines = _prepare_run(
        job_id, drama_id, drama, lines, eligible, engines, chain, opts, reflect, bulk,
        force_retranslate, own_lines_only, thinking, include_genre_notes, default_female_pronouns)

    if bulk:
        _launch_bulk(job_id, drama_id, drama, engines[0], opts, reflect, novel_reference,
                     glossary_terms, style_guidelines, force_retranslate, job_cost_cap_usd,
                     thinking, run_settings, include_genre_notes, default_female_pronouns)
        return {**resp, "fallback_engines": []}

    _launch_translate(job_id, drama_id, drama, lines, engine, cost_cap, chain, opts, reflect,
                      novel_reference, glossary_terms, style_guidelines, force_retranslate,
                      target_ids, own_lines_only, thinking, allow_paid_summary, run_settings,
                      include_genre_notes, default_female_pronouns)
    if target_ids is not None:
        # expected_en may have dropped some of the caller's ids.
        resp["line_ids"] = sorted(ln.id for ln in eligible)
    return resp


def _prepare_run(job_id, drama_id, drama, lines, eligible, engines, chain, opts, reflect, bulk,
                 force_retranslate, own_lines_only, thinking, include_genre_notes,
                 default_female_pronouns):
    """(response, run settings, novel reference, glossary terms, style guidelines)."""
    glossary_terms, style_guidelines, _names = workspace_job_service.build_run_style_context(
        drama_id, drama, lines, opts.style_preset,
        include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns,
        principal=ownership_service.acting_principal())
    novel_reference = load_novel_reference(drama_id, drama)
    resp = {"job_id": job_id, "drama_id": drama_id, "engine": opts.engine_name,
            "model": getattr(engines[0], "model", opts.model), "target_line_count": len(eligible),
            "fallback_engines": [c["engine"] for c in chain[1:]], "reflect": reflect, "bulk": bulk}
    run_settings = run_settings_service.for_translate(
        drama, include_genre_notes, default_female_pronouns, glossary_terms, style_guidelines,
        opts.style_note, resp, locale=opts.locale, style_preset=opts.style_preset,
        context_window=opts.context_window, context_window_ahead=opts.context_window_ahead,
        batch_size=opts.batch_size, force_retranslate=force_retranslate, thinking=thinking,
        own_lines_only=own_lines_only)
    return resp, run_settings, novel_reference, glossary_terms, style_guidelines


def _launch_bulk(job_id, drama_id, drama, engine, opts, reflect, novel_reference,
                 glossary_terms, style_guidelines, force_retranslate, job_cost_cap_usd,
                 thinking, run_settings, include_genre_notes, default_female_pronouns) -> None:
    submit = bulk_submit_service.bulk_submitter(
        drama_id, drama, engine, opts.engine_name, reflect, novel_reference, glossary_terms,
        style_guidelines, opts.style_note or "", opts.locale, opts.style_preset,
        opts.context_window, opts.context_window_ahead, opts.batch_size, force_retranslate,
        job_cost_cap_usd, drama.get("series_id"), thinking)
    started = job_launch.start_job(
        job_id, run_bulk_translate_job, job_id, engine, opts.engine_name, submit,
        month_cap_usd() or None,
        description=f"Bulk {'Reflect ' if reflect else ''}translation (title #{drama_id})",
        run_settings=run_settings, line_fields=("en", "flag", "flag_note"))
    if not started:
        raise ConflictError("A translation is already running for this title.")
    _record_started_run(drama_id, drama, opts, include_genre_notes, default_female_pronouns)


def _launch_translate(job_id, drama_id, drama, lines, engine, cost_cap, chain, opts, reflect,
                      novel_reference, glossary_terms, style_guidelines, force_retranslate,
                      target_ids, own_lines_only, thinking, allow_paid_summary, run_settings,
                      include_genre_notes, default_female_pronouns) -> None:
    # Imported here: it needs this module for the drama lookup and Ollama probe.
    from services import episode_summary_service
    summary_engine, summary_choice = pick_summary_engine(
        allow_paid=allow_paid_summary, run_engine=opts.engine_name, run_model=opts.model)
    summary_skip_reason = ("" if summary_engine is not None
                           else episode_summary_service.no_engine_reason(allow_paid_summary))

    started = job_launch.start_job(
        job_id, workspace_job_service.run_translate_job,
        job_id, drama_id, lines, engine, drama, opts.style_note or "",
        novel_reference, force_retranslate, opts.locale, glossary_terms,
        style_guidelines, opts.engine_name, opts.style_preset, opts.context_window,
        settings_service.get("ollama_num_ctx_override") or None, reflect=reflect,
        cost_cap_usd=cost_cap,
        context_window_ahead=opts.context_window_ahead, batch_size=opts.batch_size,
        summary_engine=summary_engine, summary_engine_choice=summary_choice,
        summary_monthly_cap_usd=month_cap_usd() or None, summary_skip_reason=summary_skip_reason,
        target_ids=target_ids, own_lines_only=own_lines_only, thinking=thinking,
        gpu_touching=translate_engines.chain_touches_local_gpu(chain),
        description=f"{'Reflect-mode t' if reflect else 'T'}ranslation (title #{drama_id})",
        run_settings=run_settings, line_fields=("en", "flag", "flag_note"))
    if not started:
        raise ConflictError("A translation is already running for this title.")
    _record_started_run(drama_id, drama, opts, include_genre_notes, default_female_pronouns)


def _resolve_run_options(drama, engine_name, model, style_preset, style_note, locale,
                         context_window, context_window_ahead, batch_size,
                         job_cost_cap_usd, gemini_free_tier) -> SimpleNamespace:
    """The run's engine, model and knobs: what the caller passed, else the title's
    last run's, else the defaults -- validated."""
    engine_name = (engine_name or drama.get("translation_engine")
                   or engine_routing_service.resolve_capability("translation.cheap"))
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    is_novel = drama.get("content_mode") == "novel_narration"
    title_engine = (drama.get("translation_engine")
                    or engine_routing_service.resolve_capability("translation.cheap"))
    last = profile_service.title_run_defaults(drama, engine_name, title_engine)
    style_preset = style_preset or last.get("style_preset") or ("novel" if is_novel else "audio_drama")
    style_note = last.get("style_note", "") if style_note is None else style_note
    locale = locale or last.get("locale") or "en-US"
    chosen_batch_size = batch_size if batch_size is not None else last.get("batch_size")
    if model is None and "model" in last:
        try:
            _require_offered_model(engine_name, last["model"])
            model = last["model"]
        except InvalidInputError:
            pass    # no longer offered: the engine's default
    defaults = get_translate_config_defaults(is_novel, engine_name, model)
    context_window = last.get("context_window", defaults["context_window"]) if context_window is None else context_window
    context_window_ahead = (last.get("context_window_ahead", defaults["context_window_ahead"])
                            if context_window_ahead is None else context_window_ahead)
    batch_size = defaults["batch_size"] if chosen_batch_size is None else chosen_batch_size
    validate_run_options(
        engine_name, model, locale=locale, style_preset=style_preset,
        context_window=context_window, context_window_ahead=context_window_ahead,
        batch_size=batch_size, job_cost_cap_usd=job_cost_cap_usd,
        gemini_free_tier=gemini_free_tier)
    return SimpleNamespace(
        engine_name=engine_name, title_engine=title_engine, model=model,
        style_preset=style_preset, style_note=style_note, locale=locale,
        chosen_batch_size=chosen_batch_size, context_window=context_window,
        context_window_ahead=context_window_ahead, batch_size=batch_size)


def _build_engine_chain(engine_name, model, fallback_chain, gemini_free_tier, job_cost_cap_usd):
    """(chain, built, caps): the main engine then any fallbacks, each with its key
    and its own cost cap. `built` is (name, api_key, model, free_tier) per engine."""
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
    return chain, built, caps


def _record_started_run(drama_id, drama, opts, include_genre_notes, default_female_pronouns) -> None:
    if not opts.record_run:    # a queued item's own settings are not the title's starting values
        return
    save_style_toggles(drama_id, include_genre_notes, default_female_pronouns)
    profile_service.remember_run(
        drama_id, drama, on_title_engine=opts.engine_name == opts.title_engine, model=opts.model,
        style_preset=opts.style_preset, style_note=opts.style_note, locale=opts.locale,
        batch_size=opts.chosen_batch_size, context_window=opts.context_window,
        context_window_ahead=opts.context_window_ahead)


def save_style_toggles(drama_id: int, include_genre_notes=None,
                       default_female_pronouns=None, thinking=None,
                       translate_by_sentence=None) -> None:
    """Stores the toggles a run was started with as the title's choice, so
    every later run that is not handed them (retry, glossary re-translate,
    line AI) and the Translate stage use the same values. None leaves
    a stored value as it is."""
    saved = {k: int(bool(v)) for k, v in (
        ("include_genre_notes", include_genre_notes),
        ("default_female_pronouns", default_female_pronouns),
        ("translate_thinking", thinking),
        ("translate_by_sentence", translate_by_sentence)) if v is not None}
    if saved:
        db.update_drama(drama_id, **saved)


def bulk_job_id(drama_id: int) -> str:
    return f"bulk_translate_{drama_id}"


_NEXT_REFLECT_STAGE = {"faithful": "reflect", "reflect": "expressive"}


def run_bulk_translate_job(job_id, engine, engine_name, submit, monthly_cap_usd=None):
    """Background job: submit, then poll inside this job until the batch is
    applied (bulk_poll.run_bulk_poller; results applied by line id).
    A bulk Reflect pipeline's later stages are submitted by bulk_translate
    as each prior stage applies; this follows each one in turn. Cancelling
    this job cancels the pending bulk job (at the provider when it can)."""
    if background_jobs.is_cancel_requested(job_id):
        return
    first_id = current = submit()
    provider = bulk_providers.make_provider(engine_name, engine)
    while True:
        bulk_poll.run_bulk_poller(job_id, current, provider=provider, engine=engine,
                                       interval=bulk_poll.POLL_INTERVAL_SECONDS,
                                       monthly_cap_usd=monthly_cap_usd)
        job = db.get_bulk_job(current) or {}
        if background_jobs.is_cancel_requested(job_id):
            if job.get("status") in db.BULK_PENDING_STATUSES + ("running",):
                bulk_poll.cancel_bulk_job(current, provider)
                job = db.get_bulk_job(current) or {}
            break
        nxt_stage = (_NEXT_REFLECT_STAGE.get(job.get("stage"))
                     if job.get("kind") == "reflect" and job.get("status") == "applied" else None)
        nxt = (bulk_reflect.sibling_stage_job(job["pipeline_id"], nxt_stage, skip_failed=True)
               if nxt_stage else None)
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


pending_bulk_engines = bulk_poll.resumable_engines


def resume_bulk_translations(drama_id: int) -> dict:
    """After a restart: starts a poller for each of this drama's pending
    bulk jobs (bulk_poll.resume_pending), with engines built from server-side keys only."""
    ownership_service.require_drama(drama_id)
    out = bulk_poll.resume_pending(drama_id, _bulk_engine_factory, month_cap_usd() or None)
    return {"drama_id": drama_id,
            "jobs": [{"bulk_job_id": k, "state": v} for k, v in sorted(out.items())]}


def _spend_cap_used_up(engine_name: str) -> bool:
    if not engine_cap_applies(engine_name, settings_service.get("gemini_free_tier")):
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
        if not settings_service.get("bulk_auto_resume"):
            return out
        out["enabled"] = True
        pending = db.list_bulk_jobs(statuses=bulk_poll.RESUMABLE_STATUSES)
        if not pending:
            return out
        if job_launch.exclusive_active() or job_launch.maintenance_active():
            out["skipped"] = len(pending)
            _notify_bulk_resume_skipped("the library is busy with a restore or maintenance")
            return out
        why = {}

        def factory(engine_name, model):
            if _spend_cap_used_up(engine_name):
                why[engine_name] = "the monthly spending cap is used up"
                return None
            try:
                engine = _bulk_engine_factory(engine_name, model)
            except MissingPackageError:
                why[engine_name] = "its Python package is not installed"
                return None
            if engine is None:
                why[engine_name] = "no API key is set for the engine"
            return engine
        cap = month_cap_usd() or None
        for drama_id in sorted({j["drama_id"] for j in pending}):
            for state in bulk_poll.resume_pending(drama_id, factory, cap).values():
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
        "Resume them from the title's Translate page.")


def list_bulk_translations(drama_id: int) -> dict:
    """Read-only: this drama's bulk jobs (newest first) with the status
    last recorded in the database. Never contacts a provider; polling stays
    with resume_bulk_translations."""
    ownership_service.require_drama(drama_id)
    return {"drama_id": drama_id,
            "jobs": [bulk_job_view.bulk_entry(j) for j in db.list_bulk_jobs(drama_id)]}


def cancel_bulk_translation(drama_id: int, bulk_job_id: int, principal) -> dict:
    """Cancel: stops polling, marks the job cancelled and
    asks the provider to cancel when a server-side key exists (best effort,
    same as bulk_poll.cancel_bulk_job). A batch records no starter, so
    only the title's owner and admins may cancel it
    (ownership_service.require_drama_work_cancellable)."""
    ownership_service.require_drama(drama_id)
    ownership_service.require_drama_work_cancellable(principal, drama_id)
    job = db.get_bulk_job(bulk_job_id)
    if not job or job["drama_id"] != drama_id:
        raise NotFoundError(f"Bulk job {bulk_job_id} not found for title {drama_id}.")
    if job["status"] not in bulk_job_view.CANCELLABLE:
        raise ConflictError(f"Bulk job {bulk_job_id} is {job['status']} and cannot be cancelled.")
    key = translate_service.resolve_api_key(job["engine"])
    package_missing = False
    try:
        engine = translate_engines.get_engine(
            job["engine"], key, job.get("model") or None) if key else None
    except DependencyUnavailableError:
        # Still cancel here: the batch keeps running at the provider, which the note says.
        engine, package_missing = None, True
    provider = bulk_providers.make_provider(job["engine"], engine) if engine else None
    note = bulk_poll.cancel_bulk_job(bulk_job_id, provider)
    if package_missing:
        note += (" The Python package for this engine is not installed, so the batch was not "
                 "cancelled at the provider; cancel it in the provider's console.")
    return {"drama_id": drama_id, "bulk_job": bulk_job_view.bulk_entry(db.get_bulk_job(bulk_job_id)),
            "message": note}


def dismiss_translate_errors(drama_id: int) -> dict:
    """Dismisses the failed-batches notice: clears only the drama's
    persisted record of the last run's failed batches
    (db.update_drama(last_translate_errors=None)); lines are untouched, so a
    later "Translate all lines" still retries the missing ones. NotFoundError
    for an unknown drama; `dismissed` is False when there was nothing to clear."""
    ownership_service.require_drama(drama_id)
    with ERRORS_LOCK:
        had = bool((db.get_drama(drama_id) or {}).get("last_translate_errors"))
        if had:
            db.update_drama(drama_id, last_translate_errors=None)
    return {"drama_id": drama_id, "dismissed": had}
