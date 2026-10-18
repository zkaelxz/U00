"""
lib/sqlite_check.py -- whether a SQLite file on disk is damaged, checked
without changing it.
"""

import os
import sqlite3
from urllib.parse import quote


def damaged(path: str, timeout: float = 5.0) -> bool:
    """True when the file at `path` fails SQLite's quick_check or is not a
    database at all. False when it is sound, missing, or can't be opened
    right now (locked, an I/O error): that is not damage to the file.
    Opened read-only, so the check never creates, migrates or repairs it."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=timeout)
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return False
    except sqlite3.DatabaseError:
        return True
    return [r[0] for r in rows] != ["ok"]
