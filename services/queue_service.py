"""
services/queue_service.py -- the user-managed job queue on the Jobs page:
titles and steps lined up to run one at a time, now or once at a set time.

The queue is one JSON document in app_settings (`QUEUE_KEY`). An item names
only a title and a step; every option comes from the title's saved choices
when the item starts, through the same service entry points the stage
buttons use, so GPU slots, the exclusive hold, ownership and the spending
caps apply exactly as for a click. An item that cannot start is marked
skipped with the plain reason and the queue moves on.

The runner is one daemon thread (`start_runner` / `stop_runner`, started
with the server). It holds `_lock` only while it reads or writes the
document, never while a job runs, and reads job state through
`jobs_service.get_job`, the same call the Jobs page uses.

Nothing is resumed after a restart: a running item becomes interrupted and a
start time that passed while the app was off (or the PC slept through it)
becomes "missed", which only the owner can turn into a run, and a restored
library brings back no start time.
"""

import datetime
import logging
import re
import threading
import time
import uuid
from typing import Optional

import db
import translate_engines
from lib import model_offline
from jobs import user_caps
from lib.errors import (ConflictError, ForbiddenError, InvalidInputError, NotFoundError,
                        RateLimitedError, ServiceError, UnsupportedOperationError)
from services import (auth_service, jobs_service,
                      model_readiness_service, ownership_service, profile_service, settings_service, transcribe_service,
                      transcribe_options_service, translate_run_service, translate_thinking_service)
from services.queue_export_settings import (EXPORT_KEYS, changed_rows as _export_changed_rows,
                                            check_export_settings, export_fields, run_export)
from services.queue_translate_settings import (TRANSLATE_KEYS, _NO_CAP, _cap_left, _check_translate_spend, _queued_model_and_batch,
                                               _translate_cost, check_translate_settings, translate_changed_rows, translate_fields)
from services.queue_transcribe_settings import (_LANGUAGES, _changed_rows, _on, _row, _transcribe_fields,
                                                 _transcribe_rows, _words)

QUEUE_KEY = "job_queue"
QUEUE_VERSION = 1
MAX_ITEMS = 200
# One member can't fill the shared list and lock everyone else out.
MAX_ITEMS_PER_MEMBER = 50
STEPS = ("transcribe", "translate", "export")
POLL_SECONDS = 5.0
# A start time first seen this late was slept through (the PC was asleep, so
# nothing ticked) and becomes "missed", like one that passed while Baihe was
# off. Far above any tick delay: an export the runner does itself, a slow disk.
SCHEDULE_LATE_SECONDS = 30 * 60

WAITING, RUNNING, DONE, FAILED, SKIPPED, INTERRUPTED, CANCELLED = (
    "waiting", "running", "done", "failed", "skipped", "interrupted", "cancelled")
FINISHED = (DONE, FAILED, SKIPPED, INTERRUPTED, CANCELLED)
RETRYABLE = (FAILED, SKIPPED, INTERRUPTED, CANCELLED)
IDLE, ACTIVE, PAUSED = "idle", "running", "paused"

_HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_MAX_REASON = 300
_STOPPED_AFTER_FAILURE = "Stopped because an item failed. Start the queue to go on."
_INTERRUPTED = "Interrupted: Baihe stopped while this ran. Press Retry to run it again."
_WAITING_FOR_CAP = ("Waiting for other jobs of the account that added it to finish; it "
                    "starts when one of them ends.")
# Marks the document as this process's. A document without it came from an
# earlier run or a restored backup, and is settled before anything reads it,
# so neither can resume work on its own.
_INSTANCE = uuid.uuid4().hex

_lock = threading.Lock()
_log = logging.getLogger(__name__)


def _clock() -> float:
    return time.time()


# --- the document -----------------------------------------------------------

def _empty() -> dict:
    return {"version": QUEUE_VERSION, "instance": _INSTANCE, "state": IDLE,
            "stop_on_failure": False, "schedule": None, "notice": None, "next_id": 1,
            "items": []}


_SETTING_KEYS = {"transcribe": transcribe_options_service.QUEUE_SETTING_KEYS,
                 "translate": TRANSLATE_KEYS, "export": EXPORT_KEYS}


def _clean_settings(raw, step: str) -> dict:
    """A stored item's own settings, reduced to the keys and kinds its step's run accepts:
    the document is editable on disk, and these reach the step's start."""
    if not isinstance(raw, dict):
        return {}
    keep = {}
    for key in _SETTING_KEYS[step]:
        value = raw.get(key)
        if isinstance(value, (bool, int, str)):
            keep[key] = value
    return keep


