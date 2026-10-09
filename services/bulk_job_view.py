"""The public view of a bulk_jobs row (services/translate_run_service)."""
import db
import translate_engines

CANCELLABLE = ("submitting",) + db.BULK_PENDING_STATUSES


def bulk_entry(job: dict) -> dict:
    """Public view of one bulk_jobs row: no prompts, no translate_args, no
    provider batch id, no raw error text beyond the redacted last_error."""
    summary = job.get("result_summary")
    err = job.get("last_error")
    return {
        "bulk_job_id": job["id"], "engine": job["engine"], "model": job.get("model"),
        "kind": job.get("kind") or "translate", "stage": job.get("stage"),
        "pipeline_id": job.get("pipeline_id"), "status": job["status"],
        "pending": job["status"] in db.BULK_PENDING_STATUSES + ("submitting", "running"),
        "cancellable": job["status"] in CANCELLABLE,
        "line_count": db.count_bulk_job_lines(job["id"]),
        "scheduled_for": job.get("scheduled_for"),
        "result_summary": summary if isinstance(summary, dict) else None,
        "last_error": translate_engines.redact_secrets(err) if err else None,
        "submitted_at": job.get("submitted_at"), "updated_at": job.get("updated_at"),
    }
