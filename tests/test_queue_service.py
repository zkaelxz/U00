"""Tests for services/queue_service.py: the user-managed job queue.

The stage start functions are replaced by fakes that start a real
background job whose body waits on an Event, so the runner sees real job
records through jobs_service, and each test decides when a job ends."""
import threading

import pytest

import background_jobs
import db
import translate_engines
from core import Line
from lib.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from services import (auth_service, queue_service as q, settings_service, transcribe_service,
                      translate_run_service)

WAIT = 10


def _drama(title="T", engine="ollama", lines=(("你好", ""),), **fields):
    fields.setdefault("transcript_mode", "whisper")
    did = db.create_drama(title_en=title, title_zh="题", translation_engine=engine, **fields)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                        for i, (z, en) in enumerate(lines)])
    return did


class FakeJobs:
    """Stands in for the stage start functions. Each started job blocks until
    its gate is set; `outcome` decides whether it raises."""

    def __init__(self, monkeypatch):
        self.started = []
        self.gates = {}
        self.fail = set()
        self.concurrent = []
        self.lock = threading.Lock()
        self.any_started = threading.Event()
        monkeypatch.setattr(transcribe_service, "start_transcribe_run", self._transcribe)
        monkeypatch.setattr(translate_run_service, "start_translate_run", self._translate)
        monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)

    def _start(self, job_id):
        gate = self.gates.setdefault(job_id, threading.Event())
        fail = job_id in self.fail

        def body():
            if not gate.wait(WAIT):
                raise RuntimeError("test gate never opened")
            if fail:
                raise RuntimeError("engine said no")
        with self.lock:
            self.concurrent.append(len([j for j in background_jobs.active_job_ids()
                                        if j.startswith(("transcribe_", "translate_"))]))
        if not background_jobs.start_job(job_id, body, description="fake"):
            raise ConflictError("busy")
        self.started.append(job_id)
        self.any_started.set()
        return {"job_id": job_id}

    def _transcribe(self, drama_id, **kw):
        return self._start(f"transcribe_{drama_id}")

    def _translate(self, drama_id, **kw):
        return self._start(f"translate_{drama_id}")

    def finish(self, job_id):
        self.gates.setdefault(job_id, threading.Event()).set()
        assert background_jobs.wait_for_job_threads(WAIT, job_ids=[job_id])


@pytest.fixture
def jobs(isolated_db, monkeypatch):
    fake = FakeJobs(monkeypatch)
    yield fake
    for gate in list(fake.gates.values()):
        gate.set()
    background_jobs.wait_for_job_threads(WAIT)


def _items():
    return q.get_queue(None)["items"]


def _status():
    return [(i["step"], i["status"]) for i in _items()]


def test_items_run_one_at_a_time_in_order(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True)
    q.add_item(None, b, "transcribe", True)
    q.add_item(None, a, "translate", True)
    q.start(None)

    q.tick()
    assert jobs.started == [f"transcribe_{a}"]
    q.tick()                       # still running: nothing new starts
    assert jobs.started == [f"transcribe_{a}"]
    assert [i["status"] for i in _items()] == ["running", "waiting", "waiting"]

    jobs.finish(f"transcribe_{a}")
    q.tick()
    assert jobs.started == [f"transcribe_{a}", f"transcribe_{b}"]
    jobs.finish(f"transcribe_{b}")
    q.tick()
    jobs.finish(f"translate_{a}")
    q.tick()
    assert jobs.started == [f"transcribe_{a}", f"transcribe_{b}", f"translate_{a}"]
    assert jobs.concurrent == [0, 0, 0]
    assert [i["status"] for i in _items()] == ["done", "done", "done"]
    assert q.get_queue(None)["state"] == "idle"


