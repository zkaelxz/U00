"""Gives library.db's free pages back to the disk, which SQLite never does on its own."""

import logging
import os
import shutil
import sqlite3

import db

log = logging.getLogger(__name__)

# A deleted title leaves its pages inside the file for reuse; only a VACUUM
# shrinks it. VACUUM rewrites the whole file, so it waits until the free space
# is worth the pause and runs only at startup, before anything else has the
# library open.
MIN_RECLAIM_BYTES = 64 * 1024 * 1024
MIN_RECLAIM_FRACTION = 0.25
NOTICE = "Shrinking library.db to give back unused disk space. This can take a minute on a large library."


def reclaimable_bytes(conn) -> tuple[int, int]:
    """(free bytes inside the file, total file bytes)."""
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    total = conn.execute("PRAGMA page_count").fetchone()[0]
    return free * page_size, total * page_size


def compact_if_worthwhile(path: str | None = None, notify=None) -> bool:
    """VACUUMs the library when enough of it is free pages. Never raises: a
    busy or full disk just leaves the file as it was. `notify(message)` is
    called just before the pause. True when it ran."""
    path = path or db.DB_PATH
    try:
        conn = sqlite3.connect(path, isolation_level=None, timeout=1.0)
        try:
            # Exclusive locking mode plus one exclusive transaction takes the
            # lock for good, or fails at once when another process (a second
            # server, a CLI run) has the file open: the VACUUM then waits for
            # a quieter start instead of stalling that process.
            conn.execute("PRAGMA locking_mode = EXCLUSIVE")
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("ROLLBACK")
            free, total = reclaimable_bytes(conn)
            if free < MIN_RECLAIM_BYTES or free < total * MIN_RECLAIM_FRACTION:
                return False
            # In WAL mode the rewritten copy is also written through the
            # log, so the pause needs room for about two copies.
            if shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free < 2 * (total - free) + MIN_RECLAIM_BYTES:
                return False
            if notify:
                notify(NOTICE)
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        log.warning("Could not shrink library.db", exc_info=True)
        return False
