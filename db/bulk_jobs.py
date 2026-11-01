"""Bulk (batch-API / off-peak) translation jobs: the job rows, the lines each
one submitted and the results applied back by line id."""

import contextlib
from lib import clock
import json

import db


BULK_PENDING_STATUSES = ("submitted", "scheduled", "auth_error", "needs_resubmit")


def create_bulk_job(drama_id: int, engine: str, model: str, status: str, lines,
                    provider_batch_id: str | None = None, scheduled_for: str | None = None,
                    translate_args: dict | None = None, kind: str = "translate",
                    stage: str | None = None, pipeline_id: str | None = None) -> int:
    """lines: [(line_id, request_key, zh_hash, en_at_submit), ...], or,
    for a non-translate kind that needs something to compare against once
    results come back (see the `bulk_job_lines` table's own comment),
    [(line_id, request_key, zh_hash, en_at_submit, state_at_submit), ...]
    -- a plain 4-tuple still works, with state_at_submit left NULL.

    kind/stage/pipeline_id: see the `bulk_jobs` table's own comment
    Every existing call site left these at their defaults, so
    an old job is still exactly what it always was: a translate job."""
    now = clock.utcnow().isoformat()
    conn = db.get_conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute("""
            INSERT INTO bulk_jobs (drama_id, engine, model, provider_batch_id, status,
                                   scheduled_for, translate_args, submitted_at, updated_at,
                                   kind, stage, pipeline_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, engine, model, provider_batch_id, status, scheduled_for,
              json.dumps(translate_args, ensure_ascii=False) if translate_args is not None else None,
              now, now, kind, stage, pipeline_id))
        job_id = db.inserted_id(cur)
        rows = []
        for row in lines:
            if len(row) == 4:
                lid, key, h, en = row
                state = None
            else:
                lid, key, h, en, state = row
            rows.append((job_id, lid, key, h, en, state))
        conn.executemany("""
            INSERT INTO bulk_job_lines (bulk_job_id, line_id, request_key, zh_hash, en_at_submit,
                                        state_at_submit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return job_id


def _bulk_job_row(r) -> dict:
    d = dict(r)
    d["translate_args"] = json.loads(d["translate_args"]) if d.get("translate_args") else None
    d["result_summary"] = json.loads(d["result_summary"]) if d.get("result_summary") else None
    return d


def get_bulk_job(bulk_job_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        r = conn.execute("SELECT * FROM bulk_jobs WHERE id = ?", (bulk_job_id,)).fetchone()
    return _bulk_job_row(r) if r else None


def list_bulk_jobs(drama_id: int | None = None, statuses=None, pipeline_id: str | None = None) -> list:
    """Newest first. statuses: optional iterable to filter on.
    pipeline_id: every stage row of one Reflect run shares one,
    so the Bulk jobs panel can pull all three (whichever exist so far) to
    show them as a single pipeline."""
    sql, params = "SELECT * FROM bulk_jobs WHERE 1=1", []
    if drama_id is not None:
        sql += " AND drama_id = ?"
        params.append(drama_id)
    if statuses:
        statuses = list(statuses)
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    if pipeline_id is not None:
        sql += " AND pipeline_id = ?"
        params.append(pipeline_id)
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    return [_bulk_job_row(r) for r in rows]


def update_bulk_job(bulk_job_id: int, **fields):
    allowed = {"status", "provider_batch_id", "scheduled_for", "last_error", "result_summary"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "result_summary" in fields and fields["result_summary"] is not None:
        fields["result_summary"] = json.dumps(fields["result_summary"])
    fields["updated_at"] = clock.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(f"UPDATE bulk_jobs SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                     list(fields.values()) + [bulk_job_id])
        conn.commit()


def set_bulk_job_status_if(bulk_job_id: int, expected, new: str) -> bool:
    """Changes the status only while it is one of `expected`, in one
    statement, so two appliers of the same paid results can't both win."""
    expected = list(expected)
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            f"UPDATE bulk_jobs SET status = ?, updated_at = ? WHERE id = ? "
            f"AND status IN ({','.join('?' * len(expected))})",
            [new, clock.utcnow().isoformat(), bulk_job_id] + expected)
        conn.commit()
        return cur.rowcount > 0


def count_bulk_job_lines(bulk_job_id: int) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) FROM bulk_job_lines WHERE bulk_job_id = ?",
                            (bulk_job_id,)).fetchone()[0]


def list_bulk_job_lines(bulk_job_id: int) -> list:
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM bulk_job_lines WHERE bulk_job_id = ? ORDER BY line_id",
                            (bulk_job_id,)).fetchall()
    return [dict(r) for r in rows]


def set_bulk_job_line_result_texts(bulk_job_id: int, result_by_line_id: dict):
    """Records a Reflect stage's own raw per-line output (a
    faithfulness draft or a reflection critique) against this job's own
    line rows -- read back by the NEXT stage's prompt builder, never
    applied to a line directly (see the `bulk_job_lines` table's own
    comment). A line id not in result_by_line_id is left NULL, meaning
    that line either dropped out at this stage (source changed/deleted)
    or the model simply returned nothing for it."""
    if not result_by_line_id:
        return
    with contextlib.closing(db.get_conn()) as conn:
        conn.executemany(
            "UPDATE bulk_job_lines SET result_text = ? WHERE bulk_job_id = ? AND line_id = ?",
            [(text, bulk_job_id, lid) for lid, text in result_by_line_id.items()])
        conn.commit()
