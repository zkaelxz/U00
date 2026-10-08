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
import re
import sqlite3
import threading
import time

import db
from translate_engines import redact_for_storage, safe_url

# Concrete starting defaults, plus the cache mode
# and the chapter-check schedule. All user-editable in Settings.
DEFAULT_SETTINGS = {
    # Seconds between requests to one source, picked fresh at random in
    # this range for every request. Raised from 1-3s after a real live
    # pass (2026-09-27) got throttled mid-read on a site that had been
    # answering fine: a steady 1-3s is quick and regular enough to read
    # as automated, and the cost of being slower is patience, while the
    # cost of being blocked is the source not working at all. A source
    # can still insist on more via its own robots.txt crawl delay
    # (`host_min_interval`), which is a floor, never lowered by this.
    "pace_min_delay": 3.0,
    "pace_max_delay": 8.0,
    "max_concurrent": 1,            # requests in flight per source
    "max_retries": 3,               # for 429/5xx/timeouts only -- never for a challenge
    "backoff_base": 2.0,            # seconds; doubles each retry
    # A longer pause every N requests to one source (N picked at random in
    # this range each time), on top of the ordinary per-request gap above --
    # a person would set the app down and come back rather than keep an
    # evenly spaced request rate going for a whole session. Either bound at
    # 0 disables it.
    "session_break_min_requests": 8,
    "session_break_max_requests": 20,
    "session_break_min_delay": 30.0,
    "session_break_max_delay": 90.0,
    "unavailable_backoff": 300.0,   # seconds a 🔴 source is left alone after failing
    "cache_mode": "temporary",
    # Roadmap 111: a size ceiling (MB) for what the keep_originals /
    # keep_both cache modes retain. 0 = no limit. Least recently used
    # content is removed first, after each import (sources.cache).
    "cache_max_mb": 0,
    "check_interval_hours": 24,
    "auto_queue_new_chapters": False,
    "demo_source_enabled": False,
    "disabled_sources": [],
    "adult_sources": [],            # sources the person opted in to adult-flagged works for
    "extraction_diagnostics": False,  # Always show Review Extraction + diagnostics
    # An HTTP(S) proxy URL (e.g. "http://127.0.0.1:8080") every
    # source adapter's requests go through. Empty (the default) means no
    # proxy -- direct connections, unchanged from before this setting
    # existed. HTTP(S) only, not SOCKS -- that needs the optional PySocks
    # package, which this app doesn't currently install.
    "http_proxy_url": "",
    # Whether the browser extension's localhost endpoint runs.
    # Off by default -- it opens a port, so it's opt-in, never something
    # a fresh install starts on its own. Persisted here because the flag
    # has to survive a restart (api/background.py reads it at startup);
    # it is not a secret, and the endpoint's token is deliberately NOT
    # stored here -- see page_server.token_path().
    "page_server_enabled": False,
}


def db_path() -> str:
    return os.path.join(db.LIBRARY_DIR, "sources.db")


def cache_dir() -> str:
    return os.path.join(db.LIBRARY_DIR, "source_cache")


BROWSER_PROFILES_DIRNAME = "profiles"


def browser_profiles_root() -> str:
    """Persistent browser profiles (sign-in state). A library restore keeps
    the current ones rather than taking them from the upload
    (services/workspace_job_service.restore_kept_names)."""
    return os.path.join(db.LIBRARY_DIR, BROWSER_PROFILES_DIRNAME)