def _clean_item(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    try:
        item_id, drama_id = int(raw["id"]), int(raw["drama_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if raw.get("step") not in STEPS or raw.get("status") not in (WAITING, RUNNING) + FINISHED:
        return None
    uid = raw.get("user_id")
    return {"id": item_id, "drama_id": drama_id, "step": raw["step"], "status": raw["status"],
            "reason": _short(raw.get("reason")), "job_id": raw.get("job_id") or None,
            "user_id": uid if isinstance(uid, int) else None,
            "override": bool(raw.get("override")),
            "settings": _clean_settings(raw.get("settings"), raw["step"]),
            # Items saved before "at_pc" was recorded count as away from the PC:
            # "override" is also true for an admin on the admin listener who
            # was not at the PC.
            "at_pc": bool(raw.get("at_pc", False)),
            "added_at": raw.get("added_at"), "started_at": raw.get("started_at"),
            "finished_at": raw.get("finished_at")}


def _load(now: float = None, settle: bool = False) -> dict:
    """Caller holds _lock. A stored document from another version, or one
    that is not a queue at all, starts an empty queue rather than guessing."""
    raw = db.get_app_setting(QUEUE_KEY, None)
    if not isinstance(raw, dict) or raw.get("version") != QUEUE_VERSION:
        return _empty()
    doc = _empty()
    doc["state"] = raw.get("state") if raw.get("state") in (IDLE, ACTIVE, PAUSED) else IDLE
    doc["stop_on_failure"] = bool(raw.get("stop_on_failure"))
    sched = raw.get("schedule")
    if isinstance(sched, dict) and isinstance(sched.get("start_at"), (int, float)) \
            and _HHMM.match(str(sched.get("time") or "")):
        doc["schedule"] = {"time": sched["time"], "start_at": float(sched["start_at"]),
                           "missed": bool(sched.get("missed"))}
    doc["notice"] = _short(raw.get("notice")) or None
    doc["items"] = [i for i in (_clean_item(r) for r in raw.get("items") or [])
                    if i is not None][:MAX_ITEMS]
    ids = [i["id"] for i in doc["items"]]
    doc["next_id"] = max([int(raw.get("next_id") or 1)] + [i + 1 for i in ids])
    if settle or raw.get("instance") != _INSTANCE:
        _settle(doc, _clock() if now is None else now)
    return doc


def _settle(doc: dict, now: float) -> None:
    """Nothing resumes in a new process: a running item is interrupted, the
    queue stops, and a start time already passed becomes "missed"."""
    for item in doc["items"]:
        if item["status"] == RUNNING:
            _finish(item, INTERRUPTED, _INTERRUPTED, now)
    doc["state"] = IDLE
    if doc["schedule"] and now >= doc["schedule"]["start_at"]:
        doc["schedule"]["missed"] = True
    doc["instance"] = _INSTANCE
    doc["settled"] = True


def _save(doc: dict) -> None:
    doc.pop("settled", None)
    db.set_app_setting(QUEUE_KEY, doc)


def _short(text) -> str:
    if not text:
        return ""
    return translate_engines.redact_secrets(str(text))[:_MAX_REASON]


def _find(doc: dict, item_id: int) -> dict:
    for item in doc["items"]:
        if item["id"] == item_id:
            return item
    raise NotFoundError("No such queue item.")


# --- who may see and change what ----------------------------------------------

def _overrides(principal) -> bool:
    """The local owner, auth off, or an admin at the PC: the queue's owner."""
    return principal is None or bool(principal.get("is_local_owner")) \
        or bool(principal.get("is_admin") and principal.get("admin_override"))


def _title_owner(drama_id: int):
    row = db.get_item_ownership("drama", drama_id)
    return row.get("owner_user_id") if row else None


def _owns_title(principal, drama_id: int) -> bool:
    if not ownership_service.can_edit_drama(principal, drama_id):
        return False
    if _overrides(principal):
        return True
    uid = ownership_service.user_id(principal)
    return uid is not None and _title_owner(drama_id) == uid


def _sees(principal, item: dict, owner_of=_title_owner) -> bool:
    if ownership_service.sees_every_job(principal):
        return True
    uid = ownership_service.user_id(principal)
    return uid is not None and owner_of(item["drama_id"]) == uid


def _may_change(principal, item: dict, owner_of=_title_owner) -> bool:
    if _overrides(principal):
        return True
    uid = ownership_service.user_id(principal)
    return uid is not None and item["user_id"] == uid and _sees(principal, item, owner_of)


def _require_controls(principal) -> None:
    # Starting the queue starts every account's items on the owner's PC and
    # keys, so only the queue's owner may start, pause or schedule it.
    if not _overrides(principal):
        raise ForbiddenError("Only the PC owner can start, pause or schedule the queue.")


# --- reading -----------------------------------------------------------------

def _title_name(drama: Optional[dict]) -> str:
    if not drama:
        return "Deleted title"
    return drama.get("title_en") or drama.get("title_zh") or f"Title {drama.get('id')}"


def _translate_engine(drama: dict) -> str:
    from services import engine_routing_service
    return (drama.get("translation_engine")
            or engine_routing_service.resolve_capability("translation.cheap"))


def _item_changed_rows(item: dict, drama: Optional[dict]) -> list:
    if not item["settings"] or not drama:
        return []
    step_rows = {"translate": translate_changed_rows, "export": _export_changed_rows}.get(item["step"])
    return step_rows(item["settings"]) if step_rows else _changed_rows(item, drama)


def _view_item(principal, item: dict, drama: Optional[dict], owner_of) -> dict:
    out = {k: item[k] for k in ("id", "drama_id", "step", "status", "job_id",
                                "added_at", "started_at", "finished_at")}
    out["reason"] = item["reason"] or None
    out["title"] = _title_name(drama)
    out["mine"] = _may_change(principal, item, owner_of)
    out["engine"] = None
    out["paid"] = False
    out["changed_settings"] = _item_changed_rows(item, drama)
    if item["step"] == "translate" and drama:
        engine = _translate_engine(drama)
        out["engine"] = engine
        out["paid"] = engine not in translate_engines.FREE_ENGINES
    return out


def get_queue(principal) -> dict:
    with _lock:
        doc = _load()
    # Up to MAX_ITEMS rows often share a title: read each title once per call.
    dramas, owners = {}, {}

    def drama_of(did):
        if did not in dramas:
            dramas[did] = db.get_drama(did)
        return dramas[did]

    def owner_of(did):
        if did not in owners:
            owners[did] = _title_owner(did)
        return owners[did]
    monthly = settings_service.get_monthly_cap_usd()
    return {
        "version": QUEUE_VERSION,
        "state": doc["state"],
        "stop_on_failure": doc["stop_on_failure"],
        "schedule": dict(doc["schedule"]) if doc["schedule"] else None,
        "notice": doc["notice"],
        "max_items": MAX_ITEMS,
        "can_control": _overrides(principal),
        "monthly_cap_usd": monthly,
        "items": [_view_item(principal, i, drama_of(i["drama_id"]), owner_of)
                  for i in doc["items"] if _sees(principal, i, owner_of)],
    }


# --- saved settings of a step -------------------------------------------------

def _translate_rows(drama: dict, settings: Optional[dict] = None) -> list:
    from engine_backends import engine_registry
    import translation_guide
    from services import language_pack_service, vdb_names_service
    drama_id = drama["id"]
    args = _translate_run_args(drama)
    engine = args["engine"]
    # The queued run repeats the title's last run (start_translate_run reads the same values).
    last = profile_service.title_run_defaults(drama, engine, engine)
    saved_model, saved_batch = _queued_model_and_batch(drama, engine)
    # The item's own model and batch size, so the cost line prices what the run will spend.
    saved_model = (settings or {}).get("model", saved_model)
    saved_batch = (settings or {}).get("batch_size", saved_batch)
    model = saved_model or translate_engines.effective_default_model(engine)
    novel = drama.get("content_mode") == "novel_narration"
    preset = translation_guide.STYLE_PRESETS[last.get("style_preset") or ("novel" if novel else "audio_drama")]
    options = translate_run_service.get_translate_config_defaults(novel, engine, model)
    thinking = translate_thinking_service.effective(
        [engine], translate_thinking_service.get_title_choice(drama_id))
    series_id = drama.get("series_id")
    terms = len(db.list_glossary_terms(series_id)) if series_id else 0
    packs = [p["name"] for p in language_pack_service.get_title_packs(drama_id)["packs"] if p["enabled"]]
    override = settings_service.reply_guard_override()
    guard = engine_registry.default_reply_guard(engine, model) if override is None else override
    return [
        _row("Engine and model", model or "", engine),
        _row("Tone and character voice", preset["label"]),
        _row("Source language", _LANGUAGES.get(drama.get("source_language") or "zh", "Chinese")),
        _row("English variant", args["locale"]),
        _row("Style note", "Set" if args["style_note"] else "None"),
        _row("Batch size", saved_batch or options["batch_size"]),
        _row("Thinking", _on(thinking)),
        _row("Glossary", f"On, {terms} terms" if terms else "Off, no glossary terms yet"),
        _row("Language packs", ", ".join(packs) if packs else "Off"),
        _row("VTuber names", _on(vdb_names_service.is_enabled(drama_id))),
        _row("Reply check", _on(guard)),
        # Only a bulk run waits for DeepSeek's off-peak hours; the queue starts a normal one.
        _row("Off-peak start", "Off: the queue runs it right away"),
        _row("Cost", _translate_cost(drama_id, engine, saved_model, saved_batch)),
        _row("Monthly cap left", _cap_left()),
    ]


def step_settings(principal, drama_id: int, step: str, holds_paid: bool = False,
                  holds_edit: bool = True, settings: Optional[dict] = None) -> dict:
    """Read-only: the settings a queued `step` would use for this title,
    resolved by the same functions the queue's own run calls."""
    if step not in STEPS:
        raise InvalidInputError("Step must be transcribe, translate or export.")
    drama = db.get_drama(drama_id)
    if drama is None or not _owns_title(principal, drama_id):
        raise NotFoundError("Title not found.")
    if step == "translate":
        rows = _translate_rows(drama, check_translate_settings(drama, _translate_engine(drama), settings, holds_paid))
    elif step == "transcribe":
        rows = _transcribe_rows(drama)
    else:
        rows = [_row("Format", "SRT"), _row("Language", "English"),
                _row("Saved as", "The title's subtitle download")]
    if step == "transcribe":
        fields = _transcribe_fields(drama, holds_paid, holds_edit)
    elif step == "translate":
        fields = translate_fields(drama, _translate_run_args(drama), holds_paid, holds_edit)
    else:
        fields = export_fields(holds_edit)
    return {"drama_id": drama_id, "step": step, "title": _title_name(drama), "rows": rows,
            "fields": fields}


# --- changing items ----------------------------------------------------------

def _check_addable(principal, drama_id: int, step: str, holds_paid: bool, at_pc: bool,
                   settings: Optional[dict], holds_edit: bool) -> dict:
    """The checks every add shares; returns the validated per-item settings."""
    if step not in STEPS:
        raise InvalidInputError("Step must be transcribe, translate or export.")
    drama = db.get_drama(drama_id)
    if drama is None or not _owns_title(principal, drama_id):
        raise NotFoundError("Title not found.")
    if step == "translate" and not holds_paid \
            and _translate_engine(drama) not in translate_engines.FREE_ENGINES:
        raise ForbiddenError("This title translates with a paid engine, which your account "
                             "can't use. Ask the owner, or pick a free engine for the title.")
    if step == "translate":
        settings = check_translate_settings(drama, _translate_engine(drama), settings, holds_paid)
    elif step == "export":
        settings = check_export_settings(settings)
    else:
        settings = transcribe_options_service.check_run_overrides(drama, settings)
    # Saving these same options on the title takes lines.edit; a queued item must not be a way around it.
    if settings and not holds_edit:
        raise ForbiddenError("Changing a step's settings needs permission to edit lines.")
    if settings.get("use_groq") and not holds_paid:
        raise ForbiddenError("Groq is a paid engine, which your account can't use.")
    if step == "transcribe":
        model_readiness_service.require_ready(
            at_pc, model_readiness_service.for_transcribe, drama_id, overrides=settings,
            run_diarize=transcribe_options_service.run_start(drama)["run_diarize"])
    return settings


def _append_items(principal, drama_id: int, entries: list, at_pc: bool) -> None:
    """Appends `entries` (step, settings) in order, all or none, under one lock."""
    with _lock:
        doc = _load()
        if len(doc["items"]) + len(entries) > MAX_ITEMS:
            raise ConflictError(f"The queue holds at most {MAX_ITEMS} items. "
                                "Clear finished items first.")
        uid = ownership_service.user_id(principal)
        if not _overrides(principal) and sum(
                1 for i in doc["items"] if i["user_id"] == uid) + len(entries) > MAX_ITEMS_PER_MEMBER:
            raise ConflictError(f"You can have at most {MAX_ITEMS_PER_MEMBER} items in the "
                                "queue. Clear your finished items first.")
        for step, settings in entries:
            doc["items"].append({
                "id": doc["next_id"], "drama_id": drama_id, "step": step, "status": WAITING,
                "reason": "", "job_id": None, "user_id": uid,
                "override": _overrides(principal), "at_pc": at_pc, "added_at": _clock(),
                "settings": settings, "started_at": None, "finished_at": None})
            doc["next_id"] += 1
        _save(doc)
    _wake.set()


def add_item(principal, drama_id: int, step: str, holds_paid: bool, *, at_pc: bool,
             settings: Optional[dict] = None, holds_edit: bool = True) -> dict:
    """Validates and appends one item. `settings` are this item's own
    changes to the title's saved options; the title is not changed. The title must exist and be the
    caller's; a Translate item on a paid engine needs `engines.paid`, as the
    Translate stage's run does; a Transcribe item added away from the PC
    needs its models on disk (model_readiness_service)."""
    settings = _check_addable(principal, drama_id, step, holds_paid, at_pc, settings, holds_edit)
    _append_items(principal, drama_id, [(step, settings)], at_pc)
    return get_queue(principal)


def _add_steps(principal, drama_id: int, steps: tuple, holds_paid: bool, at_pc: bool) -> None:
    """Every step passes the single-step checks first, so a title the caller can't run
    all of adds nothing; then the steps go in, in order, with the title's saved options."""
    for step in steps:
        _check_addable(principal, drama_id, step, holds_paid, at_pc, None, True)
    _append_items(principal, drama_id, [(step, {}) for step in steps], at_pc)


def add_all_steps(principal, drama_id: int, holds_paid: bool, *, at_pc: bool) -> dict:
    """Appends Transcribe, Translate and Export for one title, in that order. Nothing starts here."""
    _add_steps(principal, drama_id, STEPS, holds_paid, at_pc)
    return get_queue(principal)


def add_bulk(principal, drama_ids: list, step: str, holds_paid: bool, *, at_pc: bool) -> dict:
    """Adds `step` ("all" = the chain) for each title. A title failing the add checks is
    skipped with its reason; the rest go in. Nothing starts."""
    if step != "all" and step not in STEPS:
        raise InvalidInputError("Step must be transcribe, translate, export or all.")
    steps = STEPS if step == "all" else (step,)
    added, skipped = [], []
    full = None
    for drama_id in dict.fromkeys(drama_ids):
        if full:
            skipped.append({"drama_id": drama_id, "reason": full})
            continue
        try:
            _add_steps(principal, drama_id, steps, holds_paid, at_pc)
            added.append(drama_id)
        except ServiceError as e:
            reason = _short(e.message)
            # A full queue or member cap refuses every later title the same way.
            full = reason if isinstance(e, ConflictError) else None
            skipped.append({"drama_id": drama_id, "reason": reason})
    return {"queue": get_queue(principal), "added": added, "skipped": skipped}


def _mutate_item(principal, item_id: int, change) -> dict:
    with _lock:
        doc = _load()
        item = _find(doc, item_id)
        if not _sees(principal, item):
            raise NotFoundError("No such queue item.")
        if not _may_change(principal, item):
            raise ForbiddenError("Only the account that added this item can change it.")
        change(doc, item)
        _save(doc)
    _wake.set()
    return get_queue(principal)


def move_item(principal, item_id: int, direction: str) -> dict:
    """Swaps with the nearest item the caller can see, so a member's move
    never depends on, or reveals, items they can't see."""
    if direction not in ("up", "down"):
        raise InvalidInputError("Direction must be up or down.")

    def change(doc, item):
        items = doc["items"]
        here = items.index(item)
        visible = [n for n, i in enumerate(items) if _sees(principal, i)]
        pos = visible.index(here)
        other = pos - 1 if direction == "up" else pos + 1
        if 0 <= other < len(visible):
            there = visible[other]
            items[here], items[there] = items[there], items[here]
    return _mutate_item(principal, item_id, change)


def remove_item(principal, item_id: int) -> dict:
    def change(doc, item):
        if item["status"] == RUNNING:
            raise ConflictError("This item is running. Cancel its job in the list below first.")
        doc["items"].remove(item)
    return _mutate_item(principal, item_id, change)


def retry_item(principal, item_id: int, *, at_pc: bool) -> dict:
    def change(doc, item):
        if item["status"] not in RETRYABLE:
            raise ConflictError("Only a failed, skipped, cancelled or interrupted item can be retried.")
        item.update(status=WAITING, reason="", job_id=None, started_at=None, finished_at=None,
                    at_pc=item["at_pc"] and at_pc)
    return _mutate_item(principal, item_id, change)


def clear_finished(principal) -> dict:
    with _lock:
        doc = _load()
        doc["items"] = [i for i in doc["items"] if not (
            i["status"] in FINISHED and _sees(principal, i) and _may_change(principal, i))]
        _save(doc)
    return get_queue(principal)


# --- run controls --------------------------------------------------------------

def start(principal) -> dict:
    _require_controls(principal)
    with _lock:
        doc = _load()
        doc["state"] = ACTIVE
        doc["notice"] = None
        if doc["schedule"] and doc["schedule"]["missed"]:
            doc["schedule"] = None
        _save(doc)
    _wake.set()
    return get_queue(principal)


def pause(principal) -> dict:
    """Stops new starts; a running item's job carries on (Cancel stops it)."""
    _require_controls(principal)
    with _lock:
        doc = _load()
        if doc["state"] == ACTIVE:
            doc["state"] = PAUSED
        _save(doc)
    return get_queue(principal)


def next_occurrence(hhmm: str, now: float) -> float:
    """The next time the PC's clock reads hhmm, strictly after now."""
    m = _HHMM.match(hhmm or "")
    if not m:
        raise InvalidInputError("Pick a start time as HH:MM.")
    here = datetime.datetime.fromtimestamp(now)
    at = here.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if at.timestamp() <= now:
        at = at + datetime.timedelta(days=1)
    return at.timestamp()


def set_schedule(principal, hhmm: Optional[str], now: float = None) -> dict:
    """hhmm None clears the schedule (also a missed one)."""
    _require_controls(principal)
    now = _clock() if now is None else now
    sched = None if hhmm is None else {"time": hhmm, "start_at": next_occurrence(hhmm, now),
                                       "missed": False}
    with _lock:
        doc = _load()
        doc["schedule"] = sched
        _save(doc)
    _wake.set()
    return get_queue(principal)


def set_policy(principal, stop_on_failure: bool) -> dict:
    _require_controls(principal)
    with _lock:
        doc = _load()
        doc["stop_on_failure"] = bool(stop_on_failure)
        _save(doc)
    return get_queue(principal)


# --- starting one item -------------------------------------------------------

def _run_principal(item: dict):
    """The adder as they are now: permissions are re-read, so a revoked grant
    or a deactivated account stops their waiting items."""
    if item["user_id"] is None:
        return None
    row = db.auth_get_user(item["user_id"])
    if not row or not row["is_active"]:
        raise ForbiddenError("The account that added this item can no longer start jobs.")
    return {"user_id": row["id"], "email": row["email"], "is_admin": bool(row["is_admin"]),
            "admin_override": bool(row["is_admin"]) and item["override"],
            "permissions": auth_service.effective_permissions(row["id"])}


def _holds(principal, permission: str) -> bool:
    return principal is None or permission in principal.get("permissions", ())


def _start_transcribe(principal, drama: dict, settings: dict) -> str:
    # Checked on the title as this item will run it, at start: the adder's permission and the
    # title's own choices may both have changed since it was added.
    if settings and not _holds(principal, "lines.edit"):
        raise ForbiddenError("This item changes the step's settings, which needs permission to edit lines.")
    effective = transcribe_options_service.apply_run_overrides(drama, settings)
    if effective.get("use_groq") and not _holds(principal, "engines.paid"):
        raise ForbiddenError("This item transcribes with Groq, which needs paid-engine permission.")
    if (effective.get("transcript_mode") or "have_transcript") == "have_transcript":
        raise UnsupportedOperationError("This title transcribes from a transcript you paste, "
                                        "so it can't run from the queue.")
    # The form's saved run choices (speaker detection, names, prompt), as pressing Transcribe there sends them.
    args = transcribe_options_service.run_start_args(drama)
    if settings:
        args["overrides"] = settings
    return transcribe_service.start_transcribe_run(drama["id"], **args)["job_id"]


def _translate_run_args(drama: dict) -> dict:
    """What a queued translate run is started with beyond the title's own
    saved options; "Saved settings" on the Jobs page reads the same values."""
    default_locale = settings_service.get("default_locale")
    locale, style_note = profile_service.locale_and_note(
        drama, default_locale if default_locale in settings_service.LOCALE_CHOICES else "en-US",
        settings_service.get("default_style_note") or "")
    return {"engine": _translate_engine(drama), "style_note": style_note, "locale": locale}


def _start_translate(principal, drama: dict, settings: Optional[dict] = None) -> str:
    args = _translate_run_args(drama)
    holds_paid = _holds(principal, "engines.paid")
    if args["engine"] not in translate_engines.FREE_ENGINES and not holds_paid:
        raise ForbiddenError("This title translates with a paid engine, which the account "
                             "that added it can't use.")
    # Checked again at start: the adder's permission and the title's engine may have changed since.
    settings = check_translate_settings(drama, args["engine"], settings, holds_paid)
    if settings and not _holds(principal, "lines.edit"):
        raise ForbiddenError("This item changes the step's settings, which needs permission to edit lines.")
    saved = _queued_model_and_batch(drama, args["engine"])
    _check_translate_spend(drama["id"], settings.get("model", saved[0]), settings.get("batch_size", saved[1]))
    own = {k: settings[k] for k in ("style_preset", "batch_size") if k in settings}
    return translate_thinking_service.start_with_thinking(
        translate_run_service.start_translate_run, drama["id"], None, False,
        model=settings.get("model"), style_note=args["style_note"],
        locale=settings.get("locale", args["locale"]), record_run=not settings, **own,
        allow_paid_summary=holds_paid and settings_service.get_monthly_cap_usd() > 0)["job_id"]


def _start_item(item: dict) -> Optional[str]:
    """Starts the item as its adder; returns the job id, or None for a step
    that finished inline. Raises ServiceError with the reason to skip it."""
    drama = db.get_drama(item["drama_id"])
    if drama is None:
        raise NotFoundError("The title was deleted.")
    principal = _run_principal(item)
    if not _owns_title(principal, item["drama_id"]):
        raise ForbiddenError("The account that added this item no longer owns the title.")
    needed = "lines.read" if item["step"] == "export" else "jobs.start"
    if not _holds(principal, needed):
        raise ForbiddenError("The account that added this item can no longer do this step.")
    token = ownership_service.bind_request()
    # The runner thread outlives this item: the adder's offline mark is set
    # for this start only.
    offline = model_offline.set_offline(not item["at_pc"])
    try:
        # Records the job as the adder's, as if they had pressed the button.
        ownership_service.note_acting_principal(principal)
        if item["step"] == "transcribe":
            # Again at start: the title's model choice may have changed since it was added.
            model_readiness_service.require_ready(
                item["at_pc"], model_readiness_service.for_transcribe, drama["id"],
                overrides=item.get("settings") or None,
                run_diarize=transcribe_options_service.run_start(drama)["run_diarize"])
            return _start_transcribe(principal, drama, item.get("settings") or {})
        if item["step"] == "translate":
            return _start_translate(principal, drama, item.get("settings") or {})
        run_export(drama, item.get("settings") or {})
        return None
    finally:
        model_offline.reset(offline)
        ownership_service.unbind_request(token)


# --- the runner --------------------------------------------------------------

def _job_outcome(job_id: str):
    """(finished, status, reason) for an item's job, read like the Jobs page.
    The in-memory state is checked first: it is the authority while the job
    runs here, and the record can lag when a status write failed."""
    import background_jobs
    live = background_jobs.get_status(job_id)
    if live and live.get("status") in ("queued", "running"):
        return False, RUNNING, ""
    try:
        job = jobs_service.get_job(job_id)
    except NotFoundError:
        return True, FAILED, "The job's record was removed before its result could be read."
    if job["status"] in ("queued", "running"):
        if not live:
            return False, RUNNING, ""
        # The raw in-memory error: scrub it the way get_job scrubs a record's.
        job = {"status": live["status"],
               "error": jobs_service.scrub_text(live.get("error") or ""),
               "outcome": {"done": "ok", "error": "failed",
                           "cancelled": "cancelled"}.get(live["status"])}
    outcome = job.get("outcome")
    if outcome == "failed":
        return True, FAILED, job.get("outcome_message") or job.get("error") or "The job failed."
    if outcome in ("cancelled", "interrupted"):
        return True, CANCELLED, job.get("outcome_message") or "The job was cancelled."
    note = job.get("outcome_message") if outcome in ("partial", "kept_existing") else ""
    return True, DONE, note or ""


def _finish(item: dict, status: str, reason: str, now: float) -> None:
    item.update(status=status, reason=_short(reason), finished_at=now)


def tick(now: float = None) -> None:
    """One pass: fire a due schedule, settle the running item, start the next.
    Safe to call from tests with an injected clock."""
    from jobs import job_launch
    if job_launch.is_stopping():
        return     # the stop cancels jobs; the next start marks the item interrupted
    now = _clock() if now is None else now
    with _lock:
        doc = _load(now)
        changed = doc.get("settled", False)
        sched = doc["schedule"]
        if sched and not sched["missed"] and now >= sched["start_at"] + SCHEDULE_LATE_SECONDS:
            sched["missed"] = True
            changed = True
        elif sched and not sched["missed"] and now >= sched["start_at"]:
            doc["schedule"] = None
            doc["state"] = ACTIVE
            doc["notice"] = None
            changed = True
        running = next((i for i in doc["items"] if i["status"] == RUNNING), None)
        if running is not None and running["job_id"] is None:
            # Only this thread starts items, and a start finishes before the
            # next tick, so this claim's outcome was never written.
            _finish(running, INTERRUPTED, "Interrupted: its start could not be recorded. "
                    "Check the jobs list, then press Retry if needed.", now)
            running, changed = None, True
        running_job = running["job_id"] if running else None
        if changed:
            _save(doc)
    if running is not None:
        finished, status, reason = _job_outcome(running_job)
        if not finished:
            return
        with _lock:
            doc = _load()
            item = next((i for i in doc["items"] if i["id"] == running["id"]), None)
            if item is not None and item["status"] == RUNNING:
                _finish(item, status, reason, now)
                if status == FAILED and doc["stop_on_failure"]:
                    doc["state"] = IDLE
                    doc["notice"] = _STOPPED_AFTER_FAILURE
            _save(doc)
    _start_next(now)


def _start_next(now: float, deferred=frozenset()) -> None:
    """Starts the first waiting item not in `deferred`. An item whose adder
    is at their running cap (jobs/user_caps.py) goes back to waiting with a
    reason and the next one is tried, so one member's own jobs never hold up
    the line; the next tick tries it again."""
    from jobs import job_launch
    if job_launch.exclusive_active() or job_launch.maintenance_active():
        return     # a restore or cleanup holds the library: wait rather than skip everything
    with _lock:
        doc = _load(now)
        if doc["state"] != ACTIVE or any(i["status"] == RUNNING for i in doc["items"]):
            return
        item = next((i for i in doc["items"]
                     if i["status"] == WAITING and i["id"] not in deferred), None)
        if item is None:
            if not deferred:
                doc["state"] = IDLE
                _save(doc)
            return
        item.update(status=RUNNING, started_at=now, reason="", job_id=None)
        claimed = dict(item)
        _save(doc)
    status, reason, job_id = RUNNING, "", None
    try:
        with user_caps.refuse_at_cap():
            job_id = _start_item(claimed)
        if job_id is None:
            status = DONE
    except RateLimitedError:
        status = WAITING
    except ServiceError as exc:
        status, reason = SKIPPED, str(exc)
        if job_launch.is_stopping():
            status, reason = INTERRUPTED, ""
    except Exception as exc:     # a bug in a start path must not stop the queue
        _log.warning("Queue item %s did not start: %s", claimed["id"],
                     translate_engines.redact_secrets(str(exc)))
        status, reason = SKIPPED, "It could not start because of an unexpected error."
    with _lock:
        doc = _load()
        item = next((i for i in doc["items"] if i["id"] == claimed["id"]), None)
        if item is not None:
            if status == RUNNING:
                item["job_id"] = job_id
            elif status == WAITING:
                item.update(status=WAITING, reason=_WAITING_FOR_CAP, started_at=None)
            elif status == INTERRUPTED:
                _finish(item, status, _INTERRUPTED, _clock())
            else:
                _finish(item, status, reason and f"Skipped: {reason}", _clock())
                item["job_id"] = job_id
        _save(doc)
    if status == WAITING:
        _start_next(now, deferred | {claimed["id"]})
    elif status != RUNNING:
        _wake.set()     # straight on to the next item


def clear_schedule(conn) -> None:
    """Drops the start time from the document on `conn` (a restored library:
    its start time is from the moment the backup was taken, and one still
    ahead would start the queue on its own), and its instance, so the next
    read settles it even when this same server wrote the backup (a running
    queue would otherwise go on starting items). The caller commits."""
    conn.execute("UPDATE app_settings SET value = json_remove(json_set(value, '$.schedule', "
                 "json('null')), '$.instance') WHERE key = ? AND json_valid(value)", (QUEUE_KEY,))


def restore_at_startup(now: float = None) -> None:
    """The queue as this process finds it: nothing resumes on its own. Every
    read settles a document from another process anyway (`_load`), so a
    failure here can't let old work resume; this only saves the result."""
    with _lock:
        _save(_load(now, settle=True))


_wake = threading.Event()
_stop = threading.Event()
_runner: Optional[threading.Thread] = None


def _loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception as exc:
            _log.warning("Job queue tick failed: %s", translate_engines.redact_secrets(str(exc)))
        _wake.wait(POLL_SECONDS)
        _wake.clear()


def start_runner() -> bool:
    """Restores the queue, then starts the runner; False if one already runs."""
    global _runner
    if _runner is not None and _runner.is_alive():
        return False
    try:
        restore_at_startup()
    except Exception as exc:     # _load settles the document on every read regardless
        _log.warning("Job queue restore failed: %s", translate_engines.redact_secrets(str(exc)))
    _stop.clear()
    _runner = threading.Thread(target=_loop, daemon=True, name="api-job-queue")
    _runner.start()
    return True


def stop_runner(timeout: float = 5.0) -> None:
    global _runner
    runner, _runner = _runner, None
    _stop.set()
    _wake.set()
    if runner is not None:
        runner.join(timeout)
