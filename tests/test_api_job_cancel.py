"""Migration Slice 22: cross-process job cancel. Fully mocked."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})  # as the React client sends


def _own(job_id):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {"status": "running", "cancel_requested": False}


def _disown(job_id):
    with background_jobs._lock:
        background_jobs._jobs.pop(job_id, None)
    background_jobs._last_db_cancel_check.clear()


def test_unknown_404(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_finished_409(client):
    db.save_job_record("j1", "done")
    assert client.post("/api/jobs/j1/cancel").status_code == 409


def test_cross_process_cancel_sets_flag(client):
    db.save_job_record("j2", "running")
    r = client.post("/api/jobs/j2/cancel")
    assert r.status_code == 200
    assert r.json() == {"job_id": "j2", "cancel_requested": True, "status": "running"}
    assert db.is_job_record_cancel_requested("j2")
    db.save_job_record("j2", "done")
    assert not db.is_job_record_cancel_requested("j2")


def test_owner_notices_db_flag_throttled(isolated_db, monkeypatch):
    _own("j3")
    calls = []
    real = db.is_job_record_cancel_requested
    monkeypatch.setattr(db, "is_job_record_cancel_requested",
                        lambda j: calls.append(j) or real(j))
    db.save_job_record("j3", "running")
    try:
        for _ in range(20):
            assert not background_jobs.is_cancel_requested("j3")
        assert len(calls) == 1
        db.request_job_record_cancel("j3")
        assert not background_jobs.is_cancel_requested("j3")  # throttled
        background_jobs._last_db_cancel_check.clear()
        assert background_jobs.is_cancel_requested("j3")
        assert background_jobs._jobs["j3"]["cancel_requested"] is True
    finally:
        _disown("j3")


def test_in_process_job_cancelled_immediately(client):
    _own("j4")
    db.save_job_record("j4", "running")
    try:
        assert client.post("/api/jobs/j4/cancel").status_code == 200
        assert background_jobs.is_cancel_requested("j4")
    finally:
        _disown("j4")


def test_stale_orphan_record_is_closed_on_cancel(client):
    """B-04: no live owner and an old updated_at -> record becomes cancelled."""
    import time
    db.save_job_record("orphan", "running", progress=0.4)
    with db.get_conn() as conn:
        conn.execute("UPDATE job_records SET updated_at = ? WHERE job_id = 'orphan'",
                     (time.time() - 3600,))
    r = client.post("/api/jobs/orphan/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert db.get_job_record("orphan")["status"] == "cancelled"
    assert client.post("/api/jobs/orphan/cancel").status_code == 409



def _age(job_id, seconds):
    import time
    with db.get_conn() as conn:
        conn.execute("UPDATE job_records SET updated_at = ? WHERE job_id = ?",
                     (time.time() - seconds, job_id))
        conn.commit()


def test_fresh_unowned_record_is_not_closed(client):
    """B-04: a record another process heartbeated recently stays running;
    only the cancel flag is set."""
    db.save_job_record("fresh", "running")
    _age("fresh", 60)
    r = client.post("/api/jobs/fresh/cancel")
    assert r.json()["status"] == "running"
    rec = db.get_job_record("fresh")
    assert rec["status"] == "running" and rec["cancel_requested"] == 1


def test_cancel_request_does_not_refresh_staleness(client):
    """The flag write must not bump updated_at, or a second cancel on an
    orphan could never close it."""
    db.save_job_record("orphan2", "running")
    _age("orphan2", 3600)
    before = db.get_job_record("orphan2")["updated_at"]
    db.request_job_record_cancel("orphan2")
    assert db.get_job_record("orphan2")["updated_at"] == before


def test_stale_close_loses_to_a_late_heartbeat_or_done(isolated_db):
    """The close is one conditional UPDATE: a heartbeat or a terminal
    write from the owner in between wins."""
    import time
    db.save_job_record("race", "running")
    _age("race", 3600)
    cutoff = time.time() - 900
    db.touch_job_records(["race"])                 # owner heartbeats in between
    assert not db.close_stale_job_record("race", cutoff)
    assert db.get_job_record("race")["status"] == "running"
    db.save_job_record("race", "done")
    _age("race", 3600)
    assert not db.close_stale_job_record("race", cutoff)
    assert db.get_job_record("race")["status"] == "done"


def test_heartbeat_touches_only_live_owned_jobs(isolated_db):
    _own("hb")
    db.save_job_record("hb", "running")
    db.save_job_record("other", "running")
    _age("hb", 3600)
    _age("other", 3600)
    try:
        background_jobs._heartbeat_once()
        import time
        assert time.time() - db.get_job_record("hb")["updated_at"] < 60
        assert time.time() - db.get_job_record("other")["updated_at"] > 3000
    finally:
        _disown("hb")


_OWNER_SCRIPT = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
import db, background_jobs as bg
db.configure_library_dir(sys.argv[1])
bg.HEARTBEAT_INTERVAL = 0.2
bg._DB_CANCEL_CHECK_INTERVAL = 0.1

def work():
    t0 = time.time()
    while time.time() - t0 < 30:
        if bg.is_cancel_requested("live"):
            raise bg.JobCancelled("live")
        time.sleep(0.05)

bg.start_job("live", work)
print("started", flush=True)
while bg.get_status("live")["status"] == "running":
    time.sleep(0.05)
print(bg.get_status("live")["status"], flush=True)
"""


