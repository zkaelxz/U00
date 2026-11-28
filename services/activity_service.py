"""services/activity_service.py -- the activity log (what finished, when, how long) and the daily GPU hours chart, read from db's activity_log with each caller seeing only the jobs they may see."""

import time

import db
from services import ownership_service
from jobs.job_results import scrub_text

MAX_PAGE = 100
MAX_SCAN = 1000     # rows read per page: a member's own jobs are sparse among everyone's
DEFAULT_DAYS = 30
MAX_DAYS = 365


def _visible(principal, row, drama_rows) -> bool:
    return ownership_service.can_see_job(principal, row["job_id"], row["owner_user_id"], drama_rows=drama_rows)


def list_activity(principal=None, limit: int = 50, before_id: int | None = None) -> dict:
    """Newest first: {"items": [...], "next_before_id": id or None}. Never an owner id or a job id."""
    limit = max(1, min(int(limit), MAX_PAGE))
    rows = db.list_activity(MAX_SCAN, before_id)
    drama_rows = ownership_service.job_drama_ownership(principal, [r["job_id"] for r in rows])
    items = []
    for row in rows:
        if not _visible(principal, row, drama_rows):
            continue
        items.append({
            "id": row["id"], "label": scrub_text(row["label"]) or "A background job",
            "kind": _kind(row["job_id"]), "outcome": row["outcome"],
            "started_at": row["started_at"], "ended_at": row["ended_at"],
            "seconds": round(row["seconds"], 1), "gpu": bool(row["gpu"]),
            "mine": ownership_service.owns_job(principal, row["job_id"], row["owner_user_id"], drama_rows=drama_rows),
        })
        if len(items) == limit:
            return {"items": items, "next_before_id": row["id"]}
    # A full scan that found fewer visible rows than a page still has older rows behind it.
    return {"items": items, "next_before_id": rows[-1]["id"] if len(rows) == MAX_SCAN else None}


def _kind(job_id) -> str:
    from jobs import job_results
    return job_results.job_kind(job_id)


def gpu_daily(principal=None, days: int = DEFAULT_DAYS) -> dict:
    """GPU time per local calendar day for the last `days` days, every day listed (0 when idle)."""
    days = max(1, min(int(days), MAX_DAYS))
    since = time.time() - days * 86400
    totals = {}
    rows = db.gpu_rows_since(since)
    drama_rows = ownership_service.job_drama_ownership(principal, [r["job_id"] for r in rows])
    for row in rows:
        if _visible(principal, row, drama_rows):
            slot = totals.setdefault(row["day"], [0.0, 0])
            slot[0] += row["seconds"]
            slot[1] += 1
    out = []
    for back in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(time.time() - back * 86400))
        seconds, jobs = totals.get(day, [0.0, 0])
        out.append({"day": day, "gpu_seconds": round(seconds, 1), "jobs": jobs})
    return {"days": out, "total_gpu_seconds": round(sum(d["gpu_seconds"] for d in out), 1)}
