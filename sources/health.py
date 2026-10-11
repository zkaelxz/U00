"""
sources/health.py -- per-source health: 🟢/🟡/🔴, last
success/failure, error type, latency, and the backoff window that keeps a
failing source from being hit again too soon.
"""

import time

from translate_engines import redact_for_storage

from . import store

GREEN, YELLOW, RED = "🟢", "🟡", "🔴"

# A source goes 🔴 after this many consecutive failed requests (each of
# which already had its own retries), and is then left alone for
# `unavailable_backoff` seconds, doubling on each further failure.
RED_AFTER = 3
# NOT_FOUND (a dead chapter URL) is never recorded here: the site answered, so
# it must not count toward RED_AFTER. LAYOUT_CHANGED is recorded like any
# failure (it means the adapter needs attention).
MAX_BACKOFF = 6 * 3600.0


# Plain-language category for a stored error type (FailureReason value).
# The UI and the App Assistant show this instead of the raw string.
_CATEGORIES = {
    "blocked": ("CLOUDFLARE_CHALLENGE", "BOT_CHALLENGE", "ACCESS_DENIED", "IP_REPUTATION_BLOCK",
                "CDN_RESTRICTION", "GEO_RESTRICTION", "TOS_PROHIBITED"),
    "site_down": ("HTTP_ERROR", "SERVER_ERROR"),
    "page_missing": ("NOT_FOUND",),
    "layout_changed": ("LAYOUT_CHANGED",),
    "slow": ("TIMEOUT", "RATE_LIMIT"),
    "needs_sign_in": ("AUTHENTICATION_REQUIRED", "COOKIE_REQUIRED", "PURCHASE_REQUIRED"),
    "domains_unreachable": ("ALL_DOMAINS_UNREACHABLE",),
}
_CATEGORY_OF = {t: c for c, types in _CATEGORIES.items() for t in types}


def category(error_type) -> str:
    """blocked / site_down / page_missing / layout_changed / slow /
    needs_sign_in / domains_unreachable, "other" for any other error type,
    None for no error."""
    if not error_type:
        return None
    return _CATEGORY_OF.get(str(error_type), "other")


def record_success(source: str, latency: float, now: float = None):
    now = time.time() if now is None else now
    try:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO source_health(source, consecutive_failures, last_success, last_latency, "
                "unavailable_until) VALUES(?, 0, ?, ?, NULL) ON CONFLICT(source) DO UPDATE SET "
                "consecutive_failures=0, last_success=excluded.last_success, "
                "last_latency=excluded.last_latency, unavailable_until=NULL",
                (source, now, latency))
    except store.SourcesDatabaseBusy:
        # Health is a record of the fetch, not part of it.
        store.log_dropped("a source health record")


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
    try:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO source_health(source, consecutive_failures, last_failure, last_error_type, "
                "last_error, unavailable_until) VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE "
                "SET consecutive_failures=excluded.consecutive_failures, "
                "last_failure=excluded.last_failure, last_error_type=excluded.last_error_type, "
                "last_error=excluded.last_error, unavailable_until=excluded.unavailable_until",
                (source, failures, now, error_type, redact_for_storage(error)[:500], until))
    except store.SourcesDatabaseBusy:
        store.log_dropped("a source health record")
        current["consecutive_failures"] = failures
        return current
    return get(source)


def get(source: str) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM source_health WHERE source=?", (source,)).fetchone()
    if row is None:
        return {"source": source, "consecutive_failures": 0, "last_success": None,
                "last_failure": None, "last_error_type": None, "last_error": None,
                "last_latency": None, "unavailable_until": None}
    return dict(row)


def recent_failures(limit: int = 20) -> list:
    """Sources whose current failure streak is open, newest first, as
    {source, category, count, last_failure}. No error text or URLs. A source the person
    marked as extension-only is left out: it is read through the extension, so an old
    failure from the automated tiers is not news."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT source, last_error_type, consecutive_failures, last_failure FROM source_health "
            "WHERE consecutive_failures > 0 AND last_failure IS NOT NULL "
            "AND source NOT IN (SELECT source FROM source_extension_only) "
            "ORDER BY last_failure DESC LIMIT ?", (int(limit),)).fetchall()
    return [{"source": r["source"], "category": category(r["last_error_type"]),
             "count": r["consecutive_failures"], "last_failure": r["last_failure"]}
            for r in rows]


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
