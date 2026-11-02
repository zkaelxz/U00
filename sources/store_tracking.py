"""sources/store_tracking.py -- Tracked series, known chapters, import retries, check cycles and notifications.

Moved out of sources/store.py to keep it under the size ceiling. Import these
names from `sources.store`, which re-exports them at its end; nothing imports
this module first, because it needs `sources.store` fully defined.
"""

import json
import time

from engine_backends.shared import redact_for_storage
from sources.store import (connect)


# ---------------------------------------------------------------------------
# Tracked series, known chapters, notifications
# ---------------------------------------------------------------------------

# The link owner follows the drama link: it changes only when drama_id does,
# so re-tracking the same link (by anyone) keeps whoever made it.
_KEEP_LINK_OWNER = ("linked_by_user_id=CASE WHEN tracked_series.drama_id IS excluded.drama_id "
                    "THEN tracked_series.linked_by_user_id ELSE excluded.linked_by_user_id END")


def track_series(source: str, series_id: str, title: str, url: str = "",
                 drama_id: int | None = None, known_chapters=(), linked_by_user_id: int | None = None):
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
                      linked_by_user_id: int | None = None) -> bool:
    """Points a tracked series' auto-import at another drama (None = no
    auto-import target) and, if the drama changes, records who linked it.
    Touches nothing else; False if not tracked."""
    with connect() as conn:
        cur = conn.execute("UPDATE tracked_series SET linked_by_user_id=CASE WHEN drama_id IS ? "
                           "THEN linked_by_user_id ELSE ? END, drama_id=? "
                           "WHERE source=? AND series_id=?",
                           (drama_id, linked_by_user_id, drama_id, source, series_id))
        return cur.rowcount > 0


def set_tracked_save(source: str, series_id: str, on: bool, by_user_id: int | None = None) -> bool:
    """Whether the chapter check saves a tracked series' new chapters as
    CBZ files, saved as `by_user_id`'s. Turning it off drops the chapters
    waiting for a retry. Touches nothing else; False if not tracked."""
    with connect() as conn:
        cur = conn.execute("UPDATE tracked_series SET save_cbz=?, save_cbz_by_user_id=?, "
                           "save_pending=CASE WHEN ? THEN save_pending ELSE '[]' END "
                           "WHERE source=? AND series_id=?",
                           (1 if on else 0, by_user_id, 1 if on else 0, source, series_id))
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


def poll_validators(source: str, series_id: str, max_age: float | None = None) -> dict:
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


def mark_checked(source: str, series_id: str, error: str | None = None):
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


def new_chapter_counts_by_drama() -> dict:
    """{drama_id: undismissed new-chapter notifications} for tracked series
    linked to a title, in one query, for the Library's unread badge."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT t.drama_id AS drama_id, COUNT(*) AS n FROM chapter_notifications n "
            "JOIN tracked_series t ON t.source=n.source AND t.series_id=n.series_id "
            "WHERE n.dismissed=0 AND t.drama_id IS NOT NULL GROUP BY t.drama_id")
        return {r["drama_id"]: r["n"] for r in rows}


def dismiss_notification(notification_id: int):
    with connect() as conn:
        conn.execute("UPDATE chapter_notifications SET dismissed=1 WHERE id=?", (notification_id,))
