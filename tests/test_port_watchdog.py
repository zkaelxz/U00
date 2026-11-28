"""The port watchdog's restart decisions (installer/port_watchdog.py)."""

from installer import port_watchdog as pw

CFG = pw.Config(failures_needed=3, max_restarts_per_hour=2, max_defer_seconds=3600)


def step(state, now, **kw):
    kw = {"api_ok": False, "others_ok": True, "service_running": True, "job_running": False, **kw}
    return pw.decide(state, now, CFG, **kw)


def test_restarts_only_on_the_third_failure_in_a_row():
    s = pw.State()
    assert [step(s, t) for t in (0, 60, 120)] == ["WAIT", "WAIT", "RESTART"]
    assert s.total_restarts == 1 and s.total_failures == 3


def test_an_answer_resets_the_count():
    s = pw.State()
    step(s, 0), step(s, 60)
    assert step(s, 120, api_ok=True) == "OK"
    assert [step(s, 180), step(s, 240)] == ["WAIT", "WAIT"]


def test_stopped_service_is_never_restarted():
    s = pw.State()
    assert [step(s, t, service_running=False) for t in (0, 60, 120, 180)] == ["NOT_RUNNING"] * 4
    assert s.total_restarts == 0 and s.outage_started is None


def test_running_job_defers_while_other_ports_answer_then_gives_up():
    s = pw.State()
    for t in (0, 60):
        step(s, t, job_running=True)
    assert step(s, 120, job_running=True) == "DEFER_JOB"
    assert step(s, 3700, job_running=True) == "RESTART"


def test_running_job_does_not_defer_when_every_port_is_dead():
    s = pw.State()
    for t in (0, 60):
        step(s, t, job_running=True, others_ok=False)
    assert step(s, 120, job_running=True, others_ok=False) == "RESTART"


def test_grace_after_a_restart_then_cap_per_hour():
    s = pw.State()
    for t in (0, 60, 120):
        step(s, t)
    assert step(s, 130) == "WAIT" and s.failures == 0
    for t in (300, 360, 420):
        action = step(s, t)
    assert action == "RESTART"
    for t in (600, 660):
        step(s, t)
    assert step(s, 720) == "CAPPED"
    assert step(s, 3700) == "CAPPED"
    assert step(s, 3721) == "RESTART"


def test_no_restart_while_an_update_runs_or_a_start_is_young():
    s = pw.State()
    assert [step(s, t, updating=True) for t in (0, 60, 120, 180)] == ["UPDATING"] * 4
    # A 4-minute first start, as a new version compiling its .pyc files.
    assert [step(s, 300 + t, started_ago=60 + t) for t in (0, 60, 120, 180)] == ["STARTING"] * 4
    assert s.total_restarts == 0 and s.total_skips == 8
    # Still dead long after it started: the count begins then.
    assert [step(s, 1000 + t, started_ago=600 + t) for t in (0, 60, 120)] == ["WAIT", "WAIT", "RESTART"]


def test_dry_run_logs_and_restarts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pw, "port_answers", lambda port: False)
    monkeypatch.setattr(pw, "service_state", lambda: "RUNNING")
    monkeypatch.setattr(pw, "update_in_progress", lambda: False)
    monkeypatch.setattr(pw, "service_started_ago", lambda now: None)
    monkeypatch.setattr(pw, "restart_service", lambda: (_ for _ in ()).throw(AssertionError("restarted")))
    args = pw.argparse.Namespace(data_dir=str(tmp_path), state_dir=str(tmp_path), api_port=8600,
                                 other_ports=[8610], failures=3, max_restarts_per_hour=2,
                                 max_defer_minutes=60, dry_run=True)
    actions = [pw.run_once(args, now=t) for t in (0, 60, 120)]
    assert actions[-1] == "RESTART"
    log = (tmp_path / pw.LOG_NAME).read_text(encoding="utf-8")
    assert "DRY-RUN would restart" in log and "restarts_done=1" in log


def test_recovery_logs_the_time_since_the_first_failure(tmp_path, monkeypatch):
    up = {"api": False}
    monkeypatch.setattr(pw, "port_answers", lambda port: up["api"] if port == 8600 else True)
    monkeypatch.setattr(pw, "service_state", lambda: "RUNNING")
    monkeypatch.setattr(pw, "update_in_progress", lambda: False)
    monkeypatch.setattr(pw, "service_started_ago", lambda now: None)
    args = pw.argparse.Namespace(data_dir=str(tmp_path), state_dir=str(tmp_path), api_port=8600,
                                 other_ports=[8610], failures=3, max_restarts_per_hour=2,
                                 max_defer_minutes=60, dry_run=True)
    pw.run_once(args, now=1000)
    up["api"] = True
    pw.run_once(args, now=1090)
    assert "RECOVERED 90s after the first failed check" in (tmp_path / pw.LOG_NAME).read_text(encoding="utf-8")


def test_job_is_running_reads_recent_rows_only(tmp_path):
    import sqlite3
    (tmp_path / "library").mkdir()
    conn = sqlite3.connect(tmp_path / "library" / "library.db")
    conn.execute("CREATE TABLE job_records (job_id TEXT, status TEXT, updated_at REAL)")
    conn.execute("INSERT INTO job_records VALUES ('a', 'running', 100)")
    conn.commit()
    conn.close()
    assert pw.job_is_running(str(tmp_path), now=100 + 60)
    assert not pw.job_is_running(str(tmp_path), now=100 + pw.JOB_FRESH_SECONDS + 1)
    assert not pw.job_is_running(str(tmp_path / "missing"), now=100)


def test_update_in_progress_reads_state_words(monkeypatch):
    out = {"v": ""}
    monkeypatch.setattr(pw, "_sc", lambda *a: out["v"])
    for text, expected in (("ZUSTAND : 4  RUNNING", True), ("STATE : 3  STOP_PENDING", True),
                           ("STATE : 1  STOPPED", False), ("FAILED 1060: not installed", False)):
        out["v"] = text
        assert pw.update_in_progress() is expected