@pytest.mark.parametrize("stop", [False, True])
def test_a_failed_item_continues_or_stops_by_the_policy(jobs, stop):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True)
    q.add_item(None, b, "transcribe", True)
    q.set_policy(None, stop)
    q.start(None)
    jobs.fail.add(f"transcribe_{a}")
    q.tick()
    jobs.finish(f"transcribe_{a}")
    q.tick()
    first = _items()[0]
    assert first["status"] == "failed" and "engine said no" in first["reason"]
    if stop:
        assert jobs.started == [f"transcribe_{a}"]
        state = q.get_queue(None)
        assert state["state"] == "idle" and "failed" in state["notice"]
        assert _items()[1]["status"] == "waiting"
    else:
        assert jobs.started == [f"transcribe_{a}", f"transcribe_{b}"]


@pytest.fixture
def cap(monkeypatch):
    def _set(cap_usd, spend=0.0):
        real = settings_service.resolve_key
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a, **kw: str(cap_usd) if k == "monthly_cap_usd" else real(k, *a, **kw))
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: spend)
    return _set


def test_translate_is_skipped_when_the_month_cap_is_used_up(jobs, cap, monkeypatch):
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda name: "k")
    paid, free = _drama("Paid", engine="claude"), _drama("Free", engine="ollama")
    cap(5.0, spend=5.0)
    q.add_item(None, paid, "translate", True)
    q.add_item(None, free, "transcribe", True)
    q.start(None)
    q.tick()
    first = _items()[0]
    assert first["status"] == "skipped"
    assert first["reason"].startswith("Skipped: ") and "spending cap" in first["reason"]
    assert f"translate_{paid}" not in jobs.started
    # The queue moved on.
    q.tick()
    assert jobs.started == [f"transcribe_{free}"]


def test_translate_is_skipped_when_the_estimate_is_above_what_is_left(jobs, cap):
    did = _drama("Paid", engine="claude", lines=[("长" * 400, "")] * 50)
    cap(5.0, spend=4.9999999)
    q.add_item(None, did, "translate", True)
    q.start(None)
    q.tick()
    item = _items()[0]
    assert item["status"] == "skipped" and "above what is left" in item["reason"]
    assert jobs.started == []


def test_a_start_refusal_is_a_plain_skip_and_the_queue_moves_on(jobs):
    gone, ok = _drama("Gone"), _drama("Ok")
    q.add_item(None, gone, "transcribe", True)
    q.add_item(None, ok, "transcribe", True)
    db.delete_drama(gone)
    q.start(None)
    q.tick()
    q.tick()
    assert _items()[0]["status"] == "skipped"
    assert _items()[0]["reason"] == "Skipped: The title was deleted."
    assert jobs.started == [f"transcribe_{ok}"]


def test_pause_stops_new_starts_but_the_running_item_finishes(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True)
    q.add_item(None, b, "transcribe", True)
    q.start(None)
    q.tick()
    q.pause(None)
    assert q.get_queue(None)["state"] == "paused"
    jobs.finish(f"transcribe_{a}")
    q.tick()
    assert _status() == [("transcribe", "done"), ("transcribe", "waiting")]
    assert jobs.started == [f"transcribe_{a}"]
    q.start(None)
    q.tick()
    assert jobs.started == [f"transcribe_{a}", f"transcribe_{b}"]


def test_restart_marks_the_running_item_interrupted_and_runs_nothing(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True)
    q.add_item(None, b, "transcribe", True)
    q.start(None)
    q.tick()
    # As after a crash: this process's job is gone and its record was swept.
    db.save_job_record(f"transcribe_{a}", "cancelled", error=background_jobs.INTERRUPTED_MESSAGE)
    q.restore_at_startup()
    state = q.get_queue(None)
    assert state["state"] == "idle"
    assert [i["status"] for i in state["items"]] == ["interrupted", "waiting"]
    assert "Interrupted" in state["items"][0]["reason"]
    q.tick()
    assert jobs.started == [f"transcribe_{a}"]
    q.retry_item(None, state["items"][0]["id"])
    assert _items()[0]["status"] == "waiting"


