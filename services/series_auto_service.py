"""services/series_auto_service.py -- a series' auto-pipeline: new episodes go through Transcribe,
Translate, Auto QC and Export on the job queue by themselves, one at a time, inside a spending
ceiling and with a stop for review after the first episode.

State is one JSON document on `series.auto_pipeline`. `scan()` runs on the queue runner's thread
after each tick, so only one thread decides what to queue next; the lock covers the API's changes
(switch on or off, ceiling, Continue) against it.
"""

import json
import logging
import threading
from typing import Optional

import db
from engine_backends import shared as engine_shared
from lib.errors import ConflictError, InvalidInputError, NotFoundError, ServiceError, UnsupportedOperationError
from services import auth_service, ownership_service

# Pre-filled when the series is switched on. It is stored on the series either way, so there is no
# global setting: a paid run always starts from a figure its owner saw and accepted.
DEFAULT_CEILING_USD = 1.0
MAX_CEILING_USD = 1000.0

REVIEW, STOPPED, CEILING, QUEUE = "review", "stopped", "ceiling", "queue"

_lock = threading.Lock()
_log = logging.getLogger(__name__)


def _empty() -> dict:
    return {"on": False, "ceiling_usd": DEFAULT_CEILING_USD, "reviewed": False, "hold": "",
            "hold_kind": "", "current": None, "queued": [], "by": {}}


def _read(series_id: int) -> dict:
    state = _empty()
    try:
        raw = json.loads(db.get_series_auto_pipeline(series_id) or "{}")
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        return state
    state["on"] = raw.get("on") is True
    ceiling = raw.get("ceiling_usd")
    if isinstance(ceiling, (int, float)) and not isinstance(ceiling, bool) and 0 < ceiling <= MAX_CEILING_USD:
        state["ceiling_usd"] = float(ceiling)
    state["reviewed"] = raw.get("reviewed") is True
    if isinstance(raw.get("hold"), str) and raw.get("hold_kind") in (REVIEW, STOPPED, CEILING, QUEUE):
        state["hold"], state["hold_kind"] = raw["hold"][:300], raw["hold_kind"]
    if type(raw.get("current")) is int:
        state["current"] = raw["current"]
    state["queued"] = [i for i in raw.get("queued", []) if type(i) is int] \
        if isinstance(raw.get("queued"), list) else []
    raw_by = raw.get("by")
    by: dict = raw_by if isinstance(raw_by, dict) else {}
    uid = by.get("user_id")
    state["by"] = {"user_id": uid if type(uid) is int else None,
                   "override": by.get("override") is True, "at_pc": by.get("at_pc") is True}
    return state


def _write(series_id: int, state: dict) -> None:
    if not db.set_series_auto_pipeline(series_id, json.dumps(state)):
        raise NotFoundError("Series not found.")


def _spent(state: dict) -> float:
    return db.get_spend_for_dramas(state["queued"])


def _view(series_id: int, state: dict) -> dict:
    spent = _spent(state)
    return {"series_id": series_id, "on": state["on"], "ceiling_usd": state["ceiling_usd"],
            "spent_usd": round(spent, 4), "left_usd": round(max(state["ceiling_usd"] - spent, 0.0), 4),
            "reviewed": state["reviewed"], "hold": state["hold"], "hold_kind": state["hold_kind"],
            "current_drama_id": state["current"], "episodes_run": len(state["queued"])}


def _check_ceiling(value) -> float:
    # Not bool: True would pass as 1.
    if type(value) not in (int, float) or not 0 < value <= MAX_CEILING_USD:
        raise InvalidInputError(f"The spending ceiling must be more than $0 and at most ${MAX_CEILING_USD:.0f}.")
    return round(float(value), 2)


def _audit(principal, series_id: int, what: str) -> None:
    auth_service.write_audit(ownership_service.user_id(principal), "series.auto_pipeline",
                             f"series {series_id}: {what}")


# --- the API's side ----------------------------------------------------------------

def get_state(principal, series_id: int) -> dict:
    ownership_service.require_visible(principal, "series", series_id)
    with _lock:
        return _view(series_id, _read(series_id))


def switch_on(principal, series_id: int, ceiling_usd, at_pc: bool) -> dict:
    """Starts the auto-pipeline as the caller: their permissions and keys are used for each episode.
    Turning it on again after it was off starts a fresh spending count and a fresh first-episode
    review."""
    from services import queue_service
    ownership_service.require_editable(principal, "series", series_id)
    ceiling = _check_ceiling(ceiling_usd)
    with _lock:
        state = _read(series_id)
        if state["on"]:
            raise ConflictError("Auto-run is already on for this series.")
        state = {**_empty(), "on": True, "ceiling_usd": ceiling,
                 "by": {"user_id": ownership_service.user_id(principal),
                        "override": queue_service._overrides(principal), "at_pc": bool(at_pc)}}
        _write(series_id, state)
        _audit(principal, series_id, f"auto-run on, ceiling ${ceiling:.2f}")
        return _view(series_id, state)


def switch_off(principal, series_id: int) -> dict:
    """Stops queueing new episodes and withdraws this series' items that have not started. A running
    one carries on (Cancel stops it on the Jobs page)."""
    from services import queue_service
    ownership_service.require_editable(principal, "series", series_id)
    with _lock:
        state = _read(series_id)
        state["on"] = False
        _write(series_id, state)
        _audit(principal, series_id, "auto-run off")
        view = _view(series_id, state)
    queue_service.remove_waiting_auto_items(series_id)
    return view


