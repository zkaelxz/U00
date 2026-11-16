"""services/resource_monitor_service.py -- samples this PC's GPU temperature and throttling, RAM, CPU load, GPU memory and free disk, and turns a reading that stays past its limit for its time into one alert, one reminder an hour and one recovery.

UI-free. `api/background.py` calls `run_cycle` on a timer; nothing here blocks a
job or raises. Readings come from nvidia-smi and the OS only (no extra
dependency); a reading that is unavailable is None and changes nothing.

A rule alerts when the value stays past its limit for `minutes`, recovers when
it has stayed clear of the limit by a margin for RECOVERY_SECONDS (so a value
hovering at the limit does not flap), and repeats at most every
REPEAT_SECONDS while the problem lasts. Which rules are alerting is kept in
app_settings so a restart does not announce the same problem again; the
timers are in memory, so a restart restarts the clock.
"""

import shutil
import sys
import threading
import time
from typing import Any

import db
from engine_backends.shared import redact_secrets
from lib.proc import run_captured

RULES_SETTING = "resource_alert_rules"
STATE_SETTING = "resource_alert_state"

SAMPLE_SECONDS = 30.0
RECOVERY_SECONDS = 120.0
REPEAT_SECONDS = 3600.0
NVIDIA_SMI_TIMEOUT_SECONDS = 5

# Throttle-reason bits that mean the card is cutting clocks to stay safe:
# hardware slowdown, software thermal slowdown, hardware thermal slowdown and
# the hardware power brake. The software power cap (0x4) is left out on
# purpose: a card at full load sits on it all day, so it would alert on normal work.
THROTTLE_ALERT_MASK = 0x8 | 0x20 | 0x40 | 0x80

# `low` rules alert when the value is under the limit. `margin` is how far
# back from the limit a value must be to count as recovered.
METRICS: dict[str, dict[str, Any]] = {
    "gpu_temp": {"label": "GPU temperature", "unit": " °C", "limit": 85, "minutes": 5, "enabled": True,
                 "margin": 3, "range": (40, 120)},
    "gpu_throttle": {"label": "GPU throttling", "unit": "", "limit": None, "minutes": 10, "enabled": True,
                     "margin": 0},
    "ram": {"label": "RAM use", "unit": "%", "limit": 95, "minutes": 10, "enabled": True,
            "margin": 3, "range": (50, 100)},
    "disk_free": {"label": "Free disk space", "unit": " GB", "limit": 10, "minutes": 5, "enabled": True,
                  "margin": 2, "range": (1, 100000), "low": True},
    "gpu_memory_warn": {"label": "GPU memory (nearly full)", "unit": "%", "limit": 85, "minutes": 15,
                        "enabled": True, "margin": 3, "range": (50, 100)},
    "gpu_memory": {"label": "GPU memory (full)", "unit": "%", "limit": 100, "minutes": 15, "enabled": False,
                   "margin": 3, "range": (50, 100)},
    "cpu": {"label": "CPU use", "unit": "%", "limit": 95, "minutes": 30, "enabled": False,
            "margin": 3, "range": (50, 100)},
}
MINUTES_RANGE = (1, 1440)


# --- rules -------------------------------------------------------------------

def _number(value, low, high, default) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        return default
    return value


def get_rules() -> dict:
    """Every metric's {"enabled", "limit", "minutes"}: the defaults, with each
    stored value that is valid laid over them. A bad stored value falls back
    to the default for that one field."""
    from services import settings_service
    raw = settings_service.get(RULES_SETTING)
    stored: dict[str, Any] = raw if isinstance(raw, dict) else {}
    rules: dict[str, dict[str, Any]] = {}
    for name, meta in METRICS.items():
        own: dict[str, Any] = stored[name] if isinstance(stored.get(name), dict) else {}
        enabled = own.get("enabled")
        limit = meta["limit"]
        if limit is not None:
            low, high = meta["range"]
            limit = _number(own.get("limit"), low, high, limit)
        rules[name] = {"enabled": enabled if isinstance(enabled, bool) else meta["enabled"],
                       "limit": limit,
                       "minutes": _number(own.get("minutes"), *MINUTES_RANGE, meta["minutes"])}
    return rules


