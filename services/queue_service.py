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
start time that passed while the app was off becomes "missed", which only
the owner can turn into a run.
"""

import datetime
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Optional

import db
import translate_engines
from lib.errors import (ConflictError, ForbiddenError, InvalidInputError, NotFoundError,
                        ServiceError, UnsupportedOperationError)
from services import (auth_service, artifact_service, export_service, jobs_service,
                      ownership_service, settings_service, transcribe_service,
                      translate_run_service, translate_thinking_service)

QUEUE_KEY = "job_queue"
QUEUE_VERSION = 1
MAX_ITEMS = 200
# One member can't fill the shared list and lock everyone else out.
MAX_ITEMS_PER_MEMBER = 50
STEPS = ("transcribe", "translate", "export")
POLL_SECONDS = 5.0

WAITING, RUNNING, DONE, FAILED, SKIPPED, INTERRUPTED, CANCELLED = (
    "waiting", "running", "done", "failed", "skipped", "interrupted", "cancelled")
FINISHED = (DONE, FAILED, SKIPPED, INTERRUPTED, CANCELLED)
RETRYABLE = (FAILED, SKIPPED, INTERRUPTED, CANCELLED)
IDLE, ACTIVE, PAUSED = "idle", "running", "paused"

_HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_MAX_REASON = 300
_STOPPED_AFTER_FAILURE = "Stopped because an item failed. Start the queue to go on."
_INTERRUPTED = "Interrupted: Baihe stopped while this ran. Press Retry to run it again."
_NO_CAP = ("Set a monthly spending cap (Settings > Spending) to run paid translation "
           "from the queue.")

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


def _view_item(principal, item: dict, drama: Optional[dict], owner_of) -> dict:
    out = {k: item[k] for k in ("id", "drama_id", "step", "status", "job_id",
                                "added_at", "started_at", "finished_at")}
    out["reason"] = item["reason"] or None
    out["title"] = _title_name(drama)
    out["mine"] = _may_change(principal, item, owner_of)
    out["engine"] = None
    out["paid"] = False
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


# --- changing items ----------------------------------------------------------

def add_item(principal, drama_id: int, step: str, holds_paid: bool) -> dict:
    """Validates and appends one item. The title must exist and be the
    caller's; a Translate item on a paid engine needs `engines.paid`, as the
    Translate stage's run does."""
    if step not in STEPS:
        raise InvalidInputError("Step must be transcribe, translate or export.")
    drama = db.get_drama(drama_id)
    if drama is None or not _owns_title(principal, drama_id):
        raise NotFoundError("Drama not found.")
    if step == "translate" and not holds_paid \
            and _translate_engine(drama) not in translate_engines.FREE_ENGINES:
        raise ForbiddenError("This title translates with a paid engine, which your account "
                             "can't use. Ask the owner, or pick a free engine for the title.")
    with _lock:
        doc = _load()
        if len(doc["items"]) >= MAX_ITEMS:
            raise ConflictError(f"The queue holds at most {MAX_ITEMS} items. "
                                "Clear finished items first.")
        uid = ownership_service.user_id(principal)
        if not _overrides(principal) and sum(
                1 for i in doc["items"] if i["user_id"] == uid) >= MAX_ITEMS_PER_MEMBER:
            raise ConflictError(f"You can have at most {MAX_ITEMS_PER_MEMBER} items in the "
                                "queue. Clear your finished items first.")
        item = {"id": doc["next_id"], "drama_id": drama_id, "step": step, "status": WAITING,
                "reason": "", "job_id": None, "user_id": ownership_service.user_id(principal),
                "override": _overrides(principal), "added_at": _clock(),
                "started_at": None, "finished_at": None}
        doc["next_id"] += 1
        doc["items"].append(item)
        _save(doc)
    _wake.set()
    return get_queue(principal)


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


def retry_item(principal, item_id: int) -> dict:
    def change(doc, item):
        if item["status"] not in RETRYABLE:
            raise ConflictError("Only a failed, skipped, cancelled or interrupted item can be retried.")
        item.update(status=WAITING, reason="", job_id=None, started_at=None, finished_at=None)
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


def _check_translate_spend(drama_id: int) -> None:
    """The unattended twin of the Translate stage's estimate: refuse before
    any paid call when this month's cap is used up or the estimate is above
    what is left of it. The run itself still enforces the cap as it goes."""
    est = translate_run_service.estimate_translate_cost(drama_id)
    if not est["cap_applies"]:
        return
    # Like the scheduled model re-evaluation: unattended paid work needs a ceiling.
    if settings_service.get_monthly_cap_usd() <= 0:
        raise UnsupportedOperationError(_NO_CAP)
    if est["monthly_refusal"]:
        _cap, refusal = translate_engines.resolve_cost_cap(
            None, settings_service.get_monthly_cap_usd(), db.get_month_spend())
        raise UnsupportedOperationError(refusal or "This month's spending cap is used up.")
    if est["estimate_above_cap"]:
        raise UnsupportedOperationError(
            f"The estimate (${est['estimated_usd']:.2f}) is above what is left of this "
            f"month's spending cap (${est['effective_cap_usd']:.2f}).")


