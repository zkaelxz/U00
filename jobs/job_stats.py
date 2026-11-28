"""Owns the one app.log stats line written when a job ends: numbers and fixed words only."""

import atexit
import datetime
import re
import statistics
import threading
import time

from lib.error_kinds import error_kind

# Stage names are fixed phrases in job code. One that carries a digit, path or other
# punctuation was built from data (an id, a title, a file), so it is logged as "other".
_FIXED_PHRASE = re.compile(r"[A-Za-z][A-Za-z ,()-]{0,39}")
_MAX_STAGES = 8


def _stage_label(name) -> str:
    text = " ".join(str(name or "").split())
    return text if _FIXED_PHRASE.fullmatch(text) else "other"


def _stage_seconds(job_id, token) -> dict:
    import job_timing
    runs = job_timing.list_runs(job_id, 1)
    if not runs or runs[0]["run_started_at"] != token:
        return {}
    seconds = {}
    for stage in runs[0]["stages"]:
        label = _stage_label(stage["stage"])
        seconds[label] = round(seconds.get(label, 0.0) + stage["duration_seconds"], 1)
    return dict(sorted(seconds.items(), key=lambda kv: -kv[1])[:_MAX_STAGES])


def _error_counts(job) -> dict:
    texts = []
    result = job.get("result")
    errors = result.get("errors") if isinstance(result, dict) else None
    if isinstance(errors, dict):
        texts.extend(errors.values())
    elif isinstance(errors, (list, tuple)):
        texts.extend(errors)
    if job.get("error"):
        texts.append(job["error"])
    counts = {}
    for text in texts:
        kind = error_kind(text)
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def line(job_id, kind, job, token, outcome, gpu_touching) -> str:
    parts = [f"kind={kind}", f"status={outcome}",
             f"total_s={max(0.0, (job.get('finished_at') or time.time()) - token):.1f}",
             f"queue_wait_s={float(job.get('queue_wait_s') or 0.0):.1f}", f"gpu={int(bool(gpu_touching))}"]
    stages = _stage_seconds(job_id, token)
    if stages:
        parts.append("stages=" + ", ".join(f"{name} {secs}" for name, secs in stages.items()))
    errors = _error_counts(job)
    if errors:
        parts.append("errors=" + ", ".join(f"{name} {n}" for name, n in sorted(errors.items())))
    return "Job stats: " + " ".join(parts)


# The running day's figures, kept in memory: a restart starts the day's tally again, which the
# per-job lines still cover.
_day_lock = threading.Lock()
_day = {"date": None, "jobs": []}


def _summary_line(date, jobs) -> str:
    by_kind = {}
    for kind, status, total, wait, errors in jobs:
        by_kind.setdefault(kind, []).append((status, total, wait, errors))
    parts = []
    for kind, rows in sorted(by_kind.items()):
        statuses = {}
        for status, *_ in rows:
            statuses[status] = statuses.get(status, 0) + 1
        parts.append(f"{kind} {len(rows)} ({', '.join(f'{k} {n}' for k, n in sorted(statuses.items()))}) "
                     f"median {statistics.median(r[1] for r in rows):.0f} s")
    waits = [j[3] for j in jobs]
    errors = {}
    for *_, counts in jobs:
        for kind, n in counts.items():
            errors[kind] = errors.get(kind, 0) + n
    return (f"Daily job summary {date}: {len(jobs)} jobs; " + "; ".join(parts)
            + f"; median queue wait {statistics.median(waits):.1f} s; errors: "
            + (", ".join(f"{k} {n}" for k, n in sorted(errors.items())) or "none"))


def _flush_day_locked() -> None:
    if _day["jobs"]:
        import applog
        applog.get_logger().info(_summary_line(_day["date"], _day["jobs"]))
    _day["jobs"] = []


def _record(kind, job, token, outcome, today) -> None:
    with _day_lock:
        if _day["date"] != today:
            _flush_day_locked()
            _day["date"] = today
        _day["jobs"].append((kind, outcome,
                             max(0.0, (job.get("finished_at") or time.time()) - token),
                             float(job.get("queue_wait_s") or 0.0), _error_counts(job)))


def flush_day() -> None:
    with _day_lock:
        try:
            _flush_day_locked()
        except Exception:
            pass


atexit.register(flush_day)


def log_job_end(job_id, kind, job, token, outcome, gpu_touching) -> None:
    """`kind` is the job's kind word (services.jobs_service.job_kind, passed in because jobs/
    may not import services). Never raises: a logging slip must not turn a finished job into a failed one."""
    try:
        import applog
        applog.get_logger().info(line(job_id, kind, job, token, outcome, gpu_touching))
        _record(kind, job, token, outcome, datetime.date.today().isoformat())
    except Exception:
        pass
