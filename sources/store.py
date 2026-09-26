"""
sources/store.py -- persistence for the source-adapter system.

Kept in its own SQLite file (`<library>/sources.db`) rather than as new
tables in db.py: this whole system is a separate domain from the drama/
line data, and a separate file means nothing here can ever interfere
with db.save_lines() or a library backup/restore. The path is read from
db.LIBRARY_DIR at call time, so the tests' `isolated_db` fixture
redirects it along with everything else.
"""

import json
import os
import sqlite3
import time

import db

# Step 23 item 3's concrete starting defaults, plus item 4's cache mode
# and item 5's chapter-check schedule. All user-editable in Settings.
DEFAULT_SETTINGS = {
    "pace_min_delay": 1.0,          # seconds between requests to one source
    "pace_max_delay": 3.0,
    "max_concurrent": 1,            # requests in flight per source
    "max_retries": 3,               # for 429/5xx/timeouts only -- never for a challenge
    "backoff_base": 2.0,            # seconds; doubles each retry
    "unavailable_backoff": 300.0,   # seconds a 🔴 source is left alone after failing
    "cache_mode": "temporary",
    "check_interval_hours": 24,
    "auto_queue_new_chapters": False,
    "demo_source_enabled": False,
    "disabled_sources": [],
    "adult_sources": [],            # sources the person opted in to adult-flagged works for
}


def db_path() -> str:
    return os.path.join(db.LIBRARY_DIR, "sources.db")