def _start_transcribe(principal, drama: dict) -> str:
    config = transcribe_service.get_transcribe_config(drama["id"])
    if config.get("use_groq") and not _holds(principal, "engines.paid"):
        raise ForbiddenError("This title transcribes with Groq, which needs paid-engine permission.")
    if config.get("transcript_mode") == "have_transcript":
        raise UnsupportedOperationError("This title transcribes from a transcript you paste, "
                                        "so it can't run from the queue.")
    return transcribe_service.start_transcribe_run(drama["id"])["job_id"]


def _start_translate(principal, drama: dict) -> str:
    engine = _translate_engine(drama)
    holds_paid = _holds(principal, "engines.paid")
    if engine not in translate_engines.FREE_ENGINES and not holds_paid:
        raise ForbiddenError("This title translates with a paid engine, which the account "
                             "that added it can't use.")
    _check_translate_spend(drama["id"])
    locales = settings_service.LOCALE_CHOICES
    locale = settings_service.get_preference("default_locale")
    return translate_thinking_service.start_with_thinking(
        translate_run_service.start_translate_run, drama["id"], None, False,
        style_note=settings_service.get_preference("default_style_note") or "",
        locale=locale if locale in locales else "en-US",
        allow_paid_summary=holds_paid)["job_id"]


def _export(drama: dict) -> None:
    """Writes the title's English SRT to its subtitle download, the server's
    default when no export choice is saved (the stage's are per browser)."""
    import background_jobs
    if not background_jobs.enter_maintenance():
        raise ConflictError("A restore or reset is running, so the export can't run now.")
    try:
        if not any((ln.get("en") or "").strip() for ln in db.load_lines(drama["id"])):
            raise UnsupportedOperationError("There are no English lines to export yet.")
        text = export_service.generate_subtitle_text(drama["id"], "srt", "en")
        name = f"subtitles_{drama['id']}.srt"
        path = artifact_service.output_path(drama["id"], "subtitle", name)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".queue-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        artifact_service.set_download_language(
            drama["id"], "subtitle", name, export_service.field_language(drama, "en"))
    finally:
        background_jobs.exit_maintenance()


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
    try:
        # Records the job as the adder's, as if they had pressed the button.
        ownership_service.note_acting_principal(principal)
        if item["step"] == "transcribe":
            return _start_transcribe(principal, drama)
        if item["step"] == "translate":
            return _start_translate(principal, drama)
        _export(drama)
        return None
    finally:
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
        job = {"status": live["status"], "error": live.get("error"), "outcome":
               {"done": "ok", "error": "failed", "cancelled": "cancelled"}.get(live["status"])}
    outcome = job.get("outcome")
    if outcome == "failed":
        return True, FAILED, job.get("outcome_message") or job.get("error") or "The job failed."
    if outcome == "cancelled":
        return True, CANCELLED, job.get("outcome_message") or "The job was cancelled."
    note = job.get("outcome_message") if outcome in ("partial", "kept_existing") else ""
    return True, DONE, note or ""


def _finish(item: dict, status: str, reason: str, now: float) -> None:
    item.update(status=status, reason=_short(reason), finished_at=now)


def tick(now: float = None) -> None:
    """One pass: fire a due schedule, settle the running item, start the next.
    Safe to call from tests with an injected clock."""
    import background_jobs
    if background_jobs.is_stopping():
        return     # the stop cancels jobs; the next start marks the item interrupted
    now = _clock() if now is None else now
    with _lock:
        doc = _load(now)
        changed = doc.get("settled", False)
        sched = doc["schedule"]
        if sched and not sched["missed"] and now >= sched["start_at"]:
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


def _start_next(now: float) -> None:
    import background_jobs
    if background_jobs.exclusive_active() or background_jobs.maintenance_active():
        return     # a restore or cleanup holds the library: wait rather than skip everything
    with _lock:
        doc = _load(now)
        if doc["state"] != ACTIVE or any(i["status"] == RUNNING for i in doc["items"]):
            return
        item = next((i for i in doc["items"] if i["status"] == WAITING), None)
        if item is None:
            doc["state"] = IDLE
            _save(doc)
            return
        item.update(status=RUNNING, started_at=now, reason="", job_id=None)
        claimed = dict(item)
        _save(doc)
    status, reason, job_id = RUNNING, "", None
    try:
        job_id = _start_item(claimed)
        if job_id is None:
            status = DONE
    except ServiceError as exc:
        status, reason = SKIPPED, str(exc)
        if background_jobs.is_stopping():
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
            elif status == INTERRUPTED:
                _finish(item, status, _INTERRUPTED, _clock())
            else:
                _finish(item, status, reason and f"Skipped: {reason}", _clock())
                item["job_id"] = job_id
        _save(doc)
    if status != RUNNING:
        _wake.set()     # straight on to the next item


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