def test_live_record_owned_by_another_process_is_not_closed(client, monkeypatch):
    """B-04 (HIGH): a job alive in another process, older than the stale
    threshold but heartbeating, gets the cancel flag and ends cancelled
    by its owner -- the API never closes it out from under it."""
    import os
    import subprocess
    import sys
    import time
    from services import jobs_service
    monkeypatch.setattr(jobs_service, "STALE_JOB_SECONDS", 1.0)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen([sys.executable, "-c", _OWNER_SCRIPT, db.LIBRARY_DIR, repo],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "started"
        time.sleep(2.0)       # past STALE_JOB_SECONDS; only the heartbeat keeps it fresh
        r = client.post("/api/jobs/live/cancel")
        assert r.status_code == 200 and r.json()["status"] == "running"
        out, err = proc.communicate(timeout=20)
        assert out.strip() == "cancelled", err
        assert db.get_job_record("live")["status"] == "cancelled"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_job_record_says_when_a_running_record_is_stale(client):
    """The record's `stale` flag uses the server's clock: a running record
    with no heartbeat for STALE_JOB_SECONDS reads stale (a viewer's own
    clock is never consulted); a fresh one, an in-process one and a
    finished one do not."""
    db.save_job_record("dead", "running")
    _age("dead", 3600)
    db.save_job_record("alive", "running")
    _age("alive", 60)
    db.save_job_record("mine", "running")
    _age("mine", 3600)
    _own("mine")
    db.save_job_record("finished", "done")
    _age("finished", 3600)
    try:
        assert client.get("/api/jobs/dead").json()["stale"] is True
        assert client.get("/api/jobs/alive").json()["stale"] is False
        assert client.get("/api/jobs/mine").json()["stale"] is False
        assert client.get("/api/jobs/finished").json()["stale"] is False
        # listing sweeps dead owners' records first, so "dead" is closed
        # (not merely stale) by the time it is listed
        items = {j["job_id"]: j for j in client.get("/api/jobs").json()["items"]}
        assert items["dead"]["status"] == "cancelled"
        assert items["alive"]["stale"] is False
    finally:
        _disown("mine")


def test_listing_jobs_sweeps_dead_owners_records(client):
    """B-04 leftover: a dead owner's record is closed without anyone
    cancelling it -- listing jobs sweeps it; fresh and in-process ones stay."""
    db.save_job_record("dead", "running")
    _age("dead", 3600)
    db.save_job_record("alive", "running")
    _age("alive", 60)
    db.save_job_record("mine", "running")
    _age("mine", 3600)
    _own("mine")
    try:
        jobs = {j["job_id"]: j["status"] for j in client.get("/api/jobs").json()["items"]}
    finally:
        _disown("mine")
    assert jobs["dead"] == "cancelled"
    assert jobs["alive"] == "running" and jobs["mine"] == "running"


def test_api_startup_sweeps_dead_owners_records(isolated_db, monkeypatch):
    import api.background as bg
    monkeypatch.setattr(bg, "start_background_services", lambda: {})
    monkeypatch.setattr(bg, "start_gpu_queue_poller", lambda: None)
    monkeypatch.setattr(bg, "stop_gpu_queue_poller", lambda: None)
    db.save_job_record("dead_at_start", "running")
    _age("dead_at_start", 3600)
    with TestClient(create_app(ApiSettings(background_services=True))):
        pass
    assert db.get_job_record("dead_at_start")["status"] == "cancelled"


def test_one_staleness_cutoff_everywhere():
    from services import drama_service, jobs_service, novel_files_service
    assert (jobs_service.STALE_JOB_SECONDS == drama_service._STALE_JOB_RECORD_SECONDS
            == novel_files_service._STALE_JOB_RECORD_SECONDS
            == background_jobs.STALE_JOB_SECONDS)


def test_heartbeat_write_failure_is_logged_redacted(isolated_db, monkeypatch):
    """A heartbeat that can't be written is logged (secrets stripped), not
    silently dropped; the job itself is unaffected."""
    import applog
    logged = []
    monkeypatch.setattr(applog, "get_logger",
                        lambda: type("L", (), {"warning": lambda self, m, **k: logged.append(m)})())

    def boom(ids):
        raise RuntimeError("db locked sk-ant-SECRET1234567890abcdef")
    monkeypatch.setattr(db, "touch_job_records", boom)
    _own("hb_fail")
    try:
        background_jobs._heartbeat_once()
    finally:
        _disown("hb_fail")
    assert logged and "heartbeat" in logged[0]
    assert "SECRET1234567890" not in logged[0]


def test_sweep_skips_a_job_live_in_this_process(isolated_db):
    """Even with a missed heartbeat (stale row), an in-process job is never
    closed by this process's sweep."""
    from services import jobs_service
    db.save_job_record("live_stale", "running")
    _age("live_stale", 3600)
    _own("live_stale")
    try:
        assert jobs_service.sweep_stale_job_records() == 0
    finally:
        _disown("live_stale")
    assert db.get_job_record("live_stale")["status"] == "running"


def _dead_pid():
    """The pid of a process that has exited (and been reaped)."""
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


def test_record_of_a_dead_owner_is_closed_at_startup_without_waiting(isolated_db, monkeypatch):
    """Owner report: after Baihe was closed and started again, the
    transcription still showed Running / Starting... The owner process is
    gone, so the record is closed at startup even though its last heartbeat
    is recent, with a message that says why."""
    import api.background as bg
    monkeypatch.setattr(bg, "start_background_services", lambda: {})
    monkeypatch.setattr(bg, "start_gpu_queue_poller", lambda: None)
    monkeypatch.setattr(bg, "stop_gpu_queue_poller", lambda: None)
    db.save_job_record("died_recently", "running", message="Starting...",
                       owner_pid=_dead_pid())
    _age("died_recently", 30)
    with TestClient(create_app(ApiSettings(background_services=True))):
        pass
    rec = db.get_job_record("died_recently")
    assert rec["status"] == "cancelled"
    assert rec["error"] == background_jobs.INTERRUPTED_MESSAGE


def test_record_claiming_this_process_but_not_live_here_is_closed(client):
    """A row with this process's pid that this process doesn't run was
    left by an earlier run that had the same pid."""
    import os
    db.save_job_record("mine_before", "running", owner_pid=os.getpid())
    items = {j["job_id"]: j for j in client.get("/api/jobs").json()["items"]}
    assert items["mine_before"]["status"] == "cancelled"
    assert items["mine_before"]["outcome_message"] == background_jobs.INTERRUPTED_MESSAGE


def test_cancel_closes_a_dead_owners_record_at_once(client):
    """Cancel on a record whose owner process is gone used to only set a
    flag nobody would read, and answer "running"."""
    db.save_job_record("dead_owner", "running", owner_pid=_dead_pid())
    r = client.post("/api/jobs/dead_owner/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert db.get_job_record("dead_owner")["status"] == "cancelled"
    # and it can then be deleted from the history
    assert client.post("/api/jobs/dead_owner/delete", json={"confirm": True}).status_code == 200


def test_a_live_other_owner_is_never_closed_by_pid(client):
    """The owner's pid is alive (another Baihe on the same library): its
    fresh record stays running; only the cancel flag is set."""
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        db.save_job_record("other_live", "running", owner_pid=proc.pid)
        assert client.get("/api/jobs/other_live").json()["stale"] is False
        r = client.post("/api/jobs/other_live/cancel")
        assert r.json()["status"] == "running"
        assert {j["job_id"]: j["status"] for j in client.get("/api/jobs").json()["items"]}[
            "other_live"] == "running"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_jobs_mirror_records_their_owner_pid(isolated_db):
    import os
    import threading
    release = threading.Event()
    assert background_jobs.start_job("pid_rec", lambda: release.wait(5.0))
    try:
        assert db.get_job_record("pid_rec")["owner_pid"] == os.getpid()
    finally:
        release.set()
        assert background_jobs.wait_for_job_threads(5.0)
        background_jobs.clear_job("pid_rec")


def test_cancelling_a_queued_job_through_the_api_ends_it(client, monkeypatch):
    import threading
    background_jobs.set_gpu_limit_enabled(True)
    release = threading.Event()
    ran = []
    assert background_jobs.start_job("api_q_a", lambda: release.wait(5.0), gpu_touching=True)
    try:
        assert background_jobs.start_job("api_q_b", lambda: ran.append(1), gpu_touching=True)
        assert background_jobs.get_status("api_q_b")["status"] == "queued"
        r = client.post("/api/jobs/api_q_b/cancel")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert client.get("/api/jobs/api_q_b").json()["status"] == "cancelled"
    finally:
        release.set()
        assert background_jobs.wait_for_job_threads(5.0)
    assert ran == []
    background_jobs.clear_job("api_q_a")
    background_jobs.clear_job("api_q_b")


def test_a_running_jobs_record_says_cancelling_after_cancel(client):
    import threading
    release = threading.Event()

    def work():
        release.wait(5.0)
        if background_jobs.is_cancel_requested("api_c"):
            raise background_jobs.JobCancelled("api_c")

    assert background_jobs.start_job("api_c", work)
    try:
        r = client.post("/api/jobs/api_c/cancel")
        assert r.status_code == 200
        assert client.get("/api/jobs/api_c").json()["message"] == background_jobs.CANCELLING_MESSAGE
    finally:
        release.set()
        assert background_jobs.wait_for_job_threads(5.0)
    assert client.get("/api/jobs/api_c").json()["status"] == "cancelled"
    background_jobs.clear_job("api_c")
