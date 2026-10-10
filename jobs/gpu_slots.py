"""jobs/gpu_slots.py -- the one way any process takes, keeps and frees a GPU slot.

background_jobs' in-process queue is invisible to a cli.py run or a second
server, so the gpu_lock table in the shared library.db is where they meet: one
row per holder, `id` is the slot and the table's CHECK is the hard cap. This
module is the only code that reads or writes that table (db.init_db creates it).

A holder is "<scope>:<name>": `ui:<job id>` for a server job, `cli:<pid>`,
`live-whisper:<pid>` and `check:<pid>` otherwise. Whether a row still counts is
decided from its owner columns, never from the holder string; see `_alive`.
See docs/background-jobs.md ("The GPU guard").
"""

import contextlib
import math
import os
import time

import db
import gpu_probe
from jobs import job_store

# How long a row nobody heartbeats is trusted before it is treated as left
# behind by a holder that crashed or hung. Comfortably longer than the
# progress and heartbeat-thread cadence, so a live holder's beats always land
# well inside it.
GPU_LOCK_STALE_SECONDS = 600
# Matches the gpu_lock table's CHECK: no caller can hold more slots.
GPU_LOCK_MAX_SLOTS = 4
# A job joins one that is already running only with at least this much free
# VRAM per nvidia-smi, and only once the newest holder has had this long to
# load its model (so the free-VRAM reading includes it).
GPU_PARALLEL_RESERVE_MB = 2048
GPU_PARALLEL_SETTLE_SECONDS = 30

UI_PREFIX = "ui:"


def _owner_alive(pid) -> bool:
    import background_jobs
    return background_jobs.owner_process_alive(pid)


def _alive(row, now) -> bool:
    """A row counts while its heartbeat is fresh and its owner still runs. An
    owner is gone when its pid has exited, or the pid is this process's but
    the instance is not (an earlier server had the pid). A row written before
    the owner columns existed has only the heartbeat to go on."""
    if now - row["heartbeat_at"] >= GPU_LOCK_STALE_SECONDS:
        return False
    pid = row["owner_pid"]
    if pid is None:
        return True
    if pid == os.getpid():
        return row["owner_instance"] == job_store.INSTANCE_ID
    return _owner_alive(pid)


def _mine(row) -> bool:
    """Rows this process wrote (or legacy rows with no owner), so a second
    server's `ui:<id>` row of the same job id is never refreshed or freed here."""
    return row["owner_instance"] in (None, job_store.INSTANCE_ID)


def _rows(conn):
    return conn.execute("SELECT id, holder, description, acquired_at, heartbeat_at, owner_pid, "
                        "owner_instance, job_id FROM gpu_lock ORDER BY id").fetchall()


def _live_rows(now=None):
    now = time.time() if now is None else now
    with contextlib.closing(db.get_conn()) as conn:
        return [r for r in _rows(conn) if _alive(r, now)]


def take(holder: str, description: str = None, *, max_holders: int = 1,
         settle_seconds: float = 0, job_id: str = None) -> bool:
    """Claims a slot for `holder`, with no setting or VRAM check (acquire
    adds those). True if `holder` already holds one (refreshed), or fewer
    than `max_holders` (at most GPU_LOCK_MAX_SLOTS) other live holders exist
    and it now holds one. With `settle_seconds`, joining other holders is
    also refused until the newest of them has held its slot that long; it is
    checked here, inside BEGIN IMMEDIATE, so two processes reading the same
    free VRAM can't both join on it. A row that no longer counts (_alive) is
    taken over."""
    now = time.time()
    max_holders = max(1, min(int(max_holders), GPU_LOCK_MAX_SLOTS))
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = _rows(conn)
        live = [r for r in rows if _alive(r, now)]
        mine = next((r for r in live if r["holder"] == holder and _mine(r)), None)
        if mine is not None:
            slot = mine["id"]
        else:
            if len(live) >= max_holders or (
                    live and settle_seconds
                    and now - max(r["acquired_at"] for r in live) < settle_seconds):
                # Ends the IMMEDIATE transaction here so the write lock other
                # processes wait on is released at once, not left to close().
                conn.execute("ROLLBACK")
                return False
            taken = {r["id"] for r in live}
            # A dead row of this holder is reused, so a holder never has two rows.
            stale_own = [r["id"] for r in rows if r["holder"] == holder and r["id"] not in taken]
            slot = (stale_own or [i for i in range(1, GPU_LOCK_MAX_SLOTS + 1) if i not in taken])[0]
        conn.execute("""
            INSERT INTO gpu_lock (id, holder, description, acquired_at, heartbeat_at,
                                  owner_pid, owner_instance, job_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET holder = excluded.holder,
                description = excluded.description, acquired_at = excluded.acquired_at,
                heartbeat_at = excluded.heartbeat_at, owner_pid = excluded.owner_pid,
                owner_instance = excluded.owner_instance, job_id = excluded.job_id
        """, (slot, holder, description, now, now, os.getpid(), job_store.INSTANCE_ID, job_id))
        conn.commit()
        return True


