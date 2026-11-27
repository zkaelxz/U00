"""services/queue_night.py -- the queue's overnight lane: the night window, the PC idle test, the morning summary and the nightly self-check."""

import ctypes
import datetime
import logging
import re
import sys
from typing import Optional

import background_jobs
import db
from engine_backends import shared as engine_shared
from lib.errors import InvalidInputError, ServiceError

DEFAULT_NIGHT = {"start": "01:00", "end": "07:00", "self_check": True}
# A Tonight item waits this long after the last key press or mouse move, so it never starts under someone at the PC.
IDLE_SECONDS = 10 * 60
CHECK_KEY = "nightly_check"
CHECK_JOB_ID = "nightly_check"
# One failed night is often a network blip; two in a row is worth waking someone for.
ALERT_AFTER_FAILURES = 2

_HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_log = logging.getLogger(__name__)


def _minutes(hhmm: str) -> int:
    m = _HHMM.match(hhmm or "")
    if not m:
        raise InvalidInputError("Pick the night's times as HH:MM.")
    return int(m.group(1)) * 60 + int(m.group(2))


def check_night(start: str, end: str) -> None:
    if _minutes(start) == _minutes(end):
        raise InvalidInputError("The night must start and end at different times.")


def clean_night(raw) -> dict:
    """The stored window, or the default for a missing or hand-edited one."""
    if not isinstance(raw, dict):
        return dict(DEFAULT_NIGHT)
    try:
        check_night(str(raw.get("start") or ""), str(raw.get("end") or ""))
    except InvalidInputError:
        return {**DEFAULT_NIGHT, "self_check": raw.get("self_check") is not False}
    return {"start": raw["start"], "end": raw["end"], "self_check": raw.get("self_check") is not False}


def _minute_of_day(now: float) -> int:
    here = datetime.datetime.fromtimestamp(now)
    return here.hour * 60 + here.minute


def in_window(night: dict, now: float) -> bool:
    start, end, m = _minutes(night["start"]), _minutes(night["end"]), _minute_of_day(now)
    return start <= m < end if start < end else (m >= start or m < end)


def night_key(night: dict, now: float) -> str:
    """The date the current night began on, so a window past midnight is one night, not two."""
    here = datetime.datetime.fromtimestamp(now)
    if _minutes(night["start"]) > _minutes(night["end"]) and _minute_of_day(now) < _minutes(night["end"]):
        here -= datetime.timedelta(days=1)
    return here.date().isoformat()


def _clock_text(hhmm: str) -> str:
    h, m = hhmm.split(":")
    return f"{int(h)}:{m}"


def waiting_text(night: dict) -> str:
    return f"Waits for tonight, {_clock_text(night['start'])}-{_clock_text(night['end'])}."


IDLE_WAIT_TEXT = "Waits until no one has used this PC's keyboard or mouse for 10 minutes."


def pc_idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard or mouse input on Windows; None where that can't be read."""
    if sys.platform != "win32":
        return None
    try:
        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = _LastInput()
        info.cbSize = ctypes.sizeof(info)
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32  # type: ignore[attr-defined]
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        kernel32.GetTickCount.restype = ctypes.c_uint
        # Both are 32-bit millisecond tick counts, which wrap after 49.7 days.
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def pc_idle() -> bool:
    """Unknown counts as idle: a PC we can't read must not hold the night's work back for ever."""
    idle = pc_idle_seconds()
    return idle is None or idle >= IDLE_SECONDS


def summary_text(items: list, opened_at: float) -> Optional[str]:
    """The morning line for the Tonight items that started this night, or None when none did."""
    ran = [i for i in items if i.get("tonight") and (i.get("started_at") or 0) >= opened_at]
    if not ran:
        return None
    done = sum(1 for i in ran if i["status"] == "done")
    running = sum(1 for i in ran if i["status"] == "running")
    other = len(ran) - done - running
    parts = [f"{done} done", f"{other} skipped or failed"] + ([f"{running} still running"] if running else [])
    return "Overnight queue: " + ", ".join(parts) + "."


