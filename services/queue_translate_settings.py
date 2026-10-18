"""
services/queue_translate_settings.py -- the money side of the Queue card's
Translate "Saved settings for this step": the model and batch size a queued run
repeats from the title's last run, its cost line, and the monthly cap left.
"""

import db
import translate_engines
from lib.errors import InvalidInputError, ServiceError
from services import profile_service, settings_service, translate_run_service


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
    if engine in translate_engines.FREE_ENGINES:
        return "Free"
    try:
        est = translate_run_service.estimate_translate_cost(
            drama_id, model=model, batch_size=batch_size)
    except ServiceError:
        return "No estimate yet; uses your API key and costs money"
    return f"About ${est['estimated_usd']:.2f}; uses your API key and costs money"
