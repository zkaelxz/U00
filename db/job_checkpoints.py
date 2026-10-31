"""The one write of a job checkpoint unit, shared by the checkpoint service and
the process-job watcher (jobs/ sits below services/, so it cannot call the service)."""

import contextlib
import json
import time

import db


def record_checkpoint_unit(scope: str, unit_id, payload) -> None:
    with contextlib.closing(db.get_conn()) as conn:
        with conn:
            # Units are written in order, so any later one is from an earlier attempt that
            # decoded a different cut; keeping it would leave a gap in the resumed text.
            conn.execute("DELETE FROM job_checkpoints WHERE scope = ? AND CAST(unit_id AS INTEGER) > ?",
                         (scope, int(unit_id)))
            conn.execute(
                "INSERT OR REPLACE INTO job_checkpoints (scope, unit_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (scope, str(unit_id), json.dumps(payload, ensure_ascii=False), time.time()))
