"""
services/translate_thinking_service.py -- the "think harder" option of a
translation run, and the title's remembered choice for it.

Thinking is off unless a run asks for it. Only engines with a request switch
(DeepSeek, Ollama) can follow the choice; the others behave as they always
did and the Translate step says so.

The choice is remembered per title in `translate_prefs.json` in the drama's
own folder. Why a file and not a column: db.py is frozen, and the folder
already travels with the title in backups and is removed with it. A missing,
unreadable or hand-edited file reads as "off".
"""
import json
import os
import threading

import db
from engine_backends import thinking as thinking_switch

FILENAME = "translate_prefs.json"

# Hidden reasoning is billed as output tokens, but the estimate is made from
# the visible text, so it is a floor when thinking is on.
LOWER_BOUND_NOTE = ("Thinking is billed as output tokens the estimate can't predict, "
                    "so the real cost will be higher than this figure.")

_lock = threading.Lock()

SWITCH_ENGINES = thinking_switch.NO_THINKING_ENGINES


def has_switch(engine_name) -> bool:
    return engine_name in SWITCH_ENGINES


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), FILENAME)


def get_title_choice(drama_id: int) -> bool:
    with _lock:
        try:
            with open(_path(drama_id), encoding="utf-8") as fh:
                return json.load(fh).get("thinking") is True
        except (OSError, ValueError, AttributeError):
            return False


def save_title_choice(drama_id: int, thinking: bool) -> None:
    """Remembers the choice for the title's next run. Never raises: a run
    that started must not fail because its preference could not be written."""
    path = _path(drama_id)
    tmp = path + ".tmp"
    with _lock:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"thinking": bool(thinking)}, fh)
            os.replace(tmp, path)
        except OSError:
            pass


def effective(engine_names, requested) -> bool:
    """What the run really does: a request for thinking when no engine in the
    chain has a switch is ignored rather than recorded as if it had happened."""
    return bool(requested) and any(has_switch(n) for n in engine_names)


def run_setting(drama_id: int, engine_names, requested, reflect=False, remember=False) -> bool:
    """The thinking value for a run. `requested` None means "not asked": the
    title's remembered choice, so a retry or a CLI run matches the app.
    Reflect's passes go through the shared JSON helper, which has no switch,
    so a Reflect run never thinks. remember: keep an explicit choice for the
    title's next run."""
    if remember and requested is not None:
        save_title_choice(drama_id, requested)
    if requested is None:
        requested = get_title_choice(drama_id)
    return not reflect and effective(engine_names, requested)