def describe_rules() -> list[dict]:
    """What the PC alerts card shows: each metric's label, unit, current rule,
    allowed ranges and defaults, in METRICS order."""
    rules = get_rules()
    out = []
    for name, meta in METRICS.items():
        has_limit = meta["limit"] is not None
        out.append({"id": name, "label": meta["label"], "unit": meta["unit"].strip(),
                    "has_limit": has_limit, "below": bool(meta.get("low")),
                    **rules[name],
                    "limit_min": meta["range"][0] if has_limit else None,
                    "limit_max": meta["range"][1] if has_limit else None,
                    "minutes_min": MINUTES_RANGE[0], "minutes_max": MINUTES_RANGE[1],
                    "default_enabled": meta["enabled"], "default_limit": meta["limit"],
                    "default_minutes": meta["minutes"]})
    return out


def set_rules(changes: dict) -> list[dict]:
    """Applies {metric: {enabled, limit, minutes}} (any subset) and saves.
    Unlike get_rules, which quietly falls back on a bad stored value, a bad
    request is refused whole so the card never shows a limit that was not kept."""
    from lib.errors import InvalidInputError
    merged = get_rules()
    for name, change in changes.items():
        meta = METRICS.get(name)
        if meta is None:
            raise InvalidInputError(f"'{name}' is not an alert.")
        rule = merged[name]
        if "enabled" in change:
            rule["enabled"] = bool(change["enabled"])
        if "limit" in change:
            if meta["limit"] is None:
                raise InvalidInputError(f"{meta['label']} has no limit to change.")
            low, high = meta["range"]
            if _number(change["limit"], low, high, None) is None:
                raise InvalidInputError(f"{meta['label']} limit must be between {low} and {high}.")
            rule["limit"] = change["limit"]
        if "minutes" in change:
            low, high = MINUTES_RANGE
            if _number(change["minutes"], low, high, None) is None:
                raise InvalidInputError(f"{meta['label']} minutes must be between {low} and {high}.")
            rule["minutes"] = change["minutes"]
    # Only the touched metrics are stored, so an untouched one keeps following its default.
    db.update_app_setting(RULES_SETTING, lambda stored: {
        **(stored if isinstance(stored, dict) else {}), **{n: merged[n] for n in changes}})
    return describe_rules()


# --- readings ----------------------------------------------------------------

def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None   # "[N/A]" and the like


