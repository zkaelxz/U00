"""
services/queue_translate_settings.py -- the money side of the Queue card's
Translate "Saved settings for this step": the model and batch size a queued run
repeats from the title's last run, its cost line, and the monthly cap left.
"""

import db
from engine_backends import engine_registry, local, pricing
import translation_guide
from lib.errors import ForbiddenError, InvalidInputError, ServiceError, UnsupportedOperationError
from services import profile_service, settings_service, translate_run_service, translate_service

TRANSLATE_KEYS = ("model", "style_preset", "locale", "batch_size")
_LABELS = {"model": "Engine and model", "style_preset": "Tone and character voice",
           "locale": "English variant", "batch_size": "Batch size"}
_NO_CAP = ("Set a monthly spending cap (Settings > Spending) to run paid translation "
           "from the queue.")

_NEEDS_EDIT = "Needs permission to edit lines."
_PAID_ONLY = "Needs paid-engine permission."


def _cap_left() -> str:
    cap = settings_service.get_monthly_cap_usd()
    if cap <= 0:
        return "No cap set"
    return f"${max(cap - db.get_month_spend(since_reset=True), 0):.2f} of ${cap:.2f}"


def _queued_model_and_batch(drama: dict, engine: str):
    """(model, batch size) the queued run will use beyond the engine's defaults: the title's
    last run's, the model only while it is still offered (start_translate_run falls back
    to the default otherwise). None where the default applies."""
    last = profile_service.title_run_defaults(drama, engine, engine)
    model = last.get("model")
    if model:
        try:
            translate_run_service._require_offered_model(engine, model)
        except InvalidInputError:
            model = None
    return model, last.get("batch_size")


def _translate_cost(drama_id: int, engine: str, model=None, batch_size=None) -> str:
    if engine in engine_registry.FREE_ENGINES:
        return "Free"
    try:
        est = translate_run_service.estimate_translate_cost(
            drama_id, model=model, batch_size=batch_size)
    except ServiceError:
        return "No estimate yet; uses your API key and costs money"
    return f"About ${est['estimated_usd']:.2f}; uses your API key and costs money"


def _is_cloud(engine: str, model) -> bool:
    return engine == "ollama" and local.is_ollama_cloud_model(model)


def translate_fields(drama: dict, run_args: dict, holds_paid: bool, holds_edit: bool = True) -> list:
    """The Translate settings one queued item may change, each starting at what the queued
    run would use. What the run accepts is check_translate_settings'."""
    engine = run_args["engine"]
    last = profile_service.title_run_defaults(drama, engine, engine)
    saved_model, saved_batch = _queued_model_and_batch(drama, engine)
    model = saved_model or engine_registry.effective_default_model(engine)
    novel = drama.get("content_mode") == "novel_narration"
    values = {"model": model, "style_preset": last.get("style_preset") or ("novel" if novel else "audio_drama"),
              "locale": run_args["locale"],
              "batch_size": saved_batch or translate_run_service.get_translate_config_defaults(
                  novel, engine, model)["batch_size"]}
    models = next((e["models"] for e in translate_service.list_engines() if e["name"] == engine), None)
    offered = {
        "model": [m for m in (models or []) if holds_paid or not _is_cloud(engine, m)],
        "style_preset": {k: p["label"] for k, p in translation_guide.STYLE_PRESETS.items()},
        "locale": list(settings_service.LOCALE_CHOICES)}
    fields = []
    for key in TRANSLATE_KEYS:
        field = {"key": key, "label": _LABELS[key], "value": values[key], "options": [],
                 "min": None, "max": None, "locked": None if holds_edit else _NEEDS_EDIT}
        if key == "batch_size":
            field.update(kind="number", min=1, max=translate_run_service.MAX_BATCH_SIZE)
        else:
            if key == "model" and not offered["model"]:
                continue
            field["kind"] = "select"
            choices = offered[key]
            labels = choices if isinstance(choices, dict) else {c: c for c in choices}
            field["options"] = [{"value": v, "label": label} for v, label in labels.items()]
            if values[key] not in labels:
                field["options"].append({"value": values[key], "label": str(values[key])})
        fields.append(field)
    return fields


def check_translate_settings(drama: dict, engine: str, overrides, holds_paid: bool = True) -> dict:
    """The queue settings in `overrides`, checked the way the run checks its own options.
    InvalidInputError for an unknown key or a value of the wrong kind or range."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise InvalidInputError("Settings must be an object.")
    unknown = sorted(set(overrides) - set(TRANSLATE_KEYS))
    if unknown:
        raise InvalidInputError(f"Unknown setting {unknown[0]!r}.")
    given = {k: v for k, v in overrides.items() if v is not None}
    for key in ("model", "style_preset", "locale"):
        if key in given and (not isinstance(given[key], str) or not given[key]):
            raise InvalidInputError(f"{key} must be text.")
    # bool is an int subclass: True would otherwise pass as a batch size of 1.
    if "batch_size" in given:
        size = given["batch_size"]
        if isinstance(size, bool) or not isinstance(size, int):
            raise InvalidInputError("batch_size must be a whole number.")
        if not 1 <= size <= translate_run_service.MAX_BATCH_SIZE:
            raise InvalidInputError(
                f"Batch size must be from 1 to {translate_run_service.MAX_BATCH_SIZE}.")
    if "locale" in given and given["locale"] not in settings_service.LOCALE_CHOICES:
        raise InvalidInputError("Unknown English variant.")
    if "style_preset" in given and given["style_preset"] not in translation_guide.STYLE_PRESETS:
        raise InvalidInputError("Unknown style preset.")
    if "model" in given:
        translate_run_service._require_offered_model(engine, given["model"])
        # A hosted Ollama model sends the text off this PC on the owner's quota, as a paid engine does.
        if _is_cloud(engine, given["model"]) and not holds_paid:
            raise ForbiddenError("Hosted Ollama models need paid-engine permission.")
    return given


def translate_changed_rows(settings: dict) -> list:
    presets = {k: p["label"] for k, p in translation_guide.STYLE_PRESETS.items()}
    return [_row(_LABELS[k], presets.get(v, v) if k == "style_preset" else v) for k, v in settings.items()
            if k in _LABELS]


def _row(label: str, value) -> dict:
    return {"label": label, "value": str(value), "engine": None}


def _check_translate_spend(drama_id: int, model=None, batch_size=None) -> None:
    """The unattended twin of the Translate stage's estimate: refuse before
    any paid call when this month's cap is used up or the estimate is above
    what is left of it. The run itself still enforces the cap as it goes."""
    est = translate_run_service.estimate_translate_cost(
        drama_id, model=model, batch_size=batch_size)
    if not est["cap_applies"]:
        return
    # Like the scheduled model re-evaluation: unattended paid work needs a ceiling.
    if settings_service.get_monthly_cap_usd() <= 0:
        raise UnsupportedOperationError(_NO_CAP)
    if est["monthly_refusal"]:
        _cap, refusal = pricing.resolve_cost_cap(
            None, settings_service.get_monthly_cap_usd(), db.get_month_spend())
        raise UnsupportedOperationError(refusal or "This month's spending cap is used up.")
    if est["estimate_above_cap"]:
        raise UnsupportedOperationError(
            f"The estimate (${est['estimated_usd']:.2f}) is above what is left of this "
            f"month's spending cap (${est['effective_cap_usd']:.2f}).")
