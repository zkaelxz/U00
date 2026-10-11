"""
services/translate_thinking_service.py -- the "think harder" option of a
translation run, and the title's remembered choice for it.

Thinking is off unless a run asks for it. Only engines with a request switch
(DeepSeek, Ollama) can follow the choice; the others behave as they always
did and the Translate step says so.

The choice is remembered per title in `dramas.translate_thinking` (NULL =
never chosen = off), written with the other Translate toggles; see
engine_backends/thinking.py, which reads it when a run's context is built.
A run is handed its own value explicitly, so the request, the job and the
provenance record agree; the choice is saved only once the run is accepted,
so a refused request leaves it alone.
"""
import logging

import db
from engine_backends import thinking as thinking_switch
from services import translate_run_service

log = logging.getLogger(__name__)

SWITCH_ENGINES = thinking_switch.NO_THINKING_ENGINES


def has_switch(engine_name) -> bool:
    return engine_name in SWITCH_ENGINES


def get_title_choice(drama_id: int) -> bool:
    return thinking_switch.title_thinking({"id": drama_id})


def save_title_choice(drama_id: int, thinking) -> None:
    """Remembers an explicit choice (None = not asked, nothing changes).
    Never raises: a run must not fail because its preference could not be written."""
    if thinking is None or not db.get_drama(drama_id):
        return
    try:
        translate_run_service.save_style_toggles(drama_id, thinking=thinking)
    except Exception:
        log.warning("Could not save the thinking choice for drama %s", drama_id, exc_info=True)


def may_remember(holds_paid: bool, thinking) -> bool:
    """Whether a run's choice may become the title's. The title's choice also
    drives later paid calls (fix-flagged, stronger-engine retry, bulk, CLI), so
    only a holder of engines.paid may turn it on: a free-only run by anyone
    else still thinks for that run but leaves the saved choice alone. Turning
    it off is always allowed because it can only reduce spend."""
    return holds_paid or not thinking


def effective(engine_names, thinking, reflect=False) -> bool:
    """What a run really does: thinking needs an engine with a switch, and
    Reflect's passes (the shared JSON helper) have none."""
    return bool(thinking) and not reflect and any(has_switch(n) for n in engine_names)


def config_fields(drama_id: int) -> dict:
    return {"thinking_switch_engines": list(SWITCH_ENGINES),
            "title_thinking": get_title_choice(drama_id)}


def annotate_estimate(estimate: dict, drama_id: int, thinking, reflect=False) -> dict:
    """Adds whether thinking applies to the estimated run. The estimate is made
    from the visible text, and hidden reasoning is billed as output, so it is
    a lower bound when thinking is on (Ollama is free, so nothing is billed)."""
    if thinking is None:
        thinking = get_title_choice(drama_id)
    on = effective([estimate.get("engine")], thinking, reflect)
    return {**estimate, "thinking": on,
            "estimate_is_lower_bound": on and not estimate.get("free")}


def start_with_thinking(start, drama_id: int, thinking, remember: bool, *args, **kwargs) -> dict:
    """Runs a start function (translate or glossary re-translate), handing it the
    run's own thinking value so the job, the engine request and the provenance
    agree whatever the title's file says meanwhile. A start that refuses raises
    before anything is saved; only an accepted run changes the title's choice,
    and only when `remember` allows it."""
    run_thinking = get_title_choice(drama_id) if thinking is None else bool(thinking)
    started = start(drama_id, *args, thinking=run_thinking, **kwargs)
    if remember:
        save_title_choice(drama_id, thinking)
    chain = [started.get("engine")] + list(started.get("fallback_engines") or [])
    return {**started, "thinking": effective(chain, run_thinking, started.get("reflect"))}