def _nvidia_smi(query: str):
    result = run_captured(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                          timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
    if result.timed_out or result.returncode != 0:
        return None
    return [[x.strip() for x in line.split(",")] for line in result.stdout.splitlines() if line.strip()]


def _read_gpu() -> dict:
    """gpu_temp, gpu_memory and gpu_throttle of the worst card (the hottest,
    the fullest); each None when nvidia-smi does not report it."""
    out: dict[str, float | None] = {"gpu_temp": None, "gpu_memory": None, "gpu_throttle": None}
    if not shutil.which("nvidia-smi"):
        return out
    rows = _nvidia_smi("temperature.gpu,memory.used,memory.total")
    if rows:
        temps = [t for t in (_float(r[0]) for r in rows) if t is not None]
        out["gpu_temp"] = max(temps) if temps else None
        # Whole percent: the driver keeps a little memory back, so a full card
        # reads 99.8 and must still count as 100.
        shares = [round(100 * used / total) for r in rows
                  if (used := _float(r[1])) is not None and (total := _float(r[2]))]
        out["gpu_memory"] = max(shares) if shares else None
    # Renamed from clocks_throttle_reasons in newer drivers; ask for either.
    for field in ("clocks_throttle_reasons.active", "clocks_event_reasons.active"):
        rows = _nvidia_smi(field)
        masks = []
        for row in rows or []:
            try:
                masks.append(int(row[0], 16))
            except ValueError:
                pass
        if masks:
            out["gpu_throttle"] = 1.0 if any(m & THROTTLE_ALERT_MASK for m in masks) else 0.0
            break
    return out


def _read_ram_percent():
    import memory_headroom
    ram = memory_headroom._read_ram_mb()
    if not ram or not ram[0]:
        return None
    total, available = ram
    return round(100 * (total - available) / total)


def _cpu_times():
    """(busy, total) ticks since boot, or None."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                                         ctypes.byref(user)):
                return None

            def ticks(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            # Kernel time already includes idle time.
            total = ticks(kernel) + ticks(user)
            return total - ticks(idle), total
        with open("/proc/stat", encoding="ascii", errors="replace") as fh:
            fields = [int(x) for x in fh.readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return sum(fields) - idle, sum(fields)
    except Exception:
        return None


def _disk_free_gb():
    try:
        return shutil.disk_usage(db.LIBRARY_DIR).free / 1024 ** 3
    except Exception:
        return None


# --- the rule engine ---------------------------------------------------------

def _past_limit(meta, rule, value) -> bool:
    limit = rule["limit"] if rule["limit"] is not None else 1
    return value < limit if meta.get("low") else value >= limit


def _clear_of_limit(meta, rule, value) -> bool:
    limit = rule["limit"] if rule["limit"] is not None else 1
    if meta.get("low"):
        return value >= limit + meta["margin"]
    return value < limit - meta["margin"]


def _shown(name, value) -> str:
    if name == "gpu_throttle":
        return "on" if value >= 1 else "off"
    unit = METRICS[name]["unit"]
    return f"{value:.1f}{unit}" if name == "disk_free" else f"{value:.0f}{unit}"


def _duration(seconds) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes} min" if minutes < 120 else f"{round(minutes / 60)} h"


def _text(name, kind, value, rule, since_seconds) -> str:
    meta = METRICS[name]
    label = meta["label"]
    if kind == "recovered":
        return f"{label} is back to normal ({_shown(name, value)})."
    if kind == "repeat":
        return f"{label} is still {_shown(name, value)} after {_duration(since_seconds)}."
    if name == "gpu_throttle":
        return (f"The GPU has been cutting its clocks for heat or power for {_duration(since_seconds)}.")
    side = "under" if meta.get("low") else "at or above"
    return (f"{label} is {_shown(name, value)}, {side} {_shown(name, rule['limit'])} "
            f"for {_duration(since_seconds)}.")


def step(name, rule, state, value, now):
    """One rule, one reading. `state` is {"alerting", "notified_at", "past_since",
    "clear_since", "alert_since"}; returns (new_state, event or None). An
    event is {"metric", "kind": "alert"|"repeat"|"recovered", "value", "text"}."""
    meta = METRICS[name]
    state = dict(state)
    if not rule["enabled"]:
        # Switching a rule off while it alerts ends the alert quietly.
        return {"alerting": False, "notified_at": None, "past_since": None, "clear_since": None,
                "alert_since": None}, None
    if value is None:
        return state, None
    event = None
    if not state.get("alerting"):
        if _past_limit(meta, rule, value):
            state["past_since"] = state.get("past_since") or now
            if now - state["past_since"] >= rule["minutes"] * 60:
                state.update(alerting=True, notified_at=now, alert_since=state["past_since"],
                             clear_since=None)
                event = {"metric": name, "kind": "alert", "value": value,
                         "text": _text(name, "alert", value, rule, now - state["past_since"])}
        else:
            state["past_since"] = None
        return state, event
    if _clear_of_limit(meta, rule, value):
        state["clear_since"] = state.get("clear_since") or now
        if now - state["clear_since"] >= RECOVERY_SECONDS:
            event = {"metric": name, "kind": "recovered", "value": value,
                     "text": _text(name, "recovered", value, rule, 0)}
            state.update(alerting=False, notified_at=None, past_since=None, clear_since=None,
                         alert_since=None)
        return state, event
    state["clear_since"] = None
    if now - (state.get("notified_at") or now) >= REPEAT_SECONDS:
        state["notified_at"] = now
        event = {"metric": name, "kind": "repeat", "value": value,
                 "text": _text(name, "repeat", value, rule, now - (state.get("alert_since") or now))}
    return state, event


# --- the monitor -------------------------------------------------------------

_lock = threading.Lock()
_memory: dict | None = None     # name -> step state; None until first loaded
_last_cpu = None


def _load_state() -> dict:
    try:
        raw = db.get_app_setting(STATE_SETTING, None)
    except Exception:
        raw = None
    stored: dict[str, Any] = raw if isinstance(raw, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for name in METRICS:
        saved: dict[str, Any] = stored[name] if isinstance(stored.get(name), dict) else {}
        out[name] = {"alerting": saved.get("alerting") is True,
                     "notified_at": _number(saved.get("notified_at"), 0, 1e12, None),
                     "alert_since": _number(saved.get("alert_since"), 0, 1e12, None),
                     "past_since": None, "clear_since": None}
    return out


def _save_state(states) -> None:
    keep = {n: {"alerting": s["alerting"], "notified_at": s["notified_at"], "alert_since": s["alert_since"]}
            for n, s in states.items() if s["alerting"]}
    db.set_app_setting(STATE_SETTING, keep)


def sample() -> dict:
    """The current readings, None where one is unavailable. Never raises."""
    global _last_cpu
    readings: dict[str, float | None] = {name: None for name in METRICS}
    for reader in (_read_gpu,):
        try:
            readings.update(reader())
        except Exception:
            pass
    # Two rules judge the same reading, at different limits.
    readings["gpu_memory_warn"] = readings["gpu_memory"]
    for name, reader in (("ram", _read_ram_percent), ("disk_free", _disk_free_gb)):
        try:
            readings[name] = reader()
        except Exception:
            pass
    times = _cpu_times()
    if times is not None and _last_cpu is not None and times[1] > _last_cpu[1]:
        readings["cpu"] = round(100 * (times[0] - _last_cpu[0]) / (times[1] - _last_cpu[1]))
    _last_cpu = times
    return readings


def run_cycle(notify=None, now=None, readings=None) -> list:
    """Samples once, steps every rule and calls `notify(event)` for each event.
    Returns the events. Never raises: a failing `notify` is logged and the
    state moves on, so one bad send cannot repeat or hide an alert. `notify` defaults
    to the log, the bell and the push channels."""
    global _memory
    now = time.time() if now is None else now
    rules = get_rules()
    readings = sample() if readings is None else readings
    events = []
    with _lock:
        if _memory is None:
            _memory = _load_state()
        before = {n: s["alerting"] for n, s in _memory.items()}
        for name in METRICS:
            _memory[name], event = step(name, rules[name], _memory[name], readings.get(name), now)
            if event:
                events.append(event)
        if any(before[n] != _memory[n]["alerting"] for n in METRICS):
            try:
                _save_state(_memory)
            except Exception as exc:
                _log("resource monitor: the alert state was not saved (%s)", type(exc).__name__)
        elif events:
            # A repeat moves notified_at; losing it only costs an early reminder.
            try:
                _save_state(_memory)
            except Exception:
                pass
    for event in events:
        try:
            (notify or _notify)(event)
        except Exception as exc:
            _log("resource monitor: an alert was not sent (%s)", type(exc).__name__)
    return events


def _log(fmt, *args):
    try:
        from applog import get_logger
        get_logger().warning(fmt, *args)
    except Exception:
        pass


def _notify(event) -> None:
    """The default `notify`: the log, the bell and the push channels."""
    _log("resource monitor: %s", redact_secrets(event["text"]))
    from services import notification_service
    notification_service.notify_resource_alert(event["text"], recovered=event["kind"] == "recovered")


def reset_for_tests() -> None:
    global _memory, _last_cpu
    with _lock:
        _memory = None
        _last_cpu = None
