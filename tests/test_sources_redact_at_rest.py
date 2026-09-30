"""Stored (not just displayed) source and job data carries no URL query or token."""

import background_jobs
import db
from services import sources_registry_service as reg
from sources import health, store

URL = "https://example.com/book/7?sig=SECRETSIG&t=1#frag"
ERR = "GET https://example.com/x?token=abc123secret failed"


def _raw_attempts():
    with store.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT url, data FROM access_attempts")]


def test_log_attempt_stores_no_query(isolated_db):
    store.log_attempt("demo", URL, {
        "lines": [f"fetched {URL}"], "reasons": ["challenge"],
        "handoff": {"tier": "http", "reason": "captcha", "url": URL}})
    rows = _raw_attempts()
    assert rows[0]["url"] == "https://example.com/book/7"
    for r in rows:
        assert "SECRETSIG" not in r["url"] + r["data"]
        assert "?" not in r["url"] + r["data"]
    got = store.recent_attempts("demo")[0]
    assert got["handoff"]["url"] == "https://example.com/book/7"
    assert got["handoff"]["reason"] == "captcha"


def test_read_path_still_works(isolated_db):
    name = sorted(reg._visible_classes())[0]
    store.log_attempt(name, URL, {"tier": "http", "handoff": {"reason": "x", "url": URL}})
    out = reg.list_attempts(name)
    assert out and out[0]["url"] == "https://example.com/book/7"
    assert "SECRETSIG" not in repr(out)


def test_health_and_mark_checked_redacted(isolated_db):
    health.record_failure("demo", "net", ERR)
    assert "abc123secret" not in health.get("demo")["last_error"]
    store.track_series("demo", "s1", "T")
    store.mark_checked("demo", "s1", error=ERR)
    rows = store.list_tracked_series()
    assert "abc123secret" not in repr(rows)


def test_job_record_mirror_strips_query(isolated_db):
    jid = "job-redact-1"
    with background_jobs._lock:
        background_jobs._jobs[jid] = {
            "status": "error", "progress": 0.5, "message": f"Fetching {URL}",
            "error": ERR + " Bearer abcdefghijklmnopqrstuvwxyz123456",
            "description": "d"}
        background_jobs._mirror_locked(jid)
    rec = db.get_job_record(jid)
    blob = f"{rec['message']} {rec['error']}"
    assert "SECRETSIG" not in blob and "abc123secret" not in blob and "?" not in blob
    assert "https://example.com/book/7" in blob
    with background_jobs._lock:
        background_jobs._jobs.pop(jid, None)