def set_ceiling(principal, series_id: int, ceiling_usd) -> dict:
    """Lowering it below what was already spent is allowed; it just holds the series."""
    ownership_service.require_editable(principal, "series", series_id)
    ceiling = _check_ceiling(ceiling_usd)
    with _lock:
        state = _read(series_id)
        state["ceiling_usd"] = ceiling
        if state["hold_kind"] == CEILING and ceiling > _spent(state):
            state["hold"], state["hold_kind"] = "", ""
        _write(series_id, state)
        _audit(principal, series_id, f"ceiling ${ceiling:.2f}")
        return _view(series_id, state)


def resume(principal, series_id: int) -> dict:
    """The "Continue" button. After the first episode: it was reviewed, go on. After a stop: skip the
    episode that stopped, unless Retry has finished it since."""
    from services import queue_service
    ownership_service.require_editable(principal, "series", series_id)
    with _lock:
        state = _read(series_id)
        if not state["on"] or not state["hold"]:
            raise ConflictError("This series is not waiting for you.")
        kind = state["hold_kind"]
        if kind == REVIEW:
            state["reviewed"] = True
        elif kind == STOPPED and state["current"] is not None:
            mine = [i for i in queue_service.auto_items(series_id) if i["drama_id"] == state["current"]]
            if not mine or any(i["status"] != queue_service.DONE for i in mine):
                state["current"] = None
        elif kind == CEILING and _spent(state) >= state["ceiling_usd"]:
            raise UnsupportedOperationError("The spending ceiling is used up. Raise it first.")
        state["hold"], state["hold_kind"] = "", ""
        _write(series_id, state)
        _audit(principal, series_id, f"continue after {kind}")
        return _view(series_id, state)


# --- the queue's side --------------------------------------------------------------

def require_on(series_id: int) -> None:
    if not _read(series_id)["on"]:
        raise UnsupportedOperationError("Auto-run was switched off for this series.")


def ceiling_left(series_id: int) -> float:
    """What the series may still spend, always above 0: the translate run takes it as its own cost cap,
    and a cap of 0 would mean "no cap" to the resolver."""
    state = _read(series_id)
    if not state["on"]:
        raise UnsupportedOperationError("Auto-run was switched off for this series.")
    left = state["ceiling_usd"] - _spent(state)
    if left <= 0.005:
        raise UnsupportedOperationError(
            f"This series' spending ceiling (${state['ceiling_usd']:.2f}) is used up.")
    return left


def _title(drama: dict) -> str:
    return drama.get("title_en") or drama.get("title_zh") or f"Title {drama['id']}"


def _hold(series_id: int, state: dict, kind: str, text: str) -> None:
    state["hold"], state["hold_kind"] = text[:300], kind
    _write(series_id, state)


def _next_episode(series_id: int, state: dict) -> Optional[dict]:
    """The first title in reading order that has media, no lines and was not queued by this
    pipeline already. A title queued once is never queued again on its own, so a failed one is
    left for Retry rather than looping."""
    for drama in db.list_dramas_by_series(series_id):
        if drama["id"] in state["queued"]:
            continue
        if not (drama.get("audio_filename") or drama.get("source_video_filename")):
            continue
        if (drama.get("transcript_mode") or "") == "have_transcript":
            continue
        if db.load_lines(drama["id"]):
            continue
        return drama
    return None


def _scan_series(series_id: int) -> None:
    from services import queue_service as q
    state = _read(series_id)
    if not state["on"] or state["hold"]:
        return
    items = q.auto_items(series_id)
    if any(i["status"] in (q.WAITING, q.RUNNING) for i in items):
        return     # one episode at a time, so the ceiling is checked between episodes
    cur = state["current"]
    if cur is not None:
        drama = db.get_drama(cur)
        name = _title(drama) if drama else f"Title {cur}"
        mine = [i for i in items if i["drama_id"] == cur]
        bad = next((i for i in mine if i["status"] in q.RETRYABLE), None)
        if bad is not None:
            why = bad.get("reason") or bad["status"]
            return _hold(series_id, state, STOPPED,
                         f"{name} stopped at {bad['step']}: {why} Retry it on the Jobs page, "
                         "or press Continue to skip it.")
        if not mine:
            # Clearing finished items removes the evidence; trust only a title that has English.
            if not any((ln.get("en") or "").strip() for ln in db.load_lines(cur)):
                return _hold(series_id, state, STOPPED,
                             f"{name}'s queue items were removed before they finished.")
        state["current"] = None
        if not state["reviewed"]:
            return _hold(series_id, state, REVIEW,
                         f"{name} is done. Check its subtitles; press Continue to run the rest.")
        _write(series_id, state)
    spent = _spent(state)
    if spent >= state["ceiling_usd"] - 0.005:
        return _hold(series_id, state, CEILING,
                     f"Spending ceiling reached (${spent:.2f} of ${state['ceiling_usd']:.2f}).")
    nxt = _next_episode(series_id, state)
    if nxt is None:
        return
    try:
        q.add_auto_episode(series_id, nxt["id"], state["by"])
    except ServiceError as exc:
        return _hold(series_id, state, QUEUE, f"{_title(nxt)} could not be queued: {exc.message}")
    state["queued"].append(nxt["id"])
    state["current"] = nxt["id"]
    _write(series_id, state)


def scan() -> None:
    """One pass over every series with auto-run on. Never raises: a broken series is logged and the
    others carry on."""
    for series_id, _raw in db.list_series_auto_pipelines():
        try:
            with _lock:
                _scan_series(series_id)
        except Exception as exc:
            _log.warning("Series auto-run %s failed: %s", series_id,
                         engine_shared.redact_secrets(str(exc)))
