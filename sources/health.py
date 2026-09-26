"""
sources/health.py -- per-source health (Step 23 item 3): 🟢/🟡/🔴, last
success/failure, error type, latency, and the backoff window that keeps a
failing source from being hit again too soon.
"""

import time

from . import store

GREEN, YELLOW, RED = "🟢", "🟡", "🔴"

# A source goes 🔴 after this many consecutive failed requests (each of
# which already had its own retries), and is then left alone for
# `unavailable_backoff` seconds, doubling on each further failure.
RED_AFTER = 3
MAX_BACKOFF = 6 * 3600.0


def record_success(source: str, latency: float, now: float = None):
    now = time.time() if now is None else now
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO source_health(source, consecutive_failures, last_success, last_latency, "
            "unavailable_until) VALUES(?, 0, ?, ?, NULL) ON CONFLICT(source) DO UPDATE SET "
            "consecutive_failures=0, last_success=excluded.last_success, "
            "last_latency=excluded.last_latency, unavailable_until=NULL",
            (source, now, latency))


def record_failure(source: str, error_type: str, error: str, now: float = None,
                   base_backoff: float = None) -> dict:
    now = time.time() if now is None else now
    if base_backoff is None:
        base_backoff = float(store.get_setting("unavailable_backoff"))
    current = get(source)
    failures = current["consecutive_failures"] + 1
    until = None
    if failures >= RED_AFTER:
        until = now + min(base_backoff * (2 ** (failures - RED_AFTER)), MAX_BACKOFF)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO source_health(source, consecutive_failures, last_failure, last_error_type, "
            "last_error, unavailable_until) VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE "
            "SET consecutive_failures=excluded.consecutive_failures, "
            "last_failure=excluded.last_failure, last_error_type=excluded.last_error_type, "
            "last_error=excluded.last_error, unavailable_until=excluded.unavailable_until",
            (source, failures, now, error_type, error[:500], until))
    return get(source)


def get(source: str) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM source_health WHERE source=?", (source,)).fetchone()
    if row is None:
        return {"source": source, "consecutive_failures": 0, "last_success": None,
                "last_failure": None, "last_error_type": None, "last_error": None,
                "last_latency": None, "unavailable_until": None}
    return dict(row)


def light(source: str, now: float = None) -> str:
    h = get(source)
    now = time.time() if now is None else now
    if h["unavailable_until"] and h["unavailable_until"] > now:
        return RED
    if h["consecutive_failures"] >= RED_AFTER:
        return RED
    if h["consecutive_failures"]:
        return YELLOW
    return GREEN


def retry_after(source: str, now: float = None):
    """Seconds until this source may be tried again, or None if it may be
    tried now."""
    now = time.time() if now is None else now
    until = get(source)["unavailable_until"]
    if until and until > now:
        return until - now
    return None


def reset(source: str):
    """A manual "try again now" -- the person's explicit override of the
    backoff window, never something the app does on its own."""
    with store.connect() as conn:
        conn.execute("UPDATE source_health SET consecutive_failures=0, unavailable_until=NULL "
                     "WHERE source=?", (source,))
