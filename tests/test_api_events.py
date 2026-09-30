"""SSE push (GET /api/events, services/event_stream_service.py): payloads,
visibility per principal, caps, resync, heartbeat and auth re-checks.

A stream is endless in production; here MAX_STREAM_SECONDS is small so
TestClient (which reads the whole body) gets it back. Changes are fired
from a helper thread once the stream is registered."""

import json
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, event_stream_service as ev, jobs_service, live_service
from services import notification_service as ns

LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ev, "MAX_STREAM_SECONDS", 1.2)
    monkeypatch.setattr(ev, "HEARTBEAT_SECONDS", 0.3)
    monkeypatch.setattr(ev, "JOB_SWEEP_SECONDS", 0.2)
    monkeypatch.setattr(ev, "MIN_BATCH_SECONDS", 0.01)
    monkeypatch.setattr(ns, "_recent", ns.collections.deque(maxlen=ns.RECENT_KEEP))
    monkeypatch.setattr(ns, "_schedule_flush", lambda: None)
    yield
    assert ev.open_count() == 0, "a stream kept its slot after it ended"


def _parse(text):
    """[(event, data)] plus ("ping", None) for heartbeat comments."""
    out = []
    for block in text.split("\n\n"):
        if block.startswith(": ping"):
            out.append(("ping", None))
            continue
        name = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name:
            out.append((name, data))
    return out


def _after_open(fn, count=1):
    """Runs fn in a thread once `count` streams are registered."""
    def run():
        deadline = time.time() + 5
        while ev.open_count() < count and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        fn()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _job_row(job_id, status="running", owner=None, progress=0.1, message="working"):
    db.save_job_record(job_id, status=status, progress=progress, message=message, error=None,
                       description=f"job {job_id}", gpu_touching=False, started_at=time.time(),
                       finished_at=None, result_json=None, owner_user_id=owner)


