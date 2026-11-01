"""The job_records table: the cross-process mirror of background job
status. The table and its added columns are created by db.init_db."""

import contextlib
import time

import db


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------

def save_job_record(job_id: str, status: str, progress: float | None = None, message: str | None = None,
                    error: str | None = None, description: str | None = None, gpu_touching: bool = False,
                    started_at: float | None = None, finished_at: float | None = None,
                    result_json: str | None = None, owner_user_id: int | None = None, owner_pid: int | None = None,
                    conn=None):
    """Mirrors one background_jobs.py job's status-transition fields into
    the cross-process job_records table -- records
    only, no resume: this is the *last written* state, not necessarily
    the *current* state, if the process that wrote it has since died
    without writing a terminal status. A caller reading this table for
    cross-process visibility should treat a long-unchanged `updated_at`
    on a "running"/"queued" row as suspect, not trust `status` blindly.
    With `conn`, runs in the caller's transaction and the caller commits."""
    own = conn is None
    conn = db.get_conn() if own else conn
    try:
        conn.execute("""
            INSERT INTO job_records (job_id, status, progress, message, error, description,
                gpu_touching, started_at, finished_at, updated_at, result_json, owner_user_id,
                owner_pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status, progress = excluded.progress,
                message = excluded.message, error = excluded.error,
                description = excluded.description, gpu_touching = excluded.gpu_touching,
                started_at = excluded.started_at, finished_at = excluded.finished_at,
                updated_at = excluded.updated_at, result_json = excluded.result_json,
                owner_pid = excluded.owner_pid,
                owner_user_id = CASE
                    WHEN excluded.status IN ('queued', 'running')
                         AND job_records.status NOT IN ('queued', 'running')
                    THEN excluded.owner_user_id
                    -- A new run (auth B2): a stale "running" row left by a
                    -- crashed process must not keep its old owner.
                    WHEN excluded.status IN ('queued', 'running')
                         AND excluded.started_at IS NOT job_records.started_at
                    THEN excluded.owner_user_id
                    ELSE COALESCE(job_records.owner_user_id, excluded.owner_user_id) END,
                -- A new run must not inherit a cancel aimed at the run it
                -- replaced, or it would end cancelled at once and be
                -- Force-stoppable straight away. Promotion from the queue
                -- restamps started_at, but it is the same run: a cancel sent
                -- to the queued row (by the CLI or another server) must hold.
                cancel_requested = CASE WHEN excluded.status IN ('queued', 'running')
                    AND (excluded.started_at IS job_records.started_at
                         OR (job_records.status = 'queued'
                             AND excluded.owner_pid IS job_records.owner_pid))
                    THEN job_records.cancel_requested ELSE 0 END,
                cancel_requested_at = CASE WHEN excluded.status IN ('queued', 'running')
                    AND (excluded.started_at IS job_records.started_at
                         OR (job_records.status = 'queued'
                             AND excluded.owner_pid IS job_records.owner_pid))
                    THEN job_records.cancel_requested_at ELSE NULL END
        """, (job_id, status, progress, message, error, description, int(bool(gpu_touching)),
              started_at, finished_at, time.time(), result_json, owner_user_id, owner_pid))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def touch_job_records(job_ids) -> None:
    """The owning process's heartbeat (background_jobs) -- bumps
    updated_at on its still queued/running rows so a job that is alive
    but not changing status never looks abandoned. updated_at is only ever
    written by the owner (a cancel request leaves it alone)."""
    job_ids = list(job_ids)
    if not job_ids:
        return
    now = time.time()
    with contextlib.closing(db.get_conn()) as conn:
        conn.executemany(
            "UPDATE job_records SET updated_at = ? "
            "WHERE job_id = ? AND status IN ('queued', 'running')",
            [(now, j) for j in job_ids])
        conn.commit()


def list_job_records() -> list:
    """Every job_records row, newest-started first -- the cross-process
    job list a `GET /api/jobs` endpoint would read.
    Rows accumulate forever unless cleared (delete_job_record/
    clear_all_job_records) -- no automatic pruning."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM job_records ORDER BY started_at DESC NULLS LAST").fetchall()
        return [dict(r) for r in rows]


def list_job_record_fingerprints() -> dict:
    """{job_id: (status, progress, message, error, finished_at,
    cancel_requested, result length)} for every job_records row: the cheap
    change check behind the SSE stream's sweep (services/
    event_stream_service.py). updated_at is left out on purpose: the
    heartbeat bumps it without any visible change."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT job_id, status, progress, message, error, finished_at, cancel_requested, "
            "LENGTH(result_json) FROM job_records").fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def count_active_job_records() -> dict:
    """{"queued": n, "running": n} over every job_records row."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM job_records "
                            "WHERE status IN ('queued', 'running') GROUP BY status").fetchall()
    return {"queued": 0, "running": 0, **{r[0]: r[1] for r in rows}}


def get_job_record(job_id: str):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM job_records WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def delete_job_record(job_id: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM job_records WHERE job_id = ?", (job_id,))
        conn.commit()


_ANY_RUN = object()


def delete_finished_job_record(job_id: str, started_at=_ANY_RUN) -> bool:
    """Deletes the row only while it is a finished run (and, with
    `started_at`, the run the caller saw): a run of the same id that started
    since the caller's check keeps its row. False when a row is still there."""
    finished = "status IN ('done', 'error', 'cancelled')"
    with contextlib.closing(db.get_conn()) as conn:
        if started_at is _ANY_RUN:
            conn.execute(f"DELETE FROM job_records WHERE job_id = ? AND {finished}", (job_id,))
        else:
            conn.execute(f"DELETE FROM job_records WHERE job_id = ? AND {finished} "
                         "AND started_at IS ?", (job_id, started_at))
        conn.commit()
        return conn.execute("SELECT 1 FROM job_records WHERE job_id = ?",
                            (job_id,)).fetchone() is None


def clear_all_job_records():
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM job_records")
        conn.commit()