def test_restart_marks_a_passed_schedule_missed_and_never_runs_it(jobs):
    did = _drama()
    q.add_item(None, did, "transcribe", True)
    now = 1_900_000_000.0
    q.set_schedule(None, "02:00", now=now)
    start_at = q.get_queue(None)["schedule"]["start_at"]
    q.restore_at_startup(now=start_at + 60)
    sched = q.get_queue(None)["schedule"]
    assert sched["missed"] is True
    q.tick(now=start_at + 120)
    assert jobs.started == [] and q.get_queue(None)["state"] == "idle"
    # "Start now" runs it and clears the missed schedule.
    q.start(None)
    assert q.get_queue(None)["schedule"] is None
    q.tick(now=start_at + 180)
    assert jobs.started == [f"transcribe_{did}"]


def test_a_schedule_fires_once_at_its_time_and_clears(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True)
    now = 1_900_000_000.0
    q.set_schedule(None, "23:30", now=now)
    start_at = q.get_queue(None)["schedule"]["start_at"]
    assert now < start_at <= now + 86400
    q.tick(now=start_at - 1)
    assert jobs.started == []
    q.tick(now=start_at)
    assert jobs.started == [f"transcribe_{a}"]
    assert q.get_queue(None)["schedule"] is None
    jobs.finish(f"transcribe_{a}")
    q.tick(now=start_at + 10)
    assert q.get_queue(None)["state"] == "idle"
    # Added later, it waits: the schedule fired once.
    q.add_item(None, b, "transcribe", True)
    q.tick(now=start_at + 86400 + 10)
    assert jobs.started == [f"transcribe_{a}"]


def test_next_occurrence_is_strictly_after_now():
    import datetime
    base = datetime.datetime(2026, 10, 10, 22, 0).timestamp()
    assert q.next_occurrence("23:00", base) == base + 3600
    assert q.next_occurrence("22:00", base) == base + 86400
    with pytest.raises(InvalidInputError):
        q.next_occurrence("24:00", base)


def test_export_writes_the_subtitle_download_inline(jobs):
    from services import artifact_service
    did = _drama(lines=[("你好", "Hello")])
    q.add_item(None, did, "export", True)
    q.start(None)
    q.tick()
    assert _items()[0]["status"] == "done"
    art = artifact_service.get_artifact(did, "subtitle")
    with open(art["path"], encoding="utf-8") as f:
        assert "Hello" in f.read()
    assert "path" not in _items()[0]


def test_export_is_skipped_with_nothing_to_export(jobs):
    did = _drama()
    q.add_item(None, did, "export", True)
    q.start(None)
    q.tick()
    assert _items()[0]["reason"] == "Skipped: There are no English lines to export yet."


def test_validation_rejects_unknown_titles_bad_steps_and_a_full_queue(jobs):
    did = _drama()
    with pytest.raises(NotFoundError):
        q.add_item(None, 999999, "transcribe", True)
    with pytest.raises(InvalidInputError):
        q.add_item(None, did, "dub", True)
    for _ in range(q.MAX_ITEMS):
        q.add_item(None, did, "export", True)
    with pytest.raises(ConflictError):
        q.add_item(None, did, "export", True)
    assert len(_items()) == q.MAX_ITEMS