def _local(app):
    return TestClient(app, base_url=LOCAL, client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _session(email, *perms, admin=False):
    if admin:
        u = auth_service.grant_admin_local(email)
    else:
        u = auth_service.add_user(email)
        for p in perms:
            auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    s["user_id"] = u["id"]
    return s


def _cookie(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}"}


# --- auth off -----------------------------------------------------------------

def test_headers_ready_and_heartbeat(isolated_db):
    r = _local(create_app(ApiSettings(auth_mode="off"))).get("/api/events")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    events = _parse(r.text)
    assert events[0] == ("ready", {"topics": ["jobs", "notifications", "live"]})
    assert ("ping", None) in events


def test_job_progress_and_notification_pushed(isolated_db):
    def fire():
        _job_row("translate_5", progress=0.25, message="batch 1")
        background_jobs._emit_change("translate_5")
        time.sleep(0.1)
        _job_row("translate_5", status="done", progress=1.0, message="batch 2")
        background_jobs._emit_change("translate_5")
        ns.notify_job_finished("Translation", "done", job_id="translate_5")
    _after_open(fire)
    r = _local(create_app(ApiSettings(auth_mode="off"))).get("/api/events")
    events = _parse(r.text)
    jobs = [d for n, d in events if n == "job"]
    assert [j["status"] for j in jobs] == ["running", "done"]
    assert jobs[0]["progress"] == 0.25 and jobs[1]["outcome"] == "ok"
    # Exactly the GET route's shape: no owner or internal columns.
    assert set(jobs[0]) == set(jobs_service_fields())
    notes = [d for n, d in events if n == "notifications"]
    assert notes and notes[-1]["items"][0]["text"] == "Finished: Translation"
    assert set(notes[-1]["items"][0]) == {"id", "at", "kind", "text"}


def jobs_service_fields():
    from api.schemas import JobRecord
    return JobRecord.model_fields.keys()


def test_in_process_progress_overlay(isolated_db, monkeypatch):
    """job_records is written on status changes only; a running job's
    progress comes from background_jobs' memory (GET and events alike)."""
    _job_row("dub_3", progress=0.0, message="")
    monkeypatch.setitem(background_jobs._jobs, "dub_3",
                        {"status": "running", "progress": 0.6, "message": "line 6 of 10"})
    job = jobs_service.get_job("dub_3")
    assert job["progress"] == 0.6 and job["message"] == "line 6 of 10"
    r = _local(create_app(ApiSettings(auth_mode="off"))).get("/api/jobs/dub_3")
    assert r.json()["progress"] == 0.6


def test_job_from_another_process_found_by_the_sweep(isolated_db):
    """No hook fires for a CLI/Streamlit job; the job_records sweep sees it."""
    _after_open(lambda: (time.sleep(0.3), _job_row("transcribe_9", progress=0.4)))
    r = _local(create_app(ApiSettings(auth_mode="off"))).get("/api/events?topics=jobs")
    jobs = [d for n, d in _parse(r.text) if n == "job"]
    assert jobs and jobs[0]["job_id"] == "transcribe_9"
    assert len(jobs) == 1  # the same state is never sent twice


def test_topics_filter_and_bad_topics(isolated_db):
    def fire():
        _job_row("translate_1")
        background_jobs._emit_change("translate_1")
    _after_open(fire)
    c = _local(create_app(ApiSettings(auth_mode="off")))
    events = _parse(c.get("/api/events?topics=notifications").text)
    assert events[0] == ("ready", {"topics": ["notifications"]})
    assert not [n for n, _ in events if n == "job"]
    assert c.get("/api/events?topics=jobs,secrets").status_code == 422


def test_listener_failure_never_breaks_a_job(isolated_db):
    def boom(_job_id):
        raise RuntimeError("listener broke")
    background_jobs.add_change_listener(boom)
    try:
        done = threading.Event()
        assert background_jobs.start_job("evtest_1", lambda: (
            background_jobs.update_progress("evtest_1", 0.5, "half"), done.set()))
        assert done.wait(5)
        for _ in range(100):
            status = (background_jobs.get_status("evtest_1") or {}).get("status")
            if status == "done":
                break
            time.sleep(0.02)
        assert status == "done"
    finally:
        background_jobs.remove_change_listener(boom)
        background_jobs.clear_job("evtest_1")


def test_notification_listener_failure_is_ignored(isolated_db):
    def boom():
        raise RuntimeError("listener broke")
    ns.add_listener(boom)
    try:
        ns.notify_job_finished("Translation", "done", job_id="translate_2")
        assert ns.list_recent(None)[0]["text"] == "Finished: Translation"
    finally:
        ns.remove_listener(boom)


# --- bounded resources --------------------------------------------------------

def test_overflow_becomes_one_resync():
    sub = ev.Subscription("local", ev.TOPICS)
    for i in range(ev.MAX_PENDING + 5):
        sub.mark("job", f"job_{i}")
    batch = sub.drain()
    assert batch["overflow"] and batch["jobs"] == []
    assert ev.build_events(sub, batch, None) == [("resync", {"topics": list(ev.TOPICS)})]
    assert sub.drain() == {"overflow": False, "jobs": [], "live": [], "notifications": False}


def test_clear_all_jobs_is_a_resync():
    sub = ev.Subscription("local", ("jobs",))
    sub.mark("job", None)
    assert sub.drain()["overflow"] is True


def test_per_principal_and_global_caps(isolated_db, monkeypatch):
    app = create_app(ApiSettings(auth_mode="off"))
    held = [ev.open_subscription(api_auth.local_owner_principal(), ev.TOPICS)
            for _ in range(ev.MAX_STREAMS_PER_PRINCIPAL)]
    try:
        r = _local(app).get("/api/events")
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
        # Another user still gets one...
        other = ev.open_subscription({"user_id": 99}, ev.TOPICS)
        ev.close_subscription(other)
        # ...until the process-wide cap.
        monkeypatch.setattr(ev, "MAX_STREAMS", len(held))
        with pytest.raises(ev.TooManyStreams):
            ev.open_subscription({"user_id": 99}, ev.TOPICS)
    finally:
        for s in held:
            ev.close_subscription(s)


def test_slot_freed_when_stream_ends(isolated_db):
    c = _local(create_app(ApiSettings(auth_mode="off")))
    for _ in range(ev.MAX_STREAMS_PER_PRINCIPAL + 1):
        assert c.get("/api/events").status_code == 200
    assert ev.open_count() == 0


def test_job_gone_only_for_a_job_this_stream_showed(isolated_db):
    _job_row("translate_4")
    sub = ev.Subscription("local", ev.TOPICS)
    assert ev.build_events(sub, {"jobs": ["translate_4", "never_seen"]}, None) == [
        ("job", jobs_service.get_job("translate_4"))]
    db.delete_job_record("translate_4")
    assert ev.build_events(sub, {"jobs": ["translate_4", "never_seen"]}, None) == [
        ("job_gone", {"job_id": "translate_4"})]


def test_live_event_carries_status_not_cues(isolated_db, monkeypatch):
    sid = "live_" + "a" * 32
    monkeypatch.setitem(live_service._sessions, sid, {"dir": None, "engine": "google",
                                                      "owner_user_id": 7})
    monkeypatch.setitem(background_jobs._jobs, sid, {
        "status": "running", "progress": 0.0, "message": "Capturing",
        "result": [{"start": 0, "end": 2, "text": "你好", "translated": "Hello"}]})
    sub = ev.Subscription("local", ("live",))
    [(name, data)] = ev.build_events(sub, {"live": [sid]}, None)
    assert name == "live" and data["cues"] == [] and data["next_index"] == 1
    assert data["status"] == "running"
    stranger = {"user_id": 8, "is_admin": False, "permissions": ["library.read"]}
    assert ev.build_events(ev.Subscription("user:8", ("live",)), {"live": [sid]},
                           stranger) == []


# --- auth on ------------------------------------------------------------------

@pytest.fixture
def world(isolated_db):
    a = _session("a@example.com", "library.read")
    b = _session("b@example.com", "library.read")
    private = db.create_drama(title_en="A private", source_language="zh",
                              owner_user_id=a["user_id"], is_private=1)
    shared = db.create_drama(title_en="A shared", source_language="zh",
                             owner_user_id=a["user_id"], is_private=0)
    return {"a": a, "b": b, "private": private, "shared": shared}


def _remote(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)


def test_auth_on_needs_session_and_library_read(isolated_db):
    app = create_app(ApiSettings(auth_mode="on"))
    assert _remote(app).get("/api/events").status_code == 401
    nobody = _session("n@example.com")
    auth_service.revoke_permission(nobody["user_id"], "library.read")
    assert _remote(app).get("/api/events", headers=_cookie(nobody)).status_code == 403
    assert ev.open_count() == 0


def test_other_users_job_and_private_drama_never_streamed(world):
    a_id = world["a"]["user_id"]
    hidden = [f"translate_{world['private']}", "library_backup_a"]
    shown = f"translate_{world['shared']}"

    def fire():
        _job_row(hidden[0], owner=a_id)
        _job_row(hidden[1], owner=a_id)
        _job_row(shown, owner=a_id)
        for j in hidden + [shown]:
            background_jobs._emit_change(j)
        ns.notify_job_finished("Translation (drama #%d)" % world["private"], "done",
                               job_id=hidden[0], owner_user_id=a_id)
        ns.notify_job_finished("Library backup", "done", job_id=hidden[1], owner_user_id=a_id)
    _after_open(fire)
    app = create_app(ApiSettings(auth_mode="on"))
    r = _remote(app).get("/api/events", headers=_cookie(world["b"]))
    assert r.status_code == 200
    events = _parse(r.text)
    assert [d["job_id"] for n, d in events if n == "job"] == [shown]
    for name, data in events:
        if name == "notifications":
            assert data["items"] == []  # both events are A's
    for secret in hidden + ["A private", "Library backup"]:
        assert secret not in r.text


def test_owner_sees_their_own_private_job(world):
    job = f"translate_{world['private']}"

    def fire():
        _job_row(job, owner=world["a"]["user_id"])
        background_jobs._emit_change(job)
    _after_open(fire)
    r = _remote(create_app(ApiSettings(auth_mode="on"))).get("/api/events",
                                                             headers=_cookie(world["a"]))
    assert [d["job_id"] for n, d in _parse(r.text) if n == "job"] == [job]


def test_revoked_session_ends_the_stream(world, monkeypatch):
    monkeypatch.setattr(ev, "MAX_STREAM_SECONDS", 5.0)
    job = f"translate_{world['shared']}"

    def fire():
        auth_service.revoke_session(world["b"]["session_id"])
        _job_row(job)
        background_jobs._emit_change(job)
    _after_open(fire)
    started = time.time()
    r = _remote(create_app(ApiSettings(auth_mode="on"))).get("/api/events",
                                                             headers=_cookie(world["b"]))
    assert time.time() - started < 3
    assert [n for n, _ in _parse(r.text) if n == "job"] == []


def test_permission_revoked_mid_stream_ends_it(world, monkeypatch):
    monkeypatch.setattr(ev, "MAX_STREAM_SECONDS", 5.0)
    _after_open(lambda: auth_service.revoke_permission(world["b"]["user_id"], "library.read"))
    started = time.time()
    r = _remote(create_app(ApiSettings(auth_mode="on"))).get("/api/events",
                                                             headers=_cookie(world["b"]))
    assert r.status_code == 200 and time.time() - started < 3


def test_stream_rechecks_do_not_keep_an_idle_session_alive(world, monkeypatch):
    """The per-heartbeat re-check is not activity (security review LOW-1)."""
    touched = []
    real = db.auth_touch_session
    monkeypatch.setattr(db, "auth_touch_session", lambda *a, **k: (touched.append(a), real(*a, **k)))
    monkeypatch.setattr(auth_service, "_TOUCH_INTERVAL_SECONDS", 0)
    r = _remote(create_app(ApiSettings(auth_mode="on"))).get("/api/events",
                                                             headers=_cookie(world["b"]))
    assert _parse(r.text).count(("ping", None)) >= 2   # re-checked at each
    assert len(touched) <= 2  # only the request's own checks (gate + route)


def test_one_sweep_per_process(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "list_job_record_fingerprints", lambda: calls.append(1) or {})
    monkeypatch.setattr(ev, "_sweep_snapshot", None)
    ev.sweep_jobs(now=100.0)
    ev.sweep_jobs(now=100.1)
    ev.sweep_jobs(now=100.0 + ev.JOB_SWEEP_SECONDS + 0.1)
    assert len(calls) == 2