def cache_dir() -> str:
    return os.path.join(db.LIBRARY_DIR, "source_cache")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success REAL,
    last_failure REAL,
    last_error_type TEXT,
    last_error TEXT,
    last_latency REAL,
    unavailable_until REAL
);
CREATE TABLE IF NOT EXISTS source_capabilities (
    source TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS access_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tracked_series (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    drama_id INTEGER,
    last_checked REAL,
    last_check_error TEXT,
    PRIMARY KEY (source, series_id)
);
CREATE TABLE IF NOT EXISTS known_chapters (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    title TEXT,
    first_seen REAL NOT NULL,
    PRIMARY KEY (source, series_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS chapter_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    title TEXT,
    created_at REAL NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cache_index (
    url TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    retention TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_images (
    domain TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    chapter_url TEXT NOT NULL,
    PRIMARY KEY (domain, sha256, chapter_url)
);
"""


def connect() -> sqlite3.Connection:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # Every statement is CREATE ... IF NOT EXISTS, so this is cheap and
    # needs no "already initialized?" bookkeeping that could go stale when
    # the library folder moves.
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(key: str):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return json.loads(row["value"])


def set_setting(key: str, value):
    with connect() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))


def all_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    with connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            out[row["key"]] = json.loads(row["value"])
    return out


def adult_enabled(source: str) -> bool:
    return source in (get_setting("adult_sources") or [])


def set_adult_enabled(source: str, enabled: bool):
    current = set(get_setting("adult_sources") or [])
    if enabled:
        current.add(source)
    else:
        current.discard(source)
    set_setting("adult_sources", sorted(current))


# ---------------------------------------------------------------------------
# Capabilities & attempt log
# ---------------------------------------------------------------------------

def save_capabilities(source: str, data: dict):
    with connect() as conn:
        conn.execute("INSERT INTO source_capabilities(source, data, updated_at) VALUES(?, ?, ?) "
                     "ON CONFLICT(source) DO UPDATE SET data=excluded.data, "
                     "updated_at=excluded.updated_at",
                     (source, json.dumps(data, ensure_ascii=False), time.time()))


def load_capabilities(source: str):
    with connect() as conn:
        row = conn.execute("SELECT data FROM source_capabilities WHERE source=?",
                           (source,)).fetchone()
    return json.loads(row["data"]) if row else None


def log_attempt(source: str, url: str, data: dict):
    with connect() as conn:
        conn.execute("INSERT INTO access_attempts(source, url, data, created_at) VALUES(?, ?, ?, ?)",
                     (source, url, json.dumps(data, ensure_ascii=False), time.time()))


def recent_attempts(source: str = None, limit: int = 50) -> list:
    q = "SELECT source, url, data, created_at FROM access_attempts"
    args = ()
    if source:
        q += " WHERE source=?"
        args = (source,)
    q += " ORDER BY id DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(q, args + (limit,)).fetchall()
    return [{"source": r["source"], "url": r["url"], "created_at": r["created_at"],
             **json.loads(r["data"])} for r in rows]


# ---------------------------------------------------------------------------
# Tracked series, known chapters, notifications (Step 23 item 5)
# ---------------------------------------------------------------------------

def track_series(source: str, series_id: str, title: str, url: str = "",
                 drama_id: int = None, known_chapters=()):
    """Starts tracking a series. The chapters it already has are recorded
    as known, so the first check doesn't announce the whole back catalog
    as "new"."""
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO tracked_series(source, series_id, title, url, drama_id, last_checked) "
                     "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(source, series_id) DO UPDATE SET "
                     "title=excluded.title, url=excluded.url, drama_id=excluded.drama_id",
                     (source, series_id, title, url, drama_id, now))
        conn.executemany("INSERT OR IGNORE INTO known_chapters(source, series_id, chapter_id, title, "
                         "first_seen) VALUES(?, ?, ?, ?, ?)",
                         [(source, series_id, c.chapter_id, c.title, now) for c in known_chapters])


def untrack_series(source: str, series_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM tracked_series WHERE source=? AND series_id=?", (source, series_id))
        conn.execute("DELETE FROM known_chapters WHERE source=? AND series_id=?", (source, series_id))


def list_tracked_series() -> list:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM tracked_series ORDER BY title COLLATE NOCASE")]


def known_chapter_ids(source: str, series_id: str) -> set:
    with connect() as conn:
        return {r["chapter_id"] for r in conn.execute(
            "SELECT chapter_id FROM known_chapters WHERE source=? AND series_id=?",
            (source, series_id))}


def record_new_chapters(source: str, series_id: str, chapters) -> int:
    now = time.time()
    with connect() as conn:
        conn.executemany("INSERT OR IGNORE INTO known_chapters(source, series_id, chapter_id, title, "
                         "first_seen) VALUES(?, ?, ?, ?, ?)",
                         [(source, series_id, c.chapter_id, c.title, now) for c in chapters])
        conn.executemany("INSERT INTO chapter_notifications(source, series_id, chapter_id, title, "
                         "created_at) VALUES(?, ?, ?, ?, ?)",
                         [(source, series_id, c.chapter_id, c.title, now) for c in chapters])
    return len(chapters)


def mark_checked(source: str, series_id: str, error: str = None):
    with connect() as conn:
        conn.execute("UPDATE tracked_series SET last_checked=?, last_check_error=? "
                     "WHERE source=? AND series_id=?", (time.time(), error, source, series_id))


def list_notifications(include_dismissed: bool = False) -> list:
    q = "SELECT * FROM chapter_notifications"
    if not include_dismissed:
        q += " WHERE dismissed=0"
    q += " ORDER BY id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q)]


def dismiss_notification(notification_id: int):
    with connect() as conn:
        conn.execute("UPDATE chapter_notifications SET dismissed=1 WHERE id=?", (notification_id,))


# ---------------------------------------------------------------------------
# Generic-import image memory (Step 23 item 8's cross-chapter duplicate filter)
# ---------------------------------------------------------------------------

def remember_images(domain: str, chapter_url: str, hashes):
    with connect() as conn:
        conn.executemany("INSERT OR IGNORE INTO seen_images(domain, sha256, chapter_url) "
                         "VALUES(?, ?, ?)", [(domain, h, chapter_url) for h in hashes])


def hashes_seen_elsewhere(domain: str, chapter_url: str, hashes) -> set:
    """Of `hashes`, the ones already seen on a *different* chapter URL of
    the same site -- a logo/ad/nav image, not a real page."""
    hashes = list(hashes)
    if not hashes:
        return set()
    marks = ",".join("?" * len(hashes))
    with connect() as conn:
        rows = conn.execute(f"SELECT DISTINCT sha256 FROM seen_images WHERE domain=? "
                            f"AND chapter_url<>? AND sha256 IN ({marks})",
                            (domain, chapter_url, *hashes)).fetchall()
    return {r["sha256"] for r in rows}