def test_members_queue_only_their_own_titles_and_see_only_those(jobs):
    owner = auth_service.add_user("a@example.com")
    other = auth_service.add_user("b@example.com")
    for u in (owner, other):
        auth_service.grant_permission(u["id"], "jobs.start")
    mine = _drama("Mine", owner_user_id=owner["id"], is_private=1)
    shared = _drama("Shared", owner_user_id=other["id"], is_private=0)
    pa = {"user_id": owner["id"], "is_admin": False, "permissions": ["jobs.start"]}
    pb = {"user_id": other["id"], "is_admin": False, "permissions": ["jobs.start"]}
    with pytest.raises(NotFoundError):
        q.add_item(pa, shared, "transcribe", False)     # visible, but not theirs
    q.add_item(pa, mine, "transcribe", False)
    q.add_item(pb, shared, "transcribe", False)
    assert [i["title"] for i in q.get_queue(pa)["items"]] == ["Mine"]
    assert [i["title"] for i in q.get_queue(pb)["items"]] == ["Shared"]
    assert len(q.get_queue(None)["items"]) == 2
    b_item = q.get_queue(pb)["items"][0]["id"]
    with pytest.raises(NotFoundError):
        q.remove_item(pa, b_item)
    with pytest.raises(ForbiddenError):
        q.start(pa)
    assert q.get_queue(pa)["can_control"] is False


