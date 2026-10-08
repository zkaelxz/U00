"""
services/translate_thinking_service.py -- the "think harder" option of a
translation run, and the title's remembered choice for it.

Thinking is off unless a run asks for it. Only engines with a request switch
(DeepSeek, Ollama) can follow the choice; the others behave as they always
did and the Translate step says so.

The choice is remembered per title in `translate_prefs.json` in the drama's
own folder (see engine_backends/thinking.py, which reads it when a run's
context is built). The API saves it before a run starts, so the job thread
and the provenance record both see the value the run was started with.
"""
import json
import os
import threading

import db
from engine_backends import thinking as thinking_switch

SWITCH_ENGINES = thinking_switch.NO_THINKING_ENGINES

_lock = threading.Lock()


def has_switch(engine_name) -> bool:
    return engine_name in SWITCH_ENGINES


def get_title_choice(drama_id: int) -> bool:
    return thinking_switch.title_thinking({"id": drama_id})


def save_title_choice(drama_id: int, thinking) -> None:
    """Remembers an explicit choice (None = not asked, nothing changes).
    Never raises: a run must not fail because its preference could not be written."""
    if thinking is None or not db.get_drama(drama_id):
        return
    path = os.path.join(db.drama_dir(drama_id), thinking_switch.TITLE_PREFS_FILENAME)
    tmp = path + ".tmp"
    with _lock:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"thinking": bool(thinking)}, fh)
            os.replace(tmp, path)
        except OSError:
            pass


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


def annotate_started(started: dict, thinking) -> dict:
    """The run-start result with whether the run thinks. `thinking` is the
    title's choice after saving this request's."""
    chain = [started.get("engine")] + list(started.get("fallback_engines") or [])
    return {**started, "thinking": effective(chain, thinking, started.get("reflect"))}