def browser_profile_dir(key: str) -> str:
    """`profiles/<source>/` -- one Chromium profile per source (or per
    domain, for a pasted URL no adapter covers)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key or "").strip("._") or "_"
    return os.path.join(browser_profiles_root(), safe)


SCHEMA = """
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
-- One row = the person marked this source as working only through the browser
-- extension. A separate table (not the capabilities JSON) so a tier test, which
-- rewrites that record, can never wipe the marker.
CREATE TABLE IF NOT EXISTS source_extension_only (
    source TEXT PRIMARY KEY,
    marked_at REAL NOT NULL,
    marked_by_user_id INTEGER,
    note TEXT NOT NULL DEFAULT ''
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
    linked_by_user_id INTEGER,
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
CREATE TABLE IF NOT EXISTS chapter_poll_validators (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    url TEXT NOT NULL,
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (source, series_id)
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
CREATE TABLE IF NOT EXISTS imported_chapters (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    drama_id INTEGER NOT NULL,
    imported_at REAL NOT NULL,
    PRIMARY KEY (source, series_id, chapter_id, drama_id)
);
CREATE TABLE IF NOT EXISTS import_retry (
    source TEXT NOT NULL,
    series_id TEXT NOT NULL,
    drama_id INTEGER NOT NULL,
    chapter_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    text_offset INTEGER,
    PRIMARY KEY (source, series_id, drama_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS domain_proposals (
    source TEXT NOT NULL,
    host TEXT NOT NULL,
    found_at REAL NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, host)
);
CREATE TABLE IF NOT EXISTS extraction_cache (
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (kind, content_hash)
);
"""


BUSY_TIMEOUT_SECONDS = 10

BUSY_MESSAGE = "The sources database is busy, try again."


class SourcesDatabaseBusy(sqlite3.OperationalError):
    """sources.db stayed locked past the busy timeout. The message is fixed
    text, safe to show as is (the raw sqlite error is not)."""

    def __init__(self):
        super().__init__(BUSY_MESSAGE)


def _is_busy(exc) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


class _Connection(sqlite3.Connection):
    """Turns a lock timeout from any statement into SourcesDatabaseBusy, so
    the dozens of `with connect() as conn:` callers need no handling."""

    def _guard(self, name, *args):
        try:
            return getattr(super(), name)(*args)
        except SourcesDatabaseBusy:
            raise
        except sqlite3.OperationalError as e:
            if _is_busy(e):
                raise SourcesDatabaseBusy() from None
            raise

    def execute(self, *args):
        return self._guard("execute", *args)

    def executemany(self, *args):
        return self._guard("executemany", *args)

    def executescript(self, *args):
        return self._guard("executescript", *args)

    def commit(self):
        return self._guard("commit")

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        except sqlite3.OperationalError as e:
            if _is_busy(e):
                raise SourcesDatabaseBusy() from None
            raise


def log_dropped(what: str) -> None:
    """Notes a best-effort write that was dropped because sources.db stayed
    locked. Never raises."""
    try:
        import applog
        applog.get_logger().warning("Dropped %s: %s", what, BUSY_MESSAGE)
    except Exception:
        pass


# Database files whose schema and migrations have already run in this
# process. A file deleted since (a moved library, a test's temp dir) is
# initialised again.
_initialised = set()
_init_lock = threading.Lock()


def _open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, factory=_Connection)
    conn.row_factory = sqlite3.Row
    return conn


def _initialise(path: str) -> None:
    with _init_lock:
        if path in _initialised and os.path.exists(path):
            return
        conn = _open(path)
        try:
            # WAL is stored in the file, so it is set here once rather than
            # per connection: readers then never wait on the writer.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(SCHEMA)
            _add_missing_columns(conn)
        finally:
            conn.close()
        _initialised.add(path)


def connect() -> sqlite3.Connection:
    path = os.path.abspath(db_path())
    if path not in _initialised or not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _initialise(path)
    conn = _open(path)
    # synchronous is per connection (journal_mode is not).
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# Columns added after a table first shipped: CREATE TABLE IF NOT EXISTS
# leaves an older sources.db without them.
_ADDED_COLUMNS = (("tracked_series", "linked_by_user_id", "INTEGER"),
                  ("tracked_series", "save_cbz", "INTEGER NOT NULL DEFAULT 0"),
                  ("tracked_series", "save_pending", "TEXT NOT NULL DEFAULT '[]'"),
                  ("import_retry", "text_offset", "INTEGER"))


def _add_missing_columns(conn) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                conn.commit()
            except sqlite3.OperationalError as e:
                # Only "another connection added it first" is fine.
                if "duplicate column name" not in str(e).lower() and column not in {
                        r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    raise


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


def _redact_any(value):
    if isinstance(value, str):
        return redact_for_storage(value)
    if isinstance(value, dict):
        return {k: _redact_any(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_any(v) for v in value]
    return value


def log_attempt(source: str, url: str, data: dict):
    """The URL is stored as scheme+host+path and the data with secrets and URL
    queries removed: this table is part of whole-library backups. Readers
    only ever show the query-less form, so nothing needs the full URL."""
    with connect() as conn:
        conn.execute("INSERT INTO access_attempts(source, url, data, created_at) VALUES(?, ?, ?, ?)",
                     (source, safe_url(url), json.dumps(_redact_any(data), ensure_ascii=False),
                      time.time()))


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
# Tracked series, known chapters, notifications
# ---------------------------------------------------------------------------

# The link owner follows the drama link: it changes only when drama_id does,
# so re-tracking the same link (by anyone) keeps whoever made it.
_KEEP_LINK_OWNER = ("linked_by_user_id=CASE WHEN tracked_series.drama_id IS excluded.drama_id "
                    "THEN tracked_series.linked_by_user_id ELSE excluded.linked_by_user_id END")


def track_series(source: str, series_id: str, title: str, url: str = "",
                 drama_id: int = None, known_chapters=(), linked_by_user_id: int = None):
    """Starts tracking a series. The chapters it already has are recorded
    as known, so the first check doesn't announce the whole back catalog
    as "new". `linked_by_user_id`: the user who set the drama link (None =
    auth off / the PC owner); the scheduled check imports only while that
    user can still edit the drama."""
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO tracked_series(source, series_id, title, url, drama_id, "
                     "last_checked, linked_by_user_id) VALUES(?, ?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(source, series_id) DO UPDATE SET title=excluded.title, "
                     "url=excluded.url, " + _KEEP_LINK_OWNER + ", drama_id=excluded.drama_id",
                     (source, series_id, title, url, drama_id, now, linked_by_user_id))
        conn.executemany("INSERT OR IGNORE INTO known_chapters(source, series_id, chapter_id, title, "
                         "first_seen) VALUES(?, ?, ?, ?, ?)",
                         [(source, series_id, c.chapter_id, c.title, now) for c in known_chapters])


def record_imported(source: str, series_id: str, chapter_id: str, drama_id: int):
    """Records that one chapter was imported into one drama, so a later
    import of the same chapter into the same drama is skipped (the API's
    chapter import; pipeline.run_import_job calls this after each success)."""
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO imported_chapters(source, series_id, chapter_id, "
                     "drama_id, imported_at) VALUES(?, ?, ?, ?, ?)",
                     (source, str(series_id), str(chapter_id), int(drama_id), time.time()))


def imported_chapter_ids(source: str, series_id: str, drama_id: int) -> set:
    with connect() as conn:
        return {r["chapter_id"] for r in conn.execute(
            "SELECT chapter_id FROM imported_chapters WHERE source=? AND series_id=? "
            "AND drama_id=?", (source, str(series_id), int(drama_id)))}


RETRY_STATUSES = ("failed", "not_attempted")
# "partial": the chapter an unexpected error interrupted mid-write -- some of
# its pages or text may be in the drama already, so it is shown (check it
# first) but never part of the automatic retry.
# "needs_ai": the page loaded but the adapter's layout no longer matches; it
# waits for the person to confirm an AI recovery, so it is not retried either.
MANIFEST_STATUSES = RETRY_STATUSES + ("partial", "needs_ai")


def record_import_retry(source: str, series_id: str, drama_id: int, pending, done_ids=()):
    """The failed-chapter manifest for one (series, drama). `pending`
    is (chapter_id, title, status, error) per chapter that failed or was not
    attempted (status in MANIFEST_STATUSES; title and error already redacted by
    the caller); `done_ids` are chapters this import imported or skipped,
    which leave the manifest."""
    now = time.time()
    with connect() as conn:
        conn.executemany("DELETE FROM import_retry WHERE source=? AND series_id=? AND "
                         "drama_id=? AND chapter_id=?",
                         [(source, str(series_id), int(drama_id), str(c)) for c in done_ids])
        conn.executemany(
            "INSERT INTO import_retry(source, series_id, drama_id, chapter_id, title, status, "
            "error, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source, series_id, "
            "drama_id, chapter_id) DO UPDATE SET title=excluded.title, status=excluded.status, "
            "error=excluded.error, updated_at=excluded.updated_at",
            [(source, str(series_id), int(drama_id), str(cid), title or "", status, error or "",
              now) for cid, title, status, error in pending if status in MANIFEST_STATUSES])


def mark_text_in_flight(source: str, series_id: str, drama_id: int, chapter_id: str,
                        title: str, error: str, text_offset: int):
    """Marks a text chapter "failed" (retryable) before its text is appended,
    with the raw-novel file's length before the append (-1: no file), so a
    retry can find what an interrupted attempt wrote. Title and error
    already redacted by the caller."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO import_retry(source, series_id, drama_id, chapter_id, title, status, "
            "error, updated_at, text_offset) VALUES(?, ?, ?, ?, ?, 'failed', ?, ?, ?) "
            "ON CONFLICT(source, series_id, drama_id, chapter_id) DO UPDATE SET "
            "title=excluded.title, status=excluded.status, error=excluded.error, "
            "updated_at=excluded.updated_at, text_offset=excluded.text_offset",
            (source, str(series_id), int(drama_id), str(chapter_id), title or "", error or "",
             time.time(), int(text_offset)))


def import_text_offset(source: str, series_id: str, drama_id: int, chapter_id: str):
    """The offset mark_text_in_flight recorded for a chapter still in the
    manifest, or None (no row, or a row from a failure before any write)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT text_offset FROM import_retry WHERE source=? AND series_id=? AND "
            "drama_id=? AND chapter_id=?",
            (source, str(series_id), int(drama_id), str(chapter_id))).fetchone()
    return None if row is None else row["text_offset"]


def import_retry_rows(source: str, series_id: str, drama_id: int) -> list:
    """The manifest's chapters still waiting for a retry, oldest first. A
    chapter imported since (by any path, e.g. the auto-import) is left out."""
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT chapter_id, title, status, error, updated_at FROM import_retry r "
            "WHERE source=? AND series_id=? AND drama_id=? AND NOT EXISTS (SELECT 1 FROM "
            "imported_chapters i WHERE i.source=r.source AND i.series_id=r.series_id AND "
            "i.drama_id=r.drama_id AND i.chapter_id=r.chapter_id) ORDER BY updated_at, rowid",
            (source, str(series_id), int(drama_id)))]


def set_tracked_drama(source: str, series_id: str, drama_id,
                      linked_by_user_id: int = None) -> bool:
    """Points a tracked series' auto-import at another drama (None = no
    auto-import target) and, if the drama changes, records who linked it.
    Touches nothing else; False if not tracked."""
    with connect() as conn:
        cur = conn.execute("UPDATE tracked_series SET linked_by_user_id=CASE WHEN drama_id IS ? "
                           "THEN linked_by_user_id ELSE ? END, drama_id=? "
                           "WHERE source=? AND series_id=?",
                           (drama_id, linked_by_user_id, drama_id, source, series_id))
        return cur.rowcount > 0


def set_tracked_save(source: str, series_id: str, on: bool) -> bool:
    """Whether the chapter check saves a tracked series' new chapters as
    CBZ files. Turning it off drops the chapters waiting for a retry.
    Touches nothing else; False if not tracked."""
    with connect() as conn:
        cur = conn.execute("UPDATE tracked_series SET save_cbz=?, save_pending=CASE WHEN ? THEN "
                           "save_pending ELSE '[]' END WHERE source=? AND series_id=?",
                           (1 if on else 0, 1 if on else 0, source, series_id))
        return cur.rowcount > 0


MAX_SAVE_PENDING = 500


def save_pending_ids(row: dict) -> list:
    """Chapter ids a tracked series' auto-save still owes (a save that
    failed or was cut short), from a list_tracked_series() row."""
    try:
        ids = json.loads(row.get("save_pending") or "[]")
    except ValueError:
        return []
    return [str(i) for i in ids if isinstance(i, (str, int))] if isinstance(ids, list) else []


def set_save_pending(source: str, series_id: str, chapter_ids) -> None:
    """The chapters the next check retries saving (newest kept when capped)."""
    ids = list(dict.fromkeys(str(c) for c in chapter_ids))[-MAX_SAVE_PENDING:]
    with connect() as conn:
        conn.execute("UPDATE tracked_series SET save_pending=? WHERE source=? AND series_id=?",
                     (json.dumps(ids), source, series_id))


def untrack_series(source: str, series_id: str):
    with connect() as conn:
        conn.execute("DELETE FROM tracked_series WHERE source=? AND series_id=?", (source, series_id))
        conn.execute("DELETE FROM known_chapters WHERE source=? AND series_id=?", (source, series_id))
        conn.execute("DELETE FROM chapter_poll_validators WHERE source=? AND series_id=?",
                     (source, series_id))


def poll_validators(source: str, series_id: str, max_age: float = None) -> dict:
    """The ETag / Last-Modified the last chapter-list poll of this series
    got for its one URL, as conditional_poll() kwargs; {} if
    none, or if they were saved more than `max_age` seconds ago (a 304
    doesn't refresh them, so the list is fetched in full now and then)."""
    with connect() as conn:
        row = conn.execute("SELECT url, etag, last_modified, updated_at FROM "
                           "chapter_poll_validators WHERE source=? AND series_id=?",
                           (source, series_id)).fetchone()
    if not row or (max_age is not None and time.time() - float(row["updated_at"]) > max_age):
        return {}
    return {"url": row["url"], "etag": row["etag"], "last_modified": row["last_modified"]}


def save_poll_validators(source: str, series_id: str, validators) -> None:
    """Stores (url, etag, last_modified) for the next poll; None forgets
    them, so the next poll is an ordinary full fetch."""
    with connect() as conn:
        if not validators:
            conn.execute("DELETE FROM chapter_poll_validators WHERE source=? AND series_id=?",
                         (source, series_id))
            return
        url, etag, last_modified = validators
        conn.execute("INSERT INTO chapter_poll_validators(source, series_id, url, etag, "
                     "last_modified, updated_at) VALUES(?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(source, series_id) DO UPDATE SET url=excluded.url, "
                     "etag=excluded.etag, last_modified=excluded.last_modified, "
                     "updated_at=excluded.updated_at",
                     (source, series_id, url, etag or "", last_modified or "", time.time()))


def list_tracked_series() -> list:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM tracked_series ORDER BY title COLLATE NOCASE")]


def known_chapter_ids(source: str, series_id: str) -> set:
    with connect() as conn:
        return {r["chapter_id"] for r in conn.execute(
            "SELECT chapter_id FROM known_chapters WHERE source=? AND series_id=?",
            (source, series_id))}


def record_new_chapters(source: str, series_id: str, chapters) -> list:
    """Records chapters as known and adds one notification for each chapter
    that was not known yet. Returns the chapters actually recorded.

    Idempotent, including across processes (two processes can both
    run a check): the known_chapters primary key decides, and each
    notification is inserted in the same write transaction as the
    known_chapters row it depends on, so a chapter two checks find at once
    is notified (and returned, e.g. for auto-import) exactly once."""
    now = time.time()
    recorded = []
    with connect() as conn:
        for c in chapters:
            cur = conn.execute("INSERT OR IGNORE INTO known_chapters(source, series_id, "
                               "chapter_id, title, first_seen) VALUES(?, ?, ?, ?, ?)",
                               (source, series_id, c.chapter_id, c.title, now))
            if cur.rowcount == 1:
                conn.execute("INSERT INTO chapter_notifications(source, series_id, chapter_id, "
                             "title, created_at) VALUES(?, ?, ?, ?, ?)",
                             (source, series_id, c.chapter_id, c.title, now))
                recorded.append(c)
    return recorded


CHECK_CYCLE_LEASE_KEY = "check_cycle_started_at"


def _setting_float(conn, key: str) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    try:
        return float(json.loads(row["value"])) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def claim_check_cycle(now: float, lease_seconds: float, min_gap_seconds: float = 0.0):
    """Claims the next chapter-check cycle for the caller, atomically across
    processes (BEGIN IMMEDIATE: one writer at a time). Refused (None) while
    another cycle's claim is younger than `lease_seconds`, or, with
    `min_gap_seconds` > 0 (a scheduled cycle), while the last finished
    cycle is younger than that. Returns the claim token to pass to
    release_check_cycle."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        started = _setting_float(conn, CHECK_CYCLE_LEASE_KEY)
        last = _setting_float(conn, "last_check_cycle")
        if (started and now - started < lease_seconds) or (
                min_gap_seconds > 0 and now - last < min_gap_seconds):
            conn.rollback()
            return None
        token = json.dumps(now)
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (CHECK_CYCLE_LEASE_KEY, token))
        conn.commit()
        return token
    finally:
        conn.close()


def release_check_cycle(token) -> None:
    """Ends a claim, only if it is still this caller's (an expired claim
    another process took over is left alone)."""
    with connect() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key=? AND value=?",
                     (json.dumps(0), CHECK_CYCLE_LEASE_KEY, token))


def mark_checked(source: str, series_id: str, error: str = None):
    with connect() as conn:
        conn.execute("UPDATE tracked_series SET last_checked=?, last_check_error=? "
                     "WHERE source=? AND series_id=?", (time.time(), redact_for_storage(error), source, series_id))


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
# Generic-import image memory (cross-chapter duplicate filter)
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


# ---------------------------------------------------------------------------
# Extraction-result cache -- keyed by a hash of what the
# model read, so an unchanged page never costs a second LLM call. Separate
# from the raw-content cache (cache_index), which is about not re-fetching.
# ---------------------------------------------------------------------------

def get_extraction(kind: str, content_hash: str):
    with connect() as conn:
        row = conn.execute("SELECT data FROM extraction_cache WHERE kind=? AND content_hash=?",
                           (kind, content_hash)).fetchone()
    return json.loads(row["data"]) if row else None


def put_extraction(kind: str, content_hash: str, url: str, entry: dict):
    with connect() as conn:
        conn.execute("INSERT INTO extraction_cache(kind, content_hash, url, data, created_at) "
                     "VALUES(?, ?, ?, ?, ?) ON CONFLICT(kind, content_hash) DO UPDATE SET "
                     "url=excluded.url, data=excluded.data, created_at=excluded.created_at",
                     (kind, content_hash, url, json.dumps(entry, ensure_ascii=False), time.time()))


def delete_extraction(kind: str, content_hash: str):
    with connect() as conn:
        conn.execute("DELETE FROM extraction_cache WHERE kind=? AND content_hash=?",
                     (kind, content_hash))


# ---------------------------------------------------------------------------
# Domain lists (sources/domains.py). The list and the last domain that
# worked are settings, one pair of keys per source; a host found by
# a redirect is only a proposal until the owner confirms it.
# ---------------------------------------------------------------------------

def _domains_key(source: str) -> str:
    return f"source_domains.{source}"


def _last_good_key(source: str) -> str:
    return f"source_domain_last_good.{source}"


def domain_list(source: str):
    """The owner's saved domain list (https origins), or None when the
    adapter's own `base_urls` apply."""
    value = get_setting(_domains_key(source))
    return [str(v) for v in value] if isinstance(value, list) and value else None