def test_a_paid_translate_needs_engines_paid_to_add(jobs):
    u = auth_service.add_user("a@example.com")
    did = _drama(engine="claude", owner_user_id=u["id"], is_private=1)
    member = {"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]}
    with pytest.raises(ForbiddenError):
        q.add_item(member, did, "translate", False)
    q.add_item(member, did, "transcribe", False)     # a free step is fine
    state = q.add_item(None, did, "translate", True)
    state["items"] = [i for i in state["items"] if i["step"] == "translate"]
    assert state["items"][0]["engine"] == "claude" and state["items"][0]["paid"] is True


def test_a_revoked_account_has_its_waiting_items_skipped(jobs):
    u = auth_service.add_user("a@example.com")
    auth_service.grant_permission(u["id"], "jobs.start")
    did = _drama("Mine", owner_user_id=u["id"], is_private=1)
    q.add_item({"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]},
               did, "transcribe", False)
    auth_service.revoke_permission(u["id"], "jobs.start")
    q.start(None)
    q.tick()
    assert _items()[0]["status"] == "skipped" and jobs.started == []


def test_reorder_remove_and_clear_finished(jobs):
    a, b, c = _drama("A"), _drama("B"), _drama("C")
    for did in (a, b, c):
        q.add_item(None, did, "transcribe", True)
    ids = [i["id"] for i in _items()]
    q.move_item(None, ids[2], "up")
    assert [i["title"] for i in _items()] == ["A", "C", "B"]
    q.move_item(None, ids[0], "up")                   # already first: unchanged
    q.move_item(None, ids[0], "down")
    assert [i["title"] for i in _items()] == ["C", "A", "B"]
    q.remove_item(None, ids[1])
    assert [i["title"] for i in _items()] == ["C", "A"]
    q.start(None)
    q.tick()
    with pytest.raises(ConflictError):
        q.remove_item(None, ids[2])                   # running
    jobs.finish(f"transcribe_{c}")
    q.tick()
    q.clear_finished(None)
    assert [i["title"] for i in _items()] == ["A"]


def test_runner_exits_on_shutdown_and_holds_no_lock_while_a_job_runs(jobs, monkeypatch):
    monkeypatch.setattr(q, "POLL_SECONDS", 0.05)
    did = _drama()
    q.add_item(None, did, "transcribe", True)
    assert q.start_runner()
    try:
        q.start(None)
        assert jobs.any_started.wait(WAIT)
        # The job is running: an API call must not wait on the runner.
        assert q._lock.acquire(timeout=1)
        q._lock.release()
        assert q.get_queue(None)["items"][0]["status"] == "running"
    finally:
        q.stop_runner(timeout=WAIT)
    assert not any(t.name == "api-job-queue" and t.is_alive() for t in threading.enumerate())


def test_a_stored_document_from_another_version_starts_empty(isolated_db):
    db.set_app_setting(q.QUEUE_KEY, {"version": 99, "items": [{"id": 1}]})
    assert q.get_queue(None)["items"] == []


def test_a_transcribe_over_lines_needs_lines_edit_as_for_a_click(isolated_db):
    # The real start function: the queue runs it as the adder, so the
    # service's own overwrite rule applies to a queued run too.
    u = auth_service.add_user("a@example.com")
    auth_service.revoke_permission(u["id"], "lines.edit")
    did = _drama("Mine", owner_user_id=u["id"], is_private=1)
    q.add_item({"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]},
               did, "transcribe", False)
    q.start(None)
    q.tick()
    item = _items()[0]
    from services import ownership_service
    assert item["status"] == "skipped"
    assert item["reason"] == f"Skipped: {ownership_service._REPLACE_NEEDS_EDIT}"


def test_a_restored_backup_document_never_resumes_on_its_own(jobs):
    # A library restore swaps app_settings under the running server: the
    # document from the backup says "running" and holds an unfired schedule.
    a, b = _drama("A"), _drama("B")
    db.set_app_setting(q.QUEUE_KEY, {
        "version": 1, "instance": "an-earlier-process", "state": "running", "next_id": 3,
        "schedule": {"time": "02:00", "start_at": 1_000.0, "missed": False},
        "items": [{"id": 1, "drama_id": a, "step": "transcribe", "status": "running",
                   "job_id": f"transcribe_{a}"},
                  {"id": 2, "drama_id": b, "step": "transcribe", "status": "waiting"}]})
    q.tick(now=2_000.0)
    state = q.get_queue(None)
    assert jobs.started == []
    assert state["state"] == "idle" and state["schedule"]["missed"] is True
    assert [i["status"] for i in state["items"]] == ["interrupted", "waiting"]


def test_paid_translate_is_skipped_when_no_monthly_cap_is_set(jobs, cap, monkeypatch):
    monkeypatch.setattr(translate_run_service.translate_service, "resolve_api_key", lambda name: "k")
    did = _drama("Paid", engine="claude")
    cap(0.0)
    q.add_item(None, did, "translate", True)
    q.start(None)
    q.tick()
    assert _items()[0]["reason"] == f"Skipped: {q._NO_CAP}"
    assert jobs.started == []


def test_a_restore_or_cleanup_hold_makes_the_queue_wait_not_skip(jobs):
    did = _drama()
    q.add_item(None, did, "transcribe", True)
    q.start(None)
    assert background_jobs.enter_maintenance()
    try:
        q.tick()
        assert _items()[0]["status"] == "waiting" and jobs.started == []
    finally:
        background_jobs.exit_maintenance()
    q.tick()
    assert jobs.started == [f"transcribe_{did}"]


def test_a_lagging_job_record_does_not_end_the_item_early(jobs):
    a, b = _drama("A"), _drama("B")
    q.add_item(None, a, "transcribe", True)
    q.add_item(None, b, "transcribe", True)
    q.start(None)
    q.tick()
    # As if the "running" write failed and the row kept an earlier run's status.
    db.save_job_record(f"transcribe_{a}", "done")
    q.tick()
    assert jobs.started == [f"transcribe_{a}"]
    assert _items()[0]["status"] == "running"


def test_a_claim_whose_outcome_was_never_written_is_interrupted(jobs):
    did = _drama()
    q.add_item(None, did, "transcribe", True)
    with q._lock:
        doc = q._load()
        doc["items"][0].update(status="running", job_id=None)
        q._save(doc)
    q.tick()
    assert _items()[0]["status"] == "interrupted" and jobs.started == []


def test_one_member_cannot_fill_the_shared_queue(jobs):
    u = auth_service.add_user("a@example.com")
    did = _drama("Mine", owner_user_id=u["id"], is_private=1)
    member = {"user_id": u["id"], "is_admin": False, "permissions": ["jobs.start"]}
    for _ in range(q.MAX_ITEMS_PER_MEMBER):
        q.add_item(member, did, "export", False)
    with pytest.raises(ConflictError):
        q.add_item(member, did, "export", False)
    q.add_item(None, did, "export", True)     # the owner still can
