"""The activity_log table: a durable line per finished job, for the activity log and the daily GPU chart."""

import contextlib
import time

import db

# Long enough for a year's chart; older rows go on the next write so the table never needs hand care.
KEEP_DAYS = 400
MAX_LABEL = 200
OUTCOMES = ("done", "error", "cancelled")


def add_activity(job_id, label, owner_user_id, outcome, started_at, ended_at, gpu) -> None:
    """Never raises: logging a finished job must not disturb the job runner."""
    try:
        outcome = outcome if outcome in OUTCOMES else "error"
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute(
                "INSERT INTO activity_log (job_id, label, owner_user_id, outcome, started_at, ended_at, seconds, gpu) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(job_id), str(label or "")[:MAX_LABEL], owner_user_id, outcome, started_at, ended_at,
                 max(0.0, ended_at - started_at), 1 if gpu else 0))
            conn.execute("DELETE FROM activity_log WHERE ended_at < ?", (time.time() - KEEP_DAYS * 86400,))
            conn.commit()
    except Exception:
        pass


def list_activity(limit: int, before_id=None) -> list:
    """Newest first. The caller filters by who may see each row."""
    sql = ("SELECT id, job_id, label, owner_user_id, outcome, started_at, ended_at, seconds, gpu "
           "FROM activity_log")
    args = []
    if before_id is not None:
        sql += " WHERE id < ?"
        args.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def gpu_rows_since(since: float) -> list:
    """GPU jobs that ended since `since`, each with its local calendar day; the caller sums them per day
    after keeping only the rows it may see."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT owner_user_id, job_id, date(ended_at, 'unixepoch', 'localtime') AS day, seconds "
            "FROM activity_log WHERE gpu = 1 AND ended_at >= ?", (since,)).fetchall()
    return [dict(r) for r in rows]