def set_domain_list(source: str, origins):
    set_setting(_domains_key(source), list(origins) if origins else None)


def last_good_domain(source: str):
    value = get_setting(_last_good_key(source))
    return str(value) if value else None


def set_last_good_domain(source: str, origin):
    set_setting(_last_good_key(source), origin or None)


MAX_PENDING_PROPOSALS = 5


def propose_domain(source: str, host: str, now: float = None) -> bool:
    """Records a pending proposal (`host` is host[:port]). False when the
    host was already proposed, was dismissed before (a dismissed host is not
    proposed again), or the source already has MAX_PENDING_PROPOSALS
    waiting."""
    # One statement: the count and the insert run under the same write lock.
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO domain_proposals(source, host, found_at) SELECT ?, ?, ? "
            "WHERE (SELECT COUNT(*) FROM domain_proposals WHERE source=? AND dismissed=0) < ?",
            (source, host, time.time() if now is None else now, source, MAX_PENDING_PROPOSALS))
    return cur.rowcount > 0


def domain_proposals(source: str = None) -> list:
    """Pending (not dismissed) proposals, newest first."""
    sql = "SELECT source, host, found_at FROM domain_proposals WHERE dismissed=0"
    args = ()
    if source is not None:
        sql += " AND source=?"
        args = (source,)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY found_at DESC", args)]


def dismiss_domain_proposal(source: str, host: str) -> bool:
    with connect() as conn:
        cur = conn.execute("UPDATE domain_proposals SET dismissed=1 "
                           "WHERE source=? AND host=? AND dismissed=0", (source, host))
    return cur.rowcount > 0


def delete_domain_proposal(source: str, host: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM domain_proposals WHERE source=? AND host=? AND dismissed=0",
                           (source, host))
    return cur.rowcount > 0