def _vram_room_for_another_gpu_job() -> bool:
    """True only when nvidia-smi reports at least GPU_PARALLEL_RESERVE_MB
    free. No reading (no nvidia-smi, a failed query) is False: without it
    nothing can tell whether a second model fits, so jobs run one at a time.
    No per-job size estimate is used: none is known before a job loads."""
    import background_jobs
    try:
        load = gpu_probe.external_gpu_load()
    except Exception as exc:
        background_jobs._warn("GPU memory check failed; running GPU jobs one at a time", exc)
        return False
    free_mb = (load or {}).get("memory_free_mb")
    return free_mb is not None and free_mb >= GPU_PARALLEL_RESERVE_MB


def acquire(holder: str, description: str = None, *, job_id: str = None,
            check_external_load: bool = False) -> bool:
    """Claims a cross-process GPU slot for `holder`; True if taken. Shared by
    UI jobs and every CLI so all follow the same cap.

    The first holder needs only a free slot (and, with check_external_load,
    no other application loading the GPU). Joining running holders needs
    gpu_max_parallel > 1, fewer holders than that, enough free VRAM and the
    settle time (see GPU_PARALLEL_SETTLE_SECONDS). The utilization check is
    skipped when joining: Baihe's own running job is what loads the GPU
    then, and free VRAM already counts every application's use.

    May raise from the database; callers decide whether that queues/waits."""
    import background_jobs
    cap = background_jobs.get_gpu_max_parallel()
    if cap > 1:
        others = holder_count(exclude_holder=holder)
        if others >= cap:
            return False
        if others:
            if not _vram_room_for_another_gpu_job():
                return False
            return take(holder, description, max_holders=cap,
                        settle_seconds=GPU_PARALLEL_SETTLE_SECONDS, job_id=job_id)
    if check_external_load:
        try:
            if gpu_probe.external_gpu_is_busy():
                return False
        except Exception as exc:
            # Fails open on purpose: this check is optional (no nvidia-smi is
            # normal and returns False without raising), the slots remain.
            background_jobs._warn("external GPU load check failed; ignoring it", exc)
    return take(holder, description, job_id=job_id)


def release(holder: str, *, conn=None) -> None:
    """Frees `holder`'s slot. A no-op when it holds none (never took one, or
    went stale and was taken over), and never frees another process's row of
    the same name. With `conn`, runs on it and leaves the commit to the caller."""
    sql = "DELETE FROM gpu_lock WHERE holder = ? AND (owner_instance IS NULL OR owner_instance = ?)"
    if conn is not None:
        conn.execute(sql, (holder, job_store.INSTANCE_ID))
        return
    with contextlib.closing(db.get_conn()) as own:
        own.execute(sql, (holder, job_store.INSTANCE_ID))
        own.commit()


def heartbeat(holders) -> None:
    """Refreshes these holders' rows so a long, quiet holder doesn't look
    abandoned to another process before it is done."""
    holders = list(holders)
    if not holders:
        return
    marks = ",".join("?" * len(holders))
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(f"UPDATE gpu_lock SET heartbeat_at = ? WHERE holder IN ({marks}) "
                     "AND (owner_instance IS NULL OR owner_instance = ?)",
                     (time.time(), *holders, job_store.INSTANCE_ID))
        conn.commit()


def reap() -> int:
    """Deletes every row that no longer counts (_alive): an exited owner, an
    earlier server's row under this pid, or a stale heartbeat. Returns how
    many. The server runs it once at startup so the next GPU job does not
    queue behind a holder that is gone."""
    now = time.time()
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        dead = [r["id"] for r in _rows(conn) if not _alive(r, now)]
        for slot in dead:
            conn.execute("DELETE FROM gpu_lock WHERE id = ?", (slot,))
        conn.commit()
    return len(dead)


def clear(conn) -> None:
    """Empties the table on `conn` (a restored database: any row it holds is
    from another moment). The caller commits."""
    conn.execute("DELETE FROM gpu_lock")


def holder_count(exclude_holder: str = None) -> int:
    """How many live holders there are, not counting `exclude_holder`."""
    return sum(1 for r in _live_rows() if r["holder"] != exclude_holder)


def status():
    """(holder, description) of a live holder (the lowest slot), or
    (None, None) when none holds a slot."""
    rows = _live_rows()
    if not rows:
        return None, None
    return rows[0]["holder"], rows[0]["description"]


def _legacy_owner_is_other_live_process(job_id: str) -> bool:
    """For a `ui:` row with no owner columns: True when job_records says
    another process that still runs owns this job id."""
    record = db.get_job_record(job_id)
    pid = (record or {}).get("owner_pid")
    return pid is not None and pid != os.getpid() and _owner_alive(pid)


def previous_run_wait_message(now: float = None):
    """The queued-job message when the only live rows are server rows from
    before the owner columns existed whose job nobody here runs, else None.
    Rows with owner columns that still count are real holders. Never names
    the holder, its description or a path."""
    now = time.time() if now is None else now
    live = _live_rows(now)
    if not live or any(
            r["owner_pid"] is not None or not r["holder"].startswith(UI_PREFIX)
            or _legacy_owner_is_other_live_process(r["holder"][len(UI_PREFIX):])
            for r in live):
        return None
    remaining = GPU_LOCK_STALE_SECONDS - (now - max(r["heartbeat_at"] for r in live))
    return f"Waiting for a previous run to be released (up to {max(1, math.ceil(remaining / 60))} min)"
