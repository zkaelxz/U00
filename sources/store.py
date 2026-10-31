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
from lib import sqlite_check
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
    "source_pace": {},              # source -> careful|normal|fast; absent means normal
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
    (services/library_restore_service.restore_kept_names)."""
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
    save_cbz_by_user_id INTEGER,
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
-- Live: YouTube channels a user follows for streams (services/live_follow_service.py).
CREATE TABLE IF NOT EXISTS followed_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    pc_owner INTEGER NOT NULL DEFAULT 0,
    channel_url TEXT NOT NULL,
    channel_name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'notify',
    auto_check INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '{}',
    poll_minutes INTEGER NOT NULL DEFAULT 60,
    last_checked_at REAL,
    last_state TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    starts_at REAL,
    last_stream_id TEXT NOT NULL DEFAULT '',
    stream_title TEXT NOT NULL DEFAULT '',
    notified_state TEXT NOT NULL DEFAULT '',
    started_stream_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
-- Who saved each CBZ series folder; a folder with no row is the PC owner's.
CREATE TABLE IF NOT EXISTS saved_series_owners (
    source_dir TEXT NOT NULL,
    series_dir TEXT NOT NULL,
    owner_user_id INTEGER
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


def migrate_file(path: str) -> None:
    """Brings the sources database at `path` to the current schema and WAL
    mode. Also run on a library restore's staged copy, which connect()
    would otherwise never migrate: its path is already initialised."""
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


def _initialise(path: str) -> None:
    with _init_lock:
        if path in _initialised and os.path.exists(path):
            return
        migrate_file(path)
        _initialised.add(path)


def snapshot_to(dest: str, source: str = None):
    """Writes a consistent copy of sources.db (or of the sources database
    at `source`) to `dest`, as one file not in WAL mode, through SQLite's
    backup API: a plain file copy misses changes still in sources.db-wal
    and can catch a write half-done. Returns dest, or None, writing
    nothing, when there is no such database."""
    source = source or db_path()
    if not os.path.isfile(source):
        return None
    src = _open(source)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            src.backup(dest_conn)
            dest_conn.execute("PRAGMA journal_mode = DELETE")
        finally:
            dest_conn.close()
    finally:
        src.close()
    return dest


def backup_snapshot(dest: str):
    """snapshot_to for a backup zip, with the settings table emptied: a
    restore keeps the live settings, and the proxy URL can carry a password.
    secure_delete keeps the removed rows out of free pages. Any SQLite
    error (a busy database included) is an OSError, so the caller shows its
    own fixed message."""
    try:
        if snapshot_to(dest) is None:
            return None
        conn = sqlite3.connect(dest, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("DELETE FROM settings")
        finally:
            conn.close()
    except sqlite3.Error:
        raise OSError("sources snapshot could not be prepared") from None
    return dest


CORRUPT_MESSAGE = ("The sources database (sources.db in the library folder) failed its "
                   "integrity check, so followed series, site settings and import history "
                   "may not load. Baihe did not repair or move it: restore a backup, or move "
                   "sources.db out of the library folder to start with an empty one.")


def integrity_problem(path: str = None):
    """CORRUPT_MESSAGE when the sources database is damaged, else None. A
    damaged file is the owner's to restore or set aside, never repaired here."""
    damaged = sqlite_check.damaged(path or db_path(), BUSY_TIMEOUT_SECONDS)
    return CORRUPT_MESSAGE if damaged else None


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
                  ("import_retry", "text_offset", "INTEGER"),
                  ("tracked_series", "save_cbz_by_user_id", "INTEGER"))


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


def source_pace(source: str) -> str:
    return (get_setting("source_pace") or {}).get(source, "normal")


def set_source_pace(source: str, level: str):
    # The map is one JSON setting, so the read-modify-write has to be a single
    # write transaction or two POSTs for different sources lose one change.
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM settings WHERE key=?", ("source_pace",)).fetchone()
        current = dict(json.loads(row["value"])) if row else {}
        current[source] = level
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("source_pace", json.dumps(current)))


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


# Defined in sources/store_tracking.py and sources/store_domains.py; re-exported so
# `store.<name>` keeps working for every caller.
from sources.store_tracking import (  # noqa: E402,F401
    _KEEP_LINK_OWNER, track_series, record_imported, imported_chapter_ids, RETRY_STATUSES,
    MANIFEST_STATUSES, record_import_retry, mark_text_in_flight, import_text_offset,
    import_retry_rows, set_tracked_drama, set_tracked_save, MAX_SAVE_PENDING, save_pending_ids,
    set_save_pending, untrack_series, poll_validators, save_poll_validators, list_tracked_series,
    known_chapter_ids, record_new_chapters, CHECK_CYCLE_LEASE_KEY, _setting_float,
    claim_check_cycle, release_check_cycle, mark_checked, list_notifications,
    dismiss_notification)
from sources.store_domains import (  # noqa: E402,F401
    remember_images, hashes_seen_elsewhere, get_extraction, put_extraction, delete_extraction,
    _domains_key, _last_good_key, domain_list, set_domain_list, last_good_domain,
    set_last_good_domain, MAX_PENDING_PROPOSALS, propose_domain, domain_proposals,
    dismiss_domain_proposal, delete_domain_proposal)