# --- the nightly self-check --------------------------------------------------

def _state() -> dict:
    raw = db.get_app_setting(CHECK_KEY, None)
    if not isinstance(raw, dict):
        return {"night": None, "streaks": {}}
    streaks = raw.get("streaks")
    streaks = streaks if isinstance(streaks, dict) else {}
    return {"night": raw.get("night") if isinstance(raw.get("night"), str) else None,
            "streaks": {k: v for k, v in streaks.items() if isinstance(k, str) and type(v) is int}}


def maybe_start_self_check(night: dict, now: float) -> bool:
    """Starts tonight's check once per night, inside the window, with the PC idle and no job running."""
    from jobs import job_launch
    from services import library_admin_service
    if not night["self_check"] or not in_window(night, now):
        return False
    key = night_key(night, now)
    state = _state()
    if state["night"] == key or not pc_idle():
        return False
    if job_launch.exclusive_active() or job_launch.maintenance_active() or library_admin_service.any_job_running():
        return False
    db.set_app_setting(CHECK_KEY, {**state, "night": key})
    return job_launch.start_job(CHECK_JOB_ID, run_self_check, gpu_touching=True,
                                description="Nightly self-check", line_fields=())


def _paid_check() -> Optional[dict]:
    """One short call to the default translation engine, so a refused key or an empty balance shows
    up before a night's run. Skipped for an engine without a key, and once the monthly cap is spent."""
    from services import engine_routing_service, settings_service, translate_run_service
    from services.real_model_check_service import FAIL, PASS
    engine = engine_routing_service.resolve_capability("translation.cheap")
    try:
        status = engine_routing_service.engine_status(engine)
        if not status["needs_key"] or not status["key_configured"]:
            return None
        translate_run_service.refuse_when_cap_spent(engine, bool(settings_service.get("gemini_free_tier")), None)
        result = engine_routing_service.test_engine(engine)
    except ServiceError:
        return None
    ok = result["status"] == "working"
    reason = "" if ok else ((result.get("last_test") or {}).get("error") or "The engine test failed.")
    return {"id": "engine", "label": f"Translation ({engine})", "status": PASS if ok else FAIL,
            "reason": engine_shared.redact_secrets(reason)}


def run_self_check() -> None:
    from services import notification_service
    from services import real_model_check_service as checks

    def before_check(i, label):
        if background_jobs.is_cancel_requested(CHECK_JOB_ID):
            raise background_jobs.JobCancelled()
        background_jobs.update_progress(CHECK_JOB_ID, i / 4, f"Checking {label.lower()}")

    results = checks.run_checks(before_check=before_check)
    paid = _paid_check()
    if paid:
        results.append(paid)
    for text in record_results(results):
        notification_service.notify_resource_alert(text)
    failed = any(r["status"] == checks.FAIL for r in results)
    background_jobs.set_result(CHECK_JOB_ID, {"status": checks.FAIL if failed else checks.PASS})


def record_results(results: list) -> list:
    """Updates each check's run of failed nights; returns the alert lines for checks that just reached
    ALERT_AFTER_FAILURES. A skipped check neither breaks nor extends its run."""
    state = _state()
    streaks = dict(state["streaks"])
    alerts = []
    for r in results:
        if r["status"] == "fail":
            streaks[r["id"]] = streaks.get(r["id"], 0) + 1
            if streaks[r["id"]] == ALERT_AFTER_FAILURES:
                alerts.append(f"the nightly self-check failed two nights in a row. {r['label']}: "
                              f"{engine_shared.redact_secrets(r.get('reason') or 'no reason given')}")
        elif r["status"] == "pass":
            streaks[r["id"]] = 0
    db.set_app_setting(CHECK_KEY, {**state, "streaks": streaks})
    return alerts
